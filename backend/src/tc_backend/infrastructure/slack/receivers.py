"""HTTP Events and Socket Mode receivers sharing one normalization pipeline."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

from tc_backend.infrastructure.slack.errors import (
    SlackInvalidSignature,
    SlackPayloadError,
)
from tc_backend.infrastructure.slack.models import (
    ReceivedSlackEnvelope,
    SlackEnvelopeAcknowledgement,
    SlackIngressKind,
    SlackNormalizedEventType,
)
from tc_backend.infrastructure.slack.normalization import normalize_events_api_payload
from tc_backend.infrastructure.slack.signature import SlackRequestVerifier


class SlackHttpEventsReceiver:
    """Authenticate a raw HTTP request before decoding or normalizing its JSON."""

    def __init__(self, verifier: SlackRequestVerifier) -> None:
        self._verifier = verifier

    def receive(
        self,
        raw_body: bytes,
        *,
        headers: Mapping[str, str],
    ) -> ReceivedSlackEnvelope:
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        timestamp = normalized_headers.get("x-slack-request-timestamp")
        signature = normalized_headers.get("x-slack-signature")
        if timestamp is None or signature is None:
            raise SlackInvalidSignature("Slack signature headers are required")
        self._verifier.verify(raw_body, timestamp=timestamp, signature=signature)
        payload = _decode_object(raw_body, context="Slack HTTP request")
        event = normalize_events_api_payload(payload)
        response_payload = (
            {"challenge": event.challenge}
            if event.normalized_type is SlackNormalizedEventType.URL_VERIFICATION
            else None
        )
        return ReceivedSlackEnvelope(
            ingress=SlackIngressKind.HTTP,
            event=event,
            acknowledgement=SlackEnvelopeAcknowledgement(
                envelope_id=event.event_id,
                response_payload=response_payload,
            ),
        )


class SlackSocketEnvelopeReceiver:
    """Normalize one decoded Socket Mode envelope without doing network I/O."""

    def receive(self, envelope: dict[str, Any]) -> ReceivedSlackEnvelope:
        envelope_id = envelope.get("envelope_id")
        if not isinstance(envelope_id, str) or not envelope_id:
            raise SlackPayloadError("Socket Mode envelope requires envelope_id")
        if envelope.get("type") != "events_api":
            raise SlackPayloadError("Socket Mode receiver only accepts events_api envelopes")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise SlackPayloadError("Socket Mode envelope requires an object payload")
        event = normalize_events_api_payload(payload)
        return ReceivedSlackEnvelope(
            ingress=SlackIngressKind.SOCKET,
            event=event,
            acknowledgement=SlackEnvelopeAcknowledgement(envelope_id=envelope_id),
        )


class SocketModeConnection(Protocol):
    """Small async protocol implemented by websockets and deterministic fakes."""

    async def recv(self) -> str | bytes: ...

    async def send(self, message: str) -> None: ...


class SlackSocketModeReceiver:
    """Receive and acknowledge exactly one Socket Mode envelope."""

    def __init__(self, envelope_receiver: SlackSocketEnvelopeReceiver | None = None) -> None:
        self._envelope_receiver = envelope_receiver or SlackSocketEnvelopeReceiver()

    async def receive_once(self, connection: SocketModeConnection) -> ReceivedSlackEnvelope:
        raw_envelope = await connection.recv()
        raw_bytes = (
            raw_envelope.encode("utf-8")
            if isinstance(raw_envelope, str)
            else raw_envelope
        )
        envelope = _decode_object(raw_bytes, context="Slack Socket Mode envelope")
        received = self._envelope_receiver.receive(envelope)
        await connection.send(
            json.dumps(
                received.acknowledgement.socket_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return received


def _decode_object(raw_body: bytes, *, context: str) -> dict[str, Any]:
    try:
        decoded = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SlackPayloadError(f"{context} is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise SlackPayloadError(f"{context} must be a JSON object")
    return decoded
