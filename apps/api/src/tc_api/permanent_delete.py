"""Fail-closed attachment cleanup shared by aggregate permanent-delete routes."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from tc_api.object_storage_runtime import ObjectStorageRuntime
from tc_backend.db.attachments import AttachmentRepository
from tc_backend.db.audit import AuditContext
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.models import Attachment, AttachmentOwnerType, IntegrationProvider
from tc_backend.infrastructure.object_storage import (
    LegacyAttachmentMigrationRequired,
    ObjectStorageError,
)


def delete_owned_attachment_objects(
    session: Session,
    runtime: ObjectStorageRuntime,
    *,
    owner_type: AttachmentOwnerType,
    owner_ids: Sequence[uuid.UUID],
    audit: AuditContext,
) -> int:
    """Delete every owned object before its metadata becomes purge-eligible.

    Each object result is committed independently. A partial storage outage therefore
    leaves retryable metadata behind and never commits the owning aggregate purge.
    """

    if not owner_ids:
        return 0
    rows = tuple(
        session.scalars(
            select(Attachment)
            .where(
                Attachment.workspace_id == DEFAULT_WORKSPACE_ID,
                Attachment.owner_type == owner_type.value,
                Attachment.owner_id.in_(owner_ids),
            )
            .order_by(Attachment.created_at, Attachment.id)
        )
    )
    snapshots = tuple(
        (row.id, row.storage_provider, row.deletion_status) for row in rows
    )
    session.rollback()
    pending = [row for row in snapshots if row[2] != "DELETED"]
    if any(provider == IntegrationProvider.GOOGLE_DRIVE.value for _, provider, _ in pending):
        raise LegacyAttachmentMigrationRequired(
            "legacy Google Drive attachments must be migrated before permanent deletion"
        )
    adapter = runtime.require_adapter() if pending else None
    for attachment_id, _provider, known_status in snapshots:
        if known_status == "DELETED":
            continue
        with session.begin():
            repository = AttachmentRepository(session)
            row = repository.get(attachment_id, for_update=True)
            if row.deleted_at is None:
                row = repository.soft_delete(
                    attachment_id=row.id,
                    base_resource_version=row.resource_version,
                    audit=audit,
                )
            object_key = row.object_key
            deletion_version = row.resource_version
        try:
            if adapter is None:  # pragma: no cover - pending rows require an adapter
                raise RuntimeError("object storage adapter unexpectedly missing")
            adapter.delete(key=object_key)
        except ObjectStorageError as error:
            with session.begin():
                AttachmentRepository(session).mark_deletion_result(
                    attachment_id=attachment_id,
                    base_resource_version=deletion_version,
                    succeeded=False,
                    detail=error.__class__.__name__,
                    audit=audit,
                )
            raise
        with session.begin():
            AttachmentRepository(session).mark_deletion_result(
                attachment_id=attachment_id,
                base_resource_version=deletion_version,
                succeeded=True,
                detail=None,
                audit=audit,
            )
    return len(rows)
