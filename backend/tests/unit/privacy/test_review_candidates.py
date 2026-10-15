from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tc_backend.privacy.review_candidates import (
    ACTUAL_MINIMIZED_RETENTION_DAYS,
    SYNTHETIC_RETENTION_DAYS,
    FingerprintMismatchError,
    NormalizationError,
    PrivacyBoundaryError,
    ReviewCandidateValidationError,
    RevisionConflictError,
    SensitiveDataError,
    SourceSealError,
    build_review_candidate,
    canonical_json_bytes,
    deterministic_sample,
    fingerprint_sanitized_projection,
    recompute_review_candidate_fingerprints,
    sanitize_projection,
    scan_sensitive_data,
    seal_source_field,
    verify_review_candidate,
    verify_source_seals,
)

KEY = b"review-candidate-test-key-material-v1"
OTHER_KEY = b"review-candidate-test-key-material-v2"
CREATED_AT = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
SAFE_PROPERTY_NAME_CASES = json.loads(
    (
        Path(__file__).resolve().parents[4]
        / "packages/contracts/review-candidate/1.0.0/safe-property-name-cases.json"
    ).read_text(encoding="utf-8")
)


def _candidate(
    *,
    data_class: str = "SYNTHETIC",
    source_is_synthetic: bool = True,
    source_fields: dict[str, object] | None = None,
    payload: object | None = None,
) -> dict[str, object]:
    return build_review_candidate(
        purpose="automatic-generation-quality-review",
        data_class=data_class,  # type: ignore[arg-type]
        source_is_synthetic=source_is_synthetic,
        source_fields=source_fields
        or {
            "source-revision": "revision-7",
            "source-record": {"workspace": "T12345678", "message": "1700000000.000100"},
        },
        payload=payload
        or {
            "classification": "GROUNDED_ACTIVE",
            "counts": {"cases": 3, "evidence": 6},
        },
        payload_schema_version="quality-projection-v1",
        key=KEY,
        key_id="quality-key-v1",
        privacy_domain="quality-review",
        created_at=CREATED_AT,
        population_size=2,
        sample_size=2,
    )


def test_canonical_replay_and_required_order_invariance() -> None:
    assert canonical_json_bytes({"b": 2, "a": "Ａ\u200b\r\n"}) == canonical_json_bytes(
        {"a": "A\n", "b": 2}
    )
    with pytest.raises(NormalizationError, match="NORMALIZATION_OBJECT_KEY_COLLISION"):
        canonical_json_bytes({"A": 1, "Ａ": 2})

    first = _candidate(
        source_fields={"source-revision": "r7", "source-record": {"b": 2, "a": 1}},
        payload={"labels": ["fixture", "safe"], "counts": {"evidence": 4, "cases": 2}},
    )
    replay = _candidate(
        source_fields={"source-record": {"a": 1, "b": 2}, "source-revision": "r7"},
        payload={"counts": {"cases": 2, "evidence": 4}, "labels": ["fixture", "safe"]},
    )
    assert first == replay

    rows = ({"label": "gamma"}, {"label": "alpha"}, {"label": "beta"})
    assert deterministic_sample(rows, sample_size=2, seed_domain="quality") == (
        deterministic_sample(tuple(reversed(rows)), sample_size=2, seed_domain="quality")
    )


def test_source_seals_are_unlinkable_across_keys_privacy_domains_and_field_domains() -> None:
    arguments = {
        "value": {"direct": "U12345678", "url": "https://private.example/item"},
        "key_id": "key-v1",
        "privacy_domain": "review-a",
        "field_domain": "source-record",
    }
    first = seal_source_field(key=KEY, **arguments)
    replay = seal_source_field(key=KEY, **arguments)
    different_key = seal_source_field(key=OTHER_KEY, **arguments)
    different_privacy_domain = seal_source_field(
        key=KEY, **{**arguments, "privacy_domain": "review-b"}
    )
    different_field_domain = seal_source_field(
        key=KEY, **{**arguments, "field_domain": "source-revision"}
    )

    assert first == replay
    assert len({first, different_key, different_privacy_domain, different_field_domain}) == 4
    assert first.startswith("hmac-sha256:")
    assert "U12345678" not in first
    assert "private.example" not in first


def test_revision_conflicts_and_source_revision_drift_fail_closed() -> None:
    candidate = _candidate()
    verify_review_candidate(candidate, expected_revision=1)
    with pytest.raises(RevisionConflictError, match="^REVISION_CONFLICT$"):
        verify_review_candidate(candidate, expected_revision=2)

    verify_source_seals(
        candidate,
        source_fields={
            "source-revision": "revision-7",
            "source-record": {"workspace": "T12345678", "message": "1700000000.000100"},
        },
        key=KEY,
    )
    with pytest.raises(SourceSealError, match="^SOURCE_SEAL_MISMATCH$"):
        verify_source_seals(
            candidate,
            source_fields={
                "source-revision": "revision-8",
                "source-record": {
                    "workspace": "T12345678",
                    "message": "1700000000.000100",
                },
            },
            key=KEY,
        )


