"""One-way management ownership for generation-created test cases.

Revision ID: 20260827_0023
Revises: 20260827_0022
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_0023"
down_revision: str | None = "20260827_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE api_idempotency_receipts (
            workspace_id uuid NOT NULL
                REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            idempotency_key varchar(255) NOT NULL,
            operation varchar(128) NOT NULL,
            request_hash varchar(64) NOT NULL,
            response jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, idempotency_key),
            CONSTRAINT ck_api_idempotency_receipts_operation_nonblank
                CHECK (char_length(btrim(operation)) > 0),
            CONSTRAINT ck_api_idempotency_receipts_request_hash
                CHECK (request_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_api_idempotency_receipts_response_object
                CHECK (jsonb_typeof(response) = 'object')
        );

        ALTER TABLE test_cases
            ADD COLUMN generation_managed boolean NOT NULL DEFAULT false,
            ADD COLUMN generation_lineage_id uuid,
            ADD COLUMN generation_created_outcome_id uuid,
            ADD COLUMN generation_baseline_version_id uuid,
            ADD COLUMN generation_latest_version_id uuid,
            ADD COLUMN generation_released_at timestamptz,
            ADD COLUMN generation_release_reason varchar(500),
            ADD COLUMN generation_release_actor jsonb,
            ADD COLUMN generation_release_mutation varchar(40),
            ADD COLUMN generation_released_from_version_id uuid,
            ADD COLUMN generation_released_by_version_id uuid,
            ADD CONSTRAINT uq_test_cases_generation_lineage
                UNIQUE (generation_lineage_id),
            ADD CONSTRAINT uq_test_cases_generation_created_outcome
                UNIQUE (generation_created_outcome_id),
            ADD CONSTRAINT fk_test_cases_generation_created_outcome
                FOREIGN KEY (generation_created_outcome_id)
                REFERENCES generation_apply_outcomes(id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            ADD CONSTRAINT fk_test_cases_generation_baseline_version
                FOREIGN KEY (id, generation_baseline_version_id)
                REFERENCES test_case_versions(test_case_id, id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            ADD CONSTRAINT fk_test_cases_generation_latest_version
                FOREIGN KEY (id, generation_latest_version_id)
                REFERENCES test_case_versions(test_case_id, id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            ADD CONSTRAINT fk_test_cases_generation_released_from_version
                FOREIGN KEY (id, generation_released_from_version_id)
                REFERENCES test_case_versions(test_case_id, id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            ADD CONSTRAINT fk_test_cases_generation_released_by_version
                FOREIGN KEY (id, generation_released_by_version_id)
                REFERENCES test_case_versions(test_case_id, id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED;

        CREATE TABLE test_case_generation_management_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL
                REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            test_case_id uuid NOT NULL
                REFERENCES test_cases(id) ON DELETE RESTRICT,
            event_type varchar(20) NOT NULL,
            lineage_id uuid NOT NULL,
            created_outcome_id uuid NOT NULL
                REFERENCES generation_apply_outcomes(id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            baseline_generated_version_id uuid NOT NULL
                REFERENCES test_case_versions(id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            latest_generated_version_id uuid NOT NULL
                REFERENCES test_case_versions(id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            released_at timestamptz,
            release_reason varchar(500),
            release_actor jsonb,
            release_mutation varchar(40),
            released_from_version_id uuid
                REFERENCES test_case_versions(id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            released_by_version_id uuid
                REFERENCES test_case_versions(id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_test_case_generation_management_events_case_type
                UNIQUE (test_case_id, event_type),
            CONSTRAINT ck_test_case_generation_management_events_type
                CHECK (event_type IN ('INITIALIZED', 'RELEASED')),
            CONSTRAINT ck_test_case_generation_management_events_shape CHECK (
                (event_type = 'INITIALIZED' AND released_at IS NULL AND
                 release_reason IS NULL AND release_actor IS NULL AND
                 release_mutation IS NULL AND released_from_version_id IS NULL AND
                 released_by_version_id IS NULL) OR
                (event_type = 'RELEASED' AND released_at IS NOT NULL AND
                 char_length(btrim(release_reason)) > 0 AND
                 jsonb_typeof(release_actor) = 'object' AND
                 release_mutation IN (
                    'CONTENT_VERSION', 'PARENT_MOVE', 'SOFT_DELETE', 'RESTORE_DELETED',
                    'LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE'
                 ) AND released_from_version_id IS NOT NULL AND
                 ((release_mutation = 'CONTENT_VERSION' AND
                   released_by_version_id IS NOT NULL) OR
                  (release_mutation IN ('PARENT_MOVE', 'SOFT_DELETE', 'RESTORE_DELETED') AND
                   released_by_version_id IS NULL) OR
                  release_mutation = 'LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE'))
            )
        );
        CREATE INDEX ix_test_case_generation_management_events_lineage
            ON test_case_generation_management_events (lineage_id, occurred_at, id);

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM generation_apply_outcomes
                 WHERE outcome = 'CREATED'
                 GROUP BY test_case_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'generation management legacy CREATED outcome collision requires repair'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                  FROM generation_apply_outcomes AS outcome
                  JOIN generation_applies AS apply_row
                    ON apply_row.id = outcome.generation_apply_id
                  JOIN generation_jobs AS job
                    ON job.id = apply_row.generation_job_id
                  LEFT JOIN test_case_versions AS baseline
                    ON baseline.id = outcome.test_case_version_id
                 WHERE outcome.outcome = 'CREATED'
                   AND (outcome.preserved OR
                        baseline.id IS NULL OR
                        baseline.test_case_id <> outcome.test_case_id OR
                        baseline.version_number <> 1 OR
                        baseline.source_type <> 'SLACK_AI' OR
                        job.status <> 'SUCCEEDED' OR
                        job.context_digest IS DISTINCT FROM apply_row.context_digest OR
                        baseline.source_metadata->>'slackWorkItemId' IS DISTINCT FROM
                            'slack_work_item_' ||
                            replace(job.slack_work_item_id::text, '-', '') OR
                        baseline.source_metadata->>'generationJobId' IS DISTINCT FROM
                            'generation_job_' ||
                            replace(apply_row.generation_job_id::text, '-', '') OR
                        baseline.source_metadata->>'contextDigest' IS DISTINCT FROM
                            apply_row.context_digest OR
                        baseline.source_metadata->>'resultDigest' IS DISTINCT FROM
                            apply_row.result_digest OR
                        baseline.source_metadata->>'outputSchemaVersion' IS DISTINCT FROM
                            job.schema_version OR
                        baseline.source_metadata->>'generationVersion' IS DISTINCT FROM
                            job.generation_version::text OR
                        outcome.snapshot #> '{case,currentVersion,source}' IS DISTINCT FROM
                            baseline.source_metadata OR
                        baseline.source_metadata->'evidenceNodeIds' IS DISTINCT FROM
                            job.result->'cases'->outcome.generated_case_index->
                                'evidenceNodeIds' OR
                        jsonb_typeof(baseline.source_metadata->'evidenceNodeIds')
                            IS DISTINCT FROM 'array' OR
                        jsonb_array_length(baseline.source_metadata->'evidenceNodeIds') = 0 OR
                        jsonb_typeof(baseline.source_metadata->'sourceRevisionIds')
                            IS DISTINCT FROM 'array' OR
                        jsonb_array_length(baseline.source_metadata->'sourceRevisionIds') = 0 OR
                        EXISTS (
                            SELECT 1
                              FROM jsonb_array_elements_text(
                                  baseline.source_metadata->'evidenceNodeIds'
                              ) AS evidence(value)
                             WHERE NOT EXISTS (
                                 SELECT 1
                                   FROM generation_contexts AS context_row
                                  WHERE context_row.generation_job_id = job.id
                                    AND context_row.node_id IS NOT NULL
                                    AND 'knowledge_node_' || replace(
                                        context_row.node_id::text, '-', ''
                                    ) = evidence.value
                             )
                        ) OR
                        EXISTS (
                            SELECT 1
                              FROM jsonb_array_elements_text(
                                  baseline.source_metadata->'sourceRevisionIds'
                              ) AS source_revision(value)
                             WHERE NOT EXISTS (
                                 SELECT 1
                                   FROM generation_contexts AS context_row
                                  WHERE context_row.generation_job_id = job.id
                                    AND context_row.node_id IS NOT NULL
                                    AND baseline.source_metadata->'evidenceNodeIds' ?
                                        ('knowledge_node_' || replace(
                                            context_row.node_id::text, '-', ''
                                        ))
                                    AND lower(context_row.source_entity_type) || '_' ||
                                        replace(context_row.source_entity_id::text, '-', '') =
                                        source_revision.value
                             )
                        ) OR
                        EXISTS (
                            SELECT 1
                              FROM generation_contexts AS context_row
                             WHERE context_row.generation_job_id = job.id
                               AND context_row.node_id IS NOT NULL
                               AND baseline.source_metadata->'evidenceNodeIds' ?
                                   ('knowledge_node_' || replace(
                                       context_row.node_id::text, '-', ''
                                   ))
                               AND NOT baseline.source_metadata->'sourceRevisionIds' ?
                                   (lower(context_row.source_entity_type) || '_' ||
                                    replace(context_row.source_entity_id::text, '-', ''))
                        ) OR
                        jsonb_array_length(
                            baseline.source_metadata->'sourceRevisionIds'
                        ) <> (
                            SELECT count(DISTINCT source_revision.value)
                              FROM jsonb_array_elements_text(
                                  baseline.source_metadata->'sourceRevisionIds'
                              ) AS source_revision(value)
                        ))
            ) THEN
                RAISE EXCEPTION
                    'generation management legacy CREATED provenance is contradictory'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$;

        CREATE TEMPORARY TABLE tc_t04_generation_management_backfill
        ON COMMIT DROP AS
        SELECT
            test_case.id AS test_case_id,
            test_case.workspace_id,
            outcome.id AS created_outcome_id,
            outcome.test_case_version_id AS baseline_version_id,
            apply_row.applied_at AS initialized_at,
            gen_random_uuid() AS lineage_id,
            (
                test_case.deleted_at IS NOT NULL OR
                test_case.current_version_id <> outcome.test_case_version_id OR
                test_case.work_item_id <> apply_row.parent_work_item_id OR
                EXISTS (
                    SELECT 1
                      FROM audit_logs AS audit
                     WHERE audit.entity_type = 'TEST_CASE'
                       AND audit.entity_id = test_case.id
                       AND audit.occurred_at > apply_row.applied_at
                       AND audit.action IN (
                           'CREATE_VERSION', 'RESTORE_VERSION', 'MOVE',
                           'SOFT_DELETE', 'RESTORE'
                       )
                )
            ) AS diverged,
            COALESCE(
                LEAST(
                    (
                    SELECT min(audit.occurred_at)
                      FROM audit_logs AS audit
                     WHERE audit.entity_type = 'TEST_CASE'
                       AND audit.entity_id = test_case.id
                       AND audit.occurred_at > apply_row.applied_at
                       AND audit.action IN (
                           'CREATE_VERSION', 'RESTORE_VERSION', 'MOVE',
                           'SOFT_DELETE', 'RESTORE'
                       )
                    ),
                    (
                    SELECT min(later_version.created_at)
                      FROM test_case_versions AS later_version
                     WHERE later_version.test_case_id = test_case.id
                       AND later_version.version_number > 1
                    ),
                    test_case.deleted_at
                ),
                test_case.updated_at
            ) AS released_at,
            (
                SELECT later_version.id
                  FROM test_case_versions AS later_version
                 WHERE later_version.test_case_id = test_case.id
                   AND later_version.version_number > 1
                 ORDER BY later_version.version_number
                 LIMIT 1
            ) AS released_by_version_id
        FROM generation_apply_outcomes AS outcome
        JOIN generation_applies AS apply_row
          ON apply_row.id = outcome.generation_apply_id
        JOIN test_cases AS test_case
          ON test_case.id = outcome.test_case_id
        WHERE outcome.outcome = 'CREATED';

        UPDATE test_cases AS test_case
           SET generation_managed = NOT backfill.diverged,
               generation_lineage_id = backfill.lineage_id,
               generation_created_outcome_id = backfill.created_outcome_id,
               generation_baseline_version_id = backfill.baseline_version_id,
               generation_latest_version_id = backfill.baseline_version_id,
               generation_released_at = CASE
                   WHEN backfill.diverged THEN backfill.released_at ELSE NULL END,
               generation_release_reason = CASE
                   WHEN backfill.diverged
                   THEN 'LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE' ELSE NULL END,
               generation_release_actor = CASE
                   WHEN backfill.diverged
                   THEN '{"type":"SYSTEM","actorLabel":"PE-SE2-T04 migration"}'::jsonb
                   ELSE NULL END,
               generation_release_mutation = CASE
                   WHEN backfill.diverged
                   THEN 'LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE' ELSE NULL END,
               generation_released_from_version_id = CASE
                   WHEN backfill.diverged THEN backfill.baseline_version_id ELSE NULL END,
               generation_released_by_version_id = CASE
                   WHEN backfill.diverged THEN backfill.released_by_version_id ELSE NULL END
          FROM tc_t04_generation_management_backfill AS backfill
         WHERE test_case.id = backfill.test_case_id;

        INSERT INTO test_case_generation_management_events (
            id, workspace_id, test_case_id, event_type, lineage_id,
            created_outcome_id, baseline_generated_version_id,
            latest_generated_version_id, occurred_at
        )
        SELECT gen_random_uuid(), workspace_id, test_case_id, 'INITIALIZED', lineage_id,
               created_outcome_id, baseline_version_id, baseline_version_id, initialized_at
          FROM tc_t04_generation_management_backfill;

        INSERT INTO test_case_generation_management_events (
            id, workspace_id, test_case_id, event_type, lineage_id,
            created_outcome_id, baseline_generated_version_id,
            latest_generated_version_id, released_at, release_reason, release_actor,
            release_mutation, released_from_version_id, released_by_version_id, occurred_at
        )
        SELECT gen_random_uuid(), workspace_id, test_case_id, 'RELEASED', lineage_id,
               created_outcome_id, baseline_version_id, baseline_version_id, released_at,
               'LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE',
               '{"type":"SYSTEM","actorLabel":"PE-SE2-T04 migration"}'::jsonb,
               'LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE', baseline_version_id,
               released_by_version_id, released_at
          FROM tc_t04_generation_management_backfill
         WHERE diverged;

        CREATE FUNCTION tc_test_cases_generation_management_shape()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT NEW.generation_managed AND NEW.generation_lineage_id IS NULL THEN
                IF NEW.generation_created_outcome_id IS NOT NULL OR
                   NEW.generation_baseline_version_id IS NOT NULL OR
                   NEW.generation_latest_version_id IS NOT NULL OR
                   NEW.generation_released_at IS NOT NULL OR
                   NEW.generation_release_reason IS NOT NULL OR
                   NEW.generation_release_actor IS NOT NULL OR
                   NEW.generation_release_mutation IS NOT NULL OR
                   NEW.generation_released_from_version_id IS NOT NULL OR
                   NEW.generation_released_by_version_id IS NOT NULL THEN
                    RAISE EXCEPTION 'generation management never-managed shape is invalid'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF NEW.generation_lineage_id IS NULL OR
               NEW.generation_created_outcome_id IS NULL OR
               NEW.generation_baseline_version_id IS NULL OR
               NEW.generation_latest_version_id IS NULL THEN
                RAISE EXCEPTION 'generation management lineage shape is incomplete'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.generation_managed THEN
                IF NEW.generation_released_at IS NOT NULL OR
                   NEW.generation_release_reason IS NOT NULL OR
                   NEW.generation_release_actor IS NOT NULL OR
                   NEW.generation_release_mutation IS NOT NULL OR
                   NEW.generation_released_from_version_id IS NOT NULL OR
                   NEW.generation_released_by_version_id IS NOT NULL THEN
                    RAISE EXCEPTION 'generation management active shape contains a release'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF NEW.generation_released_at IS NULL OR
               NEW.generation_release_reason IS NULL OR
               char_length(btrim(NEW.generation_release_reason)) = 0 OR
               jsonb_typeof(NEW.generation_release_actor) IS DISTINCT FROM 'object' OR
               NOT NEW.generation_release_actor ? 'type' OR
               COALESCE(NEW.generation_release_actor->>'type', '') NOT IN (
                   'UI', 'API', 'SYSTEM', 'SLACK', 'WORKER'
               ) OR
               NEW.generation_release_actor - ARRAY[
                   'type', 'actorLabel', 'externalActorId'
               ]::text[] <> '{}'::jsonb OR
               (NEW.generation_release_actor ? 'actorLabel' AND
                COALESCE(char_length(btrim(
                    NEW.generation_release_actor->>'actorLabel'
                )), 0) = 0) OR
               (NEW.generation_release_actor ? 'externalActorId' AND
                COALESCE(char_length(btrim(
                    NEW.generation_release_actor->>'externalActorId'
                )), 0) = 0) OR
               NEW.generation_release_mutation NOT IN (
                   'CONTENT_VERSION', 'PARENT_MOVE', 'SOFT_DELETE', 'RESTORE_DELETED',
                   'LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE'
               ) OR
               NEW.generation_released_from_version_id IS NULL OR
               (NEW.generation_release_mutation = 'CONTENT_VERSION' AND
                NEW.generation_released_by_version_id IS NULL) OR
               (NEW.generation_release_mutation IN (
                    'PARENT_MOVE', 'SOFT_DELETE', 'RESTORE_DELETED'
                ) AND
                NEW.generation_released_by_version_id IS NOT NULL) THEN
                RAISE EXCEPTION 'generation management released shape is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_test_cases_generation_management_one_way()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            operation text := current_setting('tc.generation_management_operation', true);
        BEGIN
            IF NOT OLD.generation_managed AND NEW.generation_managed THEN
                IF OLD.generation_lineage_id IS NOT NULL OR operation <> 'initialize' THEN
                    RAISE EXCEPTION 'generation management cannot be initialized or reacquired'
                        USING ERRCODE = '55000';
                END IF;
            END IF;

            IF OLD.generation_lineage_id IS NOT NULL THEN
                IF NEW.generation_lineage_id IS DISTINCT FROM OLD.generation_lineage_id OR
                   NEW.generation_created_outcome_id IS DISTINCT FROM
                       OLD.generation_created_outcome_id OR
                   NEW.generation_baseline_version_id IS DISTINCT FROM
                       OLD.generation_baseline_version_id THEN
                    RAISE EXCEPTION 'generation management lineage is immutable'
                        USING ERRCODE = '55000';
                END IF;

                IF NOT OLD.generation_managed THEN
                    IF NEW.generation_managed IS DISTINCT FROM OLD.generation_managed OR
                       NEW.generation_latest_version_id IS DISTINCT FROM
                           OLD.generation_latest_version_id OR
                       NEW.generation_released_at IS DISTINCT FROM OLD.generation_released_at OR
                       NEW.generation_release_reason IS DISTINCT FROM
                           OLD.generation_release_reason OR
                       NEW.generation_release_actor IS DISTINCT FROM
                           OLD.generation_release_actor OR
                       NEW.generation_release_mutation IS DISTINCT FROM
                           OLD.generation_release_mutation OR
                       NEW.generation_released_from_version_id IS DISTINCT FROM
                           OLD.generation_released_from_version_id OR
                       NEW.generation_released_by_version_id IS DISTINCT FROM
                           OLD.generation_released_by_version_id THEN
                        RAISE EXCEPTION 'generation management release is irreversible'
                            USING ERRCODE = '55000';
                    END IF;
                ELSIF NEW.generation_managed THEN
                    IF NEW.current_version_id IS DISTINCT FROM OLD.current_version_id OR
                       NEW.generation_latest_version_id IS DISTINCT FROM
                           OLD.generation_latest_version_id THEN
                        RAISE EXCEPTION 'generation management automatic update is unauthorized'
                            USING ERRCODE = '55000';
                    END IF;
                ELSE
                    IF NEW.generation_latest_version_id IS DISTINCT FROM
                           OLD.generation_latest_version_id OR
                       NEW.generation_released_from_version_id IS DISTINCT FROM
                           OLD.generation_latest_version_id THEN
                        RAISE EXCEPTION 'generation management release baseline is invalid'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NEW.generation_release_mutation = 'CONTENT_VERSION' AND
                       NEW.generation_released_by_version_id IS DISTINCT FROM
                           NEW.current_version_id THEN
                        RAISE EXCEPTION 'generation management release version is invalid'
                            USING ERRCODE = '55000';
                    END IF;
                    IF NEW.generation_release_mutation IN (
                           'PARENT_MOVE', 'SOFT_DELETE', 'RESTORE_DELETED'
                       ) AND NEW.current_version_id IS DISTINCT FROM OLD.current_version_id THEN
                        RAISE EXCEPTION 'generation management aggregate release changed content'
                            USING ERRCODE = '55000';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_test_cases_generation_management_links()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            linked_outcome generation_apply_outcomes%ROWTYPE;
            linked_apply generation_applies%ROWTYPE;
            linked_job generation_jobs%ROWTYPE;
            baseline test_case_versions%ROWTYPE;
            outcome_transaction_id bigint;
            baseline_transaction_id bigint;
            initialized_event_count bigint;
            released_event_count bigint;
            current_managed boolean;
        BEGIN
            IF NEW.generation_lineage_id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT * INTO linked_outcome
              FROM generation_apply_outcomes
             WHERE id = NEW.generation_created_outcome_id;
            SELECT xmin::text::bigint INTO outcome_transaction_id
              FROM generation_apply_outcomes
             WHERE id = NEW.generation_created_outcome_id;
            IF linked_outcome.id IS NULL OR linked_outcome.outcome <> 'CREATED' OR
               linked_outcome.test_case_id <> NEW.id OR
               linked_outcome.test_case_version_id <>
                   NEW.generation_baseline_version_id THEN
                RAISE EXCEPTION 'generation management CREATED outcome link is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF (TG_OP = 'INSERT' OR OLD.generation_lineage_id IS NULL) AND
               current_setting('tc.generation_management_operation', true) = 'initialize' AND
               outcome_transaction_id IS DISTINCT FROM
                   pg_current_xact_id()::text::bigint THEN
                RAISE EXCEPTION
                    'generation management initialization must share the CREATED transaction'
                    USING ERRCODE = '55000';
            END IF;
            SELECT * INTO linked_apply
              FROM generation_applies
             WHERE id = linked_outcome.generation_apply_id;
            SELECT * INTO linked_job
              FROM generation_jobs
             WHERE id = linked_apply.generation_job_id;
            SELECT * INTO baseline
              FROM test_case_versions
             WHERE id = NEW.generation_baseline_version_id
               AND test_case_id = NEW.id;
            SELECT xmin::text::bigint INTO baseline_transaction_id
              FROM test_case_versions
             WHERE id = NEW.generation_baseline_version_id
               AND test_case_id = NEW.id;
            IF linked_apply.id IS NULL OR linked_job.id IS NULL OR baseline.id IS NULL OR
               linked_outcome.preserved OR baseline.version_number <> 1 OR
               baseline.source_type <> 'SLACK_AI' OR
               ((TG_OP = 'INSERT' OR OLD.generation_lineage_id IS NULL) AND
                linked_apply.parent_work_item_id <> NEW.work_item_id) OR
               linked_job.status <> 'SUCCEEDED' OR
               linked_job.context_digest IS DISTINCT FROM linked_apply.context_digest OR
               baseline.source_metadata->>'slackWorkItemId' IS DISTINCT FROM
                   'slack_work_item_' ||
                   replace(linked_job.slack_work_item_id::text, '-', '') OR
               baseline.source_metadata->>'generationJobId' IS DISTINCT FROM
                   'generation_job_' || replace(linked_job.id::text, '-', '') OR
               baseline.source_metadata->>'contextDigest' IS DISTINCT FROM
                   linked_apply.context_digest OR
               baseline.source_metadata->>'resultDigest' IS DISTINCT FROM
                   linked_apply.result_digest OR
               baseline.source_metadata->>'outputSchemaVersion' IS DISTINCT FROM
                   linked_job.schema_version OR
               baseline.source_metadata->>'generationVersion' IS DISTINCT FROM
                   linked_job.generation_version::text OR
               linked_outcome.snapshot #> '{case,currentVersion,source}' IS DISTINCT FROM
                   baseline.source_metadata OR
               baseline.source_metadata->'evidenceNodeIds' IS DISTINCT FROM
                   linked_job.result->'cases'->linked_outcome.generated_case_index->
                       'evidenceNodeIds' OR
               jsonb_typeof(baseline.source_metadata->'evidenceNodeIds')
                   IS DISTINCT FROM 'array' OR
               jsonb_array_length(baseline.source_metadata->'evidenceNodeIds') = 0 OR
               jsonb_typeof(baseline.source_metadata->'sourceRevisionIds')
                   IS DISTINCT FROM 'array' OR
               jsonb_array_length(baseline.source_metadata->'sourceRevisionIds') = 0 OR
               EXISTS (
                   SELECT 1
                     FROM jsonb_array_elements_text(
                         baseline.source_metadata->'evidenceNodeIds'
                     ) AS evidence(value)
                    WHERE NOT EXISTS (
                        SELECT 1
                          FROM generation_contexts AS context_row
                         WHERE context_row.generation_job_id = linked_job.id
                           AND context_row.node_id IS NOT NULL
                           AND 'knowledge_node_' || replace(
                               context_row.node_id::text, '-', ''
                           ) = evidence.value
                    )
               ) OR
               EXISTS (
                   SELECT 1
                     FROM jsonb_array_elements_text(
                         baseline.source_metadata->'sourceRevisionIds'
                     ) AS source_revision(value)
                    WHERE NOT EXISTS (
                        SELECT 1
                          FROM generation_contexts AS context_row
                         WHERE context_row.generation_job_id = linked_job.id
                           AND context_row.node_id IS NOT NULL
                           AND baseline.source_metadata->'evidenceNodeIds' ?
                               ('knowledge_node_' || replace(
                                   context_row.node_id::text, '-', ''
                               ))
                           AND lower(context_row.source_entity_type) || '_' ||
                               replace(context_row.source_entity_id::text, '-', '') =
                               source_revision.value
                    )
               ) OR
               EXISTS (
                   SELECT 1
                     FROM generation_contexts AS context_row
                    WHERE context_row.generation_job_id = linked_job.id
                      AND context_row.node_id IS NOT NULL
                      AND baseline.source_metadata->'evidenceNodeIds' ?
                          ('knowledge_node_' || replace(
                              context_row.node_id::text, '-', ''
                          ))
                      AND NOT baseline.source_metadata->'sourceRevisionIds' ?
                          (lower(context_row.source_entity_type) || '_' ||
                           replace(context_row.source_entity_id::text, '-', ''))
               ) OR
               jsonb_array_length(
                   baseline.source_metadata->'sourceRevisionIds'
               ) <> (
                   SELECT count(DISTINCT source_revision.value)
                     FROM jsonb_array_elements_text(
                         baseline.source_metadata->'sourceRevisionIds'
                     ) AS source_revision(value)
               ) OR
               (TG_OP = 'INSERT' OR OLD.generation_lineage_id IS NULL) AND
                   (NEW.deleted_at IS NOT NULL OR
                    NEW.current_version_id <> NEW.generation_baseline_version_id OR
                    NEW.generation_latest_version_id <>
                        NEW.generation_baseline_version_id) THEN
                RAISE EXCEPTION 'generation management provenance link is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF (TG_OP = 'INSERT' OR OLD.generation_lineage_id IS NULL) AND
               current_setting('tc.generation_management_operation', true) = 'initialize' AND
               baseline_transaction_id IS DISTINCT FROM
                   pg_current_xact_id()::text::bigint THEN
                RAISE EXCEPTION
                    'generation management baseline must be created in the CREATED transaction'
                    USING ERRCODE = '55000';
            END IF;

            SELECT
                count(*) FILTER (WHERE event_type = 'INITIALIZED'),
                count(*) FILTER (WHERE event_type = 'RELEASED')
              INTO initialized_event_count, released_event_count
              FROM test_case_generation_management_events
             WHERE test_case_id = NEW.id;
            SELECT generation_managed INTO current_managed
              FROM test_cases
             WHERE id = NEW.id;
            IF initialized_event_count <> 1 OR
               (current_managed AND released_event_count <> 0) OR
               (NOT current_managed AND released_event_count <> 1) THEN
                RAISE EXCEPTION
                    'generation management event ledger is incomplete for case %', NEW.id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_test_case_generation_management_events_links()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            linked_case test_cases%ROWTYPE;
        BEGIN
            SELECT * INTO linked_case
              FROM test_cases
             WHERE id = NEW.test_case_id;
            IF linked_case.id IS NULL OR
               NEW.workspace_id <> linked_case.workspace_id OR
               NEW.lineage_id <> linked_case.generation_lineage_id OR
               NEW.created_outcome_id <> linked_case.generation_created_outcome_id OR
               NEW.baseline_generated_version_id <>
                   linked_case.generation_baseline_version_id THEN
                RAISE EXCEPTION 'generation management event lineage is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.event_type = 'INITIALIZED' THEN
                IF NEW.latest_generated_version_id <>
                       NEW.baseline_generated_version_id THEN
                    RAISE EXCEPTION 'generation management initialization event is invalid'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF linked_case.generation_managed OR
               NEW.latest_generated_version_id <>
                   linked_case.generation_latest_version_id OR
               NEW.released_at IS DISTINCT FROM linked_case.generation_released_at OR
               NEW.release_reason IS DISTINCT FROM
                   linked_case.generation_release_reason OR
               NEW.release_actor IS DISTINCT FROM linked_case.generation_release_actor OR
               NEW.release_mutation IS DISTINCT FROM
                   linked_case.generation_release_mutation OR
               NEW.released_from_version_id IS DISTINCT FROM
                   linked_case.generation_released_from_version_id OR
               NEW.released_by_version_id IS DISTINCT FROM
                   linked_case.generation_released_by_version_id OR
               NEW.occurred_at IS DISTINCT FROM NEW.released_at THEN
                RAISE EXCEPTION 'generation management release event is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_test_case_generation_management_events_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'generation management events are append-only'
                USING ERRCODE = '55000';
        END;
        $$;

        CREATE FUNCTION tc_api_idempotency_receipts_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'API idempotency receipts are append-only'
                USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER trg_test_cases_generation_management_shape
            BEFORE INSERT OR UPDATE ON test_cases
            FOR EACH ROW EXECUTE FUNCTION tc_test_cases_generation_management_shape();
        CREATE TRIGGER trg_test_cases_generation_management_one_way
            BEFORE UPDATE ON test_cases
            FOR EACH ROW EXECUTE FUNCTION tc_test_cases_generation_management_one_way();
        CREATE CONSTRAINT TRIGGER trg_test_cases_generation_management_links
            AFTER INSERT OR UPDATE ON test_cases
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_test_cases_generation_management_links();
        CREATE CONSTRAINT TRIGGER trg_test_case_generation_management_events_links
            AFTER INSERT ON test_case_generation_management_events
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION tc_test_case_generation_management_events_links();
        CREATE TRIGGER trg_test_case_generation_management_events_immutable
            BEFORE UPDATE OR DELETE ON test_case_generation_management_events
            FOR EACH ROW EXECUTE FUNCTION tc_test_case_generation_management_events_immutable();
        CREATE TRIGGER trg_api_idempotency_receipts_immutable
            BEFORE UPDATE OR DELETE ON api_idempotency_receipts
            FOR EACH ROW EXECUTE FUNCTION tc_api_idempotency_receipts_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS api_idempotency_receipts;
        DROP TABLE test_case_generation_management_events;
        DROP TRIGGER trg_test_cases_generation_management_links ON test_cases;
        DROP TRIGGER trg_test_cases_generation_management_one_way ON test_cases;
        DROP TRIGGER trg_test_cases_generation_management_shape ON test_cases;
        DROP FUNCTION tc_test_case_generation_management_events_immutable();
        DROP FUNCTION IF EXISTS tc_api_idempotency_receipts_immutable();
        DROP FUNCTION tc_test_case_generation_management_events_links();
        DROP FUNCTION tc_test_cases_generation_management_links();
        DROP FUNCTION tc_test_cases_generation_management_one_way();
        DROP FUNCTION tc_test_cases_generation_management_shape();
        ALTER TABLE test_cases
            DROP COLUMN generation_released_by_version_id,
            DROP COLUMN generation_released_from_version_id,
            DROP COLUMN generation_release_mutation,
            DROP COLUMN generation_release_actor,
            DROP COLUMN generation_release_reason,
            DROP COLUMN generation_released_at,
            DROP COLUMN generation_latest_version_id,
            DROP COLUMN generation_baseline_version_id,
            DROP COLUMN generation_created_outcome_id,
            DROP COLUMN generation_lineage_id,
            DROP COLUMN generation_managed;
        """
    )
