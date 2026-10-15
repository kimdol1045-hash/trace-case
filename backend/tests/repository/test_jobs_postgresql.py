from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import sessionmaker

from tc_backend.db.jobs import JobRepository
from tc_backend.db.models import BackgroundJob, JobStatus

pytestmark = pytest.mark.postgres


def test_two_workers_cannot_claim_or_complete_one_job_twice(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        job_id = JobRepository(session).enqueue(
            job_type="KNOWLEDGE_EXTRACT",
            payload={"work_item_id": "ledger-id"},
            idempotency_key="extract:ledger-id:v1",
        )

    worker_one = sessions()
    worker_two = sessions()
    transaction_one = worker_one.begin()
    transaction_two = worker_two.begin()
    try:
        claimed_one = JobRepository(worker_one).claim_next(
            worker_id="worker-1",
            stale_after=timedelta(minutes=5),
        )
        claimed_two = JobRepository(worker_two).claim_next(
            worker_id="worker-2",
            stale_after=timedelta(minutes=5),
        )
        assert claimed_one is not None
        assert claimed_one.id == job_id
        assert claimed_two is None
        transaction_one.commit()
        transaction_two.commit()
    finally:
        if transaction_one.is_active:
            transaction_one.rollback()
        if transaction_two.is_active:
            transaction_two.rollback()
        worker_one.close()
        worker_two.close()

    with sessions.begin() as session:
        assert JobRepository(session).succeed(
            job_id=job_id,
            worker_id="worker-1",
            attempt=1,
        )
        assert not JobRepository(session).succeed(
            job_id=job_id,
            worker_id="worker-2",
            attempt=1,
        )

    with sessions() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        assert job.status == JobStatus.SUCCEEDED.value
        assert job.attempts == 1


def test_retry_then_dead_after_max_attempts(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        job_id = JobRepository(session).enqueue(
            job_type="REINDEX_KNOWLEDGE",
            payload={"scope": "all"},
            max_attempts=2,
        )

    with sessions.begin() as session:
        first = JobRepository(session).claim_next(
            worker_id="worker-1",
            stale_after=timedelta(minutes=5),
        )
        assert first is not None and first.attempt == 1
    with sessions.begin() as session:
        plan = JobRepository(session).fail(
            job_id=job_id,
            worker_id="worker-1",
            error="temporary failure",
            base_seconds=0,
            max_seconds=0,
            attempt=1,
        )
        assert plan is not None and plan.status is JobStatus.RETRY

    with sessions.begin() as session:
        second = JobRepository(session).claim_next(
            worker_id="worker-2",
            stale_after=timedelta(minutes=5),
        )
        assert second is not None and second.attempt == 2
    with sessions.begin() as session:
        plan = JobRepository(session).fail(
            job_id=job_id,
            worker_id="worker-2",
            error="permanent failure",
            base_seconds=0,
            max_seconds=0,
            attempt=2,
        )
        assert plan is not None and plan.status is JobStatus.DEAD

    with sessions() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        assert job.status == JobStatus.DEAD.value
        assert job.completed_at is not None


def test_enqueue_idempotency_key_returns_original_job(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine)
    with sessions.begin() as session:
        repository = JobRepository(session)
        first_id = repository.enqueue(
            job_type="OBJECT_STORAGE_USAGE_OBSERVE",
            payload={"sequence": 1},
            idempotency_key="object-storage-usage:2026-08-20",
        )
        second_id = repository.enqueue(
            job_type="OBJECT_STORAGE_USAGE_OBSERVE",
            payload={"sequence": 2},
            idempotency_key="object-storage-usage:2026-08-20",
        )

    assert first_id == second_id
    with sessions() as session:
        jobs = session.scalars(select(BackgroundJob)).all()
        assert len(jobs) == 1
        assert jobs[0].payload == {"sequence": 1}


def test_exhausted_stale_lock_is_reaped_to_dead(migrated_engine: Engine) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        job_id = JobRepository(session).enqueue(
            job_type="SLACK_THREAD_SYNC",
            payload={"thread_id": "ledger-id"},
            max_attempts=1,
        )
    with sessions.begin() as session:
        claim = JobRepository(session).claim_next(
            worker_id="crashed-worker",
            stale_after=timedelta(minutes=5),
        )
        assert claim is not None
    with sessions.begin() as session:
        session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id)
            .values(
                locked_at=BackgroundJob.locked_at - timedelta(hours=1),
                heartbeat_at=BackgroundJob.heartbeat_at - timedelta(hours=1),
            )
        )
    with sessions.begin() as session:
        assert (
            JobRepository(session).claim_next(
                worker_id="replacement-worker",
                stale_after=timedelta(minutes=5),
            )
            is None
        )
    with sessions() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None and job.status == JobStatus.DEAD.value


