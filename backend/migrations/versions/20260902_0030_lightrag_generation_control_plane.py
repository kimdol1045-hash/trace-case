"""Add the durable LightRAG active-generation control plane.

Revision ID: 20260902_0030
Revises: 20260901_0029

This control plane deliberately has no foreign keys to, or triggers on, the
authoritative QA/knowledge ledger.  It owns only verified auxiliary-generation
metadata, the singleton routing pointer, and immutable transition evidence.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260902_0030"
down_revision: str | None = "20260901_0029"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "lightrag_generations",
        sa.Column("generation_id", sa.String(255), primary_key=True),
        sa.Column("provider_id", sa.String(128), nullable=False),
        sa.Column("workspace", sa.String(255), nullable=False),
        sa.Column("auxiliary_target_digest", sa.String(71), nullable=False),
        sa.Column("manifest_digest", sa.String(71), nullable=False),
        sa.Column("corpus_digest", sa.String(71), nullable=False),
        sa.Column("source_ledger_digest", sa.String(71), nullable=False),
        sa.Column("projection_digest", sa.String(71), nullable=False),
        sa.Column("pgvector_digest", sa.String(71), nullable=False),
        sa.Column("document_count", sa.Integer, nullable=False),
        sa.Column(
            "verification_status",
            sa.String(16),
            server_default=sa.text("'VERIFIED'"),
            nullable=False,
        ),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("recorded_by", sa.String(256), nullable=False),
        sa.Column("registration_idempotency_key", sa.String(256), nullable=False),
        sa.Column("registration_digest", sa.String(71), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "registration_idempotency_key",
            name="uq_lightrag_generations_registration_idempotency",
        ),
        sa.UniqueConstraint(
            "auxiliary_target_digest",
            "workspace",
            name="uq_lightrag_generations_target_workspace",
        ),
        sa.CheckConstraint(
            "generation_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,254}$' AND "
            "provider_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,127}$' AND "
            "workspace ~ '^[A-Za-z][A-Za-z0-9._:-]{0,254}$'",
            name="ck_lightrag_generations_identifiers",
        ),
        sa.CheckConstraint(
            "auxiliary_target_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "manifest_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "corpus_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "source_ledger_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "projection_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "pgvector_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "registration_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_lightrag_generations_digests",
        ),
        sa.CheckConstraint(
            "document_count > 0 AND verification_status = 'VERIFIED' AND "
            "jsonb_typeof(metadata) = 'object'",
            name="ck_lightrag_generations_verified_metadata",
        ),
        sa.CheckConstraint(
            "char_length(btrim(recorded_by)) > 0 AND "
            "char_length(btrim(registration_idempotency_key)) > 0",
            name="ck_lightrag_generations_registration_shape",
        ),
    )

    op.create_table(
        "lightrag_active_generation_pointer",
        sa.Column("scope", sa.String(16), primary_key=True),
        sa.Column(
            "pointer_version", sa.Integer, server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "active_generation_id",
            sa.String(255),
            sa.ForeignKey("lightrag_generations.generation_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope = 'GLOBAL'", name="ck_lightrag_active_generation_pointer_singleton"
        ),
        sa.CheckConstraint(
            "pointer_version >= 0 AND "
            "((pointer_version = 0 AND active_generation_id IS NULL) OR "
            "(pointer_version > 0 AND active_generation_id IS NOT NULL))",
            name="ck_lightrag_active_generation_pointer_shape",
        ),
    )
    op.execute(
        "INSERT INTO lightrag_active_generation_pointer "
        "(scope, pointer_version, active_generation_id) VALUES ('GLOBAL', 0, NULL)"
    )

    op.create_table(
        "lightrag_generation_transition_events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "sequence",
            sa.BigInteger,
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        # These intentionally are not foreign keys: rejected evidence must retain
        # an attempted unknown generation identifier.
        sa.Column("target_generation_id", sa.String(255), nullable=False),
        sa.Column("expected_generation_id", sa.String(255)),
        sa.Column("observed_generation_id", sa.String(255)),
        sa.Column("after_generation_id", sa.String(255)),
        sa.Column("expected_pointer_version", sa.Integer, nullable=False),
        sa.Column("observed_pointer_version", sa.Integer, nullable=False),
        sa.Column("committed_pointer_version", sa.Integer, nullable=False),
        sa.Column("actor_id", sa.String(256), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("receipt_digest", sa.String(71), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "sequence", name="uq_lightrag_generation_transition_events_sequence"
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_lightrag_generation_transition_events_idempotency",
        ),
        sa.CheckConstraint(
            "operation IN ('ACTIVATE', 'ROLLBACK') AND "
            "outcome IN ('APPLIED', 'REJECTED')",
            name="ck_lightrag_generation_transition_events_kind",
        ),
        sa.CheckConstraint(
            "target_generation_id ~ '^[A-Za-z][A-Za-z0-9._:-]{0,254}$' AND "
            "(expected_generation_id IS NULL OR expected_generation_id ~ "
            "'^[A-Za-z][A-Za-z0-9._:-]{0,254}$') AND "
            "(observed_generation_id IS NULL OR observed_generation_id ~ "
            "'^[A-Za-z][A-Za-z0-9._:-]{0,254}$') AND "
            "(after_generation_id IS NULL OR after_generation_id ~ "
            "'^[A-Za-z][A-Za-z0-9._:-]{0,254}$')",
            name="ck_lightrag_generation_transition_events_identifiers",
        ),
        sa.CheckConstraint(
            "expected_pointer_version >= 0 AND observed_pointer_version >= 0 AND "
            "committed_pointer_version >= 0",
            name="ck_lightrag_generation_transition_events_versions",
        ),
        sa.CheckConstraint(
            "(outcome = 'APPLIED' AND error_code IS NULL AND "
            "observed_generation_id IS NOT DISTINCT FROM expected_generation_id AND "
            "after_generation_id = target_generation_id AND "
            "committed_pointer_version = observed_pointer_version + 1) OR "
            "(outcome = 'REJECTED' AND error_code IS NOT NULL AND "
            "after_generation_id IS NOT DISTINCT FROM observed_generation_id AND "
            "committed_pointer_version = observed_pointer_version)",
            name="ck_lightrag_generation_transition_events_outcome",
        ),
        sa.CheckConstraint(
            "operation <> 'ROLLBACK' OR outcome <> 'APPLIED' OR "
            "target_generation_id IS DISTINCT FROM observed_generation_id",
            name="ck_lightrag_generation_transition_events_rollback_target",
        ),
        sa.CheckConstraint(
            "char_length(btrim(actor_id)) > 0 AND "
            "char_length(btrim(idempotency_key)) > 0 AND "
            "request_digest ~ '^sha256:[a-f0-9]{64}$' AND "
            "receipt_digest ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_lightrag_generation_transition_events_evidence",
        ),
    )
    op.create_index(
        "uq_lightrag_generation_transition_events_applied_version",
        "lightrag_generation_transition_events",
        ["committed_pointer_version"],
        unique=True,
        postgresql_where=sa.text("outcome = 'APPLIED'"),
    )
    op.create_index(
        "ix_lightrag_generation_transition_events_target_sequence",
        "lightrag_generation_transition_events",
        ["target_generation_id", "sequence"],
    )

    op.execute(
        """
        CREATE FUNCTION tc_lightrag_reject_immutable_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
            USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER trg_lightrag_generations_immutable
          BEFORE UPDATE OR DELETE ON lightrag_generations
          FOR EACH ROW EXECUTE FUNCTION tc_lightrag_reject_immutable_mutation();

        CREATE TRIGGER trg_lightrag_generation_transition_events_immutable
          BEFORE UPDATE OR DELETE ON lightrag_generation_transition_events
          FOR EACH ROW EXECUTE FUNCTION tc_lightrag_reject_immutable_mutation();

        CREATE FUNCTION tc_lightrag_guard_transition_event_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF current_setting('tc.lightrag_audit_write', true) IS DISTINCT FROM 'on' THEN
            RAISE EXCEPTION 'LightRAG transition evidence is repository-managed'
              USING ERRCODE = '42501';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_lightrag_generation_transition_events_insert_guard
          BEFORE INSERT ON lightrag_generation_transition_events
          FOR EACH ROW EXECUTE FUNCTION tc_lightrag_guard_transition_event_insert();

        CREATE FUNCTION tc_lightrag_guard_active_pointer()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'LightRAG active-generation pointer cannot be deleted'
              USING ERRCODE = '55000';
          END IF;
          IF current_setting('tc.lightrag_pointer_write', true) IS DISTINCT FROM 'on' THEN
            RAISE EXCEPTION 'LightRAG active-generation pointer is repository-managed'
              USING ERRCODE = '42501';
          END IF;
          IF NEW.scope IS DISTINCT FROM OLD.scope OR
             NEW.active_generation_id IS NOT DISTINCT FROM OLD.active_generation_id OR
             NEW.active_generation_id IS NULL OR
             NEW.pointer_version <> OLD.pointer_version + 1 THEN
            RAISE EXCEPTION 'invalid LightRAG active-generation transition'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_lightrag_active_generation_pointer_guard
          BEFORE UPDATE OR DELETE ON lightrag_active_generation_pointer
          FOR EACH ROW EXECUTE FUNCTION tc_lightrag_guard_active_pointer();
        """
    )


def downgrade() -> None:
    # Do not silently erase routing or audit evidence.  A pristine, never-used
    # control plane is the only state that can safely return to 0029.
    op.execute(
        """
        DO $$
        DECLARE
          pointer_count bigint;
          pointer_is_pristine boolean;
        BEGIN
          SELECT count(*),
                 bool_and(scope = 'GLOBAL' AND pointer_version = 0
                          AND active_generation_id IS NULL)
            INTO pointer_count, pointer_is_pristine
            FROM lightrag_active_generation_pointer;
          IF EXISTS (SELECT 1 FROM lightrag_generations) OR
             EXISTS (SELECT 1 FROM lightrag_generation_transition_events) OR
             pointer_count <> 1 OR pointer_is_pristine IS NOT TRUE THEN
            RAISE EXCEPTION
              'refusing to downgrade: LightRAG generation control-plane evidence exists'
              USING ERRCODE = '55000';
          END IF;
        END;
        $$;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_lightrag_active_generation_pointer_guard
          ON lightrag_active_generation_pointer;
        DROP FUNCTION IF EXISTS tc_lightrag_guard_active_pointer();
        DROP TRIGGER IF EXISTS trg_lightrag_generation_transition_events_immutable
          ON lightrag_generation_transition_events;
        DROP TRIGGER IF EXISTS trg_lightrag_generation_transition_events_insert_guard
          ON lightrag_generation_transition_events;
        DROP FUNCTION IF EXISTS tc_lightrag_guard_transition_event_insert();
        DROP TRIGGER IF EXISTS trg_lightrag_generations_immutable ON lightrag_generations;
        DROP FUNCTION IF EXISTS tc_lightrag_reject_immutable_mutation();
        """
    )
    op.drop_table("lightrag_generation_transition_events")
    op.drop_table("lightrag_active_generation_pointer")
    op.drop_table("lightrag_generations")
