from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.orm import sessionmaker

import tc_worker.main as worker_main
from tc_backend.config import Settings
from tc_backend.db.jobs import ClaimedJob
from tc_backend.generation import (
    FakeGenerationTransport,
    GenerationConfigurationError,
    GenerationTransportResponse,
)
from tc_backend.generation.retry_history import RetryHistoryError, RetryHistoryService
from tc_backend.generation.slack_notifications import (
    GenerationSlackNotificationConflict,
)
from tc_backend.knowledge.embedding import EmbeddingBatch
from tc_backend.knowledge.lightrag import LightRAGAuxiliaryIndex
from tc_backend.knowledge.lightrag_runtime import (
    LightRAGGenerationRuntime,
    LightRAGRuntimeResolver,
)
from tc_backend.knowledge.models import ProviderExtraction, SlackSourceBundle
from tc_backend.observability import PrometheusTextfileExporter
from tc_backend.repositories.lightrag_generation_repository import (
    ActiveGenerationBinding,
)
from tc_worker.main import Worker, dispatch_job
from tc_worker.slack_runtime import (
    InvalidJobPayloadError,
    PermanentJobError,
    RetryAfterError,
    SlackWorkerRuntime,
)


def make_job(job_type: str, payload: dict[str, object]) -> ClaimedJob:
    return ClaimedJob(
        id=uuid.uuid4(),
        job_type=job_type,
        payload=payload,
        attempt=1,
        max_attempts=5,
        locked_by="test-worker",
    )


def test_dispatches_payload_to_registered_handler() -> None:
    received: list[tuple[dict[str, object], ClaimedJob | None]] = []
    job = make_job("record", {"value": 7})

    dispatch_job(
        job,
        {"record": lambda payload, claim: received.append((dict(payload), claim))},
    )

    assert received == [({"value": 7}, job)]


def test_unknown_job_type_fails_explicitly() -> None:
    with pytest.raises(LookupError, match="no handler registered"):
        dispatch_job(make_job("unknown", {}), {})


def test_automatic_generation_handler_is_not_claimable_without_dependencies() -> None:
    runtime = SlackWorkerRuntime(session_factory=sessionmaker())

    assert "SLACK_AUTOMATIC_GENERATION" not in runtime.handlers()
    with pytest.raises(RetryAfterError, match="SETUP_REQUIRED"):
        runtime.automatic_generation(
            {
                "outboxEventId": str(uuid.uuid4()),
                "workItemId": str(uuid.uuid4()),
                "sourceVersion": 1,
            }
        )


def test_automatic_generation_handler_is_registered_with_injected_dependencies() -> (
    None
):
    runtime = SlackWorkerRuntime(
        session_factory=sessionmaker(),
        automatic_generation_consumer=object(),  # type: ignore[arg-type]
    )

    assert runtime.handlers()["SLACK_AUTOMATIC_GENERATION"] == (
        runtime.automatic_generation
    )


def test_malformed_automatic_generation_payload_is_permanent() -> None:
    runtime = SlackWorkerRuntime(session_factory=sessionmaker())

    with pytest.raises(InvalidJobPayloadError, match="outboxEventId"):
        runtime.automatic_generation(
            {"workItemId": str(uuid.uuid4()), "sourceVersion": 1}
        )


def _generation_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://tc:tc@localhost:5432/tc_test",
        "encryption_master_key": "worker-test-encryption-key-0000001",
        "automatic_generation_enabled": True,
        "automatic_generation_provider_id": "scripted-provider",
        "automatic_generation_provider_endpoint": (
            "https://generation.example.test/v1/generate"
        ),
        "automatic_generation_provider_model_id": "scripted-model",
        "automatic_generation_provider_auth_token": "provider-test-secret",
        "automatic_generation_provider_timeout_seconds": 12.5,
        "automatic_generation_provider_response_max_bytes": 2048,
    }
    return Settings(_env_file=None, **(values | overrides))


