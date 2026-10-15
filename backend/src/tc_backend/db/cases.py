"""Transaction-scoped repository for the flat test library case ledger."""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, select, text
from sqlalchemy.orm import Session

from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import (
    CategoryInvariantError,
    GenerationManagementConflictError,
    PermanentDeleteConflictError,
    ResourceNotFoundError,
    TestCaseDeletedError,
    TestCaseRestoreConflictError,
    VersionConflictError,
    WorkItemKeyConflictError,
    WorkItemKeyNotFoundError,
)
from tc_backend.db.models import (
    CycleItem,
    ExpectedResultMode,
    Folder,
    GenerationApplyOutcome,
    GenerationManagementEventType,
    GenerationManagementMutation,
    GenerationSuggestion,
    GenerationSuggestionResolution,
    GenerationSuggestionTarget,
    IssueLink,
    KeyEntityType,
    OutboxEvent,
    OutboxStatus,
    Tag,
    TestCase,
    TestCaseGenerationManagementEvent,
    TestCasePriority,
    TestCaseSourceType,
    TestCaseStatus,
    TestCaseTag,
    TestCaseVersion,
    TestCaseVersionTag,
    TestStep,
    WorkItem,
    WorkspaceSettings,
)
from tc_backend.db.workspaces import WorkspaceRepository


@dataclass(frozen=True, slots=True)
class TestStepInput:
    action: str
    expected_result: str | None = None
    precondition: str | None = None


@dataclass(frozen=True, slots=True)
class TestCaseContent:
    summary: str
    precondition: str
    steps: tuple[TestStepInput, ...]
    expected_result_mode: ExpectedResultMode = ExpectedResultMode.PER_STEP
    expected_result: str | None = None
    status: TestCaseStatus = TestCaseStatus.ACTIVE
    priority: TestCasePriority = TestCasePriority.MEDIUM
    assignee_label: str | None = None
    tag_names: tuple[str, ...] = ()
    source_type: TestCaseSourceType = TestCaseSourceType.MANUAL
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    generation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class VersionStep:
    sequence: int
    precondition: str | None
    action: str
    expected_result: str | None


@dataclass(frozen=True, slots=True)
class VersionSnapshot:
    id: uuid.UUID
    test_case_id: uuid.UUID
    version_number: int
    status: str
    summary: str
    precondition: str
    expected_result_mode: str
    expected_result: str | None
    priority: str
    assignee_label: str | None
    tag_names: tuple[str, ...]
    steps: tuple[VersionStep, ...]
    source_type: str
    source_metadata: dict[str, Any]
    generation_reason: str | None
    change_reason: str
    restored_from_version_id: uuid.UUID | None
    copied_from_version_id: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VersionFieldDiff:
    field: str
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class VersionDiff:
    test_case_id: uuid.UUID
    from_version: int
    to_version: int
    fields: tuple[VersionFieldDiff, ...]


@dataclass(frozen=True, slots=True)
class CategoryProjection:
    id: uuid.UUID
    name: str
    sort_order: int
    work_item_count: int
    active_test_case_count: int


@dataclass(frozen=True, slots=True)
class CaseAggregate:
    test_case: TestCase
    current_version: VersionSnapshot
    audit_id: uuid.UUID | None = None


def normalize_tag_name(value: str) -> str:
    display_name = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    normalized = display_name.casefold()
    if not normalized:
        raise ValueError("tag name cannot be blank")
    if len(display_name) > 100 or len(normalized) > 100:
        raise ValueError("tag name cannot exceed 100 characters")
    return normalized


def validate_case_content(content: TestCaseContent) -> None:
    _nonblank(content.summary, "summary", maximum=200)
    _nonblank(content.precondition, "precondition")
    if not content.steps:
        raise ValueError("a test case version requires at least one step")
    mode = ExpectedResultMode(content.expected_result_mode)
    TestCaseStatus(content.status)
    TestCasePriority(content.priority)
    TestCaseSourceType(content.source_type)
    if not isinstance(content.source_metadata, Mapping):
        raise ValueError("source_metadata must be an object")
    if mode is ExpectedResultMode.CASE:
        _nonblank(content.expected_result, "expected_result")
    elif content.expected_result is not None:
        raise ValueError("PER_STEP mode cannot have a case-level expected result")
    for step in content.steps:
        if step.precondition is not None:
            _nonblank(step.precondition, "step precondition")
        _nonblank(step.action, "step action")
        if mode is ExpectedResultMode.PER_STEP:
            _nonblank(step.expected_result, "step expected_result")
        elif step.expected_result is not None:
            raise ValueError("CASE mode steps cannot have expected results")
    if content.assignee_label is not None:
        _nonblank(content.assignee_label, "assignee_label", maximum=200)
    if content.generation_reason is not None:
        _nonblank(content.generation_reason, "generation_reason")
    _normalized_tag_inputs(content.tag_names)


