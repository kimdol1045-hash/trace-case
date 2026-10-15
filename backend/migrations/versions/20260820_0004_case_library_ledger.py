"""Flat category/work-item/case library and immutable case versions.

Revision ID: 20260820_0004
Revises: 20260820_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260820_0004"
down_revision: str | None = "20260820_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_item_key", sa.String(length=100), nullable=False),
        sa.Column("summary", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("owner_label", sa.String(length=200), nullable=True),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
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
        sa.CheckConstraint(
            "char_length(btrim(work_item_key)) > 0",
            name="ck_work_items_key_nonblank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(summary)) > 0",
            name="ck_work_items_summary_nonblank",
        ),
        sa.CheckConstraint("jsonb_typeof(labels) = 'array'", name="ck_work_items_labels_array"),
        sa.CheckConstraint("version >= 1", name="ck_work_items_version_positive"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace_settings.id"],
            name="fk_work_items_workspace_id_workspace_settings",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["folders.id"],
            name="fk_work_items_category_id_folders",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_items"),
        sa.UniqueConstraint(
            "workspace_id",
            "work_item_key",
            name="uq_work_items_workspace_key",
        ),
        # This redundant-looking key is the target of the test-case ownership
        # FK. It makes category ownership a physical database invariant.
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            "category_id",
            name="uq_work_items_workspace_id_category",
        ),
    )
    op.create_index(
        "ix_work_items_workspace_category_updated",
        "work_items",
        ["workspace_id", "category_id", "updated_at", "id"],
    )

    op.create_table(
        "test_cases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_key", sa.String(length=40), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'ACTIVE'"), nullable=False
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "active_sort_order",
            sa.Integer(),
            sa.Computed(
                "CASE WHEN deleted_at IS NULL THEN sort_order ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.CheckConstraint(
            "case_key ~ '^[A-Z][A-Z0-9]{1,15}-[0-9]{6,}$'", name="ck_test_cases_key_format"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DEPRECATED')", name="ck_test_cases_status_allowed"
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_test_cases_sort_order_nonnegative"),
        sa.CheckConstraint("version >= 1", name="ck_test_cases_version_positive"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "work_item_id", "category_id"],
            ["work_items.workspace_id", "work_items.id", "work_items.category_id"],
            name="fk_test_cases_work_item_ownership",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_test_cases"),
        sa.UniqueConstraint(
            "workspace_id",
            "case_key",
            name="uq_test_cases_workspace_key",
        ),
        sa.UniqueConstraint(
            "work_item_id",
            "active_sort_order",
            name="uq_test_cases_active_work_item_sort",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index(
        "ix_test_cases_workspace_category_deleted",
        "test_cases",
        ["workspace_id", "category_id", "deleted_at"],
    )
    op.create_index(
        "ix_test_cases_work_item_sort_deleted",
        "test_cases",
        ["work_item_id", "sort_order", "deleted_at", "id"],
    )

    op.create_table(
        "test_case_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.String(length=200), nullable=False),
        sa.Column("precondition", sa.Text(), nullable=False),
        sa.Column("expected_result_mode", sa.String(length=20), nullable=False),
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.Column(
            "priority", sa.String(length=20), server_default=sa.text("'MEDIUM'"), nullable=False
        ),
        sa.Column("assignee_label", sa.String(length=200), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("generation_reason", sa.Text(), nullable=True),
        sa.Column("change_reason", sa.String(length=500), nullable=False),
        sa.Column("restored_from_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("copied_from_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_test_case_versions_number_positive"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DEPRECATED')", name="ck_test_case_versions_status_allowed"
        ),
        sa.CheckConstraint(
            "char_length(btrim(summary)) > 0", name="ck_test_case_versions_summary_nonblank"
        ),
        sa.CheckConstraint(
            "char_length(btrim(precondition)) > 0",
            name="ck_test_case_versions_precondition_nonblank",
        ),
        sa.CheckConstraint(
            "expected_result_mode IN ('PER_STEP', 'CASE')",
            name="ck_test_case_versions_expected_mode_allowed",
        ),
        sa.CheckConstraint(
            "(expected_result_mode = 'CASE' AND expected_result IS NOT NULL "
            "AND char_length(btrim(expected_result)) > 0) OR "
            "(expected_result_mode = 'PER_STEP' AND expected_result IS NULL)",
            name="ck_test_case_versions_expected_result_shape",
        ),
        sa.CheckConstraint(
            "priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_test_case_versions_priority_allowed",
        ),
        sa.CheckConstraint(
            "source_type IN ('MANUAL', 'SLACK_AI', 'FILE_IMPORT', 'API')",
            name="ck_test_case_versions_source_type_allowed",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name="ck_test_case_versions_source_metadata_object",
        ),
        sa.CheckConstraint(
            "char_length(btrim(change_reason)) > 0",
            name="ck_test_case_versions_change_reason_nonblank",
        ),
        sa.CheckConstraint(
            "restored_from_version_id IS NULL OR restored_from_version_id <> id",
            name="ck_test_case_versions_not_restore_self",
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id"],
            ["test_cases.id"],
            name="fk_test_case_versions_test_case_id_test_cases",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["copied_from_version_id"],
            ["test_case_versions.id"],
            name="fk_test_case_versions_copied_from",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_test_case_versions"),
        sa.UniqueConstraint(
            "test_case_id",
            "version_number",
            name="uq_test_case_versions_case_number",
        ),
        sa.UniqueConstraint(
            "test_case_id",
            "id",
            name="uq_test_case_versions_case_id",
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id", "restored_from_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_test_case_versions_restored_from_same_case",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.create_index(
        "ix_test_case_versions_case_created",
        "test_case_versions",
        ["test_case_id", "version_number", "created_at"],
    )

    op.create_foreign_key(
        "fk_test_cases_current_version_same_case",
        "test_cases",
        "test_case_versions",
        ["id", "current_version_id"],
        ["test_case_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "test_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("test_case_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.CheckConstraint("sequence >= 1", name="ck_test_steps_sequence_positive"),
        sa.CheckConstraint("char_length(btrim(action)) > 0", name="ck_test_steps_action_nonblank"),
        sa.CheckConstraint(
            "expected_result IS NULL OR char_length(btrim(expected_result)) > 0",
            name="ck_test_steps_expected_result_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["test_case_version_id"],
            ["test_case_versions.id"],
            name="fk_test_steps_test_case_version_id_test_case_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_test_steps"),
        sa.UniqueConstraint(
            "test_case_version_id",
            "sequence",
            name="uq_test_steps_version_sequence",
        ),
    )

    op.create_table(
        "tags",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name="ck_tags_name_nonblank"),
        sa.CheckConstraint(
            "char_length(normalized_name) > 0", name="ck_tags_normalized_name_nonblank"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace_settings.id"],
            name="fk_tags_workspace_id_workspace_settings",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tags"),
        sa.UniqueConstraint(
            "workspace_id", "normalized_name", name="uq_tags_workspace_normalized_name"
        ),
    )

    op.create_table(
        "test_case_tags",
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["test_case_id"],
            ["test_cases.id"],
            name="fk_test_case_tags_test_case_id_test_cases",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            name="fk_test_case_tags_tag_id_tags",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("test_case_id", "tag_id", name="pk_test_case_tags"),
    )
    op.create_index("ix_test_case_tags_tag_case", "test_case_tags", ["tag_id", "test_case_id"])

    op.create_table(
        "test_case_version_tags",
        sa.Column("test_case_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["test_case_version_id"],
            ["test_case_versions.id"],
            name="fk_test_case_version_tags_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            name="fk_test_case_version_tags_tag",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "test_case_version_id",
            "tag_id",
            name="pk_test_case_version_tags",
        ),
    )

    _create_category_guards()
    _create_immutable_version_guards()
    _create_version_shape_guards()
    _create_current_version_guards()
    _create_tag_projection_guards()


def _create_category_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_validate_work_item_category()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM folders
                WHERE id = NEW.category_id
                  AND workspace_id = NEW.workspace_id
                  AND parent_id IS NULL
                  AND deleted_at IS NULL
            ) THEN
                RAISE EXCEPTION 'work item category must be an active root folder'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_work_items_category_root
        BEFORE INSERT OR UPDATE OF workspace_id, category_id ON work_items
        FOR EACH ROW EXECUTE FUNCTION tc_validate_work_item_category()
        """
    )
    op.execute(
        """
        CREATE FUNCTION tc_protect_projected_category_folder()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM work_items WHERE category_id = OLD.id)
               AND (NEW.parent_id IS NOT NULL OR NEW.deleted_at IS NOT NULL
                    OR NEW.workspace_id <> OLD.workspace_id) THEN
                RAISE EXCEPTION 'projected category with work items must stay an active root folder'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_folders_protect_projected_category
        BEFORE UPDATE OF parent_id, deleted_at, workspace_id ON folders
        FOR EACH ROW EXECUTE FUNCTION tc_protect_projected_category_folder()
        """
    )


