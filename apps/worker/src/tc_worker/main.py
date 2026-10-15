"""PostgreSQL background-job worker entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from types import FrameType
from typing import Any, Protocol

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from tc_backend.config import Settings, SlackMode, load_settings
from tc_backend.db.jobs import ClaimedJob, JobRepository
from tc_backend.generation.application import (
    GenerationPlacementConflict,
    GenerationStoredResultInvalid,
)
from tc_backend.generation.adapter import GenerationAPIAdapter
from tc_backend.generation.config import ProviderAuth, ProviderConfig, RetryPolicy
from tc_backend.generation.errors import GenerationError
from tc_backend.generation.lifecycle import GenerationRunResult
from tc_backend.generation.retry_history import (
    AUTOMATIC_GENERATION_EXECUTE_JOB_TYPE,
    AUTOMATIC_GENERATION_MATERIALIZE_JOB_TYPE,
    RetryHistoryError,
    RetryHistoryService,
    classify_generation_error,
)
from tc_backend.generation.suggestions import GenerationSuggestionConflict
from tc_backend.generation.transport import (
    GenerationTransport,
    HttpxGenerationTransport,
)
from tc_backend.infrastructure.slack.config import (
    SlackRuntimeState,
    inspect_slack_runtime,
    require_slack_runtime,
)
from tc_backend.infrastructure.slack.http_transport import SlackWebApiHttpTransport
from tc_backend.infrastructure.slack.socket_mode import SlackSocketModeClient
from tc_backend.infrastructure.slack.transport import SlackWebApiTransport
from tc_backend.knowledge.embedding import (
    BgeM3OllamaEmbeddingProvider,
    EmbeddingProvider,
    HttpxOllamaEmbeddingTransport,
)
from tc_backend.knowledge.extraction import (
    HttpxOllamaKnowledgeExtractionTransport,
    KnowledgeExtractionProvider,
    OllamaKnowledgeExtractionProvider,
    SlackKnowledgeExtractor,
)
from tc_backend.knowledge.lightrag import LightRAGIndex
from tc_backend.knowledge.lightrag_composition import (
    build_lightrag_generation_runtime_factory,
)
from tc_backend.knowledge.lightrag_runtime import (
    LightRAGGenerationRuntimeFactory,
    LightRAGRuntimeResolver,
    build_lightrag_runtime_resolver,
)
from tc_backend.observability import (
    MetricsRegistry,
    PrometheusTextfileExporter,
    job_component,
)
from tc_backend.startup import validate_startup
from tc_backend.worker.knowledge_consumer import (
    KnowledgeJobConsumer,
    KnowledgeOutboxConsumer,
)
from tc_worker.slack_runtime import (
    PermanentJobError,
    RetryAfterError,
    SlackWorkerRuntime,
)

JobHandler = Callable[[Mapping[str, Any], ClaimedJob | None], None]


def handle_noop(
    payload: Mapping[str, Any],
    _claim: ClaimedJob | None = None,
) -> None:
    """Development smoke-test handler that intentionally has no side effects."""

    del payload


DEFAULT_HANDLERS: dict[str, JobHandler] = {"noop": handle_noop}
KNOWLEDGE_PROJECTION_JOB_TYPE = "KNOWLEDGE_PROJECTION"
KNOWLEDGE_REINDEX_JOB_TYPE = "KNOWLEDGE_REINDEX"
_AUTOMATIC_GENERATION_LEASE_SECONDS = 60


class GenerationExecutionRunner(Protocol):
    """One-call provider seam; retries are owned by the durable T07 service."""

    def run(
        self,
        payload: Mapping[str, object],
        *,
        provider_attempt_number: int,
    ) -> GenerationRunResult: ...


class GenerationAdapterExecutionRunner:
    """Synchronous worker bridge around one adapter-owned HTTP call.

    Durable T07 permits own retries, so the composed adapter is always built
    with ``max_attempts=1``.  The provider credential remains inside the
    adapter/config boundary and is never retained in this runner's repr.
    """

    def __init__(self, adapter: GenerationAPIAdapter) -> None:
        self._adapter = adapter

    def run(
        self,
        payload: Mapping[str, object],
        *,
        provider_attempt_number: int,
    ) -> GenerationRunResult:
        if provider_attempt_number < 1:
            raise ValueError("provider attempt number must be positive")
        result = asyncio.run(self._adapter.generate(payload))
        return GenerationRunResult(payload=result.payload, attempts=result.attempts)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(single_call=True)"


def build_automatic_knowledge_consumer(
    settings: Settings,
    *,
    session_factory: sessionmaker[Session],
    extraction_provider: KnowledgeExtractionProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    light_rag: LightRAGIndex | None = None,
) -> KnowledgeOutboxConsumer | None:
    """Compose the independently enabled Slack ontology projection boundary.

    The default path constructs no provider transport.  Tests and alternate
    deployment roots may explicitly inject both providers without changing
    global settings; production ``main`` activates the local Ollama adapters
    only through ``TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED=true``.
    """

    injected = extraction_provider is not None or embedding_provider is not None
    if not settings.automatic_knowledge_projection_enabled and not injected:
        return None
    if not settings.automatic_knowledge_projection_enabled and (
        extraction_provider is None or embedding_provider is None
    ):
        raise RuntimeError(
            "automatic knowledge provider injection requires extraction and embedding"
        )
    active_extraction = extraction_provider or OllamaKnowledgeExtractionProvider(
        HttpxOllamaKnowledgeExtractionTransport(),
        host=str(settings.ollama_base_url),
        model=settings.generation_model,
        timeout_seconds=settings.knowledge_extraction_timeout_seconds,
        max_response_bytes=settings.knowledge_extraction_response_max_bytes,
    )
    active_embedding = embedding_provider or BgeM3OllamaEmbeddingProvider(
        HttpxOllamaEmbeddingTransport(),
        host=str(settings.ollama_base_url),
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
    return KnowledgeOutboxConsumer(
        session_factory=session_factory,
        embedding_provider=active_embedding,
        extractor=SlackKnowledgeExtractor(active_extraction),
        light_rag=light_rag,
    )


class ClaimedKnowledgeJobHandlers:
    """Adapt the knowledge consumer to jobs already claimed by ``Worker``.

    ``KnowledgeJobConsumer.run_once`` owns its own claim lifecycle for scripts
    and tests.  Production uses the generic worker lifecycle instead, so these
    handlers invoke only the consumer's projection/reindex operation and leave
    success, retry, heartbeat, and lease fencing to ``Worker``.
    """

    def __init__(
        self,
        consumer: KnowledgeJobConsumer | None = None,
        *,
        consumer_factory: Callable[[], KnowledgeJobConsumer] | None = None,
    ) -> None:
        if consumer is None and consumer_factory is None:
            raise ValueError("knowledge job handlers require a consumer or factory")
        self.consumer = consumer
        self._consumer_factory = consumer_factory

    def handlers(self) -> dict[str, JobHandler]:
        return {
            KNOWLEDGE_PROJECTION_JOB_TYPE: self.project,
            KNOWLEDGE_REINDEX_JOB_TYPE: self.reindex,
        }

    def project(
        self,
        payload: Mapping[str, Any],
        claim: ClaimedJob | None = None,
    ) -> None:
        consumer = self._require_consumer(claim, KNOWLEDGE_PROJECTION_JOB_TYPE)
        raw_limit = payload.get("limit", 100)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as error:
            raise PermanentJobError("knowledge projection limit is invalid") from error
        if limit < 1:
            raise PermanentJobError("knowledge projection limit must be positive")

        async def run() -> None:
            for _ in range(limit):
                if await consumer.outbox_consumer.run_once() is None:
                    break

        asyncio.run(run())

    def reindex(
        self,
        payload: Mapping[str, Any],
        claim: ClaimedJob | None = None,
    ) -> None:
        consumer = self._require_consumer(claim, KNOWLEDGE_REINDEX_JOB_TYPE)
        # The generic worker already owns the BackgroundJob claim.  Calling
        # KnowledgeJobConsumer.run_once() here would claim a second queued job
        # and silently acknowledge the first, so invoke only its reindex work.
        asyncio.run(consumer._reindex(dict(payload)))

    def _require_consumer(
        self,
        claim: ClaimedJob | None,
        expected_job_type: str,
    ) -> KnowledgeJobConsumer:
        if claim is None or claim.job_type != expected_job_type:
            raise PermanentJobError("knowledge job requires its owned delivery")
        if self.consumer is None:
            assert self._consumer_factory is not None
            self.consumer = self._consumer_factory()
        return self.consumer


def build_knowledge_job_consumer(
    settings: Settings,
    *,
    session_factory: sessionmaker[Session],
    worker_id: str,
    outbox_consumer: KnowledgeOutboxConsumer | None = None,
    extraction_provider: KnowledgeExtractionProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    light_rag: LightRAGIndex | None = None,
) -> KnowledgeJobConsumer:
    """Compose the consumer for explicit API-enqueued knowledge jobs.

    Unlike automatic Slack projection, an explicitly queued API job is its
    own activation boundary.  The production worker therefore creates this
    consumer lazily on the first such delivery when automatic projection is
    disabled, preserving the dormant/default no-transport startup path.
    """

    active_outbox_consumer = outbox_consumer
    active_embedding = embedding_provider
    if active_outbox_consumer is None:
        active_extraction = extraction_provider or OllamaKnowledgeExtractionProvider(
            HttpxOllamaKnowledgeExtractionTransport(),
            host=str(settings.ollama_base_url),
            model=settings.generation_model,
            timeout_seconds=settings.knowledge_extraction_timeout_seconds,
            max_response_bytes=settings.knowledge_extraction_response_max_bytes,
        )
        active_embedding = active_embedding or BgeM3OllamaEmbeddingProvider(
            HttpxOllamaEmbeddingTransport(),
            host=str(settings.ollama_base_url),
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
        )
        active_outbox_consumer = KnowledgeOutboxConsumer(
            session_factory=session_factory,
            embedding_provider=active_embedding,
            extractor=SlackKnowledgeExtractor(active_extraction),
            light_rag=light_rag,
        )
    elif extraction_provider is not None or embedding_provider is not None:
        raise ValueError(
            "an existing knowledge outbox consumer conflicts with provider injection"
        )
    else:
        active_embedding = active_outbox_consumer.embedding_provider

    if active_embedding is None:
        raise RuntimeError("knowledge job processing requires an embedding provider")
    return KnowledgeJobConsumer(
        session_factory=session_factory,
        outbox_consumer=active_outbox_consumer,
        embedding_provider=active_embedding,
        light_rag=light_rag,
        worker_id=worker_id,
    )


def build_generation_execution_runner(
    settings: Settings,
    *,
    transport: GenerationTransport | None = None,
) -> GenerationExecutionRunner | None:
    """Compose the opt-in production adapter without exposing its credential.

    Disabled is the credential-free default and constructs no HTTP transport.
    When enabled, ``Settings`` has already required every provider field and
    ``ProviderConfig`` performs the final endpoint/header safety validation.
    """

    if not settings.automatic_generation_enabled:
        return None
    provider_id = settings.automatic_generation_provider_id
    endpoint = settings.automatic_generation_provider_endpoint
    model_id = settings.automatic_generation_provider_model_id
    token = settings.automatic_generation_provider_auth_token
    if provider_id is None or endpoint is None or model_id is None or token is None:
        raise RuntimeError("automatic generation provider configuration is incomplete")
    config = ProviderConfig(
        provider_id=provider_id,
        endpoint=str(endpoint),
        model_id=model_id,
        auth=ProviderAuth(
            credential=token.get_secret_value(),
            header_name=settings.automatic_generation_provider_auth_header,
            scheme=settings.automatic_generation_provider_auth_scheme,
        ),
        timeout_seconds=settings.automatic_generation_provider_timeout_seconds,
        response_max_bytes=(settings.automatic_generation_provider_response_max_bytes),
        retry=RetryPolicy(max_attempts=1),
        supported_schema_pairs=frozenset(
            {
                ("1.0.0", "1.0.0"),
                (
                    "generation-suggestion/1.0.0",
                    "generation-suggestion/1.0.0",
                ),
            }
        ),
    )
    return GenerationAdapterExecutionRunner(
        GenerationAPIAdapter(config, transport or HttpxGenerationTransport())
    )


def _run_with_generation_lease_heartbeat(
    *,
    session_factory: sessionmaker[Session],
    schedule_id: uuid.UUID,
    lease_token: uuid.UUID,
    runner: GenerationExecutionRunner,
    payload: Mapping[str, object],
    provider_attempt_number: int,
    lease_seconds: int = _AUTOMATIC_GENERATION_LEASE_SECONDS,
) -> GenerationRunResult:
    """Keep the domain lease alive while the generic job heartbeat runs.

    A provider timeout may exceed the 60-second domain lease.  The generic
    background-job heartbeat alone cannot authorize a late provider result,
    so this independent CAS heartbeat is required for both authorities.
    """

    stop = threading.Event()
    lease_lost = threading.Event()
    failures: list[str] = []
    interval = max(0.1, min(20.0, lease_seconds / 3))

    def heartbeat() -> None:
        while not stop.wait(interval):
            try:
                with session_factory() as session, session.begin():
                    owned = RetryHistoryService(session).heartbeat(
                        schedule_id,
                        lease_token,
                        lease_seconds=lease_seconds,
                    )
            except Exception as error:  # pragma: no cover - database fault seam
                failures.append(type(error).__name__)
                lease_lost.set()
                return
            if not owned:
                lease_lost.set()
                return

    thread = threading.Thread(
        target=heartbeat,
        name=f"generation-lease-heartbeat-{schedule_id}",
        daemon=True,
    )
    thread.start()
    try:
        result = runner.run(
            payload,
            provider_attempt_number=provider_attempt_number,
        )
    finally:
        stop.set()
        thread.join(timeout=interval + 1)
    if lease_lost.is_set():
        detail = failures[0] if failures else "lease ownership changed"
        raise LostJobLeaseError(f"automatic generation lease lost: {detail}")
    return result


def build_automatic_generation_execution_handler(
    *,
    session_factory: sessionmaker[Session],
    worker_id: str,
    generation_execution_runner: GenerationExecutionRunner | None,
    public_web_base_url: str | None = None,
) -> JobHandler:
    """Build the production automatic execution handler with short DB phases."""

    def handle(payload: Mapping[str, Any], claim: ClaimedJob | None = None) -> None:
        if claim is None:
            raise PermanentJobError("automatic generation requires an owned delivery")
        raw_job_id = payload.get("generationJobId")
        try:
            generation_job_id = uuid.UUID(str(raw_job_id))
        except (TypeError, ValueError) as error:
            raise PermanentJobError("automatic generation job id is invalid") from error
        with session_factory() as session, session.begin():
            service = RetryHistoryService(
                session, public_web_base_url=public_web_base_url
            )
            schedule = service.claim_execution(
                generation_job_id,
                background_job_id=claim.id,
                background_attempt=claim.attempt,
                worker_id=worker_id,
                lease_seconds=_AUTOMATIC_GENERATION_LEASE_SECONDS,
            )
            if schedule.state != "LEASED":
                return
            lease_token = schedule.lease_token
            provider_budget = schedule.provider_call_budget
            provider_input = service.provider_input_for_job(generation_job_id)
        if lease_token is None:
            raise LostJobLeaseError("automatic generation lease token is absent")
        if generation_execution_runner is None:
            with session_factory() as session, session.begin():
                RetryHistoryService(
                    session, public_web_base_url=public_web_base_url
                ).finalize_failure(
                    schedule.id,
                    lease_token,
                    code="GENERATION_CONFIG_INVALID",
                    retryable=False,
                )
            return
        while True:
            with session_factory() as session, session.begin():
                call = RetryHistoryService(session).permit_provider_call(
                    schedule.id, lease_token
                )
            try:
                run_result = _run_with_generation_lease_heartbeat(
                    session_factory=session_factory,
                    schedule_id=schedule.id,
                    lease_token=lease_token,
                    runner=generation_execution_runner,
                    payload=provider_input,
                    provider_attempt_number=call.provider_attempt_number,
                )
            except GenerationError as error:
                if error.attempts != 1:
                    with session_factory() as session, session.begin():
                        service = RetryHistoryService(
                            session, public_web_base_url=public_web_base_url
                        )
                        service.observe_provider_call(
                            call,
                            outcome="AMBIGUOUS",
                            error_code="GENERATION_EXECUTION_OUTCOME_UNKNOWN",
                        )
                        service.finalize_failure(
                            schedule.id,
                            lease_token,
                            code="GENERATION_EXECUTION_OUTCOME_UNKNOWN",
                            retryable=False,
                            review_required=True,
                        )
                    return
                code, retryable = classify_generation_error(error)
                with session_factory() as session, session.begin():
                    service = RetryHistoryService(
                        session, public_web_base_url=public_web_base_url
                    )
                    service.observe_provider_call(
                        call,
                        outcome=(
                            "RETRYABLE_FAILURE"
                            if retryable
                            else "NON_RETRYABLE_FAILURE"
                        ),
                        error_code=code,
                        safe_provider_code=error.code.name.casefold(),
                        http_status=error.status_code,
                    )
                    if retryable and call.provider_attempt_number < provider_budget:
                        continue
                    service.finalize_failure(
                        schedule.id,
                        lease_token,
                        code=code,
                        retryable=retryable,
                    )
                return
            except Exception:
                with session_factory() as session, session.begin():
                    service = RetryHistoryService(
                        session, public_web_base_url=public_web_base_url
                    )
                    service.observe_provider_call(
                        call,
                        outcome="AMBIGUOUS",
                        error_code="GENERATION_EXECUTION_OUTCOME_UNKNOWN",
                    )
                    service.finalize_failure(
                        schedule.id,
                        lease_token,
                        code="GENERATION_EXECUTION_OUTCOME_UNKNOWN",
                        retryable=False,
                        review_required=True,
                    )
                return
            if run_result.attempts != 1:
                with session_factory() as session, session.begin():
                    service = RetryHistoryService(
                        session, public_web_base_url=public_web_base_url
                    )
                    service.observe_provider_call(
                        call,
                        outcome="AMBIGUOUS",
                        error_code="GENERATION_EXECUTION_OUTCOME_UNKNOWN",
                    )
                    service.finalize_failure(
                        schedule.id,
                        lease_token,
                        code="GENERATION_EXECUTION_OUTCOME_UNKNOWN",
                        retryable=False,
                        review_required=True,
                    )
                return
            try:
                with session_factory() as session, session.begin():
                    service = RetryHistoryService(
                        session, public_web_base_url=public_web_base_url
                    )
                    service.observe_provider_call(
                        call,
                        outcome="SUCCEEDED",
                        result=run_result.payload,
                    )
                    service.finalize_success(
                        schedule.id, lease_token, run_result.payload
                    )
            except (GenerationStoredResultInvalid, RetryHistoryError) as error:
                if isinstance(error, RetryHistoryError) and error.status_code != 422:
                    raise
                with session_factory() as session, session.begin():
                    service = RetryHistoryService(
                        session, public_web_base_url=public_web_base_url
                    )
                    service.observe_provider_call(
                        call,
                        outcome="NON_RETRYABLE_FAILURE",
                        error_code="GENERATION_INVALID_RESPONSE",
                    )
                    service.finalize_failure(
                        schedule.id,
                        lease_token,
                        code="GENERATION_INVALID_RESPONSE",
                        retryable=False,
                    )
            return

    return handle


def build_automatic_generation_materialization_handler(
    *,
    session_factory: sessionmaker[Session],
    worker_id: str,
    public_web_base_url: str | None = None,
) -> JobHandler:
    def handle(payload: Mapping[str, Any], claim: ClaimedJob | None = None) -> None:
        if claim is None:
            raise PermanentJobError(
                "automatic materialization requires an owned delivery"
            )
        try:
            generation_job_id = uuid.UUID(str(payload.get("generationJobId")))
        except (TypeError, ValueError) as error:
            raise PermanentJobError(
                "automatic materialization job id is invalid"
            ) from error
        try:
            with session_factory() as session, session.begin():
                RetryHistoryService(
                    session, public_web_base_url=public_web_base_url
                ).materialize_succeeded_job(
                    generation_job_id,
                    background_job_id=claim.id,
                    background_attempt=claim.attempt,
                    worker_id=worker_id,
                )
        except (
            GenerationPlacementConflict,
            GenerationStoredResultInvalid,
            GenerationSuggestionConflict,
        ):
            with session_factory() as session, session.begin():
                RetryHistoryService(
                    session, public_web_base_url=public_web_base_url
                ).mark_materialization_review_required(
                    generation_job_id,
                    background_job_id=claim.id,
                    background_attempt=claim.attempt,
                    worker_id=worker_id,
                )

    return handle


def dispatch_job(job: ClaimedJob, handlers: Mapping[str, JobHandler]) -> None:
    try:
        handler = handlers[job.job_type]
    except KeyError as error:
        raise LookupError(
            f"no handler registered for job type: {job.job_type}"
        ) from error
    handler(job.payload, job)


class LostJobLeaseError(RuntimeError):
    """The worker no longer owns the claimed background-job attempt."""


class Worker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        worker_id: str,
        handlers: Mapping[str, JobHandler] = DEFAULT_HANDLERS,
        socket_ingress: Callable[[], None] | None = None,
        scheduled_maintenance: Callable[[], object] | None = None,
        maintenance_interval_seconds: float = 3_600,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        metrics: MetricsRegistry | None = None,
        metrics_exporter: PrometheusTextfileExporter | None = None,
        light_rag_runtime: LightRAGRuntimeResolver | None = None,
    ) -> None:
        if maintenance_interval_seconds <= 0:
            raise ValueError("maintenance interval must be positive")
        self.session_factory = session_factory
        self.settings = settings
        self.worker_id = worker_id
        self.handlers = handlers
        self.socket_ingress = socket_ingress
        self.scheduled_maintenance = scheduled_maintenance
        self.maintenance_interval_seconds = maintenance_interval_seconds
        self.monotonic = monotonic
        self.wall_clock = wall_clock
        self._next_maintenance_at = 0.0
        self.metrics = metrics or MetricsRegistry()
        self.metrics_exporter = metrics_exporter
        self.light_rag_runtime = light_rag_runtime
        self.metrics.set_gauge("tc_worker_up", 1)
        self.metrics.set_gauge(
            "tc_worker_last_poll_timestamp_seconds", self.wall_clock()
        )
        self._publish_metrics()

    def run_socket_once(self) -> bool:
        if self.socket_ingress is None:
            return False
        self.socket_ingress()
        return True

    def run_maintenance_once(self) -> bool:
        if self.scheduled_maintenance is None:
            return False
        now = self.monotonic()
        if now < self._next_maintenance_at:
            return False
        self.scheduled_maintenance()
        self._next_maintenance_at = now + self.maintenance_interval_seconds
        return True

    def run_once(self) -> bool:
        try:
            with self.session_factory() as session, session.begin():
                job = JobRepository(session).claim_next(
                    worker_id=self.worker_id,
                    stale_after=timedelta(
                        seconds=self.settings.job_stale_after_seconds
                    ),
                    job_types=tuple(self.handlers),
                )
        except Exception:
            self.metrics.inc("tc_worker_poll_total", outcome="error")
            self._finish_poll()
            raise
        if job is None:
            self.metrics.inc("tc_worker_poll_total", outcome="idle")
            self._finish_poll()
            return False

        self.metrics.inc("tc_worker_poll_total", outcome="claimed")
        outcome = "failure"
        try:
            self._dispatch_with_heartbeat(job)
        except Exception as error:
            with self.session_factory() as session, session.begin():
                plan = JobRepository(session).fail(
                    job_id=job.id,
                    worker_id=self.worker_id,
                    error=f"{type(error).__name__}: {error}",
                    base_seconds=self.settings.job_retry_base_seconds,
                    max_seconds=self.settings.job_retry_max_seconds,
                    attempt=job.attempt,
                    retry_after=(
                        timedelta(seconds=error.retry_after_seconds)
                        if isinstance(error, RetryAfterError)
                        else None
                    ),
                    permanent=isinstance(error, PermanentJobError),
                )
            outcome = (
                "lease_lost"
                if plan is None
                else "dead"
                if plan.status.value == "DEAD"
                else "retry"
            )
        else:
            with self.session_factory() as session, session.begin():
                succeeded = JobRepository(session).succeed(
                    job_id=job.id,
                    worker_id=self.worker_id,
                    attempt=job.attempt,
                )
            outcome = "success" if succeeded else "lease_lost"
            if succeeded:
                self.metrics.set_gauge(
                    "tc_worker_last_success_timestamp_seconds", self.wall_clock()
                )
        finally:
            self.metrics.inc(
                "tc_worker_jobs_total",
                component=job_component(job.job_type),
                outcome=outcome,
            )
            self._finish_poll()
        return True

    def _dispatch_with_heartbeat(self, job: ClaimedJob) -> None:
        stop = threading.Event()
        lease_lost = threading.Event()
        failure: list[str] = []
        interval = max(
            0.1,
            min(30.0, self.settings.job_stale_after_seconds / 3),
        )

        def heartbeat() -> None:
            while not stop.wait(interval):
                try:
                    with self.session_factory() as session, session.begin():
                        owned = JobRepository(session).heartbeat(
                            job_id=job.id,
                            worker_id=self.worker_id,
                            attempt=job.attempt,
                        )
                except Exception as error:  # pragma: no cover - database fault seam
                    failure.append(f"{type(error).__name__}: {error}")
                    self.metrics.inc("tc_worker_heartbeat_failures_total")
                    lease_lost.set()
                    return
                if not owned:
                    self.metrics.inc("tc_worker_heartbeat_failures_total")
                    lease_lost.set()
                    return

        thread = threading.Thread(
            target=heartbeat,
            name=f"job-heartbeat-{job.id}",
            daemon=True,
        )
        thread.start()
        try:
            dispatch_job(job, self.handlers)
        finally:
            stop.set()
            thread.join(timeout=interval + 1)
        if lease_lost.is_set():
            detail = failure[0] if failure else "attempt ownership changed"
            raise LostJobLeaseError(f"background-job lease lost: {detail}")

    def mark_stopped(self) -> None:
        self.metrics.set_gauge("tc_worker_up", 0)
        self._publish_metrics()
        if self.light_rag_runtime is not None:
            asyncio.run(self.light_rag_runtime.finalize())

    def _finish_poll(self) -> None:
        self.metrics.set_gauge(
            "tc_worker_last_poll_timestamp_seconds", self.wall_clock()
        )
        self._publish_metrics()

    def _publish_metrics(self) -> None:
        if self.metrics_exporter is not None:
            self.metrics_exporter.write(self.metrics)


def build_worker(
    settings: Settings,
    *,
    automatic_generation_consumer: KnowledgeOutboxConsumer | None = None,
    knowledge_extraction_provider: KnowledgeExtractionProvider | None = None,
    knowledge_embedding_provider: EmbeddingProvider | None = None,
    generation_execution_runner: GenerationExecutionRunner | None = None,
    slack_transport: SlackWebApiTransport | None = None,
    knowledge_light_rag_runtime_factory: LightRAGGenerationRuntimeFactory | None = None,
) -> tuple[Worker, Engine]:
    settings_light_rag_factory = build_lightrag_generation_runtime_factory(settings)
    if (
        settings_light_rag_factory is not None
        and knowledge_light_rag_runtime_factory is not None
    ):
        raise ValueError(
            "injecting a LightRAG runtime factory conflicts with enabled settings"
        )
    active_light_rag_factory = (
        knowledge_light_rag_runtime_factory or settings_light_rag_factory
    )
    if (
        active_light_rag_factory is not None
        and automatic_generation_consumer is not None
    ):
        raise ValueError(
            "an injected knowledge consumer cannot bypass active LightRAG generation routing"
        )
    validate_startup(settings)
    engine = create_engine(settings.sqlalchemy_database_url, pool_pre_ping=True)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    light_rag_runtime = (
        build_lightrag_runtime_resolver(engine, active_light_rag_factory)
        if active_light_rag_factory is not None
        else None
    )
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    active_automatic_generation_consumer = automatic_generation_consumer
    if active_automatic_generation_consumer is None:
        active_automatic_generation_consumer = build_automatic_knowledge_consumer(
            settings,
            session_factory=session_factory,
            extraction_provider=knowledge_extraction_provider,
            embedding_provider=knowledge_embedding_provider,
            light_rag=light_rag_runtime,
        )
    knowledge_job_consumer: KnowledgeJobConsumer | None = None
    if active_automatic_generation_consumer is not None:
        knowledge_job_consumer = build_knowledge_job_consumer(
            settings,
            session_factory=session_factory,
            worker_id=worker_id,
            outbox_consumer=active_automatic_generation_consumer,
            light_rag=light_rag_runtime,
        )
    knowledge_job_handlers = ClaimedKnowledgeJobHandlers(
        knowledge_job_consumer,
        consumer_factory=(
            None
            if knowledge_job_consumer is not None
            else lambda: build_knowledge_job_consumer(
                settings,
                session_factory=session_factory,
                worker_id=worker_id,
                extraction_provider=knowledge_extraction_provider,
                embedding_provider=knowledge_embedding_provider,
                light_rag=light_rag_runtime,
            )
        ),
    ).handlers()
    slack_runtime = SlackWorkerRuntime(
        session_factory=session_factory,
        automatic_generation_consumer=active_automatic_generation_consumer,
    )
    socket_ingress: Callable[[], None] | None = None
    scheduled_maintenance: Callable[[], object] | None = None
    if inspect_slack_runtime(settings).state is SlackRuntimeState.CONFIGURED:
        slack_config = require_slack_runtime(settings)
        transport = slack_transport or SlackWebApiHttpTransport(
            bot_token=slack_config.bot_token,
            base_url=slack_config.api_base_url,
            max_rate_limit_retries=slack_config.max_rate_limit_retries,
            max_provider_retries=slack_config.max_provider_retries,
            provider_retry_base_seconds=slack_config.retry_base_seconds,
        )
        slack_runtime = SlackWorkerRuntime.with_transport(
            session_factory=session_factory,
            transport=transport,
            automatic_generation_consumer=active_automatic_generation_consumer,
            token_workspace_id=slack_config.workspace_id,
            notification_delivery_lease_seconds=(
                slack_config.notification_delivery_lease_seconds
            ),
            notification_reconcile_window_seconds=(
                slack_config.notification_reconcile_window_seconds
            ),
            notification_retry_base_seconds=(
                slack_config.notification_retry_base_seconds
            ),
            notification_retry_max_seconds=(
                slack_config.notification_retry_max_seconds
            ),
            notification_breaker_open=slack_config.notification_breaker_open,
            notification_delivery_enabled=(
                slack_config.generation_notifications_enabled
            ),
        )
        if slack_config.mode is SlackMode.SOCKET and slack_config.app_token is not None:
            socket_client = SlackSocketModeClient(
                web_api=transport,
                app_token=slack_config.app_token,
            )

            def receive_socket() -> None:
                asyncio.run(slack_runtime.receive_socket_once(socket_client))

            socket_ingress = receive_socket

        def schedule_recent_threads() -> object:
            now = datetime.now(UTC)
            return slack_runtime.schedule_recent_thread_syncs(
                recent_since=now
                - timedelta(days=settings.slack_recent_resync_lookback_days),
                synced_before=now
                - timedelta(seconds=settings.slack_recent_resync_interval_seconds),
            )

        scheduled_maintenance = schedule_recent_threads
    automatic_handlers = {
        AUTOMATIC_GENERATION_EXECUTE_JOB_TYPE: (
            build_automatic_generation_execution_handler(
                session_factory=session_factory,
                worker_id=worker_id,
                generation_execution_runner=generation_execution_runner,
                public_web_base_url=(
                    str(settings.public_web_base_url)
                    if settings.public_web_base_url is not None
                    else None
                ),
            )
        ),
        AUTOMATIC_GENERATION_MATERIALIZE_JOB_TYPE: (
            build_automatic_generation_materialization_handler(
                session_factory=session_factory,
                worker_id=worker_id,
                public_web_base_url=(
                    str(settings.public_web_base_url)
                    if settings.public_web_base_url is not None
                    else None
                ),
            )
        ),
    }
    return (
        Worker(
            session_factory=session_factory,
            settings=settings,
            worker_id=worker_id,
            handlers={
                **DEFAULT_HANDLERS,
                **slack_runtime.handlers(),
                **automatic_handlers,
                **knowledge_job_handlers,
            },
            socket_ingress=socket_ingress,
            scheduled_maintenance=scheduled_maintenance,
            maintenance_interval_seconds=settings.slack_recent_resync_interval_seconds,
            metrics_exporter=(
                PrometheusTextfileExporter(settings.worker_metrics_path)
                if settings.worker_metrics_path is not None
                else None
            ),
            light_rag_runtime=light_rag_runtime,
        ),
        engine,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.poll_interval < 0:
        parser.error("--poll-interval cannot be negative")

    settings = load_settings()
    worker, engine = build_worker(
        settings,
        generation_execution_runner=build_generation_execution_runner(settings),
    )
    stopping = False

    def stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"worker ready: {worker.worker_id}", flush=True)
    try:
        while not stopping:
            maintained = worker.run_maintenance_once()
            received_socket = worker.run_socket_once()
            worked = worker.run_once() or received_socket or maintained
            if args.once:
                break
            if not worked:
                time.sleep(args.poll_interval)
    finally:
        mark_stopped = getattr(worker, "mark_stopped", None)
        if callable(mark_stopped):
            mark_stopped()
        engine.dispose()


if __name__ == "__main__":
    main()
