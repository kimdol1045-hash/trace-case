from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.audit import AuditContext
from tc_backend.db.cases import (
    CaseRepository,
)
from tc_backend.db.cases import (
    TestCaseContent as CaseContent,
)
from tc_backend.db.cases import (
    TestStepInput as StepInput,
)
from tc_backend.db.errors import (
    CategoryInvariantError,
    VersionConflictError,
    WorkItemKeyConflictError,
)
from tc_backend.db.folders import FolderRepository
from tc_backend.db.models import (
    AuditLog,
    AuditSourceType,
    ExpectedResultMode,
    Tag,
    WorkItem,
)
from tc_backend.db.models import (
    TestCase as CaseRow,
)
from tc_backend.db.models import (
    TestCaseTag as CaseTagRow,
)
from tc_backend.db.models import (
    TestCaseVersion as CaseVersionRow,
)
from tc_backend.db.models import (
    TestCaseVersionTag as CaseVersionTagRow,
)
from tc_backend.db.models import (
    TestStep as StepRow,
)

pytestmark = pytest.mark.postgres


def _audit(label: str) -> AuditContext:
    return AuditContext(
        source_type=AuditSourceType.API,
        actor_label="case-repository-test",
        reason=label,
        trace_id=f"trace_{label}",
    )


def _per_step_content(summary: str, *tags: str) -> CaseContent:
    return CaseContent(
        summary=summary,
        precondition="테스트 계정이 준비되어 있다",
        steps=(
            StepInput(
                precondition="로그인 세션이 유효하다",
                action="화면을 연다",
                expected_result="화면이 표시된다",
            ),
            StepInput(
                precondition="필수 입력값이 채워져 있다",
                action="저장한다",
                expected_result="저장 완료가 표시된다",
            ),
        ),
        tag_names=tuple(tags),
    )


def _case_mode_content(summary: str, *tags: str) -> CaseContent:
    return CaseContent(
        summary=summary,
        precondition="생성 입력이 준비되어 있다",
        steps=(StepInput(action="Generation API를 호출한다"),),
        expected_result_mode=ExpectedResultMode.CASE,
        expected_result="유효한 테스트케이스가 생성된다",
        tag_names=tuple(tags),
    )


def _library(
    sessions: sessionmaker[Session],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
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
            labels=("Web", " web ", "인증"),
            audit=_audit("work-members"),
        )
        second_work_item = cases.create_work_item(
            work_item_key="WORK-202",
            category_id=second_category.id,
            summary="주문 결제 개선",
            audit=_audit("work-orders"),
        )
        return first_category.id, second_category.id, first_work_item.id, second_work_item.id


