from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import CaseRepository
from tc_backend.db.cases import TestCaseContent as CaseContent
from tc_backend.db.cases import TestStepInput as StepInput
from tc_backend.db.cycles import CycleRepository, StepResultInput
from tc_backend.db.errors import (
    ActualResultRequiredError,
    CycleStateError,
    ExecutionStateError,
    InvalidCycleTransitionError,
    VersionConflictError,
)
from tc_backend.db.folders import FolderRepository
from tc_backend.db.models import (
    AuditLog,
    AuditSourceType,
    CycleItem,
    CycleScopeType,
    CycleStatus,
    ExecutionAttempt,
    ExecutionPlatform,
    ExecutionStatus,
    ExecutionStepResult,
    TestCycle,
)

pytestmark = pytest.mark.postgres


def _audit(label: str) -> AuditContext:
    return AuditContext(
        source_type=AuditSourceType.API,
        actor_label="cycle-repository-test",
        reason=label,
        trace_id=f"trace_{label}",
    )


def _content(summary: str, *, owner: str, labels: tuple[str, ...]) -> CaseContent:
    return CaseContent(
        summary=summary,
        precondition="테스트 계정이 준비되어 있다",
        steps=(
            StepInput(
                precondition="테스트 계정이 로그인되어 있다",
                action="기능을 실행한다",
                expected_result="화면이 표시된다",
            ),
            StepInput(
                precondition="외부 sandbox가 응답한다",
                action="요청을 제출한다",
                expected_result="정상 완료된다",
            ),
        ),
        assignee_label=owner,
        tag_names=labels,
    )


def _library(
    sessions: sessionmaker[Session],
) -> tuple[uuid.UUID, uuid.UUID, tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    with sessions.begin() as session:
        folders = FolderRepository(session)
        first_category = folders.create(
            name="회원",
            parent_id=None,
            sort_order=0,
            audit=_audit("category-members"),
        )
        second_category = folders.create(
            name="주문",
            parent_id=None,
            sort_order=1,
            audit=_audit("category-orders"),
        )
        cases = CaseRepository(session)
        first_work_item = cases.create_work_item(
            work_item_key="WORK-101",
            category_id=first_category.id,
            summary="회원 로그인 개선",
            owner_label="민지",
            labels=("account",),
            audit=_audit("work-members"),
        )
        second_work_item = cases.create_work_item(
            work_item_key="WORK-202",
            category_id=second_category.id,
            summary="주문 결제 개선",
            owner_label="준호",
            labels=("payment",),
            audit=_audit("work-orders"),
        )
        first_case = cases.create_case(
            work_item_id=first_work_item.id,
            content=_content("로그인 성공", owner="민지", labels=("regression", "account")),
            change_reason="initial",
            audit=_audit("case-login"),
        )
        second_case = cases.create_case(
            work_item_id=first_work_item.id,
            content=_content("로그인 실패", owner="준호", labels=("regression",)),
            change_reason="initial",
            audit=_audit("case-login-fail"),
        )
        third_case = cases.create_case(
            work_item_id=second_work_item.id,
            content=_content("결제 성공", owner="민지", labels=("regression", "payment")),
            change_reason="initial",
            audit=_audit("case-payment"),
        )
        return (
            first_work_item.id,
            second_work_item.id,
            (first_case.test_case.id, second_case.test_case.id, third_case.test_case.id),
        )


def _create_cycle(
    sessions: sessionmaker[Session],
    *,
    scope_type: CycleScopeType = CycleScopeType.WORK_ITEM,
    scope_value: str = "WORK-101",
) -> tuple[uuid.UUID, tuple[uuid.UUID, ...]]:
    with sessions.begin() as session:
        aggregate = CycleRepository(session).create(
            name="Sprint 3 QA",
            description="cycle ledger test",
            starts_at=date(2026, 8, 20),
            scope_type=scope_type,
            scope_value=scope_value,
            audit=_audit("cycle-create"),
        )
        return aggregate.cycle.id, tuple(item.id for item in aggregate.items)


def test_legacy_wip_migration_preserves_active_attempt_identity_and_audit(
    migrated_engine: Engine,
    alembic_config: Config,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _library(sessions)
    cycle_id, item_ids = _create_cycle(sessions)
    with sessions.begin() as session:
        CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("start-before-wip-migration"),
        )
        attempt = CycleRepository(session).start_attempt(
            cycle_item_id=item_ids[0],
            platform=ExecutionPlatform.WEB,
            executor_label="migration-test",
            audit=_audit("active-before-wip-migration"),
        )
        attempt_id = attempt.id

    command.downgrade(alembic_config, "20260824_0012")
    try:
        with migrated_engine.connect() as connection:
            legacy = connection.execute(
                text(
                    "SELECT id, cycle_item_id, platform, attempt_no, cycle_run_number, "
                    "status, executor_label, started_at, finished_at, created_at "
                    "FROM execution_attempts WHERE id = :attempt_id"
                ),
                {"attempt_id": attempt_id},
            ).mappings().one()
            legacy_audits = connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE entity_id = :attempt_id"),
                {"attempt_id": attempt_id},
            )
        assert legacy["status"] == "WIP"

        command.upgrade(alembic_config, "head")
        with migrated_engine.connect() as connection:
            migrated = connection.execute(
                text(
                    "SELECT id, cycle_item_id, platform, attempt_no, cycle_run_number, "
                    "status, executor_label, started_at, finished_at, created_at "
                    "FROM execution_attempts WHERE id = :attempt_id"
                ),
                {"attempt_id": attempt_id},
            ).mappings().one()
            migrated_audits = connection.scalar(
                text("SELECT count(*) FROM audit_logs WHERE entity_id = :attempt_id"),
                {"attempt_id": attempt_id},
            )
        assert migrated["status"] == "UNEXECUTED"
        assert {key: value for key, value in migrated.items() if key != "status"} == {
            key: value for key, value in legacy.items() if key != "status"
        }
        assert migrated_audits == legacy_audits == 1
    finally:
        command.upgrade(alembic_config, "head")


