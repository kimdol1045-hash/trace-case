from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

from scripts.evaluate_automatic_generation_version_regression import main
from tc_backend.generation.quality_gate import canonical_digest
from tc_backend.generation.version_regression import (
    VersionRegressionInputError,
    VersionRegressionReportError,
    evaluate_version_regression,
    validate_input,
    validate_report,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT
    / "evals/fixtures/automatic-generation-version-regression/synthetic-comparison-input-v1.json"
)


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _reseal_new_identity(value: dict[str, object]) -> None:
    candidate = _mapping(value["newCandidate"])
    identity = _mapping(candidate["identity"])
    digest = canonical_digest(identity)
    candidate["identityDigest"] = digest
    _mapping(value["changeRequest"])["proposedBaselineIdentityDigest"] = digest
    _mapping(value["newGoldenReport"])["baselineIdentityDigest"] = digest
    _mapping(value["compatibilityMatrix"])["newBaselineIdentityDigest"] = digest
    _reseal_authority_digests(value)


def _reseal_authority_digests(value: dict[str, object]) -> None:
    for container_name, field in (
        ("changeRequest", "requestDigest"),
        ("compatibilityMatrix", "matrixDigest"),
    ):
        container = _mapping(value[container_name])
        body = dict(container)
        body.pop(field)
        container[field] = canonical_digest(body)


def _snapshot(tmp_path: Path, value: dict[str, object]) -> tuple[Path, dict[str, Path]]:
    outputs = {
        "json": tmp_path / "version-regression.json",
        "junit": tmp_path / "version-regression.junit.xml",
        "markdown": tmp_path / "version-regression.md",
    }
    value["reportOutputs"] = {name: str(path) for name, path in outputs.items()}
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path, outputs


def _arguments(snapshot: Path, outputs: dict[str, Path]) -> list[str]:
    return [
        "--snapshot",
        str(snapshot),
        "--profile",
        "candidate",
        "--expected-snapshot-sha256",
        hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "--json-output",
        str(outputs["json"]),
        "--junit-output",
        str(outputs["junit"]),
        "--markdown-output",
        str(outputs["markdown"]),
    ]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("modelRevision", "latest"),
        ("embeddingRevision", "bge-m3:latest"),
        ("promptVersion", "current"),
        ("regressionPolicyVersion", "policy/stable"),
        ("placementPolicyVersion", "^2.0.0"),
        ("ontologySchemaVersion", "v2.*"),
        ("generationOutputSchemaVersion", "1.0 || 2.0"),
    ),
)
def test_missing_wildcard_and_mutable_alias_pins_are_input_errors(
    field: str, bad_value: str
) -> None:
    value = _fixture()
    identity = _mapping(_mapping(value["newCandidate"])["identity"])
    identity[field] = bad_value
    _reseal_new_identity(value)

    with pytest.raises(
        VersionRegressionInputError, match="SCHEMA_VALIDATION_FAILED|UNPINNED|RANGE"
    ):
        validate_input(value)


