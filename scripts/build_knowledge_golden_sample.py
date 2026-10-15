"""Build a privacy-safe, unlabeled knowledge golden-review candidate.

The builder accepts only an explicit JSON export produced by a read-only
PostgreSQL query or equivalent export process. It has no database connector,
does not inspect environment credentials, and writes nothing unless --output is
provided. Output paths must be separate from the source export and outside the
repository checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
SOURCE_TYPES = (
    "SLACK_MESSAGE_REVISION",
    "TEST_CASE_VERSION",
    "EXECUTION_ATTEMPT",
    "ISSUE_SNAPSHOT",
)
NODE_TYPES = ("DECISION", "TEST_CASE", "EXECUTION", "ISSUE")
LIFECYCLE_STATUSES = ("ACTIVE", "AMBIGUOUS", "SUPERSEDED", "RETRACTED")
EXECUTION_OUTCOMES = ("PASS", "FAIL", "BLOCKED")
WIRE_EXECUTION_OUTCOMES = {
    "PASS": "PASSED",
    "FAIL": "FAILED",
    "BLOCKED": "BLOCKED",
}
FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
OPAQUE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
JIRA_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]{1,15}-[0-9]+\b")
JIRA_REFERENCE_PATTERN = re.compile(r"[A-Z][A-Z0-9]{1,15}-[0-9]+")

STRATUM_ORDER = (
    "SLACK_ACTIVE_DECISION",
    "EXISTING_TEST_CASE",
    "FAILED_EXECUTION",
    "ISSUE",
    "EXCLUSION_AMBIGUOUS",
    "EXCLUSION_SUPERSEDED",
    "EXCLUSION_RETRACTED",
)
STRATUM_DESCRIPTIONS = {
    "SLACK_ACTIVE_DECISION": "Current Slack-backed decision candidate.",
    "EXISTING_TEST_CASE": "Existing immutable test-case version candidate.",
    "FAILED_EXECUTION": "Failed execution-attempt candidate.",
    "ISSUE": "Immutable issue-snapshot candidate.",
    "EXCLUSION_AMBIGUOUS": "Ambiguous Slack decision exclusion control.",
    "EXCLUSION_SUPERSEDED": "Superseded Slack decision exclusion control.",
    "EXCLUSION_RETRACTED": "Retracted Slack decision exclusion control.",
}
DEFAULT_SEED = "knowledge-golden-candidate-v1"
EXPORT_CONTRACT_HELP = """required export shape:
  exportKind=KNOWLEDGE_GOLDEN_SOURCE_EXPORT, schemaVersion=1.0.0,
  exportedAt, source={kind=POSTGRESQL_EXPORT,databaseFingerprint,queryFingerprint},
  records=[{sourceType,sourceEntityId,sourceRevisionId,
            sourceContentFingerprint,nodeId,nodeType,status,label,text,recordedAt,
            executionOutcome,personIdentifiers}]