def test_scope_snapshot_diff_explicit_refresh_and_start_lock(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _, _, case_ids = _library(sessions)
    cycle_id, item_ids = _create_cycle(sessions)
    assert len(item_ids) == 2

    with sessions() as session:
        aggregate = CycleRepository(session).get_aggregate(cycle_id)
        pinned_by_case = {item.test_case_id: item.test_case_version_id for item in aggregate.items}

    with sessions.begin() as session:
        CaseRepository(session).update_case(
            test_case_id=case_ids[0],
            base_version=1,
            content=_content(
                "로그인 성공 - 최신",
                owner="민지",
                labels=("regression", "account"),
            ),
            change_reason="library content changed",
            audit=_audit("case-update"),
        )

    with sessions.begin() as session:
        repository = CycleRepository(session)
        item = next(
            item
            for item in repository.get_aggregate(cycle_id).items
            if item.test_case_id == case_ids[0]
        )
        version_diff = repository.get_item_snapshot_diff(item.id)
        assert version_diff.pinned_version == 1
        assert version_diff.current_version == 2
        assert version_diff.has_changes
        refreshed = repository.refresh_item_snapshot(
            cycle_item_id=item.id,
            base_resource_version=1,
            audit=_audit("explicit-refresh"),
        )
        assert refreshed.cycle.resource_version == 2
        refreshed_item = next(candidate for candidate in refreshed.items if candidate.id == item.id)
        assert refreshed_item.test_case_version_id != pinned_by_case[case_ids[0]]

    with sessions.begin() as session:
        started = CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=2,
            audit=_audit("cycle-start"),
        )
        assert started.cycle.status == CycleStatus.IN_PROGRESS.value
        assert started.cycle.resource_version == 3

    with pytest.raises(CycleStateError, match="PLANNED"):
        with sessions.begin() as session:
            CycleRepository(session).refresh_snapshot(
                cycle_id=cycle_id,
                base_resource_version=3,
                audit=_audit("late-refresh"),
            )

    with pytest.raises(DBAPIError, match="immutable after start"):
        with sessions.begin() as session:
            session.execute(
                update(CycleItem)
                .where(CycleItem.id == item_ids[1])
                .values(test_case_version_id=pinned_by_case[case_ids[0]])
            )


