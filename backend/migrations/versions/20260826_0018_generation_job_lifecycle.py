"""Generation job lifecycle, retry lineage, and redacted failures.

Revision ID: 20260826_0018
Revises: 20260826_0017
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260826_0018"
down_revision: str | None = "20260826_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE generation_jobs
            ADD COLUMN root_job_id uuid,
            ADD COLUMN retry_of_job_id uuid,
            ADD COLUMN attempt_number integer,
            ADD COLUMN error_code varchar(80),
            ADD COLUMN error_retryable boolean,
            ADD COLUMN error_attempts integer,
            ADD COLUMN started_at timestamptz,
            ADD COLUMN completed_at timestamptz;

        UPDATE generation_jobs
           SET root_job_id = id,
               attempt_number = 1,
               started_at = CASE WHEN status <> 'QUEUED'
                                 THEN COALESCE(updated_at, created_at) END,
               completed_at = CASE WHEN status IN ('SUCCEEDED', 'FAILED')
                                   THEN COALESCE(updated_at, created_at) END,
               result = CASE WHEN status = 'SUCCEEDED'
                             THEN COALESCE(result, '{}'::jsonb) ELSE NULL END,
               error = CASE WHEN status = 'FAILED'
                            THEN COALESCE(NULLIF(btrim(error), ''),
                                          'Generation provider failed.') ELSE NULL END,
               error_code = CASE WHEN status = 'FAILED'
                                 THEN 'GENERATION_PROVIDER_ERROR' END,
               error_retryable = CASE WHEN status = 'FAILED' THEN false END,
               error_attempts = CASE WHEN status = 'FAILED' THEN 1 END;

        ALTER TABLE generation_jobs ALTER COLUMN root_job_id SET NOT NULL;
        ALTER TABLE generation_jobs ALTER COLUMN attempt_number SET NOT NULL;
        ALTER TABLE generation_jobs
            ADD CONSTRAINT fk_generation_jobs_root_job_id_generation_jobs
                FOREIGN KEY (root_job_id) REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            ADD CONSTRAINT fk_generation_jobs_retry_of_job_id_generation_jobs
                FOREIGN KEY (retry_of_job_id) REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            ADD CONSTRAINT uq_generation_jobs_retry_of_job_id UNIQUE (retry_of_job_id),
            ADD CONSTRAINT ck_generation_jobs_attempt_number_positive
                CHECK (attempt_number >= 1),
            ADD CONSTRAINT ck_generation_jobs_lineage_shape CHECK (
                (retry_of_job_id IS NULL AND root_job_id = id AND attempt_number = 1)
                OR
                (retry_of_job_id IS NOT NULL AND root_job_id <> id AND attempt_number > 1)
            ),
            ADD CONSTRAINT ck_generation_jobs_error_metadata_complete CHECK (
                (error_code IS NULL AND error_retryable IS NULL AND error_attempts IS NULL)
                OR
                (error_code IS NOT NULL AND error_retryable IS NOT NULL
                    AND error_attempts BETWEEN 1 AND 5)
            ),
            ADD CONSTRAINT ck_generation_jobs_error_code_allowed CHECK (
                error_code IS NULL OR error_code IN (
                    'GENERATION_UNSUPPORTED_SCHEMA_VERSION',
                    'GENERATION_AUTH_REJECTED',
                    'GENERATION_TIMEOUT',
                    'GENERATION_TRANSPORT_UNAVAILABLE',
                    'GENERATION_RATE_LIMITED',
                    'GENERATION_PROVIDER_REJECTED',
                    'GENERATION_PROVIDER_ERROR',
                    'GENERATION_INVALID_CONTENT_TYPE',
                    'GENERATION_RESPONSE_TOO_LARGE',
                    'GENERATION_MALFORMED_JSON',
                    'GENERATION_INVALID_RESPONSE'
                )
            ),
            ADD CONSTRAINT ck_generation_jobs_lifecycle_shape CHECK (
                (status = 'QUEUED' AND started_at IS NULL AND completed_at IS NULL
                    AND result IS NULL AND error IS NULL AND error_code IS NULL)
                OR
                (status = 'RUNNING' AND started_at IS NOT NULL AND completed_at IS NULL
                    AND result IS NULL AND error IS NULL AND error_code IS NULL)
                OR
                (status = 'SUCCEEDED' AND started_at IS NOT NULL AND completed_at IS NOT NULL
                    AND result IS NOT NULL AND error IS NULL AND error_code IS NULL)
                OR
                (status = 'FAILED' AND started_at IS NOT NULL AND completed_at IS NOT NULL
                    AND result IS NULL AND error IS NOT NULL AND error_code IS NOT NULL)
            );
        CREATE INDEX ix_generation_jobs_root_attempt
            ON generation_jobs (root_job_id, attempt_number);
        """
    )
    _replace_context_grounding_guard()
    _replace_job_guard()


