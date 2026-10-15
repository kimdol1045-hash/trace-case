"""Strict Sprint 1 request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

FolderOperation = Literal[
    "CREATE",
    "RENAME",
    "MOVE_SUBTREE",
    "SORT",
    "SOFT_DELETE_SUBTREE",
    "RESTORE_SUBTREE",
]
AuditSourceValue = Literal["UI", "API", "SYSTEM", "SLACK", "WORKER"]


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class RequestModel(BaseModel):
    """Request fields use their canonical wire names to avoid alias ambiguity."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    service: str
    status: str
    version: str


class ErrorResponse(ApiModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(min_length=1)
    details: dict[str, Any]
    trace_id: str = Field(min_length=1, max_length=128)


class GenerationSuggestionBaselineResource(ApiModel):
    case_resource_version: int = Field(ge=1)
    case_version_id: str = Field(pattern=r"^case_version_[A-Za-z0-9_-]+$")
    case_version: int = Field(ge=1)
    snapshot: dict[str, Any]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationSuggestionDiffResource(ApiModel):
    schema_version: Literal["generation-suggestion-diff/1.0.0"]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_fields: list[str] = Field(min_length=1)
    changes: list[dict[str, Any]] = Field(min_length=1)


class GenerationSuggestionProposalResource(ApiModel):
    snapshot: dict[str, Any]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationSuggestionSourceResource(ApiModel):
    suggestion_target_id: str = Field(pattern=r"^generation_suggestion_target_[A-Za-z0-9_-]+$")
    generation_job_id: str = Field(pattern=r"^generation_job_[A-Za-z0-9_-]+$")
    root_generation_job_id: str = Field(pattern=r"^generation_job_[A-Za-z0-9_-]+$")
    generation_change_assessment_id: str = Field(
        pattern=r"^generation_change_assessment_[A-Za-z0-9_-]+$"
    )
    slack_work_item_id: str = Field(pattern=r"^slack_work_[A-Za-z0-9_-]+$")
    source_version: int = Field(ge=1)
    generation_version: int = Field(ge=1)
    generated_case_index: int = Field(ge=0, le=9)
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    exact_content_key: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationSuggestionResolutionResource(ApiModel):
    id: str = Field(pattern=r"^generation_suggestion_resolution_[A-Za-z0-9_-]+$")
    decision: Literal["APPLIED", "IGNORED"]
    actor: dict[str, Any]
    reason: str = Field(min_length=1, max_length=500)
    observed_case_resource_version: int = Field(ge=1)
    observed_case_version_id: str = Field(pattern=r"^case_version_[A-Za-z0-9_-]+$")
    applied_case_version_id: str | None = None
    audit_id: str = Field(pattern=r"^audit_[A-Za-z0-9_-]+$")
    decided_at: datetime


class GenerationSuggestionResource(ApiModel):
    id: str = Field(pattern=r"^generation_suggestion_[A-Za-z0-9_-]+$")
    test_case_id: str = Field(pattern=r"^case_[A-Za-z0-9_-]+$")
    generation_lineage_id: str = Field(pattern=r"^generation_lineage_[A-Za-z0-9_-]+$")
    state: Literal["OPEN", "APPLIED", "IGNORED", "SUPERSEDED"]
    baseline: GenerationSuggestionBaselineResource
    proposal: GenerationSuggestionProposalResource
    diff: GenerationSuggestionDiffResource
    source: GenerationSuggestionSourceResource
    resolution: GenerationSuggestionResolutionResource | None
    superseded_by_suggestion_id: str | None
    created_at: datetime


class GenerationSuggestionPageResource(ApiModel):
    items: list[GenerationSuggestionResource]


class ApplyGenerationSuggestionRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    expectedCaseResourceVersion: int = Field(ge=1)
    expectedCaseVersionId: str = Field(pattern=r"^case_version_[A-Za-z0-9_-]+$")
    changeReason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class IgnoreGenerationSuggestionRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class ApplyGenerationSuggestionResponse(ApiModel):
    suggestion: GenerationSuggestionResource
    resolution: GenerationSuggestionResolutionResource
    test_case: TestCaseResourceResponse


class IgnoreGenerationSuggestionResponse(ApiModel):
    suggestion: GenerationSuggestionResource
    resolution: GenerationSuggestionResolutionResource


class WorkspaceSettingsResponse(ApiModel):
    id: str = Field(pattern=r"^workspace_[A-Za-z0-9_-]+$", max_length=128)
    team_name: str = Field(min_length=1, max_length=200)
    case_prefix: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,15}$")
    issue_prefix: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,15}$")
    default_environment: str = Field(min_length=1, max_length=200)
    generation_quiet_minutes: int = Field(ge=0, le=1440)
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class UpdateWorkspaceSettingsRequest(RequestModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"minProperties": 2})

    baseVersion: int = Field(ge=1)
    teamName: Annotated[
        str,
        Field(min_length=1, max_length=200, pattern=r"\S"),
    ] | SkipJsonSchema[None] = None
    casePrefix: Annotated[
        str,
        Field(pattern=r"^[A-Z][A-Z0-9]{1,15}$"),
    ] | SkipJsonSchema[None] = None
    issuePrefix: Annotated[
        str,
        Field(pattern=r"^[A-Z][A-Z0-9]{1,15}$"),
    ] | SkipJsonSchema[None] = None
    defaultEnvironment: Annotated[
        str,
        Field(min_length=1, max_length=200, pattern=r"\S"),
    ] | SkipJsonSchema[None] = None
    generationQuietMinutes: Annotated[int, Field(ge=0, le=1440)] | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def require_patch_field(self) -> Self:
        if not self.model_fields_set.difference({"baseVersion"}):
            raise ValueError("at least one workspace setting must be supplied")
        null_fields = [
            name
            for name in self.model_fields_set.difference({"baseVersion"})
            if getattr(self, name) is None
        ]
        if null_fields:
            raise ValueError(f"workspace settings cannot be null: {', '.join(sorted(null_fields))}")
        return self