def _light_rag_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://tc:tc@localhost:5432/tc_test",
        "encryption_master_key": "worker-test-encryption-key-0000001",
        "light_rag_enabled": True,
        "light_rag_authority_id": "auxiliary-test",
        "light_rag_auxiliary_target_digest": "sha256:" + "f" * 64,
        "light_rag_provider_id": "official-lightrag-postgresql",
        "light_rag_workspace": "tc",
        "light_rag_postgres_host": "auxiliary.invalid",
        "light_rag_postgres_user": "lightrag_worker",
        "light_rag_postgres_password": "worker-lightrag-secret",
        "light_rag_postgres_database": "lightrag_worker",
        "light_rag_postgres_ssl_mode": "verify-full",
        "light_rag_apache_age_available": True,
        "light_rag_runtime_bootstrap": "approved.runtime:build",
        "light_rag_working_directory_root": (tmp_path / "lightrag").resolve(),
    }
    return Settings(_env_file=None, **(values | overrides))


def _light_rag_binding() -> ActiveGenerationBinding:
    digest = "sha256:" + "a" * 64
    return ActiveGenerationBinding(
        generation_id="generation-worker",
        pointer_version=1,
        pointer_updated_at=datetime(2026, 9, 2, tzinfo=UTC),
        authority_id="auxiliary-test",
        provider_id="official-lightrag-postgresql",
        provider_contract_version="tc-lightrag-generation-provider/v1",
        provider_distribution="lightrag-hku",
        provider_version="1.5.6",
        workspace="tc",
        auxiliary_target_digest="sha256:" + "f" * 64,
        runtime_config_digest="sha256:" + "e" * 64,
        manifest_digest=digest,
        corpus_digest=digest,
        source_ledger_digest=digest,
        projection_digest=digest,
        pgvector_digest=digest,
        document_count=1,
        registration_digest=digest,
    )


def test_automatic_generation_default_constructs_no_network_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
        encryption_master_key="worker-test-encryption-key-0000001",
    )

    def forbidden_transport() -> None:
        raise AssertionError("disabled composition constructed a network transport")

    monkeypatch.setattr(worker_main, "HttpxGenerationTransport", forbidden_transport)
    monkeypatch.setattr(
        worker_main,
        "HttpxOllamaKnowledgeExtractionTransport",
        forbidden_transport,
    )
    monkeypatch.setattr(
        worker_main,
        "HttpxOllamaEmbeddingTransport",
        forbidden_transport,
    )

    assert worker_main.build_generation_execution_runner(settings) is None
    assert (
        worker_main.build_automatic_knowledge_consumer(
            settings,
            session_factory=sessionmaker(),
        )
        is None
    )


def test_automatic_knowledge_composition_requires_explicit_opt_in_or_both_injections() -> (
    None
):
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
        encryption_master_key="worker-test-encryption-key-0000001",
    )

    class Extraction:
        async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction:
            del bundle
            return ProviderExtraction("fake", "fake", "fake", ())

    class Embedding:
        async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
            del texts
            return EmbeddingBatch("bge-m3:test", 1024, (), ())

    with pytest.raises(RuntimeError, match="requires extraction and embedding"):
        worker_main.build_automatic_knowledge_consumer(
            settings,
            session_factory=sessionmaker(),
            extraction_provider=Extraction(),
        )

    consumer = worker_main.build_automatic_knowledge_consumer(
        settings,
        session_factory=sessionmaker(),
        extraction_provider=Extraction(),
        embedding_provider=Embedding(),
        light_rag=(light_rag := LightRAGAuxiliaryIndex(None)),
    )
    assert consumer is not None
    assert consumer.light_rag is light_rag


def test_enabled_automatic_generation_requires_complete_provider_config() -> None:
    with pytest.raises(ValidationError, match="automatic_generation_provider_endpoint"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
            encryption_master_key="worker-test-encryption-key-0000001",
            automatic_generation_enabled=True,
        )


