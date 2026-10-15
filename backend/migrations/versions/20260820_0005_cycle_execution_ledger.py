"""Start-gated cycle snapshots and append-only platform attempts.

Revision ID: 20260820_0005
Revises: 20260820_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260820_0005"
down_revision: str | None = "20260820_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "test_cycles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'PLANNED'"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_value", sa.String(length=200), nullable=False),
        sa.Column("resource_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("run_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("char_length(btrim(name)) > 0", name="name_nonblank"),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'IN_PROGRESS', 'COMPLETED')",
            name="status_allowed",
        ),
        sa.CheckConstraint(
            "scope_type IN ('WORK_ITEM', 'LABEL')",
            name="scope_type_allowed",
        ),
        sa.CheckConstraint(
            "char_length(btrim(scope_value)) > 0",
            name="scope_value_nonblank",
        ),
        sa.CheckConstraint(
            "resource_version >= 1",
            name="resource_version_positive",
        ),
        sa.CheckConstraint("run_number >= 1", name="run_number_positive"),
        sa.CheckConstraint(
            "(status = 'COMPLETED' AND completed_at IS NOT NULL) OR "
            "(status <> 'COMPLETED' AND completed_at IS NULL)",
            name="completed_at_matches_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace_settings.id"],
            name="fk_test_cycles_workspace_id_workspace_settings",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_test_cycles"),
    )
    op.create_index(
        "ix_test_cycles_workspace_status_scope_starts",
        "test_cycles",
        ["workspace_id", "status", "scope_type", "scope_value", "starts_at"],
    )
    op.create_index(
        "ix_test_cycles_workspace_updated",
        "test_cycles",
        ["workspace_id", "updated_at", "id"],
    )

    op.create_table(
        "cycle_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("cycle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_case_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignee_label", sa.String(length=200), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["test_cycles.id"],
            name="fk_cycle_items_cycle_id_test_cycles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id"],
            ["test_cases.id"],
            name="fk_cycle_items_test_case_id_test_cases",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id", "test_case_version_id"],
            ["test_case_versions.test_case_id", "test_case_versions.id"],
            name="fk_cycle_items_pinned_version_same_case",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_cycle_items_work_item_id_work_items",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cycle_items"),
        sa.UniqueConstraint(
            "cycle_id",
            "test_case_id",
            name="uq_cycle_items_cycle_case",
        ),
    )
    op.create_index(
        "ix_cycle_items_cycle_created",
        "cycle_items",
        ["cycle_id", "created_at", "id"],
    )
    op.create_index(
        "ix_cycle_items_work_item",
        "cycle_items",
        ["work_item_id", "cycle_id"],
    )

    op.create_table(
        "execution_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("cycle_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=10), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("cycle_run_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("actual_result", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("executor_label", sa.String(length=200), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "attempt_no >= 1",
            name="attempt_no_positive",
        ),
        sa.CheckConstraint(
            "cycle_run_number >= 1",
            name="cycle_run_number_positive",
        ),
        sa.CheckConstraint(
            "platform IN ('WEB', 'AOS', 'IOS')",
            name="platform_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('UNEXECUTED', 'WIP', 'PASS', 'FAIL', 'BLOCKED')",
            name="status_allowed",
        ),
        sa.CheckConstraint(
            "(status IN ('UNEXECUTED', 'WIP') AND finished_at IS NULL) OR "
            "(status IN ('PASS', 'FAIL', 'BLOCKED') AND finished_at IS NOT NULL)",
            name="finished_at_matches_status",
        ),
        sa.CheckConstraint(
            "status <> 'FAIL' OR (actual_result IS NOT NULL AND "
            "char_length(btrim(actual_result)) > 0)",
            name="fail_actual_result_required",
        ),
        sa.CheckConstraint(
            "status <> 'BLOCKED' OR (note IS NOT NULL AND char_length(btrim(note)) > 0)",
            name="blocked_note_required",
        ),
        sa.CheckConstraint(
            "actual_result IS NULL OR char_length(btrim(actual_result)) > 0",
            name="actual_result_nonblank",
        ),
        sa.CheckConstraint(
            "note IS NULL OR char_length(btrim(note)) > 0",
            name="note_nonblank",
        ),
        sa.CheckConstraint(
            "executor_label IS NULL OR char_length(btrim(executor_label)) > 0",
            name="executor_label_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_item_id"],
            ["cycle_items.id"],
            name="fk_execution_attempts_cycle_item_id_cycle_items",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_attempts"),
        sa.UniqueConstraint(
            "cycle_item_id",
            "platform",
            "attempt_no",
            name="uq_execution_attempts_item_platform_number",
        ),
    )
    op.create_index(
        "ix_execution_attempts_item_platform_latest",
        "execution_attempts",
        ["cycle_item_id", "platform", sa.text("attempt_no DESC")],
    )
    op.create_index(
        "ix_execution_attempts_status_finished",
        "execution_attempts",
        ["status", "finished_at"],
    )

    _create_cycle_guards()
    _create_cycle_item_guards()
    _create_execution_attempt_guards()


def _create_cycle_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_guard_cycle_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'PLANNED' OR NEW.resource_version <> 1
                   OR NEW.run_number <> 1 OR NEW.completed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'a cycle must be created in PLANNED state'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'PLANNED' THEN
                    RAISE EXCEPTION 'only a PLANNED cycle can be deleted'
                        USING ERRCODE = '55000';
                END IF;
                RETURN OLD;
            END IF;

            IF NEW.resource_version <> OLD.resource_version + 1 THEN
                RAISE EXCEPTION 'cycle resource_version must increase by exactly one'
                    USING ERRCODE = '23514';
            END IF;

            IF ROW(NEW.id, NEW.workspace_id, NEW.created_at)
               IS DISTINCT FROM ROW(OLD.id, OLD.workspace_id, OLD.created_at) THEN
                RAISE EXCEPTION 'cycle identity and creation audit timestamp are immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.status = 'PLANNED' AND NEW.status = 'PLANNED' THEN
                IF NEW.run_number <> OLD.run_number OR NEW.completed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid PLANNED cycle mutation'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF ROW(NEW.name, NEW.description, NEW.starts_at, NEW.scope_type, NEW.scope_value)
               IS DISTINCT FROM
               ROW(OLD.name, OLD.description, OLD.starts_at, OLD.scope_type, OLD.scope_value) THEN
                RAISE EXCEPTION 'cycle basic information and scope are locked after start'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.status = 'PLANNED' AND NEW.status = 'IN_PROGRESS'
               AND NEW.run_number = OLD.run_number AND NEW.completed_at IS NULL THEN
                RETURN NEW;
            END IF;
            IF OLD.status = 'IN_PROGRESS' AND NEW.status = 'COMPLETED'
               AND NEW.run_number = OLD.run_number AND NEW.completed_at IS NOT NULL THEN
                RETURN NEW;
            END IF;
            IF OLD.status = 'COMPLETED' AND NEW.status = 'IN_PROGRESS'
               AND NEW.run_number = OLD.run_number + 1 AND NEW.completed_at IS NULL THEN
                RETURN NEW;
            END IF;

            RAISE EXCEPTION 'invalid cycle status transition: % -> %', OLD.status, NEW.status
                USING ERRCODE = '23514';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_test_cycles_guard
        BEFORE INSERT OR UPDATE OR DELETE ON test_cycles
        FOR EACH ROW EXECUTE FUNCTION tc_guard_cycle_mutation()
        """
    )