class PathSegmentResponse(ApiModel):
    id: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    name: str = Field(min_length=1, max_length=200)


class FolderNodeResponse(ApiModel):
    id: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    parent_id: str | None
    name: str = Field(min_length=1, max_length=200)
    sort_order: int = Field(ge=0)
    depth: int = Field(ge=0)
    path: list[PathSegmentResponse] = Field(min_length=1)
    direct_test_case_count: int = Field(ge=0)
    subtree_test_case_count: int = Field(ge=0)
    version: int = Field(ge=1)
    deleted_at: datetime | None
    children: list[FolderNodeResponse]


class FolderTreeResponse(ApiModel):
    workspace_id: str = Field(pattern=r"^workspace_[A-Za-z0-9_-]+$", max_length=128)
    generated_at: datetime
    items: list[FolderNodeResponse]


class FolderChildrenResponse(ApiModel):
    workspace_id: str = Field(pattern=r"^workspace_[A-Za-z0-9_-]+$", max_length=128)
    parent_id: str | None
    generated_at: datetime
    items: list[FolderNodeResponse]


class CreateFolderRequest(RequestModel):
    parentId: str | None
    name: str = Field(min_length=1, max_length=200, pattern=r"\S")
    sortOrder: int = Field(default=0, ge=0)


class RenameFolderRequest(RequestModel):
    baseVersion: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200, pattern=r"\S")


class MoveFolderRequest(RequestModel):
    baseVersion: int = Field(ge=1)
    mode: Literal["SUBTREE"]
    targetParentId: str | None
    sortOrder: int = Field(ge=0)


class SortFolderRequest(RequestModel):
    baseVersion: int = Field(ge=1)
    sortOrder: int = Field(ge=0)


class SoftDeleteFolderRequest(RequestModel):
    baseVersion: int = Field(ge=1)
    mode: Literal["SUBTREE"]
    reason: Annotated[
        str,
        Field(min_length=1, max_length=500, pattern=r"\S"),
    ] | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def reject_null_reason(self) -> Self:
        if "reason" in self.model_fields_set and self.reason is None:
            raise ValueError("reason cannot be null")
        return self


