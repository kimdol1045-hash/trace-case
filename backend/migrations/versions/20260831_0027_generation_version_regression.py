"""T09 immutable version-set baseline and activation ledger.

Revision ID: 20260831_0027
Revises: 20260828_0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260831_0027"
down_revision: str | None = "20260828_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column[object]:
    return sa.Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.create_table(
        "generation_version_sets",
        _id(),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("synthetic", sa.Boolean, nullable=False),
        sa.Column("reviewed", sa.Boolean, nullable=False),
        sa.Column("identity", JSONB, nullable=False),
        sa.Column("identity_digest", sa.String(71), nullable=False),
        sa.Column("cohort_id", sa.String(256), nullable=False),
        sa.Column("cohort_digest", sa.String(71), nullable=False),
        sa.Column("population_membership_digest", sa.String(71), nullable=False),
        sa.Column("regression_report_digest", sa.String(71), nullable=False),
        sa.Column("artifact_manifest_digest", sa.String(71), nullable=False),
        sa.Column("live_attestation_digest", sa.String(71)),
        sa.Column("created_by", sa.String(256), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("version", name="uq_generation_version_sets_version"),
        sa.UniqueConstraint(
            "identity_digest", name="uq_generation_version_sets_identity_digest"
        ),
        sa.CheckConstraint(
            "version >= 1", name="ck_generation_version_sets_version_positive"
        ),
        sa.CheckConstraint(
            "identity_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "cohort_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "population_membership_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "regression_report_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "artifact_manifest_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "(live_attestation_digest IS NULL OR "
            "live_attestation_digest ~ '^sha256:[a-f0-9]{64}$')",
            name="ck_generation_version_sets_digests",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(identity) = 'object'",
            name="ck_generation_version_sets_identity_object",
        ),
        sa.CheckConstraint(
            "NOT synthetic OR (NOT reviewed AND live_attestation_digest IS NULL)",
            name="ck_generation_version_sets_synthetic_not_reviewed_or_live",
        ),
    )
    op.create_table(
        "generation_baseline_change_requests",
        _id(),
        sa.Column(
            "version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("requester_principal_id", sa.String(256), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column(
            "expected_active_version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
        ),
        sa.Column("expected_ledger_version", sa.Integer, nullable=False),
        sa.Column("old_identity_digest", sa.String(71), nullable=False),
        sa.Column("proposed_identity_digest", sa.String(71), nullable=False),
        sa.Column("change_request", JSONB, nullable=False),
        sa.Column("regression_input_digest", sa.String(71), nullable=False),
        sa.Column("regression_report", JSONB, nullable=False),
        sa.Column("regression_report_digest", sa.String(71), nullable=False),
        sa.Column("compatibility_matrix", JSONB, nullable=False),
        sa.Column("compatibility_matrix_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "version_set_id", name="uq_generation_baseline_change_version_set"
        ),
        sa.UniqueConstraint(
            "requester_principal_id",
            "idempotency_key",
            name="uq_generation_baseline_change_actor_key",
        ),
        sa.CheckConstraint(
            "status IN ('CONTRACT_ONLY', 'CANDIDATE', 'REJECTED')",
            name="ck_generation_baseline_change_requests_status",
        ),
        sa.CheckConstraint(
            "expected_ledger_version >= 0",
            name="ck_generation_baseline_change_requests_expected_version",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "old_identity_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "proposed_identity_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "regression_input_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "regression_report_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "compatibility_matrix_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_generation_baseline_change_requests_digests",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(change_request) = 'object' AND "
            "jsonb_typeof(regression_report) = 'object' AND "
            "jsonb_typeof(compatibility_matrix) = 'object'",
            name="ck_generation_baseline_change_requests_artifacts_object",
        ),
    )
    op.create_table(
        "generation_baseline_approvals",
        _id(),
        sa.Column(
            "version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("protected_manifest_digest", sa.String(71), nullable=False),
        sa.Column("approver_one_principal_id", sa.String(256), nullable=False),
        sa.Column("approver_one_role", sa.String(40), nullable=False),
        sa.Column("approver_one_decision_digest", sa.String(71), nullable=False),
        sa.Column("approver_two_principal_id", sa.String(256), nullable=False),
        sa.Column("approver_two_role", sa.String(40), nullable=False),
        sa.Column("approver_two_decision_digest", sa.String(71), nullable=False),
        sa.Column("approval_digest", sa.String(71), nullable=False),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "version_set_id", name="uq_generation_baseline_approval_version_set"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_generation_baseline_approval_key"
        ),
        sa.CheckConstraint(
            "approver_one_principal_id <> approver_two_principal_id AND "
            "approver_one_role <> approver_two_role AND "
            "approver_one_role IN ('QUALITY_OWNER', 'RELEASE_OWNER') AND "
            "approver_two_role IN ('QUALITY_OWNER', 'RELEASE_OWNER')",
            name="ck_generation_baseline_approvals_two_person_roles",
        ),
        sa.CheckConstraint(
            "protected_manifest_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "approver_one_decision_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "approver_two_decision_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "approval_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_generation_baseline_approvals_digests",
        ),
    )
    op.create_table(
        "generation_baseline_runtime",
        sa.Column("scope", sa.String(20), primary_key=True),
        sa.Column(
            "ledger_version", sa.Integer, server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "active_version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope = 'GLOBAL'", name="ck_generation_baseline_runtime_global_scope"
        ),
        sa.CheckConstraint(
            "ledger_version >= 0",
            name="ck_generation_baseline_runtime_ledger_version",
        ),
    )
    op.create_table(
        "generation_baseline_activations",
        _id(),
        sa.Column(
            "version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "previous_version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "approval_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_baseline_approvals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("principal_id", sa.String(256), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("protected_manifest_digest", sa.String(71), nullable=False),
        sa.Column("expected_ledger_version", sa.Integer, nullable=False),
        sa.Column("committed_ledger_version", sa.Integer, nullable=False),
        sa.Column("receipt_digest", sa.String(71), nullable=False),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "version_set_id", name="uq_generation_baseline_activation_version_set"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_generation_baseline_activation_key"
        ),
        sa.UniqueConstraint(
            "committed_ledger_version",
            name="uq_generation_baseline_activation_ledger_version",
        ),
        sa.CheckConstraint(
            "committed_ledger_version = expected_ledger_version + 1",
            name="ck_generation_baseline_activations_ledger_contiguous",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "protected_manifest_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "receipt_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_generation_baseline_activations_digests",
        ),
    )
    op.create_table(
        "generation_baseline_rollbacks",
        _id(),
        sa.Column(
            "failed_version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_activation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_baseline_activations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("principal_id", sa.String(256), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("target_artifact_manifest_digest", sa.String(71), nullable=False),
        sa.Column("reproduction_report", JSONB, nullable=False),
        sa.Column("reproduction_artifact_digest", sa.String(71), nullable=False),
        sa.Column("expected_ledger_version", sa.Integer, nullable=False),
        sa.Column("committed_ledger_version", sa.Integer, nullable=False),
        sa.Column("receipt_digest", sa.String(71), nullable=False),
        sa.Column(
            "rolled_back_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_generation_baseline_rollback_key"
        ),
        sa.UniqueConstraint(
            "committed_ledger_version",
            name="uq_generation_baseline_rollback_ledger_version",
        ),
        sa.CheckConstraint(
            "failed_version_set_id <> target_version_set_id AND "
            "committed_ledger_version = expected_ledger_version + 1",
            name="ck_generation_baseline_rollbacks_target_and_ledger",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "target_artifact_manifest_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "reproduction_artifact_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "receipt_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_generation_baseline_rollbacks_digests",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reproduction_report) = 'object'",
            name="ck_generation_baseline_rollbacks_report_object",
        ),
    )
    op.create_table(
        "generation_baseline_audit_events",
        _id(),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column(
            "version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("principal_id", sa.String(256), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("expected_ledger_version", sa.Integer, nullable=False),
        sa.Column("committed_ledger_version", sa.Integer, nullable=False),
        sa.Column(
            "before_active_version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "after_active_version_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_version_sets.id", ondelete="RESTRICT"),
        ),
        sa.Column("receipt_digest", sa.String(71), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "sequence", name="uq_generation_baseline_audit_sequence"
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name="ck_generation_baseline_audit_events_sequence_positive",
        ),
        sa.CheckConstraint(
            "event_type IN ('CANDIDATE_RECORDED', 'APPROVAL_RECORDED', "
            "'BASELINE_ACTIVATED', 'ROLLBACK_ACTIVATED')",
            name="ck_generation_baseline_audit_events_event_type",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "receipt_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_generation_baseline_audit_events_digests",
        ),
    )

    op.add_column(
        "generation_jobs", sa.Column("version_set_id", UUID(as_uuid=True))
    )
    op.add_column(
        "generation_jobs", sa.Column("version_set_digest", sa.String(71))
    )
    op.create_foreign_key(
        "fk_generation_jobs_version_set_id_generation_version_sets",
        "generation_jobs",
        "generation_version_sets",
        ["version_set_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_generation_jobs_version_set_pin_complete",
        "generation_jobs",
        "(version_set_id IS NULL AND version_set_digest IS NULL) OR "
        "(version_set_id IS NOT NULL AND "
        "version_set_digest ~ '^sha256:[a-f0-9]{64}$')",
    )
    _install_guards()


def _install_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION tc_generation_baseline_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER trg_generation_version_sets_immutable
            BEFORE UPDATE OR DELETE ON generation_version_sets
            FOR EACH ROW EXECUTE FUNCTION tc_generation_baseline_immutable();
        CREATE TRIGGER trg_generation_baseline_change_requests_immutable
            BEFORE UPDATE OR DELETE ON generation_baseline_change_requests
            FOR EACH ROW EXECUTE FUNCTION tc_generation_baseline_immutable();
        CREATE TRIGGER trg_generation_baseline_approvals_immutable
            BEFORE UPDATE OR DELETE ON generation_baseline_approvals
            FOR EACH ROW EXECUTE FUNCTION tc_generation_baseline_immutable();
        CREATE TRIGGER trg_generation_baseline_activations_immutable
            BEFORE UPDATE OR DELETE ON generation_baseline_activations
            FOR EACH ROW EXECUTE FUNCTION tc_generation_baseline_immutable();
        CREATE TRIGGER trg_generation_baseline_rollbacks_immutable
            BEFORE UPDATE OR DELETE ON generation_baseline_rollbacks
            FOR EACH ROW EXECUTE FUNCTION tc_generation_baseline_immutable();
        CREATE TRIGGER trg_generation_baseline_audit_events_immutable
            BEFORE UPDATE OR DELETE ON generation_baseline_audit_events
            FOR EACH ROW EXECUTE FUNCTION tc_generation_baseline_immutable();

        CREATE FUNCTION tc_validate_generation_baseline_approval()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            candidate generation_version_sets%ROWTYPE;
            change_status varchar(30);
        BEGIN
            SELECT * INTO candidate
              FROM generation_version_sets
             WHERE id = NEW.version_set_id;
            SELECT status INTO change_status
              FROM generation_baseline_change_requests
             WHERE version_set_id = NEW.version_set_id;
            IF candidate.id IS NULL OR candidate.synthetic OR NOT candidate.reviewed OR
               candidate.live_attestation_digest IS NULL OR
               candidate.artifact_manifest_digest <> NEW.protected_manifest_digest OR
               change_status IS DISTINCT FROM 'CANDIDATE' THEN
                RAISE EXCEPTION 'baseline is not eligible for approval'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_generation_baseline_approvals_validate
            BEFORE INSERT ON generation_baseline_approvals
            FOR EACH ROW EXECUTE FUNCTION tc_validate_generation_baseline_approval();

        CREATE FUNCTION tc_validate_generation_baseline_activation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            candidate generation_version_sets%ROWTYPE;
            approval generation_baseline_approvals%ROWTYPE;
        BEGIN
            SELECT * INTO candidate
              FROM generation_version_sets
             WHERE id = NEW.version_set_id;
            SELECT * INTO approval
              FROM generation_baseline_approvals
             WHERE id = NEW.approval_id;
            IF candidate.id IS NULL OR approval.id IS NULL OR
               approval.version_set_id <> NEW.version_set_id OR
               candidate.synthetic OR NOT candidate.reviewed OR
               candidate.live_attestation_digest IS NULL OR
               candidate.artifact_manifest_digest <> NEW.protected_manifest_digest OR
               approval.protected_manifest_digest <> NEW.protected_manifest_digest THEN
                RAISE EXCEPTION 'baseline activation authority is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_generation_baseline_activations_validate
            BEFORE INSERT ON generation_baseline_activations
            FOR EACH ROW EXECUTE FUNCTION tc_validate_generation_baseline_activation();

        CREATE FUNCTION tc_guard_generation_baseline_runtime()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'generation baseline runtime cannot be deleted'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.scope IS DISTINCT FROM OLD.scope OR
               NEW.ledger_version < OLD.ledger_version OR
               (NEW.active_version_set_id IS DISTINCT FROM OLD.active_version_set_id AND
                NEW.ledger_version <> OLD.ledger_version + 1) OR
               (NEW.active_version_set_id IS NOT DISTINCT FROM OLD.active_version_set_id AND
                NEW.ledger_version <> OLD.ledger_version) THEN
                RAISE EXCEPTION 'generation baseline runtime transition is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_generation_baseline_runtime_guard
            BEFORE UPDATE OR DELETE ON generation_baseline_runtime
            FOR EACH ROW EXECUTE FUNCTION tc_guard_generation_baseline_runtime();

        CREATE FUNCTION tc_guard_generation_job_version_set_pin()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            predecessor_version_set_id uuid;
            predecessor_version_set_digest varchar(71);
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.version_set_id IS NOT NULL AND ROW(
                NEW.version_set_id, NEW.version_set_digest
            ) IS DISTINCT FROM ROW(
                OLD.version_set_id, OLD.version_set_digest
            ) THEN
                RAISE EXCEPTION 'generation job version-set pin is immutable'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.retry_of_job_id IS NOT NULL AND NEW.version_set_id IS NOT NULL THEN
                SELECT version_set_id, version_set_digest
                  INTO predecessor_version_set_id, predecessor_version_set_digest
                  FROM generation_jobs
                 WHERE id = NEW.retry_of_job_id;
                IF ROW(NEW.version_set_id, NEW.version_set_digest) IS DISTINCT FROM ROW(
                    predecessor_version_set_id, predecessor_version_set_digest
                ) THEN
                    RAISE EXCEPTION 'retry version-set pin differs from predecessor'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_generation_jobs_version_set_pin
            BEFORE INSERT OR UPDATE ON generation_jobs
            FOR EACH ROW EXECUTE FUNCTION tc_guard_generation_job_version_set_pin();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER trg_generation_jobs_version_set_pin ON generation_jobs;
        DROP FUNCTION tc_guard_generation_job_version_set_pin();
        """
    )
    op.drop_constraint(
        "ck_generation_jobs_version_set_pin_complete",
        "generation_jobs",
        type_="check",
    )
    op.drop_constraint(
        "fk_generation_jobs_version_set_id_generation_version_sets",
        "generation_jobs",
        type_="foreignkey",
    )
    op.drop_column("generation_jobs", "version_set_digest")
    op.drop_column("generation_jobs", "version_set_id")

    op.execute(
        """
        DROP TRIGGER trg_generation_baseline_runtime_guard
            ON generation_baseline_runtime;
        DROP FUNCTION tc_guard_generation_baseline_runtime();

        DROP TRIGGER IF EXISTS trg_generation_baseline_activations_validate
            ON generation_baseline_activations;
        DROP FUNCTION IF EXISTS tc_validate_generation_baseline_activation();
        DROP TRIGGER IF EXISTS trg_generation_baseline_approvals_validate
            ON generation_baseline_approvals;
        DROP FUNCTION IF EXISTS tc_validate_generation_baseline_approval();

        DROP TRIGGER trg_generation_baseline_audit_events_immutable
            ON generation_baseline_audit_events;
        DROP TRIGGER trg_generation_baseline_rollbacks_immutable
            ON generation_baseline_rollbacks;
        DROP TRIGGER trg_generation_baseline_activations_immutable
            ON generation_baseline_activations;
        DROP TRIGGER trg_generation_baseline_approvals_immutable
            ON generation_baseline_approvals;
        DROP TRIGGER trg_generation_baseline_change_requests_immutable
            ON generation_baseline_change_requests;
        DROP TRIGGER trg_generation_version_sets_immutable
            ON generation_version_sets;
        """
    )
    for table in (
        "generation_baseline_audit_events",
        "generation_baseline_rollbacks",
        "generation_baseline_activations",
        "generation_baseline_runtime",
        "generation_baseline_approvals",
        "generation_baseline_change_requests",
        "generation_version_sets",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION tc_generation_baseline_immutable()")
