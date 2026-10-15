"""PostgreSQL authority and failure-finality regression for PE-SE2-T07."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.models import (
    BackgroundJob,
    GenerationAttemptEvent,
    GenerationApply,
    GenerationExecutionSchedule,
    GenerationJob,
    GenerationMaterializationSchedule,
    GenerationProviderCallPermit,
    GenerationRetryExhaustion,
    GenerationSlackNotificationIntent,
)
from tc_backend.db.jobs import JobRepository
from tc_backend.generation.retry_history import RetryHistoryError, RetryHistoryService
from tc_backend.repositories.generation_repository import GenerationRepository
from test_generation_apply_api_postgresql import _seed_apply_job
from test_phase_e_automatic_generation_impact_postgresql import (
    _consumer,
    _process_current,
    _seed_source,
)

pytestmark = pytest.mark.postgres


def test_materialization_requires_and_commits_the_claimed_background_delivery(
    clean_api_database: Engine,
) -> None:
    seed = _seed_apply_job(
        clean_api_database,
        with_human_reuse=False,
        automatic=True,
    )
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    with sessions.begin() as session:
        schedule = session.scalar(
            select(GenerationMaterializationSchedule).where(
                GenerationMaterializationSchedule.generation_job_id == seed.job_id
            )
        )
        assert schedule is not None
        delivery = session.get(BackgroundJob, schedule.background_job_id)
        assert delivery is not None
        worker_id = delivery.locked_by
        attempt = delivery.attempts
        assert worker_id is not None and delivery.status == "RUNNING"
        committed = RetryHistoryService(session).materialize_succeeded_job(
            seed.job_id,
            background_job_id=delivery.id,
            background_attempt=attempt,
            worker_id=worker_id,
        )
        assert committed.state == "COMMITTED"
        assert committed.generation_apply_id is not None
        assert committed.lease_owner is None
        assert JobRepository(session).succeed(
            job_id=delivery.id,
            worker_id=worker_id,
            attempt=attempt,
        )

    with sessions() as session:
        assert session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.job_type == "AUTOMATIC_GENERATION_MATERIALIZE")
        ) == 1
        delivery = session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.job_type == "AUTOMATIC_GENERATION_MATERIALIZE"
            )
        )
        assert delivery is not None and delivery.status == "SUCCEEDED"
        assert session.scalar(
            select(func.count())
            .select_from(GenerationApply)
            .where(GenerationApply.generation_job_id == seed.job_id)
        ) == 1
        intent = session.scalar(
            select(GenerationSlackNotificationIntent).where(
                GenerationSlackNotificationIntent.generation_job_id == seed.job_id
            )
        )
        assert intent is not None and intent.notification_kind == "SUCCESS_APPLIED"

    for statement in (
        "DELETE FROM generation_materialization_schedules "
        "WHERE generation_job_id = :job_id",
        "UPDATE background_jobs SET job_type = 'FORGED' "
        "WHERE job_type = 'AUTOMATIC_GENERATION_MATERIALIZE'",
    ):
        with pytest.raises(DBAPIError):
            with clean_api_database.begin() as connection:
                connection.execute(text(statement), {"job_id": seed.job_id})


def test_retry_authority_schema_has_all_ledgers_and_append_only_guards(
    clean_api_database: Engine,
) -> None:
    tables = {
        "generation_execution_schedules",
        "generation_attempt_events",
        "generation_provider_call_permits",
        "generation_provider_call_observations",
        "generation_retry_requests",
        "generation_retry_exhaustions",
        "generation_materialization_schedules",
    }
    with clean_api_database.begin() as connection:
        assert all(
            connection.scalar(text("SELECT to_regclass(:name)"), {"name": name})
            for name in tables
        )
        trigger_name = connection.scalar(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgname = 'trg_generation_attempt_events_append_only'"
            )
        )
        assert trigger_name is not None


def test_retry_budget_exhaustion_and_slack_intent_commit_atomically(
    clean_api_database: Engine,
) -> None:
    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions, suffix="T07BUDGET")
    _process_current(
        sessions,
        _consumer(sessions),
        work_item_id=work_item_id,
        commit_baseline=False,
    )
    with sessions() as session:
        root = session.scalar(
            select(GenerationJob).where(
                GenerationJob.slack_work_item_id == work_item_id,
                GenerationJob.generation_trigger == "AUTOMATIC",
            )
        )
        assert root is not None
        current_job_id = root.id

    for generation_attempt in range(1, 4):
        with sessions.begin() as session:
            schedule = session.scalar(
                select(GenerationExecutionSchedule).where(
                    GenerationExecutionSchedule.generation_job_id == current_job_id
                )
            )
            assert schedule is not None
            schedule.not_before = datetime.now(UTC)
            schedule.state = "READY"
            delivery = session.get(BackgroundJob, schedule.background_job_id)
            assert delivery is not None
            delivery.status = "RUNNING"
            delivery.attempts += 1
            delivery.locked_by = "t07-budget-worker"
            delivery.locked_at = datetime.now(UTC)
            delivery.heartbeat_at = delivery.locked_at
            service = RetryHistoryService(session)
            leased = service.claim_execution(
                current_job_id,
                background_job_id=delivery.id,
                background_attempt=delivery.attempts,
                worker_id="t07-budget-worker",
            )
            schedule_id = leased.id
            lease_token = leased.lease_token
            assert lease_token is not None

        for provider_attempt in range(1, 4):
            with sessions.begin() as session:
                claim = RetryHistoryService(session).permit_provider_call(
                    schedule_id, lease_token
                )
                assert claim.provider_attempt_number == provider_attempt
            with sessions.begin() as session:
                RetryHistoryService(session).observe_provider_call(
                    claim,
                    outcome="RETRYABLE_FAILURE",
                    error_code="GENERATION_TIMEOUT",
                    safe_provider_code="timeout",
                )
        with sessions.begin() as session:
            with pytest.raises(RetryHistoryError) as budget_error:
                RetryHistoryService(session).permit_provider_call(
                    schedule_id, lease_token
                )
            assert budget_error.value.code == "GENERATION_PROVIDER_BUDGET_EXHAUSTED"
        with sessions.begin() as session:
            terminal = RetryHistoryService(session).finalize_failure(
                schedule_id,
                lease_token,
                code="GENERATION_TIMEOUT",
                retryable=True,
            )
            if generation_attempt < 3:
                assert isinstance(terminal, GenerationJob)
                current_job_id = terminal.id
            else:
                assert isinstance(terminal, GenerationRetryExhaustion)

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(GenerationJob)) == 3
        assert (
            session.scalar(select(func.count()).select_from(GenerationProviderCallPermit))
            == 9
        )
        assert (
            session.scalar(select(func.count()).select_from(GenerationRetryExhaustion))
            == 1
        )
        intent = session.scalar(select(GenerationSlackNotificationIntent))
        assert intent is not None and intent.notification_kind == "FAILURE"
        event_count = session.scalar(select(func.count()).select_from(GenerationAttemptEvent))
        assert event_count is not None and event_count >= 20

    with pytest.raises(Exception):
        with clean_api_database.begin() as connection:
            connection.execute(text("DELETE FROM generation_retry_exhaustions"))

    with sessions() as session:
        retryable, _ = RetryHistoryService(session).list_failures(
            actionability="OPERATOR_RETRY_ALLOWED"
        )
        assert len(retryable) == 1
        assert retryable[0]["failedJobId"] == f"generation_job_{current_job_id.hex}"
        assert retryable[0]["latestRetryJobId"] is None

    with sessions.begin() as session:
        retry = RetryHistoryService(session).retry_with_conditions(
            current_job_id,
            idempotency_key="t07:operator:retry:001",
            expected_root_job_id=root.id,
            expected_failed_job_id=current_job_id,
            expected_attempt_number=3,
            reason="자동 재시도 소진 후 운영자 검토",
            additional_conditions="경계값 사례를 우선 검증하세요.",
        )
        retry_job_id = retry.job.id
        assert retry.job.attempt_number == 4
        assert not retry.replayed
    with sessions.begin() as session:
        replay = RetryHistoryService(session).retry_with_conditions(
            current_job_id,
            idempotency_key="t07:operator:retry:001",
            expected_root_job_id=root.id,
            expected_failed_job_id=current_job_id,
            expected_attempt_number=3,
            reason="자동 재시도 소진 후 운영자 검토",
            additional_conditions="경계값 사례를 우선 검증하세요.",
        )
        assert replay.replayed and replay.job.id == retry_job_id

    # One operator retry consumes the old exhaustion's action. A lost response
    # may replay the request, but the historical failed row must not keep an
    # enabled button that can only produce a stale-view conflict.
    with sessions() as session:
        retryable, _ = RetryHistoryService(session).list_failures(
            actionability="OPERATOR_RETRY_ALLOWED"
        )
        review_only, _ = RetryHistoryService(session).list_failures(
            actionability="REVIEW_ONLY"
        )
        assert retryable == ()
        assert len(review_only) == 1
        assert review_only[0]["actionability"] == "REVIEW_ONLY"
        assert review_only[0]["latestRetryJobId"] == (
            f"generation_job_{retry_job_id.hex}"
        )

    # Once that operator job itself reaches a terminal failure, the immutable
    # request remains history and a new explicit request may target the new
    # latest failed job without reopening the automatic budget.
    with sessions.begin() as session:
        schedule = session.scalar(
            select(GenerationExecutionSchedule).where(
                GenerationExecutionSchedule.generation_job_id == retry_job_id
            )
        )
        assert schedule is not None
        schedule.not_before = datetime.now(UTC)
        schedule.state = "READY"
        delivery = session.get(BackgroundJob, schedule.background_job_id)
        assert delivery is not None
        delivery.status = "RUNNING"
        delivery.attempts += 1
        delivery.locked_by = "t07-operator-retry-worker"
        delivery.locked_at = datetime.now(UTC)
        delivery.heartbeat_at = delivery.locked_at
        leased = RetryHistoryService(session).claim_execution(
            retry_job_id,
            background_job_id=delivery.id,
            background_attempt=delivery.attempts,
            worker_id="t07-operator-retry-worker",
        )
        assert leased.lease_token is not None
        RetryHistoryService(session).finalize_failure(
            leased.id,
            leased.lease_token,
            code="GENERATION_AUTH_REJECTED",
            retryable=False,
        )
    with sessions() as session:
        retryable, _ = RetryHistoryService(session).list_failures(
            actionability="OPERATOR_RETRY_ALLOWED"
        )
        assert len(retryable) == 1
        assert retryable[0]["failedJobId"] == f"generation_job_{retry_job_id.hex}"
        assert retryable[0]["latestRetryJobId"] == f"generation_job_{retry_job_id.hex}"

    with sessions.begin() as session:
        second_retry = RetryHistoryService(session).retry_with_conditions(
            retry_job_id,
            idempotency_key="t07:operator:retry:002",
            expected_root_job_id=root.id,
            expected_failed_job_id=retry_job_id,
            expected_attempt_number=4,
            reason="첫 운영자 재시도 실패 후 재검토",
            additional_conditions="인증 경계를 다시 확인하세요.",
        )
        second_retry_job_id = second_retry.job.id
        assert second_retry.job.attempt_number == 5

    with sessions.begin() as session:
        repository = GenerationRepository(session)
        repository.begin_execution(second_retry_job_id)
        repository.succeed_execution(
            second_retry_job_id,
            {
                "outputSchemaVersion": "1.0.0",
                "cases": [],
                "categoryCandidate": None,
            },
        )
    with sessions() as session:
        detail = RetryHistoryService(session).failure_detail(current_job_id)
        assert detail["attemptHistory"][-1]["status"] == "SUCCEEDED"
        # The root's terminal failure already owns the sole Slack reply. The
        # later operator success remains visible in history without a second
        # contradictory thread reply.
        assert session.scalar(
            select(func.count()).select_from(GenerationSlackNotificationIntent)
        ) == 1


def test_committed_success_observation_without_finalization_is_never_resent(
    clean_api_database: Engine,
) -> None:
    """Crash recovery must fail closed when only the result digest survived."""

    sessions = sessionmaker(clean_api_database, expire_on_commit=False, class_=Session)
    work_item_id, _, _ = _seed_source(sessions, suffix="T07SUCCESSCRASH")
    _process_current(
        sessions,
        _consumer(sessions),
        work_item_id=work_item_id,
        commit_baseline=False,
    )
    with sessions.begin() as session:
        root = session.scalar(
            select(GenerationJob).where(
                GenerationJob.slack_work_item_id == work_item_id,
                GenerationJob.generation_trigger == "AUTOMATIC",
            )
        )
        assert root is not None
        schedule = session.scalar(
            select(GenerationExecutionSchedule).where(
                GenerationExecutionSchedule.generation_job_id == root.id
            )
        )
        assert schedule is not None
        schedule.not_before = datetime.now(UTC)
        schedule.state = "READY"
        delivery = session.get(BackgroundJob, schedule.background_job_id)
        assert delivery is not None
        delivery.status = "RUNNING"
        delivery.attempts += 1
        delivery.locked_by = "t07-success-crash-worker"
        delivery.locked_at = datetime.now(UTC)
        delivery.heartbeat_at = delivery.locked_at
        leased = RetryHistoryService(session).claim_execution(
            root.id,
            background_job_id=delivery.id,
            background_attempt=delivery.attempts,
            worker_id="t07-success-crash-worker",
        )
        assert leased.lease_token is not None
        schedule_id = leased.id
        lease_token = leased.lease_token
        delivery_id = delivery.id
        delivery_attempt = delivery.attempts

    with sessions.begin() as session:
        claim = RetryHistoryService(session).permit_provider_call(
            schedule_id, lease_token
        )
    with sessions.begin() as session:
        RetryHistoryService(session).observe_provider_call(
            claim,
            outcome="SUCCEEDED",
            result={"outputSchemaVersion": "1.0.0", "cases": []},
        )
    with sessions.begin() as session:
        schedule = session.get(GenerationExecutionSchedule, schedule_id)
        assert schedule is not None
        schedule.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with sessions.begin() as session:
        recovered = RetryHistoryService(session).claim_execution(
            root.id,
            background_job_id=delivery_id,
            background_attempt=delivery_attempt,
            worker_id="t07-success-crash-worker",
        )
        assert recovered.state == "PROVIDER_OUTCOME_UNKNOWN"

    with sessions() as session:
        assert (
            session.scalar(select(func.count()).select_from(GenerationProviderCallPermit))
            == 1
        )
        failed = session.get(GenerationJob, root.id)
        assert failed is not None
        assert failed.status == "FAILED"
        assert failed.error_code == "GENERATION_EXECUTION_OUTCOME_UNKNOWN"
        exhaustion = session.scalar(
            select(GenerationRetryExhaustion).where(
                GenerationRetryExhaustion.root_job_id == root.id
            )
        )
        assert exhaustion is not None
        assert exhaustion.finality == "TERMINAL_REVIEW_REQUIRED"
