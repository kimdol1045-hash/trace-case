"""Transaction-scoped cycle snapshot and execution-attempt ledger."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.cases import CaseRepository, VersionDiff, normalize_tag_name
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import (
    ActualResultRequiredError,
    CycleScopeError,
    CycleSnapshotEmptyError,
    CycleStateError,
    ExecutionStateError,
    ExecutionValidationError,
    InvalidCycleTransitionError,
    PermanentDeleteConflictError,
    ResourceNotFoundError,
    VersionConflictError,
)
from tc_backend.db.models import (
    Attachment,
    CycleItem,
    CycleScopeType,
    CycleStatus,
    ExecutionAttempt,
    ExecutionPlatform,
    ExecutionStatus,
    ExecutionStepResult,
    IssueLink,
    OutboxEvent,
    OutboxStatus,
    Tag,
    TestCase,
    TestCaseStatus,
    TestCaseTag,
    TestCaseVersion,
    TestCaseVersionTag,
    TestCycle,
    TestStep,
    WorkItem,
)


class ProgressDimension(StrEnum):
    OVERALL = "OVERALL"
    PLATFORM = "PLATFORM"
    OWNER = "OWNER"
    WORK_ITEM = "WORK_ITEM"
    LABEL = "LABEL"


TERMINAL_EXECUTION_STATUSES = (
    ExecutionStatus.PASS,
    ExecutionStatus.FAIL,
    ExecutionStatus.BLOCKED,
)
ALL_EXECUTION_STATUSES = tuple(ExecutionStatus)
ALL_PLATFORMS = tuple(ExecutionPlatform)


@dataclass(frozen=True, slots=True)
class CycleAggregate:
    cycle: TestCycle
    items: tuple[CycleItem, ...]
    warnings: tuple[str, ...] = ()
    audit_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class CycleItemVersionDiff:
    cycle_item_id: uuid.UUID
    test_case_id: uuid.UUID
    pinned_version_id: uuid.UUID
    pinned_version: int
    current_version_id: uuid.UUID
    current_version: int
    diff: VersionDiff

    @property
    def has_changes(self) -> bool:
        return bool(self.diff.fields)


@dataclass(frozen=True, slots=True)
class StepResultInput:
    sequence: int
    status: ExecutionStatus | str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressBucket:
    dimension: ProgressDimension
    key: str
    total_count: int
    unexecuted_count: int
    pass_count: int
    fail_count: int
    blocked_count: int
    completion_rate: float
    success_rate: float | None

    @property
    def executed_count(self) -> int:
        """Results that participate in the shared execution verdict."""
        return self.pass_count + self.fail_count

    @property
    def completed_count(self) -> int:
        return self.pass_count + self.fail_count + self.blocked_count

    @property
    def success_denominator(self) -> int:
        return self.executed_count

    @property
    def aggregate_status(self) -> ExecutionStatus:
        if self.fail_count:
            return ExecutionStatus.FAIL
        if self.pass_count:
            return ExecutionStatus.PASS
        return ExecutionStatus.UNEXECUTED

    @property
    def total(self) -> int:
        return self.total_count

    @property
    def completion_percentage(self) -> float:
        return self.completion_rate * 100

    @property
    def success_percentage(self) -> float | None:
        return self.success_rate * 100 if self.success_rate is not None else None

    @property
    def status_counts(self) -> Mapping[str, int]:
        return {
            ExecutionStatus.UNEXECUTED.value: self.unexecuted_count,
            ExecutionStatus.PASS.value: self.pass_count,
            ExecutionStatus.FAIL.value: self.fail_count,
            ExecutionStatus.BLOCKED.value: self.blocked_count,
        }


@dataclass(frozen=True, slots=True)
class CycleProgress:
    cycle_id: uuid.UUID
    overall: ProgressBucket
    platforms: tuple[ProgressBucket, ...]
    owners: tuple[ProgressBucket, ...]
    work_items: tuple[ProgressBucket, ...]
    labels: tuple[ProgressBucket, ...]

    def for_dimension(self, dimension: ProgressDimension | str) -> tuple[ProgressBucket, ...]:
        selected = ProgressDimension(dimension)
        if selected is ProgressDimension.OVERALL:
            return (self.overall,)
        return {
            ProgressDimension.PLATFORM: self.platforms,
            ProgressDimension.OWNER: self.owners,
            ProgressDimension.WORK_ITEM: self.work_items,
            ProgressDimension.LABEL: self.labels,
        }[selected]


@dataclass(frozen=True, slots=True)
class _ProgressSlot:
    platform: str
    owner: str
    work_item: str
    labels: tuple[str, ...]
    status: str


def validate_execution_result(
    *,
    status: ExecutionStatus | str,
    actual_result: str | None,
    note: str | None,
) -> tuple[ExecutionStatus, str | None, str | None]:
    result_status = ExecutionStatus(status)
    if result_status not in TERMINAL_EXECUTION_STATUSES:
        raise ExecutionValidationError("an attempt can only finish as PASS, FAIL, or BLOCKED")
    clean_actual = (actual_result.strip() or None) if actual_result is not None else None
    clean_note = (note.strip() or None) if note is not None else None
    if result_status is ExecutionStatus.FAIL and clean_actual is None:
        raise ActualResultRequiredError("FAIL requires actual_result")
    return result_status, clean_actual, clean_note


def validate_step_result(
    status: ExecutionStatus | str, note: str | None
) -> tuple[ExecutionStatus, str | None]:
    result_status = ExecutionStatus(status)
    if result_status not in TERMINAL_EXECUTION_STATUSES:
        raise ExecutionValidationError("a step can only finish as PASS, FAIL, or BLOCKED")
    clean_note = (note.strip() or None) if note is not None else None
    return result_status, clean_note


def derive_attempt_result(
    results: Sequence[ExecutionStepResult],
) -> tuple[ExecutionStatus, str | None, str | None]:
    if not results:
        raise ExecutionValidationError("cannot derive an attempt without step results")
    statuses = {ExecutionStatus(result.status) for result in results}
    if ExecutionStatus.FAIL in statuses:
        overall = ExecutionStatus.FAIL
    elif ExecutionStatus.BLOCKED in statuses:
        overall = ExecutionStatus.BLOCKED
    else:
        overall = ExecutionStatus.PASS
    notes = [
        f"Step {result.sequence}: {result.note}"
        for result in results
        if result.note is not None
    ]
    actual_result = None
    if overall is ExecutionStatus.FAIL:
        actual_result = "; ".join(
            f"Step {result.sequence}: {result.note or 'FAIL'}"
            for result in results
            if result.status == ExecutionStatus.FAIL.value
        )
    return overall, actual_result, "; ".join(notes) or None


def aggregate_execution_statuses(
    statuses: Iterable[ExecutionStatus | str],
) -> ExecutionStatus:
    """Shared verdict rule: ignore unexecuted/blocked, with FAIL priority."""
    included = {
        ExecutionStatus(status)
        for status in statuses
        if ExecutionStatus(status)
        not in (ExecutionStatus.UNEXECUTED, ExecutionStatus.BLOCKED)
    }
    if ExecutionStatus.FAIL in included:
        return ExecutionStatus.FAIL
    if ExecutionStatus.PASS in included:
        return ExecutionStatus.PASS
    return ExecutionStatus.UNEXECUTED


def build_progress_bucket(
    *,
    dimension: ProgressDimension,
    key: str,
    statuses: Iterable[ExecutionStatus | str],
    include_success_rate: bool,
) -> ProgressBucket:
    counts = {status.value: 0 for status in ALL_EXECUTION_STATUSES}
    for status in statuses:
        counts[ExecutionStatus(status).value] += 1
    total = sum(counts.values())
    completed = sum(counts[status.value] for status in TERMINAL_EXECUTION_STATUSES)
    executed = counts[ExecutionStatus.PASS.value] + counts[ExecutionStatus.FAIL.value]
    completion_rate = completed / total if total else 0.0
    success_rate = (
        counts[ExecutionStatus.PASS.value] / executed
        if include_success_rate and executed
        else None
    )
    return ProgressBucket(
        dimension=dimension,
        key=key,
        total_count=total,
        unexecuted_count=counts[ExecutionStatus.UNEXECUTED.value],
        pass_count=counts[ExecutionStatus.PASS.value],
        fail_count=counts[ExecutionStatus.FAIL.value],
        blocked_count=counts[ExecutionStatus.BLOCKED.value],
        completion_rate=completion_rate,
        success_rate=success_rate,
    )


class CycleRepository:
    """Cycle aggregate operations; callers own the surrounding transaction."""

    def __init__(self, session: Session, *, workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def create(
        self,
        *,
        name: str,
        starts_at: datetime | date | None = None,
        scope_type: CycleScopeType | str,
        scope_value: str,
        scope_values: Sequence[str] | None = None,
        audit: AuditContext,
        description: str = "",
        start_date: date | None = None,
    ) -> CycleAggregate:
        if starts_at is not None and start_date is not None:
            raise ValueError("provide starts_at or start_date, not both")
        selected_start = starts_at if starts_at is not None else start_date
        if selected_start is None:
            raise ValueError("starts_at is required")
        selected_scope = CycleScopeType(scope_type)
        canonical_scopes = self._canonical_scope_values(
            selected_scope, scope_values or (scope_value,)
        )
        canonical_scope = canonical_scopes[0]
        now = datetime.now(UTC)
        cycle = TestCycle(
            id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            name=_nonblank(name, "name", maximum=200),
            description=description.strip(),
            starts_at=_as_datetime(selected_start),
            status=CycleStatus.PLANNED.value,
            scope_type=selected_scope.value,
            scope_value=canonical_scope,
            scope_values=list(canonical_scopes),
            resource_version=1,
            run_number=1,
            created_at=now,
            updated_at=now,
        )
        self.session.add(cycle)
        self.session.flush()
        self._replace_snapshot(cycle)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CYCLE",
            entity_id=cycle.id,
            action="CREATE",
            before=None,
            after=self._cycle_snapshot(cycle),
            context=audit,
        )
        return self.get_aggregate(cycle.id, audit_id=audit_id)

    create_cycle = create

    def get(
        self,
        cycle_id: uuid.UUID,
        *,
        for_update: bool = False,
        include_deleted: bool = False,
    ) -> TestCycle:
        filters = [
            TestCycle.id == cycle_id,
            TestCycle.workspace_id == self.workspace_id,
        ]
        if not include_deleted:
            filters.append(TestCycle.deleted_at.is_(None))
        statement = select(TestCycle).where(*filters)
        if for_update:
            statement = statement.with_for_update()
        cycle = self.session.execute(statement).scalar_one_or_none()
        if cycle is None:
            raise ResourceNotFoundError("TEST_CYCLE", cycle_id)
        return cycle

    get_cycle = get

    def get_aggregate(
        self,
        cycle_id: uuid.UUID,
        *,
        warnings: Sequence[str] = (),
        audit_id: uuid.UUID | None = None,
    ) -> CycleAggregate:
        cycle = self.get(cycle_id)
        items = tuple(
            self.session.scalars(
                select(CycleItem)
                .where(CycleItem.cycle_id == cycle.id)
                .order_by(CycleItem.created_at, CycleItem.id)
            )
        )
        return CycleAggregate(
            cycle=cycle,
            items=items,
            warnings=tuple(warnings),
            audit_id=audit_id,
        )

    def update(
        self,
        *,
        cycle_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
        name: str | None = None,
        description: str | None = None,
        starts_at: datetime | date | None = None,
        scope_type: CycleScopeType | str | None = None,
        scope_value: str | None = None,
        scope_values: Sequence[str] | None = None,
    ) -> CycleAggregate:
        cycle = self.get(cycle_id, for_update=True)
        self._assert_resource_version(cycle, base_resource_version)
        scope_requested = any(
            value is not None for value in (scope_type, scope_value, scope_values)
        )
        if scope_requested:
            self._assert_planned(cycle, "cycle scope")
        else:
            self._assert_details_mutable(cycle)
        before = self._cycle_snapshot(cycle)

        selected_scope = CycleScopeType(scope_type or cycle.scope_type)
        requested_scope_values = (
            tuple(scope_values)
            if scope_values is not None
            else (
                (scope_value,)
                if scope_value is not None
                else tuple(cycle.scope_values or (cycle.scope_value,))
            )
        )
        canonical_scopes = self._canonical_scope_values(
            selected_scope, requested_scope_values
        )
        canonical_scope = canonical_scopes[0]
        scope_changed = (
            selected_scope.value != cycle.scope_type
            or list(canonical_scopes) != list(cycle.scope_values)
        )
        if name is not None:
            cycle.name = _nonblank(name, "name", maximum=200)
        if description is not None:
            cycle.description = description.strip()
        if starts_at is not None:
            cycle.starts_at = _as_datetime(starts_at)
        cycle.scope_type = selected_scope.value
        cycle.scope_value = canonical_scope
        cycle.scope_values = list(canonical_scopes)
        self._touch(cycle)
        self.session.flush()
        if scope_changed:
            self._replace_snapshot(cycle)
            self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CYCLE",
            entity_id=cycle.id,
            action="CHANGE_SCOPE" if scope_changed else "UPDATE",
            before=before,
            after=self._cycle_snapshot(cycle),
            context=audit,
        )
        return self.get_aggregate(cycle.id, audit_id=audit_id)

    update_cycle = update

    def delete(self, *, cycle_id: uuid.UUID, audit: AuditContext) -> uuid.UUID:
        cycle = self.get(cycle_id, for_update=True)
        before = self._cycle_snapshot(cycle)
        cycle.deleted_at = datetime.now(UTC)
        self._touch(cycle, now=cycle.deleted_at)
        self.session.flush()
        return append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CYCLE",
            entity_id=cycle.id,
            action="DELETE",
            before=before,
            after=self._cycle_snapshot(cycle),
            context=audit,
        )

    delete_cycle = delete

    def restore(
        self,
        *,
        cycle_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> CycleAggregate:
        cycle = self.get(cycle_id, for_update=True, include_deleted=True)
        self._assert_resource_version(cycle, base_resource_version)
        if cycle.deleted_at is None:
            raise CycleStateError("cycle is not deleted")
        before = self._cycle_snapshot(cycle)
        cycle.deleted_at = None
        self._touch(cycle)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CYCLE",
            entity_id=cycle.id,
            action="RESTORE",
            before=before,
            after=self._cycle_snapshot(cycle),
            context=audit,
        )
        return self.get_aggregate(cycle.id, audit_id=audit_id)

    restore_cycle = restore

    def prepare_permanent_delete(
        self,
        *,
        cycle_id: uuid.UUID,
        base_resource_version: int,
    ) -> tuple[uuid.UUID, ...]:
        """Validate purge eligibility and return attachment-owner attempt ids."""

        cycle = self.get(cycle_id, for_update=True, include_deleted=True)
        self._assert_resource_version(cycle, base_resource_version)
        if cycle.deleted_at is None:
            raise PermanentDeleteConflictError(
                "TEST_CYCLE", cycle.id, "soft delete is required first"
            )
        item_ids = tuple(
            self.session.scalars(
                select(CycleItem.id)
                .where(CycleItem.cycle_id == cycle.id)
                .with_for_update()
            )
        )
        attempt_ids = (
            tuple(
                self.session.scalars(
                    select(ExecutionAttempt.id)
                    .where(ExecutionAttempt.cycle_item_id.in_(item_ids))
                    .with_for_update()
                )
            )
            if item_ids
            else ()
        )
        issue_links = (
            self.session.scalar(
                select(func.count())
                .select_from(IssueLink)
                .where(
                    (IssueLink.cycle_item_id.in_(item_ids))
                    | (IssueLink.execution_attempt_id.in_(attempt_ids))
                )
            )
            if item_ids
            else 0
        ) or 0
        if issue_links:
            raise PermanentDeleteConflictError(
                "TEST_CYCLE",
                cycle.id,
                "issue evidence still references the execution ledger",
                references={"issueLinks": issue_links},
            )
        return attempt_ids

    def permanent_delete(
        self,
        *,
        cycle_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> tuple[uuid.UUID, int]:
        cycle = self.get(cycle_id, for_update=True, include_deleted=True)
        self._assert_resource_version(cycle, base_resource_version)
        if cycle.deleted_at is None:
            raise PermanentDeleteConflictError(
                "TEST_CYCLE", cycle.id, "soft delete is required first"
            )
        item_ids = tuple(
            self.session.scalars(
                select(CycleItem.id)
                .where(CycleItem.cycle_id == cycle.id)
                .with_for_update()
            )
        )
        attempt_ids = (
            tuple(
                self.session.scalars(
                    select(ExecutionAttempt.id)
                    .where(ExecutionAttempt.cycle_item_id.in_(item_ids))
                    .with_for_update()
                )
            )
            if item_ids
            else ()
        )
        issue_links = (
            self.session.scalar(
                select(func.count())
                .select_from(IssueLink)
                .where(
                    (IssueLink.cycle_item_id.in_(item_ids))
                    | (IssueLink.execution_attempt_id.in_(attempt_ids))
                )
            )
            if item_ids
            else 0
        ) or 0
        if issue_links:
            raise PermanentDeleteConflictError(
                "TEST_CYCLE",
                cycle.id,
                "issue evidence still references the execution ledger",
                references={"issueLinks": issue_links},
            )
        attachments = (
            tuple(
                self.session.scalars(
                    select(Attachment)
                    .where(
                        Attachment.workspace_id == self.workspace_id,
                        Attachment.owner_type == "EXECUTION_ATTEMPT",
                        Attachment.owner_id.in_(attempt_ids),
                    )
                    .with_for_update()
                )
            )
            if attempt_ids
            else ()
        )
        unsafe = sum(
            row.deleted_at is None or row.deletion_status != "DELETED"
            for row in attachments
        )
        if unsafe:
            raise PermanentDeleteConflictError(
                "TEST_CYCLE",
                cycle.id,
                "attachment objects must be deleted before metadata",
                references={"pendingAttachments": unsafe},
            )
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CYCLE",
            entity_id=cycle.id,
            action="PERMANENT_DELETE",
            before=self._cycle_snapshot(cycle),
            after=None,
            context=audit,
        )
        self.session.execute(
            text("SELECT set_config('tc.permanent_delete', 'on', true)")
        )
        if attempt_ids:
            self.session.execute(
                delete(Attachment).where(
                    Attachment.workspace_id == self.workspace_id,
                    Attachment.owner_type == "EXECUTION_ATTEMPT",
                    Attachment.owner_id.in_(attempt_ids),
                )
            )
            self.session.execute(
                delete(ExecutionStepResult).where(
                    ExecutionStepResult.execution_attempt_id.in_(attempt_ids)
                )
            )
            self.session.execute(
                delete(ExecutionAttempt).where(ExecutionAttempt.id.in_(attempt_ids))
            )
        if item_ids:
            self.session.execute(delete(CycleItem).where(CycleItem.id.in_(item_ids)))
        self.session.delete(cycle)
        self.session.flush()
        return audit_id, len(attachments)

    def start(
        self,
        *,
        cycle_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> CycleAggregate:
        cycle = self.get(cycle_id, for_update=True)
        self._assert_resource_version(cycle, base_resource_version)
        if cycle.status != CycleStatus.PLANNED.value:
            raise InvalidCycleTransitionError(
                f"cannot transition cycle from {cycle.status} to IN_PROGRESS"
            )
        items = tuple(
            self.session.scalars(
                select(CycleItem)
                .where(CycleItem.cycle_id == cycle.id)
                .order_by(CycleItem.id)
                .with_for_update()
            )
        )
        if not items:
            raise CycleSnapshotEmptyError("a cycle requires at least one pinned case")
        scoped_rows = self._scoped_case_rows(
            CycleScopeType(cycle.scope_type),
            cycle.scope_value,
            scope_values=cycle.scope_values,
            for_update=True,
        )
        if {item.test_case_id for item in items} != {row[0].id for row in scoped_rows}:
            raise CycleScopeError(
                "cycle scope membership changed; explicitly refresh the PLANNED snapshot"
            )

        before = self._cycle_snapshot(cycle)
        cycle.status = CycleStatus.IN_PROGRESS.value
        self._touch(cycle)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CYCLE",
            entity_id=cycle.id,
            action="START",
            before=before,
            after=self._cycle_snapshot(cycle),
            context=audit,
        )
        return self.get_aggregate(cycle.id, audit_id=audit_id)

    start_cycle = start

    def complete(
        self,
        *,
        cycle_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> CycleAggregate:
        cycle = self.get(cycle_id, for_update=True)
        self._assert_resource_version(cycle, base_resource_version)
        if cycle.status != CycleStatus.IN_PROGRESS.value:
            raise InvalidCycleTransitionError(
                f"cannot transition cycle from {cycle.status} to COMPLETED"
            )
        progress = self.get_progress(cycle.id)
        warnings: list[str] = []
        if progress.overall.unexecuted_count:
            warnings.append(
                f"{progress.overall.unexecuted_count} platform executions are UNEXECUTED"
            )
        before = self._cycle_snapshot(cycle)
        now = datetime.now(UTC)
        cycle.status = CycleStatus.COMPLETED.value
        cycle.completed_at = now
        self._touch(cycle, now=now)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CYCLE",
            entity_id=cycle.id,
            action="COMPLETE",
            before=before,
            after={**self._cycle_snapshot(cycle), "warnings": warnings},
            context=audit,
        )
        return self.get_aggregate(cycle.id, warnings=warnings, audit_id=audit_id)

    complete_cycle = complete

    def reopen(
        self,
        *,
        cycle_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> CycleAggregate:
        cycle = self.get(cycle_id, for_update=True)
        self._assert_resource_version(cycle, base_resource_version)
        if cycle.status != CycleStatus.COMPLETED.value:
            raise InvalidCycleTransitionError(
                f"cannot reopen cycle from {cycle.status}"
            )
        before = self._cycle_snapshot(cycle)
        cycle.status = CycleStatus.IN_PROGRESS.value
        cycle.completed_at = None
        cycle.run_number += 1
        self._touch(cycle)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CYCLE",
            entity_id=cycle.id,
            action="REOPEN",
            before=before,
            after=self._cycle_snapshot(cycle),
            context=audit,
        )
        return self.get_aggregate(cycle.id, audit_id=audit_id)

    reopen_cycle = reopen

    def get_item_snapshot_diff(self, cycle_item_id: uuid.UUID) -> CycleItemVersionDiff:
        item = self._get_item(cycle_item_id)
        pinned = self.session.get(TestCaseVersion, item.test_case_version_id)
        test_case = self.session.get(TestCase, item.test_case_id)
        if pinned is None or test_case is None:
            raise ResourceNotFoundError("CYCLE_ITEM", cycle_item_id)
        current = self.session.get(TestCaseVersion, test_case.current_version_id)
        if current is None:
            raise ResourceNotFoundError("TEST_CASE_VERSION", test_case.current_version_id)
        diff = CaseRepository(self.session, workspace_id=self.workspace_id).diff_versions(
            test_case_id=test_case.id,
            from_version=pinned.version_number,
            to_version=current.version_number,
        )
        return CycleItemVersionDiff(
            cycle_item_id=item.id,
            test_case_id=item.test_case_id,
            pinned_version_id=pinned.id,
            pinned_version=pinned.version_number,
            current_version_id=current.id,
            current_version=current.version_number,
            diff=diff,
        )

    compare_item_snapshot = get_item_snapshot_diff

    def refresh_item_snapshot(
        self,
        *,
        cycle_item_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> CycleAggregate:
        item = self._get_item(cycle_item_id)
        cycle = self.get(item.cycle_id, for_update=True)
        self._assert_resource_version(cycle, base_resource_version)
        self._assert_planned(cycle, "snapshot refresh")
        item = self._get_item(cycle_item_id, for_update=True)
        scoped = {
            test_case.id: (test_case, version)
            for test_case, version in self._scoped_case_rows(
                CycleScopeType(cycle.scope_type),
                cycle.scope_value,
                scope_values=cycle.scope_values,
                for_update=True,
            )
        }
        selected = scoped.get(item.test_case_id)
        if selected is None:
            raise CycleScopeError("cycle item is no longer a member of the selected scope")
        test_case, current = selected
        before = self._cycle_snapshot(cycle)
        pinned_before = str(item.test_case_version_id)
        item.test_case_version_id = current.id
        item.work_item_id = test_case.work_item_id
        item.assignee_label = current.assignee_label
        item.updated_at = datetime.now(UTC)
        self._touch(cycle)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CYCLE",
            entity_id=cycle.id,
            action="REFRESH_SNAPSHOT",
            before={**before, "cycleItemId": str(item.id), "pinnedVersionId": pinned_before},
            after={
                **self._cycle_snapshot(cycle),
                "cycleItemId": str(item.id),
                "pinnedVersionId": str(item.test_case_version_id),
            },
            context=audit,
        )
        return self.get_aggregate(cycle.id, audit_id=audit_id)

    def refresh_snapshot(
        self,
        *,
        cycle_id: uuid.UUID,
        base_resource_version: int,
        audit: AuditContext,
    ) -> CycleAggregate:
        cycle = self.get(cycle_id, for_update=True)
        self._assert_resource_version(cycle, base_resource_version)
        self._assert_planned(cycle, "snapshot refresh")
        before = self._cycle_snapshot(cycle)
        self._replace_snapshot(cycle)
        self._touch(cycle)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CYCLE",
            entity_id=cycle.id,
            action="REFRESH_SNAPSHOT",
            before=before,
            after=self._cycle_snapshot(cycle),
            context=audit,
        )
        return self.get_aggregate(cycle.id, audit_id=audit_id)

    def start_attempt(
        self,
        *,
        cycle_item_id: uuid.UUID,
        platform: ExecutionPlatform | str,
        audit: AuditContext,
        executor_label: str | None = None,
        started_at: datetime | None = None,
    ) -> ExecutionAttempt:
        selected_platform = ExecutionPlatform(platform)
        item = self._get_item(cycle_item_id)
        cycle = self.get(item.cycle_id, for_update=True)
        if cycle.status != CycleStatus.IN_PROGRESS.value:
            raise ExecutionStateError("execution attempts require an IN_PROGRESS cycle")
        item = self._get_item(cycle_item_id, for_update=True)
        latest = self.session.execute(
            select(ExecutionAttempt)
            .where(
                ExecutionAttempt.cycle_item_id == item.id,
                ExecutionAttempt.platform == selected_platform.value,
            )
            .order_by(ExecutionAttempt.attempt_no.desc())
            .limit(1)
        ).scalar_one_or_none()
        if (
            latest is not None
            and latest.status == ExecutionStatus.UNEXECUTED.value
            and latest.cycle_run_number == cycle.run_number
        ):
            raise ExecutionStateError("the platform already has an active attempt")
        attempt = ExecutionAttempt(
            id=uuid.uuid4(),
            cycle_item_id=item.id,
            platform=selected_platform.value,
            attempt_no=1 if latest is None else latest.attempt_no + 1,
            cycle_run_number=cycle.run_number,
            status=ExecutionStatus.UNEXECUTED.value,
            executor_label=_optional_nonblank(
                executor_label, "executor_label", maximum=200
            ),
            started_at=started_at or datetime.now(UTC),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.session.add(attempt)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="EXECUTION_ATTEMPT",
            entity_id=attempt.id,
            action="START",
            before=None,
            after=self._attempt_snapshot(attempt),
            context=audit,
        )
        return attempt

    start_execution_attempt = start_attempt
    create_attempt = start_attempt

    def record_step_results(
        self,
        *,
        cycle_item_id: uuid.UUID,
        platform: ExecutionPlatform | str,
        results: Sequence[StepResultInput],
        audit: AuditContext,
        executor_label: str | None = None,
    ) -> ExecutionAttempt:
        """Upsert active step results and derive the terminal attempt when complete.

        Results are accepted only after the cycle has been explicitly started.
        An active attempt remains UNEXECUTED until every pinned step is recorded.
        """
        if not results:
            raise ExecutionValidationError("at least one step result is required")
        sequences = [result.sequence for result in results]
        if len(sequences) != len(set(sequences)):
            raise ExecutionValidationError("step result sequences must be unique")
        selected_platform = ExecutionPlatform(platform)
        item = self._get_item(cycle_item_id, for_update=True)
        cycle = self.get(item.cycle_id, for_update=True)
        if cycle.status != CycleStatus.IN_PROGRESS.value:
            raise ExecutionStateError("execution results require an IN_PROGRESS cycle")

        steps = tuple(
            self.session.scalars(
                select(TestStep)
                .where(TestStep.test_case_version_id == item.test_case_version_id)
                .order_by(TestStep.sequence)
            )
        )
        steps_by_sequence = {step.sequence: step for step in steps}
        unknown = sorted(set(sequences).difference(steps_by_sequence))
        if unknown:
            raise ExecutionValidationError(
                f"step result sequences are not in the pinned version: {unknown}"
            )

        attempt = self.session.scalar(
            select(ExecutionAttempt)
            .where(
                ExecutionAttempt.cycle_item_id == item.id,
                ExecutionAttempt.platform == selected_platform.value,
            )
            .order_by(ExecutionAttempt.attempt_no.desc())
            .limit(1)
            .with_for_update()
        )
        if attempt is None:
            attempt = self.start_attempt(
                cycle_item_id=item.id,
                platform=selected_platform,
                executor_label=executor_label,
                audit=audit,
            )
        elif (
            attempt.status != ExecutionStatus.UNEXECUTED.value
            or attempt.cycle_run_number != cycle.run_number
        ):
            raise ExecutionStateError(
                "the latest attempt is finished; append an explicit retry before recording steps"
            )
        elif executor_label is not None:
            clean_executor_label = _optional_nonblank(
                executor_label, "executor_label", maximum=200
            )
            if clean_executor_label != attempt.executor_label:
                raise ExecutionStateError(
                    "executor_label is fixed when the execution attempt starts"
                )

        existing = {
            row.sequence: row
            for row in self.session.scalars(
                select(ExecutionStepResult)
                .where(ExecutionStepResult.execution_attempt_id == attempt.id)
                .with_for_update()
            )
        }
        now = datetime.now(UTC)
        for value in results:
            status_value, note_value = validate_step_result(value.status, value.note)
            row = existing.get(value.sequence)
            before = self._step_result_snapshot(row) if row is not None else None
            if row is None:
                row = ExecutionStepResult(
                    id=uuid.uuid4(),
                    execution_attempt_id=attempt.id,
                    test_step_id=steps_by_sequence[value.sequence].id,
                    sequence=value.sequence,
                    status=status_value.value,
                    note=note_value,
                    recorded_at=now,
                    updated_at=now,
                )
                self.session.add(row)
                existing[value.sequence] = row
            else:
                row.status = status_value.value
                row.note = note_value
                row.updated_at = now
            append_audit(
                self.session,
                workspace_id=self.workspace_id,
                entity_type="EXECUTION_STEP_RESULT",
                entity_id=row.id,
                action="CREATE" if before is None else "UPDATE",
                before=before,
                after=self._step_result_snapshot(row),
                context=audit,
            )
        self.session.flush()

        if len(existing) == len(steps):
            overall_status, actual_result, overall_note = derive_attempt_result(
                tuple(existing[step.sequence] for step in steps)
            )
            attempt = self.complete_attempt(
                attempt_id=attempt.id,
                status=overall_status,
                actual_result=actual_result,
                note=overall_note,
                audit=audit,
            )
        return attempt

    def get_step_results(
        self, attempt_id: uuid.UUID
    ) -> tuple[ExecutionStepResult, ...]:
        self._get_attempt(attempt_id)
        return tuple(
            self.session.scalars(
                select(ExecutionStepResult)
                .where(ExecutionStepResult.execution_attempt_id == attempt_id)
                .order_by(ExecutionStepResult.sequence)
            )
        )

    def complete_attempt(
        self,
        *,
        attempt_id: uuid.UUID,
        status: ExecutionStatus | str,
        audit: AuditContext,
        actual_result: str | None = None,
        note: str | None = None,
        finished_at: datetime | None = None,
    ) -> ExecutionAttempt:
        result_status, clean_actual, clean_note = validate_execution_result(
            status=status,
            actual_result=actual_result,
            note=note,
        )
        attempt = self._get_attempt(attempt_id)
        item = self._get_item(attempt.cycle_item_id)
        cycle = self.get(item.cycle_id, for_update=True)
        if cycle.status != CycleStatus.IN_PROGRESS.value:
            raise ExecutionStateError("execution results require an IN_PROGRESS cycle")
        attempt = self._get_attempt(attempt_id, for_update=True)
        if attempt.status != ExecutionStatus.UNEXECUTED.value:
            raise ExecutionStateError("finished execution attempts are immutable")
        if attempt.cycle_run_number != cycle.run_number:
            raise ExecutionStateError("an attempt from a previous cycle run cannot be completed")
        step_results = self.get_step_results(attempt.id)
        if step_results:
            expected_count = self.session.scalar(
                select(func.count())
                .select_from(TestStep)
                .where(TestStep.test_case_version_id == item.test_case_version_id)
            ) or 0
            if len(step_results) != expected_count:
                raise ExecutionValidationError(
                    "all pinned test steps require a result before attempt completion"
                )
            derived_status, _, _ = derive_attempt_result(step_results)
            if result_status != derived_status:
                raise ExecutionValidationError(
                    f"attempt status must match derived step result {derived_status.value}"
                )
        before = self._attempt_snapshot(attempt)
        attempt.status = result_status.value
        attempt.actual_result = clean_actual
        attempt.note = clean_note
        attempt.finished_at = finished_at or datetime.now(UTC)
        attempt.updated_at = datetime.now(UTC)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="EXECUTION_ATTEMPT",
            entity_id=attempt.id,
            action="COMPLETE",
            before=before,
            after=self._attempt_snapshot(attempt),
            context=audit,
        )
        self._append_execution_projection_event(attempt)
        return attempt

    complete_execution_attempt = complete_attempt
    record_result = complete_attempt

    def list_attempts(
        self,
        *,
        cycle_item_id: uuid.UUID,
        platform: ExecutionPlatform | str | None = None,
    ) -> tuple[ExecutionAttempt, ...]:
        self._get_item(cycle_item_id)
        statement = select(ExecutionAttempt).where(
            ExecutionAttempt.cycle_item_id == cycle_item_id
        )
        if platform is not None:
            statement = statement.where(
                ExecutionAttempt.platform == ExecutionPlatform(platform).value
            )
        statement = statement.order_by(
            ExecutionAttempt.platform,
            ExecutionAttempt.attempt_no,
        )
        return tuple(self.session.scalars(statement))

    def get_progress(self, cycle_id: uuid.UUID) -> CycleProgress:
        cycle = self.get(cycle_id)
        rows = self.session.execute(
            select(CycleItem, WorkItem.work_item_key)
            .join(WorkItem, WorkItem.id == CycleItem.work_item_id)
            .where(CycleItem.cycle_id == cycle.id)
            .order_by(CycleItem.id)
        ).all()
        item_ids = [item.id for item, _ in rows]
        latest_attempts: dict[tuple[uuid.UUID, str], ExecutionAttempt] = {}
        if item_ids:
            attempts = self.session.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.cycle_item_id.in_(item_ids))
                .order_by(
                    ExecutionAttempt.cycle_item_id,
                    ExecutionAttempt.platform,
                    ExecutionAttempt.attempt_no.desc(),
                )
            )
            for attempt in attempts:
                latest_attempts.setdefault(
                    (attempt.cycle_item_id, attempt.platform), attempt
                )

        labels_by_version: dict[uuid.UUID, list[str]] = defaultdict(list)
        version_ids = [item.test_case_version_id for item, _ in rows]
        if version_ids:
            for version_id, label in self.session.execute(
                select(TestCaseVersionTag.test_case_version_id, Tag.name)
                .join(Tag, Tag.id == TestCaseVersionTag.tag_id)
                .where(TestCaseVersionTag.test_case_version_id.in_(version_ids))
                .order_by(Tag.name)
            ):
                labels_by_version[version_id].append(label)

        slots: list[_ProgressSlot] = []
        for item, work_item_key in rows:
            labels = tuple(labels_by_version[item.test_case_version_id]) or ("UNLABELED",)
            owner = item.assignee_label or "UNASSIGNED"
            for platform in ALL_PLATFORMS:
                latest_attempt = latest_attempts.get((item.id, platform.value))
                slots.append(
                    _ProgressSlot(
                        platform=platform.value,
                        owner=owner,
                        work_item=work_item_key,
                        labels=labels,
                        status=(
                            latest_attempt.status
                            if latest_attempt is not None
                            else ExecutionStatus.UNEXECUTED.value
                        ),
                    )
                )

        include_success_rate = cycle.status != CycleStatus.PLANNED.value
        overall = build_progress_bucket(
            dimension=ProgressDimension.OVERALL,
            key="ALL",
            statuses=(slot.status for slot in slots),
            include_success_rate=include_success_rate,
        )
        return CycleProgress(
            cycle_id=cycle.id,
            overall=overall,
            platforms=self._dimension_buckets(
                slots,
                ProgressDimension.PLATFORM,
                lambda slot: (slot.platform,),
                include_success_rate,
                preferred_order=[platform.value for platform in ALL_PLATFORMS],
            ),
            owners=self._dimension_buckets(
                slots,
                ProgressDimension.OWNER,
                lambda slot: (slot.owner,),
                include_success_rate,
            ),
            work_items=self._dimension_buckets(
                slots,
                ProgressDimension.WORK_ITEM,
                lambda slot: (slot.work_item,),
                include_success_rate,
            ),
            labels=self._dimension_buckets(
                slots,
                ProgressDimension.LABEL,
                lambda slot: slot.labels,
                include_success_rate,
            ),
        )

    progress = get_progress

    def _dimension_buckets(
        self,
        slots: Sequence[_ProgressSlot],
        dimension: ProgressDimension,
        keys: Callable[[_ProgressSlot], tuple[str, ...]],
        include_success_rate: bool,
        *,
        preferred_order: Sequence[str] = (),
    ) -> tuple[ProgressBucket, ...]:
        statuses: dict[str, list[str]] = defaultdict(list)
        for slot in slots:
            for key in keys(slot):
                statuses[key].append(slot.status)
        order = {key: index for index, key in enumerate(preferred_order)}
        return tuple(
            build_progress_bucket(
                dimension=dimension,
                key=key,
                statuses=statuses[key],
                include_success_rate=include_success_rate,
            )
            for key in sorted(statuses, key=lambda value: (order.get(value, len(order)), value))
        )

    def _replace_snapshot(self, cycle: TestCycle) -> None:
        self._assert_planned(cycle, "snapshot mutation")
        rows = self._scoped_case_rows(
            CycleScopeType(cycle.scope_type),
            cycle.scope_value,
            scope_values=cycle.scope_values,
            for_update=True,
        )
        if not rows:
            raise CycleSnapshotEmptyError("the selected scope contains no ACTIVE cases")
        self.session.execute(delete(CycleItem).where(CycleItem.cycle_id == cycle.id))
        now = datetime.now(UTC)
        self.session.add_all(
            CycleItem(
                id=uuid.uuid4(),
                cycle_id=cycle.id,
                test_case_id=test_case.id,
                test_case_version_id=version.id,
                work_item_id=test_case.work_item_id,
                assignee_label=version.assignee_label,
                created_at=now,
                updated_at=now,
            )
            for test_case, version in rows
        )

    def _scoped_case_rows(
        self,
        scope_type: CycleScopeType,
        scope_value: str,
        *,
        scope_values: Sequence[str] | None = None,
        for_update: bool,
    ) -> list[tuple[TestCase, TestCaseVersion]]:
        statement = (
            select(TestCase, TestCaseVersion)
            .join(
                TestCaseVersion,
                TestCaseVersion.id == TestCase.current_version_id,
            )
            .where(
                TestCase.workspace_id == self.workspace_id,
                TestCase.deleted_at.is_(None),
                TestCase.status == TestCaseStatus.ACTIVE.value,
                TestCaseVersion.status == TestCaseStatus.ACTIVE.value,
            )
        )
        if scope_type is CycleScopeType.WORK_ITEM:
            statement = statement.join(
                WorkItem, WorkItem.id == TestCase.work_item_id
            ).where(
                WorkItem.workspace_id == self.workspace_id,
                WorkItem.work_item_key == scope_value,
            )
        else:
            selected_values = scope_values or (scope_value,)
            normalized_values = [normalize_tag_name(value) for value in selected_values]
            statement = (
                statement.join(TestCaseTag, TestCaseTag.test_case_id == TestCase.id)
                .join(Tag, Tag.id == TestCaseTag.tag_id)
                .where(
                    Tag.workspace_id == self.workspace_id,
                    Tag.normalized_name.in_(normalized_values),
                )
            )
        statement = statement.order_by(TestCase.work_item_id, TestCase.sort_order, TestCase.id)
        if for_update:
            statement = statement.with_for_update(of=TestCase)
        rows: dict[uuid.UUID, tuple[TestCase, TestCaseVersion]] = {}
        for test_case, version in self.session.execute(statement):
            rows.setdefault(test_case.id, (test_case, version))
        return list(rows.values())

    def _canonical_scope_value(
        self,
        scope_type: CycleScopeType,
        scope_value: str,
    ) -> str:
        return self._canonical_scope_values(scope_type, (scope_value,))[0]

    def _canonical_scope_values(
        self,
        scope_type: CycleScopeType,
        scope_values: Sequence[str],
    ) -> tuple[str, ...]:
        if not scope_values or len(scope_values) > 50:
            raise CycleScopeError("cycle scope requires between 1 and 50 values")
        values = tuple(_nonblank(value, "scope_value", maximum=200) for value in scope_values)
        if scope_type is CycleScopeType.WORK_ITEM:
            if len(values) != 1:
                raise CycleScopeError("WORK_ITEM scope requires exactly one value")
            value = values[0]
            key = self.session.scalar(
                select(WorkItem.work_item_key).where(
                    WorkItem.workspace_id == self.workspace_id,
                    WorkItem.work_item_key == value,
                )
            )
            if key is None:
                raise CycleScopeError(f"WORK_ITEM scope {value!r} was not found")
            return (key,)
        normalized = [normalize_tag_name(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise CycleScopeError("LABEL scope values must be unique")
        labels = {
            row.normalized_name: row.name
            for row in self.session.scalars(
                select(Tag).where(
                    Tag.workspace_id == self.workspace_id,
                    Tag.normalized_name.in_(normalized),
                )
            )
        }
        missing = [value for value in normalized if value not in labels]
        if missing:
            raise CycleScopeError(f"LABEL scope {missing[0]!r} was not found")
        return tuple(labels[value] for value in normalized)

    def _get_item(self, item_id: uuid.UUID, *, for_update: bool = False) -> CycleItem:
        statement = (
            select(CycleItem)
            .join(TestCycle, TestCycle.id == CycleItem.cycle_id)
            .where(
                CycleItem.id == item_id,
                TestCycle.workspace_id == self.workspace_id,
                TestCycle.deleted_at.is_(None),
            )
        )
        if for_update:
            statement = statement.with_for_update(of=CycleItem)
        item = self.session.execute(statement).scalar_one_or_none()
        if item is None:
            raise ResourceNotFoundError("CYCLE_ITEM", item_id)
        return item

    def _append_execution_projection_event(self, attempt: ExecutionAttempt) -> uuid.UUID:
        event_id = uuid.uuid4()
        self.session.add(
            OutboxEvent(
                id=event_id,
                workspace_id=self.workspace_id,
                aggregate_type="EXECUTION_ATTEMPT",
                aggregate_id=attempt.id,
                event_type="EXECUTION_COMPLETED",
                aggregate_version=1,
                payload=self._attempt_snapshot(attempt),
                status=OutboxStatus.READY.value,
                attempts=0,
            )
        )
        return event_id

    def _get_attempt(
        self, attempt_id: uuid.UUID, *, for_update: bool = False
    ) -> ExecutionAttempt:
        statement = (
            select(ExecutionAttempt)
            .join(CycleItem, CycleItem.id == ExecutionAttempt.cycle_item_id)
            .join(TestCycle, TestCycle.id == CycleItem.cycle_id)
            .where(
                ExecutionAttempt.id == attempt_id,
                TestCycle.workspace_id == self.workspace_id,
                TestCycle.deleted_at.is_(None),
            )
        )
        if for_update:
            statement = statement.with_for_update(of=ExecutionAttempt)
        attempt = self.session.execute(statement).scalar_one_or_none()
        if attempt is None:
            raise ResourceNotFoundError("EXECUTION_ATTEMPT", attempt_id)
        return attempt

    def _assert_resource_version(self, cycle: TestCycle, expected: int) -> None:
        if cycle.resource_version != expected:
            raise VersionConflictError(
                "TEST_CYCLE", cycle.id, expected, cycle.resource_version
            )

    def _assert_planned(self, cycle: TestCycle, operation: str) -> None:
        if cycle.status != CycleStatus.PLANNED.value:
            raise CycleStateError(f"{operation} is only allowed while a cycle is PLANNED")

    def _assert_details_mutable(self, cycle: TestCycle) -> None:
        if cycle.status == CycleStatus.COMPLETED.value:
            raise CycleStateError("cycle details are locked after completion")

    def _touch(self, cycle: TestCycle, *, now: datetime | None = None) -> None:
        cycle.resource_version += 1
        cycle.updated_at = now or datetime.now(UTC)

    @staticmethod
    def _cycle_snapshot(cycle: TestCycle) -> dict[str, Any]:
        return {
            "id": str(cycle.id),
            "name": cycle.name,
            "description": cycle.description,
            "startsAt": cycle.starts_at.isoformat(),
            "status": cycle.status,
            "scopeType": cycle.scope_type,
            "scopeValue": cycle.scope_value,
            "scopeValues": list(cycle.scope_values),
            "resourceVersion": cycle.resource_version,
            "runNumber": cycle.run_number,
            "completedAt": cycle.completed_at.isoformat() if cycle.completed_at else None,
            "deletedAt": cycle.deleted_at.isoformat() if cycle.deleted_at else None,
            "createdAt": cycle.created_at.isoformat(),
            "updatedAt": cycle.updated_at.isoformat(),
        }

    @staticmethod
    def _attempt_snapshot(attempt: ExecutionAttempt) -> dict[str, Any]:
        return {
            "id": str(attempt.id),
            "cycleItemId": str(attempt.cycle_item_id),
            "platform": attempt.platform,
            "attemptNo": attempt.attempt_no,
            "cycleRunNumber": attempt.cycle_run_number,
            "status": attempt.status,
            "actualResult": attempt.actual_result,
            "note": attempt.note,
            "executorLabel": attempt.executor_label,
            "startedAt": attempt.started_at.isoformat(),
            "finishedAt": attempt.finished_at.isoformat() if attempt.finished_at else None,
        }

    @staticmethod
    def _step_result_snapshot(
        result: ExecutionStepResult | None,
    ) -> dict[str, Any] | None:
        if result is None:
            return None
        return {
            "id": str(result.id),
            "executionAttemptId": str(result.execution_attempt_id),
            "testStepId": str(result.test_step_id),
            "sequence": result.sequence,
            "status": result.status,
            "note": result.note,
            "recordedAt": result.recorded_at.isoformat(),
            "updatedAt": result.updated_at.isoformat(),
        }


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


def _as_datetime(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return datetime.combine(value, time.min, tzinfo=UTC)
