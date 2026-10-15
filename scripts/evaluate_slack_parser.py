"""Evaluate the conservative Slack work-item parser against golden fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tc_backend.infrastructure.slack.evaluation import (
    SlackParserEvaluation,
    evaluate_slack_parser_manifest,
)

DEFAULT_MANIFEST = Path("evals/fixtures/manifest.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--fail-under-field-accuracy",
        type=float,
        default=None,
        help="exit non-zero when four-field exact accuracy is below this 0..1 threshold",
    )
    return parser.parse_args()


def render_text(report: SlackParserEvaluation) -> str:
    lines = [
        f"Dataset: {report.dataset_id} (synthetic={str(report.synthetic).lower()})",
        f"Fixtures: {report.fixture_count}",
        (
            "Four-field exact accuracy: "
            f"{report.matched_fields}/{report.total_fields} ({report.field_accuracy:.2%})"
        ),
        f"Whole parse exact matches: {report.exact_matches}/{report.fixture_count}",
        f"missingFields matches: {report.missing_fields_matches}/{report.fixture_count}",
        f"source revision matches: {report.source_revision_matches}/{report.fixture_count}",
    ]
    mismatches = [item for item in report.results if item.mismatched_fields]
    if mismatches:
        lines.append("Field mismatches:")
        lines.extend(
            f"- {item.fixture_id}: {', '.join(item.mismatched_fields)}" for item in mismatches
        )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if args.fail_under_field_accuracy is not None and not (
        0 <= args.fail_under_field_accuracy <= 1
    ):
        raise SystemExit("--fail-under-field-accuracy must be between 0 and 1")
    report = evaluate_slack_parser_manifest(args.manifest)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    if (
        args.fail_under_field_accuracy is not None
        and report.field_accuracy < args.fail_under_field_accuracy
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
