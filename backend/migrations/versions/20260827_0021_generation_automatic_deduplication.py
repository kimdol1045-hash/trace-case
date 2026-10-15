"""Canonical automatic-generation roots and trigger provenance.

Revision ID: 20260827_0021
Revises: 20260827_0020
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_0021"
down_revision: str | None = "20260827_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE generation_jobs
            ADD COLUMN generation_trigger varchar(20);

        UPDATE generation_jobs AS job
           SET generation_trigger = 'AUTOMATIC'
         WHERE job.retry_of_job_id IS NULL
           AND (
                EXISTS (
                    SELECT 1
                      FROM knowledge_projection_events AS projection
                     WHERE projection.result ->> 'generationJobId' = job.id::text
                )
                OR EXISTS (
                    SELECT 1
                      FROM audit_logs AS audit
                     WHERE audit.entity_type = 'GENERATION_JOB'
                       AND audit.entity_id = job.id
                       AND audit.action = 'AUTOMATIC_GENERATION_JOB_CREATED'
                )
           );

        UPDATE generation_jobs AS job
           SET generation_trigger = 'AUTOMATIC'
          FROM generation_jobs AS root
         WHERE job.root_job_id = root.id
           AND root.retry_of_job_id IS NULL
           AND root.generation_trigger = 'AUTOMATIC'
           AND job.generation_trigger IS NULL;

        UPDATE generation_jobs
           SET generation_trigger = 'MANUAL'
         WHERE generation_trigger IS NULL;

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM generation_jobs
                 WHERE generation_trigger = 'AUTOMATIC'
                   AND retry_of_job_id IS NULL
                 GROUP BY slack_work_item_id, source_version
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'multiple automatic generation roots exist for one Slack source snapshot'
                    USING ERRCODE = '23505',
                          CONSTRAINT = 'uq_generation_jobs_automatic_source_version';
            END IF;
        END;
        $$;

        ALTER TABLE generation_jobs
            ALTER COLUMN generation_trigger SET DEFAULT 'MANUAL',
            ALTER COLUMN generation_trigger SET NOT NULL,
            ADD CONSTRAINT ck_generation_jobs_generation_trigger_allowed
                CHECK (generation_trigger IN ('MANUAL', 'AUTOMATIC'));

        CREATE UNIQUE INDEX uq_generation_jobs_automatic_source_version
            ON generation_jobs (slack_work_item_id, source_version)
            WHERE generation_trigger = 'AUTOMATIC' AND retry_of_job_id IS NULL;
        """
    )
    _install_trigger_aware_job_guard()


def downgrade() -> None:
    _restore_0020_job_guard()
    op.execute(
        """
        DROP INDEX uq_generation_jobs_automatic_source_version;
        ALTER TABLE generation_jobs
            DROP CONSTRAINT ck_generation_jobs_generation_trigger_allowed,
            DROP COLUMN generation_trigger;
        """
    )