@pytest.mark.parametrize(
    ("dimension", "field", "changed_value"),
    (
        ("GENERATION_POLICY", "generationPolicyDigest", "sha256:" + "81" * 32),
        ("PROMPT", "promptDigest", "sha256:" + "82" * 32),
        ("GENERATION_PROVIDER_MODEL", "modelRevision", "revision-9"),
        ("EMBEDDING_PROVIDER_MODEL_REVISION", "embeddingRevision", "revision-9"),
        ("EMBEDDING_DIMENSION", "embeddingDimensions", 384),
        ("SEMANTIC_THRESHOLD_POLICY", "semanticDuplicateThreshold", 0.9),
        ("EXACT_DUPLICATE_ALGORITHM", "exactDuplicateAlgorithmVersion", "exact-v9"),
        ("GENERATION_INPUT_SCHEMA", "generationInputSchemaVersion", "2.0.0"),
        ("GENERATION_OUTPUT_SCHEMA", "generationOutputSchemaVersion", "2.0.0"),
        ("SUGGESTION_SCHEMA", "suggestionSchemaVersion", "suggestion/2.0.0"),
        ("QUALITY_GATE_SCHEMA", "qualityGateSchemaVersion", "quality/2.0.0"),
        ("ONTOLOGY_SCHEMA", "ontologySchemaDigest", "sha256:" + "83" * 32),
        ("REGRESSION_EVALUATOR_POLICY", "regressionEvaluatorVersion", "evaluator-v9"),
        ("PLACEMENT_POLICY", "placementPolicyDigest", "sha256:" + "84" * 32),
    ),
)
def test_every_behavior_dimension_rejects_stale_unchanged_classification(
    dimension: str, field: str, changed_value: object
) -> None:
    value = _fixture()
    identity = _mapping(_mapping(value["newCandidate"])["identity"])
    identity[field] = changed_value
    _reseal_new_identity(value)
    _mapping(value["changeRequest"])["changedDimensions"] = (
        ["PROMPT"] if dimension == "PROMPT" else ["PROMPT", dimension]
    )
    entries = _mapping(value["compatibilityMatrix"])["entries"]
    assert isinstance(entries, list)
    for entry in entries:
        assert isinstance(entry, dict)
        if entry["dimension"] == dimension:
            entry["changeType"] = "UNCHANGED"
            entry["migrationPlanId"] = None
    _reseal_authority_digests(value)

    outcome = evaluate_version_regression(value)

    assert outcome.passed is False
    assert "COMPATIBILITY_CHANGE_CLASSIFICATION_MISMATCH" in outcome.reason_codes


def test_identity_report_population_and_quality_tamper_fail_closed() -> None:
    identity_mismatch = _fixture()
    _mapping(identity_mismatch["newGoldenReport"])["baselineIdentityDigest"] = (
        _mapping(identity_mismatch["oldBaseline"])["identityDigest"]
    )
    with pytest.raises(VersionRegressionInputError, match="NEW_REPORT_IDENTITY_MISMATCH"):
        validate_input(identity_mismatch)

    forged_digest = _fixture()
    _mapping(forged_digest["changeRequest"])["reasonDigest"] = "sha256:" + "c9" * 32
    with pytest.raises(VersionRegressionInputError, match="CHANGE_REQUEST_DIGEST_MISMATCH"):
        validate_input(forged_digest)

    cohort_mismatch = _fixture()
    _mapping(cohort_mismatch["newGoldenReport"])["cohortId"] = "cohort_other"
    outcome = evaluate_version_regression(cohort_mismatch)
    assert outcome.exit_code == 1
    assert "OLD_NEW_POPULATION_MISMATCH" in outcome.reason_codes

    weakened = _fixture()
    _mapping(_mapping(weakened["newGoldenReport"])["metrics"])[
        "evidenceCoverage"
    ] = 0.999
    outcome = evaluate_version_regression(weakened)
    assert outcome.exit_code == 1
    assert "NEW_QUALITY_GATE_FAILED" in outcome.reason_codes

    relaxed_threshold = _fixture()
    _mapping(relaxed_threshold["thresholds"])["duplicateCount"] = 1
    with pytest.raises(VersionRegressionInputError, match="SCHEMA_VALIDATION_FAILED"):
        validate_input(relaxed_threshold)

    prompt_mismatch = _fixture()
    old_prompt_version = _mapping(
        _mapping(prompt_mismatch["oldBaseline"])["identity"]
    )["promptVersion"]
    _mapping(_mapping(prompt_mismatch["newCandidate"])["identity"])[
        "promptVersion"
    ] = old_prompt_version
    _reseal_new_identity(prompt_mismatch)
    prompt_outcome = evaluate_version_regression(prompt_mismatch)
    assert prompt_outcome.exit_code == 1
    assert "VERSION_CONTENT_PIN_MISMATCH" in prompt_outcome.reason_codes


