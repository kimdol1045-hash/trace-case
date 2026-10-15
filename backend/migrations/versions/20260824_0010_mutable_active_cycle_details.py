"""Allow active cycle details to change while keeping execution scope pinned.

Revision ID: 20260824_0010
Revises: 20260821_0009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_0010"
down_revision: str | None = "20260821_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(_cycle_guard(active_details_mutable=True))


def downgrade() -> None:
    op.execute(_cycle_guard(active_details_mutable=False))


def _cycle_guard(*, active_details_mutable: bool) -> str:
    active_same_state = """
            IF OLD.status = 'IN_PROGRESS' AND NEW.status = 'IN_PROGRESS' THEN
                IF NEW.run_number <> OLD.run_number OR NEW.completed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid IN_PROGRESS cycle mutation'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
    """ if active_details_mutable else ""
    locked_fields = (
        "NEW.scope_type, NEW.scope_value, NEW.scope_values"
        if active_details_mutable
        else "NEW.name, NEW.description, NEW.starts_at, NEW.scope_type, NEW.scope_value, NEW.scope_values"
    )
    old_locked_fields = (
        "OLD.scope_type, OLD.scope_value, OLD.scope_values"
        if active_details_mutable
        else "OLD.name, OLD.description, OLD.starts_at, OLD.scope_type, OLD.scope_value, OLD.scope_values"
    )
    lock_message = (
        "cycle scope is locked after start"
        if active_details_mutable
        else "cycle basic information and scope are locked after start"
    )
    return f"""
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

            IF OLD.status <> 'PLANNED'
               AND ROW({locked_fields}) IS DISTINCT FROM ROW({old_locked_fields}) THEN
                RAISE EXCEPTION '{lock_message}' USING ERRCODE = '55000';
            END IF;

            {active_same_state}

            IF ROW(NEW.name, NEW.description, NEW.starts_at,
                   NEW.scope_type, NEW.scope_value, NEW.scope_values)
               IS DISTINCT FROM
               ROW(OLD.name, OLD.description, OLD.starts_at,
                   OLD.scope_type, OLD.scope_value, OLD.scope_values) THEN
                RAISE EXCEPTION 'cycle details cannot change during a status transition'
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