class RestoreFolderRequest(RequestModel):
    baseVersion: int = Field(ge=1)
    deletionBatchId: str = Field(
        pattern=r"^folder_delete_[A-Za-z0-9_-]+$",
        max_length=128,
    )
    mode: Literal["SUBTREE"]
    targetParentId: str | None = None
    reason: Annotated[
        str,
        Field(min_length=1, max_length=500, pattern=r"\S"),
    ] | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def reject_null_reason(self) -> Self:
        if "reason" in self.model_fields_set and self.reason is None:
            raise ValueError("reason cannot be null")
        return self


class FolderDeletePreviewResponse(ApiModel):
    folder_id: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    affected_folder_count: int = Field(ge=1)
    active_test_case_count: int = Field(ge=0)
    requires_confirmation: bool


class FolderMutationResponse(ApiModel):
    operation: FolderOperation
    root_folder_id: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    affected_folder_ids: list[str] = Field(min_length=1)
    root_version: int = Field(ge=1)
    audit_id: str = Field(pattern=r"^audit_[A-Za-z0-9_-]+$", max_length=128)
    deletion_batch_id: Annotated[
        str,
        Field(pattern=r"^folder_delete_[A-Za-z0-9_-]+$", max_length=128),
    ] | SkipJsonSchema[None] = None


class AuditSourceResponse(ApiModel):
    type: AuditSourceValue
    actor_label: Annotated[str, Field(min_length=1, max_length=200)] | SkipJsonSchema[
        None
    ] = None
    external_actor_id: Annotated[
        str,
        Field(min_length=1, max_length=200),
    ] | SkipJsonSchema[None] = None


class AuditEntryResponse(ApiModel):
    id: str = Field(pattern=r"^audit_[A-Za-z0-9_-]+$", max_length=128)
    workspace_id: str = Field(pattern=r"^workspace_[A-Za-z0-9_-]+$", max_length=128)
    entity_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=64)
    entity_id: str = Field(min_length=1, max_length=128)
    action: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=64)
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    source: AuditSourceResponse
    reason: Annotated[str, Field(min_length=1, max_length=500)] | SkipJsonSchema[None] = None
    occurred_at: datetime
    trace_id: str = Field(min_length=1, max_length=128)
    idempotency_key: Annotated[
        str,
        Field(min_length=1, max_length=255),
    ] | SkipJsonSchema[None] = None


class AuditPageResponse(ApiModel):
    items: list[AuditEntryResponse] = Field(max_length=200)
    next_cursor: str | None
    total: int = Field(ge=0)


class CreateWorkItemRequest(RequestModel):
    key: str = Field(pattern=r"^WORK-[0-9]+$", max_length=64)
    categoryId: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    summary: str = Field(min_length=1, max_length=200, pattern=r"\S")
    description: str = Field(default="", max_length=20000)
    ownerLabel: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S")
    labels: list[str] = Field(default_factory=list, max_length=50)


class UpdateWorkItemRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    categoryId: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    summary: str = Field(min_length=1, max_length=200, pattern=r"\S")
    description: str = Field(default="", max_length=20000)
    ownerLabel: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S")
    labels: list[str] = Field(default_factory=list, max_length=50)
    changeReason: str = Field(min_length=1, max_length=500, pattern=r"\S")


# Sprint 2 test-case ledger contracts.
TestCaseStatusValue = Literal["ACTIVE", "DEPRECATED"]
PriorityValue = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ExpectedResultModeValue = Literal["PER_STEP", "CASE"]
TestCaseSourceValue = Literal["UI", "API", "SYSTEM", "SLACK", "WORKER"]


