"""Immutable automatic-generation suggestions and decision receipts.

Revision ID: 20260827_0024
Revises: 20260827_0023
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_0024"
down_revision: str | None = "20260827_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tc_guard_generation_job_context_seal()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            actual_count integer;
            allowed_suggestion_seal boolean;
        BEGIN
            allowed_suggestion_seal :=
                current_setting('tc.generation_suggestion_seal', true) = 'on'
                AND OLD.status = 'QUEUED' AND NEW.status = 'QUEUED'
                AND OLD.generation_trigger = 'AUTOMATIC'
                AND OLD.root_job_id = OLD.id AND NEW.root_job_id = NEW.id
                AND OLD.schema_version = '1.0.0'
                AND OLD.generation_input_schema_version = '1.0.0'
                AND NEW.schema_version = 'generation-suggestion/1.0.0'
                AND NEW.generation_input_schema_version = 'generation-suggestion/1.0.0'
                AND ROW(
                    NEW.slack_work_item_id, NEW.source_version, NEW.generation_version,
                    NEW.provider, NEW.model, NEW.prompt_version,
                    NEW.ontology_schema_id, NEW.context_item_count,
                    NEW.context_sealed_at, NEW.root_job_id, NEW.retry_of_job_id,
                    NEW.attempt_number, NEW.generation_input_capture_state,
                    NEW.generation_trigger
                ) IS NOT DISTINCT FROM ROW(
                    OLD.slack_work_item_id, OLD.source_version, OLD.generation_version,
                    OLD.provider, OLD.model, OLD.prompt_version,
                    OLD.ontology_schema_id, OLD.context_item_count,
                    OLD.context_sealed_at, OLD.root_job_id, OLD.retry_of_job_id,
                    OLD.attempt_number, OLD.generation_input_capture_state,
                    OLD.generation_trigger
                );
            IF OLD.context_sealed_at IS NOT NULL AND NOT allowed_suggestion_seal AND ROW(
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

        CREATE TABLE generation_suggestion_targets (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            slack_work_item_id uuid NOT NULL REFERENCES slack_work_items(id) ON DELETE RESTRICT,
            suggestion_target_ref varchar(128) NOT NULL,
            source_change_assessment_id uuid NOT NULL
                REFERENCES generation_change_assessments(id) ON DELETE RESTRICT,
            selected_root_job_id uuid NOT NULL
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            test_case_id uuid NOT NULL REFERENCES test_cases(id) ON DELETE RESTRICT,
            generation_lineage_id uuid NOT NULL,
            generation_created_outcome_id uuid NOT NULL
                REFERENCES generation_apply_outcomes(id) ON DELETE RESTRICT,
            baseline_case_version_id uuid NOT NULL,
            baseline_case_version integer NOT NULL,
            baseline_case_resource_version integer NOT NULL,
            target_snapshot jsonb NOT NULL,
            target_digest varchar(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_suggestion_targets_ref UNIQUE (suggestion_target_ref),
            CONSTRAINT uq_generation_suggestion_targets_assessment_lineage
                UNIQUE (source_change_assessment_id, generation_lineage_id),
            CONSTRAINT uq_generation_suggestion_targets_root_case
                UNIQUE (selected_root_job_id, test_case_id),
            CONSTRAINT fk_generation_suggestion_targets_case_version
                FOREIGN KEY (test_case_id, baseline_case_version_id)
                REFERENCES test_case_versions(test_case_id, id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT ck_generation_suggestion_targets_ref
                CHECK (suggestion_target_ref ~ '^generation_suggestion_target_[A-Za-z0-9_-]+$'),
            CONSTRAINT ck_generation_suggestion_targets_versions
                CHECK (baseline_case_version >= 1 AND baseline_case_resource_version >= 1),
            CONSTRAINT ck_generation_suggestion_targets_snapshot
                CHECK (jsonb_typeof(target_snapshot) = 'object'),
            CONSTRAINT ck_generation_suggestion_targets_digest
                CHECK (target_digest ~ '^[0-9a-f]{64}$')
        );

        CREATE TABLE generation_suggestions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            test_case_id uuid NOT NULL REFERENCES test_cases(id) ON DELETE RESTRICT,
            generation_lineage_id uuid NOT NULL,
            suggestion_target_id uuid NOT NULL
                REFERENCES generation_suggestion_targets(id) ON DELETE RESTRICT,
            source_generation_job_id uuid NOT NULL
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            source_root_job_id uuid NOT NULL
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            source_change_assessment_id uuid NOT NULL
                REFERENCES generation_change_assessments(id) ON DELETE RESTRICT,
            generated_case_index smallint NOT NULL,
            exact_content_key varchar(64) NOT NULL,
            baseline_case_version_id uuid NOT NULL,
            baseline_case_version integer NOT NULL,
            baseline_case_resource_version integer NOT NULL,
            context_digest varchar(64) NOT NULL,
            result_digest varchar(64) NOT NULL,
            diff_schema_version varchar(100) NOT NULL,
            baseline_snapshot jsonb NOT NULL,
            proposed_snapshot jsonb NOT NULL,
            diff_snapshot jsonb NOT NULL,
            baseline_digest varchar(64) NOT NULL,
            proposed_digest varchar(64) NOT NULL,
            diff_digest varchar(64) NOT NULL,
            supersedes_suggestion_id uuid REFERENCES generation_suggestions(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_suggestions_target UNIQUE (suggestion_target_id),
            CONSTRAINT uq_generation_suggestions_assessment_lineage
                UNIQUE (source_change_assessment_id, generation_lineage_id),
            CONSTRAINT uq_generation_suggestions_source_candidate
                UNIQUE (suggestion_target_id, source_generation_job_id, generated_case_index),
            CONSTRAINT uq_generation_suggestions_supersedes UNIQUE (supersedes_suggestion_id),
            CONSTRAINT fk_generation_suggestions_case_version
                FOREIGN KEY (test_case_id, baseline_case_version_id)
                REFERENCES test_case_versions(test_case_id, id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT ck_generation_suggestions_index CHECK (generated_case_index BETWEEN 0 AND 9),
            CONSTRAINT ck_generation_suggestions_versions
                CHECK (baseline_case_version >= 1 AND baseline_case_resource_version >= 1),
            CONSTRAINT ck_generation_suggestions_digests CHECK (
                exact_content_key ~ '^[0-9a-f]{64}$' AND
                context_digest ~ '^[0-9a-f]{64}$' AND result_digest ~ '^[0-9a-f]{64}$' AND
                baseline_digest ~ '^[0-9a-f]{64}$' AND proposed_digest ~ '^[0-9a-f]{64}$' AND
                diff_digest ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_generation_suggestions_diff_schema
                CHECK (diff_schema_version = 'generation-suggestion-diff/1.0.0'),
            CONSTRAINT ck_generation_suggestions_snapshots CHECK (
                jsonb_typeof(baseline_snapshot) = 'object' AND
                jsonb_typeof(proposed_snapshot) = 'object' AND
                jsonb_typeof(diff_snapshot) = 'object'
            )
        );

        CREATE TABLE generation_suggestion_resolutions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspace_settings(id) ON DELETE RESTRICT,
            suggestion_id uuid NOT NULL REFERENCES generation_suggestions(id) ON DELETE RESTRICT,
            decision varchar(10) NOT NULL,
            actor jsonb NOT NULL,
            reason varchar(500) NOT NULL,
            observed_case_resource_version integer NOT NULL,
            observed_case_version_id uuid NOT NULL REFERENCES test_case_versions(id) ON DELETE RESTRICT,
            applied_case_version_id uuid REFERENCES test_case_versions(id) ON DELETE RESTRICT,
            audit_id uuid NOT NULL REFERENCES audit_logs(id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            receipt jsonb NOT NULL,
            decided_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_suggestion_resolutions_suggestion UNIQUE (suggestion_id),
            CONSTRAINT ck_generation_suggestion_resolutions_decision
                CHECK (decision IN ('APPLIED', 'IGNORED')),
            CONSTRAINT ck_generation_suggestion_resolutions_reason
                CHECK (char_length(btrim(reason)) > 0),
            CONSTRAINT ck_generation_suggestion_resolutions_shape CHECK (
                observed_case_resource_version >= 1 AND jsonb_typeof(actor) = 'object' AND
                jsonb_typeof(receipt) = 'object' AND
                ((decision = 'APPLIED' AND applied_case_version_id IS NOT NULL) OR
                 (decision = 'IGNORED' AND applied_case_version_id IS NULL))
            )
        );

        CREATE INDEX ix_generation_suggestion_targets_case
            ON generation_suggestion_targets(test_case_id, created_at DESC, id);
        CREATE INDEX ix_generation_suggestions_case
            ON generation_suggestions(test_case_id, created_at DESC, id);

        CREATE FUNCTION tc_generation_suggestion_targets_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'generation suggestion targets are append-only' USING ERRCODE = '55000';
        END;
        $$;
        CREATE FUNCTION tc_generation_suggestions_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'generation suggestions are append-only' USING ERRCODE = '55000';
        END;
        $$;
        CREATE FUNCTION tc_generation_suggestion_resolutions_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'suggestion resolutions are append-only' USING ERRCODE = '55000';
        END;
        $$;

        CREATE FUNCTION tc_generation_suggestion_targets_links()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE matches integer;
        BEGIN
            SELECT count(*) INTO matches
              FROM test_cases c
              JOIN test_case_versions v
                ON v.id = NEW.baseline_case_version_id AND v.test_case_id = c.id
              JOIN generation_apply_outcomes o ON o.id = NEW.generation_created_outcome_id
              JOIN generation_applies a ON a.id = o.generation_apply_id
              JOIN generation_jobs origin ON origin.id = a.generation_job_id
              JOIN generation_change_assessments ca ON ca.id = NEW.source_change_assessment_id
              JOIN generation_jobs selected ON selected.id = NEW.selected_root_job_id
              JOIN slack_work_items sw ON sw.id = NEW.slack_work_item_id
             WHERE c.id = NEW.test_case_id
               AND c.workspace_id = NEW.workspace_id AND sw.workspace_id = NEW.workspace_id
               AND c.generation_lineage_id = NEW.generation_lineage_id
               AND c.generation_created_outcome_id = NEW.generation_created_outcome_id
               AND c.generation_managed = false AND c.generation_released_at IS NOT NULL
               AND o.outcome = 'CREATED' AND o.test_case_id = c.id
               AND origin.slack_work_item_id = NEW.slack_work_item_id
               AND ca.slack_work_item_id = NEW.slack_work_item_id
               AND ca.outcome = 'REGENERATE' AND ca.selected_generation_job_id = selected.id
               AND selected.root_job_id = selected.id
               AND selected.slack_work_item_id = NEW.slack_work_item_id
               AND selected.generation_trigger = 'AUTOMATIC'
               AND selected.schema_version = 'generation-suggestion/1.0.0'
               AND selected.generation_input_schema_version =
                   'generation-suggestion/1.0.0'
               AND selected.generation_input_capture_state = 'SEALED'
               AND selected.generation_input->>'generationJobId' =
                   'generation_job_' || replace(selected.id::text, '-', '')
               AND selected.generation_input->>'generationChangeAssessmentId' =
                   'generation_change_assessment_' || replace(ca.id::text, '-', '')
               AND c.current_version_id = NEW.baseline_case_version_id
               AND c.version = NEW.baseline_case_resource_version
               AND v.version_number = NEW.baseline_case_version
               AND NEW.target_snapshot->>'workspaceId' =
                   'workspace_' || replace(NEW.workspace_id::text, '-', '')
               AND NEW.target_snapshot->>'slackWorkItemId' =
                   'slack_work_item_' || replace(NEW.slack_work_item_id::text, '-', '')
               AND NEW.target_snapshot->>'generationChangeAssessmentId' =
                   'generation_change_assessment_' || replace(ca.id::text, '-', '')
               AND NEW.target_snapshot->>'selectedRootJobId' =
                   'generation_job_' || replace(selected.id::text, '-', '')
               AND NEW.target_snapshot->>'testCaseId' =
                   'case_' || replace(c.id::text, '-', '')
               AND NEW.target_snapshot->>'testCaseVersionId' =
                   'case_version_' || replace(v.id::text, '-', '')
               AND NEW.target_snapshot->>'caseVersion' = v.version_number::text
               AND NEW.target_snapshot->>'caseResourceVersion' = c.version::text
               AND NEW.target_snapshot->>'generationLineageId' =
                   'generation_lineage_' || replace(NEW.generation_lineage_id::text, '-', '')
               AND NEW.target_snapshot->>'generationCreatedOutcomeId' =
                   'generation_apply_outcome_' || replace(o.id::text, '-', '')
               AND EXISTS (
                   SELECT 1
                     FROM jsonb_array_elements(
                         selected.generation_input->'suggestionTargets'
                     ) exposed
                    WHERE exposed->>'suggestionTargetRef' = NEW.suggestion_target_ref
                      AND exposed->>'testCaseId' =
                          'case_' || replace(c.id::text, '-', '')
                      AND exposed->>'testCaseVersionId' =
                          'case_version_' || replace(v.id::text, '-', '')
                      AND exposed->>'generationLineageId' =
                          'generation_lineage_' ||
                          replace(NEW.generation_lineage_id::text, '-', '')
                      AND exposed->>'targetDigest' = NEW.target_digest
               )
               AND EXISTS (
                   SELECT 1
                     FROM jsonb_array_elements(
                         selected.generation_input->'baseGenerationInput'->'existingCases'
                     ) existing_case
                    WHERE existing_case->>'testCaseId' =
                          'case_' || replace(c.id::text, '-', '')
                      AND existing_case->>'testCaseVersionId' =
                          'case_version_' || replace(v.id::text, '-', '')
               );
            IF matches <> 1 THEN
                RAISE EXCEPTION 'generation suggestion target provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_generation_suggestions_links()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE matches integer;
        BEGIN
            SELECT count(*) INTO matches
              FROM generation_suggestion_targets t
              JOIN generation_jobs j ON j.id = NEW.source_generation_job_id
              JOIN generation_jobs root ON root.id = NEW.source_root_job_id
             WHERE t.id = NEW.suggestion_target_id
               AND t.workspace_id = NEW.workspace_id
               AND t.test_case_id = NEW.test_case_id
               AND t.generation_lineage_id = NEW.generation_lineage_id
               AND t.source_change_assessment_id = NEW.source_change_assessment_id
               AND t.selected_root_job_id = NEW.source_root_job_id
               AND t.baseline_case_version_id = NEW.baseline_case_version_id
               AND t.baseline_case_version = NEW.baseline_case_version
               AND t.baseline_case_resource_version = NEW.baseline_case_resource_version
               AND j.root_job_id = NEW.source_root_job_id
               AND j.slack_work_item_id = t.slack_work_item_id
               AND j.status = 'SUCCEEDED'
               AND j.schema_version = 'generation-suggestion/1.0.0'
               AND j.generation_input_schema_version = 'generation-suggestion/1.0.0'
               AND j.generation_input_capture_state = 'SEALED'
               AND j.context_digest = NEW.context_digest
               AND root.id = root.root_job_id
               AND root.id = t.selected_root_job_id
               AND jsonb_typeof(j.result->'suggestions') = 'array'
               AND j.result->>'outputSchemaVersion' = 'generation-suggestion/1.0.0'
               AND j.result->'suggestions'->NEW.generated_case_index
                       ->>'suggestionTargetRef' = t.suggestion_target_ref
               AND NEW.baseline_snapshot = t.target_snapshot->'snapshot'
               AND (
                    (NEW.supersedes_suggestion_id IS NULL AND NOT EXISTS (
                        SELECT 1
                          FROM generation_suggestions prior
                         WHERE prior.id <> NEW.id
                           AND prior.test_case_id = NEW.test_case_id
                           AND prior.generation_lineage_id = NEW.generation_lineage_id
                           AND NOT EXISTS (
                               SELECT 1 FROM generation_suggestion_resolutions resolved
                                WHERE resolved.suggestion_id = prior.id
                           )
                           AND NOT EXISTS (
                               SELECT 1 FROM generation_suggestions newer
                                WHERE newer.supersedes_suggestion_id = prior.id
                           )
                    )) OR
                    (NEW.supersedes_suggestion_id IS NOT NULL AND EXISTS (
                        SELECT 1
                          FROM generation_suggestions prior
                          JOIN generation_jobs prior_job
                            ON prior_job.id = prior.source_generation_job_id
                         WHERE prior.id = NEW.supersedes_suggestion_id
                           AND prior.workspace_id = NEW.workspace_id
                           AND prior.test_case_id = NEW.test_case_id
                           AND prior.generation_lineage_id = NEW.generation_lineage_id
                           AND NOT EXISTS (
                               SELECT 1 FROM generation_suggestion_resolutions resolved
                                WHERE resolved.suggestion_id = prior.id
                           )
                           AND NOT EXISTS (
                               SELECT 1 FROM generation_suggestions newer
                                WHERE newer.supersedes_suggestion_id = prior.id
                                  AND newer.id <> NEW.id
                           )
                           AND ROW(j.generation_version, j.attempt_number) >
                               ROW(prior_job.generation_version, prior_job.attempt_number)
                    ))
               );
            IF matches <> 1 THEN
                RAISE EXCEPTION 'generation suggestion provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_generation_suggestions_one_open()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE open_count integer;
        BEGIN
            SELECT count(*) INTO open_count
              FROM generation_suggestions s
             WHERE s.test_case_id = NEW.test_case_id
               AND s.generation_lineage_id = NEW.generation_lineage_id
               AND NOT EXISTS (SELECT 1 FROM generation_suggestion_resolutions r
                               WHERE r.suggestion_id = s.id)
               AND NOT EXISTS (SELECT 1 FROM generation_suggestions newer
                               WHERE newer.supersedes_suggestion_id = s.id);
            IF open_count > 1 THEN
                RAISE EXCEPTION 'only one open generation suggestion is allowed'
                    USING ERRCODE = '23505';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION tc_generation_suggestion_resolutions_links()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE matches integer;
        BEGIN
            SELECT count(*) INTO matches
              FROM generation_suggestions s
              JOIN generation_suggestion_targets t ON t.id = s.suggestion_target_id
              JOIN test_cases c ON c.id = s.test_case_id
              JOIN audit_logs audit ON audit.id = NEW.audit_id
             WHERE s.id = NEW.suggestion_id AND s.workspace_id = NEW.workspace_id
               AND c.workspace_id = NEW.workspace_id
               AND t.workspace_id = NEW.workspace_id
               AND NOT EXISTS (
                   SELECT 1 FROM generation_suggestions newer
                    WHERE newer.supersedes_suggestion_id = s.id
               )
               AND audit.workspace_id = NEW.workspace_id
               AND audit.entity_type = 'GENERATION_SUGGESTION'
               AND audit.entity_id = s.id
               AND audit.action = CASE NEW.decision
                   WHEN 'APPLIED' THEN 'APPLY_GENERATION_SUGGESTION'
                   ELSE 'IGNORE_GENERATION_SUGGESTION'
               END
               AND audit.reason = NEW.reason
               AND NEW.actor = jsonb_build_object(
                   'type', audit.source_type,
                   'actorLabel', audit.actor_label,
                   'externalActorId', audit.external_actor_id
               )
               AND NEW.receipt = jsonb_build_object(
                   'suggestionId',
                       'generation_suggestion_' || replace(s.id::text, '-', ''),
                   'decision', NEW.decision
               )
               AND (
                    (NEW.decision = 'IGNORED'
                     AND NEW.observed_case_version_id = c.current_version_id
                     AND NEW.observed_case_resource_version = c.version)
                    OR
                    (NEW.decision = 'APPLIED'
                     AND NEW.observed_case_version_id = s.baseline_case_version_id
                     AND NEW.observed_case_resource_version = s.baseline_case_resource_version
                     AND NEW.applied_case_version_id = c.current_version_id
                     AND c.version = s.baseline_case_resource_version + 1
                     AND EXISTS (
                         SELECT 1 FROM test_case_versions v
                          WHERE v.id = NEW.applied_case_version_id
                            AND v.test_case_id = c.id
                            AND v.version_number = s.baseline_case_version + 1
                            AND v.source_type = 'SLACK_AI'
                            AND v.source_metadata->>'generationSuggestionId' =
                                'generation_suggestion_' || replace(s.id::text, '-', '')
                            AND v.source_metadata->>'generationJobId' =
                                'generation_job_' ||
                                replace(s.source_generation_job_id::text, '-', '')
                            AND v.source_metadata->>'contextDigest' = s.context_digest
                            AND v.source_metadata->>'resultDigest' = s.result_digest))
               );
            IF matches <> 1 THEN
                RAISE EXCEPTION 'generation suggestion resolution provenance is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_generation_suggestion_targets_immutable
            BEFORE UPDATE OR DELETE ON generation_suggestion_targets
            FOR EACH ROW EXECUTE FUNCTION tc_generation_suggestion_targets_immutable();
        CREATE CONSTRAINT TRIGGER trg_generation_suggestion_targets_links
            AFTER INSERT ON generation_suggestion_targets DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_suggestion_targets_links();
        CREATE TRIGGER trg_generation_suggestions_immutable
            BEFORE UPDATE OR DELETE ON generation_suggestions
            FOR EACH ROW EXECUTE FUNCTION tc_generation_suggestions_immutable();
        CREATE CONSTRAINT TRIGGER trg_generation_suggestions_links
            AFTER INSERT ON generation_suggestions DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_suggestions_links();
        CREATE CONSTRAINT TRIGGER trg_generation_suggestions_one_open
            AFTER INSERT ON generation_suggestions DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_suggestions_one_open();
        CREATE TRIGGER trg_generation_suggestion_resolutions_immutable
            BEFORE UPDATE OR DELETE ON generation_suggestion_resolutions
            FOR EACH ROW EXECUTE FUNCTION tc_generation_suggestion_resolutions_immutable();
        CREATE CONSTRAINT TRIGGER trg_generation_suggestion_resolutions_links
            AFTER INSERT ON generation_suggestion_resolutions DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION tc_generation_suggestion_resolutions_links();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE generation_suggestion_resolutions;
        DROP TABLE generation_suggestions;
        DROP TABLE generation_suggestion_targets;
        DROP FUNCTION tc_generation_suggestion_resolutions_links();
        DROP FUNCTION tc_generation_suggestions_one_open();
        DROP FUNCTION tc_generation_suggestions_links();
        DROP FUNCTION tc_generation_suggestion_targets_links();
        DROP FUNCTION tc_generation_suggestion_resolutions_immutable();
        DROP FUNCTION tc_generation_suggestions_immutable();
        DROP FUNCTION tc_generation_suggestion_targets_immutable();

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