sourceContentFingerprint must be sha256 of the exact UTF-8 text. Include every
known author/assignee/display name in personIdentifiers so it is removed before
preview or output. No database URL or credential field is accepted.
"""

_REDACTION_PATTERNS = (
    (
        "SECRET",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[-_]?key|access[-_]?token|refresh[-_]?token)"
            r"\s*[:=]\s*[^\s,;]+"
        ),
        "[REDACTED_SECRET]",
    ),
    (
        "SECRET",
        re.compile(
            r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+|"
            r"\b(?:xox[baprs]-[A-Za-z0-9-]+|gh[pousr]_[A-Za-z0-9_]+|"
            r"sk-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{16})\b"
        ),
        "[REDACTED_SECRET]",
    ),
    (
        "URL",
        re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()]+"),
        "[REDACTED_URL]",
    ),
    (
        "EMAIL",
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    ("JIRA", JIRA_KEY_PATTERN, "[REDACTED_JIRA]"),
    (
        "PERSON",
        re.compile(r"<@[A-Z0-9]+>|(?<![\w@])@[A-Za-z0-9][A-Za-z0-9._-]{1,63}"),
        "[REDACTED_PERSON]",
    ),
    (
        "PERSON",
        re.compile(r"\b[UW][A-Z0-9]{8,}\b"),
        "[REDACTED_PERSON]",
    ),
)


class CandidateError(ValueError):
    """An export or requested output violates the candidate-build contract."""


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_type: str
    source_entity_id: str
    source_revision_id: str
    source_content_fingerprint: str
    node_id: str
    node_type: str
    status: str
    label: str
    text: str
    recorded_at: str
    execution_outcome: str | None
    person_identifiers: tuple[str, ...]

    @property
    def identity(self) -> tuple[str, ...]:
        return (
            self.source_type,
            self.source_entity_id,
            self.source_revision_id,
            self.node_id,
        )


@dataclass(frozen=True, slots=True)
class SourceExport:
    exported_at: str
    database_fingerprint: str
    query_fingerprint: str
    input_fingerprint: str
    records: tuple[SourceRecord, ...]


@dataclass(frozen=True, slots=True)
class RedactedValue:
    value: str
    counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class CandidateBuild:
    artifact: Mapping[str, object]
    selected_count: int
    input_count: int
    duplicate_count: int
    unmatched_count: int
    insufficiency_count: int
    redaction_counts: Mapping[str, int]


def load_source_export(path: Path) -> SourceExport:
    payload = _load_object(path)
    _reject_unknown_keys(
        payload,
        {"exportKind", "schemaVersion", "exportedAt", "source", "records"},
        "export",
    )
    _expect(payload, "exportKind", "KNOWLEDGE_GOLDEN_SOURCE_EXPORT", "export")
    _expect(payload, "schemaVersion", "1.0.0", "export")
    exported_at = _required_datetime(payload, "exportedAt", "export")
    source = _required_object(payload, "source", "export")
    _reject_unknown_keys(
        source,
        {"kind", "databaseFingerprint", "queryFingerprint"},
        "export.source",
    )
    _expect(source, "kind", "POSTGRESQL_EXPORT", "export.source")
    database_fingerprint = _required_fingerprint(
        source, "databaseFingerprint", "export.source"
    )
    query_fingerprint = _required_fingerprint(
        source, "queryFingerprint", "export.source"
    )
    raw_records = _required_list(payload, "records", "export", nonempty=True)
    records = tuple(
        _parse_record(raw, f"export.records[{index}]")
        for index, raw in enumerate(raw_records)
    )
    return SourceExport(
        exported_at=exported_at,
        database_fingerprint=database_fingerprint,
        query_fingerprint=query_fingerprint,
        input_fingerprint=_fingerprint(payload),
        records=records,
    )


def build_candidate(
    source_export: SourceExport,
    *,
    per_stratum: int = 3,
    seed: str = DEFAULT_SEED,
) -> CandidateBuild:
    if per_stratum < 1 or per_stratum > 100:
        raise CandidateError("per_stratum must be between 1 and 100")
    if not seed.strip():
        raise CandidateError("seed must be non-blank")

    ordered_records = sorted(
        source_export.records,
        key=lambda item: (*item.identity, item.source_content_fingerprint),
    )
    unique_records: list[SourceRecord] = []
    seen: set[tuple[str, ...]] = set()
    duplicate_count = 0
    for record in ordered_records:
        if record.identity in seen:
            duplicate_count += 1
            continue
        seen.add(record.identity)
        unique_records.append(record)

    by_stratum: dict[str, list[SourceRecord]] = {
        stratum: [] for stratum in STRATUM_ORDER
    }
    unmatched_count = 0
    for record in unique_records:
        stratum = _record_stratum(record)
        if stratum is None:
            unmatched_count += 1
            continue
        by_stratum[stratum].append(record)

    redaction_totals = {
        category: 0
        for category in ("SECRET", "URL", "EMAIL", "JIRA", "PERSON", "REFERENCE")
    }
    candidates: list[dict[str, object]] = []
    strata_summary: list[dict[str, object]] = []
    insufficiencies: list[dict[str, object]] = []
    for stratum in STRATUM_ORDER:
        available = sorted(
            by_stratum[stratum],
            key=lambda record: (_selection_key(seed, stratum, record), record.identity),
        )
        selected = available[:per_stratum]
        strata_summary.append(
            {
                "stratum": stratum,
                "description": STRATUM_DESCRIPTIONS[stratum],
                "required": per_stratum,
                "available": len(available),
                "selected": len(selected),
            }
        )
        if len(selected) < per_stratum:
            insufficiencies.append(
                {
                    "code": "INSUFFICIENT_STRATUM",
                    "stratum": stratum,
                    "required": per_stratum,
                    "available": len(available),
                    "missing": per_stratum - len(selected),
                }
            )
        for record in selected:
            candidate, counts = _candidate_dict(record, stratum)
            candidates.append(candidate)
            for category, count in counts.items():
                redaction_totals[category] += count

    candidates.sort(
        key=lambda item: (
            STRATUM_ORDER.index(cast(str, item["stratum"])),
            cast(str, item["candidateId"]),
        )
    )
    artifact: dict[str, object] = {
        "artifactKind": "KNOWLEDGE_GOLDEN_REVIEW_CANDIDATE",
        "schemaVersion": "1.0.0",
        "synthetic": False,
        "datasetId": f"candidate_{source_export.input_fingerprint.removeprefix('sha256:')[:24]}",
        "reviewState": "UNLABELED",
        "certification": {
            "releaseEligible": False,
            "status": "NOT_CERTIFIED",
            "reason": "HUMAN_REVIEW_AND_RESULT_CAPTURE_REQUIRED",
        },
        "source": {
            "kind": "POSTGRESQL_EXPORT",
            "exportedAt": source_export.exported_at,
            "databaseFingerprint": source_export.database_fingerprint,
            "queryFingerprint": source_export.query_fingerprint,
            "inputFingerprint": source_export.input_fingerprint,
        },
        "sampling": {
            "algorithm": "sha256-stratified-v1",
            "seedFingerprint": _text_fingerprint(seed),
            "perStratum": per_stratum,
            "strata": strata_summary,
        },
        "privacy": {
            "policy": "knowledge-golden-redaction-v1",
            "rawTextIncluded": False,
            "personIdentifiersIncluded": False,
            "redactionCounts": redaction_totals,
        },
        "summary": {
            "inputRecords": len(source_export.records),
            "uniqueRecords": len(unique_records),
            "selectedCandidates": len(candidates),
            "duplicateRecords": duplicate_count,
            "unmatchedRecords": unmatched_count,
            "insufficiencyCount": len(insufficiencies),
            "readyForHumanReview": bool(candidates),
            "completeStratifiedCandidate": not insufficiencies,
        },
        "insufficiencies": insufficiencies,
        "reviewInstructions": {
            "requiredAction": "HUMAN_LABEL_RELEVANCE_AND_LIFECYCLE",
            "allowedStates": ["UNLABELED", "RELEVANT", "NOT_RELEVANT", "EXCLUDE"],
            "nextArtifact": "RESULT_SNAPSHOT_AFTER_INDEPENDENT_REVIEW_AND_SEARCH_CAPTURE",
        },
        "candidates": candidates,
    }
    return CandidateBuild(
        artifact=artifact,
        selected_count=len(candidates),
        input_count=len(source_export.records),
        duplicate_count=duplicate_count,
        unmatched_count=unmatched_count,
        insufficiency_count=len(insufficiencies),
        redaction_counts=redaction_totals,
    )


def redact_text(value: str, person_identifiers: Sequence[str] = ()) -> RedactedValue:
    redacted = value
    counts: dict[str, int] = {
        category: 0 for category in ("SECRET", "URL", "EMAIL", "JIRA", "PERSON")
    }
    for identifier in sorted(set(person_identifiers), key=lambda item: (-len(item), item)):
        if not identifier.strip():
            continue
        redacted, count = re.subn(
            re.escape(identifier),
            "[REDACTED_PERSON]",
            redacted,
            flags=re.IGNORECASE,
        )
        counts["PERSON"] += count
    for category, pattern, replacement in _REDACTION_PATTERNS:
        redacted, count = pattern.subn(replacement, redacted)
        counts[category] += count
    return RedactedValue(redacted, counts)


def render_preview(build: CandidateBuild, *, wrote_output: bool = False) -> str:
    redactions = ", ".join(
        f"{category}={count}"
        for category, count in build.redaction_counts.items()
    )
    source = build.artifact.get("source")
    input_fingerprint = (
        source.get("inputFingerprint")
        if isinstance(source, dict)
        else "unavailable"
    )
    raw_insufficiencies = build.artifact.get("insufficiencies")
    insufficient_strata = (
        [
            item.get("stratum")
            for item in raw_insufficiencies
            if isinstance(item, dict)
        ]
        if isinstance(raw_insufficiencies, list)
        else []
    )
    return "\n".join(
        (
            "Knowledge golden sample candidate preview",
            f"Input fingerprint: {input_fingerprint}",
            f"Input records: {build.input_count}",
            f"Selected candidates: {build.selected_count}",
            f"Duplicate records: {build.duplicate_count}",
            f"Unmatched records: {build.unmatched_count}",
            f"Insufficiencies: {build.insufficiency_count}",
            "Insufficient strata: "
            + (", ".join(cast(list[str], insufficient_strata)) or "none"),
            f"Redactions: {redactions}",
            "Review state: UNLABELED; release certification: NOT_CERTIFIED",
            (
                "Output written to the explicit external path."
                if wrote_output
                else "Preview only; no output file was written."
            ),
        )
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=EXPORT_CONTRACT_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="explicit normalized PostgreSQL JSON export",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write candidate JSON to a separate directory outside this repository",
    )
    parser.add_argument("--per-stratum", type=int, default=3)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--json", action="store_true", help="print the candidate JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        input_path = cast(Path, args.input)
        output_path = cast(Path | None, args.output)
        source_export = load_source_export(input_path)
        build = build_candidate(
            source_export,
            per_stratum=cast(int, args.per_stratum),
            seed=cast(str, args.seed),
        )
        machine_json = json.dumps(
            build.artifact, ensure_ascii=False, indent=2, sort_keys=True
        )
        if output_path is not None:
            _validate_output_path(input_path, output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(machine_json + "\n", encoding="utf-8")
    except (CandidateError, OSError, json.JSONDecodeError) as exc:
        print(f"knowledge golden candidate error: {exc}", file=sys.stderr)
        return 2
    if cast(bool, args.json):
        print(machine_json)
    else:
        print(render_preview(build, wrote_output=output_path is not None))
    return 0


def _parse_record(value: object, context: str) -> SourceRecord:
    payload = _as_object(value, context)
    _reject_unknown_keys(
        payload,
        {
            "sourceType",
            "sourceEntityId",
            "sourceRevisionId",
            "sourceContentFingerprint",
            "nodeId",
            "nodeType",
            "status",
            "label",
            "text",
            "recordedAt",
            "executionOutcome",
            "personIdentifiers",
        },
        context,
    )
    source_type = _required_choice(payload, "sourceType", SOURCE_TYPES, context)
    node_type = _required_choice(payload, "nodeType", NODE_TYPES, context)
    status = _required_choice(payload, "status", LIFECYCLE_STATUSES, context)
    execution_outcome = _optional_choice(
        payload, "executionOutcome", EXECUTION_OUTCOMES, context
    )
    text = _required_str(payload, "text", context)
    content_fingerprint = _required_fingerprint(
        payload, "sourceContentFingerprint", context
    )
    if _text_fingerprint(text) != content_fingerprint:
        raise CandidateError(f"{context}.sourceContentFingerprint does not match text")
    person_identifiers = tuple(
        _string_list(payload, "personIdentifiers", context)
    )
    return SourceRecord(
        source_type=source_type,
        source_entity_id=_required_opaque_reference(
            payload, "sourceEntityId", context
        ),
        source_revision_id=_required_opaque_reference(
            payload, "sourceRevisionId", context
        ),
        source_content_fingerprint=content_fingerprint,
        node_id=_required_opaque_reference(payload, "nodeId", context),
        node_type=node_type,
        status=status,
        label=_required_str(payload, "label", context),
        text=text,
        recorded_at=_required_datetime(payload, "recordedAt", context),
        execution_outcome=execution_outcome,
        person_identifiers=person_identifiers,
    )


def _record_stratum(record: SourceRecord) -> str | None:
    if record.source_type == "SLACK_MESSAGE_REVISION" and record.node_type == "DECISION":
        if record.status == "ACTIVE":
            return "SLACK_ACTIVE_DECISION"
        return f"EXCLUSION_{record.status}"
    if record.status != "ACTIVE":
        return None
    if record.source_type == "TEST_CASE_VERSION" and record.node_type == "TEST_CASE":
        return "EXISTING_TEST_CASE"
    if (
        record.source_type == "EXECUTION_ATTEMPT"
        and record.node_type == "EXECUTION"
        and record.execution_outcome == "FAIL"
    ):
        return "FAILED_EXECUTION"
    if record.source_type == "ISSUE_SNAPSHOT" and record.node_type == "ISSUE":
        return "ISSUE"
    return None


def _candidate_dict(
    record: SourceRecord, stratum: str
) -> tuple[dict[str, object], Mapping[str, int]]:
    redacted_label = redact_text(record.label, record.person_identifiers)
    redacted_text = redact_text(record.text, record.person_identifiers)
    counts = {
        category: redacted_label.counts.get(category, 0)
        + redacted_text.counts.get(category, 0)
        for category in ("SECRET", "URL", "EMAIL", "JIRA", "PERSON")
    }
    safe_entity_id, entity_redacted = _safe_reference(record.source_entity_id)
    safe_revision_id, revision_redacted = _safe_reference(record.source_revision_id)
    safe_node_id, node_redacted = _safe_reference(record.node_id)
    counts["REFERENCE"] = sum(
        (entity_redacted, revision_redacted, node_redacted)
    )
    candidate_id = "candidate_" + _sha256(
        "\x1f".join((stratum, *record.identity)).encode("utf-8")
    )[:24]
    candidate: dict[str, object] = {
        "candidateId": candidate_id,
        "stratum": stratum,
        "stratumDescription": STRATUM_DESCRIPTIONS[stratum],
        "review": {
            "state": "UNLABELED",
            "relevance": None,
            "lifecycleExclusion": None,
            "reviewer": None,
            "reviewedAt": None,
            "notes": None,
        },
        "nodeId": safe_node_id,
        "nodeType": record.node_type,
        "status": record.status,
        "executionOutcome": (
            WIRE_EXECUTION_OUTCOMES[record.execution_outcome]
            if record.execution_outcome is not None
            else None
        ),
        "label": redacted_label.value,
        "text": redacted_text.value,
        "recordedAt": record.recorded_at,
        "source": {
            "sourceType": record.source_type,
            "sourceEntityId": safe_entity_id,
            "sourceRevisionId": safe_revision_id,
        },
        "fingerprints": {
            "sourceContent": record.source_content_fingerprint,
        },
        "privacy": {
            "redactionCounts": counts,
            "rawTextIncluded": False,
            "personIdentifiersIncluded": False,
        },
    }
    candidate["fingerprints"] = {
        "sourceContent": record.source_content_fingerprint,
        "candidate": _fingerprint(candidate),
    }
    return candidate, counts


def _selection_key(seed: str, stratum: str, record: SourceRecord) -> str:
    return _sha256(
        "\x1f".join((seed, stratum, *record.identity)).encode("utf-8")
    )


def _safe_reference(value: str) -> tuple[str, int]:
    redacted = redact_text(value)
    if (
        OPAQUE_REFERENCE_PATTERN.fullmatch(value)
        and not JIRA_REFERENCE_PATTERN.search(value)
        and "@" not in value
        and not any(redacted.counts.values())
    ):
        return value, 0
    return f"ref_sha256_{_sha256(value.encode('utf-8'))[:32]}", 1


def _validate_output_path(input_path: Path, output_path: Path) -> None:
    resolved_input = input_path.resolve()
    resolved_output = output_path.resolve()
    if resolved_output == resolved_input:
        raise CandidateError("output must not overwrite the source export")
    if resolved_output.parent == resolved_input.parent:
        raise CandidateError("output must use a directory separate from the source export")
    if resolved_output == ROOT or ROOT in resolved_output.parents:
        raise CandidateError("output must be outside the repository checkout")
    if output_path.exists():
        raise CandidateError("output already exists; choose a new explicit path")


def _load_object(path: Path) -> dict[str, object]:
    value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    return _as_object(value, "export")


def _as_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CandidateError(f"{context} must be a JSON object")
    return cast(dict[str, object], value)


def _reject_unknown_keys(
    payload: Mapping[str, object], allowed: set[str], context: str
) -> None:
    unknown = set(payload).difference(allowed)
    if unknown:
        raise CandidateError(f"{context} has unsupported fields: {sorted(unknown)}")


def _required_object(
    payload: Mapping[str, object], key: str, context: str
) -> dict[str, object]:
    if key not in payload:
        raise CandidateError(f"{context}.{key} is required")
    return _as_object(payload[key], f"{context}.{key}")


def _required_list(
    payload: Mapping[str, object], key: str, context: str, *, nonempty: bool = False
) -> list[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise CandidateError(f"{context}.{key} must be an array")
    if nonempty and not value:
        raise CandidateError(f"{context}.{key} must not be empty")
    return cast(list[object], value)


def _string_list(
    payload: Mapping[str, object], key: str, context: str
) -> list[str]:
    values = _required_list(payload, key, context)
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise CandidateError(f"{context}.{key} must contain non-blank strings")
    result = cast(list[str], values)
    if len(result) != len(set(result)):
        raise CandidateError(f"{context}.{key} must contain unique values")
    return result


def _required_str(payload: Mapping[str, object], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CandidateError(f"{context}.{key} must be a non-blank string")
    return value


def _required_opaque_reference(
    payload: Mapping[str, object], key: str, context: str
) -> str:
    value = _required_str(payload, key, context)
    if len(value) > 2048:
        raise CandidateError(f"{context}.{key} is too long")
    return value


def _required_datetime(
    payload: Mapping[str, object], key: str, context: str
) -> str:
    value = _required_str(payload, key, context)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateError(f"{context}.{key} must be an ISO-8601 date-time") from exc
    if parsed.tzinfo is None:
        raise CandidateError(f"{context}.{key} must include a timezone")
    return value


def _required_choice(
    payload: Mapping[str, object],
    key: str,
    choices: Sequence[str],
    context: str,
) -> str:
    value = _required_str(payload, key, context)
    if value not in choices:
        raise CandidateError(f"{context}.{key} is unsupported")
    return value


def _optional_choice(
    payload: Mapping[str, object],
    key: str,
    choices: Sequence[str],
    context: str,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value not in choices:
        raise CandidateError(f"{context}.{key} is unsupported")
    return value


def _required_fingerprint(
    payload: Mapping[str, object], key: str, context: str
) -> str:
    value = _required_str(payload, key, context)
    if not FINGERPRINT_PATTERN.fullmatch(value):
        raise CandidateError(f"{context}.{key} must be a sha256 fingerprint")
    return value


def _expect(
    payload: Mapping[str, object], key: str, expected: object, context: str
) -> None:
    if payload.get(key) != expected:
        raise CandidateError(f"{context}.{key} must equal {expected!r}")


def _fingerprint(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return f"sha256:{_sha256(canonical)}"


def _text_fingerprint(value: str) -> str:
    return f"sha256:{_sha256(value.encode('utf-8'))}"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
