"""Automatic generation execution, retry, and history authority.

Revision ID: 20260828_0026
Revises: 20260828_0025
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260828_0026"
down_revision: str | None = "20260828_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _change_assessment_validation_function(*, committed_only: bool) -> str:
    """Keep T03 baseline authority while excluding unmaterialized T07 roots."""

    committed_filter = (
        """
               AND EXISTS (
                   SELECT 1
                     FROM generation_jobs descendant
                     JOIN generation_materialization_schedules materialization
                       ON materialization.generation_job_id = descendant.id
                    WHERE descendant.root_job_id = job.id
                      AND materialization.state = 'COMMITTED'
               )
        """
        if committed_only
        else ""
    )
    return (
        r"""
        CREATE OR REPLACE FUNCTION tc_generation_change_assessments_validate_links()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            predecessor generation_change_assessments%ROWTYPE;
            expected_predecessor_id uuid;
            expected_materialization_id uuid;
            linked_job generation_jobs%ROWTYPE;
            target_job_id uuid;
            link_kind text;
        BEGIN
            SELECT id INTO expected_predecessor_id
              FROM generation_change_assessments
             WHERE slack_work_item_id = NEW.slack_work_item_id
               AND current_source_version < NEW.current_source_version
             ORDER BY current_source_version DESC
             LIMIT 1;

            IF NEW.predecessor_assessment_id IS DISTINCT FROM expected_predecessor_id OR
               (NEW.outcome = 'INITIAL_GENERATE' AND expected_predecessor_id IS NOT NULL) THEN
                    RAISE EXCEPTION 'assessment predecessor is not immediately prior'
                        USING ERRCODE = '23514';
            END IF;

            IF NEW.predecessor_assessment_id IS NOT NULL THEN
                SELECT * INTO predecessor
                  FROM generation_change_assessments
                 WHERE id = NEW.predecessor_assessment_id;
                IF predecessor.id IS NULL OR
                   predecessor.slack_work_item_id <> NEW.slack_work_item_id OR
                   predecessor.current_source_version <> NEW.baseline_source_version THEN
                    RAISE EXCEPTION 'assessment predecessor boundary is invalid'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            SELECT job.id INTO expected_materialization_id
              FROM generation_jobs AS job
             WHERE job.slack_work_item_id = NEW.slack_work_item_id
               AND job.source_version < NEW.current_source_version
               AND job.generation_trigger = 'AUTOMATIC'
               AND job.retry_of_job_id IS NULL
        """
        + committed_filter
        + r"""
             ORDER BY job.source_version DESC, job.generation_version DESC
             LIMIT 1;
            IF NEW.materialization_baseline_generation_job_id
               IS DISTINCT FROM expected_materialization_id THEN
                RAISE EXCEPTION 'assessment materialization baseline is not canonical'
                    USING ERRCODE = '23514';
            END IF;

            FOR target_job_id, link_kind IN
                SELECT NEW.materialization_baseline_generation_job_id, 'baseline'
                UNION ALL SELECT NEW.selected_generation_job_id, 'selected'
                UNION ALL SELECT NEW.reused_generation_job_id, 'reused'
            LOOP
                CONTINUE WHEN target_job_id IS NULL;
                SELECT job.* INTO linked_job
                  FROM generation_jobs AS job
                 WHERE job.id = target_job_id;
                IF linked_job.id IS NULL OR
                   linked_job.slack_work_item_id <> NEW.slack_work_item_id OR
                   linked_job.generation_trigger <> 'AUTOMATIC' OR
                   linked_job.retry_of_job_id IS NOT NULL OR
                   linked_job.root_job_id <> linked_job.id THEN
                    RAISE EXCEPTION 'assessment % job link is not an automatic root', link_kind
                        USING ERRCODE = '23514';
                END IF;
                IF link_kind = 'selected' AND (
                    linked_job.source_version <> NEW.current_source_version OR
                    linked_job.status <> 'QUEUED' OR
                    linked_job.context_sealed_at IS NULL OR
                    linked_job.generation_input_capture_state <> 'SEALED'
                ) THEN
                    RAISE EXCEPTION 'selected job does not match the assessment boundary'
                        USING ERRCODE = '23514';
                END IF;
                IF link_kind IN ('baseline', 'reused') AND
                   linked_job.source_version >= NEW.current_source_version THEN
                    RAISE EXCEPTION 'baseline job is not older than the assessment'
                        USING ERRCODE = '23514';
                END IF;
            END LOOP;
            IF NEW.predecessor_assessment_id IS NULL AND
               NEW.materialization_baseline_generation_job_id IS NOT NULL THEN
                SELECT job.* INTO linked_job
                  FROM generation_jobs AS job
                 WHERE job.id = NEW.materialization_baseline_generation_job_id;
                IF NEW.baseline_source_version <> linked_job.source_version THEN
                    RAISE EXCEPTION 'legacy baseline version does not match its root'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


