"""Grounded knowledge projection, replay checkpoint, and bge-m3 search.

Revision ID: 20260821_0008
Revises: 20260821_0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_0008"
down_revision: str | None = "20260821_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge_source_records (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            source_kind varchar(30) NOT NULL,
            entity_type varchar(64) NOT NULL,
            entity_id uuid NOT NULL,
            revision_key varchar(200) NOT NULL,
            content_hash varchar(128) NOT NULL,
            permalink text,
            recorded_at timestamptz NOT NULL,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_knowledge_source_records PRIMARY KEY (id),
            CONSTRAINT uq_knowledge_source_records_identity
                UNIQUE (source_kind, entity_type, entity_id, revision_key),
            CONSTRAINT ck_knowledge_source_records_kind_allowed CHECK
                (source_kind IN ('SOURCE_REVISION', 'LEDGER_ENTITY')),
            CONSTRAINT ck_knowledge_source_records_entity_type_format CHECK
                (entity_type ~ '^[A-Z][A-Z0-9_]*$'),
            CONSTRAINT ck_knowledge_source_records_revision_nonblank CHECK
                (char_length(btrim(revision_key)) > 0),
            CONSTRAINT ck_knowledge_source_records_hash_nonblank CHECK
                (char_length(btrim(content_hash)) > 0)
        );
        CREATE INDEX ix_knowledge_source_records_entity
            ON knowledge_source_records (entity_type, entity_id, recorded_at);

        ALTER TABLE knowledge_evidence ADD COLUMN source_record_id uuid;

        INSERT INTO knowledge_source_records
            (source_kind, entity_type, entity_id, revision_key, content_hash,
             permalink, recorded_at)
        SELECT DISTINCT ON (
                source_type, source_id, COALESCE(NULLIF(source_revision, ''), 'legacy')
            )
            'SOURCE_REVISION', source_type, source_id,
            COALESCE(NULLIF(source_revision, ''), 'legacy'), excerpt_hash,
            permalink, recorded_at
        FROM knowledge_evidence
        ORDER BY source_type, source_id,
            COALESCE(NULLIF(source_revision, ''), 'legacy'), recorded_at, id;

        UPDATE knowledge_evidence AS evidence
        SET source_record_id = source.id
        FROM knowledge_source_records AS source
        WHERE source.source_kind = 'SOURCE_REVISION'
          AND source.entity_type = evidence.source_type
          AND source.entity_id = evidence.source_id
          AND source.revision_key =
              COALESCE(NULLIF(evidence.source_revision, ''), 'legacy');

        ALTER TABLE knowledge_evidence ALTER COLUMN source_record_id SET NOT NULL;
        ALTER TABLE knowledge_evidence
            ADD CONSTRAINT fk_knowledge_evidence_source_record_id_knowledge_source_records
            FOREIGN KEY (source_record_id) REFERENCES knowledge_source_records(id)
            ON DELETE RESTRICT;
        CREATE INDEX ix_knowledge_evidence_source_record
            ON knowledge_evidence (source_record_id);
        CREATE UNIQUE INDEX uq_knowledge_evidence_source_node
            ON knowledge_evidence (source_record_id, knowledge_node_id)
            WHERE knowledge_node_id IS NOT NULL;
        CREATE UNIQUE INDEX uq_knowledge_evidence_source_edge
            ON knowledge_evidence (source_record_id, knowledge_edge_id)
            WHERE knowledge_edge_id IS NOT NULL;

        CREATE UNIQUE INDEX uq_knowledge_nodes_schema_entity
            ON knowledge_nodes (ontology_schema_id, entity_type, entity_id)
            WHERE entity_type IS NOT NULL AND entity_id IS NOT NULL;
        CREATE UNIQUE INDEX uq_knowledge_edges_schema_relation
            ON knowledge_edges
                (ontology_schema_id, source_node_id, predicate, target_node_id);

        CREATE TABLE knowledge_projection_events (
            outbox_event_id uuid NOT NULL,
            aggregate_type varchar(64) NOT NULL,
            aggregate_id uuid NOT NULL,
            aggregate_version integer NOT NULL,
            result jsonb DEFAULT '{}'::jsonb NOT NULL,
            processed_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT pk_knowledge_projection_events PRIMARY KEY (outbox_event_id),
            CONSTRAINT fk_knowledge_projection_events_outbox_event_id_outbox_events
                FOREIGN KEY (outbox_event_id) REFERENCES outbox_events(id)
                ON DELETE RESTRICT,
            CONSTRAINT uq_knowledge_projection_events_aggregate_version
                UNIQUE (aggregate_type, aggregate_id, aggregate_version),
            CONSTRAINT ck_knowledge_projection_events_aggregate_type_format CHECK
                (aggregate_type ~ '^[A-Z][A-Z0-9_]*$'),
            CONSTRAINT ck_knowledge_projection_events_version_positive CHECK
                (aggregate_version >= 1),
            CONSTRAINT ck_knowledge_projection_events_result_object CHECK
                (jsonb_typeof(result) = 'object')
        );
        CREATE INDEX ix_knowledge_projection_events_aggregate
            ON knowledge_projection_events
                (aggregate_type, aggregate_id, aggregate_version);

        ALTER TABLE knowledge_embeddings
            ADD COLUMN model varchar(100) DEFAULT 'bge-m3' NOT NULL;
        ALTER TABLE knowledge_embeddings
            ADD CONSTRAINT ck_knowledge_embeddings_model_bge_m3
            CHECK (model = 'bge-m3');
        ALTER TABLE knowledge_embeddings
            DROP CONSTRAINT uq_knowledge_embeddings_owner_model_content;
        ALTER TABLE knowledge_embeddings
            ADD CONSTRAINT uq_knowledge_embeddings_owner_model_version_content
            UNIQUE (owner_type, owner_id, model, model_version, content_hash);
        CREATE INDEX ix_knowledge_embeddings_bge_m3_cosine
            ON knowledge_embeddings USING hnsw (embedding vector_cosine_ops);
        """
    )
    _create_relation_validator()
    _create_grounding_guards()