def _replace_context_grounding_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tc_validate_generation_context_grounding()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            job_schema_id uuid;
            job_sealed_at timestamptz;
            retry_source_id uuid;
            evidence_node_id uuid;
            evidence_edge_id uuid;
            evidence_source_record_id uuid;
            evidence_source_revision varchar(100);
            evidence_schema_id uuid;
            source_kind_value varchar(30);
            source_entity_type_value varchar(64);
            source_entity_id_value uuid;
            source_revision_value varchar(200);
            source_content_hash_value varchar(128);
            source_permalink_value text;
            resource_status varchar(20);
            resource_valid_from timestamptz;
            resource_valid_to timestamptz;
            resource_schema_id uuid;
        BEGIN
            SELECT ontology_schema_id, context_sealed_at, retry_of_job_id
              INTO job_schema_id, job_sealed_at, retry_source_id
              FROM generation_jobs
             WHERE id = NEW.generation_job_id
             FOR KEY SHARE;
            IF job_schema_id IS NULL THEN
                RAISE EXCEPTION 'generation job % does not exist', NEW.generation_job_id
                    USING ERRCODE = '23503';
            END IF;
            IF job_sealed_at IS NOT NULL THEN
                RAISE EXCEPTION 'generation context is already sealed'
                    USING ERRCODE = '55000';
            END IF;

            SELECT knowledge_node_id, knowledge_edge_id, source_record_id,
                   source_revision, ontology_schema_id
              INTO evidence_node_id, evidence_edge_id, evidence_source_record_id,
                   evidence_source_revision, evidence_schema_id
              FROM knowledge_evidence
             WHERE id = NEW.knowledge_evidence_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'knowledge evidence % does not exist', NEW.knowledge_evidence_id
                    USING ERRCODE = '23503';
            END IF;
            IF evidence_node_id IS DISTINCT FROM NEW.node_id
               OR evidence_edge_id IS DISTINCT FROM NEW.edge_id
               OR evidence_source_record_id IS DISTINCT FROM NEW.source_record_id
               OR evidence_source_revision IS NULL
               OR evidence_source_revision IS DISTINCT FROM NEW.source_revision
               OR evidence_schema_id IS DISTINCT FROM job_schema_id THEN
                RAISE EXCEPTION 'generation context evidence does not match its snapshot'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_generation_contexts_evidence_matches';
            END IF;

            SELECT source_kind, entity_type, entity_id, revision_key, content_hash, permalink
              INTO source_kind_value, source_entity_type_value, source_entity_id_value,
                   source_revision_value, source_content_hash_value, source_permalink_value
              FROM knowledge_source_records
             WHERE id = NEW.source_record_id;
            IF NOT FOUND
               OR source_kind_value IS DISTINCT FROM NEW.source_kind
               OR source_entity_type_value IS DISTINCT FROM NEW.source_entity_type
               OR source_entity_id_value IS DISTINCT FROM NEW.source_entity_id
               OR source_revision_value IS DISTINCT FROM NEW.source_revision
               OR source_content_hash_value IS DISTINCT FROM NEW.source_content_hash
               OR source_permalink_value IS DISTINCT FROM NEW.source_permalink THEN
                RAISE EXCEPTION 'generation context source revision does not match its snapshot'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_generation_contexts_source_matches';
            END IF;

            IF retry_source_id IS NOT NULL THEN
                IF NOT EXISTS (
                    SELECT 1 FROM generation_contexts prior
                     WHERE prior.generation_job_id = retry_source_id
                       AND prior.knowledge_evidence_id = NEW.knowledge_evidence_id
                       AND prior.node_id IS NOT DISTINCT FROM NEW.node_id
                       AND prior.edge_id IS NOT DISTINCT FROM NEW.edge_id
                       AND prior.source_record_id = NEW.source_record_id
                       AND prior.source_kind = NEW.source_kind
                       AND prior.source_entity_type = NEW.source_entity_type
                       AND prior.source_entity_id = NEW.source_entity_id
                       AND prior.source_revision = NEW.source_revision
                       AND prior.source_content_hash = NEW.source_content_hash
                       AND prior.source_permalink IS NOT DISTINCT FROM NEW.source_permalink
                       AND prior.rank = NEW.rank AND prior.reason = NEW.reason
                       AND prior.node_snapshot IS NOT DISTINCT FROM NEW.node_snapshot
                       AND prior.edge_snapshot IS NOT DISTINCT FROM NEW.edge_snapshot
                ) THEN
                    RAISE EXCEPTION 'retry context must exactly reuse its sealed predecessor'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_generation_contexts_retry_exact_copy';
                END IF;
                RETURN NEW;
            END IF;

            IF NEW.node_id IS NOT NULL THEN
                SELECT status, valid_from, valid_to, ontology_schema_id
                  INTO resource_status, resource_valid_from, resource_valid_to, resource_schema_id
                  FROM knowledge_nodes WHERE id = NEW.node_id;
            ELSE
                SELECT status, valid_from, valid_to, ontology_schema_id
                  INTO resource_status, resource_valid_from, resource_valid_to, resource_schema_id
                  FROM knowledge_edges WHERE id = NEW.edge_id;
            END IF;
            IF resource_status IS DISTINCT FROM 'ACTIVE'
               OR resource_schema_id IS DISTINCT FROM job_schema_id
               OR (resource_valid_from IS NOT NULL AND resource_valid_from > CURRENT_TIMESTAMP)
               OR resource_valid_to IS NOT NULL THEN
                RAISE EXCEPTION 'generation context accepts only current ACTIVE knowledge'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_generation_contexts_active_resource';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