class SourceMetadata(RequestModel):
    type: TestCaseSourceValue
    actorLabel: Annotated[
        str, Field(min_length=1, max_length=200, pattern=r"\S")
    ] | SkipJsonSchema[None] = None
    externalActorId: Annotated[
        str, Field(min_length=1, max_length=200, pattern=r"\S")
    ] | SkipJsonSchema[None] = None
    slackWorkItemId: Annotated[
        str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    ] | SkipJsonSchema[None] = None
    generationJobId: Annotated[
        str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    ] | SkipJsonSchema[None] = None
    sourceRevisionIds: Annotated[list[str], Field(min_length=1)] | SkipJsonSchema[None] = None
    evidenceNodeIds: Annotated[list[str], Field(min_length=1)] | SkipJsonSchema[None] = None
    correlationId: Annotated[
        str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    ] | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def validate_source_requirements(self) -> Self:
        null_fields = [
            name for name in self.model_fields_set if name != "type" and getattr(self, name) is None
        ]
        if null_fields:
            raise ValueError(f"source fields cannot be null: {', '.join(sorted(null_fields))}")
        if self.type == "SLACK" and (
            self.slackWorkItemId is None or self.sourceRevisionIds is None
        ):
            raise ValueError("SLACK source requires slackWorkItemId and sourceRevisionIds")
        if self.type == "WORKER" and (
            self.generationJobId is None or self.evidenceNodeIds is None
        ):
            raise ValueError("WORKER source requires generationJobId and evidenceNodeIds")
        for field_name in ("sourceRevisionIds", "evidenceNodeIds"):
            values = getattr(self, field_name)
            if values is not None and (len(set(values)) != len(values) or any(not value for value in values)):
                raise ValueError(f"{field_name} must contain unique non-blank identifiers")
        return self


class SourceMetadataResponse(ApiModel):
    type: TestCaseSourceValue
    actor_label: str | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    external_actor_id: str | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    slack_work_item_id: str | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    generation_job_id: str | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    source_revision_ids: list[str] | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    evidence_node_ids: list[str] | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    correlation_id: str | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)


class TestStepPayload(RequestModel):
    sequence: int = Field(ge=1)
    precondition: Annotated[str, Field(min_length=1, pattern=r"\S")] | SkipJsonSchema[None] = None
    action: str = Field(min_length=1, pattern=r"\S")
    expectedResult: Annotated[str, Field(min_length=1, pattern=r"\S")] | SkipJsonSchema[None] = None


class TestStepResponse(ApiModel):
    sequence: int = Field(ge=1)
    precondition: str | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    action: str = Field(min_length=1)
    expected_result: str | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)


class _ContentRequest(RequestModel):
    summary: str = Field(min_length=1, max_length=200, pattern=r"\S")
    precondition: str = Field(min_length=1, pattern=r"\S")
    expectedResultMode: ExpectedResultModeValue
    caseExpectedResult: Annotated[
        str, Field(min_length=1, pattern=r"\S")
    ] | SkipJsonSchema[None] = None
    priority: PriorityValue
    ownerLabel: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S")
    labels: list[str] = Field(max_length=50)
    steps: list[TestStepPayload] = Field(min_length=1, max_length=500)
    source: SourceMetadata
    generationReason: Annotated[
        str, Field(min_length=1, max_length=500, pattern=r"\S")
    ] | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def validate_snapshot_shape(self) -> Self:
        if [step.sequence for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("steps must use contiguous one-based sequence values")
        if self.expectedResultMode == "PER_STEP":
            if self.caseExpectedResult is not None:
                raise ValueError("PER_STEP mode forbids caseExpectedResult")
            if any(step.expectedResult is None for step in self.steps):
                raise ValueError("PER_STEP mode requires an expectedResult for every step")
        else:
            if self.caseExpectedResult is None or self.generationReason is None:
                raise ValueError("CASE mode requires caseExpectedResult and generationReason")
            if any(step.expectedResult is not None for step in self.steps):
                raise ValueError("CASE mode forbids per-step expectedResult")
        return self


class CreateTestCaseRequest(_ContentRequest):
    workItemId: str = Field(pattern=r"^work_item_[A-Za-z0-9_-]+$", max_length=128)
    status: Literal["ACTIVE"]
    ownerLabel: Annotated[
        str, Field(min_length=1, max_length=200, pattern=r"\S")
    ] | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def reject_null_optional_fields(self) -> Self:
        null_fields = [
            name
            for name in ("caseExpectedResult", "ownerLabel", "generationReason")
            if name in self.model_fields_set and getattr(self, name) is None
        ]
        if null_fields:
            raise ValueError(f"fields cannot be null: {', '.join(null_fields)}")
        return self


class UpdateTestCaseRequest(_ContentRequest):
    baseVersion: int = Field(ge=1)
    status: TestCaseStatusValue | SkipJsonSchema[None] = None
    changeReason: str = Field(min_length=1, max_length=500, pattern=r"\S")

    @model_validator(mode="after")
    def reject_null_nonnullable_fields(self) -> Self:
        null_fields = [
            name
            for name in ("caseExpectedResult", "status", "generationReason")
            if name in self.model_fields_set and getattr(self, name) is None
        ]
        if null_fields:
            raise ValueError(f"fields cannot be null: {', '.join(null_fields)}")
        return self


class TestCaseVersionResponse(ApiModel):
    id: str = Field(pattern=r"^case_version_[A-Za-z0-9_-]+$", max_length=128)
    test_case_id: str = Field(pattern=r"^case_[A-Za-z0-9_-]+$", max_length=128)
    version: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=200)
    precondition: str = Field(min_length=1)
    expected_result_mode: ExpectedResultModeValue
    case_expected_result: str | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    priority: PriorityValue
    owner_label: str | None = Field(default=None, exclude_if=lambda value: value is None)
    labels: list[str] = Field(max_length=50)
    steps: list[TestStepResponse] = Field(min_length=1, max_length=500)
    source: SourceMetadataResponse
    change_reason: str = Field(min_length=1, max_length=500)
    restored_from_version: int | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    created_at: datetime


