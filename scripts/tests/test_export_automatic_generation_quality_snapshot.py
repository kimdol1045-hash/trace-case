from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import export_automatic_generation_quality_snapshot as cli
from tc_backend.generation.quality_gate import (
    canonical_digest,
    compute_fingerprints,
    validate_input,
)
from tc_backend.generation.quality_snapshot import (
    QualitySnapshotExportError,
    _semantic_clusters,
    _validate_apply_receipt,
    export_quality_candidate_snapshot,
    build_quality_review_envelope,
    render_candidate_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "evals/fixtures/automatic-generation-quality/synthetic-input-v1.json"
ROOT_ID = "00000000-0000-7000-8000-000000000777"


def _snapshot() -> dict[str, Any]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    value["dataset"]["kind"] = "OPERATIONAL_GOLDEN"
    value["dataset"]["synthetic"] = False
    value["capture"]["environment"] = "staging"
    value["reportOutputs"] = {
        "json": "artifacts/automatic-generation-quality.json",
        "junit": "artifacts/automatic-generation-quality.junit.xml",
        "markdown": "artifacts/automatic-generation-quality.md",
    }
    fingerprints = compute_fingerprints(value)
    value["dataset"]["datasetFingerprint"] = fingerprints["datasetFingerprint"]
    value["capture"]["sourceSnapshotFingerprint"] = fingerprints[
        "sourceSnapshotFingerprint"
    ]
    value["dataset"]["snapshotFingerprint"] = fingerprints["snapshotFingerprint"]
    validate_input(value)
    return value


class _Transaction:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def rollback(self) -> None:
        self.calls.append("ROLLBACK")


class _Connection:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def execution_options(self, **options: str) -> _Connection:
        self.calls.append(f"ISOLATION={options['isolation_level']}")
        return self

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def begin(self) -> _Transaction:
        self.calls.append("BEGIN")
        return _Transaction(self.calls)

    def execute(self, statement: object) -> None:
        self.calls.append(str(statement))


class _Engine:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def connect(self) -> _Connection:
        return _Connection(self.calls)

    def dispose(self) -> None:
        self.calls.append("DISPOSE")


class _Session:
    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_cli_uses_read_only_repeatable_read_and_writes_deterministic_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    snapshot = _snapshot()
    monkeypatch.setenv("TC_DATABASE_URL", "postgresql+psycopg://tc:secret@localhost/test")
    monkeypatch.setenv(
        "TC_QUALITY_REVIEW_SOURCE_SEAL_KEY", "quality-review-source-seal-key-0001"
    )
    monkeypatch.setattr(cli, "create_engine", lambda *_args, **_kwargs: _Engine(calls))
    monkeypatch.setattr(cli, "Session", lambda **_kwargs: _Session())
    monkeypatch.setattr(
        cli,
        "export_quality_review_candidate",
        lambda *_args, **_kwargs: (
            copy.deepcopy(snapshot),
            build_quality_review_envelope(
                copy.deepcopy(snapshot),
                root_job_ids=(cli._root_uuid(ROOT_ID),),
                key=b"quality-review-source-seal-key-0001",
                key_id="quality-review-source-v1",
            ),
        ),
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    arguments = [
        "--root-job-id",
        ROOT_ID,
        "--capture-environment",
        "staging",
        "--snapshot-output",
    ]

    assert cli.main([*arguments, str(first)]) == 0
    assert cli.main([*arguments, str(second)]) == 0

    assert first.read_bytes() == second.read_bytes()
    exported = json.loads(first.read_text(encoding="utf-8"))
    assert exported["artifactKind"] == "REVIEW_CANDIDATE"
    assert exported["attestation"]["status"] == "NOT_RUN"
    serialized = first.read_text(encoding="utf-8")
    assert snapshot["dataset"]["snapshotFingerprint"] not in serialized
    assert ROOT_ID not in serialized
    assert calls.count("ISOLATION=REPEATABLE READ") == 2
    assert calls.count("SET TRANSACTION READ ONLY") == 2
    assert calls.count("ROLLBACK") == 2
    assert calls.count("DISPOSE") == 2


def test_cli_missing_database_environment_fails_without_echoing_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TC_DATABASE_URL", raising=False)
    output = tmp_path / "candidate.json"

    assert (
        cli.main(
            [
                "--root-job-id",
                ROOT_ID,
                "--capture-environment",
                "staging",
                "--snapshot-output",
                str(output),
            ]
        )
        == 2
    )

    assert "DATABASE_URL_ENV_MISSING" in capsys.readouterr().err
    assert not output.exists()


def test_export_rejects_endpoint_bearing_report_path_before_query() -> None:
    for unsafe_path in (
        "https://private.invalid/report.json",
        "artifacts/10.20.30.40/report.json",
        "artifacts/010-1234-5678/report.json",
        "artifacts/authorization/report.json",
    ):
        with pytest.raises(
            QualitySnapshotExportError, match="REPORT_OUTPUT_PATH_INVALID:json"
        ):
            export_quality_candidate_snapshot(
                object(),  # type: ignore[arg-type]
                root_job_ids=(cli._root_uuid(ROOT_ID),),
                capture_environment="staging",
                report_outputs={
                    "json": unsafe_path,
                    "junit": "artifacts/report.xml",
                    "markdown": "artifacts/report.md",
                },
            )


def test_render_fails_closed_if_any_endpoint_or_secret_is_injected() -> None:
    for injected in (
        "https://private.invalid/model",
        "Bearer leaked-token-value",
        f"Bearer {' ' * 80}leaked-token-value",
        "qa.owner@example.com",
    ):
        value = _snapshot()
        value["versionPins"]["generationPolicyVersion"] = injected
        fingerprints = compute_fingerprints(value)
        value["dataset"]["datasetFingerprint"] = fingerprints["datasetFingerprint"]
        value["capture"]["sourceSnapshotFingerprint"] = fingerprints[
            "sourceSnapshotFingerprint"
        ]
        value["dataset"]["snapshotFingerprint"] = fingerprints["snapshotFingerprint"]
        with pytest.raises(QualitySnapshotExportError):
            render_candidate_snapshot(value)


def test_semantic_components_are_global_and_order_independent_across_roots() -> None:
    members = {
        "generated:root_b_case": ((0.99, 0.01), canonical_digest([0.99, 0.01])),
        "generated:root_a_case": ((1.0, 0.0), canonical_digest([1.0, 0.0])),
        "existing:sealed_case": ((0.0, 1.0), canonical_digest([0.0, 1.0])),
    }

    first = _semantic_clusters(
        members,
        policy_digest=canonical_digest({"policy": "sealed"}),
        threshold=0.98,
    )
    replay = _semantic_clusters(
        dict(reversed(tuple(members.items()))),
        policy_digest=canonical_digest({"policy": "sealed"}),
        threshold=0.98,
    )

    assert first == replay
    assert first["generated:root_a_case"] == first["generated:root_b_case"]
    assert first["generated:root_a_case"] != first["existing:sealed_case"]


def test_apply_receipt_validation_fails_closed_on_authority_mismatch() -> None:
    job_id = uuid.uuid4()
    apply_id = uuid.uuid4()
    audit_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    applied_at = datetime.now(UTC)
    snapshot = {"generatedCaseIndex": 0, "outcome": "CREATED"}
    outcome = SimpleNamespace(
        outcome="CREATED",
        generation_apply_id=apply_id,
        snapshot=snapshot,
    )
    receipt = {
        "id": f"generation_apply_{apply_id.hex}",
        "generationJobId": f"generation_job_{job_id.hex}",
        "jobStatus": "SUCCEEDED",
        "contextDigest": "1" * 64,
        "resultDigest": "2" * 64,
        "placementDigest": "3" * 64,
        "transactionStatus": "COMMITTED",
        "parent": {
            "outcome": "REUSED",
            "workItemId": f"work_item_{parent_id.hex}",
            "categoryId": f"folder_{uuid.uuid4().hex}",
        },
        "cases": [snapshot],
        "counts": {"generated": 1, "created": 1, "reused": 0},
        "auditId": f"audit_{audit_id.hex}",
        "appliedAt": applied_at.isoformat(),
    }
    apply = SimpleNamespace(
        id=apply_id,
        generation_job_id=job_id,
        parent_work_item_id=parent_id,
        audit_id=audit_id,
        result_digest="2" * 64,
        placement_digest="3" * 64,
        receipt=receipt,
        applied_at=applied_at,
    )
    job = SimpleNamespace(id=job_id, context_digest="1" * 64)

    _validate_apply_receipt(apply, job, (outcome,))  # type: ignore[arg-type]
    apply.receipt = copy.deepcopy(receipt)
    apply.receipt["placementDigest"] = "4" * 64
    with pytest.raises(QualitySnapshotExportError, match="APPLY_RECEIPT_SEAL_MISMATCH"):
        _validate_apply_receipt(apply, job, (outcome,))  # type: ignore[arg-type]