def test_transitions_attempt_validation_reopen_and_append_only_history(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _library(sessions)
    cycle_id, item_ids = _create_cycle(sessions)
    item_id = item_ids[0]

    with pytest.raises(ExecutionStateError, match="IN_PROGRESS"):
        with sessions.begin() as session:
            CycleRepository(session).start_attempt(
                cycle_item_id=item_id,
                platform=ExecutionPlatform.WEB,
                audit=_audit("too-early"),
            )

    with sessions.begin() as session:
        started = CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("start"),
        )
        assert started.cycle.status == CycleStatus.IN_PROGRESS.value

    with pytest.raises(InvalidCycleTransitionError):
        with sessions.begin() as session:
            CycleRepository(session).start(
                cycle_id=cycle_id,
                base_resource_version=2,
                audit=_audit("invalid-start"),
            )

    with sessions.begin() as session:
        first = CycleRepository(session).start_attempt(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            executor_label="민지",
            audit=_audit("web-attempt-one"),
        )
        first_id = first.id
        assert first.attempt_no == 1

    with pytest.raises(ActualResultRequiredError):
        with sessions.begin() as session:
            CycleRepository(session).complete_attempt(
                attempt_id=first_id,
                status=ExecutionStatus.FAIL,
                actual_result=" ",
                audit=_audit("invalid-fail"),
            )

    with sessions.begin() as session:
        failed = CycleRepository(session).complete_attempt(
            attempt_id=first_id,
            status=ExecutionStatus.FAIL,
            actual_result="HTTP 500",
            audit=_audit("failed"),
        )
        assert failed.status == ExecutionStatus.FAIL.value

    with pytest.raises(DBAPIError, match="immutable"):
        with sessions.begin() as session:
            session.execute(
                update(ExecutionAttempt)
                .where(ExecutionAttempt.id == first_id)
                .values(actual_result="tampered")
            )

    with sessions.begin() as session:
        repository = CycleRepository(session)
        rerun = repository.start_attempt(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            audit=_audit("rerun"),
        )
        assert rerun.attempt_no == 2
        rerun_id = rerun.id
    with sessions.begin() as session:
        CycleRepository(session).complete_attempt(
            attempt_id=rerun_id,
            status=ExecutionStatus.PASS,
            audit=_audit("rerun-pass"),
        )

    with sessions.begin() as session:
        blocked = CycleRepository(session).start_attempt(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.AOS,
            audit=_audit("aos-start"),
        )
        blocked_id = blocked.id
    with sessions.begin() as session:
        CycleRepository(session).complete_attempt(
            attempt_id=blocked_id,
            status=ExecutionStatus.BLOCKED,
            note="",
            audit=_audit("blocked"),
        )

    with sessions.begin() as session:
        completed = CycleRepository(session).complete(
            cycle_id=cycle_id,
            base_resource_version=2,
            audit=_audit("complete"),
        )
        assert completed.cycle.status == CycleStatus.COMPLETED.value
        assert completed.warnings

    with pytest.raises(ExecutionStateError, match="IN_PROGRESS"):
        with sessions.begin() as session:
            CycleRepository(session).start_attempt(
                cycle_item_id=item_id,
                platform=ExecutionPlatform.IOS,
                audit=_audit("after-complete"),
            )

    with sessions.begin() as session:
        reopened = CycleRepository(session).reopen(
            cycle_id=cycle_id,
            base_resource_version=3,
            audit=_audit("reopen"),
        )
        assert reopened.cycle.run_number == 2
        assert reopened.cycle.status == CycleStatus.IN_PROGRESS.value
        new_attempt = CycleRepository(session).start_attempt(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            audit=_audit("post-reopen-rerun"),
        )
        assert (new_attempt.attempt_no, new_attempt.cycle_run_number) == (3, 2)

    with sessions() as session:
        attempts = CycleRepository(session).list_attempts(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
        )
        assert [(attempt.attempt_no, attempt.status) for attempt in attempts] == [
            (1, ExecutionStatus.FAIL.value),
            (2, ExecutionStatus.PASS.value),
            (3, ExecutionStatus.UNEXECUTED.value),
        ]
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == cycle_id, AuditLog.action == "REOPEN")
            )
            == 1
        )


