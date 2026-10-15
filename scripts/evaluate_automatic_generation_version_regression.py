"""Deterministic, offline PE-SE2-T09 old/new golden comparator CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tc_backend.generation.version_regression import (
    VersionRegressionInputError,
    VersionRegressionReportError,
    evaluate_version_regression,
    read_json_object,
    validate_report,
)

MAX_INPUT_BYTES = 64 * 1024 * 1024


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", type=Path)
    mode.add_argument("--validate-report", type=Path)
    result.add_argument("--profile", choices=("candidate", "production_approval"))
    result.add_argument("--expected-snapshot-sha256")
    result.add_argument("--expected-sha256")
    result.add_argument("--json-output", type=Path)
    result.add_argument("--junit-output", type=Path)
    result.add_argument("--markdown-output", type=Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.validate_report is not None:
            if any(
                value is not None
                for value in (
                    arguments.profile,
                    arguments.expected_snapshot_sha256,
                    arguments.json_output,
                    arguments.junit_output,
                    arguments.markdown_output,
                )
            ):
                raise VersionRegressionInputError("REPORT_VALIDATION_ARGUMENT_CONFLICT")
            _preflight_input(arguments.validate_report, "REPORT")
            report = read_json_object(arguments.validate_report)
            validate_report(report)
            if arguments.expected_sha256 is not None:
                _require_sha256(arguments.expected_sha256, "EXPECTED_SHA256_INVALID")
                if hashlib.sha256(arguments.validate_report.read_bytes()).hexdigest() != arguments.expected_sha256:
                    raise VersionRegressionReportError("REPORT_ARTIFACT_HASH_MISMATCH")
            return 1 if report["candidateDisposition"] == "REJECTED" else 0
        if arguments.expected_sha256 is not None:
            raise VersionRegressionInputError("EXPECTED_SHA256_ONLY_VALID_FOR_REPORT_VALIDATION")
        values = (arguments.snapshot, arguments.profile, arguments.json_output, arguments.junit_output, arguments.markdown_output)
        if any(value is None for value in values):
            raise VersionRegressionInputError("REQUIRED_ARGUMENT_MISSING")
        snapshot = arguments.snapshot
        assert snapshot is not None
        outputs = {
            "json": arguments.json_output,
            "junit": arguments.junit_output,
            "markdown": arguments.markdown_output,
        }
        assert all(isinstance(path, Path) for path in outputs.values())
        typed_outputs = {name: path for name, path in outputs.items() if isinstance(path, Path)}
        _preflight_paths(snapshot, typed_outputs)
        expected = arguments.expected_snapshot_sha256
        if arguments.profile == "production_approval" and not expected:
            raise VersionRegressionInputError("PRODUCTION_REQUIRES_TRUSTED_SNAPSHOT_SHA256")
        if expected is not None:
            _require_sha256(expected, "EXPECTED_SNAPSHOT_SHA256_INVALID")
        actual = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        if expected is not None and actual != expected:
            raise VersionRegressionInputError("SNAPSHOT_SHA256_MISMATCH")
        document = read_json_object(snapshot)
        if document.get("profile") != arguments.profile.upper():
            raise VersionRegressionInputError("PROFILE_MISMATCH")
        declared_outputs = document.get("reportOutputs")
        if not isinstance(declared_outputs, Mapping):
            raise VersionRegressionInputError("REPORT_OUTPUTS_INVALID")
        for name, path in typed_outputs.items():
            if declared_outputs.get(name) != str(path):
                raise VersionRegressionInputError(f"REPORT_OUTPUT_PATH_MISMATCH:{name}")
        outcome = evaluate_version_regression(
            document,
            artifacts={name: str(path) for name, path in typed_outputs.items()},
        )
        rendered = {
            typed_outputs["json"]: _json(outcome.report),
            typed_outputs["junit"]: _junit(outcome.report),
            typed_outputs["markdown"]: _markdown(outcome.report),
        }
        _write_fresh_outputs(rendered)
        return outcome.exit_code
    except (OSError, UnicodeError, ValueError, VersionRegressionInputError, VersionRegressionReportError) as error:
        print(f"automatic-generation version regression error: {_safe_code(error)}", file=sys.stderr)
        return 2
    except Exception as error:  # pragma: no cover - final fail-closed boundary
        print(
            "automatic-generation version regression safety error: "
            f"{type(error).__name__.upper()}",
            file=sys.stderr,
        )
        return 2


def _preflight_input(path: Path, kind: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise VersionRegressionInputError(f"{kind}_FILE_MISSING_OR_UNSAFE")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise VersionRegressionInputError(f"{kind}_FILE_TOO_LARGE")


def _preflight_paths(snapshot: Path, outputs: Mapping[str, Path]) -> None:
    _preflight_input(snapshot, "SNAPSHOT")
    resolved_snapshot = snapshot.resolve(strict=True)
    expected_suffix = {"json": ".json", "junit": ".xml", "markdown": ".md"}
    resolved_outputs: list[Path] = []
    for name, path in outputs.items():
        if path.suffix != expected_suffix[name]:
            raise VersionRegressionInputError(f"OUTPUT_EXTENSION_INVALID:{name}")
        if path.exists() or path.is_symlink():
            raise VersionRegressionInputError(f"CACHED_REPORT_REFUSED:{name}")
        if not path.parent.is_dir():
            raise VersionRegressionInputError(f"OUTPUT_PARENT_MISSING:{name}")
        resolved = path.resolve(strict=False)
        if resolved == resolved_snapshot:
            raise VersionRegressionInputError(f"SNAPSHOT_OVERWRITE_REFUSED:{name}")
        resolved_outputs.append(resolved)
    if len(resolved_outputs) != len(set(resolved_outputs)):
        raise VersionRegressionInputError("REPORT_OUTPUT_PATH_COLLISION")


def _write_fresh_outputs(rendered: Mapping[Path, str]) -> None:
    created: list[Path] = []
    try:
        for path, content in rendered.items():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def _require_sha256(value: str, code: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise VersionRegressionInputError(code)


def _json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _junit(report: Mapping[str, Any]) -> str:
    checks = report["checks"]
    failures = sum(1 for check in checks if not check["passed"])
    cases = "".join(
        f'<testcase classname="version-regression" name="{check["code"]}">' +
        (f'<failure message="{check["code"]}" />' if not check["passed"] else "") +
        "</testcase>" for check in checks
    )
    return f'<testsuite name="automatic-generation-version-regression/1.0.0" tests="{len(checks)}" failures="{failures}">{cases}</testsuite>\n'


def _markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        (
            "# Automatic generation version regression",
            "",
            f"- Profile: `{report['profile']}`",
            f"- Synthetic: `{str(report['synthetic']).lower()}`",
            f"- Disposition: `{report['candidateDisposition']}`",
            f"- Cohort: `{report['cohortId']}`",
            f"- Identity: `{report['oldBaselineIdentityDigest']}` → `{report['newBaselineIdentityDigest']}`",
            f"- Reasons: `{','.join(report['reasonCodes'])}`",
        )
    ) + "\n"


def _safe_code(error: Exception) -> str:
    value = str(error).split(":", 1)[0].strip().upper()
    cleaned = "".join(character for character in value if character.isalnum() or character in "._-")
    return cleaned[:160] or error.__class__.__name__.upper()


if __name__ == "__main__":
    raise SystemExit(main())
