"""Apply the E4 privacy envelope and database reviewer-authority boundary.

Revision ID: 20260901_0029
Revises: 20260901_0028
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20260901_0029"
down_revision: str | None = "20260901_0028"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # An old row has no trustworthy E4 envelope and cannot be upgraded without
    # the external HMAC key and authoritative source snapshot.  Fail closed
    # instead of manufacturing a CLEAN envelope in a migration.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM generation_quality_candidates) THEN
            RAISE EXCEPTION
              'legacy generation quality candidates require explicit re-import under E4';
          END IF;
        END;
        $$
        """
    )
    op.add_column(
        "generation_quality_candidates",
        sa.Column("review_envelope", JSONB, nullable=False),
    )
    op.add_column(
        "generation_quality_candidates",
        sa.Column("envelope_payload_fingerprint", sa.String(71), nullable=False),
    )
    op.add_column(
        "generation_quality_candidates",
        sa.Column("envelope_artifact_fingerprint", sa.String(71), nullable=False),
    )
    op.drop_constraint(
        "ck_generation_quality_candidates_key",
        "generation_quality_candidates",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_quality_candidates_digests",
        "generation_quality_candidates",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_quality_candidates_snapshot_boundary",
        "generation_quality_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_generation_quality_candidates_key",
        "generation_quality_candidates",
        "candidate_key ~ '^review-candidate:[a-f0-9]{64}$'",
    )
    op.create_check_constraint(
        "ck_generation_quality_candidates_digests",
        "generation_quality_candidates",
        "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
        "base_artifact_digest ~ '^sha256:[a-f0-9]{64}$' AND "
        "base_dataset_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
        "base_source_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
        "base_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
        "root_set_seal ~ '^hmac-sha256:[a-f0-9]{64}$' AND "
        "envelope_payload_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
        "envelope_artifact_fingerprint ~ '^sha256:[a-f0-9]{64}$'",
    )
    op.create_check_constraint(
        "ck_generation_quality_candidates_snapshot_boundary",
        "generation_quality_candidates",
        "jsonb_typeof(snapshot) = 'object' AND "
        "jsonb_typeof(declared_counts) = 'object' AND "
        "jsonb_typeof(classification_distribution) = 'object' AND "
        "snapshot ->> 'artifactKind' = "
        "'AUTOMATIC_GENERATION_QUALITY_EVALUATION_INPUT' AND "
        "snapshot ->> 'profile' = 'CANDIDATE' AND "
        "snapshot #>> '{dataset,kind}' = 'OPERATIONAL_GOLDEN' AND "
        "snapshot #>> '{dataset,synthetic}' = 'false' AND "
        "snapshot #>> '{dataset,review,status}' = 'UNREVIEWED' AND "
        "snapshot #>> '{liveGate,status}' = 'NOT_RUN' AND "
        "jsonb_typeof(review_envelope) = 'object' AND "
        "review_envelope ->> 'artifactKind' = 'REVIEW_CANDIDATE' AND "
        "review_envelope ->> 'contractVersion' = "
        "'review-candidate-envelope/1.0.0' AND "
        "review_envelope #>> '{candidate,candidateKey}' = candidate_key AND "
        "review_envelope #>> '{fingerprints,payloadFingerprint}' = "
        "envelope_payload_fingerprint AND "
        "review_envelope #>> '{fingerprints,artifactFingerprint}' = "
        "envelope_artifact_fingerprint AND "
        "review_envelope #>> '{sanitization,status}' = 'CLEAN' AND "
        "review_envelope #>> '{review,status}' = 'UNREVIEWED' AND "
        "review_envelope #>> '{attestation,status}' = 'NOT_RUN' AND "
        "review_envelope #>> '{certification,status}' = 'NOT_CERTIFIED'",
    )

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        CREATE FUNCTION tc_quality_review_digest(p_domain text, p_parts text[])
        RETURNS text
        LANGUAGE plpgsql
        IMMUTABLE STRICT
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
          accumulator bytea := convert_to(p_domain, 'UTF8');
          encoded bytea;
          part text;
        BEGIN
          FOREACH part IN ARRAY p_parts LOOP
            IF part IS NULL THEN
              accumulator := accumulator || int8send(-1);
            ELSE
              encoded := convert_to(part, 'UTF8');
              accumulator := accumulator || int8send(octet_length(encoded)) || encoded;
            END IF;
          END LOOP;
          RETURN 'sha256:' || encode(public.digest(accumulator, 'sha256'), 'hex');
        END;
        $$
        """
    )

    op.create_table(
        "generation_quality_reviewer_authorities",
        sa.Column("principal_id", sa.String(128), primary_key=True),
        sa.Column("credential_hash", sa.String(71), nullable=False, unique=True),
        sa.Column("authority_digest", sa.String(71), nullable=False, unique=True),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "principal_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,127}$' AND "
            "credential_hash ~ '^sha256:[a-f0-9]{64}$' AND "
            "authority_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_generation_quality_reviewer_authorities_shape",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION tc_guard_generation_quality_reviewer_authority()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
          IF NEW.authority_digest <> tc_quality_review_digest(
            'reviewer-authority', ARRAY[NEW.principal_id, NEW.credential_hash, 'ACTIVE']
          ) THEN
            RAISE EXCEPTION 'quality reviewer authority integrity mismatch'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_generation_quality_reviewer_authorities_guard
        BEFORE INSERT ON generation_quality_reviewer_authorities
        FOR EACH ROW EXECUTE FUNCTION tc_guard_generation_quality_reviewer_authority()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_generation_quality_reviewer_authorities_immutable
        BEFORE UPDATE OR DELETE ON generation_quality_reviewer_authorities
        FOR EACH ROW EXECUTE FUNCTION tc_reject_generation_quality_ledger_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION tc_install_generation_quality_reviewer(
          p_principal_id text, p_credential_hash text
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
          INSERT INTO generation_quality_reviewer_authorities (
            principal_id, credential_hash, authority_digest
          ) VALUES (
            p_principal_id,
            p_credential_hash,
            tc_quality_review_digest(
              'reviewer-authority', ARRAY[p_principal_id, p_credential_hash, 'ACTIVE']
            )
          );
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION tc_guard_generation_quality_review_decision()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
          candidate_row generation_quality_candidates%ROWTYPE;
          expected_request_digest text;
          expected_decision_digest text;
        BEGIN
          SELECT * INTO candidate_row
            FROM generation_quality_candidates
           WHERE id = NEW.candidate_id AND workspace_id = NEW.workspace_id;
          IF candidate_row.id IS NULL
             OR candidate_row.envelope_artifact_fingerprint
                <> NEW.observed_base_snapshot_fingerprint THEN
            RAISE EXCEPTION 'quality review snapshot fence mismatch'
              USING ERRCODE = '23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM generation_quality_reviewer_authorities
             WHERE principal_id = NEW.principal_id
          ) THEN
            RAISE EXCEPTION 'reviewer authority is not installed'
              USING ERRCODE = '42501';
          END IF;
          expected_request_digest := tc_quality_review_digest(
            'decision-request', ARRAY[
              candidate_row.candidate_key, NEW.decision, NEW.reason_code,
              NEW.observed_base_snapshot_fingerprint
            ]
          );
          expected_decision_digest := tc_quality_review_digest(
            'decision', ARRAY[
              candidate_row.candidate_key, NEW.principal_id, NEW.decision,
              NEW.reason_code, NEW.observed_base_snapshot_fingerprint
            ]
          );
          IF NEW.request_digest <> expected_request_digest
             OR NEW.decision_digest <> expected_decision_digest THEN
            RAISE EXCEPTION 'quality review decision integrity mismatch'
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
        CREATE OR REPLACE FUNCTION tc_guard_generation_quality_approval()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
          one_row generation_quality_review_decisions%ROWTYPE;
          two_row generation_quality_review_decisions%ROWTYPE;
          candidate_row generation_quality_candidates%ROWTYPE;
          expected_authority text;
          expected_approval text;
        BEGIN
          SELECT * INTO candidate_row FROM generation_quality_candidates
           WHERE id = NEW.candidate_id AND workspace_id = NEW.workspace_id;
          SELECT * INTO one_row FROM generation_quality_review_decisions
           WHERE id = NEW.decision_one_id AND candidate_id = NEW.candidate_id
             AND workspace_id = NEW.workspace_id;
          SELECT * INTO two_row FROM generation_quality_review_decisions
           WHERE id = NEW.decision_two_id AND candidate_id = NEW.candidate_id
             AND workspace_id = NEW.workspace_id;
          expected_authority := tc_quality_review_digest(
            'approval-authority', ARRAY[
              candidate_row.candidate_key,
              candidate_row.envelope_artifact_fingerprint,
              least(one_row.decision_digest, two_row.decision_digest),
              greatest(one_row.decision_digest, two_row.decision_digest),
              'automatic-generation-quality-review-v1'
            ]
          );
          expected_approval := tc_quality_review_digest(
            'approval-artifact', ARRAY[
              candidate_row.base_artifact_digest,
              expected_authority,
              'REVIEWED_NOT_RELEASE_CERTIFIED'
            ]
          );
          IF candidate_row.id IS NULL OR one_row.id IS NULL OR two_row.id IS NULL
             OR one_row.decision <> 'APPROVE' OR two_row.decision <> 'APPROVE'
             OR one_row.principal_id <> NEW.approver_one_principal_id
             OR two_row.principal_id <> NEW.approver_two_principal_id
             OR one_row.principal_id = two_row.principal_id
             OR NOT EXISTS (SELECT 1 FROM generation_quality_reviewer_authorities
                             WHERE principal_id = one_row.principal_id)
             OR NOT EXISTS (SELECT 1 FROM generation_quality_reviewer_authorities
                             WHERE principal_id = two_row.principal_id)
             OR one_row.observed_base_snapshot_fingerprint
                <> NEW.protected_base_snapshot_fingerprint
             OR two_row.observed_base_snapshot_fingerprint
                <> NEW.protected_base_snapshot_fingerprint
             OR candidate_row.base_artifact_digest
                <> NEW.protected_base_artifact_digest
             OR candidate_row.envelope_artifact_fingerprint
                <> NEW.protected_base_snapshot_fingerprint
             OR NEW.review_authority_digest <> expected_authority
             OR NEW.approval_artifact_digest <> expected_approval
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
        CREATE FUNCTION tc_record_generation_quality_decision(
          p_workspace_id uuid,
          p_candidate_id uuid,
          p_principal_id text,
          p_credential_hash text,
          p_decision text,
          p_reason_code text,
          p_idempotency_key text,
          p_expected_artifact_fingerprint text,
          p_request_digest text,
          p_decision_digest text
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
          candidate_row generation_quality_candidates%ROWTYPE;
          replay_row generation_quality_review_decisions%ROWTYPE;
          decision_row generation_quality_review_decisions%ROWTYPE;
          first_row generation_quality_review_decisions%ROWTYPE;
          second_row generation_quality_review_decisions%ROWTYPE;
          expected_request text;
          expected_decision text;
          approval_authority_digest text;
          approval_digest text;
        BEGIN
          SELECT * INTO candidate_row FROM generation_quality_candidates
           WHERE id = p_candidate_id AND workspace_id = p_workspace_id
           FOR UPDATE;
          IF candidate_row.id IS NULL THEN
            RAISE EXCEPTION 'quality review candidate not found' USING ERRCODE = 'P0002';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM generation_quality_reviewer_authorities AS reviewer_authority
             WHERE reviewer_authority.principal_id = p_principal_id
               AND reviewer_authority.credential_hash = p_credential_hash
               AND reviewer_authority.authority_digest = tc_quality_review_digest(
                 'reviewer-authority', ARRAY[p_principal_id, p_credential_hash, 'ACTIVE']
               )
          ) THEN
            RAISE EXCEPTION 'reviewer authority is not installed' USING ERRCODE = '42501';
          END IF;
          expected_request := tc_quality_review_digest(
            'decision-request', ARRAY[
              candidate_row.candidate_key, p_decision, p_reason_code,
              p_expected_artifact_fingerprint
            ]
          );
          expected_decision := tc_quality_review_digest(
            'decision', ARRAY[
              candidate_row.candidate_key, p_principal_id, p_decision,
              p_reason_code, p_expected_artifact_fingerprint
            ]
          );
          IF p_expected_artifact_fingerprint
               <> candidate_row.envelope_artifact_fingerprint
             OR p_request_digest <> expected_request
             OR p_decision_digest <> expected_decision THEN
            RAISE EXCEPTION 'quality review decision integrity mismatch'
              USING ERRCODE = '23514';
          END IF;
          SELECT * INTO replay_row FROM generation_quality_review_decisions
           WHERE workspace_id = p_workspace_id
             AND principal_id = p_principal_id
             AND idempotency_key = p_idempotency_key;
          IF replay_row.id IS NOT NULL THEN
            IF replay_row.candidate_id <> p_candidate_id
               OR replay_row.request_digest <> expected_request THEN
              RAISE EXCEPTION 'quality review decision idempotency conflict'
                USING ERRCODE = '23505';
            END IF;
            RETURN replay_row.id;
          END IF;
          INSERT INTO generation_quality_review_decisions (
            workspace_id, candidate_id, principal_id, decision, reason_code,
            idempotency_key, request_digest, observed_base_snapshot_fingerprint,
            decision_digest
          ) VALUES (
            p_workspace_id, p_candidate_id, p_principal_id, p_decision, p_reason_code,
            p_idempotency_key, expected_request, p_expected_artifact_fingerprint,
            expected_decision
          ) RETURNING * INTO decision_row;
          IF p_decision = 'APPROVE' AND (
            SELECT count(*) FROM generation_quality_review_decisions
             WHERE candidate_id = p_candidate_id AND decision = 'APPROVE'
          ) = 2 THEN
            SELECT * INTO first_row FROM generation_quality_review_decisions
             WHERE candidate_id = p_candidate_id AND decision = 'APPROVE'
             ORDER BY principal_id LIMIT 1;
            SELECT * INTO second_row FROM generation_quality_review_decisions
             WHERE candidate_id = p_candidate_id AND decision = 'APPROVE'
             ORDER BY principal_id OFFSET 1 LIMIT 1;
            approval_authority_digest := tc_quality_review_digest(
              'approval-authority', ARRAY[
                candidate_row.candidate_key,
                candidate_row.envelope_artifact_fingerprint,
                least(first_row.decision_digest, second_row.decision_digest),
                greatest(first_row.decision_digest, second_row.decision_digest),
                'automatic-generation-quality-review-v1'
              ]
            );
            approval_digest := tc_quality_review_digest(
              'approval-artifact', ARRAY[
                candidate_row.base_artifact_digest,
                approval_authority_digest,
                'REVIEWED_NOT_RELEASE_CERTIFIED'
              ]
            );
            INSERT INTO generation_quality_approvals (
              workspace_id, candidate_id, decision_one_id, decision_two_id,
              approver_one_principal_id, approver_two_principal_id,
              protected_base_artifact_digest, protected_base_snapshot_fingerprint,
              review_authority_digest, approval_artifact_digest
            ) VALUES (
              p_workspace_id, p_candidate_id, first_row.id, second_row.id,
              first_row.principal_id, second_row.principal_id,
              candidate_row.base_artifact_digest,
              candidate_row.envelope_artifact_fingerprint,
              approval_authority_digest, approval_digest
            );
          END IF;
          RETURN decision_row.id;
        END;
        $$
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tc_quality_review_writer') THEN
            CREATE ROLE tc_quality_review_writer NOLOGIN NOSUPERUSER NOCREATEDB
              NOCREATEROLE NOINHERIT NOREPLICATION;
          END IF;
          EXECUTE format('GRANT tc_quality_review_writer TO %I', current_user);
        END;
        $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO tc_quality_review_writer")
    op.execute(
        "GRANT SELECT ON generation_quality_candidates, generation_quality_candidate_roots, "
        "generation_quality_review_decisions, generation_quality_approvals "
        "TO tc_quality_review_writer"
    )
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE ON generation_quality_review_decisions, "
        "generation_quality_approvals FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON generation_quality_reviewer_authorities FROM PUBLIC, "
        "tc_quality_review_writer"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION tc_install_generation_quality_reviewer(text, text) FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION tc_record_generation_quality_decision("
        "uuid, uuid, text, text, text, text, text, text, text, text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION tc_record_generation_quality_decision("
        "uuid, uuid, text, text, text, text, text, text, text, text) "
        "TO tc_quality_review_writer"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE EXECUTE ON FUNCTION tc_record_generation_quality_decision("
        "uuid, uuid, text, text, text, text, text, text, text, text) "
        "FROM tc_quality_review_writer"
    )
    op.execute(
        "REVOKE SELECT ON generation_quality_candidates, generation_quality_candidate_roots, "
        "generation_quality_review_decisions, generation_quality_approvals "
        "FROM tc_quality_review_writer"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM tc_quality_review_writer")
    op.execute(
        "DROP FUNCTION tc_record_generation_quality_decision("
        "uuid, uuid, text, text, text, text, text, text, text, text)"
    )
    op.execute("DROP FUNCTION tc_install_generation_quality_reviewer(text, text)")
    op.execute(
        "DROP TRIGGER trg_generation_quality_reviewer_authorities_immutable "
        "ON generation_quality_reviewer_authorities"
    )
    op.execute(
        "DROP TRIGGER trg_generation_quality_reviewer_authorities_guard "
        "ON generation_quality_reviewer_authorities"
    )
    op.execute("DROP FUNCTION tc_guard_generation_quality_reviewer_authority()")
    op.drop_table("generation_quality_reviewer_authorities")

    # The prior migration's guard functions are intentionally restored for a
    # true schema downgrade.  Downgrade is rejected when new-envelope rows
    # exist because their candidate keys cannot satisfy the legacy contract.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM generation_quality_candidates) THEN
            RAISE EXCEPTION
              'cannot downgrade generation quality E4 envelope rows to legacy contract';
          END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tc_guard_generation_quality_review_decision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected_snapshot text;
        BEGIN
          SELECT base_snapshot_fingerprint INTO expected_snapshot
            FROM generation_quality_candidates
           WHERE id = NEW.candidate_id AND workspace_id = NEW.workspace_id;
          IF expected_snapshot IS NULL OR expected_snapshot
             <> NEW.observed_base_snapshot_fingerprint THEN
            RAISE EXCEPTION 'quality review snapshot fence mismatch' USING ERRCODE = '23514';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM generation_quality_candidate_roots
                          WHERE candidate_id = NEW.candidate_id
                            AND workspace_id = NEW.workspace_id) THEN
            RAISE EXCEPTION 'rootless quality candidate is not reviewable'
              USING ERRCODE = '23514';
          END IF;
          IF EXISTS (SELECT 1 FROM generation_quality_approvals
                      WHERE candidate_id = NEW.candidate_id)
             OR EXISTS (SELECT 1 FROM generation_quality_review_decisions
                         WHERE candidate_id = NEW.candidate_id AND decision = 'REJECT') THEN
            RAISE EXCEPTION 'quality review decision is terminal' USING ERRCODE = '23514';
          END IF;
          IF (SELECT count(*) FROM generation_quality_review_decisions
               WHERE candidate_id = NEW.candidate_id AND decision = 'APPROVE') >= 2 THEN
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
        CREATE OR REPLACE FUNCTION tc_guard_generation_quality_approval()
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
             OR candidate_row.base_artifact_digest <> NEW.protected_base_artifact_digest
             OR candidate_row.base_snapshot_fingerprint
                <> NEW.protected_base_snapshot_fingerprint
             OR (SELECT count(*) FROM generation_quality_review_decisions
                  WHERE candidate_id = NEW.candidate_id AND decision = 'APPROVE') <> 2
             OR EXISTS (SELECT 1 FROM generation_quality_review_decisions
                         WHERE candidate_id = NEW.candidate_id AND decision = 'REJECT') THEN
            RAISE EXCEPTION 'quality approval authority mismatch' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute("DROP FUNCTION tc_quality_review_digest(text, text[])")

    op.drop_constraint(
        "ck_generation_quality_candidates_snapshot_boundary",
        "generation_quality_candidates",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_quality_candidates_digests",
        "generation_quality_candidates",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_quality_candidates_key",
        "generation_quality_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_generation_quality_candidates_key",
        "generation_quality_candidates",
        "candidate_key ~ '^quality-review-candidate:[a-f0-9]{64}$'",
    )
    op.create_check_constraint(
        "ck_generation_quality_candidates_digests",
        "generation_quality_candidates",
        "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
        "base_artifact_digest ~ '^sha256:[a-f0-9]{64}$' AND "
        "base_dataset_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
        "base_source_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
        "base_snapshot_fingerprint ~ '^sha256:[a-f0-9]{64}$' AND "
        "root_set_seal ~ '^hmac-sha256:[a-f0-9]{64}$'",
    )
    op.create_check_constraint(
        "ck_generation_quality_candidates_snapshot_boundary",
        "generation_quality_candidates",
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
    )
    op.drop_column("generation_quality_candidates", "envelope_artifact_fingerprint")
    op.drop_column("generation_quality_candidates", "envelope_payload_fingerprint")
    op.drop_column("generation_quality_candidates", "review_envelope")
