from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from alembic.config import Config
from alembic.script import ScriptDirectory

from tc_backend.db import models  # noqa: F401
from tc_backend.db.base import Base
from tc_backend.knowledge.rebuild import (
    AUTHORITATIVE_LEDGER_TABLES,
    PROJECTION_RESET_TABLES,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE_MIGRATION = (
    BACKEND_ROOT
    / "migrations"
    / "versions"
    / "20260902_0030_lightrag_generation_control_plane.py"
)
CONTROL_PLANE_TABLES = {
    "lightrag_generations",
    "lightrag_active_generation_pointer",
    "lightrag_generation_transition_events",
}


class _RecordingOperations:
    def __init__(self) -> None:
        self.created_tables: list[str] = []
        self.dropped_tables: list[str] = []
        self.executed_sql: list[str] = []

    def create_table(self, name: str, *args: object, **kwargs: object) -> None:
        self.created_tables.append(name)

    def create_index(self, *args: object, **kwargs: object) -> None:
        return None

    def execute(self, statement: object) -> None:
        self.executed_sql.append(str(statement))

    def drop_table(self, name: str) -> None:
        self.dropped_tables.append(name)


def _load_control_plane_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "test_lightrag_generation_control_plane_migration",
        CONTROL_PLANE_MIGRATION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_alembic_has_single_expected_head() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260902_0030"]


def test_lightrag_control_plane_migration_isolated_from_qa_and_knowledge_tables() -> (
    None
):
    module = _load_control_plane_migration()
    operations = _RecordingOperations()
    module.op = operations

    module.upgrade()
    module.downgrade()

    assert set(operations.created_tables) == CONTROL_PLANE_TABLES
    assert set(operations.dropped_tables) == CONTROL_PLANE_TABLES
    migration_sql = "\n".join(operations.executed_sql)
    protected_tables = set(AUTHORITATIVE_LEDGER_TABLES) | set(PROJECTION_RESET_TABLES)
    assert not {
        table
        for table in protected_tables
        if re.search(rf"\b{re.escape(table)}\b", migration_sql)
    }


def test_metadata_contains_sprint_zero_and_one_tables() -> None:
    assert {
        "background_jobs",
        "ontology_schemas",
        "knowledge_nodes",
        "knowledge_edges",
        "knowledge_evidence",
        "knowledge_embeddings",
        "workspace_settings",
        "workspace_key_counters",
        "audit_logs",
        "api_idempotency_receipts",
        "folders",
        "folder_aliases",
        "work_items",
        "test_cases",
        "test_case_versions",
        "test_steps",
        "tags",
        "test_case_tags",
        "test_case_version_tags",
        "test_cycles",
        "cycle_items",
        "execution_attempts",
        "issues",
        "issue_steps",
        "issue_links",
        "comments",
        "attachments",
        "integration_settings",
        "drive_usage_snapshots",
        "drive_reconciliation_records",
        "outbox_events",
        "slack_channel_allowlist",
        "slack_events",
        "slack_work_items",
        "slack_threads",
        "slack_messages",
        "slack_message_revisions",
        "slack_parse_results",
        "generation_jobs",
        "generation_change_assessments",
        "generation_contexts",
        "generation_applies",
        "generation_apply_outcomes",
        "generation_execution_schedules",
        "generation_attempt_events",
        "generation_provider_call_permits",
        "generation_provider_call_observations",
        "generation_retry_requests",
        "generation_retry_exhaustions",
        "generation_materialization_schedules",
        "test_case_generation_management_events",
        "generation_quality_candidates",
        "generation_quality_candidate_roots",
        "generation_quality_review_decisions",
        "generation_quality_approvals",
        "generation_quality_reviewer_authorities",
        "lightrag_generations",
        "lightrag_active_generation_pointer",
        "lightrag_generation_transition_events",
    }.issubset(Base.metadata.tables)