def _create_relation_validator() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_validate_knowledge_edge_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            source_type text;
            target_type text;
            source_schema uuid;
            target_schema uuid;
            allowed_pair boolean;
        BEGIN
            SELECT node_type, ontology_schema_id
              INTO source_type, source_schema
              FROM knowledge_nodes WHERE id = NEW.source_node_id;
            SELECT node_type, ontology_schema_id
              INTO target_type, target_schema
              FROM knowledge_nodes WHERE id = NEW.target_node_id;

            IF source_schema IS NULL OR target_schema IS NULL
               OR source_schema <> NEW.ontology_schema_id
               OR target_schema <> NEW.ontology_schema_id THEN
                RAISE EXCEPTION
                    'knowledge edge nodes must exist in the edge ontology schema'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_knowledge_edges_schema_matches_nodes';
            END IF;

            allowed_pair := CASE NEW.predicate
                WHEN 'AFFECTS' THEN
                    source_type = ANY (ARRAY['WORK_ITEM','REQUIREMENT','DECISION','ISSUE','RELEASE'])
                    AND target_type = ANY (ARRAY['MENU','FEATURE','REQUIREMENT','TEST_CASE','RELEASE'])
                WHEN 'TARGETS' THEN
                    source_type = ANY (ARRAY['WORK_ITEM','REQUIREMENT','RELEASE'])
                    AND target_type = ANY (ARRAY['MENU','FEATURE','REQUIREMENT'])
                WHEN 'DISCUSSES' THEN
                    source_type = 'WORK_ITEM'
                    AND target_type = ANY (ARRAY['FEATURE','REQUIREMENT','DECISION','CONSTRAINT','OPEN_QUESTION','RISK','ISSUE'])
                WHEN 'DERIVED_FROM' THEN
                    source_type = ANY (ARRAY['REQUIREMENT','DECISION','CONSTRAINT','OPEN_QUESTION','RISK','TEST_CASE','ISSUE'])
                    AND target_type = ANY (ARRAY['WORK_ITEM','REQUIREMENT','DECISION','TEST_CASE','EXECUTION','ISSUE'])
                WHEN 'REFINES' THEN
                    source_type = ANY (ARRAY['REQUIREMENT','DECISION','CONSTRAINT'])
                    AND target_type = ANY (ARRAY['REQUIREMENT','DECISION','CONSTRAINT'])
                WHEN 'SUPERSEDES' THEN
                    source_type = ANY (ARRAY['REQUIREMENT','DECISION','CONSTRAINT'])
                    AND target_type = ANY (ARRAY['REQUIREMENT','DECISION','CONSTRAINT'])
                WHEN 'APPLIES_TO' THEN
                    source_type = ANY (ARRAY['REQUIREMENT','DECISION','CONSTRAINT','RISK'])
                    AND target_type = ANY (ARRAY['MENU','FEATURE','RELEASE'])
                WHEN 'VERIFIES' THEN
                    source_type = ANY (ARRAY['TEST_CASE','EXECUTION'])
                    AND target_type = ANY (ARRAY['FEATURE','REQUIREMENT','DECISION','CONSTRAINT','TEST_CASE'])
                WHEN 'COVERS' THEN
                    source_type = ANY (ARRAY['TEST_CASE','RELEASE'])
                    AND target_type = ANY (ARRAY['FEATURE','REQUIREMENT','RISK','TEST_CASE'])
                WHEN 'INSTANCE_OF' THEN
                    source_type = ANY (ARRAY['EXECUTION','FEATURE'])
                    AND target_type = ANY (ARRAY['TEST_CASE','MENU'])
                WHEN 'FOUND_BY' THEN
                    source_type = ANY (ARRAY['ISSUE','RISK'])
                    AND target_type = ANY (ARRAY['EXECUTION','TEST_CASE'])
                ELSE false
            END;

            IF NOT allowed_pair THEN
                RAISE EXCEPTION 'relation % does not allow % -> %',
                    NEW.predicate, source_type, target_type
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_knowledge_edges_v1_relation_pair';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_knowledge_edges_validate_v1
        BEFORE INSERT OR UPDATE OF ontology_schema_id, source_node_id, predicate, target_node_id
        ON knowledge_edges
        FOR EACH ROW EXECUTE FUNCTION tc_validate_knowledge_edge_v1();
        """
    )


def _create_grounding_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_require_knowledge_node_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM knowledge_nodes WHERE id = NEW.id)
               AND NOT EXISTS (
                    SELECT 1 FROM knowledge_evidence WHERE knowledge_node_id = NEW.id
               ) THEN
                RAISE EXCEPTION 'knowledge node % requires evidence', NEW.id
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_knowledge_nodes_evidence_required';
            END IF;
            RETURN NULL;
        END;
        $$;

        CREATE FUNCTION tc_require_knowledge_edge_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM knowledge_edges WHERE id = NEW.id)
               AND NOT EXISTS (
                    SELECT 1 FROM knowledge_evidence WHERE knowledge_edge_id = NEW.id
               ) THEN
                RAISE EXCEPTION 'knowledge edge % requires evidence', NEW.id
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_knowledge_edges_evidence_required';
            END IF;
            RETURN NULL;
        END;
        $$;

        CREATE FUNCTION tc_guard_knowledge_evidence_removal()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.knowledge_node_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM knowledge_nodes WHERE id = OLD.knowledge_node_id)
               AND NOT EXISTS (
                    SELECT 1 FROM knowledge_evidence
                    WHERE knowledge_node_id = OLD.knowledge_node_id
               ) THEN
                RAISE EXCEPTION 'knowledge node % requires evidence', OLD.knowledge_node_id
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_knowledge_nodes_evidence_required';
            END IF;
            IF OLD.knowledge_edge_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM knowledge_edges WHERE id = OLD.knowledge_edge_id)
               AND NOT EXISTS (
                    SELECT 1 FROM knowledge_evidence
                    WHERE knowledge_edge_id = OLD.knowledge_edge_id
               ) THEN
                RAISE EXCEPTION 'knowledge edge % requires evidence', OLD.knowledge_edge_id
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_knowledge_edges_evidence_required';
            END IF;
            RETURN NULL;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER trg_knowledge_nodes_require_evidence
        AFTER INSERT OR UPDATE ON knowledge_nodes
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_require_knowledge_node_evidence();
        CREATE CONSTRAINT TRIGGER trg_knowledge_edges_require_evidence
        AFTER INSERT OR UPDATE ON knowledge_edges
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_require_knowledge_edge_evidence();
        CREATE CONSTRAINT TRIGGER trg_knowledge_evidence_guard_removal
        AFTER DELETE OR UPDATE ON knowledge_evidence
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION tc_guard_knowledge_evidence_removal();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER trg_knowledge_evidence_guard_removal ON knowledge_evidence;
        DROP TRIGGER trg_knowledge_edges_require_evidence ON knowledge_edges;
        DROP TRIGGER trg_knowledge_nodes_require_evidence ON knowledge_nodes;
        DROP FUNCTION tc_guard_knowledge_evidence_removal();
        DROP FUNCTION tc_require_knowledge_edge_evidence();
        DROP FUNCTION tc_require_knowledge_node_evidence();
        DROP TRIGGER trg_knowledge_edges_validate_v1 ON knowledge_edges;
        DROP FUNCTION tc_validate_knowledge_edge_v1();

        DROP INDEX ix_knowledge_embeddings_bge_m3_cosine;
        ALTER TABLE knowledge_embeddings
            DROP CONSTRAINT uq_knowledge_embeddings_owner_model_version_content;
        ALTER TABLE knowledge_embeddings
            ADD CONSTRAINT uq_knowledge_embeddings_owner_model_content
            UNIQUE (owner_type, owner_id, model_version, content_hash);
        ALTER TABLE knowledge_embeddings
            DROP CONSTRAINT ck_knowledge_embeddings_model_bge_m3;
        ALTER TABLE knowledge_embeddings DROP COLUMN model;

        DROP INDEX uq_knowledge_edges_schema_relation;
        DROP INDEX uq_knowledge_nodes_schema_entity;
        DROP INDEX uq_knowledge_evidence_source_edge;
        DROP INDEX uq_knowledge_evidence_source_node;
        DROP INDEX ix_knowledge_evidence_source_record;
        ALTER TABLE knowledge_evidence
            DROP CONSTRAINT fk_knowledge_evidence_source_record_id_knowledge_source_records;
        ALTER TABLE knowledge_evidence DROP COLUMN source_record_id;
        DROP TABLE knowledge_projection_events;
        DROP INDEX ix_knowledge_source_records_entity;
        DROP TABLE knowledge_source_records;
        """
    )