def _install_trigger_aware_job_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tc_guard_generation_job_context_seal()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            actual_count integer;
        BEGIN
            IF OLD.context_sealed_at IS NOT NULL AND ROW(
                NEW.slack_work_item_id, NEW.source_version, NEW.generation_version,
                NEW.provider, NEW.model, NEW.prompt_version, NEW.schema_version,
                NEW.ontology_schema_id, NEW.context_item_count, NEW.context_digest,
                NEW.context_sealed_at, NEW.root_job_id, NEW.retry_of_job_id,
                NEW.attempt_number, NEW.generation_input_capture_state,
                NEW.generation_input_schema_version, NEW.generation_input,
                NEW.generation_input_digest, NEW.generation_trigger
            ) IS DISTINCT FROM ROW(
                OLD.slack_work_item_id, OLD.source_version, OLD.generation_version,
                OLD.provider, OLD.model, OLD.prompt_version, OLD.schema_version,
                OLD.ontology_schema_id, OLD.context_item_count, OLD.context_digest,
                OLD.context_sealed_at, OLD.root_job_id, OLD.retry_of_job_id,
                OLD.attempt_number, OLD.generation_input_capture_state,
                OLD.generation_input_schema_version, OLD.generation_input,
                OLD.generation_input_digest, OLD.generation_trigger
            ) THEN
                RAISE EXCEPTION 'sealed generation context and input provenance is immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.context_sealed_at IS NULL AND NEW.context_sealed_at IS NOT NULL THEN
                SELECT count(*) INTO actual_count FROM generation_contexts
                 WHERE generation_job_id = NEW.id;
                IF actual_count = 0 OR actual_count <> NEW.context_item_count THEN
                    RAISE EXCEPTION 'generation context count does not match its seal'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_generation_jobs_context_count_matches';
                END IF;
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status = 'QUEUED' AND NEW.status = 'RUNNING') OR
                (OLD.status = 'RUNNING' AND NEW.status IN ('SUCCEEDED', 'FAILED'))
            ) THEN
                RAISE EXCEPTION 'illegal generation job status transition from % to %',
                                OLD.status, NEW.status
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_generation_jobs_legal_transition';
            END IF;
            IF NEW.status IS NOT DISTINCT FROM OLD.status AND ROW(
                NEW.result, NEW.error, NEW.error_code, NEW.error_retryable,
                NEW.error_attempts, NEW.started_at, NEW.completed_at
            ) IS DISTINCT FROM ROW(
                OLD.result, OLD.error, OLD.error_code, OLD.error_retryable,
                OLD.error_attempts, OLD.started_at, OLD.completed_at
            ) THEN
                RAISE EXCEPTION 'generation job lifecycle metadata is immutable in-place'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


def _restore_0020_job_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tc_guard_generation_job_context_seal()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            actual_count integer;
        BEGIN
            IF OLD.context_sealed_at IS NOT NULL AND ROW(
                NEW.slack_work_item_id, NEW.source_version, NEW.generation_version,
                NEW.provider, NEW.model, NEW.prompt_version, NEW.schema_version,
                NEW.ontology_schema_id, NEW.context_item_count, NEW.context_digest,
                NEW.context_sealed_at, NEW.root_job_id, NEW.retry_of_job_id,
                NEW.attempt_number, NEW.generation_input_capture_state,
                NEW.generation_input_schema_version, NEW.generation_input,
                NEW.generation_input_digest
            ) IS DISTINCT FROM ROW(
                OLD.slack_work_item_id, OLD.source_version, OLD.generation_version,
                OLD.provider, OLD.model, OLD.prompt_version, OLD.schema_version,
                OLD.ontology_schema_id, OLD.context_item_count, OLD.context_digest,
                OLD.context_sealed_at, OLD.root_job_id, OLD.retry_of_job_id,
                OLD.attempt_number, OLD.generation_input_capture_state,
                OLD.generation_input_schema_version, OLD.generation_input,
                OLD.generation_input_digest
            ) THEN
                RAISE EXCEPTION 'sealed generation context and input provenance is immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.context_sealed_at IS NULL AND NEW.context_sealed_at IS NOT NULL THEN
                SELECT count(*) INTO actual_count FROM generation_contexts
                 WHERE generation_job_id = NEW.id;
                IF actual_count = 0 OR actual_count <> NEW.context_item_count THEN
                    RAISE EXCEPTION 'generation context count does not match its seal'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_generation_jobs_context_count_matches';
                END IF;
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status = 'QUEUED' AND NEW.status = 'RUNNING') OR
                (OLD.status = 'RUNNING' AND NEW.status IN ('SUCCEEDED', 'FAILED'))
            ) THEN
                RAISE EXCEPTION 'illegal generation job status transition from % to %',
                                OLD.status, NEW.status
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_generation_jobs_legal_transition';
            END IF;
            IF NEW.status IS NOT DISTINCT FROM OLD.status AND ROW(
                NEW.result, NEW.error, NEW.error_code, NEW.error_retryable,
                NEW.error_attempts, NEW.started_at, NEW.completed_at
            ) IS DISTINCT FROM ROW(
                OLD.result, OLD.error, OLD.error_code, OLD.error_retryable,
                OLD.error_attempts, OLD.started_at, OLD.completed_at
            ) THEN
                RAISE EXCEPTION 'generation job lifecycle metadata is immutable in-place'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
