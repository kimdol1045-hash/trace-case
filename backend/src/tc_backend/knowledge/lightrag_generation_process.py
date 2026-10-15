"""Private JSON-lines worker for one official LightRAG generation.

The parent passes PostgreSQL credentials on the already-open stdin pipe.  This
process installs them into only its own environment before importing the
official provider.  Stdout is reserved for the bounded protocol and ordinary
provider output is discarded so logs and exceptions cannot expose secrets to
the parent.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from importlib import metadata
from typing import Any, BinaryIO, cast

_MAX_MESSAGE_BYTES = 4 * 1024 * 1024


def _read_message(input_stream: BinaryIO) -> dict[str, object] | None:
    line = input_stream.readline(_MAX_MESSAGE_BYTES + 1)
    if not line:
        return None
    if len(line) > _MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
        raise ValueError("invalid message size")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("invalid message shape")
    return cast(dict[str, object], value)


def _write_message(
    output_stream: BinaryIO,
    request_id: object,
    payload: Mapping[str, object],
) -> None:
    encoded = json.dumps(
        {"id": request_id, "payload": dict(payload)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    if len(encoded) > _MAX_MESSAGE_BYTES:
        raise ValueError("response too large")
    output_stream.write(encoded)
    output_stream.flush()


def _load_bootstrap(path: str) -> Callable[[Mapping[str, object]], Mapping[str, object]]:
    module_name, separator, attribute = path.partition(":")
    if not separator:
        raise ValueError("invalid bootstrap")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise ValueError("invalid bootstrap")
    return cast(Callable[[Mapping[str, object]], Mapping[str, object]], factory)


def _serialize_result(result: object) -> dict[str, object]:
    from tc_backend.knowledge.lightrag import LightRAGCallStatus

    status = getattr(result, "status", None)
    if status is LightRAGCallStatus.OK:
        value: Any = getattr(result, "value", None)
        if hasattr(value, "context"):
            return {
                "status": "ok",
                "value": {
                    "context": value.context,
                    "postgresRecordIds": list(value.postgres_record_ids),
                },
            }
        if hasattr(value, "track_id"):
            return {
                "status": "ok",
                "value": {
                    "postgresRecordId": value.postgres_record_id,
                    "trackId": value.track_id,
                },
            }
        return {"status": "ok", "value": None}
    if status is LightRAGCallStatus.ERROR:
        return {"status": "error"}
    return {"status": "unavailable"}


def _run() -> int:
    protocol_output = sys.stdout.buffer
    protocol_input = sys.stdin.buffer
    devnull = open(os.devnull, "w", encoding="utf-8")
    loop = asyncio.new_event_loop()
    adapter: Any | None = None
    installed_environment_keys: tuple[str, ...] = ()
    try:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            start = _read_message(protocol_input)
            if start is None:
                return 2
            request_id = start.get("id")
            if start.get("operation") != "start" or not isinstance(
                start.get("payload"), dict
            ):
                _write_message(protocol_output, request_id, {"status": "unavailable"})
                return 2
            payload = cast(dict[str, object], start["payload"])
            environment = payload.get("postgresEnvironment")
            runtime = payload.get("runtime")
            storage = payload.get("storage")
            attestation = payload.get("attestation")
            if not all(
                isinstance(value, dict)
                for value in (environment, runtime, storage, attestation)
            ):
                _write_message(protocol_output, request_id, {"status": "unavailable"})
                return 2
            environment = cast(dict[str, object], environment)
            if any(not isinstance(key, str) or not isinstance(value, str) for key, value in environment.items()):
                _write_message(protocol_output, request_id, {"status": "unavailable"})
                return 2
            installed_environment_keys = tuple(environment)
            for key, value in environment.items():
                os.environ[key] = cast(str, value)

            runtime = cast(dict[str, object], runtime)
            distribution = runtime.get("distribution")
            required_version = runtime.get("requiredVersion")
            if not isinstance(distribution, str) or not isinstance(required_version, str):
                _write_message(protocol_output, request_id, {"status": "unavailable"})
                return 2
            if metadata.version(distribution) != required_version:
                _write_message(protocol_output, request_id, {"status": "unavailable"})
                return 2

            bootstrap_path = runtime.get("bootstrap")
            working_directory = runtime.get("workingDirectory")
            if not isinstance(bootstrap_path, str) or not isinstance(working_directory, str):
                _write_message(protocol_output, request_id, {"status": "unavailable"})
                return 2
            components = _load_bootstrap(bootstrap_path)(
                {
                    "authorityId": cast(dict[str, object], attestation).get("authorityId"),
                    "providerId": cast(dict[str, object], attestation).get("providerId"),
                    "workspace": cast(dict[str, object], attestation).get("workspace"),
                    "runtimeConfigDigest": cast(dict[str, object], attestation).get(
                        "runtimeConfigDigest"
                    ),
                }
            )
            llm_model_func = components.get("llm_model_func")
            embedding_func = components.get("embedding_func")
            if llm_model_func is None or embedding_func is None:
                _write_message(protocol_output, request_id, {"status": "unavailable"})
                return 2

            from tc_backend.knowledge.lightrag import (
                LightRAGAuxiliaryIndex,
                LightRAGCallStatus,
                LightRAGIndexDocument,
                LightRAGPostgresConnectionSettings,
                LightRAGStorageConfig,
                build_official_lightrag_postgres_runtime,
            )

            storage = cast(dict[str, object], storage)
            postgres = LightRAGPostgresConnectionSettings(
                host=cast(str, environment["POSTGRES_HOST"]),
                port=int(cast(str, environment["POSTGRES_PORT"])),
                user=cast(str, environment["POSTGRES_USER"]),
                password=cast(str, environment["POSTGRES_PASSWORD"]),
                database=cast(str, environment["POSTGRES_DATABASE"]),
                max_connections=int(cast(str, environment["POSTGRES_MAX_CONNECTIONS"])),
                ssl_mode=cast(str, environment["POSTGRES_SSL_MODE"]),
            )
            storage_config = LightRAGStorageConfig(
                kv_storage=cast(str, storage["kvStorage"]),
                vector_storage=cast(str, storage["vectorStorage"]),
                doc_status_storage=cast(str, storage["docStatusStorage"]),
                graph_storage=cast(str, storage["graphStorage"]),
                requires_apache_age=bool(storage["requiresApacheAge"]),
                workspace=cast(str, environment["POSTGRES_WORKSPACE"]),
                postgres=postgres,
            )
            core = build_official_lightrag_postgres_runtime(
                storage_config,
                working_dir=working_directory,
                llm_model_func=llm_model_func,
                embedding_func=embedding_func,
                installed_version=required_version,
                environ=os.environ,
            )
            adapter = LightRAGAuxiliaryIndex(core, storage_config=storage_config)
            initialized = loop.run_until_complete(
                adapter.initialize(
                    apache_age_available=bool(storage["apacheAgeAvailable"])
                )
            )
            if initialized.status is not LightRAGCallStatus.OK:
                _write_message(protocol_output, request_id, {"status": "unavailable"})
                return 2
            _write_message(
                protocol_output,
                request_id,
                {"status": "ready", "attestation": attestation},
            )

            while True:
                request = _read_message(protocol_input)
                if request is None:
                    break
                request_id = request.get("id")
                operation = request.get("operation")
                request_payload = request.get("payload")
                if not isinstance(request_payload, dict):
                    _write_message(protocol_output, request_id, {"status": "unavailable"})
                    continue
                if operation == "shutdown":
                    loop.run_until_complete(adapter.finalize())
                    _write_message(protocol_output, request_id, {"status": "ok"})
                    adapter = None
                    break
                if operation == "search":
                    query = request_payload.get("query")
                    mode = request_payload.get("mode")
                    if not isinstance(query, str) or not isinstance(mode, str):
                        _write_message(protocol_output, request_id, {"status": "error"})
                        continue
                    search_result = loop.run_until_complete(adapter.search(query, mode=mode))
                    _write_message(
                        protocol_output, request_id, _serialize_result(search_result)
                    )
                    continue
                if operation == "index_document":
                    record_id = request_payload.get("postgresRecordId")
                    content = request_payload.get("content")
                    source_uri = request_payload.get("sourceUri")
                    if not all(isinstance(value, str) for value in (record_id, content, source_uri)):
                        _write_message(protocol_output, request_id, {"status": "error"})
                        continue
                    index_result = loop.run_until_complete(
                        adapter.index_document(
                            LightRAGIndexDocument(
                                cast(str, record_id),
                                cast(str, content),
                                cast(str, source_uri),
                            )
                        )
                    )
                    _write_message(
                        protocol_output, request_id, _serialize_result(index_result)
                    )
                    continue
                _write_message(protocol_output, request_id, {"status": "error"})
        return 0
    except Exception:
        try:
            _write_message(
                protocol_output,
                locals().get("request_id"),
                {"status": "unavailable"},
            )
        except Exception:
            pass
        return 2
    finally:
        if adapter is not None:
            try:
                loop.run_until_complete(adapter.finalize())
            except Exception:
                pass
        for key in installed_environment_keys:
            os.environ.pop(key, None)
        loop.close()
        devnull.close()


if __name__ == "__main__":
    raise SystemExit(_run())
