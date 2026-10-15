from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from tc_backend.knowledge import (
    LightRAGAuxiliaryIndex,
    LightRAGCallStatus,
    LightRAGPostgresConnectionSettings,
    LightRAGStorageConfig,
    build_official_lightrag_postgres_runtime,
)


def _required_environment() -> Mapping[str, str]:
    required = (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DATABASE",
        "POSTGRES_MAX_CONNECTIONS",
        "POSTGRES_WORKSPACE",
        "POSTGRES_SSL_MODE",
    )
    missing = tuple(name for name in required if not os.environ.get(name))
    if missing:
        pytest.skip("live LightRAG PostgreSQL environment is not configured: " + ", ".join(missing))
    if "test" not in os.environ["POSTGRES_DATABASE"].casefold():
        pytest.skip("live LightRAG test requires a disposable database containing 'test'")
    return os.environ


@pytest.mark.postgres
def test_official_lightrag_postgres_age_storage_lifecycle(tmp_path: Path) -> None:
    environment = _required_environment()

    async def exercise() -> None:
        import asyncpg
        import numpy as np
        from lightrag.utils import EmbeddingFunc

        async def fake_llm(prompt: str, **kwargs: object) -> str:
            return "unused during storage lifecycle validation"

        async def fake_embedding(texts: list[str]) -> np.ndarray:
            return np.zeros((len(texts), 1024), dtype=np.float32)

        config = LightRAGStorageConfig(
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
        runtime = build_official_lightrag_postgres_runtime(
            config,
            working_dir=tmp_path,
            llm_model_func=fake_llm,
            embedding_func=EmbeddingFunc(
                embedding_dim=1024,
                max_token_size=8192,
                model_name="tc-deterministic-1024",
                func=fake_embedding,
            ),
        )
        adapter = LightRAGAuxiliaryIndex(runtime, storage_config=config)
        initialized = await adapter.initialize(apache_age_available=True)
        assert initialized.status is LightRAGCallStatus.OK
        postgres = config.postgres
        assert postgres is not None
        try:
            connection = await asyncpg.connect(
                host=postgres.host,
                port=postgres.port,
                user=postgres.user,
                password=postgres.password,
                database=postgres.database,
                ssl=False if postgres.ssl_mode == "disable" else None,
            )
            try:
                extensions = {
                    row["extname"]
                    for row in await connection.fetch(
                        "SELECT extname FROM pg_extension WHERE extname IN ('age', 'vector')"
                    )
                }
                tables = {
                    row["tablename"]
                    for row in await connection.fetch(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename LIKE 'lightrag_%'"
                    )
                }
                graphs = {
                    row["name"]
                    for row in await connection.fetch("SELECT name FROM ag_catalog.ag_graph")
                }
            finally:
                await connection.close()

            assert extensions == {"age", "vector"}
            assert {
                "lightrag_doc_full",
                "lightrag_doc_status",
                "lightrag_llm_cache",
            }.issubset(tables)
            assert any(name.endswith("_chunk_entity_relation") for name in graphs)
        finally:
            finalized = await adapter.finalize()
        assert finalized.status is LightRAGCallStatus.OK
        assert runtime.initialized is False

    asyncio.run(exercise())
