from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from tc_api.main import create_app
from tc_api.quality_review_runtime import QualityReviewRuntime
from test_generation_quality_snapshot_postgresql import _seed_operational_apply

from tc_backend.db.models import (
    GenerationQualityApproval,
    GenerationQualityCandidate,
    GenerationQualityReviewDecision,
)
from tc_backend.generation.quality_review import (
    GenerationQualityReviewService,
    QualityReviewError,
    ReviewerAuthority,
    hash_reviewer_token,
)
from tc_backend.generation.quality_snapshot import export_quality_candidate_snapshot

pytestmark = pytest.mark.postgres

_SOURCE_KEY = b"quality-review-source-seal-key-0001"
_TOKENS = ("alpha-review-token", "beta-review-token")


def _authority() -> ReviewerAuthority:
    return ReviewerAuthority(
        (
            ("quality-principal-alpha", hash_reviewer_token(_TOKENS[0])),
            ("quality-principal-beta", hash_reviewer_token(_TOKENS[1])),
        )
    )


def _service(session: Session) -> GenerationQualityReviewService:
    return GenerationQualityReviewService(
        session,
        reviewer_authority=_authority(),
        source_seal_key=_SOURCE_KEY,
        source_seal_key_id="quality-review-source-v1",
    )


def _runtime() -> QualityReviewRuntime:
    return QualityReviewRuntime(
        reviewer_authority=_authority(),
        source_seal_key=_SOURCE_KEY,
        source_seal_key_id="quality-review-source-v1",
    )


def _snapshot(engine: Engine, root_id: uuid.UUID, *, suffix: str) -> dict[str, object]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with Session(bind=connection) as session:
                return export_quality_candidate_snapshot(
                    session,
                    root_job_ids=(root_id,),
                    capture_environment="staging",
                    report_outputs={
                        "json": f"artifacts/{suffix}.json",
                        "junit": f"artifacts/{suffix}.xml",
                        "markdown": f"artifacts/{suffix}.md",
                    },
                )
        finally:
            transaction.rollback()


def _import(
    engine: Engine,
    root_id: uuid.UUID,
    snapshot: dict[str, object],
    *,
    key: str,
) -> uuid.UUID:
    capture = snapshot["capture"]
    assert isinstance(capture, dict)
    with Session(engine) as session, session.begin():
        row = _service(session).import_candidate(
            idempotency_key=key,
            root_job_ids=(root_id,),
            capture_environment=str(capture["environment"]),
            report_outputs=snapshot["reportOutputs"],  # type: ignore[arg-type]
        )
        return row.id


def _candidate_fingerprint(engine: Engine, candidate_id: uuid.UUID) -> str:
    with Session(engine) as session:
        row = session.get(GenerationQualityCandidate, candidate_id)
        assert row is not None
        return row.envelope_artifact_fingerprint


def _decide(
    engine: Engine,
    candidate_id: uuid.UUID,
    snapshot_fingerprint: str,
    *,
    token: str,
    key: str,
    decision: str = "APPROVE",
    reason_code: str | None = None,
) -> None:
    with Session(engine) as session, session.begin():
        _service(session).decide(
            candidate_id,
            reviewer_token=token,
            idempotency_key=key,
            decision=decision,  # type: ignore[arg-type]
            expected_artifact_fingerprint=snapshot_fingerprint,
            reason_code=reason_code,  # type: ignore[arg-type]
        )


def test_exact_import_replay_key_conflict_and_no_root_echo_in_safe_detail(
    migrated_engine: Engine,
) -> None:
    root_id = _seed_operational_apply(migrated_engine)
    snapshot = _snapshot(migrated_engine, root_id, suffix="review-replay")
    candidate_id = _import(migrated_engine, root_id, snapshot, key="quality-review-import-replay")
    assert (
        _import(migrated_engine, root_id, snapshot, key="quality-review-import-replay")
        == candidate_id
    )

    changed = _snapshot(migrated_engine, root_id, suffix="review-replay-conflict")
    with pytest.raises(QualityReviewError, match="QUALITY_REVIEW_IMPORT_IDEMPOTENCY_CONFLICT"):
        _import(
            migrated_engine,
            root_id,
            changed,
            key="quality-review-import-replay",
        )

    with Session(migrated_engine) as session:
        service = _service(session)
        detail = service.safe_detail(service.get(candidate_id))
    serialized = str(detail)
    assert str(root_id) not in serialized
    assert "rootJobIds" not in serialized
    assert detail["status"] == "UNREVIEWED"
    assert detail["releaseCertified"] is False