def _create_immutable_version_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_reject_case_version_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END
        $$
        """
    )
    for table in ("test_case_versions", "test_steps", "test_case_version_tags"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION tc_reject_case_version_mutation()
            """
        )


def _create_version_shape_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_validate_case_version_shape(version_id uuid)
        RETURNS void
        LANGUAGE plpgsql
        AS $$
        DECLARE
            mode text;
            step_count integer;
            invalid_step_count integer;
        BEGIN
            SELECT expected_result_mode INTO mode
            FROM test_case_versions WHERE id = version_id;
            IF mode IS NULL THEN
                RETURN;
            END IF;
            SELECT count(*)::integer,
                   count(*) FILTER (
                       WHERE (mode = 'PER_STEP' AND expected_result IS NULL)
                          OR (mode = 'CASE' AND expected_result IS NOT NULL)
                   )::integer
              INTO step_count, invalid_step_count
              FROM test_steps
             WHERE test_case_version_id = version_id;
            IF step_count < 1 THEN
                RAISE EXCEPTION 'test case version requires at least one step'
                    USING ERRCODE = '23514';
            END IF;
            IF invalid_step_count > 0 THEN
                RAISE EXCEPTION 'test step expected result does not match % mode', mode
                    USING ERRCODE = '23514';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION tc_check_case_version_shape()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            version_id uuid;
        BEGIN
            IF TG_TABLE_NAME = 'test_case_versions' THEN
                version_id := COALESCE(NEW.id, OLD.id);
            ELSE
                version_id := COALESCE(NEW.test_case_version_id, OLD.test_case_version_id);
            END IF;
            PERFORM tc_validate_case_version_shape(version_id);
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_test_case_versions_shape
        AFTER INSERT OR UPDATE ON test_case_versions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_check_case_version_shape()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_test_steps_version_shape
        AFTER INSERT OR UPDATE OR DELETE ON test_steps
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_check_case_version_shape()
        """
    )


def _create_current_version_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_validate_case_current_version(case_id uuid)
        RETURNS void
        LANGUAGE plpgsql
        AS $$
        DECLARE
            current_id uuid;
            current_status text;
            version_status text;
            current_number integer;
            maximum_number integer;
        BEGIN
            SELECT current_version_id, status
              INTO current_id, current_status
              FROM test_cases WHERE id = case_id;
            IF current_id IS NULL THEN
                RETURN;
            END IF;
            SELECT status, version_number
              INTO version_status, current_number
              FROM test_case_versions
             WHERE id = current_id AND test_case_id = case_id;
            SELECT max(version_number) INTO maximum_number
              FROM test_case_versions WHERE test_case_id = case_id;
            IF current_number IS NULL OR current_number <> maximum_number THEN
                RAISE EXCEPTION 'current case pointer must reference the latest version'
                    USING ERRCODE = '23514';
            END IF;
            IF current_status <> version_status THEN
                RAISE EXCEPTION 'test case status must match its current version'
                    USING ERRCODE = '23514';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION tc_check_case_current_version()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            case_id uuid;
        BEGIN
            IF TG_TABLE_NAME = 'test_cases' THEN
                case_id := COALESCE(NEW.id, OLD.id);
            ELSE
                case_id := COALESCE(NEW.test_case_id, OLD.test_case_id);
            END IF;
            PERFORM tc_validate_case_current_version(case_id);
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_test_cases_current_version
        AFTER INSERT OR UPDATE ON test_cases
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_check_case_current_version()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_test_case_versions_current_pointer
        AFTER INSERT ON test_case_versions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_check_case_current_version()
        """
    )


