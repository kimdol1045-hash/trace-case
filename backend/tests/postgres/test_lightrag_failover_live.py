from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from tc_backend.db.models import KnowledgeOwnerType, KnowledgeSourceKind
from tc_backend.knowledge import (
    BGE_M3_DIMENSION,
    EmbeddingBatch,
    LightRAGAuxiliaryIndex,
    LightRAGCallStatus,
    LightRAGPostgresConnectionSettings,
    LightRAGStorageConfig,
    build_official_lightrag_postgres_runtime,
)
from tc_backend.repositories.knowledge_repository import EvidenceInput, KnowledgeRepository
from tc_backend.services.knowledge_service import HybridSearchResult, KnowledgeService

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("TC_LIGHTRAG_FAILOVER_LIVE") != "1",
        reason="run through scripts/rehearse_lightrag_failover.py --run",
    ),
]

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
EXPECTED_PROJECT = "tc-lightrag-failover-rehearsal"
EXPECTED_AUTHORITATIVE_CONTAINER = "tc-lightrag-failover-authoritative-postgres"
EXPECTED_AUXILIARY_CONTAINER = "tc-lightrag-failover-aux-postgres"
QUERY_TIMEOUT_SECONDS = 1.0
REQUEST_LATENCY_BOUND_SECONDS = 2.5
RECOVERY_BOUND_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class _LiveSettings:
    authoritative_url: str = field(repr=False)
    lightrag: LightRAGStorageConfig


class _DeterministicQueryEmbedding:
    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        vector = tuple([1.0, *([0.0] * (BGE_M3_DIMENSION - 1))])
        normalized = tuple(text.strip() for text in texts)
        return EmbeddingBatch(
            model="tc-failover-deterministic",
            dimension=BGE_M3_DIMENSION,
            vectors=tuple(vector for _ in normalized),
            content_hashes=tuple(
                hashlib.sha256(text.encode("utf-8")).hexdigest() for text in normalized
            ),
        )


