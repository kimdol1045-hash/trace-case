#!/usr/bin/env python3
"""Revalidate, hash, and PII/secret-scan PE-SE2-T08 report artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree

from tc_backend.generation.quality_gate import (
    CLASSIFICATIONS,
    CONTRACT_VERSION,
    QualityGateInputError,
    QualityGateReportError,
    evaluate_quality_snapshot,
    load_schema,
    loads_strict_json,
    validate_report,
)

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:authorization|api[_-]?token|api[_-]?key|client[_-]?secret)\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_PII_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b"),
)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        paths = {
            "json": cast(Path, arguments.json_report),
            "junit": cast(Path, arguments.junit_report),
            "markdown": cast(Path, arguments.markdown_report),
        }
        snapshot_path = cast(Path, arguments.snapshot)
        if not snapshot_path.is_file() or snapshot_path.is_symlink():
            raise QualityGateInputError("SNAPSHOT_MISSING_OR_UNSAFE")
        for name, path in paths.items():
            if not path.is_file() or path.is_symlink():
                raise QualityGateInputError(f"ARTIFACT_MISSING_OR_UNSAFE:{name}")
        manifest_path = cast(Path, arguments.manifest_output)
        if manifest_path.exists() or manifest_path.is_symlink():
            raise QualityGateInputError("CACHED_ARTIFACT_MANIFEST_REFUSED")
        if not manifest_path.parent.is_dir():
            raise QualityGateInputError("MANIFEST_PARENT_MISSING")

        expected_snapshot_sha256 = cast(str, arguments.expected_snapshot_sha256)
        if not _is_sha256_hex(expected_snapshot_sha256):
            raise QualityGateInputError("EXPECTED_SNAPSHOT_SHA256_INVALID")
        snapshot_bytes = snapshot_path.read_bytes()
        if hashlib.sha256(snapshot_bytes).hexdigest() != expected_snapshot_sha256:
            raise QualityGateInputError("SNAPSHOT_ARTIFACT_HASH_MISMATCH")
        snapshot_value = loads_strict_json(snapshot_bytes.decode("utf-8"))
        if not isinstance(snapshot_value, dict):
            raise QualityGateInputError("SNAPSHOT_ROOT_MUST_BE_OBJECT")

        report_value = loads_strict_json(paths["json"].read_text(encoding="utf-8"))
        if not isinstance(report_value, dict):
            raise QualityGateReportError("REPORT_ROOT_MUST_BE_OBJECT")
        report = cast(dict[str, Any], report_value)
        schema = load_schema()
        validate_report(report, schema=schema)
        artifact_names = {name: str(path) for name, path in paths.items()}
        outcome = evaluate_quality_snapshot(
            cast(dict[str, Any], snapshot_value), artifacts=artifact_names, schema=schema
        )
        if outcome.report != report:
            raise QualityGateReportError("REPORT_SNAPSHOT_REPLAY_MISMATCH")
        if paths["markdown"].read_text(encoding="utf-8") != _render_markdown(report):
            raise QualityGateReportError("MARKDOWN_ARTIFACT_TAMPERED")
        _verify_junit(paths["junit"], report)
        scan_paths = {**paths, "snapshot": snapshot_path}
        _scan(scan_paths)

        artifacts = {
            name: {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
            for name, path in sorted(paths.items())
        }
        artifacts["snapshot"] = {
            "path": str(snapshot_path),
            "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
            "bytes": len(snapshot_bytes),
        }
        manifest = {
            "contractVersion": CONTRACT_VERSION,
            "snapshotFingerprint": report["snapshotFingerprint"],
            "artifacts": artifacts,
            "scans": {"secrets": "CLEAN", "pii": "CLEAN"},
        }
        descriptor = os.open(
            manifest_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        print("automatic-generation artifact hash and PII scan PASS")
        return 0
    except (QualityGateInputError, QualityGateReportError, OSError, UnicodeError) as error:
        code = "".join(
            character
            for character in str(error).split(":", maxsplit=1)[0]
            if character.isalnum() or character in "._-"
        )
        print(f"automatic-generation artifact verification error: {code}", file=sys.stderr)
        return 2
    except Exception as error:  # pragma: no cover - final fail-closed boundary
        print(
            f"automatic-generation artifact verification safety error: {type(error).__name__}",
            file=sys.stderr,
        )
        return 2


def _parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Verify quality-gate report artifacts")
    result.add_argument("--json-report", type=Path, required=True)
    result.add_argument("--junit-report", type=Path, required=True)
    result.add_argument("--markdown-report", type=Path, required=True)
    result.add_argument("--manifest-output", type=Path, required=True)
    result.add_argument("--snapshot", type=Path, required=True)
    result.add_argument("--expected-snapshot-sha256", required=True)
    return result


def _verify_junit(path: Path, report: dict[str, Any]) -> None:
    try:
        suite = ElementTree.fromstring(path.read_text(encoding="utf-8"))
    except ElementTree.ParseError as error:
        raise QualityGateReportError("JUNIT_XML_INVALID") from error
    if suite.tag != "testsuite" or suite.attrib.get("name") != CONTRACT_VERSION:
        raise QualityGateReportError("JUNIT_SUITE_MISMATCH")
    cases = suite.findall("testcase")
    failures = sum(len(case.findall("failure")) for case in cases)
    if suite.findall(".//skipped") or suite.attrib.get("skipped") != "0":
        raise QualityGateReportError("JUNIT_SKIPPED_FORBIDDEN")
    if suite.attrib.get("tests") != str(len(cases)):
        raise QualityGateReportError("JUNIT_TEST_COUNT_MISMATCH")
    if suite.attrib.get("failures") != str(failures):
        raise QualityGateReportError("JUNIT_FAILURE_COUNT_MISMATCH")
    if suite.attrib.get("errors") != "0":
        raise QualityGateReportError("JUNIT_ERRORS_FORBIDDEN")
    expected_roots = cast(dict[str, int], report["evaluatedCounts"])["generationRoots"]
    expected_count = 7 + 4 + (3 * expected_roots) + len(CLASSIFICATIONS)
    if len(cases) != expected_count:
        raise QualityGateReportError("JUNIT_REQUIRED_CASE_SET_MISMATCH")
    class_counts: dict[str, int] = {}
    for case in cases:
        classname = case.attrib.get("classname", "")
        class_counts[classname] = class_counts.get(classname, 0) + 1
    expected_classes = {
        "quality-threshold": 7,
        "global-eligibility": 4,
        "root-provenance": expected_roots,
        "root-result": expected_roots,
        "root-ordering": expected_roots,
        "classification-distribution": len(CLASSIFICATIONS),
    }
    if class_counts != expected_classes:
        raise QualityGateReportError("JUNIT_REQUIRED_CLASS_SET_MISMATCH")
    gate_passed = cast(dict[str, Any], report["gate"])["passed"] is True
    if gate_passed != (failures == 0):
        raise QualityGateReportError("JUNIT_GATE_MISMATCH")


def _scan(paths: dict[str, Path]) -> None:
    for name, path in paths.items():
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
            raise QualityGateReportError(f"ARTIFACT_SECRET_SCAN_FAILED:{name}")
        if any(pattern.search(text) for pattern in _PII_PATTERNS):
            raise QualityGateReportError(f"ARTIFACT_PII_SCAN_FAILED:{name}")
        lowered = text.casefold()
        for forbidden in (
            '"sourcetext"',
            '"prompttext"',
            '"rawproviderbody"',
            '"rawresponse"',
            '"credential"',
            '"endpoint"',
            "query=",
        ):
            if forbidden in lowered:
                raise QualityGateReportError(f"ARTIFACT_FORBIDDEN_FIELD:{name}")


def _render_markdown(report: dict[str, Any]) -> str:
    metrics = cast(dict[str, Any], report["metrics"])
    coverage = cast(dict[str, Any], metrics["evidenceCoverage"])
    duplicates = cast(dict[str, Any], metrics["duplicates"])
    retired = cast(dict[str, Any], metrics["retiredKnowledgeMisuse"])
    certification = cast(dict[str, Any], report["certification"])
    pins = cast(dict[str, Any], report["versionPins"])
    counts = cast(dict[str, Any], report["evaluatedCounts"])
    reasons = cast(list[str], certification["reasonCodes"])
    lines = [
        "# Automatic generation quality gate",
        "",
        f"- Gate: **{'PASS' if cast(dict[str, Any], report['gate'])['passed'] else 'FAIL'}**",
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


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


if __name__ == "__main__":
    raise SystemExit(main())