def _create_tag_projection_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_validate_case_tag_projection(case_id uuid)
        RETURNS void
        LANGUAGE plpgsql
        AS $$
        DECLARE
            current_id uuid;
        BEGIN
            SELECT current_version_id INTO current_id FROM test_cases WHERE id = case_id;
            IF current_id IS NULL THEN
                RETURN;
            END IF;
            IF EXISTS (
                (SELECT tag_id FROM test_case_version_tags WHERE test_case_version_id = current_id
                 EXCEPT
                 SELECT tag_id FROM test_case_tags WHERE test_case_id = case_id)
                UNION ALL
                (SELECT tag_id FROM test_case_tags WHERE test_case_id = case_id
                 EXCEPT
                 SELECT tag_id FROM test_case_version_tags WHERE test_case_version_id = current_id)
            ) THEN
                RAISE EXCEPTION 'test case tags must project the current immutable version'
                    USING ERRCODE = '23514';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION tc_check_case_tag_projection()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            case_id uuid;
        BEGIN
            IF TG_TABLE_NAME = 'test_cases' THEN
                case_id := COALESCE(NEW.id, OLD.id);
            ELSIF TG_TABLE_NAME = 'test_case_tags' THEN
                case_id := COALESCE(NEW.test_case_id, OLD.test_case_id);
            ELSE
                SELECT test_case_id INTO case_id FROM test_case_versions
                 WHERE id = COALESCE(NEW.test_case_version_id, OLD.test_case_version_id);
            END IF;
            IF case_id IS NOT NULL THEN
                PERFORM tc_validate_case_tag_projection(case_id);
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_test_cases_tag_projection
        AFTER INSERT OR UPDATE ON test_cases
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_check_case_tag_projection()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_test_case_tags_projection
        AFTER INSERT OR UPDATE OR DELETE ON test_case_tags
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_check_case_tag_projection()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_test_case_version_tags_projection
        AFTER INSERT ON test_case_version_tags
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_check_case_tag_projection()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_folders_protect_projected_category ON folders")
    op.execute("DROP TRIGGER IF EXISTS trg_work_items_category_root ON work_items")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_test_case_version_tags_projection ON test_case_version_tags"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_test_case_tags_projection ON test_case_tags")
    op.execute("DROP TRIGGER IF EXISTS trg_test_cases_tag_projection ON test_cases")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_test_case_versions_current_pointer ON test_case_versions"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_test_cases_current_version ON test_cases")
    op.execute("DROP TRIGGER IF EXISTS trg_test_steps_version_shape ON test_steps")
    op.execute("DROP TRIGGER IF EXISTS trg_test_case_versions_shape ON test_case_versions")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_test_case_version_tags_immutable ON test_case_version_tags"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_test_steps_immutable ON test_steps")
    op.execute("DROP TRIGGER IF EXISTS trg_test_case_versions_immutable ON test_case_versions")

    op.drop_constraint("fk_test_cases_current_version_same_case", "test_cases", type_="foreignkey")
    op.drop_table("test_case_version_tags")
    op.drop_index("ix_test_case_tags_tag_case", table_name="test_case_tags")
    op.drop_table("test_case_tags")
    op.drop_table("test_steps")
    op.drop_index("ix_test_case_versions_case_created", table_name="test_case_versions")
    op.drop_table("test_case_versions")
    op.drop_index("ix_test_cases_work_item_sort_deleted", table_name="test_cases")
    op.drop_index("ix_test_cases_workspace_category_deleted", table_name="test_cases")
    op.drop_table("test_cases")
    op.drop_table("tags")
    op.drop_index("ix_work_items_workspace_category_updated", table_name="work_items")
    op.drop_table("work_items")

    op.execute("DROP FUNCTION IF EXISTS tc_check_case_tag_projection()")
    op.execute("DROP FUNCTION IF EXISTS tc_validate_case_tag_projection(uuid)")
    op.execute("DROP FUNCTION IF EXISTS tc_check_case_current_version()")
    op.execute("DROP FUNCTION IF EXISTS tc_validate_case_current_version(uuid)")
    op.execute("DROP FUNCTION IF EXISTS tc_check_case_version_shape()")
    op.execute("DROP FUNCTION IF EXISTS tc_validate_case_version_shape(uuid)")
    op.execute("DROP FUNCTION IF EXISTS tc_reject_case_version_mutation()")
    op.execute("DROP FUNCTION IF EXISTS tc_protect_projected_category_folder()")
    op.execute("DROP FUNCTION IF EXISTS tc_validate_work_item_category()")
