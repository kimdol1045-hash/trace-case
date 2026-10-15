"""Issue, attachment, Drive integration, and reporting ledgers.

Revision ID: 20260820_0006
Revises: 20260820_0005
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260820_0006"
down_revision: str | None = "20260820_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE issues (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            workspace_id uuid NOT NULL,
            issue_key varchar(40) NOT NULL,
            summary varchar(200) NOT NULL,
            precondition text NOT NULL,
            expected_result text NOT NULL,
            actual_result text NOT NULL,
            source_note text,
            status varchar(20) DEFAULT 'OPEN' NOT NULL,
            severity varchar(20) DEFAULT 'MEDIUM' NOT NULL,
            assignee_label varchar(200),
            resource_version integer DEFAULT 1 NOT NULL,
            deleted_at timestamptz,
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_issues PRIMARY KEY (id),
            CONSTRAINT fk_issues_workspace_id_workspace_settings FOREIGN KEY (workspace_id)
                REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            CONSTRAINT uq_issues_workspace_key UNIQUE (workspace_id, issue_key),
            CONSTRAINT ck_issues_key_format CHECK
                (issue_key ~ '^[A-Z][A-Z0-9]{1,15}-[0-9]{6,}$'),
            CONSTRAINT ck_issues_summary_nonblank CHECK (char_length(btrim(summary)) > 0),
            CONSTRAINT ck_issues_precondition_nonblank CHECK (char_length(btrim(precondition)) > 0),
            CONSTRAINT ck_issues_expected_result_nonblank CHECK
                (char_length(btrim(expected_result)) > 0),
            CONSTRAINT ck_issues_actual_result_nonblank CHECK
                (char_length(btrim(actual_result)) > 0),
            CONSTRAINT ck_issues_status_allowed CHECK
                (status IN ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED', 'REOPENED')),
            CONSTRAINT ck_issues_severity_allowed CHECK
                (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
            CONSTRAINT ck_issues_resource_version_positive CHECK (resource_version >= 1),
            CONSTRAINT ck_issues_assignee_nonblank CHECK
                (assignee_label IS NULL OR char_length(btrim(assignee_label)) > 0),
            CONSTRAINT ck_issues_source_note_nonblank CHECK
                (source_note IS NULL OR char_length(btrim(source_note)) > 0)
        );
        CREATE INDEX ix_issues_workspace_status_updated
            ON issues (workspace_id, status, updated_at, id);
        CREATE INDEX ix_issues_workspace_severity_updated
            ON issues (workspace_id, severity, updated_at, id);
        CREATE INDEX ix_issues_workspace_deleted ON issues (workspace_id, deleted_at);

        CREATE TABLE issue_steps (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            issue_id uuid NOT NULL,
            sequence integer NOT NULL,
            action text NOT NULL,
            expected_result text,
            CONSTRAINT pk_issue_steps PRIMARY KEY (id),
            CONSTRAINT fk_issue_steps_issue_id_issues FOREIGN KEY (issue_id)
                REFERENCES issues(id) ON DELETE CASCADE,
            CONSTRAINT uq_issue_steps_issue_sequence UNIQUE (issue_id, sequence),
            CONSTRAINT ck_issue_steps_sequence_positive CHECK (sequence >= 1),
            CONSTRAINT ck_issue_steps_action_nonblank CHECK (char_length(btrim(action)) > 0),
            CONSTRAINT ck_issue_steps_expected_result_nonblank CHECK
                (expected_result IS NULL OR char_length(btrim(expected_result)) > 0)
        );

        CREATE TABLE issue_links (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            issue_id uuid NOT NULL,
            test_case_id uuid,
            test_case_version_id uuid,
            cycle_item_id uuid,
            execution_attempt_id uuid,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_issue_links PRIMARY KEY (id),
            CONSTRAINT fk_issue_links_issue_id_issues FOREIGN KEY (issue_id)
                REFERENCES issues(id) ON DELETE RESTRICT,
            CONSTRAINT fk_issue_links_test_case_id_test_cases FOREIGN KEY (test_case_id)
                REFERENCES test_cases(id) ON DELETE RESTRICT,
            CONSTRAINT fk_issue_links_pinned_version_same_case
                FOREIGN KEY (test_case_id, test_case_version_id)
                REFERENCES test_case_versions(test_case_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_issue_links_cycle_item_id_cycle_items FOREIGN KEY (cycle_item_id)
                REFERENCES cycle_items(id) ON DELETE RESTRICT,
            CONSTRAINT fk_issue_links_execution_attempt_id_execution_attempts
                FOREIGN KEY (execution_attempt_id)
                REFERENCES execution_attempts(id) ON DELETE RESTRICT,
            CONSTRAINT ck_issue_links_has_target CHECK
                (test_case_id IS NOT NULL OR cycle_item_id IS NOT NULL
                 OR execution_attempt_id IS NOT NULL),
            CONSTRAINT ck_issue_links_version_requires_case CHECK
                (test_case_version_id IS NULL OR test_case_id IS NOT NULL),
            CONSTRAINT ck_issue_links_attempt_requires_cycle_item CHECK
                (execution_attempt_id IS NULL OR cycle_item_id IS NOT NULL)
        );
        CREATE UNIQUE INDEX uq_issue_links_exact_target ON issue_links
            (issue_id, test_case_id, test_case_version_id, cycle_item_id, execution_attempt_id)
            NULLS NOT DISTINCT;
        CREATE INDEX ix_issue_links_test_case ON issue_links (test_case_id, issue_id);
        CREATE INDEX ix_issue_links_cycle_item ON issue_links (cycle_item_id, issue_id);
        CREATE INDEX ix_issue_links_attempt ON issue_links (execution_attempt_id, issue_id);

        CREATE TABLE comments (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            workspace_id uuid NOT NULL,
            owner_type varchar(40) DEFAULT 'ISSUE' NOT NULL,
            owner_id uuid NOT NULL,
            body text NOT NULL,
            author_label varchar(200),
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_comments PRIMARY KEY (id),
            CONSTRAINT fk_comments_workspace_id_workspace_settings FOREIGN KEY (workspace_id)
                REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            CONSTRAINT ck_comments_owner_type_allowed CHECK (owner_type = 'ISSUE'),
            CONSTRAINT ck_comments_body_nonblank CHECK (char_length(btrim(body)) > 0),
            CONSTRAINT ck_comments_author_nonblank CHECK
                (author_label IS NULL OR char_length(btrim(author_label)) > 0)
        );
        CREATE INDEX ix_comments_owner_created
            ON comments (owner_type, owner_id, created_at, id);

        CREATE TABLE attachments (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            workspace_id uuid NOT NULL,
            owner_type varchar(40) NOT NULL,
            owner_id uuid NOT NULL,
            drive_file_id varchar(255) NOT NULL,
            logical_path text NOT NULL,
            file_name varchar(500) NOT NULL,
            checksum varchar(64) NOT NULL,
            mime_type varchar(255) NOT NULL,
            size_bytes bigint NOT NULL,
            storage_provider varchar(40) DEFAULT 'GOOGLE_DRIVE' NOT NULL,
            storage_status varchar(20) DEFAULT 'AVAILABLE' NOT NULL,
            deletion_status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
            reconcile_status varchar(40) DEFAULT 'CONSISTENT' NOT NULL,
            reconcile_detail text,
            resource_version integer DEFAULT 1 NOT NULL,
            deleted_at timestamptz,
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_attachments PRIMARY KEY (id),
            CONSTRAINT fk_attachments_workspace_id_workspace_settings FOREIGN KEY (workspace_id)
                REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            CONSTRAINT uq_attachments_drive_file_id UNIQUE (drive_file_id),
            CONSTRAINT ck_attachments_owner_type_allowed CHECK
                (owner_type IN ('ISSUE', 'EXECUTION_ATTEMPT')),
            CONSTRAINT ck_attachments_file_name_nonblank CHECK
                (char_length(btrim(file_name)) > 0),
            CONSTRAINT ck_attachments_logical_path_nonblank CHECK
                (char_length(btrim(logical_path)) > 0),
            CONSTRAINT ck_attachments_mime_type_nonblank CHECK
                (char_length(btrim(mime_type)) > 0),
            CONSTRAINT ck_attachments_checksum_sha256 CHECK (checksum ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_attachments_size_nonnegative CHECK (size_bytes >= 0),
            CONSTRAINT ck_attachments_storage_provider_allowed CHECK
                (storage_provider = 'GOOGLE_DRIVE'),
            CONSTRAINT ck_attachments_storage_status_allowed CHECK
                (storage_status IN ('PENDING', 'AVAILABLE', 'MISSING', 'ERROR')),
            CONSTRAINT ck_attachments_deletion_status_allowed CHECK
                (deletion_status IN ('ACTIVE', 'PENDING', 'DELETED', 'FAILED')),
            CONSTRAINT ck_attachments_reconcile_status_allowed CHECK
                (reconcile_status IN ('PENDING', 'CONSISTENT', 'ORPHANED_DRIVE_FILE',
                                      'DANGLING_METADATA', 'DELETE_RETRY')),
            CONSTRAINT ck_attachments_resource_version_positive CHECK
                (resource_version >= 1),
            CONSTRAINT ck_attachments_deletion_state_matches_timestamp CHECK
                ((deleted_at IS NULL AND deletion_status = 'ACTIVE') OR
                 (deleted_at IS NOT NULL AND deletion_status IN ('PENDING', 'DELETED', 'FAILED')))
        );
        CREATE INDEX ix_attachments_owner_deleted
            ON attachments (workspace_id, owner_type, owner_id, deleted_at);
        CREATE INDEX ix_attachments_reconcile
            ON attachments (workspace_id, reconcile_status, updated_at);

        CREATE TABLE integration_settings (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            workspace_id uuid NOT NULL,
            provider varchar(40) NOT NULL,
            enabled boolean DEFAULT false NOT NULL,
            status varchar(20) DEFAULT 'DISCONNECTED' NOT NULL,
            credential_nonce bytea,
            credential_key_version integer,
            credential_ciphertext bytea,
            root_folder_id varchar(255),
            settings jsonb DEFAULT '{}'::jsonb NOT NULL,
            last_error text,
            resource_version integer DEFAULT 1 NOT NULL,
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_integration_settings PRIMARY KEY (id),
            CONSTRAINT fk_integration_settings_workspace_id_workspace_settings
                FOREIGN KEY (workspace_id) REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            CONSTRAINT uq_integration_settings_workspace_provider UNIQUE (workspace_id, provider),
            CONSTRAINT ck_integration_settings_provider_allowed CHECK (provider = 'GOOGLE_DRIVE'),
            CONSTRAINT ck_integration_settings_status_allowed CHECK
                (status IN ('DISCONNECTED', 'CONNECTED', 'REVOKED', 'ERROR')),
            CONSTRAINT ck_integration_settings_credentials_complete CHECK
                ((credential_nonce IS NULL AND credential_key_version IS NULL
                  AND credential_ciphertext IS NULL) OR
                 (credential_nonce IS NOT NULL AND credential_key_version IS NOT NULL
                  AND credential_key_version >= 1 AND credential_ciphertext IS NOT NULL)),
            CONSTRAINT ck_integration_settings_settings_object CHECK
                (jsonb_typeof(settings) = 'object'),
            CONSTRAINT ck_integration_settings_resource_version_positive CHECK
                (resource_version >= 1)
        );

        CREATE TABLE drive_usage_snapshots (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            workspace_id uuid NOT NULL,
            integration_setting_id uuid NOT NULL,
            used_bytes bigint NOT NULL,
            limit_bytes bigint NOT NULL,
            warning_level varchar(20) NOT NULL,
            collected_at timestamptz DEFAULT now() NOT NULL,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_drive_usage_snapshots PRIMARY KEY (id),
            CONSTRAINT fk_drive_usage_snapshots_workspace_id_workspace_settings
                FOREIGN KEY (workspace_id) REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            CONSTRAINT fk_drive_usage_snapshots_integration_setting_id_integration_settings
                FOREIGN KEY (integration_setting_id) REFERENCES integration_settings(id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_drive_usage_snapshots_used_nonnegative CHECK (used_bytes >= 0),
            CONSTRAINT ck_drive_usage_snapshots_limit_positive CHECK (limit_bytes > 0),
            CONSTRAINT ck_drive_usage_snapshots_warning_level_allowed CHECK
                (warning_level IN ('NONE', '70', '85', '95', 'EXCEEDED')),
            CONSTRAINT ck_drive_usage_snapshots_warning_matches_usage CHECK
                ((warning_level = 'EXCEEDED' AND used_bytes >= limit_bytes) OR
                 (warning_level = '95' AND used_bytes < limit_bytes
                  AND used_bytes::numeric / limit_bytes >= 0.95) OR
                 (warning_level = '85' AND used_bytes::numeric / limit_bytes >= 0.85
                  AND used_bytes::numeric / limit_bytes < 0.95) OR
                 (warning_level = '70' AND used_bytes::numeric / limit_bytes >= 0.70
                  AND used_bytes::numeric / limit_bytes < 0.85) OR
                 (warning_level = 'NONE' AND used_bytes::numeric / limit_bytes < 0.70))
        );
        CREATE INDEX ix_drive_usage_workspace_collected
            ON drive_usage_snapshots (workspace_id, collected_at, id);

        CREATE TABLE drive_reconciliation_records (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            workspace_id uuid NOT NULL,
            attachment_id uuid,
            drive_file_id varchar(255) NOT NULL,
            finding_type varchar(40) NOT NULL,
            status varchar(20) DEFAULT 'OPEN' NOT NULL,
            details jsonb DEFAULT '{}'::jsonb NOT NULL,
            created_at timestamptz DEFAULT now() NOT NULL,
            resolved_at timestamptz,
            CONSTRAINT pk_drive_reconciliation_records PRIMARY KEY (id),
            CONSTRAINT fk_drive_reconciliation_records_workspace_id_workspace_settings
                FOREIGN KEY (workspace_id) REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            CONSTRAINT fk_drive_reconciliation_records_attachment_id_attachments
                FOREIGN KEY (attachment_id) REFERENCES attachments(id) ON DELETE RESTRICT,
            CONSTRAINT ck_drive_reconciliation_records_finding_type_allowed CHECK
                (finding_type IN ('ORPHANED_DRIVE_FILE', 'DANGLING_METADATA')),
            CONSTRAINT ck_drive_reconciliation_records_status_allowed CHECK
                (status IN ('OPEN', 'RESOLVED')),
            CONSTRAINT ck_drive_reconciliation_records_details_object CHECK
                (jsonb_typeof(details) = 'object'),
            CONSTRAINT ck_drive_reconciliation_records_resolved_at_matches_status CHECK
                ((status = 'OPEN' AND resolved_at IS NULL) OR
                 (status = 'RESOLVED' AND resolved_at IS NOT NULL))
        );
        CREATE INDEX ix_drive_reconcile_open
            ON drive_reconciliation_records (workspace_id, status, finding_type, created_at);

        CREATE TABLE outbox_events (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            workspace_id uuid NOT NULL,
            aggregate_type varchar(64) NOT NULL,
            aggregate_id uuid NOT NULL,
            event_type varchar(64) NOT NULL,
            aggregate_version integer NOT NULL,
            payload jsonb DEFAULT '{}'::jsonb NOT NULL,
            status varchar(20) DEFAULT 'READY' NOT NULL,
            attempts integer DEFAULT 0 NOT NULL,
            available_at timestamptz DEFAULT now() NOT NULL,
            occurred_at timestamptz DEFAULT now() NOT NULL,
            processed_at timestamptz,
            CONSTRAINT pk_outbox_events PRIMARY KEY (id),
            CONSTRAINT fk_outbox_events_workspace_id_workspace_settings FOREIGN KEY (workspace_id)
                REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            CONSTRAINT uq_outbox_events_aggregate_event_version UNIQUE
                (aggregate_type, aggregate_id, event_type, aggregate_version),
            CONSTRAINT ck_outbox_events_aggregate_type_format CHECK
                (aggregate_type ~ '^[A-Z][A-Z0-9_]*$'),
            CONSTRAINT ck_outbox_events_event_type_format CHECK
                (event_type ~ '^[A-Z][A-Z0-9_]*$'),
            CONSTRAINT ck_outbox_events_aggregate_version_positive CHECK
                (aggregate_version >= 1),
            CONSTRAINT ck_outbox_events_status_allowed CHECK
                (status IN ('READY', 'PROCESSING', 'PUBLISHED', 'FAILED', 'DEAD')),
            CONSTRAINT ck_outbox_events_attempts_nonnegative CHECK (attempts >= 0),
            CONSTRAINT ck_outbox_events_payload_object CHECK (jsonb_typeof(payload) = 'object')
        );
        CREATE INDEX ix_outbox_events_ready ON outbox_events (available_at, occurred_at)
            WHERE status IN ('READY', 'FAILED');
        """
    )
    _create_issue_guards()
    _create_link_and_comment_guards()
    _create_attachment_and_drive_guards()