def test_production_generation_runner_is_single_call_and_redacted() -> None:
    secret = "provider-test-secret"
    transport = FakeGenerationTransport(
        [
            GenerationTransportResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                body=b'{"outputSchemaVersion":"1.0.0","cases":[]}',
            )
        ]
    )
    runner = worker_main.build_generation_execution_runner(
        _generation_settings(), transport=transport
    )
    assert runner is not None

    result = runner.run(
        {
            "inputSchemaVersion": "1.0.0",
            "requestedOutputSchemaVersion": "1.0.0",
        },
        provider_attempt_number=1,
    )

    assert result.attempts == 1
    assert len(transport.requests) == 1
    assert transport.requests[0].attempt == 1
    assert transport.requests[0].timeout_seconds == 12.5
    assert transport.requests[0].max_response_bytes == 2048
    assert transport.requests[0].headers["Authorization"] == f"Bearer {secret}"
    assert secret not in repr(runner)
    assert secret not in repr(transport.requests[0])


def test_generation_provider_endpoint_userinfo_is_rejected_without_secret_echo() -> (
    None
):
    secret = "provider-test-secret"
    settings = _generation_settings(
        automatic_generation_provider_endpoint=(
            "https://endpoint-user:endpoint-password@generation.example.test/v1"
        ),
        automatic_generation_provider_auth_token=secret,
    )

    with pytest.raises(GenerationConfigurationError) as exc_info:
        worker_main.build_generation_execution_runner(settings)

    assert "credentials" in str(exc_info.value)
    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)


def test_main_passes_composed_generation_runner_to_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _generation_settings()
    sentinel = object()
    captured: dict[str, object] = {}

    class FakeWorker:
        worker_id = "main-composition-test"

        @staticmethod
        def run_maintenance_once() -> bool:
            return False

        @staticmethod
        def run_socket_once() -> bool:
            return False

        @staticmethod
        def run_once() -> bool:
            return False

    class FakeEngine:
        @staticmethod
        def dispose() -> None:
            return None

    def fake_build_worker(
        received_settings: Settings,
        *,
        automatic_generation_consumer: object | None = None,
        generation_execution_runner: object | None = None,
    ) -> tuple[FakeWorker, FakeEngine]:
        del automatic_generation_consumer
        captured["settings"] = received_settings
        captured["runner"] = generation_execution_runner
        return FakeWorker(), FakeEngine()

    monkeypatch.setattr(worker_main, "load_settings", lambda: settings)
    monkeypatch.setattr(
        worker_main, "build_generation_execution_runner", lambda _settings: sentinel
    )
    monkeypatch.setattr(worker_main, "build_worker", fake_build_worker)
    monkeypatch.setattr(sys, "argv", ["tc-worker", "--once"])

    worker_main.main()

    assert captured == {"settings": settings, "runner": sentinel}


@pytest.mark.parametrize(
    "secret_like",
    [
        "AWS AKIA1234567890ABCDEF",
        "OpenAI sk-proj-abcdefghijklmnopqrstuvwxyz",
        "Slack xoxb-1234567890-abcdefghijklmnop",
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "Basic YWxhZGRpbjpvcGVuc2VzYW1l",
        "GitHub ghp_abcdefghijklmnopqrstuvwxyz123456",
        "Google AIzaabcdefghijklmnopqrstuvwxyz123456",
        "JWT eyJabcdefgh.eyJijklmnop.abcdefghijkl",
    ],
)
def test_operator_conditions_reject_common_secret_formats(secret_like: str) -> None:
    value = uuid.uuid4()
    with pytest.raises(RetryHistoryError) as exc_info:
        RetryHistoryService(sessionmaker()()).retry_with_conditions(
            value,
            idempotency_key="t07:secret:test:001",
            expected_root_job_id=value,
            expected_failed_job_id=value,
            expected_attempt_number=1,
            reason="operator retry",
            additional_conditions=f"다음 값을 사용하세요: {secret_like}",
        )

    assert exc_info.value.code == "VALIDATION_FAILED"
    assert exc_info.value.status_code == 422


