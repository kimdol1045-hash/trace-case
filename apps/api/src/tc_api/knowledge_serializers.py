"""Serialization of authoritative knowledge rows to Sprint 6 wire IDs."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from urllib.parse import quote, urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from tc_api.ids import encode_identifier
from tc_api.knowledge_models import (
    HybridSearchDegradationReasonValue,
    HybridSearchRecency,
    HybridSearchResultItem,
    HybridSearchResultReason,
    HybridSearchSignalValue,
    KnowledgeCitation,
    KnowledgeEdgeResource,
    KnowledgeEvidenceResource,
    KnowledgeNodeResource,
    KnowledgeSourceRef,
    KnowledgeStatusAuditResource,
)
from tc_backend.db.models import (
    AuditLog,
    CycleItem,
    ExecutionAttempt,
    Issue,
    KnowledgeEdge,
    KnowledgeEvidence,
    KnowledgeNode,
    OutboxEvent,
    TestCase,
    TestCaseVersion,
    TestCycle,
)
from tc_backend.services.knowledge_service import (
    HybridSearchMatch,
    node_content,
    resource_version,
    wire_source_type,
)


def knowledge_node_resource(
    session: Session, node: KnowledgeNode
) -> KnowledgeNodeResource:
    evidence = _evidence_for_node(session, node.id)
    primary = evidence[0]
    canonical = node.properties.get("canonicalKey")
    if not canonical:
        canonical = (
            f"{node.entity_type.lower()}:{node.entity_id}"
            if node.entity_type and node.entity_id
            else f"{node.node_type.lower()}:{node.id}"
        )
    return KnowledgeNodeResource(
        id=encode_identifier("knowledge", node.id),
        node_type=node.node_type,  # type: ignore[arg-type]
        canonical_key=str(canonical),
        label=node.label[:500],
        content=node_content(node),
        status=node.status,  # type: ignore[arg-type]
        valid_from=node.valid_from or node.recorded_at,
        valid_to=node.valid_to,
        primary_source=knowledge_source_ref(primary),
        evidence_ids=[encode_identifier("evidence", item.id) for item in evidence],
        resource_version=resource_version(node),
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def knowledge_edge_resource(
    session: Session, edge: KnowledgeEdge
) -> KnowledgeEdgeResource:
    evidence = _evidence_for_edge(session, edge.id)
    return KnowledgeEdgeResource(
        id=encode_identifier("knowledge_edge", edge.id),
        from_node_id=encode_identifier("knowledge", edge.source_node_id),
        predicate=edge.predicate,  # type: ignore[arg-type]
        to_node_id=encode_identifier("knowledge", edge.target_node_id),
        status=edge.status,  # type: ignore[arg-type]
        valid_from=edge.valid_from or edge.recorded_at,
        valid_to=edge.valid_to,
        primary_source=knowledge_source_ref(evidence[0]),
        evidence_ids=[encode_identifier("evidence", item.id) for item in evidence],
        resource_version=resource_version(edge),
        created_at=edge.created_at,
        updated_at=edge.updated_at,
    )


def knowledge_source_ref(evidence: KnowledgeEvidence) -> KnowledgeSourceRef:
    source_type = wire_source_type(evidence.source_type)
    revision_id = (
        encode_identifier("source_revision", evidence.source_record_id)
        if source_type in {"SLACK_MESSAGE_REVISION", "TEST_CASE_VERSION"}
        else None
    )
    return KnowledgeSourceRef(
        source_type=source_type,  # type: ignore[arg-type]
        source_entity_id=encode_identifier("source", evidence.source_id),
        source_revision_id=revision_id,
    )


def knowledge_evidence_resource(
    session: Session, evidence: KnowledgeEvidence
) -> KnowledgeEvidenceResource:
    if evidence.knowledge_node_id is not None:
        subject_type = "NODE"
        subject_id = encode_identifier("knowledge", evidence.knowledge_node_id)
        node = session.get(KnowledgeNode, evidence.knowledge_node_id)
        if node is None:
            raise LookupError("knowledge evidence references a missing node")
        excerpt = node_content(node)
        label = node.label
        deleted = bool(
            node.properties.get("classificationReason") == "source_message_deleted"
        )
    else:
        subject_type = "EDGE"
        assert evidence.knowledge_edge_id is not None
        subject_id = encode_identifier("knowledge_edge", evidence.knowledge_edge_id)
        edge = session.get(KnowledgeEdge, evidence.knowledge_edge_id)
        if edge is None:
            raise LookupError("knowledge evidence references a missing edge")
        excerpt = f"{edge.predicate} relation evidence"
        label = f"{edge.predicate} relation"
        deleted = False
    source = knowledge_source_ref(evidence)
    citation_uri = knowledge_citation_uri(session, evidence)
    return KnowledgeEvidenceResource(
        id=encode_identifier("evidence", evidence.id),
        subject_type=subject_type,  # type: ignore[arg-type]
        subject_id=subject_id,
        source=source,
        revision_ordinal=_revision_ordinal(evidence.source_revision),
        content_hash=_wire_hash(evidence.excerpt_hash),
        citation=KnowledgeCitation(
            source=source,
            label=label,
            uri=citation_uri,
            excerpt=None if deleted else excerpt[:2000],
            occurred_at=evidence.recorded_at,
            deleted=deleted,
        ),
        captured_at=evidence.created_at,
    )


def knowledge_audit_resource(
    audit: AuditLog, target_type: str
) -> KnowledgeStatusAuditResource:
    before = dict(audit.before or {})
    after = dict(audit.after or {})
    prefix = "knowledge" if target_type == "NODE" else "knowledge_edge"
    evidence_ids = [
        encode_identifier("evidence", uuid.UUID(str(value)))
        for value in after.get("evidenceIds", [])
    ]
    return KnowledgeStatusAuditResource(
        id=encode_identifier("knowledge_audit", audit.id),
        target_type=target_type,  # type: ignore[arg-type]
        target_id=encode_identifier(prefix, audit.entity_id),
        from_status=str(before["status"]),  # type: ignore[arg-type]
        to_status=str(after["status"]),  # type: ignore[arg-type]
        reason=audit.reason or "Knowledge lifecycle correction",
        evidence_ids=evidence_ids,
        changed_by=audit.actor_label or str(after.get("changedBy") or "knowledge-worker"),
        changed_at=audit.occurred_at,
        target_version=int(after["resourceVersion"]),
    )


def hybrid_item(
    session: Session,
    match: HybridSearchMatch,
    *,
    rank: int,
    degraded: bool,
    degradation_reasons: list[HybridSearchDegradationReasonValue],
    executed_at: datetime,
) -> HybridSearchResultItem:
    node = match.node
    evidence = match.evidence
    source = knowledge_source_ref(evidence)
    age_seconds = max(0, int((executed_at - node.recorded_at).total_seconds()))
    recency_score = 1.0 / (1.0 + age_seconds / 2_592_000)
    recency = HybridSearchRecency(
        basis_at=node.recorded_at,
        age_seconds=age_seconds,
        score=max(0.0, min(1.0, recency_score)),
    )
    signals = list(match.signals)
    return HybridSearchResultItem(
        rank=rank,
        node_id=encode_identifier("knowledge", node.id),
        node_type=node.node_type,  # type: ignore[arg-type]
        label=node.label[:500],
        content=node_content(node),
        score=match.score,
        reason=HybridSearchResultReason(
            summary=hybrid_reason_summary(
                rank=rank,
                score=match.score,
                signals=signals,  # type: ignore[arg-type]
                feature_distance=match.feature_distance,
                recency=recency,
            ),
            signals=signals,  # type: ignore[arg-type]
        ),
        status=node.status,  # type: ignore[arg-type]
        source=source,
        feature_distance=match.feature_distance,
        recency=recency,
        degraded=degraded,
        degradation_reasons=degradation_reasons,
        citation=KnowledgeCitation(
            source=source,
            label=node.label,
            uri=knowledge_citation_uri(session, evidence),
            excerpt=node_content(node)[:2000],
            occurred_at=evidence.recorded_at,
            deleted=False,
        ),
    )


def hybrid_reason_summary(
    *,
    rank: int,
    score: float,
    signals: list[HybridSearchSignalValue],
    feature_distance: int | None,
    recency: HybridSearchRecency,
) -> str:
    """Explain only the signals that the authoritative hybrid ranker returned."""

    details = ["typed graph candidate"]
    if "EXACT_TEXT" in signals:
        details.append("exact query text matched")
    if "VECTOR" in signals:
        details.append("vector similarity contributed")
    if "FEATURE_PROXIMITY" in signals:
        if feature_distance is not None:
            unit = "hop" if feature_distance == 1 else "hops"
            details.append(f"selected-feature distance was {feature_distance} {unit}")
    else:
        details.append("feature proximity did not contribute (no feature scope/path)")
    if "LIGHTRAG" in signals:
        details.append(
            "LightRAG availability was recorded as an auxiliary signal, not a numeric score component"
        )
    details.append(
        "recency contributed "
        f"{recency.score:.3f} from {recency.basis_at.isoformat()} "
        f"({recency.age_seconds} seconds old)"
    )
    return f"Rank {rank} with score {score:.3f}: " + "; ".join(details) + "."


def knowledge_citation_uri(
    session: Session, evidence: KnowledgeEvidence
) -> str | None:
    """Resolve a stable navigation URI without deriving routes from opaque wire IDs."""

    source_type = wire_source_type(evidence.source_type)
    if source_type == "SLACK_MESSAGE_REVISION":
        return evidence.permalink
    if source_type == "TEST_CASE_VERSION":
        case_row = session.execute(
            select(TestCaseVersion, TestCase)
            .join(TestCase, TestCase.id == TestCaseVersion.test_case_id)
            .where(TestCaseVersion.id == evidence.source_id)
        ).one_or_none()
        if case_row is None:
            return None
        _, test_case = case_row
        return f"/test-cases/{quote(test_case.case_key, safe='')}"
    if source_type == "EXECUTION_ATTEMPT":
        execution_row = session.execute(
            select(ExecutionAttempt, CycleItem, TestCycle, TestCase)
            .join(CycleItem, CycleItem.id == ExecutionAttempt.cycle_item_id)
            .join(TestCycle, TestCycle.id == CycleItem.cycle_id)
            .join(TestCase, TestCase.id == CycleItem.test_case_id)
            .where(ExecutionAttempt.id == evidence.source_id)
        ).one_or_none()
        if execution_row is None:
            return None
        attempt, _, cycle, test_case = execution_row
        query = urlencode(
            {
                "caseKey": test_case.case_key,
                "platform": attempt.platform,
                "attempt": attempt.attempt_no,
                "run": attempt.cycle_run_number,
            }
        )
        cycle_id = encode_identifier("cycle", cycle.id)
        return f"/cycles/{cycle_id}/execute?{query}"
    if source_type == "ISSUE_SNAPSHOT":
        event = session.get(OutboxEvent, evidence.source_id)
        if event is None or event.aggregate_type != "ISSUE":
            return None
        issue = session.get(Issue, event.aggregate_id)
        if issue is None:
            return None
        return f"/issues/{quote(_public_issue_key(issue.issue_key), safe='')}"
    return evidence.permalink


def scope_wire_ids(node: KnowledgeNode) -> list[str]:
    return [
        encode_identifier("knowledge", uuid.UUID(str(value)))
        for value in node.properties.get("scopeNodeIds", [])
    ]


def _evidence_for_node(session: Session, node_id: uuid.UUID) -> tuple[KnowledgeEvidence, ...]:
    from sqlalchemy import select

    rows = tuple(
        session.scalars(
            select(KnowledgeEvidence)
            .where(KnowledgeEvidence.knowledge_node_id == node_id)
            .order_by(KnowledgeEvidence.recorded_at, KnowledgeEvidence.id)
        )
    )
    if not rows:
        raise LookupError("knowledge node has no immutable evidence")
    return rows


def _evidence_for_edge(session: Session, edge_id: uuid.UUID) -> tuple[KnowledgeEvidence, ...]:
    from sqlalchemy import select

    rows = tuple(
        session.scalars(
            select(KnowledgeEvidence)
            .where(KnowledgeEvidence.knowledge_edge_id == edge_id)
            .order_by(KnowledgeEvidence.recorded_at, KnowledgeEvidence.id)
        )
    )
    if not rows:
        raise LookupError("knowledge edge has no immutable evidence")
    return rows


def _revision_ordinal(value: str | None) -> int:
    digits = re.sub(r"\D", "", value or "")
    return max(1, int(digits or "1"))


def _wire_hash(value: str) -> str:
    digest = value.removeprefix("sha256:").lower()
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        digest = hashlib.sha256(value.encode()).hexdigest()
    return f"sha256:{digest}"


def _public_issue_key(value: str) -> str:
    prefix, separator, number = value.rpartition("-")
    if separator and number.isdigit():
        return f"{prefix}-{int(number)}"
    return value
