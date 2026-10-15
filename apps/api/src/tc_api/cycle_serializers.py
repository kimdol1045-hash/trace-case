"""Database-to-wire serializers for the Sprint 3 cycle ledger."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tc_api.cycle_models import (
    CycleItemResponse,
    CycleListItemResponse,
    CycleProgressResponse,
    CycleResourceResponse,
    ExecutionAttemptResponse,
    ExecutionStepResponse,
    ExecutionStatusCountsResponse,
    LabelProgressResponse,
    LabelScope,
    LatestCaseVersionRefResponse,
    LatestPlatformExecutionResponse,
    OverallProgressResponse,
    OwnerProgressResponse,
    PlatformProgressResponse,
    ProgressValuesResponse,
    StepExecutionResponse,
    WorkItemProgressResponse,
    WorkItemScope,
)
from tc_api.ids import encode_identifier
from tc_api.models import (
    FieldDiffResponse,
    StepDiffResponse,
    TestCaseVersionDiffResponse,
    TestStepResponse,
)
from tc_api.serializers import version_response
from tc_backend.db.cases import CaseRepository, VersionSnapshot
from tc_backend.db.cycles import (
    CycleAggregate,
    CycleItemVersionDiff,
    CycleProgress,
    ProgressBucket,
)
from tc_backend.db.models import (
    AuditLog,
    CycleItem,
    CycleScopeType,
    CycleStatus,
    ExecutionAttempt,
    ExecutionPlatform,
    ExecutionStepResult,
    ExecutionStatus,
    TestCase,
    TestCaseVersion,
    TestCycle,
    TestStep,
    WorkItem,
)


def cycle_scope_response(
    session: Session, cycle: TestCycle
) -> WorkItemScope | LabelScope:
    if cycle.scope_type == CycleScopeType.WORK_ITEM.value:
        work_item = session.execute(
            select(WorkItem).where(
                WorkItem.workspace_id == cycle.workspace_id,
                WorkItem.work_item_key == cycle.scope_value,
            )
        ).scalar_one_or_none()
        if work_item is None:
            raise RuntimeError("cycle WORK_ITEM scope no longer resolves")
        return WorkItemScope(
            type="WORK_ITEM",
            workItemId=encode_identifier("work_item", work_item.id),
            workItemKey=work_item.work_item_key,
        )
    return LabelScope(
        type="LABEL",
        label=cycle.scope_value,
        labels=list(cycle.scope_values or [cycle.scope_value]),
    )


def cycle_resource_response(
    session: Session,
    value: CycleAggregate | TestCycle,
) -> CycleResourceResponse:
    cycle = value.cycle if isinstance(value, CycleAggregate) else value
    item_count = (
        len(value.items)
        if isinstance(value, CycleAggregate)
        else session.scalar(select(func.count()).where(CycleItem.cycle_id == cycle.id))
        or 0
    )
    started_at = _cycle_started_at(session, cycle)
    return CycleResourceResponse(
        id=encode_identifier("cycle", cycle.id),
        name=cycle.name,
        description=cycle.description,
        start_date=cycle.start_date,
        status=cast(Any, cycle.status),
        scope=cycle_scope_response(session, cycle),
        scope_locked=cycle.status != CycleStatus.PLANNED.value,
        resource_version=cycle.resource_version,
        item_count=item_count,
        created_at=cycle.created_at,
        updated_at=cycle.updated_at,
        started_at=started_at,
        completed_at=cycle.completed_at,
        deleted_at=cycle.deleted_at,
    )


def cycle_list_item_response(
    session: Session, cycle: TestCycle
) -> CycleListItemResponse:
    items = list(
        session.scalars(select(CycleItem.id).where(CycleItem.cycle_id == cycle.id))
    )
    attempts = (
        list(
            session.scalars(
                select(ExecutionAttempt)
                .where(ExecutionAttempt.cycle_item_id.in_(items))
                .order_by(
                    ExecutionAttempt.cycle_item_id,
                    ExecutionAttempt.platform,
                    ExecutionAttempt.attempt_no.desc(),
                )
            )
        )
        if items
        else []
    )
    latest: dict[tuple[uuid.UUID, str], ExecutionAttempt] = {}
    for attempt in attempts:
        latest.setdefault((attempt.cycle_item_id, attempt.platform), attempt)
    return CycleListItemResponse(
        id=encode_identifier("cycle", cycle.id),
        name=cycle.name,
        start_date=cycle.start_date,
        status=cast(Any, cycle.status),
        scope=cycle_scope_response(session, cycle),
        resource_version=cycle.resource_version,
        item_count=len(items),
        platform_result_count=sum(
            row.status
            in {
                ExecutionStatus.PASS.value,
                ExecutionStatus.FAIL.value,
                ExecutionStatus.BLOCKED.value,
            }
            for row in latest.values()
        ),
        updated_at=cycle.updated_at,
        deleted_at=cycle.deleted_at,
    )


def attempt_response(
    session: Session,
    attempt: ExecutionAttempt,
    *,
    is_latest: bool | None = None,
) -> ExecutionAttemptResponse:
    item = session.get(CycleItem, attempt.cycle_item_id)
    if item is None:
        raise RuntimeError("execution attempt has no cycle item")
    if is_latest is None:
        latest_no = session.scalar(
            select(func.max(ExecutionAttempt.attempt_no)).where(
                ExecutionAttempt.cycle_item_id == attempt.cycle_item_id,
                ExecutionAttempt.platform == attempt.platform,
            )
        )
        is_latest = attempt.attempt_no == latest_no
    return ExecutionAttemptResponse(
        id=encode_identifier("attempt", attempt.id),
        cycle_id=encode_identifier("cycle", item.cycle_id),
        cycle_item_id=encode_identifier("cycle_item", item.id),
        pinned_version_id=encode_identifier("case_version", item.test_case_version_id),
        platform=cast(Any, attempt.platform),
        attempt_no=attempt.attempt_no,
        status=cast(Any, attempt.status),
        actual_result=attempt.actual_result,
        note=attempt.note,
        executor_label=attempt.executor_label,
        started_at=attempt.started_at,
        completed_at=attempt.finished_at,
        is_latest=is_latest,
    )


def step_execution_response(
    session: Session,
    item: CycleItem,
    platform: str,
    attempt: ExecutionAttempt | None,
) -> StepExecutionResponse:
    steps = tuple(
        session.scalars(
            select(TestStep)
            .where(TestStep.test_case_version_id == item.test_case_version_id)
            .order_by(TestStep.sequence)
        )
    )
    results = (
        {
            row.sequence: row
            for row in session.scalars(
                select(ExecutionStepResult)
                .where(ExecutionStepResult.execution_attempt_id == attempt.id)
                .order_by(ExecutionStepResult.sequence)
            )
        }
        if attempt is not None
        else {}
    )
    return StepExecutionResponse(
        cycle_id=encode_identifier("cycle", item.cycle_id),
        cycle_item_id=encode_identifier("cycle_item", item.id),
        pinned_version_id=encode_identifier("case_version", item.test_case_version_id),
        platform=cast(Any, platform),
        attempt_id=(encode_identifier("attempt", attempt.id) if attempt else None),
        attempt_no=attempt.attempt_no if attempt else None,
        status=cast(Any, attempt.status if attempt else ExecutionStatus.UNEXECUTED.value),
        executor_label=attempt.executor_label if attempt else None,
        started_at=attempt.started_at if attempt else None,
        completed_at=attempt.finished_at if attempt else None,
        recorded_step_count=len(results),
        total_step_count=len(steps),
        steps=[
            ExecutionStepResponse(
                test_step_id=encode_identifier("test_step", step.id),
                sequence=step.sequence,
                precondition=step.precondition,
                action=step.action,
                expected_result=step.expected_result,
                status=cast(
                    Any,
                    results[step.sequence].status
                    if step.sequence in results
                    else ExecutionStatus.UNEXECUTED.value,
                ),
                note=(results[step.sequence].note if step.sequence in results else None),
                recorded_at=(
                    results[step.sequence].recorded_at
                    if step.sequence in results
                    else None
                ),
                updated_at=(
                    results[step.sequence].updated_at
                    if step.sequence in results
                    else None
                ),
            )
            for step in steps
        ],
    )


def cycle_item_response(session: Session, item: CycleItem) -> CycleItemResponse:
    test_case = session.get(TestCase, item.test_case_id)
    pinned_row = session.get(TestCaseVersion, item.test_case_version_id)
    work_item = session.get(WorkItem, item.work_item_id)
    if test_case is None or pinned_row is None or work_item is None:
        raise RuntimeError("cycle item references an unavailable library row")
    latest_row = session.get(TestCaseVersion, test_case.current_version_id)
    if latest_row is None:
        raise RuntimeError("cycle item test case has no latest version")
    repository = CaseRepository(session, workspace_id=test_case.workspace_id)
    pinned = repository.get_version(test_case.id, pinned_row.version_number)
    latest_attempts = _latest_attempts(session, item.id)
    platform_executions: list[LatestPlatformExecutionResponse] = []
    for platform in ExecutionPlatform:
        attempt = latest_attempts.get(platform.value)
        platform_executions.append(
            LatestPlatformExecutionResponse(
                platform=cast(Any, platform.value),
                status=cast(
                    Any,
                    attempt.status
                    if attempt is not None
                    else ExecutionStatus.UNEXECUTED.value,
                ),
                latest_attempt=(
                    attempt_response(session, attempt, is_latest=True)
                    if attempt
                    else None
                ),
            )
        )
    return CycleItemResponse(
        id=encode_identifier("cycle_item", item.id),
        cycle_id=encode_identifier("cycle", item.cycle_id),
        test_case_id=encode_identifier("case", item.test_case_id),
        case_key=test_case.case_key,
        work_item_id=encode_identifier("work_item", item.work_item_id),
        work_item_key=work_item.work_item_key,
        owner_label=pinned.assignee_label,
        labels=list(pinned.tag_names),
        sort_order=test_case.sort_order,
        pinned_version_id=encode_identifier("case_version", item.test_case_version_id),
        pinned_version=version_response(session, pinned),
        latest_version=LatestCaseVersionRefResponse(
            version_id=encode_identifier("case_version", latest_row.id),
            version=latest_row.version_number,
            updated_at=latest_row.created_at,
        ),
        is_latest_version=item.test_case_version_id == latest_row.id,
        platform_executions=platform_executions,
        created_at=item.created_at,
    )


def cycle_item_version_diff(
    session: Session,
    value: CycleItemVersionDiff,
) -> TestCaseVersionDiffResponse:
    before = CaseRepository(session).get_version(
        value.test_case_id, value.pinned_version
    )
    after = CaseRepository(session).get_version(
        value.test_case_id, value.current_version
    )
    return _test_case_diff(before, after)


def cycle_progress_response(
    session: Session, progress: CycleProgress
) -> CycleProgressResponse:
    cycle = session.get(TestCycle, progress.cycle_id)
    if cycle is None:
        raise RuntimeError("cycle progress references a missing cycle")
    work_items = {
        row.work_item_key: row
        for row in session.scalars(
            select(WorkItem).where(
                WorkItem.workspace_id == cycle.workspace_id
            )
        )
    }
    return CycleProgressResponse(
        cycle_id=encode_identifier("cycle", progress.cycle_id),
        calculated_at=datetime.now(UTC),
        excludes_planned=True,
        latest_attempt_only=True,
        overall=OverallProgressResponse(
            dimension="OVERALL",
            key="ALL",
            label="All executions",
            values=_progress_values(progress.overall),
        ),
        by_platform=[
            PlatformProgressResponse(
                dimension="PLATFORM",
                key=cast(Any, bucket.key),
                label=bucket.key,
                values=_progress_values(bucket),
            )
            for bucket in progress.platforms
        ],
        by_owner=[
            OwnerProgressResponse(
                dimension="OWNER",
                key=bucket.key,
                label=bucket.key,
                values=_progress_values(bucket),
            )
            for bucket in progress.owners
        ],
        by_work_item=[
            WorkItemProgressResponse(
                dimension="WORK_ITEM",
                key=(
                    encode_identifier("work_item", work_items[bucket.key].id)
                    if bucket.key in work_items
                    else bucket.key
                ),
                label=bucket.key,
                values=_progress_values(bucket),
            )
            for bucket in progress.work_items
        ],
        by_label=[
            LabelProgressResponse(
                dimension="LABEL",
                key=bucket.key,
                label=bucket.key,
                values=_progress_values(bucket),
            )
            for bucket in progress.labels
        ],
    )


def _progress_values(bucket: ProgressBucket) -> ProgressValuesResponse:
    executed = bucket.executed_count
    return ProgressValuesResponse(
        counts=ExecutionStatusCountsResponse(
            total=bucket.total_count,
            UNEXECUTED=bucket.unexecuted_count,
            PASS=bucket.pass_count,
            FAIL=bucket.fail_count,
            BLOCKED=bucket.blocked_count,
        ),
        executed_count=executed,
        pass_rate=bucket.pass_count / executed if executed else 0,
        aggregate_status=cast(Any, bucket.aggregate_status.value),
    )


def _cycle_started_at(session: Session, cycle: TestCycle) -> datetime | None:
    if cycle.status == CycleStatus.PLANNED.value:
        return None
    for row in session.new:
        if (
            isinstance(row, AuditLog)
            and row.entity_id == cycle.id
            and row.action == "START"
        ):
            return cycle.updated_at
    return (
        session.scalar(
            select(AuditLog.occurred_at)
            .where(
                AuditLog.entity_type == "TEST_CYCLE",
                AuditLog.entity_id == cycle.id,
                AuditLog.action == "START",
            )
            .order_by(AuditLog.occurred_at.asc())
            .limit(1)
        )
        or cycle.updated_at
    )


def _latest_attempts(
    session: Session, cycle_item_id: uuid.UUID
) -> dict[str, ExecutionAttempt]:
    rows = session.scalars(
        select(ExecutionAttempt)
        .where(ExecutionAttempt.cycle_item_id == cycle_item_id)
        .order_by(ExecutionAttempt.platform, ExecutionAttempt.attempt_no.desc())
    )
    latest: dict[str, ExecutionAttempt] = {}
    for attempt in rows:
        latest.setdefault(attempt.platform, attempt)
    return latest


def _test_case_diff(
    before: VersionSnapshot, after: VersionSnapshot
) -> TestCaseVersionDiffResponse:
    before_fields = _diff_fields(before)
    after_fields = _diff_fields(after)
    return TestCaseVersionDiffResponse(
        test_case_id=encode_identifier("case", before.test_case_id),
        from_version=before.version_number,
        to_version=after.version_number,
        field_changes=[
            FieldDiffResponse(
                field=cast(Any, name),
                before=before_fields[name],
                after=after_fields[name],
            )
            for name in before_fields
            if before_fields[name] != after_fields[name]
        ],
        step_changes=_step_diff(before.steps, after.steps),
    )


def _diff_fields(snapshot: VersionSnapshot) -> dict[str, Any]:
    return {
        "summary": snapshot.summary,
        "precondition": snapshot.precondition,
        "expectedResultMode": snapshot.expected_result_mode,
        "caseExpectedResult": snapshot.expected_result,
        "priority": snapshot.priority,
        "ownerLabel": snapshot.assignee_label,
        "labels": list(snapshot.tag_names),
    }


def _step(value: Any) -> TestStepResponse:
    return TestStepResponse(
        sequence=value.sequence,
        precondition=value.precondition,
        action=value.action,
        expected_result=value.expected_result,
    )


def _step_diff(
    before: tuple[Any, ...], after: tuple[Any, ...]
) -> list[StepDiffResponse]:
    changes: list[StepDiffResponse] = []
    before_by_content = {
        (item.precondition, item.action, item.expected_result): item for item in before
    }
    after_by_content = {
        (item.precondition, item.action, item.expected_result): item for item in after
    }
    moved_before: set[int] = set()
    moved_after: set[int] = set()
    for content, old in before_by_content.items():
        new = after_by_content.get(content)
        if new is not None and new.sequence != old.sequence:
            changes.append(
                StepDiffResponse(
                    change_type="MOVED", before=_step(old), after=_step(new)
                )
            )
            moved_before.add(old.sequence)
            moved_after.add(new.sequence)
    old_by_seq = {
        item.sequence: item for item in before if item.sequence not in moved_before
    }
    new_by_seq = {
        item.sequence: item for item in after if item.sequence not in moved_after
    }
    for sequence in sorted(old_by_seq.keys() | new_by_seq.keys()):
        old = old_by_seq.get(sequence)
        new = new_by_seq.get(sequence)
        if old is None:
            changes.append(
                StepDiffResponse(change_type="ADDED", before=None, after=_step(new))
            )
        elif new is None:
            changes.append(
                StepDiffResponse(change_type="REMOVED", before=_step(old), after=None)
            )
        elif (old.action, old.expected_result) != (new.action, new.expected_result):
            changes.append(
                StepDiffResponse(
                    change_type="CHANGED", before=_step(old), after=_step(new)
                )
            )
    return changes