def test_step_results_are_independent_per_platform_and_reruns_append_attempts(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _library(sessions)
    cycle_id, item_ids = _create_cycle(sessions)
    item_id = item_ids[0]
    with sessions.begin() as session:
        CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("start-platform-step-matrix"),
        )

    with sessions.begin() as session:
        partial = CycleRepository(session).record_step_results(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            results=(StepResultInput(sequence=1, status="PASS"),),
            audit=_audit("web-step-one"),
        )
        assert partial.status == ExecutionStatus.UNEXECUTED.value
    with sessions.begin() as session:
        web = CycleRepository(session).record_step_results(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            results=(StepResultInput(sequence=2, status="PASS"),),
            audit=_audit("web-step-two"),
        )
        assert web.status == ExecutionStatus.PASS.value
    with sessions.begin() as session:
        aos = CycleRepository(session).record_step_results(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.AOS,
            results=(
                StepResultInput(sequence=1, status="PASS"),
                StepResultInput(sequence=2, status="FAIL", note="AOS failure"),
            ),
            audit=_audit("aos-steps"),
        )
        assert aos.status == ExecutionStatus.FAIL.value
    with sessions.begin() as session:
        ios = CycleRepository(session).record_step_results(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.IOS,
            results=(
                StepResultInput(sequence=1, status="BLOCKED"),
                StepResultInput(sequence=2, status="PASS"),
            ),
            audit=_audit("ios-steps"),
        )
        assert ios.status == ExecutionStatus.BLOCKED.value
    with sessions.begin() as session:
        rerun = CycleRepository(session).start_attempt(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            audit=_audit("web-rerun"),
        )
        assert rerun.attempt_no == 2
    with sessions.begin() as session:
        rerun = CycleRepository(session).record_step_results(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            results=(
                StepResultInput(sequence=1, status="PASS"),
                StepResultInput(sequence=2, status="FAIL", note="rerun regression"),
            ),
            audit=_audit("web-rerun-steps"),
        )
        assert rerun.status == ExecutionStatus.FAIL.value

    with sessions() as session:
        attempts = CycleRepository(session).list_attempts(cycle_item_id=item_id)
        assert [(row.platform, row.attempt_no, row.status) for row in attempts] == [
            ("AOS", 1, "FAIL"),
            ("IOS", 1, "BLOCKED"),
            ("WEB", 1, "PASS"),
            ("WEB", 2, "FAIL"),
        ]
        assert session.scalar(select(func.count()).select_from(ExecutionStepResult)) == 8


def test_concurrent_platform_attempt_start_has_one_winner(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _library(sessions)
    cycle_id, item_ids = _create_cycle(sessions)
    with sessions.begin() as session:
        CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("start-concurrent"),
        )

    barrier = Barrier(2)

    def start_once(label: str) -> str:
        barrier.wait()
        try:
            with sessions.begin() as session:
                CycleRepository(session).start_attempt(
                    cycle_item_id=item_ids[0],
                    platform=ExecutionPlatform.IOS,
                    executor_label=label,
                    audit=_audit(f"concurrent-{label}"),
                )
            return "started"
        except ExecutionStateError:
            return "active"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(start_once, ("a", "b")))

    assert sorted(outcomes) == ["active", "started"]
    with sessions() as session:
        attempts = CycleRepository(session).list_attempts(
            cycle_item_id=item_ids[0],
            platform=ExecutionPlatform.IOS,
        )
        assert len(attempts) == 1
        assert attempts[0].attempt_no == 1


