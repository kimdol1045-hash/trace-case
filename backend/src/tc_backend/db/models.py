"""SQLAlchemy models matching the canonical PostgreSQL migrations."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tc_backend.config import ONTOLOGY_EMBEDDING_DIMENSION
from tc_backend.db.base import Base
from tc_backend.db.constants import AI_INBOX_SYSTEM_KIND


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    SUCCEEDED = "SUCCEEDED"
    DEAD = "DEAD"


class KeyEntityType(StrEnum):
    TEST_CASE = "TEST_CASE"
    ISSUE = "ISSUE"


class AuditSourceType(StrEnum):
    UI = "UI"
    API = "API"
    SYSTEM = "SYSTEM"
    SLACK = "SLACK"
    WORKER = "WORKER"


class ExpectedResultMode(StrEnum):
    PER_STEP = "PER_STEP"
    CASE = "CASE"


class TestCaseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


class TestCasePriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TestCaseSourceType(StrEnum):
    MANUAL = "MANUAL"
    SLACK_AI = "SLACK_AI"
    FILE_IMPORT = "FILE_IMPORT"
    API = "API"


class GenerationManagementEventType(StrEnum):
    INITIALIZED = "INITIALIZED"
    RELEASED = "RELEASED"


class GenerationManagementMutation(StrEnum):
    CONTENT_VERSION = "CONTENT_VERSION"
    PARENT_MOVE = "PARENT_MOVE"
    SOFT_DELETE = "SOFT_DELETE"
    RESTORE_DELETED = "RESTORE_DELETED"
    LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE = "LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE"


class GenerationSuggestionDecision(StrEnum):
    APPLIED = "APPLIED"
    IGNORED = "IGNORED"


class CycleStatus(StrEnum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class CycleScopeType(StrEnum):
    WORK_ITEM = "WORK_ITEM"
    LABEL = "LABEL"


class ExecutionPlatform(StrEnum):
    WEB = "WEB"
    AOS = "AOS"
    IOS = "IOS"


class ExecutionStatus(StrEnum):
    UNEXECUTED = "UNEXECUTED"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


class IssueStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class IssueSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AttachmentOwnerType(StrEnum):
    ISSUE = "ISSUE"
    EXECUTION_ATTEMPT = "EXECUTION_ATTEMPT"


class AttachmentStorageStatus(StrEnum):
    PENDING = "PENDING"
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    ERROR = "ERROR"


class AttachmentDeletionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    DELETED = "DELETED"
    FAILED = "FAILED"


class AttachmentReconcileStatus(StrEnum):
    PENDING = "PENDING"
    CONSISTENT = "CONSISTENT"
    ORPHANED_DRIVE_FILE = "ORPHANED_DRIVE_FILE"
    DANGLING_METADATA = "DANGLING_METADATA"
    METADATA_MISMATCH = "METADATA_MISMATCH"
    DELETE_RETRY = "DELETE_RETRY"


class IntegrationProvider(StrEnum):
    GOOGLE_DRIVE = "GOOGLE_DRIVE"
    R2 = "R2"


class OutboxStatus(StrEnum):
    READY = "READY"
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    DEAD = "DEAD"


class KnowledgeSourceKind(StrEnum):
    SOURCE_REVISION = "SOURCE_REVISION"
    LEDGER_ENTITY = "LEDGER_ENTITY"


class KnowledgeOwnerType(StrEnum):
    NODE = "NODE"
    EDGE = "EDGE"


class SlackEventType(StrEnum):
    ROOT = "ROOT"
    REPLY = "REPLY"
    EDIT = "EDIT"
    DELETE = "DELETE"
    THREAD_SYNC = "THREAD_SYNC"


class SlackEventStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"


class SlackParseStatus(StrEnum):
    PENDING = "PENDING"
    PARSED = "PARSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class SlackThreadSyncStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SYNCED = "SYNCED"
    RETRY = "RETRY"
    DEAD = "DEAD"


class GenerationJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class GenerationTrigger(StrEnum):
    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"


class GenerationChangeOutcome(StrEnum):
    INITIAL_GENERATE = "INITIAL_GENERATE"
    SKIP = "SKIP"
    REGENERATE = "REGENERATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'RETRY', 'SUCCEEDED', 'DEAD')",
            name="status_allowed",
        ),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint(
            "(status = 'RUNNING' AND locked_by IS NOT NULL AND locked_at IS NOT NULL) "
            "OR (status <> 'RUNNING' AND locked_by IS NULL AND locked_at IS NULL "
            "AND heartbeat_at IS NULL)",
            name="lock_matches_status",
        ),
        Index(
            "ix_background_jobs_ready",
            text("priority DESC"),
            "run_after",
            "created_at",
            postgresql_where=text("status IN ('QUEUED', 'RETRY')"),
        ),
        Index(
            "ix_background_jobs_running_liveness",
            "heartbeat_at",
            "locked_at",
            postgresql_where=text("status = 'RUNNING'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JobStatus.QUEUED.value, server_default=text("'QUEUED'")
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    locked_by: Mapped[str | None] = mapped_column(String(200))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkspaceSettings(Base):
    __tablename__ = "workspace_settings"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="singleton"),
        CheckConstraint("char_length(btrim(team_name)) > 0", name="team_name_nonblank"),
        CheckConstraint("case_prefix ~ '^[A-Z][A-Z0-9]{1,15}$'", name="case_prefix_format"),
        CheckConstraint("issue_prefix ~ '^[A-Z][A-Z0-9]{1,15}$'", name="issue_prefix_format"),
        CheckConstraint(
            "generation_quiet_minutes BETWEEN 0 AND 1440",
            name="quiet_minutes_range",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("singleton_key", name="uq_workspace_settings_singleton_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    singleton_key: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    team_name: Mapped[str] = mapped_column(
        String(200), nullable=False, default="QA Team", server_default=text("'QA Team'")
    )
    case_prefix: Mapped[str] = mapped_column(
        String(16), nullable=False, default="TC", server_default=text("'TC'")
    )
    issue_prefix: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ISSUE", server_default=text("'ISSUE'")
    )
    default_environment: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="staging",
        server_default=text("'staging'"),
    )
    generation_quiet_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default=text("10")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkspaceKeyCounter(Base):
    __tablename__ = "workspace_key_counters"
    __table_args__ = (
        CheckConstraint("entity_type IN ('TEST_CASE', 'ISSUE')", name="entity_type_allowed"),
        CheckConstraint("next_value >= 1", name="next_value_positive"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    entity_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    next_value: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("entity_type ~ '^[A-Z][A-Z0-9_]*$'", name="entity_type_format"),
        CheckConstraint("action ~ '^[A-Z][A-Z0-9_]*$'", name="action_format"),
        CheckConstraint(
            "source_type IN ('UI', 'API', 'SYSTEM', 'SLACK', 'WORKER')",
            name="source_type_allowed",
        ),
        CheckConstraint('"before" IS NOT NULL OR "after" IS NOT NULL', name="has_snapshot"),
        Index("ix_audit_logs_workspace_occurred", "workspace_id", "occurred_at", "id"),
        Index("ix_audit_logs_entity_occurred", "entity_type", "entity_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_label: Mapped[str | None] = mapped_column(String(200))
    external_actor_id: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(String(500))
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApiIdempotencyReceipt(Base):
    __tablename__ = "api_idempotency_receipts"
    __table_args__ = (
        CheckConstraint("char_length(btrim(operation)) > 0", name="operation_nonblank"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash"),
        CheckConstraint("jsonb_typeof(response) = 'object'", name="response_object"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Folder(Base):
    __tablename__ = "folders"
    __table_args__ = (
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_self_parent"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_nonblank"),
        CheckConstraint("char_length(normalized_name) > 0", name="normalized_name_nonblank"),
        CheckConstraint("sort_order >= 0", name="sort_order_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "system_kind IS NULL OR system_kind = 'AI_GENERATED_INBOX'",
            name="system_kind_allowed",
        ),
        CheckConstraint(
            "system_kind IS NULL OR (parent_id IS NULL AND deleted_at IS NULL "
            "AND deletion_batch_id IS NULL AND name = 'AI 생성함' "
            "AND normalized_name = 'ai 생성함')",
            name="system_shape",
        ),
        CheckConstraint(
            "(deleted_at IS NULL AND deletion_batch_id IS NULL) OR "
            "(deleted_at IS NOT NULL AND deletion_batch_id IS NOT NULL)",
            name="deletion_batch_matches_state",
        ),
        Index(
            "uq_folders_active_root_name",
            "workspace_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND parent_id IS NULL"),
        ),
        Index(
            "uq_folders_active_child_name",
            "workspace_id",
            "parent_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND parent_id IS NOT NULL"),
        ),
        Index(
            "uq_folders_workspace_system_kind",
            "workspace_id",
            "system_kind",
            unique=True,
            postgresql_where=text("system_kind IS NOT NULL"),
        ),
        Index(
            "ix_folders_workspace_parent_sort_deleted",
            "workspace_id",
            "parent_id",
            "sort_order",
            "deleted_at",
        ),
        Index("ix_folders_workspace_deleted", "workspace_id", "deleted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("folders.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    system_kind: Mapped[str | None] = mapped_column(String(40))
    deletion_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def is_system(self) -> bool:
        return self.system_kind == AI_INBOX_SYSTEM_KIND


class FolderAlias(Base):
    __tablename__ = "folder_aliases"
    __table_args__ = (
        CheckConstraint("char_length(btrim(alias)) > 0", name="alias_nonblank"),
        CheckConstraint("char_length(normalized_alias) > 0", name="normalized_alias_nonblank"),
        UniqueConstraint(
            "workspace_id",
            "normalized_alias",
            name="uq_folder_aliases_workspace_normalized_alias",
        ),
        Index("ix_folder_aliases_folder", "workspace_id", "folder_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    folder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("folders.id", ondelete="RESTRICT"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (
        CheckConstraint("char_length(btrim(work_item_key)) > 0", name="key_nonblank"),
        CheckConstraint("char_length(btrim(summary)) > 0", name="summary_nonblank"),
        CheckConstraint("jsonb_typeof(labels) = 'array'", name="labels_array"),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("workspace_id", "work_item_key", name="uq_work_items_workspace_key"),
        UniqueConstraint(
            "workspace_id",
            "id",
            "category_id",
            name="uq_work_items_workspace_id_category",
        ),
        Index(
            "ix_work_items_workspace_category_updated",
            "workspace_id",
            "category_id",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("folders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    work_item_key: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    owner_label: Mapped[str | None] = mapped_column(String(200))
    labels: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TestCase(Base):
    __tablename__ = "test_cases"
    __table_args__ = (
        CheckConstraint(
            "case_key ~ '^[A-Z][A-Z0-9]{1,15}-[0-9]{6,}$'",
            name="key_format",
        ),
        CheckConstraint("status IN ('ACTIVE', 'DEPRECATED')", name="status_allowed"),
        CheckConstraint("sort_order >= 0", name="sort_order_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        ForeignKeyConstraint(
            ["workspace_id", "work_item_id", "category_id"],
            ["work_items.workspace_id", "work_items.id", "work_items.category_id"],
            name="fk_test_cases_work_item_ownership",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["generation_created_outcome_id"],
            ["generation_apply_outcomes.id"],
            name="fk_test_cases_generation_created_outcome",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["id", "generation_baseline_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_test_cases_generation_baseline_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["id", "generation_latest_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_test_cases_generation_latest_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["id", "generation_released_from_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_test_cases_generation_released_from_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["id", "generation_released_by_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_test_cases_generation_released_by_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        # The matching FK is declared by TestCaseVersion after both tables
        # exist in metadata; it enforces that the pointer belongs to this case.
        UniqueConstraint("workspace_id", "case_key", name="uq_test_cases_workspace_key"),
        UniqueConstraint("generation_lineage_id", name="uq_test_cases_generation_lineage"),
        UniqueConstraint(
            "generation_created_outcome_id",
            name="uq_test_cases_generation_created_outcome",
        ),
        UniqueConstraint(
            "work_item_id",
            "active_sort_order",
            name="uq_test_cases_active_work_item_sort",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "ix_test_cases_workspace_category_deleted",
            "workspace_id",
            "category_id",
            "deleted_at",
        ),
        Index(
            "ix_test_cases_work_item_sort_deleted",
            "work_item_id",
            "sort_order",
            "deleted_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    work_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_key: Mapped[str] = mapped_column(String(40), nullable=False)
    current_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=TestCaseStatus.ACTIVE.value,
        server_default=text("'ACTIVE'"),
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    active_sort_order: Mapped[int | None] = mapped_column(
        Integer,
        Computed("CASE WHEN deleted_at IS NULL THEN sort_order ELSE NULL END", persisted=True),
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation_managed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    generation_lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    generation_created_outcome_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    generation_baseline_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    generation_latest_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    generation_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation_release_reason: Mapped[str | None] = mapped_column(String(500))
    generation_release_actor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    generation_release_mutation: Mapped[str | None] = mapped_column(String(40))
    generation_released_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )
    generation_released_by_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TestCaseVersion(Base):
    __tablename__ = "test_case_versions"
    __table_args__ = (
        CheckConstraint("version_number >= 1", name="number_positive"),
        CheckConstraint("status IN ('ACTIVE', 'DEPRECATED')", name="status_allowed"),
        CheckConstraint("char_length(btrim(summary)) > 0", name="summary_nonblank"),
        CheckConstraint("char_length(btrim(precondition)) > 0", name="precondition_nonblank"),
        CheckConstraint(
            "expected_result_mode IN ('PER_STEP', 'CASE')",
            name="expected_mode_allowed",
        ),
        CheckConstraint(
            "(expected_result_mode = 'CASE' AND expected_result IS NOT NULL "
            "AND char_length(btrim(expected_result)) > 0) OR "
            "(expected_result_mode = 'PER_STEP' AND expected_result IS NULL)",
            name="expected_result_shape",
        ),
        CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="priority_allowed",
        ),
        CheckConstraint(
            "source_type IN ('MANUAL', 'SLACK_AI', 'FILE_IMPORT', 'API')",
            name="source_type_allowed",
        ),
        CheckConstraint("jsonb_typeof(source_metadata) = 'object'", name="source_metadata_object"),
        CheckConstraint("char_length(btrim(change_reason)) > 0", name="change_reason_nonblank"),
        CheckConstraint(
            "restored_from_version_id IS NULL OR restored_from_version_id <> id",
            name="not_restore_self",
        ),
        ForeignKeyConstraint(
            ["test_case_id"],
            ["test_cases.id"],
            name="fk_test_case_versions_test_case_id_test_cases",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["test_case_id", "restored_from_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_test_case_versions_restored_from_same_case",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "test_case_id", "version_number", name="uq_test_case_versions_case_number"
        ),
        UniqueConstraint("test_case_id", "id", name="uq_test_case_versions_case_id"),
        Index(
            "ix_test_case_versions_case_created",
            "test_case_id",
            "version_number",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    test_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(String(200), nullable=False)
    precondition: Mapped[str] = mapped_column(Text, nullable=False)
    expected_result_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_result: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=TestCasePriority.MEDIUM.value,
        server_default=text("'MEDIUM'"),
    )
    assignee_label: Mapped[str | None] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    generation_reason: Mapped[str | None] = mapped_column(Text)
    change_reason: Mapped[str] = mapped_column(String(500), nullable=False)
    restored_from_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    copied_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Add the other side of the deferred cycle only after TestCaseVersion has a
# concrete table. PostgreSQL permits the cycle because both FKs are deferred.
TestCase.__table__.append_constraint(  # type: ignore[attr-defined]
    ForeignKeyConstraint(
        [TestCase.__table__.c.id, TestCase.__table__.c.current_version_id],
        [TestCaseVersion.__table__.c.test_case_id, TestCaseVersion.__table__.c.id],
        name="fk_test_cases_current_version_same_case",
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
        use_alter=True,
    )
)


class TestStep(Base):
    __tablename__ = "test_steps"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("char_length(btrim(action)) > 0", name="action_nonblank"),
        CheckConstraint(
            "precondition IS NULL OR char_length(btrim(precondition)) > 0",
            name="precondition_nonblank",
        ),
        CheckConstraint(
            "expected_result IS NULL OR char_length(btrim(expected_result)) > 0",
            name="expected_result_nonblank",
        ),
        UniqueConstraint(
            "test_case_version_id",
            "sequence",
            name="uq_test_steps_version_sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    test_case_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    precondition: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    expected_result: Mapped[str | None] = mapped_column(Text)


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) > 0", name="name_nonblank"),
        CheckConstraint("char_length(normalized_name) > 0", name="normalized_name_nonblank"),
        UniqueConstraint(
            "workspace_id", "normalized_name", name="uq_tags_workspace_normalized_name"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TestCaseTag(Base):
    __tablename__ = "test_case_tags"
    __table_args__ = (Index("ix_test_case_tags_tag_case", "tag_id", "test_case_id"),)

    test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class TestCaseVersionTag(Base):
    __tablename__ = "test_case_version_tags"

    test_case_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class TestCycle(Base):
    __tablename__ = "test_cycles"
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) > 0", name="name_nonblank"),
        CheckConstraint(
            "status IN ('PLANNED', 'IN_PROGRESS', 'COMPLETED')",
            name="status_allowed",
        ),
        CheckConstraint("scope_type IN ('WORK_ITEM', 'LABEL')", name="scope_type_allowed"),
        CheckConstraint("char_length(btrim(scope_value)) > 0", name="scope_value_nonblank"),
        CheckConstraint("jsonb_typeof(scope_values) = 'array'", name="scope_values_array"),
        CheckConstraint(
            "jsonb_array_length(scope_values) BETWEEN 1 AND 50",
            name="scope_values_count",
        ),
        CheckConstraint(
            "scope_values ->> 0 = scope_value",
            name="scope_value_is_first",
        ),
        CheckConstraint("resource_version >= 1", name="resource_version_positive"),
        CheckConstraint("run_number >= 1", name="run_number_positive"),
        CheckConstraint(
            "(status = 'COMPLETED' AND completed_at IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND completed_at IS NULL)",
            name="completed_at_matches_status",
        ),
        Index(
            "ix_test_cycles_workspace_status_scope_starts",
            "workspace_id",
            "status",
            "scope_type",
            "scope_value",
            "starts_at",
        ),
        Index("ix_test_cycles_workspace_updated", "workspace_id", "updated_at", "id"),
        Index(
            "ix_test_cycles_workspace_deleted_updated",
            "workspace_id",
            "deleted_at",
            "updated_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CycleStatus.PLANNED.value,
        server_default=text("'PLANNED'"),
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_value: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_values: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    resource_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    run_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def start_date(self) -> date:
        return self.starts_at.date()


class CycleItem(Base):
    __tablename__ = "cycle_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["test_case_id", "test_case_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_cycle_items_pinned_version_same_case",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("cycle_id", "test_case_id", name="uq_cycle_items_cycle_case"),
        Index("ix_cycle_items_cycle_created", "cycle_id", "created_at", "id"),
        Index("ix_cycle_items_work_item", "work_item_id", "cycle_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_cycles.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    test_case_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assignee_label: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempts"
    __table_args__ = (
        CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        CheckConstraint("cycle_run_number >= 1", name="cycle_run_number_positive"),
        CheckConstraint("platform IN ('WEB', 'AOS', 'IOS')", name="platform_allowed"),
        CheckConstraint(
            "status IN ('UNEXECUTED', 'PASS', 'FAIL', 'BLOCKED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(status = 'UNEXECUTED' AND finished_at IS NULL) OR "
            "(status IN ('PASS', 'FAIL', 'BLOCKED') AND finished_at IS NOT NULL)",
            name="finished_at_matches_status",
        ),
        CheckConstraint(
            "status <> 'FAIL' OR (actual_result IS NOT NULL AND "
            "char_length(btrim(actual_result)) > 0)",
            name="fail_actual_result_required",
        ),
        CheckConstraint(
            "actual_result IS NULL OR char_length(btrim(actual_result)) > 0",
            name="actual_result_nonblank",
        ),
        CheckConstraint("note IS NULL OR char_length(btrim(note)) > 0", name="note_nonblank"),
        CheckConstraint(
            "executor_label IS NULL OR char_length(btrim(executor_label)) > 0",
            name="executor_label_nonblank",
        ),
        UniqueConstraint(
            "cycle_item_id",
            "platform",
            "attempt_no",
            name="uq_execution_attempts_item_platform_number",
        ),
        Index(
            "ix_execution_attempts_item_platform_latest",
            "cycle_item_id",
            "platform",
            text("attempt_no DESC"),
        ),
        Index("ix_execution_attempts_status_finished", "status", "finished_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    cycle_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cycle_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(10), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    cycle_run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    actual_result: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    executor_label: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExecutionStepResult(Base):
    __tablename__ = "execution_step_results"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(
            "status IN ('PASS', 'FAIL', 'BLOCKED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "note IS NULL OR char_length(btrim(note)) > 0",
            name="note_nonblank",
        ),
        UniqueConstraint(
            "execution_attempt_id",
            "sequence",
            name="uq_execution_step_results_attempt_sequence",
        ),
        UniqueConstraint(
            "execution_attempt_id",
            "test_step_id",
            name="uq_execution_step_results_attempt_step",
        ),
        Index(
            "ix_execution_step_results_attempt_sequence",
            "execution_attempt_id",
            "sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    execution_attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("execution_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    test_step_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_steps.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        CheckConstraint(
            "issue_key ~ '^[A-Z][A-Z0-9]{1,15}-[0-9]{6,}$'",
            name="key_format",
        ),
        CheckConstraint("char_length(btrim(summary)) > 0", name="summary_nonblank"),
        CheckConstraint("char_length(btrim(precondition)) > 0", name="precondition_nonblank"),
        CheckConstraint("char_length(btrim(expected_result)) > 0", name="expected_result_nonblank"),
        CheckConstraint("char_length(btrim(actual_result)) > 0", name="actual_result_nonblank"),
        CheckConstraint(
            "status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED', 'REOPENED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="severity_allowed",
        ),
        CheckConstraint("resource_version >= 1", name="resource_version_positive"),
        CheckConstraint(
            "assignee_label IS NULL OR char_length(btrim(assignee_label)) > 0",
            name="assignee_nonblank",
        ),
        CheckConstraint(
            "source_note IS NULL OR char_length(btrim(source_note)) > 0",
            name="source_note_nonblank",
        ),
        UniqueConstraint("workspace_id", "issue_key", name="uq_issues_workspace_key"),
        Index("ix_issues_workspace_status_updated", "workspace_id", "status", "updated_at", "id"),
        Index(
            "ix_issues_workspace_severity_updated", "workspace_id", "severity", "updated_at", "id"
        ),
        Index("ix_issues_workspace_deleted", "workspace_id", "deleted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    issue_key: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(String(200), nullable=False)
    precondition: Mapped[str] = mapped_column(Text, nullable=False)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False)
    actual_result: Mapped[str] = mapped_column(Text, nullable=False)
    source_note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=IssueStatus.OPEN.value, server_default=text("'OPEN'")
    )
    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=IssueSeverity.MEDIUM.value,
        server_default=text("'MEDIUM'"),
    )
    assignee_label: Mapped[str | None] = mapped_column(String(200))
    resource_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IssueStep(Base):
    __tablename__ = "issue_steps"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("char_length(btrim(action)) > 0", name="action_nonblank"),
        CheckConstraint(
            "expected_result IS NULL OR char_length(btrim(expected_result)) > 0",
            name="expected_result_nonblank",
        ),
        UniqueConstraint("issue_id", "sequence", name="uq_issue_steps_issue_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    expected_result: Mapped[str | None] = mapped_column(Text)


class IssueLink(Base):
    __tablename__ = "issue_links"
    __table_args__ = (
        CheckConstraint(
            "test_case_id IS NOT NULL OR cycle_item_id IS NOT NULL OR execution_attempt_id IS NOT NULL",
            name="has_target",
        ),
        CheckConstraint(
            "test_case_version_id IS NULL OR test_case_id IS NOT NULL",
            name="version_requires_case",
        ),
        CheckConstraint(
            "execution_attempt_id IS NULL OR cycle_item_id IS NOT NULL",
            name="attempt_requires_cycle_item",
        ),
        ForeignKeyConstraint(
            ["test_case_id", "test_case_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_issue_links_pinned_version_same_case",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_issue_links_exact_target",
            "issue_id",
            "test_case_id",
            "test_case_version_id",
            "cycle_item_id",
            "execution_attempt_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_issue_links_test_case", "test_case_id", "issue_id"),
        Index("ix_issue_links_cycle_item", "cycle_item_id", "issue_id"),
        Index("ix_issue_links_attempt", "execution_attempt_id", "issue_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("issues.id", ondelete="RESTRICT"), nullable=False
    )
    test_case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT")
    )
    test_case_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    cycle_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cycle_items.id", ondelete="RESTRICT")
    )
    execution_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("execution_attempts.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("owner_type = 'ISSUE'", name="owner_type_allowed"),
        CheckConstraint("char_length(btrim(body)) > 0", name="body_nonblank"),
        CheckConstraint(
            "author_label IS NULL OR char_length(btrim(author_label)) > 0",
            name="author_nonblank",
        ),
        Index("ix_comments_owner_created", "owner_type", "owner_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    owner_type: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'ISSUE'")
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_label: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint("owner_type IN ('ISSUE', 'EXECUTION_ATTEMPT')", name="owner_type_allowed"),
        CheckConstraint("char_length(btrim(file_name)) > 0", name="file_name_nonblank"),
        CheckConstraint("char_length(btrim(logical_path)) > 0", name="logical_path_nonblank"),
        CheckConstraint("char_length(btrim(mime_type)) > 0", name="mime_type_nonblank"),
        CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint(
            "storage_provider IN ('GOOGLE_DRIVE', 'R2')",
            name="storage_provider_allowed",
        ),
        CheckConstraint(
            "storage_status IN ('PENDING', 'AVAILABLE', 'MISSING', 'ERROR')",
            name="storage_status_allowed",
        ),
        CheckConstraint(
            "deletion_status IN ('ACTIVE', 'PENDING', 'DELETED', 'FAILED')",
            name="deletion_status_allowed",
        ),
        CheckConstraint(
            "reconcile_status IN ('PENDING', 'CONSISTENT', 'ORPHANED_DRIVE_FILE', "
            "'DANGLING_METADATA', 'METADATA_MISMATCH', 'DELETE_RETRY')",
            name="reconcile_status_allowed",
        ),
        CheckConstraint("resource_version >= 1", name="resource_version_positive"),
        CheckConstraint(
            "(deleted_at IS NULL AND deletion_status = 'ACTIVE') OR "
            "(deleted_at IS NOT NULL AND deletion_status IN ('PENDING', 'DELETED', 'FAILED'))",
            name="deletion_state_matches_timestamp",
        ),
        UniqueConstraint(
            "storage_provider", "object_key", name="uq_attachments_provider_object_key"
        ),
        Index(
            "ix_attachments_owner_deleted", "workspace_id", "owner_type", "owner_id", "deleted_at"
        ),
        Index("ix_attachments_reconcile", "workspace_id", "reconcile_status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    owner_type: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    drive_file_id: Mapped[str | None] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    logical_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_provider: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'GOOGLE_DRIVE'")
    )
    storage_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'AVAILABLE'")
    )
    deletion_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVE'")
    )
    reconcile_status: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'CONSISTENT'")
    )
    reconcile_detail: Mapped[str | None] = mapped_column(Text)
    resource_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IntegrationSetting(Base):
    """Historical table mapping retained only for existing migration/row compatibility."""

    __tablename__ = "integration_settings"
    __table_args__ = (
        CheckConstraint("provider = 'GOOGLE_DRIVE'", name="provider_allowed"),
        CheckConstraint(
            "status IN ('DISCONNECTED', 'CONNECTED', 'REVOKED', 'ERROR')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(credential_nonce IS NULL AND credential_key_version IS NULL AND "
            "credential_ciphertext IS NULL) OR (credential_nonce IS NOT NULL AND "
            "credential_key_version IS NOT NULL AND credential_key_version >= 1 AND "
            "credential_ciphertext IS NOT NULL)",
            name="credentials_complete",
        ),
        CheckConstraint("jsonb_typeof(settings) = 'object'", name="settings_object"),
        CheckConstraint("resource_version >= 1", name="resource_version_positive"),
        UniqueConstraint(
            "workspace_id", "provider", name="uq_integration_settings_workspace_provider"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'DISCONNECTED'")
    )
    credential_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    credential_key_version: Mapped[int | None] = mapped_column(Integer)
    credential_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    root_folder_id: Mapped[str | None] = mapped_column(String(255))
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    resource_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DriveUsageSnapshot(Base):
    """Historical Drive ledger; no current runtime reads or writes this table."""

    __tablename__ = "drive_usage_snapshots"
    __table_args__ = (
        CheckConstraint("used_bytes >= 0", name="used_nonnegative"),
        CheckConstraint("limit_bytes > 0", name="limit_positive"),
        CheckConstraint(
            "warning_level IN ('NONE', '70', '85', '95', 'EXCEEDED')",
            name="warning_level_allowed",
        ),
        CheckConstraint(
            "(warning_level = 'EXCEEDED' AND used_bytes >= limit_bytes) OR "
            "(warning_level = '95' AND used_bytes < limit_bytes "
            "AND used_bytes::numeric / limit_bytes >= 0.95) OR "
            "(warning_level = '85' AND used_bytes::numeric / limit_bytes >= 0.85 "
            "AND used_bytes::numeric / limit_bytes < 0.95) OR "
            "(warning_level = '70' AND used_bytes::numeric / limit_bytes >= 0.70 "
            "AND used_bytes::numeric / limit_bytes < 0.85) OR "
            "(warning_level = 'NONE' AND used_bytes::numeric / limit_bytes < 0.70)",
            name="warning_matches_usage",
        ),
        Index("ix_drive_usage_workspace_collected", "workspace_id", "collected_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    integration_setting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration_settings.id", ondelete="RESTRICT"), nullable=False
    )
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    limit_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warning_level: Mapped[str] = mapped_column(String(20), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DriveReconciliationRecord(Base):
    """Historical Drive ledger; current object integrity uses attachment status."""

    __tablename__ = "drive_reconciliation_records"
    __table_args__ = (
        CheckConstraint(
            "finding_type IN ('ORPHANED_DRIVE_FILE', 'DANGLING_METADATA')",
            name="finding_type_allowed",
        ),
        CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="status_allowed"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details_object"),
        CheckConstraint(
            "(status = 'OPEN' AND resolved_at IS NULL) OR "
            "(status = 'RESOLVED' AND resolved_at IS NOT NULL)",
            name="resolved_at_matches_status",
        ),
        Index("ix_drive_reconcile_open", "workspace_id", "status", "finding_type", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="SET NULL")
    )
    drive_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    finding_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'OPEN'"))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("aggregate_type ~ '^[A-Z][A-Z0-9_]*$'", name="aggregate_type_format"),
        CheckConstraint("event_type ~ '^[A-Z][A-Z0-9_]*$'", name="event_type_format"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint(
            "status IN ('READY', 'PROCESSING', 'PUBLISHED', 'FAILED', 'DEAD')",
            name="status_allowed",
        ),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "event_type",
            "aggregate_version",
            name="uq_outbox_events_aggregate_event_version",
        ),
        Index(
            "ix_outbox_events_ready",
            "available_at",
            "occurred_at",
            postgresql_where=text("status IN ('READY', 'FAILED')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'READY'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SlackChannelAllowlist(Base):
    __tablename__ = "slack_channel_allowlist"
    __table_args__ = (
        CheckConstraint("char_length(btrim(slack_workspace_id)) > 0", name="workspace_nonblank"),
        CheckConstraint("char_length(btrim(channel_id)) > 0", name="channel_nonblank"),
        CheckConstraint(
            "channel_name IS NULL OR char_length(btrim(channel_name)) > 0",
            name="name_nonblank",
        ),
        CheckConstraint("NOT include_private OR is_private", name="private_explicit"),
        UniqueConstraint(
            "workspace_id",
            "slack_workspace_id",
            "channel_id",
            name="uq_slack_channel_allowlist_workspace_channel",
        ),
        Index(
            "ix_slack_channel_allowlist_workspace_enabled",
            "workspace_id",
            "enabled",
            "slack_workspace_id",
            "channel_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    slack_workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)
    channel_name: Mapped[str | None] = mapped_column(String(200))
    is_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    include_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SlackEvent(Base):
    __tablename__ = "slack_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "slack_workspace_id", "channel_id"],
            [
                "slack_channel_allowlist.workspace_id",
                "slack_channel_allowlist.slack_workspace_id",
                "slack_channel_allowlist.channel_id",
            ],
            name="fk_slack_events_allowlisted_channel",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "event_type IN ('ROOT', 'REPLY', 'EDIT', 'DELETE', 'THREAD_SYNC')",
            name="type_allowed",
        ),
        CheckConstraint("event_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'", name="ts_format"),
        CheckConstraint("event_ts_key = replace(event_ts, '.', '')::bigint", name="ts_key_matches"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        CheckConstraint(
            "status IN ('RECEIVED', 'PROCESSED', 'IGNORED', 'FAILED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(status IN ('PROCESSED', 'IGNORED') AND processed_at IS NOT NULL) OR "
            "(status IN ('RECEIVED', 'FAILED') AND processed_at IS NULL)",
            name="processed_matches_status",
        ),
        CheckConstraint("retry_at IS NULL OR status = 'FAILED'", name="retry_matches_failed"),
        UniqueConstraint("workspace_id", "event_id", name="uq_slack_events_workspace_event_id"),
        UniqueConstraint(
            "workspace_id",
            "slack_workspace_id",
            "channel_id",
            "event_ts",
            name="uq_slack_events_workspace_channel_event_ts",
        ),
        Index("ix_slack_events_processing", "workspace_id", "status", "retry_at", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    slack_workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    event_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    event_ts_key: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SlackEventStatus.RECEIVED.value,
        server_default=text("'RECEIVED'"),
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class SlackWorkItem(Base):
    __tablename__ = "slack_work_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "slack_workspace_id", "channel_id"],
            [
                "slack_channel_allowlist.workspace_id",
                "slack_channel_allowlist.slack_workspace_id",
                "slack_channel_allowlist.channel_id",
            ],
            name="fk_slack_work_items_allowlisted_channel",
            ondelete="RESTRICT",
        ),
        CheckConstraint("message_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'", name="message_ts_format"),
        CheckConstraint(
            "message_ts_key = replace(message_ts, '.', '')::bigint",
            name="message_ts_key_matches",
        ),
        CheckConstraint("source_version >= 0", name="source_version_nonnegative"),
        CheckConstraint(
            "parse_status IN ('PENDING', 'PARSED', 'NEEDS_REVIEW', 'FAILED')",
            name="parse_status_allowed",
        ),
        CheckConstraint(
            "parse_result IS NULL OR jsonb_typeof(parse_result) = 'object'",
            name="parse_result_object",
        ),
        CheckConstraint(
            "(last_activity_ts IS NULL AND last_activity_ts_key IS NULL "
            "AND last_activity_at IS NULL AND quiet_due_at IS NULL) OR "
            "(last_activity_ts IS NOT NULL AND last_activity_ts_key IS NOT NULL "
            "AND last_activity_at IS NOT NULL AND quiet_due_at IS NOT NULL)",
            name="last_activity_complete",
        ),
        CheckConstraint(
            "last_activity_ts IS NULL OR last_activity_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'",
            name="last_activity_ts_format",
        ),
        CheckConstraint(
            "last_activity_ts IS NULL OR "
            "last_activity_ts_key = replace(last_activity_ts, '.', '')::bigint",
            name="last_activity_ts_key_matches",
        ),
        CheckConstraint(
            "quiet_job_id IS NULL OR quiet_due_at IS NOT NULL", name="quiet_job_requires_due"
        ),
        UniqueConstraint(
            "workspace_id",
            "slack_workspace_id",
            "channel_id",
            "message_ts",
            name="uq_slack_work_items_workspace_channel_message",
        ),
        Index(
            "ix_slack_work_items_quiet_due",
            "quiet_due_at",
            "id",
            postgresql_where=text("quiet_due_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    slack_workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)
    message_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    message_ts_key: Mapped[int] = mapped_column(BigInteger, nullable=False)
    permalink: Mapped[str | None] = mapped_column(Text)
    root_seen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    source_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    parse_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SlackParseStatus.PENDING.value,
        server_default=text("'PENDING'"),
    )
    parse_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    last_activity_ts: Mapped[str | None] = mapped_column(String(32))
    last_activity_ts_key: Mapped[int | None] = mapped_column(BigInteger)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quiet_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quiet_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="RESTRICT")
    )
    created_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("slack_events.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SlackThread(Base):
    __tablename__ = "slack_threads"
    __table_args__ = (
        CheckConstraint("thread_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'", name="ts_format"),
        CheckConstraint(
            "thread_ts_key = replace(thread_ts, '.', '')::bigint", name="ts_key_matches"
        ),
        CheckConstraint(
            "sync_status IN ('PENDING', 'RUNNING', 'SYNCED', 'RETRY', 'DEAD')",
            name="status_allowed",
        ),
        CheckConstraint("sync_attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("max_sync_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint(
            "(sync_watermark_ts IS NULL AND sync_watermark_ts_key IS NULL) OR "
            "(sync_watermark_ts IS NOT NULL AND sync_watermark_ts_key IS NOT NULL)",
            name="watermark_complete",
        ),
        CheckConstraint(
            "sync_watermark_ts IS NULL OR sync_watermark_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'",
            name="watermark_ts_format",
        ),
        CheckConstraint(
            "sync_watermark_ts IS NULL OR "
            "sync_watermark_ts_key = replace(sync_watermark_ts, '.', '')::bigint",
            name="watermark_ts_key_matches",
        ),
        CheckConstraint("retry_at IS NULL OR sync_status = 'RETRY'", name="retry_matches_status"),
        CheckConstraint(
            "sync_status <> 'DEAD' OR sync_attempts >= max_sync_attempts",
            name="dead_exhausted",
        ),
        UniqueConstraint("slack_work_item_id", name="uq_slack_threads_work_item"),
        Index("ix_slack_threads_resync", "sync_status", "last_synced_at", "retry_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    slack_work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("slack_work_items.id", ondelete="RESTRICT"), nullable=False
    )
    thread_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    thread_ts_key: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sync_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SlackThreadSyncStatus.PENDING.value,
        server_default=text("'PENDING'"),
    )
    sync_cursor: Mapped[str | None] = mapped_column(Text)
    sync_watermark_ts: Mapped[str | None] = mapped_column(String(32))
    sync_watermark_ts_key: Mapped[int | None] = mapped_column(BigInteger)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_sync_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SlackMessage(Base):
    __tablename__ = "slack_messages"
    __table_args__ = (
        CheckConstraint("message_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'", name="ts_format"),
        CheckConstraint(
            "message_ts_key = replace(message_ts, '.', '')::bigint", name="ts_key_matches"
        ),
        CheckConstraint(
            "author_id IS NULL OR char_length(btrim(author_id)) > 0", name="author_nonblank"
        ),
        UniqueConstraint("thread_id", "message_ts", name="uq_slack_messages_thread_message"),
        Index(
            "uq_slack_messages_thread_root",
            "thread_id",
            unique=True,
            postgresql_where=text("is_root"),
        ),
        Index("ix_slack_messages_thread_chronology", "thread_id", "message_ts_key", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("slack_threads.id", ondelete="RESTRICT"), nullable=False
    )
    message_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    message_ts_key: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[str | None] = mapped_column(String(200))
    permalink: Mapped[str | None] = mapped_column(Text)
    is_root: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    first_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("slack_events.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SlackMessageRevision(Base):
    __tablename__ = "slack_message_revisions"
    __table_args__ = (
        CheckConstraint("source_kind IN ('EVENT', 'THREAD_SYNC')", name="source_kind_allowed"),
        CheckConstraint(
            "(source_kind = 'EVENT' AND source_event_id IS NOT NULL) OR "
            "(source_kind = 'THREAD_SYNC' AND source_event_id IS NULL)",
            name="source_matches_kind",
        ),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("revision_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'", name="ts_format"),
        CheckConstraint(
            "revision = replace(revision_ts, '.', '')::bigint",
            name="revision_matches_ts",
        ),
        CheckConstraint(
            "(is_deleted AND body IS NULL AND deleted_at IS NOT NULL) OR "
            "(NOT is_deleted AND body IS NOT NULL AND deleted_at IS NULL)",
            name="tombstone_shape",
        ),
        CheckConstraint("jsonb_typeof(raw_payload) = 'object'", name="raw_payload_object"),
        UniqueConstraint(
            "message_id", "revision", name="uq_slack_message_revisions_message_revision"
        ),
        UniqueConstraint(
            "message_id",
            "revision_ts",
            name="uq_slack_message_revisions_message_revision_ts",
        ),
        Index(
            "uq_slack_message_revisions_source_event",
            "source_event_id",
            unique=True,
            postgresql_where=text("source_event_id IS NOT NULL"),
        ),
        Index("ix_slack_message_revisions_message_chronology", "message_id", "revision", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("slack_messages.id", ondelete="RESTRICT"), nullable=False
    )
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("slack_events.id", ondelete="RESTRICT")
    )
    source_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="EVENT", server_default=text("'EVENT'")
    )
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SlackParseResult(Base):
    __tablename__ = "slack_parse_results"
    __table_args__ = (
        CheckConstraint("source_version >= 1", name="source_version_positive"),
        CheckConstraint("char_length(btrim(parser_version)) > 0", name="parser_nonblank"),
        CheckConstraint("status IN ('PARSED', 'NEEDS_REVIEW', 'FAILED')", name="status_allowed"),
        CheckConstraint("jsonb_typeof(result) = 'object'", name="result_object"),
        CheckConstraint("jsonb_typeof(missing_fields) = 'array'", name="missing_fields_array"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
        CheckConstraint(
            "(status = 'FAILED' AND error IS NOT NULL) OR (status <> 'FAILED' AND error IS NULL)",
            name="error_matches_status",
        ),
        UniqueConstraint(
            "slack_work_item_id",
            "source_version",
            "parser_version",
            name="uq_slack_parse_results_source_parser",
        ),
        Index(
            "ix_slack_parse_results_work_item_created",
            "slack_work_item_id",
            "source_version",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    slack_work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("slack_work_items.id", ondelete="RESTRICT"), nullable=False
    )
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    missing_fields: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Compatibility with the existing plural settings aggregate naming.
IntegrationSettings = IntegrationSetting


class KnowledgeSourceRecord(Base):
    __tablename__ = "knowledge_source_records"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ('SOURCE_REVISION', 'LEDGER_ENTITY')",
            name="kind_allowed",
        ),
        CheckConstraint(
            "entity_type ~ '^[A-Z][A-Z0-9_]*$'",
            name="entity_type_format",
        ),
        CheckConstraint("char_length(btrim(revision_key)) > 0", name="revision_nonblank"),
        CheckConstraint("char_length(btrim(content_hash)) > 0", name="hash_nonblank"),
        UniqueConstraint(
            "source_kind",
            "entity_type",
            "entity_id",
            "revision_key",
            name="uq_knowledge_source_records_identity",
        ),
        Index(
            "ix_knowledge_source_records_entity",
            "entity_type",
            "entity_id",
            "recorded_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision_key: Mapped[str] = mapped_column(String(200), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    permalink: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeProjectionEvent(Base):
    __tablename__ = "knowledge_projection_events"
    __table_args__ = (
        CheckConstraint(
            "aggregate_type ~ '^[A-Z][A-Z0-9_]*$'",
            name="aggregate_type_format",
        ),
        CheckConstraint("aggregate_version >= 1", name="version_positive"),
        CheckConstraint("jsonb_typeof(result) = 'object'", name="result_object"),
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            name="uq_knowledge_projection_events_aggregate_version",
        ),
        Index(
            "ix_knowledge_projection_events_aggregate",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
        ),
    )

    outbox_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("outbox_events.id", ondelete="RESTRICT"), primary_key=True
    )
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OntologySchema(Base):
    __tablename__ = "ontology_schemas"
    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        Index(
            "uq_ontology_schemas_single_active",
            "active",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_type IN ('WORK_ITEM', 'MENU', 'FEATURE', 'REQUIREMENT', 'DECISION', "
            "'CONSTRAINT', 'OPEN_QUESTION', 'RISK', 'TEST_CASE', 'EXECUTION', "
            "'ISSUE', 'RELEASE')",
            name="node_type_allowed",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'AMBIGUOUS', 'SUPERSEDED', 'RETRACTED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name="valid_window"
        ),
        Index(
            "ix_knowledge_nodes_type_status_entity",
            "node_type",
            "status",
            "entity_type",
            "entity_id",
        ),
        Index(
            "uq_knowledge_nodes_schema_entity",
            "ontology_schema_id",
            "entity_type",
            "entity_id",
            unique=True,
            postgresql_where=text("entity_type IS NOT NULL AND entity_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ontology_schema_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ontology_schemas.id", ondelete="RESTRICT"), nullable=False
    )
    node_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"
    __table_args__ = (
        CheckConstraint(
            "predicate IN ('AFFECTS', 'TARGETS', 'DISCUSSES', 'DERIVED_FROM', "
            "'REFINES', 'SUPERSEDES', 'APPLIES_TO', 'VERIFIES', 'COVERS', "
            "'INSTANCE_OF', 'FOUND_BY')",
            name="predicate_allowed",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'AMBIGUOUS', 'SUPERSEDED', 'RETRACTED')",
            name="status_allowed",
        ),
        CheckConstraint("source_node_id <> target_node_id", name="different_nodes"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name="valid_window"
        ),
        Index(
            "ix_knowledge_edges_source_predicate_target",
            "source_node_id",
            "predicate",
            "target_node_id",
        ),
        Index(
            "uq_knowledge_edges_schema_relation",
            "ontology_schema_id",
            "source_node_id",
            "predicate",
            "target_node_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ontology_schema_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ontology_schemas.id", ondelete="RESTRICT"), nullable=False
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    predicate: Mapped[str] = mapped_column(String(40), nullable=False)
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeEvidence(Base):
    __tablename__ = "knowledge_evidence"
    __table_args__ = (
        CheckConstraint(
            "(knowledge_node_id IS NOT NULL AND knowledge_edge_id IS NULL) OR "
            "(knowledge_node_id IS NULL AND knowledge_edge_id IS NOT NULL)",
            name="exactly_one_owner",
        ),
        UniqueConstraint(
            "source_type",
            "source_id",
            "source_revision",
            "knowledge_node_id",
            "knowledge_edge_id",
            name="uq_knowledge_evidence_source_owner",
        ),
        Index("ix_knowledge_evidence_source", "source_type", "source_id", "source_revision"),
        Index("ix_knowledge_evidence_source_record", "source_record_id"),
        Index(
            "uq_knowledge_evidence_source_node",
            "source_record_id",
            "knowledge_node_id",
            unique=True,
            postgresql_where=text("knowledge_node_id IS NOT NULL"),
        ),
        Index(
            "uq_knowledge_evidence_source_edge",
            "source_record_id",
            "knowledge_edge_id",
            unique=True,
            postgresql_where=text("knowledge_edge_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ontology_schema_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ontology_schemas.id", ondelete="RESTRICT"), nullable=False
    )
    knowledge_node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE")
    )
    knowledge_edge_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_edges.id", ondelete="CASCADE")
    )
    source_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_source_records.id", ondelete="RESTRICT"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(100))
    permalink: Mapped[str | None] = mapped_column(Text)
    excerpt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        CheckConstraint("model = 'bge-m3'", name="model_bge_m3"),
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "model",
            "model_version",
            "content_hash",
            name="uq_knowledge_embeddings_owner_model_version_content",
        ),
        Index("ix_knowledge_embeddings_owner", "owner_type", "owner_id"),
        Index(
            "ix_knowledge_embeddings_bge_m3_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_type: Mapped[str] = mapped_column(String(50), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model: Mapped[str] = mapped_column(
        String(100), nullable=False, default="bge-m3", server_default=text("'bge-m3'")
    )
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(ONTOLOGY_EMBEDDING_DIMENSION), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LightRAGGeneration(Base):
    """Immutable metadata for one fully verified auxiliary generation."""

    __tablename__ = "lightrag_generations"
    __table_args__ = (
        UniqueConstraint(
            "registration_idempotency_key",
            name="uq_lightrag_generations_registration_idempotency",
        ),
        UniqueConstraint(
            "auxiliary_target_digest",
            "workspace",
            name="uq_lightrag_generations_target_workspace",
        ),
        CheckConstraint(
            "generation_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,254}$' AND "
            "provider_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,127}$' AND "
            "workspace ~ '^[A-Za-z][A-Za-z0-9._:-]{0,254}$'",
            name="identifiers",
        ),
        CheckConstraint(
            "auxiliary_target_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "manifest_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "corpus_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "source_ledger_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "projection_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "pgvector_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "registration_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="digests",
        ),
        CheckConstraint(
            "document_count > 0 AND verification_status = 'VERIFIED' AND "
            "jsonb_typeof(metadata) = 'object'",
            name="verified_metadata",
        ),
        CheckConstraint(
            "char_length(btrim(recorded_by)) > 0 AND "
            "char_length(btrim(registration_idempotency_key)) > 0",
            name="registration_shape",
        ),
    )

    generation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace: Mapped[str] = mapped_column(String(255), nullable=False)
    auxiliary_target_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    corpus_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    source_ledger_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    projection_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    pgvector_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="VERIFIED", server_default=text("'VERIFIED'")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    recorded_by: Mapped[str] = mapped_column(String(256), nullable=False)
    registration_idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    registration_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LightRAGActiveGenerationPointer(Base):
    """Singleton, monotonically versioned routing pointer."""

    __tablename__ = "lightrag_active_generation_pointer"
    __table_args__ = (
        CheckConstraint("scope = 'GLOBAL'", name="singleton"),
        CheckConstraint(
            "pointer_version >= 0 AND "
            "((pointer_version = 0 AND active_generation_id IS NULL) OR "
            "(pointer_version > 0 AND active_generation_id IS NOT NULL))",
            name="shape",
        ),
    )

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    pointer_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    active_generation_id: Mapped[str | None] = mapped_column(
        ForeignKey("lightrag_generations.generation_id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LightRAGGenerationTransitionEvent(Base):
    """Append-only applied or rejected CAS transition evidence."""

    __tablename__ = "lightrag_generation_transition_events"
    __table_args__ = (
        UniqueConstraint(
            "sequence", name="uq_lightrag_generation_transition_events_sequence"
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_lightrag_generation_transition_events_idempotency",
        ),
        CheckConstraint(
            "operation IN ('ACTIVATE', 'ROLLBACK') AND "
            "outcome IN ('APPLIED', 'REJECTED')",
            name="kind",
        ),
        CheckConstraint(
            "target_generation_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,254}$' AND "
            "(expected_generation_id IS NULL OR expected_generation_id ~ "
            "'^[A-Za-z][A-Za-z0-9._:-]{0,254}$') AND "
            "(observed_generation_id IS NULL OR observed_generation_id ~ "
            "'^[A-Za-z][A-Za-z0-9._:-]{0,254}$') AND "
            "(after_generation_id IS NULL OR after_generation_id ~ "
            "'^[A-Za-z][A-Za-z0-9._:-]{0,254}$')",
            name="identifiers",
        ),
        CheckConstraint(
            "expected_pointer_version >= 0 AND observed_pointer_version >= 0 AND "
            "committed_pointer_version >= 0",
            name="versions",
        ),
        CheckConstraint(
            "(outcome = 'APPLIED' AND error_code IS NULL AND "
            "observed_generation_id IS NOT DISTINCT FROM expected_generation_id AND "
            "after_generation_id = target_generation_id AND "
            "committed_pointer_version = observed_pointer_version + 1) OR "
            "(outcome = 'REJECTED' AND error_code IS NOT NULL AND "
            "after_generation_id IS NOT DISTINCT FROM observed_generation_id AND "
            "committed_pointer_version = observed_pointer_version)",
            name="outcome",
        ),
        CheckConstraint(
            "operation <> 'ROLLBACK' OR outcome <> 'APPLIED' OR "
            "target_generation_id IS DISTINCT FROM observed_generation_id",
            name="rollback_target",
        ),
        CheckConstraint(
            "char_length(btrim(actor_id)) > 0 AND "
            "char_length(btrim(idempotency_key)) > 0 AND "
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "receipt_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="evidence",
        ),
        Index(
            "uq_lightrag_generation_transition_events_applied_version",
            "committed_pointer_version",
            unique=True,
            postgresql_where=text("outcome = 'APPLIED'"),
        ),
        Index(
            "ix_lightrag_generation_transition_events_target_sequence",
            "target_generation_id",
            "sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    target_generation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_generation_id: Mapped[str | None] = mapped_column(String(255))
    observed_generation_id: Mapped[str | None] = mapped_column(String(255))
    after_generation_id: Mapped[str | None] = mapped_column(String(255))
    expected_pointer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_pointer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_pointer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationVersionSet(Base):
    """Immutable T09 behavior-affecting version-set candidate/baseline."""

    __tablename__ = "generation_version_sets"
    __table_args__ = (
        UniqueConstraint("version", name="uq_generation_version_sets_version"),
        UniqueConstraint("identity_digest", name="uq_generation_version_sets_identity_digest"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "identity_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "cohort_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "population_membership_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "regression_report_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "artifact_manifest_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "(live_attestation_digest IS NULL OR "
            "live_attestation_digest ~ '^sha256:[a-f0-9]{64}$')",
            name="digests",
        ),
        CheckConstraint("jsonb_typeof(identity) = 'object'", name="identity_object"),
        CheckConstraint(
            "NOT synthetic OR (NOT reviewed AND live_attestation_digest IS NULL)",
            name="synthetic_not_reviewed_or_live",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    identity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    identity_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    cohort_id: Mapped[str] = mapped_column(String(256), nullable=False)
    cohort_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    population_membership_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    regression_report_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    live_attestation_digest: Mapped[str | None] = mapped_column(String(71))
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationBaselineChangeRequest(Base):
    """Append-only change request and sealed regression decision."""

    __tablename__ = "generation_baseline_change_requests"
    __table_args__ = (
        UniqueConstraint("version_set_id", name="uq_generation_baseline_change_version_set"),
        UniqueConstraint(
            "requester_principal_id",
            "idempotency_key",
            name="uq_generation_baseline_change_actor_key",
        ),
        CheckConstraint("status IN ('CONTRACT_ONLY', 'CANDIDATE', 'REJECTED')", name="status"),
        CheckConstraint("expected_ledger_version >= 0", name="expected_version"),
        CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "old_identity_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "proposed_identity_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "regression_input_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "regression_report_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "compatibility_matrix_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="digests",
        ),
        CheckConstraint(
            "jsonb_typeof(change_request) = 'object' AND "
            "jsonb_typeof(regression_report) = 'object' AND "
            "jsonb_typeof(compatibility_matrix) = 'object'",
            name="artifacts_object",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    version_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT"), nullable=False
    )
    requester_principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    expected_active_version_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT")
    )
    expected_ledger_version: Mapped[int] = mapped_column(Integer, nullable=False)
    old_identity_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    proposed_identity_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    change_request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    regression_input_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    regression_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    regression_report_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    compatibility_matrix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    compatibility_matrix_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationBaselineApproval(Base):
    """Immutable two-person approval bound to one protected manifest."""

    __tablename__ = "generation_baseline_approvals"
    __table_args__ = (
        UniqueConstraint("version_set_id", name="uq_generation_baseline_approval_version_set"),
        UniqueConstraint("idempotency_key", name="uq_generation_baseline_approval_key"),
        CheckConstraint(
            "approver_one_principal_id <> approver_two_principal_id AND "
            "approver_one_role <> approver_two_role AND "
            "approver_one_role IN ('QUALITY_OWNER', 'RELEASE_OWNER') AND "
            "approver_two_role IN ('QUALITY_OWNER', 'RELEASE_OWNER')",
            name="two_person_roles",
        ),
        CheckConstraint(
            "protected_manifest_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "approver_one_decision_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "approver_two_decision_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "approval_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="digests",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    version_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    protected_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    approver_one_principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    approver_one_role: Mapped[str] = mapped_column(String(40), nullable=False)
    approver_one_decision_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    approver_two_principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    approver_two_role: Mapped[str] = mapped_column(String(40), nullable=False)
    approver_two_decision_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    approval_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationBaselineRuntime(Base):
    """Mutable singleton projection; append-only receipts remain authority."""

    __tablename__ = "generation_baseline_runtime"
    __table_args__ = (
        CheckConstraint("scope = 'GLOBAL'", name="global_scope"),
        CheckConstraint("ledger_version >= 0", name="ledger_version"),
    )

    scope: Mapped[str] = mapped_column(String(20), primary_key=True)
    ledger_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_version_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationBaselineActivation(Base):
    """Append-only atomic activation/supersession receipt."""

    __tablename__ = "generation_baseline_activations"
    __table_args__ = (
        UniqueConstraint("version_set_id", name="uq_generation_baseline_activation_version_set"),
        UniqueConstraint("idempotency_key", name="uq_generation_baseline_activation_key"),
        UniqueConstraint(
            "committed_ledger_version", name="uq_generation_baseline_activation_ledger_version"
        ),
        CheckConstraint(
            "committed_ledger_version = expected_ledger_version + 1", name="ledger_contiguous"
        ),
        CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "protected_manifest_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "receipt_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="digests",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    version_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT"), nullable=False
    )
    previous_version_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT")
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_baseline_approvals.id", ondelete="RESTRICT"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    protected_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    expected_ledger_version: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_ledger_version: Mapped[int] = mapped_column(Integer, nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationBaselineRollback(Base):
    """Append-only rollback receipt bound to a prior activation and reproduction."""

    __tablename__ = "generation_baseline_rollbacks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_generation_baseline_rollback_key"),
        UniqueConstraint(
            "committed_ledger_version", name="uq_generation_baseline_rollback_ledger_version"
        ),
        CheckConstraint(
            "failed_version_set_id <> target_version_set_id AND "
            "committed_ledger_version = expected_ledger_version + 1",
            name="target_and_ledger",
        ),
        CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "target_artifact_manifest_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "reproduction_artifact_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "receipt_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="digests",
        ),
        CheckConstraint("jsonb_typeof(reproduction_report) = 'object'", name="report_object"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    failed_version_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT"), nullable=False
    )
    target_version_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT"), nullable=False
    )
    target_activation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_baseline_activations.id", ondelete="RESTRICT"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    target_artifact_manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    reproduction_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reproduction_artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    expected_ledger_version: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_ledger_version: Mapped[int] = mapped_column(Integer, nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    rolled_back_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationBaselineAuditEvent(Base):
    """Immutable activation/rollback/change audit sequence."""

    __tablename__ = "generation_baseline_audit_events"
    __table_args__ = (
        UniqueConstraint("sequence", name="uq_generation_baseline_audit_sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(
            "event_type IN ('CANDIDATE_RECORDED', 'APPROVAL_RECORDED', "
            "'BASELINE_ACTIVATED', 'ROLLBACK_ACTIVATED')",
            name="event_type",
        ),
        CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND receipt_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="digests",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    version_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    expected_ledger_version: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_ledger_version: Mapped[int] = mapped_column(Integer, nullable=False)
    before_active_version_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT")
    )
    after_active_version_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT")
    )
    receipt_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        CheckConstraint("source_version >= 1", name="source_version_positive"),
        CheckConstraint("generation_version >= 1", name="generation_version_positive"),
        CheckConstraint(
            "generation_trigger IN ('MANUAL', 'AUTOMATIC')",
            name="generation_trigger_allowed",
        ),
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="status_allowed",
        ),
        CheckConstraint("char_length(btrim(provider)) > 0", name="provider_nonblank"),
        CheckConstraint("char_length(btrim(model)) > 0", name="model_nonblank"),
        CheckConstraint("char_length(btrim(prompt_version)) > 0", name="prompt_version_nonblank"),
        CheckConstraint("char_length(btrim(schema_version)) > 0", name="schema_version_nonblank"),
        CheckConstraint("context_item_count >= 0", name="context_count_nonnegative"),
        CheckConstraint(
            "(context_sealed_at IS NULL AND context_item_count = 0 "
            "AND context_digest IS NULL) OR "
            "(context_sealed_at IS NOT NULL AND context_item_count > 0 "
            "AND context_digest ~ '^[0-9a-f]{64}$')",
            name="context_seal_complete",
        ),
        CheckConstraint(
            "(generation_input_capture_state = 'LEGACY_NOT_CAPTURED' "
            "AND generation_input_schema_version IS NULL "
            "AND generation_input IS NULL AND generation_input_digest IS NULL) OR "
            "(generation_input_capture_state = 'SEALED' "
            "AND char_length(btrim(generation_input_schema_version)) > 0 "
            "AND jsonb_typeof(generation_input) = 'object' "
            "AND generation_input_digest ~ '^[0-9a-f]{64}$')",
            name="generation_input_capture_complete",
        ),
        CheckConstraint(
            "(version_set_id IS NULL AND version_set_digest IS NULL) OR "
            "(version_set_id IS NOT NULL AND "
            "version_set_digest ~ '^sha256:[a-f0-9]{64}$')",
            name="version_set_pin_complete",
        ),
        CheckConstraint("result IS NULL OR jsonb_typeof(result) = 'object'", name="result_object"),
        CheckConstraint("error IS NULL OR char_length(btrim(error)) > 0", name="error_nonblank"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint(
            "(retry_of_job_id IS NULL AND root_job_id = id AND attempt_number = 1) OR "
            "(retry_of_job_id IS NOT NULL AND root_job_id <> id AND attempt_number > 1)",
            name="lineage_shape",
        ),
        CheckConstraint(
            "(error_code IS NULL AND error_retryable IS NULL AND error_attempts IS NULL) OR "
            "(error_code IS NOT NULL AND error_retryable IS NOT NULL "
            "AND error_attempts BETWEEN 1 AND 5)",
            name="error_metadata_complete",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ("
            "'GENERATION_CONFIG_INVALID', 'GENERATION_UNSUPPORTED_SCHEMA_VERSION', "
            "'GENERATION_AUTH_REQUIRED', 'GENERATION_AUTH_REJECTED', "
            "'GENERATION_TIMEOUT', 'GENERATION_TRANSPORT_UNAVAILABLE', "
            "'GENERATION_RATE_LIMITED', 'GENERATION_PROVIDER_REJECTED', "
            "'GENERATION_PROVIDER_ERROR', 'GENERATION_INVALID_CONTENT_TYPE', "
            "'GENERATION_RESPONSE_TOO_LARGE', 'GENERATION_MALFORMED_JSON', "
            "'GENERATION_INVALID_RESPONSE', 'GENERATION_SOURCE_STALE', "
            "'GENERATION_SOURCE_UNAVAILABLE', 'GENERATION_WORK_ITEM_DELETED', "
            "'GENERATION_EXECUTION_OUTCOME_UNKNOWN', "
            "'GENERATION_WORKER_DELIVERY_EXHAUSTED')",
            name="error_code_allowed",
        ),
        CheckConstraint(
            "(status = 'QUEUED' AND started_at IS NULL AND completed_at IS NULL "
            "AND result IS NULL AND error IS NULL AND error_code IS NULL) OR "
            "(status = 'RUNNING' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND result IS NULL AND error IS NULL AND error_code IS NULL) OR "
            "(status = 'SUCCEEDED' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND result IS NOT NULL AND error IS NULL AND error_code IS NULL) OR "
            "(status = 'FAILED' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND result IS NULL AND error IS NOT NULL AND error_code IS NOT NULL)",
            name="lifecycle_shape",
        ),
        UniqueConstraint(
            "slack_work_item_id",
            "generation_version",
            name="uq_generation_jobs_work_item_generation_version",
        ),
        UniqueConstraint("retry_of_job_id", name="uq_generation_jobs_retry_of_job_id"),
        Index(
            "ix_generation_jobs_work_item_created",
            "slack_work_item_id",
            "created_at",
            "id",
        ),
        Index("ix_generation_jobs_status_created", "status", "created_at", "id"),
        Index("ix_generation_jobs_root_attempt", "root_job_id", "attempt_number"),
        Index(
            "uq_generation_jobs_automatic_source_version",
            "slack_work_item_id",
            "source_version",
            unique=True,
            postgresql_where=text("generation_trigger = 'AUTOMATIC' AND retry_of_job_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    slack_work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("slack_work_items.id", ondelete="RESTRICT"), nullable=False
    )
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_trigger: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=GenerationTrigger.MANUAL.value,
        server_default=text("'MANUAL'"),
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=GenerationJobStatus.QUEUED.value,
        server_default=text("'QUEUED'"),
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    ontology_schema_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ontology_schemas.id", ondelete="RESTRICT"), nullable=False
    )
    context_item_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    context_digest: Mapped[str | None] = mapped_column(String(64))
    context_sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation_input_capture_state: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="LEGACY_NOT_CAPTURED",
        server_default=text("'LEGACY_NOT_CAPTURED'"),
    )
    generation_input_schema_version: Mapped[str | None] = mapped_column(String(100))
    generation_input: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    generation_input_digest: Mapped[str | None] = mapped_column(String(64))
    version_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_version_sets.id", ondelete="RESTRICT")
    )
    version_set_digest: Mapped[str | None] = mapped_column(String(71))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[str | None] = mapped_column(Text)
    root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    retry_of_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT")
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)
    error_attempts: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationExecutionSchedule(Base):
    """Mutable CAS projection for one automatic generation-job delivery."""

    __tablename__ = "generation_execution_schedules"
    __table_args__ = (
        UniqueConstraint("generation_job_id", name="uq_generation_execution_schedule_job"),
        UniqueConstraint(
            "background_job_id", name="uq_generation_execution_schedule_background_job"
        ),
        CheckConstraint(
            "state IN ('NOT_BEFORE', 'READY', 'LEASED', 'PROVIDER_OUTCOME_UNKNOWN', 'TERMINAL')",
            name="state",
        ),
        CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'LEASED' AND lease_owner IS NULL AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="lease",
        ),
        Index(
            "uq_generation_execution_active_root",
            "root_job_id",
            unique=True,
            postgresql_where=text(
                "state IN ('NOT_BEFORE', 'READY', 'LEASED', 'PROVIDER_OUTCOME_UNKNOWN')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    background_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resource_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider_permit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_delivery_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_call_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_job_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    retry_policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    materialization_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationAttemptEvent(Base):
    """Sanitized append-only root timeline."""

    __tablename__ = "generation_attempt_events"
    __table_args__ = (
        UniqueConstraint(
            "root_job_id", "sequence", name="uq_generation_attempt_event_root_sequence"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_attempt_number: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    retry_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "generation_retry_requests.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    generation_apply_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_applies.id", ondelete="RESTRICT")
    )
    suggestion_materialization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_suggestion_materializations.id", ondelete="RESTRICT")
    )
    notification_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_slack_notification_intents.id", ondelete="RESTRICT")
    )
    notification_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_slack_notification_receipts.id", ondelete="RESTRICT")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationProviderCallPermit(Base):
    __tablename__ = "generation_provider_call_permits"
    __table_args__ = (
        UniqueConstraint(
            "generation_job_id",
            "provider_attempt_number",
            name="uq_generation_provider_permit_attempt",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    execution_schedule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_execution_schedules.id", ondelete="RESTRICT"), nullable=False
    )
    provider_attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    permitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationProviderCallObservation(Base):
    __tablename__ = "generation_provider_call_observations"
    __table_args__ = (
        UniqueConstraint("permit_id", name="uq_generation_provider_observation_permit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    permit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_provider_call_permits.id", ondelete="RESTRICT"), nullable=False
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    safe_provider_code: Mapped[str | None] = mapped_column(String(80))
    http_status: Mapped[int | None] = mapped_column(Integer)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer)
    result_digest: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationRetryRequest(Base):
    __tablename__ = "generation_retry_requests"
    __table_args__ = (
        UniqueConstraint(
            "actor_principal_id",
            "idempotency_key",
            name="uq_generation_retry_request_actor_idempotency",
        ),
        UniqueConstraint("retry_job_id", name="uq_generation_retry_request_retry_job"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    actor_principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_display_label: Mapped[str] = mapped_column(String(200), nullable=False)
    root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    expected_failed_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    expected_attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    context_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    context_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    normalized_reason: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_additional_conditions: Mapped[str] = mapped_column(Text, nullable=False)
    condition_code_points: Mapped[int] = mapped_column(Integer, nullable=False)
    condition_utf8_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    original_condition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    condition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    retry_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationRetryExhaustion(Base):
    __tablename__ = "generation_retry_exhaustions"
    __table_args__ = (
        UniqueConstraint("root_job_id", name="uq_generation_retry_exhaustion_root"),
        UniqueConstraint(
            "terminal_failed_job_id", name="uq_generation_retry_exhaustion_failed_job"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    terminal_failed_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    finality: Mapped[str] = mapped_column(String(40), nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    provider_call_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_job_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str] = mapped_column(String(80), nullable=False)
    exhausted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationMaterializationSchedule(Base):
    __tablename__ = "generation_materialization_schedules"
    __table_args__ = (
        UniqueConstraint("generation_job_id", name="uq_generation_materialization_job"),
        UniqueConstraint("root_job_id", name="uq_generation_materialization_root"),
        UniqueConstraint("background_job_id", name="uq_generation_materialization_background_job"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    background_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resource_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    generation_apply_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_applies.id", ondelete="RESTRICT")
    )
    suggestion_materialization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_suggestion_materializations.id", ondelete="RESTRICT")
    )
    terminal_error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationChangeAssessment(Base):
    """Append-only semantic decision for one canonical Slack source version."""

    __tablename__ = "generation_change_assessments"
    __table_args__ = (
        CheckConstraint(
            "(outcome = 'INITIAL_GENERATE' AND reason_code = 'FIRST_AUTOMATIC_SOURCE') OR "
            "(outcome = 'SKIP' AND reason_code = 'NO_GENERATION_RELEVANT_CHANGE') OR "
            "(outcome = 'REGENERATE' AND reason_code IN "
            "('GENERATION_RELEVANT_FIELDS_CHANGED', 'GENERATION_CONTRACT_CHANGED')) OR "
            "(outcome = 'REVIEW_REQUIRED' AND reason_code IN "
            "('UNKNOWN_EFFECTIVE_STATE', 'AMBIGUOUS_KNOWLEDGE', "
            "'DELETED_OR_RETRACTED_EVIDENCE', 'LEGACY_INPUT_INCOMPATIBLE', "
            "'UNSUPPORTED_COMPARISON_SCHEMA', 'INCOMPATIBLE_CONTRACT_CONFIG', "
            "'CORRUPT_SEAL', 'IMPACT_COMPARISON_UNSAFE'))",
            name="outcome_allowed",
        ),
        CheckConstraint(
            "current_source_version >= 1 AND "
            "(baseline_source_version IS NULL OR "
            "baseline_source_version >= 1 AND baseline_source_version < current_source_version)",
            name="source_versions",
        ),
        CheckConstraint(
            "(fingerprint_schema_version IS NULL OR "
            "char_length(btrim(fingerprint_schema_version)) > 0) AND "
            "(contract_config_schema_version IS NULL OR "
            "char_length(btrim(contract_config_schema_version)) > 0) AND "
            "(baseline_fingerprint IS NULL OR baseline_fingerprint ~ '^[0-9a-f]{64}$') AND "
            "(current_fingerprint IS NULL OR current_fingerprint ~ '^[0-9a-f]{64}$') AND "
            "(baseline_contract_config_digest IS NULL OR "
            "baseline_contract_config_digest ~ '^[0-9a-f]{64}$') AND "
            "(current_contract_config_digest IS NULL OR "
            "current_contract_config_digest ~ '^[0-9a-f]{64}$') AND "
            "(outcome = 'REVIEW_REQUIRED' OR "
            "fingerprint_schema_version = 'generation-impact/1.0.0' AND "
            "contract_config_schema_version = 'generation-impact-config/1.0.0' AND "
            "current_fingerprint IS NOT NULL AND "
            "current_contract_config_digest IS NOT NULL) AND "
            "(outcome NOT IN ('SKIP', 'REGENERATE') OR "
            "baseline_fingerprint IS NOT NULL AND "
            "baseline_contract_config_digest IS NOT NULL) AND "
            "(outcome <> 'SKIP' OR baseline_fingerprint = current_fingerprint AND "
            "baseline_contract_config_digest = current_contract_config_digest) AND "
            "(outcome <> 'REGENERATE' OR baseline_fingerprint <> current_fingerprint OR "
            "baseline_contract_config_digest <> current_contract_config_digest)",
            name="fingerprint_shape",
        ),
        CheckConstraint(
            "(outcome = 'INITIAL_GENERATE' AND predecessor_assessment_id IS NULL AND "
            "baseline_source_version IS NULL AND "
            "materialization_baseline_generation_job_id IS NULL AND "
            "selected_generation_job_id IS NOT NULL AND reused_generation_job_id IS NULL) OR "
            "(outcome = 'SKIP' AND predecessor_assessment_id IS NOT NULL AND "
            "baseline_source_version IS NOT NULL AND "
            "materialization_baseline_generation_job_id IS NOT NULL AND "
            "selected_generation_job_id IS NULL AND "
            "reused_generation_job_id = materialization_baseline_generation_job_id) OR "
            "(outcome = 'REGENERATE' AND predecessor_assessment_id IS NOT NULL AND "
            "baseline_source_version IS NOT NULL AND "
            "materialization_baseline_generation_job_id IS NOT NULL AND "
            "selected_generation_job_id IS NOT NULL AND reused_generation_job_id IS NULL) OR "
            "(outcome = 'REVIEW_REQUIRED' AND selected_generation_job_id IS NULL AND "
            "reused_generation_job_id IS NULL)",
            name="job_shape",
        ),
        CheckConstraint(
            "jsonb_typeof(changed_fields) = 'array' AND "
            "tc_generation_impact_changed_fields_valid(changed_fields) AND "
            'changed_fields <@ \'["$INITIAL_SOURCE", "$REVIEW_REQUIRED", '
            '"slackWorkItem.developmentItem", "slackWorkItem.requestedMenu", '
            '"slackWorkItem.featureDescription", "slackWorkItem.requestDetails", '
            '"slackWorkItem.openQuestions", "activeKnowledge.requirements", '
            '"activeKnowledge.decisions", "activeKnowledge.constraints", '
            '"activeKnowledge.evidence", "retrievalInputs.existingCases", '
            '"retrievalInputs.failedOrBlockedExecutions", "retrievalInputs.openIssues", '
            '"retrievalInputs.extraSources", "categoryCandidates", '
            '"generationPolicy", "contractConfig"]\'::jsonb AND '
            "(outcome <> 'INITIAL_GENERATE' OR "
            "changed_fields = '[\"$INITIAL_SOURCE\"]'::jsonb) AND "
            "(outcome <> 'SKIP' OR changed_fields = '[]'::jsonb) AND "
            "(outcome <> 'REGENERATE' OR jsonb_array_length(changed_fields) > 0 AND "
            "NOT (changed_fields ?| ARRAY['$INITIAL_SOURCE', '$REVIEW_REQUIRED'])) AND "
            "(outcome <> 'REVIEW_REQUIRED' OR "
            "changed_fields = '[\"$REVIEW_REQUIRED\"]'::jsonb)",
            name="changed_fields_array",
        ),
        UniqueConstraint(
            "slack_work_item_id",
            "current_source_version",
            name="uq_generation_change_assessments_source_version",
        ),
        Index(
            "uq_generation_change_assessments_selected_job",
            "selected_generation_job_id",
            unique=True,
            postgresql_where=text("selected_generation_job_id IS NOT NULL"),
        ),
        Index(
            "ix_generation_change_assessments_work_item_version",
            "slack_work_item_id",
            text("current_source_version DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    slack_work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("slack_work_items.id", ondelete="RESTRICT"), nullable=False
    )
    predecessor_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_change_assessments.id", ondelete="RESTRICT")
    )
    baseline_source_version: Mapped[int | None] = mapped_column(Integer)
    current_source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    materialization_baseline_generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT")
    )
    selected_generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT")
    )
    reused_generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT")
    )
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    changed_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    fingerprint_schema_version: Mapped[str | None] = mapped_column(String(100))
    baseline_fingerprint: Mapped[str | None] = mapped_column(String(64))
    current_fingerprint: Mapped[str | None] = mapped_column(String(64))
    contract_config_schema_version: Mapped[str | None] = mapped_column(String(100))
    baseline_contract_config_digest: Mapped[str | None] = mapped_column(String(64))
    current_contract_config_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationContext(Base):
    __tablename__ = "generation_contexts"
    __table_args__ = (
        CheckConstraint(
            "(node_id IS NOT NULL AND edge_id IS NULL "
            "AND node_snapshot IS NOT NULL AND edge_snapshot IS NULL) OR "
            "(node_id IS NULL AND edge_id IS NOT NULL "
            "AND node_snapshot IS NULL AND edge_snapshot IS NOT NULL)",
            name="exactly_one_resource",
        ),
        CheckConstraint(
            "source_kind IN ('SOURCE_REVISION', 'LEDGER_ENTITY')",
            name="source_kind_allowed",
        ),
        CheckConstraint(
            "source_entity_type ~ '^[A-Z][A-Z0-9_]*$'",
            name="source_entity_type_format",
        ),
        CheckConstraint("char_length(btrim(source_revision)) > 0", name="source_revision_nonblank"),
        CheckConstraint("char_length(btrim(source_content_hash)) > 0", name="source_hash_nonblank"),
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint("char_length(btrim(reason)) > 0", name="reason_nonblank"),
        CheckConstraint(
            "node_snapshot IS NULL OR jsonb_typeof(node_snapshot) = 'object'",
            name="node_snapshot_object",
        ),
        CheckConstraint(
            "edge_snapshot IS NULL OR jsonb_typeof(edge_snapshot) = 'object'",
            name="edge_snapshot_object",
        ),
        UniqueConstraint("generation_job_id", "rank", name="uq_generation_contexts_job_rank"),
        UniqueConstraint(
            "generation_job_id",
            "knowledge_evidence_id",
            name="uq_generation_contexts_job_evidence",
        ),
        Index("ix_generation_contexts_job_rank", "generation_job_id", "rank"),
        Index(
            "ix_generation_contexts_source",
            "source_entity_type",
            "source_entity_id",
            "source_revision",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    knowledge_evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_evidence.id", ondelete="RESTRICT"), nullable=False
    )
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="RESTRICT")
    )
    edge_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_edges.id", ondelete="RESTRICT")
    )
    source_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_source_records.id", ondelete="RESTRICT"), nullable=False
    )
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    source_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(200), nullable=False)
    source_content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source_permalink: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    node_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    edge_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationApply(Base):
    __tablename__ = "generation_applies"
    __table_args__ = (
        CheckConstraint("char_length(btrim(idempotency_key)) > 0", name="idempotency_key_nonblank"),
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="request_digest"),
        CheckConstraint("context_digest ~ '^[0-9a-f]{64}$'", name="context_digest"),
        CheckConstraint("result_digest ~ '^[0-9a-f]{64}$'", name="result_digest"),
        CheckConstraint("placement_digest ~ '^[0-9a-f]{64}$'", name="placement_digest"),
        CheckConstraint("jsonb_typeof(receipt) = 'object'", name="receipt_object"),
        UniqueConstraint("generation_job_id", name="uq_generation_applies_generation_job"),
        UniqueConstraint("idempotency_key", name="uq_generation_applies_idempotency_key"),
        Index("ix_generation_applies_parent_work_item", "parent_work_item_id", "applied_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    parent_work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    context_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    placement_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "audit_logs.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    receipt: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationApplyOutcome(Base):
    __tablename__ = "generation_apply_outcomes"
    __table_args__ = (
        CheckConstraint("generated_case_index BETWEEN 0 AND 9", name="index"),
        CheckConstraint("exact_content_key ~ '^[0-9a-f]{64}$'", name="exact_key"),
        CheckConstraint("outcome IN ('CREATED', 'REUSED')", name="outcome"),
        CheckConstraint(
            "match_reason IS NULL OR match_reason IN ('EXACT', 'SEMANTIC')",
            name="match_reason",
        ),
        CheckConstraint(
            "(outcome = 'CREATED' AND test_case_version_id IS NOT NULL "
            "AND match_reason IS NULL AND preserved = false) OR "
            "(outcome = 'REUSED' AND test_case_version_id IS NOT NULL "
            "AND match_reason IS NOT NULL AND preserved = true)",
            name="shape",
        ),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="snapshot_object"),
        UniqueConstraint(
            "generation_apply_id",
            "generated_case_index",
            name="uq_generation_apply_outcomes_apply_index",
        ),
        Index("ix_generation_apply_outcomes_case", "test_case_id", "generation_apply_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    generation_apply_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_applies.id", ondelete="RESTRICT"), nullable=False
    )
    generated_case_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    exact_content_key: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=False
    )
    test_case_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="RESTRICT"), nullable=False
    )
    match_reason: Mapped[str | None] = mapped_column(String(10))
    preserved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationSuggestionMaterialization(Base):
    """Immutable batch receipt for one successful suggestion analysis."""

    __tablename__ = "generation_suggestion_materializations"
    __table_args__ = (
        CheckConstraint(
            "candidate_count >= 0 AND suggestion_count >= 0 AND no_change_count >= 0 "
            "AND suggestion_count + no_change_count = candidate_count",
            name="counts",
        ),
        CheckConstraint("result_digest ~ '^[0-9a-f]{64}$'", name="digest"),
        CheckConstraint("jsonb_typeof(receipt) = 'object'", name="receipt"),
        UniqueConstraint(
            "generation_job_id",
            name="uq_generation_suggestion_materializations_job",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    generation_root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    generation_change_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_change_assessments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    suggestion_count: Mapped[int] = mapped_column(Integer, nullable=False)
    no_change_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    materialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationSlackNotificationIntent(Base):
    """Frozen automatic-generation terminal decision and Slack message."""

    __tablename__ = "generation_slack_notification_intents"
    __table_args__ = (
        UniqueConstraint(
            "generation_job_id",
            name="uq_generation_slack_notification_intents_generation_job",
        ),
        UniqueConstraint(
            "notification_key",
            name="uq_generation_slack_notification_intents_notification_key",
        ),
        UniqueConstraint(
            "client_message_id",
            name="uq_generation_slack_notification_intents_client_message_id",
        ),
        CheckConstraint("source_version >= 1 AND attempt_number >= 1", name="source_attempt"),
        CheckConstraint("terminal_status IN ('SUCCEEDED', 'FAILED')", name="terminal_status"),
        CheckConstraint(
            "notification_kind IN ('SUCCESS_APPLIED', 'SUCCESS_SUGGESTIONS', 'FAILURE')",
            name="kind",
        ),
        CheckConstraint(
            "delivery_disposition IN ('POST', 'SUPPRESSED_STALE_SOURCE', "
            "'SUPPRESSED_SOURCE_UNAVAILABLE', 'SUPPRESSED_CONFIGURATION')",
            name="disposition",
        ),
        CheckConstraint("message_digest ~ '^[0-9a-f]{64}$'", name="message_digest"),
        CheckConstraint("jsonb_typeof(message_snapshot) = 'object'", name="message_snapshot"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    slack_work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("slack_work_items.id", ondelete="RESTRICT"), nullable=False
    )
    generation_root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    generation_apply_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_applies.id", ondelete="RESTRICT")
    )
    generation_change_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_change_assessments.id", ondelete="RESTRICT")
    )
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    terminal_status: Mapped[str] = mapped_column(String(20), nullable=False)
    notification_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    delivery_disposition: Mapped[str] = mapped_column(String(40), nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(80))
    slack_workspace_id: Mapped[str] = mapped_column(String(80), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)
    root_message_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    thread_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    template_version: Mapped[str] = mapped_column(String(100), nullable=False)
    locale: Mapped[str] = mapped_column(String(10), nullable=False)
    message_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    link_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    ui_url: Mapped[str | None] = mapped_column(Text)
    notification_key: Mapped[str] = mapped_column(String(512), nullable=False)
    client_message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    message_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationSlackNotificationDeliveryAttempt(Base):
    """Committed permit for exactly one Slack write or reconciliation read."""

    __tablename__ = "generation_slack_notification_delivery_attempts"
    __table_args__ = (
        UniqueConstraint(
            "intent_id",
            "background_job_id",
            "background_job_attempt",
            "operation",
            name="uq_generation_slack_notification_delivery_attempts_permit",
        ),
        CheckConstraint("background_job_attempt >= 1", name="job_attempt"),
        CheckConstraint("operation IN ('POST', 'RECONCILE')", name="operation"),
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="request_digest"),
        Index(
            "ix_generation_slack_notification_delivery_attempts_intent",
            "intent_id",
            "prepared_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    intent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_slack_notification_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    background_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    background_job_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(200), nullable=False)
    lease_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationSlackNotificationDeliveryObservation(Base):
    """Sanitized result of one permitted provider operation."""

    __tablename__ = "generation_slack_notification_delivery_observations"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            name="uq_generation_slack_notification_delivery_observations_attempt",
        ),
        CheckConstraint(
            "classification IN ('ACKNOWLEDGED', 'DEFINITIVE_NO_DELIVERY', "
            "'RATE_LIMITED', 'AMBIGUOUS', 'RECONCILED_FOUND', "
            "'RECONCILED_NOT_FOUND')",
            name="classification",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "generation_slack_notification_delivery_attempts.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_code: Mapped[str | None] = mapped_column(String(80))
    http_status: Mapped[int | None] = mapped_column(Integer)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer)
    safe_diagnostic: Mapped[str | None] = mapped_column(String(80))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationSlackNotificationReceipt(Base):
    """One immutable local terminal delivery truth per intent."""

    __tablename__ = "generation_slack_notification_receipts"
    __table_args__ = (
        UniqueConstraint(
            "intent_id",
            name="uq_generation_slack_notification_receipts_intent",
        ),
        UniqueConstraint(
            "slack_workspace_id",
            "channel_id",
            "message_ts",
            name="uq_generation_slack_notification_receipts_provider_message",
        ),
        CheckConstraint(
            "terminal_outcome IN ('DELIVERED', 'SUPPRESSED', 'DEAD')",
            name="terminal_outcome",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    intent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_slack_notification_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "generation_slack_notification_delivery_attempts.id",
            ondelete="RESTRICT",
        )
    )
    terminal_outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    proof: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(80))
    slack_workspace_id: Mapped[str | None] = mapped_column(String(80))
    channel_id: Mapped[str | None] = mapped_column(String(80))
    root_message_ts: Mapped[str | None] = mapped_column(String(32))
    message_ts: Mapped[str | None] = mapped_column(String(32))
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    message_digest: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationSuggestionTarget(Base):
    """Server-issued, append-only authority for one released lineage target."""

    __tablename__ = "generation_suggestion_targets"
    __table_args__ = (
        CheckConstraint(
            "suggestion_target_ref ~ '^generation_suggestion_target_[A-Za-z0-9_-]+$'",
            name="ref",
        ),
        CheckConstraint(
            "baseline_case_version >= 1 AND baseline_case_resource_version >= 1",
            name="versions",
        ),
        CheckConstraint("jsonb_typeof(target_snapshot) = 'object'", name="snapshot"),
        CheckConstraint("target_digest ~ '^[0-9a-f]{64}$'", name="digest"),
        UniqueConstraint("suggestion_target_ref", name="uq_generation_suggestion_targets_ref"),
        UniqueConstraint(
            "source_change_assessment_id",
            "generation_lineage_id",
            name="uq_generation_suggestion_targets_assessment_lineage",
        ),
        UniqueConstraint(
            "selected_root_job_id",
            "test_case_id",
            name="uq_generation_suggestion_targets_root_case",
        ),
        ForeignKeyConstraint(
            ["test_case_id", "baseline_case_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_generation_suggestion_targets_case_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "ix_generation_suggestion_targets_case",
            "test_case_id",
            text("created_at DESC"),
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    slack_work_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("slack_work_items.id", ondelete="RESTRICT"), nullable=False
    )
    suggestion_target_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    source_change_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_change_assessments.id", ondelete="RESTRICT"), nullable=False
    )
    selected_root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=False
    )
    generation_lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    generation_created_outcome_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_apply_outcomes.id", ondelete="RESTRICT"), nullable=False
    )
    baseline_case_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    baseline_case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_case_resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationSuggestion(Base):
    """Immutable proposal, including baseline/proposal/diff snapshot seals."""

    __tablename__ = "generation_suggestions"
    __table_args__ = (
        UniqueConstraint("suggestion_target_id", name="uq_generation_suggestions_target"),
        UniqueConstraint(
            "source_change_assessment_id",
            "generation_lineage_id",
            name="uq_generation_suggestions_assessment_lineage",
        ),
        UniqueConstraint(
            "suggestion_target_id",
            "source_generation_job_id",
            "generated_case_index",
            name="uq_generation_suggestions_source_candidate",
        ),
        UniqueConstraint("supersedes_suggestion_id", name="uq_generation_suggestions_supersedes"),
        ForeignKeyConstraint(
            ["test_case_id", "baseline_case_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_generation_suggestions_case_version",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("generated_case_index BETWEEN 0 AND 9", name="index"),
        CheckConstraint(
            "baseline_case_version >= 1 AND baseline_case_resource_version >= 1",
            name="versions",
        ),
        CheckConstraint(
            "exact_content_key ~ '^[0-9a-f]{64}$' AND "
            "context_digest ~ '^[0-9a-f]{64}$' AND result_digest ~ '^[0-9a-f]{64}$' AND "
            "baseline_digest ~ '^[0-9a-f]{64}$' AND proposed_digest ~ '^[0-9a-f]{64}$' AND "
            "diff_digest ~ '^[0-9a-f]{64}$'",
            name="digests",
        ),
        CheckConstraint(
            "diff_schema_version = 'generation-suggestion-diff/1.0.0'",
            name="diff_schema",
        ),
        CheckConstraint(
            "jsonb_typeof(baseline_snapshot) = 'object' AND "
            "jsonb_typeof(proposed_snapshot) = 'object' AND "
            "jsonb_typeof(diff_snapshot) = 'object'",
            name="snapshots",
        ),
        Index(
            "ix_generation_suggestions_case",
            "test_case_id",
            text("created_at DESC"),
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=False
    )
    generation_lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    suggestion_target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_suggestion_targets.id", ondelete="RESTRICT"), nullable=False
    )
    source_generation_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    source_root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    source_change_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_change_assessments.id", ondelete="RESTRICT"), nullable=False
    )
    generated_case_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    exact_content_key: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_case_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    baseline_case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_case_resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    context_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    baseline_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    proposed_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    diff_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    baseline_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_suggestion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_suggestions.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationSuggestionResolution(Base):
    """Append-only human apply/ignore decision receipt."""

    __tablename__ = "generation_suggestion_resolutions"
    __table_args__ = (
        UniqueConstraint("suggestion_id", name="uq_generation_suggestion_resolutions_suggestion"),
        CheckConstraint("decision IN ('APPLIED', 'IGNORED')", name="decision"),
        CheckConstraint("char_length(btrim(reason)) > 0", name="reason"),
        CheckConstraint(
            "observed_case_resource_version >= 1 AND jsonb_typeof(actor) = 'object' AND "
            "jsonb_typeof(receipt) = 'object' AND "
            "((decision = 'APPLIED' AND applied_case_version_id IS NOT NULL) OR "
            "(decision = 'IGNORED' AND applied_case_version_id IS NULL))",
            name="shape",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_suggestions.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    actor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    observed_case_resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_case_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="RESTRICT"), nullable=False
    )
    applied_case_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_case_versions.id", ondelete="RESTRICT")
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("audit_logs.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"),
        nullable=False,
    )
    receipt: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TestCaseGenerationManagementEvent(Base):
    __tablename__ = "test_case_generation_management_events"
    __table_args__ = (
        CheckConstraint("event_type IN ('INITIALIZED', 'RELEASED')", name="type"),
        CheckConstraint(
            "(event_type = 'INITIALIZED' AND released_at IS NULL "
            "AND release_reason IS NULL AND release_actor IS NULL "
            "AND release_mutation IS NULL AND released_from_version_id IS NULL "
            "AND released_by_version_id IS NULL) OR "
            "(event_type = 'RELEASED' AND released_at IS NOT NULL "
            "AND char_length(btrim(release_reason)) > 0 "
            "AND jsonb_typeof(release_actor) = 'object' "
            "AND release_mutation IN ('CONTENT_VERSION', 'PARENT_MOVE', "
            "'SOFT_DELETE', 'RESTORE_DELETED', "
            "'LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE') "
            "AND released_from_version_id IS NOT NULL "
            "AND ((release_mutation = 'CONTENT_VERSION' "
            "AND released_by_version_id IS NOT NULL) OR "
            "(release_mutation IN ('PARENT_MOVE', 'SOFT_DELETE', 'RESTORE_DELETED') "
            "AND released_by_version_id IS NULL) OR "
            "release_mutation = 'LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE'))",
            name="shape",
        ),
        UniqueConstraint(
            "test_case_id",
            "event_type",
            name="uq_test_case_generation_management_events_case_type",
        ),
        Index(
            "ix_test_case_generation_management_events_lineage",
            "lineage_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_cases.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_outcome_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "generation_apply_outcomes.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    baseline_generated_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "test_case_versions.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    latest_generated_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "test_case_versions.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(500))
    release_actor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    release_mutation: Mapped[str | None] = mapped_column(String(40))
    released_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "test_case_versions.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    released_by_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "test_case_versions.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationQualityCandidate(Base):
    """Immutable private T08 source plus its verified public E4 envelope."""

    __tablename__ = "generation_quality_candidates"
    __table_args__ = (
        UniqueConstraint(
            "id", "workspace_id", name="uq_generation_quality_candidates_id_workspace"
        ),
        UniqueConstraint(
            "workspace_id",
            "candidate_key",
            name="uq_generation_quality_candidates_workspace_key",
        ),
        UniqueConstraint(
            "workspace_id",
            "import_idempotency_key",
            name="uq_generation_quality_candidates_workspace_idempotency",
        ),
        CheckConstraint("candidate_key ~ '^review-candidate:[a-f0-9]{64}$'", name="key"),
        CheckConstraint(
            "import_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,254}$'",
            name="idempotency",
        ),
        CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "base_artifact_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "base_dataset_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "base_source_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "base_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "root_set_seal ~ '^hmac-sha256:[a-f0-9]{64}$' AND "
            "envelope_payload_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "envelope_artifact_fingerprint ~ '^sha256:[a-f0-9]{64}$'",
            name="digests",
        ),
        CheckConstraint(
            "capture_environment IN ('staging', 'production') AND "
            "char_length(btrim(capture_revision)) > 0 AND "
            "char_length(btrim(privacy_policy_version)) > 0 AND "
            "privacy_scan_status = 'CLEAN'",
            name="capture_privacy",
        ),
        CheckConstraint(
            "jsonb_typeof(snapshot) = 'object' AND "
            "jsonb_typeof(declared_counts) = 'object' AND "
            "jsonb_typeof(classification_distribution) = 'object' AND "
            "snapshot ->> 'artifactKind' = "
            "'AUTOMATIC_GENERATION_QUALITY_EVALUATION_INPUT' AND "
            "snapshot ->> 'profile' = 'CANDIDATE' AND "
            "snapshot #>> '{dataset,kind}' = 'OPERATIONAL_GOLDEN' AND "
            "snapshot #>> '{dataset,synthetic}' = 'false' AND "
            "snapshot #>> '{dataset,review,status}' = 'UNREVIEWED' AND "
            "snapshot #>> '{liveGate,status}' = 'NOT_RUN' AND "
            "jsonb_typeof(review_envelope) = 'object' AND "
            "review_envelope ->> 'artifactKind' = 'REVIEW_CANDIDATE' AND "
            "review_envelope ->> 'contractVersion' = 'review-candidate-envelope/1.0.0' AND "
            "review_envelope #>> '{candidate,candidateKey}' = candidate_key AND "
            "review_envelope #>> '{fingerprints,payloadFingerprint}' = "
            "envelope_payload_fingerprint AND "
            "review_envelope #>> '{fingerprints,artifactFingerprint}' = "
            "envelope_artifact_fingerprint AND "
            "review_envelope #>> '{sanitization,status}' = 'CLEAN' AND "
            "review_envelope #>> '{review,status}' = 'UNREVIEWED' AND "
            "review_envelope #>> '{attestation,status}' = 'NOT_RUN' AND "
            "review_envelope #>> '{certification,status}' = 'NOT_CERTIFIED'",
            name="snapshot_boundary",
        ),
        Index(
            "ix_generation_quality_candidates_workspace_created",
            "workspace_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_settings.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_key: Mapped[str] = mapped_column(String(89), nullable=False)
    import_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    base_artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    base_dataset_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    base_source_snapshot_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    base_snapshot_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    root_set_seal: Mapped[str] = mapped_column(String(76), nullable=False)
    capture_environment: Mapped[str] = mapped_column(String(20), nullable=False)
    capture_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    privacy_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    privacy_scan_status: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    review_envelope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    envelope_payload_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    envelope_artifact_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    declared_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    classification_distribution: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationQualityCandidateRoot(Base):
    """Private mapping from an opaque review candidate to authoritative root jobs."""

    __tablename__ = "generation_quality_candidate_roots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_id", "workspace_id"],
            [
                "generation_quality_candidates.id",
                "generation_quality_candidates.workspace_id",
            ],
            name="fk_generation_quality_candidate_roots_candidate_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "candidate_id", "position", name="uq_generation_quality_candidate_roots_position"
        ),
        UniqueConstraint(
            "candidate_id", "root_job_id", name="uq_generation_quality_candidate_roots_root"
        ),
        CheckConstraint("position >= 0", name="position"),
        Index("ix_generation_quality_candidate_roots_private_root", "root_job_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    root_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationQualityReviewDecision(Base):
    """Append-only credentialed reviewer decision against one sealed snapshot."""

    __tablename__ = "generation_quality_review_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_id", "workspace_id"],
            [
                "generation_quality_candidates.id",
                "generation_quality_candidates.workspace_id",
            ],
            name="fk_generation_quality_review_decisions_candidate_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "candidate_id",
            "workspace_id",
            name="uq_generation_quality_review_decisions_identity",
        ),
        UniqueConstraint(
            "candidate_id",
            "principal_id",
            name="uq_generation_quality_review_decisions_candidate_principal",
        ),
        UniqueConstraint(
            "workspace_id",
            "principal_id",
            "idempotency_key",
            name="uq_generation_quality_review_decisions_principal_idempotency",
        ),
        CheckConstraint(
            "principal_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,127}$' AND "
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,254}$'",
            name="identifiers",
        ),
        CheckConstraint(
            "decision IN ('APPROVE', 'REJECT') AND "
            "((decision = 'APPROVE' AND reason_code IS NULL) OR "
            "(decision = 'REJECT' AND reason_code IN ("
            "'QUALITY_EVIDENCE_INSUFFICIENT', 'PRIVACY_FENCE_FAILED', "
            "'SOURCE_DRIFT', 'TAMPER_DETECTED', 'OTHER_POLICY_FAILURE'))) ",
            name="shape",
        ),
        CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "observed_base_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "decision_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="digests",
        ),
        Index(
            "ix_generation_quality_review_decisions_candidate_time",
            "candidate_id",
            "decided_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    observed_base_snapshot_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    decision_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationQualityApproval(Base):
    """One immutable approval aggregate created from exactly two principals."""

    __tablename__ = "generation_quality_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_id", "workspace_id"],
            [
                "generation_quality_candidates.id",
                "generation_quality_candidates.workspace_id",
            ],
            name="fk_generation_quality_approvals_candidate_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["decision_one_id", "candidate_id", "workspace_id"],
            [
                "generation_quality_review_decisions.id",
                "generation_quality_review_decisions.candidate_id",
                "generation_quality_review_decisions.workspace_id",
            ],
            name="fk_generation_quality_approvals_decision_one",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["decision_two_id", "candidate_id", "workspace_id"],
            [
                "generation_quality_review_decisions.id",
                "generation_quality_review_decisions.candidate_id",
                "generation_quality_review_decisions.workspace_id",
            ],
            name="fk_generation_quality_approvals_decision_two",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("candidate_id", name="uq_generation_quality_approvals_candidate"),
        CheckConstraint(
            "decision_one_id <> decision_two_id AND "
            "approver_one_principal_id <> approver_two_principal_id",
            name="two_person",
        ),
        CheckConstraint(
            "protected_base_artifact_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "protected_base_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "review_authority_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "approval_artifact_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="digests",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision_one_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision_two_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approver_one_principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    approver_two_principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    protected_base_artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    protected_base_snapshot_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    review_authority_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    approval_artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationQualityReviewerAuthority(Base):
    """Installed, immutable reviewer principals; credential hashes are never public DTOs."""

    __tablename__ = "generation_quality_reviewer_authorities"
    __table_args__ = (
        CheckConstraint(
            "principal_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,127}$' AND "
            "credential_hash ~ '^sha256:[a-f0-9]{64}$' AND "
            "authority_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="shape",
        ),
    )

    principal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    credential_hash: Mapped[str] = mapped_column(String(71), unique=True, nullable=False)
    authority_digest: Mapped[str] = mapped_column(String(71), unique=True, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
