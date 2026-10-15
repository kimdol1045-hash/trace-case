from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from sqlalchemy import Engine, event, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.attachments import (
    AttachmentMetadata,
    AttachmentRepository,
)
from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import CaseRepository
from tc_backend.db.cases import TestCaseContent as CaseContent
from tc_backend.db.cases import TestStepInput as StepInput
from tc_backend.db.cycles import CycleRepository
from tc_backend.db.errors import IssueLinkCycleError, IssueLinkError, VersionConflictError
from tc_backend.db.folders import FolderRepository
from tc_backend.db.issues import IssueRepository
from tc_backend.db.models import (
    AttachmentDeletionStatus,
    AttachmentOwnerType,
    AttachmentReconcileStatus,
    AuditLog,
    AuditSourceType,
    Comment,
    ExecutionAttempt,
    ExecutionPlatform,
    ExecutionStatus,
    IntegrationProvider,
    Issue,
    IssueLink,
    IssueSeverity,
    IssueStatus,
    OutboxEvent,
    OutboxStatus,
)
from tc_backend.db.reports import ReportRepository

pytestmark = pytest.mark.postgres


def _audit(label: str) -> AuditContext:
    return AuditContext(
        source_type=AuditSourceType.API,
        actor_label="sprint4-repository-test",
        reason=label,
        trace_id=f"trace_{label}",
    )


def _execution_fixture(
    sessions: sessionmaker[Session],
    *,
    result: ExecutionStatus = ExecutionStatus.FAIL,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    with sessions.begin() as session:
        category = FolderRepository(session).create(
            name="결제",
            parent_id=None,
            sort_order=0,
            audit=_audit("category"),
        )
        cases = CaseRepository(session)
        work_item = cases.create_work_item(
            work_item_key="WORK-401",
            category_id=category.id,
            summary="결제 안정화",
            audit=_audit("work-item"),
        )
        case = cases.create_case(
            work_item_id=work_item.id,
            content=CaseContent(
                summary="결제 승인",
                precondition="테스트 카드가 등록되어 있다",
                steps=(
                    StepInput(action="결제 버튼을 누른다", expected_result="승인된다"),
                    StepInput(action="주문을 확인한다", expected_result="주문이 보인다"),
                ),
                assignee_label="민지",
            ),
            change_reason="initial",
            audit=_audit("case"),
        )
        cycle = CycleRepository(session).create(
            name="Sprint 4 QA",
            starts_at=date(2026, 8, 20),
            scope_type="WORK_ITEM",
            scope_value="WORK-401",
            audit=_audit("cycle"),
        )
        cycle_id = cycle.cycle.id
        item_id = cycle.items[0].id
        version_id = cycle.items[0].test_case_version_id
        case_id = case.test_case.id

    with sessions.begin() as session:
        CycleRepository(session).start(
            cycle_id=cycle_id,
            base_resource_version=1,
            audit=_audit("cycle-start"),
        )
    with sessions.begin() as session:
        attempt = CycleRepository(session).start_attempt(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            audit=_audit("attempt-start"),
        )
        attempt_id = attempt.id
    with sessions.begin() as session:
        CycleRepository(session).complete_attempt(
            attempt_id=attempt_id,
            status=result,
            actual_result="승인 API가 HTTP 500을 반환했다" if result is ExecutionStatus.FAIL else None,
            note="payment service unavailable" if result is ExecutionStatus.BLOCKED else "trace-401",
            audit=_audit("attempt-complete"),
        )
    return cycle_id, item_id, case_id, version_id


def _latest_attempt_id(sessions: sessionmaker[Session], item_id: uuid.UUID) -> uuid.UUID:
    with sessions() as session:
        return session.execute(
            select(ExecutionAttempt.id).where(ExecutionAttempt.cycle_item_id == item_id)
        ).scalar_one()


@pytest.mark.parametrize(
    "result",
    (ExecutionStatus.FAIL, ExecutionStatus.BLOCKED),
)
def test_issue_from_attempt_copies_immutable_snapshot_link_audit_and_outbox(
    migrated_engine: Engine,
    result: ExecutionStatus,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _, item_id, case_id, version_id = _execution_fixture(sessions, result=result)
    attempt_id = _latest_attempt_id(sessions, item_id)

    with sessions.begin() as session:
        created = IssueRepository(session).create_from_attempt(
            attempt_id=attempt_id,
            severity=IssueSeverity.CRITICAL,
            audit=_audit("issue-from-attempt"),
        )
        issue_id = created.issue.id
        assert created.issue.issue_key == "ISSUE-000001"
        assert created.issue.summary == "결제 승인"
        assert created.issue.precondition == "테스트 카드가 등록되어 있다"
        assert created.issue.expected_result == "1. 승인된다\n2. 주문이 보인다"
        expected_actual = (
            "승인 API가 HTTP 500을 반환했다"
            if result is ExecutionStatus.FAIL
            else "payment service unavailable"
        )
        expected_note = (
            "trace-401"
            if result is ExecutionStatus.FAIL
            else "payment service unavailable"
        )
        assert created.issue.actual_result == expected_actual
        assert created.issue.source_note == expected_note
        assert [step.action for step in created.steps] == ["결제 버튼을 누른다", "주문을 확인한다"]
        assert created.links[0].test_case_id == case_id
        assert created.links[0].test_case_version_id == version_id
        assert created.links[0].cycle_item_id == item_id
        assert created.links[0].execution_attempt_id == attempt_id

    with sessions() as session:
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.entity_id == issue_id,
                AuditLog.action == "CREATE_FROM_ATTEMPT",
            )
        )
        outbox = session.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == issue_id)
        )
        assert audit is not None and audit.after is not None
        assert outbox is not None
        assert outbox.status == OutboxStatus.READY.value
        assert outbox.event_type == "ISSUE_CREATED_FROM_ATTEMPT"
        assert outbox.payload["sourceTestCaseVersionId"] == str(version_id)
        assert outbox.payload["sourceStatus"] == result.value

    with sessions.begin() as session:
        aggregate = CaseRepository(session).get_aggregate(case_id)
        CaseRepository(session).update_case(
            test_case_id=case_id,
            base_version=aggregate.test_case.version,
            content=CaseContent(
                summary="변경된 원본 케이스",
                precondition="변경된 사전조건",
                steps=(StepInput(action="다른 단계", expected_result="다른 결과"),),
            ),
            change_reason="prove issue snapshot isolation",
            audit=_audit("case-update"),
        )

    with sessions() as session:
        snapshot = IssueRepository(session).get_aggregate(issue_id)
        assert snapshot.issue.summary == "결제 승인"
        assert snapshot.steps[0].action == "결제 버튼을 누른다"

    with sessions.begin() as session:
        rerun = CycleRepository(session).start_attempt(
            cycle_item_id=item_id,
            platform=ExecutionPlatform.WEB,
            audit=_audit("rerun-after-linked-issue"),
        )
        rerun_id = rerun.id
    with sessions.begin() as session:
        CycleRepository(session).complete_attempt(
            attempt_id=rerun_id,
            status=ExecutionStatus.PASS,
            audit=_audit("rerun-passed"),
        )
    with sessions() as session:
        preserved = session.scalar(
            select(IssueLink).where(IssueLink.issue_id == issue_id)
        )
        assert preserved is not None
        assert preserved.execution_attempt_id == attempt_id
        assert session.scalar(
            select(func.count())
            .select_from(ExecutionAttempt)
            .where(ExecutionAttempt.cycle_item_id == item_id)
        ) == 2


