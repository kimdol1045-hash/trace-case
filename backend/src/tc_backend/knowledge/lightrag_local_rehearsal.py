"""Deterministic, network-free callables for the PF-SF1-T09 wave-8 drill.

This module is intentionally unusable as a production bootstrap.  It accepts
only the generated local disposable authority/workspace namespace and never
constructs an HTTP client, model-provider SDK, or socket.  The same embedding
function is used to seed the disposable authoritative pgvector projection so
the fallback check is deterministic.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from tc_backend.knowledge.lightrag import OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID

WAVE8_TOKEN = "pf-sf1-t09-wave8"
_AUTHORITY = re.compile(
    r"^tc-phase-f-pf-sf1-t09-wave8-[a-z0-9]{8,16}-disposable$"
)
_WORKSPACE = re.compile(
    r"^pf-sf1-t09-wave8-[a-z0-9]{8,16}-(?:seed|[a-f0-9]{12})$"
)
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_UUID = re.compile(
    r"(?<![0-9a-fA-F])"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
    r"(?![0-9a-fA-F])"
)


def deterministic_embedding_vector(text: str) -> tuple[float, ...]:
    """Return one stable, finite, non-zero 1024-dimensional vector."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("wave-8 embedding text must not be empty")
    seed = hashlib.sha256(text.strip().encode("utf-8")).digest()
    values = [0.125] * 1024
    for index, byte in enumerate(seed):
        values[index] += (byte + 1) / 512.0
        values[64 + index] += (seed[-index - 1] + 1) / 1024.0
    norm = math.sqrt(sum(value * value for value in values))
    return tuple(value / norm for value in values)


async def _embedding(texts: Sequence[str]) -> np.ndarray:
    return np.asarray(
        [deterministic_embedding_vector(text) for text in texts],
        dtype=np.float32,
    )


async def _llm(prompt: str, **kwargs: object) -> str:
    """Emit a small valid graph extraction without consulting any provider."""

    del kwargs
    if "identify and extract any missed" in prompt.casefold():
        return "<|COMPLETE|>"
    if "extract entities and relationships" in prompt.casefold():
        return "\n".join(
            (
                "entity<|#|>Wave Eight Source<|#|>System<|#|>The disposable source owns the reviewed local corpus.",
                "entity<|#|>Wave Eight Check<|#|>Process<|#|>The local check verifies retrieval and rollback.",
                "relation<|#|>Wave Eight Source<|#|>Wave Eight Check<|#|>verification<|#|>The check verifies the disposable source.",
                "<|COMPLETE|>",
            )
        )
    # LightRAG 1.5.6 canonicalizes citation paths to basenames.  Echo only the
    # UUID citation identities already present in its query prompt so the
    # production adapter exercises its non-authoritative ID extraction path.
    identities = tuple(dict.fromkeys(value.lower() for value in _UUID.findall(prompt)))
    return "wave-8 deterministic network-free response " + " ".join(identities)


def build_network_free_components(
    attestation: Mapping[str, object],
) -> Mapping[str, object]:
    """Build official-core callables only for the exact wave-8 namespace."""

    if set(attestation) != {
        "authorityId",
        "providerId",
        "workspace",
        "runtimeConfigDigest",
    }:
        raise ValueError("wave-8 bootstrap attestation shape mismatch")
    authority = attestation["authorityId"]
    provider = attestation["providerId"]
    workspace = attestation["workspace"]
    runtime_digest = attestation["runtimeConfigDigest"]
    if not isinstance(authority, str) or _AUTHORITY.fullmatch(authority) is None:
        raise ValueError("wave-8 bootstrap authority mismatch")
    if provider != OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID:
        raise ValueError("wave-8 bootstrap provider mismatch")
    if not isinstance(workspace, str) or _WORKSPACE.fullmatch(workspace) is None:
        raise ValueError("wave-8 bootstrap workspace mismatch")
    if (
        not isinstance(runtime_digest, str)
        or _DIGEST.fullmatch(runtime_digest) is None
    ):
        raise ValueError("wave-8 bootstrap runtime digest mismatch")

    from lightrag.utils import EmbeddingFunc  # type: ignore[import-untyped]

    embedding: Any = EmbeddingFunc(
        embedding_dim=1024,
        max_token_size=8192,
        model_name="pf-sf1-t09-wave8-network-free-1024",
        func=_embedding,
    )
    return {"llm_model_func": _llm, "embedding_func": embedding}


__all__ = [
    "WAVE8_TOKEN",
    "build_network_free_components",
    "deterministic_embedding_vector",
]
