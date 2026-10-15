"""Immutable provider-neutral Generation Input capture.

Revision ID: 20260827_0020
Revises: 20260826_0019
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_0020"
down_revision: str | None = "20260826_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE generation_jobs
            ADD COLUMN generation_input_capture_state varchar(30)
                DEFAULT 'LEGACY_NOT_CAPTURED' NOT NULL,
            ADD COLUMN generation_input_schema_version varchar(100),
            ADD COLUMN generation_input jsonb,
            ADD COLUMN generation_input_digest varchar(64),
            ADD CONSTRAINT ck_generation_jobs_generation_input_capture_complete CHECK (
                (generation_input_capture_state = 'LEGACY_NOT_CAPTURED'
                    AND generation_input_schema_version IS NULL
                    AND generation_input IS NULL
                    AND generation_input_digest IS NULL)
                OR
                (generation_input_capture_state = 'SEALED'
                    AND char_length(btrim(generation_input_schema_version)) > 0
                    AND jsonb_typeof(generation_input) = 'object'
                    AND generation_input_digest ~ '^[0-9a-f]{64}$')
            );
        """
    )
    _install_input_aware_job_guard()


def downgrade() -> None:
    _restore_0018_job_guard()
    op.execute(
        """
        ALTER TABLE generation_jobs
            DROP CONSTRAINT ck_generation_jobs_generation_input_capture_complete,
            DROP COLUMN generation_input_digest,
            DROP COLUMN generation_input,
            DROP COLUMN generation_input_schema_version,
            DROP COLUMN generation_input_capture_state;
        """
    )


def _install_input_aware_job_guard() -> None:
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


def _restore_0018_job_guard() -> None:
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
                NEW.attempt_number
            ) IS DISTINCT FROM ROW(
                OLD.slack_work_item_id, OLD.source_version, OLD.generation_version,
                OLD.provider, OLD.model, OLD.prompt_version, OLD.schema_version,
                OLD.ontology_schema_id, OLD.context_item_count, OLD.context_digest,
                OLD.context_sealed_at, OLD.root_job_id, OLD.retry_of_job_id,
                OLD.attempt_number
            ) THEN
                RAISE EXCEPTION 'sealed generation context provenance is immutable'
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