def _create_issue_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_guard_issue_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'issues use soft delete' USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'OPEN' OR NEW.resource_version <> 1 OR NEW.deleted_at IS NOT NULL THEN
                    RAISE EXCEPTION 'an issue must be created OPEN at resource version one'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.deleted_at IS NOT NULL THEN
                RAISE EXCEPTION 'deleted issues are immutable' USING ERRCODE = '55000';
            END IF;
            IF NEW.resource_version <> OLD.resource_version + 1 THEN
                RAISE EXCEPTION 'issue resource_version must increase by exactly one'
                    USING ERRCODE = '23514';
            END IF;
            IF ROW(NEW.id, NEW.workspace_id, NEW.issue_key, NEW.created_at)
               IS DISTINCT FROM ROW(OLD.id, OLD.workspace_id, OLD.issue_key, OLD.created_at) THEN
                RAISE EXCEPTION 'issue identity is immutable' USING ERRCODE = '55000';
            END IF;
            IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NULL AND NEW.status <> OLD.status THEN
                IF NOT (
                    (OLD.status = 'OPEN' AND NEW.status = 'IN_PROGRESS') OR
                    (OLD.status = 'IN_PROGRESS' AND NEW.status = 'RESOLVED') OR
                    (OLD.status = 'RESOLVED' AND NEW.status IN ('CLOSED', 'REOPENED')) OR
                    (OLD.status = 'CLOSED' AND NEW.status = 'REOPENED') OR
                    (OLD.status = 'REOPENED' AND NEW.status = 'OPEN')
                ) THEN
                    RAISE EXCEPTION 'invalid issue status transition: % -> %', OLD.status, NEW.status
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION 'soft delete cannot also change issue status'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER trg_issues_guard
            BEFORE INSERT OR UPDATE OR DELETE ON issues
            FOR EACH ROW EXECUTE FUNCTION tc_guard_issue_mutation();

        CREATE FUNCTION tc_require_issue_steps()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE target_issue_id uuid;
        BEGIN
            IF TG_TABLE_NAME = 'issues' THEN
                target_issue_id := NEW.id;
            ELSIF TG_OP = 'DELETE' THEN
                target_issue_id := OLD.issue_id;
            ELSE
                target_issue_id := NEW.issue_id;
            END IF;
            IF EXISTS (SELECT 1 FROM issues WHERE id = target_issue_id)
               AND NOT EXISTS (SELECT 1 FROM issue_steps WHERE issue_id = target_issue_id) THEN
                RAISE EXCEPTION 'an issue requires at least one reproduction step'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END
        $$;
        CREATE CONSTRAINT TRIGGER trg_issues_require_steps
            AFTER INSERT OR UPDATE ON issues DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_require_issue_steps();
        CREATE CONSTRAINT TRIGGER trg_issue_steps_require_one
            AFTER INSERT OR UPDATE OR DELETE ON issue_steps DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_require_issue_steps();
        """
    )


def _create_link_and_comment_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_guard_issue_link_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            item_case_id uuid;
            item_version_id uuid;
            item_cycle_id uuid;
            attempt_item_id uuid;
            existing_cycle_id uuid;
            issue_workspace_id uuid;
            target_workspace_id uuid;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'issue links are append-only' USING ERRCODE = '55000';
            END IF;
            SELECT workspace_id INTO issue_workspace_id FROM issues
                WHERE id = NEW.issue_id AND deleted_at IS NULL;
            IF issue_workspace_id IS NULL THEN
                RAISE EXCEPTION 'issue link owner must be an active issue' USING ERRCODE = '23503';
            END IF;
            IF NEW.execution_attempt_id IS NOT NULL THEN
                SELECT cycle_item_id INTO attempt_item_id FROM execution_attempts
                    WHERE id = NEW.execution_attempt_id;
                IF attempt_item_id IS NULL OR attempt_item_id <> NEW.cycle_item_id THEN
                    RAISE EXCEPTION 'execution attempt does not belong to the linked cycle item'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF NEW.cycle_item_id IS NOT NULL THEN
                SELECT ci.test_case_id, ci.test_case_version_id, ci.cycle_id, tc.workspace_id
                    INTO item_case_id, item_version_id, item_cycle_id, target_workspace_id
                    FROM cycle_items ci
                    JOIN test_cycles tc ON tc.id = ci.cycle_id
                    WHERE ci.id = NEW.cycle_item_id;
                IF item_cycle_id IS NULL OR NEW.test_case_id IS DISTINCT FROM item_case_id THEN
                    RAISE EXCEPTION 'cycle item does not belong to the linked test case'
                        USING ERRCODE = '23514';
                END IF;
                IF target_workspace_id <> issue_workspace_id THEN
                    RAISE EXCEPTION 'issue link target belongs to a different workspace'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.test_case_version_id IS DISTINCT FROM item_version_id THEN
                    RAISE EXCEPTION 'issue link must preserve the cycle pinned case version'
                        USING ERRCODE = '23514';
                END IF;
                SELECT ci.cycle_id INTO existing_cycle_id
                  FROM issue_links link
                  JOIN cycle_items ci ON ci.id = link.cycle_item_id
                 WHERE link.issue_id = NEW.issue_id
                 LIMIT 1;
                IF existing_cycle_id IS NOT NULL AND existing_cycle_id <> item_cycle_id THEN
                    RAISE EXCEPTION 'an issue cannot link cycle evidence from a different cycle'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NOT EXISTS (
                SELECT 1 FROM test_cases
                 WHERE id = NEW.test_case_id AND workspace_id = issue_workspace_id
            ) THEN
                RAISE EXCEPTION 'issue link test case belongs to a different workspace'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER trg_issue_links_append_only
            BEFORE INSERT OR UPDATE OR DELETE ON issue_links
            FOR EACH ROW EXECUTE FUNCTION tc_guard_issue_link_mutation();

        CREATE FUNCTION tc_guard_comment_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'comments are append-only' USING ERRCODE = '55000';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM issues
                 WHERE id = NEW.owner_id AND workspace_id = NEW.workspace_id
                   AND deleted_at IS NULL
            ) THEN
                RAISE EXCEPTION 'comment owner must be an active workspace issue'
                    USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER trg_comments_append_only
            BEFORE INSERT OR UPDATE OR DELETE ON comments
            FOR EACH ROW EXECUTE FUNCTION tc_guard_comment_append_only();
        """
    )


