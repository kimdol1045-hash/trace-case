"""Stable domain failures raised by transaction-scoped repositories."""

from __future__ import annotations

import uuid


class DomainError(RuntimeError):
    code = "DOMAIN_ERROR"


class ResourceNotFoundError(DomainError):
    code = "RESOURCE_NOT_FOUND"

    def __init__(self, resource_type: str, resource_id: uuid.UUID) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(f"{resource_type} {resource_id} was not found")


class VersionConflictError(DomainError):
    code = "VERSION_CONFLICT"

    def __init__(self, resource_type: str, resource_id: uuid.UUID, expected: int, actual: int) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"{resource_type} {resource_id} version conflict: expected {expected}, actual {actual}"
        )


class FolderNameConflictError(DomainError):
    code = "FOLDER_NAME_CONFLICT"


class FolderCycleError(DomainError):
    code = "FOLDER_CYCLE"


class FolderParentDeletedError(DomainError):
    code = "FOLDER_PARENT_DELETED"


class FolderRestoreConflictError(DomainError):
    code = "FOLDER_RESTORE_CONFLICT"


class FolderAliasConflictError(DomainError):
    code = "FOLDER_ALIAS_CONFLICT"


class FolderAliasTargetDeletedError(DomainError):
    code = "FOLDER_ALIAS_TARGET_DELETED"


class SystemFolderProtectedError(DomainError):
    code = "SYSTEM_FOLDER_PROTECTED"


class FolderContentWarning(DomainError):
    code = "FOLDER_CONTENT_WARNING"

    def __init__(self, *, folder_count: int, test_case_count: int) -> None:
        self.folder_count = folder_count
        self.test_case_count = test_case_count
        super().__init__(
            f"folder subtree contains {folder_count} folders and {test_case_count} test cases"
        )


class FolderNotEmptyError(FolderContentWarning):
    code = "FOLDER_NOT_EMPTY"


class KeyAllocationError(DomainError):
    code = "KEY_ALLOCATION_FAILED"


class WorkItemKeyConflictError(DomainError):
    code = "WORK_ITEM_KEY_CONFLICT"

    def __init__(self, work_item_key: str) -> None:
        self.work_item_key = work_item_key
        super().__init__(f"work item key {work_item_key} is already in use")


class WorkItemKeyNotFoundError(DomainError):
    code = "RESOURCE_NOT_FOUND"

    def __init__(self, work_item_key: str) -> None:
        self.work_item_key = work_item_key
        super().__init__(f"work item {work_item_key} was not found")


class CategoryInvariantError(DomainError):
    code = "INVALID_CATEGORY"


class TestCaseDeletedError(DomainError):
    code = "TEST_CASE_DELETED"


class TestCaseRestoreConflictError(DomainError):
    code = "TEST_CASE_RESTORE_CONFLICT"


class GenerationManagementConflictError(DomainError):
    code = "GENERATION_MANAGEMENT_CONFLICT"

    def __init__(self, test_case_id: uuid.UUID, reason: str) -> None:
        self.test_case_id = test_case_id
        self.reason = reason
        super().__init__(f"test case {test_case_id} generation management conflict: {reason}")


class PermanentDeleteConflictError(DomainError):
    code = "PERMANENT_DELETE_CONFLICT"

    def __init__(
        self,
        resource_type: str,
        resource_id: uuid.UUID,
        reason: str,
        *,
        references: dict[str, int] | None = None,
    ) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.reason = reason
        self.references = references or {}
        super().__init__(
            f"{resource_type} {resource_id} cannot be permanently deleted: {reason}"
        )


class CycleStateError(DomainError):
    code = "INVALID_CYCLE_STATE"


class InvalidCycleTransitionError(CycleStateError):
    code = "INVALID_CYCLE_TRANSITION"


class CycleScopeError(DomainError):
    code = "INVALID_CYCLE_SCOPE"


class CycleSnapshotEmptyError(CycleScopeError):
    code = "EMPTY_CYCLE_SNAPSHOT"


class ExecutionStateError(DomainError):
    code = "INVALID_EXECUTION_STATE"


class ExecutionValidationError(DomainError):
    code = "INVALID_EXECUTION_RESULT"


class ActualResultRequiredError(ExecutionValidationError):
    code = "ACTUAL_RESULT_REQUIRED"


class IssueStateError(DomainError):
    code = "INVALID_ISSUE_STATE"


class InvalidIssueTransitionError(IssueStateError):
    code = "INVALID_ISSUE_TRANSITION"


class IssueDeletedError(IssueStateError):
    code = "ISSUE_DELETED"


class IssueRestoreConflictError(IssueStateError):
    code = "ISSUE_RESTORE_CONFLICT"


class IssueLinkError(DomainError):
    code = "INVALID_ISSUE_LINK"


class IssueLinkCycleError(IssueLinkError):
    code = "ISSUE_LINK_CYCLE_MISMATCH"


class IssueSourceAttemptError(IssueLinkError):
    code = "INVALID_ISSUE_SOURCE_ATTEMPT"


class AttachmentStateError(DomainError):
    code = "INVALID_ATTACHMENT_STATE"


class AttachmentDeletedError(AttachmentStateError):
    code = "ATTACHMENT_DELETED"


class AttachmentOwnerError(DomainError):
    code = "INVALID_ATTACHMENT_OWNER"


class SlackLedgerError(DomainError):
    code = "SLACK_LEDGER_ERROR"


class SlackChannelDeniedError(SlackLedgerError):
    code = "SLACK_CHANNEL_DENIED"


class SlackPrivateChannelDeniedError(SlackChannelDeniedError):
    code = "SLACK_PRIVATE_CHANNEL_DENIED"


class SlackThreadSyncError(SlackLedgerError):
    code = "SLACK_THREAD_SYNC_ERROR"
