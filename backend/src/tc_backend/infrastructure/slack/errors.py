"""Typed failures exposed by the Slack provider boundary."""

from __future__ import annotations


class SlackError(RuntimeError):
    """Base class for Slack adapter failures."""


class SlackIntegrationDisabled(SlackError):
    """Slack is intentionally disabled for this process."""


class SlackSetupRequired(SlackError):
    """Slack was selected but its credential/configuration bundle is incomplete."""


class SlackRequestVerificationError(SlackError):
    """A public HTTP Events request failed authenticity validation."""


class SlackInvalidSignature(SlackRequestVerificationError):
    """The Slack v0 HMAC signature did not match the raw request body."""


class SlackStaleRequest(SlackRequestVerificationError):
    """The signed Slack request is outside the accepted timestamp window."""


class SlackPayloadError(SlackError, ValueError):
    """A provider payload is malformed or unsupported."""


class SlackTransportError(SlackError):
    """Base class for Slack Web API failures."""


class SlackAuthError(SlackTransportError):
    """Slack rejected the token, authorization, or required scope."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_code = provider_code
        self.status_code = status_code


class SlackRateLimitError(SlackTransportError):
    """Slack rate-limited a request after the configured retries were exhausted."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None,
        status_code: int = 429,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.status_code = status_code


class SlackProviderError(SlackTransportError):
    """Slack or the network returned a non-auth, non-rate provider failure."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider_code = provider_code
        self.status_code = status_code
        self.retryable = retryable


class SlackPermanentWriteError(SlackTransportError):
    """A write was definitively rejected and must never use a fallback destination."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_code = provider_code
        self.status_code = status_code


class SlackAmbiguousWriteError(SlackTransportError):
    """The request may have committed remotely; only reconciliation is safe."""

    def __init__(
        self,
        message: str,
        *,
        provider_code: str = "write_ambiguous",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_code = provider_code
        self.status_code = status_code
