"""Adjacency-list folder queries and topology-safe mutations."""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import bindparam, func, select, text, update
from sqlalchemy.orm import Session

from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import (
    FolderAliasConflictError,
    FolderAliasTargetDeletedError,
    FolderContentWarning,
    FolderCycleError,
    FolderNameConflictError,
    FolderNotEmptyError,
    FolderParentDeletedError,
    FolderRestoreConflictError,
    ResourceNotFoundError,
    SystemFolderProtectedError,
    VersionConflictError,
)
from tc_backend.db.models import Folder, FolderAlias, WorkspaceSettings


@dataclass(frozen=True, slots=True)
class FolderDeleteImpact:
    folder_count: int
    test_case_count: int

    @property
    def requires_confirmation(self) -> bool:
        return self.folder_count > 1 or self.test_case_count > 0


@dataclass(frozen=True, slots=True)
class FolderMutationResult:
    root_folder_id: uuid.UUID
    affected_folder_ids: tuple[uuid.UUID, ...]
    root_version: int
    audit_id: uuid.UUID
    deletion_batch_id: uuid.UUID | None = None
    impact: FolderDeleteImpact | None = None


@dataclass(slots=True)
class FolderTreeNode:
    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    sort_order: int
    version: int
    system_kind: str | None
    deleted_at: datetime | None
    depth: int = 0
    path: tuple[tuple[uuid.UUID, str], ...] = ()
    direct_test_case_count: int = 0
    subtree_test_case_count: int = 0
    children: list[FolderTreeNode] = field(default_factory=list)


class FolderCaseCounter(Protocol):
    """Sprint 2 can provide counts without changing the folder API shape."""

    def count_direct(
        self,
        session: Session,
        folder_ids: Sequence[uuid.UUID],
    ) -> Mapping[uuid.UUID, int]: ...


class _UseCurrentParent:
    pass


USE_CURRENT_PARENT = _UseCurrentParent()

_ACTIVE_FOLDER_TREE_SQL = text(
    """
    WITH RECURSIVE folder_tree AS (
        SELECT
            folder.id,
            folder.parent_id,
            folder.name,
            folder.sort_order,
            folder.version,
            folder.system_kind,
            folder.deleted_at,
            ARRAY[folder.id]::uuid[] AS visited_ids
        FROM folders AS folder
        WHERE folder.workspace_id = :workspace_id
          AND folder.parent_id IS NULL
          AND folder.deleted_at IS NULL
        UNION ALL
        SELECT
            child.id,
            child.parent_id,
            child.name,
            child.sort_order,
            child.version,
            child.system_kind,
            child.deleted_at,
            parent.visited_ids || child.id
        FROM folders AS child
        JOIN folder_tree AS parent ON child.parent_id = parent.id
        WHERE child.workspace_id = :workspace_id
          AND child.deleted_at IS NULL
          AND NOT child.id = ANY(parent.visited_ids)
    )
    SELECT id, parent_id, name, sort_order, version, system_kind, deleted_at
    FROM folder_tree
    ORDER BY sort_order, name, id
    """
)

_ALL_FOLDER_TREE_SQL = text(
    """
    WITH RECURSIVE folder_tree AS (
        SELECT
            folder.id,
            folder.parent_id,
            folder.name,
            folder.sort_order,
            folder.version,
            folder.system_kind,
            folder.deleted_at,
            ARRAY[folder.id]::uuid[] AS visited_ids
        FROM folders AS folder
        WHERE folder.workspace_id = :workspace_id
          AND folder.parent_id IS NULL
        UNION ALL
        SELECT
            child.id,
            child.parent_id,
            child.name,
            child.sort_order,
            child.version,
            child.system_kind,
            child.deleted_at,
            parent.visited_ids || child.id
        FROM folders AS child
        JOIN folder_tree AS parent ON child.parent_id = parent.id
        WHERE child.workspace_id = :workspace_id
          AND NOT child.id = ANY(parent.visited_ids)
    )
    SELECT id, parent_id, name, sort_order, version, system_kind, deleted_at
    FROM folder_tree
    ORDER BY sort_order, name, id
    """
)

