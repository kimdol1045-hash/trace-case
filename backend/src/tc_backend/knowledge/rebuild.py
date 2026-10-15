"""Safe, deterministic rebuilds of the PostgreSQL knowledge projection.

The outbox and domain ledgers are authoritative and are never reset here.
Only the six derived knowledge tables listed in ``PROJECTION_RESET_TABLES``
are cleared, inside the same transaction that replays every supported event.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from tc_backend.db.models import (
    KnowledgeEdge,
    KnowledgeEmbedding,
    KnowledgeEvidence,
    KnowledgeNode,
    KnowledgeProjectionEvent,
    KnowledgeSourceRecord,
    OutboxEvent,
)
from tc_backend.worker.knowledge_consumer import (
    KNOWLEDGE_REBUILD_LOCK_KEY,
    SUPPORTED_KNOWLEDGE_AGGREGATES,
    KnowledgeOutboxConsumer,
    SessionFactory,
    knowledge_event_order,
)

REBUILD_CONFIRMATION = "REBUILD-KNOWLEDGE-PROJECTION"
SNAPSHOT_SCHEMA_VERSION = "knowledge-projection-snapshot-v1"
PROJECTION_RESET_TABLES = (
    "knowledge_projection_events",
    "knowledge_embeddings",
    "knowledge_evidence",
    "knowledge_edges",
    "knowledge_nodes",
    "knowledge_source_records",
)
AUTHORITATIVE_LEDGER_TABLES = (
    "audit_logs",
    "folders",
    "folder_aliases",
    "tags",
    "work_items",
    "test_cases",
    "test_case_versions",
    "test_steps",
    "test_case_tags",
    "test_case_version_tags",
    "test_cycles",
    "cycle_items",
    "execution_attempts",
    "execution_step_results",
    "issues",
    "issue_steps",
    "issue_links",
    "comments",
    "slack_events",
    "slack_work_items",
    "slack_threads",
    "slack_messages",
    "slack_message_revisions",
    "slack_parse_results",
    "outbox_events",
)


class UnsafeKnowledgeRebuild(ValueError):
    """A rebuild was requested without the required preview/apply guard."""


class SourceLedgerChanged(RuntimeError):
    """The rebuild transaction unexpectedly changed an authoritative ledger."""


@dataclass(frozen=True, slots=True)
class CanonicalKnowledgeSnapshot:
    payload: dict[str, Any]
    digest: str
    counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
            "digest": self.digest,
            "counts": dict(self.counts),
            "projection": self.payload,
        }


@dataclass(frozen=True, slots=True)
class RebuildEvent:
    event_id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_version: int
    event_type: str
    occurred_at: datetime
    payload_digest: str

    def order_dict(self) -> dict[str, Any]:
        return {
            "eventId": str(self.event_id),
            "aggregateType": self.aggregate_type,
            "aggregateId": str(self.aggregate_id),
            "aggregateVersion": self.aggregate_version,
            "eventType": self.event_type,
            "occurredAt": _timestamp(self.occurred_at),
            "payloadDigest": self.payload_digest,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeRebuildPlan:
    events: tuple[RebuildEvent, ...]
    event_order_digest: str
    event_status_counts: dict[str, int]
    current_snapshot: CanonicalKnowledgeSnapshot
    source_ledger_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "dry-run",
            "confirmationRequired": REBUILD_CONFIRMATION,
            "writeScope": list(PROJECTION_RESET_TABLES),
            "preservedLedgers": list(AUTHORITATIVE_LEDGER_TABLES),
            "auxiliaryIndexIncluded": False,
            "transaction": {
                "atomic": True,
                "isolation": "REPEATABLE READ",
                "advisoryLockKey": KNOWLEDGE_REBUILD_LOCK_KEY,
            },
            "eventCount": len(self.events),
            "eventOrderDigest": self.event_order_digest,
            "eventStatusCounts": dict(sorted(self.event_status_counts.items())),
            "currentProjection": self.current_snapshot.as_dict(),
            "sourceLedgerDigest": self.source_ledger_digest,
            "events": [event.order_dict() for event in self.events],
        }


@dataclass(frozen=True, slots=True)
class KnowledgeRebuildReport:
    event_count: int
    event_order_digest: str
    before_digest: str
    after_snapshot: CanonicalKnowledgeSnapshot
    source_ledger_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "apply",
            "writeScope": list(PROJECTION_RESET_TABLES),
            "preservedLedgers": list(AUTHORITATIVE_LEDGER_TABLES),
            "auxiliaryIndexIncluded": False,
            "eventCount": self.event_count,
            "eventOrderDigest": self.event_order_digest,
            "beforeDigest": self.before_digest,
            "afterProjection": self.after_snapshot.as_dict(),
            "sourceLedgerDigest": self.source_ledger_digest,
        }


class KnowledgeProjectionRebuilder:
    """Preview and atomically rebuild the outbox-backed knowledge projection."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        consumer: KnowledgeOutboxConsumer | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.consumer = consumer

    def plan(self) -> KnowledgeRebuildPlan:
        """Read the complete deterministic plan without acquiring write locks."""

        with self.session_factory() as session:
            return _plan(session)

    async def apply(
        self,
        *,
        confirmation: str,
        expected_event_order_digest: str,
        expected_source_ledger_digest: str,
    ) -> KnowledgeRebuildReport:
        """Apply one atomic reset/replay after matching an explicit preview."""

        if confirmation != REBUILD_CONFIRMATION:
            raise UnsafeKnowledgeRebuild(
                f"confirmation must exactly equal {REBUILD_CONFIRMATION!r}"
            )
        if not expected_event_order_digest.strip():
            raise UnsafeKnowledgeRebuild("expected_event_order_digest is required")
        if not expected_source_ledger_digest.strip():
            raise UnsafeKnowledgeRebuild("expected_source_ledger_digest is required")
        if self.consumer is None:
            raise UnsafeKnowledgeRebuild(
                "apply requires the same configured KnowledgeOutboxConsumer providers "
                "used by ordinary incremental processing"
            )

        with self.session_factory() as session, session.begin():
            # This must be the first statement: the repeatable-read snapshot
            # freezes the source ledger view for both the preview check and replay.
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:lock_key, 0))"
                ),
                {"lock_key": KNOWLEDGE_REBUILD_LOCK_KEY},
            )
            locked_plan = _plan(session)
            if locked_plan.event_order_digest != expected_event_order_digest:
                raise UnsafeKnowledgeRebuild(
                    "outbox changed after preview; run a fresh dry-run and use its "
                    "eventOrderDigest"
                )
            if locked_plan.source_ledger_digest != expected_source_ledger_digest:
                raise UnsafeKnowledgeRebuild(
                    "source ledger changed after preview; run a fresh dry-run and use its "
                    "sourceLedgerDigest"
                )
            before_source_digest = locked_plan.source_ledger_digest
            before_digest = locked_plan.current_snapshot.digest
            _clear_projection(session)
            for event in locked_plan.events:
                await self.consumer.process_rebuild_event(session, event.event_id)
            session.flush()
            after_snapshot = canonical_knowledge_snapshot(session)
            after_source_digest = authoritative_ledger_digest(session)
            if after_source_digest != before_source_digest:
                raise SourceLedgerChanged(
                    "knowledge rebuild changed an authoritative source ledger"
                )
            return KnowledgeRebuildReport(
                event_count=len(locked_plan.events),
                event_order_digest=locked_plan.event_order_digest,
                before_digest=before_digest,
                after_snapshot=after_snapshot,
                source_ledger_digest=after_source_digest,
            )


