from __future__ import annotations

from pathlib import Path

from tc_backend.infrastructure.slack.evaluation import evaluate_slack_parser_manifest

ROOT = Path(__file__).resolve().parents[4]


def test_synthetic_slack_parser_evaluation_is_reproducible() -> None:
    report = evaluate_slack_parser_manifest(ROOT / "evals/fixtures/manifest.json")

    assert report.dataset_id == "synthetic-slack-golden-v1"
    assert report.synthetic is True
    assert report.fixture_count == 12
    assert report.total_fields == 48
    assert report.field_accuracy >= 0.8
    assert report.missing_fields_matches == report.fixture_count
    assert {item.category for item in report.results} == {
        "NORMAL",
        "MISSING",
        "REVERSAL",
        "CONFLICT",
    }
    assert all(item.total_fields == 4 for item in report.results)
