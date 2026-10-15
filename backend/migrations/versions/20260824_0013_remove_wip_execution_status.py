"""Remove WIP while preserving active attempt ledgers.

Revision ID: 20260824_0013
Revises: 20260824_0012
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_0013"
down_revision: str | None = "20260824_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _attempt_guard(*, active_status: str) -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION tc_guard_execution_attempt_mutation()
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
                IF NEW.status <> '{active_status}' THEN
                    RAISE EXCEPTION 'an execution attempt must start before it can finish'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.status <> '{active_status}' OR OLD.finished_at IS NOT NULL THEN
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


def _step_guard(*, active_status: str) -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION tc_guard_execution_step_result()
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
            IF parent_status <> '{active_status}' THEN
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


def upgrade() -> None:
    op.execute("ALTER TABLE execution_attempts DISABLE TRIGGER trg_execution_attempts_append_only")
    op.execute("ALTER TABLE execution_step_results DISABLE TRIGGER trg_execution_step_results_guard")
    op.execute("ALTER TABLE execution_attempts DROP CONSTRAINT ck_execution_attempts_status_allowed")
    op.execute("ALTER TABLE execution_attempts DROP CONSTRAINT ck_execution_attempts_finished_at_matches_status")
    # Only the status label changes. Attempt ids, timestamps, partial step rows,
    # issue links, audit rows and run/attempt sequence numbers remain untouched.
    op.execute("UPDATE execution_attempts SET status = 'UNEXECUTED' WHERE status = 'WIP'")
    op.execute(
        "ALTER TABLE execution_attempts ADD CONSTRAINT ck_execution_attempts_status_allowed "
        "CHECK (status IN ('UNEXECUTED', 'PASS', 'FAIL', 'BLOCKED'))"
    )
    op.execute(
        "ALTER TABLE execution_attempts ADD CONSTRAINT ck_execution_attempts_finished_at_matches_status "
        "CHECK ((status = 'UNEXECUTED' AND finished_at IS NULL) OR "
        "(status IN ('PASS', 'FAIL', 'BLOCKED') AND finished_at IS NOT NULL))"
    )
    _attempt_guard(active_status="UNEXECUTED")
    _step_guard(active_status="UNEXECUTED")
    op.execute("ALTER TABLE execution_step_results ENABLE TRIGGER trg_execution_step_results_guard")
    op.execute("ALTER TABLE execution_attempts ENABLE TRIGGER trg_execution_attempts_append_only")


def downgrade() -> None:
    op.execute("ALTER TABLE execution_attempts DISABLE TRIGGER trg_execution_attempts_append_only")
    op.execute("ALTER TABLE execution_step_results DISABLE TRIGGER trg_execution_step_results_guard")
    op.execute("ALTER TABLE execution_attempts DROP CONSTRAINT ck_execution_attempts_status_allowed")
    op.execute("ALTER TABLE execution_attempts DROP CONSTRAINT ck_execution_attempts_finished_at_matches_status")
    op.execute("UPDATE execution_attempts SET status = 'WIP' WHERE status = 'UNEXECUTED' AND finished_at IS NULL")
    op.execute(
        "ALTER TABLE execution_attempts ADD CONSTRAINT ck_execution_attempts_status_allowed "
        "CHECK (status IN ('UNEXECUTED', 'WIP', 'PASS', 'FAIL', 'BLOCKED'))"
    )
    op.execute(
        "ALTER TABLE execution_attempts ADD CONSTRAINT ck_execution_attempts_finished_at_matches_status "
        "CHECK ((status IN ('UNEXECUTED', 'WIP') AND finished_at IS NULL) OR "
        "(status IN ('PASS', 'FAIL', 'BLOCKED') AND finished_at IS NOT NULL))"
    )
    _attempt_guard(active_status="WIP")
    _step_guard(active_status="WIP")
    op.execute("ALTER TABLE execution_step_results ENABLE TRIGGER trg_execution_step_results_guard")
    op.execute("ALTER TABLE execution_attempts ENABLE TRIGGER trg_execution_attempts_append_only")