def test_progress_uses_latest_attempt_across_all_dimensions(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _library(sessions)
    cycle_id, item_ids = _create_cycle(
        sessions,
        scope_type=CycleScopeType.LABEL,
        scope_value="REGRESSION",
    )
    assert len(item_ids) == 3

    with sessions() as session:
        planned = CycleRepository(session).get_progress(cycle_id)
        assert planned.overall.total_count == 9
        assert planned.overall.success_rate is None

    with sessions.begin() as session:
        CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("progress-start"),
        )

    def record(
        item_id: uuid.UUID,
        platform: ExecutionPlatform,
        status: ExecutionStatus | None,
        label: str,
    ) -> None:
        with sessions.begin() as session:
            attempt = CycleRepository(session).start_attempt(
                cycle_item_id=item_id,
                platform=platform,
                audit=_audit(f"{label}-start"),
            )
            attempt_id = attempt.id
        if status is not None:
            with sessions.begin() as session:
                CycleRepository(session).complete_attempt(
                    attempt_id=attempt_id,
                    status=status,
                    actual_result="unexpected result" if status is ExecutionStatus.FAIL else None,
                    note="blocked by environment" if status is ExecutionStatus.BLOCKED else None,
                    audit=_audit(f"{label}-complete"),
                )

    record(item_ids[0], ExecutionPlatform.WEB, ExecutionStatus.FAIL, "first-web-fail")
    record(item_ids[0], ExecutionPlatform.WEB, ExecutionStatus.PASS, "first-web-pass")
    record(item_ids[0], ExecutionPlatform.AOS, ExecutionStatus.BLOCKED, "first-aos")
    record(item_ids[1], ExecutionPlatform.WEB, ExecutionStatus.PASS, "second-web")
    record(item_ids[1], ExecutionPlatform.AOS, None, "second-aos-active")
    record(item_ids[2], ExecutionPlatform.IOS, ExecutionStatus.FAIL, "third-ios")

    with sessions() as session:
        progress = CycleRepository(session).get_progress(cycle_id)

    assert progress.overall.total_count == 9
    assert progress.overall.pass_count == 2
    assert progress.overall.fail_count == 1
    assert progress.overall.blocked_count == 1
    assert progress.overall.unexecuted_count == 5
    assert progress.overall.success_rate == pytest.approx(2 / 3)
    assert {bucket.key for bucket in progress.platforms} == {"WEB", "AOS", "IOS"}
    web = next(bucket for bucket in progress.platforms if bucket.key == "WEB")
    assert (web.total_count, web.pass_count, web.fail_count) == (3, 2, 0)
    assert {bucket.key for bucket in progress.owners} == {"민지", "준호"}
    assert {bucket.key for bucket in progress.work_items} == {"WORK-101", "WORK-202"}
    assert {"regression", "account", "payment"}.issubset({bucket.key for bucket in progress.labels})


