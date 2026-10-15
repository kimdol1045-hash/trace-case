from __future__ import annotations

import asyncio

import httpx
import pytest

from tc_backend.generation import (
    MAX_PROVIDER_ATTEMPTS,
    FakeGenerationTransport,
    GenerationAdapter,
    GenerationConfigurationError,
    GenerationErrorCode,
    GenerationRequest,
    GenerationResponseError,
    GenerationTransportError,
    GenerationTransportRequest,
    GenerationTransportResponse,
    HttpxGenerationTransport,
    ProviderAuth,
    ProviderConfig,
    RetryPolicy,
    parse_json_response,
)


def _response(
    body: bytes = b'{"cases": [], "categoryCandidate": {}}',
    *,
    status: int = 200,
    content_type: str = "application/json; charset=utf-8",
    headers: dict[str, str] | None = None,
) -> GenerationTransportResponse:
    return GenerationTransportResponse(
        status,
        {"Content-Type": content_type, **(headers or {})},
        body,
    )


async def _no_sleep(_: float) -> None:
    return None


def _config(
    *,
    endpoint: str = "https://generation.example.test/v1/generate",
    timeout_seconds: float = 2.0,
    retry: RetryPolicy | None = None,
) -> ProviderConfig:
    return ProviderConfig(
        provider_id="local-compatible",
        endpoint=endpoint,
        model_id="trusted-config-model",
        auth=ProviderAuth("top-secret-token"),
        timeout_seconds=timeout_seconds,
        retry=retry or RetryPolicy(max_attempts=3, base_delay_seconds=0),
    )


def _request() -> GenerationRequest:
    return GenerationRequest(
        {
            "inputSchemaVersion": "1.0.0",
            "requestedOutputSchemaVersion": "1.0.0",
            "requestId": "generation_request_test",
            "activeKnowledge": [{"content": "private context"}],
        }
    )


def test_adapter_materializes_auth_only_at_transport_and_uses_local_identity() -> None:
    transport = FakeGenerationTransport([_response(b'{"provider":"untrusted"}')])
    config = _config()
    request = _request()

    result = asyncio.run(
        GenerationAdapter(config, transport, sleep=_no_sleep).generate(request)
    )

    assert result.provider_id == "local-compatible"
    assert result.model_id == "trusted-config-model"
    assert result.schema_version == "1.0.0"
    assert result.payload == {"provider": "untrusted"}
    assert transport.requests[0].headers["Authorization"] == "Bearer top-secret-token"
    assert transport.requests[0].json_body is request.payload
    assert "top-secret-token" not in repr(config)
    assert "top-secret-token" not in repr(config.auth)
    assert "top-secret-token" not in repr(transport.requests[0])


def test_retry_is_bounded_and_final_error_has_stable_redacted_code() -> None:
    secret = "secret-embedded-in-provider-exception"
    transport = FakeGenerationTransport([ConnectionError(secret)] * 3)

    with pytest.raises(GenerationTransportError) as exc_info:
        asyncio.run(
            GenerationAdapter(_config(), transport, sleep=_no_sleep).generate(
                _request()
            )
        )

    failure = exc_info.value
    assert failure.code is GenerationErrorCode.TRANSPORT_UNAVAILABLE
    assert failure.retryable is True
    assert failure.attempts == 3
    assert len(transport.requests) == 3
    assert secret not in str(failure)
    assert secret not in repr(failure)


def test_retryable_http_then_success_and_auth_failure_is_not_retried() -> None:
    retrying = FakeGenerationTransport([_response(status=503), _response(b'{"ok":true}')])
    result = asyncio.run(
        GenerationAdapter(_config(), retrying, sleep=_no_sleep).generate(
            _request()
        )
    )
    assert result.attempts == 2
    assert [request.attempt for request in retrying.requests] == [1, 2]

    auth_failure = FakeGenerationTransport([_response(status=401), _response(b'{"ok":true}')])
    with pytest.raises(GenerationTransportError) as exc_info:
        asyncio.run(
            GenerationAdapter(_config(), auth_failure, sleep=_no_sleep).generate(
                _request()
            )
        )
    assert exc_info.value.code is GenerationErrorCode.AUTH_REJECTED
    assert len(auth_failure.requests) == 1


