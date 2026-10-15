"""API readiness and database-backed operational metric collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.db.models import (
    Attachment,
    BackgroundJob,
    GenerationAttemptEvent,
    GenerationExecutionSchedule,
    GenerationJob,
    GenerationMaterializationSchedule,
    GenerationProviderCallObservation,
    GenerationRetryRequest,
    GenerationRetryExhaustion,
    GenerationSlackNotificationIntent,
    IntegrationProvider,
    KnowledgeProjectionEvent,
    OutboxEvent,
    SlackEvent,
)
from tc_backend.observability import MetricsRegistry, job_component, safe_error_code


@dataclass(frozen=True, slots=True)
class DatabaseObservation:
    up: bool
    storage_bytes: int = 0


def probe_database(sessions: sessionmaker[Session] | None) -> bool:
    if sessions is None:
        return False
    try:
        with sessions() as session:
            return session.execute(text("SELECT 1")).scalar_one() == 1
    except Exception:
        return False


def collect_database_metrics(
    sessions: sessionmaker[Session] | None,
    *,
    now: datetime | None = None,
) -> tuple[MetricsRegistry, DatabaseObservation]:
    """Collect bounded aggregates; failures expose only a zero health gauge."""

    metrics = MetricsRegistry()
    if sessions is None:
        metrics.set_gauge("tc_database_up", 0)
        return metrics, DatabaseObservation(up=False)
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        with sessions() as session:
            session.execute(text("SELECT 1")).scalar_one()
            _collect_background_jobs(session, metrics, observed_at)
            _collect_outbox(session, metrics, observed_at)
            _collect_slack(session, metrics)
            storage_bytes = _collect_storage(session, metrics)
            _collect_knowledge(session, metrics)
            _collect_generation(session, metrics, observed_at)
    except Exception:
        metrics = MetricsRegistry()
        metrics.set_gauge("tc_database_up", 0)
        return metrics, DatabaseObservation(up=False)
    metrics.set_gauge("tc_database_up", 1)
    return metrics, DatabaseObservation(up=True, storage_bytes=storage_bytes)


def _collect_background_jobs(
    session: Session, metrics: MetricsRegistry, observed_at: datetime
) -> None:
    rows = session.execute(
        select(BackgroundJob.job_type, BackgroundJob.status, func.count())
        .group_by(BackgroundJob.job_type, BackgroundJob.status)
    ).all()
    counts: dict[tuple[str, str], int] = {}
    for job_type, status, count in rows:
        key = (job_component(str(job_type)), str(status))
        counts[key] = counts.get(key, 0) + int(count)
    for (component, status), count in counts.items():
        metrics.set_gauge(
            "tc_background_jobs", count, component=component, status=status
        )

    ready_rows = session.execute(
        select(BackgroundJob.job_type, func.min(BackgroundJob.run_after))
        .where(
            BackgroundJob.status.in_(("QUEUED", "RETRY")),
            BackgroundJob.run_after <= observed_at,
        )
        .group_by(BackgroundJob.job_type)
    ).all()
    oldest: dict[str, float] = {}
    for job_type, run_after in ready_rows:
        if run_after is None:
            continue
        component = job_component(str(job_type))
        oldest[component] = max(
            oldest.get(component, 0.0), _age_seconds(observed_at, run_after)
        )
    for component, age in oldest.items():
        metrics.set_gauge(
            "tc_background_job_oldest_ready_age_seconds", age, component=component
        )


def _collect_outbox(
    session: Session, metrics: MetricsRegistry, observed_at: datetime
) -> None:
    for status, count in session.execute(
        select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)
    ):
        metrics.set_gauge("tc_outbox_events", int(count), status=str(status))
    oldest = session.scalar(
        select(func.min(OutboxEvent.available_at)).where(
            OutboxEvent.status.in_(("READY", "FAILED")),
            OutboxEvent.available_at <= observed_at,
        )
    )
    metrics.set_gauge(
        "tc_outbox_oldest_ready_age_seconds",
        _age_seconds(observed_at, oldest) if oldest is not None else 0,
    )


def _collect_slack(session: Session, metrics: MetricsRegistry) -> None:
    for status, count in session.execute(
        select(SlackEvent.status, func.count()).group_by(SlackEvent.status)
    ):
        metrics.set_gauge("tc_slack_events", int(count), status=str(status))


def _collect_storage(session: Session, metrics: MetricsRegistry) -> int:
    storage_bytes = int(
        session.scalar(
            select(func.coalesce(func.sum(Attachment.size_bytes), 0)).where(
                Attachment.storage_provider == IntegrationProvider.R2.value,
                Attachment.deleted_at.is_(None),
            )
        )
        or 0
    )
    metrics.set_gauge("tc_object_storage_bytes", storage_bytes)
    return storage_bytes


def _collect_knowledge(session: Session, metrics: MetricsRegistry) -> None:
    count = int(
        session.scalar(select(func.count()).select_from(KnowledgeProjectionEvent)) or 0
    )
    metrics.set_counter("tc_knowledge_projection_events_total", count)


def _collect_generation(
    session: Session, metrics: MetricsRegistry, observed_at: datetime
) -> None:
    for status, count in session.execute(
        select(GenerationJob.status, func.count()).group_by(GenerationJob.status)
    ):
        metrics.set_gauge("tc_generation_jobs", int(count), status=str(status))

    for state, count in session.execute(
        select(GenerationExecutionSchedule.state, func.count()).group_by(
            GenerationExecutionSchedule.state
        )
    ):
        metrics.set_gauge(
            "tc_generation_execution_schedules", int(count), state=str(state)
        )
    lease_count = int(
        session.scalar(
            select(func.count())
            .select_from(GenerationAttemptEvent)
            .where(GenerationAttemptEvent.event_type == "WORKER_LEASED")
        )
        or 0
    )
    lease_counts = (
        select(
            GenerationAttemptEvent.root_job_id,
            func.count().label("claim_count"),
        )
        .where(GenerationAttemptEvent.event_type == "WORKER_LEASED")
        .group_by(GenerationAttemptEvent.root_job_id)
        .subquery()
    )
    lease_reclaims = int(
        session.scalar(
            select(
                func.coalesce(
                    func.sum(func.greatest(lease_counts.c.claim_count - 1, 0)),
                    0,
                )
            )
        )
        or 0
    )
    metrics.set_counter(
        "generation_execution_claim_total", lease_count, outcome="LEASED"
    )
    metrics.set_counter(
        "generation_execution_lease_reclaim_total",
        lease_reclaims,
        phase="EXECUTION",
    )
    ready_since = session.scalar(
        select(func.min(GenerationExecutionSchedule.not_before)).where(
            GenerationExecutionSchedule.state == "READY",
            GenerationExecutionSchedule.not_before <= observed_at,
        )
    )
    metrics.set_gauge(
        "generation_execution_schedule_lag_seconds",
        _age_seconds(observed_at, ready_since) if ready_since is not None else 0,
    )
    outcome_unknown = int(
        session.scalar(
            select(func.count())
            .select_from(GenerationExecutionSchedule)
            .where(GenerationExecutionSchedule.state == "PROVIDER_OUTCOME_UNKNOWN")
        )
        or 0
    )
    metrics.set_gauge("generation_execution_outcome_unknown", outcome_unknown)

    for outcome, error_code, count in session.execute(
        select(
            GenerationProviderCallObservation.outcome,
            GenerationProviderCallObservation.error_code,
            func.count(),
        ).group_by(
            GenerationProviderCallObservation.outcome,
            GenerationProviderCallObservation.error_code,
        )
    ):
        metrics.set_counter(
            "generation_provider_attempt_total",
            int(count),
            outcome=str(outcome),
            error_code=safe_error_code(error_code),
        )

    exhaustion_total = 0
    outcome_unknown_total = 0
    for error_code, finality, count in session.execute(
        select(
            GenerationRetryExhaustion.error_code,
            GenerationRetryExhaustion.finality,
            func.count(),
        ).group_by(
            GenerationRetryExhaustion.error_code,
            GenerationRetryExhaustion.finality,
        )
    ):
        amount = int(count)
        exhaustion_total += amount
        if error_code == "GENERATION_EXECUTION_OUTCOME_UNKNOWN":
            outcome_unknown_total += amount
        metrics.set_counter(
            "generation_retry_exhaustion_total",
            amount,
            error_code=safe_error_code(str(error_code)),
            finality=str(finality),
        )
    metrics.set_gauge("generation_failure_queue_actionable", exhaustion_total)
    metrics.set_counter(
        "generation_execution_outcome_unknown_total", outcome_unknown_total
    )

    for error_code, count in session.execute(
        select(GenerationAttemptEvent.error_code, func.count())
        .where(GenerationAttemptEvent.event_type == "RETRY_SCHEDULED")
        .group_by(GenerationAttemptEvent.error_code)
    ):
        metrics.set_counter(
            "generation_job_retry_total",
            int(count),
            origin="AUTOMATIC",
            error_code=safe_error_code(error_code),
        )
    operator_retry_total = 0
    for error_code, count in session.execute(
        select(GenerationJob.error_code, func.count())
        .join(
            GenerationRetryRequest,
            GenerationRetryRequest.expected_failed_job_id == GenerationJob.id,
        )
        .group_by(GenerationJob.error_code)
    ):
        amount = int(count)
        operator_retry_total += amount
        metrics.set_counter(
            "generation_job_retry_total",
            amount,
            origin="OPERATOR",
            error_code=safe_error_code(error_code),
        )
    metrics.set_counter(
        "generation_operator_retry_total", operator_retry_total, outcome="ACCEPTED"
    )

    terminal_states = {
        "COMMITTED",
        "FAILED",
        "REVIEW_REQUIRED",
        "SKIPPED_SOURCE_UNAVAILABLE",
        "SKIPPED_STALE_SOURCE",
    }
    materialization_rows = session.execute(
        select(
            GenerationMaterializationSchedule.mode,
            GenerationMaterializationSchedule.state,
            func.count(),
        ).group_by(
            GenerationMaterializationSchedule.mode,
            GenerationMaterializationSchedule.state,
        )
    ).all()
    materialization_totals: dict[tuple[str, str], int] = {}
    for mode, state, count in materialization_rows:
        mode_value = str(mode)
        metrics.set_gauge(
            "tc_generation_materialization_schedules",
            int(count),
            mode=mode_value,
            state=str(state),
        )
        outcome = _materialization_outcome(str(state))
        if outcome is not None:
            key = (mode_value, outcome)
            materialization_totals[key] = materialization_totals.get(key, 0) + int(
                count
            )
    for (mode, outcome), count in materialization_totals.items():
        metrics.set_counter(
            "generation_materialization_total",
            count,
            mode=mode,
            outcome=outcome,
        )
    oldest_materialization = session.scalar(
        select(func.min(GenerationMaterializationSchedule.created_at)).where(
            GenerationMaterializationSchedule.state.not_in(terminal_states)
        )
    )
    metrics.set_gauge(
        "tc_generation_materialization_oldest_nonterminal_age_seconds",
        (
            _age_seconds(observed_at, oldest_materialization)
            if oldest_materialization is not None
            else 0
        ),
    )

    notification_counts: dict[str, int] = {"POST": 0, "SUPPRESSED": 0}
    for disposition, count in session.execute(
        select(GenerationSlackNotificationIntent.delivery_disposition, func.count()).group_by(
            GenerationSlackNotificationIntent.delivery_disposition
        )
    ):
        outcome = "POST" if disposition == "POST" else "SUPPRESSED"
        notification_counts[outcome] += int(count)
    for outcome, count in notification_counts.items():
        metrics.set_counter(
            "generation_terminal_notification_capture_total", count, outcome=outcome
        )
    notification_gap = int(
        session.scalar(
            select(func.count(GenerationRetryExhaustion.id))
            .outerjoin(
                GenerationSlackNotificationIntent,
                GenerationSlackNotificationIntent.generation_job_id
                == GenerationRetryExhaustion.terminal_failed_job_id,
            )
            .where(GenerationSlackNotificationIntent.id.is_(None))
        )
        or 0
    )
    metrics.set_gauge(
        "generation_terminal_notification_capture_gap", notification_gap
    )


def _age_seconds(now: datetime, value: datetime) -> float:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return max(0.0, (now - normalized).total_seconds())


def _materialization_outcome(state: str) -> str | None:
    if state == "COMMITTED":
        return "COMMITTED"
    if state == "REVIEW_REQUIRED":
        return "REVIEW_REQUIRED"
    if state.startswith("SKIPPED_"):
        return "SKIPPED"
    if state == "FAILED":
        return "FAILED"
    return None
