"""Step preconditions, multi-label cycle scope, and step execution results.

Revision ID: 20260821_0009
Revises: 20260821_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260821_0009"
down_revision: str | None = "20260821_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0005 made BLOCKED notes mandatory.  Notes are now optional for both
    # attempt-level and step-level results, including databases upgraded from
    # the historical execution ledger.
    op.execute(
        "ALTER TABLE execution_attempts DROP CONSTRAINT IF EXISTS "
        "ck_execution_attempts_blocked_note_required"
    )

    op.add_column("test_steps", sa.Column("precondition", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_test_steps_precondition_nonblank",
        "test_steps",
        "precondition IS NULL OR char_length(btrim(precondition)) > 0",
    )

    op.add_column(
        "test_cycles",
        sa.Column("scope_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # This is a schema backfill, not a user-visible cycle edit.  The cycle
    # ledger trigger rejects same-state updates for IN_PROGRESS/COMPLETED
    # cycles (and requires a resource-version bump for PLANNED cycles), so a
    # populated pre-0009 database cannot otherwise reach head.  PostgreSQL
    # applies the disable/backfill/enable sequence transactionally.
    op.execute("ALTER TABLE test_cycles DISABLE TRIGGER trg_test_cycles_guard")
    op.execute("UPDATE test_cycles SET scope_values = jsonb_build_array(scope_value)")
    op.execute("ALTER TABLE test_cycles ENABLE TRIGGER trg_test_cycles_guard")
    op.alter_column("test_cycles", "scope_values", nullable=False)
    op.create_check_constraint(
        "ck_test_cycles_scope_values_array",
        "test_cycles",
        "jsonb_typeof(scope_values) = 'array'",
    )
    op.create_check_constraint(
        "ck_test_cycles_scope_values_count",
        "test_cycles",
        "jsonb_array_length(scope_values) BETWEEN 1 AND 50",
    )
    op.create_check_constraint(
        "ck_test_cycles_scope_value_is_first",
        "test_cycles",
        "scope_values ->> 0 = scope_value",
    )

    op.create_table(
        "execution_step_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("execution_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "recorded_at",
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
        sa.CheckConstraint("sequence >= 1", name="sequence_positive"),
        sa.CheckConstraint(
            "status IN ('PASS', 'FAIL', 'BLOCKED')",
            name="status_allowed",
        ),
        sa.CheckConstraint(
            "note IS NULL OR char_length(btrim(note)) > 0",
            name="note_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["execution_attempt_id"],
            ["execution_attempts.id"],
            name="fk_execution_step_results_attempt_id_execution_attempts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_step_id"],
            ["test_steps.id"],
            name="fk_execution_step_results_test_step_id_test_steps",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_step_results"),
        sa.UniqueConstraint(
            "execution_attempt_id",
            "sequence",
            name="uq_execution_step_results_attempt_sequence",
        ),
        sa.UniqueConstraint(
            "execution_attempt_id",
            "test_step_id",
            name="uq_execution_step_results_attempt_step",
        ),
    )
    op.create_index(
        "ix_execution_step_results_attempt_sequence",
        "execution_step_results",
        ["execution_attempt_id", "sequence"],
    )
    _create_execution_step_result_guard()


def _create_execution_step_result_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_guard_execution_step_result()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_status text;
            pinned_version_id uuid;
            step_version_id uuid;
            step_sequence integer;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'execution step results are immutable after removal';
            END IF;

            IF TG_OP = 'UPDATE' AND (
                NEW.execution_attempt_id <> OLD.execution_attempt_id
                OR NEW.test_step_id <> OLD.test_step_id
                OR NEW.sequence <> OLD.sequence
                OR NEW.recorded_at <> OLD.recorded_at
            ) THEN
                RAISE EXCEPTION 'execution step result identity is immutable';
            END IF;

            SELECT ea.status, ci.test_case_version_id
              INTO parent_status, pinned_version_id
              FROM execution_attempts ea
              JOIN cycle_items ci ON ci.id = ea.cycle_item_id
             WHERE ea.id = NEW.execution_attempt_id
             FOR UPDATE OF ea;
            IF parent_status IS NULL THEN
                RAISE EXCEPTION 'execution attempt does not exist';
            END IF;
            IF parent_status <> 'WIP' THEN
                RAISE EXCEPTION 'execution step results are immutable after attempt completion';
            END IF;

            SELECT ts.test_case_version_id, ts.sequence
              INTO step_version_id, step_sequence
              FROM test_steps ts
             WHERE ts.id = NEW.test_step_id;
            IF step_version_id IS NULL
               OR step_version_id <> pinned_version_id
               OR step_sequence <> NEW.sequence THEN
                RAISE EXCEPTION 'execution step result must reference the pinned step sequence';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_execution_step_results_guard
        BEFORE INSERT OR UPDATE OR DELETE ON execution_step_results
        FOR EACH ROW EXECUTE FUNCTION tc_guard_execution_step_result()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_execution_step_results_guard ON execution_step_results")
    op.execute("DROP FUNCTION tc_guard_execution_step_result()")
    op.drop_index(
        "ix_execution_step_results_attempt_sequence",
        table_name="execution_step_results",
    )
    op.drop_table("execution_step_results")

    # 0010 replaces this function with a scope_values-aware variant.  Restore
    # the 0008 guard before removing that column so a populated downgrade can
    # still perform lifecycle transitions.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tc_guard_cycle_mutation()
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

    op.drop_constraint("ck_test_cycles_scope_value_is_first", "test_cycles", type_="check")
    op.drop_constraint("ck_test_cycles_scope_values_count", "test_cycles", type_="check")
    op.drop_constraint("ck_test_cycles_scope_values_array", "test_cycles", type_="check")
    op.drop_column("test_cycles", "scope_values")

    op.drop_constraint("ck_test_steps_precondition_nonblank", "test_steps", type_="check")
    op.drop_column("test_steps", "precondition")

    # A development database may have applied an earlier 0009 draft that did
    # not remove the 0005 constraint.  Recreate the 0008 invariant
    # deterministically, and make optional-note rows downgradeable.
    op.execute("ALTER TABLE execution_attempts DISABLE TRIGGER trg_execution_attempts_append_only")
    op.execute(
        "UPDATE execution_attempts SET note = 'No BLOCKED note was recorded' "
        "WHERE status = 'BLOCKED' AND note IS NULL"
    )
    op.execute("ALTER TABLE execution_attempts ENABLE TRIGGER trg_execution_attempts_append_only")
    op.execute(
        "ALTER TABLE execution_attempts DROP CONSTRAINT IF EXISTS "
        "ck_execution_attempts_blocked_note_required"
    )
    op.create_check_constraint(
        "blocked_note_required",
        "execution_attempts",
        "status <> 'BLOCKED' OR (note IS NOT NULL AND char_length(btrim(note)) > 0)",
    )
