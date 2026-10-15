"""Soft-delete cycles while preserving execution and issue ledgers.

Revision ID: 20260825_0014
Revises: 20260824_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0014"
down_revision: str | None = "20260824_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "test_cycles",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_test_cycles_workspace_deleted_updated",
        "test_cycles",
        ["workspace_id", "deleted_at", "updated_at"],
    )
    op.execute(_soft_delete_guard())


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM test_cycles WHERE deleted_at IS NOT NULL) THEN
                RAISE EXCEPTION 'cannot downgrade while soft-deleted cycle ledgers exist';
            END IF;
        END
        $$
        """
    )
    op.execute(_legacy_guard())
    op.drop_index("ix_test_cycles_workspace_deleted_updated", table_name="test_cycles")
    op.drop_column("test_cycles", "deleted_at")


def _soft_delete_guard() -> str:
    return """
        CREATE OR REPLACE FUNCTION tc_guard_cycle_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'PLANNED' OR NEW.resource_version <> 1
                   OR NEW.run_number <> 1 OR NEW.completed_at IS NOT NULL
                   OR NEW.deleted_at IS NOT NULL THEN
                    RAISE EXCEPTION 'a cycle must be created in active PLANNED state'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'cycles are soft-delete only'
                    USING ERRCODE = '55000';
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

            IF OLD.deleted_at IS NOT NULL THEN
                RAISE EXCEPTION 'a soft-deleted cycle is immutable'
                    USING ERRCODE = '55000';
            END IF;

            IF NEW.deleted_at IS NOT NULL THEN
                IF ROW(NEW.name, NEW.description, NEW.starts_at, NEW.status,
                       NEW.scope_type, NEW.scope_value, NEW.scope_values,
                       NEW.run_number, NEW.completed_at)
                   IS DISTINCT FROM
                   ROW(OLD.name, OLD.description, OLD.starts_at, OLD.status,
                       OLD.scope_type, OLD.scope_value, OLD.scope_values,
                       OLD.run_number, OLD.completed_at) THEN
                    RAISE EXCEPTION 'soft deletion cannot mutate cycle details or status'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.status = 'PLANNED' AND NEW.status = 'PLANNED' THEN
                IF NEW.run_number <> OLD.run_number OR NEW.completed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid PLANNED cycle mutation'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF OLD.status <> 'PLANNED'
               AND ROW(NEW.scope_type, NEW.scope_value, NEW.scope_values)
                   IS DISTINCT FROM
                   ROW(OLD.scope_type, OLD.scope_value, OLD.scope_values) THEN
                RAISE EXCEPTION 'cycle scope is locked after start'
                    USING ERRCODE = '55000';
            END IF;

            IF OLD.status = 'IN_PROGRESS' AND NEW.status = 'IN_PROGRESS' THEN
                IF NEW.run_number <> OLD.run_number OR NEW.completed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid IN_PROGRESS cycle mutation'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

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


def _legacy_guard() -> str:
    return """
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
                    RAISE EXCEPTION 'invalid PLANNED cycle mutation' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status <> 'PLANNED'
               AND ROW(NEW.scope_type, NEW.scope_value, NEW.scope_values)
                   IS DISTINCT FROM ROW(OLD.scope_type, OLD.scope_value, OLD.scope_values) THEN
                RAISE EXCEPTION 'cycle scope is locked after start' USING ERRCODE = '55000';
            END IF;
            IF OLD.status = 'IN_PROGRESS' AND NEW.status = 'IN_PROGRESS' THEN
                IF NEW.run_number <> OLD.run_number OR NEW.completed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid IN_PROGRESS cycle mutation' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF ROW(NEW.name, NEW.description, NEW.starts_at,
                   NEW.scope_type, NEW.scope_value, NEW.scope_values)
               IS DISTINCT FROM ROW(OLD.name, OLD.description, OLD.starts_at,
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
