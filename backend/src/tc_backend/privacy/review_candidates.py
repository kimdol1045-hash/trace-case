"""Fail-closed privacy and fingerprint primitives for review candidates.

This module is deliberately independent from exporters, database sessions, API
models, provider clients, and process configuration.  Callers pass key bytes
explicitly and receive only opaque source seals and sanitized JSON values.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, cast

CONTRACT_VERSION: Final = "review-candidate-envelope/1.0.0"
NORMALIZATION_VERSION: Final = "review-candidate-nfkc-lf-ignorables-v1"
SANITIZATION_POLICY_VERSION: Final = "review-candidate-sensitive-data-v1"
SAMPLING_ALGORITHM_VERSION: Final = "review-candidate-sampling-v1"
RETENTION_POLICY_VERSION: Final = "review-candidate-retention-v1"
SOURCE_SEAL_ALGORITHM: Final = "HMAC-SHA-256"
SYNTHETIC_RETENTION_DAYS: Final = 90
ACTUAL_MINIMIZED_RETENTION_DAYS: Final = 30

type DataClass = Literal["SYNTHETIC", "ACTUAL_MINIMIZED"]
type SanitizationMode = Literal["REDACT", "REJECT"]
type SamplingStrategy = Literal["ALL", "DETERMINISTIC_HASH"]
type SensitiveCategory = Literal[
    "ABSOLUTE_PATH",
    "DIRECT_IDENTIFIER",
    "EMAIL",
    "IP_ADDRESS",
    "KOREAN_IDENTIFIER",
    "MAC_ADDRESS",
    "PAYMENT_OR_ACCOUNT",
    "PHONE",
    "RAW_CONTENT_SHA",
    "SECRET",
    "URL",
]
type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

_MAX_DEPTH: Final = 64
_MAX_ARRAY_ITEMS: Final = 10_000
_MAX_OBJECT_PROPERTIES: Final = 1_000
_MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
_OPAQUE_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_RESERVED_TOKENS: Final = frozenset({"latest", "unknown", "unconfigured", "unpinned"})
_SAFE_PROPERTY_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_SENSITIVE_PROPERTY_NAME_PATTERNS: Final[
    tuple[tuple[SensitiveCategory, re.Pattern[str]], ...]
] = (
    ("RAW_CONTENT_SHA", re.compile(r"[Rr][Aa][Ww]")),
    (
        "URL",
        re.compile(r"[Uu][Rr][Ll]|[Uu][Rr][Ii]|[Pp]ermalink|[Hh]ref|[Ee]ndpoint"),
    ),
    (
        "SECRET",
        re.compile(
            r"[Ss]ecret|[Tt]oken|[Pp]assword|[Pp]asswd|[Aa]uthorization|"
            r"[Cc]redential|[Pp]rivate[Kk]ey|[Aa]pi[_-]?[Kk]ey"
        ),
    ),
    ("EMAIL", re.compile(r"[Ee]mail")),
    ("PHONE", re.compile(r"[Pp]hone|[Mm]obile|[Tt]elephone|[Ff]ax")),
    ("MAC_ADDRESS", re.compile(r"[Mm]ac[Aa]ddress|^[Mm][Aa][Cc]$")),
    ("IP_ADDRESS", re.compile(r"[Ii]p[Aa]ddress|^[Ii][Pp]$")),
    ("DIRECT_IDENTIFIER", re.compile(r"[Pp]ostal[Aa]ddress")),
    ("ABSOLUTE_PATH", re.compile(r"[Pp]ath")),
    (
        "RAW_CONTENT_SHA",
        re.compile(r"[Ss]ource|[Cc]ontent|[Bb]ody|[Mm]essage|[Pp]rompt|[Ee]xcerpt"),
    ),
    (
        "RAW_CONTENT_SHA",
        re.compile(r"[Cc]ontent(?:[Ss]ha|[Hh]ash|[Dd]igest)|[Ss]ha256"),
    ),
    (
        "DIRECT_IDENTIFIER",
        re.compile(
            r"(?:^[Ii][Dd][Ss]?$|(?:Id[s]?|ID[Ss]?)$|_[Ii][Dd][Ss]?$|"
            r"-[Ii][Dd][Ss]?$|[Ii]dentifier)"
        ),
    ),
    ("DIRECT_IDENTIFIER", re.compile(r"[Ss]lack|[Jj]ira")),
    (
        "PAYMENT_OR_ACCOUNT",
        re.compile(
            r"[Cc]ustomer|[Oo]rder|[Aa]ccount|[Pp]erson|[Pp]ayment|[Cc]ard|"
            r"[Ii]ban|[Pp]an|[Bb]illing|[Ff]irst[Nn]ame|[Ll]ast[Nn]ame|"
            r"[Ff]ull[Nn]ame"
        ),
    ),
)
_URL = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.-]*://|\bwww\.|"
    r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|ai|kr|co\.kr)(?:[/:?#]|\b))"
)
_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?82[- .]?)?01[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)|"
    r"(?<!\d)\+\d{1,3}(?:[- .()]?\d){7,14}(?!\d)|"
    r"(?<!\d)\d{2,4}[- ]\d{3,4}[- ]\d{4}(?!\d)"
)
_KOREAN_IDENTIFIER = re.compile(r"(?<!\d)(?:\d{6}[- ]?[1-8]\d{6}|\d{3}[- ]?\d{2}[- ]?\d{5})(?!\d)")
_MAC_ADDRESS = re.compile(r"(?i)\b[0-9a-f]{2}(?:(?::|-)[0-9a-f]{2}){5}\b")
_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:^|[\s\"'(])(?:/(?:users|home|var|etc|private|tmp|opt|srv|mnt|root)/"
    r"[^\s\"')]*|[a-z]:\\[^\s\"')]+|\\\\[^\s\\]+\\[^\s]+)"
)
_PRIVATE_KEY = re.compile(r"(?i)-----begin [a-z0-9 ]*private key-----")
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{4,}")
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|secret|api[-_ ]?key|access[-_ ]?token|auth[-_ ]?token|"
    r"authorization|client[-_ ]?secret)\b\s*[:=]\s*[^\s,;]{3,}"
)
_KNOWN_SECRET = re.compile(
    r"(?i)\b(?:xox[baprs]-[a-z0-9-]{8,}|sk-[a-z0-9_-]{12,}|"
    r"gh[pousr]_[a-z0-9]{20,}|akia[0-9a-z]{16}|"
    r"eyj[a-z0-9_-]{5,}\.[a-z0-9_-]{5,}\.[a-z0-9_-]{5,})\b"
)
_RAW_SHA = re.compile(r"(?i)(?:\bsha256:)?\b[a-f0-9]{64}\b")
_SLACK_IDENTIFIER = re.compile(r"\b(?:[TUWCGD][A-Z0-9]{8,}|\d{10}\.\d{6})\b")
_JIRA_IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-[1-9]\d*\b")
_BUSINESS_IDENTIFIER = re.compile(
    r"(?i)\b(?:customer|order|account|person|user|member|case|ticket)"
    r"[-_ ]?(?:id|number|no|key)\s*[:=#-]?\s*[a-z0-9][a-z0-9._:-]{3,}\b"
)
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_IPV4_CANDIDATE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6_CANDIDATE = re.compile(r"(?<![0-9a-fA-F:])[0-9a-fA-F:]{3,}(?![0-9a-fA-F:])")

_CONFUSABLE_TRANSLATION: Final = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "і": "i",
        "ј": "j",
        "ӏ": "l",
        "Α": "a",
        "Β": "b",
        "Ε": "e",
        "Ζ": "z",
        "Η": "h",
        "Ι": "i",
        "Κ": "k",
        "Μ": "m",
        "Ν": "n",
        "Ο": "o",
        "Ρ": "p",
        "Τ": "t",
        "Υ": "y",
        "Χ": "x",
        "α": "a",
        "β": "b",
        "ε": "e",
        "ι": "i",
        "κ": "k",
        "ο": "o",
        "ρ": "p",
        "τ": "t",
        "υ": "y",
        "χ": "x",
    }
)

_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "artifactKind",
        "contractVersion",
        "purpose",
        "dataClass",
        "candidate",
        "source",
        "sanitization",
        "sampling",
        "payload",
        "fingerprints",
        "review",
        "attestation",
        "retention",
        "certification",
    }
)
_NESTED_FIELDS: Final = {
    "candidate": frozenset(
        {"candidateKey", "revision", "createdAt", "predecessorArtifactFingerprint"}
    ),
    "source": frozenset({"sealAlgorithm", "keyId", "privacyDomain", "synthetic", "fieldSeals"}),
    "sourceField": frozenset({"fieldDomain", "seal"}),
    "sanitization": frozenset(
        {
            "policyVersion",
            "normalizationVersion",
            "mode",
            "status",
            "redactionCount",
            "findingCategories",
            "policyFingerprint",
        }
    ),
    "sampling": frozenset(
        {"strategy", "algorithmVersion", "populationSize", "sampleSize", "seedFingerprint"}
    ),
    "payload": frozenset({"schemaVersion", "projection"}),
    "fingerprints": frozenset({"payloadFingerprint", "artifactFingerprint"}),
    "review": frozenset(
        {"status", "reviewedAt", "reviewAuthoritySeal", "approvalArtifactFingerprint"}
    ),
    "attestation": frozenset({"status", "attestedAt", "attestationFingerprint"}),
    "retention": frozenset({"policyVersion", "disposition", "maxAgeDays", "expiresAt"}),
    "certification": frozenset(
        {"status", "eligible", "scope", "certificationFingerprint", "limitations"}
    ),
}


class ReviewCandidateError(ValueError):
    """Base class whose string form is always a stable, input-free code."""

    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class NormalizationError(ReviewCandidateError):
    """The input cannot be represented by the pinned JSON normalization."""


class SensitiveDataError(ReviewCandidateError):
    """A sanitized-only boundary received sensitive data."""

    categories: tuple[SensitiveCategory, ...]

    def __init__(self, categories: Sequence[SensitiveCategory] = ()) -> None:
        self.categories = tuple(sorted(set(categories)))
        super().__init__("SENSITIVE_DATA_DETECTED")


class RedactionError(ReviewCandidateError):
    """The conservative redactor could not prove that its output is clean."""


class PrivacyBoundaryError(ReviewCandidateError):
    """Synthetic and actual-minimized provenance declarations disagree."""


class SourceSealError(ReviewCandidateError):
    """Source key material or a declared source seal is invalid."""


class RevisionConflictError(ReviewCandidateError):
    """The caller's expected immutable revision does not match the artifact."""


