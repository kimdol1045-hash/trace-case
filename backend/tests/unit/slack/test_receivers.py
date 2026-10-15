from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from types import TracebackType

import pytest

from tc_backend.infrastructure.slack.errors import (
    SlackInvalidSignature,
    SlackStaleRequest,
)
from tc_backend.infrastructure.slack.models import (
    SlackMessageOperation,
    SlackNormalizedEventType,
)
from tc_backend.infrastructure.slack.receivers import (
    SlackHttpEventsReceiver,
    SlackSocketEnvelopeReceiver,
    SlackSocketModeReceiver,
)
from tc_backend.infrastructure.slack.signature import (
    SlackRequestVerifier,
    sign_slack_request,
)
from tc_backend.infrastructure.slack.socket_mode import SlackSocketModeClient
from tc_backend.infrastructure.slack.testing import (
    DeterministicFakeSlackTransport,
    DeterministicFakeSocketConnection,
)

NOW = 1_787_300_000
SIGNING_SECRET = "test-signing-secret"


def _event_payload() -> dict[str, object]:
    return {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev123",
        "event_time": NOW,
        "event": {
            "type": "message",
            "channel": "C123",
            "user": "U123",
            "text": "개발건: 로그인 개선",
            "ts": "1787300000.000001",
            "event_ts": "1787300000.000001",
        },
    }


def _signed_headers(raw_body: bytes, *, timestamp: int = NOW) -> dict[str, str]:
    return {
        "X-Slack-Request-Timestamp": str(timestamp),
        "X-Slack-Signature": sign_slack_request(
            signing_secret=SIGNING_SECRET,
            timestamp=timestamp,
            raw_body=raw_body,
        ),
    }


def _http_receiver(*, tolerance_seconds: int = 300) -> SlackHttpEventsReceiver:
    return SlackHttpEventsReceiver(
        SlackRequestVerifier(
            SIGNING_SECRET,
            tolerance_seconds=tolerance_seconds,
            clock=lambda: NOW,
        )
    )


def test_http_verifies_exact_raw_body_before_normalizing() -> None:
    raw_body = json.dumps(_event_payload(), separators=(",", ":")).encode()
    received = _http_receiver().receive(raw_body, headers=_signed_headers(raw_body))

    assert received.event.event_id == "Ev123"
    assert received.event.message is not None
    assert received.event.message.operation is SlackMessageOperation.CREATE
    assert received.event.message.text == "개발건: 로그인 개선"
    assert received.acknowledgement.http_body() is None

    reformatted = json.dumps(_event_payload(), indent=2).encode()
    with pytest.raises(SlackInvalidSignature):
        _http_receiver().receive(reformatted, headers=_signed_headers(raw_body))


def test_http_rejects_forged_and_out_of_window_requests() -> None:
    raw_body = json.dumps(_event_payload()).encode()
    forged = _signed_headers(raw_body) | {"X-Slack-Signature": "v0=" + "0" * 64}
    with pytest.raises(SlackInvalidSignature):
        _http_receiver().receive(raw_body, headers=forged)

    stale_timestamp = NOW - 301
    with pytest.raises(SlackStaleRequest):
        _http_receiver().receive(
            raw_body,
            headers=_signed_headers(raw_body, timestamp=stale_timestamp),
        )


def test_url_verification_is_normalized_and_acknowledged() -> None:
    payload = {"type": "url_verification", "challenge": "challenge-value"}
    raw_body = json.dumps(payload).encode()

    received = _http_receiver().receive(raw_body, headers=_signed_headers(raw_body))

    assert received.event.normalized_type is SlackNormalizedEventType.URL_VERIFICATION
    assert received.event.challenge == "challenge-value"
    assert received.acknowledgement.http_body() == {"challenge": "challenge-value"}


def test_http_and_socket_mode_share_the_same_normalized_event_and_ack_model() -> None:
    payload = _event_payload()
    raw_body = json.dumps(payload, sort_keys=True).encode()
    http_received = _http_receiver().receive(raw_body, headers=_signed_headers(raw_body))
    socket_received = SlackSocketEnvelopeReceiver().receive(
        {
            "envelope_id": "En123",
            "type": "events_api",
            "accepts_response_payload": False,
            "payload": payload,
        }
    )

    assert socket_received.event == http_received.event
    assert socket_received.acknowledgement.socket_payload() == {"envelope_id": "En123"}


def test_socket_mode_protocol_acks_before_returning_received_event() -> None:
    connection = DeterministicFakeSocketConnection(
        [
            json.dumps(
                {
                    "envelope_id": "En123",
                    "type": "events_api",
                    "payload": _event_payload(),
                }
            )
        ]
    )

    received = asyncio.run(SlackSocketModeReceiver().receive_once(connection))

    assert received.event.event_id == "Ev123"
    assert json.loads(connection.sent[0]) == {"envelope_id": "En123"}


def test_socket_mode_client_uses_apps_connections_open_and_injected_connector() -> None:
    connection = DeterministicFakeSocketConnection(
        [
            json.dumps(
                {
                    "envelope_id": "En-health",
                    "type": "events_api",
                    "payload": _event_payload(),
                }
            )
        ]
    )
    transport = DeterministicFakeSlackTransport()
    opened_urls: list[str] = []

    class FakeContext(AbstractAsyncContextManager[DeterministicFakeSocketConnection]):
        async def __aenter__(self) -> DeterministicFakeSocketConnection:
            return connection

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

    def connector(url: str) -> FakeContext:
        opened_urls.append(url)
        return FakeContext()

    received = asyncio.run(
        SlackSocketModeClient(
            web_api=transport,
            app_token="xapp-test",
            connector=connector,
        ).receive_one()
    )

    assert received.event.event_id == "Ev123"
    assert opened_urls == [transport.socket_mode_url]
    assert transport.socket_open_calls == 1