def test_two_distinct_approvals_create_one_row_under_race_and_export_is_deterministic(
    migrated_engine: Engine,
) -> None:
    root_id = _seed_operational_apply(migrated_engine)
    snapshot = _snapshot(migrated_engine, root_id, suffix="review-race")
    candidate_id = _import(migrated_engine, root_id, snapshot, key="quality-review-import-race")
    fingerprint = _candidate_fingerprint(migrated_engine, candidate_id)
    barrier = Barrier(2)

    def approve(index: int) -> None:
        barrier.wait()
        _decide(
            migrated_engine,
            candidate_id,
            fingerprint,
            token=_TOKENS[index],
            key=f"quality-review-race-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve, index) for index in range(2)]
        for future in futures:
            future.result(timeout=30)

    with Session(migrated_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(GenerationQualityReviewDecision)) == 2
        )
        assert session.scalar(select(func.count()).select_from(GenerationQualityApproval)) == 1
        detail = _service(session).safe_detail(_service(session).get(candidate_id))
    assert detail["status"] == "APPROVED"
    assert detail["progress"] == {
        "approveCount": 2,
        "requiredApproveCount": 2,
        "rejected": False,
    }

    with Session(migrated_engine) as session, session.begin():
        first = _service(session).reviewed_snapshot(candidate_id)
    with Session(migrated_engine) as session, session.begin():
        second = _service(session).reviewed_snapshot(candidate_id)
    assert first == second
    assert first["contractVersion"] == "automatic-generation-quality-reviewed-export/2.0.0"
    assert first["baseEnvelope"]["artifactKind"] == "REVIEW_CANDIDATE"
    assert first["attestation"]["status"] == "NOT_RUN"
    assert first["certification"]["eligible"] is False


def test_one_approval_is_one_of_two_same_principal_is_blocked_and_rejection_is_terminal(
    migrated_engine: Engine,
) -> None:
    root_id = _seed_operational_apply(migrated_engine)
    approved_snapshot = _snapshot(migrated_engine, root_id, suffix="review-one")
    approved_id = _import(
        migrated_engine,
        root_id,
        approved_snapshot,
        key="quality-review-import-one",
    )
    fingerprint = _candidate_fingerprint(migrated_engine, approved_id)
    _decide(
        migrated_engine,
        approved_id,
        fingerprint,
        token=_TOKENS[0],
        key="quality-review-one-approve",
    )
    _decide(
        migrated_engine,
        approved_id,
        fingerprint,
        token=_TOKENS[0],
        key="quality-review-one-approve",
    )
    with pytest.raises(QualityReviewError, match="QUALITY_REVIEW_DECISION_IDEMPOTENCY_CONFLICT"):
        _decide(
            migrated_engine,
            approved_id,
            fingerprint,
            token=_TOKENS[0],
            key="quality-review-one-approve",
            decision="REJECT",
            reason_code="OTHER_POLICY_FAILURE",
        )
    with Session(migrated_engine) as session:
        detail = _service(session).safe_detail(_service(session).get(approved_id))
        assert detail["status"] == "PENDING_APPROVAL"
        assert detail["progress"]["approveCount"] == 1
        assert session.scalar(select(func.count()).select_from(GenerationQualityApproval)) == 0
    with pytest.raises(QualityReviewError, match="QUALITY_REVIEW_PRINCIPAL_ALREADY_DECIDED"):
        _decide(
            migrated_engine,
            approved_id,
            fingerprint,
            token=_TOKENS[0],
            key="quality-review-same-principal",
        )
    for statement in (
        "UPDATE generation_quality_review_decisions SET reason_code = 'OTHER_POLICY_FAILURE'",
        "DELETE FROM generation_quality_review_decisions",
    ):
        with pytest.raises(DBAPIError):
            with migrated_engine.begin() as connection:
                connection.execute(text(statement))

    rejected_snapshot = _snapshot(migrated_engine, root_id, suffix="review-reject")
    rejected_id = _import(
        migrated_engine,
        root_id,
        rejected_snapshot,
        key="quality-review-import-reject",
    )
    rejected_fingerprint = _candidate_fingerprint(migrated_engine, rejected_id)
    _decide(
        migrated_engine,
        rejected_id,
        rejected_fingerprint,
        token=_TOKENS[0],
        key="quality-review-reject",
        decision="REJECT",
        reason_code="QUALITY_EVIDENCE_INSUFFICIENT",
    )
    with pytest.raises(QualityReviewError, match="QUALITY_REVIEW_TERMINAL"):
        _decide(
            migrated_engine,
            rejected_id,
            rejected_fingerprint,
            token=_TOKENS[1],
            key="quality-review-after-reject",
        )