def _create_attachment_and_drive_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_guard_attachment_mutation()
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
        CREATE TRIGGER trg_attachments_guard
            BEFORE INSERT OR UPDATE OR DELETE ON attachments
            FOR EACH ROW EXECUTE FUNCTION tc_guard_attachment_mutation();

        CREATE FUNCTION tc_guard_integration_setting_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'integration settings cannot be physically deleted'
                    USING ERRCODE = '55000';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.resource_version <> 1 THEN
                    RAISE EXCEPTION 'integration resource_version must start at one'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.resource_version <> OLD.resource_version + 1 THEN
                RAISE EXCEPTION 'integration resource_version must increase by exactly one'
                    USING ERRCODE = '23514';
            END IF;
            IF ROW(NEW.id, NEW.workspace_id, NEW.provider, NEW.created_at)
               IS DISTINCT FROM ROW(OLD.id, OLD.workspace_id, OLD.provider, OLD.created_at) THEN
                RAISE EXCEPTION 'integration identity is immutable' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER trg_integration_settings_guard
            BEFORE INSERT OR UPDATE OR DELETE ON integration_settings
            FOR EACH ROW EXECUTE FUNCTION tc_guard_integration_setting_mutation();

        CREATE FUNCTION tc_guard_drive_usage_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'Drive usage snapshots are append-only' USING ERRCODE = '55000';
        END
        $$;
        CREATE TRIGGER trg_drive_usage_snapshots_immutable
            BEFORE UPDATE OR DELETE ON drive_usage_snapshots
            FOR EACH ROW EXECUTE FUNCTION tc_guard_drive_usage_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_drive_usage_snapshots_immutable ON drive_usage_snapshots")
    op.execute("DROP TRIGGER IF EXISTS trg_integration_settings_guard ON integration_settings")
    op.execute("DROP TRIGGER IF EXISTS trg_attachments_guard ON attachments")
    op.execute("DROP TRIGGER IF EXISTS trg_comments_append_only ON comments")
    op.execute("DROP TRIGGER IF EXISTS trg_issue_links_append_only ON issue_links")
    op.execute("DROP TRIGGER IF EXISTS trg_issue_steps_require_one ON issue_steps")
    op.execute("DROP TRIGGER IF EXISTS trg_issues_require_steps ON issues")
    op.execute("DROP TRIGGER IF EXISTS trg_issues_guard ON issues")

    op.drop_table("outbox_events")
    op.drop_table("drive_reconciliation_records")
    op.drop_table("drive_usage_snapshots")
    op.drop_table("integration_settings")
    op.drop_table("attachments")
    op.drop_table("comments")
    op.drop_table("issue_links")
    op.drop_table("issue_steps")
    op.drop_table("issues")

    op.execute("DROP FUNCTION IF EXISTS tc_guard_drive_usage_immutable()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_integration_setting_mutation()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_attachment_mutation()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_comment_append_only()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_issue_link_mutation()")
    op.execute("DROP FUNCTION IF EXISTS tc_require_issue_steps()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_issue_mutation()")