def test_production_main_registers_notification_delivery_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_main, "validate_startup", lambda _settings: None)
    monkeypatch.setattr(
        worker_main,
        "create_engine",
        lambda *_args, **_kwargs: sqlalchemy_create_engine("sqlite://"),
    )
    values: dict[str, Any] = {
        "database_url": "postgresql+psycopg://tc:tc@localhost:5432/tc_test",
        "encryption_master_key": "worker-test-encryption-key-0000001",
        "slack_mode": "socket",
        "slack_app_token": "xapp-test",
        "slack_bot_token": "xoxb-test",
        "slack_workspace_id": "T123",
        "slack_generation_notifications_enabled": True,
    }

    configured, configured_engine = worker_main.build_worker(
        Settings(_env_file=None, **values)
    )
    disabled, disabled_engine = worker_main.build_worker(
        Settings(
            _env_file=None,
            **(values | {"slack_generation_notifications_enabled": False}),
        )
    )
    try:
        assert "SLACK_GENERATION_NOTIFICATION_DELIVERY" in configured.handlers
        assert "SLACK_GENERATION_NOTIFICATION_DELIVERY" not in disabled.handlers
    finally:
        configured_engine.dispose()
        disabled_engine.dispose()


def test_worker_composes_the_versioned_lightrag_resolver_from_a_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_main, "validate_startup", lambda _settings: None)
    monkeypatch.setattr(
        worker_main,
        "create_engine",
        lambda *_args, **_kwargs: sqlalchemy_create_engine("sqlite://"),
    )

    def factory(_binding: ActiveGenerationBinding) -> LightRAGGenerationRuntime:
        raise AssertionError("worker startup must not resolve an unused generation")

    worker, engine = worker_main.build_worker(
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
            encryption_master_key="worker-test-encryption-key-0000001",
        ),
        knowledge_light_rag_runtime_factory=factory,
    )
    try:
        assert isinstance(worker.light_rag_runtime, LightRAGRuntimeResolver)
    finally:
        worker.mark_stopped()
        engine.dispose()


def test_worker_default_settings_leave_lightrag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_transport() -> None:
        raise AssertionError("disabled worker constructed an Ollama transport")

    monkeypatch.setattr(worker_main, "validate_startup", lambda _settings: None)
    monkeypatch.setattr(
        worker_main,
        "create_engine",
        lambda *_args, **_kwargs: sqlalchemy_create_engine("sqlite://"),
    )
    monkeypatch.setattr(
        worker_main,
        "HttpxOllamaKnowledgeExtractionTransport",
        forbidden_transport,
    )
    monkeypatch.setattr(
        worker_main,
        "HttpxOllamaEmbeddingTransport",
        forbidden_transport,
    )
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
        encryption_master_key="worker-test-encryption-key-0000001",
    )

    worker, engine = worker_main.build_worker(settings)
    try:
        assert worker.light_rag_runtime is None
        assert {
            worker_main.KNOWLEDGE_PROJECTION_JOB_TYPE,
            worker_main.KNOWLEDGE_REINDEX_JOB_TYPE,
        } <= worker.handlers.keys()
        projection_adapter = worker.handlers[
            worker_main.KNOWLEDGE_PROJECTION_JOB_TYPE
        ].__self__
        assert projection_adapter.consumer is None
    finally:
        worker.mark_stopped()
        engine.dispose()


def test_default_worker_lazily_handles_explicit_api_knowledge_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[str] = []
    handled: list[tuple[str, object]] = []

    def extraction_transport() -> object:
        constructed.append("extraction")
        return object()

    def embedding_transport() -> object:
        constructed.append("embedding")
        return object()

    async def project_once(_consumer: object) -> None:
        handled.append(("projection", {}))
        return None

    async def reindex(_consumer: object, payload: dict[str, object]) -> None:
        handled.append(("reindex", payload))

    monkeypatch.setattr(worker_main, "validate_startup", lambda _settings: None)
    monkeypatch.setattr(
        worker_main,
        "create_engine",
        lambda *_args, **_kwargs: sqlalchemy_create_engine("sqlite://"),
    )
    monkeypatch.setattr(
        worker_main,
        "HttpxOllamaKnowledgeExtractionTransport",
        extraction_transport,
    )
    monkeypatch.setattr(
        worker_main,
        "HttpxOllamaEmbeddingTransport",
        embedding_transport,
    )
    monkeypatch.setattr(worker_main.KnowledgeOutboxConsumer, "run_once", project_once)
    monkeypatch.setattr(worker_main.KnowledgeJobConsumer, "_reindex", reindex)

    worker, engine = worker_main.build_worker(
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
            encryption_master_key="worker-test-encryption-key-0000001",
        )
    )
    try:
        assert constructed == []
        dispatch_job(
            make_job(worker_main.KNOWLEDGE_PROJECTION_JOB_TYPE, {"limit": 1}),
            worker.handlers,
        )
        dispatch_job(
            make_job(worker_main.KNOWLEDGE_REINDEX_JOB_TYPE, {"nodeIds": []}),
            worker.handlers,
        )
        assert constructed == ["extraction", "embedding"]
        assert handled == [
            ("projection", {}),
            ("reindex", {"nodeIds": []}),
        ]
    finally:
        worker.mark_stopped()
        engine.dispose()


