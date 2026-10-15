"""Deterministic evaluation for the Slack work-item parser golden dataset."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tc_backend.infrastructure.slack.models import SLACK_WORK_ITEM_FIELDS, SlackSourceMessage
from tc_backend.infrastructure.slack.parser import SlackWorkItemParser


@dataclass(frozen=True, slots=True)
class SlackParserFixtureResult:
    fixture_id: str
    category: str
    matched_fields: int
    total_fields: int
    missing_fields_match: bool
    source_revisions_match: bool
    exact_match: bool
    mismatched_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "fixtureId": self.fixture_id,
            "category": self.category,
            "matchedFields": self.matched_fields,
            "totalFields": self.total_fields,
            "missingFieldsMatch": self.missing_fields_match,
            "sourceRevisionsMatch": self.source_revisions_match,
            "exactMatch": self.exact_match,
            "mismatchedFields": list(self.mismatched_fields),
        }


@dataclass(frozen=True, slots=True)
class SlackParserEvaluation:
    dataset_id: str
    synthetic: bool
    results: tuple[SlackParserFixtureResult, ...]

    @property
    def fixture_count(self) -> int:
        return len(self.results)

    @property
    def matched_fields(self) -> int:
        return sum(item.matched_fields for item in self.results)

    @property
    def total_fields(self) -> int:
        return sum(item.total_fields for item in self.results)

    @property
    def field_accuracy(self) -> float:
        return self.matched_fields / self.total_fields if self.total_fields else 0.0

    @property
    def exact_matches(self) -> int:
        return sum(item.exact_match for item in self.results)

    @property
    def missing_fields_matches(self) -> int:
        return sum(item.missing_fields_match for item in self.results)

    @property
    def source_revision_matches(self) -> int:
        return sum(item.source_revisions_match for item in self.results)

    def as_dict(self) -> dict[str, Any]:
        category_counts = Counter(item.category for item in self.results)
        category_field_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for item in self.results:
            category_field_totals[item.category][0] += item.matched_fields
            category_field_totals[item.category][1] += item.total_fields
        return {
            "datasetId": self.dataset_id,
            "synthetic": self.synthetic,
            "fixtureCount": self.fixture_count,
            "fieldAccuracy": round(self.field_accuracy, 6),
            "matchedFields": self.matched_fields,
            "totalFields": self.total_fields,
            "exactMatches": self.exact_matches,
            "missingFieldsMatches": self.missing_fields_matches,
            "sourceRevisionMatches": self.source_revision_matches,
            "categories": {
                category: {
                    "fixtureCount": category_counts[category],
                    "fieldAccuracy": round(matched / total, 6),
                }
                for category, (matched, total) in sorted(category_field_totals.items())
            },
            "results": [item.as_dict() for item in self.results],
        }


def evaluate_slack_parser_manifest(
    manifest_path: Path,
    *,
    parser: SlackWorkItemParser | None = None,
) -> SlackParserEvaluation:
    manifest = _read_object(manifest_path)
    dataset_id = _required_string(manifest, "datasetId", context=str(manifest_path))
    synthetic = manifest.get("synthetic")
    if not isinstance(synthetic, bool):
        raise ValueError(f"{manifest_path}: synthetic must be a boolean")
    fixture_paths = manifest.get("fixtures")
    if not isinstance(fixture_paths, list) or not fixture_paths:
        raise ValueError(f"{manifest_path}: fixtures must be a non-empty array")

    active_parser = parser or SlackWorkItemParser()
    results = tuple(
        _evaluate_fixture(
            _safe_fixture_path(manifest_path.parent, relative_path),
            parser=active_parser,
        )
        for relative_path in fixture_paths
    )
    return SlackParserEvaluation(
        dataset_id=dataset_id,
        synthetic=synthetic,
        results=results,
    )


def _evaluate_fixture(
    fixture_path: Path,
    *,
    parser: SlackWorkItemParser,
) -> SlackParserFixtureResult:
    fixture = _read_object(fixture_path)
    fixture_id = _required_string(fixture, "fixtureId", context=str(fixture_path))
    category = _required_string(fixture, "category", context=fixture_id)
    source = _required_object(fixture, "source", context=fixture_id)
    root = _required_object(source, "root", context=f"{fixture_id}.source")
    thread = source.get("thread")
    if not isinstance(thread, list):
        raise ValueError(f"{fixture_id}: source.thread must be an array")
    raw_messages = [root, *thread]
    messages = [
        SlackSourceMessage(
            message_revision_id=_required_string(
                message,
                "messageRevisionId",
                context=f"{fixture_id}.message[{index}]",
            ),
            body=_required_string(message, "body", context=f"{fixture_id}.message[{index}]"),
            message_ts=_required_string(
                message,
                "messageTs",
                context=f"{fixture_id}.message[{index}]",
            ),
        )
        for index, message in enumerate(raw_messages)
    ]
    expected_container = _required_object(fixture, "expected", context=fixture_id)
    expected = _required_object(expected_container, "parseResult", context=fixture_id)
    actual = parser.parse(messages).as_dict()

    mismatched_fields = tuple(
        field_name
        for field_name in SLACK_WORK_ITEM_FIELDS
        if actual.get(field_name) != expected.get(field_name)
    )
    return SlackParserFixtureResult(
        fixture_id=fixture_id,
        category=category,
        matched_fields=len(SLACK_WORK_ITEM_FIELDS) - len(mismatched_fields),
        total_fields=len(SLACK_WORK_ITEM_FIELDS),
        missing_fields_match=actual.get("missingFields") == expected.get("missingFields"),
        source_revisions_match=(
            actual.get("sourceMessageRevisions") == expected.get("sourceMessageRevisions")
        ),
        exact_match=actual == expected,
        mismatched_fields=mismatched_fields,
    )


def _safe_fixture_path(root: Path, relative_path: object) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("fixture path must be a non-empty string")
    resolved_root = root.resolve()
    resolved = (root / relative_path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"fixture path escapes the dataset root: {relative_path}")
    return resolved


def _read_object(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read JSON object: {path}") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return decoded


def _required_object(value: dict[str, Any], key: str, *, context: str) -> dict[str, Any]:
    candidate = value.get(key)
    if not isinstance(candidate, dict):
        raise ValueError(f"{context}: {key} must be an object")
    return candidate


def _required_string(value: dict[str, Any], key: str, *, context: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError(f"{context}: {key} must be a non-empty string")
    return candidate