def canonical_knowledge_snapshot(session: Session) -> CanonicalKnowledgeSnapshot:
    """Return a stable projection snapshot without generated row IDs/timestamps."""

    nodes = tuple(session.scalars(select(KnowledgeNode)))
    node_key_by_id = {node.id: _node_key(node) for node in nodes}
    node_rows = [
        {
            "key": node_key_by_id[node.id],
            "ontologySchemaId": str(node.ontology_schema_id),
            "nodeType": node.node_type,
            "status": node.status,
            "label": node.label,
            "properties": _normalize(node.properties),
            "validFrom": _timestamp(node.valid_from),
            "validTo": _timestamp(node.valid_to),
        }
        for node in nodes
    ]

    edges = tuple(session.scalars(select(KnowledgeEdge)))
    edge_key_by_id = {
        edge.id: _edge_key(edge, node_key_by_id=node_key_by_id) for edge in edges
    }
    edge_rows = [
        {
            "key": edge_key_by_id[edge.id],
            "ontologySchemaId": str(edge.ontology_schema_id),
            "source": node_key_by_id[edge.source_node_id],
            "predicate": edge.predicate,
            "target": node_key_by_id[edge.target_node_id],
            "status": edge.status,
            "properties": _normalize(edge.properties),
            "validFrom": _timestamp(edge.valid_from),
            "validTo": _timestamp(edge.valid_to),
        }
        for edge in edges
    ]

    sources = tuple(session.scalars(select(KnowledgeSourceRecord)))
    source_key_by_id = {source.id: _source_key(source) for source in sources}
    source_rows = [
        {
            "key": source_key_by_id[source.id],
            "sourceKind": source.source_kind,
            "entityType": source.entity_type,
            "entityId": str(source.entity_id),
            "revisionKey": source.revision_key,
            "contentHash": source.content_hash,
            "permalink": source.permalink,
        }
        for source in sources
    ]

    evidence_rows: list[dict[str, Any]] = []
    for evidence in session.scalars(select(KnowledgeEvidence)):
        if evidence.knowledge_node_id is not None:
            owner = {"type": "NODE", "key": node_key_by_id[evidence.knowledge_node_id]}
        else:
            assert evidence.knowledge_edge_id is not None
            owner = {"type": "EDGE", "key": edge_key_by_id[evidence.knowledge_edge_id]}
        evidence_rows.append(
            {
                "owner": owner,
                "source": source_key_by_id[evidence.source_record_id],
                "sourceType": evidence.source_type,
                "sourceId": str(evidence.source_id),
                "sourceRevision": evidence.source_revision,
                "permalink": evidence.permalink,
                "excerptHash": evidence.excerpt_hash,
            }
        )

    embedding_rows: list[dict[str, Any]] = []
    for embedding in session.scalars(select(KnowledgeEmbedding)):
        if embedding.owner_type == "NODE":
            owner_key = node_key_by_id[embedding.owner_id]
        elif embedding.owner_type == "EDGE":
            owner_key = edge_key_by_id[embedding.owner_id]
        else:
            owner_key = f"{embedding.owner_type}:{embedding.owner_id}"
        embedding_rows.append(
            {
                "ownerType": embedding.owner_type,
                "owner": owner_key,
                "model": embedding.model,
                "modelVersion": embedding.model_version,
                "contentHash": embedding.content_hash,
                "vectorDigest": _vector_digest(embedding.embedding),
            }
        )

    checkpoint_rows: list[dict[str, Any]] = []
    checkpoint_statement = select(KnowledgeProjectionEvent, OutboxEvent).join(
        OutboxEvent,
        OutboxEvent.id == KnowledgeProjectionEvent.outbox_event_id,
    )
    for checkpoint, event in session.execute(checkpoint_statement):
        result = dict(checkpoint.result)
        raw_node_ids = result.pop("nodeIds", [])
        raw_edge_ids = result.pop("edgeIds", [])
        checkpoint_rows.append(
            {
                "aggregateType": checkpoint.aggregate_type,
                "aggregateId": str(checkpoint.aggregate_id),
                "aggregateVersion": checkpoint.aggregate_version,
                "eventType": event.event_type,
                "nodes": sorted(
                    node_key_by_id[uuid.UUID(str(node_id))] for node_id in raw_node_ids
                ),
                "edges": sorted(
                    edge_key_by_id[uuid.UUID(str(edge_id))] for edge_id in raw_edge_ids
                ),
                "result": _normalize(result),
            }
        )

    payload = {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "nodes": _sort_rows(node_rows),
        "edges": _sort_rows(edge_rows),
        "sources": _sort_rows(source_rows),
        "evidence": _sort_rows(evidence_rows),
        "embeddings": _sort_rows(embedding_rows),
        "checkpoints": _sort_rows(checkpoint_rows),
    }
    counts = {
        "nodes": len(node_rows),
        "edges": len(edge_rows),
        "sources": len(source_rows),
        "evidence": len(evidence_rows),
        "embeddings": len(embedding_rows),
        "checkpoints": len(checkpoint_rows),
    }
    return CanonicalKnowledgeSnapshot(
        payload=payload,
        digest=_json_digest(payload),
        counts=counts,
    )


