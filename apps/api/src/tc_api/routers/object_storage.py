"""Private R2/MinIO attachments and operational storage verification jobs."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Header, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from tc_api.dependencies import ObjectStorageRuntimeDependency, SessionDependency
from tc_api.errors import IdempotencyConflictError
from tc_api.idempotency import execute_idempotent, pending_audit_id
from tc_api.ids import decode_identifier, encode_identifier
from tc_api.models import ErrorResponse
from tc_api.serializers import audit_context
from tc_api.sprint4_models import (
    AttachmentPage,
    AttachmentUploadResponse,
    AttachmentUploadSessionRequest,
    AttachmentUploadSessionResponse,
    AttachmentUploadTarget,
    CompleteAttachmentUploadRequest,
    LegacyAttachmentInventory,
    LegacyAttachmentInventoryItem,
    MetadataIntegrityItem,
    MetadataIntegrityRequest,
    MetadataIntegrityResponse,
    ObjectStorageStatusResource,
    OrphanReconcileItem,
    OrphanReconcileRequest,
    OrphanReconcileResponse,
    QuotaWarning,
    RestoreSampleItem,
    RestoreSampleRequest,
    RestoreSampleResponse,
    SoftDeleteAttachmentRequest,
    SoftDeleteAttachmentResponse,
    StorageUsageObservation,
)
from tc_api.sprint4_serializers import attachment_owner, attachment_resource
from tc_backend.db.attachments import AttachmentMetadata, AttachmentRepository
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.models import (
    Attachment,
    AttachmentOwnerType,
    AttachmentReconcileStatus,
    IntegrationProvider,
)
from tc_backend.infrastructure.object_storage import (
    AttachmentValidationError,
    ChecksumMismatch,
    FileSizeMismatch,
    LegacyAttachmentMigrationRequired,
    MimeContentMismatch,
    ObjectNotFound,
    ObjectStorageError,
    attachment_content_disposition,
    validate_declaration,
)

router = APIRouter(tags=["object-storage"])
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Attachment owner or metadata not found"},
    409: {"model": ErrorResponse, "description": "Idempotency, metadata, or legacy conflict"},
    413: {"model": ErrorResponse, "description": "Attachment exceeds the configured size"},
    422: {"model": ErrorResponse, "description": "Unsafe attachment or upload claim"},
    503: {"model": ErrorResponse, "description": "Object storage or scanner unavailable"},
}
IdempotencyHeader = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


@router.get(
    "/api/integrations/object-storage/status",
    operation_id="getObjectStorageStatus",
    response_model=ObjectStorageStatusResource,
)
def get_object_storage_status(
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
) -> ObjectStorageStatusResource:
    legacy_count = session.scalar(
        select(func.count())
        .select_from(Attachment)
        .where(Attachment.storage_provider == IntegrationProvider.GOOGLE_DRIVE.value)
    ) or 0
    return ObjectStorageStatusResource(
        status=(
            "READY"
            if runtime.adapter is not None and runtime.scanner is not None
            else "SETUP_REQUIRED"
        ),
        provider=cast(Any, runtime.provider),
        bucket=runtime.bucket,
        max_file_bytes=runtime.max_file_bytes,
        legacy_row_count=legacy_count,
    )


@router.get(
    "/api/integrations/object-storage/observations",
    operation_id="getObjectStorageObservations",
    response_model=StorageUsageObservation,
)
def get_object_storage_observations(
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
) -> StorageUsageObservation:
    storage_bytes = session.scalar(
        select(func.coalesce(func.sum(Attachment.size_bytes), 0)).where(
            Attachment.storage_provider == IntegrationProvider.R2.value,
            Attachment.deleted_at.is_(None),
        )
    ) or 0
    observation = runtime.observation(storage_bytes=int(storage_bytes))
    return StorageUsageObservation(
        storage_bytes=observation.storage_bytes,
        class_a_requests=observation.class_a_requests,
        class_b_requests=observation.class_b_requests,
        storage_budget_bytes=observation.storage_budget_bytes,
        class_a_request_budget=observation.class_a_request_budget,
        class_b_request_budget=observation.class_b_request_budget,
        warning=QuotaWarning(
            level=cast(Any, observation.warning_level),
            threshold_percent=cast(Any, observation.threshold_percent),
            message=(
                None
                if observation.warning_level == "NONE"
                else "Configured object-storage budget threshold reached."
            ),
        ),
        estimated_monthly_cost_usd=observation.estimated_monthly_cost_usd,
        estimate_quality=cast(Any, observation.estimate_quality),
        observed_since=observation.observed_since,
    )


@router.post(
    "/api/issues/{issueId}/attachment-upload-sessions",
    operation_id="createIssueAttachmentUploadSession",
    response_model=AttachmentUploadSessionResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_issue_upload_session(
    issue_id: Annotated[str, Path(alias="issueId")],
    payload: AttachmentUploadSessionRequest,
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
    idempotency_header: IdempotencyHeader = None,
) -> AttachmentUploadSessionResponse:
    return _create_upload_session(
        owner_type=AttachmentOwnerType.ISSUE,
        owner_id=decode_identifier(issue_id, prefix="issue", field="issueId"),
        payload=payload,
        session=session,
        runtime=runtime,
        idempotency_header=idempotency_header,
    )


@router.post(
    "/api/execution-attempts/{attemptId}/attachment-upload-sessions",
    operation_id="createExecutionAttemptAttachmentUploadSession",
    response_model=AttachmentUploadSessionResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def create_attempt_upload_session(
    attempt_id: Annotated[str, Path(alias="attemptId")],
    payload: AttachmentUploadSessionRequest,
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
    idempotency_header: IdempotencyHeader = None,
) -> AttachmentUploadSessionResponse:
    return _create_upload_session(
        owner_type=AttachmentOwnerType.EXECUTION_ATTEMPT,
        owner_id=decode_identifier(attempt_id, prefix="attempt", field="attemptId"),
        payload=payload,
        session=session,
        runtime=runtime,
        idempotency_header=idempotency_header,
    )


@router.post(
    "/api/attachment-upload-sessions/complete",
    operation_id="completeAttachmentUpload",
    response_model=AttachmentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
def complete_attachment_upload(
    payload: CompleteAttachmentUploadRequest,
    request: Request,
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
    idempotency_header: IdempotencyHeader = None,
) -> AttachmentUploadResponse:
    if idempotency_header is not None and idempotency_header != payload.idempotencyKey:
        raise IdempotencyConflictError(payload.idempotencyKey)
    claim = runtime.decode_claim(payload.uploadToken)
    if claim.idempotency_key != payload.idempotencyKey:
        raise IdempotencyConflictError(payload.idempotencyKey)
    adapter = runtime.require_upload_ready()
    try:
        remote = adapter.head(key=claim.object_key)
        if remote.size_bytes != claim.size_bytes:
            raise FileSizeMismatch(
                "uploaded object size does not match the upload session"
            )
        if remote.content_type.lower() != claim.mime_type.lower():
            raise MimeContentMismatch(
                "uploaded object MIME type does not match the upload session"
            )
        if remote.sha256 != claim.sha256:
            raise ChecksumMismatch(
                "uploaded object checksum metadata does not match the upload session"
            )
        runtime.observe_direct_put()
        runtime.verify_quarantined_upload(claim)
    except AttachmentValidationError:
        try:
            adapter.delete(key=claim.object_key)
        except ObjectStorageError:
            # An unreferenced quarantine object is safe and will be retried by
            # the cutoff-protected orphan job.
            pass
        raise
    owner_type = AttachmentOwnerType(claim.owner_type)
    owner_id = uuid.UUID(claim.owner_id)
    context = audit_context(
        request, idempotency_key=payload.idempotencyKey, reason="Attachment upload completed"
    )

    def commit(_: Any) -> AttachmentUploadResponse:
        row = AttachmentRepository(session).create(
            owner_type=owner_type,
            owner_id=owner_id,
            metadata=AttachmentMetadata(
                drive_file_id=None,
                object_key=claim.object_key,
                logical_path=claim.object_key,
                file_name=claim.file_name,
                checksum=claim.sha256,
                mime_type=claim.mime_type,
                size_bytes=claim.size_bytes,
                storage_provider=IntegrationProvider.R2,
            ),
            audit=context,
        )
        audit_id = pending_audit_id(
            session, trace_id=cast(str, context.trace_id), action="CREATE"
        )
        return AttachmentUploadResponse(
            attachment=attachment_resource(row),
            audit_id=encode_identifier("audit", audit_id),
        )

    with session.begin():
        return execute_idempotent(
            session,
            key=payload.idempotencyKey,
            operation="COMPLETE_OBJECT_ATTACHMENT_UPLOAD",
            payload={
                "ownerType": claim.owner_type,
                "ownerId": claim.owner_id,
                "objectKey": claim.object_key,
                "filename": claim.file_name,
                "mimeType": claim.mime_type,
                "sizeBytes": claim.size_bytes,
                "sha256": claim.sha256,
            },
            response_model=AttachmentUploadResponse,
            context=context,
            action=commit,
            audit_action="CREATE",
        )


@router.get(
    "/api/attachments",
    operation_id="listAttachments",
    response_model=AttachmentPage,
    responses=ERROR_RESPONSES,
)
def list_attachments(
    session: SessionDependency,
    owner_types: Annotated[
        list[Literal["ISSUE", "EXECUTION_ATTEMPT"]] | None,
        Query(alias="ownerTypes"),
    ] = None,
    issue_ids: Annotated[list[str] | None, Query(alias="issueIds")] = None,
    execution_attempt_ids: Annotated[
        list[str] | None, Query(alias="executionAttemptIds")
    ] = None,
    mime_types: Annotated[list[str] | None, Query(alias="mimeTypes")] = None,
    include_deleted: Annotated[bool, Query(alias="includeDeleted")] = False,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AttachmentPage:
    raw_filters = {
        "ownerTypes": owner_types,
        "issueIds": issue_ids,
        "executionAttemptIds": execution_attempt_ids,
        "mimeTypes": mime_types,
    }
    for field, values in raw_filters.items():
        if values is not None and len(values) != len(set(values)):
            raise ValueError(f"{field} must contain unique values")
    filters: list[Any] = [Attachment.workspace_id == DEFAULT_WORKSPACE_ID]
    if owner_types:
        filters.append(Attachment.owner_type.in_(owner_types))
    if issue_ids:
        filters.append(
            Attachment.owner_id.in_(
                decode_identifier(value, prefix="issue", field="issueIds")
                for value in issue_ids
            )
        )
    if execution_attempt_ids:
        filters.append(
            Attachment.owner_id.in_(
                decode_identifier(value, prefix="attempt", field="executionAttemptIds")
                for value in execution_attempt_ids
            )
        )
    if mime_types:
        filters.append(Attachment.mime_type.in_(mime_types))
    if not include_deleted:
        filters.append(Attachment.deleted_at.is_(None))
    fingerprint = _cursor_fingerprint(
        {
            **{field: sorted(values or ()) for field, values in raw_filters.items()},
            "includeDeleted": include_deleted,
        }
    )
    offset = _cursor_offset(cursor, fingerprint) if cursor else 0
    statement = select(Attachment).where(*filters)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(
        session.scalars(
            statement.order_by(Attachment.created_at, Attachment.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    return AttachmentPage(
        items=[attachment_resource(row) for row in rows[:limit]],
        next_cursor=(
            _cursor_token(offset + limit, fingerprint) if len(rows) > limit else None
        ),
        total=total,
    )


@router.get(
    "/api/attachments/{attachmentId}/content",
    operation_id="downloadAttachmentContent",
    responses={
        **ERROR_RESPONSES,
        200: {
            "description": "Private binary stream",
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        },
    },
)
def download_attachment_content(
    attachment_id: Annotated[str, Path(alias="attachmentId")],
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
) -> StreamingResponse:
    row = AttachmentRepository(session).get(
        decode_identifier(attachment_id, prefix="attachment", field="attachmentId")
    )
    if row.storage_provider == IntegrationProvider.GOOGLE_DRIVE.value:
        raise LegacyAttachmentMigrationRequired(
            "legacy Google Drive attachment requires offline migration to R2"
        )
    if row.deleted_at is not None:
        from tc_backend.db.errors import AttachmentDeletedError

        raise AttachmentDeletedError("deleted attachment content is unavailable")
    if row.storage_status != "AVAILABLE" or row.reconcile_status != "CONSISTENT":
        raise ObjectStorageError("attachment is not in a verified readable state")
    content = runtime.require_adapter().download(key=row.object_key)
    return StreamingResponse(
        content,
        media_type=row.mime_type,
        headers={
            "Content-Disposition": attachment_content_disposition(row.file_name),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Length": str(row.size_bytes),
        },
    )


@router.delete(
    "/api/attachments/{attachmentId}",
    operation_id="deleteAttachment",
    response_model=SoftDeleteAttachmentResponse,
    responses=ERROR_RESPONSES,
)
def delete_attachment(
    attachment_id: Annotated[str, Path(alias="attachmentId")],
    payload: SoftDeleteAttachmentRequest,
    request: Request,
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
) -> SoftDeleteAttachmentResponse:
    decoded = decode_identifier(attachment_id, prefix="attachment", field="attachmentId")
    existing = AttachmentRepository(session).get(decoded)
    if existing.storage_provider == IntegrationProvider.GOOGLE_DRIVE.value:
        raise LegacyAttachmentMigrationRequired(
            "legacy Google Drive attachment must be migrated before deletion"
        )
    session.rollback()
    context = audit_context(request, idempotency_key=None, reason=payload.reason)
    with session.begin():
        repository = AttachmentRepository(session)
        row = repository.soft_delete(
            attachment_id=decoded,
            base_resource_version=payload.baseResourceVersion,
            audit=context,
        )
        audit_id = pending_audit_id(
            session, trace_id=cast(str, context.trace_id), action="DELETE_REQUEST"
        )
        object_key = row.object_key
        deletion_version = row.resource_version
    succeeded = False
    detail: str | None = None
    try:
        runtime.require_adapter().delete(key=object_key)
        succeeded = True
    except ObjectStorageError as error:
        detail = error.__class__.__name__
    with session.begin():
        row = AttachmentRepository(session).mark_deletion_result(
            attachment_id=decoded,
            base_resource_version=deletion_version,
            succeeded=succeeded,
            detail=detail,
            audit=context,
        )
    if row.deleted_at is None:
        raise RuntimeError("soft-deleted attachment lost deletion timestamp")
    return SoftDeleteAttachmentResponse(
        attachment_id=attachment_id,
        resource_version=row.resource_version,
        deleted_at=row.deleted_at,
        storage_delete_state="DELETED" if succeeded else "FAILED_RETRYABLE",
        audit_id=encode_identifier("audit", audit_id),
    )


@router.post(
    "/api/integrations/object-storage/reconcile-orphans",
    operation_id="reconcileObjectStorageOrphans",
    response_model=OrphanReconcileResponse,
    responses=ERROR_RESPONSES,
)
def reconcile_object_storage_orphans(
    payload: OrphanReconcileRequest,
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
    idempotency_header: IdempotencyHeader = None,
) -> OrphanReconcileResponse:
    if idempotency_header is not None and idempotency_header != payload.idempotencyKey:
        raise IdempotencyConflictError(payload.idempotencyKey)
    if payload.olderThan.tzinfo is None:
        raise ValueError("olderThan must include a timezone offset")
    cutoff = payload.olderThan.astimezone(UTC)
    known_keys = set(
        session.scalars(
            select(Attachment.object_key).where(
                Attachment.storage_provider == IntegrationProvider.R2.value
            )
        )
    )
    page = runtime.require_adapter().list_objects(
        prefix=f"quarantine/attachments/{DEFAULT_WORKSPACE_ID}/",
        cursor=payload.cursor,
        limit=payload.limit,
    )
    items: list[OrphanReconcileItem] = []
    for stored in page.items:
        if stored.key in known_keys or stored.last_modified >= cutoff:
            continue
        action: Literal["WOULD_DELETE", "DELETED", "RETAINED", "FAILED_RETRYABLE"]
        error_code: str | None = None
        if payload.dryRun:
            action = "WOULD_DELETE"
        else:
            try:
                runtime.require_adapter().delete(key=stored.key)
                action = "DELETED"
            except ObjectStorageError as error:
                action = "FAILED_RETRYABLE"
                error_code = error.__class__.__name__
        items.append(
            OrphanReconcileItem(
                orphan_key=stored.key,
                path=stored.key,
                detected_at=stored.last_modified,
                action=action,
                error_code=error_code,
            )
        )
    return OrphanReconcileResponse(
        dry_run=payload.dryRun,
        scanned_count=len(page.items),
        orphan_count=len(items),
        deleted_count=sum(item.action == "DELETED" for item in items),
        failed_count=sum(item.action == "FAILED_RETRYABLE" for item in items),
        items=items,
        next_cursor=page.next_cursor,
        completed_at=datetime.now(UTC),
    )


@router.post(
    "/api/integrations/object-storage/verify-metadata",
    operation_id="verifyObjectStorageMetadata",
    response_model=MetadataIntegrityResponse,
    responses=ERROR_RESPONSES,
)
def verify_object_storage_metadata(
    payload: MetadataIntegrityRequest,
    request: Request,
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
) -> MetadataIntegrityResponse:
    fingerprint = _cursor_fingerprint(
        {"job": "object-storage-metadata-integrity", "provider": "R2"}
    )
    offset = _cursor_offset(payload.cursor, fingerprint) if payload.cursor else 0
    rows = list(
        session.scalars(
            select(Attachment)
            .where(
                Attachment.storage_provider == IntegrationProvider.R2.value,
                Attachment.deleted_at.is_(None),
            )
            .order_by(Attachment.created_at, Attachment.id)
            .offset(offset)
            .limit(payload.limit + 1)
        )
    )
    snapshots = [
        (
            row.id,
            row.object_key,
            row.size_bytes,
            row.mime_type,
            row.checksum,
            row.resource_version,
            row.reconcile_status,
        )
        for row in rows[: payload.limit]
    ]
    session.rollback()
    context = audit_context(request, idempotency_key=None, reason="Object metadata integrity")
    items: list[MetadataIntegrityItem] = []
    for row_id, key, size_bytes, mime_type, checksum, version, previous_status in snapshots:
        detail: str | None = None
        selected_status: AttachmentReconcileStatus | None = None
        try:
            remote = runtime.require_adapter().head(key=key)
            differences = []
            if remote.size_bytes != size_bytes:
                differences.append("size")
            if remote.content_type.lower() != mime_type.lower():
                differences.append("mime")
            if remote.sha256 != checksum:
                differences.append("sha256-metadata")
            if differences:
                result_status = "METADATA_MISMATCH"
                detail = ",".join(differences)
                selected_status = AttachmentReconcileStatus.METADATA_MISMATCH
            else:
                result_status = "CONSISTENT"
                selected_status = AttachmentReconcileStatus.CONSISTENT
        except ObjectNotFound:
            result_status = "MISSING"
            detail = "object not found"
            selected_status = AttachmentReconcileStatus.DANGLING_METADATA
        except ObjectStorageError as error:
            result_status = "FAILED_RETRYABLE"
            detail = error.__class__.__name__
        if (
            not payload.dryRun
            and selected_status is not None
            and previous_status != selected_status.value
        ):
            with session.begin():
                AttachmentRepository(session).mark_reconcile_status(
                    attachment_id=row_id,
                    base_resource_version=version,
                    status=selected_status,
                    detail=detail,
                    audit=context,
                )
        items.append(
            MetadataIntegrityItem(
                attachment_id=encode_identifier("attachment", row_id),
                object_key=key,
                status=cast(Any, result_status),
                detail=detail,
            )
        )
    return MetadataIntegrityResponse(
        dry_run=payload.dryRun,
        scanned_count=len(items),
        consistent_count=sum(item.status == "CONSISTENT" for item in items),
        mismatch_count=sum(
            item.status in {"MISSING", "METADATA_MISMATCH"} for item in items
        ),
        failed_count=sum(item.status == "FAILED_RETRYABLE" for item in items),
        items=items,
        next_cursor=(
            _cursor_token(offset + payload.limit, fingerprint)
            if len(rows) > payload.limit
            else None
        ),
        completed_at=datetime.now(UTC),
    )


@router.post(
    "/api/integrations/object-storage/restore-sample",
    operation_id="verifyObjectStorageRestoreSample",
    response_model=RestoreSampleResponse,
    responses=ERROR_RESPONSES,
)
def verify_object_storage_restore_sample(
    payload: RestoreSampleRequest,
    session: SessionDependency,
    runtime: ObjectStorageRuntimeDependency,
) -> RestoreSampleResponse:
    rows = list(
        session.scalars(
            select(Attachment)
            .where(
                Attachment.storage_provider == IntegrationProvider.R2.value,
                Attachment.deleted_at.is_(None),
            )
            .order_by(Attachment.id)
            .limit(payload.sampleSize)
        )
    )
    items: list[RestoreSampleItem] = []
    for row in rows:
        if row.size_bytes > payload.maxBytesPerObject:
            items.append(
                RestoreSampleItem(
                    attachment_id=encode_identifier("attachment", row.id),
                    object_key=row.object_key,
                    status="SKIPPED_TOO_LARGE",
                    expected_sha256=row.checksum,
                    observed_sha256=None,
                    observed_size_bytes=None,
                )
            )
            continue
        try:
            observed_sha256, observed_size = _hash_download(
                runtime.require_adapter().download(key=row.object_key),
                limit=payload.maxBytesPerObject,
            )
            sample_status = (
                "VERIFIED"
                if observed_sha256 == row.checksum and observed_size == row.size_bytes
                else "CHECKSUM_MISMATCH"
            )
        except ObjectNotFound:
            observed_sha256, observed_size, sample_status = None, None, "MISSING"
        except ObjectStorageError:
            observed_sha256, observed_size, sample_status = (
                None,
                None,
                "FAILED_RETRYABLE",
            )
        items.append(
            RestoreSampleItem(
                attachment_id=encode_identifier("attachment", row.id),
                object_key=row.object_key,
                status=cast(Any, sample_status),
                expected_sha256=row.checksum,
                observed_sha256=observed_sha256,
                observed_size_bytes=observed_size,
            )
        )
    return RestoreSampleResponse(
        requested_count=payload.sampleSize,
        sampled_count=len(items),
        verified_count=sum(item.status == "VERIFIED" for item in items),
        failed_count=sum(item.status != "VERIFIED" for item in items),
        items=items,
        completed_at=datetime.now(UTC),
    )


@router.get(
    "/api/integrations/object-storage/legacy-attachments",
    operation_id="getLegacyAttachmentInventory",
    response_model=LegacyAttachmentInventory,
)
def get_legacy_attachment_inventory(
    session: SessionDependency,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> LegacyAttachmentInventory:
    statement = select(Attachment).where(
        Attachment.storage_provider == IntegrationProvider.GOOGLE_DRIVE.value
    )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    fingerprint = _cursor_fingerprint(
        {"job": "legacy-attachment-inventory", "provider": "GOOGLE_DRIVE"}
    )
    offset = _cursor_offset(cursor, fingerprint) if cursor else 0
    rows = list(
        session.scalars(
            statement.order_by(Attachment.created_at, Attachment.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    return LegacyAttachmentInventory(
        total=total,
        items=[
            LegacyAttachmentInventoryItem(
                attachment_id=encode_identifier("attachment", row.id),
                owner=attachment_owner(row),
                filename=row.file_name,
                mime_type=cast(Any, row.mime_type),
                size_bytes=row.size_bytes,
                sha256=row.checksum,
            )
            for row in rows[:limit]
        ],
        next_cursor=(
            _cursor_token(offset + limit, fingerprint) if len(rows) > limit else None
        ),
    )


def _create_upload_session(
    *,
    owner_type: AttachmentOwnerType,
    owner_id: uuid.UUID,
    payload: AttachmentUploadSessionRequest,
    session: Any,
    runtime: Any,
    idempotency_header: str | None,
) -> AttachmentUploadSessionResponse:
    if idempotency_header is not None and idempotency_header != payload.idempotencyKey:
        raise IdempotencyConflictError(payload.idempotencyKey)
    filename, mime_type, sha256 = validate_declaration(
        filename=payload.filename,
        mime_type=payload.declaredMimeType,
        size_bytes=payload.declaredSizeBytes,
        sha256=payload.clientSha256,
        allowed_mime_types=runtime.allowed_mime_types,
        max_file_bytes=runtime.max_file_bytes,
    )
    adapter = runtime.require_upload_ready()
    with session.begin():
        AttachmentRepository(session).assert_owner(owner_type=owner_type, owner_id=owner_id)
    claim, token = runtime.create_claim(
        owner_type=owner_type,
        owner_id=owner_id,
        file_name=filename,
        mime_type=mime_type,
        size_bytes=payload.declaredSizeBytes,
        sha256=sha256,
        idempotency_key=payload.idempotencyKey,
    )
    upload_url, headers, expires_at = adapter.presign_put(
        key=claim.object_key,
        content_type=claim.mime_type,
        sha256=claim.sha256,
        expires_seconds=runtime.presign_ttl_seconds,
    )
    return AttachmentUploadSessionResponse(
        upload_token=token,
        object_key=claim.object_key,
        upload=AttachmentUploadTarget(
            url=upload_url, headers=headers, expires_at=expires_at
        ),
        max_size_bytes=runtime.max_file_bytes,
    )


def _hash_download(chunks: Any, *, limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in chunks:
        size += len(chunk)
        if size > limit:
            raise ObjectStorageError("restore sample object exceeded the configured bound")
        digest.update(chunk)
    return digest.hexdigest(), size


def _cursor_fingerprint(values: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _cursor_token(offset: int, fingerprint: str) -> str:
    raw = json.dumps(
        {"offset": offset, "filter": fingerprint}, separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_offset(cursor: str, fingerprint: str) -> int:
    try:
        value = json.loads(
            base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        )
        if (
            not isinstance(value, dict)
            or value.get("filter") != fingerprint
            or not isinstance(value.get("offset"), int)
            or value["offset"] < 0
        ):
            raise ValueError
        return value["offset"]
    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("attachment cursor does not match the requested filters") from error
