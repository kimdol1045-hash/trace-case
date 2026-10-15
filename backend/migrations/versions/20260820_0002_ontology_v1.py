"""Ontology v1 schema skeleton and pgvector embedding ledger.

Revision ID: 20260820_0002
Revises: 20260820_0001
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20260820_0002"
down_revision: str | None = "20260820_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_V1_ID = uuid.UUID("00000000-0000-7000-8000-000000000001")
NODE_TYPES = (
    "WORK_ITEM",
    "MENU",
    "FEATURE",
    "REQUIREMENT",
    "DECISION",
    "CONSTRAINT",
    "OPEN_QUESTION",
    "RISK",
    "TEST_CASE",
    "EXECUTION",
    "ISSUE",
    "RELEASE",
)
PREDICATES = (
    "AFFECTS",
    "TARGETS",
    "DISCUSSES",
    "DERIVED_FROM",
    "REFINES",
    "SUPERSEDES",
    "APPLIES_TO",
    "VERIFIES",
    "COVERS",
    "INSTANCE_OF",
    "FOUND_BY",
)
KNOWLEDGE_STATUSES = ("ACTIVE", "AMBIGUOUS", "SUPERSEDED", "RETRACTED")

SCHEMA_V1_DEFINITION = {
    "node_types": list(NODE_TYPES),
    "predicates": list(PREDICATES),
    "statuses": list(KNOWLEDGE_STATUSES),
    "temporal_fields": ["recorded_at", "valid_from", "valid_to"],
    "evidence_fields": [
        "source_type",
        "source_id",
        "source_revision",
        "permalink",
        "excerpt_hash",
    ],
    # Application validation can tighten these declarations without changing
    # the ledger shape. They are intentionally explicit rather than accepting
    # arbitrary node pairs from model output.
    "allowed_relations": {
        "AFFECTS": {
            "source": ["WORK_ITEM", "REQUIREMENT", "DECISION", "ISSUE", "RELEASE"],
            "target": ["MENU", "FEATURE", "REQUIREMENT", "TEST_CASE", "RELEASE"],
        },
        "TARGETS": {
            "source": ["WORK_ITEM", "REQUIREMENT", "RELEASE"],
            "target": ["MENU", "FEATURE", "REQUIREMENT"],
        },
        "DISCUSSES": {
            "source": ["WORK_ITEM"],
            "target": ["FEATURE", "REQUIREMENT", "DECISION", "CONSTRAINT", "OPEN_QUESTION", "RISK", "ISSUE"],
        },
        "DERIVED_FROM": {
            "source": ["REQUIREMENT", "DECISION", "CONSTRAINT", "OPEN_QUESTION", "RISK", "TEST_CASE", "ISSUE"],
            "target": ["WORK_ITEM", "REQUIREMENT", "DECISION", "TEST_CASE", "EXECUTION", "ISSUE"],
        },
        "REFINES": {
            "source": ["REQUIREMENT", "DECISION", "CONSTRAINT"],
            "target": ["REQUIREMENT", "DECISION", "CONSTRAINT"],
        },
        "SUPERSEDES": {
            "source": ["REQUIREMENT", "DECISION", "CONSTRAINT"],
            "target": ["REQUIREMENT", "DECISION", "CONSTRAINT"],
        },
        "APPLIES_TO": {
            "source": ["REQUIREMENT", "DECISION", "CONSTRAINT", "RISK"],
            "target": ["MENU", "FEATURE", "RELEASE"],
        },
        "VERIFIES": {
            "source": ["TEST_CASE", "EXECUTION"],
            "target": ["FEATURE", "REQUIREMENT", "DECISION", "CONSTRAINT", "TEST_CASE"],
        },
        "COVERS": {
            "source": ["TEST_CASE", "RELEASE"],
            "target": ["FEATURE", "REQUIREMENT", "RISK", "TEST_CASE"],
        },
        "INSTANCE_OF": {
            "source": ["EXECUTION", "FEATURE"],
            "target": ["TEST_CASE", "MENU"],
        },
        "FOUND_BY": {
            "source": ["ISSUE", "RISK"],
            "target": ["EXECUTION", "TEST_CASE"],
        },
    },
}


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "ontology_schemas",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_ontology_schemas_version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_ontology_schemas"),
        sa.UniqueConstraint("version", name="uq_ontology_schemas_version"),
    )
    op.create_index(
        "uq_ontology_schemas_single_active",
        "ontology_schemas",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "knowledge_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ontology_schema_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("properties", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"node_type IN ({_sql_values(NODE_TYPES)})", name="ck_knowledge_nodes_node_type_allowed"),
        sa.CheckConstraint(f"status IN ({_sql_values(KNOWLEDGE_STATUSES)})", name="ck_knowledge_nodes_status_allowed"),
        sa.CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name="ck_knowledge_nodes_valid_window"),
        sa.ForeignKeyConstraint(["ontology_schema_id"], ["ontology_schemas.id"], name="fk_knowledge_nodes_ontology_schema_id_ontology_schemas", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_nodes"),
    )
    op.create_index(
        "ix_knowledge_nodes_type_status_entity",
        "knowledge_nodes",
        ["node_type", "status", "entity_type", "entity_id"],
    )

    op.create_table(
        "knowledge_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ontology_schema_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("predicate", sa.String(length=40), nullable=False),
        sa.Column("target_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("properties", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"predicate IN ({_sql_values(PREDICATES)})", name="ck_knowledge_edges_predicate_allowed"),
        sa.CheckConstraint(f"status IN ({_sql_values(KNOWLEDGE_STATUSES)})", name="ck_knowledge_edges_status_allowed"),
        sa.CheckConstraint("source_node_id <> target_node_id", name="ck_knowledge_edges_different_nodes"),
        sa.CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name="ck_knowledge_edges_valid_window"),
        sa.ForeignKeyConstraint(["ontology_schema_id"], ["ontology_schemas.id"], name="fk_knowledge_edges_ontology_schema_id_ontology_schemas", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_node_id"], ["knowledge_nodes.id"], name="fk_knowledge_edges_source_node_id_knowledge_nodes", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_node_id"], ["knowledge_nodes.id"], name="fk_knowledge_edges_target_node_id_knowledge_nodes", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_edges"),
    )
    op.create_index(
        "ix_knowledge_edges_source_predicate_target",
        "knowledge_edges",
        ["source_node_id", "predicate", "target_node_id"],
    )

    op.create_table(
        "knowledge_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ontology_schema_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("knowledge_edge_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_revision", sa.String(length=100), nullable=True),
        sa.Column("permalink", sa.Text(), nullable=True),
        sa.Column("excerpt_hash", sa.String(length=128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(knowledge_node_id IS NOT NULL AND knowledge_edge_id IS NULL) OR "
            "(knowledge_node_id IS NULL AND knowledge_edge_id IS NOT NULL)",
            name="ck_knowledge_evidence_exactly_one_owner",
        ),
        sa.ForeignKeyConstraint(["ontology_schema_id"], ["ontology_schemas.id"], name="fk_knowledge_evidence_ontology_schema_id_ontology_schemas", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["knowledge_edge_id"], ["knowledge_edges.id"], name="fk_knowledge_evidence_knowledge_edge_id_knowledge_edges", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_node_id"], ["knowledge_nodes.id"], name="fk_knowledge_evidence_knowledge_node_id_knowledge_nodes", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_evidence"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "source_revision",
            "knowledge_node_id",
            "knowledge_edge_id",
            name="uq_knowledge_evidence_source_owner",
        ),
    )
    op.create_index(
        "ix_knowledge_evidence_source",
        "knowledge_evidence",
        ["source_type", "source_id", "source_revision"],
    )

    op.create_table(
        "knowledge_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_type", sa.String(length=50), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("embedding", Vector(dim=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_embeddings"),
        sa.UniqueConstraint(
            "owner_type",
            "owner_id",
            "model_version",
            "content_hash",
            name="uq_knowledge_embeddings_owner_model_content",
        ),
    )
    op.create_index(
        "ix_knowledge_embeddings_owner",
        "knowledge_embeddings",
        ["owner_type", "owner_id"],
    )

    # A migration-owned constant is safely escaped here so both online and
    # offline (``alembic --sql``) migration modes can render JSONB.
    definition_json = json.dumps(
        SCHEMA_V1_DEFINITION,
        ensure_ascii=True,
        separators=(",", ":"),
    ).replace("'", "''")
    op.execute(
        sa.text(
            "INSERT INTO ontology_schemas (id, version, name, definition, active) "
            f"VALUES ('{SCHEMA_V1_ID}'::uuid, 1, 'qa-ontology-v1', "
            f"'{definition_json}'::jsonb, true)"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_embeddings_owner", table_name="knowledge_embeddings")
    op.drop_table("knowledge_embeddings")
    op.drop_index("ix_knowledge_evidence_source", table_name="knowledge_evidence")
    op.drop_table("knowledge_evidence")
    op.drop_index("ix_knowledge_edges_source_predicate_target", table_name="knowledge_edges")
    op.drop_table("knowledge_edges")
    op.drop_index("ix_knowledge_nodes_type_status_entity", table_name="knowledge_nodes")
    op.drop_table("knowledge_nodes")
    op.drop_index("uq_ontology_schemas_single_active", table_name="ontology_schemas")
    op.drop_table("ontology_schemas")
