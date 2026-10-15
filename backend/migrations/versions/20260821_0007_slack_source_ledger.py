"""Slack allowlist, delivery, source revision, sync, and quiet-window ledger.

Revision ID: 20260821_0007
Revises: 20260820_0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_0007"
down_revision: str | None = "20260820_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE slack_channel_allowlist (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            workspace_id uuid NOT NULL,
            slack_workspace_id varchar(80) NOT NULL,
            channel_id varchar(80) NOT NULL,
            channel_name varchar(200),
            is_private boolean DEFAULT false NOT NULL,
            include_private boolean DEFAULT false NOT NULL,
            enabled boolean DEFAULT true NOT NULL,
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_slack_channel_allowlist PRIMARY KEY (id),
            CONSTRAINT fk_slack_channel_allowlist_workspace_id_workspace_settings
                FOREIGN KEY (workspace_id) REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            CONSTRAINT uq_slack_channel_allowlist_workspace_channel
                UNIQUE (workspace_id, slack_workspace_id, channel_id),
            CONSTRAINT ck_slack_channel_allowlist_workspace_nonblank CHECK
                (char_length(btrim(slack_workspace_id)) > 0),
            CONSTRAINT ck_slack_channel_allowlist_channel_nonblank CHECK
                (char_length(btrim(channel_id)) > 0),
            CONSTRAINT ck_slack_channel_allowlist_name_nonblank CHECK
                (channel_name IS NULL OR char_length(btrim(channel_name)) > 0),
            CONSTRAINT ck_slack_channel_allowlist_private_explicit CHECK
                (NOT include_private OR is_private)
        );
        CREATE INDEX ix_slack_channel_allowlist_workspace_enabled
            ON slack_channel_allowlist (workspace_id, enabled, slack_workspace_id, channel_id);

        CREATE TABLE slack_events (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            workspace_id uuid NOT NULL,
            slack_workspace_id varchar(80) NOT NULL,
            channel_id varchar(80) NOT NULL,
            event_id varchar(255) NOT NULL,
            event_type varchar(40) NOT NULL,
            event_ts varchar(32) NOT NULL,
            event_ts_key bigint NOT NULL,
            payload jsonb DEFAULT '{}'::jsonb NOT NULL,
            status varchar(20) DEFAULT 'RECEIVED' NOT NULL,
            received_at timestamptz DEFAULT now() NOT NULL,
            processed_at timestamptz,
            retry_at timestamptz,
            error text,
            CONSTRAINT pk_slack_events PRIMARY KEY (id),
            CONSTRAINT fk_slack_events_workspace_id_workspace_settings
                FOREIGN KEY (workspace_id) REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            CONSTRAINT fk_slack_events_allowlisted_channel
                FOREIGN KEY (workspace_id, slack_workspace_id, channel_id)
                REFERENCES slack_channel_allowlist
                    (workspace_id, slack_workspace_id, channel_id) ON DELETE RESTRICT,
            CONSTRAINT uq_slack_events_workspace_event_id UNIQUE (workspace_id, event_id),
            CONSTRAINT uq_slack_events_workspace_channel_event_ts
                UNIQUE (workspace_id, slack_workspace_id, channel_id, event_ts),
            CONSTRAINT ck_slack_events_type_allowed CHECK
                (event_type IN ('ROOT', 'REPLY', 'EDIT', 'DELETE', 'THREAD_SYNC')),
            CONSTRAINT ck_slack_events_ts_format CHECK
                (event_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'),
            CONSTRAINT ck_slack_events_ts_key_matches CHECK
                (event_ts_key = replace(event_ts, '.', '')::bigint),
            CONSTRAINT ck_slack_events_payload_object CHECK (jsonb_typeof(payload) = 'object'),
            CONSTRAINT ck_slack_events_status_allowed CHECK
                (status IN ('RECEIVED', 'PROCESSED', 'IGNORED', 'FAILED')),
            CONSTRAINT ck_slack_events_processed_matches_status CHECK
                ((status IN ('PROCESSED', 'IGNORED') AND processed_at IS NOT NULL) OR
                 (status IN ('RECEIVED', 'FAILED') AND processed_at IS NULL)),
            CONSTRAINT ck_slack_events_retry_matches_failed CHECK
                (retry_at IS NULL OR status = 'FAILED')
        );
        CREATE INDEX ix_slack_events_processing
            ON slack_events (workspace_id, status, retry_at, received_at);

        CREATE TABLE slack_work_items (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            workspace_id uuid NOT NULL,
            slack_workspace_id varchar(80) NOT NULL,
            channel_id varchar(80) NOT NULL,
            message_ts varchar(32) NOT NULL,
            message_ts_key bigint NOT NULL,
            permalink text,
            root_seen boolean DEFAULT false NOT NULL,
            source_version integer DEFAULT 0 NOT NULL,
            parse_status varchar(20) DEFAULT 'PENDING' NOT NULL,
            parse_result jsonb,
            last_activity_ts varchar(32),
            last_activity_ts_key bigint,
            last_activity_at timestamptz,
            quiet_due_at timestamptz,
            quiet_job_id uuid,
            created_event_id uuid NOT NULL,
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_slack_work_items PRIMARY KEY (id),
            CONSTRAINT fk_slack_work_items_workspace_id_workspace_settings
                FOREIGN KEY (workspace_id) REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            CONSTRAINT fk_slack_work_items_allowlisted_channel
                FOREIGN KEY (workspace_id, slack_workspace_id, channel_id)
                REFERENCES slack_channel_allowlist
                    (workspace_id, slack_workspace_id, channel_id) ON DELETE RESTRICT,
            CONSTRAINT fk_slack_work_items_created_event_id_slack_events
                FOREIGN KEY (created_event_id) REFERENCES slack_events(id) ON DELETE RESTRICT,
            CONSTRAINT fk_slack_work_items_quiet_job_id_background_jobs
                FOREIGN KEY (quiet_job_id) REFERENCES background_jobs(id) ON DELETE RESTRICT,
            CONSTRAINT uq_slack_work_items_workspace_channel_message
                UNIQUE (workspace_id, slack_workspace_id, channel_id, message_ts),
            CONSTRAINT ck_slack_work_items_message_ts_format CHECK
                (message_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'),
            CONSTRAINT ck_slack_work_items_message_ts_key_matches CHECK
                (message_ts_key = replace(message_ts, '.', '')::bigint),
            CONSTRAINT ck_slack_work_items_source_version_nonnegative CHECK (source_version >= 0),
            CONSTRAINT ck_slack_work_items_parse_status_allowed CHECK
                (parse_status IN ('PENDING', 'PARSED', 'NEEDS_REVIEW', 'FAILED')),
            CONSTRAINT ck_slack_work_items_parse_result_object CHECK
                (parse_result IS NULL OR jsonb_typeof(parse_result) = 'object'),
            CONSTRAINT ck_slack_work_items_last_activity_complete CHECK
                ((last_activity_ts IS NULL AND last_activity_ts_key IS NULL
                  AND last_activity_at IS NULL AND quiet_due_at IS NULL) OR
                 (last_activity_ts IS NOT NULL AND last_activity_ts_key IS NOT NULL
                  AND last_activity_at IS NOT NULL AND quiet_due_at IS NOT NULL)),
            CONSTRAINT ck_slack_work_items_last_activity_ts_format CHECK
                (last_activity_ts IS NULL OR last_activity_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'),
            CONSTRAINT ck_slack_work_items_last_activity_ts_key_matches CHECK
                (last_activity_ts IS NULL OR
                 last_activity_ts_key = replace(last_activity_ts, '.', '')::bigint),
            CONSTRAINT ck_slack_work_items_quiet_job_requires_due CHECK
                (quiet_job_id IS NULL OR quiet_due_at IS NOT NULL)
        );
        CREATE INDEX ix_slack_work_items_quiet_due
            ON slack_work_items (quiet_due_at, id) WHERE quiet_due_at IS NOT NULL;

        CREATE TABLE slack_threads (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            slack_work_item_id uuid NOT NULL,
            thread_ts varchar(32) NOT NULL,
            thread_ts_key bigint NOT NULL,
            sync_status varchar(20) DEFAULT 'PENDING' NOT NULL,
            sync_cursor text,
            sync_watermark_ts varchar(32),
            sync_watermark_ts_key bigint,
            last_synced_at timestamptz,
            sync_attempts integer DEFAULT 0 NOT NULL,
            max_sync_attempts integer DEFAULT 5 NOT NULL,
            retry_at timestamptz,
            last_error text,
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_slack_threads PRIMARY KEY (id),
            CONSTRAINT fk_slack_threads_slack_work_item_id_slack_work_items
                FOREIGN KEY (slack_work_item_id) REFERENCES slack_work_items(id) ON DELETE RESTRICT,
            CONSTRAINT uq_slack_threads_work_item UNIQUE (slack_work_item_id),
            CONSTRAINT ck_slack_threads_ts_format CHECK
                (thread_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'),
            CONSTRAINT ck_slack_threads_ts_key_matches CHECK
                (thread_ts_key = replace(thread_ts, '.', '')::bigint),
            CONSTRAINT ck_slack_threads_status_allowed CHECK
                (sync_status IN ('PENDING', 'RUNNING', 'SYNCED', 'RETRY', 'DEAD')),
            CONSTRAINT ck_slack_threads_attempts_nonnegative CHECK (sync_attempts >= 0),
            CONSTRAINT ck_slack_threads_max_attempts_positive CHECK (max_sync_attempts >= 1),
            CONSTRAINT ck_slack_threads_watermark_complete CHECK
                ((sync_watermark_ts IS NULL AND sync_watermark_ts_key IS NULL) OR
                 (sync_watermark_ts IS NOT NULL AND sync_watermark_ts_key IS NOT NULL)),
            CONSTRAINT ck_slack_threads_watermark_ts_format CHECK
                (sync_watermark_ts IS NULL OR sync_watermark_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'),
            CONSTRAINT ck_slack_threads_watermark_ts_key_matches CHECK
                (sync_watermark_ts IS NULL OR
                 sync_watermark_ts_key = replace(sync_watermark_ts, '.', '')::bigint),
            CONSTRAINT ck_slack_threads_retry_matches_status CHECK
                (retry_at IS NULL OR sync_status = 'RETRY'),
            CONSTRAINT ck_slack_threads_dead_exhausted CHECK
                (sync_status <> 'DEAD' OR sync_attempts >= max_sync_attempts)
        );
        CREATE INDEX ix_slack_threads_resync
            ON slack_threads (sync_status, last_synced_at, retry_at, id);

        CREATE TABLE slack_messages (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            thread_id uuid NOT NULL,
            message_ts varchar(32) NOT NULL,
            message_ts_key bigint NOT NULL,
            author_id varchar(200),
            permalink text,
            is_root boolean DEFAULT false NOT NULL,
            first_event_id uuid,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_slack_messages PRIMARY KEY (id),
            CONSTRAINT fk_slack_messages_thread_id_slack_threads
                FOREIGN KEY (thread_id) REFERENCES slack_threads(id) ON DELETE RESTRICT,
            CONSTRAINT fk_slack_messages_first_event_id_slack_events
                FOREIGN KEY (first_event_id) REFERENCES slack_events(id) ON DELETE RESTRICT,
            CONSTRAINT uq_slack_messages_thread_message UNIQUE (thread_id, message_ts),
            CONSTRAINT ck_slack_messages_ts_format CHECK
                (message_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'),
            CONSTRAINT ck_slack_messages_ts_key_matches CHECK
                (message_ts_key = replace(message_ts, '.', '')::bigint),
            CONSTRAINT ck_slack_messages_author_nonblank CHECK
                (author_id IS NULL OR char_length(btrim(author_id)) > 0)
        );
        CREATE UNIQUE INDEX uq_slack_messages_thread_root
            ON slack_messages (thread_id) WHERE is_root;
        CREATE INDEX ix_slack_messages_thread_chronology
            ON slack_messages (thread_id, message_ts_key, id);

        CREATE TABLE slack_message_revisions (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            message_id uuid NOT NULL,
            source_event_id uuid,
            source_kind varchar(20) DEFAULT 'EVENT' NOT NULL,
            revision bigint NOT NULL,
            revision_ts varchar(32) NOT NULL,
            body text,
            is_deleted boolean DEFAULT false NOT NULL,
            edited_at timestamptz,
            deleted_at timestamptz,
            raw_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
            received_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_slack_message_revisions PRIMARY KEY (id),
            CONSTRAINT fk_slack_message_revisions_message_id_slack_messages
                FOREIGN KEY (message_id) REFERENCES slack_messages(id) ON DELETE RESTRICT,
            CONSTRAINT fk_slack_message_revisions_source_event_id_slack_events
                FOREIGN KEY (source_event_id) REFERENCES slack_events(id) ON DELETE RESTRICT,
            CONSTRAINT uq_slack_message_revisions_message_revision UNIQUE (message_id, revision),
            CONSTRAINT uq_slack_message_revisions_message_revision_ts
                UNIQUE (message_id, revision_ts),
            CONSTRAINT ck_slack_message_revisions_source_kind_allowed CHECK
                (source_kind IN ('EVENT', 'THREAD_SYNC')),
            CONSTRAINT ck_slack_message_revisions_source_matches_kind CHECK
                ((source_kind = 'EVENT' AND source_event_id IS NOT NULL) OR
                 (source_kind = 'THREAD_SYNC' AND source_event_id IS NULL)),
            CONSTRAINT ck_slack_message_revisions_revision_positive CHECK (revision > 0),
            CONSTRAINT ck_slack_message_revisions_ts_format CHECK
                (revision_ts ~ '^[0-9]{10,12}\\.[0-9]{6}$'),
            CONSTRAINT ck_slack_message_revisions_revision_matches_ts CHECK
                (revision = replace(revision_ts, '.', '')::bigint),
            CONSTRAINT ck_slack_message_revisions_tombstone_shape CHECK
                ((is_deleted AND body IS NULL AND deleted_at IS NOT NULL) OR
                 (NOT is_deleted AND body IS NOT NULL AND deleted_at IS NULL)),
            CONSTRAINT ck_slack_message_revisions_raw_payload_object CHECK
                (jsonb_typeof(raw_payload) = 'object')
        );
        CREATE UNIQUE INDEX uq_slack_message_revisions_source_event
            ON slack_message_revisions (source_event_id) WHERE source_event_id IS NOT NULL;
        CREATE INDEX ix_slack_message_revisions_message_chronology
            ON slack_message_revisions (message_id, revision, id);

        CREATE TABLE slack_parse_results (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            slack_work_item_id uuid NOT NULL,
            source_version integer NOT NULL,
            parser_version varchar(100) NOT NULL,
            status varchar(20) NOT NULL,
            result jsonb DEFAULT '{}'::jsonb NOT NULL,
            missing_fields jsonb DEFAULT '[]'::jsonb NOT NULL,
            confidence numeric(5,4),
            error text,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_slack_parse_results PRIMARY KEY (id),
            CONSTRAINT fk_slack_parse_results_slack_work_item_id_slack_work_items
                FOREIGN KEY (slack_work_item_id) REFERENCES slack_work_items(id) ON DELETE RESTRICT,
            CONSTRAINT uq_slack_parse_results_source_parser
                UNIQUE (slack_work_item_id, source_version, parser_version),
            CONSTRAINT ck_slack_parse_results_source_version_positive CHECK (source_version >= 1),
            CONSTRAINT ck_slack_parse_results_parser_nonblank CHECK
                (char_length(btrim(parser_version)) > 0),
            CONSTRAINT ck_slack_parse_results_status_allowed CHECK
                (status IN ('PARSED', 'NEEDS_REVIEW', 'FAILED')),
            CONSTRAINT ck_slack_parse_results_result_object CHECK (jsonb_typeof(result) = 'object'),
            CONSTRAINT ck_slack_parse_results_missing_fields_array CHECK
                (jsonb_typeof(missing_fields) = 'array'),
            CONSTRAINT ck_slack_parse_results_confidence_range CHECK
                (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            CONSTRAINT ck_slack_parse_results_error_matches_status CHECK
                ((status = 'FAILED' AND error IS NOT NULL) OR
                 (status <> 'FAILED' AND error IS NULL))
        );
        CREATE INDEX ix_slack_parse_results_work_item_created
            ON slack_parse_results (slack_work_item_id, source_version, created_at);
        """
    )
    _create_guards()


