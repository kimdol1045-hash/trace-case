"""Grounded generation jobs and immutable context snapshots.

Revision ID: 20260826_0017
Revises: 20260825_0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260826_0017"
down_revision: str | None = "20260825_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE generation_jobs (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            slack_work_item_id uuid NOT NULL,
            source_version integer NOT NULL,
            generation_version integer NOT NULL,
            status varchar(20) DEFAULT 'QUEUED' NOT NULL,
            provider varchar(100) NOT NULL,
            model varchar(200) NOT NULL,
            prompt_version varchar(100) NOT NULL,
            schema_version varchar(100) NOT NULL,
            ontology_schema_id uuid NOT NULL,
            context_item_count integer DEFAULT 0 NOT NULL,
            context_digest varchar(64),
            context_sealed_at timestamptz,
            result jsonb,
            error text,
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_generation_jobs PRIMARY KEY (id),
            CONSTRAINT fk_generation_jobs_slack_work_item_id_slack_work_items
                FOREIGN KEY (slack_work_item_id) REFERENCES slack_work_items(id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_generation_jobs_ontology_schema_id_ontology_schemas
                FOREIGN KEY (ontology_schema_id) REFERENCES ontology_schemas(id)
                ON DELETE RESTRICT,
            CONSTRAINT uq_generation_jobs_work_item_generation_version
                UNIQUE (slack_work_item_id, generation_version),
            CONSTRAINT ck_generation_jobs_source_version_positive
                CHECK (source_version >= 1),
            CONSTRAINT ck_generation_jobs_generation_version_positive
                CHECK (generation_version >= 1),
            CONSTRAINT ck_generation_jobs_status_allowed
                CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
            CONSTRAINT ck_generation_jobs_provider_nonblank
                CHECK (char_length(btrim(provider)) > 0),
            CONSTRAINT ck_generation_jobs_model_nonblank
                CHECK (char_length(btrim(model)) > 0),
            CONSTRAINT ck_generation_jobs_prompt_version_nonblank
                CHECK (char_length(btrim(prompt_version)) > 0),
            CONSTRAINT ck_generation_jobs_schema_version_nonblank
                CHECK (char_length(btrim(schema_version)) > 0),
            CONSTRAINT ck_generation_jobs_context_count_nonnegative
                CHECK (context_item_count >= 0),
            CONSTRAINT ck_generation_jobs_context_seal_complete
                CHECK (
                    (context_sealed_at IS NULL AND context_item_count = 0
                        AND context_digest IS NULL)
                    OR
                    (context_sealed_at IS NOT NULL AND context_item_count > 0
                        AND context_digest ~ '^[0-9a-f]{64}$')
                ),
            CONSTRAINT ck_generation_jobs_result_object
                CHECK (result IS NULL OR jsonb_typeof(result) = 'object'),
            CONSTRAINT ck_generation_jobs_error_nonblank
                CHECK (error IS NULL OR char_length(btrim(error)) > 0)
        );
        CREATE INDEX ix_generation_jobs_work_item_created
            ON generation_jobs (slack_work_item_id, created_at, id);
        CREATE INDEX ix_generation_jobs_status_created
            ON generation_jobs (status, created_at, id);

        CREATE TABLE generation_contexts (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            generation_job_id uuid NOT NULL,
            knowledge_evidence_id uuid NOT NULL,
            node_id uuid,
            edge_id uuid,
            source_record_id uuid NOT NULL,
            source_kind varchar(30) NOT NULL,
            source_entity_type varchar(64) NOT NULL,
            source_entity_id uuid NOT NULL,
            source_revision varchar(200) NOT NULL,
            source_content_hash varchar(128) NOT NULL,
            source_permalink text,
            rank integer NOT NULL,
            reason varchar(200) NOT NULL,
            node_snapshot jsonb,
            edge_snapshot jsonb,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_generation_contexts PRIMARY KEY (id),
            CONSTRAINT fk_generation_contexts_generation_job_id_generation_jobs
                FOREIGN KEY (generation_job_id) REFERENCES generation_jobs(id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_generation_contexts_knowledge_evidence_id_knowledge_evidence
                FOREIGN KEY (knowledge_evidence_id) REFERENCES knowledge_evidence(id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_generation_contexts_node_id_knowledge_nodes
                FOREIGN KEY (node_id) REFERENCES knowledge_nodes(id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_generation_contexts_edge_id_knowledge_edges
                FOREIGN KEY (edge_id) REFERENCES knowledge_edges(id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_generation_contexts_source_record_id_knowledge_source_records
                FOREIGN KEY (source_record_id) REFERENCES knowledge_source_records(id)
                ON DELETE RESTRICT,
            CONSTRAINT uq_generation_contexts_job_rank UNIQUE (generation_job_id, rank),
            CONSTRAINT uq_generation_contexts_job_evidence
                UNIQUE (generation_job_id, knowledge_evidence_id),
            CONSTRAINT ck_generation_contexts_exactly_one_resource CHECK (
                (node_id IS NOT NULL AND edge_id IS NULL
                    AND node_snapshot IS NOT NULL AND edge_snapshot IS NULL)
                OR
                (node_id IS NULL AND edge_id IS NOT NULL
                    AND node_snapshot IS NULL AND edge_snapshot IS NOT NULL)
            ),
            CONSTRAINT ck_generation_contexts_source_kind_allowed
                CHECK (source_kind IN ('SOURCE_REVISION', 'LEDGER_ENTITY')),
            CONSTRAINT ck_generation_contexts_source_entity_type_format
                CHECK (source_entity_type ~ '^[A-Z][A-Z0-9_]*$'),
            CONSTRAINT ck_generation_contexts_source_revision_nonblank
                CHECK (char_length(btrim(source_revision)) > 0),
            CONSTRAINT ck_generation_contexts_source_hash_nonblank
                CHECK (char_length(btrim(source_content_hash)) > 0),
            CONSTRAINT ck_generation_contexts_rank_positive CHECK (rank >= 1),
            CONSTRAINT ck_generation_contexts_reason_nonblank
                CHECK (char_length(btrim(reason)) > 0),
            CONSTRAINT ck_generation_contexts_node_snapshot_object
                CHECK (node_snapshot IS NULL OR jsonb_typeof(node_snapshot) = 'object'),
            CONSTRAINT ck_generation_contexts_edge_snapshot_object
                CHECK (edge_snapshot IS NULL OR jsonb_typeof(edge_snapshot) = 'object')
        );
        CREATE INDEX ix_generation_contexts_job_rank
            ON generation_contexts (generation_job_id, rank);
        CREATE INDEX ix_generation_contexts_source
            ON generation_contexts
                (source_entity_type, source_entity_id, source_revision);
        """
    )
    _create_context_grounding_guard()
    _create_context_immutability_guards()