def test_production_worker_claim_filter_includes_api_knowledge_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claimed_job_types: list[tuple[str, ...]] = []

    class FakeRepository:
        def __init__(self, _session: object) -> None:
            pass

        @staticmethod
        def claim_next(**kwargs: object) -> None:
            job_types = kwargs["job_types"]
            assert isinstance(job_types, tuple)
            claimed_job_types.append(job_types)
            return None

    monkeypatch.setattr(worker_main, "validate_startup", lambda _settings: None)
    monkeypatch.setattr(
        worker_main,
        "create_engine",
        lambda *_args, **_kwargs: sqlalchemy_create_engine("sqlite://"),
    )
    monkeypatch.setattr(worker_main, "JobRepository", FakeRepository)

    worker, engine = worker_main.build_worker(
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
            encryption_master_key="worker-test-encryption-key-0000001",
        )
    )
    try:
        assert worker.run_once() is False
        assert len(claimed_job_types) == 1
        assert {
            worker_main.KNOWLEDGE_PROJECTION_JOB_TYPE,
            worker_main.KNOWLEDGE_REINDEX_JOB_TYPE,
        } <= set(claimed_job_types[0])
    finally:
        worker.mark_stopped()
        engine.dispose()


def test_worker_builds_enabled_lightrag_factory_from_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(worker_main, "validate_startup", lambda _settings: None)
    monkeypatch.setattr(
        worker_main,
        "create_engine",
        lambda *_args, **_kwargs: sqlalchemy_create_engine("sqlite://"),
    )

    settings = _light_rag_settings(tmp_path)
    assert "worker-lightrag-secret" not in repr(settings)

    worker, engine = worker_main.build_worker(settings)
    try:
        assert isinstance(worker.light_rag_runtime, LightRAGRuntimeResolver)
    finally:
        worker.mark_stopped()
        engine.dispose()


def test_worker_lightrag_startup_failure_precedes_database_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _light_rag_settings(
        tmp_path, light_rag_provider_id="invalid/provider"
    )
    validated: list[Settings] = []
    monkeypatch.setattr(
        worker_main, "validate_startup", lambda received: validated.append(received)
    )

    with pytest.raises(RuntimeError, match="provider_unavailable"):
        worker_main.build_worker(settings)

    assert validated == []


def test_worker_rejects_partial_lightrag_configuration_without_secret_echo() -> (
    None
):
    secret = "partial-lightrag-password-must-stay-secret"
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
            encryption_master_key="worker-test-encryption-key-0000001",
            light_rag_enabled=True,
            light_rag_postgres_password=secret,
        )

    message = str(exc_info.value)
    assert "LightRAG runtime requires" in message
    assert "light_rag_provider_id" in message
    assert secret not in message


def test_worker_lightrag_active_generation_mismatch_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _light_rag_binding()

    class Source:
        @staticmethod
        def active_generation() -> ActiveGenerationBinding:
            return binding

    monkeypatch.setattr(worker_main, "validate_startup", lambda _settings: None)
    monkeypatch.setattr(
        worker_main,
        "create_engine",
        lambda *_args, **_kwargs: sqlalchemy_create_engine("sqlite://"),
    )
    monkeypatch.setattr(
        worker_main,
        "build_lightrag_runtime_resolver",
        lambda _engine, factory: LightRAGRuntimeResolver(Source(), factory),
    )

    worker, engine = worker_main.build_worker(_light_rag_settings(tmp_path))
    try:
        assert worker.light_rag_runtime is not None
        result = asyncio.run(worker.light_rag_runtime.search("mismatched generation"))
        assert result.status.value == "unavailable"
    finally:
        worker.mark_stopped()
        engine.dispose()


