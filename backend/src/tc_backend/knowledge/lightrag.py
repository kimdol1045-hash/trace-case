"""Optional LightRAG auxiliary index boundary.

This adapter follows the current embedded-core coroutine interface:
``initialize_storages()``, ``ainsert(...)``, ``aquery(..., param=QueryParam)``
and ``finalize_storages()``.  PostgreSQL knowledge rows are authoritative;
LightRAG output is only a retrieval candidate that callers must re-resolve
against the typed graph and current lifecycle state.
"""

from __future__ import annotations

import asyncio
import importlib
import math
import os
import re
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import metadata
from pathlib import Path
from typing import Final, Protocol, cast

LIGHTRAG_DISTRIBUTION = "lightrag-hku"
LIGHTRAG_PINNED_VERSION = "1.5.6"
OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID: Final = (
    "official-lightrag-postgresql"
)


class KnowledgeFallback(StrEnum):
    TYPED_GRAPH_PGVECTOR = "typed_graph+pgvector"


class LightRAGCallStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class LightRAGAdapterError(RuntimeError):
    code = "LIGHTRAG_ERROR"

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.fallback = KnowledgeFallback.TYPED_GRAPH_PGVECTOR


class LightRAGUnavailableError(LightRAGAdapterError):
    code = "LIGHTRAG_UNAVAILABLE"


class LightRAGOperationError(LightRAGAdapterError):
    code = "LIGHTRAG_OPERATION_ERROR"


@dataclass(frozen=True, slots=True)
class LightRAGCallResult[T]:
    status: LightRAGCallStatus
    value: T | None = None
    failure: LightRAGAdapterError | None = None

    def __post_init__(self) -> None:
        if self.status is LightRAGCallStatus.OK and self.failure is not None:
            raise ValueError("successful LightRAG result cannot contain a failure")
        if self.status is not LightRAGCallStatus.OK and self.failure is None:
            raise ValueError("failed LightRAG result requires a typed failure")
        if self.status is LightRAGCallStatus.UNAVAILABLE and not isinstance(
            self.failure, LightRAGUnavailableError
        ):
            raise ValueError("unavailable LightRAG result requires unavailable failure")
        if self.status is LightRAGCallStatus.ERROR and not isinstance(
            self.failure, LightRAGOperationError
        ):
            raise ValueError("error LightRAG result requires operation failure")
        if self.failure is not None and self.value is not None:
            raise ValueError("failed LightRAG result cannot contain a value")

    @property
    def should_fallback(self) -> bool:
        return self.status is not LightRAGCallStatus.OK

    @property
    def fallback(self) -> KnowledgeFallback | None:
        return self.failure.fallback if self.failure is not None else None

    def require_value(self) -> T:
        if self.failure is not None:
            raise self.failure
        return cast(T, self.value)


