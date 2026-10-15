"""Stable, redacted errors for the generation provider boundary."""

from __future__ import annotations

from enum import StrEnum


class GenerationErrorCode(StrEnum):
    CONFIG_INVALID = "GENERATION_CONFIG_INVALID"
    UNSUPPORTED_SCHEMA_VERSION = "GENERATION_UNSUPPORTED_SCHEMA_VERSION"
    AUTH_REQUIRED = "GENERATION_AUTH_REQUIRED"
    AUTH_REJECTED = "GENERATION_AUTH_REJECTED"
    TIMEOUT = "GENERATION_TIMEOUT"
    TRANSPORT_UNAVAILABLE = "GENERATION_TRANSPORT_UNAVAILABLE"
    RATE_LIMITED = "GENERATION_RATE_LIMITED"
    PROVIDER_REJECTED = "GENERATION_PROVIDER_REJECTED"
    PROVIDER_ERROR = "GENERATION_PROVIDER_ERROR"
    INVALID_CONTENT_TYPE = "GENERATION_INVALID_CONTENT_TYPE"
    RESPONSE_TOO_LARGE = "GENERATION_RESPONSE_TOO_LARGE"
    MALFORMED_JSON = "GENERATION_MALFORMED_JSON"
    INVALID_RESPONSE = "GENERATION_INVALID_RESPONSE"


class GenerationError(RuntimeError):
    """A safe-to-report generation failure with a stable machine code.

    Provider response bodies, URLs, credentials, and arbitrary transport
    exception messages are deliberately not retained on the exception.
    """

    def __init__(
        self,
        code: GenerationErrorCode,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.attempts = attempts

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(code={self.code.value!r}, "
            f"retryable={self.retryable!r}, status_code={self.status_code!r}, "
            f"attempts={self.attempts!r})"
        )


class GenerationConfigurationError(GenerationError, ValueError):
    """Local provider configuration is missing or invalid."""


class GenerationTransportError(GenerationError):
    """The request failed before a valid JSON payload was available."""


class GenerationResponseError(GenerationError, ValueError):
    """The provider response cannot be safely decoded or accepted."""


class GenerationValidationError(GenerationResponseError):
    """Decoded provider output violates the deterministic output contract."""