def test_worker_routes_automatic_knowledge_consumer_through_active_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Extraction:
        async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction:
            del bundle
            return ProviderExtraction("fake", "fake", "fake", ())

    class Embedding:
        async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
            del texts
            return EmbeddingBatch("bge-m3:test", 1024, (), ())

    monkeypatch.setattr(worker_main, "validate_startup", lambda _settings: None)
    monkeypatch.setattr(
        worker_main,
        "create_engine",
        lambda *_args, **_kwargs: sqlalchemy_create_engine("sqlite://"),
    )
    settings = _light_rag_settings(
        tmp_path, automatic_knowledge_projection_enabled=True
    )

    worker, engine = worker_main.build_worker(
        settings,
        knowledge_extraction_provider=Extraction(),
        knowledge_embedding_provider=Embedding(),
    )
    try:
        handler = worker.handlers["SLACK_AUTOMATIC_GENERATION"]
        slack_runtime = handler.__self__
        consumer = slack_runtime.automatic_generation_consumer
        assert consumer is not None
        assert consumer.light_rag is worker.light_rag_runtime
        assert isinstance(consumer.light_rag, LightRAGRuntimeResolver)
    finally:
        worker.mark_stopped()
        engine.dispose()


def test_worker_handles_api_knowledge_jobs_through_active_resolver_and_finalizes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Extraction:
        async def extract(self, bundle: SlackSourceBundle) -> ProviderExtraction:
            del bundle
            return ProviderExtraction("fake", "fake", "fake", ())

    class Embedding:
        async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
            del texts
            return EmbeddingBatch("bge-m3:test", 1024, (), ())

    class Resolver:
        finalized = False

        async def finalize(self) -> None:
            self.finalized = True

    resolver = Resolver()
    monkeypatch.setattr(worker_main, "validate_startup", lambda _settings: None)
    monkeypatch.setattr(
        worker_main,
        "create_engine",
        lambda *_args, **_kwargs: sqlalchemy_create_engine("sqlite://"),
    )
    monkeypatch.setattr(
        worker_main,
        "build_lightrag_runtime_resolver",
        lambda _engine, _factory: resolver,
    )

    worker, engine = worker_main.build_worker(
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
            encryption_master_key="worker-test-encryption-key-0000001",
            automatic_knowledge_projection_enabled=True,
        ),
        knowledge_extraction_provider=Extraction(),
        knowledge_embedding_provider=Embedding(),
        knowledge_light_rag_runtime_factory=lambda _binding: pytest.fail(
            "unused generation must remain lazy"
        ),
    )
    handled: list[tuple[str, object]] = []
    projection_handler = worker.handlers[
        worker_main.KNOWLEDGE_PROJECTION_JOB_TYPE
    ]
    reindex_handler = worker.handlers[worker_main.KNOWLEDGE_REINDEX_JOB_TYPE]
    projection_adapter = projection_handler.__self__
    reindex_adapter = reindex_handler.__self__
    assert projection_adapter is reindex_adapter
    consumer = projection_adapter.consumer
    assert consumer is not None
    assert consumer.light_rag is resolver
    assert consumer.outbox_consumer.light_rag is resolver

    projection_results = [object(), None]

    async def project_once() -> object | None:
        handled.append(("projection", len(projection_results)))
        return projection_results.pop(0)

    async def reindex(payload: dict[str, object]) -> None:
        handled.append(("reindex", payload))

    monkeypatch.setattr(consumer.outbox_consumer, "run_once", project_once)
    monkeypatch.setattr(consumer, "_reindex", reindex)
    try:
        dispatch_job(
            make_job(worker_main.KNOWLEDGE_PROJECTION_JOB_TYPE, {"limit": 5}),
            worker.handlers,
        )
        dispatch_job(
            make_job(
                worker_main.KNOWLEDGE_REINDEX_JOB_TYPE,
                {"nodeIds": [str(uuid.uuid4())]},
            ),
            worker.handlers,
        )
        assert [name for name, _value in handled] == [
            "projection",
            "projection",
            "reindex",
        ]
    finally:
        worker.mark_stopped()
        engine.dispose()
    assert resolver.finalized is True