def upgrade() -> None:
    op.execute(
        r"""
        ALTER TABLE generation_jobs DROP CONSTRAINT ck_generation_jobs_error_code_allowed;
        ALTER TABLE generation_jobs ADD CONSTRAINT ck_generation_jobs_error_code_allowed CHECK (
            error_code IS NULL OR error_code IN (
                'GENERATION_CONFIG_INVALID', 'GENERATION_UNSUPPORTED_SCHEMA_VERSION',
                'GENERATION_AUTH_REQUIRED', 'GENERATION_AUTH_REJECTED',
                'GENERATION_TIMEOUT', 'GENERATION_TRANSPORT_UNAVAILABLE',
                'GENERATION_RATE_LIMITED', 'GENERATION_PROVIDER_REJECTED',
                'GENERATION_PROVIDER_ERROR', 'GENERATION_INVALID_CONTENT_TYPE',
                'GENERATION_RESPONSE_TOO_LARGE', 'GENERATION_MALFORMED_JSON',
                'GENERATION_INVALID_RESPONSE', 'GENERATION_SOURCE_STALE',
                'GENERATION_SOURCE_UNAVAILABLE', 'GENERATION_WORK_ITEM_DELETED',
                'GENERATION_EXECUTION_OUTCOME_UNKNOWN',
                'GENERATION_WORKER_DELIVERY_EXHAUSTED'
            )
        );

        CREATE TABLE generation_execution_schedules (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            generation_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            root_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            background_job_id uuid NOT NULL REFERENCES background_jobs(id) ON DELETE RESTRICT,
            state varchar(40) NOT NULL,
            not_before timestamptz NOT NULL DEFAULT now(),
            lease_owner varchar(200),
            lease_token uuid,
            lease_expires_at timestamptz,
            resource_version integer NOT NULL DEFAULT 1,
            provider_permit_count integer NOT NULL DEFAULT 0,
            worker_delivery_attempt_count integer NOT NULL DEFAULT 0,
            provider_call_budget integer NOT NULL,
            generation_job_budget integer NOT NULL,
            retry_policy_version varchar(100) NOT NULL,
            retry_policy_digest varchar(64) NOT NULL,
            materialization_mode varchar(40) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_execution_schedule_job UNIQUE (generation_job_id),
            CONSTRAINT uq_generation_execution_schedule_background_job
                UNIQUE (background_job_id),
            CONSTRAINT ck_generation_execution_schedule_state CHECK (
                state IN ('NOT_BEFORE', 'READY', 'LEASED',
                          'PROVIDER_OUTCOME_UNKNOWN', 'TERMINAL')
            ),
            CONSTRAINT ck_generation_execution_schedule_lease CHECK (
                (state = 'LEASED' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL
                    AND lease_expires_at IS NOT NULL) OR
                (state <> 'LEASED' AND lease_owner IS NULL AND lease_token IS NULL
                    AND lease_expires_at IS NULL)
            ),
            CONSTRAINT ck_generation_execution_schedule_counts CHECK (
                resource_version >= 1 AND provider_permit_count BETWEEN 0 AND 5
                AND worker_delivery_attempt_count >= 0
                AND provider_call_budget BETWEEN 1 AND 5
                AND generation_job_budget BETWEEN 1 AND 1000
            ),
            CONSTRAINT ck_generation_execution_schedule_policy CHECK (
                char_length(btrim(retry_policy_version)) > 0
                AND retry_policy_digest ~ '^[0-9a-f]{64}$'
                AND materialization_mode IN ('AUTOMATIC_APPLY', 'AUTOMATIC_SUGGESTIONS')
            )
        );
        CREATE UNIQUE INDEX uq_generation_execution_active_root
            ON generation_execution_schedules (root_job_id)
            WHERE state IN ('NOT_BEFORE', 'READY', 'LEASED', 'PROVIDER_OUTCOME_UNKNOWN');
        CREATE INDEX ix_generation_execution_schedules_claim
            ON generation_execution_schedules (not_before, created_at, id)
            WHERE state IN ('NOT_BEFORE', 'READY');

        CREATE TABLE generation_attempt_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            root_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            generation_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            sequence integer NOT NULL,
            event_type varchar(50) NOT NULL,
            provider_attempt_number integer,
            error_code varchar(80),
            retry_request_id uuid,
            generation_apply_id uuid REFERENCES generation_applies(id) ON DELETE RESTRICT,
            suggestion_materialization_id uuid
                REFERENCES generation_suggestion_materializations(id) ON DELETE RESTRICT,
            notification_intent_id uuid
                REFERENCES generation_slack_notification_intents(id) ON DELETE RESTRICT,
            notification_receipt_id uuid
                REFERENCES generation_slack_notification_receipts(id) ON DELETE RESTRICT,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_attempt_event_root_sequence UNIQUE (root_job_id, sequence),
            CONSTRAINT ck_generation_attempt_event_sequence CHECK (sequence >= 1),
            CONSTRAINT ck_generation_attempt_event_provider_attempt CHECK (
                provider_attempt_number IS NULL OR provider_attempt_number BETWEEN 1 AND 5
            ),
            CONSTRAINT ck_generation_attempt_event_type CHECK (
                event_type IN ('INPUT_SEALED', 'EXECUTION_QUEUED', 'WORKER_LEASED',
                    'PROVIDER_ATTEMPT_STARTED', 'PROVIDER_ATTEMPT_FAILED',
                    'PROVIDER_ATTEMPT_SUCCEEDED', 'JOB_FAILED', 'RETRY_SCHEDULED',
                    'JOB_SUCCEEDED', 'APPLY_SCHEDULED', 'APPLY_COMMITTED',
                    'SUGGESTION_SCHEDULED', 'SUGGESTION_COMMITTED',
                    'MATERIALIZATION_SKIPPED', 'SLACK_INTENT_CAPTURED',
                    'SLACK_DELIVERED', 'SLACK_SUPPRESSED', 'SLACK_DEAD')
            )
        );

        CREATE TABLE generation_provider_call_permits (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            generation_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            root_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            execution_schedule_id uuid NOT NULL
                REFERENCES generation_execution_schedules(id) ON DELETE RESTRICT,
            provider_attempt_number integer NOT NULL,
            lease_token uuid NOT NULL,
            request_digest varchar(64) NOT NULL,
            provider_id varchar(100) NOT NULL,
            model_id varchar(200) NOT NULL,
            permitted_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_provider_permit_attempt
                UNIQUE (generation_job_id, provider_attempt_number),
            CONSTRAINT ck_generation_provider_permit_attempt CHECK (
                provider_attempt_number BETWEEN 1 AND 5
            ),
            CONSTRAINT ck_generation_provider_permit_digest CHECK (
                request_digest ~ '^[0-9a-f]{64}$'
                AND char_length(btrim(provider_id)) > 0
                AND char_length(btrim(model_id)) > 0
            )
        );

        CREATE TABLE generation_provider_call_observations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            permit_id uuid NOT NULL
                REFERENCES generation_provider_call_permits(id) ON DELETE RESTRICT,
            generation_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            root_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            outcome varchar(30) NOT NULL,
            error_code varchar(80),
            safe_provider_code varchar(80),
            http_status integer,
            retry_after_seconds integer,
            result_digest varchar(64),
            started_at timestamptz NOT NULL,
            finished_at timestamptz,
            observed_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_provider_observation_permit UNIQUE (permit_id),
            CONSTRAINT ck_generation_provider_observation_outcome CHECK (
                outcome IN ('SUCCEEDED', 'RETRYABLE_FAILURE',
                            'NON_RETRYABLE_FAILURE', 'AMBIGUOUS')
            ),
            CONSTRAINT ck_generation_provider_observation_code CHECK (
                safe_provider_code IS NULL OR safe_provider_code ~ '^[a-z0-9_]{1,80}$'
            ),
            CONSTRAINT ck_generation_provider_observation_http CHECK (
                http_status IS NULL OR http_status BETWEEN 100 AND 599
            ),
            CONSTRAINT ck_generation_provider_observation_retry_after CHECK (
                retry_after_seconds IS NULL OR retry_after_seconds BETWEEN 0 AND 86400
            ),
            CONSTRAINT ck_generation_provider_observation_digest CHECK (
                result_digest IS NULL OR result_digest ~ '^[0-9a-f]{64}$'
            )
        );

        CREATE TABLE generation_retry_requests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            actor_principal_id varchar(128) NOT NULL,
            actor_display_label varchar(200) NOT NULL,
            root_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            expected_failed_job_id uuid NOT NULL
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            expected_attempt_number integer NOT NULL,
            source_version integer NOT NULL,
            context_digest varchar(64) NOT NULL,
            input_digest varchar(64) NOT NULL,
            context_mode varchar(30) NOT NULL,
            normalized_reason text NOT NULL,
            normalized_additional_conditions text NOT NULL,
            condition_code_points integer NOT NULL,
            condition_utf8_bytes integer NOT NULL,
            original_condition_digest varchar(64) NOT NULL,
            condition_digest varchar(64) NOT NULL,
            idempotency_key varchar(255) NOT NULL,
            request_digest varchar(64) NOT NULL,
            retry_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_retry_request_actor_idempotency
                UNIQUE (actor_principal_id, idempotency_key),
            CONSTRAINT uq_generation_retry_request_retry_job UNIQUE (retry_job_id),
            CONSTRAINT ck_generation_retry_request_shape CHECK (
                expected_attempt_number >= 1 AND source_version >= 1
                AND context_mode = 'REUSE_SEALED'
                AND char_length(btrim(actor_principal_id)) > 0
                AND char_length(btrim(actor_display_label)) > 0
                AND char_length(normalized_reason) BETWEEN 1 AND 500
                AND char_length(normalized_additional_conditions) BETWEEN 1 AND 1000
                AND condition_code_points BETWEEN 1 AND 1000
                AND condition_utf8_bytes BETWEEN 1 AND 4000
                AND context_digest ~ '^[0-9a-f]{64}$'
                AND input_digest ~ '^[0-9a-f]{64}$'
                AND original_condition_digest ~ '^[0-9a-f]{64}$'
                AND condition_digest ~ '^[0-9a-f]{64}$'
                AND request_digest ~ '^[0-9a-f]{64}$'
            )
        );

        ALTER TABLE generation_attempt_events
            ADD CONSTRAINT fk_generation_attempt_event_retry_request
            FOREIGN KEY (retry_request_id) REFERENCES generation_retry_requests(id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;

        CREATE TABLE generation_retry_exhaustions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            root_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            terminal_failed_job_id uuid NOT NULL
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            finality varchar(40) NOT NULL,
            retryable boolean NOT NULL,
            provider_call_budget integer NOT NULL,
            generation_job_budget integer NOT NULL,
            error_code varchar(80) NOT NULL,
            exhausted_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_retry_exhaustion_root UNIQUE (root_job_id),
            CONSTRAINT uq_generation_retry_exhaustion_failed_job UNIQUE (terminal_failed_job_id),
            CONSTRAINT ck_generation_retry_exhaustion_finality CHECK (
                (finality = 'TERMINAL_EXHAUSTED' AND retryable) OR
                (finality IN ('TERMINAL_NON_RETRYABLE', 'TERMINAL_REVIEW_REQUIRED')
                    AND NOT retryable)
            ),
            CONSTRAINT ck_generation_retry_exhaustion_budget CHECK (
                provider_call_budget BETWEEN 1 AND 5
                AND generation_job_budget BETWEEN 1 AND 1000
                AND char_length(btrim(error_code)) > 0
            )
        );

        CREATE TABLE generation_materialization_schedules (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            generation_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            root_job_id uuid NOT NULL REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            background_job_id uuid NOT NULL REFERENCES background_jobs(id) ON DELETE RESTRICT,
            mode varchar(40) NOT NULL,
            state varchar(40) NOT NULL,
            not_before timestamptz NOT NULL DEFAULT now(),
            lease_owner varchar(200),
            lease_token uuid,
            lease_expires_at timestamptz,
            resource_version integer NOT NULL DEFAULT 1,
            generation_apply_id uuid REFERENCES generation_applies(id) ON DELETE RESTRICT,
            suggestion_materialization_id uuid
                REFERENCES generation_suggestion_materializations(id) ON DELETE RESTRICT,
            terminal_error_code varchar(80),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_materialization_job UNIQUE (generation_job_id),
            CONSTRAINT uq_generation_materialization_root UNIQUE (root_job_id),
            CONSTRAINT uq_generation_materialization_background_job UNIQUE (background_job_id),
            CONSTRAINT ck_generation_materialization_schedule_mode CHECK (
                mode IN ('AUTOMATIC_APPLY', 'AUTOMATIC_SUGGESTIONS')
            ),
            CONSTRAINT ck_generation_materialization_schedule_state CHECK (
                state IN ('QUEUED', 'LEASED', 'COMMITTED', 'SKIPPED_STALE_SOURCE',
                    'SKIPPED_SOURCE_UNAVAILABLE', 'REVIEW_REQUIRED', 'FAILED')
            ),
            CONSTRAINT ck_generation_materialization_schedule_lease CHECK (
                (state = 'LEASED' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL
                    AND lease_expires_at IS NOT NULL) OR
                (state <> 'LEASED' AND lease_owner IS NULL AND lease_token IS NULL
                    AND lease_expires_at IS NULL)
            ),
            CONSTRAINT ck_generation_materialization_schedule_receipt CHECK (
                (state = 'COMMITTED' AND
                    ((mode = 'AUTOMATIC_APPLY' AND generation_apply_id IS NOT NULL
                        AND suggestion_materialization_id IS NULL) OR
                     (mode = 'AUTOMATIC_SUGGESTIONS' AND generation_apply_id IS NULL
                        AND suggestion_materialization_id IS NOT NULL))) OR
                (state <> 'COMMITTED' AND generation_apply_id IS NULL
                    AND suggestion_materialization_id IS NULL)
            ),
            CONSTRAINT ck_generation_materialization_schedule_version CHECK (
                resource_version >= 1
            )
        );

        CREATE UNIQUE INDEX uq_generation_slack_notification_terminal_root
            ON generation_slack_notification_intents (generation_root_job_id);

        CREATE FUNCTION tc_generation_retry_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'generation retry/history authority is append-only'
                USING ERRCODE = '55000';
        END;
        $$;

        CREATE FUNCTION tc_generation_retry_no_delete()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'generation execution/materialization authority cannot be deleted'
                USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER trg_generation_execution_schedules_no_delete
            BEFORE DELETE ON generation_execution_schedules
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_no_delete();
        CREATE TRIGGER trg_generation_materialization_schedules_no_delete
            BEFORE DELETE ON generation_materialization_schedules
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_no_delete();

        CREATE TRIGGER trg_generation_attempt_events_append_only
            BEFORE UPDATE OR DELETE ON generation_attempt_events
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_immutable();
        CREATE TRIGGER trg_generation_provider_call_permits_append_only
            BEFORE UPDATE OR DELETE ON generation_provider_call_permits
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_immutable();
        CREATE TRIGGER trg_generation_provider_call_observations_append_only
            BEFORE UPDATE OR DELETE ON generation_provider_call_observations
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_immutable();
        CREATE TRIGGER trg_generation_retry_requests_append_only
            BEFORE UPDATE OR DELETE ON generation_retry_requests
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_immutable();
        CREATE TRIGGER trg_generation_retry_exhaustions_append_only
            BEFORE UPDATE OR DELETE ON generation_retry_exhaustions
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_immutable();

        CREATE FUNCTION tc_generation_retry_authority_links()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE bad_count integer;
        BEGIN
            SELECT count(*) INTO bad_count
              FROM generation_jobs job
              JOIN generation_jobs root ON root.id = job.root_job_id
              JOIN slack_work_items item ON item.id = job.slack_work_item_id
             WHERE job.generation_trigger = 'AUTOMATIC'
               AND (root.id <> root.root_job_id OR root.retry_of_job_id IS NOT NULL
                    OR root.attempt_number <> 1
                    OR root.generation_trigger <> 'AUTOMATIC'
                    OR root.slack_work_item_id <> job.slack_work_item_id
                    OR root.source_version <> job.source_version
                    OR (job.retry_of_job_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM generation_jobs predecessor
                         WHERE predecessor.id = job.retry_of_job_id
                           AND predecessor.root_job_id = job.root_job_id
                           AND predecessor.slack_work_item_id = job.slack_work_item_id
                           AND predecessor.source_version = job.source_version
                           AND predecessor.attempt_number + 1 = job.attempt_number
                    )));
            IF bad_count <> 0 THEN
                RAISE EXCEPTION 'generation retry lineage crosses workspace/source/root'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO bad_count FROM generation_execution_schedules schedule
              JOIN generation_jobs job ON job.id = schedule.generation_job_id
              JOIN slack_work_items item ON item.id = job.slack_work_item_id
              JOIN background_jobs background ON background.id = schedule.background_job_id
             WHERE schedule.root_job_id <> job.root_job_id
                OR schedule.workspace_id <> item.workspace_id
                OR job.generation_trigger <> 'AUTOMATIC'
                OR background.job_type <> 'AUTOMATIC_GENERATION_EXECUTE'
                OR background.payload ->> 'generationJobId' <> job.id::text
                OR schedule.provider_permit_count <> (
                    SELECT count(*) FROM generation_provider_call_permits permit
                     WHERE permit.execution_schedule_id = schedule.id)
                OR EXISTS (
                    SELECT 1 FROM generation_execution_schedules root_schedule
                     WHERE root_schedule.root_job_id = schedule.root_job_id
                       AND (root_schedule.provider_call_budget <> schedule.provider_call_budget
                            OR (root_schedule.generation_job_budget < job.attempt_number
                                AND NOT EXISTS (
                                    SELECT 1 FROM generation_retry_requests operator_request
                                     WHERE operator_request.retry_job_id = job.id))
                            OR root_schedule.retry_policy_version <> schedule.retry_policy_version
                            OR root_schedule.retry_policy_digest <> schedule.retry_policy_digest
                            OR root_schedule.materialization_mode <> schedule.materialization_mode)
                );
            IF bad_count <> 0 THEN
                RAISE EXCEPTION 'generation execution schedule provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO bad_count FROM generation_provider_call_permits permit
              JOIN generation_execution_schedules schedule
                ON schedule.id = permit.execution_schedule_id
              JOIN generation_jobs job ON job.id = permit.generation_job_id
              JOIN slack_work_items item ON item.id = job.slack_work_item_id
             WHERE permit.root_job_id <> job.root_job_id
                OR permit.workspace_id <> item.workspace_id
                OR schedule.generation_job_id <> job.id
                OR schedule.root_job_id <> job.root_job_id
                OR permit.provider_attempt_number > schedule.provider_call_budget
                OR permit.request_digest <> job.generation_input_digest
                OR permit.provider_id <> job.provider OR permit.model_id <> job.model
                OR EXISTS (
                    SELECT 1 FROM generation_provider_call_permits prior
                     WHERE prior.generation_job_id = permit.generation_job_id
                       AND prior.provider_attempt_number < permit.provider_attempt_number
                       AND NOT EXISTS (
                           SELECT 1 FROM generation_provider_call_observations observed
                            WHERE observed.permit_id = prior.id));
            IF bad_count <> 0 THEN
                RAISE EXCEPTION 'generation provider permit provenance/budget is invalid'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO bad_count FROM generation_provider_call_observations observation
              JOIN generation_provider_call_permits permit ON permit.id = observation.permit_id
              JOIN generation_jobs job ON job.id = observation.generation_job_id
              JOIN slack_work_items item ON item.id = job.slack_work_item_id
             WHERE observation.generation_job_id <> permit.generation_job_id
                OR observation.root_job_id <> permit.root_job_id
                OR observation.workspace_id <> permit.workspace_id
                OR observation.workspace_id <> item.workspace_id
                OR observation.finished_at IS NOT NULL
                    AND observation.finished_at < observation.started_at;
            IF bad_count <> 0 THEN
                RAISE EXCEPTION 'generation provider observation provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO bad_count FROM generation_attempt_events event
              JOIN generation_jobs job ON job.id = event.generation_job_id
              JOIN slack_work_items item ON item.id = job.slack_work_item_id
             WHERE event.root_job_id <> job.root_job_id
                OR event.workspace_id <> item.workspace_id
                OR event.sequence <> (
                    SELECT count(*) FROM generation_attempt_events earlier
                     WHERE earlier.root_job_id = event.root_job_id
                       AND earlier.sequence <= event.sequence);
            IF bad_count <> 0 THEN
                RAISE EXCEPTION 'generation attempt timeline is not contiguous or scoped'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO bad_count FROM generation_retry_requests request
              JOIN generation_jobs failed ON failed.id = request.expected_failed_job_id
              JOIN generation_jobs retry ON retry.id = request.retry_job_id
              JOIN slack_work_items item ON item.id = failed.slack_work_item_id
             WHERE failed.status <> 'FAILED' OR failed.root_job_id <> request.root_job_id
                OR failed.attempt_number <> request.expected_attempt_number
                OR retry.retry_of_job_id <> failed.id OR retry.root_job_id <> request.root_job_id
                OR retry.attempt_number <> failed.attempt_number + 1
                OR retry.slack_work_item_id <> failed.slack_work_item_id
                OR retry.source_version <> failed.source_version
                OR request.workspace_id <> item.workspace_id
                OR request.source_version <> failed.source_version
                OR request.context_digest <> failed.context_digest
                OR request.input_digest <> failed.generation_input_digest
                OR octet_length(request.normalized_additional_conditions)
                    <> request.condition_utf8_bytes
                OR char_length(request.normalized_additional_conditions)
                    <> request.condition_code_points;
            IF bad_count <> 0 THEN
                RAISE EXCEPTION 'generation operator retry provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO bad_count FROM generation_retry_exhaustions exhaustion
              JOIN generation_jobs failed ON failed.id = exhaustion.terminal_failed_job_id
              JOIN slack_work_items item ON item.id = failed.slack_work_item_id
             WHERE failed.status <> 'FAILED' OR failed.root_job_id <> exhaustion.root_job_id
                OR exhaustion.workspace_id <> item.workspace_id
                OR (exhaustion.finality = 'TERMINAL_EXHAUSTED'
                    AND failed.attempt_number < exhaustion.generation_job_budget);
            IF bad_count <> 0 THEN
                RAISE EXCEPTION 'generation retry exhaustion provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO bad_count FROM generation_materialization_schedules materialization
              JOIN generation_jobs job ON job.id = materialization.generation_job_id
              JOIN slack_work_items item ON item.id = job.slack_work_item_id
              JOIN background_jobs background
                ON background.id = materialization.background_job_id
             WHERE job.status <> 'SUCCEEDED' OR job.generation_trigger <> 'AUTOMATIC'
                OR materialization.root_job_id <> job.root_job_id
                OR materialization.workspace_id <> item.workspace_id
                OR background.job_type <> 'AUTOMATIC_GENERATION_MATERIALIZE'
                OR background.payload ->> 'generationJobId' <> job.id::text
                OR (materialization.generation_apply_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM generation_applies apply
                     WHERE apply.id = materialization.generation_apply_id
                       AND apply.generation_job_id = job.id))
                OR (materialization.suggestion_materialization_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM generation_suggestion_materializations suggestion
                     WHERE suggestion.id = materialization.suggestion_materialization_id
                       AND suggestion.generation_job_id = job.id));
            IF bad_count <> 0 THEN
                RAISE EXCEPTION 'generation materialization provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO bad_count FROM generation_slack_notification_intents intent
             WHERE intent.notification_kind = 'FAILURE'
               AND NOT EXISTS (
                   SELECT 1 FROM generation_retry_exhaustions exhaustion
                    WHERE exhaustion.root_job_id = intent.generation_root_job_id
                      AND exhaustion.terminal_failed_job_id = intent.generation_job_id)
                OR intent.notification_kind = 'SUCCESS_APPLIED' AND NOT EXISTS (
                   SELECT 1 FROM generation_materialization_schedules materialization
                    WHERE materialization.root_job_id = intent.generation_root_job_id
                      AND materialization.generation_job_id = intent.generation_job_id
                      AND materialization.state = 'COMMITTED'
                      AND materialization.generation_apply_id = intent.generation_apply_id)
                OR intent.notification_kind = 'SUCCESS_SUGGESTIONS' AND NOT EXISTS (
                   SELECT 1 FROM generation_materialization_schedules materialization
                    WHERE materialization.root_job_id = intent.generation_root_job_id
                      AND materialization.generation_job_id = intent.generation_job_id
                      AND materialization.state = 'COMMITTED'
                      AND materialization.suggestion_materialization_id IS NOT NULL);
            IF bad_count <> 0 THEN
                RAISE EXCEPTION 'generation terminal notification lacks T07 authority'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER trg_generation_retry_authority_jobs
            AFTER INSERT OR UPDATE ON generation_jobs DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_authority_links();
        CREATE CONSTRAINT TRIGGER trg_generation_retry_authority_background_jobs
            AFTER INSERT OR UPDATE ON background_jobs DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_authority_links();
        CREATE CONSTRAINT TRIGGER trg_generation_retry_authority_execution
            AFTER INSERT OR UPDATE ON generation_execution_schedules
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION tc_generation_retry_authority_links();
        CREATE CONSTRAINT TRIGGER trg_generation_retry_authority_events
            AFTER INSERT ON generation_attempt_events DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_authority_links();
        CREATE CONSTRAINT TRIGGER trg_generation_retry_authority_permits
            AFTER INSERT ON generation_provider_call_permits DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_authority_links();
        CREATE CONSTRAINT TRIGGER trg_generation_retry_authority_observations
            AFTER INSERT ON generation_provider_call_observations DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_authority_links();
        CREATE CONSTRAINT TRIGGER trg_generation_retry_authority_requests
            AFTER INSERT ON generation_retry_requests DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_authority_links();
        CREATE CONSTRAINT TRIGGER trg_generation_retry_authority_exhaustions
            AFTER INSERT ON generation_retry_exhaustions DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_retry_authority_links();
        CREATE CONSTRAINT TRIGGER trg_generation_retry_authority_materialization
            AFTER INSERT OR UPDATE ON generation_materialization_schedules
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION tc_generation_retry_authority_links();
        CREATE CONSTRAINT TRIGGER trg_generation_retry_authority_notification
            AFTER INSERT ON generation_slack_notification_intents
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION tc_generation_retry_authority_links();
        """
    )
    op.execute(_change_assessment_validation_function(committed_only=True))