def authoritative_ledger_digest(session: Session) -> str:
    """Hash exact source-ledger rows so a rebuild can prove non-mutation."""

    digest = hashlib.sha256()
    for table_name in AUTHORITATIVE_LEDGER_TABLES:
        digest.update(table_name.encode("utf-8"))
        statement = text(
            # The identifier comes only from the module-level immutable allowlist above.
            f'SELECT to_jsonb(source_row)::text FROM "{table_name}" AS source_row '  # nosec B608
            "ORDER BY to_jsonb(source_row)::text"
        )
        for row_json in session.scalars(statement):
            encoded = str(row_json).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return digest.hexdigest()


def _plan(session: Session) -> KnowledgeRebuildPlan:
    statement = (
        select(OutboxEvent)
        .where(OutboxEvent.aggregate_type.in_(SUPPORTED_KNOWLEDGE_AGGREGATES))
        .order_by(*knowledge_event_order())
    )
    rows = tuple(session.scalars(statement))
    events = tuple(
        RebuildEvent(
            event_id=row.id,
            aggregate_type=row.aggregate_type,
            aggregate_id=row.aggregate_id,
            aggregate_version=row.aggregate_version,
            event_type=row.event_type,
            occurred_at=row.occurred_at,
            payload_digest=_json_digest(_normalize(row.payload)),
        )
        for row in rows
    )
    return KnowledgeRebuildPlan(
        events=events,
        event_order_digest=_json_digest([event.order_dict() for event in events]),
        event_status_counts=dict(Counter(row.status for row in rows)),
        current_snapshot=canonical_knowledge_snapshot(session),
        source_ledger_digest=authoritative_ledger_digest(session),
    )


