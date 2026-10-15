"""Credential-owning, process-isolated LightRAG generation runtimes.

LightRAG 1.5.6's official PostgreSQL adapters read their connection settings
from ``os.environ`` and share one ``ClientManager`` pool per interpreter.  A
thread lock around temporary environment changes cannot make two generations
safe: constructors, initialization, and later pool reuse all cross await
points.  This module therefore keeps the official ``POSTGRES_*`` credential
environment and pool out of the API/worker interpreter and gives every cached
generation a dedicated child interpreter.

The application defaults remain disabled.  API and worker startup compose this
factory only when the complete ``TC_LIGHT_RAG_*`` opt-in is present.  The
parent can therefore hold the password as ``SecretStr`` (and its deployment
environment may be the secret injection source), but it never installs the
official provider's ``POSTGRES_*`` variables or pool.  Those are child-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import metadata
from pathlib import Path
from typing import Any, Final, cast

from pydantic import SecretStr

from tc_backend.knowledge.lightrag import (
    LIGHTRAG_DISTRIBUTION,
    LIGHTRAG_PINNED_VERSION,
    OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
    LightRAGCallResult,
    LightRAGCallStatus,
    LightRAGIndexDocument,
    LightRAGIndexReceipt,
    LightRAGOperationError,
    LightRAGSearchCandidate,
    LightRAGUnavailableError,
)
from tc_backend.knowledge.lightrag_runtime import LightRAGGenerationRuntime
from tc_backend.repositories.lightrag_generation_repository import ActiveGenerationBinding

_PROTOCOL: Final = "tc-lightrag-generation-runtime/v1"
_PROVIDER_CONTRACT: Final = "tc-lightrag-generation-provider/v1"
_MAX_MESSAGE_BYTES: Final = 4 * 1024 * 1024
_IDENTIFIER: Final = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")
_DIGEST: Final = re.compile(r"^sha256:[a-f0-9]{64}$")
_BOOTSTRAP: Final = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$"
)
_OFFICIAL_STORAGE: Final = {
    "kvStorage": "PGKVStorage",
    "vectorStorage": "PGVectorStorage",
    "docStatusStorage": "PGDocStatusStorage",
    "graphStorage": "PGGraphStorage",
}
_INHERITED_ENVIRONMENT_ALLOWLIST: Final = (
    "DYLD_LIBRARY_PATH",
    "LANG",
    "LC_ALL",
    "LD_LIBRARY_PATH",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TZ",
    "WINDIR",
)


class LightRAGGenerationRuntimeUnavailableReason(StrEnum):
    """Secret-safe reasons for refusing a configured generation runtime."""

    DISABLED = "disabled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    VERSION_UNAVAILABLE = "version_unavailable"
    VERSION_MISMATCH = "version_mismatch"
    BINDING_MISMATCH = "binding_mismatch"
    AUTHORITY_MISMATCH = "authority_mismatch"
    PROVIDER_CONTRACT_MISMATCH = "provider_contract_mismatch"
    WORKSPACE_MISMATCH = "workspace_mismatch"
    CONFIG_DIGEST_MISMATCH = "config_digest_mismatch"
    TLS_REQUIRED = "tls_required"
    STORAGE_MISMATCH = "storage_mismatch"
    APACHE_AGE_UNAVAILABLE = "apache_age_unavailable"
    PROCESS_UNAVAILABLE = "process_unavailable"
    PROCESS_PROTOCOL_ERROR = "process_protocol_error"


class LightRAGGenerationRuntimeUnavailable(LightRAGUnavailableError):
    """Typed, redacted failure raised before an isolated runtime is usable."""

    code = "LIGHTRAG_GENERATION_RUNTIME_UNAVAILABLE"

    def __init__(
        self,
        reason: LightRAGGenerationRuntimeUnavailableReason,
        *,
        retryable: bool = False,
    ) -> None:
        self.reason = reason
        super().__init__(
            f"isolated LightRAG generation runtime unavailable ({reason.value})",
            retryable=retryable,
        )


@dataclass(frozen=True, slots=True)
class LightRAGSubprocessPostgresCredentials:
    """PostgreSQL values owned by the factory and revealed only to its child."""

    host: str
    port: int
    user: str
    password: SecretStr = field(repr=False)
    database: str
    max_connections: int = 12
    ssl_mode: str = "require"

    def __post_init__(self) -> None:
        if not isinstance(self.password, SecretStr):
            raise TypeError("LightRAG PostgreSQL password must be SecretStr")
        for name in ("host", "user", "database"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"LightRAG PostgreSQL {name} must not be empty")
        if not self.password.get_secret_value():
            raise ValueError("LightRAG PostgreSQL password must not be empty")
        if isinstance(self.port, bool) or not 1 <= self.port <= 65_535:
            raise ValueError("LightRAG PostgreSQL port must be between 1 and 65535")
        if isinstance(self.max_connections, bool) or self.max_connections < 1:
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

    def public_attestation(self) -> dict[str, object]:
        """Return connection authority fields, deliberately excluding password."""

        return {
            "host": self.host.strip(),
            "port": self.port,
            "user": self.user.strip(),
            "database": self.database.strip(),
            "maxConnections": self.max_connections,
            "sslMode": self.ssl_mode,
        }

    def _official_environment(self, workspace: str) -> dict[str, str]:
        return {
            "POSTGRES_HOST": self.host.strip(),
            "POSTGRES_PORT": str(self.port),
            "POSTGRES_USER": self.user.strip(),
            "POSTGRES_PASSWORD": self.password.get_secret_value(),
            "POSTGRES_DATABASE": self.database.strip(),
            "POSTGRES_MAX_CONNECTIONS": str(self.max_connections),
            "POSTGRES_WORKSPACE": workspace,
            "POSTGRES_SSL_MODE": self.ssl_mode,
        }


@dataclass(frozen=True, slots=True)
class LightRAGSubprocessStorageConfig:
    """The only storage combination supported by the official T09 adapter."""

    kv_storage: str = "PGKVStorage"
    vector_storage: str = "PGVectorStorage"
    doc_status_storage: str = "PGDocStatusStorage"
    graph_storage: str = "PGGraphStorage"
    requires_apache_age: bool = True
    apache_age_available: bool = False

    def attestation(self) -> dict[str, object]:
        return {
            "kvStorage": self.kv_storage,
            "vectorStorage": self.vector_storage,
            "docStatusStorage": self.doc_status_storage,
            "graphStorage": self.graph_storage,
            "requiresApacheAge": self.requires_apache_age,
            "apacheAgeAvailable": self.apache_age_available,
        }


@dataclass(frozen=True, slots=True)
class IsolatedLightRAGGenerationConfig:
    """One explicitly allowed immutable generation authority.

    ``auxiliary_target_digest`` identifies the stable auxiliary authority and
    is deliberately distinct from ``runtime_config_digest``.  The latter
    excludes both password and workspace: credential rotation does not change
    immutable evidence, while each verified binding can select its own fresh
    workspace without pre-registering one static config per generation.
    """

    enabled: bool = False
    authority_id: str = ""
    auxiliary_target_digest: str = ""
    provider_id: str = ""
    provider_contract_version: str = _PROVIDER_CONTRACT
    postgres: LightRAGSubprocessPostgresCredentials | None = None
    storage: LightRAGSubprocessStorageConfig = field(
        default_factory=LightRAGSubprocessStorageConfig
    )
    distribution: str = LIGHTRAG_DISTRIBUTION
    required_version: str = LIGHTRAG_PINNED_VERSION
    runtime_bootstrap: str = ""
    deployment_environment: str = "local"
    working_directory_root: Path = Path(".lightrag-generation-runtimes")
    startup_timeout_seconds: float = 20.0
    operation_timeout_seconds: float = 30.0
    shutdown_timeout_seconds: float = 5.0

    @property
    def runtime_config_digest(self) -> str:
        return _canonical_digest(
            {
                "schemaVersion": _PROTOCOL,
                "authorityId": self.authority_id,
                "auxiliaryTargetDigest": _normalize_digest(
                    self.auxiliary_target_digest
                ),
                "providerId": self.provider_id,
                "providerContractVersion": self.provider_contract_version,
                "distribution": self.distribution,
                "requiredVersion": self.required_version,
                "postgres": (
                    self.postgres.public_attestation() if self.postgres is not None else None
                ),
                "storage": self.storage.attestation(),
                "runtimeBootstrap": self.runtime_bootstrap,
                "deploymentEnvironment": self.deployment_environment,
            }
        )

    @property
    def config_digest(self) -> str:
        """Compatibility alias for callers migrating to the explicit name."""

        return self.runtime_config_digest


DistributionVersionResolver = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class IsolatedLightRAGRuntimeConfigAttestation:
    """Credential-free fields a controller must seal into a durable row."""

    authority_id: str
    auxiliary_target_digest: str
    provider_id: str
    provider_contract_version: str
    provider_distribution: str
    provider_version: str
    runtime_config_digest: str


class IsolatedLightRAGGenerationRuntimeFactory:
    """Select exact configs and return one subprocess-backed runtime per miss.

    The factory itself has no runtime cache.  ``LightRAGRuntimeResolver`` owns
    the deterministic one-entry cache and finalizes the evicted child only
    after its final in-flight lease is released.
    """

    def __init__(
        self,
        configs: Iterable[IsolatedLightRAGGenerationConfig] = (),
        *,
        version_resolver: DistributionVersionResolver | None = None,
        worker_command: Sequence[str] | None = None,
    ) -> None:
        self._configs = tuple(configs)
        self._version_resolver = version_resolver or _installed_distribution_version
        self._worker_command = tuple(worker_command) if worker_command is not None else None
        exact_keys: set[tuple[str, str, str | None, str]] = set()
        for config in self._configs:
            key = (
                config.authority_id,
                config.provider_id,
                _normalize_digest(config.auxiliary_target_digest),
                config.runtime_config_digest,
            )
            if key in exact_keys:
                raise ValueError("duplicate isolated LightRAG generation configuration")
            exact_keys.add(key)

    def __call__(self, binding: ActiveGenerationBinding) -> LightRAGGenerationRuntime:
        config = self._validated_config(binding)
        index = SubprocessLightRAGIndex(
            binding=binding,
            config=config,
            worker_command=self._worker_command,
        )
        return LightRAGGenerationRuntime(binding=binding, index=index)

    def validate_binding(self, binding: ActiveGenerationBinding) -> None:
        """Attest an active binding without starting or caching a child."""

        self._validated_config(binding)

    def _validated_config(
        self, binding: ActiveGenerationBinding
    ) -> IsolatedLightRAGGenerationConfig:
        if not _binding_is_well_formed(binding):
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.BINDING_MISMATCH
            )
        config = self._select_config(binding)
        self._validate_config(config)
        return config

    def validate_configuration(self) -> None:
        """Fail startup before a resolver can advertise an unusable factory."""

        if not self._configs:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
            )
        for config in self._configs:
            self._validate_config(config)

    def configuration_attestations(
        self,
    ) -> tuple[IsolatedLightRAGRuntimeConfigAttestation, ...]:
        """Return only the exact non-secret values safe to persist in a row."""

        self.validate_configuration()
        return tuple(
            IsolatedLightRAGRuntimeConfigAttestation(
                authority_id=config.authority_id,
                auxiliary_target_digest=cast(
                    str, _normalize_digest(config.auxiliary_target_digest)
                ),
                provider_id=config.provider_id,
                provider_contract_version=config.provider_contract_version,
                provider_distribution=config.distribution,
                provider_version=config.required_version,
                runtime_config_digest=config.runtime_config_digest,
            )
            for config in self._configs
        )

    def _select_config(
        self, binding: ActiveGenerationBinding
    ) -> IsolatedLightRAGGenerationConfig:
        provider_configs = tuple(
            config for config in self._configs if config.provider_id == binding.provider_id
        )
        if not provider_configs:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
            )
        authority_configs = tuple(
            config
            for config in provider_configs
            if config.authority_id == binding.authority_id
        )
        if not authority_configs:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.AUTHORITY_MISMATCH
            )
        authority_digest_configs = tuple(
            config
            for config in authority_configs
            if _normalize_digest(config.auxiliary_target_digest)
            == binding.auxiliary_target_digest
        )
        if not authority_digest_configs:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.AUTHORITY_MISMATCH
            )
        contract_configs = tuple(
            config
            for config in authority_digest_configs
            if config.provider_contract_version == binding.provider_contract_version
        )
        if not contract_configs:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROVIDER_CONTRACT_MISMATCH
            )
        version_configs = tuple(
            config
            for config in contract_configs
            if config.distribution == binding.provider_distribution
            and config.required_version == binding.provider_version
        )
        if not version_configs:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.VERSION_MISMATCH
            )
        digest_configs = tuple(
            config
            for config in version_configs
            if config.runtime_config_digest == binding.runtime_config_digest
        )
        if len(digest_configs) != 1:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.CONFIG_DIGEST_MISMATCH
            )
        return digest_configs[0]

    def _validate_config(self, config: IsolatedLightRAGGenerationConfig) -> None:
        if not config.enabled:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.DISABLED
            )
        if (
            not _IDENTIFIER.fullmatch(config.authority_id)
            or not _IDENTIFIER.fullmatch(config.provider_id)
            or config.provider_id != OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID
            or _normalize_digest(config.auxiliary_target_digest) is None
            or config.postgres is None
            or not _BOOTSTRAP.fullmatch(config.runtime_bootstrap)
            or config.deployment_environment not in {"local", "staging", "production"}
        ):
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
            )
        if not config.required_version:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.VERSION_UNAVAILABLE
            )
        if config.provider_contract_version != _PROVIDER_CONTRACT:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROVIDER_CONTRACT_MISMATCH
            )
        if (
            config.distribution != LIGHTRAG_DISTRIBUTION
            or config.required_version != LIGHTRAG_PINNED_VERSION
        ):
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.VERSION_MISMATCH
            )
        try:
            installed_version = self._version_resolver(config.distribution)
        except Exception:
            installed_version = None
        if installed_version is None:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.VERSION_UNAVAILABLE
            )
        if installed_version != config.required_version:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.VERSION_MISMATCH
            )
        storage = config.storage.attestation()
        if any(storage[name] != expected for name, expected in _OFFICIAL_STORAGE.items()) or not (
            config.storage.requires_apache_age
        ):
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.STORAGE_MISMATCH
            )
        if not config.storage.apache_age_available:
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.APACHE_AGE_UNAVAILABLE
            )
        assert config.postgres is not None
        if (
            config.deployment_environment in {"staging", "production"}
            and not _is_loopback_host(config.postgres.host)
            and config.postgres.ssl_mode != "verify-full"
        ):
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.TLS_REQUIRED
            )
        for timeout in (
            config.startup_timeout_seconds,
            config.operation_timeout_seconds,
            config.shutdown_timeout_seconds,
        ):
            if isinstance(timeout, bool) or not 0 < timeout <= 600:
                raise LightRAGGenerationRuntimeUnavailable(
                    LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
                )
        if not config.working_directory_root.is_absolute():
            raise LightRAGGenerationRuntimeUnavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
            )


class SubprocessLightRAGIndex:
    """``LightRAGIndex`` proxy whose child owns all mutable official state."""

    def __init__(
        self,
        *,
        binding: ActiveGenerationBinding,
        config: IsolatedLightRAGGenerationConfig,
        worker_command: tuple[str, ...] | None,
    ) -> None:
        self._binding = binding
        self._client = _GenerationProcessClient(
            binding=binding,
            config=config,
            worker_command=worker_command,
        )

    def __repr__(self) -> str:
        return (
            "SubprocessLightRAGIndex("
            f"generation_id={self._binding.generation_id!r}, "
            f"pointer_version={self._binding.pointer_version!r})"
        )

    @property
    def worker_pid(self) -> int | None:
        return self._client.worker_pid

    @property
    def worker_running(self) -> bool:
        return self._client.worker_running

    @property
    def working_directory(self) -> Path | None:
        return self._client.working_directory

    async def search(
        self,
        query: str,
        *,
        mode: str = "mix",
    ) -> LightRAGCallResult[LightRAGSearchCandidate]:
        try:
            response = await asyncio.to_thread(
                self._client.request,
                "search",
                {"query": query, "mode": mode},
            )
            return _decode_search_response(response)
        except LightRAGGenerationRuntimeUnavailable as exc:
            return LightRAGCallResult(status=LightRAGCallStatus.UNAVAILABLE, failure=exc)

    async def index_document(
        self, document: LightRAGIndexDocument
    ) -> LightRAGCallResult[LightRAGIndexReceipt]:
        try:
            response = await asyncio.to_thread(
                self._client.request,
                "index_document",
                {
                    "postgresRecordId": document.postgres_record_id,
                    "content": document.content,
                    "sourceUri": document.source_uri,
                },
            )
            return _decode_index_response(response)
        except LightRAGGenerationRuntimeUnavailable as exc:
            return LightRAGCallResult(status=LightRAGCallStatus.UNAVAILABLE, failure=exc)

    async def finalize(self) -> LightRAGCallResult[None]:
        await asyncio.to_thread(self._client.close)
        return LightRAGCallResult(status=LightRAGCallStatus.OK)


class _GenerationProcessClient:
    def __init__(
        self,
        *,
        binding: ActiveGenerationBinding,
        config: IsolatedLightRAGGenerationConfig,
        worker_command: tuple[str, ...] | None,
    ) -> None:
        self._binding = binding
        self._config = config
        self._worker_command = worker_command
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._responses: queue.Queue[bytes | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._next_request_id = 1
        self._closed = False
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._last_working_directory: Path | None = None
        self._last_pid: int | None = None

    @property
    def worker_pid(self) -> int | None:
        with self._lock:
            process = self._process
            return process.pid if process is not None else self._last_pid

    @property
    def worker_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def working_directory(self) -> Path | None:
        with self._lock:
            if self._temporary_directory is not None:
                return Path(self._temporary_directory.name)
            return self._last_working_directory

    def request(self, operation: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        with self._lock:
            if self._closed:
                raise _unavailable(
                    LightRAGGenerationRuntimeUnavailableReason.PROCESS_UNAVAILABLE,
                    retryable=True,
                )
            if self._process is None:
                self._start_locked()
            return self._round_trip_locked(
                operation,
                payload,
                timeout=self._config.operation_timeout_seconds,
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    self._round_trip_locked(
                        "shutdown",
                        {},
                        timeout=self._config.shutdown_timeout_seconds,
                    )
                except LightRAGGenerationRuntimeUnavailable:
                    pass
            self._stop_process_locked()

    def _start_locked(self) -> None:
        config = self._config
        try:
            config.working_directory_root.mkdir(parents=True, exist_ok=True)
            prefix = (
                f"{self._binding.generation_id}-p{self._binding.pointer_version}-"
            )
            self._temporary_directory = tempfile.TemporaryDirectory(
                prefix=prefix,
                dir=config.working_directory_root,
            )
            self._last_working_directory = Path(self._temporary_directory.name)
            command = self._worker_command or _default_worker_command()
            environment = _subprocess_environment()
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment,
                cwd=self._temporary_directory.name,
                close_fds=True,
            )
            self._process = process
            self._last_pid = process.pid
            assert process.stdout is not None
            responses: queue.Queue[bytes | None] = queue.Queue()
            self._responses = responses
            self._reader = threading.Thread(
                target=self._read_responses,
                args=(process.stdout, responses),
                name=f"lightrag-generation-{process.pid}",
                daemon=True,
            )
            self._reader.start()
            binding_payload = _binding_payload(self._binding)
            expected_attestation = _process_attestation(config, binding_payload)
            assert config.postgres is not None
            startup_payload: dict[str, object] = {
                "protocol": _PROTOCOL,
                "binding": binding_payload,
                "attestation": expected_attestation,
                "postgresEnvironment": config.postgres._official_environment(
                    self._binding.workspace
                ),
                "storage": config.storage.attestation(),
                "runtime": {
                    "bootstrap": config.runtime_bootstrap,
                    "workingDirectory": str(self._temporary_directory.name),
                    "distribution": config.distribution,
                    "requiredVersion": config.required_version,
                },
            }
            try:
                response = self._round_trip_locked(
                    "start",
                    startup_payload,
                    timeout=config.startup_timeout_seconds,
                )
            finally:
                cast(dict[str, str], startup_payload["postgresEnvironment"]).clear()
            if response.get("status") != "ready" or response.get(
                "attestation"
            ) != expected_attestation:
                raise _unavailable(
                    LightRAGGenerationRuntimeUnavailableReason.BINDING_MISMATCH
                )
        except LightRAGGenerationRuntimeUnavailable:
            self._stop_process_locked()
            raise
        except Exception:
            self._stop_process_locked()
            raise _unavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROCESS_UNAVAILABLE,
                retryable=True,
            ) from None

    def _round_trip_locked(
        self,
        operation: str,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> Mapping[str, object]:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise _unavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROCESS_UNAVAILABLE,
                retryable=True,
            )
        request_id = self._next_request_id
        self._next_request_id += 1
        try:
            encoded = _encode_message(
                {"id": request_id, "operation": operation, "payload": dict(payload)}
            )
            process.stdin.write(encoded)
            process.stdin.flush()
            raw_response = self._responses.get(timeout=timeout)
        except (BrokenPipeError, OSError, queue.Empty):
            self._stop_process_locked()
            raise _unavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROCESS_UNAVAILABLE,
                retryable=True,
            ) from None
        if raw_response is None:
            self._stop_process_locked()
            raise _unavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROCESS_UNAVAILABLE,
                retryable=True,
            )
        try:
            decoded = json.loads(raw_response)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._stop_process_locked()
            raise _unavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROCESS_PROTOCOL_ERROR
            ) from None
        if (
            not isinstance(decoded, dict)
            or decoded.get("id") != request_id
            or not isinstance(decoded.get("payload"), dict)
        ):
            self._stop_process_locked()
            raise _unavailable(
                LightRAGGenerationRuntimeUnavailableReason.PROCESS_PROTOCOL_ERROR
            )
        return cast(Mapping[str, object], decoded["payload"])

    def _read_responses(
        self,
        output: Any,
        responses: queue.Queue[bytes | None],
    ) -> None:
        while True:
            try:
                line = output.readline(_MAX_MESSAGE_BYTES + 1)
            except Exception:
                responses.put(None)
                return
            if not line or len(line) > _MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                responses.put(None)
                return
            responses.put(line)

    def _stop_process_locked(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=self._config.shutdown_timeout_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=self._config.shutdown_timeout_seconds)
                    except subprocess.TimeoutExpired:
                        pass
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=0.2)
        temporary_directory = self._temporary_directory
        self._temporary_directory = None
        if temporary_directory is not None:
            try:
                temporary_directory.cleanup()
            except OSError:
                path = Path(temporary_directory.name)
                if path.parent == self._config.working_directory_root:
                    shutil.rmtree(path, ignore_errors=True)


def _decode_search_response(
    response: Mapping[str, object],
) -> LightRAGCallResult[LightRAGSearchCandidate]:
    status = response.get("status")
    if status == "ok":
        value = response.get("value")
        if not isinstance(value, dict) or not isinstance(value.get("context"), str):
            return _protocol_unavailable()
        record_ids = value.get("postgresRecordIds", [])
        if not isinstance(record_ids, list) or any(
            not isinstance(item, str) for item in record_ids
        ):
            return _protocol_unavailable()
        return LightRAGCallResult(
            status=LightRAGCallStatus.OK,
            value=LightRAGSearchCandidate(
                context=value["context"],
                postgres_record_ids=tuple(record_ids),
            ),
        )
    return _decode_failure(response, operation="search")


def _decode_index_response(
    response: Mapping[str, object],
) -> LightRAGCallResult[LightRAGIndexReceipt]:
    status = response.get("status")
    if status == "ok":
        value = response.get("value")
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("postgresRecordId"), str)
            or not isinstance(value.get("trackId"), str)
        ):
            return _protocol_unavailable()
        return LightRAGCallResult(
            status=LightRAGCallStatus.OK,
            value=LightRAGIndexReceipt(
                postgres_record_id=value["postgresRecordId"],
                track_id=value["trackId"],
            ),
        )
    return _decode_failure(response, operation="index")


def _decode_failure[T](
    response: Mapping[str, object], *, operation: str
) -> LightRAGCallResult[T]:
    if response.get("status") == "error":
        return LightRAGCallResult(
            status=LightRAGCallStatus.ERROR,
            failure=LightRAGOperationError(
                f"isolated LightRAG generation {operation} failed",
                retryable=False,
            ),
        )
    return LightRAGCallResult(
        status=LightRAGCallStatus.UNAVAILABLE,
        failure=LightRAGUnavailableError(
            f"isolated LightRAG generation {operation} unavailable",
            retryable=True,
        ),
    )


def _protocol_unavailable[T]() -> LightRAGCallResult[T]:
    return LightRAGCallResult(
        status=LightRAGCallStatus.UNAVAILABLE,
        failure=_unavailable(
            LightRAGGenerationRuntimeUnavailableReason.PROCESS_PROTOCOL_ERROR
        ),
    )


def _unavailable(
    reason: LightRAGGenerationRuntimeUnavailableReason,
    *,
    retryable: bool = False,
) -> LightRAGGenerationRuntimeUnavailable:
    return LightRAGGenerationRuntimeUnavailable(reason, retryable=retryable)


def _binding_payload(binding: ActiveGenerationBinding) -> dict[str, object]:
    return {
        "generationId": binding.generation_id,
        "pointerVersion": binding.pointer_version,
        "pointerUpdatedAt": binding.pointer_updated_at.isoformat(),
        "authorityId": binding.authority_id,
        "providerId": binding.provider_id,
        "providerContractVersion": binding.provider_contract_version,
        "providerDistribution": binding.provider_distribution,
        "providerVersion": binding.provider_version,
        "workspace": binding.workspace,
        "auxiliaryTargetDigest": binding.auxiliary_target_digest,
        "runtimeConfigDigest": binding.runtime_config_digest,
        "manifestDigest": binding.manifest_digest,
        "corpusDigest": binding.corpus_digest,
        "sourceLedgerDigest": binding.source_ledger_digest,
        "projectionDigest": binding.projection_digest,
        "pgvectorDigest": binding.pgvector_digest,
        "documentCount": binding.document_count,
        "registrationDigest": binding.registration_digest,
    }


def _binding_is_well_formed(binding: ActiveGenerationBinding) -> bool:
    if (
        isinstance(binding.pointer_version, bool)
        or binding.pointer_version < 1
        or not _IDENTIFIER.fullmatch(binding.generation_id)
        or not _IDENTIFIER.fullmatch(binding.authority_id)
        or not _IDENTIFIER.fullmatch(binding.provider_id)
        or not binding.provider_contract_version
        or not binding.provider_distribution
        or not binding.provider_version
        or not _IDENTIFIER.fullmatch(binding.workspace)
        or isinstance(binding.document_count, bool)
        or binding.document_count < 1
    ):
        return False
    return all(
        _DIGEST.fullmatch(value) is not None
        for value in (
            binding.auxiliary_target_digest,
            binding.runtime_config_digest,
            binding.manifest_digest,
            binding.corpus_digest,
            binding.source_ledger_digest,
            binding.projection_digest,
            binding.pgvector_digest,
            binding.registration_digest,
        )
    )


def _process_attestation(
    config: IsolatedLightRAGGenerationConfig,
    binding_payload: Mapping[str, object],
) -> dict[str, object]:
    return {
        "authorityId": config.authority_id,
        "auxiliaryTargetDigest": _normalize_digest(
            config.auxiliary_target_digest
        ),
        "providerId": config.provider_id,
        "providerContractVersion": config.provider_contract_version,
        "providerDistribution": config.distribution,
        "providerVersion": config.required_version,
        "workspace": binding_payload["workspace"],
        "runtimeConfigDigest": config.runtime_config_digest,
        "bindingDigest": _canonical_digest(binding_payload),
    }


def _normalize_digest(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value if value.startswith("sha256:") else f"sha256:{value}"
    return normalized if _DIGEST.fullmatch(normalized) else None


def _is_loopback_host(value: str) -> bool:
    host = value.strip().casefold().strip("[]")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _canonical_digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _encode_message(value: Mapping[str, object]) -> bytes:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    if len(encoded) > _MAX_MESSAGE_BYTES:
        raise _unavailable(
            LightRAGGenerationRuntimeUnavailableReason.PROCESS_PROTOCOL_ERROR
        )
    return encoded


def _installed_distribution_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _default_worker_command() -> tuple[str, ...]:
    # Module execution prevents this package's ``knowledge/lightrag.py`` from
    # shadowing the installed official ``lightrag`` package on ``sys.path[0]``.
    return (
        sys.executable,
        "-u",
        "-m",
        "tc_backend.knowledge.lightrag_generation_process",
    )


def _subprocess_environment() -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in _INHERITED_ENVIRONMENT_ALLOWLIST
        if name in os.environ
    }
    package_root = str(Path(__file__).resolve().parents[2])
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": package_root,
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


__all__ = [
    "IsolatedLightRAGGenerationConfig",
    "IsolatedLightRAGGenerationRuntimeFactory",
    "IsolatedLightRAGRuntimeConfigAttestation",
    "LightRAGGenerationRuntimeUnavailable",
    "LightRAGGenerationRuntimeUnavailableReason",
    "LightRAGSubprocessPostgresCredentials",
    "LightRAGSubprocessStorageConfig",
    "SubprocessLightRAGIndex",
]