def _required_environment() -> _LiveSettings:
    required = (
        "TC_TEST_DATABASE_URL",
        "TC_LIGHTRAG_FAILOVER_PROJECT",
        "TC_LIGHTRAG_FAILOVER_AUTHORITATIVE_CONTAINER",
        "TC_LIGHTRAG_FAILOVER_AUXILIARY_CONTAINER",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DATABASE",
        "POSTGRES_MAX_CONNECTIONS",
        "POSTGRES_WORKSPACE",
        "POSTGRES_SSL_MODE",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.fail("live failover environment is incomplete: " + ", ".join(missing))
    if os.environ["TC_LIGHTRAG_FAILOVER_PROJECT"] != EXPECTED_PROJECT:
        pytest.fail("live failover project does not match the scoped rehearsal project")
    if (
        os.environ["TC_LIGHTRAG_FAILOVER_AUTHORITATIVE_CONTAINER"]
        != EXPECTED_AUTHORITATIVE_CONTAINER
    ):
        pytest.fail("authoritative container does not match the scoped rehearsal container")
    if os.environ["TC_LIGHTRAG_FAILOVER_AUXILIARY_CONTAINER"] != EXPECTED_AUXILIARY_CONTAINER:
        pytest.fail("auxiliary container does not match the scoped rehearsal container")

    authoritative_url = make_url(os.environ["TC_TEST_DATABASE_URL"])
    if not authoritative_url.drivername.startswith("postgresql"):
        pytest.fail("TC_TEST_DATABASE_URL must use PostgreSQL")
    for label, database in (
        ("authoritative", authoritative_url.database),
        ("auxiliary", os.environ["POSTGRES_DATABASE"]),
    ):
        if not database or "test" not in database.casefold():
            pytest.fail(f"{label} rehearsal database must contain 'test'")
    if authoritative_url.database == os.environ["POSTGRES_DATABASE"]:
        pytest.fail("authoritative and auxiliary databases must be separate")
    return _LiveSettings(
        authoritative_url=os.environ["TC_TEST_DATABASE_URL"],
        lightrag=_storage_config(os.environ),
    )


def _docker_inspect(container: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "inspect", "--type", "container", container],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    values = json.loads(result.stdout)
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise AssertionError(f"unexpected docker inspect result for {container}")
    inspected = cast(dict[str, Any], values[0])
    labels = inspected.get("Config", {}).get("Labels", {})
    assert labels.get("com.docker.compose.project") == EXPECTED_PROJECT
    assert labels.get("com.tc.rehearsal") == EXPECTED_PROJECT
    return inspected


def _sever_auxiliary() -> None:
    inspected = _docker_inspect(EXPECTED_AUXILIARY_CONTAINER)
    assert inspected["State"]["Running"] is True
    subprocess.run(
        ["docker", "kill", "--signal", "KILL", EXPECTED_AUXILIARY_CONTAINER],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert _docker_inspect(EXPECTED_AUXILIARY_CONTAINER)["State"]["Running"] is False


def _start_auxiliary() -> None:
    _docker_inspect(EXPECTED_AUXILIARY_CONTAINER)
    subprocess.run(
        ["docker", "start", EXPECTED_AUXILIARY_CONTAINER],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )


def _evidence(label: str) -> EvidenceInput:
    return EvidenceInput.from_content(
        source_kind=KnowledgeSourceKind.LEDGER_ENTITY,
        entity_type="LIGHTRAG_FAILOVER_FIXTURE",
        entity_id=uuid.uuid4(),
        revision_key="1",
        content=label,
        recorded_at=datetime.now(UTC),
    )


def _prepare_authoritative_database(url: str) -> tuple[Engine, dict[str, uuid.UUID]]:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as connection:
        server_version_num = int(connection.scalar(text("SHOW server_version_num")))
        assert server_version_num >= 160000
        assert connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        )
        connection.execute(
            text(
                "TRUNCATE TABLE knowledge_projection_events, knowledge_embeddings, "
                "knowledge_evidence, knowledge_edges, knowledge_nodes, "
                "knowledge_source_records CASCADE"
            )
        )

    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as session:
        repository = KnowledgeRepository(session)
        feature = repository.create_node(
            node_type="FEATURE",
            label="Password reset",
            evidence=_evidence("authoritative feature"),
        )
        exact = repository.create_node(
            node_type="REQUIREMENT",
            label="Password reset authoritative requirement",
            evidence=_evidence("authoritative exact"),
        )
        vector = repository.create_node(
            node_type="CONSTRAINT",
            label="Authoritative semantic constraint",
            evidence=_evidence("authoritative vector"),
        )
        graph = repository.create_node(
            node_type="TEST_CASE",
            label="Authoritative graph regression",
            evidence=_evidence("authoritative graph"),
        )
        hinted = repository.create_node(
            node_type="RISK",
            label="Auxiliary-only current risk",
            evidence=_evidence("authoritative resolution for auxiliary hint"),
        )
        repository.create_edge(
            source_node_id=graph.id,
            predicate="COVERS",
            target_node_id=feature.id,
            evidence=_evidence("authoritative typed graph edge"),
        )
        vector_value = tuple([1.0, *([0.0] * (BGE_M3_DIMENSION - 1))])
        repository.put_embedding(
            owner_type=KnowledgeOwnerType.NODE,
            owner_id=vector.id,
            model_version="tc-failover-deterministic",
            embedding=vector_value,
            content=vector.label,
        )
    return engine, {
        "feature": feature.id,
        "exact": exact.id,
        "vector": vector.id,
        "graph": graph.id,
        "hinted": hinted.id,
    }


def _storage_config(environment: Mapping[str, str]) -> LightRAGStorageConfig:
    return LightRAGStorageConfig(
        workspace=environment["POSTGRES_WORKSPACE"],
        postgres=LightRAGPostgresConnectionSettings(
            host=environment["POSTGRES_HOST"],
            port=int(environment["POSTGRES_PORT"]),
            user=environment["POSTGRES_USER"],
            password=environment["POSTGRES_PASSWORD"],
            database=environment["POSTGRES_DATABASE"],
            max_connections=int(environment["POSTGRES_MAX_CONNECTIONS"]),
            ssl_mode=environment["POSTGRES_SSL_MODE"],
        ),
    )


async def _wait_for_auxiliary(config: LightRAGStorageConfig, timeout_seconds: float) -> None:
    import asyncpg

    postgres = config.postgres
    assert postgres is not None
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            connection = await asyncpg.connect(
                host=postgres.host,
                port=postgres.port,
                user=postgres.user,
                password=postgres.password,
                database=postgres.database,
                ssl=False,
                timeout=1.0,
            )
            try:
                extensions = {
                    row["extname"]
                    for row in await connection.fetch(
                        "SELECT extname FROM pg_extension WHERE extname IN ('age', 'vector')"
                    )
                }
                assert extensions == {"age", "vector"}
                assert int(await connection.fetchval("SHOW server_version_num")) >= 160000
                return
            finally:
                await connection.close()
        except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
            last_error = exc
            await asyncio.sleep(0.2)
    raise AssertionError("LightRAG auxiliary PostgreSQL did not recover in time") from last_error


async def _exercise_live_failover(
    engine: Engine,
    node_ids: Mapping[str, uuid.UUID],
    config: LightRAGStorageConfig,
    tmp_path: Path,
) -> dict[str, object]:
    import asyncpg
    import numpy as np
    from lightrag import QueryParam
    from lightrag.utils import EmbeddingFunc
    from lightrag.utils import logger as lightrag_logger

    lightrag_logger.setLevel(logging.ERROR)

    async def deterministic_embedding(texts: list[str], **kwargs: object) -> np.ndarray:
        del kwargs
        vectors = np.zeros((len(texts), BGE_M3_DIMENSION), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors

    async def unused_llm(prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        return json.dumps(
            {
                "high_level_keywords": ["password reset"],
                "low_level_keywords": ["password reset"],
            }
        )

    runtime = build_official_lightrag_postgres_runtime(
        config,
        working_dir=tmp_path,
        llm_model_func=unused_llm,
        embedding_func=EmbeddingFunc(
            embedding_dim=BGE_M3_DIMENSION,
            max_token_size=8192,
            model_name="tc-failover-deterministic",
            func=deterministic_embedding,
        ),
    )
    adapter = LightRAGAuxiliaryIndex(
        runtime,
        query_param_factory=lambda mode: QueryParam(
            mode=mode,
            only_need_context=True,
            stream=False,
            hl_keywords=["password reset"],
            ll_keywords=["password reset"],
        ),
        storage_config=config,
        query_timeout_seconds=QUERY_TIMEOUT_SECONDS,
    )
    initialized = await adapter.initialize(apache_age_available=True)
    assert initialized.status is LightRAGCallStatus.OK

    core = cast(Any, runtime)._core
    assert core.full_docs.__class__.__module__ == "lightrag.kg.postgres_impl"
    assert core.chunk_entity_relation_graph.__class__.__module__ == "lightrag.kg.postgres_impl"
    assert isinstance(core.full_docs.db.pool, asyncpg.Pool)
    await core.ainsert_custom_kg(
        {
            "chunks": [
                {
                    "content": (
                        "Password reset auxiliary risk grounded by authoritative node "
                        f"{node_ids['hinted']}"
                    ),
                    "source_id": "tc-live-failover-source",
                    "file_path": "tc-live-failover-fixture",
                }
            ],
            "entities": [
                {
                    "entity_name": str(node_ids["hinted"]),
                    "entity_type": "RISK",
                    "description": "Password reset auxiliary risk",
                    "source_id": "tc-live-failover-source",
                    "file_path": "tc-live-failover-fixture",
                }
            ],
            "relationships": [],
        }
    )

    postgres = config.postgres
    assert postgres is not None
    auxiliary_connection = await asyncpg.connect(
        host=postgres.host,
        port=postgres.port,
        user=postgres.user,
        password=postgres.password,
        database=postgres.database,
        ssl=False,
    )
    try:
        tables = {
            row["tablename"]
            for row in await auxiliary_connection.fetch(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename LIKE 'lightrag_%'"
            )
        }
        graphs = {
            row["name"] for row in await auxiliary_connection.fetch("SELECT name FROM ag_catalog.ag_graph")
        }
    finally:
        await auxiliary_connection.close()
    assert {"lightrag_doc_full", "lightrag_doc_status", "lightrag_llm_cache"}.issubset(tables)
    assert any(name.endswith("_chunk_entity_relation") for name in graphs)

    sessions = sessionmaker(engine, expire_on_commit=False)

    async def search() -> HybridSearchResult:
        with sessions() as session:
            return await KnowledgeService(
                session,
                embedding_provider=_DeterministicQueryEmbedding(),
                light_rag=adapter,
            ).hybrid_search(
                query="Password reset",
                statuses=("ACTIVE",),
                source_types=(),
                node_types=(),
                feature_id=node_ids["feature"],
                limit=100,
                cursor=None,
            )

    available = await search()
    available_by_id = {match.node.id: match for match in available.matches}
    assert available.degraded is False
    assert "LIGHTRAG" in available_by_id[node_ids["hinted"]].signals
    expected_fallback_ids = {
        node_ids["feature"],
        node_ids["exact"],
        node_ids["vector"],
        node_ids["graph"],
    }
    assert set(available_by_id) == {*expected_fallback_ids, node_ids["hinted"]}
    expected_order = tuple(
        match.node.id for match in available.matches if match.node.id in expected_fallback_ids
    )
    expected_signatures = tuple(
        (match.node.id, match.signals, match.feature_distance)
        for match in available.matches
        if match.node.id in expected_fallback_ids
    )
    expected_scores = tuple(
        match.score for match in available.matches if match.node.id in expected_fallback_ids
    )
    assert "VECTOR" in available_by_id[node_ids["vector"]].signals
    assert "FEATURE_PROXIMITY" in available_by_id[node_ids["graph"]].signals

    auxiliary_stopped = False
    recovery_elapsed = 0.0
    try:
        _sever_auxiliary()
        auxiliary_stopped = True
        assert _docker_inspect(EXPECTED_AUTHORITATIVE_CONTAINER)["State"]["Running"] is True
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1

        single_started = time.monotonic()
        failed = await search()
        single_elapsed = time.monotonic() - single_started
        print(
            json.dumps(
                {
                    "phase": "auxiliary-stopped-single",
                    "degraded": failed.degraded,
                    "reasons": failed.degradation_reasons,
                    "elapsed": round(single_elapsed, 3),
                    "ids": [str(match.node.id) for match in failed.matches],
                },
                sort_keys=True,
            )
        )
        assert single_elapsed <= REQUEST_LATENCY_BOUND_SECONDS
        assert failed.degraded is True
        assert failed.degradation_reasons == ("LIGHTRAG_UNAVAILABLE",)
        assert tuple(match.node.id for match in failed.matches) == expected_order
        assert tuple(
            (match.node.id, match.signals, match.feature_distance) for match in failed.matches
        ) == expected_signatures
        assert tuple(match.score for match in failed.matches) == pytest.approx(
            expected_scores, abs=1e-5
        )
        assert all("LIGHTRAG" not in match.signals for match in failed.matches)

        concurrent_started = time.monotonic()
        concurrent = await asyncio.gather(*(search() for _ in range(4)))
        concurrent_elapsed = time.monotonic() - concurrent_started
        assert concurrent_elapsed <= REQUEST_LATENCY_BOUND_SECONDS
        for result in concurrent:
            assert result.degradation_reasons == ("LIGHTRAG_UNAVAILABLE",)
            assert tuple(match.node.id for match in result.matches) == expected_order
            assert tuple(
                (match.node.id, match.signals, match.feature_distance) for match in result.matches
            ) == expected_signatures

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1

        recovery_started = time.monotonic()
        _start_auxiliary()
        auxiliary_stopped = False
        await _wait_for_auxiliary(config, RECOVERY_BOUND_SECONDS)
        recovered: HybridSearchResult | None = None
        while time.monotonic() - recovery_started < RECOVERY_BOUND_SECONDS:
            candidate = await search()
            if not candidate.degraded:
                recovered = candidate
                break
            await asyncio.sleep(0.2)
        recovery_elapsed = time.monotonic() - recovery_started
        assert recovery_elapsed <= RECOVERY_BOUND_SECONDS
        assert recovered is not None
        recovered_by_id = {match.node.id: match for match in recovered.matches}
        assert "LIGHTRAG" in recovered_by_id[node_ids["hinted"]].signals
        assert recovered.degradation_reasons == ()
    finally:
        if auxiliary_stopped:
            _start_auxiliary()
            await _wait_for_auxiliary(config, RECOVERY_BOUND_SECONDS)
        finalized = await adapter.finalize()
        assert finalized.status is LightRAGCallStatus.OK

    return {
        "authoritativePostgresStayedOnline": True,
        "auxiliaryBackend": "LightRAG PGGraphStorage+PGVectorStorage via asyncpg",
        "auxiliarySeverMethod": "docker kill --signal KILL",
        "auxiliaryTables": len(tables),
        "auxiliaryGraphs": sorted(graphs),
        "singleFallbackLatencySeconds": round(single_elapsed, 3),
        "concurrentRequests": len(concurrent),
        "concurrentFallbackLatencySeconds": round(concurrent_elapsed, 3),
        "degradationReasons": ["LIGHTRAG_UNAVAILABLE"],
        "recoveryLatencySeconds": round(recovery_elapsed, 3),
    }


def test_real_lightrag_process_severing_keeps_authoritative_fallback_and_recovers(
    tmp_path: Path,
) -> None:
    settings = _required_environment()
    engine, node_ids = _prepare_authoritative_database(settings.authoritative_url)
    try:
        evidence = asyncio.run(_exercise_live_failover(engine, node_ids, settings.lightrag, tmp_path))
    finally:
        engine.dispose()
    print(json.dumps({"liveLightRAGFailoverEvidence": evidence}, sort_keys=True))