def _replace_job_guard() -> None:
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


def downgrade() -> None:
    _restore_0017_guards()
    op.execute(
        """
        DROP INDEX ix_generation_jobs_root_attempt;
        ALTER TABLE generation_jobs
            DROP CONSTRAINT ck_generation_jobs_lifecycle_shape,
            DROP CONSTRAINT ck_generation_jobs_error_code_allowed,
            DROP CONSTRAINT ck_generation_jobs_error_metadata_complete,
            DROP CONSTRAINT ck_generation_jobs_lineage_shape,
            DROP CONSTRAINT ck_generation_jobs_attempt_number_positive,
            DROP CONSTRAINT uq_generation_jobs_retry_of_job_id,
            DROP CONSTRAINT fk_generation_jobs_retry_of_job_id_generation_jobs,
            DROP CONSTRAINT fk_generation_jobs_root_job_id_generation_jobs,
            DROP COLUMN completed_at,
            DROP COLUMN started_at,
            DROP COLUMN error_attempts,
            DROP COLUMN error_retryable,
            DROP COLUMN error_code,
            DROP COLUMN attempt_number,
            DROP COLUMN retry_of_job_id,
            DROP COLUMN root_job_id;
        """
    )


def _restore_0017_guards() -> None:
    """Return both trigger functions to their exact 0017 column contract."""

    op.execute(
        """
        CREATE OR REPLACE FUNCTION tc_validate_generation_context_grounding()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            job_schema_id uuid;
            job_sealed_at timestamptz;
            evidence_node_id uuid;
            evidence_edge_id uuid;
            evidence_source_record_id uuid;
            evidence_source_revision varchar(100);
            evidence_schema_id uuid;
            source_kind_value varchar(30);
            source_entity_type_value varchar(64);
            source_entity_id_value uuid;
            source_revision_value varchar(200);
            source_content_hash_value varchar(128);
            source_permalink_value text;
            resource_status varchar(20);
            resource_valid_from timestamptz;
            resource_valid_to timestamptz;
            resource_schema_id uuid;
        BEGIN
            SELECT ontology_schema_id, context_sealed_at
              INTO job_schema_id, job_sealed_at
              FROM generation_jobs
             WHERE id = NEW.generation_job_id
             FOR KEY SHARE;
            IF job_schema_id IS NULL THEN
                RAISE EXCEPTION 'generation job % does not exist', NEW.generation_job_id
                    USING ERRCODE = '23503';
            END IF;
            IF job_sealed_at IS NOT NULL THEN
                RAISE EXCEPTION 'generation context is already sealed'
                    USING ERRCODE = '55000';
            END IF;

            SELECT knowledge_node_id, knowledge_edge_id, source_record_id,
                   source_revision, ontology_schema_id
              INTO evidence_node_id, evidence_edge_id, evidence_source_record_id,
                   evidence_source_revision, evidence_schema_id
              FROM knowledge_evidence
             WHERE id = NEW.knowledge_evidence_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'knowledge evidence % does not exist', NEW.knowledge_evidence_id
                    USING ERRCODE = '23503';
            END IF;
            IF evidence_node_id IS DISTINCT FROM NEW.node_id
               OR evidence_edge_id IS DISTINCT FROM NEW.edge_id
               OR evidence_source_record_id IS DISTINCT FROM NEW.source_record_id
               OR evidence_source_revision IS NULL
               OR evidence_source_revision IS DISTINCT FROM NEW.source_revision
               OR evidence_schema_id IS DISTINCT FROM job_schema_id THEN
                RAISE EXCEPTION 'generation context evidence does not match its snapshot'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_generation_contexts_evidence_matches';
            END IF;

            SELECT source_kind, entity_type, entity_id, revision_key,
                   content_hash, permalink
              INTO source_kind_value, source_entity_type_value, source_entity_id_value,
                   source_revision_value, source_content_hash_value, source_permalink_value
              FROM knowledge_source_records
             WHERE id = NEW.source_record_id;
            IF NOT FOUND
               OR source_kind_value IS DISTINCT FROM NEW.source_kind
               OR source_entity_type_value IS DISTINCT FROM NEW.source_entity_type
               OR source_entity_id_value IS DISTINCT FROM NEW.source_entity_id
               OR source_revision_value IS DISTINCT FROM NEW.source_revision
               OR source_content_hash_value IS DISTINCT FROM NEW.source_content_hash
               OR source_permalink_value IS DISTINCT FROM NEW.source_permalink THEN
                RAISE EXCEPTION 'generation context source revision does not match its snapshot'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_generation_contexts_source_matches';
            END IF;

            IF NEW.node_id IS NOT NULL THEN
                SELECT status, valid_from, valid_to, ontology_schema_id
                  INTO resource_status, resource_valid_from, resource_valid_to,
                       resource_schema_id
                  FROM knowledge_nodes WHERE id = NEW.node_id;
            ELSE
                SELECT status, valid_from, valid_to, ontology_schema_id
                  INTO resource_status, resource_valid_from, resource_valid_to,
                       resource_schema_id
                  FROM knowledge_edges WHERE id = NEW.edge_id;
            END IF;
            IF resource_status IS DISTINCT FROM 'ACTIVE'
               OR resource_schema_id IS DISTINCT FROM job_schema_id
               OR (resource_valid_from IS NOT NULL AND resource_valid_from > CURRENT_TIMESTAMP)
               OR resource_valid_to IS NOT NULL THEN
                RAISE EXCEPTION 'generation context accepts only current ACTIVE knowledge'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_generation_contexts_active_resource';
            END IF;
            RETURN NEW;
        END;
        $$;

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
                NEW.context_sealed_at
            ) IS DISTINCT FROM ROW(
                OLD.slack_work_item_id, OLD.source_version, OLD.generation_version,
                OLD.provider, OLD.model, OLD.prompt_version, OLD.schema_version,
                OLD.ontology_schema_id, OLD.context_item_count, OLD.context_digest,
                OLD.context_sealed_at
            ) THEN
                RAISE EXCEPTION 'sealed generation context provenance is immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.context_sealed_at IS NULL AND NEW.context_sealed_at IS NOT NULL THEN
                SELECT count(*) INTO actual_count
                  FROM generation_contexts
                 WHERE generation_job_id = NEW.id;
                IF actual_count = 0 OR actual_count <> NEW.context_item_count THEN
                    RAISE EXCEPTION 'generation context count does not match its seal'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_generation_jobs_context_count_matches';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
