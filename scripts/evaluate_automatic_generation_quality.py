#!/usr/bin/env python3
"""Offline CLI for the PE-SE2-T08 automatic-generation quality gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree

from tc_backend.generation.quality_gate import (
    CLASSIFICATIONS,
    CONTRACT_VERSION,
    QualityGateInputError,
    QualityGateOutcome,
    QualityGateReportError,
    evaluate_quality_snapshot,
    load_schema,
    loads_strict_json,
    validate_report,
)

MAX_INPUT_BYTES = 64 * 1024 * 1024


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Deterministic offline automatic-generation quality evaluator"
    )
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", type=Path)
    mode.add_argument("--validate-report", type=Path)
    result.add_argument("--profile", choices=("candidate", "release"))
    result.add_argument("--json-output", type=Path)
    result.add_argument("--junit-output", type=Path)
    result.add_argument("--markdown-output", type=Path)
    result.add_argument("--expected-sha256")
    result.add_argument("--expected-snapshot-sha256")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.validate_report is not None:
            return _validate_cached_report(arguments)
        return _evaluate(arguments)
    except (QualityGateInputError, QualityGateReportError, OSError, UnicodeError) as error:
        print(f"automatic-generation quality gate input error: {_safe_code(error)}", file=sys.stderr)
        return 2
    except Exception as error:  # pragma: no cover - final fail-closed CLI boundary
        print(
            f"automatic-generation quality gate safety error: {type(error).__name__}",
            file=sys.stderr,
        )
        return 2


def _evaluate(arguments: argparse.Namespace) -> int:
    required = {
        "profile": arguments.profile,
        "json-output": arguments.json_output,
        "junit-output": arguments.junit_output,
        "markdown-output": arguments.markdown_output,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise QualityGateInputError(f"MISSING_ARGUMENT:{missing[0]}")
    if arguments.expected_sha256 is not None:
        raise QualityGateInputError("EXPECTED_SHA256_ONLY_VALID_FOR_REPORT_VALIDATION")

    snapshot_path = cast(Path, arguments.snapshot)
    outputs = {
        "json": cast(Path, arguments.json_output),
        "junit": cast(Path, arguments.junit_output),
        "markdown": cast(Path, arguments.markdown_output),
    }
    requested_profile = cast(str, arguments.profile).upper()
    if requested_profile == "RELEASE" and arguments.expected_snapshot_sha256 is None:
        raise QualityGateInputError("RELEASE_REQUIRES_TRUSTED_SNAPSHOT_SHA256")
    _preflight_paths(snapshot_path, outputs)
    expected_snapshot_sha256 = arguments.expected_snapshot_sha256
    if expected_snapshot_sha256 is not None:
        if not _is_sha256_hex(expected_snapshot_sha256):
            raise QualityGateInputError("EXPECTED_SNAPSHOT_SHA256_INVALID")
        if hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != expected_snapshot_sha256:
            raise QualityGateInputError("SNAPSHOT_ARTIFACT_HASH_MISMATCH")
    snapshot = _load_json_object(snapshot_path)
    if snapshot.get("profile") != requested_profile:
        raise QualityGateInputError("PROFILE_SNAPSHOT_MISMATCH")
    declared_outputs = cast(Mapping[str, Any], snapshot.get("reportOutputs", {}))
    for name, path in outputs.items():
        if declared_outputs.get(name) != str(path):
            raise QualityGateInputError(f"REPORT_OUTPUT_PATH_MISMATCH:{name}")

    artifact_names = {name: str(path) for name, path in outputs.items()}
    outcome = evaluate_quality_snapshot(
        snapshot,
        artifacts=artifact_names,
        schema=load_schema(),
    )
    rendered = {
        outputs["json"]: json.dumps(
            outcome.report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        outputs["junit"]: render_junit(outcome, snapshot),
        outputs["markdown"]: render_markdown(outcome, snapshot),
    }
    _write_fresh_outputs(rendered)
    print(
        "automatic-generation quality gate "
        f"{'PASS' if outcome.passed else 'FAIL'} "
        f"profile={requested_profile.lower()} "
        f"releaseCertified={str(outcome.report['certification']['releaseCertified']).lower()}"
    )
    return outcome.exit_code


def _validate_cached_report(arguments: argparse.Namespace) -> int:
    forbidden = (
        arguments.profile,
        arguments.json_output,
        arguments.junit_output,
        arguments.markdown_output,
        arguments.expected_snapshot_sha256,
    )
    if any(value is not None for value in forbidden):
        raise QualityGateInputError("REPORT_VALIDATION_ARGUMENT_CONFLICT")
    report_path = cast(Path, arguments.validate_report)
    report = _load_json_object(report_path)
    validate_report(report, schema=load_schema())
    expected = arguments.expected_sha256
    if expected is not None:
        if not isinstance(expected, str) or not _is_sha256_hex(expected):
            raise QualityGateInputError("EXPECTED_SHA256_INVALID")
        actual = hashlib.sha256(report_path.read_bytes()).hexdigest()
        if actual != expected:
            raise QualityGateReportError("REPORT_ARTIFACT_HASH_MISMATCH")
    print("automatic-generation quality report schema validation PASS")
    return 0


def render_junit(
    outcome: QualityGateOutcome, snapshot: Mapping[str, Any]
) -> str:
    """Render stable, text-free JUnit checks with no skipped-as-pass behavior."""

    cases: list[ElementTree.Element] = []
    checks = cast(Sequence[Mapping[str, Any]], outcome.report["checks"])
    for check in checks[:7]:
        case = ElementTree.Element(
            "testcase",
            classname="quality-threshold",
            name=cast(str, check["code"]),
        )
        if check["passed"] is not True:
            _failure(
                case,
                cast(str, check["code"]),
                count=cast(int | float, check["actual"]),
            )
        cases.append(case)

    eligibility_groups = {
        "dataset-counts": ("DATASET_COUNT_MISMATCH",),
        "dataset-distribution": ("DATASET_DISTRIBUTION_MISMATCH",),
        "offline-privacy": (
            "OFFLINE_REPLAY_NOT_SEALED",
            "EVALUATION_EXTERNAL_CALL_OBSERVED",
            "DEGRADED_CAPTURE",
            "SECRETS_SCAN_NOT_CLEAN",
            "PII_SCAN_NOT_CLEAN",
            "RAW_SOURCE_TEXT_PRESENT",
        ),
        "operational-release-eligibility": tuple(
            code
            for code in outcome.eligibility_reason_codes
            if code
            not in {
                "DATASET_COUNT_MISMATCH",
                "DATASET_DISTRIBUTION_MISMATCH",
                "OFFLINE_REPLAY_NOT_SEALED",
                "EVALUATION_EXTERNAL_CALL_OBSERVED",
                "DEGRADED_CAPTURE",
                "SECRETS_SCAN_NOT_CLEAN",
                "PII_SCAN_NOT_CLEAN",
                "RAW_SOURCE_TEXT_PRESENT",
            }
        ),
    }
    for name, codes in eligibility_groups.items():
        present = [code for code in codes if code in outcome.eligibility_reason_codes]
        case = ElementTree.Element("testcase", classname="global-eligibility", name=name)
        if present:
            _failure(case, present[0], count=len(present))
        cases.append(case)

    for root in outcome.roots:
        for name, root_defects in (
            ("provenance", root.provenance_failures),
            ("result", root.result_failures),
            ("ordering", root.ordering_failures),
        ):
            case = ElementTree.Element(
                "testcase",
                classname=f"root-{name}",
                name=f"{root.generation_root_id}:{name}",
            )
            if root_defects:
                _failure(
                    case,
                    root_defects[0],
                    count=len(root_defects),
                    root=root.generation_root_id,
                    job=root.generation_job_id,
                )
            cases.append(case)

    dataset = cast(Mapping[str, Any], snapshot["dataset"])
    declared = cast(Mapping[str, int], dataset["classificationDistribution"])
    actual = {
        classification: sum(
            root["primaryClassification"] == classification
            for root in cast(Sequence[Mapping[str, Any]], snapshot["generationRoots"])
        )
        for classification in CLASSIFICATIONS
    }
    release = snapshot["profile"] == "RELEASE"
    for classification in CLASSIFICATIONS:
        case = ElementTree.Element(
            "testcase",
            classname="classification-distribution",
            name=classification,
        )
        valid = declared[classification] == actual[classification]
        if release:
            valid = valid and actual[classification] >= 2
        if not valid:
            _failure(
                case,
                "CLASSIFICATION_DISTRIBUTION_FAILURE",
                count=actual[classification],
            )
        cases.append(case)

    failure_count = sum(bool(case.findall("failure")) for case in cases)
    suite = ElementTree.Element(
        "testsuite",
        name=CONTRACT_VERSION,
        tests=str(len(cases)),
        failures=str(failure_count),
        errors="0",
        skipped="0",
    )
    for case in cases:
        suite.append(case)
    ElementTree.indent(suite, space="  ")
    return ElementTree.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"


def render_markdown(
    outcome: QualityGateOutcome, snapshot: Mapping[str, Any]
) -> str:
    """Render a hash/ID/count-only human summary."""

    report = outcome.report
    metrics = cast(Mapping[str, Any], report["metrics"])
    coverage = cast(Mapping[str, Any], metrics["evidenceCoverage"])
    duplicates = cast(Mapping[str, Any], metrics["duplicates"])
    retired = cast(Mapping[str, Any], metrics["retiredKnowledgeMisuse"])
    certification = cast(Mapping[str, Any], report["certification"])
    pins = cast(Mapping[str, Any], report["versionPins"])
    counts = cast(Mapping[str, Any], report["evaluatedCounts"])
    reasons = cast(Sequence[str], certification["reasonCodes"])
    lines = [
        "# Automatic generation quality gate",
        "",
        f"- Gate: **{'PASS' if outcome.passed else 'FAIL'}**",
        f"- Dataset: `{report['datasetKind']}` (`{report['datasetId']}`)",
        f"- Synthetic: `{str(report['synthetic']).lower()}`",
        f"- Release certified: `{str(certification['releaseCertified']).lower()}`",
        f"- Certification status: `{certification['status']}`",
        f"- Dataset fingerprint: `{report['datasetFingerprint']}`",
        f"- Snapshot fingerprint: `{report['snapshotFingerprint']}`",
        "",
        "## 100/0/0 metrics",
        "",
        f"- Evidence coverage: `{coverage['numerator']}/{coverage['denominator']}` (`{coverage['rate']}`)",
        f"- Retired knowledge misuse: `{retired['total']}`",
        f"- Duplicate total: `{duplicates['total']}`",
        f"- Unresolved authority: `{metrics['unresolvedAuthorityCount']}`",
        f"- Provenance failures: `{metrics['provenanceFailureCount']}`",
        f"- Invalid/blank results: `{metrics['invalidOrBlankResultCount']}`",
        f"- Ordering failures: `{metrics['orderingFailureCount']}`",
        "",
        "## Evaluated counts",
        "",
    ]
    lines.extend(f"- {name}: `{counts[name]}`" for name in sorted(counts))
    lines.extend(
        [
            "",
            "## Sealed duplicate policy",
            "",
            f"- Exact algorithm: `{pins['exactDuplicateAlgorithmVersion']}`",
            f"- Semantic algorithm: `{pins['semanticDuplicateAlgorithmVersion']}`",
            f"- Semantic model: `{pins['embeddingProviderId']}:{pins['embeddingModelId']}:{pins['embeddingRevision']}`",
            f"- Dimension: `{pins['embeddingDimensions']}`",
            f"- Threshold: `{pins['semanticDuplicateThreshold']}`",
            "",
            "## Stable reason codes",
            "",
        ]
    )
    lines.extend(f"- `{reason}`" for reason in reasons)
    if not reasons:
        lines.append("- `NONE`")
    return "\n".join(lines) + "\n"


def _failure(
    case: ElementTree.Element,
    code: str,
    *,
    count: int | float,
    root: str | None = None,
    job: str | None = None,
) -> None:
    identifiers = ""
    if root is not None and job is not None:
        identifiers = f" root={root} job={job}"
    ElementTree.SubElement(
        case,
        "failure",
        message=f"{_stable_code(code)}{identifiers} count={count}",
        type="quality-gate-failure",
    )


def _preflight_paths(snapshot: Path, outputs: Mapping[str, Path]) -> None:
    if not snapshot.is_file() or snapshot.is_symlink():
        raise QualityGateInputError("SNAPSHOT_FILE_MISSING_OR_UNSAFE")
    resolved_snapshot = snapshot.resolve(strict=True)
    resolved_outputs: list[Path] = []
    for name, path in outputs.items():
        if path.exists() or path.is_symlink():
            raise QualityGateInputError(f"CACHED_REPORT_REFUSED:{name}")
        if not path.parent.is_dir():
            raise QualityGateInputError(f"OUTPUT_PARENT_MISSING:{name}")
        resolved = path.resolve(strict=False)
        if resolved == resolved_snapshot:
            raise QualityGateInputError(f"SNAPSHOT_OVERWRITE_REFUSED:{name}")
        resolved_outputs.append(resolved)
    if len(resolved_outputs) != len(set(resolved_outputs)):
        raise QualityGateInputError("REPORT_OUTPUT_PATH_COLLISION")


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise QualityGateInputError("JSON_FILE_MISSING_OR_UNSAFE")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise QualityGateInputError("JSON_INPUT_TOO_LARGE")
    value = loads_strict_json(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise QualityGateInputError("JSON_ROOT_MUST_BE_OBJECT")
    return cast(dict[str, Any], value)


def _write_fresh_outputs(rendered: Mapping[Path, str]) -> None:
    created: list[Path] = []
    try:
        for path, text in rendered.items():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def _safe_code(error: Exception) -> str:
    return _stable_code(str(error).split(":", maxsplit=1)[0])


def _stable_code(value: str) -> str:
    cleaned = "".join(character for character in value if character.isalnum() or character in "._-")
    return cleaned[:160] or "UNKNOWN_ERROR"


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


if __name__ == "__main__":
    raise SystemExit(main())
