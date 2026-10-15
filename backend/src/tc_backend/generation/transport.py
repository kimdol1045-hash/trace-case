"""Narrow HTTP transport protocol for provider-neutral generation calls."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx

from tc_backend.generation.errors import (
    GenerationErrorCode,
    GenerationResponseError,
)


@dataclass(frozen=True, slots=True)
class GenerationTransportRequest:
    endpoint: str
    headers: Mapping[str, str]
    json_body: Mapping[str, object]
    timeout_seconds: float
    attempt: int
    max_response_bytes: int = 1024 * 1024

    def __repr__(self) -> str:
        """Keep authorization material out of diagnostics and test failures."""

        header_names = tuple(sorted(self.headers))
        return (
            f"{self.__class__.__name__}(endpoint_configured=True, "
            f"header_names={header_names!r}, timeout_seconds={self.timeout_seconds!r}, "
            f"attempt={self.attempt!r}, max_response_bytes={self.max_response_bytes!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class GenerationTransportResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(status_code={self.status_code!r}, "
            f"header_names={tuple(sorted(self.headers))!r}, body_bytes={len(self.body)!r})"
        )


class GenerationTransport(Protocol):
    async def send(self, request: GenerationTransportRequest) -> GenerationTransportResponse: ...


class HttpxGenerationTransport:
    """Default async HTTP transport; provider semantics stay in the adapter."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def send(self, request: GenerationTransportRequest) -> GenerationTransportResponse:
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                    return await self._send_with_client(client, request)
            return await self._send_with_client(self._client, request)
        except httpx.TimeoutException:
            raise TimeoutError("generation transport timed out") from None
        except httpx.TransportError:
            raise ConnectionError("generation transport unavailable") from None

    @staticmethod
    async def _send_with_client(
        client: httpx.AsyncClient,
        request: GenerationTransportRequest,
    ) -> GenerationTransportResponse:
        async with client.stream(
            "POST",
            request.endpoint,
            headers=request.headers,
            json=request.json_body,
            timeout=request.timeout_seconds,
        ) as response:
            declared_length = response.headers.get("Content-Length")
            if declared_length is not None:
                try:
                    content_length = int(declared_length, 10)
                except ValueError:
                    raise GenerationResponseError(
                        GenerationErrorCode.INVALID_RESPONSE,
                        "generation response has an invalid Content-Length",
                    ) from None
                if content_length < 0:
                    raise GenerationResponseError(
                        GenerationErrorCode.INVALID_RESPONSE,
                        "generation response has an invalid Content-Length",
                    )
                if content_length > request.max_response_bytes:
                    raise GenerationResponseError(
                        GenerationErrorCode.RESPONSE_TOO_LARGE,
                        "generation response exceeds the configured byte limit",
                    )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > request.max_response_bytes:
                    raise GenerationResponseError(
                        GenerationErrorCode.RESPONSE_TOO_LARGE,
                        "generation response exceeds the configured byte limit",
                    )
            return GenerationTransportResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                body=bytes(body),
            )
