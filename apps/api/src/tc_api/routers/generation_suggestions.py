"""Read and deliberate apply/ignore API for immutable generation suggestions."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Header, Path, Request

from tc_api.dependencies import SessionDependency
from tc_api.idempotency import execute_idempotent
from tc_api.ids import decode_identifier, encode_identifier
from tc_api.models import (
    ApplyGenerationSuggestionRequest,
    ApplyGenerationSuggestionResponse,
    ErrorResponse,
    GenerationSuggestionBaselineResource,
    GenerationSuggestionDiffResource,
    GenerationSuggestionPageResource,
    GenerationSuggestionProposalResource,
    GenerationSuggestionResolutionResource,
    GenerationSuggestionResource,
    GenerationSuggestionSourceResource,
    IgnoreGenerationSuggestionRequest,
    IgnoreGenerationSuggestionResponse,
)
from tc_api.serializers import audit_context, case_resource_response
from tc_backend.db.models import (
    GenerationJob,
    GenerationSuggestion,
    GenerationSuggestionResolution,
    GenerationSuggestionTarget,
)
from tc_backend.generation.suggestions import GenerationSuggestionService

router = APIRouter(tags=["generation-suggestions"])
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Suggestion or case not found"},
    409: {"model": ErrorResponse, "description": "Suggestion or idempotency conflict"},
    422: {"model": ErrorResponse, "description": "Malformed suggestion request"},
    500: {"model": ErrorResponse, "description": "Suggestion transaction rolled back"},
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


@router.get(
    "/api/test-cases/{caseId}/generation-suggestions",
    operation_id="listTestCaseGenerationSuggestions",
    response_model=GenerationSuggestionPageResource,
    responses={404: ERROR_RESPONSES[404], 422: ERROR_RESPONSES[422]},
)
def list_test_case_generation_suggestions(
    case_id: Annotated[str, Path(alias="caseId")],
    session: SessionDependency,
) -> GenerationSuggestionPageResource:
    decoded = decode_identifier(case_id, prefix="case", field="caseId")
    service = GenerationSuggestionService(session)
    return GenerationSuggestionPageResource(
        items=[_resource(session, service, row) for row in service.list_for_case(decoded)]
    )


@router.get(
    "/api/generation-suggestions/{suggestionId}",
    operation_id="getGenerationSuggestion",
    response_model=GenerationSuggestionResource,
    responses={404: ERROR_RESPONSES[404], 422: ERROR_RESPONSES[422]},
)
def get_generation_suggestion(
    suggestion_id: Annotated[str, Path(alias="suggestionId")],
    session: SessionDependency,
) -> GenerationSuggestionResource:
    decoded = decode_identifier(
        suggestion_id, prefix="generation_suggestion", field="suggestionId"
    )
    service = GenerationSuggestionService(session)
    return _resource(session, service, service.get(decoded))


@router.post(
    "/api/generation-suggestions/{suggestionId}/apply",
    operation_id="applyGenerationSuggestion",
    response_model=ApplyGenerationSuggestionResponse,
    responses=ERROR_RESPONSES,
)
def apply_generation_suggestion(
    suggestion_id: Annotated[str, Path(alias="suggestionId")],
    payload: ApplyGenerationSuggestionRequest,
    request: Request,
    session: SessionDependency,
    idempotency_header: IdempotencyHeader,
) -> ApplyGenerationSuggestionResponse:
    decoded = decode_identifier(
        suggestion_id, prefix="generation_suggestion", field="suggestionId"
    )
    key = _matching_key(payload.idempotencyKey, idempotency_header)
    expected_version_id = decode_identifier(
        payload.expectedCaseVersionId,
        prefix="case_version",
        field="expectedCaseVersionId",
    )
    context = audit_context(request, idempotency_key=key, reason=payload.changeReason)
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=f"APPLY_GENERATION_SUGGESTION:{suggestion_id}",
            payload=payload.model_dump(mode="json"),
            response_model=ApplyGenerationSuggestionResponse,
            context=context,
            audit_action="APPLY_GENERATION_SUGGESTION",
            action=lambda audit: _apply(
                session,
                decoded,
                expected_case_resource_version=payload.expectedCaseResourceVersion,
                expected_case_version_id=expected_version_id,
                change_reason=payload.changeReason,
                audit=audit,
            ),
        )


@router.post(
    "/api/generation-suggestions/{suggestionId}/ignore",
    operation_id="ignoreGenerationSuggestion",
    response_model=IgnoreGenerationSuggestionResponse,
    responses=ERROR_RESPONSES,
)
def ignore_generation_suggestion(
    suggestion_id: Annotated[str, Path(alias="suggestionId")],
    payload: IgnoreGenerationSuggestionRequest,
    request: Request,
    session: SessionDependency,
    idempotency_header: IdempotencyHeader,
) -> IgnoreGenerationSuggestionResponse:
    decoded = decode_identifier(
        suggestion_id, prefix="generation_suggestion", field="suggestionId"
    )
    key = _matching_key(payload.idempotencyKey, idempotency_header)
    context = audit_context(request, idempotency_key=key, reason=payload.reason)
    with session.begin():
        return execute_idempotent(
            session,
            key=key,
            operation=f"IGNORE_GENERATION_SUGGESTION:{suggestion_id}",
            payload=payload.model_dump(mode="json"),
            response_model=IgnoreGenerationSuggestionResponse,
            context=context,
            audit_action="IGNORE_GENERATION_SUGGESTION",
            action=lambda audit: _ignore(
                session, decoded, reason=payload.reason, audit=audit
            ),
        )


def _apply(
    session: Any,
    suggestion_id: Any,
    *,
    expected_case_resource_version: int,
    expected_case_version_id: Any,
    change_reason: str,
    audit: Any,
) -> ApplyGenerationSuggestionResponse:
    service = GenerationSuggestionService(session)
    suggestion, resolution, aggregate = service.apply(
        suggestion_id,
        expected_case_resource_version=expected_case_resource_version,
        expected_case_version_id=expected_case_version_id,
        change_reason=change_reason,
        audit=audit,
    )
    return ApplyGenerationSuggestionResponse(
        suggestion=_resource(session, service, suggestion),
        resolution=_resolution_resource(resolution),
        test_case=case_resource_response(session, aggregate),
    )


def _ignore(
    session: Any,
    suggestion_id: Any,
    *,
    reason: str,
    audit: Any,
) -> IgnoreGenerationSuggestionResponse:
    service = GenerationSuggestionService(session)
    suggestion, resolution = service.ignore(
        suggestion_id, reason=reason, audit=audit
    )
    return IgnoreGenerationSuggestionResponse(
        suggestion=_resource(session, service, suggestion),
        resolution=_resolution_resource(resolution),
    )


def _resource(
    session: Any,
    service: GenerationSuggestionService,
    row: GenerationSuggestion,
) -> GenerationSuggestionResource:
    target = session.get(GenerationSuggestionTarget, row.suggestion_target_id)
    job = session.get(GenerationJob, row.source_generation_job_id)
    if target is None or job is None:
        raise RuntimeError("suggestion source seal is incomplete")
    resolution = service.resolution(row.id)
    superseder = service.superseder(row.id)
    return GenerationSuggestionResource(
        id=encode_identifier("generation_suggestion", row.id),
        test_case_id=encode_identifier("case", row.test_case_id),
        generation_lineage_id=encode_identifier(
            "generation_lineage", row.generation_lineage_id
        ),
        state=cast(Any, service.state(row)),
        baseline=GenerationSuggestionBaselineResource(
            case_resource_version=row.baseline_case_resource_version,
            case_version_id=encode_identifier(
                "case_version", row.baseline_case_version_id
            ),
            case_version=row.baseline_case_version,
            snapshot=row.baseline_snapshot,
            digest=row.baseline_digest,
        ),
        proposal=GenerationSuggestionProposalResource(
            snapshot=row.proposed_snapshot,
            digest=row.proposed_digest,
        ),
        diff=GenerationSuggestionDiffResource(
            schema_version="generation-suggestion-diff/1.0.0",
            digest=row.diff_digest,
            changed_fields=cast(list[str], row.diff_snapshot["changedFields"]),
            changes=cast(list[dict[str, Any]], row.diff_snapshot["changes"]),
        ),
        source=GenerationSuggestionSourceResource(
            suggestion_target_id=encode_identifier(
                "generation_suggestion_target", target.id
            ),
            generation_job_id=encode_identifier(
                "generation_job", row.source_generation_job_id
            ),
            root_generation_job_id=encode_identifier(
                "generation_job", row.source_root_job_id
            ),
            generation_change_assessment_id=encode_identifier(
                "generation_change_assessment", row.source_change_assessment_id
            ),
            slack_work_item_id=encode_identifier(
                "slack_work", target.slack_work_item_id
            ),
            source_version=job.source_version,
            generation_version=job.generation_version,
            generated_case_index=row.generated_case_index,
            context_digest=row.context_digest,
            result_digest=row.result_digest,
            exact_content_key=row.exact_content_key,
        ),
        resolution=(
            _resolution_resource(resolution) if resolution is not None else None
        ),
        superseded_by_suggestion_id=(
            encode_identifier("generation_suggestion", superseder.id)
            if superseder is not None
            else None
        ),
        created_at=row.created_at,
    )


def _resolution_resource(
    row: GenerationSuggestionResolution,
) -> GenerationSuggestionResolutionResource:
    return GenerationSuggestionResolutionResource(
        id=encode_identifier("generation_suggestion_resolution", row.id),
        decision=cast(Any, row.decision),
        actor=row.actor,
        reason=row.reason,
        observed_case_resource_version=row.observed_case_resource_version,
        observed_case_version_id=encode_identifier(
            "case_version", row.observed_case_version_id
        ),
        applied_case_version_id=(
            encode_identifier("case_version", row.applied_case_version_id)
            if row.applied_case_version_id is not None
            else None
        ),
        audit_id=encode_identifier("audit", row.audit_id),
        decided_at=row.decided_at,
    )


def _matching_key(body: str, header: str) -> str:
    if body != header:
        raise ValueError("Idempotency-Key header must match idempotencyKey")
    return body
