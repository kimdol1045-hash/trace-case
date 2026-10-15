"""Sprint 6 typed knowledge graph, hybrid search, explorer and job endpoints."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, status

from tc_api.dependencies import (
    KnowledgeEmbeddingDependency,
    KnowledgeLightRAGDependency,
    SessionDependency,
)
from tc_api.ids import decode_identifier, encode_identifier
from tc_api.knowledge_models import (
    HybridSearchRequest,
    HybridSearchResponse,
    KnowledgeEdgeResource,
    KnowledgeEvidencePage,
    KnowledgeJobResponse,
    KnowledgeNodeDetail,
    KnowledgeNodePage,
    KnowledgeNodeResource,
    KnowledgeNodeTypeValue,
    KnowledgeProjectionJobRequest,
    KnowledgeReindexJobRequest,
    KnowledgeStatusAuditPage,
    KnowledgeStatusPatchRequest,
    KnowledgeStatusPatchResponse,
    KnowledgeStatusValue,
    OntologyExplorerNode,
    OntologyExplorerRequest,
    OntologyExplorerResponse,
)
from tc_api.knowledge_serializers import (
    hybrid_item,
    knowledge_audit_resource,
    knowledge_edge_resource,
    knowledge_evidence_resource,
    knowledge_node_resource,
    scope_wire_ids,
)
from tc_api.models import ErrorResponse
from tc_backend.db.errors import ResourceNotFoundError, VersionConflictError
from tc_backend.db.models import BackgroundJob
from tc_backend.db.models import KnowledgeEdge, KnowledgeNode
from tc_backend.services.knowledge_service import (
    KnowledgeService,
    KnowledgeVersionConflict,
)
from tc_backend.observability import MetricsRegistry

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Knowledge resource not found"},
    409: {"model": ErrorResponse, "description": "Knowledge resource version conflict"},
    422: {"model": ErrorResponse, "description": "Invalid knowledge request"},
}


@router.get(
    "/nodes",
    operation_id="listKnowledgeNodes",
    response_model=KnowledgeNodePage,
    responses=ERROR_RESPONSES,
)
def list_knowledge_nodes(
    session: SessionDependency,
    statuses: Annotated[list[KnowledgeStatusValue], Query()] = ["ACTIVE"],
    node_types: Annotated[
        list[KnowledgeNodeTypeValue] | None, Query(alias="nodeTypes")
    ] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> KnowledgeNodePage:
    service = KnowledgeService(session)
    rows, total, next_cursor = service.list_nodes(
        statuses=tuple(statuses),
        node_types=tuple(node_types or ()),
        cursor=cursor,
        limit=limit,
    )
    return KnowledgeNodePage(
        items=[knowledge_node_resource(session, row) for row in rows],
        next_cursor=next_cursor,
        total=total,
    )


@router.get(
    "/nodes/{nodeId}",
    operation_id="getKnowledgeNode",
    response_model=KnowledgeNodeDetail,
    responses=ERROR_RESPONSES,
)
def get_knowledge_node(
    node_id: Annotated[str, Path(alias="nodeId")], session: SessionDependency
) -> KnowledgeNodeDetail:
    resolved = _knowledge_id(node_id, "nodeId")
    service = KnowledgeService(session)
    node = _node(service, resolved)
    evidence = service.evidence_for("NODE", node.id)
    return KnowledgeNodeDetail(
        node=knowledge_node_resource(session, node),
        scope_node_ids=scope_wire_ids(node),
        evidence=[knowledge_evidence_resource(session, item) for item in evidence],
    )


@router.get(
    "/nodes/{nodeId}/evidence",
    operation_id="listKnowledgeNodeEvidence",
    response_model=KnowledgeEvidencePage,
    responses=ERROR_RESPONSES,
)
def list_knowledge_node_evidence(
    node_id: Annotated[str, Path(alias="nodeId")], session: SessionDependency
) -> KnowledgeEvidencePage:
    resolved = _knowledge_id(node_id, "nodeId")
    service = KnowledgeService(session)
    _node(service, resolved)
    rows = service.evidence_for("NODE", resolved)
    return KnowledgeEvidencePage(
        items=[knowledge_evidence_resource(session, row) for row in rows],
        total=len(rows),
    )


@router.patch(
    "/status",
    operation_id="patchKnowledgeStatus",
    response_model=KnowledgeStatusPatchResponse,
    responses=ERROR_RESPONSES,
)
def patch_knowledge_status(
    payload: KnowledgeStatusPatchRequest, session: SessionDependency
) -> KnowledgeStatusPatchResponse:
    prefix = "knowledge" if payload.targetType == "NODE" else "knowledge_edge"
    target_id = decode_identifier(payload.targetId, prefix=prefix, field="targetId")
    evidence_ids = tuple(
        decode_identifier(value, prefix="evidence", field="evidenceIds")
        for value in payload.evidenceIds
    )
    scope_ids = (
        tuple(_knowledge_id(value, "scopeNodeIds") for value in payload.scopeNodeIds)
        if payload.scopeNodeIds is not None
        else None
    )
    service = KnowledgeService(session)
    try:
        with session.begin():
            result = service.patch(
                target_type=payload.targetType,
                target_id=target_id,
                expected_version=payload.expectedVersion,
                status=payload.status,
                effective_at=payload.effectiveAt,
                reason=payload.reason,
                evidence_ids=evidence_ids,
                changed_by=payload.changedBy,
                scope_node_ids=scope_ids,
                valid_from=payload.validFrom,
                valid_to=payload.validTo,
                validity_supplied=bool(
                    {"validFrom", "validTo"}.intersection(payload.model_fields_set)
                ),
            )
    except KnowledgeVersionConflict as exc:
        raise VersionConflictError(
            f"KNOWLEDGE_{payload.targetType}", target_id, exc.expected, exc.actual
        ) from exc
    except LookupError as exc:
        raise ResourceNotFoundError(f"KNOWLEDGE_{payload.targetType}", target_id) from exc
    target: KnowledgeNodeResource | KnowledgeEdgeResource
    if isinstance(result.target, KnowledgeNode):
        target = knowledge_node_resource(session, result.target)
    else:
        assert isinstance(result.target, KnowledgeEdge)
        target = knowledge_edge_resource(session, result.target)
    return KnowledgeStatusPatchResponse(
        target=target,
        audit=knowledge_audit_resource(result.audit, payload.targetType),
        projection_queued=True,
    )


@router.get(
    "/nodes/{nodeId}/audit",
    operation_id="listKnowledgeNodeAudit",
    response_model=KnowledgeStatusAuditPage,
    responses=ERROR_RESPONSES,
)
def list_knowledge_node_audit(
    node_id: Annotated[str, Path(alias="nodeId")], session: SessionDependency
) -> KnowledgeStatusAuditPage:
    resolved = _knowledge_id(node_id, "nodeId")
    service = KnowledgeService(session)
    _node(service, resolved)
    rows = service.audit_history("NODE", resolved)
    return KnowledgeStatusAuditPage(
        items=[knowledge_audit_resource(row, "NODE") for row in rows],
        total=len(rows),
    )


@router.post(
    "/search",
    operation_id="searchKnowledge",
    response_model=HybridSearchResponse,
    responses=ERROR_RESPONSES,
)
async def search_knowledge(
    payload: HybridSearchRequest,
    request: Request,
    session: SessionDependency,
    embedding_provider: KnowledgeEmbeddingDependency,
    light_rag: KnowledgeLightRAGDependency,
) -> HybridSearchResponse:
    feature_id = (
        _knowledge_id(payload.featureId, "featureId")
        if payload.featureId is not None
        else None
    )
    service = KnowledgeService(
        session, embedding_provider=embedding_provider, light_rag=light_rag
    )
    result = await service.hybrid_search(
        query=payload.query,
        statuses=tuple(payload.statuses),
        source_types=tuple(payload.sourceTypes),
        node_types=tuple(payload.nodeTypes),
        feature_id=feature_id,
        limit=payload.limit,
        cursor=payload.cursor,
    )
    rank_offset = _hybrid_rank_offset(payload.cursor)
    degradation_reasons = list(result.degradation_reasons)
    metrics = getattr(request.app.state, "metrics", None)
    if result.degraded and isinstance(metrics, MetricsRegistry):
        for reason in result.degradation_reasons:
            metrics.inc("tc_knowledge_degraded_responses_total", reason=reason)
    return HybridSearchResponse(
        query=payload.query,
        items=[
            hybrid_item(
                session,
                match,
                rank=rank_offset + index,
                degraded=result.degraded,
                degradation_reasons=degradation_reasons,  # type: ignore[arg-type]
                executed_at=result.executed_at,
            )
            for index, match in enumerate(result.matches, start=1)
        ],
        next_cursor=result.next_cursor,
        total=result.total,
        degraded=result.degraded,
        degradation_reasons=degradation_reasons,  # type: ignore[arg-type]
        executed_at=result.executed_at,
    )


@router.post(
    "/explorer",
    operation_id="exploreKnowledgeOntology",
    response_model=OntologyExplorerResponse,
    responses=ERROR_RESPONSES,
)
def explore_knowledge(
    payload: OntologyExplorerRequest, session: SessionDependency
) -> OntologyExplorerResponse:
    root_id = _knowledge_id(payload.rootNodeId, "rootNodeId")
    service = KnowledgeService(session)
    try:
        result = service.explore(
            root_node_id=root_id,
            direction=payload.direction,
            predicates=tuple(payload.predicates),
            statuses=tuple(payload.statuses),
            max_depth=payload.maxDepth,
            limit=payload.limit,
            cursor=payload.cursor,
        )
    except LookupError as exc:
        raise ResourceNotFoundError("KNOWLEDGE_NODE", root_id) from exc
    return OntologyExplorerResponse(
        root_node_id=payload.rootNodeId,
        nodes=[
            OntologyExplorerNode(node=knowledge_node_resource(session, node), depth=depth)
            for node, depth in result.nodes
        ],
        edges=[knowledge_edge_resource(session, edge) for edge in result.edges],
        evidence=[knowledge_evidence_resource(session, row) for row in result.evidence],
        truncated=result.truncated,
        next_cursor=result.next_cursor,
    )


@router.post(
    "/jobs/projection",
    operation_id="createKnowledgeProjectionJob",
    response_model=KnowledgeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ERROR_RESPONSES,
)
def create_projection_job(
    payload: KnowledgeProjectionJobRequest, session: SessionDependency
) -> KnowledgeJobResponse:
    with session.begin():
        job_id = KnowledgeService(session).enqueue_projection_job(
            limit=payload.limit, idempotency_key=payload.idempotencyKey
        )
    return _job_response(session, job_id)


@router.post(
    "/jobs/reindex",
    operation_id="createKnowledgeReindexJob",
    response_model=KnowledgeJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=ERROR_RESPONSES,
)
def create_reindex_job(
    payload: KnowledgeReindexJobRequest, session: SessionDependency
) -> KnowledgeJobResponse:
    node_ids = tuple(_knowledge_id(value, "nodeIds") for value in payload.nodeIds)
    try:
        with session.begin():
            job_id = KnowledgeService(session).enqueue_reindex_job(
                node_ids=node_ids, idempotency_key=payload.idempotencyKey
            )
    except LookupError as exc:
        raise ResourceNotFoundError("KNOWLEDGE_NODE", node_ids[0]) from exc
    return _job_response(session, job_id)


def _knowledge_id(value: str, field: str) -> uuid.UUID:
    return decode_identifier(value, prefix="knowledge", field=field)


def _hybrid_rank_offset(cursor: str | None) -> int:
    """Read the service's opaque offset cursor solely to preserve absolute result ranks."""

    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        offset = payload["offset"]
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError
        return offset
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("knowledge cursor is invalid") from exc


def _node(service: KnowledgeService, node_id: uuid.UUID):  # type: ignore[no-untyped-def]
    try:
        return service.get_node(node_id)
    except LookupError as exc:
        raise ResourceNotFoundError("KNOWLEDGE_NODE", node_id) from exc


def _job_response(session: SessionDependency, job_id: uuid.UUID) -> KnowledgeJobResponse:
    job = session.get(BackgroundJob, job_id)
    if job is None:
        raise ResourceNotFoundError("KNOWLEDGE_JOB", job_id)
    return KnowledgeJobResponse(
        id=encode_identifier("knowledge_job", job.id),
        job_type=job.job_type,  # type: ignore[arg-type]
        status=job.status,  # type: ignore[arg-type]
    )
