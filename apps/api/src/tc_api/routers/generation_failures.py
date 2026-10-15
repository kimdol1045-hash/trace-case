"""Single-team automatic generation failure queue and conditional retry API."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Path, Query, Request, Response, status

from tc_api.dependencies import SessionDependency
from tc_api.generation_failure_models import (
    FailureDetail,
    FailureFinality,
    FailureQueuePage,
    GenerationErrorCode,
    OperatorRetryRequest,
    RetryScheduledResponse,
)
from tc_api.ids import decode_identifier
from tc_api.models import ErrorResponse
from tc_backend.generation.retry_history import (
    RETRY_HISTORY_CONTRACT_VERSION,
    RetryHistoryError,
    RetryHistoryService,
)

router = APIRouter(prefix="/api/generation-failures", tags=["generation-failures"])
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (404, 409, 422, 503)
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


@router.get(
    "",
    operation_id="list_generation_failures",
    response_model=FailureQueuePage,
    responses=ERROR_RESPONSES,
)
def list_generation_failures(
    session: SessionDependency,
    actionability: Annotated[
        Literal[
            "AUTOMATIC_RETRY_SCHEDULED",
            "OPERATOR_RETRY_ALLOWED",
            "REASSESS_CURRENT_SOURCE",
            "REVIEW_ONLY",
        ]
        | None,
        Query(),
    ] = None,
    finality: Annotated[FailureFinality | None, Query()] = None,
    error_code: Annotated[GenerationErrorCode | None, Query(alias="errorCode")] = None,
    failed_before: Annotated[datetime | None, Query(alias="failedBefore")] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> FailureQueuePage:
    fingerprint = _filter_fingerprint(
        actionability, finality, error_code, failed_before
    )
    before = _decode_cursor(cursor, fingerprint) if cursor is not None else None
    if failed_before is not None:
        failed_before = _aware(failed_before)
        failed_boundary = (failed_before, uuid.UUID(int=(1 << 128) - 1))
        if before is None or failed_boundary < before:
            before = failed_boundary
    items, next_value = RetryHistoryService(session).list_failures(
        limit=limit,
        actionability=actionability,
        finality=finality,
        error_code=error_code,
        before=before,
    )
    return FailureQueuePage.model_validate(
        {
            "contractVersion": RETRY_HISTORY_CONTRACT_VERSION,
            "items": items,
            "nextCursor": _encode_cursor(next_value, fingerprint)
            if next_value
            else None,
        }
    )


@router.get(
    "/{failed_job_id}",
    operation_id="get_generation_failure",
    response_model=FailureDetail,
    responses=ERROR_RESPONSES,
)
def get_generation_failure(
    failed_job_id: Annotated[str, Path()],
    session: SessionDependency,
) -> FailureDetail:
    decoded = decode_identifier(
        failed_job_id, prefix="generation_job", field="failed_job_id"
    )
    return FailureDetail.model_validate(
        RetryHistoryService(session).failure_detail(decoded)
    )


@router.get(
    "/{failed_job_id}/attempt-history",
    operation_id="get_generation_attempt_history",
    response_model=FailureDetail,
    responses=ERROR_RESPONSES,
)
def get_generation_attempt_history(
    failed_job_id: Annotated[str, Path()],
    session: SessionDependency,
) -> FailureDetail:
    decoded = decode_identifier(
        failed_job_id, prefix="generation_job", field="failed_job_id"
    )
    return FailureDetail.model_validate(
        RetryHistoryService(session).attempt_history(decoded)
    )


@router.post(
    "/{failed_job_id}/retry",
    operation_id="retry_automatic_generation_failure",
    response_model=RetryScheduledResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def retry_automatic_generation_failure(
    failed_job_id: Annotated[str, Path()],
    payload: OperatorRetryRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    idempotency_header: IdempotencyHeader = None,
) -> RetryScheduledResponse:
    del (
        request
    )  # actor/workspace are deliberately server-owned in the single-team product
    if idempotency_header is not None and idempotency_header != payload.idempotencyKey:
        raise RetryHistoryError("VALIDATION_FAILED", status_code=422)
    failed_uuid = decode_identifier(
        failed_job_id, prefix="generation_job", field="failed_job_id"
    )
    expected_root = decode_identifier(
        payload.expectedRootJobId,
        prefix="generation_job",
        field="expectedRootJobId",
    )
    expected_failed = decode_identifier(
        payload.expectedFailedJobId,
        prefix="generation_job",
        field="expectedFailedJobId",
    )
    with session.begin():
        result = RetryHistoryService(session).retry_with_conditions(
            failed_uuid,
            idempotency_key=payload.idempotencyKey,
            expected_root_job_id=expected_root,
            expected_failed_job_id=expected_failed,
            expected_attempt_number=payload.expectedAttemptNumber,
            reason=payload.reason,
            additional_conditions=payload.additionalConditions,
        )
        response.status_code = (
            status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED
        )
        response.headers["Location"] = (
            f"/api/generation-failures/{payload.expectedFailedJobId}/attempt-history"
        )
        return RetryScheduledResponse.model_validate(
            {
                "contractVersion": RETRY_HISTORY_CONTRACT_VERSION,
                "retryRequestId": f"generation_retry_request_{result.request.id.hex}",
                "requestDigest": result.request.request_digest,
                "actor": {
                    "type": "AUTHENTICATED_OPERATOR",
                    "principalId": result.request.actor_principal_id,
                    "displayLabel": result.request.actor_display_label,
                },
                "sourceFailedJobId": f"generation_job_{result.request.expected_failed_job_id.hex}",
                "retryJobId": f"generation_job_{result.job.id.hex}",
                "jobAttemptNumber": result.job.attempt_number,
                "retryOrigin": "OPERATOR_CONDITIONS",
                "contextMode": "REUSE_SEALED",
                "notBefore": result.schedule.not_before,
                "additionalConditions": {
                    "text": result.request.normalized_additional_conditions,
                    "unicodeCodePoints": result.request.condition_code_points,
                    "utf8Bytes": result.request.condition_utf8_bytes,
                    "digest": result.request.condition_digest,
                },
            }
        )


def _filter_fingerprint(*values: object) -> str:
    return hashlib.sha256(
        json.dumps(values, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _encode_cursor(value: tuple[datetime, uuid.UUID], fingerprint: str) -> str:
    raw = json.dumps(
        {
            "failedAt": _aware(value[0]).isoformat(),
            "failedJobId": value[1].hex,
            "filter": fingerprint,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str, fingerprint: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if set(decoded) != {"failedAt", "failedJobId", "filter"}:
            raise ValueError
        if decoded["filter"] != fingerprint:
            raise ValueError
        return _aware(datetime.fromisoformat(decoded["failedAt"])), uuid.UUID(
            decoded["failedJobId"]
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise RetryHistoryError("VALIDATION_FAILED", status_code=422) from error


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise RetryHistoryError("VALIDATION_FAILED", status_code=422)
    return value.astimezone(UTC)