@pytest.mark.parametrize(
    ("value", "category"),
    [
        ("password=hunter2-secret", "SECRET"),
        ("bеаrеr abcdefghijklmnop", "SECRET"),
        ("BЕАRЕR abcdefghijklmnop", "SECRET"),
        ("-----BEGIN PRIVATE KEY-----", "SECRET"),
        ("person@example.com", "EMAIL"),
        ("010-1234-5678", "PHONE"),
        ("900101-1234567", "KOREAN_IDENTIFIER"),
        ("203.0.113.12", "IP_ADDRESS"),
        ("2001:db8::1", "IP_ADDRESS"),
        ("00:11:22:33:44:55", "MAC_ADDRESS"),
        ("https://workspace.example/private", "URL"),
        ("/Users/private/customer.txt", "ABSOLUTE_PATH"),
        ("U12345678", "DIRECT_IDENTIFIER"),
        ("PROJ-1234", "DIRECT_IDENTIFIER"),
        ("4111 1111 1111 1111", "PAYMENT_OR_ACCOUNT"),
        ("a" * 64, "RAW_CONTENT_SHA"),
    ],
)
def test_sensitive_value_categories_are_exhaustively_redacted_and_rejected(
    value: str, category: str
) -> None:
    findings = scan_sensitive_data({"note": value})
    assert category in {finding.category for finding in findings}

    redacted = sanitize_projection({"note": value})
    assert redacted.redaction_count >= 1
    assert value not in json.dumps(redacted.projection, ensure_ascii=False)
    assert scan_sensitive_data(redacted.projection) == ()

    with pytest.raises(SensitiveDataError, match="^SENSITIVE_DATA_DETECTED$"):
        sanitize_projection({"note": value}, mode="REJECT")
    with pytest.raises(SensitiveDataError, match="^SENSITIVE_DATA_DETECTED$"):
        fingerprint_sanitized_projection({"note": value}, field_domain="payload")


@pytest.mark.parametrize(
    "field",
    [
        "rawSource",
        "sourceText",
        "sourceRevisionId",
        "sourceUrl",
        "apiToken",
        "contentSha256",
        "customerId",
        "orderNumber",
        "accountNumber",
        "personId",
        "firstName",
        "cardNumber",
        "slackChannel",
        "jiraIssue",
        "drawCount",
    ],
)
def test_sensitive_field_names_are_removed_without_preserving_direct_identifiers(
    field: str,
) -> None:
    raw_value = "do-not-preserve-this-value"
    sanitized = sanitize_projection({field: raw_value, "safeMetric": 4})
    serialized = json.dumps(sanitized.projection, ensure_ascii=False, sort_keys=True)
    assert field not in serialized
    assert raw_value not in serialized
    assert scan_sensitive_data(sanitized.projection) == ()


def test_python_producer_matches_exhaustive_schema_safe_property_name_cases() -> None:
    assert len(SAFE_PROPERTY_NAME_CASES["e9KeyMatrix"]) == 31
    for field in SAFE_PROPERTY_NAME_CASES["e9KeyMatrix"]:
        assert scan_sensitive_data({field: 2}), field
        with pytest.raises(SensitiveDataError, match="^SENSITIVE_DATA_DETECTED$"):
            sanitize_projection({field: 2}, mode="REJECT")
    for field in SAFE_PROPERTY_NAME_CASES["representativeSafeKeys"]:
        assert scan_sensitive_data({field: 2}) == (), field
        assert sanitize_projection({field: 2}, mode="REJECT").projection == {field: 2}


def test_reject_mode_candidate_cannot_emit_schema_rejected_credentials_key() -> None:
    with pytest.raises(SensitiveDataError, match="^SENSITIVE_DATA_DETECTED$"):
        build_review_candidate(
            purpose="automatic-generation-quality-review",
            data_class="SYNTHETIC",
            source_is_synthetic=True,
            source_fields={"source-revision": "revision-7"},
            payload={"credentials": "opaque-looking-value"},
            payload_schema_version="quality-projection-v1",
            key=KEY,
            key_id="quality-key-v1",
            privacy_domain="quality-review",
            created_at=CREATED_AT,
            sanitization_mode="REJECT",
        )


def test_raw_content_sha_never_crosses_the_payload_boundary() -> None:
    raw_digest = "a" * 64
    candidate = _candidate(
        payload={
            "contentSha256": raw_digest,
            "safeMetric": 3,
            "nested": {"rawSourceHash": f"sha256:{raw_digest}"},
        }
    )
    serialized = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    projection = candidate["payload"]
    assert isinstance(projection, dict)
    assert "contentSha256" not in serialized
    assert "rawSourceHash" not in serialized
    assert raw_digest not in serialized
    assert "hmac-sha256:" in serialized
    verify_review_candidate(candidate)