class CopiedFromResponse(ApiModel):
    test_case_id: str = Field(pattern=r"^case_[A-Za-z0-9_-]+$", max_length=128)
    case_key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,15}-[0-9]{6,}$", max_length=64)
    version_id: str = Field(pattern=r"^case_version_[A-Za-z0-9_-]+$", max_length=128)
    version: int = Field(ge=1)


class GenerationManagementActorResponse(ApiModel):
    type: AuditSourceValue
    actor_label: str | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    external_actor_id: str | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None
    )


class GenerationManagementNeverManagedResponse(ApiModel):
    managed: Literal[False]


class GenerationManagementManagedResponse(ApiModel):
    managed: Literal[True]
    lineage_id: str
    created_outcome_id: str
    baseline_generated_version_id: str
    latest_generated_version_id: str


class GenerationManagementReleasedResponse(ApiModel):
    managed: Literal[False]
    lineage_id: str
    created_outcome_id: str
    baseline_generated_version_id: str
    latest_generated_version_id: str
    released_at: datetime
    release_reason: str
    release_actor: GenerationManagementActorResponse
    release_mutation: Literal[
        "CONTENT_VERSION",
        "PARENT_MOVE",
        "SOFT_DELETE",
        "RESTORE_DELETED",
        "LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE",
    ]
    released_from_version_id: str
    released_by_version_id: str | SkipJsonSchema[None] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


GenerationManagementResponse = (
    GenerationManagementNeverManagedResponse
    | GenerationManagementManagedResponse
    | GenerationManagementReleasedResponse
)


class TestCaseResourceResponse(ApiModel):
    id: str = Field(pattern=r"^case_[A-Za-z0-9_-]+$", max_length=128)
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,15}-[0-9]{6,}$", max_length=64)
    work_item_id: str = Field(pattern=r"^work_item_[A-Za-z0-9_-]+$", max_length=128)
    category_id: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    sort_order: int = Field(ge=0)
    status: TestCaseStatusValue
    resource_version: int = Field(ge=1)
    version: int = Field(ge=1)
    current_version: TestCaseVersionResponse
    generation_management: GenerationManagementResponse
    copied_from: CopiedFromResponse | SkipJsonSchema[None] = Field(default=None, exclude_if=lambda value: value is None)
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class TestCaseListItemResponse(ApiModel):
    id: str
    key: str
    work_item_id: str
    work_item_key: str
    work_item_summary: str
    category_id: str
    sort_order: int = Field(ge=0)
    status: TestCaseStatusValue
    summary: str
    priority: PriorityValue
    owner_label: str | None = Field(default=None, exclude_if=lambda value: value is None)
    labels: list[str]
    resource_version: int = Field(ge=1)
    version: int = Field(ge=1)
    source: SourceMetadataResponse
    updated_at: datetime
    deleted_at: datetime | None


class TestCasePageResponse(ApiModel):
    items: list[TestCaseListItemResponse] = Field(max_length=200)
    next_cursor: str | None
    total: int = Field(ge=0)


