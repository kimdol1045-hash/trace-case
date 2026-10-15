"""Manual generation job lifecycle endpoints."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Query, Request, status
from sqlalchemy import and_, or_, select

from tc_api.dependencies import (
    GenerationApplyFaultDependency,
    GenerationRunnerDependency,
    SessionDependency,
)
from tc_api.errors import GenerationCursorError
from tc_api.generation_models import (
    ApplyGenerationJobRequest,
    ApplyGenerationJobResponse,
    CreateGenerationCasePlacementRequest,
    CreateGenerationParentPlacementRequest,
    CreateGenerationJobRequest,
    ExecuteGenerationJobRequest,
    GenerationApplyPlanResource,
    GenerationComparisonResponse,
    GenerationJobDetailResource,
    GenerationJobPageResponse,
    GenerationReconstructionResponse,
    GenerationJobStatusValue,
    ReuseGenerationCasePlacementRequest,
    ReuseGenerationParentPlacementRequest,
    RetryGenerationJobRequest,
)
from tc_api.generation_serializers import (
    generation_apply_receipt,
    generation_audit_snapshot,
    generation_comparison,
    generation_job_detail,
    generation_job_resource,
    generation_reconstruction,
)
from tc_api.idempotency import execute_idempotent
from tc_api.ids import decode_identifier
from tc_api.ids import encode_identifier
from tc_api.models import ErrorResponse
from tc_api.serializers import audit_context
from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import ResourceNotFoundError
from tc_backend.db.models import GenerationJob, SlackWorkItem
from tc_backend.generation.application import (
    CategoryApplyPlacement,
    CreateCaseApplyPlacement,
    CreateParentApplyPlacement,
    GenerationApplicationService,
    GenerationApplyCommand,
    GenerationStoredResultInvalid,
    ReuseCaseApplyPlacement,
    ReuseParentApplyPlacement,
)
from tc_backend.generation.lifecycle import GenerationJobLifecycle
from tc_backend.generation.retry_history import RetryHistoryError
from tc_backend.repositories.generation_repository import (
    GenerationRepository,
    UngroundedGenerationContext,
)

router = APIRouter(tags=["generation-jobs"])
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Generation job not found"},
    409: {"model": ErrorResponse, "description": "State or idempotency conflict"},
    422: {"model": ErrorResponse, "description": "Validation or cursor error"},
    500: {"model": ErrorResponse, "description": "Apply transaction rolled back"},
    503: {"model": ErrorResponse, "description": "Generation setup required"},
}
APPLY_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Apply resource not found"},
    409: {
        "model": ErrorResponse,
        "description": "Apply state, result, or placement conflict",
    },
    422: {"model": ErrorResponse, "description": "Malformed apply request"},
    500: {"model": ErrorResponse, "description": "Apply transaction rolled back"},
}
COMPARISON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Generation job not found"},
    409: {"model": ErrorResponse, "description": "Sealed comparison data is invalid"},
}
RECONSTRUCTION_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Generation job not found"},
}
IdempotencyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
ApplyIdempotencyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]


@router.post(
    "/api/generation-jobs",
    operation_id="createGenerationJob",
    response_model=GenerationJobDetailResource,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_generation_job(
    payload: CreateGenerationJobRequest,
    request: Request,
    session: SessionDependency,
    idempotency_header: IdempotencyHeader = None,
) -> GenerationJobDetailResource:
    key = _idempotency_key(payload.idempotencyKey, idempotency_header)
    slack_work_item_id = decode_identifier(
        payload.slackWorkItemId,
        prefix="slack_work_item",
        field="slackWorkItemId",
    )
    context = audit_context(request, idempotency_key=key)
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation="CREATE_GENERATION_JOB",
            payload={"slackWorkItemId": payload.slackWorkItemId},
            response_model=GenerationJobDetailResource,
            context=context,
            audit_action="CREATE_GENERATION_JOB",
            action=lambda audit: _create(session, slack_work_item_id, audit),
        )


@router.get(
    "/api/generation-jobs",
    operation_id="listGenerationJobs",
    response_model=GenerationJobPageResponse,
    responses=ERROR_RESPONSES,
)
def list_generation_jobs(
    session: SessionDependency,
    slack_work_item_id: Annotated[
        str | None, Query(alias="slackWorkItemId", min_length=1, max_length=128)
    ] = None,
    statuses: Annotated[list[GenerationJobStatusValue] | None, Query()] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> GenerationJobPageResponse:
    if statuses is not None and len(statuses) != len(set(statuses)):
        raise ValueError("statuses must contain unique values")
    work_item_uuid = (
        decode_identifier(
            slack_work_item_id,
            prefix="slack_work_item",
            field="slackWorkItemId",
        )
        if slack_work_item_id is not None
        else None
    )
    fingerprint = _filter_fingerprint(slack_work_item_id, statuses or [])
    filters: list[Any] = []
    if work_item_uuid is not None:
        filters.append(GenerationJob.slack_work_item_id == work_item_uuid)
    if statuses:
        filters.append(GenerationJob.status.in_(statuses))
    if cursor is not None:
        created_at, job_id = _decode_cursor(cursor, fingerprint)
        filters.append(
            or_(
                GenerationJob.created_at < created_at,
                and_(
                    GenerationJob.created_at == created_at,
                    GenerationJob.id < job_id,
                ),
            )
        )
    rows = list(
        session.scalars(
            select(GenerationJob)
            .where(*filters)
            .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc())
            .limit(limit + 1)
        )
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        next_cursor = _encode_cursor(page[-1], fingerprint)
    return GenerationJobPageResponse(
        items=[generation_job_resource(job) for job in page],
        next_cursor=next_cursor,
    )


@router.get(
    "/api/generation-jobs/{jobId}",
    operation_id="getGenerationJob",
    response_model=GenerationJobDetailResource,
    responses=ERROR_RESPONSES,
)
def get_generation_job(
    job_id: Annotated[str, Path(alias="jobId")],
    session: SessionDependency,
) -> GenerationJobDetailResource:
    repository = GenerationRepository(session)
    decoded = decode_identifier(job_id, prefix="generation_job", field="jobId")
    return generation_job_detail(_load_context(repository, decoded))


@router.get(
    "/api/generation-jobs/{jobId}/comparison",
    operation_id="getGenerationJobComparison",
    response_model=GenerationComparisonResponse,
    responses=COMPARISON_ERROR_RESPONSES,
)
def get_generation_job_comparison(
    job_id: Annotated[str, Path(alias="jobId")],
    session: SessionDependency,
) -> GenerationComparisonResponse:
    decoded = decode_identifier(job_id, prefix="generation_job", field="jobId")
    repository = GenerationRepository(session)
    try:
        persisted = repository.load_comparison(decoded)
    except LookupError as error:
        raise ResourceNotFoundError("GENERATION_JOB", decoded) from error
    except UngroundedGenerationContext as error:
        raise GenerationStoredResultInvalid(
            decoded, "CONTEXT", "SEALED_CONTEXT_INVALID"
        ) from error
    return generation_comparison(persisted)


@router.get(
    "/api/generation-jobs/{jobId}/reconstruction",
    operation_id="getGenerationJobReconstruction",
    response_model=GenerationReconstructionResponse,
    responses=RECONSTRUCTION_ERROR_RESPONSES,
)
def get_generation_job_reconstruction(
    job_id: Annotated[str, Path(alias="jobId")],
    session: SessionDependency,
) -> GenerationReconstructionResponse:
    decoded = decode_identifier(job_id, prefix="generation_job", field="jobId")
    try:
        reconstructed = GenerationRepository(session).load_reconstruction(decoded)
    except LookupError as error:
        raise ResourceNotFoundError("GENERATION_JOB", decoded) from error
    return generation_reconstruction(reconstructed)


@router.post(
    "/api/generation-jobs/{jobId}/execute",
    operation_id="executeGenerationJob",
    response_model=GenerationJobDetailResource,
    responses=ERROR_RESPONSES,
)
def execute_generation_job(
    job_id: Annotated[str, Path(alias="jobId")],
    payload: ExecuteGenerationJobRequest,
    request: Request,
    session: SessionDependency,
    runner: GenerationRunnerDependency,
    idempotency_header: IdempotencyHeader = None,
) -> GenerationJobDetailResource:
    decoded = decode_identifier(job_id, prefix="generation_job", field="jobId")
    key = _idempotency_key(payload.idempotencyKey, idempotency_header)
    context = audit_context(request, idempotency_key=key)
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation="EXECUTE_GENERATION_JOB",
            payload={"jobId": job_id},
            response_model=GenerationJobDetailResource,
            context=context,
            audit_action="EXECUTE_GENERATION_JOB",
            action=lambda audit: _execute(
                session,
                decoded,
                runner,
                audit,
                public_web_base_url=getattr(
                    request.app.state, "public_web_base_url", None
                ),
            ),
        )


@router.post(
    "/api/generation-jobs/{jobId}/retry",
    operation_id="retryGenerationJob",
    response_model=GenerationJobDetailResource,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def retry_generation_job(
    job_id: Annotated[str, Path(alias="jobId")],
    payload: RetryGenerationJobRequest,
    request: Request,
    session: SessionDependency,
    runner: GenerationRunnerDependency,
    idempotency_header: IdempotencyHeader = None,
) -> GenerationJobDetailResource:
    decoded = decode_identifier(job_id, prefix="generation_job", field="jobId")
    key = _idempotency_key(payload.idempotencyKey, idempotency_header)
    context = audit_context(request, idempotency_key=key, reason=payload.reason)
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation="RETRY_GENERATION_JOB",
            payload={
                "jobId": job_id,
                "contextMode": payload.contextMode,
                "reason": payload.reason,
            },
            response_model=GenerationJobDetailResource,
            context=context,
            audit_action="RETRY_GENERATION_JOB",
            action=lambda audit: _retry(session, decoded, runner, audit),
        )


@router.post(
    "/api/generation-jobs/{jobId}/apply",
    operation_id="applyGenerationJob",
    response_model=ApplyGenerationJobResponse,
    status_code=status.HTTP_201_CREATED,
    responses=APPLY_ERROR_RESPONSES,
)
def apply_generation_job(
    job_id: Annotated[str, Path(alias="jobId")],
    payload: ApplyGenerationJobRequest,
    request: Request,
    session: SessionDependency,
    fault: GenerationApplyFaultDependency,
    idempotency_header: ApplyIdempotencyHeader,
) -> ApplyGenerationJobResponse:
    decoded = decode_identifier(job_id, prefix="generation_job", field="jobId")
    key = _idempotency_key(payload.idempotencyKey, idempotency_header)
    command = _apply_command(decoded, payload)
    context = audit_context(request, idempotency_key=key)
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation="APPLY_GENERATION_JOB",
            payload={
                "jobId": job_id,
                "expectedContextDigest": payload.expectedContextDigest,
                "expectedResultDigest": payload.expectedResultDigest,
                "placement": payload.placement.model_dump(mode="json"),
            },
            response_model=ApplyGenerationJobResponse,
            context=context,
            audit_action="APPLY_GENERATION_JOB",
            action=lambda audit: generation_apply_receipt(
                GenerationApplicationService(
                    session,
                    fault=fault,
                    public_web_base_url=getattr(
                        request.app.state, "public_web_base_url", None
                    ),
                ).apply(
                    command, audit=audit
                )
            ),
        )


@router.get(
    "/api/generation-jobs/{jobId}/apply-plan",
    operation_id="getGenerationJobApplyPlan",
    response_model=GenerationApplyPlanResource,
    responses=APPLY_ERROR_RESPONSES,
)
def get_generation_job_apply_plan(
    job_id: Annotated[str, Path(alias="jobId")],
    session: SessionDependency,
) -> GenerationApplyPlanResource:
    decoded = decode_identifier(job_id, prefix="generation_job", field="jobId")
    with session.begin():
        draft = GenerationApplicationService(session).plan(decoded)
        return GenerationApplyPlanResource.model_validate(
            {
                "generationJobId": encode_identifier("generation_job", draft.job_id),
                "jobStatus": "SUCCEEDED",
                "expectedContextDigest": draft.context_digest,
                "expectedResultDigest": draft.result_digest,
                "placement": draft.placement,
            }
        )


def _create(
    session: Any,
    slack_work_item_id: uuid.UUID,
    audit: AuditContext,
) -> GenerationJobDetailResource:
    if session.get(SlackWorkItem, slack_work_item_id) is None:
        raise ResourceNotFoundError("SLACK_WORK_ITEM", slack_work_item_id)
    persisted = GenerationRepository(session).create_next_job_with_context(
        slack_work_item_id=slack_work_item_id
    )
    append_audit(
        session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        entity_type="GENERATION_JOB",
        entity_id=persisted.job.id,
        action="CREATE_GENERATION_JOB",
        before=None,
        after=generation_audit_snapshot(persisted.job),
        context=audit,
    )
    return generation_job_detail(persisted)


def _execute(
    session: Any,
    job_id: uuid.UUID,
    runner: Any,
    audit: AuditContext,
    *,
    public_web_base_url: str | None,
) -> GenerationJobDetailResource:
    repository = GenerationRepository(session)
    try:
        source_job = repository.get_job(job_id, for_update=True)
        if source_job.generation_trigger == "AUTOMATIC":
            raise RetryHistoryError("GENERATION_AUTOMATIC_OPERATION_FORBIDDEN")
        before = generation_audit_snapshot(source_job)
    except LookupError as error:
        raise ResourceNotFoundError("GENERATION_JOB", job_id) from error
    persisted = GenerationJobLifecycle(
        repository,
        runner,
        public_web_base_url=public_web_base_url,
    ).execute(job_id)
    append_audit(
        session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        entity_type="GENERATION_JOB",
        entity_id=persisted.job.id,
        action="EXECUTE_GENERATION_JOB",
        before=before,
        after=generation_audit_snapshot(persisted.job),
        context=audit,
    )
    return generation_job_detail(persisted)


def _retry(
    session: Any,
    source_job_id: uuid.UUID,
    runner: Any,
    audit: AuditContext,
) -> GenerationJobDetailResource:
    repository = GenerationRepository(session)
    try:
        source_job = repository.get_job(source_job_id, for_update=True)
        if source_job.generation_trigger == "AUTOMATIC":
            raise RetryHistoryError("GENERATION_AUTOMATIC_OPERATION_FORBIDDEN")
        before = generation_audit_snapshot(source_job)
    except LookupError as error:
        raise ResourceNotFoundError("GENERATION_JOB", source_job_id) from error
    persisted = GenerationJobLifecycle(repository, runner).retry(source_job_id)
    append_audit(
        session,
        workspace_id=DEFAULT_WORKSPACE_ID,
        entity_type="GENERATION_JOB",
        entity_id=persisted.job.id,
        action="RETRY_GENERATION_JOB",
        before=before,
        after=generation_audit_snapshot(persisted.job),
        context=audit,
    )
    return generation_job_detail(persisted)


def _load_context(repository: GenerationRepository, job_id: uuid.UUID) -> Any:
    try:
        return repository.load_context(job_id)
    except LookupError as error:
        raise ResourceNotFoundError("GENERATION_JOB", job_id) from error


def _apply_command(
    job_id: uuid.UUID,
    payload: ApplyGenerationJobRequest,
) -> GenerationApplyCommand:
    category = payload.placement.category
    raw_parent = payload.placement.parent
    parent: ReuseParentApplyPlacement | CreateParentApplyPlacement
    if isinstance(raw_parent, ReuseGenerationParentPlacementRequest):
        parent = ReuseParentApplyPlacement(
            work_item_id=decode_identifier(
                raw_parent.workItemId,
                prefix="work_item",
                field="placement.parent.workItemId",
            ),
            expected_resource_version=raw_parent.expectedResourceVersion,
            expected_category_id=decode_identifier(
                raw_parent.expectedCategoryId,
                prefix="folder",
                field="placement.parent.expectedCategoryId",
            ),
            method=raw_parent.method,
            score=raw_parent.score,
        )
    elif isinstance(raw_parent, CreateGenerationParentPlacementRequest):
        parent = CreateParentApplyPlacement(
            expected_work_item_key=raw_parent.expectedWorkItemKey,
            expected_summary=raw_parent.expectedSummary,
            category_id=decode_identifier(
                raw_parent.categoryId,
                prefix="folder",
                field="placement.parent.categoryId",
            ),
        )
    else:  # pragma: no cover - Pydantic discriminates the closed union
        raise AssertionError("unknown generation parent placement")
    cases: list[CreateCaseApplyPlacement | ReuseCaseApplyPlacement] = []
    for raw_case in payload.placement.cases:
        if isinstance(raw_case, ReuseGenerationCasePlacementRequest):
            cases.append(
                ReuseCaseApplyPlacement(
                    generated_case_index=raw_case.generatedCaseIndex,
                    exact_content_key=raw_case.exactContentKey,
                    test_case_id=decode_identifier(
                        raw_case.testCaseId,
                        prefix="case",
                        field="placement.cases.testCaseId",
                    ),
                    expected_resource_version=raw_case.expectedResourceVersion,
                    expected_current_version_id=decode_identifier(
                        raw_case.expectedCurrentVersionId,
                        prefix="case_version",
                        field="placement.cases.expectedCurrentVersionId",
                    ),
                    expected_current_version=raw_case.expectedCurrentVersion,
                    match_reason=raw_case.matchReason,
                )
            )
        elif isinstance(raw_case, CreateGenerationCasePlacementRequest):
            cases.append(
                CreateCaseApplyPlacement(
                    generated_case_index=raw_case.generatedCaseIndex,
                    exact_content_key=raw_case.exactContentKey,
                )
            )
        else:  # pragma: no cover - Pydantic discriminates the closed union
            raise AssertionError("unknown generation case placement")
    return GenerationApplyCommand(
        job_id=job_id,
        idempotency_key=payload.idempotencyKey,
        expected_context_digest=payload.expectedContextDigest,
        expected_result_digest=payload.expectedResultDigest,
        category=CategoryApplyPlacement(
            category_id=decode_identifier(
                category.categoryId,
                prefix="folder",
                field="placement.category.categoryId",
            ),
            expected_resource_version=category.expectedResourceVersion,
            method=category.method,
            score=category.score,
        ),
        parent=parent,
        cases=tuple(cases),
    )


def _idempotency_key(body_key: str, header_key: str | None) -> str:
    if header_key is not None and header_key != body_key:
        raise ValueError("Idempotency-Key header must match body idempotencyKey")
    return body_key


def _filter_fingerprint(
    slack_work_item_id: str | None, statuses: list[GenerationJobStatusValue]
) -> str:
    canonical = json.dumps(
        {
            "slackWorkItemId": slack_work_item_id,
            "statuses": sorted(statuses),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _encode_cursor(job: GenerationJob, fingerprint: str) -> str:
    raw = json.dumps(
        {
            "createdAt": job.created_at.isoformat(),
            "id": job.id.hex,
            "filters": fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, fingerprint: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(payload, dict) or payload.get("filters") != fingerprint:
            raise GenerationCursorError
        created_at = datetime.fromisoformat(payload["createdAt"])
        if created_at.tzinfo is None:
            raise GenerationCursorError
        return created_at, uuid.UUID(payload["id"])
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise GenerationCursorError from error
