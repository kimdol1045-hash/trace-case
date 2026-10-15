"""Stable domain-to-HTTP error mapping."""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from tc_api.dependencies import DatabaseUnavailableError
from tc_api.ids import IdentifierError, encode_identifier, encode_resource_identifier
from tc_api.models import ErrorResponse
from tc_backend.db.errors import (
    ActualResultRequiredError,
    CategoryInvariantError,
    DomainError,
    FolderAliasConflictError,
    FolderAliasTargetDeletedError,
    FolderContentWarning,
    FolderCycleError,
    FolderNameConflictError,
    FolderNotEmptyError,
    FolderParentDeletedError,
    FolderRestoreConflictError,
    GenerationManagementConflictError,
    InvalidIssueTransitionError,
    IssueLinkCycleError,
    IssueLinkError,
    IssueSourceAttemptError,
    PermanentDeleteConflictError,
    ResourceNotFoundError,
    SystemFolderProtectedError,
    TestCaseDeletedError,
    TestCaseRestoreConflictError,
    VersionConflictError,
    WorkItemKeyConflictError,
    WorkItemKeyNotFoundError,
)
from tc_backend.infrastructure.object_storage.errors import (
    AttachmentValidationError,
    ChecksumMismatch,
    FileSizeMismatch,
    FileTooLarge,
    LegacyAttachmentMigrationRequired,
    MalwareDetected,
    MalwareScannerUnavailable,
    MimeContentMismatch,
    MimeTypeNotAllowed,
    ObjectNotFound,
    ObjectStorageError,
    ObjectStorageSetupRequired,
    UnsafeFilename,
)
from tc_backend.infrastructure.slack.errors import (
    SlackIntegrationDisabled,
    SlackPayloadError,
    SlackRequestVerificationError,
    SlackSetupRequired,
)
from tc_backend.generation.lifecycle import GenerationSetupRequired
from tc_backend.generation.retry_history import RetryHistoryError
from tc_backend.generation.reconstruction import GenerationInputUnavailable
from tc_backend.generation.application import (
    GenerationApplyIdempotencyConflict,
    GenerationApplyTransactionFailed,
    GenerationJobAlreadyApplied,
    GenerationPlacementConflict,
    GenerationStoredResultInvalid,
    GenerationSuggestionApplyConflict,
)
from tc_backend.generation.suggestions import GenerationSuggestionConflict
from tc_backend.generation.quality_review import QualityReviewError
from tc_backend.repositories.generation_repository import GenerationJobStateConflict


