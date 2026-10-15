"""Durable automatic-generation Slack notification ledgers.

Revision ID: 20260828_0025
Revises: 20260827_0024
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260828_0025"
down_revision: str | None = "20260827_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        r"""
        ALTER TABLE generation_jobs
            ADD CONSTRAINT uq_generation_jobs_root_attempt
                UNIQUE (root_job_id, attempt_number);
        CREATE UNIQUE INDEX uq_generation_jobs_root_succeeded
            ON generation_jobs (root_job_id)
            WHERE status = 'SUCCEEDED';

        CREATE TABLE generation_suggestion_materializations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL
                REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            generation_job_id uuid NOT NULL
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            generation_root_job_id uuid NOT NULL
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            generation_change_assessment_id uuid NOT NULL
                REFERENCES generation_change_assessments(id) ON DELETE RESTRICT,
            candidate_count integer NOT NULL,
            suggestion_count integer NOT NULL,
            no_change_count integer NOT NULL,
            result_digest varchar(64) NOT NULL,
            receipt jsonb NOT NULL,
            materialized_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_suggestion_materializations_job
                UNIQUE (generation_job_id),
            CONSTRAINT ck_generation_suggestion_materializations_counts CHECK (
                candidate_count >= 0 AND suggestion_count >= 0 AND no_change_count >= 0
                AND suggestion_count + no_change_count = candidate_count
            ),
            CONSTRAINT ck_generation_suggestion_materializations_digest
                CHECK (result_digest ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_generation_suggestion_materializations_receipt
                CHECK (jsonb_typeof(receipt) = 'object')
        );

        CREATE TABLE generation_slack_notification_intents (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL
                REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            slack_work_item_id uuid NOT NULL
                REFERENCES slack_work_items(id) ON DELETE RESTRICT,
            generation_root_job_id uuid NOT NULL
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            generation_job_id uuid NOT NULL
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            generation_apply_id uuid
                REFERENCES generation_applies(id) ON DELETE RESTRICT,
            generation_change_assessment_id uuid
                REFERENCES generation_change_assessments(id) ON DELETE RESTRICT,
            source_version integer NOT NULL,
            attempt_number integer NOT NULL,
            terminal_status varchar(20) NOT NULL,
            notification_kind varchar(30) NOT NULL,
            delivery_disposition varchar(40) NOT NULL,
            suppression_reason varchar(80),
            slack_workspace_id varchar(80) NOT NULL,
            channel_id varchar(80) NOT NULL,
            root_message_ts varchar(32) NOT NULL,
            thread_ts varchar(32) NOT NULL,
            template_version varchar(100) NOT NULL,
            locale varchar(10) NOT NULL,
            message_snapshot jsonb NOT NULL,
            message_text text NOT NULL,
            message_digest varchar(64) NOT NULL,
            link_mode varchar(20) NOT NULL,
            ui_url text,
            notification_key varchar(512) NOT NULL,
            client_message_id uuid NOT NULL,
            metadata jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_slack_notification_intents_generation_job
                UNIQUE (generation_job_id),
            CONSTRAINT uq_generation_slack_notification_intents_notification_key
                UNIQUE (notification_key),
            CONSTRAINT uq_generation_slack_notification_intents_client_message_id
                UNIQUE (client_message_id),
            CONSTRAINT ck_generation_slack_notification_intents_source_attempt
                CHECK (source_version >= 1 AND attempt_number >= 1),
            CONSTRAINT ck_generation_slack_notification_intents_terminal_status
                CHECK (terminal_status IN ('SUCCEEDED', 'FAILED')),
            CONSTRAINT ck_generation_slack_notification_intents_kind
                CHECK (notification_kind IN (
                    'SUCCESS_APPLIED', 'SUCCESS_SUGGESTIONS', 'FAILURE'
                )),
            CONSTRAINT ck_generation_slack_notification_intents_disposition
                CHECK (delivery_disposition IN (
                    'POST', 'SUPPRESSED_STALE_SOURCE',
                    'SUPPRESSED_SOURCE_UNAVAILABLE', 'SUPPRESSED_CONFIGURATION'
                )),
            CONSTRAINT ck_generation_slack_notification_intents_suppression CHECK (
                (delivery_disposition = 'POST' AND suppression_reason IS NULL) OR
                (delivery_disposition <> 'POST' AND suppression_reason = delivery_disposition)
            ),
            CONSTRAINT ck_generation_slack_notification_intents_status_kind CHECK (
                (terminal_status = 'FAILED' AND notification_kind = 'FAILURE') OR
                (terminal_status = 'SUCCEEDED' AND notification_kind IN (
                    'SUCCESS_APPLIED', 'SUCCESS_SUGGESTIONS'
                ))
            ),
            CONSTRAINT ck_generation_slack_notification_intents_authority CHECK (
                (notification_kind = 'SUCCESS_APPLIED' AND
                    generation_apply_id IS NOT NULL AND
                    generation_change_assessment_id IS NULL) OR
                (notification_kind = 'SUCCESS_SUGGESTIONS' AND
                    generation_apply_id IS NULL AND
                    generation_change_assessment_id IS NOT NULL) OR
                (notification_kind = 'FAILURE' AND
                    generation_apply_id IS NULL AND
                    generation_change_assessment_id IS NULL)
            ),
            CONSTRAINT ck_generation_slack_notification_intents_destination CHECK (
                char_length(btrim(slack_workspace_id)) > 0 AND
                char_length(btrim(channel_id)) > 0 AND
                root_message_ts ~ '^[0-9]{10,12}\.[0-9]{6}$' AND
                thread_ts = root_message_ts
            ),
            CONSTRAINT ck_generation_slack_notification_intents_template CHECK (
                template_version = 'automatic-generation-slack-reply/1.0.0' AND
                locale = 'ko-KR' AND char_length(message_text) BETWEEN 1 AND 4000 AND
                message_digest ~ '^[0-9a-f]{64}$' AND
                jsonb_typeof(message_snapshot) = 'object'
            ),
            CONSTRAINT ck_generation_slack_notification_intents_link CHECK (
                (link_mode = 'OMITTED' AND ui_url IS NULL) OR
                (link_mode = 'ABSOLUTE_HTTPS' AND ui_url ~ '^https://' AND
                    ui_url !~ '[@?#<>|[:space:]\\]')
            ),
            CONSTRAINT ck_generation_slack_notification_intents_notification_key CHECK (
                notification_key = 'automatic-generation-slack-reply/1.0.0:' ||
                    'generation_job_' || replace(generation_job_id::text, '-', '') ||
                    ':' || terminal_status
            ),
            CONSTRAINT ck_generation_slack_notification_intents_metadata CHECK (
                metadata = jsonb_build_object(
                    'event_type', 'tc_generation_terminal_notification_v1',
                    'event_payload', jsonb_build_object(
                        'notification_id',
                            'generation_slack_notification_' || replace(id::text, '-', ''),
                        'generation_job_id',
                            'generation_job_' || replace(generation_job_id::text, '-', ''),
                        'outcome', notification_kind,
                        'contract_version', '1.0.0'
                    )
                )
            )
        );

        CREATE TABLE generation_slack_notification_delivery_attempts (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            intent_id uuid NOT NULL
                REFERENCES generation_slack_notification_intents(id) ON DELETE RESTRICT,
            background_job_id uuid NOT NULL
                REFERENCES background_jobs(id) ON DELETE RESTRICT,
            background_job_attempt integer NOT NULL,
            operation varchar(20) NOT NULL,
            worker_id varchar(200) NOT NULL,
            lease_token uuid NOT NULL,
            lease_expires_at timestamptz NOT NULL,
            request_digest varchar(64) NOT NULL,
            prepared_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_slack_notification_delivery_attempts_permit
                UNIQUE (intent_id, background_job_id, background_job_attempt, operation),
            CONSTRAINT ck_generation_slack_notification_delivery_attempts_shape CHECK (
                background_job_attempt >= 1 AND operation IN ('POST', 'RECONCILE') AND
                char_length(btrim(worker_id)) > 0 AND
                request_digest ~ '^[0-9a-f]{64}$' AND
                lease_expires_at > prepared_at
            )
        );
        CREATE INDEX ix_generation_slack_notification_delivery_attempts_intent
            ON generation_slack_notification_delivery_attempts
                (intent_id, prepared_at, id);

        CREATE TABLE generation_slack_notification_delivery_observations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            attempt_id uuid NOT NULL
                REFERENCES generation_slack_notification_delivery_attempts(id)
                ON DELETE RESTRICT,
            classification varchar(40) NOT NULL,
            provider_code varchar(80),
            http_status integer,
            retry_after_seconds integer,
            safe_diagnostic varchar(80),
            observed_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_slack_notification_delivery_observations_attempt
                UNIQUE (attempt_id),
            CONSTRAINT ck_generation_slack_notification_delivery_observations_class CHECK (
                classification IN (
                    'ACKNOWLEDGED', 'DEFINITIVE_NO_DELIVERY', 'RATE_LIMITED',
                    'AMBIGUOUS', 'RECONCILED_FOUND', 'RECONCILED_NOT_FOUND'
                )
            ),
            CONSTRAINT ck_generation_slack_notification_delivery_observations_code CHECK (
                provider_code IS NULL OR provider_code ~ '^[a-z0-9_]{1,80}$'
            ),
            CONSTRAINT ck_generation_slack_notification_delivery_observations_http
                CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
            CONSTRAINT ck_generation_slack_notification_delivery_observations_retry CHECK (
                (classification = 'RATE_LIMITED' AND retry_after_seconds >= 0) OR
                (classification <> 'RATE_LIMITED' AND retry_after_seconds IS NULL)
            ),
            CONSTRAINT ck_generation_slack_notification_delivery_observations_diagnostic CHECK (
                safe_diagnostic IS NULL OR safe_diagnostic IN (
                    'ACK_VALID', 'REQUEST_NOT_SENT', 'RATE_LIMITED', 'AUTH_REJECTED',
                    'SCOPE_REJECTED', 'DESTINATION_REJECTED', 'WRITE_AMBIGUOUS',
                    'RECONCILIATION_FOUND', 'RECONCILIATION_NOT_FOUND',
                    'RECONCILIATION_CONFLICT', 'RESPONSE_INVALID'
                )
            )
        );

        CREATE TABLE generation_slack_notification_receipts (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            intent_id uuid NOT NULL
                REFERENCES generation_slack_notification_intents(id) ON DELETE RESTRICT,
            attempt_id uuid
                REFERENCES generation_slack_notification_delivery_attempts(id)
                ON DELETE RESTRICT,
            terminal_outcome varchar(20) NOT NULL,
            proof varchar(40),
            reason varchar(80),
            slack_workspace_id varchar(80),
            channel_id varchar(80),
            root_message_ts varchar(32),
            message_ts varchar(32),
            client_message_id uuid,
            message_digest varchar(64),
            acknowledged_at timestamptz,
            recorded_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_slack_notification_receipts_intent
                UNIQUE (intent_id),
            CONSTRAINT uq_generation_slack_notification_receipts_provider_message
                UNIQUE (slack_workspace_id, channel_id, message_ts),
            CONSTRAINT ck_generation_slack_notification_receipts_shape CHECK (
                (terminal_outcome = 'DELIVERED' AND attempt_id IS NOT NULL AND
                    proof IN ('POST_ACK', 'THREAD_RECONCILIATION') AND reason IS NULL AND
                    slack_workspace_id IS NOT NULL AND channel_id IS NOT NULL AND
                    root_message_ts ~ '^[0-9]{10,12}\.[0-9]{6}$' AND
                    message_ts ~ '^[0-9]{10,12}\.[0-9]{6}$' AND
                    message_ts <> root_message_ts AND
                    client_message_id IS NOT NULL AND
                    message_digest ~ '^[0-9a-f]{64}$' AND
                    acknowledged_at IS NOT NULL) OR
                (terminal_outcome = 'SUPPRESSED' AND attempt_id IS NULL AND proof IS NULL AND
                    reason IN (
                        'SUPPRESSED_STALE_SOURCE',
                        'SUPPRESSED_SOURCE_UNAVAILABLE',
                        'SUPPRESSED_CONFIGURATION'
                    ) AND slack_workspace_id IS NULL AND channel_id IS NULL AND
                    root_message_ts IS NULL AND message_ts IS NULL AND
                    client_message_id IS NULL AND message_digest IS NULL AND
                    acknowledged_at IS NULL) OR
                (terminal_outcome = 'DEAD' AND proof IS NULL AND reason IS NOT NULL AND
                    reason IN (
                        'AUTH_REJECTED', 'DESTINATION_REJECTED',
                        'RECONCILIATION_UNAVAILABLE', 'RECONCILIATION_CONFLICT',
                        'AMBIGUOUS_UNRESOLVED', 'RATE_LIMIT_EXHAUSTED'
                    ) AND attempt_id IS NOT NULL AND
                    slack_workspace_id IS NOT NULL AND channel_id IS NOT NULL AND
                    root_message_ts ~ '^[0-9]{10,12}\.[0-9]{6}$' AND
                    client_message_id IS NOT NULL AND
                    message_digest ~ '^[0-9a-f]{64}$' AND
                    message_ts IS NULL AND acknowledged_at IS NULL)
            )
        );

        CREATE FUNCTION tc_generation_slack_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'generation Slack notification ledgers are append-only'
                USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER trg_generation_suggestion_materializations_immutable
        BEFORE UPDATE OR DELETE ON generation_suggestion_materializations
        FOR EACH ROW EXECUTE FUNCTION tc_generation_slack_immutable();
        CREATE TRIGGER trg_generation_slack_notification_intents_immutable
        BEFORE UPDATE OR DELETE ON generation_slack_notification_intents
        FOR EACH ROW EXECUTE FUNCTION tc_generation_slack_immutable();
        CREATE TRIGGER trg_generation_slack_notification_delivery_attempts_immutable
        BEFORE UPDATE OR DELETE ON generation_slack_notification_delivery_attempts
        FOR EACH ROW EXECUTE FUNCTION tc_generation_slack_immutable();
        -- PostgreSQL identifiers are limited to 63 bytes; spell the immutable
        -- observation trigger exactly as PostgreSQL stores it.
        CREATE TRIGGER trg_generation_slack_notification_delivery_observations_immutab
        BEFORE UPDATE OR DELETE ON generation_slack_notification_delivery_observations
        FOR EACH ROW EXECUTE FUNCTION tc_generation_slack_immutable();
        CREATE TRIGGER trg_generation_slack_notification_receipts_immutable
        BEFORE UPDATE OR DELETE ON generation_slack_notification_receipts
        FOR EACH ROW EXECUTE FUNCTION tc_generation_slack_immutable();

        CREATE FUNCTION tc_generation_suggestion_materializations_links()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE matches integer;
        BEGIN
            SELECT count(*) INTO matches
              FROM generation_jobs job
              JOIN generation_jobs root ON root.id = job.root_job_id
              JOIN slack_work_items item ON item.id = job.slack_work_item_id
              JOIN generation_change_assessments assessment
                ON assessment.id = NEW.generation_change_assessment_id
             WHERE job.id = NEW.generation_job_id
               AND root.id = NEW.generation_root_job_id
               AND item.workspace_id = NEW.workspace_id
               AND job.status = 'SUCCEEDED'
               AND job.generation_trigger = 'AUTOMATIC'
               AND root.generation_trigger = 'AUTOMATIC'
               AND job.schema_version = 'generation-suggestion/1.0.0'
               AND assessment.slack_work_item_id = item.id
               AND assessment.outcome = 'REGENERATE'
               AND assessment.selected_generation_job_id = root.id
               AND (NEW.receipt->>'generationJobId') =
                    'generation_job_' || replace(job.id::text, '-', '')
               AND (NEW.receipt->>'id') =
                    'generation_suggestion_materialization_' ||
                    replace(NEW.id::text, '-', '')
               AND (NEW.receipt->>'generationRootJobId') =
                    'generation_job_' || replace(root.id::text, '-', '')
               AND (NEW.receipt->>'generationChangeAssessmentId') =
                    'generation_change_assessment_' || replace(assessment.id::text, '-', '')
               AND (NEW.receipt->>'candidateCount')::integer = NEW.candidate_count
               AND (NEW.receipt->>'suggestionCount')::integer = NEW.suggestion_count
               AND (NEW.receipt->>'noChangeCount')::integer = NEW.no_change_count
               AND (NEW.receipt->>'automaticOverwriteCount')::integer = 0
               AND (NEW.receipt->>'resultDigest') = NEW.result_digest;
            IF matches <> 1 THEN
                RAISE EXCEPTION 'generation suggestion materialization provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_generation_slack_notification_intents_links()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE matches integer;
        BEGIN
            SELECT count(*) INTO matches
              FROM generation_jobs job
              JOIN generation_jobs root ON root.id = job.root_job_id
              JOIN slack_work_items item ON item.id = job.slack_work_item_id
              LEFT JOIN slack_channel_allowlist allowlist
                ON allowlist.workspace_id = item.workspace_id
               AND allowlist.slack_workspace_id = item.slack_workspace_id
               AND allowlist.channel_id = item.channel_id
              LEFT JOIN generation_applies apply_row ON apply_row.id = NEW.generation_apply_id
              LEFT JOIN generation_suggestion_materializations materialization
                ON materialization.generation_job_id = job.id
               AND materialization.generation_change_assessment_id =
                    NEW.generation_change_assessment_id
             WHERE job.id = NEW.generation_job_id
               AND root.id = NEW.generation_root_job_id
               AND item.id = NEW.slack_work_item_id
               AND item.workspace_id = NEW.workspace_id
               AND item.slack_workspace_id = NEW.slack_workspace_id
               AND item.channel_id = NEW.channel_id
               AND item.message_ts = NEW.root_message_ts
               AND NEW.thread_ts = item.message_ts
               AND (NEW.delivery_disposition <> 'POST' OR allowlist.enabled)
               AND (NEW.delivery_disposition <> 'POST' OR
                    item.source_version = NEW.source_version)
               AND job.slack_work_item_id = root.slack_work_item_id
               AND job.source_version = root.source_version
               AND job.source_version = NEW.source_version
               AND job.attempt_number = NEW.attempt_number
               AND job.status = NEW.terminal_status
               AND job.generation_trigger = 'AUTOMATIC'
               AND root.generation_trigger = 'AUTOMATIC'
               AND (NEW.message_snapshot->>'templateVersion') = NEW.template_version
               AND (NEW.message_snapshot->>'locale') = NEW.locale
               AND (NEW.message_snapshot->>'outcome') = NEW.notification_kind
               AND (NEW.message_snapshot->>'messageText') = NEW.message_text
               AND (NEW.message_snapshot->>'messageDigest') = NEW.message_digest
               AND NEW.message_snapshot->'link' = jsonb_build_object(
                    'linkMode', NEW.link_mode, 'uiUrl', NEW.ui_url
               )
               AND (NEW.notification_kind <> 'SUCCESS_APPLIED' OR
                    apply_row.generation_job_id = job.id AND
                    (SELECT count(*) FROM jsonb_object_keys(NEW.message_snapshot)) = 10 AND
                    NEW.message_snapshot ?& ARRAY[
                        'templateVersion', 'locale', 'outcome', 'createdCount',
                        'reusedCount', 'generatedCount', 'parentOutcome', 'link',
                        'messageText', 'messageDigest'
                    ] AND
                    (NEW.message_snapshot->>'createdCount')::integer =
                        (apply_row.receipt->'counts'->>'created')::integer AND
                    (NEW.message_snapshot->>'reusedCount')::integer =
                        (apply_row.receipt->'counts'->>'reused')::integer AND
                    (NEW.message_snapshot->>'generatedCount')::integer =
                        (apply_row.receipt->'counts'->>'generated')::integer AND
                    (NEW.message_snapshot->>'parentOutcome') =
                        (apply_row.receipt->'parent'->>'outcome'))
               AND (NEW.notification_kind <> 'SUCCESS_SUGGESTIONS' OR
                    materialization.id IS NOT NULL AND
                    (SELECT count(*) FROM jsonb_object_keys(NEW.message_snapshot)) = 8 AND
                    NEW.message_snapshot ?& ARRAY[
                        'templateVersion', 'locale', 'outcome', 'suggestionCount',
                        'automaticOverwriteCount', 'link', 'messageText',
                        'messageDigest'
                    ] AND
                    (NEW.message_snapshot->>'suggestionCount')::integer =
                        materialization.suggestion_count AND
                    (NEW.message_snapshot->>'automaticOverwriteCount')::integer = 0)
               AND (NEW.notification_kind <> 'FAILURE' OR
                    job.error_retryable = false AND
                    (SELECT count(*) FROM jsonb_object_keys(NEW.message_snapshot)) = 10 AND
                    NEW.message_snapshot ?& ARRAY[
                        'templateVersion', 'locale', 'outcome', 'attemptNumber',
                        'errorCode', 'errorLabel', 'retryable', 'link',
                        'messageText', 'messageDigest'
                    ] AND
                    (NEW.message_snapshot->>'attemptNumber')::integer =
                        job.attempt_number AND
                    (NEW.message_snapshot->>'retryable')::boolean = false AND
                    (NEW.message_snapshot->>'errorCode') = CASE
                        WHEN job.error_code IN (
                            'GENERATION_UNSUPPORTED_SCHEMA_VERSION',
                            'GENERATION_AUTH_REJECTED', 'GENERATION_TIMEOUT',
                            'GENERATION_TRANSPORT_UNAVAILABLE',
                            'GENERATION_RATE_LIMITED',
                            'GENERATION_PROVIDER_REJECTED',
                            'GENERATION_PROVIDER_ERROR',
                            'GENERATION_INVALID_CONTENT_TYPE',
                            'GENERATION_RESPONSE_TOO_LARGE',
                            'GENERATION_MALFORMED_JSON',
                            'GENERATION_INVALID_RESPONSE'
                        ) THEN job.error_code
                        ELSE 'GENERATION_PROVIDER_ERROR'
                    END)
               AND (NEW.delivery_disposition <> 'POST' OR EXISTS (
                    SELECT 1
                      FROM slack_threads thread
                      JOIN slack_messages message
                        ON message.thread_id = thread.id AND message.is_root
                     WHERE thread.slack_work_item_id = item.id
                       AND thread.thread_ts = item.message_ts
                       AND message.message_ts = item.message_ts
                       AND (SELECT revision.is_deleted
                              FROM slack_message_revisions revision
                             WHERE revision.message_id = message.id
                             ORDER BY revision.revision DESC, revision.id DESC
                             LIMIT 1) = false
               ));
            IF matches <> 1 THEN
                RAISE EXCEPTION 'generation Slack notification intent provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_generation_slack_notification_delivery_attempts_links()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE matches integer;
        BEGIN
            SELECT count(*) INTO matches
              FROM generation_slack_notification_intents intent
              JOIN background_jobs job ON job.id = NEW.background_job_id
             WHERE intent.id = NEW.intent_id
               AND intent.delivery_disposition = 'POST'
               AND job.job_type = 'SLACK_GENERATION_NOTIFICATION_DELIVERY'
               AND job.payload = jsonb_build_object(
                    'notificationIntentId', NEW.intent_id::text
               )
               AND job.status = 'RUNNING'
               AND job.attempts = NEW.background_job_attempt
               AND job.locked_by = NEW.worker_id;
            IF matches <> 1 THEN
                RAISE EXCEPTION 'generation Slack notification delivery permit is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.operation = 'POST' AND EXISTS (
                SELECT 1
                  FROM generation_slack_notification_delivery_attempts prior
                  LEFT JOIN generation_slack_notification_delivery_observations observed
                    ON observed.attempt_id = prior.id
                 WHERE prior.intent_id = NEW.intent_id AND prior.operation = 'POST'
                   AND prior.id <> NEW.id
                   AND (observed.id IS NULL OR observed.classification NOT IN (
                       'DEFINITIVE_NO_DELIVERY', 'RATE_LIMITED'
                   ))
            ) THEN
                RAISE EXCEPTION 'an unresolved Slack POST permit may only reconcile'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.operation = 'RECONCILE' AND NOT EXISTS (
                SELECT 1
                  FROM generation_slack_notification_delivery_attempts prior
                  LEFT JOIN generation_slack_notification_delivery_observations observed
                    ON observed.attempt_id = prior.id
                 WHERE prior.intent_id = NEW.intent_id AND prior.operation = 'POST'
                   AND prior.id <> NEW.id
                   AND (observed.id IS NULL OR observed.classification NOT IN (
                       'DEFINITIVE_NO_DELIVERY', 'RATE_LIMITED'
                   ))
            ) THEN
                RAISE EXCEPTION 'Slack reconciliation requires an unresolved POST permit'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_generation_slack_notification_delivery_observations_links()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE operation_value varchar(20);
        BEGIN
            SELECT operation INTO operation_value
              FROM generation_slack_notification_delivery_attempts
             WHERE id = NEW.attempt_id;
            IF operation_value IS NULL OR
               (operation_value = 'POST' AND NEW.classification IN (
                    'RECONCILED_FOUND', 'RECONCILED_NOT_FOUND'
               )) OR
               (operation_value = 'RECONCILE' AND NEW.classification NOT IN (
                    'AMBIGUOUS', 'RATE_LIMITED',
                    'RECONCILED_FOUND', 'RECONCILED_NOT_FOUND'
               )) THEN
                RAISE EXCEPTION 'Slack delivery observation does not match its permit'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_generation_slack_notification_receipts_links()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE matches integer;
        BEGIN
            IF NEW.terminal_outcome = 'SUPPRESSED' THEN
                SELECT count(*) INTO matches
                  FROM generation_slack_notification_intents intent
                  JOIN slack_work_items item ON item.id = intent.slack_work_item_id
                 WHERE intent.id = NEW.intent_id
                   AND (
                       NEW.reason = 'SUPPRESSED_CONFIGURATION' OR
                       NEW.reason = 'SUPPRESSED_STALE_SOURCE' AND
                            item.source_version <> intent.source_version OR
                       NEW.reason = 'SUPPRESSED_SOURCE_UNAVAILABLE' AND (
                            NOT item.root_seen OR NOT EXISTS (
                                SELECT 1
                                  FROM slack_threads thread
                                  JOIN slack_messages message
                                    ON message.thread_id = thread.id
                                   AND message.is_root
                                 WHERE thread.slack_work_item_id = item.id
                                   AND thread.thread_ts = item.message_ts
                                   AND message.message_ts = item.message_ts
                                   AND (SELECT revision.is_deleted
                                          FROM slack_message_revisions revision
                                         WHERE revision.message_id = message.id
                                         ORDER BY revision.revision DESC,
                                                  revision.id DESC
                                         LIMIT 1) = false
                            )
                       )
                   );
            ELSE
                SELECT count(*) INTO matches
                  FROM generation_slack_notification_intents intent
                  JOIN generation_slack_notification_delivery_attempts attempt
                    ON attempt.id = NEW.attempt_id AND attempt.intent_id = intent.id
                 JOIN generation_slack_notification_delivery_observations observation
                    ON observation.attempt_id = attempt.id
                 WHERE intent.id = NEW.intent_id
                   AND NEW.slack_workspace_id = intent.slack_workspace_id
                   AND NEW.channel_id = intent.channel_id
                   AND NEW.root_message_ts = intent.root_message_ts
                   AND NEW.client_message_id = intent.client_message_id
                   AND NEW.message_digest = intent.message_digest
                   AND (
                       NEW.terminal_outcome = 'DELIVERED' AND
                       ((NEW.proof = 'POST_ACK' AND
                         observation.classification = 'ACKNOWLEDGED') OR
                        (NEW.proof = 'THREAD_RECONCILIATION' AND
                         observation.classification = 'RECONCILED_FOUND')) OR
                       NEW.terminal_outcome = 'DEAD' AND (
                           NEW.reason = 'AUTH_REJECTED' AND
                               observation.classification = 'DEFINITIVE_NO_DELIVERY' AND
                               observation.safe_diagnostic = 'AUTH_REJECTED' OR
                           NEW.reason = 'DESTINATION_REJECTED' AND
                               observation.classification = 'DEFINITIVE_NO_DELIVERY' AND
                               observation.safe_diagnostic = 'DESTINATION_REJECTED' OR
                           NEW.reason = 'RECONCILIATION_UNAVAILABLE' AND
                               observation.classification = 'AMBIGUOUS' AND
                               observation.safe_diagnostic = 'DESTINATION_REJECTED' OR
                           NEW.reason = 'RECONCILIATION_CONFLICT' AND
                               observation.classification = 'AMBIGUOUS' AND
                               observation.safe_diagnostic = 'RECONCILIATION_CONFLICT' OR
                           NEW.reason = 'AMBIGUOUS_UNRESOLVED' AND
                               ((observation.classification = 'RECONCILED_NOT_FOUND' AND
                                 observation.safe_diagnostic = 'RECONCILIATION_NOT_FOUND') OR
                                (observation.classification = 'AMBIGUOUS' AND
                                 observation.safe_diagnostic = 'WRITE_AMBIGUOUS')) OR
                           NEW.reason = 'RATE_LIMIT_EXHAUSTED' AND
                               observation.classification = 'RATE_LIMITED' AND
                               observation.safe_diagnostic = 'RATE_LIMITED'
                       )
                   );
            END IF;
            IF matches <> 1 THEN
                RAISE EXCEPTION 'generation Slack notification receipt proof is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_generation_jobs_t06_lineage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE matches integer;
        BEGIN
            IF NEW.retry_of_job_id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT count(*) INTO matches
              FROM generation_jobs parent
              JOIN generation_jobs root ON root.id = parent.root_job_id
             WHERE parent.id = NEW.retry_of_job_id
               AND parent.root_job_id = NEW.root_job_id
               AND parent.slack_work_item_id = NEW.slack_work_item_id
               AND parent.source_version = NEW.source_version
               AND parent.generation_trigger = NEW.generation_trigger
               AND parent.attempt_number + 1 = NEW.attempt_number
               AND root.id = NEW.root_job_id
               AND root.slack_work_item_id = NEW.slack_work_item_id
               AND root.source_version = NEW.source_version
               AND root.generation_trigger = NEW.generation_trigger;
            IF matches <> 1 THEN
                RAISE EXCEPTION 'generation retry lineage crosses its sealed root'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER trg_generation_suggestion_materializations_links
        AFTER INSERT ON generation_suggestion_materializations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_generation_suggestion_materializations_links();
        CREATE CONSTRAINT TRIGGER trg_generation_slack_notification_intents_links
        AFTER INSERT ON generation_slack_notification_intents
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_generation_slack_notification_intents_links();
        CREATE CONSTRAINT TRIGGER trg_generation_slack_notification_delivery_attempts_links
        AFTER INSERT ON generation_slack_notification_delivery_attempts
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_generation_slack_notification_delivery_attempts_links();
        CREATE CONSTRAINT TRIGGER trg_generation_slack_notification_delivery_observations_links
        AFTER INSERT ON generation_slack_notification_delivery_observations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_generation_slack_notification_delivery_observations_links();
        CREATE CONSTRAINT TRIGGER trg_generation_slack_notification_receipts_links
        AFTER INSERT ON generation_slack_notification_receipts
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_generation_slack_notification_receipts_links();
        CREATE CONSTRAINT TRIGGER trg_generation_jobs_t06_lineage
        AFTER INSERT OR UPDATE ON generation_jobs
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_generation_jobs_t06_lineage();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER trg_generation_jobs_t06_lineage ON generation_jobs;
        DROP FUNCTION tc_generation_jobs_t06_lineage();
        DROP TRIGGER trg_generation_slack_notification_receipts_links
            ON generation_slack_notification_receipts;
        DROP TRIGGER trg_generation_slack_notification_delivery_observations_links
            ON generation_slack_notification_delivery_observations;
        DROP TRIGGER trg_generation_slack_notification_delivery_attempts_links
            ON generation_slack_notification_delivery_attempts;
        DROP TRIGGER trg_generation_slack_notification_intents_links
            ON generation_slack_notification_intents;
        DROP TRIGGER trg_generation_suggestion_materializations_links
            ON generation_suggestion_materializations;
        DROP FUNCTION tc_generation_slack_notification_receipts_links();
        DROP FUNCTION tc_generation_slack_notification_delivery_observations_links();
        DROP FUNCTION tc_generation_slack_notification_delivery_attempts_links();
        DROP FUNCTION tc_generation_slack_notification_intents_links();
        DROP FUNCTION tc_generation_suggestion_materializations_links();

        DROP TRIGGER trg_generation_slack_notification_receipts_immutable
            ON generation_slack_notification_receipts;
        DROP TRIGGER trg_generation_slack_notification_delivery_observations_immutable
            ON generation_slack_notification_delivery_observations;
        DROP TRIGGER trg_generation_slack_notification_delivery_attempts_immutable
            ON generation_slack_notification_delivery_attempts;
        DROP TRIGGER trg_generation_slack_notification_intents_immutable
            ON generation_slack_notification_intents;
        DROP TRIGGER trg_generation_suggestion_materializations_immutable
            ON generation_suggestion_materializations;
        DROP FUNCTION tc_generation_slack_immutable();

        DROP TABLE generation_slack_notification_receipts;
        DROP TABLE generation_slack_notification_delivery_observations;
        DROP TABLE generation_slack_notification_delivery_attempts;
        DROP TABLE generation_slack_notification_intents;
        DROP TABLE generation_suggestion_materializations;
        DROP INDEX uq_generation_jobs_root_succeeded;
        ALTER TABLE generation_jobs DROP CONSTRAINT uq_generation_jobs_root_attempt;
        """
    )