def test_claim_filter_does_not_claim_or_reap_an_unsupported_job(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        unsupported_id = JobRepository(session).enqueue(
            job_type="KNOWLEDGE_PROJECTION",
            payload={"limit": 10},
            priority=100,
        )
        supported_id = JobRepository(session).enqueue(
            job_type="SLACK_QUIET_WINDOW",
            payload={"workItemId": "synthetic"},
        )

    with sessions.begin() as session:
        claimed = JobRepository(session).claim_next(
            worker_id="slack-worker",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_QUIET_WINDOW",),
        )
        assert claimed is not None and claimed.id == supported_id

    with sessions() as session:
        unsupported = session.get(BackgroundJob, unsupported_id)
        assert unsupported is not None
        assert unsupported.status == JobStatus.QUEUED.value
        assert unsupported.attempts == 0


def test_attempt_fence_rejects_a_reclaimed_attempt_from_the_same_worker_id(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        job_id = JobRepository(session).enqueue(
            job_type="SLACK_AUTOMATIC_GENERATION",
            payload={"sourceVersion": 1},
        )
    with sessions.begin() as session:
        first = JobRepository(session).claim_next(
            worker_id="reused-worker-id",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_AUTOMATIC_GENERATION",),
        )
        assert first is not None and first.attempt == 1
    with sessions.begin() as session:
        session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id)
            .values(
                locked_at=BackgroundJob.locked_at - timedelta(hours=1),
                heartbeat_at=BackgroundJob.heartbeat_at - timedelta(hours=1),
            )
        )
    with sessions.begin() as session:
        second = JobRepository(session).claim_next(
            worker_id="reused-worker-id",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_AUTOMATIC_GENERATION",),
        )
        assert second is not None and second.attempt == 2
    with sessions.begin() as session:
        repository = JobRepository(session)
        assert not repository.succeed(
            job_id=job_id,
            worker_id="reused-worker-id",
            attempt=first.attempt,
        )
        assert repository.succeed(
            job_id=job_id,
            worker_id="reused-worker-id",
            attempt=second.attempt,
        )


def test_permanent_failure_dead_letters_without_consuming_all_attempts(
    migrated_engine: Engine,
) -> None:
    sessions = sessionmaker(migrated_engine, expire_on_commit=False)
    with sessions.begin() as session:
        job_id = JobRepository(session).enqueue(
            job_type="SLACK_AUTOMATIC_GENERATION",
            payload={"sourceVersion": "malformed"},
            max_attempts=5,
        )
    with sessions.begin() as session:
        claimed = JobRepository(session).claim_next(
            worker_id="slack-worker",
            stale_after=timedelta(minutes=5),
            job_types=("SLACK_AUTOMATIC_GENERATION",),
        )
        assert claimed is not None
    with sessions.begin() as session:
        plan = JobRepository(session).fail(
            job_id=job_id,
            worker_id="slack-worker",
            attempt=claimed.attempt,
            error="InvalidJobPayloadError: sourceVersion",
            base_seconds=1,
            max_seconds=60,
            permanent=True,
        )
        assert plan is not None and plan.status is JobStatus.DEAD
    with sessions() as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        assert job.status == JobStatus.DEAD.value
        assert job.attempts == 1
