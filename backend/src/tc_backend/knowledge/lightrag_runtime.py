"""Version-fenced runtime routing for immutable LightRAG generations.

The resolver accepts no provider credential or process-environment selector.
Each operation reads the TC PostgreSQL pointer, routes through a factory bound
to the exact immutable generation, and reads the pointer again before exposing
auxiliary output.  A missing, invalid, or changed route is therefore the same
typed-unavailable signal already consumed by graph+pgvector fallback paths.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Engine

from tc_backend.knowledge.lightrag import (
    LightRAGCallResult,
    LightRAGCallStatus,
    LightRAGIndex,
    LightRAGIndexDocument,
    LightRAGIndexReceipt,
    LightRAGSearchCandidate,
    LightRAGUnavailableError,
)
from tc_backend.repositories.lightrag_generation_repository import (
    ActiveGenerationBinding,
    DurableLightRAGGenerationControlPlane,
)

_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


class ActiveGenerationBindingSource(Protocol):
    """Read the current pointer and immutable generation atomically."""

    def active_generation(self) -> ActiveGenerationBinding: ...


@dataclass(frozen=True, slots=True)
class LightRAGGenerationRuntime:
    """Factory result attested to the exact ledger binding it serves."""

    binding: ActiveGenerationBinding
    index: LightRAGIndex


class LightRAGGenerationRuntimeFactory(Protocol):
    """Credential-owning boundary outside the credential-free resolver."""

    def __call__(self, binding: ActiveGenerationBinding) -> LightRAGGenerationRuntime: ...


@dataclass(slots=True)
class _CacheEntry:
    runtime: LightRAGGenerationRuntime
    in_flight: int = 0
    retired: bool = False
    finalize_started: bool = False


class LightRAGRuntimeResolver:
    """Route complete operations through the current versioned generation.

    The one-entry cache is fenced by pointer version as well as immutable
    generation identity.  Retired entries stay alive while an operation is in
    flight, but that operation's result is discarded unless the durable route
    is unchanged after the auxiliary call completes.
    """

    def __init__(
        self,
        binding_source: ActiveGenerationBindingSource,
        runtime_factory: LightRAGGenerationRuntimeFactory,
    ) -> None:
        self._binding_source = binding_source
        self._runtime_factory = runtime_factory
        self._lock = threading.RLock()
        self._current: _CacheEntry | None = None

    async def search(
        self,
        query: str,
        *,
        mode: str = "mix",
    ) -> LightRAGCallResult[LightRAGSearchCandidate]:
        try:
            binding = self._read_binding()
            entry, retired = self._acquire(binding)
        except Exception:
            await self._finalize_entry(self._retire_current())
            return _routing_unavailable()
        await self._finalize_entry(retired)
        try:
            if not self._binding_is_current(binding):
                self._retire_if_current(entry)
                return _routing_unavailable()
            try:
                result = await entry.runtime.index.search(query, mode=mode)
            except Exception:
                return _routing_unavailable()
            if not self._binding_is_current(binding):
                self._retire_if_current(entry)
                return _routing_unavailable()
            return result
        finally:
            await self._finalize_entry(self._release(entry))

    async def index_document(
        self, document: LightRAGIndexDocument
    ) -> LightRAGCallResult[LightRAGIndexReceipt]:
        try:
            binding = self._read_binding()
            entry, retired = self._acquire(binding)
        except Exception:
            await self._finalize_entry(self._retire_current())
            return _routing_unavailable()
        await self._finalize_entry(retired)
        try:
            if not self._binding_is_current(binding):
                self._retire_if_current(entry)
                return _routing_unavailable()
            try:
                result = await entry.runtime.index.index_document(document)
            except Exception:
                return _routing_unavailable()
            if not self._binding_is_current(binding):
                self._retire_if_current(entry)
                return _routing_unavailable()
            return result
        finally:
            await self._finalize_entry(self._release(entry))

    async def finalize(self) -> LightRAGCallResult[None]:
        await self._finalize_entry(self._retire_current())
        return LightRAGCallResult(status=LightRAGCallStatus.OK)

    def active_generation_ready(self) -> bool:
        """Probe the durable route without constructing provider resources."""

        validator = getattr(self._runtime_factory, "validate_binding", None)
        if not callable(validator):
            # Explicit test/alternate factories retain the legacy configured
            # readiness contract because they expose no side-effect-free probe.
            return True
        try:
            validator(self._read_binding())
        except Exception:
            return False
        return True

    def _read_binding(self) -> ActiveGenerationBinding:
        binding = self._binding_source.active_generation()
        _validate_binding(binding)
        return binding

    def _binding_is_current(self, expected: ActiveGenerationBinding) -> bool:
        try:
            return self._read_binding() == expected
        except Exception:
            return False

    def _acquire(self, binding: ActiveGenerationBinding) -> tuple[_CacheEntry, _CacheEntry | None]:
        with self._lock:
            current = self._current
            if (
                current is not None
                and not current.retired
                and current.runtime.binding.cache_key == binding.cache_key
            ):
                if current.runtime.binding != binding:
                    raise ValueError("cached LightRAG binding does not match durable metadata")
                current.in_flight += 1
                return current, None

            runtime = self._runtime_factory(binding)
            if runtime.binding != binding:
                raise ValueError("LightRAG runtime factory attested the wrong generation")
            entry = _CacheEntry(runtime=runtime, in_flight=1)
            self._current = entry
            retired = self._retire(current)
            return entry, retired

    def _release(self, entry: _CacheEntry) -> _CacheEntry | None:
        with self._lock:
            if entry.in_flight < 1:
                raise RuntimeError("LightRAG runtime lease underflow")
            entry.in_flight -= 1
            return self._ready_to_finalize(entry)

    def _retire_current(self) -> _CacheEntry | None:
        with self._lock:
            current = self._current
            self._current = None
            return self._retire(current)

    def _retire_if_current(self, entry: _CacheEntry) -> None:
        with self._lock:
            if self._current is entry:
                self._current = None
            entry.retired = True

    def _retire(self, entry: _CacheEntry | None) -> _CacheEntry | None:
        if entry is None:
            return None
        entry.retired = True
        return self._ready_to_finalize(entry)

    def _ready_to_finalize(self, entry: _CacheEntry) -> _CacheEntry | None:
        if entry.retired and entry.in_flight == 0 and not entry.finalize_started:
            entry.finalize_started = True
            return entry
        return None

    @staticmethod
    async def _finalize_entry(entry: _CacheEntry | None) -> None:
        if entry is None:
            return
        finalize = getattr(entry.runtime.index, "finalize", None)
        if callable(finalize):
            try:
                await finalize()
            except Exception:
                # Cleanup cannot make authoritative retrieval unavailable.
                return


def build_lightrag_runtime_resolver(
    engine: Engine,
    runtime_factory: LightRAGGenerationRuntimeFactory,
) -> LightRAGRuntimeResolver:
    """Compose the resolver against the actual T09A PostgreSQL contract."""

    return LightRAGRuntimeResolver(DurableLightRAGGenerationControlPlane(engine), runtime_factory)


def _validate_binding(binding: ActiveGenerationBinding) -> None:
    if isinstance(binding.pointer_version, bool) or binding.pointer_version < 1:
        raise ValueError("active LightRAG pointer version is invalid")
    for value in (
        binding.generation_id,
        binding.authority_id,
        binding.provider_id,
        binding.workspace,
    ):
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ValueError("active LightRAG generation identity is invalid")
    for value in (
        binding.provider_contract_version,
        binding.provider_distribution,
        binding.provider_version,
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError("active LightRAG provider contract is invalid")
    for value in (
        binding.auxiliary_target_digest,
        binding.runtime_config_digest,
        binding.manifest_digest,
        binding.corpus_digest,
        binding.source_ledger_digest,
        binding.projection_digest,
        binding.pgvector_digest,
        binding.registration_digest,
    ):
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            raise ValueError("active LightRAG generation digest is invalid")
    if isinstance(binding.document_count, bool) or binding.document_count < 1:
        raise ValueError("active LightRAG generation cardinality is invalid")


def _routing_unavailable[T]() -> LightRAGCallResult[T]:
    return LightRAGCallResult(
        status=LightRAGCallStatus.UNAVAILABLE,
        failure=LightRAGUnavailableError(
            "LightRAG active generation is unavailable or changed",
            retryable=True,
        ),
    )


__all__ = [
    "ActiveGenerationBindingSource",
    "LightRAGGenerationRuntime",
    "LightRAGGenerationRuntimeFactory",
    "LightRAGRuntimeResolver",
    "build_lightrag_runtime_resolver",
]
