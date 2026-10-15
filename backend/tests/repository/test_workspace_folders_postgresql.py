from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.audit import AuditContext
from tc_backend.db.constants import AI_INBOX_FOLDER_ID, DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import (
    FolderAliasTargetDeletedError,
    FolderContentWarning,
    FolderCycleError,
    FolderNameConflictError,
    FolderRestoreConflictError,
    SystemFolderProtectedError,
)
from tc_backend.db.folders import FolderRepository
from tc_backend.db.models import (
    AuditLog,
    AuditSourceType,
    Folder,
    FolderAlias,
    KeyEntityType,
    WorkspaceKeyCounter,
    WorkspaceSettings,
)
from tc_backend.db.workspaces import WorkspacePatch, WorkspaceRepository, ensure_default_workspace

pytestmark = pytest.mark.postgres


def _audit(label: str) -> AuditContext:
    return AuditContext(
        source_type=AuditSourceType.API,
        actor_label="repository-test",
        trace_id=f"trace_{label}",
    )


def _create_three_depth_tree(sessions: sessionmaker[Session]) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    with sessions.begin() as session:
        repository = FolderRepository(session)
        root = repository.create(
            name="회원",
            parent_id=None,
            sort_order=0,
            audit=_audit("create-members"),
        )
        child = repository.create(
            name="계정관리",
            parent_id=root.id,
            sort_order=0,
            audit=_audit("create-accounts"),
        )
        grandchild = repository.create(
            name="비밀번호",
            parent_id=child.id,
            sort_order=0,
            audit=_audit("create-password"),
        )
        return root.id, child.id, grandchild.id


def test_single_workspace_update_has_before_after_audit_and_append_only_guard(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        settings = WorkspaceRepository(session).update_settings(
            base_version=1,
            patch=WorkspacePatch(team_name="Platform QA", case_prefix="CASE"),
            audit=_audit("workspace-update"),
        )
        assert settings.version == 2

    with sessions() as session:
        rows = session.scalars(select(WorkspaceSettings)).all()
        assert len(rows) == 1
        audit = session.scalars(
            select(AuditLog).where(AuditLog.entity_type == "WORKSPACE_SETTINGS")
        ).one()
        assert audit.before is not None and audit.before["casePrefix"] == "TC"
        assert audit.after is not None and audit.after["casePrefix"] == "CASE"

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.add(
                WorkspaceSettings(
                    id=uuid.uuid4(),
                    singleton_key=1,
                    team_name="Other",
                    case_prefix="TC",
                    issue_prefix="ISSUE",
                    default_environment="test",
                    generation_quiet_minutes=10,
                    version=1,
                )
            )
            session.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        with sessions.begin() as session:
            session.execute(
                update(AuditLog)
                .where(AuditLog.entity_type == "WORKSPACE_SETTINGS")
                .values(action="TAMPER")
            )


def test_concurrent_workspace_key_allocation_is_unique_and_audited(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)

    def allocate(index: int) -> tuple[str, int]:
        with sessions.begin() as session:
            allocation = WorkspaceRepository(session).allocate_key(
                entity_type=KeyEntityType.TEST_CASE,
                entity_id=uuid.uuid4(),
                audit=_audit(f"key-{index}"),
            )
            return allocation.key, allocation.ordinal

    with ThreadPoolExecutor(max_workers=8) as executor:
        allocations = list(executor.map(allocate, range(24)))

    assert {key for key, _ in allocations} == {f"TC-{ordinal:06d}" for ordinal in range(1, 25)}
    assert {ordinal for _, ordinal in allocations} == set(range(1, 25))
    with sessions() as session:
        counter = session.get(
            WorkspaceKeyCounter,
            {"workspace_id": DEFAULT_WORKSPACE_ID, "entity_type": "TEST_CASE"},
        )
        assert counter is not None and counter.next_value == 25
        assert session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "ALLOCATE_KEY")
        ) == 24


