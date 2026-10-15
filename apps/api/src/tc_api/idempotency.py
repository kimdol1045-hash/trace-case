"""Durable mutation replay semantics backed by append-only receipts."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from tc_api.errors import IdempotencyConflictError
from tc_backend.db.audit import AuditContext
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.models import ApiIdempotencyReceipt, AuditLog

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
_METADATA_KEY = "_idempotency"


def execute_idempotent(
    session: Session,
    *,
    key: str | None,
    operation: str,
    payload: Mapping[str, Any],
    response_model: type[ResponseModel],
    context: AuditContext,
    action: Callable[[AuditContext], ResponseModel],
    audit_action: str | None = None,
) -> ResponseModel:
    if key is None:
        return action(context)
    request_hash = _request_hash(operation, payload)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"{DEFAULT_WORKSPACE_ID}:{key}"},
    )
    existing_receipt = session.get(
        ApiIdempotencyReceipt,
        (DEFAULT_WORKSPACE_ID, key),
    )
    if existing_receipt is not None:
        if (
            existing_receipt.operation != operation
            or existing_receipt.request_hash != request_hash
        ):
            raise IdempotencyConflictError(key)
        return response_model.model_validate(existing_receipt.response)

    existing_rows = list(session.scalars(
        select(AuditLog)
        .where(
            AuditLog.workspace_id == DEFAULT_WORKSPACE_ID,
            AuditLog.idempotency_key == key,
        )
        .order_by(AuditLog.occurred_at.asc(), AuditLog.id.asc())
    ))
    existing = next(
        (
            row
            for row in existing_rows
            if isinstance((row.after or {}).get(_METADATA_KEY), dict)
        ),
        existing_rows[0] if existing_rows else None,
    )
    if existing is not None:
        metadata = (existing.after or {}).get(_METADATA_KEY)
        if not isinstance(metadata, dict) or metadata.get("requestHash") != request_hash:
            raise IdempotencyConflictError(key)
        response = metadata.get("response")
        if not isinstance(response, dict):
            raise IdempotencyConflictError(key)
        return response_model.model_validate(response)

    # Keep the mutation's final audit row pending so replay metadata is part of
    # its original INSERT. Audit rows are append-only and cannot be patched
    # after an autoflush.
    with session.no_autoflush:
        response = action(context)
    metadata = {
        "requestHash": request_hash,
        "response": response.model_dump(mode="json", by_alias=True),
    }
    audit = _pending_audit(session, key, action=audit_action)
    if audit is None:
        # A semantic no-op has no domain audit row to carry replay metadata.
        # Persist the exact response in a separate append-only ledger so the
        # domain remains a total no-op while keyed retries still replay.
        session.add(
            ApiIdempotencyReceipt(
                workspace_id=DEFAULT_WORKSPACE_ID,
                idempotency_key=key,
                operation=operation,
                request_hash=request_hash,
                response=metadata["response"],
            )
        )
    elif inspect(audit).persistent:
        # Some aggregate operations deliberately flush intermediate ledger rows
        # (for example issue create-from-attempt plus its three links). Audit
        # rows are append-only, so replay metadata is appended as a companion
        # record rather than attempting an UPDATE forbidden by the database.
        session.add(
            AuditLog(
                id=uuid.uuid4(),
                workspace_id=audit.workspace_id,
                entity_type=audit.entity_type,
                entity_id=audit.entity_id,
                action="IDEMPOTENCY_RECORDED",
                before=None,
                after={_METADATA_KEY: metadata},
                source_type=context.source_type.value,
                actor_label=context.actor_label,
                external_actor_id=context.external_actor_id,
                reason=context.reason,
                trace_id=context.trace_id or f"trace_{uuid.uuid4().hex}",
                idempotency_key=key,
            )
        )
    else:
        audit.after = {**(audit.after or {}), _METADATA_KEY: metadata}
    return response


def pending_audit_id(session: Session, *, trace_id: str, action: str) -> uuid.UUID:
    matches = [
        row
        for row in session.new
        if isinstance(row, AuditLog) and row.trace_id == trace_id and row.action == action
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one pending {action} audit row, found {len(matches)}")
    return matches[0].id


def strip_idempotency_metadata(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if snapshot is None or _METADATA_KEY not in snapshot:
        return snapshot
    return {key: value for key, value in snapshot.items() if key != _METADATA_KEY}


def _pending_audit(
    session: Session,
    key: str,
    *,
    action: str | None = None,
) -> AuditLog | None:
    matches = [
        row
        for row in session.new
        if isinstance(row, AuditLog)
        and row.idempotency_key == key
        and (action is None or row.action == action)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        statement = select(AuditLog).where(
            AuditLog.workspace_id == DEFAULT_WORKSPACE_ID,
            AuditLog.idempotency_key == key,
        )
        if action is not None:
            statement = statement.where(AuditLog.action == action)
        persisted = list(
            session.scalars(
                statement.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(2)
            )
        )
        if len(persisted) == 1:
            return persisted[0]
        if not persisted:
            return None
    raise RuntimeError(f"expected one pending idempotency audit row, found {len(matches)}")


def _request_hash(operation: str, payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"operation": operation, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
