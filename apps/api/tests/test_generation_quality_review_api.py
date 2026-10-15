from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from tc_api.main import create_app
from tc_api.quality_review_runtime import QualityReviewRuntime
from tc_backend.generation.quality_review import ReviewerAuthority, hash_reviewer_token


def _runtime() -> QualityReviewRuntime:
    return QualityReviewRuntime(
        reviewer_authority=ReviewerAuthority(
            (
                ("quality-principal-alpha", hash_reviewer_token("alpha-review-token")),
                ("quality-principal-beta", hash_reviewer_token("beta-review-token")),
            )
        ),
        source_seal_key=b"quality-review-source-seal-key-0001",
        source_seal_key_id="quality-review-source-v1",
    )


def _client() -> TestClient:
    return TestClient(
        create_app(
            validate_database=False,
            database_engine=create_engine("sqlite+pysqlite:///:memory:"),
            quality_review_runtime=_runtime(),
        ),
        raise_server_exceptions=False,
    )


def _decision() -> dict[str, object]:
    return {
        "idempotencyKey": "quality-review-decision-001",
        "decision": "APPROVE",
        "expectedArtifactFingerprint": f"sha256:{'a' * 64}",
        "reasonCode": None,
    }


def test_missing_and_invalid_reviewer_tokens_are_401_and_actor_labels_are_ignored() -> (
    None
):
    candidate = f"review-candidate:{'1' * 64}"
    with _client() as client:
        missing = client.post(
            f"/api/generation-quality/review-candidates/{candidate}/decisions",
            headers={"Idempotency-Key": "quality-review-decision-001"},
            json=_decision(),
        )
        spoofed = client.post(
            f"/api/generation-quality/review-candidates/{candidate}/decisions",
            headers={
                "Idempotency-Key": "quality-review-decision-001",
                "X-Quality-Reviewer-Token": "invalid-token",
                "X-Actor-Label": "quality-principal-alpha",
            },
            json=_decision(),
        )

    assert missing.status_code == 401
    assert spoofed.status_code == 401
    assert missing.json()["code"] == "QUALITY_REVIEWER_UNAUTHORIZED"
    assert spoofed.json()["code"] == "QUALITY_REVIEWER_UNAUTHORIZED"
    assert "invalid-token" not in spoofed.text


def test_decision_body_rejects_forged_principal_and_raw_fields() -> None:
    candidate = f"review-candidate:{'1' * 64}"
    for forged in ("principalId", "actorLabel", "rootJobIds", "rawSourceText"):
        payload = _decision() | {forged: "forged"}
        with _client() as client:
            response = client.post(
                f"/api/generation-quality/review-candidates/{candidate}/decisions",
                headers={
                    "Idempotency-Key": "quality-review-decision-001",
                    "X-Quality-Reviewer-Token": "alpha-review-token",
                },
                json=payload,
            )
        assert response.status_code == 422, forged
        assert response.json()["code"] == "VALIDATION_FAILED"


def test_import_rejects_legacy_snapshot_hash_and_alias_fields_before_database_use() -> None:
    payload = {
        "idempotencyKey": "quality-review-legacy-import",
        "rootJobIds": ["00000000-0000-7000-8000-000000000001"],
        "captureEnvironment": "staging",
        "reportOutputs": {
            "json": "artifacts/report.json",
            "junit": "artifacts/report.xml",
            "markdown": "artifacts/report.md",
        },
        "snapshot": {"rawSourceText": "must never be accepted"},
        "snapshotFingerprint": f"sha256:{'1' * 64}",
        "candidateAlias": "legacy-alias",
    }
    with _client() as client:
        response = client.post(
            "/api/generation-quality/review-candidates",
            headers={"Idempotency-Key": "quality-review-legacy-import"},
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"


def test_openapi_exposes_dedicated_token_header_and_safe_response_models() -> None:
    document = create_app(validate_database=False).openapi()
    decision = document["paths"][
        "/api/generation-quality/review-candidates/{candidateId}/decisions"
    ]["post"]
    headers = {
        parameter["name"]: parameter
        for parameter in decision["parameters"]
        if parameter["in"] == "header"
    }
    candidate_schema = document["components"]["schemas"][
        "GenerationQualityCandidateResource"
    ]
    envelope_schema = document["components"]["schemas"][
        "GenerationQualityBaseEnvelopeResource"
    ]

    assert "X-Quality-Reviewer-Token" in headers
    assert headers["X-Quality-Reviewer-Token"]["required"] is False
    assert {"401", "409", "422", "503"}.issubset(decision["responses"])
    serialized = str(candidate_schema)
    for forbidden in (
        "rootJobIds",
        "sourceIds",
        "principalId",
        "reviewerToken",
        "rawSourceText",
        "permalink",
    ):
        assert forbidden not in serialized
    assert envelope_schema["additionalProperties"] is False
    assert set(envelope_schema["required"]) == {
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
