"""BGE-M3/Ollama embedding protocol with deterministic response validation."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

BGE_M3_MODEL = "bge-m3:latest"
BGE_M3_DIMENSION = 1024


class EmbeddingError(RuntimeError):
    """Base embedding boundary error."""


class EmbeddingUnavailable(EmbeddingError):
    """Ollama is stopped, unreachable, or timed out."""


class EmbeddingResponseError(EmbeddingError):
    """Ollama returned a response that violates the configured vector contract."""


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    model: str
    dimension: int
    vectors: tuple[tuple[float, ...], ...]
    content_hashes: tuple[str, ...]


class EmbeddingProvider(Protocol):
    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...


class OllamaEmbeddingTransport(Protocol):
    """Transport for Ollama's current ``POST /api/embed`` batch interface."""

    async def embed(
        self,
        *,
        host: str,
        model: str,
        inputs: tuple[str, ...],
        truncate: bool,
        timeout_seconds: float,
    ) -> Mapping[str, object]: ...


class HttpxOllamaEmbeddingTransport:
    async def embed(
        self,
        *,
        host: str,
        model: str,
        inputs: tuple[str, ...],
        truncate: bool,
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{host.rstrip('/')}/api/embed",
                    json={"model": model, "input": list(inputs), "truncate": truncate},
                )
        except httpx.TransportError as exc:
            raise ConnectionError("Ollama embedding endpoint is unreachable") from exc
        if response.status_code >= 500:
            raise ConnectionError(
                f"Ollama embedding endpoint returned HTTP {response.status_code}"
            )
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise EmbeddingResponseError("Ollama embedding request was rejected") from exc
        if not isinstance(payload, dict):
            raise EmbeddingResponseError("Ollama embedding response must be an object")
        return payload


class BgeM3OllamaEmbeddingProvider:
    """Validated 1024-dimensional BGE-M3 embeddings from local Ollama.

    Ollama's official API accepts a string or list in ``input`` and returns an
    ``embeddings`` matrix.  Batch cardinality, finite numbers, and dimension
    are checked here before any vector may be persisted to pgvector.
    """

    def __init__(
        self,
        transport: OllamaEmbeddingTransport,
        *,
        host: str = "http://127.0.0.1:11434",
        model: str = BGE_M3_MODEL,
        dimension: int = BGE_M3_DIMENSION,
        truncate: bool = True,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not host.strip():
            raise ValueError("Ollama host must not be empty")
        if model.strip().split(":", maxsplit=1)[0] != "bge-m3":
            raise ValueError("BGE-M3 provider requires a bge-m3 model tag")
        if dimension != BGE_M3_DIMENSION:
            raise ValueError(f"BGE-M3 embedding dimension must be {BGE_M3_DIMENSION}")
        if timeout_seconds <= 0:
            raise ValueError("embedding timeout must be positive")
        self._transport = transport
        self._host = host.strip()
        self._model = model.strip()
        self._dimension = dimension
        self._truncate = truncate
        self._timeout_seconds = timeout_seconds

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        normalized = tuple(_required_text(text) for text in texts)
        if not normalized:
            raise ValueError("at least one text is required for embedding")
        try:
            payload = await self._transport.embed(
                host=self._host,
                model=self._model,
                inputs=normalized,
                truncate=self._truncate,
                timeout_seconds=self._timeout_seconds,
            )
        except EmbeddingResponseError:
            raise
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise EmbeddingUnavailable(
                f"Ollama embedding unavailable: {exc.__class__.__name__}"
            ) from exc
        except Exception as exc:
            raise EmbeddingError(
                f"Ollama embedding failed: {exc.__class__.__name__}"
            ) from exc

        raw_vectors = payload.get("embeddings")
        if not isinstance(raw_vectors, list) or len(raw_vectors) != len(normalized):
            raise EmbeddingResponseError(
                "Ollama embeddings count does not match the requested text count"
            )
        vectors: list[tuple[float, ...]] = []
        for raw_vector in raw_vectors:
            if not isinstance(raw_vector, list) or len(raw_vector) != self._dimension:
                raise EmbeddingResponseError(
                    f"Ollama embedding dimension must be {self._dimension}"
                )
            vector: list[float] = []
            for component in raw_vector:
                if isinstance(component, bool) or not isinstance(component, int | float):
                    raise EmbeddingResponseError("Ollama embedding contains a non-number")
                value = float(component)
                if not math.isfinite(value):
                    raise EmbeddingResponseError("Ollama embedding contains a non-finite number")
                vector.append(value)
            vectors.append(tuple(vector))
        response_model = payload.get("model")
        if response_model is not None and response_model != self._model:
            raise EmbeddingResponseError("Ollama response model does not match the request")
        return EmbeddingBatch(
            model=self._model,
            dimension=self._dimension,
            vectors=tuple(vectors),
            content_hashes=tuple(
                hashlib.sha256(text.encode("utf-8")).hexdigest() for text in normalized
            ),
        )


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("embedding text must not be empty")
    return value.strip()
