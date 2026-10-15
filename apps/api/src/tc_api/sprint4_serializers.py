"""Sprint 4 ledger-to-wire serialization without exposing provider secrets."""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from tc_api.ids import encode_identifier
from tc_api.sprint4_models import (
    AttachmentOwner,
    AttachmentResource,
    ExecutionAttemptIssueOrigin,
    ExecutionIssueSnapshot,
    IssueCommentResource,
    IssueHistoryChange,
    IssueHistoryEvent,
    IssueLinkResource,
    IssueListItem,
    IssueResource,
    IssueStepPayload,
    ManualIssueOrigin,
)
from tc_backend.db.issues import IssueAggregate
from tc_backend.db.models import (
    Attachment,
    AuditLog,
    Comment,
    CycleItem,
    ExecutionAttempt,
    Issue,
    IssueLink,
    OutboxEvent,
    TestCaseVersion,
)


def issue_resource(session: Session, aggregate: IssueAggregate) -> IssueResource:
    row = aggregate.issue
    created_event = session.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.aggregate_type == "ISSUE",
            OutboxEvent.aggregate_id == row.id,
            OutboxEvent.event_type.in_(
                ("ISSUE_CREATED", "ISSUE_CREATED_FROM_ATTEMPT")
            ),
        )
        .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
        .limit(1)
    )
    reproduction = (
        _execution_snapshot(session, created_event)
        if created_event is not None
        and created_event.event_type == "ISSUE_CREATED_FROM_ATTEMPT"
        else None
    )
    origin: ManualIssueOrigin | ExecutionAttemptIssueOrigin
    if reproduction is None:
        origin = ManualIssueOrigin()
    else:
        origin = ExecutionAttemptIssueOrigin(
            execution_attempt_id=reproduction.source_attempt_id
        )
    return IssueResource(
        id=encode_identifier("issue", row.id),
        key=_public_issue_key(row.issue_key),
        summary=row.summary,
        precondition=row.precondition,
        steps=[
            IssueStepPayload(
                sequence=step.sequence,
                action=step.action,
                expected_result=step.expected_result or row.expected_result,
            )
            for step in aggregate.steps
        ],
        expected_result=row.expected_result,
        actual_result=row.actual_result,
        status=cast(Any, row.status),
        severity=cast(Any, row.severity),
        assignee_label=row.assignee_label,
        origin=origin,
        reproduction_snapshot=reproduction,
        resource_version=row.resource_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def issue_list_item(
    session: Session, row: Issue, *, linked_cycle_ids: list[uuid.UUID] | None = None
) -> IssueListItem:
    if linked_cycle_ids is None:
        linked_cycle_ids = list(
            session.scalars(
                select(CycleItem.cycle_id)
                .join(IssueLink, IssueLink.cycle_item_id == CycleItem.id)
                .where(IssueLink.issue_id == row.id)
                .distinct()
                .order_by(CycleItem.cycle_id)
            )
        )
    return IssueListItem(
        id=encode_identifier("issue", row.id),
        key=_public_issue_key(row.issue_key),
        summary=row.summary,
        status=cast(Any, row.status),
        severity=cast(Any, row.severity),
        assignee_label=row.assignee_label,
        linked_cycle_ids=[encode_identifier("cycle", value) for value in linked_cycle_ids],
        resource_version=row.resource_version,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def issue_link_resource(
    session: Session, link: IssueLink, *, actor_label: str | None = None
) -> IssueLinkResource:
    if actor_label is None:
        actor_label = _link_actor(session, link)
    if link.execution_attempt_id is not None:
        target = {
            "type": "EXECUTION_ATTEMPT",
            "executionAttemptId": encode_identifier("attempt", link.execution_attempt_id),
        }
    elif link.cycle_item_id is not None:
        target = {
            "type": "CYCLE_ITEM",
            "cycleItemId": encode_identifier("cycle_item", link.cycle_item_id),
        }
    elif link.test_case_id is not None:
        target = {
            "type": "TEST_CASE",
            "testCaseId": encode_identifier("case", link.test_case_id),
        }
    else:  # Protected by the database check constraint.
        raise RuntimeError("issue link has no public target")
    return IssueLinkResource(
        id=encode_identifier("issue_link", link.id),
        issue_id=encode_identifier("issue", link.issue_id),
        target=target,
        created_by_label=actor_label,
        created_at=link.created_at,
    )


def issue_comment_resource(comment: Comment) -> IssueCommentResource:
    return IssueCommentResource(
        id=encode_identifier("issue_comment", comment.id),
        issue_id=encode_identifier("issue", comment.owner_id),
        body=comment.body,
        author_label=comment.author_label,
        created_at=comment.created_at,
        edited_at=None,
    )


def issue_history_event(
    row: AuditLog, *, issue_id: uuid.UUID | None = None
) -> IssueHistoryEvent:
    before = _without_metadata(row.before or {})
    after = _without_metadata(row.after or {})
    if row.entity_type == "ATTACHMENT":
        event_type = (
            "ATTACHMENT_ADDED" if row.action == "CREATE" else "ATTACHMENT_DELETED"
        )
        public_fields = {
            "fileName",
            "mimeType",
            "sizeBytes",
            "resourceVersion",
            "deletedAt",
        }
        before = {key: value for key, value in before.items() if key in public_fields}
        after = {key: value for key, value in after.items() if key in public_fields}
    else:
        event_type = {
            "CREATE": "CREATED",
            "CREATE_FROM_ATTEMPT": "CREATED",
            "UPDATE": "UPDATED",
            "TRANSITION": "STATUS_CHANGED",
            "LINK": "LINK_ADDED",
            "UNLINK": "LINK_REMOVED",
            "COMMENT": "COMMENT_ADDED",
            "DELETE": "DELETED",
        }.get(row.action, "UPDATED")
    related: str | None = None
    if isinstance(after.get("link"), dict):
        related = _related_identifier(after["link"].get("id"), "issue_link")
    elif isinstance(after.get("comment"), dict):
        related = _related_identifier(after["comment"].get("id"), "issue_comment")
    elif row.entity_type == "ATTACHMENT":
        related = encode_identifier("attachment", row.entity_id)
    changes = [
        IssueHistoryChange(field=field, before=before.get(field), after=after.get(field))
        for field in sorted(set(before) | set(after))
        if field not in {"link", "comment"} and before.get(field) != after.get(field)
    ]
    return IssueHistoryEvent(
        id=encode_identifier("issue_history", row.id),
        issue_id=encode_identifier("issue", issue_id or row.entity_id),
        event_type=cast(Any, event_type),
        actor_label=row.actor_label,
        changes=changes,
        related_resource_id=related,
        occurred_at=row.occurred_at,
    )


def attachment_owner(row: Attachment) -> AttachmentOwner:
    if row.owner_type == "ISSUE":
        return AttachmentOwner(
            type="ISSUE", issue_id=encode_identifier("issue", row.owner_id)
        )
    return AttachmentOwner(
        type="EXECUTION_ATTEMPT",
        execution_attempt_id=encode_identifier("attempt", row.owner_id),
    )


def attachment_resource(row: Attachment) -> AttachmentResource:
    identifier = encode_identifier("attachment", row.id)
    return AttachmentResource(
        id=identifier,
        owner=attachment_owner(row),
        filename=row.file_name,
        mime_type=cast(Any, row.mime_type),
        size_bytes=row.size_bytes,
        sha256=row.checksum,
        storage_provider=cast(Any, row.storage_provider),
        scan_status=(
            "LEGACY_MIGRATION_REQUIRED"
            if row.storage_provider == "GOOGLE_DRIVE"
            else "CLEAN" if row.storage_status == "AVAILABLE" else "ERROR"
        ),
        proxy_path=f"/api/attachments/{identifier}/content",
        resource_version=row.resource_version,
        uploaded_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _execution_snapshot(
    session: Session, event: OutboxEvent
) -> ExecutionIssueSnapshot | None:
    payload = event.payload
    raw_attempt_id = payload.get("sourceAttemptId")
    raw_item_id = payload.get("sourceCycleItemId")
    raw_version_id = payload.get("sourceTestCaseVersionId")
    if not all(isinstance(value, str) for value in (raw_attempt_id, raw_item_id, raw_version_id)):
        return None
    try:
        attempt_id = uuid.UUID(cast(str, raw_attempt_id))
        item_id = uuid.UUID(cast(str, raw_item_id))
        version_id = uuid.UUID(cast(str, raw_version_id))
    except ValueError:
        return None
    attempt = session.get(ExecutionAttempt, attempt_id)
    item = session.get(CycleItem, item_id)
    version = session.get(TestCaseVersion, version_id)
    if attempt is None or item is None or version is None:
        return None
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        return None
    steps = [
        IssueStepPayload(
            sequence=int(step["sequence"]),
            action=str(step["action"]),
            expected_result=str(step.get("expectedResult") or payload["expectedResult"]),
        )
        for step in raw_steps
        if isinstance(step, dict)
    ]
    attachments = list(
        session.scalars(
            select(Attachment.id)
            .where(
                Attachment.owner_type == "EXECUTION_ATTEMPT",
                Attachment.owner_id == attempt.id,
                Attachment.created_at <= event.occurred_at,
            )
            .order_by(Attachment.created_at, Attachment.id)
        )
    )
    return ExecutionIssueSnapshot(
        source_attempt_id=encode_identifier("attempt", attempt.id),
        source_attempt_status=cast(Any, payload.get("sourceStatus", attempt.status)),
        cycle_id=encode_identifier("cycle", item.cycle_id),
        cycle_item_id=encode_identifier("cycle_item", item.id),
        test_case_id=encode_identifier("case", item.test_case_id),
        pinned_version_id=encode_identifier("case_version", version.id),
        pinned_version=version.version_number,
        platform=cast(Any, attempt.platform),
        summary=str(payload["summary"]),
        precondition=str(payload["precondition"]),
        steps=steps,
        expected_result=str(payload["expectedResult"]),
        actual_result=str(payload["actualResult"]),
        note=cast(str | None, payload.get("sourceNote")),
        attachment_ids=[encode_identifier("attachment", value) for value in attachments],
        captured_at=event.occurred_at,
    )


def _link_actor(session: Session, link: IssueLink) -> str | None:
    audits = session.scalars(
        select(AuditLog)
        .where(
            AuditLog.entity_type == "ISSUE",
            AuditLog.entity_id == link.issue_id,
            AuditLog.action == "LINK",
        )
        .order_by(AuditLog.occurred_at, AuditLog.id)
    )
    for audit in audits:
        snapshot = (audit.after or {}).get("link")
        if isinstance(snapshot, dict) and snapshot.get("id") == str(link.id):
            return audit.actor_label
    return None


def _without_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "_idempotency"}


def _related_identifier(value: object, prefix: str) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return encode_identifier(prefix, uuid.UUID(value))
    except ValueError:
        return None


def _public_issue_key(value: str) -> str:
    prefix, separator, number = value.rpartition("-")
    if separator and number.isdigit():
        return f"{prefix}-{int(number)}"
    return value
