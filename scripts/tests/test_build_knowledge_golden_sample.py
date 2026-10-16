from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.build_knowledge_golden_sample import (
    DEFAULT_SEED,
    ROOT,
    CandidateError,
    build_candidate,
    load_source_export,
    redact_text,
)

SCRIPT = ROOT / "scripts/build_knowledge_golden_sample.py"


def _fingerprint(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _record(
    key: str,
    source_type: str,
    node_type: str,
    *,
    status: str = "ACTIVE",
    execution_outcome: str | None = None,
    label: str | None = None,
    text: str | None = None,
    person_identifiers: list[str] | None = None,
) -> dict[str, object]:
    selected_text = text or f"Safe candidate text {key}."
    record: dict[str, object] = {
        "sourceType": source_type,
        "sourceEntityId": f"source_{key}",
        "sourceRevisionId": f"revision_{key}_1",
        "sourceContentFingerprint": _fingerprint(selected_text),
        "nodeId": f"node_{key}",
        "nodeType": node_type,
        "status": status,
        "label": label or f"Candidate {key}",
        "text": selected_text,
        "recordedAt": "2026-08-26T10:00:00Z",
        "executionOutcome": execution_outcome,
        "personIdentifiers": person_identifiers or [],
    }
    return record


def _complete_records() -> list[dict[str, object]]:
    sensitive = (
        "Alice Smith <@U12345678> emailed alice@example.com about PROJ-123 at "
        "https://jira.example.invalid/browse/PROJ-123 with password=hunter2 "
        "and token " + "xoxb-" + "123456-secret for @bob."
    )
    return [
        _record(
            "slack_active",
            "SLACK_MESSAGE_REVISION",
            "DECISION",
            text=sensitive,
            person_identifiers=["Alice Smith"],
        ),
        _record("case", "TEST_CASE_VERSION", "TEST_CASE"),
        _record(
            "execution",
            "EXECUTION_ATTEMPT",
            "EXECUTION",
            execution_outcome="FAIL",
        ),
        _record("issue", "ISSUE_SNAPSHOT", "ISSUE"),
        _record(
            "ambiguous",
            "SLACK_MESSAGE_REVISION",
            "DECISION",
            status="AMBIGUOUS",
        ),
        _record(
            "superseded",
            "SLACK_MESSAGE_REVISION",
            "DECISION",
            status="SUPERSEDED",
        ),
        _record(
            "retracted",
            "SLACK_MESSAGE_REVISION",
            "DECISION",
            status="RETRACTED",
        ),
    ]


def _export(records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "exportKind": "KNOWLEDGE_GOLDEN_SOURCE_EXPORT",
        "schemaVersion": "1.0.0",
        "exportedAt": "2026-08-26T10:05:00Z",
        "source": {
            "kind": "POSTGRESQL_EXPORT",
            "databaseFingerprint": f"sha256:{'a' * 64}",
            "queryFingerprint": f"sha256:{'b' * 64}",
        },
        "records": records,
    }


def _write_export(
    tmp_path: Path,
    payload: dict[str, object],
    *,
    directory: str = "source",
) -> Path:
    source_dir = tmp_path / directory
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / "knowledge-export.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_complete_export_builds_all_unlabeled_strata_and_never_certifies(
    tmp_path: Path,
) -> None:
    source_path = _write_export(tmp_path, _export(_complete_records()))

    build = build_candidate(load_source_export(source_path), per_stratum=1)
    artifact = build.artifact

    assert build.selected_count == 7
    assert build.insufficiency_count == 0
    assert artifact["artifactKind"] == "KNOWLEDGE_GOLDEN_REVIEW_CANDIDATE"
    assert artifact["reviewState"] == "UNLABELED"
    assert artifact["certification"] == {
        "releaseEligible": False,
        "status": "NOT_CERTIFIED",
        "reason": "HUMAN_REVIEW_AND_RESULT_CAPTURE_REQUIRED",
    }
    candidates = artifact["candidates"]
    assert isinstance(candidates, list)
    assert {item["stratum"] for item in candidates} == {
        "SLACK_ACTIVE_DECISION",
        "EXISTING_TEST_CASE",
        "FAILED_EXECUTION",
        "ISSUE",
        "EXCLUSION_AMBIGUOUS",
        "EXCLUSION_SUPERSEDED",
        "EXCLUSION_RETRACTED",
    }
    assert all(item["review"]["state"] == "UNLABELED" for item in candidates)
    assert all(item["review"]["reviewer"] is None for item in candidates)
    execution = next(item for item in candidates if item["stratum"] == "FAILED_EXECUTION")
    assert execution["executionOutcome"] == "FAILED"


