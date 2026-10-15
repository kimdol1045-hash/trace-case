from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.dialects import postgresql

from tc_backend.db.jobs import build_claim_statement, calculate_backoff, plan_failure
from tc_backend.db.models import JobStatus


@pytest.mark.parametrize(
    ("attempt", "expected_seconds"),
    [(1, 5), (2, 10), (3, 20), (10, 60)],
)
def test_exponential_backoff_is_capped(attempt: int, expected_seconds: int) -> None:
    assert calculate_backoff(
        attempt,
        base_seconds=5,
        max_seconds=60,
    ) == timedelta(seconds=expected_seconds)


def test_failure_plan_retries_before_final_attempt() -> None:
    plan = plan_failure(
        attempt=2,
        max_attempts=3,
        base_seconds=5,
        max_seconds=60,
    )

    assert plan.status is JobStatus.RETRY
    assert plan.delay == timedelta(seconds=10)


def test_failure_plan_marks_final_attempt_dead() -> None:
    plan = plan_failure(
        attempt=3,
        max_attempts=3,
        base_seconds=5,
        max_seconds=60,
    )

    assert plan.status is JobStatus.DEAD
    assert plan.delay == timedelta(0)


def test_claim_statement_uses_postgresql_skip_locked() -> None:
    statement = build_claim_statement(stale_after=timedelta(minutes=5))
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "background_jobs.run_after <= now()" in sql
    assert "coalesce(background_jobs.heartbeat_at, background_jobs.locked_at)" in sql


def test_claim_rejects_nonpositive_stale_window() -> None:
    with pytest.raises(ValueError, match="positive"):
        build_claim_statement(stale_after=timedelta(0))

