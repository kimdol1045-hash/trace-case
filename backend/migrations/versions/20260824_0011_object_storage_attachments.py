"""Add provider-neutral object keys for private R2 attachments.

Revision ID: 20260824_0011
Revises: 20260824_0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_0011"
down_revision: str | None = "20260824_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE attachments ADD COLUMN object_key text;
        UPDATE attachments SET object_key = drive_file_id;
        ALTER TABLE attachments ALTER COLUMN object_key SET NOT NULL;
        ALTER TABLE attachments ALTER COLUMN drive_file_id DROP NOT NULL;
        ALTER TABLE attachments DROP CONSTRAINT uq_attachments_drive_file_id;
        ALTER TABLE attachments DROP CONSTRAINT ck_attachments_storage_provider_allowed;
        ALTER TABLE attachments ADD CONSTRAINT ck_attachments_storage_provider_allowed
            CHECK (storage_provider IN ('GOOGLE_DRIVE', 'R2'));
        ALTER TABLE attachments ADD CONSTRAINT uq_attachments_provider_object_key
            UNIQUE (storage_provider, object_key);

        CREATE OR REPLACE FUNCTION tc_guard_attachment_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'attachments use soft delete' USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.resource_version <> 1 OR NEW.deleted_at IS NOT NULL
                   OR NEW.deletion_status <> 'ACTIVE' THEN
                    RAISE EXCEPTION 'invalid initial attachment state' USING ERRCODE = '23514';
                END IF;
                IF NEW.storage_provider = 'GOOGLE_DRIVE' AND NEW.drive_file_id IS NULL THEN
                    RAISE EXCEPTION 'Google Drive attachment requires drive_file_id'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.owner_type = 'ISSUE' AND NOT EXISTS (
                    SELECT 1 FROM issues WHERE id = NEW.owner_id
                     AND workspace_id = NEW.workspace_id AND deleted_at IS NULL
                ) THEN
                    RAISE EXCEPTION 'attachment issue owner was not found' USING ERRCODE = '23503';
                END IF;
                IF NEW.owner_type = 'EXECUTION_ATTEMPT' AND NOT EXISTS (
                    SELECT 1 FROM execution_attempts ea
                    JOIN cycle_items ci ON ci.id = ea.cycle_item_id
                    JOIN test_cycles tc ON tc.id = ci.cycle_id
                    WHERE ea.id = NEW.owner_id AND tc.workspace_id = NEW.workspace_id
                ) THEN
                    RAISE EXCEPTION 'attachment execution owner was not found'
                        USING ERRCODE = '23503';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.resource_version <> OLD.resource_version + 1 THEN
                RAISE EXCEPTION 'attachment resource_version must increase by exactly one'
                    USING ERRCODE = '23514';
            END IF;
            IF ROW(NEW.id, NEW.workspace_id, NEW.owner_type, NEW.owner_id, NEW.drive_file_id,
                   NEW.object_key, NEW.logical_path, NEW.file_name, NEW.checksum,
                   NEW.mime_type, NEW.size_bytes, NEW.storage_provider, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.workspace_id, OLD.owner_type, OLD.owner_id, OLD.drive_file_id,
                   OLD.object_key, OLD.logical_path, OLD.file_name, OLD.checksum,
                   OLD.mime_type, OLD.size_bytes, OLD.storage_provider, OLD.created_at) THEN
                RAISE EXCEPTION 'attachment identity and content metadata are immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
                RAISE EXCEPTION 'attachment soft deletion cannot be reversed'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM attachments WHERE storage_provider = 'R2';
        ALTER TABLE attachments DROP CONSTRAINT uq_attachments_provider_object_key;
        ALTER TABLE attachments DROP CONSTRAINT ck_attachments_storage_provider_allowed;
        ALTER TABLE attachments ADD CONSTRAINT ck_attachments_storage_provider_allowed
            CHECK (storage_provider = 'GOOGLE_DRIVE');
        ALTER TABLE attachments ALTER COLUMN drive_file_id SET NOT NULL;
        ALTER TABLE attachments ADD CONSTRAINT uq_attachments_drive_file_id UNIQUE (drive_file_id);
        ALTER TABLE attachments DROP COLUMN object_key;

        CREATE OR REPLACE FUNCTION tc_guard_attachment_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'attachments use soft delete' USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.resource_version <> 1 OR NEW.deleted_at IS NOT NULL
                   OR NEW.deletion_status <> 'ACTIVE' THEN
                    RAISE EXCEPTION 'invalid initial attachment state' USING ERRCODE = '23514';
                END IF;
                IF NEW.owner_type = 'ISSUE' AND NOT EXISTS (
                    SELECT 1 FROM issues WHERE id = NEW.owner_id
                     AND workspace_id = NEW.workspace_id AND deleted_at IS NULL
                ) THEN
                    RAISE EXCEPTION 'attachment issue owner was not found' USING ERRCODE = '23503';
                END IF;
                IF NEW.owner_type = 'EXECUTION_ATTEMPT' AND NOT EXISTS (
                    SELECT 1 FROM execution_attempts ea
                    JOIN cycle_items ci ON ci.id = ea.cycle_item_id
                    JOIN test_cycles tc ON tc.id = ci.cycle_id
                    WHERE ea.id = NEW.owner_id AND tc.workspace_id = NEW.workspace_id
                ) THEN
                    RAISE EXCEPTION 'attachment execution owner was not found'
                        USING ERRCODE = '23503';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.resource_version <> OLD.resource_version + 1 THEN
                RAISE EXCEPTION 'attachment resource_version must increase by exactly one'
                    USING ERRCODE = '23514';
            END IF;
            IF ROW(NEW.id, NEW.workspace_id, NEW.owner_type, NEW.owner_id, NEW.drive_file_id,
                   NEW.logical_path, NEW.file_name, NEW.checksum, NEW.mime_type, NEW.size_bytes,
                   NEW.storage_provider, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.workspace_id, OLD.owner_type, OLD.owner_id, OLD.drive_file_id,
                   OLD.logical_path, OLD.file_name, OLD.checksum, OLD.mime_type, OLD.size_bytes,
                   OLD.storage_provider, OLD.created_at) THEN
                RAISE EXCEPTION 'attachment identity and content metadata are immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
                RAISE EXCEPTION 'attachment soft deletion cannot be reversed'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$;
        """
    )