class WorkItemResourceResponse(ApiModel):
    id: str = Field(pattern=r"^work_item_[A-Za-z0-9_-]+$", max_length=128)
    key: str = Field(min_length=1, max_length=100, pattern=r"\S")
    category_id: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    summary: str = Field(min_length=1, max_length=200)
    description: str
    owner_label: str | None
    labels: list[str] = Field(max_length=50)
    resource_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class WorkItemPageResponse(ApiModel):
    items: list[WorkItemResourceResponse] = Field(max_length=200)
    total: int = Field(ge=0)


class WorkItemDetailResponse(ApiModel):
    work_item: WorkItemResourceResponse
    test_cases: list[TestCaseListItemResponse] = Field(max_length=200)


class TestCaseVersionPageResponse(ApiModel):
    items: list[TestCaseVersionResponse] = Field(max_length=200)
    next_cursor: str | None
    total: int = Field(ge=0)


class FieldDiffResponse(ApiModel):
    field: Literal[
        "summary", "precondition", "expectedResultMode", "caseExpectedResult", "priority", "ownerLabel", "labels"
    ]
    before: Any
    after: Any


class StepDiffResponse(ApiModel):
    change_type: Literal["ADDED", "REMOVED", "CHANGED", "MOVED"]
    before: TestStepResponse | None
    after: TestStepResponse | None


class TestCaseVersionDiffResponse(ApiModel):
    test_case_id: str
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    field_changes: list[FieldDiffResponse]
    step_changes: list[StepDiffResponse]


class RestoreTestCaseVersionRequest(RequestModel):
    baseVersion: int = Field(ge=1)
    changeReason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    source: SourceMetadata


class RestoreTestCaseVersionResponse(ApiModel):
    test_case: TestCaseResourceResponse
    restored_from_version: int = Field(ge=1)
    audit_id: str


class MoveTestCaseRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    targetWorkItemId: str
    targetSortOrder: int = Field(ge=0)
    changeReason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    source: SourceMetadata


class MoveTestCaseResponse(ApiModel):
    operation: Literal["MOVE"]
    test_case: TestCaseResourceResponse
    previous_work_item_id: str
    previous_category_id: str
    previous_sort_order: int = Field(ge=0)
    audit_id: str | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None
    )


class CopyTestCaseRequest(RequestModel):
    sourceVersion: int = Field(ge=1)
    targetWorkItemId: str
    targetSortOrder: int = Field(ge=0)
    summary: Annotated[
        str, Field(min_length=1, max_length=200, pattern=r"\S")
    ] | SkipJsonSchema[None] = None
    changeReason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    source: SourceMetadata

    @model_validator(mode="after")
    def reject_null_summary(self) -> Self:
        if "summary" in self.model_fields_set and self.summary is None:
            raise ValueError("summary cannot be null")
        return self


class CopyTestCaseResponse(ApiModel):
    operation: Literal["COPY"]
    test_case: TestCaseResourceResponse
    copied_from: CopiedFromResponse
    audit_id: str


class SoftDeleteTestCaseRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    source: SourceMetadata


class SoftDeleteTestCaseResponse(ApiModel):
    operation: Literal["SOFT_DELETE"]
    test_case: TestCaseResourceResponse
    audit_id: str


class RestoreDeletedTestCaseRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    targetWorkItemId: str | SkipJsonSchema[None] = None
    targetSortOrder: Annotated[int, Field(ge=0)] | SkipJsonSchema[None] = None
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    source: SourceMetadata

    @model_validator(mode="after")
    def require_target_for_sort(self) -> Self:
        null_fields = [
            name
            for name in ("targetWorkItemId", "targetSortOrder")
            if name in self.model_fields_set and getattr(self, name) is None
        ]
        if null_fields:
            raise ValueError(f"fields cannot be null: {', '.join(null_fields)}")
        if self.targetSortOrder is not None and self.targetWorkItemId is None:
            raise ValueError("targetSortOrder requires targetWorkItemId")
        return self


class RestoreDeletedTestCaseResponse(ApiModel):
    operation: Literal["RESTORE_DELETED"]
    test_case: TestCaseResourceResponse
    audit_id: str


class PermanentDeleteTestCaseRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    source: SourceMetadata


class PermanentDeleteTestCaseResponse(ApiModel):
    operation: Literal["PERMANENT_DELETE"]
    test_case_id: str
    deleted_attachment_count: Literal[0]
    audit_id: str
