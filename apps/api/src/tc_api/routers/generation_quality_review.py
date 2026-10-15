"""Safe API surface for importing and reviewing T08 quality candidates."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Path, Query, Request, status

from tc_api.dependencies import QualityReviewRuntimeDependency, SessionDependency
from tc_api.models import ErrorResponse
from tc_api.quality_review_models import (
    CreateGenerationQualityDecisionRequest,
    GenerationQualityCandidatePage,
    GenerationQualityCandidateResource,
    GenerationQualityReviewedSnapshotResource,
    ImportGenerationQualityCandidateRequest,
    QualityReviewClassification,
)
from tc_backend.generation.quality_review import (
    GenerationQualityReviewService,
    QualityReviewError,
)

router = APIRouter(
    prefix="/api/generation-quality/review-candidates",
    tags=["generation-quality-review"],
)
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Reviewer token missing or invalid"},
    404: {"model": ErrorResponse, "description": "Candidate not found"},
    409: {
        "model": ErrorResponse,
        "description": "Review terminal, stale, or conflicting",
    },
    422: {
        "model": ErrorResponse,
        "description": "Strict candidate or request validation failed",
    },
    503: {"model": ErrorResponse, "description": "Review authority unavailable"},
}
IdempotencyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
ReviewerTokenHeader = Annotated[
    str | None,
    Header(alias="X-Quality-Reviewer-Token", min_length=1, max_length=512),
]


@router.post(
    "",
    operation_id="importGenerationQualityReviewCandidate",
    response_model=GenerationQualityCandidateResource,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def import_candidate(
    payload: ImportGenerationQualityCandidateRequest,
    request: Request,
    session: SessionDependency,
    runtime: QualityReviewRuntimeDependency,
    idempotency_header: IdempotencyHeader,
) -> GenerationQualityCandidateResource:
    del request  # X-Actor-* is intentionally ignored at this authority boundary.
    if payload.idempotencyKey != idempotency_header:
        raise QualityReviewError(
            "QUALITY_REVIEW_IDEMPOTENCY_HEADER_MISMATCH", status_code=422
        )
    service = _service(session, runtime)
    with session.begin():
        row = service.import_candidate(
            idempotency_key=payload.idempotencyKey,
            root_job_ids=payload.rootJobIds,
            capture_environment=payload.captureEnvironment,
            report_outputs={str(key): value for key, value in payload.reportOutputs.items()},
        )
        return GenerationQualityCandidateResource.model_validate(
            service.safe_detail(row)
        )


@router.get(
    "",
    operation_id="listGenerationQualityReviewCandidates",
    response_model=GenerationQualityCandidatePage,
    responses={422: ERROR_RESPONSES[422], 503: ERROR_RESPONSES[503]},
)
def list_candidates(
    session: SessionDependency,
    runtime: QualityReviewRuntimeDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[
        str | None, Query(pattern=r"^review-candidate:[a-f0-9]{64}$")
    ] = None,
    review_status: Annotated[
        Literal["UNREVIEWED", "PENDING_APPROVAL", "APPROVED", "REJECTED"] | None,
        Query(alias="status"),
    ] = None,
    classification: Annotated[QualityReviewClassification | None, Query()] = None,
    materialization: Annotated[Literal["COMPLETE"] | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=81)] = None,
) -> GenerationQualityCandidatePage:
    service = _service(session, runtime)
    rows, next_id = service.list_candidates(
        limit=limit,
        cursor_key=cursor,
        status_filter=review_status,
        classification_filter=classification,
        search=search,
    )
    items = [service.safe_detail(row) for row in rows]
    assert materialization in {None, "COMPLETE"}
    return GenerationQualityCandidatePage(
        items=[
            GenerationQualityCandidateResource.model_validate(item) for item in items
        ],
        next_cursor=next_id,
    )


@router.get(
    "/{candidateId}",
    operation_id="getGenerationQualityReviewCandidate",
    response_model=GenerationQualityCandidateResource,
    responses={
        404: ERROR_RESPONSES[404],
        422: ERROR_RESPONSES[422],
        503: ERROR_RESPONSES[503],
    },
)
def get_candidate(
    candidate_id: Annotated[str, Path(alias="candidateId")],
    session: SessionDependency,
    runtime: QualityReviewRuntimeDependency,
) -> GenerationQualityCandidateResource:
    service = _service(session, runtime)
    row = service.get_by_key(candidate_id)
    return GenerationQualityCandidateResource.model_validate(service.safe_detail(row))


@router.post(
    "/{candidateId}/decisions",
    operation_id="createGenerationQualityReviewDecision",
    response_model=GenerationQualityCandidateResource,
    responses=ERROR_RESPONSES,
)
def create_decision(
    candidate_id: Annotated[str, Path(alias="candidateId")],
    payload: CreateGenerationQualityDecisionRequest,
    request: Request,
    session: SessionDependency,
    runtime: QualityReviewRuntimeDependency,
    idempotency_header: IdempotencyHeader,
    reviewer_token: ReviewerTokenHeader = None,
) -> GenerationQualityCandidateResource:
    del request  # Never consume X-Actor-* as reviewer identity.
    if payload.idempotencyKey != idempotency_header:
        raise QualityReviewError(
            "QUALITY_REVIEW_IDEMPOTENCY_HEADER_MISMATCH", status_code=422
        )
    service = _service(session, runtime)
    with session.begin():
        # Authenticate before candidate lookup so the endpoint does not become
        # a candidate-existence oracle for unauthenticated callers.
        runtime.reviewer_authority.resolve(reviewer_token)
        row = service.decide(
            candidate_id,
            reviewer_token=reviewer_token,
            idempotency_key=payload.idempotencyKey,
            decision=payload.decision,
            expected_artifact_fingerprint=payload.expectedArtifactFingerprint,
            reason_code=payload.reasonCode,
        )
        return GenerationQualityCandidateResource.model_validate(
            service.safe_detail(row)
        )


@router.get(
    "/{candidateId}/reviewed-snapshot",
    operation_id="getGenerationQualityReviewedSnapshot",
    response_model=GenerationQualityReviewedSnapshotResource,
    responses={
        404: ERROR_RESPONSES[404],
        409: ERROR_RESPONSES[409],
        503: ERROR_RESPONSES[503],
    },
)
def get_reviewed_snapshot(
    candidate_id: Annotated[str, Path(alias="candidateId")],
    session: SessionDependency,
    runtime: QualityReviewRuntimeDependency,
) -> GenerationQualityReviewedSnapshotResource:
    service = _service(session, runtime)
    with session.begin():
        candidate = service.get_by_key(candidate_id)
        value = service.reviewed_snapshot(candidate.id)
        return GenerationQualityReviewedSnapshotResource.model_validate(value)


def _service(session: Any, runtime: Any) -> GenerationQualityReviewService:
    return GenerationQualityReviewService(
        session,
        reviewer_authority=runtime.reviewer_authority,
        source_seal_key=runtime.source_seal_key,
        source_seal_key_id=runtime.source_seal_key_id,
    )