class IdempotencyConflictError(RuntimeError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__("idempotency key was already used with a different request")


class AuditCursorError(ValueError):
    pass


class TestCaseCursorError(ValueError):
    pass


class CycleCursorError(ValueError):
    pass


class GenerationCursorError(ValueError):
    pass


class CycleApiError(RuntimeError):
    """A canonical Sprint 3 conflict/warning that carries its wire details."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any],
        *,
        status: int = 409,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details
        self.status = status
        super().__init__(message)


class LabelConflictError(RuntimeError):
    def __init__(self, labels: list[str], normalized_label: str) -> None:
        self.labels = labels
        self.normalized_label = normalized_label
        super().__init__("labels must be unique after normalization")


class ExpectedResultModeError(RuntimeError):
    def __init__(self, expected_result_mode: str, source_type: str) -> None:
        self.expected_result_mode = expected_result_mode
        self.source_type = source_type
        super().__init__("expected result mode does not match its source or step shape")


def register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(QualityReviewError)
    async def quality_review_handler(
        request: Request, error: QualityReviewError
    ) -> JSONResponse:
        return _response(
            request,
            status=error.status_code,
            code=error.code,
            message="The quality review request could not be completed.",
            details={},
        )

    @application.exception_handler(RetryHistoryError)
    async def retry_history_handler(
        request: Request, error: RetryHistoryError
    ) -> JSONResponse:
        details: dict[str, Any] = {}
        if error.winner_job_id is not None:
            details["winningJobId"] = encode_identifier(
                "generation_job", error.winner_job_id
            )
        return _response(
            request,
            status=error.status_code,
            code=error.code,
            message="The automatic generation request could not be completed.",
            details=details,
        )

    @application.exception_handler(SlackRequestVerificationError)
    async def slack_verification_handler(
        request: Request, error: SlackRequestVerificationError
    ) -> JSONResponse:
        return _response(
            request,
            status=401,
            code="SLACK_SIGNATURE_INVALID",
            message="Slack request authentication failed.",
            details={"reason": str(error)},
        )

    @application.exception_handler(SlackPayloadError)
    async def slack_payload_handler(
        request: Request, error: SlackPayloadError
    ) -> JSONResponse:
        return _response(
            request,
            status=422,
            code="SLACK_PAYLOAD_INVALID",
            message="Slack request payload is invalid.",
            details={"reason": str(error)},
        )

    @application.exception_handler(SlackIntegrationDisabled)
    @application.exception_handler(SlackSetupRequired)
    async def slack_setup_handler(
        request: Request, error: SlackIntegrationDisabled | SlackSetupRequired
    ) -> JSONResponse:
        return _response(
            request,
            status=503,
            code=(
                "SLACK_DISABLED"
                if isinstance(error, SlackIntegrationDisabled)
                else "SLACK_SETUP_REQUIRED"
            ),
            message=str(error),
            details={"connectionStatus": "SETUP_REQUIRED"},
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        violations = [
            {
                "field": ".".join(str(part) for part in item["loc"] if part != "body"),
                "reason": item["msg"],
            }
            for item in error.errors()
        ]
        return _response(
            request,
            status=422,
            code="VALIDATION_FAILED",
            message="Request validation failed.",
            details={"fieldViolations": violations},
        )

    @application.exception_handler(IdentifierError)
    async def identifier_handler(
        request: Request, error: IdentifierError
    ) -> JSONResponse:
        return _response(
            request,
            status=422,
            code="VALIDATION_FAILED",
            message="Request validation failed.",
            details={
                "fieldViolations": [
                    {"field": error.field, "reason": str(error)},
                ]
            },
        )

    @application.exception_handler(ValueError)
    async def value_error_handler(request: Request, error: ValueError) -> JSONResponse:
        return _response(
            request,
            status=422,
            code="VALIDATION_FAILED",
            message="Request validation failed.",
            details={"fieldViolations": [{"field": "request", "reason": str(error)}]},
        )

    @application.exception_handler(AuditCursorError)
    async def audit_cursor_handler(
        request: Request, error: AuditCursorError
    ) -> JSONResponse:
        del error
        return _response(
            request,
            status=422,
            code="AUDIT_CURSOR_INVALID",
            message="The audit cursor is invalid.",
            details={"cursor": request.query_params.get("cursor", "")},
        )

    @application.exception_handler(TestCaseCursorError)
    async def test_case_cursor_handler(
        request: Request, error: TestCaseCursorError
    ) -> JSONResponse:
        del error
        return _response(
            request,
            status=422,
            code="CURSOR_INVALID",
            message="The cursor does not match the requested filters.",
            details={"cursor": request.query_params.get("cursor", "")},
        )

    @application.exception_handler(CycleCursorError)
    async def cycle_cursor_handler(
        request: Request, error: CycleCursorError
    ) -> JSONResponse:
        del error
        return _response(
            request,
            status=422,
            code="CURSOR_INVALID",
            message="The cursor does not match the requested cycle filters.",
            details={"cursor": request.query_params.get("cursor", "")},
        )

    @application.exception_handler(CycleApiError)
    async def cycle_api_error_handler(
        request: Request, error: CycleApiError
    ) -> JSONResponse:
        return _response(
            request,
            status=error.status,
            code=error.code,
            message=error.message,
            details=error.details,
        )

    @application.exception_handler(LabelConflictError)
    async def label_conflict_handler(
        request: Request, error: LabelConflictError
    ) -> JSONResponse:
        return _response(
            request,
            status=409,
            code="LABEL_CONFLICT",
            message="Labels must be unique after Unicode and case normalization.",
            details={"labels": error.labels, "normalizedLabel": error.normalized_label},
        )

    @application.exception_handler(ExpectedResultModeError)
    async def expected_mode_handler(
        request: Request, error: ExpectedResultModeError
    ) -> JSONResponse:
        return _response(
            request,
            status=409,
            code="EXPECTED_RESULT_MODE_INVALID",
            message="The expected-result mode is invalid for the supplied source and steps.",
            details={
                "expectedResultMode": error.expected_result_mode,
                "sourceType": error.source_type,
            },
        )

    @application.exception_handler(IdempotencyConflictError)
    @application.exception_handler(GenerationApplyIdempotencyConflict)
    async def idempotency_handler(
        request: Request,
        error: IdempotencyConflictError | GenerationApplyIdempotencyConflict,
    ) -> JSONResponse:
        return _response(
            request,
            status=409,
            code="IDEMPOTENCY_CONFLICT",
            message="Idempotency key conflicts with an earlier request.",
            details={"idempotencyKey": error.key},
        )

    @application.exception_handler(GenerationCursorError)
    async def generation_cursor_handler(
        request: Request, error: GenerationCursorError
    ) -> JSONResponse:
        del error
        return _response(
            request,
            status=422,
            code="CURSOR_INVALID",
            message="The cursor does not match the requested generation job filters.",
            details={"cursor": request.query_params.get("cursor", "")},
        )

    @application.exception_handler(GenerationSetupRequired)
    async def generation_setup_handler(
        request: Request, error: GenerationSetupRequired
    ) -> JSONResponse:
        return _response(
            request,
            status=503,
            code="GENERATION_SETUP_REQUIRED",
            message=str(error),
            details={
                "jobId": encode_identifier("generation_job", error.job_id),
                "setupState": "SETUP_REQUIRED",
                "blockingDecisions": [
                    "PROVIDER_SELECTION",
                    "ENDPOINT",
                    "CREDENTIALS",
                    "COST_APPROVAL",
                ],
                "externalCallAttempted": False,
                "manualCaseCreationAvailable": True,
            },
        )

    @application.exception_handler(GenerationInputUnavailable)
    async def generation_input_unavailable_handler(
        request: Request, error: GenerationInputUnavailable
    ) -> JSONResponse:
        return _response(
            request,
            status=409,
            code="GENERATION_INPUT_UNAVAILABLE",
            message=str(error),
            details={
                "jobId": encode_identifier("generation_job", error.job_id),
                "reason": error.reason.value,
                "externalCallAttempted": False,
            },
        )

    @application.exception_handler(GenerationJobStateConflict)
    async def generation_state_handler(
        request: Request, error: GenerationJobStateConflict
    ) -> JSONResponse:
        return _response(
            request,
            status=409,
            code="GENERATION_JOB_STATE_CONFLICT",
            message="The generation job is not eligible for this operation.",
            details={
                "jobId": encode_identifier("generation_job", error.job_id),
                "operation": error.operation,
                "actualStatus": error.actual_status,
                "allowedStatuses": list(error.allowed_statuses),
                **({"ledgerWriteStarted": False} if error.operation == "APPLY" else {}),
            },
        )

    @application.exception_handler(GenerationStoredResultInvalid)
    async def generation_stored_result_handler(
        request: Request, error: GenerationStoredResultInvalid
    ) -> JSONResponse:
        return _response(
            request,
            status=409,
            code="GENERATION_STORED_RESULT_INVALID",
            message="The stored generation result failed trusted validation.",
            details={
                "jobId": encode_identifier("generation_job", error.job_id),
                "stage": error.stage,
                "reason": error.reason,
                "ledgerWriteStarted": False,
            },
        )

    @application.exception_handler(GenerationPlacementConflict)
    async def generation_placement_handler(
        request: Request, error: GenerationPlacementConflict
    ) -> JSONResponse:
        details: dict[str, Any] = {
            "jobId": encode_identifier("generation_job", error.job_id),
            "reason": error.reason,
            "ledgerWriteStarted": False,
        }
        if error.generated_case_index is not None:
            details["generatedCaseIndex"] = error.generated_case_index
        if error.resource_type is not None and error.resource_id is not None:
            details["resourceType"] = error.resource_type
            prefix = {
                "CATEGORY": "folder",
                "WORK_ITEM": "work_item",
                "TEST_CASE": "case",
            }[error.resource_type]
            details["resourceId"] = encode_identifier(prefix, error.resource_id)
        return _response(
            request,
            status=409,
            code="GENERATION_PLACEMENT_CONFLICT",
            message="The generation placement is stale or cannot be reproduced.",
            details=details,
        )

    @application.exception_handler(GenerationJobAlreadyApplied)
    async def generation_already_applied_handler(
        request: Request, error: GenerationJobAlreadyApplied
    ) -> JSONResponse:
        return _response(
            request,
            status=409,
            code="GENERATION_JOB_ALREADY_APPLIED",
            message="The generation job already has a committed apply receipt.",
            details={
                "jobId": encode_identifier("generation_job", error.job_id),
                "applyId": encode_identifier("generation_apply", error.apply_id),
            },
        )

    @application.exception_handler(GenerationSuggestionApplyConflict)
    async def generation_suggestion_apply_handler(
        request: Request, error: GenerationSuggestionApplyConflict
    ) -> JSONResponse:
        return _response(
            request,
            status=409,
            code="GENERATION_SUGGESTION_CONFLICT",
            message="Suggestion jobs cannot use whole-batch generation apply.",
            details={
                "jobId": encode_identifier("generation_job", error.job_id),
                "reason": error.reason,
                "ledgerWriteStarted": False,
            },
        )

    @application.exception_handler(GenerationSuggestionConflict)
    async def generation_suggestion_conflict_handler(
        request: Request, error: GenerationSuggestionConflict
    ) -> JSONResponse:
        return _response(
            request,
            status=409,
            code="GENERATION_SUGGESTION_CONFLICT",
            message="The generation suggestion is not eligible for this operation.",
            details={
                "reason": error.reason,
                **(
                    {
                        "suggestionId": encode_identifier(
                            "generation_suggestion", error.suggestion_id
                        )
                    }
                    if error.suggestion_id is not None
                    else {}
                ),
            },
        )

    @application.exception_handler(GenerationApplyTransactionFailed)
    async def generation_apply_transaction_handler(
        request: Request, error: GenerationApplyTransactionFailed
    ) -> JSONResponse:
        return _response(
            request,
            status=500,
            code="GENERATION_APPLY_TRANSACTION_FAILED",
            message="The generation apply transaction was rolled back.",
            details={
                "jobId": encode_identifier("generation_job", error.job_id),
                "stage": error.stage,
                "transactionStatus": "ROLLED_BACK",
            },
        )

    @application.exception_handler(DomainError)
    async def domain_error_handler(
        request: Request, error: DomainError
    ) -> JSONResponse:
        status, message, details = _domain_error(request, error)
        return _response(
            request,
            status=status,
            code=_domain_code(request, error),
            message=message,
            details=details,
        )

    @application.exception_handler(ObjectStorageError)
    async def object_storage_error_handler(
        request: Request, error: ObjectStorageError
    ) -> JSONResponse:
        if isinstance(error, LegacyAttachmentMigrationRequired):
            status, code = 409, "LEGACY_ATTACHMENT_MIGRATION_REQUIRED"
        elif isinstance(error, MalwareScannerUnavailable):
            status, code = 503, "ATTACHMENT_SCAN_UNAVAILABLE"
        elif isinstance(error, MalwareDetected):
            status, code = 422, "ATTACHMENT_MALWARE_DETECTED"
        elif isinstance(error, UnsafeFilename):
            status, code = 422, "ATTACHMENT_FILENAME_INVALID"
        elif isinstance(error, (MimeTypeNotAllowed, MimeContentMismatch)):
            status, code = 422, "ATTACHMENT_MIME_INVALID"
        elif isinstance(error, (FileTooLarge, FileSizeMismatch)):
            status, code = 413, "ATTACHMENT_SIZE_INVALID"
        elif isinstance(error, ChecksumMismatch):
            status, code = 422, "ATTACHMENT_CHECKSUM_MISMATCH"
        elif isinstance(error, AttachmentValidationError):
            status, code = 422, "ATTACHMENT_VALIDATION_FAILED"
        elif isinstance(error, ObjectNotFound):
            status, code = 409, "ATTACHMENT_OBJECT_MISSING"
        elif isinstance(error, ObjectStorageSetupRequired):
            status, code = 503, "OBJECT_STORAGE_SETUP_REQUIRED"
        else:
            status, code = 503, "OBJECT_STORAGE_UNAVAILABLE"
        return _response(
            request,
            status=status,
            code=code,
            message=str(error),
            details={
                **(
                    {
                        "attachmentId": request.path_params.get(
                            "attachmentId", "attachment_unknown"
                        ),
                        "migrationRequired": True,
                    }
                    if isinstance(error, LegacyAttachmentMigrationRequired)
                    else {}
                ),
                **(
                    {"policy": "FAIL_CLOSED", "quarantined": True}
                    if isinstance(error, (MalwareDetected, MalwareScannerUnavailable))
                    else {}
                ),
            },
        )

    @application.exception_handler(IntegrityError)
    async def integrity_error_handler(
        request: Request, error: IntegrityError
    ) -> JSONResponse:
        if "uq_folders_active_" in str(error.orig):
            return _response(
                request,
                status=409,
                code="FOLDER_NAME_CONFLICT",
                message="An active sibling folder already uses that normalized name.",
                details={"folderId": _folder_path_id(request)},
            )
        return _response(
            request,
            status=409,
            code="DATA_CONFLICT",
            message="The requested change conflicts with current data.",
            details={},
        )

    @application.exception_handler(DatabaseUnavailableError)
    async def database_unavailable_handler(
        request: Request,
        error: DatabaseUnavailableError,
    ) -> JSONResponse:
        del error
        return _response(
            request,
            status=503,
            code="SERVICE_UNAVAILABLE",
            message="Database service is unavailable.",
            details={},
        )


def _domain_error(
    request: Request, error: DomainError
) -> tuple[int, str, dict[str, Any]]:
    if isinstance(error, WorkItemKeyNotFoundError):
        return (
            404,
            "The requested work item was not found.",
            {"resourceType": "WORK_ITEM", "workItemKey": error.work_item_key},
        )
    if isinstance(error, ResourceNotFoundError):
        return (
            404,
            "The requested resource was not found.",
            {
                "resourceType": error.resource_type,
                "resourceId": encode_resource_identifier(
                    error.resource_type, error.resource_id
                ),
            },
        )
    if isinstance(error, VersionConflictError):
        return (
            409,
            "The resource changed after it was loaded.",
            {
                "resourceType": error.resource_type,
                "resourceId": encode_resource_identifier(
                    error.resource_type, error.resource_id
                ),
                "expectedVersion": error.expected,
                "actualVersion": error.actual,
            },
        )
    if isinstance(error, PermanentDeleteConflictError):
        return (
            409,
            "The resource cannot be permanently deleted from its current state.",
            {
                "resourceType": error.resource_type,
                "resourceId": encode_resource_identifier(
                    error.resource_type, error.resource_id
                ),
                "reason": error.reason,
                "references": error.references,
            },
        )
    if isinstance(error, GenerationManagementConflictError):
        return (
            409,
            "The automatic generation operation is not authorized for this test case.",
            {
                "testCaseId": encode_identifier("case", error.test_case_id),
                "reason": error.reason,
            },
        )
    if isinstance(error, WorkItemKeyConflictError):
        return (
            409,
            "The work item key is already in use.",
            {"workItemKey": error.work_item_key},
        )
    if isinstance(error, ActualResultRequiredError):
        return (
            422,
            "FAIL execution results require actualResult.",
            {
                "attemptId": _attempt_path_id(request),
                "status": "FAIL",
                "field": "actualResult",
            },
        )
    if isinstance(error, IssueSourceAttemptError):
        return (
            409,
            "Only failed or blocked attempts can create an issue.",
            {
                "attemptId": _attempt_path_id(request),
                "eligibleStatuses": ["FAIL", "BLOCKED"],
            },
        )
    if isinstance(error, InvalidIssueTransitionError):
        return (
            409,
            "The requested issue status transition is invalid.",
            {"issueId": _issue_path_id(request)},
        )
    if isinstance(error, IssueLinkCycleError):
        return (
            409,
            "Issue evidence cannot be linked across different cycles.",
            {"issueId": _issue_path_id(request)},
        )
    if isinstance(error, IssueLinkError):
        return (
            409,
            "The issue link conflicts with the existing relation ledger.",
            {"issueId": _issue_path_id(request)},
        )
    if isinstance(error, FolderNotEmptyError):
        return (
            409,
            "The folder subtree contains active test cases.",
            {
                "folderId": _folder_path_id(request),
                "activeTestCaseCount": error.test_case_count,
            },
        )
    if isinstance(error, TestCaseDeletedError):
        return (
            409,
            "The test case is already deleted.",
            {"testCaseId": _case_path_id(request)},
        )
    if isinstance(error, TestCaseRestoreConflictError):
        return (
            409,
            "The test case cannot be restored from its current state.",
            {"testCaseId": _case_path_id(request)},
        )
    if isinstance(error, CategoryInvariantError):
        if request.url.path.startswith("/api/work-items"):
            return (
                409,
                "The category must be an active root folder in this workspace.",
                {"categoryId": "folder_unknown"},
            )
        return (
            409,
            "The target work item is not available for this mutation.",
            {
                "testCaseId": _case_path_id(request),
                **(
                    {"targetWorkItemId": request.path_params["targetWorkItemId"]}
                    if "targetWorkItemId" in request.path_params
                    else {}
                ),
            },
        )
    if isinstance(error, FolderContentWarning):
        return (
            409,
            "The folder subtree requires explicit confirmation.",
            {
                "folderId": _folder_path_id(request),
                "affectedFolderCount": error.folder_count,
                "activeTestCaseCount": error.test_case_count,
                "requiresConfirmation": True,
            },
        )
    conflict_messages: list[tuple[type[DomainError], str]] = [
        (FolderCycleError, "A folder cannot be moved below its own subtree."),
        (
            FolderNameConflictError,
            "An active sibling folder already uses that normalized name.",
        ),
        (FolderParentDeletedError, "The target parent folder is deleted."),
        (
            FolderRestoreConflictError,
            "The folder subtree cannot be restored at that location.",
        ),
        (FolderAliasConflictError, "The normalized alias is already reserved."),
        (FolderAliasTargetDeletedError, "The alias target folder is deleted."),
        (SystemFolderProtectedError, "The system folder cannot be changed."),
    ]
    for error_type, message in conflict_messages:
        if isinstance(error, error_type):
            details: dict[str, Any] = {"folderId": _folder_path_id(request)}
            conflict_id = _uuid_from_message(str(error))
            if conflict_id is not None:
                encoded_conflict_id = encode_identifier("folder", conflict_id)
                details["conflictFolderId"] = encoded_conflict_id
                if details["folderId"] == "folder_unknown":
                    details["folderId"] = encoded_conflict_id
            return 409, message, details
    return 409, "The requested domain change conflicts with current state.", {}


def _domain_code(request: Request, error: DomainError) -> str:
    if isinstance(error, CategoryInvariantError) and request.url.path.startswith(
        "/api/work-items"
    ):
        return "WORK_ITEM_CATEGORY_CONFLICT"
    if isinstance(error, ActualResultRequiredError):
        return "ACTUAL_RESULT_REQUIRED"
    if isinstance(error, IssueSourceAttemptError):
        return "ISSUE_CREATION_ATTEMPT_NOT_ELIGIBLE"
    if isinstance(error, InvalidIssueTransitionError):
        return "ISSUE_STATE_TRANSITION_INVALID"
    if isinstance(error, IssueLinkCycleError):
        return "ISSUE_LINK_SOURCE_MISMATCH"
    if isinstance(error, IssueLinkError):
        return (
            "ISSUE_LINK_DUPLICATE"
            if "already exists" in str(error)
            else "ISSUE_LINK_SOURCE_MISMATCH"
        )
    if isinstance(error, TestCaseDeletedError):
        return "TEST_CASE_ALREADY_DELETED"
    if isinstance(error, TestCaseRestoreConflictError):
        return "TEST_CASE_NOT_DELETED"
    if isinstance(error, CategoryInvariantError):
        if request.url.path.endswith("/copy"):
            return "TEST_CASE_COPY_CONFLICT"
        if request.url.path.endswith("/restore"):
            return "TEST_CASE_RESTORE_CONFLICT"
        return "TEST_CASE_MOVE_CONFLICT"
    return error.code


def _folder_path_id(request: Request) -> str:
    value = request.path_params.get("folderId", request.path_params.get("folder_id"))
    if isinstance(value, str) and value.startswith("folder_"):
        return value
    return "folder_unknown"


def _case_path_id(request: Request) -> str:
    value = request.path_params.get("caseId", request.path_params.get("case_id"))
    if isinstance(value, str) and value.startswith("case_"):
        return value
    return "case_unknown"


def _attempt_path_id(request: Request) -> str:
    value = request.path_params.get("attemptId", request.path_params.get("attempt_id"))
    if isinstance(value, str) and value.startswith("attempt_"):
        return value
    return "attempt_unknown"


def _issue_path_id(request: Request) -> str:
    value = request.path_params.get("issueId", request.path_params.get("issue_id"))
    if isinstance(value, str) and value.startswith("issue_"):
        return value
    return "issue_unknown"


def _uuid_from_message(message: str) -> uuid.UUID | None:
    match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        message,
    )
    return uuid.UUID(match.group(0)) if match is not None else None


def _response(
    request: Request,
    *,
    status: int,
    code: str,
    message: str,
    details: dict[str, Any],
) -> JSONResponse:
    payload = ErrorResponse(
        code=code,
        message=message,
        details=details,
        trace_id=_trace_id(request),
    )
    return JSONResponse(
        status_code=status,
        content=payload.model_dump(mode="json", by_alias=True),
        headers={"X-Trace-Id": payload.trace_id},
    )


def _trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", f"trace_{uuid.uuid4().hex}")
