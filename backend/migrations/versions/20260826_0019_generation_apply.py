"""Atomic generation apply receipts and ordered outcomes.

Revision ID: 20260826_0019
Revises: 20260826_0018
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260826_0019"
down_revision: str | None = "20260826_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE generation_applies (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            generation_job_id uuid NOT NULL
                REFERENCES generation_jobs(id) ON DELETE RESTRICT,
            parent_work_item_id uuid NOT NULL
                REFERENCES work_items(id) ON DELETE RESTRICT,
            idempotency_key varchar(255) NOT NULL,
            request_digest varchar(64) NOT NULL,
            context_digest varchar(64) NOT NULL,
            result_digest varchar(64) NOT NULL,
            placement_digest varchar(64) NOT NULL,
            audit_id uuid NOT NULL
                REFERENCES audit_logs(id) ON DELETE RESTRICT
                DEFERRABLE INITIALLY DEFERRED,
            receipt jsonb NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_applies_generation_job UNIQUE (generation_job_id),
            CONSTRAINT uq_generation_applies_idempotency_key UNIQUE (idempotency_key),
            CONSTRAINT ck_generation_applies_idempotency_key_nonblank
                CHECK (char_length(btrim(idempotency_key)) > 0),
            CONSTRAINT ck_generation_applies_request_digest
                CHECK (request_digest ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_generation_applies_context_digest
                CHECK (context_digest ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_generation_applies_result_digest
                CHECK (result_digest ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_generation_applies_placement_digest
                CHECK (placement_digest ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_generation_applies_receipt_object
                CHECK (jsonb_typeof(receipt) = 'object')
        );

        CREATE INDEX ix_generation_applies_parent_work_item
            ON generation_applies (parent_work_item_id, applied_at, id);

        CREATE TABLE generation_apply_outcomes (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            generation_apply_id uuid NOT NULL
                REFERENCES generation_applies(id) ON DELETE RESTRICT,
            generated_case_index smallint NOT NULL,
            exact_content_key varchar(64) NOT NULL,
            outcome varchar(10) NOT NULL,
            test_case_id uuid NOT NULL
                REFERENCES test_cases(id) ON DELETE RESTRICT,
            test_case_version_id uuid
                REFERENCES test_case_versions(id) ON DELETE RESTRICT,
            match_reason varchar(10),
            preserved boolean NOT NULL,
            snapshot jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_generation_apply_outcomes_apply_index
                UNIQUE (generation_apply_id, generated_case_index),
            CONSTRAINT ck_generation_apply_outcomes_index
                CHECK (generated_case_index BETWEEN 0 AND 9),
            CONSTRAINT ck_generation_apply_outcomes_exact_key
                CHECK (exact_content_key ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_generation_apply_outcomes_outcome
                CHECK (outcome IN ('CREATED', 'REUSED')),
            CONSTRAINT ck_generation_apply_outcomes_match_reason
                CHECK (match_reason IS NULL OR match_reason IN ('EXACT', 'SEMANTIC')),
            CONSTRAINT ck_generation_apply_outcomes_shape CHECK (
                (outcome = 'CREATED' AND test_case_version_id IS NOT NULL
                    AND match_reason IS NULL AND preserved = false)
                OR
                (outcome = 'REUSED' AND test_case_version_id IS NOT NULL
                    AND match_reason IS NOT NULL AND preserved = true)
            ),
            CONSTRAINT ck_generation_apply_outcomes_snapshot_object
                CHECK (jsonb_typeof(snapshot) = 'object')
        );

        CREATE INDEX ix_generation_apply_outcomes_case
            ON generation_apply_outcomes (test_case_id, generation_apply_id);

        CREATE FUNCTION tc_reject_generation_apply_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'generation apply records are immutable'
                USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER trg_generation_applies_immutable
        BEFORE UPDATE OR DELETE ON generation_applies
        FOR EACH ROW EXECUTE FUNCTION tc_reject_generation_apply_mutation();

        CREATE TRIGGER trg_generation_apply_outcomes_immutable
        BEFORE UPDATE OR DELETE ON generation_apply_outcomes
        FOR EACH ROW EXECUTE FUNCTION tc_reject_generation_apply_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER trg_generation_apply_outcomes_immutable
            ON generation_apply_outcomes;
        DROP TRIGGER trg_generation_applies_immutable ON generation_applies;
        DROP FUNCTION tc_reject_generation_apply_mutation();
        DROP TABLE generation_apply_outcomes;
        DROP TABLE generation_applies;
        """
    )
