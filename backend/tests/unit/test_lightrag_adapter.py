from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

import pytest

from tc_backend.knowledge import (
    BGE_M3_DIMENSION,
    BGE_M3_MODEL,
    LIGHTRAG_PINNED_VERSION,
    BgeM3OllamaEmbeddingProvider,
    EmbeddingResponseError,
    EmbeddingUnavailable,
    KnowledgeFallback,
    LightRAGAuxiliaryIndex,
    LightRAGCallStatus,
    LightRAGIndexDocument,
    LightRAGOperationError,
    LightRAGPostgresConnectionSettings,
    LightRAGStorageConfig,
    LightRAGUnavailableError,
    build_official_lightrag_postgres_runtime,
)


class FakeOllamaTransport:
    def __init__(
        self,
        *,
        payload: Mapping[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.requests: list[dict[str, object]] = []

    async def embed(
        self,
        *,
        host: str,
        model: str,
        inputs: tuple[str, ...],
        truncate: bool,
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self.requests.append(
            {
                "host": host,
                "model": model,
                "inputs": inputs,
                "truncate": truncate,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.payload is not None
        return self.payload


class FakeLightRAGCore:
    def __init__(self) -> None:
        self.initialize_error: Exception | None = None
        self.insert_error: Exception | None = None
        self.query_error: Exception | None = None
        self.query_delay_seconds = 0.0
        self.query_result = "auxiliary context"
        self.finalize_error: Exception | None = None
        self.initialized = 0
        self.finalized = 0
        self.insert_calls: list[dict[str, object]] = []
        self.query_calls: list[dict[str, object]] = []

    async def initialize_storages(self) -> object:
        self.initialized += 1
        if self.initialize_error is not None:
            raise self.initialize_error
        return None

    async def ainsert(
        self,
        input: str | list[str],
        split_by_character: str | None = None,
        split_by_character_only: bool = False,
        ids: str | list[str] | None = None,
        file_paths: str | list[str] | None = None,
        track_id: str | None = None,
    ) -> str:
        self.insert_calls.append(
            {"input": input, "ids": ids, "file_paths": file_paths, "track_id": track_id}
        )
        if self.insert_error is not None:
            raise self.insert_error
        return "track-1"

    async def aquery(
        self,
        query: str,
        param: object,
        system_prompt: str | None = None,
    ) -> str | AsyncIterator[str]:
        self.query_calls.append({"query": query, "param": param})
        if self.query_delay_seconds:
            await asyncio.sleep(self.query_delay_seconds)
        if self.query_error is not None:
            raise self.query_error
        return self.query_result

    async def finalize_storages(self) -> object:
        self.finalized += 1
        if self.finalize_error is not None:
            raise self.finalize_error
        return None


def _storage_config(**overrides: object) -> LightRAGStorageConfig:
    values: dict[str, object] = {
        "workspace": "tc-test",
        "postgres": LightRAGPostgresConnectionSettings(
            host="127.0.0.1",
            port=55432,
            user="tc_lightrag",
            password="test-only-password",
            database="tc_lightrag_test",
            max_connections=8,
            ssl_mode="disable",
        ),
    }
    values.update(overrides)
    return LightRAGStorageConfig(**values)


def _adapter(
    core: FakeLightRAGCore, *, query_timeout_seconds: float = 5.0
) -> LightRAGAuxiliaryIndex:
    return LightRAGAuxiliaryIndex(
        core,
        query_param_factory=lambda mode: {"mode": mode},
        storage_config=_storage_config(),
        query_timeout_seconds=query_timeout_seconds,
    )


def test_bge_m3_ollama_batch_contract_validates_dimension_and_hashes() -> None:
    transport = FakeOllamaTransport(
        payload={
            "model": BGE_M3_MODEL,
            "embeddings": [[0.0] * BGE_M3_DIMENSION, [0.5] * BGE_M3_DIMENSION],
        }
    )
    provider = BgeM3OllamaEmbeddingProvider(transport)

    batch = asyncio.run(provider.embed(["첫 문장", "둘째 문장"]))

    assert batch.model == "bge-m3:latest"
    assert batch.dimension == 1024
    assert len(batch.vectors) == 2
    assert batch.content_hashes[0] != batch.content_hashes[1]
    assert transport.requests[0]["inputs"] == ("첫 문장", "둘째 문장")


def test_ollama_stopped_is_typed_unavailable_and_bad_shape_is_response_error() -> None:
    stopped = BgeM3OllamaEmbeddingProvider(FakeOllamaTransport(error=ConnectionError("refused")))
    with pytest.raises(EmbeddingUnavailable):
        asyncio.run(stopped.embed(["text"]))

    wrong_dimension = BgeM3OllamaEmbeddingProvider(
        FakeOllamaTransport(payload={"model": BGE_M3_MODEL, "embeddings": [[0.0]]})
    )
    with pytest.raises(EmbeddingResponseError, match="dimension"):
        asyncio.run(wrong_dimension.embed(["text"]))


def test_official_postgres_storage_names_and_age_requirement_are_explicit() -> None:
    config = _storage_config()
    config.validate()

    assert config.kv_storage == "PGKVStorage"
    assert config.vector_storage == "PGVectorStorage"
    assert config.doc_status_storage == "PGDocStatusStorage"
    assert config.graph_storage == "PGGraphStorage"
    assert config.requires_apache_age is True
    assert config.as_light_rag_kwargs() == {
        "kv_storage": "PGKVStorage",
        "vector_storage": "PGVectorStorage",
        "doc_status_storage": "PGDocStatusStorage",
        "graph_storage": "PGGraphStorage",
    }
    assert config.as_official_environment() == {
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "55432",
        "POSTGRES_USER": "tc_lightrag",
        "POSTGRES_PASSWORD": "test-only-password",
        "POSTGRES_DATABASE": "tc_lightrag_test",
        "POSTGRES_MAX_CONNECTIONS": "8",
        "POSTGRES_WORKSPACE": "tc-test",
        "POSTGRES_SSL_MODE": "disable",
    }


def test_official_runtime_validates_pin_adapters_connection_and_lifecycle() -> None:
    config = _storage_config()
    environment = config.as_official_environment()
    captured: dict[str, object] = {}
    core = FakeLightRAGCore()

    def factory(**kwargs: object) -> FakeLightRAGCore:
        captured.update(kwargs)
        for name in ("kv_storage", "vector_storage", "doc_status_storage", "graph_storage"):
            setattr(core, name, kwargs[name])
        return core

    runtime = build_official_lightrag_postgres_runtime(
        config,
        working_dir="/tmp/tc-lightrag-test",
        llm_model_func=object(),
        embedding_func=object(),
        core_factory=factory,
        installed_version=LIGHTRAG_PINNED_VERSION,
        environ=environment,
    )

    assert captured["workspace"] == "tc-test"
    assert captured["kv_storage"] == "PGKVStorage"
    assert captured["vector_storage"] == "PGVectorStorage"
    assert captured["doc_status_storage"] == "PGDocStatusStorage"
    assert captured["graph_storage"] == "PGGraphStorage"
    asyncio.run(runtime.initialize_storages())
    assert runtime.initialized is True
    asyncio.run(runtime.finalize_storages())
    assert runtime.initialized is False
    assert core.initialized == 1
    assert core.finalized == 1


def test_missing_or_invalid_postgres_config_fails_before_official_core_construction() -> None:
    with pytest.raises(ValueError, match="connection settings are required"):
        LightRAGStorageConfig().validate()
    missing_result = asyncio.run(
        LightRAGAuxiliaryIndex(
            FakeLightRAGCore(), storage_config=LightRAGStorageConfig()
        ).initialize(apache_age_available=True)
    )
    assert missing_result.status is LightRAGCallStatus.ERROR
    assert missing_result.fallback is KnowledgeFallback.TYPED_GRAPH_PGVECTOR
    with pytest.raises(ValueError, match="host must not be empty"):
        _storage_config(
            postgres=LightRAGPostgresConnectionSettings(
                host=" ",
                port=5432,
                user="tc",
                password="secret",
                database="tc_test",
            )
        ).validate()
    with pytest.raises(ValueError, match="kv_storage"):
        _storage_config(kv_storage="JsonKVStorage").validate()
    with pytest.raises(ValueError, match="environment is missing"):
        build_official_lightrag_postgres_runtime(
            _storage_config(),
            working_dir="/tmp/tc-lightrag-test",
            llm_model_func=object(),
            embedding_func=object(),
            core_factory=lambda **kwargs: FakeLightRAGCore(),
            installed_version=LIGHTRAG_PINNED_VERSION,
            environ={},
        )


def test_official_runtime_rejects_dependency_version_drift_with_typed_fallback() -> None:
    config = _storage_config()
    with pytest.raises(LightRAGUnavailableError) as captured:
        build_official_lightrag_postgres_runtime(
            config,
            working_dir="/tmp/tc-lightrag-test",
            llm_model_func=object(),
            embedding_func=object(),
            core_factory=lambda **kwargs: FakeLightRAGCore(),
            installed_version="1.5.5",
            environ=config.as_official_environment(),
        )

    assert captured.value.retryable is False
    assert captured.value.fallback is KnowledgeFallback.TYPED_GRAPH_PGVECTOR


def test_missing_age_returns_unavailable_without_claiming_live_initialization() -> None:
    core = FakeLightRAGCore()
    adapter = _adapter(core)

    result = asyncio.run(adapter.initialize(apache_age_available=False))

    assert result.status is LightRAGCallStatus.UNAVAILABLE
    assert result.should_fallback is True
    assert result.fallback is KnowledgeFallback.TYPED_GRAPH_PGVECTOR
    assert isinstance(result.failure, LightRAGUnavailableError)
    assert core.initialized == 0


def test_fake_storage_initialization_failure_is_typed_unavailable() -> None:
    core = FakeLightRAGCore()
    core.initialize_error = ConnectionError("postgres unavailable")
    adapter = _adapter(core)

    result = asyncio.run(adapter.initialize(apache_age_available=True))

    assert result.status is LightRAGCallStatus.UNAVAILABLE
    assert isinstance(result.failure, LightRAGUnavailableError)
    assert result.fallback is KnowledgeFallback.TYPED_GRAPH_PGVECTOR


def test_lightrag_ainsert_uses_postgres_id_but_receipt_is_not_authoritative() -> None:
    core = FakeLightRAGCore()
    adapter = _adapter(core)
    assert (
        asyncio.run(adapter.initialize(apache_age_available=True)).status is LightRAGCallStatus.OK
    )

    result = asyncio.run(
        adapter.index_document(
            LightRAGIndexDocument(
                postgres_record_id="knowledge-node-123",
                content="인증번호는 5분간 유효하다.",
                source_uri="slack://T_TEST/C_TEST/100.1",
            )
        )
    )

    receipt = result.require_value()
    assert receipt.postgres_record_id == "knowledge-node-123"
    assert receipt.authoritative is False
    assert core.insert_calls == [
        {
            "input": "인증번호는 5분간 유효하다.",
            "ids": "knowledge-node-123",
            "file_paths": "slack://T_TEST/C_TEST/100.1",
            "track_id": None,
        }
    ]


def test_lightrag_failure_results_explicitly_route_to_typed_graph_pgvector() -> None:
    core = FakeLightRAGCore()
    adapter = _adapter(core)
    asyncio.run(adapter.initialize(apache_age_available=True))
    core.insert_error = RuntimeError("corrupt auxiliary index")

    result = asyncio.run(
        adapter.index_document(LightRAGIndexDocument("pg-id", "content", "slack://source"))
    )

    assert result.status is LightRAGCallStatus.ERROR
    assert isinstance(result.failure, LightRAGOperationError)
    assert result.failure.code == "LIGHTRAG_OPERATION_ERROR"
    assert result.fallback is KnowledgeFallback.TYPED_GRAPH_PGVECTOR
    with pytest.raises(LightRAGOperationError):
        result.require_value()


def test_lightrag_query_transport_failure_is_unavailable_and_success_is_auxiliary() -> None:
    core = FakeLightRAGCore()
    adapter = _adapter(core)
    asyncio.run(adapter.initialize(apache_age_available=True))
    core.query_error = ConnectionError("down")

    unavailable = asyncio.run(adapter.search("인증번호", mode="hybrid"))

    assert unavailable.status is LightRAGCallStatus.UNAVAILABLE
    assert unavailable.fallback is KnowledgeFallback.TYPED_GRAPH_PGVECTOR

    core.query_error = None
    success = asyncio.run(adapter.search("인증번호", mode="mix"))
    candidate = success.require_value()
    assert candidate.context == "auxiliary context"
    assert candidate.authoritative is False
    assert candidate.requires_postgresql_resolution is True
    assert core.query_calls[-1]["param"] == {"mode": "mix"}


def test_lightrag_query_timeout_falls_back_then_recovers_without_cached_hints() -> None:
    core = FakeLightRAGCore()
    adapter = _adapter(core, query_timeout_seconds=0.001)
    asyncio.run(adapter.initialize(apache_age_available=True))
    core.query_delay_seconds = 0.05

    timed_out = asyncio.run(adapter.search("인증번호"))

    assert timed_out.status is LightRAGCallStatus.UNAVAILABLE
    assert timed_out.value is None
    assert timed_out.fallback is KnowledgeFallback.TYPED_GRAPH_PGVECTOR
    core.query_delay_seconds = 0.0
    recovered = asyncio.run(adapter.search("인증번호"))
    assert recovered.status is LightRAGCallStatus.OK
    assert recovered.require_value().postgres_record_ids == ()


def test_failed_reinitialization_clears_ready_state_until_retry_succeeds() -> None:
    core = FakeLightRAGCore()
    adapter = _adapter(core)
    assert (
        asyncio.run(adapter.initialize(apache_age_available=True)).status is LightRAGCallStatus.OK
    )
    core.initialize_error = ConnectionError("replacement pool unavailable")

    failed = asyncio.run(adapter.initialize(apache_age_available=True))
    calls_before_search = len(core.query_calls)

    assert failed.status is LightRAGCallStatus.UNAVAILABLE
    assert asyncio.run(adapter.search("fallback")).status is LightRAGCallStatus.UNAVAILABLE
    assert len(core.query_calls) == calls_before_search
    core.initialize_error = None
    assert (
        asyncio.run(adapter.initialize(apache_age_available=True)).status is LightRAGCallStatus.OK
    )
    assert asyncio.run(adapter.search("recovered")).status is LightRAGCallStatus.OK


@pytest.mark.parametrize("timeout", (0.0, -1.0, float("inf"), float("nan"), True))
def test_lightrag_query_timeout_must_be_positive_and_finite(timeout: float) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        _adapter(FakeLightRAGCore(), query_timeout_seconds=timeout)


def test_lightrag_query_extracts_ordered_deduplicated_postgres_uuid_hints() -> None:
    first = "018f3d5c-2f37-7ad4-8c9e-1a2b3c4d5e6f"
    second = "018f3d5c-2f37-7ad4-9c9e-1a2b3c4d5e70"
    core = FakeLightRAGCore()
    core.query_result = (
        f"source postgres://knowledge/nodes/{first}\n"
        f"duplicate {first.upper()} and evidence {second}; ungrounded words are ignored"
    )
    adapter = _adapter(core)
    asyncio.run(adapter.initialize(apache_age_available=True))

    candidate = asyncio.run(adapter.search("인증번호")).require_value()

    assert candidate.postgres_record_ids == (first, second)
    assert candidate.authoritative is False
    assert candidate.requires_postgresql_resolution is True


def test_finalize_failure_is_typed_and_adapter_returns_to_fallback_state() -> None:
    core = FakeLightRAGCore()
    adapter = _adapter(core)
    assert (
        asyncio.run(adapter.initialize(apache_age_available=True)).status is LightRAGCallStatus.OK
    )
    core.finalize_error = RuntimeError("pool close failed")

    finalized = asyncio.run(adapter.finalize())

    assert finalized.status is LightRAGCallStatus.ERROR
    assert finalized.fallback is KnowledgeFallback.TYPED_GRAPH_PGVECTOR
    assert asyncio.run(adapter.search("fallback")).status is LightRAGCallStatus.UNAVAILABLE