def _clear_projection(session: Session) -> None:
    # Explicit models and order are intentional. No TRUNCATE, CASCADE, schema,
    # outbox, ontology, Slack, test-case, execution, or issue table is targeted.
    session.execute(delete(KnowledgeEmbedding))
    session.execute(delete(KnowledgeEvidence))
    session.execute(delete(KnowledgeEdge))
    session.execute(delete(KnowledgeNode))
    session.execute(delete(KnowledgeSourceRecord))
    session.execute(delete(KnowledgeProjectionEvent))
    session.flush()


def _node_key(node: KnowledgeNode) -> str:
    if node.entity_type is not None and node.entity_id is not None:
        return f"ENTITY:{node.entity_type}:{node.entity_id}"
    semantic = {
        "ontologySchemaId": str(node.ontology_schema_id),
        "nodeType": node.node_type,
        "status": node.status,
        "label": node.label,
        "properties": _normalize(node.properties),
    }
    return f"ANONYMOUS:{_json_digest(semantic)}"


def _edge_key(
    edge: KnowledgeEdge,
    *,
    node_key_by_id: Mapping[uuid.UUID, str],
) -> str:
    return _canonical_json(
        {
            "source": node_key_by_id[edge.source_node_id],
            "predicate": edge.predicate,
            "target": node_key_by_id[edge.target_node_id],
        }
    )


def _source_key(source: KnowledgeSourceRecord) -> str:
    return _canonical_json(
        {
            "sourceKind": source.source_kind,
            "entityType": source.entity_type,
            "entityId": str(source.entity_id),
            "revisionKey": source.revision_key,
        }
    )


def _vector_digest(values: Sequence[float]) -> str:
    normalized = [format(float(value), ".17g") for value in values]
    return _json_digest(normalized)


def _sort_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=_canonical_json)


def _json_digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalize(value: object) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical knowledge snapshots require finite floats")
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return _timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_normalize(item) for item in value]
    raise TypeError(f"unsupported canonical snapshot value: {type(value).__name__}")


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
