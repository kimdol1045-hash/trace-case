"""Add the immutable automatic-generation quality review ledger.

Revision ID: 20260901_0028
Revises: 20260831_0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260901_0028"
down_revision: str | None = "20260831_0027"
branch_labels: str | None = None
depends_on: str | None = None


def _id() -> sa.Column[object]:
    return sa.Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.create_table(
        "generation_quality_candidates",
        _id(),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspace_settings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("candidate_key", sa.String(89), nullable=False),
        sa.Column("import_idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("base_artifact_digest", sa.String(71), nullable=False),
        sa.Column("base_dataset_fingerprint", sa.String(71), nullable=False),
        sa.Column("base_source_snapshot_fingerprint", sa.String(71), nullable=False),
        sa.Column("base_snapshot_fingerprint", sa.String(71), nullable=False),
        sa.Column("root_set_seal", sa.String(76), nullable=False),
        sa.Column("capture_environment", sa.String(20), nullable=False),
        sa.Column("capture_revision", sa.String(128), nullable=False),
        sa.Column("privacy_policy_version", sa.String(128), nullable=False),
        sa.Column("privacy_scan_status", sa.String(16), nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("declared_counts", JSONB, nullable=False),
        sa.Column("classification_distribution", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "id", "workspace_id", name="uq_generation_quality_candidates_id_workspace"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "candidate_key",
            name="uq_generation_quality_candidates_workspace_key",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "import_idempotency_key",
            name="uq_generation_quality_candidates_workspace_idempotency",
        ),
        sa.CheckConstraint(
            "candidate_key ~ '^quality-review-candidate:[a-f0-9]{64}$'",
            name="ck_generation_quality_candidates_key",
        ),
        sa.CheckConstraint(
            "import_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,254}$'",
            name="ck_generation_quality_candidates_idempotency",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "base_artifact_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "base_dataset_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "base_source_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "base_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "root_set_seal ~ '^hmac-sha256:[a-f0-9]{64}$'",
            name="ck_generation_quality_candidates_digests",
        ),
        sa.CheckConstraint(
            "capture_environment IN ('staging', 'production') AND "
            "char_length(btrim(capture_revision)) > 0 AND "
            "char_length(btrim(privacy_policy_version)) > 0 AND "
            "privacy_scan_status = 'CLEAN'",
            name="ck_generation_quality_candidates_capture_privacy",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object' AND "
            "jsonb_typeof(declared_counts) = 'object' AND "
            "jsonb_typeof(classification_distribution) = 'object' AND "
            "snapshot ->> 'artifactKind' = "
            "'AUTOMATIC_GENERATION_QUALITY_EVALUATION_INPUT' AND "
            "snapshot ->> 'profile' = 'CANDIDATE' AND "
            "snapshot #>> '{dataset,kind}' = 'OPERATIONAL_GOLDEN' AND "
            "snapshot #>> '{dataset,synthetic}' = 'false' AND "
            "snapshot #>> '{dataset,review,status}' = 'UNREVIEWED' AND "
            "snapshot #>> '{liveGate,status}' = 'NOT_RUN'",
            name="ck_generation_quality_candidates_snapshot_boundary",
        ),
    )
    op.create_index(
        "ix_generation_quality_candidates_workspace_created",
        "generation_quality_candidates",
        ["workspace_id", "created_at", "id"],
    )

    op.create_table(
        "generation_quality_candidate_roots",
        _id(),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column(
            "root_job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generation_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "workspace_id"],
            [
                "generation_quality_candidates.id",
                "generation_quality_candidates.workspace_id",
            ],
            name="fk_generation_quality_candidate_roots_candidate_workspace",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "position",
            name="uq_generation_quality_candidate_roots_position",
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "root_job_id",
            name="uq_generation_quality_candidate_roots_root",
        ),
        sa.CheckConstraint("position >= 0", name="ck_generation_quality_candidate_roots_position"),
    )
    op.create_index(
        "ix_generation_quality_candidate_roots_private_root",
        "generation_quality_candidate_roots",
        ["root_job_id"],
    )

    op.create_table(
        "generation_quality_review_decisions",
        _id(),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("observed_base_snapshot_fingerprint", sa.String(71), nullable=False),
        sa.Column("decision_digest", sa.String(71), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "workspace_id"],
            [
                "generation_quality_candidates.id",
                "generation_quality_candidates.workspace_id",
            ],
            name="fk_generation_quality_review_decisions_candidate_workspace",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "candidate_id",
            "workspace_id",
            name="uq_generation_quality_review_decisions_identity",
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "principal_id",
            name="uq_generation_quality_review_decisions_candidate_principal",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "principal_id",
            "idempotency_key",
            name="uq_generation_quality_review_decisions_principal_idempotency",
        ),
        sa.CheckConstraint(
            "principal_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,127}$' AND "
            "idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,254}$'",
            name="ck_generation_quality_review_decisions_identifiers",
        ),
        sa.CheckConstraint(
            "decision IN ('APPROVE', 'REJECT') AND "
            "((decision = 'APPROVE' AND reason_code IS NULL) OR "
            "(decision = 'REJECT' AND reason_code IN ("
            "'QUALITY_EVIDENCE_INSUFFICIENT', 'PRIVACY_FENCE_FAILED', "
            "'SOURCE_DRIFT', 'TAMPER_DETECTED', 'OTHER_POLICY_FAILURE'))) ",
            name="ck_generation_quality_review_decisions_shape",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "observed_base_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "decision_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_generation_quality_review_decisions_digests",
        ),
    )
    op.create_index(
        "ix_generation_quality_review_decisions_candidate_time",
        "generation_quality_review_decisions",
        ["candidate_id", "decided_at", "id"],
    )

    op.create_table(
        "generation_quality_approvals",
        _id(),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", UUID(as_uuid=True), nullable=False),
        sa.Column("decision_one_id", UUID(as_uuid=True), nullable=False),
        sa.Column("decision_two_id", UUID(as_uuid=True), nullable=False),
        sa.Column("approver_one_principal_id", sa.String(128), nullable=False),
        sa.Column("approver_two_principal_id", sa.String(128), nullable=False),
        sa.Column("protected_base_artifact_digest", sa.String(71), nullable=False),
        sa.Column("protected_base_snapshot_fingerprint", sa.String(71), nullable=False),
        sa.Column("review_authority_digest", sa.String(71), nullable=False),
        sa.Column("approval_artifact_digest", sa.String(71), nullable=False),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "workspace_id"],
            [
                "generation_quality_candidates.id",
                "generation_quality_candidates.workspace_id",
            ],
            name="fk_generation_quality_approvals_candidate_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_one_id", "candidate_id", "workspace_id"],
            [
                "generation_quality_review_decisions.id",
                "generation_quality_review_decisions.candidate_id",
                "generation_quality_review_decisions.workspace_id",
            ],
            name="fk_generation_quality_approvals_decision_one",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_two_id", "candidate_id", "workspace_id"],
            [
                "generation_quality_review_decisions.id",
                "generation_quality_review_decisions.candidate_id",
                "generation_quality_review_decisions.workspace_id",
            ],
            name="fk_generation_quality_approvals_decision_two",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("candidate_id", name="uq_generation_quality_approvals_candidate"),
        sa.CheckConstraint(
            "decision_one_id <> decision_two_id AND "
            "approver_one_principal_id <> approver_two_principal_id",
            name="ck_generation_quality_approvals_two_person",
        ),
        sa.CheckConstraint(
            "protected_base_artifact_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "protected_base_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
            "review_authority_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "approval_artifact_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_generation_quality_approvals_digests",
        ),
    )

    op.execute(
        """
        CREATE FUNCTION tc_reject_generation_quality_ledger_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'generation quality review ledger is append-only'
            USING ERRCODE = '23514';
        END;
        $$
        """
    )
    for table in (
        "generation_quality_candidates",
        "generation_quality_candidate_roots",
        "generation_quality_review_decisions",
        "generation_quality_approvals",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION tc_reject_generation_quality_ledger_mutation()
            """
        )

    op.execute(
        """
        CREATE FUNCTION tc_guard_generation_quality_candidate_root()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
              FROM generation_jobs AS job
              JOIN slack_work_items AS item ON item.id = job.slack_work_item_id
             WHERE job.id = NEW.root_job_id
               AND job.root_job_id = job.id
               AND item.workspace_id = NEW.workspace_id
          ) THEN
            RAISE EXCEPTION 'quality candidate root is not an owned root job'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_generation_quality_candidate_roots_guard
        BEFORE INSERT ON generation_quality_candidate_roots
        FOR EACH ROW EXECUTE FUNCTION tc_guard_generation_quality_candidate_root()
        """
    )

    op.execute(
        """
        CREATE FUNCTION tc_guard_generation_quality_review_decision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          expected_snapshot text;
        BEGIN
          SELECT base_snapshot_fingerprint
            INTO expected_snapshot
            FROM generation_quality_candidates
           WHERE id = NEW.candidate_id AND workspace_id = NEW.workspace_id;
          IF expected_snapshot IS NULL OR expected_snapshot <> NEW.observed_base_snapshot_fingerprint THEN
            RAISE EXCEPTION 'quality review snapshot fence mismatch'
              USING ERRCODE = '23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM generation_quality_candidate_roots
             WHERE candidate_id = NEW.candidate_id AND workspace_id = NEW.workspace_id
          ) THEN
            RAISE EXCEPTION 'rootless quality candidate is not reviewable'
              USING ERRCODE = '23514';
          END IF;
          IF EXISTS (
            SELECT 1 FROM generation_quality_approvals
             WHERE candidate_id = NEW.candidate_id
          ) OR EXISTS (
            SELECT 1 FROM generation_quality_review_decisions
             WHERE candidate_id = NEW.candidate_id AND decision = 'REJECT'
          ) THEN
            RAISE EXCEPTION 'quality review decision is terminal'
              USING ERRCODE = '23514';
          END IF;
          IF (
            SELECT count(*) FROM generation_quality_review_decisions
             WHERE candidate_id = NEW.candidate_id AND decision = 'APPROVE'
          ) >= 2 THEN
            RAISE EXCEPTION 'quality candidate already has two approvals'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_generation_quality_review_decisions_guard
        BEFORE INSERT ON generation_quality_review_decisions
        FOR EACH ROW EXECUTE FUNCTION tc_guard_generation_quality_review_decision()
        """
    )

    op.execute(
        """
        CREATE FUNCTION tc_guard_generation_quality_approval()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          one_row generation_quality_review_decisions%ROWTYPE;
          two_row generation_quality_review_decisions%ROWTYPE;
          candidate_row generation_quality_candidates%ROWTYPE;
        BEGIN
          SELECT * INTO candidate_row FROM generation_quality_candidates
           WHERE id = NEW.candidate_id AND workspace_id = NEW.workspace_id;
          SELECT * INTO one_row FROM generation_quality_review_decisions
           WHERE id = NEW.decision_one_id AND candidate_id = NEW.candidate_id
             AND workspace_id = NEW.workspace_id;
          SELECT * INTO two_row FROM generation_quality_review_decisions
           WHERE id = NEW.decision_two_id AND candidate_id = NEW.candidate_id
             AND workspace_id = NEW.workspace_id;
          IF candidate_row.id IS NULL OR one_row.id IS NULL OR two_row.id IS NULL
             OR one_row.decision <> 'APPROVE' OR two_row.decision <> 'APPROVE'
             OR one_row.principal_id <> NEW.approver_one_principal_id
             OR two_row.principal_id <> NEW.approver_two_principal_id
             OR one_row.principal_id = two_row.principal_id
             OR one_row.observed_base_snapshot_fingerprint
                <> NEW.protected_base_snapshot_fingerprint
             OR two_row.observed_base_snapshot_fingerprint
                <> NEW.protected_base_snapshot_fingerprint
             OR candidate_row.base_artifact_digest
                <> NEW.protected_base_artifact_digest
             OR candidate_row.base_snapshot_fingerprint
                <> NEW.protected_base_snapshot_fingerprint
             OR (SELECT count(*) FROM generation_quality_review_decisions
                  WHERE candidate_id = NEW.candidate_id AND decision = 'APPROVE') <> 2
             OR EXISTS (SELECT 1 FROM generation_quality_review_decisions
                         WHERE candidate_id = NEW.candidate_id AND decision = 'REJECT') THEN
            RAISE EXCEPTION 'quality approval authority mismatch'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_generation_quality_approvals_guard
        BEFORE INSERT ON generation_quality_approvals
        FOR EACH ROW EXECUTE FUNCTION tc_guard_generation_quality_approval()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_generation_quality_approvals_guard "
        "ON generation_quality_approvals"
    )
    op.execute("DROP FUNCTION IF EXISTS tc_guard_generation_quality_approval()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_generation_quality_review_decisions_guard "
        "ON generation_quality_review_decisions"
    )
    op.execute("DROP FUNCTION IF EXISTS tc_guard_generation_quality_review_decision()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_generation_quality_candidate_roots_guard "
        "ON generation_quality_candidate_roots"
    )
    op.execute("DROP FUNCTION IF EXISTS tc_guard_generation_quality_candidate_root()")
    op.drop_table("generation_quality_approvals")
    op.drop_index(
        "ix_generation_quality_review_decisions_candidate_time",
        table_name="generation_quality_review_decisions",
    )
    op.drop_table("generation_quality_review_decisions")
    op.drop_index(
        "ix_generation_quality_candidate_roots_private_root",
        table_name="generation_quality_candidate_roots",
    )
    op.drop_table("generation_quality_candidate_roots")
    op.drop_index(
        "ix_generation_quality_candidates_workspace_created",
        table_name="generation_quality_candidates",
    )
    op.drop_table("generation_quality_candidates")
    op.execute("DROP FUNCTION IF EXISTS tc_reject_generation_quality_ledger_mutation()")
