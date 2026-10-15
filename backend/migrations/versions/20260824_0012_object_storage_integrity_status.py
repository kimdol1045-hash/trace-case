"""Add provider-neutral attachment metadata mismatch reconciliation.

Revision ID: 20260824_0012
Revises: 20260824_0011
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_0012"
down_revision: str | None = "20260824_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE attachments DROP CONSTRAINT ck_attachments_reconcile_status_allowed;
        ALTER TABLE attachments ADD CONSTRAINT ck_attachments_reconcile_status_allowed
            CHECK (reconcile_status IN (
                'PENDING', 'CONSISTENT', 'ORPHANED_DRIVE_FILE',
                'DANGLING_METADATA', 'METADATA_MISMATCH', 'DELETE_RETRY'
            ));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE attachments
           SET reconcile_status = 'PENDING',
               reconcile_detail = 'downgraded from METADATA_MISMATCH'
         WHERE reconcile_status = 'METADATA_MISMATCH';
        ALTER TABLE attachments DROP CONSTRAINT ck_attachments_reconcile_status_allowed;
        ALTER TABLE attachments ADD CONSTRAINT ck_attachments_reconcile_status_allowed
            CHECK (reconcile_status IN (
                'PENDING', 'CONSISTENT', 'ORPHANED_DRIVE_FILE',
                'DANGLING_METADATA', 'DELETE_RETRY'
            ));
        """
    )