def test_schema_and_embedding_migrations_bind_versions_and_population() -> None:
    schema_value = _fixture()
    schema_identity = _mapping(_mapping(schema_value["newCandidate"])["identity"])
    schema_identity["generationInputSchemaVersion"] = "2.0.0"
    _reseal_new_identity(schema_value)
    _mapping(schema_value["changeRequest"])["changedDimensions"] = [
        "PROMPT",
        "GENERATION_INPUT_SCHEMA",
    ]
    schema_matrix = _mapping(schema_value["compatibilityMatrix"])
    schema_entries = schema_matrix["entries"]
    assert isinstance(schema_entries, list)
    schema_entry = next(
        entry
        for entry in schema_entries
        if isinstance(entry, dict) and entry["dimension"] == "GENERATION_INPUT_SCHEMA"
    )
    schema_entry.update(
        {
            "changeType": "MIGRATION_REQUIRED",
            "oldReaderNewArtifact": "MIGRATION_REQUIRED",
            "newReaderOldArtifact": "MIGRATION_REQUIRED",
            "migrationPlanId": "input-schema-v2",
        }
    )
    population = _mapping(schema_value["comparisonPopulation"])
    schema_matrix["schemaMigrations"] = [
        {
            "migrationPlanId": "input-schema-v2",
            "dimension": "GENERATION_INPUT_SCHEMA",
            "fromVersion": "1.0.0",
            "toVersion": "2.0.0",
            "mode": "OFFLINE_DETERMINISTIC",
            "lossless": True,
            "reversible": True,
            "migrationArtifactDigest": "sha256:" + "d1" * 32,
            "verificationStatus": "VERIFIED",
            "verificationReceiptDigest": "sha256:" + "d2" * 32,
            "rollbackArtifactDigest": "sha256:" + "d3" * 32,
            "sourcePopulationDigest": population["sourcePopulationDigest"],
            "populationMembershipDigest": population["populationMembershipDigest"],
        }
    ]
    _reseal_authority_digests(schema_value)
    assert evaluate_version_regression(schema_value).exit_code == 0

    schema_matrix["schemaMigrations"][0]["populationMembershipDigest"] = (
        "sha256:" + "d4" * 32
    )
    _reseal_authority_digests(schema_value)
    schema_outcome = evaluate_version_regression(schema_value)
    assert schema_outcome.exit_code == 1
    assert "SCHEMA_MIGRATION_POPULATION_MISMATCH" in schema_outcome.reason_codes

    embedding_value = _fixture()
    embedding_identity = _mapping(_mapping(embedding_value["newCandidate"])["identity"])
    embedding_identity["embeddingDimensions"] = 384
    _reseal_new_identity(embedding_value)
    _mapping(embedding_value["changeRequest"])["changedDimensions"] = [
        "PROMPT",
        "EMBEDDING_DIMENSION",
    ]
    embedding_matrix = _mapping(embedding_value["compatibilityMatrix"])
    embedding_entries = embedding_matrix["entries"]
    assert isinstance(embedding_entries, list)
    embedding_entry = next(
        entry
        for entry in embedding_entries
        if isinstance(entry, dict) and entry["dimension"] == "EMBEDDING_DIMENSION"
    )
    embedding_entry.update(
        {
            "changeType": "MIGRATION_REQUIRED",
            "oldReaderNewArtifact": "MIGRATION_REQUIRED",
            "newReaderOldArtifact": "MIGRATION_REQUIRED",
            "migrationPlanId": "embedding-384",
        }
    )
    embedding_population = _mapping(embedding_value["comparisonPopulation"])
    embedding_matrix["embeddingMigration"] = {
        "migrationPlanId": "embedding-384",
        "oldDimensions": 256,
        "newDimensions": 384,
        "sourcePopulationDigest": embedding_population["sourcePopulationDigest"],
        "reembeddedPopulationDigest": "sha256:" + "e1" * 32,
        "rollbackVectorPopulationDigest": "sha256:" + "e2" * 32,
        "mixedDimensionsObserved": False,
        "verificationStatus": "VERIFIED",
        "receiptDigest": "sha256:" + "e3" * 32,
    }
    _reseal_authority_digests(embedding_value)
    assert evaluate_version_regression(embedding_value).exit_code == 0

    embedding_matrix["embeddingMigration"]["sourcePopulationDigest"] = (
        "sha256:" + "e4" * 32
    )
    _reseal_authority_digests(embedding_value)
    embedding_outcome = evaluate_version_regression(embedding_value)
    assert embedding_outcome.exit_code == 1
    assert "SEMANTIC_VECTOR_DIMENSION_DRIFT_UNMIGRATED" in embedding_outcome.reason_codes


