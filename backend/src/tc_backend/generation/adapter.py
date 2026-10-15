"""Provider-neutral generation runtime with bounded retries and strict JSON."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from tc_backend.generation.config import ProviderConfig
from tc_backend.generation.errors import (
    GenerationConfigurationError,
    GenerationError,
    GenerationErrorCode,
    GenerationResponseError,
    GenerationTransportError,
)
from tc_backend.generation.transport import (
    GenerationTransport,
    GenerationTransportRequest,
    GenerationTransportResponse,
)

_JSON_MEDIA_TYPE = "application/json"
_Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True, repr=False)
class GenerationRequest:
    """Canonical input document sent unchanged to the configured endpoint."""

    payload: Mapping[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise ValueError("generation request payload must be an object")
        if any(not isinstance(key, str) for key in self.payload):
            raise ValueError("generation request fields must have string names")
        for name in ("inputSchemaVersion", "requestedOutputSchemaVersion"):
            value = self.payload.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"generation request {name} must not be empty")

    @property
    def requested_output_schema_version(self) -> str:
        value = self.payload["requestedOutputSchemaVersion"]
        assert isinstance(value, str)
        return value

    @property
    def input_schema_version(self) -> str:
        value = self.payload["inputSchemaVersion"]
        assert isinstance(value, str)
        return value

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(input_schema_version="
            f"{self.payload.get('inputSchemaVersion')!r}, requested_output_schema_version="
            f"{self.payload.get('requestedOutputSchemaVersion')!r}, "
            f"field_names={tuple(sorted(self.payload))!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class GenerationProviderResult:
    provider_id: str
    model_id: str
    schema_version: str
    payload: Mapping[str, object]
    attempts: int

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(provider_id={self.provider_id!r}, "
            f"model_id={self.model_id!r}, schema_version={self.schema_version!r}, "
            f"payload_field_names={tuple(sorted(self.payload))!r}, attempts={self.attempts!r})"
        )


class GenerationProvider(Protocol):
    async def generate(
        self,
        request: GenerationRequest | Mapping[str, object],
    ) -> GenerationProviderResult: ...


class GenerationAPIAdapter:
    """Send canonical generation requests without accepting provider identity from output."""

    def __init__(
        self,
        config: ProviderConfig,
        transport: GenerationTransport,
        *,
        sleep: _Sleep = asyncio.sleep,
    ) -> None:
        self._config = config
        self._transport = transport
        self._sleep = sleep

    async def generate(
        self,
        request: GenerationRequest | Mapping[str, object],
    ) -> GenerationProviderResult:
        generation_request = (
            request if isinstance(request, GenerationRequest) else GenerationRequest(request)
        )
        schema_pair = (
            generation_request.input_schema_version,
            generation_request.requested_output_schema_version,
        )
        if schema_pair not in self._config.supported_schema_pairs:
            raise GenerationConfigurationError(
                GenerationErrorCode.UNSUPPORTED_SCHEMA_VERSION,
                "generation request uses an unsupported schema version pair",
            )
        headers = {
            "Accept": _JSON_MEDIA_TYPE,
            "Content-Type": _JSON_MEDIA_TYPE,
            **self._config.extra_headers,
        }
        if self._config.auth is not None:
            name, value = self._config.auth.materialize_header()
            headers[name] = value

        for attempt in range(1, self._config.retry.max_attempts + 1):
            if attempt > 1:
                await self._sleep(self._config.retry.delay_before_attempt(attempt))
            outbound = GenerationTransportRequest(
                endpoint=self._config.endpoint,
                headers=headers,
                json_body=generation_request.payload,
                timeout_seconds=self._config.timeout_seconds,
                attempt=attempt,
                max_response_bytes=self._config.response_max_bytes,
            )
            try:
                response = await asyncio.wait_for(
                    self._transport.send(outbound),
                    timeout=self._config.timeout_seconds,
                )
            except TimeoutError:
                if attempt < self._config.retry.max_attempts:
                    continue
                raise GenerationTransportError(
                    GenerationErrorCode.TIMEOUT,
                    "generation provider timed out",
                    retryable=True,
                    attempts=attempt,
                ) from None
            except (ConnectionError, OSError):
                if attempt < self._config.retry.max_attempts:
                    continue
                raise GenerationTransportError(
                    GenerationErrorCode.TRANSPORT_UNAVAILABLE,
                    "generation provider is unavailable",
                    retryable=True,
                    attempts=attempt,
                ) from None
            except GenerationError:
                raise
            except Exception:
                raise GenerationTransportError(
                    GenerationErrorCode.TRANSPORT_UNAVAILABLE,
                    "generation transport failed",
                    retryable=False,
                    attempts=attempt,
                ) from None

            failure = _status_failure(
                response.status_code,
                attempts=attempt,
                retryable_status_codes=self._config.retry.retryable_status_codes,
            )
            if failure is not None:
                if failure.retryable and attempt < self._config.retry.max_attempts:
                    continue
                raise failure

            payload = parse_json_response(response, max_bytes=self._config.response_max_bytes)
            return GenerationProviderResult(
                provider_id=self._config.provider_id,
                model_id=self._config.model_id,
                schema_version=generation_request.requested_output_schema_version,
                payload=payload,
                attempts=attempt,
            )
        raise AssertionError("bounded generation attempt loop did not terminate")


# Short alias for callers that do not need the HTTP boundary in the name.
GenerationAdapter = GenerationAPIAdapter
GenerationProviderRuntime = GenerationAPIAdapter


def parse_json_response(
    response: GenerationTransportResponse,
    *,
    max_bytes: int,
) -> Mapping[str, object]:
    """Strictly decode one JSON object after media type and byte-size checks."""

    declared_length = _header(response.headers, "content-length")
    if declared_length is not None:
        try:
            content_length = int(declared_length, 10)
        except ValueError:
            raise _response_error(
                GenerationErrorCode.INVALID_RESPONSE,
                "generation response has an invalid Content-Length",
            ) from None
        if content_length < 0:
            raise _response_error(
                GenerationErrorCode.INVALID_RESPONSE,
                "generation response has an invalid Content-Length",
            )
        if content_length > max_bytes:
            raise _response_error(
                GenerationErrorCode.RESPONSE_TOO_LARGE,
                "generation response exceeds the configured byte limit",
            )
    if len(response.body) > max_bytes:
        raise _response_error(
            GenerationErrorCode.RESPONSE_TOO_LARGE,
            "generation response exceeds the configured byte limit",
        )

    content_type = _header(response.headers, "content-type")
    if content_type is None or not _is_json_content_type(content_type):
        raise _response_error(
            GenerationErrorCode.INVALID_CONTENT_TYPE,
            "generation response Content-Type must be JSON",
        )
    try:
        text = response.body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _response_error(
            GenerationErrorCode.MALFORMED_JSON,
            "generation response is not valid UTF-8 JSON",
        ) from None
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError, TypeError):
        raise _response_error(
            GenerationErrorCode.MALFORMED_JSON,
            "generation response contains malformed JSON",
        ) from None
    if not isinstance(payload, dict):
        raise _response_error(
            GenerationErrorCode.INVALID_RESPONSE,
            "generation response must be a JSON object",
        )
    return payload


def _status_failure(
    status_code: int,
    *,
    attempts: int,
    retryable_status_codes: frozenset[int],
) -> GenerationTransportError | None:
    if 200 <= status_code <= 299:
        return None
    if status_code in {401, 403}:
        return GenerationTransportError(
            GenerationErrorCode.AUTH_REJECTED,
            "generation provider rejected authorization",
            status_code=status_code,
            attempts=attempts,
        )
    if status_code == 429:
        return GenerationTransportError(
            GenerationErrorCode.RATE_LIMITED,
            "generation provider rate limit was exhausted",
            retryable=status_code in retryable_status_codes,
            status_code=status_code,
            attempts=attempts,
        )
    if status_code in {408, 425, 500, 502, 503, 504} | retryable_status_codes:
        return GenerationTransportError(
            GenerationErrorCode.PROVIDER_ERROR,
            "generation provider returned a retryable failure",
            retryable=status_code in retryable_status_codes,
            status_code=status_code,
            attempts=attempts,
        )
    return GenerationTransportError(
        GenerationErrorCode.PROVIDER_REJECTED,
        "generation provider rejected the request",
        status_code=status_code,
        attempts=attempts,
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    folded = name.casefold()
    for key, value in headers.items():
        if isinstance(key, str) and key.casefold() == folded:
            return value.strip() if isinstance(value, str) else None
    return None


def _is_json_content_type(value: str) -> bool:
    media_type = value.split(";", maxsplit=1)[0].strip().casefold()
    return media_type == _JSON_MEDIA_TYPE or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON constant: {value}")


def _response_error(code: GenerationErrorCode, message: str) -> GenerationResponseError:
    return GenerationResponseError(code, message)
