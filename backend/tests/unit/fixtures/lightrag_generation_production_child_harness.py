"""Run the production generation child with a network-free official-core double."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from tc_backend.knowledge import lightrag
from tc_backend.knowledge import lightrag_generation_process as process


class _Core:
    """Minimal core retaining the exact official PostgreSQL storage kwargs."""

    def __init__(self, **kwargs: object) -> None:
        self.kv_storage = kwargs["kv_storage"]
        self.vector_storage = kwargs["vector_storage"]
        self.doc_status_storage = kwargs["doc_status_storage"]
        self.graph_storage = kwargs["graph_storage"]

    async def initialize_storages(self) -> None:
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
        del input, split_by_character, split_by_character_only, file_paths, track_id
        assert isinstance(ids, str)
        return f"{ids}-production-child-track"

    async def aquery(
        self,
        query: str,
        param: object,
        system_prompt: str | None = None,
    ) -> str | AsyncIterator[str]:
        del param, system_prompt
        return query

    async def finalize_storages(self) -> None:
        return None


def _bootstrap_components(attestation: Mapping[str, object]) -> Mapping[str, object]:
    assert set(attestation) == {
        "authorityId",
        "providerId",
        "workspace",
        "runtimeConfigDigest",
    }
    assert attestation["authorityId"] == "authority-test"
    assert attestation["providerId"] == lightrag.OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID
    assert attestation["workspace"] == "workspace-a"
    runtime_config_digest = attestation["runtimeConfigDigest"]
    assert isinstance(runtime_config_digest, str)
    assert runtime_config_digest.startswith("sha256:")
    assert len(runtime_config_digest) == 71
    return {"llm_model_func": object(), "embedding_func": object()}


def _load_bootstrap(path: str) -> Any:
    assert path == "approved.runtime:build"
    return _bootstrap_components


def main() -> int:
    process.metadata.version = lambda distribution: (
        lightrag.LIGHTRAG_PINNED_VERSION
        if distribution == lightrag.LIGHTRAG_DISTRIBUTION
        else "unexpected"
    )
    process._load_bootstrap = _load_bootstrap
    lightrag._official_lightrag_factory = lambda: _Core
    return process._run()


if __name__ == "__main__":
    raise SystemExit(main())