def downgrade() -> None:
    op.execute(_change_assessment_validation_function(committed_only=False))
    op.execute(
        r"""
        DROP TRIGGER trg_generation_retry_authority_notification
            ON generation_slack_notification_intents;
        DROP TRIGGER trg_generation_retry_authority_materialization
            ON generation_materialization_schedules;
        DROP TRIGGER trg_generation_retry_authority_exhaustions
            ON generation_retry_exhaustions;
        DROP TRIGGER trg_generation_retry_authority_requests ON generation_retry_requests;
        DROP TRIGGER trg_generation_retry_authority_observations
            ON generation_provider_call_observations;
        DROP TRIGGER trg_generation_retry_authority_permits
            ON generation_provider_call_permits;
        DROP TRIGGER trg_generation_retry_authority_events ON generation_attempt_events;
        DROP TRIGGER trg_generation_retry_authority_execution
            ON generation_execution_schedules;
        DROP TRIGGER trg_generation_retry_authority_jobs ON generation_jobs;
        DROP TRIGGER IF EXISTS trg_generation_retry_authority_background_jobs
            ON background_jobs;
        DROP FUNCTION tc_generation_retry_authority_links();

        DROP TRIGGER IF EXISTS trg_generation_materialization_schedules_no_delete
            ON generation_materialization_schedules;
        DROP TRIGGER IF EXISTS trg_generation_execution_schedules_no_delete
            ON generation_execution_schedules;
        DROP FUNCTION IF EXISTS tc_generation_retry_no_delete();

        DROP TRIGGER trg_generation_retry_exhaustions_append_only
            ON generation_retry_exhaustions;
        DROP TRIGGER trg_generation_retry_requests_append_only ON generation_retry_requests;
        DROP TRIGGER trg_generation_provider_call_observations_append_only
            ON generation_provider_call_observations;
        DROP TRIGGER trg_generation_provider_call_permits_append_only
            ON generation_provider_call_permits;
        DROP TRIGGER trg_generation_attempt_events_append_only ON generation_attempt_events;
        DROP FUNCTION tc_generation_retry_immutable();

        DROP INDEX uq_generation_slack_notification_terminal_root;
        DROP TABLE generation_materialization_schedules;
        DROP TABLE generation_retry_exhaustions;
        ALTER TABLE generation_attempt_events
            DROP CONSTRAINT fk_generation_attempt_event_retry_request;
        DROP TABLE generation_retry_requests;
        DROP TABLE generation_provider_call_observations;
        DROP TABLE generation_provider_call_permits;
        DROP TABLE generation_attempt_events;
        DROP TABLE generation_execution_schedules;
        ALTER TABLE generation_jobs DROP CONSTRAINT ck_generation_jobs_error_code_allowed;
        ALTER TABLE generation_jobs ADD CONSTRAINT ck_generation_jobs_error_code_allowed CHECK (
            error_code IS NULL OR error_code IN (
                'GENERATION_UNSUPPORTED_SCHEMA_VERSION', 'GENERATION_AUTH_REJECTED',
                'GENERATION_TIMEOUT', 'GENERATION_TRANSPORT_UNAVAILABLE',
                'GENERATION_RATE_LIMITED', 'GENERATION_PROVIDER_REJECTED',
                'GENERATION_PROVIDER_ERROR', 'GENERATION_INVALID_CONTENT_TYPE',
                'GENERATION_RESPONSE_TOO_LARGE', 'GENERATION_MALFORMED_JSON',
                'GENERATION_INVALID_RESPONSE'
            )
        );
        """
    )