_ACTIVE_SUBTREE_SQL = text(
    """
    WITH RECURSIVE folder_subtree AS (
        SELECT folder.id, ARRAY[folder.id]::uuid[] AS visited_ids
        FROM folders AS folder
        WHERE folder.workspace_id = :workspace_id
          AND folder.id = :folder_id
          AND folder.deleted_at IS NULL
        UNION ALL
        SELECT child.id, parent.visited_ids || child.id
        FROM folders AS child
        JOIN folder_subtree AS parent ON child.parent_id = parent.id
        WHERE child.workspace_id = :workspace_id
          AND child.deleted_at IS NULL
          AND NOT child.id = ANY(parent.visited_ids)
    )
    SELECT id FROM folder_subtree
    """
)

_ALL_SUBTREE_SQL = text(
    """
    WITH RECURSIVE folder_subtree AS (
        SELECT folder.id, ARRAY[folder.id]::uuid[] AS visited_ids
        FROM folders AS folder
        WHERE folder.workspace_id = :workspace_id
          AND folder.id = :folder_id
        UNION ALL
        SELECT child.id, parent.visited_ids || child.id
        FROM folders AS child
        JOIN folder_subtree AS parent ON child.parent_id = parent.id
        WHERE child.workspace_id = :workspace_id
          AND NOT child.id = ANY(parent.visited_ids)
    )
    SELECT id FROM folder_subtree
    """
)


class ZeroFolderCaseCounter:
    def count_direct(
        self,
        session: Session,
        folder_ids: Sequence[uuid.UUID],
    ) -> Mapping[uuid.UUID, int]:
        del session
        return {folder_id: 0 for folder_id in folder_ids}


class AutoFolderCaseCounter:
    """Use Sprint 2 ``test_cases`` when present, otherwise return exact zeroes."""

    _count_sql = text(
        "SELECT category_id AS folder_id, count(*)::integer AS case_count "
        "FROM test_cases "
        "WHERE deleted_at IS NULL AND category_id IN :folder_ids "
        "GROUP BY category_id"
    ).bindparams(bindparam("folder_ids", expanding=True))

    def count_direct(
        self,
        session: Session,
        folder_ids: Sequence[uuid.UUID],
    ) -> Mapping[uuid.UUID, int]:
        if not folder_ids:
            return {}
        test_cases_table = session.execute(select(func.to_regclass("test_cases"))).scalar_one()
        if test_cases_table is None:
            return {folder_id: 0 for folder_id in folder_ids}
        rows = session.execute(self._count_sql, {"folder_ids": list(folder_ids)}).all()
        counts = {row.folder_id: row.case_count for row in rows}
        return {folder_id: counts.get(folder_id, 0) for folder_id in folder_ids}


def normalize_folder_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.strip().split()).casefold()
    if not normalized:
        raise ValueError("folder name cannot be blank")
    if len(value) > 200:
        raise ValueError("folder name cannot exceed 200 characters")
    return normalized