def test_privacy_redaction_removes_person_email_url_jira_and_secret_values(
    tmp_path: Path,
) -> None:
    source_path = _write_export(tmp_path, _export(_complete_records()))

    build = build_candidate(load_source_export(source_path), per_stratum=1)
    serialized = json.dumps(build.artifact, ensure_ascii=False)

    for sensitive in (
        "Alice Smith",
        "U12345678",
        "alice@example.com",
        "PROJ-123",
        "jira.example.invalid",
        "hunter2",
        "xoxb-" + "123456-secret",
        "@bob",
    ):
        assert sensitive not in serialized
    for marker in (
        "[REDACTED_PERSON]",
        "[REDACTED_EMAIL]",
        "[REDACTED_JIRA]",
        "[REDACTED_URL]",
        "[REDACTED_SECRET]",
    ):
        assert marker in serialized
    assert all(count > 0 for count in build.redaction_counts.values() if count)


def test_unsafe_nonopaque_references_are_pseudonymized(tmp_path: Path) -> None:
    records = _complete_records()
    records[0]["sourceEntityId"] = "source_PROJ-123"
    records[0]["sourceRevisionId"] = "person@example.com"
    records[0]["nodeId"] = "https://example.invalid/node"
    source_path = _write_export(tmp_path, _export(records))

    build = build_candidate(load_source_export(source_path), per_stratum=1)
    candidates = build.artifact["candidates"]
    assert isinstance(candidates, list)
    slack = next(item for item in candidates if item["stratum"] == "SLACK_ACTIVE_DECISION")

    assert slack["nodeId"].startswith("ref_sha256_")
    assert slack["source"]["sourceEntityId"].startswith("ref_sha256_")
    assert slack["source"]["sourceRevisionId"].startswith("ref_sha256_")
    assert slack["privacy"]["redactionCounts"]["REFERENCE"] == 3


def test_selection_is_deterministic_and_independent_of_input_order(
    tmp_path: Path,
) -> None:
    records = _complete_records()
    records.extend(
        _record(f"slack_extra_{index}", "SLACK_MESSAGE_REVISION", "DECISION")
        for index in range(5)
    )
    first_path = _write_export(tmp_path, _export(records), directory="first")
    second_path = _write_export(
        tmp_path, _export(list(reversed(records))), directory="second"
    )

    first = build_candidate(
        load_source_export(first_path), per_stratum=1, seed=DEFAULT_SEED
    )
    second = build_candidate(
        load_source_export(second_path), per_stratum=1, seed=DEFAULT_SEED
    )

    assert first.artifact["candidates"] == second.artifact["candidates"]


def test_duplicates_unmatched_records_and_stratum_shortfalls_are_reported(
    tmp_path: Path,
) -> None:
    records = _complete_records()[:2]
    records.append(json.loads(json.dumps(records[0])))
    records.append(
        _record(
            "passing_execution",
            "EXECUTION_ATTEMPT",
            "EXECUTION",
            execution_outcome="PASS",
        )
    )
    source_path = _write_export(tmp_path, _export(records))

    build = build_candidate(load_source_export(source_path), per_stratum=2)
    artifact = build.artifact

    assert build.duplicate_count == 1
    assert build.unmatched_count == 1
    assert build.insufficiency_count == 7
    insufficiencies = artifact["insufficiencies"]
    assert isinstance(insufficiencies, list)
    assert all(item["code"] == "INSUFFICIENT_STRATUM" for item in insufficiencies)
    summary = artifact["summary"]
    assert summary["completeStratifiedCandidate"] is False


