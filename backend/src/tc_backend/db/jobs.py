"""PostgreSQL-backed background-job queue prototype."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tc_backend.db.models import BackgroundJob, JobStatus


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: uuid.UUID
    job_type: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    locked_by: str


@dataclass(frozen=True, slots=True)
class FailurePlan:
    status: JobStatus
    delay: timedelta


def calculate_backoff(
    attempt: int,
    *,
    base_seconds: int,
    max_seconds: int,
) -> timedelta:
    """Return deterministic exponential backoff for a one-based attempt."""

    if attempt < 1:
        raise ValueError("attempt must be at least one")
    if base_seconds < 0 or max_seconds < 0:
        raise ValueError("retry delays cannot be negative")
    if max_seconds < base_seconds:
        raise ValueError("maximum retry delay cannot be smaller than the base")
    delay_seconds = min(max_seconds, base_seconds * (2 ** (attempt - 1)))
    return timedelta(seconds=delay_seconds)


def plan_failure(
    *,
    attempt: int,
    max_attempts: int,
    base_seconds: int,
    max_seconds: int,
) -> FailurePlan:
    if attempt >= max_attempts:
        return FailurePlan(status=JobStatus.DEAD, delay=timedelta(0))
    return FailurePlan(
        status=JobStatus.RETRY,
        delay=calculate_backoff(
            attempt,
            base_seconds=base_seconds,
            max_seconds=max_seconds,
        ),
    )


def build_claim_statement(
    *,
    stale_after: timedelta,
    job_types: Collection[str] | None = None,
) -> Select[tuple[BackgroundJob]]:
    """Build the locking query separately so its SQL is unit-testable."""

    if stale_after <= timedelta(0):
        raise ValueError("stale_after must be positive")
    stale_cutoff = func.now() - stale_after
    ready = BackgroundJob.status.in_((JobStatus.QUEUED.value, JobStatus.RETRY.value)) & (
        BackgroundJob.run_after <= func.now()
    )
    stale = (
        (BackgroundJob.status == JobStatus.RUNNING.value)
        & (func.coalesce(BackgroundJob.heartbeat_at, BackgroundJob.locked_at) <= stale_cutoff)
        & (BackgroundJob.attempts < BackgroundJob.max_attempts)
    )
    statement = select(BackgroundJob).where(or_(ready, stale))
    if job_types is not None:
        supported = tuple(dict.fromkeys(job_types))
        if not supported:
            raise ValueError("job_types cannot be empty")
        statement = statement.where(BackgroundJob.job_type.in_(supported))
    return (
        statement.order_by(
            BackgroundJob.priority.desc(),
            BackgroundJob.run_after.asc(),
            BackgroundJob.created_at.asc(),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )


class JobRepository:
    """Transaction-scoped queue operations.

    This class never commits. Callers must wrap every operation in
    ``with session.begin():`` so claim and state transitions are atomic.
    Processing happens after the short claim transaction has committed.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self,
        *,
        job_type: str,
        payload: Mapping[str, Any],
        max_attempts: int = 5,
        priority: int = 0,
        run_after: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> uuid.UUID:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        job_id = uuid.uuid4()
        values: dict[str, Any] = {
            "id": job_id,
            "job_type": job_type,
            "payload": dict(payload),
            "status": JobStatus.QUEUED.value,
            "priority": priority,
            "attempts": 0,
            "max_attempts": max_attempts,
            "idempotency_key": idempotency_key,
        }
        if run_after is not None:
            values["run_after"] = run_after

        statement = insert(BackgroundJob).values(**values)
        if idempotency_key is not None:
            statement = statement.on_conflict_do_nothing(
                index_elements=[BackgroundJob.idempotency_key]
            )
        inserted_id = self.session.execute(
            statement.returning(BackgroundJob.id)
        ).scalar_one_or_none()
        if inserted_id is not None:
            return inserted_id
        existing_id = self.session.execute(
            select(BackgroundJob.id).where(BackgroundJob.idempotency_key == idempotency_key)
        ).scalar_one()
        return existing_id

    def claim_next(
        self,
        *,
        worker_id: str,
        stale_after: timedelta,
        job_types: Collection[str] | None = None,
    ) -> ClaimedJob | None:
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        self._mark_exhausted_stale_jobs_dead(
            stale_after=stale_after,
            job_types=job_types,
        )
        job = self.session.execute(
            build_claim_statement(stale_after=stale_after, job_types=job_types)
        ).scalar_one_or_none()
        if job is None:
            return None

        now = func.now()
        job.status = JobStatus.RUNNING.value
        job.attempts += 1
        job.locked_by = worker_id
        job.locked_at = now
        job.heartbeat_at = now
        job.updated_at = now
        job.last_error = None
        self.session.flush()
        return ClaimedJob(
            id=job.id,
            job_type=job.job_type,
            payload=dict(job.payload),
            attempt=job.attempts,
            max_attempts=job.max_attempts,
            locked_by=worker_id,
        )

    def heartbeat(self, *, job_id: uuid.UUID, worker_id: str, attempt: int) -> bool:
        statement = (
            update(BackgroundJob)
            .where(
                BackgroundJob.id == job_id,
                BackgroundJob.status == JobStatus.RUNNING.value,
                BackgroundJob.locked_by == worker_id,
                BackgroundJob.attempts == attempt,
            )
            .values(heartbeat_at=func.now(), updated_at=func.now())
            .returning(BackgroundJob.id)
        )
        return self.session.execute(statement).scalar_one_or_none() is not None

    def succeed(self, *, job_id: uuid.UUID, worker_id: str, attempt: int) -> bool:
        statement = (
            update(BackgroundJob)
            .where(
                BackgroundJob.id == job_id,
                BackgroundJob.status == JobStatus.RUNNING.value,
                BackgroundJob.locked_by == worker_id,
                BackgroundJob.attempts == attempt,
            )
            .values(
                status=JobStatus.SUCCEEDED.value,
                locked_by=None,
                locked_at=None,
                heartbeat_at=None,
                completed_at=func.now(),
                updated_at=func.now(),
                last_error=None,
            )
            .returning(BackgroundJob.id)
        )
        return self.session.execute(statement).scalar_one_or_none() is not None

    def fail(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        error: str,
        base_seconds: int,
        max_seconds: int,
        attempt: int,
        retry_after: timedelta | None = None,
        permanent: bool = False,
    ) -> FailurePlan | None:
        job = self.session.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.id == job_id,
                BackgroundJob.status == JobStatus.RUNNING.value,
                BackgroundJob.locked_by == worker_id,
                BackgroundJob.attempts == attempt,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if job is None:
            return None

        plan = (
            FailurePlan(status=JobStatus.DEAD, delay=timedelta(0))
            if permanent
            else plan_failure(
                attempt=job.attempts,
                max_attempts=job.max_attempts,
                base_seconds=base_seconds,
                max_seconds=max_seconds,
            )
        )
        if retry_after is not None:
            if retry_after < timedelta(0):
                raise ValueError("retry_after cannot be negative")
            if plan.status is JobStatus.RETRY:
                plan = FailurePlan(status=plan.status, delay=retry_after)
        now = func.now()
        job.status = plan.status.value
        job.run_after = now + plan.delay
        job.locked_by = None
        job.locked_at = None
        job.heartbeat_at = None
        job.updated_at = now
        job.last_error = error[:4_000]
        job.completed_at = now if plan.status is JobStatus.DEAD else None
        self.session.flush()
        return plan

    def _mark_exhausted_stale_jobs_dead(
        self,
        *,
        stale_after: timedelta,
        job_types: Collection[str] | None,
    ) -> None:
        if stale_after <= timedelta(0):
            raise ValueError("stale_after must be positive")
        stale_cutoff = func.now() - stale_after
        statement = update(BackgroundJob).where(
            BackgroundJob.status == JobStatus.RUNNING.value,
            func.coalesce(BackgroundJob.heartbeat_at, BackgroundJob.locked_at) <= stale_cutoff,
            BackgroundJob.attempts >= BackgroundJob.max_attempts,
        )
        if job_types is not None:
            supported = tuple(dict.fromkeys(job_types))
            if not supported:
                raise ValueError("job_types cannot be empty")
            statement = statement.where(BackgroundJob.job_type.in_(supported))
        self.session.execute(
            statement.values(
                status=JobStatus.DEAD.value,
                locked_by=None,
                locked_at=None,
                heartbeat_at=None,
                completed_at=func.now(),
                updated_at=func.now(),
                last_error=func.coalesce(
                    BackgroundJob.last_error,
                    "stale worker lock expired after the final attempt",
                ),
            )
        )
