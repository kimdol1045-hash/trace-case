from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from tc_backend.generation.quality_gate import (
    QualityGateInputError,
    compute_fingerprints,
    evaluate_quality_snapshot,
    validate_input,
)

from scripts.evaluate_automatic_generation_quality import main
from scripts.verify_automatic_generation_quality_artifacts import main as verify_main

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT / "evals/fixtures/automatic-generation-quality/synthetic-input-v1.json"
)


def _reseal(value: dict[str, object]) -> None:
    fingerprints = compute_fingerprints(value)
    dataset = value["dataset"]
    capture = value["capture"]
    assert isinstance(dataset, dict) and isinstance(capture, dict)
    dataset["datasetFingerprint"] = fingerprints["datasetFingerprint"]
    capture["sourceSnapshotFingerprint"] = fingerprints["sourceSnapshotFingerprint"]
    dataset["snapshotFingerprint"] = fingerprints["snapshotFingerprint"]


def _snapshot(tmp_path: Path, *, profile: str = "CANDIDATE") -> tuple[Path, dict[str, Path]]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    value["profile"] = profile
    outputs = {
        "json": tmp_path / "report.json",
        "junit": tmp_path / "report.junit.xml",
        "markdown": tmp_path / "report.md",
    }
    value["reportOutputs"] = {name: str(path) for name, path in outputs.items()}
    _reseal(value)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, outputs


def _arguments(snapshot: Path, outputs: dict[str, Path], *, profile: str) -> list[str]:
    return [
        "--snapshot",
        str(snapshot),
        "--profile",
        profile,
        "--json-output",
        str(outputs["json"]),
        "--junit-output",
        str(outputs["junit"]),
        "--markdown-output",
        str(outputs["markdown"]),
        "--expected-snapshot-sha256",
        hashlib.sha256(snapshot.read_bytes()).hexdigest(),
    ]


def test_synthetic_candidate_writes_three_valid_non_certifying_reports(
    tmp_path: Path,
) -> None:
    snapshot, outputs = _snapshot(tmp_path)

    assert main(_arguments(snapshot, outputs, profile="candidate")) == 0
    report = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert report["gate"]["passed"] is True
    assert report["metrics"]["evidenceCoverage"] == {
        "denominator": 10,
        "numerator": 10,
        "rate": 1.0,
    }
    assert report["certification"]["releaseCertified"] is False
    assert report["certification"]["status"] == "NOT_CERTIFIED_SYNTHETIC"
    assert 'skipped="0"' in outputs["junit"].read_text(encoding="utf-8")
    assert main(["--validate-report", str(outputs["json"])]) == 0


def test_metric_failure_is_exit_one_but_writes_deterministic_failure_reports(
    tmp_path: Path,
) -> None:
    snapshot, outputs = _snapshot(tmp_path)
    value = json.loads(snapshot.read_text(encoding="utf-8"))
    cases = value["generationRoots"][0]["selectedJob"]["result"]["cases"]
    cases[1]["exactContentKey"] = cases[0]["exactContentKey"]
    fingerprints = compute_fingerprints(value)
    value["dataset"]["datasetFingerprint"] = fingerprints["datasetFingerprint"]
    value["dataset"]["snapshotFingerprint"] = fingerprints["snapshotFingerprint"]
    value["capture"]["sourceSnapshotFingerprint"] = fingerprints[
        "sourceSnapshotFingerprint"
    ]
    snapshot.write_text(json.dumps(value), encoding="utf-8")

    assert main(_arguments(snapshot, outputs, profile="candidate")) == 1
    report = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert report["metrics"]["duplicates"]["exactKey"] == 1
    assert report["gate"]["passed"] is False


def test_empty_nan_missing_tampered_and_cached_inputs_fail_closed_with_exit_two(
    tmp_path: Path,
) -> None:
    snapshot, outputs = _snapshot(tmp_path)
    assert main(_arguments(snapshot, outputs, profile="candidate")) == 0
    assert main(_arguments(snapshot, outputs, profile="candidate")) == 2

    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    fresh_outputs = {
        "json": tmp_path / "empty-report.json",
        "junit": tmp_path / "empty-report.xml",
        "markdown": tmp_path / "empty-report.md",
    }
    assert main(_arguments(empty, fresh_outputs, profile="candidate")) == 2

    missing_value = json.loads(snapshot.read_text(encoding="utf-8"))
    del missing_value["versionPins"]["modelRevision"]
    missing = tmp_path / "missing.json"
    missing_value["reportOutputs"] = {
        name: str(tmp_path / f"missing-{name}.{ 'xml' if name == 'junit' else name }")
        for name in fresh_outputs
    }
    missing.write_text(json.dumps(missing_value), encoding="utf-8")
    missing_outputs = {
        name: Path(path) for name, path in missing_value["reportOutputs"].items()
    }
    assert main(_arguments(missing, missing_outputs, profile="candidate")) == 2

    tampered_value = copy.deepcopy(json.loads(snapshot.read_text(encoding="utf-8")))
    tampered_value["dataset"]["snapshotFingerprint"] = f"sha256:{'f' * 64}"
    tampered_value["reportOutputs"] = {
        "json": str(tmp_path / "tampered.json.out.json"),
        "junit": str(tmp_path / "tampered.junit.xml"),
        "markdown": str(tmp_path / "tampered.md"),
    }
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(tampered_value), encoding="utf-8")
    tampered_outputs = {
        name: Path(path) for name, path in tampered_value["reportOutputs"].items()
    }
    assert main(_arguments(tampered, tampered_outputs, profile="candidate")) == 2

    nan_value = snapshot.read_text(encoding="utf-8").replace(
        '"semanticDuplicateThreshold": 0.85',
        '"semanticDuplicateThreshold": NaN',
    )
    nan = tmp_path / "nan.json"
    nan.write_text(nan_value, encoding="utf-8")
    assert main(_arguments(nan, fresh_outputs, profile="candidate")) == 2


