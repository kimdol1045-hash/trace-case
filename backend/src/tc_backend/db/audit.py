"""Append-only audit helpers shared by domain repositories."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from tc_backend.db.models import AuditLog, AuditSourceType


@dataclass(frozen=True, slots=True)
class AuditContext:
    source_type: AuditSourceType = AuditSourceType.SYSTEM
    actor_label: str | None = None
    external_actor_id: str | None = None
    reason: str | None = None
    trace_id: str | None = None
    idempotency_key: str | None = None


def append_audit(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    context: AuditContext,
) -> uuid.UUID:
    """Append one audit row without committing the caller's transaction."""

    if before is None and after is None:
        raise ValueError("audit requires a before or after snapshot")
    audit_id = uuid.uuid4()
    session.add(
        AuditLog(
            id=audit_id,
            workspace_id=workspace_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
            source_type=context.source_type.value,
            actor_label=context.actor_label,
            external_actor_id=context.external_actor_id,
            reason=context.reason,
            trace_id=context.trace_id or f"trace_{uuid.uuid4().hex}",
            idempotency_key=context.idempotency_key,
        )
    )
    return audit_id