class FolderRepository:
    """Transaction-scoped folder service.

    Every topology mutation locks the singleton workspace row before reading
    folder state. Callers must wrap mutations in ``with session.begin():``.
    """

    def __init__(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID,
        case_counter: FolderCaseCounter | None = None,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.case_counter = case_counter or AutoFolderCaseCounter()

    def create(
        self,
        *,
        name: str,
        parent_id: uuid.UUID | None,
        sort_order: int,
        audit: AuditContext,
    ) -> Folder:
        self._validate_sort_order(sort_order)
        self._lock_topology()
        if parent_id is not None:
            self._get_folder(parent_id)
        normalized_name = normalize_folder_name(name)
        self._assert_sibling_name_available(parent_id, normalized_name)
        now = datetime.now(UTC)
        folder = Folder(
            id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            parent_id=parent_id,
            name=name.strip(),
            normalized_name=normalized_name,
            sort_order=sort_order,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.session.add(folder)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="FOLDER",
            entity_id=folder.id,
            action="CREATE",
            before=None,
            after=_folder_snapshot(folder),
            context=audit,
        )
        return folder

    def rename(
        self,
        *,
        folder_id: uuid.UUID,
        base_version: int,
        name: str,
        audit: AuditContext,
    ) -> Folder:
        self._lock_topology()
        folder = self._get_folder(folder_id)
        self._assert_mutable(folder)
        self._assert_version(folder, base_version)
        normalized_name = normalize_folder_name(name)
        self._assert_sibling_name_available(
            folder.parent_id,
            normalized_name,
            exclude_id=folder.id,
        )
        before = _folder_snapshot(folder)
        folder.name = name.strip()
        folder.normalized_name = normalized_name
        self._touch(folder)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="FOLDER",
            entity_id=folder.id,
            action="RENAME",
            before=before,
            after=_folder_snapshot(folder),
            context=audit,
        )
        return folder

    def move(
        self,
        *,
        folder_id: uuid.UUID,
        base_version: int,
        target_parent_id: uuid.UUID | None,
        sort_order: int,
        audit: AuditContext,
    ) -> FolderMutationResult:
        return self._move_or_sort(
            folder_id=folder_id,
            base_version=base_version,
            target_parent_id=target_parent_id,
            sort_order=sort_order,
            action="MOVE_SUBTREE",
            audit=audit,
        )

    def sort(
        self,
        *,
        folder_id: uuid.UUID,
        base_version: int,
        sort_order: int,
        audit: AuditContext,
    ) -> FolderMutationResult:
        return self._move_or_sort(
            folder_id=folder_id,
            base_version=base_version,
            target_parent_id=USE_CURRENT_PARENT,
            sort_order=sort_order,
            action="SORT",
            audit=audit,
        )

    def preview_soft_delete(self, *, folder_id: uuid.UUID) -> FolderDeleteImpact:
        root = self._get_folder(folder_id)
        self._assert_mutable(root)
        subtree_ids = self._subtree_ids(folder_id, include_deleted=False)
        counts = self.case_counter.count_direct(self.session, subtree_ids)
        return FolderDeleteImpact(
            folder_count=len(subtree_ids),
            test_case_count=sum(counts.values()),
        )

    def soft_delete(
        self,
        *,
        folder_id: uuid.UUID,
        base_version: int,
        acknowledge_contents: bool,
        audit: AuditContext,
    ) -> FolderMutationResult:
        self._lock_topology()
        root = self._get_folder(folder_id)
        self._assert_mutable(root)
        self._assert_version(root, base_version)
        subtree_ids = self._subtree_ids(folder_id, include_deleted=False)
        counts = self.case_counter.count_direct(self.session, subtree_ids)
        impact = FolderDeleteImpact(
            folder_count=len(subtree_ids),
            test_case_count=sum(counts.values()),
        )
        if impact.test_case_count:
            raise FolderNotEmptyError(
                folder_count=impact.folder_count,
                test_case_count=impact.test_case_count,
            )
        if impact.requires_confirmation and not acknowledge_contents:
            raise FolderContentWarning(
                folder_count=impact.folder_count,
                test_case_count=impact.test_case_count,
            )
        before = _folder_snapshot(root)
        deletion_batch_id = uuid.uuid4()
        deleted_at = datetime.now(UTC)
        self.session.execute(
            update(Folder)
            .where(
                Folder.workspace_id == self.workspace_id,
                Folder.id.in_(subtree_ids),
                Folder.deleted_at.is_(None),
            )
            .values(
                deleted_at=deleted_at,
                deletion_batch_id=deletion_batch_id,
                version=Folder.version + 1,
                updated_at=deleted_at,
            )
            .execution_options(synchronize_session=False)
        )
        self.session.flush()
        self.session.expire(root)
        self.session.refresh(root)
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="FOLDER",
            entity_id=root.id,
            action="SOFT_DELETE_SUBTREE",
            before=before,
            after=_folder_snapshot(root),
            context=audit,
        )
        return FolderMutationResult(
            root_folder_id=root.id,
            affected_folder_ids=tuple(subtree_ids),
            root_version=root.version,
            audit_id=audit_id,
            deletion_batch_id=deletion_batch_id,
            impact=impact,
        )

    def restore(
        self,
        *,
        folder_id: uuid.UUID,
        base_version: int,
        deletion_batch_id: uuid.UUID,
        audit: AuditContext,
        use_original_parent: bool = True,
        target_parent_id: uuid.UUID | None = None,
    ) -> FolderMutationResult:
        self._lock_topology()
        root = self._get_folder(folder_id, include_deleted=True)
        self._assert_mutable(root)
        self._assert_version(root, base_version)
        if root.deleted_at is None or root.deletion_batch_id is None:
            raise FolderRestoreConflictError("folder is not deleted as a restorable batch")
        if root.deletion_batch_id != deletion_batch_id:
            raise FolderRestoreConflictError("deletion batch does not match the folder")
        batch_id = root.deletion_batch_id
        batch_ids = list(
            self.session.scalars(
                select(Folder.id).where(
                    Folder.workspace_id == self.workspace_id,
                    Folder.deletion_batch_id == batch_id,
                )
            )
        )
        if not batch_ids:
            raise FolderRestoreConflictError("deletion batch is empty")
        restored_parent_id = root.parent_id if use_original_parent else target_parent_id
        if restored_parent_id in batch_ids:
            raise FolderRestoreConflictError("restore target cannot be inside the deletion batch")
        if restored_parent_id is not None:
            try:
                self._get_folder(restored_parent_id)
            except ResourceNotFoundError as error:
                raise FolderParentDeletedError("restore parent is missing or deleted") from error
        self._assert_sibling_name_available(
            restored_parent_id,
            root.normalized_name,
            exclude_id=root.id,
            restore=True,
        )
        before = _folder_snapshot(root)
        now = datetime.now(UTC)
        root.parent_id = restored_parent_id
        root.deleted_at = None
        root.deletion_batch_id = None
        self._touch(root, now=now)
        descendant_ids = [candidate for candidate in batch_ids if candidate != root.id]
        if descendant_ids:
            self.session.execute(
                update(Folder)
                .where(
                    Folder.workspace_id == self.workspace_id,
                    Folder.id.in_(descendant_ids),
                    Folder.deletion_batch_id == batch_id,
                )
                .values(
                    deleted_at=None,
                    deletion_batch_id=None,
                    version=Folder.version + 1,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="FOLDER",
            entity_id=root.id,
            action="RESTORE_SUBTREE",
            before=before,
            after=_folder_snapshot(root),
            context=audit,
        )
        return FolderMutationResult(
            root_folder_id=root.id,
            affected_folder_ids=tuple(batch_ids),
            root_version=root.version,
            audit_id=audit_id,
            deletion_batch_id=batch_id,
            impact=FolderDeleteImpact(folder_count=len(batch_ids), test_case_count=0),
        )

    def upsert_alias(
        self,
        *,
        alias: str,
        folder_id: uuid.UUID,
        audit: AuditContext,
    ) -> FolderAlias:
        self._lock_topology()
        folder = self._get_folder(folder_id, include_deleted=True)
        if folder.deleted_at is not None:
            raise FolderAliasTargetDeletedError("alias target is deleted")
        normalized_alias = normalize_folder_name(alias)
        existing = self.session.execute(
            select(FolderAlias).where(
                FolderAlias.workspace_id == self.workspace_id,
                FolderAlias.normalized_alias == normalized_alias,
            )
        ).scalar_one_or_none()
        if existing is not None and existing.folder_id != folder_id:
            raise FolderAliasConflictError("alias already points to another folder")
        before = _alias_snapshot(existing) if existing is not None else None
        now = datetime.now(UTC)
        if existing is None:
            existing = FolderAlias(
                id=uuid.uuid4(),
                workspace_id=self.workspace_id,
                folder_id=folder_id,
                alias=alias.strip(),
                normalized_alias=normalized_alias,
                created_at=now,
                updated_at=now,
            )
            self.session.add(existing)
        else:
            existing.alias = alias.strip()
            existing.updated_at = now
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="FOLDER_ALIAS",
            entity_id=existing.id,
            action="UPSERT_ALIAS",
            before=before,
            after=_alias_snapshot(existing),
            context=audit,
        )
        return existing

    def resolve_alias(self, alias: str) -> Folder | None:
        normalized_alias = normalize_folder_name(alias)
        row = self.session.execute(
            select(FolderAlias, Folder)
            .join(Folder, Folder.id == FolderAlias.folder_id)
            .where(
                FolderAlias.workspace_id == self.workspace_id,
                FolderAlias.normalized_alias == normalized_alias,
            )
        ).one_or_none()
        if row is None:
            return None
        folder: Folder = row[1]
        if folder.deleted_at is not None:
            raise FolderAliasTargetDeletedError("alias target is deleted")
        return folder

    def list_tree(self, *, include_deleted: bool = False) -> list[FolderTreeNode]:
        statement = _ALL_FOLDER_TREE_SQL if include_deleted else _ACTIVE_FOLDER_TREE_SQL
        folders = list(
            self.session.execute(statement, {"workspace_id": self.workspace_id}).mappings()
        )
        direct_counts = self.case_counter.count_direct(
            self.session,
            [folder["id"] for folder in folders],
        )
        nodes = {
            folder["id"]: FolderTreeNode(
                id=folder["id"],
                parent_id=folder["parent_id"],
                name=folder["name"],
                sort_order=folder["sort_order"],
                version=folder["version"],
                system_kind=folder["system_kind"],
                deleted_at=folder["deleted_at"],
                direct_test_case_count=direct_counts.get(folder["id"], 0),
            )
            for folder in folders
        }
        roots: list[FolderTreeNode] = []
        for folder in folders:
            node = nodes[folder["id"]]
            parent_id = folder["parent_id"]
            parent = nodes.get(parent_id) if parent_id is not None else None
            if parent is None:
                roots.append(node)
            else:
                parent.children.append(node)
        for root in roots:
            _finalize_tree(root, ())
        return roots

    def list_children(self, *, parent_id: uuid.UUID | None) -> list[FolderTreeNode]:
        roots = self.list_tree()
        if parent_id is None:
            return roots
        parent = _find_tree_node(roots, parent_id)
        if parent is None:
            raise ResourceNotFoundError("FOLDER", parent_id)
        return parent.children

    def _move_or_sort(
        self,
        *,
        folder_id: uuid.UUID,
        base_version: int,
        target_parent_id: uuid.UUID | None | _UseCurrentParent,
        sort_order: int,
        action: str,
        audit: AuditContext,
    ) -> FolderMutationResult:
        self._validate_sort_order(sort_order)
        self._lock_topology()
        folder = self._get_folder(folder_id)
        self._assert_mutable(folder)
        self._assert_version(folder, base_version)
        resolved_parent_id = (
            folder.parent_id if isinstance(target_parent_id, _UseCurrentParent) else target_parent_id
        )
        if resolved_parent_id is not None:
            self._get_folder(resolved_parent_id)
        subtree_ids = self._subtree_ids(folder_id, include_deleted=False)
        if resolved_parent_id in subtree_ids:
            raise FolderCycleError("folder cannot move below its own subtree")
        self._assert_sibling_name_available(
            resolved_parent_id,
            folder.normalized_name,
            exclude_id=folder.id,
        )
        before = _folder_snapshot(folder)
        folder.parent_id = resolved_parent_id
        folder.sort_order = sort_order
        self._touch(folder)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="FOLDER",
            entity_id=folder.id,
            action=action,
            before=before,
            after=_folder_snapshot(folder),
            context=audit,
        )
        return FolderMutationResult(
            root_folder_id=folder.id,
            affected_folder_ids=tuple(subtree_ids),
            root_version=folder.version,
            audit_id=audit_id,
        )

    def _lock_topology(self) -> None:
        workspace = self.session.execute(
            select(WorkspaceSettings.id)
            .where(WorkspaceSettings.id == self.workspace_id)
            .with_for_update()
        ).scalar_one_or_none()
        if workspace is None:
            raise ResourceNotFoundError("WORKSPACE", self.workspace_id)

    def _get_folder(self, folder_id: uuid.UUID, *, include_deleted: bool = False) -> Folder:
        statement = select(Folder).where(
            Folder.id == folder_id,
            Folder.workspace_id == self.workspace_id,
        )
        if not include_deleted:
            statement = statement.where(Folder.deleted_at.is_(None))
        folder = self.session.execute(statement).scalar_one_or_none()
        if folder is None:
            raise ResourceNotFoundError("FOLDER", folder_id)
        return folder

    def _subtree_ids(self, folder_id: uuid.UUID, *, include_deleted: bool) -> list[uuid.UUID]:
        statement = _ALL_SUBTREE_SQL if include_deleted else _ACTIVE_SUBTREE_SQL
        return list(
            self.session.execute(
                statement,
                {"workspace_id": self.workspace_id, "folder_id": folder_id},
            ).scalars()
        )

    def _assert_sibling_name_available(
        self,
        parent_id: uuid.UUID | None,
        normalized_name: str,
        *,
        exclude_id: uuid.UUID | None = None,
        restore: bool = False,
    ) -> None:
        statement = select(Folder.id).where(
            Folder.workspace_id == self.workspace_id,
            Folder.parent_id == parent_id,
            Folder.normalized_name == normalized_name,
            Folder.deleted_at.is_(None),
        )
        if exclude_id is not None:
            statement = statement.where(Folder.id != exclude_id)
        conflict_id = self.session.execute(statement.limit(1)).scalar_one_or_none()
        if conflict_id is not None:
            if restore:
                raise FolderRestoreConflictError(f"active sibling {conflict_id} has the same name")
            raise FolderNameConflictError(f"active sibling {conflict_id} has the same name")

    @staticmethod
    def _assert_mutable(folder: Folder) -> None:
        if folder.is_system:
            raise SystemFolderProtectedError(f"system folder {folder.id} cannot be mutated")

    @staticmethod
    def _assert_version(folder: Folder, expected: int) -> None:
        if folder.version != expected:
            raise VersionConflictError("FOLDER", folder.id, expected, folder.version)

    @staticmethod
    def _validate_sort_order(sort_order: int) -> None:
        if sort_order < 0:
            raise ValueError("sort_order cannot be negative")

    @staticmethod
    def _touch(folder: Folder, *, now: datetime | None = None) -> None:
        folder.version += 1
        folder.updated_at = now or datetime.now(UTC)