def _create_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_guard_slack_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'slack_events cannot be deleted' USING ERRCODE = '55000';
            END IF;
            IF ROW(NEW.id, NEW.workspace_id, NEW.slack_workspace_id, NEW.channel_id,
                   NEW.event_id, NEW.event_type, NEW.event_ts, NEW.event_ts_key,
                   NEW.payload, NEW.received_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.workspace_id, OLD.slack_workspace_id, OLD.channel_id,
                   OLD.event_id, OLD.event_type, OLD.event_ts, OLD.event_ts_key,
                   OLD.payload, OLD.received_at) THEN
                RAISE EXCEPTION 'slack event identity and payload are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER trg_slack_events_guard
            BEFORE UPDATE OR DELETE ON slack_events
            FOR EACH ROW EXECUTE FUNCTION tc_guard_slack_event_mutation();

        CREATE FUNCTION tc_guard_slack_work_item_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'slack_work_items cannot be deleted' USING ERRCODE = '55000';
            END IF;
            IF ROW(NEW.id, NEW.workspace_id, NEW.slack_workspace_id, NEW.channel_id,
                   NEW.message_ts, NEW.message_ts_key, NEW.created_event_id, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.workspace_id, OLD.slack_workspace_id, OLD.channel_id,
                   OLD.message_ts, OLD.message_ts_key, OLD.created_event_id, OLD.created_at) THEN
                RAISE EXCEPTION 'slack work item identity is immutable' USING ERRCODE = '55000';
            END IF;
            IF NEW.source_version < OLD.source_version
               OR NEW.source_version > OLD.source_version + 1 THEN
                RAISE EXCEPTION 'slack source_version must stay or increase by one'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.root_seen AND NOT NEW.root_seen THEN
                RAISE EXCEPTION 'slack root_seen cannot be reversed' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER trg_slack_work_items_guard
            BEFORE UPDATE OR DELETE ON slack_work_items
            FOR EACH ROW EXECUTE FUNCTION tc_guard_slack_work_item_mutation();

        CREATE FUNCTION tc_guard_slack_thread_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE expected_thread_ts varchar(32);
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'slack_threads cannot be deleted' USING ERRCODE = '55000';
            END IF;
            SELECT message_ts INTO expected_thread_ts
              FROM slack_work_items WHERE id = NEW.slack_work_item_id;
            IF expected_thread_ts IS NULL OR NEW.thread_ts <> expected_thread_ts THEN
                RAISE EXCEPTION 'Slack thread timestamp must match its work item root'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'UPDATE' AND
               ROW(NEW.id, NEW.slack_work_item_id, NEW.thread_ts,
                   NEW.thread_ts_key, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.slack_work_item_id, OLD.thread_ts,
                   OLD.thread_ts_key, OLD.created_at) THEN
                RAISE EXCEPTION 'slack thread identity is immutable' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER trg_slack_threads_guard
            BEFORE INSERT OR UPDATE OR DELETE ON slack_threads
            FOR EACH ROW EXECUTE FUNCTION tc_guard_slack_thread_mutation();

        CREATE FUNCTION tc_guard_slack_message_shape()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE expected_thread_ts varchar(32);
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'slack_messages cannot be deleted' USING ERRCODE = '55000';
            END IF;
            SELECT thread_ts INTO expected_thread_ts FROM slack_threads WHERE id = NEW.thread_id;
            IF expected_thread_ts IS NULL THEN
                RAISE EXCEPTION 'slack message thread was not found' USING ERRCODE = '23503';
            END IF;
            IF NEW.is_root IS DISTINCT FROM (NEW.message_ts = expected_thread_ts) THEN
                RAISE EXCEPTION 'slack root flag must match the thread timestamp'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'UPDATE' AND
               ROW(NEW.id, NEW.thread_id, NEW.message_ts, NEW.message_ts_key,
                   NEW.is_root, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.thread_id, OLD.message_ts, OLD.message_ts_key,
                   OLD.is_root, OLD.created_at) THEN
                RAISE EXCEPTION 'slack message identity is immutable' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER trg_slack_messages_shape
            BEFORE INSERT OR UPDATE OR DELETE ON slack_messages
            FOR EACH ROW EXECUTE FUNCTION tc_guard_slack_message_shape();

        CREATE FUNCTION tc_reject_slack_source_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
        END
        $$;
        CREATE TRIGGER trg_slack_message_revisions_append_only
            BEFORE UPDATE OR DELETE ON slack_message_revisions
            FOR EACH ROW EXECUTE FUNCTION tc_reject_slack_source_mutation();
        CREATE TRIGGER trg_slack_parse_results_append_only
            BEFORE UPDATE OR DELETE ON slack_parse_results
            FOR EACH ROW EXECUTE FUNCTION tc_reject_slack_source_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_slack_parse_results_append_only ON slack_parse_results")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_slack_message_revisions_append_only "
        "ON slack_message_revisions"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_slack_messages_shape ON slack_messages")
    op.execute("DROP TRIGGER IF EXISTS trg_slack_threads_guard ON slack_threads")
    op.execute("DROP TRIGGER IF EXISTS trg_slack_work_items_guard ON slack_work_items")
    op.execute("DROP TRIGGER IF EXISTS trg_slack_events_guard ON slack_events")

    op.drop_table("slack_parse_results")
    op.drop_table("slack_message_revisions")
    op.drop_table("slack_messages")
    op.drop_table("slack_threads")
    op.drop_table("slack_work_items")
    op.drop_table("slack_events")
    op.drop_table("slack_channel_allowlist")

    op.execute("DROP FUNCTION IF EXISTS tc_reject_slack_source_mutation()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_slack_message_shape()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_slack_thread_mutation()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_slack_work_item_mutation()")
    op.execute("DROP FUNCTION IF EXISTS tc_guard_slack_event_mutation()")
