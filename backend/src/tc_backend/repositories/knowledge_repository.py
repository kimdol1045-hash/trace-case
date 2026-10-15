"""Grounded typed-knowledge projection and pgvector repository.

Callers own the surrounding SQLAlchemy transaction. Projection processing locks
one outbox row, writes the graph and its replay checkpoint, and marks the event
published in that same transaction.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tc_backend.db.models import (
    CycleItem,
    ExecutionAttempt,
    Issue,
    KnowledgeEdge,
    KnowledgeEmbedding,
    KnowledgeEvidence,
    KnowledgeNode,
    KnowledgeOwnerType,
    KnowledgeProjectionEvent,
    KnowledgeSourceKind,
    KnowledgeSourceRecord,
    OutboxEvent,
    OutboxStatus,
    SlackMessage,
    SlackMessageRevision,
    SlackThread,
    SlackWorkItem,
    TestCase,
    TestCaseVersion,
)
from tc_backend.knowledge.models import (
    SlackSourceBundle,
    SlackSourceRevision,
    SourceRevisionOperation,
)

ONTOLOGY_SCHEMA_V1_ID = uuid.UUID("00000000-0000-7000-8000-000000000001")
BGE_M3_MODEL = "bge-m3"
BGE_M3_DIMENSION = 1024

NODE_TYPES = frozenset(
    {
        "WORK_ITEM",
        "MENU",
        "FEATURE",
        "REQUIREMENT",
        "DECISION",
        "CONSTRAINT",
        "OPEN_QUESTION",
        "RISK",
        "TEST_CASE",
        "EXECUTION",
        "ISSUE",
        "RELEASE",
    }
)
KNOWLEDGE_STATUSES = frozenset({"ACTIVE", "AMBIGUOUS", "SUPERSEDED", "RETRACTED"})
ALLOWED_RELATIONS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "AFFECTS": (
        frozenset({"WORK_ITEM", "REQUIREMENT", "DECISION", "ISSUE", "RELEASE"}),
        frozenset({"MENU", "FEATURE", "REQUIREMENT", "TEST_CASE", "RELEASE"}),
    ),
    "TARGETS": (
        frozenset({"WORK_ITEM", "REQUIREMENT", "RELEASE"}),
        frozenset({"MENU", "FEATURE", "REQUIREMENT"}),
    ),
    "DISCUSSES": (
        frozenset({"WORK_ITEM"}),
        frozenset(
            {"FEATURE", "REQUIREMENT", "DECISION", "CONSTRAINT", "OPEN_QUESTION", "RISK", "ISSUE"}
        ),
    ),
    "DERIVED_FROM": (
        frozenset(
            {"REQUIREMENT", "DECISION", "CONSTRAINT", "OPEN_QUESTION", "RISK", "TEST_CASE", "ISSUE"}
        ),
        frozenset({"WORK_ITEM", "REQUIREMENT", "DECISION", "TEST_CASE", "EXECUTION", "ISSUE"}),
    ),
    "REFINES": (
        frozenset({"REQUIREMENT", "DECISION", "CONSTRAINT"}),
        frozenset({"REQUIREMENT", "DECISION", "CONSTRAINT"}),
    ),
    "SUPERSEDES": (
        frozenset({"REQUIREMENT", "DECISION", "CONSTRAINT"}),
        frozenset({"REQUIREMENT", "DECISION", "CONSTRAINT"}),
    ),
    "APPLIES_TO": (
        frozenset({"REQUIREMENT", "DECISION", "CONSTRAINT", "RISK"}),
        frozenset({"MENU", "FEATURE", "RELEASE"}),
    ),
    "VERIFIES": (
        frozenset({"TEST_CASE", "EXECUTION"}),
        frozenset({"FEATURE", "REQUIREMENT", "DECISION", "CONSTRAINT", "TEST_CASE"}),
    ),
    "COVERS": (
        frozenset({"TEST_CASE", "RELEASE"}),
        frozenset({"FEATURE", "REQUIREMENT", "RISK", "TEST_CASE"}),
    ),
    "INSTANCE_OF": (
        frozenset({"EXECUTION", "FEATURE"}),
        frozenset({"TEST_CASE", "MENU"}),
    ),
    "FOUND_BY": (
        frozenset({"ISSUE", "RISK"}),
        frozenset({"EXECUTION", "TEST_CASE"}),
    ),
}


class InvalidKnowledgeRelation(ValueError):
    """A predicate does not allow the supplied v1 source/target types."""


class SourceRevisionConflictError(ValueError):
    """An immutable source identity was reused with different content."""


class UnsupportedProjectionEvent(ValueError):
    """The outbox aggregate has no knowledge projection."""


@dataclass(frozen=True, slots=True)
class EvidenceInput:
    source_kind: KnowledgeSourceKind | str
    entity_type: str
    entity_id: uuid.UUID
    revision_key: str
    content_hash: str
    recorded_at: datetime
    permalink: str | None = None

    @classmethod
    def from_content(
        cls,
        *,
        source_kind: KnowledgeSourceKind | str,
        entity_type: str,
        entity_id: uuid.UUID,
        revision_key: str,
        content: str | bytes,
        recorded_at: datetime,
        permalink: str | None = None,
    ) -> EvidenceInput:
        return cls(
            source_kind=source_kind,
            entity_type=entity_type,
            entity_id=entity_id,
            revision_key=revision_key,
            content_hash=content_sha256(content),
            recorded_at=recorded_at,
            permalink=permalink,
        )


@dataclass(frozen=True, slots=True)
class EmbeddingSearchResult:
    embedding_id: uuid.UUID
    owner_type: str
    owner_id: uuid.UUID
    model_version: str
    content_hash: str
    cosine_distance: float
    score: float


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    outbox_event_id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_version: int
    node_ids: tuple[uuid.UUID, ...]
    edge_ids: tuple[uuid.UUID, ...]
    replayed: bool
    stale: bool = False


def content_sha256(content: str | bytes) -> str:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(raw).hexdigest()


def validate_v1_relation(predicate: str, source_type: str, target_type: str) -> None:
    relation = ALLOWED_RELATIONS.get(predicate)
    if relation is None:
        raise InvalidKnowledgeRelation(f"unknown ontology v1 predicate: {predicate}")
    allowed_sources, allowed_targets = relation
    if source_type not in allowed_sources or target_type not in allowed_targets:
        raise InvalidKnowledgeRelation(
            f"ontology v1 predicate {predicate} does not allow {source_type} -> {target_type}"
        )


def validate_bge_m3_embedding(embedding: list[float] | tuple[float, ...]) -> list[float]:
    if len(embedding) != BGE_M3_DIMENSION:
        raise ValueError(
            f"bge-m3 embedding must have {BGE_M3_DIMENSION} dimensions; got {len(embedding)}"
        )
    normalized = [float(value) for value in embedding]
    if not all(math.isfinite(value) for value in normalized):
        raise ValueError("bge-m3 embedding values must be finite")
    return normalized


class KnowledgeRepository:
    """Node, edge, evidence, embedding, and outbox projection operations."""

    def __init__(
        self,
        session: Session,
        *,
        ontology_schema_id: uuid.UUID = ONTOLOGY_SCHEMA_V1_ID,
    ) -> None:
        self.session = session
        self.ontology_schema_id = ontology_schema_id

    def load_slack_source_bundle(self, work_item_id: uuid.UUID) -> SlackSourceBundle:
        """Load one deterministic root-plus-thread bundle from the source ledger.

        The Slack ledger remains authoritative. Every immutable revision is
        returned in message chronology followed by provider revision chronology;
        tombstones are retained and per-message permalinks fall back to the root
        permalink only when Slack did not provide a more specific citation.
        """

        row = self.session.execute(
            select(SlackWorkItem, SlackThread)
            .join(SlackThread, SlackThread.slack_work_item_id == SlackWorkItem.id)
            .where(SlackWorkItem.id == work_item_id)
        ).one_or_none()
        if row is None:
            raise LookupError(f"Slack work item not found: {work_item_id}")
        work_item, thread = row
        rows = self.session.execute(
            select(SlackMessage, SlackMessageRevision)
            .join(SlackMessageRevision, SlackMessageRevision.message_id == SlackMessage.id)
            .where(SlackMessage.thread_id == thread.id)
            .order_by(
                SlackMessage.is_root.desc(),
                SlackMessage.message_ts_key,
                SlackMessage.id,
                SlackMessageRevision.revision,
                SlackMessageRevision.id,
            )
        ).all()
        revisions: list[SlackSourceRevision] = []
        seen_messages: set[uuid.UUID] = set()
        for message, revision in rows:
            operation = (
                SourceRevisionOperation.DELETE
                if revision.is_deleted
                else SourceRevisionOperation.EDIT
                if revision.edited_at is not None or message.id in seen_messages
                else SourceRevisionOperation.CREATE
            )
            seen_messages.add(message.id)
            revisions.append(
                SlackSourceRevision(
                    source_revision_id=str(revision.id),
                    message_id=str(message.id),
                    message_ts=message.message_ts,
                    is_root=message.is_root,
                    revision_ts=revision.revision_ts,
                    author_id=message.author_id,
                    body=revision.body,
                    permalink=message.permalink or work_item.permalink,
                    operation=operation,
                    recorded_at=revision.received_at,
                )
            )
        if not revisions:
            raise LookupError(f"Slack work item has no source revisions: {work_item_id}")
        return SlackSourceBundle(
            workspace_id=work_item.slack_workspace_id,
            channel_id=work_item.channel_id,
            thread_ts=thread.thread_ts,
            revisions=tuple(revisions),
            work_item_id=str(work_item.id),
            source_version=work_item.source_version,
        )

    def register_source(self, evidence: EvidenceInput) -> KnowledgeSourceRecord:
        source_kind = KnowledgeSourceKind(evidence.source_kind).value
        entity_type = _identifier(evidence.entity_type, "entity_type", maximum=64)
        revision_key = _nonblank(evidence.revision_key, "revision_key", maximum=200)
        content_hash = _nonblank(evidence.content_hash, "content_hash", maximum=128)
        statement = (
            insert(KnowledgeSourceRecord)
            .values(
                id=uuid.uuid4(),
                source_kind=source_kind,
                entity_type=entity_type,
                entity_id=evidence.entity_id,
                revision_key=revision_key,
                content_hash=content_hash,
                permalink=evidence.permalink,
                recorded_at=evidence.recorded_at,
            )
            .on_conflict_do_nothing(
                constraint="uq_knowledge_source_records_identity"
            )
            .returning(KnowledgeSourceRecord.id)
        )
        source_id = self.session.scalar(statement)
        if source_id is None:
            source = self.session.execute(
                select(KnowledgeSourceRecord).where(
                    KnowledgeSourceRecord.source_kind == source_kind,
                    KnowledgeSourceRecord.entity_type == entity_type,
                    KnowledgeSourceRecord.entity_id == evidence.entity_id,
                    KnowledgeSourceRecord.revision_key == revision_key,
                )
            ).scalar_one()
            if source.content_hash != content_hash:
                raise SourceRevisionConflictError(
                    "source revision identity already exists with different content"
                )
            return source
        return self.session.get(KnowledgeSourceRecord, source_id)  # type: ignore[return-value]

    def create_node(
        self,
        *,
        node_type: str,
        label: str,
        evidence: EvidenceInput,
        status: str = "ACTIVE",
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        properties: dict[str, Any] | None = None,
        recorded_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
    ) -> KnowledgeNode:
        node_type = _node_type(node_type)
        status = _status(status)
        if (entity_type is None) != (entity_id is None):
            raise ValueError("entity_type and entity_id must be supplied together")
        normalized_entity_type = (
            _identifier(entity_type, "entity_type", maximum=50)
            if entity_type is not None
            else None
        )
        node: KnowledgeNode | None = None
        if normalized_entity_type is not None:
            node = self.session.execute(
                select(KnowledgeNode)
                .where(
                    KnowledgeNode.ontology_schema_id == self.ontology_schema_id,
                    KnowledgeNode.entity_type == normalized_entity_type,
                    KnowledgeNode.entity_id == entity_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
        now = recorded_at or datetime.now(UTC)
        if node is None:
            node = KnowledgeNode(
                id=uuid.uuid4(),
                ontology_schema_id=self.ontology_schema_id,
                node_type=node_type,
                status=status,
                label=_nonblank(label, "label"),
                entity_type=normalized_entity_type,
                entity_id=entity_id,
                properties=dict(properties or {}),
                recorded_at=now,
                valid_from=valid_from,
                valid_to=valid_to,
                created_at=now,
                updated_at=now,
            )
            self.session.add(node)
        else:
            if node.node_type != node_type:
                raise ValueError("a projected entity cannot change ontology node type")
            if now >= node.recorded_at:
                node.status = status
                node.label = _nonblank(label, "label")
                node.properties = dict(properties or {})
                node.recorded_at = now
                if valid_from is not None or node.valid_from is None:
                    node.valid_from = valid_from
                node.valid_to = valid_to
                node.updated_at = datetime.now(UTC)
        self.session.flush()
        self.add_evidence(node_id=node.id, evidence=evidence)
        return node

    upsert_node = create_node

    def create_edge(
        self,
        *,
        source_node_id: uuid.UUID,
        predicate: str,
        target_node_id: uuid.UUID,
        evidence: EvidenceInput,
        status: str = "ACTIVE",
        properties: dict[str, Any] | None = None,
        recorded_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
    ) -> KnowledgeEdge:
        source = self._node(source_node_id)
        target = self._node(target_node_id)
        validate_v1_relation(predicate, source.node_type, target.node_type)
        if (
            source.ontology_schema_id != self.ontology_schema_id
            or target.ontology_schema_id != self.ontology_schema_id
        ):
            raise InvalidKnowledgeRelation("edge nodes must use the repository ontology schema")
        edge = self.session.execute(
            select(KnowledgeEdge)
            .where(
                KnowledgeEdge.ontology_schema_id == self.ontology_schema_id,
                KnowledgeEdge.source_node_id == source.id,
                KnowledgeEdge.predicate == predicate,
                KnowledgeEdge.target_node_id == target.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        now = recorded_at or datetime.now(UTC)
        if edge is None:
            edge = KnowledgeEdge(
                id=uuid.uuid4(),
                ontology_schema_id=self.ontology_schema_id,
                source_node_id=source.id,
                predicate=predicate,
                target_node_id=target.id,
                status=_status(status),
                properties=dict(properties or {}),
                recorded_at=now,
                valid_from=valid_from,
                valid_to=valid_to,
                created_at=now,
                updated_at=now,
            )
            self.session.add(edge)
        else:
            if now >= edge.recorded_at:
                edge.status = _status(status)
                edge.properties = dict(properties or {})
                edge.recorded_at = now
                edge.valid_from = valid_from
                edge.valid_to = valid_to
                edge.updated_at = datetime.now(UTC)
        self.session.flush()
        self.add_evidence(edge_id=edge.id, evidence=evidence)
        return edge

    upsert_edge = create_edge

    def add_evidence(
        self,
        *,
        evidence: EvidenceInput,
        node_id: uuid.UUID | None = None,
        edge_id: uuid.UUID | None = None,
    ) -> KnowledgeEvidence:
        if (node_id is None) == (edge_id is None):
            raise ValueError("evidence requires exactly one node_id or edge_id")
        source = self.register_source(evidence)
        owner_column = (
            KnowledgeEvidence.knowledge_node_id
            if node_id is not None
            else KnowledgeEvidence.knowledge_edge_id
        )
        owner_id = node_id or edge_id
        existing = self.session.execute(
            select(KnowledgeEvidence).where(
                KnowledgeEvidence.source_record_id == source.id,
                owner_column == owner_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        row = KnowledgeEvidence(
            id=uuid.uuid4(),
            ontology_schema_id=self.ontology_schema_id,
            knowledge_node_id=node_id,
            knowledge_edge_id=edge_id,
            source_record_id=source.id,
            source_type=source.entity_type[:50],
            source_id=source.entity_id,
            source_revision=source.revision_key[:100],
            permalink=source.permalink,
            excerpt_hash=source.content_hash,
            recorded_at=source.recorded_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def put_embedding(
        self,
        *,
        owner_type: KnowledgeOwnerType | str,
        owner_id: uuid.UUID,
        model_version: str,
        embedding: list[float] | tuple[float, ...],
        content: str | bytes | None = None,
        content_hash: str | None = None,
        model: str = BGE_M3_MODEL,
    ) -> KnowledgeEmbedding:
        selected_owner = KnowledgeOwnerType(owner_type).value
        if model != BGE_M3_MODEL:
            raise ValueError("ontology v1 embeddings require bge-m3")
        self._assert_owner(selected_owner, owner_id)
        values = validate_bge_m3_embedding(embedding)
        if (content is None) == (content_hash is None):
            raise ValueError("provide exactly one of content or content_hash")
        selected_hash = (
            content_sha256(content) if content is not None else _nonblank(content_hash, "content_hash", maximum=128)
        )
        selected_version = _nonblank(model_version, "model_version", maximum=200)
        embedding_id = self.session.scalar(
            insert(KnowledgeEmbedding)
            .values(
                id=uuid.uuid4(),
                owner_type=selected_owner,
                owner_id=owner_id,
                model=model,
                model_version=selected_version,
                content_hash=selected_hash,
                embedding=values,
            )
            .on_conflict_do_nothing(
                constraint="uq_knowledge_embeddings_owner_model_version_content"
            )
            .returning(KnowledgeEmbedding.id)
        )
        if embedding_id is None:
            return self.session.execute(
                select(KnowledgeEmbedding).where(
                    KnowledgeEmbedding.owner_type == selected_owner,
                    KnowledgeEmbedding.owner_id == owner_id,
                    KnowledgeEmbedding.model == model,
                    KnowledgeEmbedding.model_version == selected_version,
                    KnowledgeEmbedding.content_hash == selected_hash,
                )
            ).scalar_one()
        return self.session.get(KnowledgeEmbedding, embedding_id)  # type: ignore[return-value]

    def search_embeddings(
        self,
        query_embedding: list[float] | tuple[float, ...],
        *,
        limit: int = 10,
        owner_type: KnowledgeOwnerType | str | None = None,
        model_version: str | None = None,
    ) -> tuple[EmbeddingSearchResult, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        query = validate_bge_m3_embedding(query_embedding)
        distance = KnowledgeEmbedding.embedding.cosine_distance(query).label("distance")
        statement = select(KnowledgeEmbedding, distance).where(
            KnowledgeEmbedding.model == BGE_M3_MODEL
        )
        if owner_type is not None:
            statement = statement.where(
                KnowledgeEmbedding.owner_type == KnowledgeOwnerType(owner_type).value
            )
        if model_version is not None:
            statement = statement.where(
                KnowledgeEmbedding.model_version == model_version
            )
        rows = self.session.execute(statement.order_by(distance, KnowledgeEmbedding.id).limit(limit))
        return tuple(
            EmbeddingSearchResult(
                embedding_id=row.id,
                owner_type=row.owner_type,
                owner_id=row.owner_id,
                model_version=row.model_version,
                content_hash=row.content_hash,
                cosine_distance=float(raw_distance),
                score=1.0 - float(raw_distance),
            )
            for row, raw_distance in rows
        )

    def project_outbox_event(
        self,
        outbox_event_id: uuid.UUID,
        *,
        publish_outbox: bool = True,
    ) -> ProjectionResult:
        checkpoint = self.session.get(KnowledgeProjectionEvent, outbox_event_id)
        if checkpoint is not None:
            return self._checkpoint_result(checkpoint, replayed=True)
        event = self.session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.id == outbox_event_id)
            .with_for_update()
        ).scalar_one_or_none()
        if event is None:
            raise LookupError(f"outbox event not found: {outbox_event_id}")
        checkpoint = self.session.get(KnowledgeProjectionEvent, outbox_event_id)
        if checkpoint is not None:
            return self._checkpoint_result(checkpoint, replayed=True)

        latest_version = self.session.scalar(
            select(func.max(KnowledgeProjectionEvent.aggregate_version)).where(
                KnowledgeProjectionEvent.aggregate_type == event.aggregate_type,
                KnowledgeProjectionEvent.aggregate_id == event.aggregate_id,
            )
        )
        stale = latest_version is not None and latest_version >= event.aggregate_version
        if stale:
            nodes: tuple[KnowledgeNode, ...] = ()
            edges: tuple[KnowledgeEdge, ...] = ()
        elif event.aggregate_type == "TEST_CASE":
            nodes, edges = self._project_test_case(event)
        elif event.aggregate_type == "EXECUTION_ATTEMPT":
            nodes, edges = self._project_execution(event)
        elif event.aggregate_type == "ISSUE":
            nodes, edges = self._project_issue(event)
        else:
            raise UnsupportedProjectionEvent(
                f"unsupported knowledge projection aggregate: {event.aggregate_type}"
            )

        result = {
            "nodeIds": [str(node.id) for node in nodes],
            "edgeIds": [str(edge.id) for edge in edges],
            "stale": stale,
        }
        checkpoint = KnowledgeProjectionEvent(
            outbox_event_id=event.id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            aggregate_version=event.aggregate_version,
            result=result,
            processed_at=datetime.now(UTC),
        )
        self.session.add(checkpoint)
        if publish_outbox:
            event.status = OutboxStatus.PUBLISHED.value
            event.processed_at = checkpoint.processed_at
        self.session.flush()
        return self._checkpoint_result(checkpoint, replayed=False)

    def _project_test_case(
        self, event: OutboxEvent
    ) -> tuple[tuple[KnowledgeNode, ...], tuple[KnowledgeEdge, ...]]:
        payload = dict(event.payload)
        version_id = _payload_uuid(payload, "currentVersionId")
        version = self.session.get(TestCaseVersion, version_id)
        test_case = self.session.get(TestCase, event.aggregate_id)
        if version is None or test_case is None or version.test_case_id != test_case.id:
            raise LookupError("test-case outbox event references a missing ledger version")
        snapshot = {**payload, "summary": version.summary, "priority": version.priority}
        evidence = self._test_case_version_evidence(version, event.occurred_at)
        node = self.create_node(
            node_type="TEST_CASE",
            label=version.summary,
            status="RETRACTED" if payload.get("deletedAt") else "ACTIVE",
            entity_type="TEST_CASE",
            entity_id=test_case.id,
            properties=snapshot,
            recorded_at=event.occurred_at,
            evidence=evidence,
        )
        return (node,), ()

    def _project_execution(
        self, event: OutboxEvent
    ) -> tuple[tuple[KnowledgeNode, ...], tuple[KnowledgeEdge, ...]]:
        row = self.session.execute(
            select(ExecutionAttempt, CycleItem, TestCaseVersion)
            .join(CycleItem, CycleItem.id == ExecutionAttempt.cycle_item_id)
            .join(TestCaseVersion, TestCaseVersion.id == CycleItem.test_case_version_id)
            .where(ExecutionAttempt.id == event.aggregate_id)
        ).one_or_none()
        if row is None:
            raise LookupError("execution outbox event references a missing ledger attempt")
        attempt, item, version = row
        snapshot = dict(event.payload)
        evidence = self._ledger_evidence(
            entity_type="EXECUTION_ATTEMPT",
            entity_id=attempt.id,
            revision_key=str(event.aggregate_version),
            snapshot=snapshot,
            recorded_at=event.occurred_at,
        )
        case_node = self._project_case_reference(item.test_case_id, version, event.occurred_at)
        execution_node = self.create_node(
            node_type="EXECUTION",
            label=f"{version.summary} · {attempt.platform} {attempt.status}",
            entity_type="EXECUTION_ATTEMPT",
            entity_id=attempt.id,
            properties=snapshot,
            recorded_at=event.occurred_at,
            evidence=evidence,
        )
        edge = self.create_edge(
            source_node_id=execution_node.id,
            predicate="INSTANCE_OF",
            target_node_id=case_node.id,
            evidence=evidence,
            recorded_at=event.occurred_at,
        )
        return (case_node, execution_node), (edge,)

    def _project_issue(
        self, event: OutboxEvent
    ) -> tuple[tuple[KnowledgeNode, ...], tuple[KnowledgeEdge, ...]]:
        issue = self.session.get(Issue, event.aggregate_id)
        if issue is None:
            raise LookupError("issue outbox event references a missing ledger issue")
        payload = dict(event.payload)
        evidence = EvidenceInput.from_content(
            source_kind=KnowledgeSourceKind.SOURCE_REVISION,
            entity_type="OUTBOX_EVENT",
            entity_id=event.id,
            revision_key=str(event.aggregate_version),
            content=_canonical_json(payload),
            recorded_at=event.occurred_at,
        )
        issue_node = self.create_node(
            node_type="ISSUE",
            label=str(payload.get("summary") or issue.summary),
            status="RETRACTED" if payload.get("deletedAt") else "ACTIVE",
            entity_type="ISSUE",
            entity_id=issue.id,
            properties=payload,
            recorded_at=event.occurred_at,
            evidence=evidence,
        )
        nodes = [issue_node]
        edges: list[KnowledgeEdge] = []
        attempt_id = _optional_payload_uuid(payload, "sourceAttemptId")
        case_id = _optional_payload_uuid(payload, "sourceTestCaseId")
        if attempt_id is not None:
            attempt_event = self.session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_type == "EXECUTION_ATTEMPT",
                    OutboxEvent.aggregate_id == attempt_id,
                )
                .order_by(OutboxEvent.aggregate_version.desc())
                .limit(1)
            )
            if attempt_event is not None:
                attempt_nodes, _ = self._project_execution(attempt_event)
                execution_node = next(node for node in attempt_nodes if node.node_type == "EXECUTION")
                nodes.extend(node for node in attempt_nodes if node.id not in {item.id for item in nodes})
                edges.append(
                    self.create_edge(
                        source_node_id=issue_node.id,
                        predicate="FOUND_BY",
                        target_node_id=execution_node.id,
                        evidence=evidence,
                        recorded_at=event.occurred_at,
                    )
                )
        elif case_id is not None:
            test_case = self.session.get(TestCase, case_id)
            if test_case is not None:
                version = self.session.get(TestCaseVersion, test_case.current_version_id)
                if version is not None:
                    case_node = self._project_case_reference(case_id, version, event.occurred_at)
                    nodes.append(case_node)
                    edges.append(
                        self.create_edge(
                            source_node_id=issue_node.id,
                            predicate="FOUND_BY",
                            target_node_id=case_node.id,
                            evidence=evidence,
                            recorded_at=event.occurred_at,
                        )
                    )
        return tuple(nodes), tuple(edges)

    def _project_case_reference(
        self,
        test_case_id: uuid.UUID,
        version: TestCaseVersion,
        recorded_at: datetime,
    ) -> KnowledgeNode:
        evidence = self._test_case_version_evidence(version, recorded_at)
        return self.create_node(
            node_type="TEST_CASE",
            label=version.summary,
            entity_type="TEST_CASE",
            entity_id=test_case_id,
            properties={"currentVersionId": str(version.id), "summary": version.summary},
            recorded_at=recorded_at,
            evidence=evidence,
        )

    @staticmethod
    def _test_case_version_evidence(
        version: TestCaseVersion,
        recorded_at: datetime,
    ) -> EvidenceInput:
        """Ground every reference in the same immutable ledger-version body."""

        return KnowledgeRepository._ledger_evidence(
            entity_type="TEST_CASE_VERSION",
            entity_id=version.id,
            revision_key=str(version.version_number),
            snapshot={
                "testCaseId": str(version.test_case_id),
                "versionNumber": version.version_number,
                "status": version.status,
                "summary": version.summary,
                "precondition": version.precondition,
                "expectedResultMode": version.expected_result_mode,
                "expectedResult": version.expected_result,
                "priority": version.priority,
                "assigneeLabel": version.assignee_label,
                "sourceType": version.source_type,
                "sourceMetadata": version.source_metadata,
                "generationReason": version.generation_reason,
                "changeReason": version.change_reason,
                "restoredFromVersionId": (
                    str(version.restored_from_version_id)
                    if version.restored_from_version_id is not None
                    else None
                ),
                "copiedFromVersionId": (
                    str(version.copied_from_version_id)
                    if version.copied_from_version_id is not None
                    else None
                ),
            },
            recorded_at=recorded_at,
        )

    @staticmethod
    def _ledger_evidence(
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        revision_key: str,
        snapshot: dict[str, Any],
        recorded_at: datetime,
    ) -> EvidenceInput:
        return EvidenceInput.from_content(
            source_kind=KnowledgeSourceKind.LEDGER_ENTITY,
            entity_type=entity_type,
            entity_id=entity_id,
            revision_key=revision_key,
            content=_canonical_json(snapshot),
            recorded_at=recorded_at,
        )

    @staticmethod
    def _checkpoint_result(
        checkpoint: KnowledgeProjectionEvent, *, replayed: bool
    ) -> ProjectionResult:
        result = checkpoint.result
        return ProjectionResult(
            outbox_event_id=checkpoint.outbox_event_id,
            aggregate_type=checkpoint.aggregate_type,
            aggregate_id=checkpoint.aggregate_id,
            aggregate_version=checkpoint.aggregate_version,
            node_ids=tuple(uuid.UUID(value) for value in result.get("nodeIds", [])),
            edge_ids=tuple(uuid.UUID(value) for value in result.get("edgeIds", [])),
            replayed=replayed,
            stale=bool(result.get("stale", False)),
        )

    def _node(self, node_id: uuid.UUID) -> KnowledgeNode:
        node = self.session.get(KnowledgeNode, node_id)
        if node is None:
            raise LookupError(f"knowledge node not found: {node_id}")
        return node

    def _assert_owner(self, owner_type: str, owner_id: uuid.UUID) -> None:
        model = KnowledgeNode if owner_type == KnowledgeOwnerType.NODE.value else KnowledgeEdge
        if self.session.get(model, owner_id) is None:
            raise LookupError(f"knowledge {owner_type.lower()} not found: {owner_id}")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _payload_uuid(payload: dict[str, Any], key: str) -> uuid.UUID:
    value = _optional_payload_uuid(payload, key)
    if value is None:
        raise ValueError(f"outbox payload requires {key}")
    return value


def _optional_payload_uuid(payload: dict[str, Any], key: str) -> uuid.UUID | None:
    value = payload.get(key)
    return uuid.UUID(str(value)) if value else None


def _nonblank(value: str | None, field: str, *, maximum: int | None = None) -> str:
    clean = value.strip() if value is not None else ""
    if not clean:
        raise ValueError(f"{field} cannot be blank")
    if maximum is not None and len(clean) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return clean


def _identifier(value: str | None, field: str, *, maximum: int) -> str:
    clean = _nonblank(value, field, maximum=maximum)
    if not clean[0].isalpha() or not all(char.isupper() or char.isdigit() or char == "_" for char in clean):
        raise ValueError(f"{field} must be an uppercase identifier")
    return clean


def _node_type(value: str) -> str:
    if value not in NODE_TYPES:
        raise ValueError(f"unknown ontology v1 node type: {value}")
    return value


def _status(value: str) -> str:
    if value not in KNOWLEDGE_STATUSES:
        raise ValueError(f"unknown knowledge status: {value}")
    return value
