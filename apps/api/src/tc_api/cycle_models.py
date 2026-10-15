"""Strict Sprint 3 cycle and execution-ledger HTTP models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import ConfigDict, Field, RootModel, model_validator
from pydantic.json_schema import SkipJsonSchema

from tc_api.models import (
    ApiModel,
    RequestModel,
    TestCaseVersionDiffResponse,
    TestCaseVersionResponse,
)

CycleStatusValue = Literal["PLANNED", "IN_PROGRESS", "COMPLETED"]
ScopeTypeValue = Literal["WORK_ITEM", "LABEL"]
PlatformValue = Literal["WEB", "AOS", "IOS"]
ExecutionStatusValue = Literal["UNEXECUTED", "PASS", "FAIL", "BLOCKED"]
ExecutionAggregateStatusValue = Literal["UNEXECUTED", "PASS", "FAIL"]
TerminalExecutionStatusValue = Literal["PASS", "FAIL", "BLOCKED"]


class WorkItemScope(RequestModel):
    type: Literal["WORK_ITEM"]
    workItemId: str = Field(pattern=r"^work_item_[A-Za-z0-9_-]+$", max_length=128)
    workItemKey: str = Field(min_length=1, max_length=100, pattern=r"\S")


class LabelScope(RequestModel):
    type: Literal["LABEL"]
    label: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]
    labels: (
        Annotated[
            list[Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]],
            Field(min_length=1, max_length=50),
        ]
        | SkipJsonSchema[None]
    ) = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="before")
    @classmethod
    def fill_legacy_label_from_labels(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and not value.get("label")
            and isinstance(value.get("labels"), list)
            and value["labels"]
        ):
            return {**value, "label": value["labels"][0]}
        return value

    @model_validator(mode="after")
    def canonicalize_labels(self) -> Self:
        values = list(self.labels or [])
        if not any(
            value.strip().casefold() == self.label.strip().casefold() for value in values
        ):
            values.insert(0, self.label)
        normalized = [value.strip().casefold() for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("LABEL scope labels must be unique")
        object.__setattr__(self, "label", values[0])
        object.__setattr__(self, "labels", values)
        return self


CycleScope = Annotated[WorkItemScope | LabelScope, Field(discriminator="type")]


class CycleResourceResponse(ApiModel):
    id: str
    name: str
    description: str
    start_date: date
    status: CycleStatusValue
    scope: CycleScope
    scope_locked: bool
    resource_version: int = Field(ge=1)
    item_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    deleted_at: datetime | None


class CreateCycleRequest(RequestModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"\S")
    description: str = Field(max_length=10_000)
    startDate: date
    scope: CycleScope


class UpdateCycleRequest(RequestModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"minProperties": 2})

    baseResourceVersion: int = Field(ge=1)
    name: (
        Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None
    description: Annotated[str, Field(max_length=10_000)] | SkipJsonSchema[None] = None
    startDate: date | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def require_patch(self) -> Self:
        fields = self.model_fields_set.difference({"baseResourceVersion"})
        if not fields:
            raise ValueError("at least one cycle field must be supplied")
        nulls = [name for name in fields if getattr(self, name) is None]
        if nulls:
            raise ValueError(f"cycle fields cannot be null: {', '.join(sorted(nulls))}")
        return self


class UpdateCycleScopeRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    scope: CycleScope
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class DeleteCycleRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class DeleteCycleResponse(ApiModel):
    operation: Literal["DELETE"]
    cycle_id: str
    deleted_at: datetime
    audit_id: str


class RestoreCycleRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class CycleListItemResponse(ApiModel):
    id: str
    name: str
    start_date: date
    status: CycleStatusValue
    scope: CycleScope
    resource_version: int = Field(ge=1)
    item_count: int = Field(ge=0)
    platform_result_count: int = Field(ge=0)
    updated_at: datetime
    deleted_at: datetime | None


class RestoreCycleResponse(ApiModel):
    operation: Literal["RESTORE"]
    cycle: CycleListItemResponse
    audit_id: str


class PermanentDeleteCycleRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class PermanentDeleteCycleResponse(ApiModel):
    operation: Literal["PERMANENT_DELETE"]
    cycle_id: str
    deleted_attachment_count: int = Field(ge=0)
    audit_id: str


class CycleStatusCountsResponse(ApiModel):
    planned: int = Field(ge=0, alias="PLANNED")
    in_progress: int = Field(ge=0, alias="IN_PROGRESS")
    completed: int = Field(ge=0, alias="COMPLETED")


class CyclePageResponse(ApiModel):
    items: list[CycleListItemResponse] = Field(max_length=200)
    next_cursor: str | None
    total: int = Field(ge=0)
    status_counts: CycleStatusCountsResponse


class WorkItemScopeOptionResponse(ApiModel):
    type: Literal["WORK_ITEM"]
    work_item_id: str
    work_item_key: str
    summary: str
    owner_label: str | None = None
    case_count: int = Field(ge=0)


class LabelScopeOptionResponse(ApiModel):
    type: Literal["LABEL"]
    label: str
    case_count: int = Field(ge=0)


ScopeOptionResponse = Annotated[
    WorkItemScopeOptionResponse | LabelScopeOptionResponse, Field(discriminator="type")
]


class ScopeOptionPageResponse(ApiModel):
    items: list[ScopeOptionResponse] = Field(max_length=200)
    next_cursor: str | None
    total: int = Field(ge=0)


class ScopePreviewItemResponse(ApiModel):
    test_case_id: str
    case_key: str
    summary: str
    work_item_id: str
    work_item_key: str
    owner_label: str | None
    labels: list[str]
    current_version_id: str
    current_version: int = Field(ge=1)


class ScopePreviewResponse(ApiModel):
    scope: CycleScope
    items: list[ScopePreviewItemResponse] = Field(max_length=200)
    next_cursor: str | None
    total: int = Field(ge=0)
    resolved_at: datetime


class ExecutionAttemptResponse(ApiModel):
    id: str
    cycle_id: str
    cycle_item_id: str
    pinned_version_id: str
    platform: PlatformValue
    attempt_no: int = Field(ge=1)
    status: ExecutionStatusValue
    actual_result: str | None
    note: str | None
    executor_label: str | None
    started_at: datetime
    completed_at: datetime | None
    is_latest: bool


class LatestPlatformExecutionResponse(ApiModel):
    platform: PlatformValue
    status: ExecutionStatusValue
    latest_attempt: ExecutionAttemptResponse | None


class LatestCaseVersionRefResponse(ApiModel):
    version_id: str
    version: int = Field(ge=1)
    updated_at: datetime


class CycleItemResponse(ApiModel):
    id: str
    cycle_id: str
    test_case_id: str
    case_key: str
    work_item_id: str
    work_item_key: str
    owner_label: str | None
    labels: list[str]
    sort_order: int = Field(ge=0)
    pinned_version_id: str
    pinned_version: TestCaseVersionResponse
    latest_version: LatestCaseVersionRefResponse
    is_latest_version: bool
    platform_executions: list[LatestPlatformExecutionResponse] = Field(
        min_length=3, max_length=3
    )
    created_at: datetime


class CycleItemPageResponse(ApiModel):
    items: list[CycleItemResponse] = Field(max_length=200)
    next_cursor: str | None
    total: int = Field(ge=0)


class StartCycleRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    actorLabel: (
        Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None


class StartCycleResponse(ApiModel):
    cycle: CycleResourceResponse
    items: list[CycleItemResponse]
    audit_id: str


class CompleteCycleRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    acknowledgeIncomplete: bool
    actorLabel: (
        Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None


class IncompleteExecutionWarningResponse(ApiModel):
    code: Literal["INCOMPLETE_EXECUTIONS"]
    unexecuted_count: int = Field(ge=0)


class CompleteCycleResponse(ApiModel):
    cycle: CycleResourceResponse
    warnings: list[IncompleteExecutionWarningResponse] = Field(max_length=1)
    audit_id: str


class ReopenCycleRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    actorLabel: (
        Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None


class ReopenCycleResponse(ApiModel):
    cycle: CycleResourceResponse
    audit_id: str


class RefreshCycleItemSnapshotRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    expectedPinnedVersionId: str
    targetVersionId: str
    confirm: Literal[True]
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class RefreshCycleItemSnapshotResponse(ApiModel):
    cycle_item: CycleItemResponse
    previous_pinned_version_id: str
    diff: TestCaseVersionDiffResponse
    audit_id: str


class RefreshCycleSnapshotRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    confirm: Literal[True]
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class StartExecutionAttemptRequest(RequestModel):
    platform: PlatformValue
    executorLabel: (
        Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None
    note: (
        Annotated[str, Field(min_length=1, max_length=10_000, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None


class CompleteExecutionAttemptRequest(RequestModel):
    status: TerminalExecutionStatusValue
    actualResult: (
        Annotated[str, Field(min_length=1, max_length=50_000, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None
    note: (
        Annotated[str, Field(min_length=1, max_length=10_000, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None
    executorLabel: (
        Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None


class RetryExecutionAttemptRequest(RequestModel):
    previousAttemptId: str
    executorLabel: (
        Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None
    note: (
        Annotated[str, Field(min_length=1, max_length=10_000, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None


class RetryExecutionAttemptResponse(ApiModel):
    previous_attempt: ExecutionAttemptResponse
    attempt: ExecutionAttemptResponse


class ExecutionAttemptHistoryPageResponse(ApiModel):
    items: list[ExecutionAttemptResponse] = Field(max_length=200)
    next_cursor: str | None
    total: int = Field(ge=0)
    latest_attempt_ids: list[str]


class StepResultInput(RequestModel):
    sequence: int = Field(ge=1)
    status: TerminalExecutionStatusValue
    note: (
        Annotated[str, Field(min_length=1, max_length=10_000, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None

class RecordStepResultsRequest(RequestModel):
    results: list[StepResultInput] = Field(min_length=1, max_length=500)
    executorLabel: (
        Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
        | SkipJsonSchema[None]
    ) = None

    @model_validator(mode="after")
    def unique_sequences(self) -> Self:
        sequences = [result.sequence for result in self.results]
        if len(sequences) != len(set(sequences)):
            raise ValueError("step result sequences must be unique")
        return self


class ExecutionStepResponse(ApiModel):
    test_step_id: str
    sequence: int = Field(ge=1)
    precondition: str | None
    action: str
    expected_result: str | None
    status: ExecutionStatusValue
    note: str | None
    recorded_at: datetime | None
    updated_at: datetime | None


class StepExecutionResponse(ApiModel):
    cycle_id: str
    cycle_item_id: str
    pinned_version_id: str
    platform: PlatformValue
    attempt_id: str | None
    attempt_no: int | None = Field(default=None, ge=1)
    status: ExecutionStatusValue
    executor_label: str | None
    started_at: datetime | None
    completed_at: datetime | None
    recorded_step_count: int = Field(ge=0)
    total_step_count: int = Field(ge=1)
    steps: list[ExecutionStepResponse] = Field(min_length=1, max_length=500)


class ExecutionStatusCountsResponse(ApiModel):
    total: int = Field(ge=0)
    UNEXECUTED: int = Field(ge=0)
    PASS: int = Field(ge=0)
    FAIL: int = Field(ge=0)
    BLOCKED: int = Field(ge=0)


class ProgressValuesResponse(ApiModel):
    counts: ExecutionStatusCountsResponse
    executed_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    aggregate_status: ExecutionAggregateStatusValue


class OverallProgressResponse(ApiModel):
    dimension: Literal["OVERALL"]
    key: Literal["ALL"]
    label: str
    values: ProgressValuesResponse


class PlatformProgressResponse(ApiModel):
    dimension: Literal["PLATFORM"]
    key: PlatformValue
    label: str
    values: ProgressValuesResponse


class OwnerProgressResponse(ApiModel):
    dimension: Literal["OWNER"]
    key: str
    label: str
    values: ProgressValuesResponse


class WorkItemProgressResponse(ApiModel):
    dimension: Literal["WORK_ITEM"]
    key: str
    label: str
    values: ProgressValuesResponse


class LabelProgressResponse(ApiModel):
    dimension: Literal["LABEL"]
    key: str
    label: str
    values: ProgressValuesResponse


class CycleProgressResponse(ApiModel):
    cycle_id: str
    calculated_at: datetime
    excludes_planned: Literal[True]
    latest_attempt_only: Literal[True]
    overall: OverallProgressResponse
    by_platform: list[PlatformProgressResponse]
    by_owner: list[OwnerProgressResponse]
    by_work_item: list[WorkItemProgressResponse]
    by_label: list[LabelProgressResponse]


class _CycleErrorBase(ApiModel):
    message: str
    trace_id: str


class ValidationCycleErrorResponse(_CycleErrorBase):
    code: Literal["VALIDATION_FAILED"]
    details: dict[str, Any]


class ResourceNotFoundCycleErrorResponse(_CycleErrorBase):
    code: Literal["RESOURCE_NOT_FOUND"]
    details: dict[str, Any]


class VersionConflictCycleErrorResponse(_CycleErrorBase):
    code: Literal["VERSION_CONFLICT"]
    details: dict[str, Any]


class IdempotencyConflictCycleErrorResponse(_CycleErrorBase):
    code: Literal["IDEMPOTENCY_CONFLICT"]
    details: dict[str, Any]


class CursorInvalidCycleErrorResponse(_CycleErrorBase):
    code: Literal["CURSOR_INVALID"]
    details: dict[str, Any]


class CycleStateTransitionErrorResponse(_CycleErrorBase):
    code: Literal["CYCLE_STATE_TRANSITION_INVALID"]
    details: dict[str, Any]


class CycleMutationLockedErrorResponse(_CycleErrorBase):
    code: Literal[
        "CYCLE_SCOPE_LOCKED",
        "CYCLE_EXECUTION_NOT_STARTED",
        "CYCLE_EXECUTION_LOCKED",
        "CYCLE_DELETE_LOCKED",
        "CYCLE_RESTORE_CONFLICT",
    ]
    details: dict[str, Any]


class CycleScopeErrorResponse(_CycleErrorBase):
    code: Literal[
        "CYCLE_SCOPE_EMPTY", "CYCLE_SCOPE_INVALID", "CYCLE_SCOPE_DUPLICATE_CASE"
    ]
    details: dict[str, Any]


class SnapshotRefreshConflictErrorResponse(_CycleErrorBase):
    code: Literal[
        "SNAPSHOT_PIN_CONFLICT", "SNAPSHOT_TARGET_NOT_LATEST", "SNAPSHOT_CASE_MISMATCH"
    ]
    details: dict[str, Any]


class ExecutionAttemptConflictErrorResponse(_CycleErrorBase):
    code: Literal[
        "EXECUTION_ATTEMPT_ALREADY_ACTIVE",
        "EXECUTION_ATTEMPT_FINAL",
        "EXECUTION_ATTEMPT_SEQUENCE_CONFLICT",
        "EXECUTION_PLATFORM_DUPLICATE",
    ]
    details: dict[str, Any]


class ExecutionResultValidationErrorResponse(_CycleErrorBase):
    code: Literal["ACTUAL_RESULT_REQUIRED"]
    details: dict[str, Any]


class CycleIncompleteWarningErrorResponse(_CycleErrorBase):
    code: Literal["CYCLE_INCOMPLETE_WARNING"]
    details: dict[str, Any]


class CycleErrorResponse(
    RootModel[
        ValidationCycleErrorResponse
        | ResourceNotFoundCycleErrorResponse
        | VersionConflictCycleErrorResponse
        | IdempotencyConflictCycleErrorResponse
        | CursorInvalidCycleErrorResponse
        | CycleStateTransitionErrorResponse
        | CycleMutationLockedErrorResponse
        | CycleScopeErrorResponse
        | SnapshotRefreshConflictErrorResponse
        | ExecutionAttemptConflictErrorResponse
        | ExecutionResultValidationErrorResponse
        | CycleIncompleteWarningErrorResponse
    ]
):
    """Canonical cycle errors exposed as a generated-client union."""