def test_synthetic_pass_is_contract_only_and_has_no_false_junit_failure(
    tmp_path: Path,
) -> None:
    snapshot, outputs = _snapshot(tmp_path, _fixture())

    assert main(_arguments(snapshot, outputs)) == 0
    report = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert report["candidateDisposition"] == "CONTRACT_ONLY"
    assert report["approvalEligible"] is False
    assert all(check["passed"] for check in report["checks"])
    assert 'failures="0"' in outputs["junit"].read_text(encoding="utf-8")


def test_failed_cached_report_is_not_confused_with_a_passing_artifact(
    tmp_path: Path,
) -> None:
    value = _fixture()
    _mapping(_mapping(value["newGoldenReport"])["metrics"])["duplicateCount"] = 1
    snapshot, outputs = _snapshot(tmp_path, value)

    assert main(_arguments(snapshot, outputs)) == 1
    digest = hashlib.sha256(outputs["json"].read_bytes()).hexdigest()
    assert main(
        [
            "--validate-report",
            str(outputs["json"]),
            "--expected-sha256",
            digest,
        ]
    ) == 1

    forged = json.loads(outputs["json"].read_text(encoding="utf-8"))
    forged["candidateDisposition"] = "CONTRACT_ONLY"
    forged["synthetic"] = True
    with pytest.raises(VersionRegressionReportError, match="FAILED_CHECK"):
        validate_report(forged)


def test_cli_refuses_hash_tamper_unsafe_paths_and_partial_outputs(
    tmp_path: Path,
) -> None:
    snapshot, outputs = _snapshot(tmp_path, _fixture())
    arguments = _arguments(snapshot, outputs)
    arguments[arguments.index("--expected-snapshot-sha256") + 1] = "0" * 64
    assert main(arguments) == 2
    assert not any(path.exists() for path in outputs.values())

    symlink = tmp_path / "snapshot-link.json"
    symlink.symlink_to(snapshot)
    unsafe = _arguments(symlink, outputs)
    unsafe[unsafe.index("--expected-snapshot-sha256") + 1] = hashlib.sha256(
        snapshot.read_bytes()
    ).hexdigest()
    assert main(unsafe) == 2
    assert not any(path.exists() for path in outputs.values())

    outputs["markdown"].write_text("cached", encoding="utf-8")
    assert main(_arguments(snapshot, outputs)) == 2
    assert not outputs["json"].exists()
    assert not outputs["junit"].exists()
    assert outputs["markdown"].read_text(encoding="utf-8") == "cached"


def test_evaluator_has_network_tripwire_and_rejects_raw_text_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    outcome = evaluate_version_regression(_fixture())
    assert outcome.exit_code == 0

    value = _fixture()
    secret = "person@example.invalid raw prompt customer-991"
    _mapping(value["newCandidate"])["rawPrompt"] = secret
    snapshot, outputs = _snapshot(tmp_path, value)
    assert main(_arguments(snapshot, outputs)) == 2
    captured = capsys.readouterr()
    assert secret not in captured.err
    assert not any(path.exists() for path in outputs.values())


def test_release_workflow_keeps_synthetic_and_operational_authority_separate() -> None:
    workflow = (
        ROOT
        / ".github/workflows/automatic-generation-version-regression-release.yml"
    ).read_text(encoding="utf-8")

    assert "Synthetic T09 contract candidate" in workflow
    assert "never activate" in workflow
    assert "Protected operational baseline release" in workflow
    assert "environment: automatic-generation-version-regression-release" in workflow
    assert "APPROVED_REGRESSION_SHA256" in workflow
    assert "--expected-snapshot-sha256" in workflow
    assert "--expected-sha256" in workflow
    assert "continue-on-error" not in workflow
    assert "activate(" not in workflow