def test_candidate_payload_and_artifact_tamper_each_fail_verification() -> None:
    candidate = _candidate()
    recomputed = recompute_review_candidate_fingerprints(candidate)
    assert recomputed.candidate_key == candidate["candidate"]["candidateKey"]  # type: ignore[index]
    assert recomputed.payload_fingerprint == candidate["fingerprints"]["payloadFingerprint"]  # type: ignore[index]
    assert recomputed.artifact_fingerprint == candidate["fingerprints"]["artifactFingerprint"]  # type: ignore[index]

    payload_tamper = deepcopy(candidate)
    payload = payload_tamper["payload"]
    assert isinstance(payload, dict) and isinstance(payload["projection"], dict)
    payload["projection"]["classification"] = "SUPERSEDED_TRAP"
    with pytest.raises(FingerprintMismatchError, match="payloadFingerprint"):
        verify_review_candidate(payload_tamper)

    artifact_tamper = deepcopy(candidate)
    fingerprints = artifact_tamper["fingerprints"]
    assert isinstance(fingerprints, dict)
    fingerprints["artifactFingerprint"] = f"sha256:{'9' * 64}"
    with pytest.raises(FingerprintMismatchError, match="artifactFingerprint"):
        verify_review_candidate(artifact_tamper)

    source_tamper = deepcopy(candidate)
    source = source_tamper["source"]
    assert isinstance(source, dict) and isinstance(source["fieldSeals"], list)
    source["fieldSeals"][0]["seal"] = f"hmac-sha256:{'8' * 64}"
    with pytest.raises(FingerprintMismatchError):
        verify_review_candidate(source_tamper)


def test_synthetic_actual_boundary_and_retention_defaults_are_explicit() -> None:
    synthetic = _candidate()
    assert synthetic["dataClass"] == "SYNTHETIC"
    assert synthetic["source"]["synthetic"] is True  # type: ignore[index]
    assert synthetic["retention"]["maxAgeDays"] == SYNTHETIC_RETENTION_DAYS  # type: ignore[index]
    assert synthetic["certification"]["eligible"] is False  # type: ignore[index]

    actual = _candidate(
        data_class="ACTUAL_MINIMIZED",
        source_is_synthetic=False,
        payload={"summary": "safe minimized projection", "caseCount": 2},
    )
    assert actual["source"]["synthetic"] is False  # type: ignore[index]
    assert actual["retention"]["maxAgeDays"] == ACTUAL_MINIMIZED_RETENTION_DAYS  # type: ignore[index]
    assert actual["review"]["status"] == "UNREVIEWED"  # type: ignore[index]
    assert actual["attestation"]["status"] == "NOT_RUN"  # type: ignore[index]
    assert actual["certification"]["eligible"] is False  # type: ignore[index]

    with pytest.raises(PrivacyBoundaryError, match="^DATA_CLASS_SOURCE_MISMATCH$"):
        _candidate(data_class="SYNTHETIC", source_is_synthetic=False)
    with pytest.raises(PrivacyBoundaryError, match="^DATA_CLASS_SOURCE_MISMATCH$"):
        _candidate(data_class="ACTUAL_MINIMIZED", source_is_synthetic=True)


def test_errors_never_echo_secrets_raw_values_or_key_material() -> None:
    secret = "Bearer do-not-echo-this-secret"
    with pytest.raises(SensitiveDataError) as sensitive:
        fingerprint_sanitized_projection({"note": secret}, field_domain="payload")
    assert str(sensitive.value) == "SENSITIVE_DATA_DETECTED"
    assert secret not in str(sensitive.value)
    assert secret not in repr(sensitive.value)

    with pytest.raises(ReviewCandidateValidationError) as purpose:
        build_review_candidate(
            purpose=secret,
            data_class="SYNTHETIC",
            source_is_synthetic=True,
            source_fields={"source": "raw"},
            payload={"safe": True},
            payload_schema_version="safe-v1",
            key=KEY,
            key_id="key-v1",
            privacy_domain="privacy-v1",
            created_at=CREATED_AT,
        )
    assert str(purpose.value) == "PURPOSE_INVALID"
    assert secret not in str(purpose.value)

    with pytest.raises(SourceSealError) as short_key:
        seal_source_field(
            "raw-value",
            key=b"too-short",
            key_id="key-v1",
            privacy_domain="privacy-v1",
            field_domain="source",
        )
    assert str(short_key.value) == "SOURCE_KEY_INVALID"
    assert "too-short" not in str(short_key.value)


def test_fresh_candidate_cannot_claim_review_attestation_or_certification() -> None:
    candidate = _candidate()
    for field, key, value, code in (
        ("review", "status", "REVIEWED", "FRESH_REVIEW_STATE_INVALID"),
        ("attestation", "status", "PASSED", "FRESH_ATTESTATION_STATE_INVALID"),
        ("certification", "eligible", True, "FRESH_CERTIFICATION_STATE_INVALID"),
    ):
        tampered = deepcopy(candidate)
        section = tampered[field]
        assert isinstance(section, dict)
        section[key] = value
        with pytest.raises(ReviewCandidateValidationError, match=code):
            verify_review_candidate(tampered)