def test_key_counter_and_audit_roll_back_with_the_caller_transaction(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)

    with pytest.raises(RuntimeError, match="force aggregate failure"):
        with sessions.begin() as session:
            WorkspaceRepository(session).allocate_key(
                entity_type=KeyEntityType.ISSUE,
                entity_id=uuid.uuid4(),
                audit=_audit("rolled-back-key"),
            )
            raise RuntimeError("force aggregate failure")

    with sessions() as session:
        counter = session.get(
            WorkspaceKeyCounter,
            {"workspace_id": DEFAULT_WORKSPACE_ID, "entity_type": "ISSUE"},
        )
        assert counter is not None and counter.next_value == 1
        assert session.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.trace_id == "trace_rolled-back-key"
            )
        ) == 0


def test_active_sibling_names_are_normalized_and_unique(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        repository = FolderRepository(session)
        parent = repository.create(
            name="회원",
            parent_id=None,
            sort_order=0,
            audit=_audit("parent"),
        )
        first = repository.create(
            name="로그인",
            parent_id=parent.id,
            sort_order=0,
            audit=_audit("login"),
        )
        with pytest.raises(FolderNameConflictError):
            repository.create(
                name="  로그인  ",
                parent_id=parent.id,
                sort_order=1,
                audit=_audit("duplicate-login"),
            )

    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.add(
                Folder(
                    id=uuid.uuid4(),
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    parent_id=parent.id,
                    name="LOGIN",
                    normalized_name=first.normalized_name,
                    sort_order=2,
                    version=1,
                )
            )
            session.flush()


def test_three_depth_tree_children_cycle_guard_and_count_ready_sql(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    root_id, child_id, grandchild_id = _create_three_depth_tree(sessions)

    with sessions.begin() as session:
        session.execute(
            text(
                "CREATE TEMP TABLE test_cases ("
                "id uuid PRIMARY KEY, category_id uuid NOT NULL, deleted_at timestamptz NULL"
                ") ON COMMIT DROP"
            )
        )
        session.execute(
            text(
                "INSERT INTO test_cases (id, category_id, deleted_at) VALUES "
                "(:one, :root, NULL), (:two, :child, NULL), "
                "(:three, :grandchild, NULL), (:deleted, :grandchild, now())"
            ),
            {
                "one": uuid.uuid4(),
                "two": uuid.uuid4(),
                "three": uuid.uuid4(),
                "deleted": uuid.uuid4(),
                "root": root_id,
                "child": child_id,
                "grandchild": grandchild_id,
            },
        )
        repository = FolderRepository(session)
        roots = repository.list_tree()
        members = next(root for root in roots if root.id == root_id)
        accounts = members.children[0]
        password = accounts.children[0]
        assert members.depth == 0
        assert accounts.depth == 1
        assert password.depth == 2
        assert [segment[0] for segment in password.path] == [root_id, child_id, grandchild_id]
        assert members.direct_test_case_count == 1
        assert members.subtree_test_case_count == 3
        assert accounts.subtree_test_case_count == 2
        assert password.direct_test_case_count == 1
        assert [child.id for child in repository.list_children(parent_id=child_id)] == [
            grandchild_id
        ]
        with pytest.raises(FolderCycleError):
            repository.move(
                folder_id=root_id,
                base_version=1,
                target_parent_id=grandchild_id,
                sort_order=0,
                audit=_audit("cycle"),
            )


def test_concurrent_moves_serialize_and_prevent_a_cycle(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        repository = FolderRepository(session)
        folder_a = repository.create(
            name="A",
            parent_id=None,
            sort_order=0,
            audit=_audit("create-a"),
        )
        folder_b = repository.create(
            name="B",
            parent_id=None,
            sort_order=1,
            audit=_audit("create-b"),
        )
        folder_c = repository.create(
            name="C",
            parent_id=folder_a.id,
            sort_order=0,
            audit=_audit("create-c"),
        )
        ids = folder_a.id, folder_b.id, folder_c.id

    barrier = Barrier(2)

    def move(source: uuid.UUID, target: uuid.UUID, label: str) -> str:
        barrier.wait()
        try:
            with sessions.begin() as session:
                FolderRepository(session).move(
                    folder_id=source,
                    base_version=1,
                    target_parent_id=target,
                    sort_order=0,
                    audit=_audit(label),
                )
            return "moved"
        except FolderCycleError:
            return "cycle"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            executor.submit(move, ids[0], ids[1], "a-under-b"),
            executor.submit(move, ids[1], ids[2], "b-under-c"),
        ]
        outcomes = [result.result(timeout=10) for result in results]

    assert sorted(outcomes) == ["cycle", "moved"]
    with sessions() as session:
        folders = session.scalars(
            select(Folder).where(Folder.id.in_(ids)).order_by(Folder.id)
        ).all()
        assert len(folders) == 3
        assert sum(folder.parent_id is None for folder in folders) == 1


def test_soft_delete_warns_then_restores_the_entire_subtree(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        repository = FolderRepository(session)
        root = repository.create(
            name="주문",
            parent_id=None,
            sort_order=0,
            audit=_audit("create-orders"),
        )
        child = repository.create(
            name="결제",
            parent_id=root.id,
            sort_order=0,
            audit=_audit("create-payments"),
        )
        ids = root.id, child.id

    with sessions.begin() as session:
        repository = FolderRepository(session)
        impact = repository.preview_soft_delete(folder_id=ids[0])
        assert impact.folder_count == 2
        assert impact.test_case_count == 0
        assert impact.requires_confirmation
        with pytest.raises(FolderContentWarning):
            repository.soft_delete(
                folder_id=ids[0],
                base_version=1,
                acknowledge_contents=False,
                audit=_audit("delete-warning"),
            )

    with sessions.begin() as session:
        deleted = FolderRepository(session).soft_delete(
            folder_id=ids[0],
            base_version=1,
            acknowledge_contents=True,
            audit=_audit("delete-orders"),
        )
        assert set(deleted.affected_folder_ids) == set(ids)
        assert deleted.root_version == 2
        assert deleted.deletion_batch_id is not None

    with sessions.begin() as session:
        repository = FolderRepository(session)
        assert all(root.id not in ids for root in repository.list_tree())
        with pytest.raises(FolderRestoreConflictError):
            repository.restore(
                folder_id=ids[0],
                base_version=2,
                deletion_batch_id=uuid.uuid4(),
                audit=_audit("restore-wrong-batch"),
            )
        restored = repository.restore(
            folder_id=ids[0],
            base_version=2,
            deletion_batch_id=deleted.deletion_batch_id,
            audit=_audit("restore-orders"),
        )
        assert set(restored.affected_folder_ids) == set(ids)
        assert restored.root_version == 3
        orders = next(root for root in repository.list_tree() if root.id == ids[0])
        assert [candidate.id for candidate in orders.children] == [ids[1]]


def test_seed_is_idempotent_system_folder_is_protected_and_alias_suspends(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        ensure_default_workspace(session)
        ensure_default_workspace(session)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(WorkspaceSettings)) == 1
        assert session.scalar(select(func.count()).select_from(WorkspaceKeyCounter)) == 2
        assert session.scalar(
            select(func.count()).select_from(Folder).where(Folder.id == AI_INBOX_FOLDER_ID)
        ) == 1

    with sessions.begin() as session:
        repository = FolderRepository(session)
        system_folder = session.get(Folder, AI_INBOX_FOLDER_ID)
        assert system_folder is not None
        with pytest.raises(SystemFolderProtectedError):
            repository.rename(
                folder_id=system_folder.id,
                base_version=system_folder.version,
                name="다른 이름",
                audit=_audit("rename-system"),
            )
        with pytest.raises(SystemFolderProtectedError):
            repository.soft_delete(
                folder_id=system_folder.id,
                base_version=system_folder.version,
                acknowledge_contents=True,
                audit=_audit("delete-system"),
            )

        target = repository.create(
            name="계정",
            parent_id=None,
            sort_order=0,
            audit=_audit("alias-target"),
        )
        alias = repository.upsert_alias(
            alias="마이페이지",
            folder_id=target.id,
            audit=_audit("alias"),
        )
        assert repository.resolve_alias("  마이페이지 ") == target
        repository.soft_delete(
            folder_id=target.id,
            base_version=1,
            acknowledge_contents=True,
            audit=_audit("delete-alias-target"),
        )
        with pytest.raises(FolderAliasTargetDeletedError):
            repository.resolve_alias("마이페이지")
        assert session.scalar(
            select(func.count()).select_from(FolderAlias).where(FolderAlias.id == alias.id)
        ) == 1
