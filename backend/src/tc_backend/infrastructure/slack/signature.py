"""Slack HTTP Events v0 request signing verification."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable

from tc_backend.infrastructure.slack.errors import (
    SlackInvalidSignature,
    SlackStaleRequest,
)

SLACK_SIGNATURE_VERSION = "v0"
DEFAULT_SIGNATURE_TOLERANCE_SECONDS = 5 * 60


class SlackRequestVerifier:
    """Verify the exact raw body Slack signed with its v0 HMAC SHA-256 scheme."""

    def __init__(
        self,
        signing_secret: str,
        *,
        tolerance_seconds: int = DEFAULT_SIGNATURE_TOLERANCE_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not signing_secret:
            raise ValueError("Slack signing secret must not be empty")
        if tolerance_seconds < 0:
            raise ValueError("Slack signature tolerance must not be negative")
        self._secret = signing_secret.encode("utf-8")
        self._tolerance_seconds = tolerance_seconds
        self._clock = clock

    def verify(self, raw_body: bytes, *, timestamp: str, signature: str) -> int:
        """Return the signed epoch second or raise a typed verification failure."""

        try:
            signed_at = int(timestamp, 10)
        except (TypeError, ValueError) as exc:
            raise SlackInvalidSignature("Slack request timestamp is not an integer") from exc
        if signed_at < 0:
            raise SlackInvalidSignature("Slack request timestamp must not be negative")
        if abs(self._clock() - signed_at) > self._tolerance_seconds:
            raise SlackStaleRequest("Slack request timestamp is outside the accepted window")

        base = b":".join((SLACK_SIGNATURE_VERSION.encode("ascii"), timestamp.encode("ascii"), raw_body))
        expected = f"{SLACK_SIGNATURE_VERSION}=" + hmac.new(
            self._secret,
            base,
            hashlib.sha256,
        ).hexdigest()
        # compare_digest is deliberately used even when the supplied value has
        # an invalid length or prefix, keeping comparison timing independent of
        # the first differing byte.
        if not hmac.compare_digest(expected, signature):
            raise SlackInvalidSignature("Slack request signature did not match")
        return signed_at


def sign_slack_request(*, signing_secret: str, timestamp: int, raw_body: bytes) -> str:
    """Deterministic helper for contract tests and local provider simulators."""

    timestamp_text = str(timestamp)
    base = b":".join((b"v0", timestamp_text.encode("ascii"), raw_body))
    return "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        base,
        hashlib.sha256,
    ).hexdigest()
