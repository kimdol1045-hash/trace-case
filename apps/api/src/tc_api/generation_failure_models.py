"""Automatic generation retry/history API 1.0.0 models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from tc_api.models import ApiModel, RequestModel

GenerationErrorCode = Literal[
    "GENERATION_CONFIG_INVALID",
    "GENERATION_UNSUPPORTED_SCHEMA_VERSION",
    "GENERATION_AUTH_REQUIRED",
    "GENERATION_AUTH_REJECTED",
    "GENERATION_TIMEOUT",
    "GENERATION_TRANSPORT_UNAVAILABLE",
    "GENERATION_RATE_LIMITED",
    "GENERATION_PROVIDER_REJECTED",
    "GENERATION_PROVIDER_ERROR",
    "GENERATION_INVALID_CONTENT_TYPE",
    "GENERATION_RESPONSE_TOO_LARGE",
    "GENERATION_MALFORMED_JSON",
    "GENERATION_INVALID_RESPONSE",
    "GENERATION_SOURCE_STALE",
    "GENERATION_SOURCE_UNAVAILABLE",
    "GENERATION_WORK_ITEM_DELETED",
    "GENERATION_EXECUTION_OUTCOME_UNKNOWN",
    "GENERATION_WORKER_DELIVERY_EXHAUSTED",
]
FailureFinality = Literal[
    "AUTOMATIC_RETRY_SCHEDULED",
    "TERMINAL_NON_RETRYABLE",
    "TERMINAL_EXHAUSTED",
    "TERMINAL_REVIEW_REQUIRED",
]


class FailureSummary(ApiModel):
    code: GenerationErrorCode
    message_label: str = Field(min_length=1, max_length=200)
    retryable: bool
    finality: FailureFinality
    provider_attempt_count: int = Field(ge=0, le=5)
    failed_at: datetime
    next_attempt_not_before: datetime | None
    exhaustion_receipt_id: str | None


class FailureQueueItem(ApiModel):
    root_job_id: str
    failed_job_id: str
    slack_work_item_id: str
    source_version: int = Field(ge=1)
    job_attempt_number: int = Field(ge=1)
    failure: FailureSummary
    actionability: Literal[
        "AUTOMATIC_RETRY_SCHEDULED",
        "OPERATOR_RETRY_ALLOWED",
        "REASSESS_CURRENT_SOURCE",
        "REVIEW_ONLY",
    ]
    latest_retry_job_id: str | None


class FailureQueuePage(ApiModel):
    contract_version: Literal["automatic-generation-retry-history/1.0.0"]
    items: list[FailureQueueItem] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, max_length=1024)


class AdditionalConditions(ApiModel):
    text: str = Field(min_length=1, max_length=1000)
    unicode_code_points: int = Field(ge=1, le=1000)
    utf8_bytes: int = Field(ge=1, le=4000)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class InputSeal(ApiModel):
    context_policy: Literal["REUSE_SEALED"]
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_schema_version: str = Field(min_length=1, max_length=100)
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    operator_conditions: AdditionalConditions | None


class ProviderAttempt(ApiModel):
    provider_attempt_number: int = Field(ge=1, le=5)
    outcome: Literal[
        "SUCCEEDED", "RETRYABLE_FAILURE", "NON_RETRYABLE_FAILURE", "AMBIGUOUS"
    ]
    error_code: GenerationErrorCode | None
    safe_provider_code: str | None = Field(default=None, max_length=80)
    http_status: int | None = Field(default=None, ge=100, le=599)
    started_at: datetime
    finished_at: datetime | None


class MaterializationSummary(ApiModel):
    mode: Literal["MANUAL_ONLY", "AUTOMATIC_APPLY", "AUTOMATIC_SUGGESTIONS"]
    state: Literal[
        "NOT_SCHEDULED",
        "QUEUED",
        "RUNNING",
        "COMMITTED",
        "SKIPPED_STALE_SOURCE",
        "SKIPPED_SOURCE_UNAVAILABLE",
        "REVIEW_REQUIRED",
        "FAILED",
    ]
    apply_id: str | None
    suggestion_materialization_id: str | None


class NotificationSummary(ApiModel):
    intent_id: str | None
    receipt_id: str | None
    outcome: Literal["NONE", "PENDING", "DELIVERED", "SUPPRESSED", "DEAD"]


class AttemptHistoryItem(ApiModel):
    job_id: str
    root_job_id: str
    retry_of_job_id: str | None
    job_attempt_number: int = Field(ge=1)
    retry_origin: Literal[
        "ROOT", "AUTOMATIC_POLICY", "OPERATOR_CONDITIONS", "MANUAL_API"
    ]
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    execution_state: Literal[
        "NOT_BEFORE", "READY", "LEASED", "PROVIDER_OUTCOME_UNKNOWN", "TERMINAL"
    ]
    not_before: datetime
    worker_delivery_attempt_count: int = Field(ge=0)
    input: InputSeal
    provider_attempts: list[ProviderAttempt] = Field(max_length=5)
    result_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    failure: FailureSummary | None
    materialization: MaterializationSummary
    notification: NotificationSummary
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TimelineReferences(ApiModel):
    retry_request_id: str | None
    apply_id: str | None
    suggestion_materialization_id: str | None
    notification_intent_id: str | None
    notification_receipt_id: str | None


class TimelineEvent(ApiModel):
    sequence: int = Field(ge=1)
    event_type: Literal[
        "INPUT_SEALED",
        "EXECUTION_QUEUED",
        "WORKER_LEASED",
        "PROVIDER_ATTEMPT_STARTED",
        "PROVIDER_ATTEMPT_FAILED",
        "PROVIDER_ATTEMPT_SUCCEEDED",
        "JOB_FAILED",
        "RETRY_SCHEDULED",
        "JOB_SUCCEEDED",
        "APPLY_SCHEDULED",
        "APPLY_COMMITTED",
        "SUGGESTION_SCHEDULED",
        "SUGGESTION_COMMITTED",
        "MATERIALIZATION_SKIPPED",
        "SLACK_INTENT_CAPTURED",
        "SLACK_DELIVERED",
        "SLACK_SUPPRESSED",
        "SLACK_DEAD",
    ]
    job_id: str
    job_attempt_number: int = Field(ge=1)
    provider_attempt_number: int | None = Field(default=None, ge=1, le=5)
    error_code: GenerationErrorCode | None
    occurred_at: datetime
    references: TimelineReferences


class FailureDetail(ApiModel):
    contract_version: Literal["automatic-generation-retry-history/1.0.0"]
    failure: FailureQueueItem
    attempt_history: list[AttemptHistoryItem] = Field(min_length=1, max_length=100)
    timeline: list[TimelineEvent] = Field(min_length=1, max_length=2000)


class OperatorRetryRequest(RequestModel):
    idempotencyKey: str = Field(
        min_length=8, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    expectedRootJobId: str = Field(min_length=1, max_length=128)
    expectedFailedJobId: str = Field(min_length=1, max_length=128)
    expectedAttemptNumber: int = Field(ge=1)
    contextMode: Literal["REUSE_SEALED"]
    reason: str = Field(min_length=1, max_length=500)
    additionalConditions: str = Field(min_length=1, max_length=1000)


class OperatorActor(ApiModel):
    type: Literal["AUTHENTICATED_OPERATOR"]
    principal_id: str
    display_label: str = Field(min_length=1, max_length=200)


class RetryScheduledResponse(ApiModel):
    contract_version: Literal["automatic-generation-retry-history/1.0.0"]
    retry_request_id: str
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor: OperatorActor
    source_failed_job_id: str
    retry_job_id: str
    job_attempt_number: int = Field(ge=2)
    retry_origin: Literal["OPERATOR_CONDITIONS"]
    context_mode: Literal["REUSE_SEALED"]
    not_before: datetime
    additional_conditions: AdditionalConditions
