"""Durable automatic-generation semantic impact assessments.

Revision ID: 20260827_0022
Revises: 20260827_0021
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_0022"
down_revision: str | None = "20260827_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_generation_impact_changed_fields_valid(fields jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        AS $$
        DECLARE
            field_name text;
            field_position integer;
            previous_position integer := 0;
            seen_fields text[] := ARRAY[]::text[];
            contract_order constant text[] := ARRAY[
                '$INITIAL_SOURCE',
                'slackWorkItem.developmentItem',
                'slackWorkItem.requestedMenu',
                'slackWorkItem.featureDescription',
                'slackWorkItem.requestDetails',
                'slackWorkItem.openQuestions',
                'activeKnowledge.requirements',
                'activeKnowledge.decisions',
                'activeKnowledge.constraints',
                'activeKnowledge.evidence',
                'retrievalInputs.existingCases',
                'retrievalInputs.failedOrBlockedExecutions',
                'retrievalInputs.openIssues',
                'retrievalInputs.extraSources',
                'categoryCandidates',
                'generationPolicy',
                'contractConfig',
                '$REVIEW_REQUIRED'
            ];
        BEGIN
            IF jsonb_typeof(fields) IS DISTINCT FROM 'array' THEN
                RETURN false;
            END IF;
            FOR field_name IN SELECT jsonb_array_elements_text(fields)
            LOOP
                field_position := array_position(contract_order, field_name);
                IF field_position IS NULL OR
                   field_name = ANY(seen_fields) OR
                   field_position <= previous_position THEN
                    RETURN false;
                END IF;
                seen_fields := array_append(seen_fields, field_name);
                previous_position := field_position;
            END LOOP;
            RETURN true;
        END;
        $$;

        CREATE TABLE generation_change_assessments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            slack_work_item_id uuid NOT NULL
                REFERENCES slack_work_items(id) ON DELETE RESTRICT,
            predecessor_assessment_id uuid
                REFERENCES generation_change_assessments(id) ON DELETE RESTRICT,
            baseline_source_version integer,
            current_source_version integer NOT NULL,
            materialization_baseline_generation_job_id uuid
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            selected_generation_job_id uuid
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            reused_generation_job_id uuid
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            outcome varchar(30) NOT NULL,
            reason_code varchar(80) NOT NULL,
            changed_fields jsonb NOT NULL,
            fingerprint_schema_version varchar(100),
            baseline_fingerprint varchar(64),
            current_fingerprint varchar(64),
            contract_config_schema_version varchar(100),
            baseline_contract_config_digest varchar(64),
            current_contract_config_digest varchar(64),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_change_assessments_source_version
                UNIQUE (slack_work_item_id, current_source_version),
            CONSTRAINT ck_generation_change_assessments_outcome_allowed CHECK (
                (outcome = 'INITIAL_GENERATE' AND reason_code = 'FIRST_AUTOMATIC_SOURCE') OR
                (outcome = 'SKIP' AND reason_code = 'NO_GENERATION_RELEVANT_CHANGE') OR
                (outcome = 'REGENERATE' AND reason_code IN (
                    'GENERATION_RELEVANT_FIELDS_CHANGED', 'GENERATION_CONTRACT_CHANGED'
                )) OR
                (outcome = 'REVIEW_REQUIRED' AND reason_code IN (
                    'UNKNOWN_EFFECTIVE_STATE', 'AMBIGUOUS_KNOWLEDGE',
                    'DELETED_OR_RETRACTED_EVIDENCE', 'LEGACY_INPUT_INCOMPATIBLE',
                    'UNSUPPORTED_COMPARISON_SCHEMA', 'INCOMPATIBLE_CONTRACT_CONFIG',
                    'CORRUPT_SEAL', 'IMPACT_COMPARISON_UNSAFE'
                ))
            ),
            CONSTRAINT ck_generation_change_assessments_source_versions CHECK (
                current_source_version >= 1 AND
                (baseline_source_version IS NULL OR
                 baseline_source_version >= 1 AND
                 baseline_source_version < current_source_version)
            ),
            CONSTRAINT ck_generation_change_assessments_fingerprint_shape CHECK (
                (fingerprint_schema_version IS NULL OR
                 char_length(btrim(fingerprint_schema_version)) > 0) AND
                (contract_config_schema_version IS NULL OR
                 char_length(btrim(contract_config_schema_version)) > 0) AND
                (baseline_fingerprint IS NULL OR baseline_fingerprint ~ '^[0-9a-f]{64}$') AND
                (current_fingerprint IS NULL OR current_fingerprint ~ '^[0-9a-f]{64}$') AND
                (baseline_contract_config_digest IS NULL OR
                 baseline_contract_config_digest ~ '^[0-9a-f]{64}$') AND
                (current_contract_config_digest IS NULL OR
                 current_contract_config_digest ~ '^[0-9a-f]{64}$') AND
                (outcome = 'REVIEW_REQUIRED' OR
                 fingerprint_schema_version = 'generation-impact/1.0.0' AND
                 contract_config_schema_version = 'generation-impact-config/1.0.0' AND
                 current_fingerprint IS NOT NULL AND
                 current_contract_config_digest IS NOT NULL) AND
                (outcome NOT IN ('SKIP', 'REGENERATE') OR
                 baseline_fingerprint IS NOT NULL AND
                 baseline_contract_config_digest IS NOT NULL) AND
                (outcome <> 'SKIP' OR
                 baseline_fingerprint = current_fingerprint AND
                 baseline_contract_config_digest = current_contract_config_digest) AND
                (outcome <> 'REGENERATE' OR
                 baseline_fingerprint <> current_fingerprint OR
                 baseline_contract_config_digest <> current_contract_config_digest)
            ),
            CONSTRAINT ck_generation_change_assessments_job_shape CHECK (
                (outcome = 'INITIAL_GENERATE' AND
                 predecessor_assessment_id IS NULL AND baseline_source_version IS NULL AND
                 materialization_baseline_generation_job_id IS NULL AND
                 selected_generation_job_id IS NOT NULL AND reused_generation_job_id IS NULL) OR
                (outcome = 'SKIP' AND predecessor_assessment_id IS NOT NULL AND
                 baseline_source_version IS NOT NULL AND
                 materialization_baseline_generation_job_id IS NOT NULL AND
                 selected_generation_job_id IS NULL AND
                 reused_generation_job_id = materialization_baseline_generation_job_id) OR
                (outcome = 'REGENERATE' AND predecessor_assessment_id IS NOT NULL AND
                 baseline_source_version IS NOT NULL AND
                 materialization_baseline_generation_job_id IS NOT NULL AND
                 selected_generation_job_id IS NOT NULL AND reused_generation_job_id IS NULL) OR
                (outcome = 'REVIEW_REQUIRED' AND selected_generation_job_id IS NULL AND
                 reused_generation_job_id IS NULL)
            ),
            CONSTRAINT ck_generation_change_assessments_changed_fields_array CHECK (
                jsonb_typeof(changed_fields) = 'array' AND
                tc_generation_impact_changed_fields_valid(changed_fields) AND
                changed_fields <@ '[
                    "$INITIAL_SOURCE", "$REVIEW_REQUIRED",
                    "slackWorkItem.developmentItem", "slackWorkItem.requestedMenu",
                    "slackWorkItem.featureDescription", "slackWorkItem.requestDetails",
                    "slackWorkItem.openQuestions", "activeKnowledge.requirements",
                    "activeKnowledge.decisions", "activeKnowledge.constraints",
                    "activeKnowledge.evidence", "retrievalInputs.existingCases",
                    "retrievalInputs.failedOrBlockedExecutions", "retrievalInputs.openIssues",
                    "retrievalInputs.extraSources", "categoryCandidates", "generationPolicy",
                    "contractConfig"
                ]'::jsonb AND
                (outcome <> 'INITIAL_GENERATE' OR changed_fields = '["$INITIAL_SOURCE"]'::jsonb) AND
                (outcome <> 'SKIP' OR changed_fields = '[]'::jsonb) AND
                (outcome <> 'REGENERATE' OR
                 jsonb_array_length(changed_fields) > 0 AND
                 NOT (changed_fields ?| ARRAY['$INITIAL_SOURCE', '$REVIEW_REQUIRED'])) AND
                (outcome <> 'REVIEW_REQUIRED' OR
                 changed_fields = '["$REVIEW_REQUIRED"]'::jsonb)
            )
        );

        CREATE UNIQUE INDEX uq_generation_change_assessments_selected_job
            ON generation_change_assessments (selected_generation_job_id)
            WHERE selected_generation_job_id IS NOT NULL;
        CREATE INDEX ix_generation_change_assessments_work_item_version
            ON generation_change_assessments
            (slack_work_item_id, current_source_version DESC);

        CREATE FUNCTION tc_generation_change_assessments_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'generation change assessments are append-only'
                USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER trg_generation_change_assessments_immutable
            BEFORE UPDATE OR DELETE ON generation_change_assessments
            FOR EACH ROW EXECUTE FUNCTION tc_generation_change_assessments_immutable();

        CREATE FUNCTION tc_generation_change_assessments_validate_links()
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

        CREATE CONSTRAINT TRIGGER trg_generation_change_assessments_validate_links
            AFTER INSERT ON generation_change_assessments
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_change_assessments_validate_links();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE generation_change_assessments;
        DROP FUNCTION tc_generation_change_assessments_validate_links();
        DROP FUNCTION tc_generation_change_assessments_immutable();
        DROP FUNCTION IF EXISTS tc_generation_impact_changed_fields_valid(jsonb);
        """
    )