def test_issue_from_attempt_rolls_back_issue_link_audit_outbox_and_key(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _, item_id, _, _ = _execution_fixture(sessions)
    attempt_id = _latest_attempt_id(sessions, item_id)
    with sessions() as session:
        baseline_outbox_count = (
            session.scalar(select(func.count()).select_from(OutboxEvent)) or 0
        )

    with pytest.raises(RuntimeError, match="inject rollback"):
        with sessions.begin() as session:
            IssueRepository(session).create_from_attempt(
                attempt_id=attempt_id,
                audit=_audit("rolled-back-issue"),
            )
            raise RuntimeError("inject rollback")

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Issue)) == 0
        assert session.scalar(select(func.count()).select_from(IssueLink)) == 0
        assert (
            session.scalar(select(func.count()).select_from(OutboxEvent))
            == baseline_outbox_count
        )
        assert session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.entity_type == "ISSUE")
        ) == 0

    with sessions.begin() as session:
        committed = IssueRepository(session).create_from_attempt(
            attempt_id=attempt_id,
            audit=_audit("committed-after-rollback"),
        )
        assert committed.issue.issue_key == "ISSUE-000001"


def test_issue_transition_concurrency_comments_and_cross_cycle_link_guard(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _, item_id, _, _ = _execution_fixture(sessions)
    attempt_id = _latest_attempt_id(sessions, item_id)
    with sessions.begin() as session:
        issue = IssueRepository(session).create_from_attempt(
            attempt_id=attempt_id,
            audit=_audit("issue"),
        ).issue
        issue_id = issue.id
        IssueRepository(session).add_comment(
            issue_id=issue_id,
            body="재현을 확인했습니다",
            author_label="준호",
            audit=_audit("comment"),
        )

    barrier = Barrier(2)

    def transition(label: str) -> str:
        try:
            with sessions.begin() as session:
                barrier.wait()
                IssueRepository(session).transition_status(
                    issue_id=issue_id,
                    status=IssueStatus.IN_PROGRESS,
                    base_resource_version=1,
                    audit=_audit(label),
                )
            return "updated"
        except VersionConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(transition, ("concurrent-a", "concurrent-b")))
    assert outcomes == ["conflict", "updated"]

    with pytest.raises(DBAPIError, match="append-only"):
        with sessions.begin() as session:
            comment_id = session.scalar(select(Comment.id).where(Comment.owner_id == issue_id))
            session.execute(update(Comment).where(Comment.id == comment_id).values(body="tampered"))

    with sessions.begin() as session:
        second_cycle = CycleRepository(session).create(
            name="다른 사이클",
            starts_at=date(2026, 8, 21),
            scope_type="WORK_ITEM",
            scope_value="WORK-401",
            audit=_audit("second-cycle"),
        )
        second_item_id = second_cycle.items[0].id
    with pytest.raises(IssueLinkCycleError):
        with sessions.begin() as session:
            IssueRepository(session).add_link(
                issue_id=issue_id,
                cycle_item_id=second_item_id,
                audit=_audit("cross-cycle"),
            )
    with pytest.raises(IssueLinkError, match="already exists"):
        with sessions.begin() as session:
            IssueRepository(session).add_link(
                issue_id=issue_id,
                cycle_item_id=item_id,
                execution_attempt_id=attempt_id,
                audit=_audit("duplicate"),
            )