class CaseRepository:
    """Case aggregate operations; callers own the surrounding transaction."""

    def __init__(self, session: Session, *, workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def list_categories(self) -> list[CategoryProjection]:
        statement = (
            select(
                Folder.id,
                Folder.name,
                Folder.sort_order,
                func.count(func.distinct(WorkItem.id)).label("work_item_count"),
                func.count(TestCase.id).label("active_test_case_count"),
            )
            .outerjoin(
                WorkItem,
                and_(
                    WorkItem.workspace_id == Folder.workspace_id,
                    WorkItem.category_id == Folder.id,
                ),
            )
            .outerjoin(
                TestCase,
                and_(
                    TestCase.workspace_id == Folder.workspace_id,
                    TestCase.work_item_id == WorkItem.id,
                    TestCase.deleted_at.is_(None),
                ),
            )
            .where(
                Folder.workspace_id == self.workspace_id,
                Folder.parent_id.is_(None),
                Folder.deleted_at.is_(None),
            )
            .group_by(Folder.id, Folder.name, Folder.sort_order)
            .order_by(Folder.sort_order, Folder.name, Folder.id)
        )
        return [
            CategoryProjection(
                id=row.id,
                name=row.name,
                sort_order=row.sort_order,
                work_item_count=row.work_item_count,
                active_test_case_count=row.active_test_case_count,
            )
            for row in self.session.execute(statement)
        ]

    def create_work_item(
        self,
        *,
        work_item_key: str,
        category_id: uuid.UUID,
        summary: str,
        description: str = "",
        owner_label: str | None = None,
        labels: Sequence[str] = (),
        audit: AuditContext,
    ) -> WorkItem:
        self._lock_library()
        self._get_category(category_id)
        key = _nonblank(work_item_key, "work_item_key", maximum=100)
        if self.session.scalar(
            select(WorkItem.id).where(
                WorkItem.workspace_id == self.workspace_id,
                WorkItem.work_item_key == key,
            )
        ) is not None:
            raise WorkItemKeyConflictError(key)
        clean_summary = _nonblank(summary, "summary", maximum=200)
        clean_owner = _optional_nonblank(owner_label, "owner_label", maximum=200)
        clean_labels = _normalized_display_values(labels, maximum=100)
        now = datetime.now(UTC)
        work_item = WorkItem(
            id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            category_id=category_id,
            work_item_key=key,
            summary=clean_summary,
            description=description.strip(),
            owner_label=clean_owner,
            labels=clean_labels,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.session.add(work_item)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="WORK_ITEM",
            entity_id=work_item.id,
            action="CREATE",
            before=None,
            after=_work_item_snapshot(work_item),
            context=audit,
        )
        return work_item

    def get_work_item(self, work_item_id: uuid.UUID, *, for_update: bool = False) -> WorkItem:
        statement = select(WorkItem).where(
            WorkItem.id == work_item_id,
            WorkItem.workspace_id == self.workspace_id,
        )
        if for_update:
            statement = statement.with_for_update()
        work_item = self.session.execute(statement).scalar_one_or_none()
        if work_item is None:
            raise ResourceNotFoundError("WORK_ITEM", work_item_id)
        return work_item

    def get_work_item_by_key(
        self,
        work_item_key: str,
        *,
        for_update: bool = False,
    ) -> WorkItem:
        statement = select(WorkItem).where(
            WorkItem.work_item_key == work_item_key,
            WorkItem.workspace_id == self.workspace_id,
        )
        if for_update:
            statement = statement.with_for_update()
        work_item = self.session.execute(statement).scalar_one_or_none()
        if work_item is None:
            raise WorkItemKeyNotFoundError(work_item_key)
        return work_item

    def list_work_items(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkItem]:
        statement = (
            select(WorkItem)
            .where(WorkItem.workspace_id == self.workspace_id)
            .order_by(WorkItem.updated_at.desc(), WorkItem.work_item_key)
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list(
            self.session.scalars(statement)
        )

    def update_work_item(
        self,
        *,
        work_item_key: str,
        base_version: int,
        category_id: uuid.UUID,
        summary: str,
        description: str,
        owner_label: str | None,
        labels: Sequence[str],
        audit: AuditContext,
    ) -> WorkItem:
        self._lock_library()
        work_item = self.get_work_item_by_key(work_item_key, for_update=True)
        self._assert_work_item_version(work_item, base_version)
        self._get_category(category_id)
        before = _work_item_snapshot(work_item)
        now = datetime.now(UTC)
        if work_item.category_id != category_id:
            children = list(
                self.session.scalars(
                    select(TestCase)
                    .where(
                        TestCase.workspace_id == self.workspace_id,
                        TestCase.work_item_id == work_item.id,
                    )
                    .with_for_update()
                )
            )
            for test_case in children:
                current_version = self._current_version(test_case).version_number
                child_before = _case_snapshot(test_case, current_version)
                test_case.category_id = category_id
                test_case.version += 1
                test_case.updated_at = now
                append_audit(
                    self.session,
                    workspace_id=self.workspace_id,
                    entity_type="TEST_CASE",
                    entity_id=test_case.id,
                    action="WORK_ITEM_CATEGORY_SYNC",
                    before=child_before,
                    after=_case_snapshot(test_case, current_version),
                    context=audit,
                )
        work_item.category_id = category_id
        work_item.summary = _nonblank(summary, "summary", maximum=200)
        work_item.description = description.strip()
        work_item.owner_label = _optional_nonblank(owner_label, "owner_label", maximum=200)
        work_item.labels = _normalized_display_values(labels, maximum=100)
        work_item.version += 1
        work_item.updated_at = now
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="WORK_ITEM",
            entity_id=work_item.id,
            action="UPDATE",
            before=before,
            after=_work_item_snapshot(work_item),
            context=audit,
        )
        return work_item

    def create_case(
        self,
        *,
        work_item_id: uuid.UUID,
        content: TestCaseContent,
        change_reason: str,
        audit: AuditContext,
        sort_order: int | None = None,
    ) -> CaseAggregate:
        self._lock_library()
        work_item = self.get_work_item(work_item_id, for_update=True)
        return self._create_case_locked(
            work_item=work_item,
            content=content,
            change_reason=change_reason,
            audit=audit,
            sort_order=sort_order,
            copied_from_version_id=None,
        )

    def update_case(
        self,
        *,
        test_case_id: uuid.UUID,
        base_version: int,
        content: TestCaseContent,
        change_reason: str,
        audit: AuditContext,
    ) -> CaseAggregate:
        self._lock_library()
        test_case = self._get_case(test_case_id, for_update=True)
        self._assert_version(test_case, base_version)
        current_snapshot = self._version_snapshot(self._current_version(test_case))
        validate_case_content(content)
        _nonblank(change_reason, "change_reason", maximum=500)
        if _semantic_content_key(content) == _snapshot_semantic_content_key(current_snapshot):
            return self.get_aggregate(test_case.id, include_deleted=False)
        before_number = current_snapshot.version_number
        before = _case_snapshot(test_case, before_number)
        new_version = self._append_version(
            test_case=test_case,
            content=content,
            change_reason=change_reason,
        )
        test_case.current_version_id = new_version.id
        test_case.status = new_version.status
        test_case.version += 1
        test_case.updated_at = datetime.now(UTC)
        self._release_generation_management(
            test_case,
            audit=audit,
            reason=change_reason,
            mutation=GenerationManagementMutation.CONTENT_VERSION,
            released_by_version_id=new_version.id,
            before=before,
            current_version_number=new_version.version_number,
        )
        self._replace_current_tags(test_case.id, new_version.id, content.tag_names)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CASE",
            entity_id=test_case.id,
            action="CREATE_VERSION",
            before=before,
            after=_case_snapshot(test_case, new_version.version_number),
            context=audit,
        )
        self._append_projection_event(test_case, new_version.version_number, "CREATE_VERSION")
        return self.get_aggregate(test_case.id, include_deleted=False, audit_id=audit_id)

    def list_versions(self, test_case_id: uuid.UUID) -> tuple[VersionSnapshot, ...]:
        self._get_case(test_case_id, include_deleted=True)
        version_numbers = self.session.scalars(
            select(TestCaseVersion.version_number)
            .where(TestCaseVersion.test_case_id == test_case_id)
            .order_by(TestCaseVersion.version_number)
        ).all()
        return tuple(self.get_version(test_case_id, number) for number in version_numbers)

    def get_version(self, test_case_id: uuid.UUID, version_number: int) -> VersionSnapshot:
        version = self.session.execute(
            select(TestCaseVersion).where(
                TestCaseVersion.test_case_id == test_case_id,
                TestCaseVersion.version_number == version_number,
            )
        ).scalar_one_or_none()
        if version is None:
            raise ResourceNotFoundError("TEST_CASE_VERSION", test_case_id)
        return self._version_snapshot(version)

    def diff_versions(
        self,
        *,
        test_case_id: uuid.UUID,
        from_version: int,
        to_version: int,
    ) -> VersionDiff:
        before = self.get_version(test_case_id, from_version)
        after = self.get_version(test_case_id, to_version)
        before_fields = _version_diff_values(before)
        after_fields = _version_diff_values(after)
        fields = tuple(
            VersionFieldDiff(field=name, before=before_fields[name], after=after_fields[name])
            for name in before_fields
            if before_fields[name] != after_fields[name]
        )
        return VersionDiff(
            test_case_id=test_case_id,
            from_version=from_version,
            to_version=to_version,
            fields=fields,
        )

    def restore_version(
        self,
        *,
        test_case_id: uuid.UUID,
        base_version: int,
        restore_version: int,
        change_reason: str,
        audit: AuditContext,
    ) -> CaseAggregate:
        self._lock_library()
        test_case = self._get_case(test_case_id, for_update=True)
        self._assert_version(test_case, base_version)
        source = self.get_version(test_case_id, restore_version)
        current = self._current_version(test_case)
        if source.id == current.id:
            raise ValueError("restore target must be an older version")
        before = _case_snapshot(test_case, current.version_number)
        content = _content_from_snapshot(source)
        new_version = self._append_version(
            test_case=test_case,
            content=content,
            change_reason=change_reason,
            restored_from_version_id=source.id,
        )
        test_case.current_version_id = new_version.id
        test_case.status = new_version.status
        test_case.version += 1
        test_case.updated_at = datetime.now(UTC)
        self._release_generation_management(
            test_case,
            audit=audit,
            reason=change_reason,
            mutation=GenerationManagementMutation.CONTENT_VERSION,
            released_by_version_id=new_version.id,
            before=before,
            current_version_number=new_version.version_number,
        )
        self._replace_current_tags(test_case.id, new_version.id, content.tag_names)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CASE",
            entity_id=test_case.id,
            action="RESTORE_VERSION",
            before=before,
            after=_case_snapshot(test_case, new_version.version_number),
            context=audit,
        )
        self._append_projection_event(test_case, new_version.version_number, "RESTORE_VERSION")
        return self.get_aggregate(test_case.id, include_deleted=False, audit_id=audit_id)

    def copy_case(
        self,
        *,
        source_test_case_id: uuid.UUID,
        target_work_item_id: uuid.UUID,
        audit: AuditContext,
        change_reason: str,
        source_version: int | None = None,
        sort_order: int | None = None,
    ) -> CaseAggregate:
        self._lock_library()
        source_case = self._get_case(source_test_case_id, for_update=True)
        source = (
            self._version_snapshot(self._current_version(source_case))
            if source_version is None
            else self.get_version(source_test_case_id, source_version)
        )
        target = self.get_work_item(target_work_item_id, for_update=True)
        return self._create_case_locked(
            work_item=target,
            content=_content_from_snapshot(source),
            change_reason=change_reason,
            audit=audit,
            sort_order=sort_order,
            copied_from_version_id=source.id,
        )

    def move_case(
        self,
        *,
        test_case_id: uuid.UUID,
        base_version: int,
        target_work_item_id: uuid.UUID,
        sort_order: int,
        audit: AuditContext,
    ) -> CaseAggregate:
        self._lock_library()
        test_case = self._get_case(test_case_id, for_update=True)
        self._assert_version(test_case, base_version)
        target = self.get_work_item(target_work_item_id, for_update=True)
        current_number = self._current_version(test_case).version_number
        before = _case_snapshot(test_case, current_number)
        source_work_item_id = test_case.work_item_id
        if source_work_item_id == target.id:
            ordered = [case for case in self._active_cases(target.id) if case.id != test_case.id]
            _validate_insertion_order(sort_order, len(ordered))
            if test_case.sort_order == sort_order:
                return self.get_aggregate(test_case.id, include_deleted=False)
            ordered.insert(sort_order, test_case)
            self._apply_order(ordered)
            action = "REORDER"
        else:
            source_order = [
                case for case in self._active_cases(source_work_item_id) if case.id != test_case.id
            ]
            target_order = self._active_cases(target.id)
            _validate_insertion_order(sort_order, len(target_order))
            self._apply_order(source_order)
            test_case.work_item_id = target.id
            test_case.category_id = target.category_id
            target_order.insert(sort_order, test_case)
            self._apply_order(target_order)
            action = "MOVE"
        test_case.version += 1
        test_case.updated_at = datetime.now(UTC)
        if action == "MOVE":
            self._release_generation_management(
                test_case,
                audit=audit,
                reason=audit.reason or "Test case moved to another work item.",
                mutation=GenerationManagementMutation.PARENT_MOVE,
                released_by_version_id=None,
                before=before,
                current_version_number=current_number,
            )
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CASE",
            entity_id=test_case.id,
            action=action,
            before=before,
            after=_case_snapshot(test_case, current_number),
            context=audit,
        )
        self._append_projection_event(test_case, current_number, action)
        return self.get_aggregate(test_case.id, include_deleted=False, audit_id=audit_id)

    def soft_delete(
        self,
        *,
        test_case_id: uuid.UUID,
        base_version: int,
        audit: AuditContext,
    ) -> CaseAggregate:
        self._lock_library()
        test_case = self._get_case(test_case_id, for_update=True)
        self._assert_version(test_case, base_version)
        current_number = self._current_version(test_case).version_number
        before = _case_snapshot(test_case, current_number)
        test_case.deleted_at = datetime.now(UTC)
        test_case.version += 1
        test_case.updated_at = test_case.deleted_at
        remaining = [
            case for case in self._active_cases(test_case.work_item_id) if case.id != test_case.id
        ]
        self._apply_order(remaining)
        self._release_generation_management(
            test_case,
            audit=audit,
            reason=audit.reason or "Test case was soft deleted.",
            mutation=GenerationManagementMutation.SOFT_DELETE,
            released_by_version_id=None,
            before=before,
            current_version_number=current_number,
        )
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CASE",
            entity_id=test_case.id,
            action="SOFT_DELETE",
            before=before,
            after=_case_snapshot(test_case, current_number),
            context=audit,
        )
        self._append_projection_event(test_case, current_number, "SOFT_DELETE")
        return self.get_aggregate(test_case.id, include_deleted=True, audit_id=audit_id)

    def restore_deleted(
        self,
        *,
        test_case_id: uuid.UUID,
        base_version: int,
        audit: AuditContext,
        target_work_item_id: uuid.UUID | None = None,
        sort_order: int | None = None,
    ) -> CaseAggregate:
        self._lock_library()
        test_case = self._get_case(test_case_id, for_update=True, include_deleted=True)
        self._assert_version(test_case, base_version)
        if test_case.deleted_at is None:
            raise TestCaseRestoreConflictError("test case is not deleted")
        current_number = self._current_version(test_case).version_number
        before = _case_snapshot(test_case, current_number)
        target = self.get_work_item(target_work_item_id or test_case.work_item_id, for_update=True)
        ordered = self._active_cases(target.id)
        insertion_order = len(ordered) if sort_order is None else sort_order
        _validate_insertion_order(insertion_order, len(ordered))
        test_case.work_item_id = target.id
        test_case.category_id = target.category_id
        test_case.deleted_at = None
        ordered.insert(insertion_order, test_case)
        self._apply_order(ordered)
        test_case.version += 1
        test_case.updated_at = datetime.now(UTC)
        self._release_generation_management(
            test_case,
            audit=audit,
            reason=audit.reason or "Deleted test case was restored.",
            mutation=GenerationManagementMutation.RESTORE_DELETED,
            released_by_version_id=None,
            before=before,
            current_version_number=current_number,
        )
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CASE",
            entity_id=test_case.id,
            action="RESTORE",
            before=before,
            after=_case_snapshot(test_case, current_number),
            context=audit,
        )
        self._append_projection_event(test_case, current_number, "RESTORE")
        return self.get_aggregate(test_case.id, include_deleted=False, audit_id=audit_id)

    def permanent_delete(
        self,
        *,
        test_case_id: uuid.UUID,
        base_version: int,
        audit: AuditContext,
    ) -> uuid.UUID:
        """Delete a soft-deleted case whose immutable versions have no external consumers."""

        self._lock_library()
        test_case = self._get_case(test_case_id, for_update=True, include_deleted=True)
        self._assert_version(test_case, base_version)
        if test_case.deleted_at is None:
            raise PermanentDeleteConflictError(
                "TEST_CASE", test_case.id, "soft delete is required first"
            )
        version_ids = select(TestCaseVersion.id).where(
            TestCaseVersion.test_case_id == test_case.id
        )
        references = {
            "cycleItems": self.session.scalar(
                select(func.count())
                .select_from(CycleItem)
                .where(CycleItem.test_case_id == test_case.id)
            )
            or 0,
            "issueLinks": self.session.scalar(
                select(func.count())
                .select_from(IssueLink)
                .where(IssueLink.test_case_id == test_case.id)
            )
            or 0,
            "copiedVersions": self.session.scalar(
                select(func.count())
                .select_from(TestCaseVersion)
                .where(TestCaseVersion.copied_from_version_id.in_(version_ids))
            )
            or 0,
            "generationApplyOutcomes": self.session.scalar(
                select(func.count())
                .select_from(GenerationApplyOutcome)
                .where(GenerationApplyOutcome.test_case_id == test_case.id)
            )
            or 0,
            "generationSuggestionTargets": self.session.scalar(
                select(func.count())
                .select_from(GenerationSuggestionTarget)
                .where(GenerationSuggestionTarget.test_case_id == test_case.id)
            )
            or 0,
            "generationSuggestions": self.session.scalar(
                select(func.count())
                .select_from(GenerationSuggestion)
                .where(GenerationSuggestion.test_case_id == test_case.id)
            )
            or 0,
            "generationSuggestionResolutions": self.session.scalar(
                select(func.count())
                .select_from(GenerationSuggestionResolution)
                .join(
                    GenerationSuggestion,
                    GenerationSuggestion.id
                    == GenerationSuggestionResolution.suggestion_id,
                )
                .where(GenerationSuggestion.test_case_id == test_case.id)
            )
            or 0,
        }
        blocking = {name: count for name, count in references.items() if count}
        if blocking:
            raise PermanentDeleteConflictError(
                "TEST_CASE",
                test_case.id,
                "immutable cycle, issue, copy, generation, or suggestion history still references the case",
                references=blocking,
            )
        current_number = self._current_version(test_case).version_number
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CASE",
            entity_id=test_case.id,
            action="PERMANENT_DELETE",
            before=_case_snapshot(test_case, current_number),
            after=None,
            context=audit,
        )
        # Immutable version triggers recognize this transaction-local capability;
        # the parent state and every external reference were locked/validated above.
        self.session.execute(
            text("SELECT set_config('tc.permanent_delete', 'on', true)")
        )
        self.session.delete(test_case)
        self.session.flush()
        return audit_id

    def initialize_generation_management(
        self,
        *,
        test_case_id: uuid.UUID,
        created_outcome_id: uuid.UUID,
    ) -> None:
        """Initialize ownership from an outcome already written by this apply transaction."""

        test_case = self._get_case(test_case_id, for_update=True)
        if test_case.generation_lineage_id is not None:
            raise GenerationManagementConflictError(
                test_case.id, "management was already initialized"
            )
        baseline_version_id = test_case.current_version_id
        lineage_id = uuid.uuid4()
        self.session.execute(
            text(
                "SELECT set_config(" 
                "'tc.generation_management_operation', 'initialize', true)"
            )
        )
        test_case.generation_managed = True
        test_case.generation_lineage_id = lineage_id
        test_case.generation_created_outcome_id = created_outcome_id
        test_case.generation_baseline_version_id = baseline_version_id
        test_case.generation_latest_version_id = baseline_version_id
        self.session.add(
            TestCaseGenerationManagementEvent(
                id=uuid.uuid4(),
                workspace_id=self.workspace_id,
                test_case_id=test_case.id,
                event_type=GenerationManagementEventType.INITIALIZED.value,
                lineage_id=lineage_id,
                created_outcome_id=created_outcome_id,
                baseline_generated_version_id=baseline_version_id,
                latest_generated_version_id=baseline_version_id,
                occurred_at=datetime.now(UTC),
            )
        )
        self.session.flush()
        self.session.execute(
            text(
                "SELECT set_config(" 
                "'tc.generation_management_operation', 'off', true)"
            )
        )

    def get_aggregate(
        self,
        test_case_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        audit_id: uuid.UUID | None = None,
    ) -> CaseAggregate:
        test_case = self._get_case(test_case_id, include_deleted=include_deleted)
        return CaseAggregate(
            test_case=test_case,
            current_version=self._version_snapshot(self._current_version(test_case)),
            audit_id=audit_id,
        )

    def _create_case_locked(
        self,
        *,
        work_item: WorkItem,
        content: TestCaseContent,
        change_reason: str,
        audit: AuditContext,
        sort_order: int | None,
        copied_from_version_id: uuid.UUID | None,
    ) -> CaseAggregate:
        validate_case_content(content)
        clean_reason = _nonblank(change_reason, "change_reason", maximum=500)
        ordered = self._active_cases(work_item.id)
        insertion_order = len(ordered) if sort_order is None else sort_order
        _validate_insertion_order(insertion_order, len(ordered))
        test_case_id = uuid.uuid4()
        version_id = uuid.uuid4()
        allocation = WorkspaceRepository(
            self.session,
            workspace_id=self.workspace_id,
        ).allocate_key(
            entity_type=KeyEntityType.TEST_CASE,
            entity_id=test_case_id,
            audit=audit,
        )
        now = datetime.now(UTC)
        test_case = TestCase(
            id=test_case_id,
            workspace_id=self.workspace_id,
            work_item_id=work_item.id,
            category_id=work_item.category_id,
            case_key=allocation.key,
            current_version_id=version_id,
            status=TestCaseStatus(content.status).value,
            sort_order=insertion_order,
            version=1,
            created_at=now,
            updated_at=now,
        )
        ordered.insert(insertion_order, test_case)
        self._apply_order(ordered)
        version = self._new_version(
            id=version_id,
            test_case_id=test_case_id,
            version_number=1,
            content=content,
            change_reason=clean_reason,
            copied_from_version_id=copied_from_version_id,
        )
        self.session.add_all([test_case, version])
        tags = self._ensure_tags(content.tag_names)
        self._add_version_children(version.id, content.steps, tags)
        self.session.add_all(TestCaseTag(test_case_id=test_case.id, tag_id=tag.id) for tag in tags)
        self.session.flush()
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CASE",
            entity_id=test_case.id,
            action="COPY" if copied_from_version_id is not None else "CREATE",
            before=None,
            after=_case_snapshot(test_case, 1),
            context=audit,
        )
        self._append_projection_event(
            test_case,
            1,
            "COPY" if copied_from_version_id is not None else "CREATE",
        )
        return self.get_aggregate(test_case.id, audit_id=audit_id)

    def _release_generation_management(
        self,
        test_case: TestCase,
        *,
        audit: AuditContext,
        reason: str,
        mutation: GenerationManagementMutation,
        released_by_version_id: uuid.UUID | None,
        before: dict[str, Any],
        current_version_number: int,
    ) -> None:
        if not test_case.generation_managed:
            return
        lineage_id = test_case.generation_lineage_id
        outcome_id = test_case.generation_created_outcome_id
        baseline_id = test_case.generation_baseline_version_id
        latest_id = test_case.generation_latest_version_id
        if None in (lineage_id, outcome_id, baseline_id, latest_id):
            raise RuntimeError("managed test case has incomplete generation lineage")
        clean_reason = _nonblank(reason, "generation release reason", maximum=500)
        released_at = datetime.now(UTC)
        actor: dict[str, str] = {"type": audit.source_type.value}
        if audit.actor_label is not None:
            actor["actorLabel"] = audit.actor_label
        if audit.external_actor_id is not None:
            actor["externalActorId"] = audit.external_actor_id
        test_case.generation_managed = False
        test_case.generation_released_at = released_at
        test_case.generation_release_reason = clean_reason
        test_case.generation_release_actor = actor
        test_case.generation_release_mutation = mutation.value
        test_case.generation_released_from_version_id = latest_id
        test_case.generation_released_by_version_id = released_by_version_id
        self.session.add(
            TestCaseGenerationManagementEvent(
                id=uuid.uuid4(),
                workspace_id=self.workspace_id,
                test_case_id=test_case.id,
                event_type=GenerationManagementEventType.RELEASED.value,
                lineage_id=lineage_id,
                created_outcome_id=outcome_id,
                baseline_generated_version_id=baseline_id,
                latest_generated_version_id=latest_id,
                released_at=released_at,
                release_reason=clean_reason,
                release_actor=actor,
                release_mutation=mutation.value,
                released_from_version_id=latest_id,
                released_by_version_id=released_by_version_id,
                occurred_at=released_at,
            )
        )
        append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type="TEST_CASE",
            entity_id=test_case.id,
            action="GENERATION_MANAGEMENT_RELEASED",
            before=before,
            after=_case_snapshot(test_case, current_version_number),
            context=AuditContext(
                source_type=audit.source_type,
                actor_label=audit.actor_label,
                external_actor_id=audit.external_actor_id,
                reason=audit.reason,
                trace_id=audit.trace_id,
            ),
        )

    def _append_projection_event(
        self,
        test_case: TestCase,
        current_version_number: int,
        change_kind: str,
    ) -> uuid.UUID:
        event_id = uuid.uuid4()
        self.session.add(
            OutboxEvent(
                id=event_id,
                workspace_id=self.workspace_id,
                aggregate_type="TEST_CASE",
                aggregate_id=test_case.id,
                event_type="TEST_CASE_CHANGED",
                aggregate_version=test_case.version,
                payload={
                    **_case_snapshot(test_case, current_version_number),
                    "changeKind": change_kind,
                },
                status=OutboxStatus.READY.value,
                attempts=0,
            )
        )
        return event_id

    def _append_version(
        self,
        *,
        test_case: TestCase,
        content: TestCaseContent,
        change_reason: str,
        restored_from_version_id: uuid.UUID | None = None,
    ) -> TestCaseVersion:
        validate_case_content(content)
        clean_reason = _nonblank(change_reason, "change_reason", maximum=500)
        current = self._current_version(test_case)
        version = self._new_version(
            id=uuid.uuid4(),
            test_case_id=test_case.id,
            version_number=current.version_number + 1,
            content=content,
            change_reason=clean_reason,
            restored_from_version_id=restored_from_version_id,
        )
        self.session.add(version)
        tags = self._ensure_tags(content.tag_names)
        self._add_version_children(version.id, content.steps, tags)
        return version

    def _new_version(
        self,
        *,
        id: uuid.UUID,
        test_case_id: uuid.UUID,
        version_number: int,
        content: TestCaseContent,
        change_reason: str,
        restored_from_version_id: uuid.UUID | None = None,
        copied_from_version_id: uuid.UUID | None = None,
    ) -> TestCaseVersion:
        mode = ExpectedResultMode(content.expected_result_mode)
        return TestCaseVersion(
            id=id,
            test_case_id=test_case_id,
            version_number=version_number,
            status=TestCaseStatus(content.status).value,
            summary=content.summary.strip(),
            precondition=content.precondition.strip(),
            expected_result_mode=mode.value,
            expected_result=(content.expected_result.strip() if content.expected_result else None),
            priority=TestCasePriority(content.priority).value,
            assignee_label=_optional_nonblank(
                content.assignee_label, "assignee_label", maximum=200
            ),
            source_type=TestCaseSourceType(content.source_type).value,
            source_metadata=dict(content.source_metadata),
            generation_reason=_optional_nonblank(content.generation_reason, "generation_reason"),
            change_reason=change_reason,
            restored_from_version_id=restored_from_version_id,
            copied_from_version_id=copied_from_version_id,
            created_at=datetime.now(UTC),
        )

    def _add_version_children(
        self,
        version_id: uuid.UUID,
        steps: Sequence[TestStepInput],
        tags: Sequence[Tag],
    ) -> None:
        self.session.add_all(
            TestStep(
                id=uuid.uuid4(),
                test_case_version_id=version_id,
                sequence=index,
                precondition=(step.precondition.strip() if step.precondition else None),
                action=step.action.strip(),
                expected_result=(step.expected_result.strip() if step.expected_result else None),
            )
            for index, step in enumerate(steps, start=1)
        )
        self.session.add_all(
            TestCaseVersionTag(test_case_version_id=version_id, tag_id=tag.id) for tag in tags
        )

    def _replace_current_tags(
        self,
        test_case_id: uuid.UUID,
        version_id: uuid.UUID,
        tag_names: Sequence[str],
    ) -> None:
        del version_id  # projection validity is enforced by a deferred DB trigger
        tags = self._ensure_tags(tag_names)
        self.session.execute(
            delete(TestCaseTag)
            .where(TestCaseTag.test_case_id == test_case_id)
            .execution_options(synchronize_session=False)
        )
        self.session.add_all(TestCaseTag(test_case_id=test_case_id, tag_id=tag.id) for tag in tags)

    def _ensure_tags(self, tag_names: Sequence[str]) -> list[Tag]:
        tags: list[Tag] = []
        for display_name, normalized_name in _normalized_tag_inputs(tag_names):
            tag = self.session.execute(
                select(Tag).where(
                    Tag.workspace_id == self.workspace_id,
                    Tag.normalized_name == normalized_name,
                )
            ).scalar_one_or_none()
            if tag is None:
                tag = Tag(
                    id=uuid.uuid4(),
                    workspace_id=self.workspace_id,
                    name=display_name,
                    normalized_name=normalized_name,
                    created_at=datetime.now(UTC),
                )
                self.session.add(tag)
            tags.append(tag)
        return tags

    def _version_snapshot(self, version: TestCaseVersion) -> VersionSnapshot:
        steps = tuple(
            VersionStep(
                sequence=step.sequence,
                precondition=step.precondition,
                action=step.action,
                expected_result=step.expected_result,
            )
            for step in self.session.scalars(
                select(TestStep)
                .where(TestStep.test_case_version_id == version.id)
                .order_by(TestStep.sequence)
            )
        )
        tag_names = tuple(
            self.session.scalars(
                select(Tag.name)
                .join(TestCaseVersionTag, TestCaseVersionTag.tag_id == Tag.id)
                .where(TestCaseVersionTag.test_case_version_id == version.id)
                .order_by(Tag.normalized_name, Tag.id)
            )
        )
        return VersionSnapshot(
            id=version.id,
            test_case_id=version.test_case_id,
            version_number=version.version_number,
            status=version.status,
            summary=version.summary,
            precondition=version.precondition,
            expected_result_mode=version.expected_result_mode,
            expected_result=version.expected_result,
            priority=version.priority,
            assignee_label=version.assignee_label,
            tag_names=tag_names,
            steps=steps,
            source_type=version.source_type,
            source_metadata=dict(version.source_metadata),
            generation_reason=version.generation_reason,
            change_reason=version.change_reason,
            restored_from_version_id=version.restored_from_version_id,
            copied_from_version_id=version.copied_from_version_id,
            created_at=version.created_at,
        )

    def _current_version(self, test_case: TestCase) -> TestCaseVersion:
        version = self.session.execute(
            select(TestCaseVersion).where(
                TestCaseVersion.id == test_case.current_version_id,
                TestCaseVersion.test_case_id == test_case.id,
            )
        ).scalar_one_or_none()
        if version is None:
            raise RuntimeError(f"test case {test_case.id} has no current version")
        return version

    def _get_case(
        self,
        test_case_id: uuid.UUID,
        *,
        for_update: bool = False,
        include_deleted: bool = False,
    ) -> TestCase:
        statement = select(TestCase).where(
            TestCase.id == test_case_id,
            TestCase.workspace_id == self.workspace_id,
        )
        if not include_deleted:
            statement = statement.where(TestCase.deleted_at.is_(None))
        if for_update:
            statement = statement.with_for_update()
        test_case = self.session.execute(statement).scalar_one_or_none()
        if test_case is None:
            deleted = self.session.scalar(
                select(TestCase.deleted_at).where(
                    TestCase.id == test_case_id,
                    TestCase.workspace_id == self.workspace_id,
                )
            )
            if deleted is not None:
                raise TestCaseDeletedError(f"test case {test_case_id} is deleted")
            raise ResourceNotFoundError("TEST_CASE", test_case_id)
        return test_case

    def _active_cases(self, work_item_id: uuid.UUID) -> list[TestCase]:
        return list(
            self.session.scalars(
                select(TestCase)
                .where(
                    TestCase.workspace_id == self.workspace_id,
                    TestCase.work_item_id == work_item_id,
                    TestCase.deleted_at.is_(None),
                )
                .order_by(TestCase.sort_order, TestCase.id)
                .with_for_update()
            )
        )

    @staticmethod
    def _apply_order(cases: Sequence[TestCase]) -> None:
        for index, test_case in enumerate(cases):
            test_case.sort_order = index

    def _lock_library(self) -> None:
        workspace = self.session.execute(
            select(WorkspaceSettings.id)
            .where(WorkspaceSettings.id == self.workspace_id)
            .with_for_update()
        ).scalar_one_or_none()
        if workspace is None:
            raise ResourceNotFoundError("WORKSPACE", self.workspace_id)

    def _get_category(self, category_id: uuid.UUID) -> Folder:
        category = self.session.execute(
            select(Folder).where(
                Folder.id == category_id,
                Folder.workspace_id == self.workspace_id,
                Folder.parent_id.is_(None),
                Folder.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if category is None:
            raise CategoryInvariantError("category must be an active root folder")
        return category

    @staticmethod
    def _assert_version(test_case: TestCase, base_version: int) -> None:
        if test_case.version != base_version:
            raise VersionConflictError(
                "TEST_CASE",
                test_case.id,
                base_version,
                test_case.version,
            )

    @staticmethod
    def _assert_work_item_version(work_item: WorkItem, base_version: int) -> None:
        if work_item.version != base_version:
            raise VersionConflictError(
                "WORK_ITEM",
                work_item.id,
                base_version,
                work_item.version,
            )


def _nonblank(value: str | None, field_name: str, *, maximum: int | None = None) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{field_name} cannot be blank")
    clean = value.strip()
    if maximum is not None and len(clean) > maximum:
        raise ValueError(f"{field_name} cannot exceed {maximum} characters")
    return clean


def _optional_nonblank(
    value: str | None,
    field_name: str,
    *,
    maximum: int | None = None,
) -> str | None:
    if value is None:
        return None
    return _nonblank(value, field_name, maximum=maximum)


def _normalized_tag_inputs(values: Sequence[str]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_tag_name(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append((" ".join(unicodedata.normalize("NFKC", value).strip().split()), normalized))
    return result


def _normalized_display_values(values: Sequence[str], *, maximum: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _nonblank(value, "label", maximum=maximum)
        normalized = unicodedata.normalize("NFKC", clean).casefold()
        if normalized not in seen:
            result.append(clean)
            seen.add(normalized)
    return result


def _validate_insertion_order(sort_order: int, sibling_count: int) -> None:
    if not 0 <= sort_order <= sibling_count:
        raise ValueError(f"sort_order must be between 0 and {sibling_count}")


def _work_item_snapshot(work_item: WorkItem) -> dict[str, Any]:
    return {
        "id": str(work_item.id),
        "workItemKey": work_item.work_item_key,
        "categoryId": str(work_item.category_id),
        "summary": work_item.summary,
        "description": work_item.description,
        "ownerLabel": work_item.owner_label,
        "labels": list(work_item.labels),
        "version": work_item.version,
    }


def _case_snapshot(test_case: TestCase, current_version_number: int) -> dict[str, Any]:
    return {
        "id": str(test_case.id),
        "caseKey": test_case.case_key,
        "workItemId": str(test_case.work_item_id),
        "categoryId": str(test_case.category_id),
        "sortOrder": test_case.sort_order,
        "status": test_case.status,
        "currentVersionId": str(test_case.current_version_id),
        "currentVersionNumber": current_version_number,
        "version": test_case.version,
        "deletedAt": test_case.deleted_at.isoformat() if test_case.deleted_at else None,
        "generationManaged": test_case.generation_managed,
        "generationLineageId": (
            str(test_case.generation_lineage_id)
            if test_case.generation_lineage_id is not None
            else None
        ),
        "generationCreatedOutcomeId": (
            str(test_case.generation_created_outcome_id)
            if test_case.generation_created_outcome_id is not None
            else None
        ),
        "generationBaselineVersionId": (
            str(test_case.generation_baseline_version_id)
            if test_case.generation_baseline_version_id is not None
            else None
        ),
        "generationLatestVersionId": (
            str(test_case.generation_latest_version_id)
            if test_case.generation_latest_version_id is not None
            else None
        ),
        "generationReleasedAt": (
            test_case.generation_released_at.isoformat()
            if test_case.generation_released_at is not None
            else None
        ),
        "generationReleaseReason": test_case.generation_release_reason,
        "generationReleaseActor": test_case.generation_release_actor,
        "generationReleaseMutation": test_case.generation_release_mutation,
        "generationReleasedFromVersionId": (
            str(test_case.generation_released_from_version_id)
            if test_case.generation_released_from_version_id is not None
            else None
        ),
        "generationReleasedByVersionId": (
            str(test_case.generation_released_by_version_id)
            if test_case.generation_released_by_version_id is not None
            else None
        ),
    }


def _content_from_snapshot(snapshot: VersionSnapshot) -> TestCaseContent:
    return TestCaseContent(
        summary=snapshot.summary,
        precondition=snapshot.precondition,
        steps=tuple(
            TestStepInput(
                action=step.action,
                expected_result=step.expected_result,
                precondition=step.precondition,
            )
            for step in snapshot.steps
        ),
        expected_result_mode=ExpectedResultMode(snapshot.expected_result_mode),
        expected_result=snapshot.expected_result,
        status=TestCaseStatus(snapshot.status),
        priority=TestCasePriority(snapshot.priority),
        assignee_label=snapshot.assignee_label,
        tag_names=snapshot.tag_names,
        source_type=TestCaseSourceType(snapshot.source_type),
        source_metadata=snapshot.source_metadata,
        generation_reason=snapshot.generation_reason,
    )


def _semantic_content_key(content: TestCaseContent) -> tuple[Any, ...]:
    mode = ExpectedResultMode(content.expected_result_mode)
    return (
        TestCaseStatus(content.status).value,
        content.summary.strip(),
        content.precondition.strip(),
        mode.value,
        content.expected_result.strip() if content.expected_result is not None else None,
        TestCasePriority(content.priority).value,
        _optional_nonblank(content.assignee_label, "assignee_label", maximum=200),
        tuple(sorted(normalized for _, normalized in _normalized_tag_inputs(content.tag_names))),
        tuple(
            (
                step.precondition.strip() if step.precondition is not None else None,
                step.action.strip(),
                step.expected_result.strip() if step.expected_result is not None else None,
            )
            for step in content.steps
        ),
    )


def _snapshot_semantic_content_key(snapshot: VersionSnapshot) -> tuple[Any, ...]:
    return (
        snapshot.status,
        snapshot.summary,
        snapshot.precondition,
        snapshot.expected_result_mode,
        snapshot.expected_result,
        snapshot.priority,
        snapshot.assignee_label,
        tuple(sorted(normalize_tag_name(name) for name in snapshot.tag_names)),
        tuple(
            (step.precondition, step.action, step.expected_result)
            for step in snapshot.steps
        ),
    )


def _version_diff_values(snapshot: VersionSnapshot) -> dict[str, Any]:
    return {
        "status": snapshot.status,
        "summary": snapshot.summary,
        "precondition": snapshot.precondition,
        "expectedResultMode": snapshot.expected_result_mode,
        "expectedResult": snapshot.expected_result,
        "priority": snapshot.priority,
        "assigneeLabel": snapshot.assignee_label,
        "tags": list(snapshot.tag_names),
        "steps": [
            {
                "sequence": step.sequence,
                "precondition": step.precondition,
                "action": step.action,
                "expectedResult": step.expected_result,
            }
            for step in snapshot.steps
        ],
        "sourceType": snapshot.source_type,
        "sourceMetadata": snapshot.source_metadata,
        "generationReason": snapshot.generation_reason,
    }
