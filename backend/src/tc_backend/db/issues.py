"""Transaction-scoped internal issue, link, comment, audit, and outbox ledger."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import (
    InvalidIssueTransitionError,
    IssueDeletedError,
    IssueLinkCycleError,
    IssueLinkError,
    IssueRestoreConflictError,
    IssueSourceAttemptError,
    PermanentDeleteConflictError,
    ResourceNotFoundError,
    VersionConflictError,
)
from tc_backend.db.models import (
    Attachment,
    AuditLog,
    Comment,
    CycleItem,
    ExecutionAttempt,
    ExecutionStatus,
    Issue,
    IssueLink,
    IssueSeverity,
    IssueStatus,
    IssueStep,
    KeyEntityType,
    OutboxEvent,
    OutboxStatus,
    TestCase,
    TestCaseVersion,
    TestCycle,
    TestStep,
)
from tc_backend.db.workspaces import WorkspaceRepository


@dataclass(frozen=True, slots=True)
class IssueStepInput:
    action: str
    expected_result: str | None = None


@dataclass(frozen=True, slots=True)
class IssueContent:
    summary: str
    precondition: str
    steps: tuple[IssueStepInput, ...]
    expected_result: str
    actual_result: str
    severity: IssueSeverity = IssueSeverity.MEDIUM
    assignee_label: str | None = None
    source_note: str | None = None


@dataclass(frozen=True, slots=True)
class IssueAggregate:
    issue: Issue
    steps: tuple[IssueStep, ...]
    links: tuple[IssueLink, ...]
    comments: tuple[Comment, ...]
    audit_id: uuid.UUID | None = None
    outbox_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class IssueFieldChange:
    audit_id: uuid.UUID
    action: str
    field: str
    before: Any
    after: Any
    occurred_at: datetime


ISSUE_TRANSITIONS: dict[IssueStatus, frozenset[IssueStatus]] = {
    IssueStatus.OPEN: frozenset({IssueStatus.IN_PROGRESS}),
    IssueStatus.IN_PROGRESS: frozenset({IssueStatus.RESOLVED}),
    IssueStatus.RESOLVED: frozenset({IssueStatus.CLOSED, IssueStatus.REOPENED}),
    IssueStatus.CLOSED: frozenset({IssueStatus.REOPENED}),
    IssueStatus.REOPENED: frozenset({IssueStatus.OPEN}),
}

_UNSET = object()


def validate_issue_transition(
    current: IssueStatus | str,
    target: IssueStatus | str,
) -> IssueStatus:
    current_status = IssueStatus(current)
    target_status = IssueStatus(target)
    if target_status is current_status:
        return target_status
    if target_status not in ISSUE_TRANSITIONS[current_status]:
        raise InvalidIssueTransitionError(
            f"cannot transition issue from {current_status.value} to {target_status.value}"
        )
    return target_status


class IssueRepository:
    """Issue aggregate operations; the caller owns commit and rollback."""

    def __init__(self, session: Session, *, workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def create(
        self,
        *,
        audit: AuditContext,
        content: IssueContent | None = None,
        summary: str | None = None,
        precondition: str | None = None,
        steps: Sequence[IssueStepInput] | None = None,
        expected_result: str | None = None,
        actual_result: str | None = None,
        severity: IssueSeverity | str = IssueSeverity.MEDIUM,
        assignee_label: str | None = None,
        source_note: str | None = None,
        _audit_action: str = "CREATE",
        _event_type: str = "ISSUE_CREATED",
        _event_payload: dict[str, Any] | None = None,
    ) -> IssueAggregate:
        selected = content or IssueContent(
            summary=_required(summary, "summary"),
            precondition=_required(precondition, "precondition"),
            steps=tuple(steps or ()),
            expected_result=_required(expected_result, "expected_result"),
            actual_result=_required(actual_result, "actual_result"),
            severity=IssueSeverity(severity),
            assignee_label=assignee_label,
            source_note=source_note,
        )
        normalized = _normalize_content(selected)
        issue_id = uuid.uuid4()
        allocated = WorkspaceRepository(
            self.session, workspace_id=self.workspace_id
        ).allocate_key(entity_type=KeyEntityType.ISSUE, entity_id=issue_id, audit=audit)
        now = datetime.now(UTC)
        issue = Issue(
            id=issue_id,
            workspace_id=self.workspace_id,
            issue_key=allocated.key,
            summary=normalized.summary,
            precondition=normalized.precondition,
            expected_result=normalized.expected_result,
            actual_result=normalized.actual_result,
            source_note=normalized.source_note,
            status=IssueStatus.OPEN.value,
            severity=normalized.severity.value,
            assignee_label=normalized.assignee_label,
            resource_version=1,
            created_at=now,
            updated_at=now,
        )
        self.session.add(issue)
        self._replace_steps(issue.id, normalized.steps)
        self.session.flush()
        after = self._snapshot(issue)
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ISSUE",
            entity_id=issue.id,
            action=_audit_action,
            before=None,
            after=after,
            context=audit,
        )
        outbox_id = self._append_outbox(
            issue,
            _event_type,
            {**after, **(_event_payload or {})},
        )
        return self.get_aggregate(issue.id, audit_id=audit_id, outbox_id=outbox_id)

    create_issue = create

    def create_from_attempt(
        self,
        *,
        attempt_id: uuid.UUID,
        audit: AuditContext,
        severity: IssueSeverity | str = IssueSeverity.HIGH,
        summary: str | None = None,
        assignee_label: str | None = None,
    ) -> IssueAggregate:
        attempt, item, version = self._attempt_context(attempt_id, for_update=True)
        if attempt.status not in (ExecutionStatus.FAIL.value, ExecutionStatus.BLOCKED.value):
            raise IssueSourceAttemptError("issues can only be created from a FAIL or BLOCKED attempt")
        source_steps = tuple(
            self.session.scalars(
                select(TestStep)
                .where(TestStep.test_case_version_id == version.id)
                .order_by(TestStep.sequence)
            )
        )
        if not source_steps:
            raise IssueSourceAttemptError("the pinned case version has no reproduction steps")
        copied_actual = (
            attempt.actual_result
            if attempt.status == ExecutionStatus.FAIL.value
            else (attempt.actual_result or attempt.note)
        )
        if copied_actual is None or not copied_actual.strip():
            raise IssueSourceAttemptError("the source attempt has no reproducible actual result")
        content = IssueContent(
            summary=summary or version.summary,
            precondition=version.precondition,
            steps=tuple(
                IssueStepInput(action=step.action, expected_result=step.expected_result)
                for step in source_steps
            ),
            expected_result=_copied_expected_result(version, source_steps),
            actual_result=copied_actual,
            source_note=attempt.note,
            severity=IssueSeverity(severity),
            assignee_label=assignee_label if assignee_label is not None else version.assignee_label,
        )
        aggregate = self.create(
            content=content,
            audit=audit,
            _audit_action="CREATE_FROM_ATTEMPT",
            _event_type="ISSUE_CREATED_FROM_ATTEMPT",
            _event_payload={
                "sourceAttemptId": str(attempt.id),
                "sourceCycleItemId": str(item.id),
                "sourceTestCaseId": str(item.test_case_id),
                "sourceTestCaseVersionId": str(item.test_case_version_id),
                "sourceStatus": attempt.status,
                "sourceNote": attempt.note,
            },
        )
        self.add_link(
            issue_id=aggregate.issue.id,
            test_case_id=item.test_case_id,
            cycle_item_id=item.id,
            execution_attempt_id=attempt.id,
            audit=audit,
        )
        self.session.flush()
        return self.get_aggregate(
            aggregate.issue.id,
            audit_id=aggregate.audit_id,
            outbox_id=aggregate.outbox_id,
        )

    create_issue_from_attempt = create_from_attempt
    create_from_execution_attempt = create_from_attempt

    def get(
        self,
        issue_id: uuid.UUID,
        *,
        for_update: bool = False,
        include_deleted: bool = False,
    ) -> Issue:
        statement = select(Issue).where(
            Issue.id == issue_id,
            Issue.workspace_id == self.workspace_id,
        )
        if not include_deleted:
            statement = statement.where(Issue.deleted_at.is_(None))
        if for_update:
            statement = statement.with_for_update()
        issue = self.session.execute(statement).scalar_one_or_none()
        if issue is None:
            raise ResourceNotFoundError("ISSUE", issue_id)
        return issue

    get_issue = get

    def get_aggregate(
        self,
        issue_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        audit_id: uuid.UUID | None = None,
        outbox_id: uuid.UUID | None = None,
    ) -> IssueAggregate:
        issue = self.get(issue_id, include_deleted=include_deleted)
        return IssueAggregate(
            issue=issue,
            steps=tuple(
                self.session.scalars(
                    select(IssueStep)
                    .where(IssueStep.issue_id == issue.id)
                    .order_by(IssueStep.sequence)
                )
            ),
            links=tuple(
                self.session.scalars(
                    select(IssueLink)
                    .where(IssueLink.issue_id == issue.id)
                    .order_by(IssueLink.created_at, IssueLink.id)
                )
            ),
            comments=tuple(
                self.session.scalars(
                    select(Comment)
                    .where(Comment.owner_type == "ISSUE", Comment.owner_id == issue.id)
                    .order_by(Comment.created_at, Comment.id)
                )
            ),
            audit_id=audit_id,
            outbox_id=outbox_id,
        )

    def update(
        self,
        *,
        issue_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
        summary: str | None = None,
        precondition: str | None = None,
        steps: Sequence[IssueStepInput] | None = None,
        expected_result: str | None = None,
        actual_result: str | None = None,
        status: IssueStatus | str | None = None,
        severity: IssueSeverity | str | None = None,
        assignee_label: str | None | object = _UNSET,
        source_note: str | None | object = _UNSET,
    ) -> IssueAggregate:
        issue = self.get(issue_id, for_update=True)
        self._assert_mutable(issue, base_resource_version)
        before = self._snapshot(issue)
        if summary is not None:
            issue.summary = _nonblank(summary, "summary", maximum=200)
        if precondition is not None:
            issue.precondition = _nonblank(precondition, "precondition")
        if expected_result is not None:
            issue.expected_result = _nonblank(expected_result, "expected_result")
        if actual_result is not None:
            issue.actual_result = _nonblank(actual_result, "actual_result")
        if status is not None:
            issue.status = validate_issue_transition(issue.status, status).value
        if severity is not None:
            issue.severity = IssueSeverity(severity).value
        if assignee_label is not _UNSET:
            issue.assignee_label = _optional_nonblank(
                cast(str | None, assignee_label), "assignee_label", maximum=200
            )
        if source_note is not _UNSET:
            issue.source_note = _optional_nonblank(
                cast(str | None, source_note), "source_note"
            )
        if steps is not None:
            self._replace_steps(issue.id, tuple(steps))
        issue.resource_version += 1
        issue.updated_at = datetime.now(UTC)
        self.session.flush()
        after = self._snapshot(issue)
        action = "TRANSITION" if status is not None else "UPDATE"
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ISSUE",
            entity_id=issue.id,
            action=action,
            before=before,
            after=after,
            context=audit,
        )
        outbox_id = self._append_outbox(
            issue,
            "ISSUE_TRANSITIONED" if status is not None else "ISSUE_UPDATED",
            after,
        )
        return self.get_aggregate(
            issue.id,
            audit_id=audit_id,
            outbox_id=outbox_id,
        )

    update_issue = update

    def update_content(
        self,
        *,
        issue_id: uuid.UUID,
        base_resource_version: int,
        content: IssueContent,
        audit: AuditContext,
    ) -> IssueAggregate:
        normalized = _normalize_content(content)
        return self.update(
            issue_id=issue_id,
            base_resource_version=base_resource_version,
            summary=normalized.summary,
            precondition=normalized.precondition,
            steps=normalized.steps,
            expected_result=normalized.expected_result,
            actual_result=normalized.actual_result,
            severity=normalized.severity,
            assignee_label=normalized.assignee_label,
            source_note=normalized.source_note,
            audit=audit,
        )

    def transition_status(
        self,
        *,
        issue_id: uuid.UUID,
        status: IssueStatus | str,
        base_resource_version: int,
        audit: AuditContext,
    ) -> IssueAggregate:
        return self.update(
            issue_id=issue_id,
            base_resource_version=base_resource_version,
            status=status,
            audit=audit,
        )

    def soft_delete(
        self,
        *,
        issue_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> IssueAggregate:
        issue = self.get(issue_id, for_update=True)
        self._assert_mutable(issue, base_resource_version)
        before = self._snapshot(issue)
        issue.deleted_at = datetime.now(UTC)
        issue.updated_at = issue.deleted_at
        issue.resource_version += 1
        self.session.flush()
        after = self._snapshot(issue)
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ISSUE",
            entity_id=issue.id,
            action="DELETE",
            before=before,
            after=after,
            context=audit,
        )
        outbox_id = self._append_outbox(issue, "ISSUE_DELETED", after)
        return self.get_aggregate(
            issue.id,
            include_deleted=True,
            audit_id=audit_id,
            outbox_id=outbox_id,
        )

    delete = soft_delete
    delete_issue = soft_delete

    def restore(
        self,
        *,
        issue_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> IssueAggregate:
        issue = self.get(issue_id, for_update=True, include_deleted=True)
        if issue.resource_version != base_resource_version:
            raise VersionConflictError(
                "ISSUE", issue.id, base_resource_version, issue.resource_version
            )
        if issue.deleted_at is None:
            raise IssueRestoreConflictError("issue is not deleted")
        before = self._snapshot(issue)
        issue.deleted_at = None
        issue.resource_version += 1
        issue.updated_at = datetime.now(UTC)
        self.session.flush()
        after = self._snapshot(issue)
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ISSUE",
            entity_id=issue.id,
            action="RESTORE",
            before=before,
            after=after,
            context=audit,
        )
        outbox_id = self._append_outbox(issue, "ISSUE_RESTORED", after)
        return self.get_aggregate(issue.id, audit_id=audit_id, outbox_id=outbox_id)

    restore_issue = restore

    def permanent_delete(
        self,
        *,
        issue_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> tuple[uuid.UUID, int]:
        issue = self.get(issue_id, for_update=True, include_deleted=True)
        if issue.resource_version != base_resource_version:
            raise VersionConflictError(
                "ISSUE", issue.id, base_resource_version, issue.resource_version
            )
        if issue.deleted_at is None:
            raise PermanentDeleteConflictError(
                "ISSUE", issue.id, "soft delete is required first"
            )
        attachments = tuple(
            self.session.scalars(
                select(Attachment)
                .where(
                    Attachment.workspace_id == self.workspace_id,
                    Attachment.owner_type == "ISSUE",
                    Attachment.owner_id == issue.id,
                )
                .with_for_update()
            )
        )
        unsafe = sum(
            row.deleted_at is None or row.deletion_status != "DELETED"
            for row in attachments
        )
        if unsafe:
            raise PermanentDeleteConflictError(
                "ISSUE",
                issue.id,
                "attachment objects must be deleted before metadata",
                references={"pendingAttachments": unsafe},
            )
        before = self._snapshot(issue)
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ISSUE",
            entity_id=issue.id,
            action="PERMANENT_DELETE",
            before=before,
            after=None,
            context=audit,
        )
        self.session.execute(
            text("SELECT set_config('tc.permanent_delete', 'on', true)")
        )
        self.session.execute(
            delete(Attachment).where(
                Attachment.workspace_id == self.workspace_id,
                Attachment.owner_type == "ISSUE",
                Attachment.owner_id == issue.id,
            )
        )
        self.session.execute(
            delete(Comment).where(
                Comment.workspace_id == self.workspace_id,
                Comment.owner_type == "ISSUE",
                Comment.owner_id == issue.id,
            )
        )
        self.session.execute(delete(IssueLink).where(IssueLink.issue_id == issue.id))
        self.session.delete(issue)
        self.session.flush()
        return audit_id, len(attachments)

    def add_link(
        self,
        *,
        issue_id: uuid.UUID,
        audit: AuditContext,
        test_case_id: uuid.UUID | None = None,
        cycle_item_id: uuid.UUID | None = None,
        execution_attempt_id: uuid.UUID | None = None,
    ) -> IssueLink:
        issue = self.get(issue_id)
        if issue.deleted_at is not None:
            raise IssueDeletedError("cannot link a deleted issue")
        resolved_item: CycleItem | None = None
        if execution_attempt_id is not None:
            attempt, resolved_item, _ = self._attempt_context(execution_attempt_id)
            if cycle_item_id is not None and cycle_item_id != attempt.cycle_item_id:
                raise IssueLinkError("execution attempt does not belong to cycle_item_id")
            cycle_item_id = attempt.cycle_item_id
        if cycle_item_id is not None:
            resolved_item = resolved_item or self._get_cycle_item(cycle_item_id)
            if test_case_id is not None and test_case_id != resolved_item.test_case_id:
                raise IssueLinkError("cycle item does not belong to test_case_id")
            test_case_id = resolved_item.test_case_id
            version_id = resolved_item.test_case_version_id
            self._assert_same_link_cycle(issue.id, resolved_item.cycle_id)
        elif test_case_id is not None:
            test_case = self.session.get(TestCase, test_case_id)
            if test_case is None or test_case.workspace_id != self.workspace_id:
                raise ResourceNotFoundError("TEST_CASE", test_case_id)
            version_id = test_case.current_version_id
        else:
            raise IssueLinkError("an issue link requires a test case, cycle item, or attempt")
        duplicate = self.session.scalar(
            select(func.count())
            .select_from(IssueLink)
            .where(
                IssueLink.issue_id == issue.id,
                IssueLink.test_case_id == test_case_id,
                IssueLink.test_case_version_id == version_id,
                IssueLink.cycle_item_id.is_(cycle_item_id)
                if cycle_item_id is None
                else IssueLink.cycle_item_id == cycle_item_id,
                IssueLink.execution_attempt_id.is_(execution_attempt_id)
                if execution_attempt_id is None
                else IssueLink.execution_attempt_id == execution_attempt_id,
            )
        )
        if duplicate:
            raise IssueLinkError("the issue link already exists")
        link = IssueLink(
            id=uuid.uuid4(),
            issue_id=issue.id,
            test_case_id=test_case_id,
            test_case_version_id=version_id,
            cycle_item_id=cycle_item_id,
            execution_attempt_id=execution_attempt_id,
            created_at=datetime.now(UTC),
        )
        self.session.add(link)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ISSUE",
            entity_id=issue.id,
            action="LINK",
            before=self._snapshot(issue),
            after={**self._snapshot(issue), "link": self._link_snapshot(link)},
            context=audit,
        )
        return link

    link = add_link
    link_issue = add_link

    def add_comment(
        self,
        *,
        issue_id: uuid.UUID,
        body: str,
        audit: AuditContext,
        author_label: str | None = None,
    ) -> Comment:
        issue = self.get(issue_id)
        if issue.deleted_at is not None:
            raise IssueDeletedError("cannot comment on a deleted issue")
        now = datetime.now(UTC)
        comment = Comment(
            id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            owner_type="ISSUE",
            owner_id=issue.id,
            body=_nonblank(body, "body"),
            author_label=_optional_nonblank(author_label, "author_label", maximum=200),
            created_at=now,
            updated_at=now,
        )
        self.session.add(comment)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="ISSUE",
            entity_id=issue.id,
            action="COMMENT",
            before=self._snapshot(issue),
            after={
                **self._snapshot(issue),
                "comment": {
                    "id": str(comment.id),
                    "body": comment.body,
                    "authorLabel": comment.author_label,
                    "createdAt": comment.created_at.isoformat(),
                },
            },
            context=audit,
        )
        return comment

    append_comment = add_comment

    def list_field_changes(self, issue_id: uuid.UUID) -> tuple[IssueFieldChange, ...]:
        self.get(issue_id)
        rows = self.session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "ISSUE", AuditLog.entity_id == issue_id)
            .order_by(AuditLog.occurred_at, AuditLog.id)
        )
        changes: list[IssueFieldChange] = []
        for row in rows:
            before = row.before or {}
            after = row.after or {}
            for field in sorted(set(before) | set(after)):
                if before.get(field) != after.get(field):
                    changes.append(
                        IssueFieldChange(
                            audit_id=row.id,
                            action=row.action,
                            field=field,
                            before=before.get(field),
                            after=after.get(field),
                            occurred_at=row.occurred_at,
                        )
                    )
        return tuple(changes)

    field_history = list_field_changes

    def _replace_steps(self, issue_id: uuid.UUID, steps: Sequence[IssueStepInput]) -> None:
        normalized = _normalize_steps(steps)
        self.session.execute(delete(IssueStep).where(IssueStep.issue_id == issue_id))
        self.session.add_all(
            IssueStep(
                id=uuid.uuid4(),
                issue_id=issue_id,
                sequence=index,
                action=step.action,
                expected_result=step.expected_result,
            )
            for index, step in enumerate(normalized, start=1)
        )

    def _attempt_context(
        self,
        attempt_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> tuple[ExecutionAttempt, CycleItem, TestCaseVersion]:
        statement = (
            select(ExecutionAttempt, CycleItem, TestCaseVersion)
            .join(CycleItem, CycleItem.id == ExecutionAttempt.cycle_item_id)
            .join(TestCycle, TestCycle.id == CycleItem.cycle_id)
            .join(TestCaseVersion, TestCaseVersion.id == CycleItem.test_case_version_id)
            .where(
                ExecutionAttempt.id == attempt_id,
                TestCycle.workspace_id == self.workspace_id,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=ExecutionAttempt)
        row = self.session.execute(statement).one_or_none()
        if row is None:
            raise ResourceNotFoundError("EXECUTION_ATTEMPT", attempt_id)
        return row[0], row[1], row[2]

    def _get_cycle_item(self, cycle_item_id: uuid.UUID) -> CycleItem:
        item = self.session.execute(
            select(CycleItem)
            .join(TestCycle, TestCycle.id == CycleItem.cycle_id)
            .where(
                CycleItem.id == cycle_item_id,
                TestCycle.workspace_id == self.workspace_id,
            )
        ).scalar_one_or_none()
        if item is None:
            raise ResourceNotFoundError("CYCLE_ITEM", cycle_item_id)
        return item

    def _assert_same_link_cycle(self, issue_id: uuid.UUID, cycle_id: uuid.UUID) -> None:
        existing_cycle_id = self.session.scalar(
            select(CycleItem.cycle_id)
            .join(IssueLink, IssueLink.cycle_item_id == CycleItem.id)
            .where(IssueLink.issue_id == issue_id)
            .limit(1)
        )
        if existing_cycle_id is not None and existing_cycle_id != cycle_id:
            raise IssueLinkCycleError("an issue cannot link evidence from a different cycle")

    def _assert_mutable(self, issue: Issue, expected_version: int) -> None:
        if issue.resource_version != expected_version:
            raise VersionConflictError(
                "ISSUE", issue.id, expected_version, issue.resource_version
            )
        if issue.deleted_at is not None:
            raise IssueDeletedError("deleted issues are immutable")

    def _append_outbox(
        self,
        issue: Issue,
        event_type: str,
        payload: dict[str, Any],
    ) -> uuid.UUID:
        event_id = uuid.uuid4()
        self.session.add(
            OutboxEvent(
                id=event_id,
                workspace_id=self.workspace_id,
                aggregate_type="ISSUE",
                aggregate_id=issue.id,
                event_type=event_type,
                aggregate_version=issue.resource_version,
                payload=payload,
                status=OutboxStatus.READY.value,
                attempts=0,
            )
        )
        return event_id

    def _snapshot(self, issue: Issue) -> dict[str, Any]:
        steps = tuple(
            self.session.scalars(
                select(IssueStep)
                .where(IssueStep.issue_id == issue.id)
                .order_by(IssueStep.sequence)
            )
        )
        return {
            "id": str(issue.id),
            "issueKey": issue.issue_key,
            "summary": issue.summary,
            "precondition": issue.precondition,
            "steps": [
                {
                    "sequence": step.sequence,
                    "action": step.action,
                    "expectedResult": step.expected_result,
                }
                for step in steps
            ],
            "expectedResult": issue.expected_result,
            "actualResult": issue.actual_result,
            "sourceNote": issue.source_note,
            "status": issue.status,
            "severity": issue.severity,
            "assigneeLabel": issue.assignee_label,
            "resourceVersion": issue.resource_version,
            "deletedAt": issue.deleted_at.isoformat() if issue.deleted_at else None,
            "createdAt": issue.created_at.isoformat(),
            "updatedAt": issue.updated_at.isoformat(),
        }

    @staticmethod
    def _link_snapshot(link: IssueLink) -> dict[str, Any]:
        return {
            "id": str(link.id),
            "testCaseId": str(link.test_case_id) if link.test_case_id else None,
            "testCaseVersionId": (
                str(link.test_case_version_id) if link.test_case_version_id else None
            ),
            "cycleItemId": str(link.cycle_item_id) if link.cycle_item_id else None,
            "executionAttemptId": (
                str(link.execution_attempt_id) if link.execution_attempt_id else None
            ),
        }


def _normalize_content(content: IssueContent) -> IssueContent:
    return IssueContent(
        summary=_nonblank(content.summary, "summary", maximum=200),
        precondition=_nonblank(content.precondition, "precondition"),
        steps=_normalize_steps(content.steps),
        expected_result=_nonblank(content.expected_result, "expected_result"),
        actual_result=_nonblank(content.actual_result, "actual_result"),
        source_note=_optional_nonblank(content.source_note, "source_note"),
        severity=IssueSeverity(content.severity),
        assignee_label=_optional_nonblank(
            content.assignee_label, "assignee_label", maximum=200
        ),
    )


def _normalize_steps(steps: Sequence[IssueStepInput]) -> tuple[IssueStepInput, ...]:
    if not steps:
        raise ValueError("an issue requires at least one reproduction step")
    return tuple(
        IssueStepInput(
            action=_nonblank(step.action, f"steps[{index}].action"),
            expected_result=_optional_nonblank(
                step.expected_result, f"steps[{index}].expected_result"
            ),
        )
        for index, step in enumerate(steps, start=1)
    )


def _copied_expected_result(
    version: TestCaseVersion,
    steps: Sequence[TestStep],
) -> str:
    if version.expected_result is not None:
        return _nonblank(version.expected_result, "expected_result")
    per_step = [
        f"{step.sequence}. {step.expected_result}"
        for step in steps
        if step.expected_result is not None
    ]
    if not per_step:
        raise IssueSourceAttemptError("the pinned case version has no expected result")
    return "\n".join(per_step)


def _required(value: str | None, field: str) -> str:
    if value is None:
        raise ValueError(f"{field} is required")
    return value


def _nonblank(value: str, field: str, *, maximum: int | None = None) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field} cannot be blank")
    if maximum is not None and len(clean) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return clean


def _optional_nonblank(
    value: str | None,
    field: str,
    *,
    maximum: int | None = None,
) -> str | None:
    if value is None:
        return None
    return _nonblank(value, field, maximum=maximum)