def test_multi_label_union_and_explicit_start_before_step_results(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _library(sessions)
    with sessions.begin() as session:
        aggregate = CycleRepository(session).create(
            name="union cycle",
            starts_at=date(2026, 8, 21),
            scope_type=CycleScopeType.LABEL,
            scope_value="account",
            scope_values=("account", "payment"),
            audit=_audit("multi-label-create"),
        )
        cycle_id = aggregate.cycle.id
        item_id = aggregate.items[0].id
        assert aggregate.cycle.scope_values == ["account", "payment"]
        assert len(aggregate.items) == 2

    with pytest.raises(ExecutionStateError, match="IN_PROGRESS"):
        with sessions.begin() as session:
            CycleRepository(session).record_step_results(
                cycle_item_id=item_id,
                platform=ExecutionPlatform.WEB,
                results=(StepResultInput(sequence=1, status="PASS", note="ready"),),
                executor_label="민지",
                audit=_audit("reject-planned-step-result"),
            )

    with sessions.begin() as session:
        CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("explicit-start"),
        )
        attempt = CycleRepository(session).record_step_results(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            results=(StepResultInput(sequence=1, status="PASS", note="ready"),),
            executor_label="민지",
            audit=_audit("first-step-after-start"),
        )
        attempt_id = attempt.id
        assert attempt.status == ExecutionStatus.UNEXECUTED.value
        cycle = session.get(TestCycle, cycle_id)
        assert cycle is not None
        assert cycle.status == CycleStatus.IN_PROGRESS.value
        assert cycle.resource_version == 2

    with sessions.begin() as session:
        attempt = CycleRepository(session).record_step_results(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            results=(
                StepResultInput(sequence=1, status="FAIL", note="HTTP 500"),
                StepResultInput(sequence=2, status="BLOCKED", note="sandbox down"),
            ),
            audit=_audit("finish-derived-attempt"),
        )
        assert attempt.id == attempt_id
        assert attempt.status == ExecutionStatus.FAIL.value
        assert attempt.actual_result == "Step 1: HTTP 500"
        results = CycleRepository(session).get_step_results(attempt_id)
        assert [(row.sequence, row.status, row.note) for row in results] == [
            (1, "FAIL", "HTTP 500"),
            (2, "BLOCKED", "sandbox down"),
        ]

    with pytest.raises(DBAPIError, match="immutable after attempt completion"):
        with sessions.begin() as session:
            session.execute(
                update(ExecutionStepResult)
                .where(ExecutionStepResult.execution_attempt_id == attempt_id)
                .values(note="tampered")
            )


def test_cycle_resource_version_conflict_and_database_duplicate_guards(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _library(sessions)
    cycle_id, item_ids = _create_cycle(sessions)

    with sessions.begin() as session:
        CycleRepository(session).update(
            cycle_id=cycle_id,
            base_resource_version=1,
            name="renamed cycle",
            audit=_audit("rename-cycle"),
        )
    with pytest.raises(VersionConflictError):
        with sessions.begin() as session:
            CycleRepository(session).update(
                cycle_id=cycle_id,
                base_resource_version=1,
                description="stale",
                audit=_audit("stale-cycle"),
            )

    with sessions() as session:
        existing = session.get(CycleItem, item_ids[0])
        assert existing is not None
        duplicate_values = {
            "id": uuid.uuid4(),
            "cycle_id": existing.cycle_id,
            "test_case_id": existing.test_case_id,
            "test_case_version_id": existing.test_case_version_id,
            "work_item_id": existing.work_item_id,
            "assignee_label": existing.assignee_label,
        }
    with pytest.raises(DBAPIError, match="uq_cycle_items_cycle_case"):
        with sessions.begin() as session:
            session.add(CycleItem(**duplicate_values))

    with sessions.begin() as session:
        repository = CycleRepository(session)
        repository.start(
            cycle_id=cycle_id,
            base_resource_version=2,
            audit=_audit("start-duplicate-attempt"),
        )
        first_attempt = repository.start_attempt(
            cycle_item_id=item_ids[0],
            platform=ExecutionPlatform.WEB,
            audit=_audit("first-attempt-for-duplicate-guard"),
        )
        duplicate_attempt_values = {
            "cycle_item_id": first_attempt.cycle_item_id,
            "platform": first_attempt.platform,
            "attempt_no": first_attempt.attempt_no,
            "cycle_run_number": first_attempt.cycle_run_number,
            "status": ExecutionStatus.UNEXECUTED.value,
        }
    with pytest.raises(DBAPIError, match="uq_execution_attempts_item_platform_number"):
        with sessions.begin() as session:
            session.add(ExecutionAttempt(**duplicate_attempt_values))
