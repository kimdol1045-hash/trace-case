"""Typed configuration and the narrow credential materialization boundary."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlsplit

from tc_backend.generation.errors import GenerationConfigurationError, GenerationErrorCode

MAX_PROVIDER_ATTEMPTS = 5
MAX_TIMEOUT_SECONDS = 600.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Deterministic exponential retry policy with hard safety bounds."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    retryable_status_codes: frozenset[int] = _RETRYABLE_STATUS_CODES

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not 1 <= self.max_attempts <= MAX_PROVIDER_ATTEMPTS:
            _config_error(f"max_attempts must be between 1 and {MAX_PROVIDER_ATTEMPTS}")
        if not _finite_between(self.base_delay_seconds, minimum=0.0, maximum=60.0):
            _config_error("base_delay_seconds must be between 0 and 60")
        if not _finite_between(self.max_delay_seconds, minimum=0.0, maximum=60.0):
            _config_error("max_delay_seconds must be between 0 and 60")
        if self.max_delay_seconds < self.base_delay_seconds:
            _config_error("max_delay_seconds must not be below base_delay_seconds")
        if any(
            isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599
            for status in self.retryable_status_codes
        ):
            _config_error("retryable_status_codes contains an invalid HTTP status")

    def delay_before_attempt(self, attempt: int) -> float:
        """Return the delay before one-based ``attempt`` (attempt 1 has no delay)."""

        if attempt <= 1:
            return 0.0
        return float(
            min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt - 2)))
        )


@dataclass(frozen=True, slots=True)
class ProviderAuth:
    """A header credential that remains redacted until transport dispatch."""

    credential: str = field(repr=False)
    header_name: str = "Authorization"
    scheme: str = "Bearer"

    def __post_init__(self) -> None:
        if not isinstance(self.credential, str) or not self.credential:
            _config_error("provider credential must not be empty", code=GenerationErrorCode.AUTH_REQUIRED)
        if "\r" in self.credential or "\n" in self.credential:
            _config_error("provider credential contains invalid header characters")
        if not _is_header_token(self.header_name):
            _config_error("provider auth header name is invalid")
        if "\r" in self.scheme or "\n" in self.scheme:
            _config_error("provider auth scheme is invalid")

    def materialize_header(self) -> tuple[str, str]:
        """Reveal the secret only for the final outbound transport request."""

        value = f"{self.scheme.strip()} {self.credential}" if self.scheme.strip() else self.credential
        return self.header_name, value


@dataclass(frozen=True, slots=True, repr=False)
class ProviderConfig:
    """Provider-neutral HTTP runtime configuration.

    The endpoint must not contain URL user-info because that would leak a
    credential through routine URL logging. Provider/model IDs are trusted
    local routing configuration, never accepted from model output.
    """

    provider_id: str
    endpoint: str
    model_id: str
    auth: ProviderAuth | None = field(default=None, repr=False)
    timeout_seconds: float = 60.0
    response_max_bytes: int = 1024 * 1024
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    supported_schema_pairs: frozenset[tuple[str, str]] = frozenset({("1.0.0", "1.0.0")})

    def __post_init__(self) -> None:
        for name, value in (("provider_id", self.provider_id), ("model_id", self.model_id)):
            if not isinstance(value, str) or not value.strip():
                _config_error(f"{name} must not be empty")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            _config_error("endpoint must be an HTTP URL")
        if parsed.username is not None or parsed.password is not None:
            _config_error("endpoint must not contain user credentials")
        if not _finite_between(self.timeout_seconds, minimum=0.001, maximum=MAX_TIMEOUT_SECONDS):
            _config_error(f"timeout_seconds must be positive and at most {MAX_TIMEOUT_SECONDS:g}")
        if (
            isinstance(self.response_max_bytes, bool)
            or not isinstance(self.response_max_bytes, int)
            or not 1 <= self.response_max_bytes <= MAX_RESPONSE_BYTES
        ):
            _config_error(f"response_max_bytes must be between 1 and {MAX_RESPONSE_BYTES}")
        headers = dict(self.extra_headers)
        for name, value in headers.items():
            if not _is_header_token(name) or not isinstance(value, str):
                _config_error("extra_headers contains an invalid header")
            if "\r" in value or "\n" in value:
                _config_error("extra_headers contains an invalid header value")
            if name.casefold() in {"authorization", "content-type", "content-length"}:
                _config_error("extra_headers cannot override protected transport headers")
        if not self.supported_schema_pairs:
            _config_error("supported_schema_pairs must not be empty")
        for pair in self.supported_schema_pairs:
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or any(not isinstance(version, str) or not version.strip() for version in pair)
            ):
                _config_error("supported_schema_pairs contains an invalid version pair")
        object.__setattr__(self, "provider_id", self.provider_id.strip())
        object.__setattr__(self, "model_id", self.model_id.strip())
        object.__setattr__(self, "endpoint", self.endpoint.strip())
        object.__setattr__(self, "extra_headers", MappingProxyType(headers))

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(provider_id={self.provider_id!r}, "
            f"endpoint_configured=True, model_id={self.model_id!r}, "
            f"auth_configured={self.auth is not None!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"response_max_bytes={self.response_max_bytes!r}, retry={self.retry!r}, "
            f"extra_header_names={tuple(sorted(self.extra_headers))!r}, "
            f"supported_schema_pairs={tuple(sorted(self.supported_schema_pairs))!r})"
        )


def _finite_between(value: object, *, minimum: float, maximum: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def _is_header_token(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return all(character.isalnum() or character in "!#$%&'*+-.^_`|~" for character in value)


def _config_error(
    message: str,
    *,
    code: GenerationErrorCode = GenerationErrorCode.CONFIG_INVALID,
) -> None:
    raise GenerationConfigurationError(code, message)