def test_source_content_fingerprint_mismatch_is_rejected(tmp_path: Path) -> None:
    records = _complete_records()
    records[0]["sourceContentFingerprint"] = f"sha256:{'0' * 64}"
    source_path = _write_export(tmp_path, _export(records))

    with pytest.raises(CandidateError, match="does not match text"):
        load_source_export(source_path)


def test_export_contract_rejects_unknown_secret_fields(tmp_path: Path) -> None:
    payload = _export(_complete_records())
    source = payload["source"]
    assert isinstance(source, dict)
    source["databaseUrl"] = "postgresql://user:secret@example.invalid/db"
    source_path = _write_export(tmp_path, payload)

    with pytest.raises(CandidateError, match="unsupported fields"):
        load_source_export(source_path)


def test_redact_text_handles_explicit_person_identifiers_case_insensitively() -> None:
    result = redact_text(
        "ALICE SMITH met Alice Smith and emailed alice@example.com.",
        ["Alice Smith"],
    )

    assert result.value == (
        "[REDACTED_PERSON] met [REDACTED_PERSON] and emailed [REDACTED_EMAIL]."
    )
    assert result.counts["PERSON"] == 2
    assert result.counts["EMAIL"] == 1


def test_cli_defaults_to_preview_and_does_not_write(tmp_path: Path) -> None:
    source_path = _write_export(tmp_path, _export(_complete_records()))
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source_path),
            "--per-stratum",
            "1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    assert result.returncode == 0, result.stderr
    assert "Preview only; no output file was written." in result.stdout
    assert "UNLABELED" in result.stdout
    assert before == after


def test_cli_json_preview_is_redacted_and_unlabeled(tmp_path: Path) -> None:
    source_path = _write_export(tmp_path, _export(_complete_records()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source_path),
            "--per-stratum",
            "1",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["reviewState"] == "UNLABELED"
    assert payload["certification"]["releaseEligible"] is False
    assert "alice@example.com" not in result.stdout
    assert "hunter2" not in result.stdout


def test_cli_writes_only_to_explicit_separate_external_output(tmp_path: Path) -> None:
    source_path = _write_export(tmp_path, _export(_complete_records()))
    output_path = tmp_path / "review" / "candidate.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source_path),
            "--per-stratum",
            "1",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output_path.is_file()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["reviewState"] == "UNLABELED"
    assert "Output written to the explicit external path." in result.stdout


def test_cli_never_overwrites_an_existing_output(tmp_path: Path) -> None:
    source_path = _write_export(tmp_path, _export(_complete_records()))
    output_path = tmp_path / "review" / "candidate.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text("keep-existing", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source_path),
            "--per-stratum",
            "1",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "output already exists" in result.stderr
    assert output_path.read_text(encoding="utf-8") == "keep-existing"


@pytest.mark.parametrize("output_kind", ["same-file", "same-directory", "repository"])
def test_cli_rejects_source_or_repository_output_paths(
    tmp_path: Path, output_kind: str
) -> None:
    source_path = _write_export(tmp_path, _export(_complete_records()))
    if output_kind == "same-file":
        output_path = source_path
    elif output_kind == "same-directory":
        output_path = source_path.parent / "candidate.json"
    else:
        output_path = ROOT / "candidate-must-not-be-written.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source_path),
            "--per-stratum",
            "1",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "knowledge golden candidate error" in result.stderr
    if output_kind == "repository":
        assert not output_path.exists()