def _finalize_tree(
    node: FolderTreeNode,
    ancestors: tuple[tuple[uuid.UUID, str], ...],
) -> int:
    stack: list[
        tuple[FolderTreeNode, tuple[tuple[uuid.UUID, str], ...], bool]
    ] = [(node, ancestors, False)]
    while stack:
        current, current_ancestors, expanded = stack.pop()
        if expanded:
            current.subtree_test_case_count = current.direct_test_case_count + sum(
                child.subtree_test_case_count for child in current.children
            )
            continue
        current.path = (*current_ancestors, (current.id, current.name))
        current.depth = len(current_ancestors)
        stack.append((current, current_ancestors, True))
        stack.extend(
            (child, current.path, False) for child in reversed(current.children)
        )
    return node.subtree_test_case_count


def _find_tree_node(
    roots: Sequence[FolderTreeNode],
    folder_id: uuid.UUID,
) -> FolderTreeNode | None:
    stack = list(reversed(roots))
    while stack:
        candidate = stack.pop()
        if candidate.id == folder_id:
            return candidate
        stack.extend(reversed(candidate.children))
    return None


def _folder_snapshot(folder: Folder) -> dict[str, object]:
    return {
        "id": str(folder.id),
        "workspaceId": str(folder.workspace_id),
        "parentId": str(folder.parent_id) if folder.parent_id is not None else None,
        "name": folder.name,
        "normalizedName": folder.normalized_name,
        "sortOrder": folder.sort_order,
        "version": folder.version,
        "systemKind": folder.system_kind,
        "deletionBatchId": (
            str(folder.deletion_batch_id) if folder.deletion_batch_id is not None else None
        ),
        "deletedAt": folder.deleted_at.isoformat() if folder.deleted_at is not None else None,
    }


def _alias_snapshot(alias: FolderAlias | None) -> dict[str, object] | None:
    if alias is None:
        return None
    return {
        "id": str(alias.id),
        "workspaceId": str(alias.workspace_id),
        "folderId": str(alias.folder_id),
        "alias": alias.alias,
        "normalizedAlias": alias.normalized_alias,
    }