def test_provider_timeout_is_enforced_outside_transport() -> None:
    class SlowTransport:
        calls = 0

        async def send(self, request: object) -> GenerationTransportResponse:
            self.calls += 1
            await asyncio.sleep(0.02)
            return _response(b'{"ok":true}')

    transport = SlowTransport()
    config = _config(timeout_seconds=0.001, retry=RetryPolicy(max_attempts=2, base_delay_seconds=0))
    with pytest.raises(GenerationTransportError) as exc_info:
        asyncio.run(
            GenerationAdapter(config, transport, sleep=_no_sleep).generate(
                _request()
            )
        )
    assert exc_info.value.code is GenerationErrorCode.TIMEOUT
    assert exc_info.value.attempts == 2
    assert transport.calls == 2


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (_response(content_type="text/plain"), GenerationErrorCode.INVALID_CONTENT_TYPE),
        (_response(b"not-json"), GenerationErrorCode.MALFORMED_JSON),
        (_response(b'{"a":1,"a":2}'), GenerationErrorCode.MALFORMED_JSON),
        (_response(b"NaN"), GenerationErrorCode.MALFORMED_JSON),
        (_response(b"[]"), GenerationErrorCode.INVALID_RESPONSE),
        (
            _response(b"{}", headers={"Content-Length": "200"}),
            GenerationErrorCode.RESPONSE_TOO_LARGE,
        ),
        (_response(b"{" + b" " * 100 + b"}"), GenerationErrorCode.RESPONSE_TOO_LARGE),
    ],
)
def test_strict_json_response_boundary(
    response: GenerationTransportResponse,
    code: GenerationErrorCode,
) -> None:
    with pytest.raises(GenerationResponseError) as exc_info:
        parse_json_response(response, max_bytes=100)
    assert exc_info.value.code is code


def test_json_vendor_media_type_is_accepted_but_trailing_content_is_not() -> None:
    response = _response(b'{"ok":true}', content_type="application/problem+json")
    assert parse_json_response(response, max_bytes=100) == {"ok": True}

    with pytest.raises(GenerationResponseError) as exc_info:
        parse_json_response(_response(b'{"ok":true} trailing'), max_bytes=100)
    assert exc_info.value.code is GenerationErrorCode.MALFORMED_JSON


def test_configuration_limits_attempts_and_rejects_url_credentials() -> None:
    with pytest.raises(GenerationConfigurationError):
        RetryPolicy(max_attempts=MAX_PROVIDER_ATTEMPTS + 1)
    with pytest.raises(GenerationConfigurationError):
        _config(endpoint="https://secret@example.test/generate")
    with pytest.raises(GenerationConfigurationError) as exc_info:
        ProviderAuth("")
    assert exc_info.value.code is GenerationErrorCode.AUTH_REQUIRED


def test_retry_status_allowlist_can_disable_provider_retry() -> None:
    transport = FakeGenerationTransport([_response(status=503), _response(b'{"ok":true}')])
    config = _config(
        retry=RetryPolicy(
            max_attempts=3,
            base_delay_seconds=0,
            retryable_status_codes=frozenset(),
        )
    )

    with pytest.raises(GenerationTransportError) as exc_info:
        asyncio.run(GenerationAdapter(config, transport, sleep=_no_sleep).generate(_request()))

    assert exc_info.value.code is GenerationErrorCode.PROVIDER_ERROR
    assert exc_info.value.retryable is False
    assert len(transport.requests) == 1


def test_adapter_rejects_non_allowlisted_schema_pair_before_transport() -> None:
    transport = FakeGenerationTransport([_response(b'{"ok":true}')])
    request = GenerationRequest(
        {
            "inputSchemaVersion": "1.0.0",
            "requestedOutputSchemaVersion": "2.0.0",
        }
    )

    with pytest.raises(GenerationConfigurationError) as exc_info:
        asyncio.run(GenerationAdapter(_config(), transport).generate(request))

    assert exc_info.value.code is GenerationErrorCode.UNSUPPORTED_SCHEMA_VERSION
    assert transport.requests == []


def test_httpx_transport_enforces_streamed_response_limit() -> None:
    async def exercise() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer transport-secret"
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=b'{"value":"' + b"x" * 200 + b'"}',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            transport = HttpxGenerationTransport(client)
            with pytest.raises(GenerationResponseError) as exc_info:
                await transport.send(
                    GenerationTransportRequest(
                        endpoint="https://generation.example.test",
                        headers={"Authorization": "Bearer transport-secret"},
                        json_body=_request().payload,
                        timeout_seconds=1,
                        attempt=1,
                        max_response_bytes=100,
                    )
                )
            assert exc_info.value.code is GenerationErrorCode.RESPONSE_TOO_LARGE

    asyncio.run(exercise())


def test_config_request_and_response_repr_redact_sensitive_values() -> None:
    config = ProviderConfig(
        provider_id="provider",
        endpoint="https://generation.example.test",
        model_id="model",
        auth=ProviderAuth("auth-secret"),
        extra_headers={"X-API-Key": "header-secret"},
    )
    request = _request()
    response = _response(b'{"private":"response-secret"}')

    assert "auth-secret" not in repr(config)
    assert "header-secret" not in repr(config)
    assert "private context" not in repr(request)
    assert "response-secret" not in repr(response)