class FingerprintMismatchError(ReviewCandidateError):
    """A candidate key or public fingerprint does not recompute exactly."""


class ReviewCandidateValidationError(ReviewCandidateError):
    """The envelope is malformed or requests an unsupported trust transition."""


@dataclass(frozen=True, slots=True)
class SensitiveFinding:
    """A source-free finding; location exposes only structural ordinals."""

    category: SensitiveCategory
    location: str


@dataclass(frozen=True, slots=True)
class SanitizationResult:
    """A normalized, scanner-clean projection and its source-free audit counts."""

    projection: JsonValue
    findings: tuple[SensitiveFinding, ...]

    @property
    def categories(self) -> tuple[SensitiveCategory, ...]:
        return tuple(sorted({finding.category for finding in self.findings}))

    @property
    def redaction_count(self) -> int:
        return len(self.findings)


@dataclass(frozen=True, slots=True)
class ReviewCandidateFingerprints:
    """Recomputed immutable identifiers for an envelope."""

    candidate_key: str
    payload_fingerprint: str
    artifact_fingerprint: str


def normalize_text(value: str) -> str:
    """Apply the pinned NFKC, LF, and Unicode-default-ignorable rules."""

    if not isinstance(value, str):
        raise NormalizationError("NORMALIZATION_TEXT_REQUIRED")
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(character for character in normalized if unicodedata.category(character) != "Cf")


