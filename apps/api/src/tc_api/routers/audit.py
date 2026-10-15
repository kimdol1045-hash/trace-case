"""Append-only audit read endpoint with stable cursor pagination."""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, func, or_, select

from tc_api.dependencies import SessionDependency
from tc_api.errors import AuditCursorError
from tc_api.idempotency import strip_idempotency_metadata
from tc_api.ids import decode_any_resource_identifier
from tc_api.models import AuditPageResponse, ErrorResponse
from tc_api.serializers import audit_entry_response
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.models import AuditLog

router = APIRouter(prefix="/api/audit-logs", tags=["audit"])


@router.get(
    "",
    operation_id="listAuditLogs",
    response_model=AuditPageResponse,
    response_model_exclude_none=True,
    responses={422: {"model": ErrorResponse, "description": "Invalid audit cursor or filter"}},
)
def list_audit_logs(
    request: Request,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    entity_type: Annotated[str | None, Query(alias="entityType", pattern=r"^[A-Z][A-Z0-9_]*$")] = None,
    entity_id: Annotated[str | None, Query(alias="entityId")] = None,
) -> AuditPageResponse:
    del request
    filters = [AuditLog.workspace_id == DEFAULT_WORKSPACE_ID]
    if entity_type is not None:
        filters.append(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        filters.append(
            AuditLog.entity_id == decode_any_resource_identifier(entity_id, field="entityId")
        )
    total = session.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    statement = select(AuditLog).where(*filters)
    if cursor is not None:
        occurred_at, audit_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                AuditLog.occurred_at < occurred_at,
                and_(AuditLog.occurred_at == occurred_at, AuditLog.id < audit_id),
            )
        )
    rows = list(
        session.scalars(
            statement.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(limit + 1)
        )
    )
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if len(rows) > limit and page else None
    return AuditPageResponse(
        items=[
            audit_entry_response(
                row,
                before=strip_idempotency_metadata(row.before),
                after=strip_idempotency_metadata(row.after),
            )
            for row in page
        ],
        next_cursor=next_cursor,
        total=total,
    )


def _encode_cursor(row: AuditLog) -> str:
    value = f"{row.occurred_at.isoformat()}|{row.id}"
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True).decode()
        timestamp, identifier = raw.rsplit("|", 1)
        occurred_at = datetime.fromisoformat(timestamp)
        audit_id = uuid.UUID(identifier)
        if occurred_at.tzinfo is None:
            raise ValueError("cursor timestamp must include a timezone")
        return occurred_at, audit_id
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise AuditCursorError("audit cursor is invalid") from error
