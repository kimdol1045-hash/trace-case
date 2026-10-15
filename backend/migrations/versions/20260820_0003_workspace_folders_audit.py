"""Workspace settings, transactional keys, audit ledger, and folder tree.

Revision ID: 20260820_0003
Revises: 20260820_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260820_0003"
down_revision: str | None = "20260820_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_WORKSPACE_ID = "00000000-0000-7000-8000-000000000100"
AI_INBOX_FOLDER_ID = "00000000-0000-7000-8000-000000000101"


def upgrade() -> None:
    op.create_table(
        "workspace_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("singleton_key", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("team_name", sa.String(length=200), server_default=sa.text("'QA Team'"), nullable=False),
        sa.Column("case_prefix", sa.String(length=16), server_default=sa.text("'TC'"), nullable=False),
        sa.Column("issue_prefix", sa.String(length=16), server_default=sa.text("'ISSUE'"), nullable=False),
        sa.Column(
            "default_environment",
            sa.String(length=200),
            server_default=sa.text("'staging'"),
            nullable=False,
        ),
        sa.Column(
            "generation_quiet_minutes",
            sa.Integer(),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("singleton_key = 1", name="ck_workspace_settings_singleton"),
        sa.CheckConstraint("char_length(btrim(team_name)) > 0", name="ck_workspace_settings_team_name_nonblank"),
        sa.CheckConstraint(
            "case_prefix ~ '^[A-Z][A-Z0-9]{1,15}$'",
            name="ck_workspace_settings_case_prefix_format",
        ),
        sa.CheckConstraint(
            "issue_prefix ~ '^[A-Z][A-Z0-9]{1,15}$'",
            name="ck_workspace_settings_issue_prefix_format",
        ),
        sa.CheckConstraint(
            "generation_quiet_minutes BETWEEN 0 AND 1440",
            name="ck_workspace_settings_quiet_minutes_range",
        ),
        sa.CheckConstraint("version >= 1", name="ck_workspace_settings_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_settings"),
        sa.UniqueConstraint("singleton_key", name="uq_workspace_settings_singleton_key"),
    )

    op.create_table(
        "workspace_key_counters",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("next_value", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_type IN ('TEST_CASE', 'ISSUE')",
            name="ck_workspace_key_counters_entity_type_allowed",
        ),
        sa.CheckConstraint("next_value >= 1", name="ck_workspace_key_counters_next_value_positive"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace_settings.id"],
            name="fk_workspace_key_counters_workspace_id_workspace_settings",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "entity_type",
            name="pk_workspace_key_counters",
        ),
    )

    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("actor_label", sa.String(length=200), nullable=True),
        sa.Column("external_actor_id", sa.String(length=200), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_type ~ '^[A-Z][A-Z0-9_]*$'",
            name="ck_audit_logs_entity_type_format",
        ),
        sa.CheckConstraint("action ~ '^[A-Z][A-Z0-9_]*$'", name="ck_audit_logs_action_format"),
        sa.CheckConstraint(
            "source_type IN ('UI', 'API', 'SYSTEM', 'SLACK', 'WORKER')",
            name="ck_audit_logs_source_type_allowed",
        ),
        sa.CheckConstraint(
            '"before" IS NOT NULL OR "after" IS NOT NULL',
            name="ck_audit_logs_has_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace_settings.id"],
            name="fk_audit_logs_workspace_id_workspace_settings",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index(
        "ix_audit_logs_workspace_occurred",
        "audit_logs",
        ["workspace_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_audit_logs_entity_occurred",
        "audit_logs",
        ["entity_type", "entity_id", "occurred_at"],
    )
    op.execute(
        """
        CREATE FUNCTION tc_reject_audit_log_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only'
                USING ERRCODE = '55000';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION tc_reject_audit_log_mutation()
        """
    )

    op.create_table(
        "folders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("system_kind", sa.String(length=40), nullable=True),
        sa.Column("deletion_batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_folders_not_self_parent"),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name="ck_folders_name_nonblank"),
        sa.CheckConstraint("char_length(normalized_name) > 0", name="ck_folders_normalized_name_nonblank"),
        sa.CheckConstraint("sort_order >= 0", name="ck_folders_sort_order_nonnegative"),
        sa.CheckConstraint("version >= 1", name="ck_folders_version_positive"),
        sa.CheckConstraint(
            "system_kind IS NULL OR system_kind = 'AI_GENERATED_INBOX'",
            name="ck_folders_system_kind_allowed",
        ),
        sa.CheckConstraint(
            "system_kind IS NULL OR (parent_id IS NULL AND deleted_at IS NULL "
            "AND deletion_batch_id IS NULL AND name = 'AI 생성함' "
            "AND normalized_name = 'ai 생성함')",
            name="ck_folders_system_shape",
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL AND deletion_batch_id IS NULL) OR "
            "(deleted_at IS NOT NULL AND deletion_batch_id IS NOT NULL)",
            name="ck_folders_deletion_batch_matches_state",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["folders.id"],
            name="fk_folders_parent_id_folders",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace_settings.id"],
            name="fk_folders_workspace_id_workspace_settings",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_folders"),
    )
    op.create_index(
        "uq_folders_active_root_name",
        "folders",
        ["workspace_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND parent_id IS NULL"),
    )
    op.create_index(
        "uq_folders_active_child_name",
        "folders",
        ["workspace_id", "parent_id", "normalized_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND parent_id IS NOT NULL"),
    )
    op.create_index(
        "uq_folders_workspace_system_kind",
        "folders",
        ["workspace_id", "system_kind"],
        unique=True,
        postgresql_where=sa.text("system_kind IS NOT NULL"),
    )
    op.create_index(
        "ix_folders_workspace_parent_sort_deleted",
        "folders",
        ["workspace_id", "parent_id", "sort_order", "deleted_at"],
    )
    op.create_index(
        "ix_folders_workspace_deleted",
        "folders",
        ["workspace_id", "deleted_at"],
    )

    op.create_table(
        "folder_aliases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.Column("normalized_alias", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("char_length(btrim(alias)) > 0", name="ck_folder_aliases_alias_nonblank"),
        sa.CheckConstraint(
            "char_length(normalized_alias) > 0",
            name="ck_folder_aliases_normalized_alias_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["folders.id"],
            name="fk_folder_aliases_folder_id_folders",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace_settings.id"],
            name="fk_folder_aliases_workspace_id_workspace_settings",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_folder_aliases"),
        sa.UniqueConstraint(
            "workspace_id",
            "normalized_alias",
            name="uq_folder_aliases_workspace_normalized_alias",
        ),
    )
    op.create_index("ix_folder_aliases_folder", "folder_aliases", ["workspace_id", "folder_id"])

    op.execute(
        sa.text(
            "INSERT INTO workspace_settings "
            "(id, singleton_key, team_name, case_prefix, issue_prefix, "
            "default_environment, generation_quiet_minutes, version) "
            f"VALUES ('{DEFAULT_WORKSPACE_ID}'::uuid, 1, 'QA Team', 'TC', 'ISSUE', "
            "'staging', 10, 1) ON CONFLICT DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO workspace_key_counters (workspace_id, entity_type, next_value) "
            f"VALUES ('{DEFAULT_WORKSPACE_ID}'::uuid, 'TEST_CASE', 1), "
            f"('{DEFAULT_WORKSPACE_ID}'::uuid, 'ISSUE', 1) ON CONFLICT DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO folders "
            "(id, workspace_id, parent_id, name, normalized_name, sort_order, version, system_kind) "
            f"VALUES ('{AI_INBOX_FOLDER_ID}'::uuid, '{DEFAULT_WORKSPACE_ID}'::uuid, NULL, "
            "'AI 생성함', 'ai 생성함', 2147483647, 1, 'AI_GENERATED_INBOX') "
            "ON CONFLICT DO NOTHING"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_folder_aliases_folder", table_name="folder_aliases")
    op.drop_table("folder_aliases")
    op.drop_index("ix_folders_workspace_deleted", table_name="folders")
    op.drop_index("ix_folders_workspace_parent_sort_deleted", table_name="folders")
    op.drop_index("uq_folders_workspace_system_kind", table_name="folders")
    op.drop_index("uq_folders_active_child_name", table_name="folders")
    op.drop_index("uq_folders_active_root_name", table_name="folders")
    op.drop_table("folders")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs")
    op.drop_index("ix_audit_logs_entity_occurred", table_name="audit_logs")
    op.drop_index("ix_audit_logs_workspace_occurred", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.execute("DROP FUNCTION IF EXISTS tc_reject_audit_log_mutation()")
    op.drop_table("workspace_key_counters")
    op.drop_table("workspace_settings")
