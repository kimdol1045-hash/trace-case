"""Strict Sprint 4 issue, private object-storage, and reporting wire models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from tc_api.models import ApiModel, RequestModel

IssueStatusValue = Literal["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED", "REOPENED"]
IssueSeverityValue = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ExecutionPlatformValue = Literal["WEB", "AOS", "IOS"]
ExecutionStatusValue = Literal["UNEXECUTED", "PASS", "FAIL", "BLOCKED"]
ExecutionAggregateStatusValue = Literal["UNEXECUTED", "PASS", "FAIL"]
CycleStatusValue = Literal["PLANNED", "IN_PROGRESS", "COMPLETED"]
AttachmentMimeTypeValue = Literal[
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "video/mp4",
    "video/webm",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
    "application/zip",
    "application/gzip",
]


class IssueStepPayload(ApiModel):
    sequence: int = Field(ge=1)
    action: str = Field(min_length=1, max_length=20_000, pattern=r"\S")
    expected_result: str = Field(min_length=1, max_length=20_000, pattern=r"\S")


class TestCaseLinkTarget(RequestModel):
    type: Literal["TEST_CASE"]
    testCaseId: str = Field(pattern=r"^case_[A-Za-z0-9_-]+$", max_length=128)


class CycleItemLinkTarget(RequestModel):
    type: Literal["CYCLE_ITEM"]
    cycleItemId: str = Field(pattern=r"^cycle_item_[A-Za-z0-9_-]+$", max_length=128)


class ExecutionAttemptLinkTarget(RequestModel):
    type: Literal["EXECUTION_ATTEMPT"]
    executionAttemptId: str = Field(pattern=r"^attempt_[A-Za-z0-9_-]+$", max_length=128)


IssueLinkTarget = Annotated[
    TestCaseLinkTarget | CycleItemLinkTarget | ExecutionAttemptLinkTarget,
    Field(discriminator="type"),
]


class ManualIssueOrigin(ApiModel):
    type: Literal["MANUAL"] = "MANUAL"


class ExecutionAttemptIssueOrigin(ApiModel):
    type: Literal["EXECUTION_ATTEMPT"] = "EXECUTION_ATTEMPT"
    execution_attempt_id: str


class ExecutionIssueSnapshot(ApiModel):
    source_attempt_id: str
    source_attempt_status: Literal["FAIL", "BLOCKED"]
    cycle_id: str
    cycle_item_id: str
    test_case_id: str
    pinned_version_id: str
    pinned_version: int = Field(ge=1)
    platform: ExecutionPlatformValue
    summary: str
    precondition: str
    steps: list[IssueStepPayload]
    expected_result: str
    actual_result: str
    note: str | None
    attachment_ids: list[str]
    captured_at: datetime


class IssueResource(ApiModel):
    id: str
    key: str
    summary: str
    precondition: str
    steps: list[IssueStepPayload]
    expected_result: str
    actual_result: str
    status: IssueStatusValue
    severity: IssueSeverityValue
    assignee_label: str | None
    origin: ManualIssueOrigin | ExecutionAttemptIssueOrigin
    reproduction_snapshot: ExecutionIssueSnapshot | None
    resource_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class CreateIssueRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    summary: str = Field(min_length=1, max_length=200, pattern=r"\S")
    precondition: str = Field(min_length=1, max_length=20_000, pattern=r"\S")
    steps: list[IssueStepPayload] = Field(min_length=1, max_length=200)
    expectedResult: str = Field(min_length=1, max_length=20_000, pattern=r"\S")
    actualResult: str = Field(min_length=1, max_length=20_000, pattern=r"\S")
    severity: IssueSeverityValue
    assigneeLabel: str | None = Field(default=None, min_length=1, max_length=200)
    initialLinks: list[IssueLinkTarget] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def contiguous_steps(self) -> Self:
        if [step.sequence for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("step sequence values must be contiguous from 1")
        return self


class CreateIssueFromAttemptRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    severity: IssueSeverityValue
    assigneeLabel: str | None = Field(default=None, min_length=1, max_length=200)


class UpdateIssueRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    summary: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"\S")
    precondition: str | None = Field(default=None, min_length=1, max_length=20_000, pattern=r"\S")
    steps: list[IssueStepPayload] | None = Field(default=None, min_length=1, max_length=200)
    expectedResult: str | None = Field(default=None, min_length=1, max_length=20_000, pattern=r"\S")
    actualResult: str | None = Field(default=None, min_length=1, max_length=20_000, pattern=r"\S")
    status: IssueStatusValue | None = None
    severity: IssueSeverityValue | None = None
    assigneeLabel: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=500, pattern=r"\S")

    @model_validator(mode="after")
    def require_patch(self) -> Self:
        mutable = self.model_fields_set.difference({"baseResourceVersion", "reason"})
        if not mutable:
            raise ValueError("at least one mutable issue field is required")
        if self.steps is not None and [step.sequence for step in self.steps] != list(
            range(1, len(self.steps) + 1)
        ):
            raise ValueError("step sequence values must be contiguous from 1")
        return self


class DeleteIssueRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class DeleteIssueResponse(ApiModel):
    operation: Literal["SOFT_DELETE"]
    issue_id: str
    resource_version: int
    deleted_at: datetime
    audit_id: str


class RestoreIssueRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class RestoreIssueResponse(ApiModel):
    operation: Literal["RESTORE"]
    issue: IssueResource
    audit_id: str


class PermanentDeleteIssueRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class PermanentDeleteIssueResponse(ApiModel):
    operation: Literal["PERMANENT_DELETE"]
    issue_id: str
    deleted_attachment_count: int = Field(ge=0)
    audit_id: str


class IssueListItem(ApiModel):
    id: str
    key: str
    summary: str
    status: IssueStatusValue
    severity: IssueSeverityValue
    assignee_label: str | None
    linked_cycle_ids: list[str]
    resource_version: int
    updated_at: datetime
    deleted_at: datetime | None


class IssuePage(ApiModel):
    items: list[IssueListItem]
    next_cursor: str | None
    total: int


class CreateIssueLinkRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    target: IssueLinkTarget


class IssueLinkResource(ApiModel):
    id: str
    issue_id: str
    target: dict[str, str]
    created_by_label: str | None
    created_at: datetime


class IssueLinkPage(ApiModel):
    items: list[IssueLinkResource]
    next_cursor: str | None
    total: int


class DeleteIssueLinkRequest(RequestModel):
    baseIssueResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class DeleteIssueLinkResponse(ApiModel):
    operation: Literal["UNLINK"] = "UNLINK"
    issue_id: str
    issue_link_id: str
    issue_resource_version: int = Field(ge=2)
    deleted_at: datetime
    audit_id: str


class CreateIssueCommentRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    body: str = Field(min_length=1, max_length=20_000, pattern=r"\S")


class IssueCommentResource(ApiModel):
    id: str
    issue_id: str
    body: str
    author_label: str | None
    created_at: datetime
    edited_at: datetime | None = None


class IssueCommentPage(ApiModel):
    items: list[IssueCommentResource]
    next_cursor: str | None
    total: int


class IssueHistoryChange(ApiModel):
    field: str
    before: Any
    after: Any


class IssueHistoryEvent(ApiModel):
    id: str
    issue_id: str
    event_type: Literal[
        "CREATED",
        "UPDATED",
        "STATUS_CHANGED",
        "LINK_ADDED",
        "LINK_REMOVED",
        "COMMENT_ADDED",
        "ATTACHMENT_ADDED",
        "ATTACHMENT_DELETED",
        "DELETED",
    ]
    actor_label: str | None
    changes: list[IssueHistoryChange]
    related_resource_id: str | None
    occurred_at: datetime


class IssueHistoryPage(ApiModel):
    items: list[IssueHistoryEvent]
    next_cursor: str | None
    total: int


class AttachmentOwner(ApiModel):
    type: Literal["ISSUE", "EXECUTION_ATTEMPT"]
    issue_id: str | None = None
    execution_attempt_id: str | None = None


class AttachmentResource(ApiModel):
    id: str
    owner: AttachmentOwner
    filename: str
    mime_type: AttachmentMimeTypeValue
    size_bytes: int
    sha256: str
    storage_provider: Literal["R2", "GOOGLE_DRIVE"]
    scan_status: Literal["CLEAN", "LEGACY_MIGRATION_REQUIRED", "ERROR"]
    privacy: Literal["PRIVATE"] = "PRIVATE"
    download_transport: Literal["BACKEND_PROXY"] = "BACKEND_PROXY"
    proxy_path: str
    resource_version: int
    uploaded_at: datetime
    deleted_at: datetime | None


class AttachmentUploadResponse(ApiModel):
    attachment: AttachmentResource
    audit_id: str


class AttachmentUploadSessionRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    filename: str = Field(min_length=1, max_length=500, pattern=r"\S")
    declaredMimeType: AttachmentMimeTypeValue
    declaredSizeBytes: int = Field(ge=1, le=50 * 1024 * 1024)
    clientSha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AttachmentUploadTarget(ApiModel):
    method: Literal["PUT"] = "PUT"
    url: str
    headers: dict[str, str]
    expires_at: datetime


class AttachmentUploadSessionResponse(ApiModel):
    upload_token: str
    object_key: str
    upload: AttachmentUploadTarget
    max_size_bytes: int


class CompleteAttachmentUploadRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    uploadToken: str = Field(min_length=32, max_length=16_384)


class ObjectStorageStatusResource(ApiModel):
    status: Literal["READY", "SETUP_REQUIRED"]
    provider: Literal["R2", "MINIO"]
    bucket: str | None
    max_file_bytes: int
    upload_transport: Literal["PRESIGNED_PUT"] = "PRESIGNED_PUT"
    download_transport: Literal["BACKEND_PROXY"] = "BACKEND_PROXY"
    privacy: Literal["PRIVATE"] = "PRIVATE"
    scan_policy: Literal["FAIL_CLOSED_CLAMAV"] = "FAIL_CLOSED_CLAMAV"
    legacy_row_policy: Literal["MIGRATION_REQUIRED"] = "MIGRATION_REQUIRED"
    legacy_row_count: int = Field(ge=0)


class StorageUsageObservation(ApiModel):
    storage_bytes: int = Field(ge=0)
    class_a_requests: int = Field(ge=0)
    class_b_requests: int = Field(ge=0)
    storage_budget_bytes: int = Field(ge=1)
    class_a_request_budget: int = Field(ge=1)
    class_b_request_budget: int = Field(ge=1)
    warning: QuotaWarning
    estimated_monthly_cost_usd: float = Field(ge=0)
    estimate_quality: Literal["PROCESS_LOCAL_CONFIGURED_ESTIMATE"]
    observed_since: datetime


class MetadataIntegrityRequest(RequestModel):
    dryRun: bool = True
    cursor: str | None = Field(default=None, min_length=1, max_length=1024)
    limit: int = Field(default=100, ge=1, le=1000)


class MetadataIntegrityItem(ApiModel):
    attachment_id: str
    object_key: str
    status: Literal["CONSISTENT", "MISSING", "METADATA_MISMATCH", "FAILED_RETRYABLE"]
    detail: str | None


class MetadataIntegrityResponse(ApiModel):
    dry_run: bool
    scanned_count: int = Field(ge=0)
    consistent_count: int = Field(ge=0)
    mismatch_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    items: list[MetadataIntegrityItem]
    next_cursor: str | None
    completed_at: datetime


class RestoreSampleRequest(RequestModel):
    sampleSize: int = Field(default=10, ge=1, le=100)
    maxBytesPerObject: int = Field(default=50 * 1024 * 1024, ge=1)


class RestoreSampleItem(ApiModel):
    attachment_id: str
    object_key: str
    status: Literal["VERIFIED", "MISSING", "CHECKSUM_MISMATCH", "SKIPPED_TOO_LARGE", "FAILED_RETRYABLE"]
    expected_sha256: str
    observed_sha256: str | None
    observed_size_bytes: int | None


class RestoreSampleResponse(ApiModel):
    requested_count: int = Field(ge=1)
    sampled_count: int = Field(ge=0)
    verified_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    items: list[RestoreSampleItem]
    completed_at: datetime


class LegacyAttachmentInventoryItem(ApiModel):
    attachment_id: str
    owner: AttachmentOwner
    filename: str
    mime_type: AttachmentMimeTypeValue
    size_bytes: int = Field(ge=0)
    sha256: str
    migration_state: Literal["MIGRATION_REQUIRED"] = "MIGRATION_REQUIRED"


class LegacyAttachmentInventory(ApiModel):
    policy: Literal["MIGRATION_REQUIRED"] = "MIGRATION_REQUIRED"
    total: int = Field(ge=0)
    items: list[LegacyAttachmentInventoryItem]
    next_cursor: str | None


class AttachmentPage(ApiModel):
    items: list[AttachmentResource]
    next_cursor: str | None
    total: int


class SoftDeleteAttachmentRequest(RequestModel):
    baseResourceVersion: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class SoftDeleteAttachmentResponse(ApiModel):
    operation: Literal["SOFT_DELETE"] = "SOFT_DELETE"
    attachment_id: str
    resource_version: int
    deleted_at: datetime
    storage_delete_state: Literal["PENDING", "DELETED", "FAILED_RETRYABLE"]
    audit_id: str


class QuotaWarning(ApiModel):
    level: Literal["NONE", "WARNING_70", "WARNING_85", "WARNING_95", "EXCEEDED"]
    threshold_percent: Literal[70, 85, 95, 100] | None
    message: str | None


class OrphanReconcileRequest(RequestModel):
    idempotencyKey: str = Field(min_length=8, max_length=255)
    dryRun: bool
    olderThan: datetime
    cursor: str | None = Field(default=None, min_length=1, max_length=1024)
    limit: int = Field(default=100, ge=1, le=1000)


class OrphanReconcileItem(ApiModel):
    orphan_key: str
    path: str
    detected_at: datetime
    action: Literal["WOULD_DELETE", "DELETED", "RETAINED", "FAILED_RETRYABLE"]
    error_code: str | None


class OrphanReconcileResponse(ApiModel):
    dry_run: bool
    scanned_count: int = Field(ge=0)
    orphan_count: int = Field(ge=0)
    deleted_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    items: list[OrphanReconcileItem]
    next_cursor: str | None
    completed_at: datetime


class ExecutionStatusCounts(ApiModel):
    UNEXECUTED: int = Field(ge=0)
    PASS: int = Field(ge=0)
    FAIL: int = Field(ge=0)
    BLOCKED: int = Field(ge=0)
    total: int = Field(ge=0)
    executed: int = Field(ge=0)
    terminal: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    aggregate_status: ExecutionAggregateStatusValue


class PlatformKpi(ApiModel):
    platform: ExecutionPlatformValue
    counts: ExecutionStatusCounts


class IssueSeverityKpi(ApiModel):
    severity: IssueSeverityValue
    total: int
    open: int
    resolved: int
    closed: int


class WorkItemKpiRef(ApiModel):
    work_item_id: str
    category_id: str
    category_name: str
    key: str
    summary: str


class CycleKpi(ApiModel):
    cycle_id: str
    cycle_name: str
    cycle_status: Literal["PLANNED", "IN_PROGRESS", "COMPLETED"]
    case_count: int
    platform_slot_count: int
    work_items: list[WorkItemKpiRef]
    execution: ExecutionStatusCounts
    platforms: list[PlatformKpi]
    issue_severities: list[IssueSeverityKpi]


class KpiFilterEcho(ApiModel):
    cycle_ids: list[str]
    work_item_ids: list[str]
    cycle_statuses: list[CycleStatusValue]
    platforms: list[ExecutionPlatformValue]
    execution_statuses: list[ExecutionStatusValue]
    issue_severities: list[IssueSeverityValue]
    issue_statuses: list[IssueStatusValue]
    from_: datetime | None = Field(serialization_alias="from", validation_alias="from")
    to: datetime | None


class OverviewKpiResponse(ApiModel):
    filters: KpiFilterEcho
    cycle_count: int
    case_count: int
    platform_slot_count: int
    work_items: list[WorkItemKpiRef]
    execution: ExecutionStatusCounts
    platforms: list[PlatformKpi]
    issue_severities: list[IssueSeverityKpi]
    generated_at: datetime


class CycleReportPageResponse(ApiModel):
    filters: KpiFilterEcho
    items: list[CycleKpi]
    next_cursor: str | None
    total: int
    generated_at: datetime


# Used only to omit absent nullable patch members from OpenAPI where appropriate.
OptionalPatchString = Annotated[str, Field(min_length=1)] | SkipJsonSchema[None]
