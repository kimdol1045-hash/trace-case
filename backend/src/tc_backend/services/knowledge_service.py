"""Authoritative typed-graph queries and lifecycle corrections.

PostgreSQL rows are always the source of truth.  LightRAG contributes only a
degradation signal/candidate hint, while pgvector and the typed graph determine
which current resources are returned.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import (
    AuditLog,
    AuditSourceType,
    KnowledgeEdge,
    KnowledgeEvidence,
    KnowledgeNode,
    KnowledgeOwnerType,
)
from tc_backend.knowledge.embedding import EmbeddingError, EmbeddingProvider
from tc_backend.knowledge.lightrag import LightRAGIndex
from tc_backend.repositories.knowledge_repository import KnowledgeRepository

KnowledgeStatus = Literal["ACTIVE", "AMBIGUOUS", "SUPERSEDED", "RETRACTED"]
TargetType = Literal["NODE", "EDGE"]
Direction = Literal["OUTBOUND", "INBOUND", "BOTH"]
_STATUSES = frozenset({"ACTIVE", "AMBIGUOUS", "SUPERSEDED", "RETRACTED"})
_TYPED_GRAPH_WEIGHT = 0.04
_EXACT_TEXT_WEIGHT = 0.36
_VECTOR_WEIGHT = 0.28
_FEATURE_PROXIMITY_WEIGHT = 0.18
_LIGHTRAG_WEIGHT = 0.10
_RECENCY_WEIGHT = 0.04


class KnowledgeVersionConflict(RuntimeError):
    """The caller patched an out-of-date graph resource."""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected resource version {expected}, found {actual}")


@dataclass(frozen=True, slots=True)
class KnowledgeMutationResult:
    target: KnowledgeNode | KnowledgeEdge
    audit: AuditLog
    projection_job_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class HybridSearchMatch:
    node: KnowledgeNode
    evidence: KnowledgeEvidence
    score: float
    signals: tuple[str, ...]
    feature_distance: int | None


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    matches: tuple[HybridSearchMatch, ...]
    total: int
    next_cursor: str | None
    degraded: bool
    degradation_reasons: tuple[str, ...]
    executed_at: datetime


@dataclass(frozen=True, slots=True)
class ExplorerResult:
    root: KnowledgeNode
    nodes: tuple[tuple[KnowledgeNode, int], ...]
    edges: tuple[KnowledgeEdge, ...]
    evidence: tuple[KnowledgeEvidence, ...]
    truncated: bool
    next_cursor: str | None


class KnowledgeService:
    def __init__(
        self,
        session: Session,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        light_rag: LightRAGIndex | None = None,
    ) -> None:
        self.session = session
        self.embedding_provider = embedding_provider
        self.light_rag = light_rag

    def list_nodes(
        self,
        *,
        statuses: tuple[str, ...] = ("ACTIVE",),
        node_types: tuple[str, ...] = (),
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[tuple[KnowledgeNode, ...], int, str | None]:
        selected_statuses = _validate_statuses(statuses)
        offset = _decode_cursor(cursor)
        filters = [KnowledgeNode.status.in_(selected_statuses)]
        if node_types:
            filters.append(KnowledgeNode.node_type.in_(node_types))
        statement = select(KnowledgeNode).where(*filters)
        total = self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = tuple(
            self.session.scalars(
                statement.order_by(KnowledgeNode.updated_at.desc(), KnowledgeNode.id)
                .offset(offset)
                .limit(limit + 1)
            )
        )
        return rows[:limit], total, _encode_cursor(offset + limit) if len(rows) > limit else None

    def get_node(self, node_id: uuid.UUID) -> KnowledgeNode:
        node = self.session.get(KnowledgeNode, node_id)
        if node is None:
            raise LookupError(f"knowledge node not found: {node_id}")
        return node

    def get_edge(self, edge_id: uuid.UUID) -> KnowledgeEdge:
        edge = self.session.get(KnowledgeEdge, edge_id)
        if edge is None:
            raise LookupError(f"knowledge edge not found: {edge_id}")
        return edge

    def evidence_for(
        self, target_type: TargetType, target_id: uuid.UUID
    ) -> tuple[KnowledgeEvidence, ...]:
        owner = (
            KnowledgeEvidence.knowledge_node_id
            if target_type == "NODE"
            else KnowledgeEvidence.knowledge_edge_id
        )
        return tuple(
            self.session.scalars(
                select(KnowledgeEvidence)
                .where(owner == target_id)
                .order_by(KnowledgeEvidence.recorded_at, KnowledgeEvidence.id)
            )
        )

    def get_evidence(self, evidence_id: uuid.UUID) -> KnowledgeEvidence:
        evidence = self.session.get(KnowledgeEvidence, evidence_id)
        if evidence is None:
            raise LookupError(f"knowledge evidence not found: {evidence_id}")
        return evidence

    def patch(
        self,
        *,
        target_type: TargetType,
        target_id: uuid.UUID,
        expected_version: int,
        status: KnowledgeStatus | None,
        effective_at: datetime,
        reason: str,
        evidence_ids: tuple[uuid.UUID, ...],
        changed_by: str,
        scope_node_ids: tuple[uuid.UUID, ...] | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        validity_supplied: bool = False,
    ) -> KnowledgeMutationResult:
        if not reason.strip() or not changed_by.strip():
            raise ValueError("reason and changedBy must not be blank")
        if not evidence_ids:
            raise ValueError("at least one evidenceId is required")
        target: KnowledgeNode | KnowledgeEdge | None
        if target_type == "NODE":
            target = self.session.execute(
                select(KnowledgeNode).where(KnowledgeNode.id == target_id).with_for_update()
            ).scalar_one_or_none()
        else:
            target = self.session.execute(
                select(KnowledgeEdge).where(KnowledgeEdge.id == target_id).with_for_update()
            ).scalar_one_or_none()
        if target is None:
            raise LookupError(f"knowledge {target_type.lower()} not found: {target_id}")

        properties = dict(target.properties)
        current_version = int(properties.get("resourceVersion", 1))
        if current_version != expected_version:
            raise KnowledgeVersionConflict(expected_version, current_version)
        owned_evidence = self.evidence_for(target_type, target_id)
        owned_ids = {item.id for item in owned_evidence}
        if not set(evidence_ids).issubset(owned_ids):
            raise ValueError("evidenceIds must reference evidence owned by the target")
        if scope_node_ids is not None:
            if target_type != "NODE":
                raise ValueError("scopeNodeIds can only be patched on a knowledge node")
            found = set(
                self.session.scalars(
                    select(KnowledgeNode.id).where(KnowledgeNode.id.in_(scope_node_ids))
                )
            )
            if found != set(scope_node_ids):
                raise ValueError("scopeNodeIds contains an unknown knowledge node")
            properties["scopeNodeIds"] = [str(value) for value in scope_node_ids]

        before = _snapshot(target, current_version)
        if status is not None:
            if status not in _STATUSES:
                raise ValueError(f"unknown knowledge status: {status}")
            target.status = status
            if status == "ACTIVE":
                target.valid_to = None
            elif status in {"SUPERSEDED", "RETRACTED"} and not validity_supplied:
                target.valid_to = effective_at
        if validity_supplied:
            selected_from = valid_from if valid_from is not None else target.valid_from
            if valid_to is not None and selected_from is not None and valid_to <= selected_from:
                raise ValueError("validTo must be later than validFrom")
            if target.status == "ACTIVE" and valid_to is not None:
                raise ValueError("ACTIVE knowledge cannot have validTo")
            if target.status in {"SUPERSEDED", "RETRACTED"} and valid_to is None:
                raise ValueError("SUPERSEDED or RETRACTED knowledge requires validTo")
            target.valid_from = selected_from
            target.valid_to = valid_to

        next_version = current_version + 1
        properties["resourceVersion"] = next_version
        target.properties = properties
        target.updated_at = datetime.now(UTC)
        after = {
            **_snapshot(target, next_version),
            "evidenceIds": [str(item) for item in evidence_ids],
            "changedBy": changed_by.strip(),
        }
        audit_id = append_audit(
            self.session,
            workspace_id=DEFAULT_WORKSPACE_ID,
            entity_type=f"KNOWLEDGE_{target_type}",
            entity_id=target.id,
            action="KNOWLEDGE_PATCH",
            before=before,
            after=after,
            context=AuditContext(
                source_type=AuditSourceType.API,
                actor_label=changed_by.strip(),
                reason=reason.strip(),
                trace_id=f"knowledge_patch_{uuid.uuid4().hex}",
            ),
        )
        job_id = JobRepository(self.session).enqueue(
            job_type="KNOWLEDGE_REINDEX",
            payload={"targetType": target_type, "targetId": str(target.id)},
            idempotency_key=f"knowledge-reindex:{target_type}:{target.id}:{next_version}",
        )
        self.session.flush()
        audit = self.session.get(AuditLog, audit_id)
        assert audit is not None
        return KnowledgeMutationResult(target=target, audit=audit, projection_job_id=job_id)

    def audit_history(self, target_type: TargetType, target_id: uuid.UUID) -> tuple[AuditLog, ...]:
        return tuple(
            self.session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == f"KNOWLEDGE_{target_type}",
                    AuditLog.entity_id == target_id,
                    AuditLog.action == "KNOWLEDGE_PATCH",
                )
                .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
            )
        )

    async def hybrid_search(
        self,
        *,
        query: str,
        statuses: tuple[str, ...],
        source_types: tuple[str, ...],
        node_types: tuple[str, ...],
        feature_id: uuid.UUID | None,
        limit: int,
        cursor: str | None,
    ) -> HybridSearchResult:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("knowledge search query must not be empty")
        executed_at = datetime.now(UTC)
        selected_statuses = _validate_statuses(statuses)
        offset = _decode_cursor(cursor)
        degradation: list[str] = []

        light_rag_hints: tuple[uuid.UUID, ...] = ()
        if self.light_rag is None:
            degradation.append("LIGHTRAG_UNAVAILABLE")
        else:
            light_result = await self.light_rag.search(normalized_query)
            if light_result.should_fallback:
                degradation.append("LIGHTRAG_UNAVAILABLE")
            else:
                candidate = light_result.require_value()
                light_rag_hints = _uuid_hints(candidate.postgres_record_ids)
        light_rag_node_ids = self._resolve_light_rag_node_ids(light_rag_hints)

        vector_scores: dict[uuid.UUID, float] = {}
        if self.embedding_provider is None:
            degradation.append("VECTOR_UNAVAILABLE")
        else:
            try:
                batch = await self.embedding_provider.embed((normalized_query,))
                for item in KnowledgeRepository(self.session).search_embeddings(
                    batch.vectors[0], owner_type=KnowledgeOwnerType.NODE, limit=100
                ):
                    score = max(0.0, min(1.0, item.score))
                    vector_scores[item.owner_id] = max(score, vector_scores.get(item.owner_id, 0.0))
            except EmbeddingError:
                degradation.append("VECTOR_UNAVAILABLE")

        graph_distances = self._graph_distances(
            feature_id,
            selected_statuses,
            max_depth=5,
            as_of=executed_at,
        )
        candidates = self._hybrid_candidates(
            query=normalized_query,
            statuses=selected_statuses,
            node_types=node_types,
            as_of=executed_at,
            candidate_ids=(set(vector_scores) | set(graph_distances) | set(light_rag_node_ids)),
        )

        # Every exact feature hit establishes proximity. Merging all roots by
        # minimum distance avoids relying on database row order.
        feature_hits = tuple(
            sorted(
                (
                    node.id
                    for node in candidates
                    if node.node_type == "FEATURE" and _is_exact_text_match(node, normalized_query)
                ),
                key=lambda value: value.hex,
            )
        )
        if feature_id is None and feature_hits:
            for root_id in feature_hits:
                for node_id, root_distance in self._graph_distances(
                    root_id,
                    selected_statuses,
                    max_depth=5,
                    as_of=executed_at,
                ).items():
                    graph_distances[node_id] = min(
                        root_distance, graph_distances.get(node_id, root_distance)
                    )
            candidates = self._hybrid_candidates(
                query=normalized_query,
                statuses=selected_statuses,
                node_types=node_types,
                as_of=executed_at,
                candidate_ids=(set(vector_scores) | set(graph_distances) | set(light_rag_node_ids)),
            )

        matches: list[HybridSearchMatch] = []
        evidence_by_node = self._primary_evidence_by_node(
            tuple(node.id for node in candidates), source_types=source_types
        )
        for node in candidates:
            primary = evidence_by_node.get(node.id)
            if primary is None:
                continue
            exact = _is_exact_text_match(node, normalized_query)
            vector = vector_scores.get(node.id)
            distance = graph_distances.get(node.id)
            light_rag = node.id in light_rag_node_ids
            age_seconds = max(0, int((executed_at - node.recorded_at).total_seconds()))
            recency = 1.0 / (1.0 + age_seconds / 2_592_000)
            matches.append(
                HybridSearchMatch(
                    node=node,
                    evidence=primary,
                    score=_hybrid_score(
                        exact=exact,
                        vector_score=vector,
                        feature_distance=distance,
                        light_rag=light_rag,
                        recency_score=recency,
                    ),
                    signals=_hybrid_signals(
                        exact=exact,
                        vector_score=vector,
                        feature_distance=distance,
                        light_rag=light_rag,
                    ),
                    feature_distance=distance,
                )
            )
        matches.sort(key=lambda item: (-item.score, item.node.id.hex))
        page = matches[offset : offset + limit]
        next_cursor = _encode_cursor(offset + limit) if offset + limit < len(matches) else None
        reasons = tuple(dict.fromkeys(degradation))
        return HybridSearchResult(
            matches=tuple(page),
            total=len(matches),
            next_cursor=next_cursor,
            degraded=bool(reasons),
            degradation_reasons=reasons,
            executed_at=executed_at,
        )

    def _resolve_light_rag_node_ids(
        self, record_ids: tuple[uuid.UUID, ...]
    ) -> frozenset[uuid.UUID]:
        if not record_ids:
            return frozenset()
        evidence_node_ids = self.session.scalars(
            select(KnowledgeEvidence.knowledge_node_id).where(
                KnowledgeEvidence.id.in_(record_ids),
                KnowledgeEvidence.knowledge_node_id.is_not(None),
            )
        )
        # Direct IDs still pass through the filtered PostgreSQL node query and
        # every final result must also resolve a PostgreSQL evidence row.
        return frozenset((*record_ids, *(item for item in evidence_node_ids if item is not None)))

    def _hybrid_candidates(
        self,
        *,
        query: str,
        statuses: tuple[str, ...],
        node_types: tuple[str, ...],
        as_of: datetime,
        candidate_ids: set[uuid.UUID],
    ) -> tuple[KnowledgeNode, ...]:
        filters: list[ColumnElement[bool]] = [KnowledgeNode.status.in_(statuses)]
        if node_types:
            filters.append(KnowledgeNode.node_type.in_(node_types))
        if "ACTIVE" in statuses:
            filters.append(_current_lifecycle_filter(KnowledgeNode, as_of))

        escaped = _escape_like(query.casefold())
        pattern = f"%{escaped}%"
        text_match = or_(
            func.lower(KnowledgeNode.label).like(pattern, escape="\\"),
            func.lower(func.coalesce(KnowledgeNode.properties["content"].as_string(), "")).like(
                pattern, escape="\\"
            ),
            func.lower(func.coalesce(KnowledgeNode.properties["summary"].as_string(), "")).like(
                pattern, escape="\\"
            ),
        )
        retrieval_match: ColumnElement[bool]
        if candidate_ids:
            retrieval_match = or_(text_match, KnowledgeNode.id.in_(candidate_ids))
        else:
            retrieval_match = text_match
        return tuple(
            self.session.scalars(
                select(KnowledgeNode).where(*filters, retrieval_match).order_by(KnowledgeNode.id)
            )
        )

    def _primary_evidence_by_node(
        self,
        node_ids: tuple[uuid.UUID, ...],
        *,
        source_types: tuple[str, ...],
    ) -> dict[uuid.UUID, KnowledgeEvidence]:
        if not node_ids:
            return {}
        source_filter = set(source_types)
        selected: dict[uuid.UUID, KnowledgeEvidence] = {}
        for evidence in self.session.scalars(
            select(KnowledgeEvidence)
            .where(KnowledgeEvidence.knowledge_node_id.in_(node_ids))
            .order_by(
                KnowledgeEvidence.knowledge_node_id,
                KnowledgeEvidence.recorded_at,
                KnowledgeEvidence.id,
            )
        ):
            node_id = evidence.knowledge_node_id
            if node_id is None or node_id in selected:
                continue
            if source_filter and _wire_source_type(evidence.source_type) not in source_filter:
                continue
            selected[node_id] = evidence
        return selected

    def explore(
        self,
        *,
        root_node_id: uuid.UUID,
        direction: Direction,
        predicates: tuple[str, ...],
        statuses: tuple[str, ...],
        max_depth: int,
        limit: int,
        cursor: str | None,
    ) -> ExplorerResult:
        root = self.get_node(root_node_id)
        selected_statuses = _validate_statuses(statuses)
        offset = _decode_cursor(cursor)
        distances, edges = self._walk(
            root_node_id,
            direction=direction,
            predicates=predicates,
            statuses=selected_statuses,
            max_depth=max_depth,
        )
        ordered_ids = sorted(distances, key=lambda value: (distances[value], value.hex))
        selected_ids = ordered_ids[offset : offset + limit]
        nodes_by_id = {
            node.id: node
            for node in self.session.scalars(
                select(KnowledgeNode).where(KnowledgeNode.id.in_(selected_ids))
            )
        }
        selected_set = set(selected_ids)
        selected_edges = tuple(
            edge
            for edge in edges
            if edge.source_node_id in selected_set and edge.target_node_id in selected_set
        )
        evidence = tuple(
            self.session.scalars(
                select(KnowledgeEvidence)
                .where(
                    or_(
                        KnowledgeEvidence.knowledge_node_id.in_(selected_set),
                        KnowledgeEvidence.knowledge_edge_id.in_(
                            tuple(edge.id for edge in selected_edges)
                        ),
                    )
                )
                .order_by(KnowledgeEvidence.recorded_at, KnowledgeEvidence.id)
            )
        )
        truncated = offset + limit < len(ordered_ids)
        return ExplorerResult(
            root=root,
            nodes=tuple((nodes_by_id[item], distances[item]) for item in selected_ids),
            edges=selected_edges,
            evidence=evidence,
            truncated=truncated,
            next_cursor=_encode_cursor(offset + limit) if truncated else None,
        )

    def enqueue_projection_job(self, *, limit: int, idempotency_key: str) -> uuid.UUID:
        return JobRepository(self.session).enqueue(
            job_type="KNOWLEDGE_PROJECTION",
            payload={"limit": limit},
            idempotency_key=idempotency_key,
        )

    def enqueue_reindex_job(
        self, *, node_ids: tuple[uuid.UUID, ...], idempotency_key: str
    ) -> uuid.UUID:
        for node_id in node_ids:
            self.get_node(node_id)
        return JobRepository(self.session).enqueue(
            job_type="KNOWLEDGE_REINDEX",
            payload={"nodeIds": [str(value) for value in node_ids]},
            idempotency_key=idempotency_key,
        )

    def _graph_distances(
        self,
        root_id: uuid.UUID | None,
        statuses: tuple[str, ...],
        *,
        max_depth: int,
        as_of: datetime | None = None,
    ) -> dict[uuid.UUID, int]:
        if root_id is None:
            return {}
        distances, _ = self._walk(
            root_id,
            direction="BOTH",
            predicates=(),
            statuses=statuses,
            max_depth=max_depth,
            as_of=as_of,
        )
        return distances

    def _walk(
        self,
        root_id: uuid.UUID,
        *,
        direction: Direction,
        predicates: tuple[str, ...],
        statuses: tuple[str, ...],
        max_depth: int,
        as_of: datetime | None = None,
    ) -> tuple[dict[uuid.UUID, int], tuple[KnowledgeEdge, ...]]:
        root = self.get_node(root_id)
        if root.status not in statuses:
            raise ValueError("root knowledge status is outside the requested status scope")
        edge_filters: list[ColumnElement[bool]] = [KnowledgeEdge.status.in_(statuses)]
        if predicates:
            edge_filters.append(KnowledgeEdge.predicate.in_(predicates))
        if as_of is not None and "ACTIVE" in statuses:
            if not _is_current_lifecycle(root, as_of):
                raise ValueError("root knowledge node is outside the current lifecycle window")
            edge_filters.append(_current_lifecycle_filter(KnowledgeEdge, as_of))
        all_edges = tuple(self.session.scalars(select(KnowledgeEdge).where(*edge_filters)))
        node_filters: list[ColumnElement[bool]] = [KnowledgeNode.status.in_(statuses)]
        if as_of is not None and "ACTIVE" in statuses:
            node_filters.append(_current_lifecycle_filter(KnowledgeNode, as_of))
        active_nodes = set(self.session.scalars(select(KnowledgeNode.id).where(*node_filters)))
        distances = {root_id: 0}
        queue: deque[uuid.UUID] = deque((root_id,))
        used_edges: dict[uuid.UUID, KnowledgeEdge] = {}
        while queue:
            current = queue.popleft()
            depth = distances[current]
            if depth >= max_depth:
                continue
            for edge in all_edges:
                adjacent: uuid.UUID | None = None
                if direction in {"OUTBOUND", "BOTH"} and edge.source_node_id == current:
                    adjacent = edge.target_node_id
                elif direction in {"INBOUND", "BOTH"} and edge.target_node_id == current:
                    adjacent = edge.source_node_id
                if adjacent is None or adjacent not in active_nodes:
                    continue
                used_edges[edge.id] = edge
                if adjacent not in distances:
                    distances[adjacent] = depth + 1
                    queue.append(adjacent)
        return distances, tuple(used_edges.values())


def node_content(node: KnowledgeNode) -> str:
    return _node_content(node)


def resource_version(resource: KnowledgeNode | KnowledgeEdge) -> int:
    return int(resource.properties.get("resourceVersion", 1))


def wire_source_type(source_type: str) -> str:
    return _wire_source_type(source_type)


def _node_content(node: KnowledgeNode) -> str:
    value = node.properties.get("content") or node.properties.get("summary") or node.label
    return str(value).strip() or node.label


def _searchable_node_text(node: KnowledgeNode) -> str:
    return "\n".join(
        (
            node.label,
            str(node.properties.get("content") or ""),
            str(node.properties.get("summary") or ""),
        )
    )


def _is_exact_text_match(node: KnowledgeNode, query: str) -> bool:
    return query.casefold() in _searchable_node_text(node).casefold()


def _hybrid_score(
    *,
    exact: bool,
    vector_score: float | None,
    feature_distance: int | None,
    light_rag: bool,
    recency_score: float,
) -> float:
    """Stable additive rank: graph .04, exact .36, vector .28, proximity .18,
    LightRAG .10, and recency .04. Duplicate hints never add weight.
    """

    score = _TYPED_GRAPH_WEIGHT
    if exact:
        score += _EXACT_TEXT_WEIGHT
    if vector_score is not None:
        score += _VECTOR_WEIGHT * max(0.0, min(1.0, vector_score))
    if feature_distance is not None:
        score += _FEATURE_PROXIMITY_WEIGHT / (feature_distance + 1)
    if light_rag:
        score += _LIGHTRAG_WEIGHT
    score += _RECENCY_WEIGHT * max(0.0, min(1.0, recency_score))
    return round(max(0.0, min(1.0, score)), 12)


def _hybrid_signals(
    *,
    exact: bool,
    vector_score: float | None,
    feature_distance: int | None,
    light_rag: bool,
) -> tuple[str, ...]:
    signals = ["TYPED_GRAPH"]
    if exact:
        signals.append("EXACT_TEXT")
    if vector_score is not None:
        signals.append("VECTOR")
    if feature_distance is not None:
        signals.append("FEATURE_PROXIMITY")
    if light_rag:
        signals.append("LIGHTRAG")
    signals.append("RECENCY")
    return tuple(signals)


def _current_lifecycle_filter(
    model: type[KnowledgeNode] | type[KnowledgeEdge], as_of: datetime
) -> ColumnElement[bool]:
    return or_(
        model.status != "ACTIVE",
        and_(
            or_(model.valid_from.is_(None), model.valid_from <= as_of),
            model.valid_to.is_(None),
        ),
    )


def _is_current_lifecycle(resource: KnowledgeNode | KnowledgeEdge, as_of: datetime) -> bool:
    if resource.status != "ACTIVE":
        return True
    return (
        resource.valid_from is None or resource.valid_from <= as_of
    ) and resource.valid_to is None


def _uuid_hints(values: tuple[str, ...]) -> tuple[uuid.UUID, ...]:
    selected: dict[uuid.UUID, None] = {}
    for value in values:
        try:
            selected.setdefault(uuid.UUID(value), None)
        except (AttributeError, ValueError):
            continue
    return tuple(selected)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _wire_source_type(source_type: str) -> str:
    return {
        "SLACK_MESSAGE_REVISION": "SLACK_MESSAGE_REVISION",
        "TEST_CASE_VERSION": "TEST_CASE_VERSION",
        "EXECUTION_ATTEMPT": "EXECUTION_ATTEMPT",
        "OUTBOX_EVENT": "ISSUE_SNAPSHOT",
    }.get(source_type, "MANUAL")


def _snapshot(target: KnowledgeNode | KnowledgeEdge, version: int) -> dict[str, object]:
    return {
        "status": target.status,
        "validFrom": target.valid_from.isoformat() if target.valid_from else None,
        "validTo": target.valid_to.isoformat() if target.valid_to else None,
        "scopeNodeIds": list(target.properties.get("scopeNodeIds", [])),
        "resourceVersion": version,
    }


def _validate_statuses(statuses: tuple[str, ...]) -> tuple[str, ...]:
    if not statuses or len(statuses) != len(set(statuses)):
        raise ValueError("statuses must contain unique values")
    if not set(statuses).issubset(_STATUSES):
        raise ValueError("statuses contains an unknown knowledge status")
    return statuses


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"offset": offset}).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        offset = payload["offset"]
        if not isinstance(offset, int) or offset < 0:
            raise ValueError
        return offset
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("knowledge cursor is invalid") from exc