def _create_context_grounding_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_validate_generation_context_grounding()
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

        CREATE TRIGGER trg_generation_contexts_validate_grounding
        BEFORE INSERT ON generation_contexts
        FOR EACH ROW EXECUTE FUNCTION tc_validate_generation_context_grounding();
        """
    )


def _create_context_immutability_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_reject_generation_context_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'generation_contexts are append-only'
                USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER trg_generation_contexts_append_only
        BEFORE UPDATE OR DELETE ON generation_contexts
        FOR EACH ROW EXECUTE FUNCTION tc_reject_generation_context_mutation();

        CREATE FUNCTION tc_guard_generation_job_context_seal()
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

        CREATE TRIGGER trg_generation_jobs_guard_context_seal
        BEFORE UPDATE ON generation_jobs
        FOR EACH ROW EXECUTE FUNCTION tc_guard_generation_job_context_seal();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER trg_generation_jobs_guard_context_seal ON generation_jobs;
        DROP FUNCTION tc_guard_generation_job_context_seal();
        DROP TRIGGER trg_generation_contexts_append_only ON generation_contexts;
        DROP FUNCTION tc_reject_generation_context_mutation();
        DROP TRIGGER trg_generation_contexts_validate_grounding ON generation_contexts;
        DROP FUNCTION tc_validate_generation_context_grounding();
        DROP TABLE generation_contexts;
        DROP INDEX ix_generation_jobs_status_created;
        DROP INDEX ix_generation_jobs_work_item_created;
        DROP TABLE generation_jobs;
        """
    )
