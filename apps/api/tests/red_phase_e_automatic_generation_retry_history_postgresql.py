"""PE-SE2-T07 focused PostgreSQL RED for durable retry authority.

The non-default filename prevents accidental collection in the green gate.
Run this exact path with ``-m postgres`` and a disposable test database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.postgres


EXPECTED_TABLES = (
    "generation_execution_schedules",
    "generation_attempt_events",
    "generation_provider_call_permits",
    "generation_provider_call_observations",
    "generation_retry_requests",
    "generation_retry_exhaustions",
    "generation_materialization_schedules",
)

EXPECTED_GUARDS = (
    "uq_generation_execution_active_root",
    "uq_generation_provider_permit_attempt",
    "uq_generation_provider_observation_permit",
    "uq_generation_retry_request_actor_idempotency",
    "uq_generation_retry_exhaustion_root",
    "uq_generation_materialization_job",
    "trg_generation_attempt_events_append_only",
    "trg_generation_provider_call_permits_append_only",
    "trg_generation_provider_call_observations_append_only",
    "trg_generation_retry_requests_append_only",
    "trg_generation_retry_exhaustions_append_only",
    "trg_generation_execution_schedules_no_delete",
    "trg_generation_materialization_schedules_no_delete",
    "trg_generation_retry_authority_background_jobs",
)


def test_retry_authority_tables_are_migrated(clean_api_database: Engine) -> None:
    with clean_api_database.connect() as connection:
        missing = [
            table
            for table in EXPECTED_TABLES
            if connection.scalar(text("SELECT to_regclass(:table_name)"), {"table_name": table})
            is None
        ]
    assert not missing, f"RED T07: add authoritative migration tables: {', '.join(missing)}"


def test_retry_authority_unique_and_append_only_guards_are_migrated(
    clean_api_database: Engine,
) -> None:
    with clean_api_database.connect() as connection:
        names = set(
            connection.scalars(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() "
                    "UNION ALL "
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
                )
            )
        )
    missing = sorted(set(EXPECTED_GUARDS) - names)
    assert not missing, f"RED T07: add database uniqueness/append-only guards: {', '.join(missing)}"
