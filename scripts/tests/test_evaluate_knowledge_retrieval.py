from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.evaluate_knowledge_retrieval import (
    DEFAULT_MANIFEST,
    DeterministicRetriever,
    Document,
    FixtureError,
    QueryCase,
    RetrievalItem,
    RetrievalResponse,
    evaluate_manifest,
    evaluate_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/evaluate_knowledge_retrieval.py"
FIXTURE_DIR = ROOT / "evals/fixtures/knowledge-retrieval"


def _snapshot_item(
    node_id: str,
    node_type: str,
    *,
    degraded: bool,
    execution_outcome: str | None = None,
) -> dict[str, object]:
    source = {
        "sourceType": f"{node_type}_REVISION",
        "sourceEntityId": f"entity_{node_id}",
        "sourceRevisionId": f"revision_{node_id}_1",
    }
    item: dict[str, object] = {
        "nodeId": node_id,
        "nodeType": node_type,
        "status": "ACTIVE",
        "evidenceIds": [f"evidence_{node_id}"],
        "source": source,
        "citation": {
            "source": dict(source),
            "label": f"Reviewed {node_type} result",
            "uri": f"https://knowledge.example.invalid/{node_id}",
            "excerpt": f"Reviewed evidence excerpt for {node_id}.",
        },
        "rank": 1,
        "score": 0.9,
        "reason": {
            "summary": "Captured search ranking signals.",
            "signals": ["VECTOR"] if degraded else ["VECTOR", "LIGHTRAG"],
        },
        "degraded": degraded,
    }
    if execution_outcome is not None:
        item["executionOutcome"] = execution_outcome
    return item


def _live_snapshot(*, reviewed: bool = True, provenance: bool = True) -> dict[str, object]:
    labels = (
        ("query_existing_case", "EXISTING_TEST_CASE", "case_42", "TEST_CASE", None),
        (
            "query_failed_execution",
            "FAILED_EXECUTION",
            "execution_99",
            "EXECUTION",
            "FAILED",
        ),
        ("query_issue", "ISSUE", "issue_17", "ISSUE", None),
    )
    queries: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    for query_id, category, node_id, node_type, outcome in labels:
        relevant: dict[str, object] = {"nodeId": node_id, "nodeType": node_type}
        if outcome is not None:
            relevant["executionOutcome"] = outcome
        queries.append(
            {
                "queryId": query_id,
                "text": f"reviewed relevance query for {category.lower()}",
                "statuses": ["ACTIVE"],
                "relevanceCategory": category,
                "relevantNodes": [relevant],
            }
        )
        results.append(
            {
                "queryId": query_id,
                "baseline": {
                    "degraded": False,
                    "degradationReasons": [],
                    "items": [
                        _snapshot_item(
                            node_id,
                            node_type,
                            degraded=False,
                            execution_outcome=outcome,
                        )
                    ],
                },
                "degraded": {
                    "degraded": True,
                    "degradationReasons": ["LIGHTRAG_UNAVAILABLE"],
                    "items": [
                        _snapshot_item(
                            node_id,
                            node_type,
                            degraded=True,
                            execution_outcome=outcome,
                        )
                    ],
                },
            }
        )
    review: dict[str, object] = {"status": "UNREVIEWED"}
    if reviewed:
        review = {
            "status": "REVIEWED",
            "reviewedBy": "retrieval-quality-reviewers",
            "reviewedAt": "2026-08-26T10:00:00Z",
            "labelSetId": "labels_operational_v1",
        }
    snapshot: dict[str, object] = {
        "fixtureKind": "RESULT_SNAPSHOT",
        "schemaVersion": "1.0.0",
        "synthetic": False,
        "dataset": {
            "datasetId": "operational_retrieval_review_v1",
            "description": "Explicit offline snapshot with reviewed operational labels.",
            "kValues": [1, 3],
            "fallbackK": 3,
            "review": review,
            "queries": queries,
        },
        "results": results,
    }
    if provenance:
        snapshot["provenance"] = {
            "capturedAt": "2026-08-26T10:05:00Z",
            "environment": "release-candidate",
            "runtime": {"serviceVersion": "2026.08.26", "revision": "abc1234"},
            "model": {
                "embeddingModel": "bge-m3-reviewed-version",
                "rerankerModel": "not-used",
            },
            "search": {
                "implementation": "hybrid-search-v2",
                "indexRevision": "index-2026-08-26",
                "configurationRevision": "search-config-7",
            },
        }
    return snapshot


def _write_snapshot(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FaultyRetriever:
    """Inject duplicate, inactive, and incomplete result defects into the double."""

    def __init__(self) -> None:
        self.delegate = DeterministicRetriever()

    def search(
        self,
        query: QueryCase,
        documents: list[Document] | tuple[Document, ...],
        *,
        limit: int,
        light_rag_available: bool,
    ) -> RetrievalResponse:
        response = self.delegate.search(
            query,
            documents,
            limit=limit,
            light_rag_available=light_rag_available,
        )
        inactive = next(document for document in documents if document.status == "SUPERSEDED")
        injected = RetrievalItem(
            document=inactive,
            rank=len(response.items) + 1,
            score=0.01,
            reason_summary="",
            reason_signals=(),
            degraded=response.degraded,
        )
        duplicate = replace(response.items[0], rank=len(response.items) + 2)
        return replace(response, items=(*response.items, injected, duplicate))


def test_golden_retrieval_metrics_and_default_gates_pass() -> None:
    report = evaluate_manifest(DEFAULT_MANIFEST)

    assert report.passed is True
    assert report.recall_at_k == {1: 0.625, 3: 1.0}
    assert report.active_evidence_coverage == 1.0
    assert report.inactive_misuse_by_status == {
        "AMBIGUOUS": 0,
        "SUPERSEDED": 0,
        "RETRACTED": 0,
    }
    assert report.citation_source_revision_link_rate == 1.0
    assert report.duplicate_count == 0
    assert report.reason_rank_completeness == 1.0
    assert report.degraded_fallback_retention == 1.0
    assert report.fallback_contract_failures == 0


def test_v1_manifest_without_threshold_profiles_remains_supported(
    tmp_path: Path,
) -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    manifest["schemaVersion"] = "1.0.0"
    manifest.pop("thresholdProfiles")
    manifest["fixtureSchema"] = str(FIXTURE_DIR / "retrieval-eval.schema.json")
    manifest["fixtures"] = [str(FIXTURE_DIR / "golden-v1.json")]
    manifest_path = tmp_path / "v1-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = evaluate_manifest(manifest_path)

    assert report.passed is True
    assert report.dataset.threshold_profile == "candidate"
    with pytest.raises(FixtureError, match="release profile is not defined"):
        evaluate_manifest(manifest_path, profile="release")


def test_degraded_run_keeps_active_fallback_and_marks_lightrag_unavailable() -> None:
    report = evaluate_manifest(DEFAULT_MANIFEST)

    for query in report.queries:
        assert query.baseline.degraded is False
        assert query.baseline.degradation_reasons == ()
        assert query.degraded.degraded is True
        assert query.degraded.degradation_reasons == ("LIGHTRAG_UNAVAILABLE",)
        assert all(item.degraded for item in query.degraded.items)
        assert all("LIGHTRAG" not in item.reason_signals for item in query.degraded.items)
        assert set(query.relevant_node_ids).issubset(
            {item.document.node_id for item in query.degraded.items[:3]}
        )
        assert query.fallback_contract_passed is True


def test_duplicate_inactive_and_reason_rank_defects_fail_the_gate() -> None:
    report = evaluate_manifest(DEFAULT_MANIFEST, retriever=FaultyRetriever())

    assert report.passed is False
    assert report.duplicate_count > 0
    assert report.inactive_misuse_count > 0
    assert report.reason_rank_completeness < 1.0
    failed = {check.metric for check in report.checks if not check.passed}
    assert {"duplicateCount", "inactiveMisuseCount", "reasonRankCompleteness"}.issubset(
        failed
    )


def test_schema_accepts_manifest_golden_dataset_and_result_snapshot(
    tmp_path: Path,
) -> None:
    schema = FIXTURE_DIR / "retrieval-eval.schema.json"
    manifest = FIXTURE_DIR / "manifest.json"
    golden = FIXTURE_DIR / "golden-v1.json"
    snapshot = _write_snapshot(tmp_path, _live_snapshot())
    javascript = """
const fs = require('fs');
const Ajv2020 = require('ajv/dist/2020');
const addFormats = require('ajv-formats');
const schema = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const ajv = new Ajv2020({allErrors: true, strict: true});
addFormats(ajv);
const validate = ajv.compile(schema);
for (const file of process.argv.slice(2)) {
  const value = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!validate(value)) {
    process.stderr.write(JSON.stringify(validate.errors));
    process.exit(1);
  }
}
"""

    result = subprocess.run(
        [
            "node",
            "-e",
            javascript,
            str(schema),
            str(manifest),
            str(golden),
            str(snapshot),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_release_profile_fails_closed_for_synthetic_fixture() -> None:
    report = evaluate_manifest(DEFAULT_MANIFEST, profile="release")

    assert report.passed is False
    assert report.release_certified is False
    failed = {check.metric for check in report.checks if not check.passed}
    assert "releaseDatasetNonSynthetic" in failed
    assert {f"relevanceRecall:{category}" for category in (
        "EXISTING_TEST_CASE",
        "FAILED_EXECUTION",
        "ISSUE",
    )}.issubset(failed)


def test_reviewed_operational_snapshot_can_pass_release_contract(
    tmp_path: Path,
) -> None:
    snapshot = _write_snapshot(tmp_path, _live_snapshot())

    report = evaluate_snapshot(snapshot, profile="release")

    assert report.passed is True
    assert report.release_certified is True
    assert report.dataset.synthetic is False
    assert report.dataset.evaluation_mode == "result-snapshot"
    assert report.relevance_recall_by_category == {
        "EXISTING_TEST_CASE": 1.0,
        "FAILED_EXECUTION": 1.0,
        "ISSUE": 1.0,
    }
    assert report.dataset.dataset_fingerprint.startswith("sha256:")
    assert report.results_fingerprint.startswith("sha256:")


@pytest.mark.parametrize(
    ("reviewed", "provenance", "failed_metric"),
    [
        (False, True, "releaseDatasetReviewed"),
        (True, False, "releaseRuntimeProvenance"),
    ],
)
def test_release_profile_rejects_unreviewed_or_missing_provenance(
    tmp_path: Path,
    reviewed: bool,
    provenance: bool,
    failed_metric: str,
) -> None:
    snapshot = _write_snapshot(
        tmp_path, _live_snapshot(reviewed=reviewed, provenance=provenance)
    )

    candidate = evaluate_snapshot(snapshot, profile="candidate")
    release = evaluate_snapshot(snapshot, profile="release")

    assert candidate.passed is True
    assert release.passed is False
    assert release.release_certified is False
    assert failed_metric in {check.metric for check in release.checks if not check.passed}


def test_operational_labels_enforce_category_node_type_and_failed_outcome(
    tmp_path: Path,
) -> None:
    snapshot_payload = _live_snapshot()
    dataset = snapshot_payload["dataset"]
    assert isinstance(dataset, dict)
    queries = dataset["queries"]
    assert isinstance(queries, list)
    failed_query = queries[1]
    assert isinstance(failed_query, dict)
    relevant_nodes = failed_query["relevantNodes"]
    assert isinstance(relevant_nodes, list)
    relevant = relevant_nodes[0]
    assert isinstance(relevant, dict)
    relevant["executionOutcome"] = "PASSED"
    snapshot = _write_snapshot(tmp_path, snapshot_payload)

    with pytest.raises(FixtureError, match="executionOutcome=FAILED"):
        evaluate_snapshot(snapshot, profile="candidate")


def test_snapshot_contract_rejects_secret_or_unknown_provenance_fields(
    tmp_path: Path,
) -> None:
    snapshot_payload = _live_snapshot()
    provenance = snapshot_payload["provenance"]
    assert isinstance(provenance, dict)
    provenance["apiKey"] = "must-never-be-embedded"
    snapshot = _write_snapshot(tmp_path, snapshot_payload)

    with pytest.raises(FixtureError, match="unsupported fields"):
        evaluate_snapshot(snapshot, profile="candidate")


def test_snapshot_gate_detects_lifecycle_citation_duplicate_reason_and_degraded_defects(
    tmp_path: Path,
) -> None:
    snapshot_payload = _live_snapshot()
    results = snapshot_payload["results"]
    assert isinstance(results, list)
    first_result = results[0]
    assert isinstance(first_result, dict)
    for response_name in ("baseline", "degraded"):
        response = first_result[response_name]
        assert isinstance(response, dict)
        items = response["items"]
        assert isinstance(items, list)
        item = items[0]
        assert isinstance(item, dict)
        item["status"] = "SUPERSEDED"
        citation = item["citation"]
        assert isinstance(citation, dict)
        citation_source = citation["source"]
        assert isinstance(citation_source, dict)
        citation_source["sourceRevisionId"] = "different_revision"
        item["evidenceIds"] = []
        reason = item["reason"]
        assert isinstance(reason, dict)
        reason["summary"] = ""
        duplicate = json.loads(json.dumps(item))
        duplicate["rank"] = 2
        items.append(duplicate)
    second_result = results[1]
    assert isinstance(second_result, dict)
    for response_name in ("baseline", "degraded"):
        response = second_result[response_name]
        assert isinstance(response, dict)
        items = response["items"]
        assert isinstance(items, list)
        item = items[0]
        assert isinstance(item, dict)
        item["evidenceIds"] = []
    degraded_response = first_result["degraded"]
    assert isinstance(degraded_response, dict)
    degraded_response["degraded"] = False
    snapshot = _write_snapshot(tmp_path, snapshot_payload)

    report = evaluate_snapshot(snapshot, profile="candidate")

    assert report.passed is False
    failed = {check.metric for check in report.checks if not check.passed}
    assert {
        "activeEvidenceCoverage",
        "inactiveMisuseCount",
        "citationSourceRevisionLinkRate",
        "duplicateCount",
        "reasonRankCompleteness",
        "fallbackContractFailures",
    }.issubset(failed)


def test_snapshot_fingerprints_separate_labels_from_results(tmp_path: Path) -> None:
    first_payload = _live_snapshot()
    first_path = _write_snapshot(tmp_path, first_payload)
    first = evaluate_snapshot(first_path)

    second_payload = json.loads(json.dumps(first_payload))
    second_results = second_payload["results"]
    second_results[0]["baseline"]["items"][0]["score"] = 0.81
    second_path = tmp_path / "snapshot-2.json"
    second_path.write_text(json.dumps(second_payload), encoding="utf-8")
    second = evaluate_snapshot(second_path)

    assert first.dataset.dataset_fingerprint == second.dataset.dataset_fingerprint
    assert first.results_fingerprint != second.results_fingerprint


def test_cli_emits_json_and_returns_nonzero_when_a_gate_fails(tmp_path: Path) -> None:
    passing = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert passing.returncode == 0, passing.stderr
    payload = json.loads(passing.stdout)
    assert payload["gate"]["passed"] is True
    assert payload["synthetic"] is True

    manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    manifest["fixtureSchema"] = str(FIXTURE_DIR / "retrieval-eval.schema.json")
    manifest["fixtures"] = [str(FIXTURE_DIR / "golden-v1.json")]
    manifest["defaultGates"]["minRecallAtK"]["1"] = 1.0
    manifest["thresholdProfiles"]["candidate"]["minRecallAtK"]["1"] = 1.0
    failing_manifest = tmp_path / "failing-manifest.json"
    failing_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    failing = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(failing_manifest), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failing.returncode == 1, failing.stderr
    failed_payload = json.loads(failing.stdout)
    assert failed_payload["gate"]["passed"] is False
    assert any(
        check["metric"] == "recall@1" and check["passed"] is False
        for check in failed_payload["gate"]["checks"]
    )


def test_snapshot_cli_requires_and_writes_explicit_json_and_markdown_outputs(
    tmp_path: Path,
) -> None:
    snapshot = _write_snapshot(tmp_path, _live_snapshot())
    json_output = tmp_path / "reports" / "evaluation.json"
    markdown_output = tmp_path / "reports" / "evaluation.md"

    missing_outputs = subprocess.run(
        [sys.executable, str(SCRIPT), "--snapshot", str(snapshot)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing_outputs.returncode == 2
    assert "requires both --json-output and --markdown-output" in missing_outputs.stderr

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--snapshot",
            str(snapshot),
            "--profile",
            "release",
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload == json.loads(result.stdout)
    assert payload["certification"]["releaseCertified"] is True
    markdown = markdown_output.read_text(encoding="utf-8")
    assert "# Knowledge retrieval evaluation report" in markdown
    assert "Release certified: `yes`" in markdown
    assert payload["fingerprints"]["dataset"] in markdown