def test_attachment_r2_soft_delete_and_reconciliation(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _, item_id, _, _ = _execution_fixture(sessions)
    attempt_id = _latest_attempt_id(sessions, item_id)
    with sessions.begin() as session:
        issue_id = IssueRepository(session).create_from_attempt(
            attempt_id=attempt_id,
            audit=_audit("issue-for-attachment"),
        ).issue.id
        repository = AttachmentRepository(session)
        attachment = repository.create(
            owner_type=AttachmentOwnerType.ISSUE,
            owner_id=issue_id,
            metadata=AttachmentMetadata(
                drive_file_id=None,
                object_key="quarantine/attachments/issue/error.png",
                logical_path="2026/ISSUE-000001/error.png",
                file_name="error.png",
                checksum="a" * 64,
                mime_type="image/png",
                size_bytes=1024,
                storage_provider=IntegrationProvider.R2,
            ),
            audit=_audit("attachment"),
        )
        attachment_id = attachment.id

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            AttachmentRepository(session).create(
                owner_type=AttachmentOwnerType.EXECUTION_ATTEMPT,
                owner_id=attempt_id,
                metadata=AttachmentMetadata(
                    drive_file_id=None,
                    object_key="quarantine/attachments/issue/error.png",
                    logical_path="2026/cycle/error.png",
                    file_name="duplicate.png",
                    checksum="b" * 64,
                    mime_type="image/png",
                    size_bytes=1,
                    storage_provider=IntegrationProvider.R2,
                ),
                audit=_audit("duplicate-object-key"),
            )

    with sessions.begin() as session:
        repository = AttachmentRepository(session)
        deleted = repository.soft_delete(
            attachment_id=attachment_id,
            base_resource_version=1,
            audit=_audit("delete-request"),
        )
        assert deleted.deletion_status == AttachmentDeletionStatus.PENDING.value
        failed = repository.mark_deletion_result(
            attachment_id=attachment_id,
            base_resource_version=2,
            succeeded=False,
            detail="R2 503",
            audit=_audit("delete-failed"),
        )
        assert failed.reconcile_status == AttachmentReconcileStatus.DELETE_RETRY.value


def test_cycle_platform_issue_severity_report_and_csv_share_source(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    cycle_id, item_id, _, _ = _execution_fixture(sessions)
    attempt_id = _latest_attempt_id(sessions, item_id)
    with sessions.begin() as session:
        IssueRepository(session).create_from_attempt(
            attempt_id=attempt_id,
            severity=IssueSeverity.CRITICAL,
            audit=_audit("report-issue"),
        )
    with sessions() as session:
        repository = ReportRepository(session)
        source_rows = repository.source_rows(cycle_id=cycle_id)
        report = repository.cycle_report(cycle_id)
        csv_text = repository.to_csv(cycle_id=cycle_id)

        assert len(source_rows) == 3
        assert report.overall.total == 3
        assert report.overall.failed == 1
        assert report.overall.unexecuted == 2
        assert next(
            row for row in source_rows if row.platform is ExecutionPlatform.WEB
        ).failed == 1
        assert dict((row.severity, row.count) for row in report.issue_severities)[
            IssueSeverity.CRITICAL
        ] == 1
        for row in source_rows:
            assert ",".join(
                str(row.csv_values()[column])
                for column in (
                    "cycle_id",
                    "cycle_name",
                    "cycle_status",
                    "platform",
                    "total",
                )
            ) in csv_text
        assert session.scalar(select(func.count()).select_from(Issue)) == 1


def test_report_source_uses_a_fixed_four_query_plan(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _execution_fixture(sessions)
    statements: list[str] = []

    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(migrated_engine, "before_cursor_execute", capture)
    try:
        with sessions() as session:
            rows = ReportRepository(session).source_rows()
    finally:
        event.remove(migrated_engine, "before_cursor_execute", capture)

    assert len(rows) == 3
    assert len(statements) == 4