def test_release_profile_refuses_synthetic_certification_and_artifact_verifier_hashes(
    tmp_path: Path,
) -> None:
    snapshot, outputs = _snapshot(tmp_path, profile="RELEASE")
    assert main(_arguments(snapshot, outputs, profile="release")) == 1
    report = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert report["certification"]["releaseCertified"] is False
    assert report["certification"]["status"] == "NOT_CERTIFIED_SYNTHETIC"

    manifest = tmp_path / "manifest.json"
    assert (
        verify_main(
            [
                "--json-report",
                str(outputs["json"]),
                "--junit-report",
                str(outputs["junit"]),
                "--markdown-report",
                str(outputs["markdown"]),
                "--manifest-output",
                str(manifest),
                "--snapshot",
                str(snapshot),
                "--expected-snapshot-sha256",
                hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            ]
        )
        == 0
    )
    manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_value["scans"] == {"pii": "CLEAN", "secrets": "CLEAN"}
    assert len(manifest_value["artifacts"]["json"]["sha256"]) == 64
    assert manifest_value["artifacts"]["snapshot"]["sha256"] == hashlib.sha256(
        snapshot.read_bytes()
    ).hexdigest()


def test_release_cli_without_runner_owned_snapshot_hash_is_exit_two(tmp_path: Path) -> None:
    snapshot, outputs = _snapshot(tmp_path, profile="RELEASE")
    arguments = _arguments(snapshot, outputs, profile="release")[:-2]
    assert main(arguments) == 2
    assert not any(path.exists() for path in outputs.values())


def test_provenance_mutations_fail_for_duplicate_outcome_and_source_digest_drift() -> None:
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mutations = []

    duplicate_outcome = copy.deepcopy(base)
    cases = duplicate_outcome["generationRoots"][0]["selectedJob"]["result"]["cases"]
    cases[1]["materialization"]["applyOutcomeId"] = cases[0]["materialization"][
        "applyOutcomeId"
    ]
    mutations.append(duplicate_outcome)

    for target, name, value in (
        ("selectedJob", "workspaceId", "workspace_cross_context"),
        ("selectedJob", "sourceVersion", 99),
        ("selectedJob", "inputDigest", f"sha256:{'9' * 64}"),
        ("result", "workspaceId", "workspace_cross_result"),
        ("materialization", "sourceWorkspaceId", "workspace_cross_materialization"),
        ("materialization", "contextDigest", f"sha256:{'8' * 64}"),
        ("materialization", "resultDigest", f"sha256:{'7' * 64}"),
    ):
        mutated = copy.deepcopy(base)
        job = mutated["generationRoots"][0]["selectedJob"]
        if target == "selectedJob":
            job[name] = value
        elif target == "result":
            job["result"][name] = value
        else:
            job["result"]["cases"][0]["materialization"][name] = value
        mutations.append(mutated)

    for mutated in mutations:
        _reseal(mutated)
        outcome = evaluate_quality_snapshot(mutated)
        assert outcome.passed is False
        assert outcome.report["metrics"]["provenanceFailureCount"] > 0