def test_tamper_fence_source_drift_and_append_only_raw_sql_guards(
    migrated_engine: Engine,
) -> None:
    root_id = _seed_operational_apply(migrated_engine)
    snapshot = _snapshot(migrated_engine, root_id, suffix="review-drift")
    candidate_id = _import(migrated_engine, root_id, snapshot, key="quality-review-import-drift")
    fingerprint = _candidate_fingerprint(migrated_engine, candidate_id)

    with pytest.raises(QualityReviewError, match="QUALITY_REVIEW_STALE_SNAPSHOT"):
        _decide(
            migrated_engine,
            candidate_id,
            f"sha256:{'f' * 64}",
            token=_TOKENS[0],
            key="quality-review-tampered-fingerprint",
        )

    with migrated_engine.begin() as connection:
        connection.execute(text("UPDATE knowledge_source_records SET content_hash = 'drifted'"))
    with pytest.raises(QualityReviewError, match="QUALITY_REVIEW_SOURCE_DRIFT"):
        _decide(
            migrated_engine,
            candidate_id,
            fingerprint,
            token=_TOKENS[0],
            key="quality-review-source-drift",
        )

    for statement in (
        "UPDATE generation_quality_candidates SET privacy_scan_status = 'FAILED'",
        "UPDATE generation_quality_candidates SET review_envelope = "
        "review_envelope || '{\"unexpected\": true}'::jsonb",
        "DELETE FROM generation_quality_candidate_roots",
    ):
        with pytest.raises(DBAPIError):
            with migrated_engine.begin() as connection:
                connection.execute(text(statement))

    with Session(migrated_engine) as session:
        candidate = session.get(GenerationQualityCandidate, candidate_id)
        assert candidate is not None
        assert candidate.privacy_scan_status == "CLEAN"


def test_database_role_rejects_direct_dml_unknown_principals_and_forged_integrity(
    migrated_engine: Engine,
) -> None:
    root_id = _seed_operational_apply(migrated_engine)
    snapshot = _snapshot(migrated_engine, root_id, suffix="review-db-authority")
    candidate_id = _import(
        migrated_engine,
        root_id,
        snapshot,
        key="quality-review-import-db-authority",
    )
    fingerprint = _candidate_fingerprint(migrated_engine, candidate_id)

    with pytest.raises(DBAPIError):
        with migrated_engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE tc_quality_review_writer"))
            connection.execute(
                text(
                    "INSERT INTO generation_quality_review_decisions ("
                    "workspace_id, candidate_id, principal_id, decision, reason_code, "
                    "idempotency_key, request_digest, observed_base_snapshot_fingerprint, "
                    "decision_digest) SELECT workspace_id, id, 'forged-principal', "
                    "'APPROVE', NULL, 'quality-review-direct-forge', :digest, "
                    "envelope_artifact_fingerprint, :digest "
                    "FROM generation_quality_candidates WHERE id = :candidate_id"
                ),
                {"candidate_id": candidate_id, "digest": f"sha256:{'a' * 64}"},
            )
    for direct_insert in (
        "INSERT INTO generation_quality_approvals DEFAULT VALUES",
        "INSERT INTO generation_quality_reviewer_authorities "
        "(principal_id, credential_hash, authority_digest) VALUES "
        f"('regex-valid-forge', 'sha256:{'4' * 64}', 'sha256:{'5' * 64}')",
    ):
        with pytest.raises(DBAPIError):
            with migrated_engine.begin() as connection:
                connection.execute(text("SET LOCAL ROLE tc_quality_review_writer"))
                connection.execute(text(direct_insert))

    function_sql = text(
        "SELECT tc_record_generation_quality_decision("
        "workspace_id, id, :principal, :credential_hash, 'APPROVE', NULL, :idempotency, "
        "envelope_artifact_fingerprint, :request_digest, :decision_digest) "
        "FROM generation_quality_candidates WHERE id = :candidate_id"
    )
    with pytest.raises(DBAPIError, match="reviewer authority is not installed"):
        with migrated_engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE tc_quality_review_writer"))
            connection.execute(
                function_sql,
                {
                    "candidate_id": candidate_id,
                    "principal": "forged-principal",
                    "credential_hash": f"sha256:{'b' * 64}",
                    "idempotency": "quality-review-function-forge",
                    "request_digest": f"sha256:{'c' * 64}",
                    "decision_digest": f"sha256:{'d' * 64}",
                },
            )
    with pytest.raises(DBAPIError, match="quality review decision integrity mismatch"):
        with migrated_engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE tc_quality_review_writer"))
            connection.execute(
                function_sql,
                {
                    "candidate_id": candidate_id,
                    "principal": "quality-principal-alpha",
                    "credential_hash": hash_reviewer_token(_TOKENS[0]),
                    "idempotency": "quality-review-function-tamper",
                    "request_digest": f"sha256:{'c' * 64}",
                    "decision_digest": f"sha256:{'d' * 64}",
                },
            )

    for statement in (
        "UPDATE generation_quality_reviewer_authorities SET principal_id = principal_id",
        "DELETE FROM generation_quality_reviewer_authorities",
    ):
        with pytest.raises(DBAPIError):
            with migrated_engine.begin() as connection:
                connection.execute(text(statement))

    _decide(
        migrated_engine,
        candidate_id,
        fingerprint,
        token=_TOKENS[0],
        key="quality-review-db-boundary-valid",
    )


