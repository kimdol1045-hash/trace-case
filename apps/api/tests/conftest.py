from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"


@pytest.fixture(scope="session")
def api_postgres_url() -> str:
    value = os.getenv("TC_TEST_DATABASE_URL")
    if not value:
        pytest.skip(
            "TC_TEST_DATABASE_URL is required for PostgreSQL API contract tests"
        )
    url = make_url(value)
    if not url.drivername.startswith("postgresql"):
        pytest.fail("TC_TEST_DATABASE_URL must use PostgreSQL")
    if not url.database or "test" not in url.database.lower():
        pytest.fail(
            "API contract tests require a disposable database containing 'test'"
        )
    return value


@pytest.fixture(scope="session")
def api_migrated_engine(api_postgres_url: str) -> Iterator[Engine]:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", api_postgres_url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(api_postgres_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_api_database(api_migrated_engine: Engine) -> Iterator[Engine]:
    _reset_database(api_migrated_engine)
    yield api_migrated_engine
    _reset_database(api_migrated_engine)


def _reset_database(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE generation_quality_approvals, "
                "generation_quality_review_decisions, "
                "generation_quality_candidate_roots, generation_quality_candidates, "
                "generation_materialization_schedules, "
                "generation_retry_exhaustions, generation_retry_requests, "
                "generation_provider_call_observations, "
                "generation_provider_call_permits, generation_attempt_events, "
                "generation_execution_schedules, generation_apply_outcomes, "
                "generation_applies, "
                "generation_contexts, generation_jobs, "
                "knowledge_projection_events, knowledge_embeddings, "
                "knowledge_evidence, knowledge_edges, knowledge_nodes, "
                "knowledge_source_records, outbox_events, "
                "execution_attempts, cycle_items, test_cycles, "
                "test_case_version_tags, test_case_tags, test_steps, "
                "test_case_versions, test_cases, tags, work_items, folder_aliases, "
                "audit_logs, folders, "
                "workspace_key_counters, workspace_settings, background_jobs CASCADE"
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
