"""PostgreSQL 16, pgvector, and background job baseline.

Revision ID: 20260820_0001
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260820_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF current_setting('server_version_num')::integer < 160000 THEN
                RAISE EXCEPTION 'PostgreSQL 16 or newer is required (server_version_num=%)',
                    current_setting('server_version_num');
            END IF;
        END
        $$
        """
    )
    # CREATE EXTENSION fails loudly when the pgvector package is unavailable.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                RAISE EXCEPTION 'pgvector extension validation failed';
            END IF;
        END
        $$
        """
    )

    op.create_table(
        "background_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'QUEUED'"), nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("locked_by", sa.String(length=200), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="ck_background_jobs_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_background_jobs_max_attempts_positive"),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'RETRY', 'SUCCEEDED', 'DEAD')",
            name="ck_background_jobs_status_allowed",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND locked_by IS NOT NULL AND locked_at IS NOT NULL) "
            "OR (status <> 'RUNNING' AND locked_by IS NULL AND locked_at IS NULL "
            "AND heartbeat_at IS NULL)",
            name="ck_background_jobs_lock_matches_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_background_jobs"),
        sa.UniqueConstraint("idempotency_key", name="uq_background_jobs_idempotency_key"),
    )
    op.create_index(
        "ix_background_jobs_ready",
        "background_jobs",
        [sa.literal_column("priority DESC"), "run_after", "created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('QUEUED', 'RETRY')"),
    )
    op.create_index(
        "ix_background_jobs_running_liveness",
        "background_jobs",
        ["heartbeat_at", "locked_at"],
        unique=False,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )


def downgrade() -> None:
    op.drop_index("ix_background_jobs_running_liveness", table_name="background_jobs")
    op.drop_index("ix_background_jobs_ready", table_name="background_jobs")
    op.drop_table("background_jobs")
    # The vector extension may be shared by other applications, so rollback
    # intentionally does not remove it.