def test_attempt_history_rejects_filtered_and_unattested_rejected_attempts() -> None:
    filtered = json.loads(FIXTURE.read_text(encoding="utf-8"))
    root = filtered["generationRoots"][0]
    selected = root["selectedJob"]
    selected["attemptNumber"] = 2
    selected["retryOfJobId"] = "generation_job_filtered_001"
    root["attemptHistory"][0].update(
        {
            "attemptNumber": 2,
            "retryOfJobId": "generation_job_filtered_001",
        }
    )
    _reseal(filtered)
    filtered_outcome = evaluate_quality_snapshot(filtered)
    assert "ATTEMPT_HISTORY_COUNT_MISMATCH" in filtered_outcome.roots[0].provenance_failures

    rejected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    root = rejected["generationRoots"][0]
    selected = root["selectedJob"]
    selected["attemptNumber"] = 2
    selected["retryOfJobId"] = "generation_job_rejected_001"
    selected_attempt = copy.deepcopy(root["attemptHistory"][0])
    selected_attempt.update(
        {
            "attemptNumber": 2,
            "retryOfJobId": "generation_job_rejected_001",
        }
    )
    rejected_attempt = copy.deepcopy(selected_attempt)
    rejected_attempt.update(
        {
            "generationJobId": "generation_job_rejected_001",
            "attemptNumber": 1,
            "retryOfJobId": None,
            "status": "REJECTED",
            "resultDigest": None,
            "rejectionCode": None,
        }
    )
    root["attemptHistory"] = [rejected_attempt, selected_attempt]
    _reseal(rejected)
    rejected_outcome = evaluate_quality_snapshot(rejected)
    assert "REJECTED_ATTEMPT_CODE_MISSING" in rejected_outcome.roots[0].provenance_failures


def test_fingerprint_binds_review_capture_live_gate_and_rejects_unknown_lifecycle() -> None:
    for mutate in (
        lambda value: value["dataset"]["review"].__setitem__("approvalId", "forged_approval"),
        lambda value: value["capture"].__setitem__("releaseCandidateAt", "2026-09-28T00:00:00Z"),
        lambda value: value["liveGate"].__setitem__("approvedBy", "forged_reviewer"),
    ):
        value = json.loads(FIXTURE.read_text(encoding="utf-8"))
        mutate(value)
        with pytest.raises(QualityGateInputError, match="FINGERPRINT_MISMATCH"):
            validate_input(value)

    unknown = json.loads(FIXTURE.read_text(encoding="utf-8"))
    unknown["generationRoots"][0]["contextSeal"]["entries"][0][
        "lifecycleStatusAtSeal"
    ] = "UNKNOWN"
    with pytest.raises(QualityGateInputError, match="SCHEMA_VALIDATION_FAILED"):
        validate_input(unknown)


def test_artifact_verifier_refuses_snapshot_replay_markdown_tamper_and_pii(
    tmp_path: Path,
) -> None:
    snapshot, outputs = _snapshot(tmp_path)
    snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert main(_arguments(snapshot, outputs, profile="candidate")) == 0

    replay_value = json.loads(snapshot.read_text(encoding="utf-8"))
    replay_value["generationRoots"][0]["sourceVersion"] = 2
    replay_value["generationRoots"][0]["selectedJob"]["sourceVersion"] = 2
    replay_value["generationRoots"][0]["attemptHistory"][0]["sourceVersion"] = 2
    for generated_case in replay_value["generationRoots"][0]["selectedJob"]["result"]["cases"]:
        generated_case["materialization"]["sourceVersion"] = 2
    _reseal(replay_value)
    replay_snapshot = tmp_path / "replay-snapshot.json"
    replay_snapshot.write_text(json.dumps(replay_value), encoding="utf-8")
    replay_hash = hashlib.sha256(replay_snapshot.read_bytes()).hexdigest()

    base_verify = [
        "--json-report", str(outputs["json"]),
        "--junit-report", str(outputs["junit"]),
        "--markdown-report", str(outputs["markdown"]),
        "--manifest-output", str(tmp_path / "manifest-replay.json"),
        "--snapshot", str(replay_snapshot),
        "--expected-snapshot-sha256", replay_hash,
    ]
    assert verify_main(base_verify) == 2

    outputs["markdown"].write_text(
        outputs["markdown"].read_text(encoding="utf-8") + "reviewer@example.com\n",
        encoding="utf-8",
    )
    tamper_verify = [
        "--json-report", str(outputs["json"]),
        "--junit-report", str(outputs["junit"]),
        "--markdown-report", str(outputs["markdown"]),
        "--manifest-output", str(tmp_path / "manifest-tamper.json"),
        "--snapshot", str(snapshot),
        "--expected-snapshot-sha256", snapshot_hash,
    ]
    assert verify_main(tamper_verify) == 2


def test_release_workflow_uses_only_protected_trust_anchors_and_pinned_actions() -> None:
    workflow = (ROOT / ".github/workflows/automatic-generation-quality-release.yml").read_text(
        encoding="utf-8"
    )
    ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "inputs:" not in workflow
    assert "${{ vars.APPROVED_SNAPSHOT_PATH }}" in workflow
    assert "${{ secrets.APPROVED_SNAPSHOT_SHA256 }}" in workflow
    assert "inputs.approved_snapshot" not in workflow
    assert "generation:quality:eval --" not in workflow
    assert "generation:quality:verify-artifacts --" not in workflow
    for document in (workflow, ci_workflow):
        uses = re.findall(r"^\s*-?\s*uses:\s+([^\s]+)$", document, flags=re.MULTILINE)
        assert uses
        assert all(re.search(r"@[0-9a-f]{40}$", value) for value in uses)
