"""Provider-neutral private attachment metadata repository."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import (
    AttachmentDeletedError,
    AttachmentOwnerError,
    ResourceNotFoundError,
    VersionConflictError,
)
from tc_backend.db.models import (
    Attachment,
    AttachmentDeletionStatus,
    AttachmentOwnerType,
    AttachmentReconcileStatus,
    AttachmentStorageStatus,
    CycleItem,
    ExecutionAttempt,
    IntegrationProvider,
    Issue,
    TestCycle,
)

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AttachmentMetadata:
    drive_file_id: str | None
    logical_path: str
    file_name: str
    checksum: str
    mime_type: str
    size_bytes: int
    object_key: str | None = None
    storage_provider: IntegrationProvider = IntegrationProvider.R2


class AttachmentRepository:
    """Metadata-only attachment transaction boundary."""

    def __init__(self, session: Session, *, workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def create(
        self,
        *,
        owner_type: AttachmentOwnerType | str,
        owner_id: uuid.UUID,
        metadata: AttachmentMetadata,
        audit: AuditContext,
        storage_status: AttachmentStorageStatus | str = AttachmentStorageStatus.AVAILABLE,
        reconcile_status: AttachmentReconcileStatus | str = AttachmentReconcileStatus.CONSISTENT,
    ) -> Attachment:
        selected_owner = AttachmentOwnerType(owner_type)
        self._validate_owner(selected_owner, owner_id)
        normalized = _normalize_metadata(metadata)
        if normalized.storage_provider is IntegrationProvider.GOOGLE_DRIVE:
            raise ValueError(
                "new Google Drive attachments are disabled; legacy rows are read-only"
            )
        now = datetime.now(UTC)
        attachment = Attachment(
            id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            owner_type=selected_owner.value,
            owner_id=owner_id,
            drive_file_id=normalized.drive_file_id,
            object_key=normalized.object_key,
            logical_path=normalized.logical_path,
            file_name=normalized.file_name,
            checksum=normalized.checksum,
            mime_type=normalized.mime_type,
            size_bytes=normalized.size_bytes,
            storage_provider=normalized.storage_provider.value,
            storage_status=AttachmentStorageStatus(storage_status).value,
            deletion_status=AttachmentDeletionStatus.ACTIVE.value,
            reconcile_status=AttachmentReconcileStatus(reconcile_status).value,
            resource_version=1,
            created_at=now,
            updated_at=now,
        )
        self.session.add(attachment)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ATTACHMENT",
            entity_id=attachment.id,
            action="CREATE",
            before=None,
            after=self._snapshot(attachment),
            context=audit,
        )
        return attachment

    create_attachment = create
    register_metadata = create

    def assert_owner(
        self, *, owner_type: AttachmentOwnerType | str, owner_id: uuid.UUID
    ) -> None:
        self._validate_owner(AttachmentOwnerType(owner_type), owner_id)

    def get(self, attachment_id: uuid.UUID, *, for_update: bool = False) -> Attachment:
        statement = select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.workspace_id == self.workspace_id,
        )
        if for_update:
            statement = statement.with_for_update()
        attachment = self.session.execute(statement).scalar_one_or_none()
        if attachment is None:
            raise ResourceNotFoundError("ATTACHMENT", attachment_id)
        return attachment

    get_attachment = get

    def list_by_owner(
        self,
        *,
        owner_type: AttachmentOwnerType | str,
        owner_id: uuid.UUID,
        include_deleted: bool = False,
    ) -> tuple[Attachment, ...]:
        statement = select(Attachment).where(
            Attachment.workspace_id == self.workspace_id,
            Attachment.owner_type == AttachmentOwnerType(owner_type).value,
            Attachment.owner_id == owner_id,
        )
        if not include_deleted:
            statement = statement.where(Attachment.deleted_at.is_(None))
        return tuple(self.session.scalars(statement.order_by(Attachment.created_at, Attachment.id)))

    def soft_delete(
        self,
        *,
        attachment_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> Attachment:
        attachment = self.get(attachment_id, for_update=True)
        self._assert_version(attachment, base_resource_version)
        if attachment.deleted_at is not None:
            raise AttachmentDeletedError("attachment is already deleted")
        before = self._snapshot(attachment)
        now = datetime.now(UTC)
        attachment.deleted_at = now
        attachment.deletion_status = AttachmentDeletionStatus.PENDING.value
        attachment.reconcile_status = AttachmentReconcileStatus.PENDING.value
        self._touch(attachment, now)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ATTACHMENT",
            entity_id=attachment.id,
            action="DELETE_REQUEST",
            before=before,
            after=self._snapshot(attachment),
            context=audit,
        )
        return attachment

    delete = soft_delete
    delete_attachment = soft_delete

    def mark_deletion_result(
        self,
        *,
        attachment_id: uuid.UUID,
        base_resource_version: int,
        succeeded: bool,
        audit: AuditContext,
        detail: str | None = None,
    ) -> Attachment:
        attachment = self.get(attachment_id, for_update=True)
        self._assert_version(attachment, base_resource_version)
        if attachment.deleted_at is None:
            raise AttachmentDeletedError("deletion result requires a soft-deleted attachment")
        before = self._snapshot(attachment)
        attachment.deletion_status = (
            AttachmentDeletionStatus.DELETED.value
            if succeeded
            else AttachmentDeletionStatus.FAILED.value
        )
        attachment.storage_status = (
            AttachmentStorageStatus.MISSING.value
            if succeeded
            else AttachmentStorageStatus.ERROR.value
        )
        attachment.reconcile_status = (
            AttachmentReconcileStatus.CONSISTENT.value
            if succeeded
            else AttachmentReconcileStatus.DELETE_RETRY.value
        )
        attachment.reconcile_detail = _optional_nonblank(detail, "detail")
        self._touch(attachment)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ATTACHMENT",
            entity_id=attachment.id,
            action="DELETE_SUCCEEDED" if succeeded else "DELETE_FAILED",
            before=before,
            after=self._snapshot(attachment),
            context=audit,
        )
        return attachment

    def mark_reconcile_status(
        self,
        *,
        attachment_id: uuid.UUID,
        base_resource_version: int,
        status: AttachmentReconcileStatus | str,
        audit: AuditContext,
        detail: str | None = None,
    ) -> Attachment:
        attachment = self.get(attachment_id, for_update=True)
        self._assert_version(attachment, base_resource_version)
        before = self._snapshot(attachment)
        selected = AttachmentReconcileStatus(status)
        attachment.reconcile_status = selected.value
        attachment.reconcile_detail = _optional_nonblank(detail, "detail")
        if selected is AttachmentReconcileStatus.DANGLING_METADATA:
            attachment.storage_status = AttachmentStorageStatus.MISSING.value
        elif selected is AttachmentReconcileStatus.METADATA_MISMATCH:
            attachment.storage_status = AttachmentStorageStatus.ERROR.value
        elif selected is AttachmentReconcileStatus.CONSISTENT and attachment.deleted_at is None:
            attachment.storage_status = AttachmentStorageStatus.AVAILABLE.value
        self._touch(attachment)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ATTACHMENT",
            entity_id=attachment.id,
            action="RECONCILE_STATUS",
            before=before,
            after=self._snapshot(attachment),
            context=audit,
        )
        return attachment

    def _validate_owner(self, owner_type: AttachmentOwnerType, owner_id: uuid.UUID) -> None:
        if owner_type is AttachmentOwnerType.ISSUE:
            issue = self.session.get(Issue, owner_id)
            if issue is None or issue.workspace_id != self.workspace_id or issue.deleted_at is not None:
                raise AttachmentOwnerError("attachment owner must be an active workspace issue")
            return
        exists = self.session.scalar(
            select(ExecutionAttempt.id)
            .join(CycleItem, CycleItem.id == ExecutionAttempt.cycle_item_id)
            .join(TestCycle, TestCycle.id == CycleItem.cycle_id)
            .where(
                ExecutionAttempt.id == owner_id,
                TestCycle.workspace_id == self.workspace_id,
            )
        )
        if exists is None:
            raise AttachmentOwnerError("attachment owner must be a workspace execution attempt")

    @staticmethod
    def _assert_version(attachment: Attachment, expected: int) -> None:
        if attachment.resource_version != expected:
            raise VersionConflictError(
                "ATTACHMENT", attachment.id, expected, attachment.resource_version
            )

    @staticmethod
    def _touch(attachment: Attachment, now: datetime | None = None) -> None:
        attachment.resource_version += 1
        attachment.updated_at = now or datetime.now(UTC)

    @staticmethod
    def _snapshot(attachment: Attachment) -> dict[str, Any]:
        return {
            "id": str(attachment.id),
            "ownerType": attachment.owner_type,
            "ownerId": str(attachment.owner_id),
            "objectKey": attachment.object_key,
            "logicalPath": attachment.logical_path,
            "fileName": attachment.file_name,
            "checksum": attachment.checksum,
            "mimeType": attachment.mime_type,
            "sizeBytes": attachment.size_bytes,
            "storageProvider": attachment.storage_provider,
            "storageStatus": attachment.storage_status,
            "deletionStatus": attachment.deletion_status,
            "reconcileStatus": attachment.reconcile_status,
            "reconcileDetail": attachment.reconcile_detail,
            "resourceVersion": attachment.resource_version,
            "deletedAt": attachment.deleted_at.isoformat() if attachment.deleted_at else None,
        }



def _normalize_metadata(metadata: AttachmentMetadata) -> AttachmentMetadata:
    checksum = metadata.checksum.strip().lower()
    if not SHA256_PATTERN.fullmatch(checksum):
        raise ValueError("checksum must be a lowercase SHA-256 hex digest")
    if metadata.size_bytes < 0:
        raise ValueError("size_bytes cannot be negative")
    provider = IntegrationProvider(metadata.storage_provider)
    drive_file_id = (
        _nonblank(metadata.drive_file_id, "drive_file_id", maximum=255)
        if metadata.drive_file_id is not None
        else None
    )
    if provider is IntegrationProvider.GOOGLE_DRIVE and drive_file_id is None:
        raise ValueError("drive_file_id is required for Google Drive attachments")
    object_key = _nonblank(
        metadata.object_key or drive_file_id or "", "object_key", maximum=1024
    )
    return AttachmentMetadata(
        drive_file_id=drive_file_id,
        logical_path=_nonblank(metadata.logical_path, "logical_path"),
        file_name=_nonblank(metadata.file_name, "file_name", maximum=500),
        checksum=checksum,
        mime_type=_nonblank(metadata.mime_type, "mime_type", maximum=255),
        size_bytes=metadata.size_bytes,
        object_key=object_key,
        storage_provider=provider,
    )


def _nonblank(value: str, field: str, *, maximum: int | None = None) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field} cannot be blank")
    if maximum is not None and len(clean) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return clean


def _optional_nonblank(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    return _nonblank(value, field)