@dataclass(frozen=True, slots=True)
class LightRAGPostgresConnectionSettings:
    """Validated values consumed by LightRAG's official PostgreSQL backend."""

    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str
    max_connections: int = 12
    ssl_mode: str = "disable"

    def validate(self) -> None:
        for field_name in ("host", "user", "password", "database"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"LightRAG PostgreSQL {field_name} must not be empty")
        if not 1 <= self.port <= 65_535:
            raise ValueError("LightRAG PostgreSQL port must be between 1 and 65535")
        if self.max_connections < 1:
            raise ValueError("LightRAG PostgreSQL max_connections must be positive")
        if self.ssl_mode not in {
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ValueError("unsupported LightRAG PostgreSQL ssl_mode")

    def as_official_environment(self, *, workspace: str) -> dict[str, str]:
        """Return the exact environment contract read by LightRAG 1.5.6."""

        self.validate()
        environment = {
            "POSTGRES_HOST": self.host.strip(),
            "POSTGRES_PORT": str(self.port),
            "POSTGRES_USER": self.user.strip(),
            "POSTGRES_PASSWORD": self.password,
            "POSTGRES_DATABASE": self.database.strip(),
            "POSTGRES_MAX_CONNECTIONS": str(self.max_connections),
            "POSTGRES_WORKSPACE": workspace,
            "POSTGRES_SSL_MODE": self.ssl_mode,
        }
        return environment


@dataclass(frozen=True, slots=True)
class LightRAGStorageConfig:
    """Official PostgreSQL storage identifiers for the AGE-backed variant.

    ``PGGraphStorage`` requires Apache AGE.  The repository's current
    ``pgvector/pgvector:0.8.1-pg16`` image only provisions pgvector, so this
    configuration must not be reported ready against that image.
    """

    kv_storage: str = "PGKVStorage"
    vector_storage: str = "PGVectorStorage"
    doc_status_storage: str = "PGDocStatusStorage"
    graph_storage: str = "PGGraphStorage"
    requires_apache_age: bool = True
    workspace: str = "tc"
    postgres: LightRAGPostgresConnectionSettings | None = None

    def validate(self) -> None:
        expected = {
            "kv_storage": "PGKVStorage",
            "vector_storage": "PGVectorStorage",
            "doc_status_storage": "PGDocStatusStorage",
            "graph_storage": "PGGraphStorage",
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise ValueError(f"{field_name} must use current LightRAG class {expected_value}")
        if not self.requires_apache_age:
            raise ValueError("PGGraphStorage requires Apache AGE")
        if (
            not self.workspace.strip()
            or "/" in self.workspace
            or "\\" in self.workspace
            or self.workspace in {".", ".."}
        ):
            raise ValueError("LightRAG workspace must be a non-empty path-safe name")
        if self.postgres is None:
            raise ValueError("LightRAG PostgreSQL connection settings are required")
        self.postgres.validate()

    def as_light_rag_kwargs(self) -> dict[str, str]:
        """Return keyword names accepted by the current ``LightRAG`` constructor."""

        self.validate()
        return {
            "kv_storage": self.kv_storage,
            "vector_storage": self.vector_storage,
            "doc_status_storage": self.doc_status_storage,
            "graph_storage": self.graph_storage,
        }

    def as_official_environment(self) -> dict[str, str]:
        self.validate()
        assert self.postgres is not None
        return self.postgres.as_official_environment(workspace=self.workspace)

    def validate_process_environment(self, environ: Mapping[str, str] | None = None) -> None:
        """Validate the process-global values read by the official backend.

        The boundary deliberately does not mutate these variables across
        ``await`` points: doing so could route a concurrent workspace to the
        wrong PostgreSQL database.
        """

        selected = os.environ if environ is None else environ
        expected = self.as_official_environment()
        missing = sorted(key for key in expected if key not in selected)
        mismatched = sorted(
            key for key, value in expected.items() if key in selected and selected[key] != value
        )
        if missing:
            raise ValueError(
                "LightRAG official PostgreSQL environment is missing: " + ", ".join(missing)
            )
        if mismatched:
            raise ValueError(
                "LightRAG official PostgreSQL environment does not match validated config: "
                + ", ".join(mismatched)
            )


class LightRAGCore(Protocol):
    """Narrow structural protocol matching LightRAG's current async core API."""

    async def initialize_storages(self) -> object: ...

    async def ainsert(
        self,
        input: str | list[str],
        split_by_character: str | None = None,
        split_by_character_only: bool = False,
        ids: str | list[str] | None = None,
        file_paths: str | list[str] | None = None,
        track_id: str | None = None,
    ) -> str: ...

    async def aquery(
        self,
        query: str,
        param: object,
        system_prompt: str | None = None,
    ) -> str | AsyncIterator[str]: ...

    async def finalize_storages(self) -> object: ...


QueryParamFactory = Callable[[str], object]
LightRAGCoreFactory = Callable[..., LightRAGCore]


@dataclass(frozen=True, slots=True)
class LightRAGIndexDocument:
    """Auxiliary document tied to its authoritative PostgreSQL record ID."""

    postgres_record_id: str
    content: str
    source_uri: str


@dataclass(frozen=True, slots=True)
class LightRAGIndexReceipt:
    postgres_record_id: str
    track_id: str
    authoritative: bool = False


@dataclass(frozen=True, slots=True)
class LightRAGSearchCandidate:
    context: str
    postgres_record_ids: tuple[str, ...] = ()
    authoritative: bool = False
    requires_postgresql_resolution: bool = True


class LightRAGIndex(Protocol):
    """Auxiliary operation surface shared by static and generation-routed indexes."""

    async def index_document(
        self, document: LightRAGIndexDocument
    ) -> LightRAGCallResult[LightRAGIndexReceipt]: ...

    async def search(
        self,
        query: str,
        *,
        mode: str = "mix",
    ) -> LightRAGCallResult[LightRAGSearchCandidate]: ...


class OfficialLightRAGPostgresRuntime:
    """Version-checked official core using the four PostgreSQL adapters.

    This object only owns a rebuildable auxiliary index.  Receipts and query
    candidates remain non-authoritative at the adapter boundary below.
    """

    def __init__(
        self,
        core: LightRAGCore,
        *,
        storage_config: LightRAGStorageConfig,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._core = core
        self._storage_config = storage_config
        self._environ = os.environ if environ is None else environ
        self._initialized = False

    async def initialize_storages(self) -> object:
        self._storage_config.validate_process_environment(self._environ)
        result = await self._core.initialize_storages()
        self._initialized = True
        return result

    async def ainsert(
        self,
        input: str | list[str],
        split_by_character: str | None = None,
        split_by_character_only: bool = False,
        ids: str | list[str] | None = None,
        file_paths: str | list[str] | None = None,
        track_id: str | None = None,
    ) -> str:
        return await self._core.ainsert(
            input,
            split_by_character=split_by_character,
            split_by_character_only=split_by_character_only,
            ids=ids,
            file_paths=file_paths,
            track_id=track_id,
        )

    async def aquery(
        self,
        query: str,
        param: object,
        system_prompt: str | None = None,
    ) -> str | AsyncIterator[str]:
        return await self._core.aquery(query, param=param, system_prompt=system_prompt)

    async def finalize_storages(self) -> object:
        try:
            return await self._core.finalize_storages()
        finally:
            self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized


def build_official_lightrag_postgres_runtime(
    storage_config: LightRAGStorageConfig,
    *,
    working_dir: str | Path,
    llm_model_func: object,
    embedding_func: object,
    core_factory: LightRAGCoreFactory | None = None,
    installed_version: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> OfficialLightRAGPostgresRuntime:
    """Construct the exact pinned official runtime after fail-fast validation."""

    storage_config.validate_process_environment(environ)
    actual_version = installed_version or _installed_lightrag_version()
    if actual_version != LIGHTRAG_PINNED_VERSION:
        raise LightRAGUnavailableError(
            f"{LIGHTRAG_DISTRIBUTION}=={LIGHTRAG_PINNED_VERSION} is required; "
            f"found {actual_version}",
            retryable=False,
        )
    selected_factory = core_factory or _official_lightrag_factory()
    core = selected_factory(
        working_dir=str(working_dir),
        workspace=storage_config.workspace,
        llm_model_func=llm_model_func,
        embedding_func=embedding_func,
        **storage_config.as_light_rag_kwargs(),
    )
    for field_name, expected in storage_config.as_light_rag_kwargs().items():
        if getattr(core, field_name, None) != expected:
            raise ValueError(f"official LightRAG core did not retain {field_name}={expected}")
    return OfficialLightRAGPostgresRuntime(
        core,
        storage_config=storage_config,
        environ=environ,
    )


class LightRAGAuxiliaryIndex:
    """Never blocks the authoritative typed-graph + pgvector fallback path."""

    def __init__(
        self,
        core: LightRAGCore | None,
        *,
        query_param_factory: QueryParamFactory | None = None,
        storage_config: LightRAGStorageConfig | None = None,
        query_timeout_seconds: float = 5.0,
    ) -> None:
        if (
            isinstance(query_timeout_seconds, bool)
            or not math.isfinite(query_timeout_seconds)
            or query_timeout_seconds <= 0
        ):
            raise ValueError("LightRAG query timeout must be a positive finite number")
        self._core = core
        self._query_param_factory = query_param_factory or official_query_param
        self._storage_config = storage_config or LightRAGStorageConfig()
        self._query_timeout_seconds = float(query_timeout_seconds)
        self._initialized = False

    async def initialize(self, *, apache_age_available: bool) -> LightRAGCallResult[None]:
        # A failed reinitialization must never leave an earlier pool marked
        # ready. Operators may retry initialize after the auxiliary service is
        # healthy; authoritative PostgreSQL retrieval does not depend on it.
        self._initialized = False
        try:
            self._storage_config.validate()
        except ValueError as exc:
            return _error_result(exc, operation="initialize")
        if self._storage_config.requires_apache_age and not apache_age_available:
            return _unavailable_result("Apache AGE is required by PGGraphStorage")
        if self._core is None:
            return _unavailable_result("LightRAG core is not installed or configured")
        try:
            await self._core.initialize_storages()
        except Exception as exc:
            return _unavailable_result(
                f"LightRAG storage initialization failed: {exc.__class__.__name__}"
            )
        self._initialized = True
        return LightRAGCallResult(status=LightRAGCallStatus.OK)

    async def index_document(
        self, document: LightRAGIndexDocument
    ) -> LightRAGCallResult[LightRAGIndexReceipt]:
        _validate_document(document)
        unavailable: LightRAGCallResult[LightRAGIndexReceipt] | None = self._ready_or_unavailable()
        if unavailable is not None:
            return unavailable
        assert self._core is not None
        try:
            track_id = await self._core.ainsert(
                document.content,
                ids=document.postgres_record_id,
                file_paths=document.source_uri,
            )
        except (ConnectionError, TimeoutError, OSError) as exc:
            return _unavailable_result(
                f"LightRAG indexing transport failed: {exc.__class__.__name__}"
            )
        except Exception as exc:
            return _error_result(exc, operation="index")
        if not isinstance(track_id, str) or not track_id.strip():
            return _error_result(
                ValueError("LightRAG ainsert returned an empty track ID"), operation="index"
            )
        return LightRAGCallResult(
            status=LightRAGCallStatus.OK,
            value=LightRAGIndexReceipt(
                postgres_record_id=document.postgres_record_id,
                track_id=track_id,
            ),
        )

    async def search(
        self,
        query: str,
        *,
        mode: str = "mix",
    ) -> LightRAGCallResult[LightRAGSearchCandidate]:
        if not query.strip():
            raise ValueError("LightRAG query must not be empty")
        if mode not in {"local", "global", "hybrid", "naive", "mix", "bypass"}:
            raise ValueError(f"unsupported LightRAG query mode: {mode}")
        unavailable: LightRAGCallResult[LightRAGSearchCandidate] | None = (
            self._ready_or_unavailable()
        )
        if unavailable is not None:
            return unavailable
        assert self._core is not None
        try:
            query_param = self._query_param_factory(mode)
            context = await asyncio.wait_for(
                self._core.aquery(query.strip(), param=query_param),
                timeout=self._query_timeout_seconds,
            )
        except LightRAGUnavailableError as exc:
            return LightRAGCallResult(
                status=LightRAGCallStatus.UNAVAILABLE,
                failure=exc,
            )
        except (ImportError, ModuleNotFoundError, ConnectionError, TimeoutError, OSError) as exc:
            return _unavailable_result(f"LightRAG query unavailable: {exc.__class__.__name__}")
        except Exception as exc:
            return _error_result(exc, operation="query")
        if not isinstance(context, str):
            return _error_result(
                TypeError("LightRAG context query unexpectedly streamed"), operation="query"
            )
        return LightRAGCallResult(
            status=LightRAGCallStatus.OK,
            value=LightRAGSearchCandidate(
                context=context,
                postgres_record_ids=_postgres_record_ids(context),
            ),
        )

    async def finalize(self) -> LightRAGCallResult[None]:
        if not self._initialized or self._core is None:
            return LightRAGCallResult(status=LightRAGCallStatus.OK)
        try:
            await self._core.finalize_storages()
        except Exception as exc:
            return _error_result(exc, operation="finalize")
        finally:
            self._initialized = False
        return LightRAGCallResult(status=LightRAGCallStatus.OK)

    def _ready_or_unavailable[T](self) -> LightRAGCallResult[T] | None:
        if self._core is None or not self._initialized:
            return _unavailable_result("LightRAG auxiliary index is not initialized")
        return None


def official_query_param(mode: str) -> object:
    """Build current LightRAG ``QueryParam`` without a mandatory dependency."""

    try:
        module = importlib.import_module("lightrag")
    except (ImportError, ModuleNotFoundError) as exc:
        raise LightRAGUnavailableError(
            "LightRAG QueryParam is unavailable", retryable=False
        ) from exc
    query_param = getattr(module, "QueryParam", None)
    if query_param is None or not callable(query_param):
        raise LightRAGUnavailableError("LightRAG QueryParam is unavailable", retryable=False)
    return query_param(mode=mode, only_need_context=True, stream=False)


def _installed_lightrag_version() -> str:
    try:
        return metadata.version(LIGHTRAG_DISTRIBUTION)
    except metadata.PackageNotFoundError as exc:
        raise LightRAGUnavailableError(
            f"{LIGHTRAG_DISTRIBUTION}=={LIGHTRAG_PINNED_VERSION} is not installed",
            retryable=False,
        ) from exc


def _official_lightrag_factory() -> LightRAGCoreFactory:
    try:
        module = importlib.import_module("lightrag")
    except (ImportError, ModuleNotFoundError) as exc:
        raise LightRAGUnavailableError(
            "official LightRAG core is unavailable", retryable=False
        ) from exc
    factory = getattr(module, "LightRAG", None)
    if factory is None or not callable(factory):
        raise LightRAGUnavailableError("official LightRAG core is unavailable", retryable=False)
    return cast(LightRAGCoreFactory, factory)


def _validate_document(document: LightRAGIndexDocument) -> None:
    if not document.postgres_record_id.strip():
        raise ValueError("authoritative PostgreSQL record ID must not be empty")
    if not document.content.strip():
        raise ValueError("LightRAG index content must not be empty")
    if not document.source_uri.strip():
        raise ValueError("LightRAG source URI must not be empty")


_POSTGRES_RECORD_ID = re.compile(
    r"(?<![0-9a-fA-F])"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
    r"(?![0-9a-fA-F])"
)


def _postgres_record_ids(context: str) -> tuple[str, ...]:
    """Extract ordered UUID hints without treating LightRAG text as authoritative."""

    return tuple(
        dict.fromkeys(match.group(0).lower() for match in _POSTGRES_RECORD_ID.finditer(context))
    )


def _unavailable_result[T](message: str) -> LightRAGCallResult[T]:
    return LightRAGCallResult(
        status=LightRAGCallStatus.UNAVAILABLE,
        failure=LightRAGUnavailableError(message, retryable=True),
    )


def _error_result[T](exc: Exception, *, operation: str) -> LightRAGCallResult[T]:
    return LightRAGCallResult(
        status=LightRAGCallStatus.ERROR,
        failure=LightRAGOperationError(
            f"LightRAG {operation} failed: {exc.__class__.__name__}", retryable=False
        ),
    )