def test_quality_review_http_slice_never_echoes_roots_tokens_or_release_certification(
    migrated_engine: Engine,
) -> None:
    root_id = _seed_operational_apply(migrated_engine)
    snapshot = _snapshot(migrated_engine, root_id, suffix="review-http")
    capture = snapshot["capture"]
    assert isinstance(capture, dict)
    app = create_app(
        validate_database=False,
        database_engine=migrated_engine,
        quality_review_runtime=_runtime(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        imported = client.post(
            "/api/generation-quality/review-candidates",
            headers={"Idempotency-Key": "quality-review-http-import"},
            json={
                "idempotencyKey": "quality-review-http-import",
                "rootJobIds": [str(root_id)],
                "captureEnvironment": capture["environment"],
                "reportOutputs": snapshot["reportOutputs"],
            },
        )
        assert imported.status_code == 201, imported.text
        candidate_id = imported.json()["candidateId"]
        serialized = imported.text
        assert str(root_id) not in serialized
        assert "rootJobIds" not in serialized
        for forbidden in (
            "baseArtifactDigest",
            "datasetFingerprint",
            "sourceSnapshotFingerprint",
            "snapshotFingerprint",
            "generationRootId",
            "reviewerAlias",
            "decisionId",
            "providerEndpoint",
            "sourceText",
            "excerpt",
        ):
            assert forbidden not in serialized

        artifact_fingerprint = imported.json()["fingerprints"]["artifactFingerprint"]

        for index, token in enumerate(_TOKENS):
            response = client.post(
                f"/api/generation-quality/review-candidates/{candidate_id}/decisions",
                headers={
                    "Idempotency-Key": f"quality-review-http-approve-{index}",
                    "X-Quality-Reviewer-Token": token,
                    "X-Actor-Label": "spoofed-reviewer-label",
                },
                json={
                    "idempotencyKey": f"quality-review-http-approve-{index}",
                    "decision": "APPROVE",
                    "expectedArtifactFingerprint": artifact_fingerprint,
                    "reasonCode": None,
                },
            )
            assert response.status_code == 200, response.text
            assert token not in response.text
            assert "spoofed-reviewer-label" not in response.text

        detail = client.get(f"/api/generation-quality/review-candidates/{candidate_id}")
        distribution = imported.json()["classificationDistribution"]
        populated_classification = next(name for name, count in distribution.items() if count > 0)
        listed = client.get(
            "/api/generation-quality/review-candidates",
            params={
                "status": "APPROVED",
                "classification": populated_classification,
                "materialization": "COMPLETE",
                "search": candidate_id,
            },
        )
        reviewed = client.get(
            f"/api/generation-quality/review-candidates/{candidate_id}/reviewed-snapshot"
        )

    assert detail.status_code == 200
    assert detail.json()["status"] == "APPROVED"
    assert listed.status_code == 200, listed.text
    assert [item["candidateId"] for item in listed.json()["items"]] == [candidate_id]
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["contractVersion"] == (
        "automatic-generation-quality-reviewed-export/2.0.0"
    )
    assert reviewed.json()["attestation"]["status"] == "NOT_RUN"
    assert reviewed.json()["certification"]["status"] == (
        "REVIEWED_NOT_RELEASE_CERTIFIED"
    )
    assert reviewed.json()["certification"]["releaseCertified"] is False
    assert "snapshot" not in reviewed.json()
    public_responses = "\n".join(
        (imported.text, detail.text, listed.text, reviewed.text)
    )
    assert str(root_id) not in public_responses
    private_report_outputs = snapshot["reportOutputs"]
    assert isinstance(private_report_outputs, dict)
    for private_report_path in private_report_outputs.values():
        assert private_report_path not in public_responses
    for forbidden in (
        "baseArtifactDigest",
        "datasetFingerprint",
        "sourceSnapshotFingerprint",
        "snapshotFingerprint",
        "generationRootId",
        "sourceSnapshotId",
        "caseSnapshotId",
        "reviewerAlias",
        "decisionId",
        "approvalId",
        "providerEndpoint",
        "permalink",
        "sourceText",
        "rawSourceText",
        "excerpt",
    ):
        assert forbidden not in public_responses