def normalize_json(value: Any, *, _depth: int = 0) -> JsonValue:
    """Normalize a JSON value and reject ambiguous or cross-runtime-unsafe input."""

    if _depth > _MAX_DEPTH:
        raise NormalizationError("NORMALIZATION_MAX_DEPTH")
    if value is None or isinstance(value, bool):
        return cast(JsonScalar, value)
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise NormalizationError("NORMALIZATION_INTEGER_RANGE")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NormalizationError("NORMALIZATION_NON_FINITE_NUMBER")
        return 0 if value == 0 else value
    if isinstance(value, Mapping):
        if len(value) > _MAX_OBJECT_PROPERTIES:
            raise NormalizationError("NORMALIZATION_OBJECT_TOO_LARGE")
        normalized_items: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise NormalizationError("NORMALIZATION_OBJECT_KEY_TYPE")
            normalized_key = normalize_text(key)
            if not normalized_key:
                raise NormalizationError("NORMALIZATION_OBJECT_KEY_EMPTY")
            if normalized_key in normalized_items:
                raise NormalizationError("NORMALIZATION_OBJECT_KEY_COLLISION")
            normalized_items[normalized_key] = normalize_json(item, _depth=_depth + 1)
        return {key: normalized_items[key] for key in sorted(normalized_items)}
    if isinstance(value, list | tuple):
        if len(value) > _MAX_ARRAY_ITEMS:
            raise NormalizationError("NORMALIZATION_ARRAY_TOO_LARGE")
        return [normalize_json(item, _depth=_depth + 1) for item in value]
    raise NormalizationError("NORMALIZATION_UNSUPPORTED_TYPE")


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes under the pinned normalization."""

    return _canonicalize(normalize_json(value)).encode("utf-8")


def scan_sensitive_data(value: Any) -> tuple[SensitiveFinding, ...]:
    """Scan every normalized key and leaf without returning excerpts or raw paths."""

    normalized = normalize_json(value)
    findings: list[SensitiveFinding] = []
    _scan_node(normalized, "$", findings)
    return tuple(sorted(findings, key=lambda finding: (finding.location, finding.category)))


def sanitize_projection(value: Any, *, mode: SanitizationMode = "REDACT") -> SanitizationResult:
    """Normalize and either reject or conservatively replace sensitive leaves."""

    if mode not in {"REDACT", "REJECT"}:
        raise ReviewCandidateValidationError("SANITIZATION_MODE_INVALID")
    normalized = normalize_json(value)
    findings = scan_sensitive_data(normalized)
    if mode == "REJECT" and findings:
        raise SensitiveDataError([finding.category for finding in findings])
    projection = normalized if not findings else _redact_node(normalized)
    residual = scan_sensitive_data(projection)
    if residual:
        raise RedactionError("REDACTION_RESIDUAL_SENSITIVE_DATA")
    return SanitizationResult(projection=projection, findings=findings)


def seal_source_field(
    value: Any,
    *,
    key: bytes,
    key_id: str,
    privacy_domain: str,
    field_domain: str,
) -> str:
    """Create a domain-separated HMAC-SHA-256 seal for one raw source field."""

    _validate_hmac_key(key)
    safe_key_id = _opaque_token(key_id, "SOURCE_KEY_ID_INVALID")
    safe_privacy_domain = _opaque_token(privacy_domain, "PRIVACY_DOMAIN_INVALID")
    safe_field_domain = _opaque_token(field_domain, "FIELD_DOMAIN_INVALID")
    message = _framed(
        (
            b"tc.review-candidate.source-seal.v1",
            safe_key_id.encode("utf-8"),
            safe_privacy_domain.encode("utf-8"),
            safe_field_domain.encode("utf-8"),
            canonical_json_bytes(value),
        )
    )
    return f"hmac-sha256:{hmac.new(key, message, hashlib.sha256).hexdigest()}"


def fingerprint_sanitized_projection(value: Any, *, field_domain: str) -> str:
    """Hash only a scanner-clean projection using an explicit public domain."""

    safe_domain = _opaque_token(field_domain, "FINGERPRINT_DOMAIN_INVALID")
    normalized = normalize_json(value)
    findings = scan_sensitive_data(normalized)
    if findings:
        raise SensitiveDataError([finding.category for finding in findings])
    return _public_digest_unchecked(safe_domain, normalized)


def deterministic_sample(
    values: Sequence[Any], *, sample_size: int, seed_domain: str
) -> tuple[JsonValue, ...]:
    """Select an order-invariant sample from already-sanitized JSON values."""

    if isinstance(values, str | bytes) or sample_size < 0 or sample_size > len(values):
        raise ReviewCandidateValidationError("SAMPLING_INPUT_INVALID")
    safe_seed = _opaque_token(seed_domain, "SAMPLING_SEED_DOMAIN_INVALID")
    ranked: list[tuple[str, bytes, JsonValue]] = []
    for value in values:
        normalized = sanitize_projection(value, mode="REJECT").projection
        canonical = canonical_json_bytes(normalized)
        rank = hashlib.sha256(
            _framed((b"tc.review-candidate.sample.v1", safe_seed.encode(), canonical))
        ).hexdigest()
        ranked.append((rank, canonical, normalized))
    ranked.sort(key=lambda row: (row[0], row[1]))
    return tuple(row[2] for row in ranked[:sample_size])


def build_review_candidate(
    *,
    purpose: str,
    data_class: DataClass,
    source_is_synthetic: bool,
    source_fields: Mapping[str, Any],
    payload: Any,
    payload_schema_version: str,
    key: bytes,
    key_id: str,
    privacy_domain: str,
    created_at: datetime,
    sanitization_mode: SanitizationMode = "REDACT",
    sampling_strategy: SamplingStrategy = "ALL",
    population_size: int = 1,
    sample_size: int = 1,
    retention_days: int | None = None,
) -> JsonObject:
    """Build a deterministic revision-one, explicitly uncertified envelope."""

    safe_purpose = normalize_text(purpose)
    if not safe_purpose.strip() or scan_sensitive_data(safe_purpose):
        raise ReviewCandidateValidationError("PURPOSE_INVALID")
    if data_class not in {"SYNTHETIC", "ACTUAL_MINIMIZED"}:
        raise PrivacyBoundaryError("DATA_CLASS_INVALID")
    if source_is_synthetic is not (data_class == "SYNTHETIC"):
        raise PrivacyBoundaryError("DATA_CLASS_SOURCE_MISMATCH")
    safe_key_id = _opaque_token(key_id, "SOURCE_KEY_ID_INVALID")
    safe_privacy_domain = _opaque_token(privacy_domain, "PRIVACY_DOMAIN_INVALID")
    safe_schema_version = _opaque_token(payload_schema_version, "PAYLOAD_SCHEMA_VERSION_INVALID")
    if not source_fields or len(source_fields) > 256:
        raise SourceSealError("SOURCE_FIELD_SET_INVALID")
    _validate_hmac_key(key)

    field_seals: list[JsonValue] = []
    seen_domains: set[str] = set()
    for field_domain, value in source_fields.items():
        safe_field_domain = _opaque_token(field_domain, "FIELD_DOMAIN_INVALID")
        if safe_field_domain in seen_domains:
            raise SourceSealError("SOURCE_FIELD_DOMAIN_COLLISION")
        seen_domains.add(safe_field_domain)
        field_seals.append(
            {
                "fieldDomain": safe_field_domain,
                "seal": seal_source_field(
                    value,
                    key=key,
                    key_id=safe_key_id,
                    privacy_domain=safe_privacy_domain,
                    field_domain=safe_field_domain,
                ),
            }
        )
    field_seals.sort(key=lambda row: cast(str, cast(Mapping[str, JsonValue], row)["fieldDomain"]))
    source: JsonObject = {
        "sealAlgorithm": SOURCE_SEAL_ALGORITHM,
        "keyId": safe_key_id,
        "privacyDomain": safe_privacy_domain,
        "synthetic": source_is_synthetic,
        "fieldSeals": field_seals,
    }
    candidate_key = _candidate_key_value(safe_purpose, data_class, source)
    sanitized = sanitize_projection(payload, mode=sanitization_mode)
    payload_fingerprint = fingerprint_sanitized_projection(
        sanitized.projection, field_domain="payload"
    )
    _validate_sampling(sampling_strategy, population_size, sample_size)
    seed_fingerprint = _public_digest_unchecked(
        "sampling-seed",
        {
            "candidateKey": candidate_key,
            "strategy": sampling_strategy,
            "populationSize": population_size,
            "sampleSize": sample_size,
        },
    )
    created = _utc_timestamp(created_at)
    default_retention = (
        SYNTHETIC_RETENTION_DAYS if data_class == "SYNTHETIC" else ACTUAL_MINIMIZED_RETENTION_DAYS
    )
    effective_retention = default_retention if retention_days is None else retention_days
    if not isinstance(effective_retention, int) or isinstance(effective_retention, bool):
        raise ReviewCandidateValidationError("RETENTION_DAYS_INVALID")
    if effective_retention < 1 or effective_retention > default_retention:
        raise ReviewCandidateValidationError("RETENTION_DAYS_INVALID")
    expires = _utc_timestamp(created_at.astimezone(UTC) + timedelta(days=effective_retention))
    policy_fingerprint = _public_digest_unchecked(
        "sanitization-policy",
        {
            "normalizationVersion": NORMALIZATION_VERSION,
            "policyVersion": SANITIZATION_POLICY_VERSION,
            "findingCategories": list(sanitized.categories),
        },
    )
    envelope: JsonObject = {
        "artifactKind": "REVIEW_CANDIDATE",
        "contractVersion": CONTRACT_VERSION,
        "purpose": safe_purpose,
        "dataClass": data_class,
        "candidate": {
            "candidateKey": candidate_key,
            "revision": 1,
            "createdAt": created,
            "predecessorArtifactFingerprint": None,
        },
        "source": source,
        "sanitization": {
            "policyVersion": SANITIZATION_POLICY_VERSION,
            "normalizationVersion": NORMALIZATION_VERSION,
            "mode": sanitization_mode,
            "status": "CLEAN",
            "redactionCount": sanitized.redaction_count,
            "findingCategories": list(sanitized.categories),
            "policyFingerprint": policy_fingerprint,
        },
        "sampling": {
            "strategy": sampling_strategy,
            "algorithmVersion": SAMPLING_ALGORITHM_VERSION,
            "populationSize": population_size,
            "sampleSize": sample_size,
            "seedFingerprint": seed_fingerprint,
        },
        "payload": {
            "schemaVersion": safe_schema_version,
            "projection": sanitized.projection,
        },
        "fingerprints": {
            "payloadFingerprint": payload_fingerprint,
            "artifactFingerprint": f"sha256:{'0' * 64}",
        },
        "review": {
            "status": "UNREVIEWED",
            "reviewedAt": None,
            "reviewAuthoritySeal": None,
            "approvalArtifactFingerprint": None,
        },
        "attestation": {
            "status": "NOT_RUN",
            "attestedAt": None,
            "attestationFingerprint": None,
        },
        "retention": {
            "policyVersion": RETENTION_POLICY_VERSION,
            "disposition": "DELETE",
            "maxAgeDays": effective_retention,
            "expiresAt": expires,
        },
        "certification": {
            "status": "NOT_CERTIFIED",
            "eligible": False,
            "scope": None,
            "certificationFingerprint": None,
            "limitations": [
                "ATTESTATION_NOT_RUN",
                "NOT_A_RELEASE_CERTIFICATION",
                "REVIEW_REQUIRED",
                "SYNTHETIC_ONLY" if data_class == "SYNTHETIC" else "ACTUAL_DATA_MINIMIZED",
            ],
        },
    }
    fingerprints = cast(JsonObject, envelope["fingerprints"])
    fingerprints["artifactFingerprint"] = recompute_artifact_fingerprint(envelope)
    verify_review_candidate(envelope, expected_revision=1)
    return cast(JsonObject, normalize_json(envelope))


def recompute_candidate_key(envelope: Mapping[str, Any]) -> str:
    """Recompute the stable candidate key from purpose, class, and source seals."""

    normalized = normalize_json(envelope)
    if not isinstance(normalized, dict):
        raise ReviewCandidateValidationError("ENVELOPE_OBJECT_REQUIRED")
    purpose = normalized.get("purpose")
    data_class = normalized.get("dataClass")
    source = normalized.get("source")
    if (
        not isinstance(purpose, str)
        or data_class not in {"SYNTHETIC", "ACTUAL_MINIMIZED"}
        or not isinstance(source, dict)
    ):
        raise ReviewCandidateValidationError("CANDIDATE_KEY_INPUT_INVALID")
    _validate_public_source_metadata(source, cast(DataClass, data_class))
    return _candidate_key_value(purpose, cast(DataClass, data_class), source)


def recompute_artifact_fingerprint(envelope: Mapping[str, Any]) -> str:
    """Recompute the artifact fingerprint without trusting its declared value."""

    normalized = normalize_json(envelope)
    if not isinstance(normalized, dict):
        raise ReviewCandidateValidationError("ENVELOPE_OBJECT_REQUIRED")
    _require_exact_fields(normalized, _TOP_LEVEL_FIELDS, "ENVELOPE_FIELD_SET_INVALID")
    purpose = normalized.get("purpose")
    data_class = normalized.get("dataClass")
    if (
        not isinstance(purpose, str)
        or scan_sensitive_data(purpose)
        or data_class not in {"SYNTHETIC", "ACTUAL_MINIMIZED"}
    ):
        raise ReviewCandidateValidationError("ARTIFACT_FINGERPRINT_INPUT_INVALID")
    for name in _NESTED_FIELDS:
        if name == "sourceField":
            continue
        section = _strict_object(normalized, name)
        _require_exact_fields(section, _NESTED_FIELDS[name], "ENVELOPE_FIELD_SET_INVALID")
    source = _strict_object(normalized, "source")
    _validate_public_source_metadata(source, cast(DataClass, data_class))
    payload = _strict_object(normalized, "payload")
    if scan_sensitive_data(payload["projection"]):
        raise SensitiveDataError()
    fingerprints = normalized.get("fingerprints")
    if not isinstance(fingerprints, dict):
        raise ReviewCandidateValidationError("FINGERPRINTS_OBJECT_REQUIRED")
    identity = {key: value for key, value in normalized.items() if key != "fingerprints"}
    identity["fingerprints"] = {
        key: value for key, value in fingerprints.items() if key != "artifactFingerprint"
    }
    return _public_digest_unchecked("artifact", identity)


def recompute_review_candidate_fingerprints(
    envelope: Mapping[str, Any],
) -> ReviewCandidateFingerprints:
    """Return all recomputable envelope identifiers without mutating the input."""

    normalized = normalize_json(envelope)
    if not isinstance(normalized, dict):
        raise ReviewCandidateValidationError("ENVELOPE_OBJECT_REQUIRED")
    payload = normalized.get("payload")
    if not isinstance(payload, dict) or "projection" not in payload:
        raise ReviewCandidateValidationError("PAYLOAD_OBJECT_INVALID")
    return ReviewCandidateFingerprints(
        candidate_key=recompute_candidate_key(normalized),
        payload_fingerprint=fingerprint_sanitized_projection(
            payload["projection"], field_domain="payload"
        ),
        artifact_fingerprint=recompute_artifact_fingerprint(normalized),
    )


def verify_review_candidate(
    envelope: Mapping[str, Any], *, expected_revision: int | None = None
) -> None:
    """Validate strict structure, privacy boundary, revision, and fingerprints."""

    normalized = normalize_json(envelope)
    if not isinstance(normalized, dict):
        raise ReviewCandidateValidationError("ENVELOPE_OBJECT_REQUIRED")
    if normalized != envelope:
        raise ReviewCandidateValidationError("ENVELOPE_NOT_NORMALIZED")
    _require_exact_fields(normalized, _TOP_LEVEL_FIELDS, "ENVELOPE_FIELD_SET_INVALID")
    if (
        normalized["artifactKind"] != "REVIEW_CANDIDATE"
        or normalized["contractVersion"] != CONTRACT_VERSION
    ):
        raise ReviewCandidateValidationError("ENVELOPE_PIN_INVALID")
    purpose = normalized["purpose"]
    data_class = normalized["dataClass"]
    if not isinstance(purpose, str) or not purpose.strip() or scan_sensitive_data(purpose):
        raise ReviewCandidateValidationError("PURPOSE_INVALID")
    if data_class not in {"SYNTHETIC", "ACTUAL_MINIMIZED"}:
        raise PrivacyBoundaryError("DATA_CLASS_INVALID")

    candidate = _strict_object(normalized, "candidate")
    source = _strict_object(normalized, "source")
    sanitization = _strict_object(normalized, "sanitization")
    sampling = _strict_object(normalized, "sampling")
    payload = _strict_object(normalized, "payload")
    fingerprints = _strict_object(normalized, "fingerprints")
    review = _strict_object(normalized, "review")
    attestation = _strict_object(normalized, "attestation")
    retention = _strict_object(normalized, "retention")
    certification = _strict_object(normalized, "certification")
    for name, value in (
        ("candidate", candidate),
        ("source", source),
        ("sanitization", sanitization),
        ("sampling", sampling),
        ("payload", payload),
        ("fingerprints", fingerprints),
        ("review", review),
        ("attestation", attestation),
        ("retention", retention),
        ("certification", certification),
    ):
        _require_exact_fields(value, _NESTED_FIELDS[name], "ENVELOPE_FIELD_SET_INVALID")

    revision = candidate["revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ReviewCandidateValidationError("CANDIDATE_REVISION_INVALID")
    if expected_revision is not None and revision != expected_revision:
        raise RevisionConflictError("REVISION_CONFLICT")
    _utc_datetime(cast(str, candidate["createdAt"]), "CANDIDATE_TIMESTAMP_INVALID")
    if revision > 1 and candidate["predecessorArtifactFingerprint"] is None:
        raise ReviewCandidateValidationError("PREDECESSOR_FINGERPRINT_REQUIRED")

    if source["sealAlgorithm"] != SOURCE_SEAL_ALGORITHM:
        raise SourceSealError("SOURCE_SEAL_ALGORITHM_INVALID")
    _opaque_token(cast(str, source["keyId"]), "SOURCE_KEY_ID_INVALID")
    _opaque_token(cast(str, source["privacyDomain"]), "PRIVACY_DOMAIN_INVALID")
    if source["synthetic"] is not (data_class == "SYNTHETIC"):
        raise PrivacyBoundaryError("DATA_CLASS_SOURCE_MISMATCH")
    field_seals = source["fieldSeals"]
    if not isinstance(field_seals, list) or not field_seals or len(field_seals) > 256:
        raise SourceSealError("SOURCE_FIELD_SET_INVALID")
    domains: list[str] = []
    for field in field_seals:
        if not isinstance(field, dict):
            raise SourceSealError("SOURCE_FIELD_SEAL_INVALID")
        _require_exact_fields(field, _NESTED_FIELDS["sourceField"], "SOURCE_FIELD_SEAL_INVALID")
        domain = _opaque_token(cast(str, field["fieldDomain"]), "FIELD_DOMAIN_INVALID")
        seal = field["seal"]
        if not isinstance(seal, str) or re.fullmatch(r"hmac-sha256:[a-f0-9]{64}", seal) is None:
            raise SourceSealError("SOURCE_FIELD_SEAL_INVALID")
        domains.append(domain)
    if domains != sorted(set(domains)):
        raise SourceSealError("SOURCE_FIELD_DOMAIN_ORDER_INVALID")

    projection = payload["projection"]
    residual = scan_sensitive_data(projection)
    if residual:
        raise SensitiveDataError([finding.category for finding in residual])
    _opaque_token(cast(str, payload["schemaVersion"]), "PAYLOAD_SCHEMA_VERSION_INVALID")
    if (
        sanitization["policyVersion"] != SANITIZATION_POLICY_VERSION
        or sanitization["normalizationVersion"] != NORMALIZATION_VERSION
        or sanitization["mode"] not in {"REDACT", "REJECT"}
        or sanitization["status"] != "CLEAN"
        or not isinstance(sanitization["redactionCount"], int)
        or isinstance(sanitization["redactionCount"], bool)
        or cast(int, sanitization["redactionCount"]) < 0
    ):
        raise ReviewCandidateValidationError("SANITIZATION_METADATA_INVALID")
    category_values = sanitization["findingCategories"]
    if not isinstance(category_values, list) or any(
        not isinstance(category, str) for category in category_values
    ):
        raise ReviewCandidateValidationError("SANITIZATION_CATEGORIES_INVALID")
    categories = cast(list[str], category_values)
    if categories != sorted(set(categories)):
        raise ReviewCandidateValidationError("SANITIZATION_CATEGORIES_INVALID")
    allowed_categories = {
        "ABSOLUTE_PATH",
        "DIRECT_IDENTIFIER",
        "EMAIL",
        "IP_ADDRESS",
        "KOREAN_IDENTIFIER",
        "MAC_ADDRESS",
        "PAYMENT_OR_ACCOUNT",
        "PHONE",
        "RAW_CONTENT_SHA",
        "SECRET",
        "URL",
    }
    if any(category not in allowed_categories for category in categories):
        raise ReviewCandidateValidationError("SANITIZATION_CATEGORIES_INVALID")
    expected_policy_fingerprint = _public_digest_unchecked(
        "sanitization-policy",
        {
            "normalizationVersion": NORMALIZATION_VERSION,
            "policyVersion": SANITIZATION_POLICY_VERSION,
            "findingCategories": categories,
        },
    )
    if sanitization["policyFingerprint"] != expected_policy_fingerprint:
        raise FingerprintMismatchError("FINGERPRINT_MISMATCH:policyFingerprint")

    if sampling["algorithmVersion"] != SAMPLING_ALGORITHM_VERSION:
        raise ReviewCandidateValidationError("SAMPLING_ALGORITHM_INVALID")
    _validate_sampling(
        cast(SamplingStrategy, sampling["strategy"]),
        cast(int, sampling["populationSize"]),
        cast(int, sampling["sampleSize"]),
    )
    expected_seed = _public_digest_unchecked(
        "sampling-seed",
        {
            "candidateKey": candidate["candidateKey"],
            "strategy": sampling["strategy"],
            "populationSize": sampling["populationSize"],
            "sampleSize": sampling["sampleSize"],
        },
    )
    if sampling["seedFingerprint"] != expected_seed:
        raise FingerprintMismatchError("FINGERPRINT_MISMATCH:seedFingerprint")

    if (
        retention["policyVersion"] != RETENTION_POLICY_VERSION
        or retention["disposition"] != "DELETE"
    ):
        raise ReviewCandidateValidationError("RETENTION_POLICY_INVALID")
    days = retention["maxAgeDays"]
    maximum_days = (
        SYNTHETIC_RETENTION_DAYS if data_class == "SYNTHETIC" else ACTUAL_MINIMIZED_RETENTION_DAYS
    )
    if not isinstance(days, int) or isinstance(days, bool) or days < 1 or days > maximum_days:
        raise ReviewCandidateValidationError("RETENTION_DAYS_INVALID")
    created_at = _utc_datetime(cast(str, candidate["createdAt"]), "CANDIDATE_TIMESTAMP_INVALID")
    expires_at = _utc_datetime(cast(str, retention["expiresAt"]), "RETENTION_TIMESTAMP_INVALID")
    if expires_at != created_at + timedelta(days=days):
        raise ReviewCandidateValidationError("RETENTION_EXPIRY_MISMATCH")

    if revision == 1:
        if candidate["predecessorArtifactFingerprint"] is not None:
            raise ReviewCandidateValidationError("FRESH_PREDECESSOR_FORBIDDEN")
        if review != {
            "approvalArtifactFingerprint": None,
            "reviewAuthoritySeal": None,
            "reviewedAt": None,
            "status": "UNREVIEWED",
        }:
            raise ReviewCandidateValidationError("FRESH_REVIEW_STATE_INVALID")
        if attestation != {
            "attestationFingerprint": None,
            "attestedAt": None,
            "status": "NOT_RUN",
        }:
            raise ReviewCandidateValidationError("FRESH_ATTESTATION_STATE_INVALID")
        if (
            certification["status"] != "NOT_CERTIFIED"
            or certification["eligible"] is not False
            or certification["scope"] is not None
            or certification["certificationFingerprint"] is not None
        ):
            raise ReviewCandidateValidationError("FRESH_CERTIFICATION_STATE_INVALID")
    if certification["status"] == "CERTIFIED" or certification["eligible"] is not False:
        raise ReviewCandidateValidationError("CERTIFICATION_UNSUPPORTED")

    expected = recompute_review_candidate_fingerprints(normalized)
    if candidate["candidateKey"] != expected.candidate_key:
        raise FingerprintMismatchError("FINGERPRINT_MISMATCH:candidateKey")
    if fingerprints["payloadFingerprint"] != expected.payload_fingerprint:
        raise FingerprintMismatchError("FINGERPRINT_MISMATCH:payloadFingerprint")
    if fingerprints["artifactFingerprint"] != expected.artifact_fingerprint:
        raise FingerprintMismatchError("FINGERPRINT_MISMATCH:artifactFingerprint")


def verify_source_seals(
    envelope: Mapping[str, Any], *, source_fields: Mapping[str, Any], key: bytes
) -> None:
    """Recompute source seals when an authorized caller possesses raw inputs."""

    normalized = normalize_json(envelope)
    if not isinstance(normalized, dict) or not isinstance(normalized.get("source"), dict):
        raise SourceSealError("SOURCE_OBJECT_INVALID")
    source = cast(dict[str, JsonValue], normalized["source"])
    key_id = cast(str, source.get("keyId"))
    privacy_domain = cast(str, source.get("privacyDomain"))
    declared = source.get("fieldSeals")
    if not isinstance(declared, list) or set(source_fields) != {
        cast(str, field.get("fieldDomain")) for field in declared if isinstance(field, dict)
    }:
        raise SourceSealError("SOURCE_FIELD_SET_MISMATCH")
    expected = [
        {
            "fieldDomain": field_domain,
            "seal": seal_source_field(
                value,
                key=key,
                key_id=key_id,
                privacy_domain=privacy_domain,
                field_domain=field_domain,
            ),
        }
        for field_domain, value in sorted(source_fields.items())
    ]
    if not hmac.compare_digest(canonical_json_bytes(declared), canonical_json_bytes(expected)):
        raise SourceSealError("SOURCE_SEAL_MISMATCH")


def _canonicalize(value: JsonValue) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == 0:
            return "0"
        if value.is_integer() and abs(value) < 1e21:
            return str(int(value))
        rendered = json.dumps(value, allow_nan=False, separators=(",", ":"))
        if "e" in rendered:
            mantissa, exponent = rendered.split("e")
            sign = "-" if exponent.startswith("-") else "+"
            digits = exponent.lstrip("+-0") or "0"
            return f"{mantissa}e{sign}{digits}"
        return rendered
    if isinstance(value, list):
        return "[" + ",".join(_canonicalize(item) for item in value) + "]"
    return (
        "{"
        + ",".join(f"{_canonicalize(key)}:{_canonicalize(value[key])}" for key in sorted(value))
        + "}"
    )


def _skeleton(value: str) -> str:
    normalized = normalize_text(value).casefold().translate(_CONFUSABLE_TRANSLATION)
    decomposed = unicodedata.normalize("NFKD", normalized)
    return "".join(character for character in decomposed if unicodedata.category(character) != "Mn")


def _key_categories(key: str) -> set[SensitiveCategory]:
    normalized = normalize_text(key)
    # This is the producer copy of SafePropertyName.  Keeping the ASCII
    # grammar and the conservative substring patterns literal makes the
    # producer and JSON Schema decide every normalized key identically.
    categories: set[SensitiveCategory] = (
        set() if _SAFE_PROPERTY_NAME.fullmatch(normalized) is not None else {"DIRECT_IDENTIFIER"}
    )
    categories.update(
        category
        for category, pattern in _SENSITIVE_PROPERTY_NAME_PATTERNS
        if pattern.search(normalized) is not None
    )
    return categories


def _text_categories(value: str) -> set[SensitiveCategory]:
    skeleton = _skeleton(value)
    categories: set[SensitiveCategory] = set()
    if _URL.search(skeleton):
        categories.add("URL")
    if _EMAIL.search(skeleton):
        categories.add("EMAIL")
    if _PHONE.search(skeleton):
        categories.add("PHONE")
    if _KOREAN_IDENTIFIER.search(skeleton):
        categories.add("KOREAN_IDENTIFIER")
    if _MAC_ADDRESS.search(skeleton):
        categories.add("MAC_ADDRESS")
    if _ABSOLUTE_PATH.search(skeleton):
        categories.add("ABSOLUTE_PATH")
    if (
        _PRIVATE_KEY.search(skeleton)
        or _BEARER.search(skeleton)
        or _CREDENTIAL_ASSIGNMENT.search(skeleton)
        or _KNOWN_SECRET.search(skeleton)
    ):
        categories.add("SECRET")
    if _RAW_SHA.search(skeleton):
        categories.add("RAW_CONTENT_SHA")
    if (
        _SLACK_IDENTIFIER.search(normalize_text(value))
        or _JIRA_IDENTIFIER.search(normalize_text(value))
        or _BUSINESS_IDENTIFIER.search(skeleton)
    ):
        categories.add("DIRECT_IDENTIFIER")
    if _BUSINESS_IDENTIFIER.search(skeleton) or any(
        _luhn_valid(re.sub(r"\D", "", candidate.group()))
        for candidate in _CARD_CANDIDATE.finditer(skeleton)
    ):
        categories.add("PAYMENT_OR_ACCOUNT")
    if _contains_ip_address(skeleton):
        categories.add("IP_ADDRESS")
    return categories


def _scan_node(value: JsonValue, location: str, findings: list[SensitiveFinding]) -> None:
    if isinstance(value, str):
        findings.extend(
            SensitiveFinding(category, location) for category in sorted(_text_categories(value))
        )
        return
    if isinstance(value, int) and not isinstance(value, bool):
        digits = str(abs(value))
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            findings.append(SensitiveFinding("PAYMENT_OR_ACCOUNT", location))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _scan_node(item, f"{location}[{index}]", findings)
        return
    if isinstance(value, dict):
        for index, key in enumerate(sorted(value), start=1):
            child_location = f"{location}.field[{index}]"
            findings.extend(
                SensitiveFinding(category, child_location)
                for category in sorted(_key_categories(key))
            )
            _scan_node(value[key], child_location, findings)


def _redact_node(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        categories = _text_categories(value)
        return _redaction_marker(categories) if categories else value
    if isinstance(value, int) and not isinstance(value, bool):
        digits = str(abs(value))
        return (
            _redaction_marker({"PAYMENT_OR_ACCOUNT"})
            if 13 <= len(digits) <= 19 and _luhn_valid(digits)
            else value
        )
    if isinstance(value, list):
        return [_redact_node(item) for item in value]
    if isinstance(value, dict):
        output: dict[str, JsonValue] = {}
        redacted_index = 0
        for key in sorted(value):
            key_categories = _key_categories(key)
            if key_categories:
                redacted_index += 1
                nested_categories = {
                    finding.category for finding in scan_sensitive_data(value[key])
                }
                output[f"redactedField{redacted_index}"] = _redaction_marker(
                    key_categories | nested_categories
                )
            else:
                output[key] = _redact_node(value[key])
        return output
    return value


def _redaction_marker(categories: set[SensitiveCategory]) -> str:
    joined = "+".join(sorted(categories)) if categories else "SENSITIVE"
    return f"[REDACTED:{joined}]"


def _contains_ip_address(value: str) -> bool:
    for candidate in _IPV4_CANDIDATE.findall(value):
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return True
    for candidate in _IPV6_CANDIDATE.findall(value):
        if candidate.count(":") < 2:
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return True
    return False


def _luhn_valid(digits: str) -> bool:
    if not digits.isdigit() or not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        value = int(character)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        checksum += value
    return checksum % 10 == 0


def _validate_hmac_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) < 32:
        raise SourceSealError("SOURCE_KEY_INVALID")


def _opaque_token(value: str, code: str) -> str:
    if not isinstance(value, str):
        raise ReviewCandidateValidationError(code)
    normalized = normalize_text(value)
    if _OPAQUE_TOKEN.fullmatch(normalized) is None or normalized.casefold() in _RESERVED_TOKENS:
        raise ReviewCandidateValidationError(code)
    return normalized


def _framed(parts: Sequence[bytes]) -> bytes:
    return b"".join(len(part).to_bytes(8, "big") + part for part in parts)


def _public_digest_unchecked(field_domain: str, value: Any) -> str:
    message = _framed(
        (
            b"tc.review-candidate.public-fingerprint.v1",
            field_domain.encode("utf-8"),
            canonical_json_bytes(value),
        )
    )
    return f"sha256:{hashlib.sha256(message).hexdigest()}"


def _candidate_key_value(purpose: str, data_class: DataClass, source: Mapping[str, Any]) -> str:
    digest = _public_digest_unchecked(
        "candidate-key",
        {"purpose": purpose, "dataClass": data_class, "source": source},
    )
    return f"review-candidate:{digest.removeprefix('sha256:')}"


def _validate_sampling(strategy: SamplingStrategy, population_size: int, sample_size: int) -> None:
    if strategy not in {"ALL", "DETERMINISTIC_HASH"}:
        raise ReviewCandidateValidationError("SAMPLING_STRATEGY_INVALID")
    if (
        not isinstance(population_size, int)
        or isinstance(population_size, bool)
        or not isinstance(sample_size, int)
        or isinstance(sample_size, bool)
        or population_size < 0
        or sample_size < 0
        or sample_size > population_size
        or (strategy == "ALL" and sample_size != population_size)
    ):
        raise ReviewCandidateValidationError("SAMPLING_COUNTS_INVALID")


def _validate_public_source_metadata(
    source: Mapping[str, JsonValue], data_class: DataClass
) -> None:
    _require_exact_fields(source, _NESTED_FIELDS["source"], "SOURCE_OBJECT_INVALID")
    if source.get("sealAlgorithm") != SOURCE_SEAL_ALGORITHM:
        raise SourceSealError("SOURCE_SEAL_ALGORITHM_INVALID")
    _opaque_token(cast(str, source.get("keyId")), "SOURCE_KEY_ID_INVALID")
    _opaque_token(cast(str, source.get("privacyDomain")), "PRIVACY_DOMAIN_INVALID")
    if source.get("synthetic") is not (data_class == "SYNTHETIC"):
        raise PrivacyBoundaryError("DATA_CLASS_SOURCE_MISMATCH")
    field_seals = source.get("fieldSeals")
    if not isinstance(field_seals, list) or not field_seals or len(field_seals) > 256:
        raise SourceSealError("SOURCE_FIELD_SET_INVALID")
    domains: list[str] = []
    for field in field_seals:
        if not isinstance(field, dict):
            raise SourceSealError("SOURCE_FIELD_SEAL_INVALID")
        _require_exact_fields(field, _NESTED_FIELDS["sourceField"], "SOURCE_FIELD_SEAL_INVALID")
        domain = _opaque_token(cast(str, field.get("fieldDomain")), "FIELD_DOMAIN_INVALID")
        seal = field.get("seal")
        if not isinstance(seal, str) or re.fullmatch(r"hmac-sha256:[a-f0-9]{64}", seal) is None:
            raise SourceSealError("SOURCE_FIELD_SEAL_INVALID")
        domains.append(domain)
    if domains != sorted(set(domains)):
        raise SourceSealError("SOURCE_FIELD_DOMAIN_ORDER_INVALID")


def _utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ReviewCandidateValidationError("TIMESTAMP_MUST_BE_AWARE")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_datetime(value: str, code: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReviewCandidateValidationError(code)
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        raise ReviewCandidateValidationError(code) from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ReviewCandidateValidationError(code)
    return parsed


def _strict_object(parent: Mapping[str, JsonValue], field: str) -> dict[str, JsonValue]:
    value = parent.get(field)
    if not isinstance(value, dict):
        raise ReviewCandidateValidationError("ENVELOPE_OBJECT_FIELD_INVALID")
    return value


def _require_exact_fields(
    value: Mapping[str, JsonValue], expected: frozenset[str], code: str
) -> None:
    if set(value) != expected:
        raise ReviewCandidateValidationError(code)
