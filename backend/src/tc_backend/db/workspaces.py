"""Workspace settings, transactional public-key allocation, and seed data."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tc_backend.db.audit import AuditContext, append_audit
from tc_backend.db.constants import (
    AI_INBOX_FOLDER_ID,
    AI_INBOX_NAME,
    AI_INBOX_NORMALIZED_NAME,
    AI_INBOX_SYSTEM_KIND,
    DEFAULT_WORKSPACE_ID,
    KEY_ENTITY_TYPES,
)
from tc_backend.db.errors import KeyAllocationError, ResourceNotFoundError, VersionConflictError
from tc_backend.db.models import Folder, KeyEntityType, WorkspaceKeyCounter, WorkspaceSettings

PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")


@dataclass(frozen=True, slots=True)
class WorkspacePatch:
    team_name: str | None = None
    case_prefix: str | None = None
    issue_prefix: str | None = None
    default_environment: str | None = None
    generation_quiet_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class AllocatedKey:
    workspace_id: uuid.UUID
    entity_type: KeyEntityType
    key: str
    ordinal: int
    audit_id: uuid.UUID


def ensure_default_workspace(session: Session) -> uuid.UUID:
    """Idempotently install the singleton workspace, counters, and AI inbox."""

    session.execute(
        insert(WorkspaceSettings)
        .values(
            id=DEFAULT_WORKSPACE_ID,
            singleton_key=1,
            team_name="QA Team",
            case_prefix="TC",
            issue_prefix="ISSUE",
            default_environment="staging",
            generation_quiet_minutes=10,
            version=1,
        )
        .on_conflict_do_nothing()
    )
    for entity_type in KEY_ENTITY_TYPES:
        session.execute(
            insert(WorkspaceKeyCounter)
            .values(
                workspace_id=DEFAULT_WORKSPACE_ID,
                entity_type=entity_type,
                next_value=1,
            )
            .on_conflict_do_nothing()
        )
    session.execute(
        insert(Folder)
        .values(
            id=AI_INBOX_FOLDER_ID,
            workspace_id=DEFAULT_WORKSPACE_ID,
            parent_id=None,
            name=AI_INBOX_NAME,
            normalized_name=AI_INBOX_NORMALIZED_NAME,
            sort_order=2_147_483_647,
            version=1,
            system_kind=AI_INBOX_SYSTEM_KIND,
        )
        .on_conflict_do_nothing()
    )
    return DEFAULT_WORKSPACE_ID


class WorkspaceRepository:
    """Transaction-scoped workspace operations; callers own commit/rollback."""

    def __init__(self, session: Session, *, workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def get(self, *, for_update: bool = False) -> WorkspaceSettings:
        statement = select(WorkspaceSettings).where(WorkspaceSettings.id == self.workspace_id)
        if for_update:
            statement = statement.with_for_update()
        settings = self.session.execute(statement).scalar_one_or_none()
        if settings is None:
            raise ResourceNotFoundError("WORKSPACE", self.workspace_id)
        return settings

    def update_settings(
        self,
        *,
        base_version: int,
        patch: WorkspacePatch,
        audit: AuditContext,
    ) -> WorkspaceSettings:
        settings = self.get(for_update=True)
        if settings.version != base_version:
            raise VersionConflictError(
                "WORKSPACE",
                settings.id,
                base_version,
                settings.version,
            )
        changes = _patch_values(patch)
        if not changes:
            raise ValueError("workspace patch must change at least one field")
        before = _settings_snapshot(settings)
        for field, value in changes.items():
            setattr(settings, field, value)
        settings.version += 1
        settings.updated_at = datetime.now(UTC)
        self.session.flush()
        append_audit(
            self.session,
            workspace_id=settings.id,
            entity_type="WORKSPACE_SETTINGS",
            entity_id=settings.id,
            action="UPDATE",
            before=before,
            after=_settings_snapshot(settings),
            context=audit,
        )
        return settings

    def allocate_key(
        self,
        *,
        entity_type: KeyEntityType,
        entity_id: uuid.UUID,
        audit: AuditContext,
    ) -> AllocatedKey:
        # Keep the prefix snapshot and the counter increment inside the same
        # serialized workspace transaction. A concurrent prefix update must
        # either happen entirely before or entirely after this allocation.
        settings = self.get(for_update=True)
        statement = (
            update(WorkspaceKeyCounter)
            .where(
                WorkspaceKeyCounter.workspace_id == self.workspace_id,
                WorkspaceKeyCounter.entity_type == entity_type.value,
            )
            .values(
                next_value=WorkspaceKeyCounter.next_value + 1,
                updated_at=func.now(),
            )
            .returning((WorkspaceKeyCounter.next_value - 1).label("ordinal"))
        )
        ordinal = self.session.execute(statement).scalar_one_or_none()
        if ordinal is None:
            raise KeyAllocationError(f"missing counter for {entity_type.value}")
        prefix = settings.case_prefix if entity_type is KeyEntityType.TEST_CASE else settings.issue_prefix
        public_key = f"{prefix}-{ordinal:06d}"
        audit_id = append_audit(
            self.session,
            workspace_id=self.workspace_id,
            entity_type=entity_type.value,
            entity_id=entity_id,
            action="ALLOCATE_KEY",
            before=None,
            after={"key": public_key, "ordinal": ordinal},
            context=audit,
        )
        return AllocatedKey(
            workspace_id=self.workspace_id,
            entity_type=entity_type,
            key=public_key,
            ordinal=ordinal,
            audit_id=audit_id,
        )


def _patch_values(patch: WorkspacePatch) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if patch.team_name is not None:
        team_name = patch.team_name.strip()
        if not team_name:
            raise ValueError("team_name cannot be blank")
        values["team_name"] = team_name
    if patch.case_prefix is not None:
        values["case_prefix"] = _validated_prefix(patch.case_prefix, "case_prefix")
    if patch.issue_prefix is not None:
        values["issue_prefix"] = _validated_prefix(patch.issue_prefix, "issue_prefix")
    if patch.default_environment is not None:
        environment = patch.default_environment.strip()
        if not environment:
            raise ValueError("default_environment cannot be blank")
        values["default_environment"] = environment
    if patch.generation_quiet_minutes is not None:
        if not 0 <= patch.generation_quiet_minutes <= 1440:
            raise ValueError("generation_quiet_minutes must be between 0 and 1440")
        values["generation_quiet_minutes"] = patch.generation_quiet_minutes
    return values


def _validated_prefix(value: str, field: str) -> str:
    normalized = value.strip().upper()
    if not PREFIX_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must match {PREFIX_PATTERN.pattern}")
    return normalized


def _settings_snapshot(settings: WorkspaceSettings) -> dict[str, Any]:
    return {
        "id": str(settings.id),
        "teamName": settings.team_name,
        "casePrefix": settings.case_prefix,
        "issuePrefix": settings.issue_prefix,
        "defaultEnvironment": settings.default_environment,
        "generationQuietMinutes": settings.generation_quiet_minutes,
        "version": settings.version,
        "updatedAt": settings.updated_at.isoformat(),
    }