def test_worker_shutdown_finalizes_lightrag_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Resolver:
        finalized = False

        async def finalize(self) -> None:
            self.finalized = True

    resolver = Resolver()
    monkeypatch.setattr(worker_main, "validate_startup", lambda _settings: None)
    monkeypatch.setattr(
        worker_main,
        "create_engine",
        lambda *_args, **_kwargs: sqlalchemy_create_engine("sqlite://"),
    )
    monkeypatch.setattr(
        worker_main,
        "build_lightrag_runtime_resolver",
        lambda _engine, _factory: resolver,
    )

    worker, engine = worker_main.build_worker(_light_rag_settings(tmp_path))
    try:
        worker.mark_stopped()
        assert resolver.finalized is True
    finally:
        engine.dispose()


def test_destination_provenance_conflict_is_a_permanent_worker_failure() -> None:
    class ConflictingDelivery:
        def deliver(self, _intent_id: uuid.UUID, _claim: ClaimedJob) -> None:
            raise GenerationSlackNotificationConflict("cross-workspace destination")

    runtime = SlackWorkerRuntime(
        session_factory=sessionmaker(),
        notification_delivery=ConflictingDelivery(),  # type: ignore[arg-type]
    )
    claim = make_job(
        "SLACK_GENERATION_NOTIFICATION_DELIVERY",
        {"notificationIntentId": str(uuid.uuid4())},
    )

    with pytest.raises(PermanentJobError, match="provenance is invalid"):
        runtime.deliver_generation_notification(claim.payload, claim)


def test_scheduled_maintenance_runs_immediately_then_at_the_configured_interval() -> (
    None
):
    now = [100.0]
    calls: list[str] = []
    worker = Worker(
        session_factory=sessionmaker(),
        settings=Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
            encryption_master_key="worker-test-encryption-key-0000001",
        ),
        worker_id="maintenance-test",
        scheduled_maintenance=lambda: calls.append("scheduled"),
        maintenance_interval_seconds=10,
        monotonic=lambda: now[0],
    )

    assert worker.run_maintenance_once()
    assert not worker.run_maintenance_once()
    now[0] = 109.99
    assert not worker.run_maintenance_once()
    now[0] = 110.0
    assert worker.run_maintenance_once()
    assert calls == ["scheduled", "scheduled"]


def test_worker_exports_bounded_poll_and_job_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job = make_job("SLACK_AUTOMATIC_GENERATION", {"private": "raw Slack text"})
    claims = [job]

    class Transaction:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeSession:
        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        @staticmethod
        def begin() -> Transaction:
            return Transaction()

    class FakeRepository:
        def __init__(self, _session: FakeSession) -> None:
            pass

        @staticmethod
        def claim_next(**_kwargs: object) -> ClaimedJob | None:
            return claims.pop(0) if claims else None

        @staticmethod
        def succeed(**_kwargs: object) -> bool:
            return True

    monkeypatch.setattr(worker_main, "JobRepository", FakeRepository)
    target = tmp_path / "worker.prom"
    worker = Worker(
        session_factory=lambda: FakeSession(),  # type: ignore[arg-type]
        settings=Settings(
            _env_file=None,
            database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
            encryption_master_key="worker-test-encryption-key-0000001",
        ),
        worker_id="metrics-test",
        handlers={"SLACK_AUTOMATIC_GENERATION": lambda _payload, _claim: None},
        wall_clock=lambda: 1234.5,
        metrics_exporter=PrometheusTextfileExporter(target),
    )

    assert worker.run_once()
    rendered = target.read_text()
    assert 'tc_worker_poll_total{outcome="claimed"} 1' in rendered
    assert (
        'tc_worker_jobs_total{component="generation",outcome="success"} 1' in rendered
    )
    assert "tc_worker_last_success_timestamp_seconds 1234.5" in rendered
    assert "raw Slack text" not in rendered
    assert str(job.id) not in rendered

    worker.mark_stopped()
    assert "tc_worker_up 0" in target.read_text()