def _create_cycle_item_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_guard_cycle_item_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_cycle_id uuid;
            parent_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                parent_cycle_id := OLD.cycle_id;
            ELSE
                parent_cycle_id := NEW.cycle_id;
            END IF;
            SELECT status INTO parent_status FROM test_cycles WHERE id = parent_cycle_id;
            IF parent_status IS NULL AND TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            IF parent_status <> 'PLANNED' THEN
                RAISE EXCEPTION 'cycle snapshot is immutable after start'
                    USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'UPDATE'
               AND ROW(NEW.id, NEW.cycle_id, NEW.test_case_id, NEW.created_at)
                   IS DISTINCT FROM
                   ROW(OLD.id, OLD.cycle_id, OLD.test_case_id, OLD.created_at) THEN
                RAISE EXCEPTION 'cycle item snapshot identity is immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_cycle_items_planned_only
        BEFORE INSERT OR UPDATE OR DELETE ON cycle_items
        FOR EACH ROW EXECUTE FUNCTION tc_guard_cycle_item_mutation()
        """
    )


def _create_execution_attempt_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_guard_execution_attempt_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_status text;
            parent_run_number integer;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'execution attempts are append-only'
                    USING ERRCODE = '55000';
            END IF;

            SELECT c.status, c.run_number INTO parent_status, parent_run_number
              FROM cycle_items i
              JOIN test_cycles c ON c.id = i.cycle_id
             WHERE i.id = NEW.cycle_item_id;
            IF parent_status <> 'IN_PROGRESS' THEN
                RAISE EXCEPTION 'execution results require an IN_PROGRESS cycle'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.cycle_run_number <> parent_run_number THEN
                RAISE EXCEPTION 'execution attempt run does not match the active cycle run'
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP = 'INSERT' THEN
                IF NEW.status NOT IN ('UNEXECUTED', 'WIP') THEN
                    RAISE EXCEPTION 'an execution attempt must start before it can finish'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.status NOT IN ('UNEXECUTED', 'WIP') OR OLD.finished_at IS NOT NULL THEN
                RAISE EXCEPTION 'finished execution attempts are immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.status NOT IN ('PASS', 'FAIL', 'BLOCKED') THEN
                RAISE EXCEPTION 'an execution attempt can only be completed once'
                    USING ERRCODE = '55000';
            END IF;
            IF ROW(NEW.id, NEW.cycle_item_id, NEW.platform, NEW.attempt_no,
                   NEW.cycle_run_number, NEW.executor_label, NEW.started_at, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.cycle_item_id, OLD.platform, OLD.attempt_no,
                   OLD.cycle_run_number, OLD.executor_label, OLD.started_at, OLD.created_at) THEN
                RAISE EXCEPTION 'execution attempt identity is immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_execution_attempts_append_only
        BEFORE INSERT OR UPDATE OR DELETE ON execution_attempts
        FOR EACH ROW EXECUTE FUNCTION tc_guard_execution_attempt_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_execution_attempts_append_only ON execution_attempts"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_cycle_items_planned_only ON cycle_items")
    op.execute("DROP TRIGGER IF EXISTS trg_test_cycles_guard ON test_cycles")

    op.drop_index("ix_execution_attempts_status_finished", table_name="execution_attempts")
    op.drop_index(
        "ix_execution_attempts_item_platform_latest",
        table_name="execution_attempts",
    )
    op.drop_table("execution_attempts")
    op.drop_index("ix_cycle_items_work_item", table_name="cycle_items")
    op.drop_index("ix_cycle_items_cycle_created", table_name="cycle_items")
    op.drop_table("cycle_items")
    op.drop_index("ix_test_cycles_workspace_updated", table_name="test_cycles")
    op.drop_index(
        "ix_test_cycles_workspace_status_scope_starts",
        table_name="test_cycles",
    )
    op.drop_table("test_cycles")

    op.execute("DROP FUNCTION IF EXISTS tc_guard_execution_attempt_mutation()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_cycle_item_mutation()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_cycle_mutation()")