def test_category_projection_and_work_item_root_ownership(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    first_category_id, _, first_work_item_id, _ = _library(sessions)

    with sessions.begin() as session:
        child = FolderRepository(session).create(
            name="노출하지 않는 child",
            parent_id=first_category_id,
            sort_order=0,
            audit=_audit("child-folder"),
        )
        with pytest.raises(CategoryInvariantError):
            CaseRepository(session).create_work_item(
                work_item_key="WORK-BAD",
                category_id=child.id,
                summary="잘못된 ownership",
                audit=_audit("invalid-work-item"),
            )

    with sessions() as session:
        repository = CaseRepository(session)
        categories = repository.list_categories()
        members = next(category for category in categories if category.id == first_category_id)
        work_item = repository.get_work_item(first_work_item_id)
        assert members.work_item_count == 1
        assert members.active_test_case_count == 0
        assert work_item.labels == ["Web", "인증"]
        assert all(category.id != child.id for category in categories)

    with pytest.raises(DBAPIError, match="active root folder"):
        with sessions.begin() as session:
            session.add(
                WorkItem(
                    id=uuid.uuid4(),
                    workspace_id=work_item.workspace_id,
                    category_id=child.id,
                    work_item_key="WORK-DIRECT-BAD",
                    summary="direct invalid category",
                    labels=[],
                    version=1,
                )
            )


def test_work_item_update_uses_resource_version_and_appends_audit(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    first_category_id, second_category_id, first_work_item_id, _ = _library(sessions)

    with pytest.raises(WorkItemKeyConflictError):
        with sessions.begin() as session:
            CaseRepository(session).create_work_item(
                work_item_key="WORK-101",
                category_id=first_category_id,
                summary="Duplicate",
                audit=_audit("duplicate-work-key"),
            )

    with sessions.begin() as session:
        repository = CaseRepository(session)
        item = repository.get_work_item_by_key("WORK-101")
        updated = repository.update_work_item(
            work_item_key=item.work_item_key,
            base_version=1,
            category_id=second_category_id,
            summary="회원 로그인 안정화",
            description="PostgreSQL 원장에 유지",
            owner_label="QA Kim",
            labels=("auth", "regression"),
            audit=_audit("update-work-item"),
        )
        assert updated.id == first_work_item_id
        assert updated.version == 2
        assert updated.category_id == second_category_id
        assert [candidate.work_item_key for candidate in repository.list_work_items()] == [
            "WORK-101",
            "WORK-202",
        ]
        assert [
            candidate.work_item_key
            for candidate in repository.list_work_items(limit=1, offset=1)
        ] == ["WORK-202"]

    with sessions() as session:
        item = CaseRepository(session).get_work_item_by_key("WORK-101")
        assert item.description == "PostgreSQL 원장에 유지"
        assert item.owner_label == "QA Kim"
        assert item.labels == ["auth", "regression"]
        assert [
            action
            for action in session.scalars(
                select(AuditLog.action)
                .where(AuditLog.entity_type == "WORK_ITEM", AuditLog.entity_id == item.id)
                .order_by(AuditLog.occurred_at)
            )
        ] == ["CREATE", "UPDATE"]

    with pytest.raises(VersionConflictError):
        with sessions.begin() as session:
            CaseRepository(session).update_work_item(
                work_item_key="WORK-101",
                base_version=1,
                category_id=first_category_id,
                summary="stale",
                description="",
                owner_label=None,
                labels=(),
                audit=_audit("stale-work-item"),
            )

def test_per_step_and_case_modes_tags_and_deferred_shape_constraints(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _, _, work_item_id, _ = _library(sessions)

    with sessions.begin() as session:
        repository = CaseRepository(session)
        manual = repository.create_case(
            work_item_id=work_item_id,
            content=_per_step_content("수동 케이스", "Regression", " regression ", "회원"),
            change_reason="initial manual case",
            audit=_audit("create-manual"),
        )
        generated = repository.create_case(
            work_item_id=work_item_id,
            content=_case_mode_content("생성 케이스", "Generated"),
            change_reason="initial generated case",
            audit=_audit("create-generated"),
        )
        ids = manual.test_case.id, generated.test_case.id

    with sessions() as session:
        repository = CaseRepository(session)
        manual = repository.get_aggregate(ids[0])
        generated = repository.get_aggregate(ids[1])
        assert manual.test_case.case_key == "TC-000001"
        assert generated.test_case.case_key == "TC-000002"
        assert manual.current_version.expected_result_mode == "PER_STEP"
        assert all(step.expected_result for step in manual.current_version.steps)
        assert [step.precondition for step in manual.current_version.steps] == [
            "로그인 세션이 유효하다",
            "필수 입력값이 채워져 있다",
        ]
        assert set(manual.current_version.tag_names) == {"Regression", "회원"}
        assert generated.current_version.expected_result_mode == "CASE"
        assert generated.current_version.expected_result == "유효한 테스트케이스가 생성된다"
        assert generated.current_version.steps[0].expected_result is None
        assert session.scalar(select(func.count()).select_from(Tag)) == 3

    with pytest.raises(DBAPIError, match="does not match PER_STEP mode"):
        with sessions.begin() as session:
            current_version_id = session.get(CaseRow, ids[0]).current_version_id
            session.add(
                StepRow(
                    id=uuid.uuid4(),
                    test_case_version_id=current_version_id,
                    sequence=3,
                    action="invalid direct step",
                    expected_result=None,
                )
            )

    with pytest.raises(DBAPIError, match="requires at least one step"):
        with sessions.begin() as session:
            test_case = session.get(CaseRow, ids[1])
            assert test_case is not None
            new_version_id = uuid.uuid4()
            session.add(
                CaseVersionRow(
                    id=new_version_id,
                    test_case_id=test_case.id,
                    version_number=2,
                    status="ACTIVE",
                    summary="no steps",
                    precondition="still valid",
                    expected_result_mode="CASE",
                    expected_result="result",
                    priority="MEDIUM",
                    source_type="API",
                    source_metadata={},
                    change_reason="invalid direct version",
                )
            )
            tag_ids = session.scalars(
                select(CaseTagRow.tag_id).where(CaseTagRow.test_case_id == test_case.id)
            ).all()
            session.add_all(
                CaseVersionTagRow(test_case_version_id=new_version_id, tag_id=tag_id)
                for tag_id in tag_ids
            )
            test_case.current_version_id = new_version_id
            test_case.version += 1


def test_optimistic_update_diff_restore_and_immutable_history(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _, _, work_item_id, _ = _library(sessions)
    with sessions.begin() as session:
        created = CaseRepository(session).create_case(
            work_item_id=work_item_id,
            content=_per_step_content("원래 서머리", "smoke"),
            change_reason="initial",
            audit=_audit("create-versioned"),
        )
        case_id = created.test_case.id
        original_version_id = created.current_version.id

    with sessions.begin() as session:
        updated = CaseRepository(session).update_case(
            test_case_id=case_id,
            base_version=1,
            content=_case_mode_content("변경된 서머리", "regression"),
            change_reason="switch generated contract",
            audit=_audit("update-versioned"),
        )
        assert updated.test_case.version == 2
        assert updated.current_version.version_number == 2

    with pytest.raises(VersionConflictError) as conflict:
        with sessions.begin() as session:
            CaseRepository(session).update_case(
                test_case_id=case_id,
                base_version=1,
                content=_per_step_content("stale write"),
                change_reason="stale",
                audit=_audit("stale-versioned"),
            )
    assert conflict.value.actual == 2

    with sessions.begin() as session:
        repository = CaseRepository(session)
        diff = repository.diff_versions(test_case_id=case_id, from_version=1, to_version=2)
        assert {field.field for field in diff.fields} >= {
            "summary",
            "expectedResultMode",
            "expectedResult",
            "tags",
            "steps",
        }
        restored = repository.restore_version(
            test_case_id=case_id,
            base_version=2,
            restore_version=1,
            change_reason="restore known-good manual script",
            audit=_audit("restore-versioned"),
        )
        assert restored.test_case.version == 3
        assert restored.current_version.version_number == 3
        assert restored.current_version.restored_from_version_id == original_version_id
        assert restored.current_version.summary == "원래 서머리"

    with sessions() as session:
        versions = CaseRepository(session).list_versions(case_id)
        assert [version.version_number for version in versions] == [1, 2, 3]
        assert versions[0].summary == "원래 서머리"
        assert versions[1].summary == "변경된 서머리"
        assert versions[2].summary == "원래 서머리"

    with pytest.raises(DBAPIError, match="immutable"):
        with sessions.begin() as session:
            session.execute(
                update(CaseVersionRow)
                .where(CaseVersionRow.id == original_version_id)
                .values(summary="tampered")
            )


def test_concurrent_optimistic_updates_allow_one_new_version(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _, _, work_item_id, _ = _library(sessions)
    with sessions.begin() as session:
        case_id = (
            CaseRepository(session)
            .create_case(
                work_item_id=work_item_id,
                content=_per_step_content("concurrent base"),
                change_reason="initial",
                audit=_audit("concurrent-create"),
            )
            .test_case.id
        )

    barrier = Barrier(2)

    def update_once(label: str) -> str:
        barrier.wait()
        try:
            with sessions.begin() as session:
                CaseRepository(session).update_case(
                    test_case_id=case_id,
                    base_version=1,
                    content=_per_step_content(f"winner {label}"),
                    change_reason=f"candidate {label}",
                    audit=_audit(f"concurrent-{label}"),
                )
            return "updated"
        except VersionConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(update_once, ("a", "b")))

    assert sorted(outcomes) == ["conflict", "updated"]
    with sessions() as session:
        repository = CaseRepository(session)
        aggregate = repository.get_aggregate(case_id)
        assert aggregate.test_case.version == 2
        assert aggregate.current_version.version_number == 2
        assert len(repository.list_versions(case_id)) == 2


def test_move_reorder_soft_delete_and_restore_preserve_ownership_and_sort(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    first_category_id, second_category_id, first_work_item_id, second_work_item_id = _library(
        sessions
    )
    with sessions.begin() as session:
        repository = CaseRepository(session)
        first = repository.create_case(
            work_item_id=first_work_item_id,
            content=_per_step_content("첫째"),
            change_reason="first",
            audit=_audit("first"),
        )
        second = repository.create_case(
            work_item_id=first_work_item_id,
            content=_per_step_content("둘째"),
            change_reason="second",
            audit=_audit("second"),
        )
        third = repository.create_case(
            work_item_id=first_work_item_id,
            content=_per_step_content("셋째"),
            change_reason="third",
            audit=_audit("third"),
        )
        ids = first.test_case.id, second.test_case.id, third.test_case.id

    with sessions.begin() as session:
        moved_third = CaseRepository(session).move_case(
            test_case_id=ids[2],
            base_version=1,
            target_work_item_id=first_work_item_id,
            sort_order=0,
            audit=_audit("reorder-third"),
        )
        assert moved_third.test_case.version == 2

    with sessions.begin() as session:
        moved_first = CaseRepository(session).move_case(
            test_case_id=ids[0],
            base_version=1,
            target_work_item_id=second_work_item_id,
            sort_order=0,
            audit=_audit("move-first"),
        )
        assert moved_first.test_case.category_id == second_category_id
        assert moved_first.test_case.work_item_id == second_work_item_id

    with sessions.begin() as session:
        deleted = CaseRepository(session).soft_delete(
            test_case_id=ids[2],
            base_version=2,
            audit=_audit("delete-third"),
        )
        assert deleted.test_case.deleted_at is not None
        assert deleted.test_case.version == 3

    with sessions.begin() as session:
        restored = CaseRepository(session).restore_deleted(
            test_case_id=ids[2],
            base_version=3,
            target_work_item_id=second_work_item_id,
            sort_order=1,
            audit=_audit("restore-third"),
        )
        assert restored.test_case.deleted_at is None
        assert restored.test_case.category_id == second_category_id
        assert restored.test_case.version == 4

    with sessions() as session:
        first_order = session.scalars(
            select(CaseRow)
            .where(CaseRow.work_item_id == first_work_item_id, CaseRow.deleted_at.is_(None))
            .order_by(CaseRow.sort_order)
        ).all()
        second_order = session.scalars(
            select(CaseRow)
            .where(CaseRow.work_item_id == second_work_item_id, CaseRow.deleted_at.is_(None))
            .order_by(CaseRow.sort_order)
        ).all()
        assert [(case.id, case.sort_order) for case in first_order] == [(ids[1], 0)]
        assert [(case.id, case.sort_order) for case in second_order] == [
            (ids[0], 0),
            (ids[2], 1),
        ]
        assert all(case.category_id == second_category_id for case in second_order)
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.entity_type == "TEST_CASE",
                    AuditLog.action.in_(["REORDER", "MOVE", "SOFT_DELETE", "RESTORE"]),
                )
            )
            == 4
        )

    with pytest.raises(DBAPIError, match="fk_test_cases_work_item_ownership"):
        with sessions.begin() as session:
            session.execute(
                update(CaseRow).where(CaseRow.id == ids[0]).values(category_id=first_category_id)
            )

    with pytest.raises(DBAPIError, match="uq_test_cases_active_work_item_sort"):
        with sessions.begin() as session:
            session.execute(update(CaseRow).where(CaseRow.id == ids[2]).values(sort_order=0))


def test_copy_starts_an_independent_history_without_mixing_source_versions(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    _, _, first_work_item_id, second_work_item_id = _library(sessions)
    with sessions.begin() as session:
        source = CaseRepository(session).create_case(
            work_item_id=first_work_item_id,
            content=_per_step_content("source v1", "source"),
            change_reason="source initial",
            audit=_audit("copy-source"),
        )
        source_id = source.test_case.id

    with sessions.begin() as session:
        repository = CaseRepository(session)
        source_v2 = repository.update_case(
            test_case_id=source_id,
            base_version=1,
            content=_per_step_content("source v2", "source", "updated"),
            change_reason="source update",
            audit=_audit("copy-source-update"),
        )
        copied = repository.copy_case(
            source_test_case_id=source_id,
            source_version=2,
            target_work_item_id=second_work_item_id,
            change_reason="copy current script",
            audit=_audit("copy-target"),
        )
        copied_id = copied.test_case.id
        assert copied.test_case.case_key == "TC-000002"
        assert copied.current_version.version_number == 1
        assert copied.current_version.summary == "source v2"
        assert copied.current_version.copied_from_version_id == source_v2.current_version.id

    with sessions() as session:
        repository = CaseRepository(session)
        assert len(repository.list_versions(source_id)) == 2
        copied_versions = repository.list_versions(copied_id)
        assert len(copied_versions) == 1
        assert copied_versions[0].test_case_id == copied_id
        assert (
            session.scalar(
                select(func.count())
                .select_from(CaseVersionTagRow)
                .where(CaseVersionTagRow.test_case_version_id == copied_versions[0].id)
            )
            == 2
        )
