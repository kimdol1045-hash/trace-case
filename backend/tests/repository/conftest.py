from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def postgres_url() -> str:
    value = os.getenv("TC_TEST_DATABASE_URL")
    if not value:
        pytest.skip(
            "TC_TEST_DATABASE_URL is not set; see backend/README.md for the "
            "disposable PostgreSQL 16 + pgvector command"
        )
    url = make_url(value)
    if not url.drivername.startswith("postgresql"):
        pytest.fail("TC_TEST_DATABASE_URL must use PostgreSQL")
    if not url.database or "test" not in url.database.lower():
        pytest.fail("repository tests require a disposable database name containing 'test'")
    return value


@pytest.fixture(scope="session")
def alembic_config(postgres_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def migrated_engine(postgres_url: str, alembic_config: Config) -> Iterator[Engine]:
    command.upgrade(alembic_config, "head")
    engine = create_engine(postgres_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(migrated_engine: Engine) -> Iterator[None]:
    _reset_sprint_tables(migrated_engine)
    yield
    _reset_sprint_tables(migrated_engine)


def _reset_sprint_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE lightrag_generation_transition_events, "
                "lightrag_active_generation_pointer, lightrag_generations, "
                "generation_quality_approvals, "
                "generation_quality_review_decisions, "
                "generation_quality_reviewer_authorities, "
                "generation_quality_candidate_roots, generation_quality_candidates, "
                "generation_baseline_audit_events, "
                "generation_baseline_rollbacks, generation_baseline_activations, "
                "generation_baseline_runtime, generation_baseline_approvals, "
                "generation_baseline_change_requests, generation_version_sets, "
                "generation_materialization_schedules, "
                "generation_retry_exhaustions, generation_retry_requests, "
                "generation_provider_call_observations, "
                "generation_provider_call_permits, generation_attempt_events, "
                "generation_execution_schedules, generation_apply_outcomes, "
                "generation_applies, "
                "generation_contexts, generation_jobs, "
                "knowledge_projection_events, knowledge_embeddings, "
                "knowledge_evidence, knowledge_edges, knowledge_nodes, "
                "knowledge_source_records, "
                "slack_parse_results, slack_message_revisions, "
                "slack_messages, slack_threads, slack_work_items, slack_events, "
                "slack_channel_allowlist, drive_reconciliation_records, drive_usage_snapshots, "
                "integration_settings, attachments, comments, issue_links, issue_steps, "
                "issues, outbox_events, execution_attempts, cycle_items, test_cycles, "
                "folder_aliases, audit_logs, folders, "
                "test_case_version_tags, test_case_tags, test_steps, test_case_versions, "
                "test_cases, tags, work_items, workspace_key_counters, "
                "workspace_settings, background_jobs CASCADE"
            )
        )
        connection.execute(
            text(
                "INSERT INTO lightrag_active_generation_pointer "
                "(scope, pointer_version, active_generation_id) "
                "VALUES ('GLOBAL', 0, NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO workspace_settings "
                "(id, singleton_key, team_name, case_prefix, issue_prefix, "
                "default_environment, generation_quiet_minutes, version) "
                "VALUES ('00000000-0000-7000-8000-000000000100'::uuid, 1, "
                "'QA Team', 'TC', 'ISSUE', 'staging', 10, 1)"
            )
        )
        for principal, token in (
            ("quality-principal-alpha", "alpha-review-token"),
            ("quality-principal-beta", "beta-review-token"),
        ):
            connection.execute(
                text("SELECT tc_install_generation_quality_reviewer(:principal, :credential_hash)"),
                {
                    "principal": principal,
                    "credential_hash": "sha256:"
                    + hashlib.sha256(token.encode("utf-8")).hexdigest(),
                },
            )
        connection.execute(
            text(
                "INSERT INTO workspace_key_counters (workspace_id, entity_type, next_value) "
                "VALUES ('00000000-0000-7000-8000-000000000100'::uuid, 'TEST_CASE', 1), "
                "('00000000-0000-7000-8000-000000000100'::uuid, 'ISSUE', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO folders "
                "(id, workspace_id, name, normalized_name, sort_order, version, system_kind) "
                "VALUES ('00000000-0000-7000-8000-000000000101'::uuid, "
                "'00000000-0000-7000-8000-000000000100'::uuid, "
                "'AI 생성함', 'ai 생성함', 2147483647, 1, 'AI_GENERATED_INBOX')"
            )
        )
