"""Deterministic, offline PE-SE2-T08 automatic-generation quality gate.

The module deliberately accepts a sealed JSON snapshot instead of a database
session or provider client.  Schema and fingerprint failures are input errors;
schema-valid quality and release-eligibility failures remain ordinary red gate
decisions.  Synthetic fixtures can exercise every evaluator branch but can
never produce a release certification.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, cast

CONTRACT_VERSION: Final = "automatic-generation-quality-gate/1.0.0"
EVALUATOR_VERSION: Final = "automatic-generation-quality-evaluator-v1"
SCHEMA_ID: Final = (
    "https://qa-tool.local/schemas/generation/quality-gate/1.0.0/schema.json"
)
CLASSIFICATIONS: Final = (
    "GROUNDED_ACTIVE",
    "SUPERSEDED_TRAP",
    "RETRACTED_TRAP",
    "DELETED_TRAP",
    "REJECTED_TRAP",
    "UNAVAILABLE_TRAP",
    "UNRESOLVED_CONFLICT",
    "DECISION_CANDIDATE",
    "EXACT_DUPLICATE_TRAP",
    "SEMANTIC_DUPLICATE_TRAP",
    "CATEGORY_COLLISION_TRAP",
    "PARENT_COLLISION_TRAP",
    "CASE_ORDERING",
    "BLANK_OR_INVALID_RESULT",
    "APPLY_PROVENANCE",
    "SUGGESTION_PROVENANCE",
)
RETIRED_STATUSES: Final = (
    "SUPERSEDED",
    "RETRACTED",
    "DELETED",
    "REJECTED",
    "UNAVAILABLE",
)
COUNT_KEYS: Final = (
    "generationRoots",
    "generationJobs",
    "generationResults",
    "generatedCases",
    "generatedSteps",
    "evidenceReferences",
)
MINIMUM_RELEASE_COUNTS: Final = {
    "generationRoots": 32,
    "generationJobs": 32,
    "generationResults": 32,
    "generatedCases": 160,
    "generatedSteps": 160,
    "evidenceReferences": 320,
}
_SCHEMA_PATH: Final = (
    Path(__file__).resolve().parents[4]
    / "packages/contracts/generation/quality-gate/1.0.0/schema.json"
)
_RESERVED_PIN_VALUES: Final = {"latest", "unknown", "unconfigured", "unpinned"}

JsonObject = dict[str, Any]


class QualityGateInputError(ValueError):
    """The snapshot, schema, fingerprint, or evaluator safety input is invalid."""


class QualityGateReportError(ValueError):
    """A generated or cached report does not satisfy the report contract."""


@dataclass(frozen=True, slots=True)
class RootEvaluation:
    """Stable root-scoped failure codes used by JUnit without source text."""

    generation_root_id: str
    generation_job_id: str
    provenance_failures: tuple[str, ...]
    result_failures: tuple[str, ...]
    ordering_failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualityGateOutcome:
    """Pure evaluation result and renderer details."""

    report: JsonObject
    roots: tuple[RootEvaluation, ...]
    eligibility_reason_codes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return bool(cast(Mapping[str, Any], self.report["gate"])["passed"])

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1


def load_schema() -> JsonObject:
    """Load the repository's normative 1.0.0 schema."""

    try:
        value = loads_strict_json(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, QualityGateInputError) as error:
        raise QualityGateInputError("QUALITY_GATE_SCHEMA_UNAVAILABLE") from error
    if not isinstance(value, dict) or value.get("$id") != SCHEMA_ID:
        raise QualityGateInputError("QUALITY_GATE_SCHEMA_ID_MISMATCH")
    return cast(JsonObject, value)


def loads_strict_json(text: str) -> Any:
    """Parse JSON while rejecting empty, duplicate-key, and NaN/Infinity input."""

    if not text.strip():
        raise QualityGateInputError("EMPTY_JSON_INPUT")

    def reject_constant(value: str) -> None:
        raise QualityGateInputError(f"NON_FINITE_JSON_NUMBER:{value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> JsonObject:
        output: JsonObject = {}
        for key, value in pairs:
            if key in output:
                raise QualityGateInputError(f"DUPLICATE_JSON_KEY:{key}")
            output[key] = value
        return output

    try:
        return json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except QualityGateInputError:
        raise
    except (json.JSONDecodeError, RecursionError) as error:
        raise QualityGateInputError("MALFORMED_JSON_INPUT") from error


def validate_input(snapshot: Mapping[str, Any], *, schema: Mapping[str, Any] | None = None) -> None:
    """Strictly validate shape, exact pins, and all three declared fingerprints."""

    document = schema or load_schema()
    _validate_schema_instance(snapshot, document, "EvaluationInput")
    _validate_exact_pins(cast(Mapping[str, Any], snapshot["versionPins"]))
    expected = compute_fingerprints(snapshot)
    dataset = cast(Mapping[str, Any], snapshot["dataset"])
    capture = cast(Mapping[str, Any], snapshot["capture"])
    declared = {
        "datasetFingerprint": dataset["datasetFingerprint"],
        "sourceSnapshotFingerprint": capture["sourceSnapshotFingerprint"],
        "snapshotFingerprint": dataset["snapshotFingerprint"],
    }
    for name, digest in expected.items():
        if declared[name] != digest:
            raise QualityGateInputError(f"FINGERPRINT_MISMATCH:{name}")


def validate_report(report: Mapping[str, Any], *, schema: Mapping[str, Any] | None = None) -> None:
    """Revalidate a report independently before it is treated as a CI artifact."""

    try:
        _validate_schema_instance(report, schema or load_schema(), "EvaluationReport")
    except QualityGateInputError as error:
        raise QualityGateReportError(str(error)) from error
    metrics = cast(Mapping[str, Any], report["metrics"])
    coverage = cast(Mapping[str, Any], metrics["evidenceCoverage"])
    numerator = cast(int, coverage["numerator"])
    denominator = cast(int, coverage["denominator"])
    expected_rate = 0 if denominator == 0 else numerator / denominator
    if coverage["rate"] != expected_rate:
        raise QualityGateReportError("REPORT_COVERAGE_ARITHMETIC_MISMATCH")
    duplicate = cast(Mapping[str, Any], metrics["duplicates"])
    duplicate_total = sum(
        cast(int, duplicate[name])
        for name in ("exactKey", "semanticCluster", "categoryCollision", "parentCollision")
    )
    if duplicate["total"] != duplicate_total:
        raise QualityGateReportError("REPORT_DUPLICATE_ARITHMETIC_MISMATCH")
    retired = cast(Mapping[str, Any], metrics["retiredKnowledgeMisuse"])
    retired_total = sum(cast(int, retired[name]) for name in RETIRED_STATUSES)
    if retired["total"] != retired_total:
        raise QualityGateReportError("REPORT_RETIRED_ARITHMETIC_MISMATCH")
    checks = cast(Sequence[Mapping[str, Any]], report["checks"])
    check_by_code = {cast(str, check["code"]): check for check in checks}
    if len(check_by_code) != len(checks):
        raise QualityGateReportError("REPORT_DUPLICATE_CHECK_CODE")
    expected_actuals: dict[str, int | float] = {
        "evidenceCoverage": cast(float, coverage["rate"]),
        "retiredKnowledgeMisuseCount": retired_total,
        "duplicateCount": duplicate_total,
        "unresolvedAuthorityCount": cast(int, metrics["unresolvedAuthorityCount"]),
        "provenanceFailureCount": cast(int, metrics["provenanceFailureCount"]),
        "invalidOrBlankResultCount": cast(int, metrics["invalidOrBlankResultCount"]),
        "orderingFailureCount": cast(int, metrics["orderingFailureCount"]),
        "releaseEligibilityFailureCount": cast(int, metrics["eligibilityFailureCount"]),
    }
    if set(check_by_code) != set(expected_actuals):
        raise QualityGateReportError("REPORT_CHECK_SET_MISMATCH")
    for code, expected_actual in expected_actuals.items():
        check = check_by_code[code]
        if check["actual"] != expected_actual or check["operator"] != "==":
            raise QualityGateReportError("REPORT_CHECK_VALUE_MISMATCH")
        expected_passed = check["actual"] == check["threshold"]
        if code == "evidenceCoverage":
            expected_passed = denominator > 0 and numerator == denominator
        if check["passed"] is not expected_passed:
            raise QualityGateReportError("REPORT_CHECK_OUTCOME_MISMATCH")
    if bool(cast(Mapping[str, Any], report["gate"])["passed"]) != all(
        bool(check["passed"]) for check in checks
    ):
        raise QualityGateReportError("REPORT_GATE_CHECK_MISMATCH")
    certification = cast(Mapping[str, Any], report["certification"])
    if bool(certification["releaseCertified"]) and not (
        report["profile"] == "RELEASE"
        and report["datasetKind"] == "OPERATIONAL_GOLDEN"
        and report["synthetic"] is False
        and cast(Mapping[str, Any], report["gate"])["passed"] is True
    ):
        raise QualityGateReportError("REPORT_CERTIFICATION_SPOOF")


def compute_fingerprints(snapshot: Mapping[str, Any]) -> dict[str, str]:
    """Recompute the normative RFC-8785-style SHA-256 fingerprint envelopes."""

    dataset = cast(Mapping[str, Any], snapshot["dataset"])
    capture = cast(Mapping[str, Any], snapshot["capture"])
    roots = cast(Sequence[Mapping[str, Any]], snapshot["generationRoots"])
    inventory = snapshot["referenceInventory"]
    capture_identity = {
        name: value
        for name, value in capture.items()
        if name != "sourceSnapshotFingerprint"
    }
    live_gate_identity = {
        name: value
        for name, value in cast(Mapping[str, Any], snapshot["liveGate"]).items()
        if name != "capturedSnapshotFingerprint"
    }
    labels = [
        {
            "generationRootId": root["generationRootId"],
            "primaryClassification": root["primaryClassification"],
        }
        for root in roots
    ]
    dataset_fingerprint = canonical_digest(
        {
            "datasetId": dataset["datasetId"],
            "kind": dataset["kind"],
            "synthetic": dataset["synthetic"],
            "description": dataset["description"],
            "labels": labels,
            "declaredCounts": dataset["declaredCounts"],
            "classificationDistribution": dataset["classificationDistribution"],
            "review": dataset["review"],
            "privacy": dataset["privacy"],
            "referenceInventory": inventory,
        }
    )
    source_fingerprint = canonical_digest(
        {
            "sourceSnapshotId": capture["sourceSnapshotId"],
            "capture": capture_identity,
            "generationRoots": [
                {
                    "generationRootId": root["generationRootId"],
                    "workspaceId": root["workspaceId"],
                    "slackWorkItemId": root["slackWorkItemId"],
                    "sourceVersion": root["sourceVersion"],
                    "inputDigest": root["inputDigest"],
                    "sealedSourceTime": root["sealedSourceTime"],
                    "contextSeal": root["contextSeal"],
                    "attemptHistory": root["attemptHistory"],
                }
                for root in roots
            ],
        }
    )
    snapshot_fingerprint = canonical_digest(
        {
            "artifactKind": snapshot["artifactKind"],
            "contractVersion": snapshot["contractVersion"],
            "profile": snapshot["profile"],
            "versionPins": snapshot["versionPins"],
            "thresholds": snapshot["thresholds"],
            "generationRoots": roots,
            "referenceInventory": inventory,
            "datasetFingerprint": dataset_fingerprint,
            "sourceSnapshotFingerprint": source_fingerprint,
            "capture": capture_identity,
            "liveGate": live_gate_identity,
        }
    )
    return {
        "datasetFingerprint": dataset_fingerprint,
        "sourceSnapshotFingerprint": source_fingerprint,
        "snapshotFingerprint": snapshot_fingerprint,
    }


def canonical_digest(value: Any) -> str:
    """Return a lower-case SHA-256 digest of canonical UTF-8 JSON."""

    return f"sha256:{hashlib.sha256(_canonicalize(value).encode('utf-8')).hexdigest()}"


def evaluate_quality_snapshot(
    snapshot: Mapping[str, Any],
    *,
    artifacts: Mapping[str, str] | None = None,
    schema: Mapping[str, Any] | None = None,
) -> QualityGateOutcome:
    """Validate and evaluate a sealed snapshot without network, DB, or wall-clock reads."""

    validate_input(snapshot, schema=schema)
    dataset = cast(Mapping[str, Any], snapshot["dataset"])
    live_gate = cast(Mapping[str, Any], snapshot["liveGate"])
    roots = cast(Sequence[Mapping[str, Any]], snapshot["generationRoots"])
    inventory = cast(Mapping[str, Sequence[Mapping[str, Any]]], snapshot["referenceInventory"])
    thresholds = cast(Mapping[str, int | float], snapshot["thresholds"])
    profile = cast(str, snapshot["profile"])

    evaluated_counts = _actual_counts(roots)
    actual_distribution = Counter(cast(str, root["primaryClassification"]) for root in roots)

    coverage_denominator = 0
    coverage_numerator = 0
    retired = Counter({name: 0 for name in RETIRED_STATUSES})
    unresolved_count = 0
    provenance_count = 0
    invalid_count = 0
    ordering_count = 0
    generated_exact: list[str] = []
    generated_semantic: list[str] = []
    exact_existing_collisions = 0
    semantic_existing_collisions = 0
    category_collisions = 0
    parent_collisions = 0
    root_evaluations: list[RootEvaluation] = []
    seen_ids: set[str] = set()

    active_existing_exact = {
        cast(str, row["exactContentKey"])
        for row in inventory["existingCases"]
        if row["activeAtSeal"] is True
    }
    active_existing_semantic = {
        cast(str, row["semanticDuplicateClusterId"])
        for row in inventory["existingCases"]
        if row["activeAtSeal"] is True
    }
    active_categories = {
        cast(str, row["canonicalKey"])
        for row in inventory["categories"]
        if row["activeAtSeal"] is True
    }
    active_parents = {
        cast(str, row["canonicalKey"])
        for row in inventory["parents"]
        if row["activeAtSeal"] is True
    }

    for root in roots:
        root_provenance: list[str] = []
        root_result: list[str] = []
        root_ordering: list[str] = []
        root_id = cast(str, root["generationRootId"])
        job = cast(Mapping[str, Any], root["selectedJob"])
        job_id = cast(str, job["generationJobId"])
        result = cast(Mapping[str, Any], job["result"])
        cases = cast(Sequence[Mapping[str, Any]], result["cases"])
        context = cast(Mapping[str, Any], root["contextSeal"])
        entries = cast(Sequence[Mapping[str, Any]], context["entries"])
        entries_by_id: dict[str, Mapping[str, Any]] = {}

        for value in (
            root_id,
            cast(str, context["contextSnapshotId"]),
            cast(str, result["resultSnapshotId"]),
        ):
            provenance_count += _record_unique_id(value, seen_ids, root_provenance)
        attempt_failures = _attempt_history_failures(root, job)
        provenance_count += len(attempt_failures)
        root_provenance.extend(attempt_failures)
        for attempt in cast(Sequence[Mapping[str, Any]], root["attemptHistory"]):
            provenance_count += _record_unique_id(
                cast(str, attempt["generationJobId"]), seen_ids, root_provenance
            )
        for entry in entries:
            entry_id = cast(str, entry["contextEntryId"])
            provenance_count += _record_unique_id(entry_id, seen_ids, root_provenance)
            if entry_id in entries_by_id:
                provenance_count += 1
                root_provenance.append("DUPLICATE_CONTEXT_ENTRY_ID")
            entries_by_id[entry_id] = entry

        result_failures = _result_failures(root, job, result, cases, snapshot)
        invalid_count += len(result_failures)
        root_result.extend(result_failures)
        provenance_failures = _job_provenance_failures(root, job, result, context)
        provenance_count += len(provenance_failures)
        root_provenance.extend(provenance_failures)
        if context["sealedAt"] != root["sealedSourceTime"]:
            provenance_count += 1
            root_provenance.append("CONTEXT_SEAL_TIME_MISMATCH")

        if [case["caseIndex"] for case in cases] != list(range(len(cases))):
            ordering_count += 1
            root_ordering.append("CASE_ORDER_MISMATCH")

        for generated_case in cases:
            case_id = cast(str, generated_case["caseSnapshotId"])
            provenance_count += _record_unique_id(case_id, seen_ids, root_provenance)
            steps = cast(Sequence[Mapping[str, Any]], generated_case["steps"])
            references = cast(
                Sequence[Mapping[str, Any]], generated_case["evidenceReferences"]
            )
            coverage_denominator += 1 + len(steps)
            generated_exact.append(cast(str, generated_case["exactContentKey"]))
            generated_semantic.append(
                cast(str, generated_case["semanticDuplicateClusterId"])
            )

            case_invalid = _case_invalid(generated_case, steps)
            invalid_count += len(case_invalid)
            root_result.extend(case_invalid)
            if [step["sequence"] for step in steps] != list(range(1, len(steps) + 1)):
                ordering_count += 1
                root_ordering.append(f"STEP_ORDER_MISMATCH:{case_id}")

            valid_coverage: set[str] = set()
            step_sequences = {cast(int, step["sequence"]) for step in steps}
            for reference in references:
                reference_id = cast(str, reference["evidenceReferenceId"])
                provenance_count += _record_unique_id(
                    reference_id, seen_ids, root_provenance
                )
                target = cast(Mapping[str, Any], reference["coverageTarget"])
                target_key = "CASE"
                if target["unit"] == "STEP":
                    sequence = cast(int, target["stepSequence"])
                    target_key = f"STEP:{sequence}"
                    if sequence not in step_sequences:
                        provenance_count += 1
                        root_provenance.append(f"UNKNOWN_STEP_TARGET:{reference_id}")
                        continue
                matched_entry = entries_by_id.get(cast(str, reference["contextEntryId"]))
                if matched_entry is None or not _reference_matches_entry(reference, matched_entry):
                    provenance_count += 1
                    root_provenance.append(f"EVIDENCE_PROVENANCE_MISMATCH:{reference_id}")
                    continue
                lifecycle = cast(str, matched_entry["lifecycleStatusAtSeal"])
                if lifecycle in RETIRED_STATUSES:
                    retired[lifecycle] += 1
                if (
                    matched_entry["conflictState"] == "UNRESOLVED"
                    or matched_entry["decisionState"] == "CANDIDATE"
                ):
                    unresolved_count += 1
                if _entry_active_at_seal(
                    matched_entry, cast(str, root["sealedSourceTime"])
                ):
                    valid_coverage.add(target_key)
            coverage_numerator += int("CASE" in valid_coverage)
            coverage_numerator += sum(
                f"STEP:{cast(int, step['sequence'])}" in valid_coverage for step in steps
            )

            materialization_failures = _materialization_failures(
                generated_case, root, job
            )
            provenance_count += len(materialization_failures)
            root_provenance.extend(materialization_failures)
            materialization = cast(Mapping[str, Any], generated_case["materialization"])
            kind = cast(str, materialization["kind"])
            for identity_name in (
                "applyOutcomeId",
                "suggestionId",
                "suggestionTargetId",
                "releaseReceiptId",
            ):
                identity = materialization[identity_name]
                if identity is not None:
                    provenance_count += _record_unique_id(
                        cast(str, identity), seen_ids, root_provenance
                    )
            exact_key = cast(str, generated_case["exactContentKey"])
            semantic_cluster = cast(str, generated_case["semanticDuplicateClusterId"])
            if kind == "APPLY_CREATED":
                exact_existing_collisions += int(exact_key in active_existing_exact)
                semantic_existing_collisions += int(
                    semantic_cluster in active_existing_semantic
                )
            elif kind == "APPLY_REUSED" and not (
                exact_key in active_existing_exact
                or semantic_cluster in active_existing_semantic
            ):
                provenance_count += 1
                root_provenance.append(f"REUSE_TARGET_NOT_IN_SEALED_INVENTORY:{case_id}")

            placement = cast(Mapping[str, Any], generated_case["placement"])
            category_key = cast(str, placement["categoryCanonicalKey"])
            parent_key = cast(str, placement["parentCanonicalKey"])
            if placement["categoryDecision"] == "CREATE_NEW":
                category_collisions += int(category_key in active_categories)
            elif category_key not in active_categories:
                provenance_count += 1
                root_provenance.append(f"CATEGORY_REUSE_TARGET_MISSING:{case_id}")
            if placement["parentDecision"] == "CREATE_NEW":
                parent_collisions += int(parent_key in active_parents)
            elif parent_key not in active_parents:
                provenance_count += 1
                root_provenance.append(f"PARENT_REUSE_TARGET_MISSING:{case_id}")

        root_evaluations.append(
            RootEvaluation(
                generation_root_id=root_id,
                generation_job_id=job_id,
                provenance_failures=tuple(root_provenance),
                result_failures=tuple(root_result),
                ordering_failures=tuple(root_ordering),
            )
        )

    exact_duplicates = _count_repeated(generated_exact) + exact_existing_collisions
    semantic_duplicates = (
        _count_repeated(generated_semantic) + semantic_existing_collisions
    )
    duplicate_total = (
        exact_duplicates
        + semantic_duplicates
        + category_collisions
        + parent_collisions
    )
    coverage_rate = (
        0 if coverage_denominator == 0 else coverage_numerator / coverage_denominator
    )
    retired_total = sum(retired.values())

    eligibility_codes = _eligibility_failures(
        snapshot,
        evaluated_counts=evaluated_counts,
        actual_distribution=actual_distribution,
    )
    metrics: JsonObject = {
        "evidenceCoverage": {
            "numerator": coverage_numerator,
            "denominator": coverage_denominator,
            "rate": coverage_rate,
        },
        "retiredKnowledgeMisuse": {
            "total": retired_total,
            **{name: retired[name] for name in RETIRED_STATUSES},
        },
        "duplicates": {
            "total": duplicate_total,
            "exactKey": exact_duplicates,
            "semanticCluster": semantic_duplicates,
            "categoryCollision": category_collisions,
            "parentCollision": parent_collisions,
        },
        "unresolvedAuthorityCount": unresolved_count,
        "provenanceFailureCount": provenance_count,
        "invalidOrBlankResultCount": invalid_count,
        "orderingFailureCount": ordering_count,
        "eligibilityFailureCount": len(eligibility_codes),
    }
    check_values = (
        (
            "evidenceCoverage",
            coverage_rate,
            cast(float, thresholds["evidenceCoverage"]),
            coverage_denominator > 0 and coverage_numerator == coverage_denominator,
        ),
        (
            "retiredKnowledgeMisuseCount",
            retired_total,
            cast(float, thresholds["retiredKnowledgeMisuseCount"]),
            retired_total == thresholds["retiredKnowledgeMisuseCount"],
        ),
        (
            "duplicateCount",
            duplicate_total,
            cast(float, thresholds["duplicateCount"]),
            duplicate_total == thresholds["duplicateCount"],
        ),
        (
            "unresolvedAuthorityCount",
            unresolved_count,
            cast(float, thresholds["unresolvedAuthorityCount"]),
            unresolved_count == thresholds["unresolvedAuthorityCount"],
        ),
        (
            "provenanceFailureCount",
            provenance_count,
            cast(float, thresholds["provenanceFailureCount"]),
            provenance_count == thresholds["provenanceFailureCount"],
        ),
        (
            "invalidOrBlankResultCount",
            invalid_count,
            cast(float, thresholds["invalidOrBlankResultCount"]),
            invalid_count == thresholds["invalidOrBlankResultCount"],
        ),
        (
            "orderingFailureCount",
            ordering_count,
            cast(float, thresholds["orderingFailureCount"]),
            ordering_count == thresholds["orderingFailureCount"],
        ),
        (
            "releaseEligibilityFailureCount",
            len(eligibility_codes),
            0.0,
            not eligibility_codes,
        ),
    )
    checks: list[JsonObject] = [
        {
            "code": code,
            "actual": actual,
            "operator": "==",
            "threshold": threshold,
            "passed": passed,
        }
        for code, actual, threshold, passed in check_values
    ]
    gate_passed = all(cast(bool, check["passed"]) for check in checks)
    synthetic = bool(dataset["synthetic"])
    certification = _certification(
        profile=profile,
        synthetic=synthetic,
        gate_passed=gate_passed,
        live_gate=live_gate,
        eligibility_codes=eligibility_codes,
    )
    output_paths = dict(artifacts or cast(Mapping[str, str], snapshot["reportOutputs"]))
    report: JsonObject = {
        "artifactKind": "AUTOMATIC_GENERATION_QUALITY_EVALUATION_REPORT",
        "contractVersion": CONTRACT_VERSION,
        "profile": profile,
        "datasetId": dataset["datasetId"],
        "datasetKind": dataset["kind"],
        "synthetic": synthetic,
        "datasetFingerprint": dataset["datasetFingerprint"],
        "snapshotFingerprint": dataset["snapshotFingerprint"],
        "versionPins": snapshot["versionPins"],
        "evaluatorVersion": EVALUATOR_VERSION,
        "deterministicOfflineReplay": True,
        "evaluatedCounts": evaluated_counts,
        "metrics": metrics,
        "checks": checks,
        "gate": {"passed": gate_passed, "allOrNothing": True},
        "certification": certification,
        "privacy": dataset["privacy"],
        "artifacts": output_paths,
        "retention": {
            "retentionDays": 30 if synthetic else 400,
            "immutable": True,
            "legalHold": False,
        },
    }
    validate_report(report, schema=schema)
    return QualityGateOutcome(
        report=report,
        roots=tuple(root_evaluations),
        eligibility_reason_codes=eligibility_codes,
    )


def decide_release(
    snapshot: Mapping[str, Any],
    *,
    artifacts: Mapping[str, str] | None = None,
    schema: Mapping[str, Any] | None = None,
) -> QualityGateOutcome:
    """Production release seam; a non-release snapshot is never promoted implicitly."""

    if snapshot.get("profile") != "RELEASE":
        raise QualityGateInputError("RELEASE_SEAM_REQUIRES_RELEASE_PROFILE")
    return evaluate_quality_snapshot(snapshot, artifacts=artifacts, schema=schema)


def _actual_counts(roots: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    cases = [
        generated_case
        for root in roots
        for generated_case in cast(
            Sequence[Mapping[str, Any]],
            cast(Mapping[str, Any], cast(Mapping[str, Any], root["selectedJob"])["result"])[
                "cases"
            ],
        )
    ]
    return {
        "generationRoots": len(roots),
        "generationJobs": len(roots),
        "generationResults": len(roots),
        "generatedCases": len(cases),
        "generatedSteps": sum(
            len(cast(Sequence[Any], generated_case["steps"])) for generated_case in cases
        ),
        "evidenceReferences": sum(
            len(cast(Sequence[Any], generated_case["evidenceReferences"]))
            for generated_case in cases
        ),
    }


def _result_failures(
    root: Mapping[str, Any],
    job: Mapping[str, Any],
    result: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    snapshot: Mapping[str, Any],
) -> list[str]:
    failures: list[str] = []
    pins = cast(Mapping[str, Any], snapshot["versionPins"])
    if job["trigger"] != "AUTOMATIC" or job["status"] != "SUCCEEDED":
        failures.append("SELECTED_JOB_NOT_AUTOMATIC_SUCCEEDED")
    if result["validationStatus"] != "VALID":
        failures.append("RESULT_NOT_VALID")
    if not 5 <= len(cases) <= 10:
        failures.append("RESULT_CASE_COUNT_OUT_OF_RANGE")
    if job["inputSchemaVersion"] != pins["inputSchemaVersion"]:
        failures.append("INPUT_SCHEMA_PIN_MISMATCH")
    if job["outputSchemaVersion"] != pins["outputSchemaVersion"]:
        failures.append("OUTPUT_SCHEMA_PIN_MISMATCH")
    if result["outputSchemaVersion"] != pins["outputSchemaVersion"]:
        failures.append("RESULT_SCHEMA_PIN_MISMATCH")
    if not cases:
        failures.append("EMPTY_RESULT")
    return failures


def _job_provenance_failures(
    root: Mapping[str, Any],
    job: Mapping[str, Any],
    result: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[str]:
    failures: list[str] = []
    if job["rootJobId"] != root["generationRootId"]:
        failures.append("ROOT_JOB_ID_MISMATCH")
    if job["workspaceId"] != root["workspaceId"]:
        failures.append("JOB_WORKSPACE_MISMATCH")
    if result["workspaceId"] != root["workspaceId"]:
        failures.append("RESULT_WORKSPACE_MISMATCH")
    if job["sourceVersion"] != root["sourceVersion"]:
        failures.append("SOURCE_VERSION_MISMATCH")
    if job["contextDigest"] != context["contextDigest"]:
        failures.append("CONTEXT_DIGEST_MISMATCH")
    if job["inputDigest"] != root["inputDigest"]:
        failures.append("INPUT_DIGEST_MISMATCH")
    if job["resultDigest"] != result["resultDigest"]:
        failures.append("RESULT_DIGEST_MISMATCH")
    attempt = cast(int, job["attemptNumber"])
    retry_of = job["retryOfJobId"]
    if (attempt == 1 and retry_of is not None) or (attempt > 1 and retry_of is None):
        failures.append("RETRY_LINEAGE_MISMATCH")
    return failures


def _attempt_history_failures(
    root: Mapping[str, Any], job: Mapping[str, Any]
) -> list[str]:
    """Require an unfiltered contiguous retry chain ending in the selected job."""

    attempts = cast(Sequence[Mapping[str, Any]], root["attemptHistory"])
    failures: list[str] = []
    selected_attempt = cast(int, job["attemptNumber"])
    if len(attempts) != selected_attempt:
        failures.append("ATTEMPT_HISTORY_COUNT_MISMATCH")
    if [attempt["attemptNumber"] for attempt in attempts] != list(
        range(1, len(attempts) + 1)
    ):
        failures.append("ATTEMPT_HISTORY_ORDER_MISMATCH")
    for index, attempt in enumerate(attempts):
        expected_retry = None if index == 0 else attempts[index - 1]["generationJobId"]
        if attempt["retryOfJobId"] != expected_retry:
            failures.append("ATTEMPT_RETRY_CHAIN_MISMATCH")
        if attempt["rootJobId"] != root["generationRootId"]:
            failures.append("ATTEMPT_ROOT_MISMATCH")
        if attempt["workspaceId"] != root["workspaceId"]:
            failures.append("ATTEMPT_WORKSPACE_MISMATCH")
        if attempt["sourceVersion"] != root["sourceVersion"]:
            failures.append("ATTEMPT_SOURCE_VERSION_MISMATCH")
        if attempt["contextDigest"] != root["contextSeal"]["contextDigest"]:
            failures.append("ATTEMPT_CONTEXT_DIGEST_MISMATCH")
        if attempt["inputDigest"] != root["inputDigest"]:
            failures.append("ATTEMPT_INPUT_DIGEST_MISMATCH")
        if index < len(attempts) - 1 and attempt["status"] == "SUCCEEDED":
            failures.append("FILTERED_SUCCESS_ATTEMPT")
        if attempt["status"] in {"REJECTED", "UNAVAILABLE"} and not attempt[
            "rejectionCode"
        ]:
            failures.append("REJECTED_ATTEMPT_CODE_MISSING")
    if attempts:
        selected = attempts[-1]
        expected_selected = {
            "generationJobId": job["generationJobId"],
            "rootJobId": job["rootJobId"],
            "workspaceId": job["workspaceId"],
            "sourceVersion": job["sourceVersion"],
            "retryOfJobId": job["retryOfJobId"],
            "attemptNumber": job["attemptNumber"],
            "status": job["status"],
            "contextDigest": job["contextDigest"],
            "inputDigest": job["inputDigest"],
            "resultDigest": job["resultDigest"],
            "rejectionCode": None,
        }
        if dict(selected) != expected_selected:
            failures.append("SELECTED_ATTEMPT_MISMATCH")
    return failures


def _case_invalid(
    generated_case: Mapping[str, Any], steps: Sequence[Mapping[str, Any]]
) -> list[str]:
    failures: list[str] = []
    case_id = cast(str, generated_case["caseSnapshotId"])
    for name in ("summary", "caseExpectedResult", "generationReason"):
        if cast(Mapping[str, Any], generated_case[name])["normalizedLength"] == 0:
            failures.append(f"BLANK_{name.upper()}:{case_id}")
    if not steps:
        failures.append(f"EMPTY_STEPS:{case_id}")
    for step in steps:
        sequence = step["sequence"]
        for name in ("action", "expectedResult"):
            if cast(Mapping[str, Any], step[name])["normalizedLength"] == 0:
                failures.append(f"BLANK_STEP_{name.upper()}:{case_id}:{sequence}")
    return failures


def _reference_matches_entry(
    reference: Mapping[str, Any], entry: Mapping[str, Any]
) -> bool:
    entry_source = cast(Mapping[str, Any], entry["source"])
    reference_source = cast(Mapping[str, Any], reference["source"])
    return (
        entry["knowledgeNodeId"] == reference["knowledgeNodeId"]
        and entry_source == reference_source
        and entry_source["sourceRevisionId"] == reference["citationSourceRevisionId"]
        and reference["citationExcerptHash"]
        in cast(Sequence[str], entry["approvedEvidenceExcerptHashes"])
    )


def _entry_active_at_seal(entry: Mapping[str, Any], sealed_source_time: str) -> bool:
    sealed = _parse_datetime(sealed_source_time)
    valid_from = _parse_datetime(cast(str, entry["validFrom"]))
    valid_to_value = entry["validTo"]
    valid_to = _parse_datetime(cast(str, valid_to_value)) if valid_to_value else None
    return (
        entry["lifecycleStatusAtSeal"] == "ACTIVE"
        and entry["availableAtSeal"] is True
        and entry["conflictState"] == "NONE"
        and entry["decisionState"] in {"ACCEPTED", "NOT_APPLICABLE"}
        and valid_from <= sealed
        and (valid_to is None or sealed < valid_to)
    )


def _materialization_failures(
    generated_case: Mapping[str, Any],
    root: Mapping[str, Any],
    job: Mapping[str, Any],
) -> list[str]:
    materialization = cast(Mapping[str, Any], generated_case["materialization"])
    case_id = cast(str, generated_case["caseSnapshotId"])
    failures: list[str] = []
    if materialization["sourceWorkspaceId"] != root["workspaceId"]:
        failures.append(f"MATERIALIZATION_WORKSPACE_MISMATCH:{case_id}")
    if materialization["sourceGenerationJobId"] != job["generationJobId"]:
        failures.append(f"MATERIALIZATION_JOB_MISMATCH:{case_id}")
    if materialization["sourceRootJobId"] != root["generationRootId"]:
        failures.append(f"MATERIALIZATION_ROOT_MISMATCH:{case_id}")
    if materialization["sourceVersion"] != root["sourceVersion"]:
        failures.append(f"MATERIALIZATION_SOURCE_VERSION_MISMATCH:{case_id}")
    if materialization["contextDigest"] != job["contextDigest"]:
        failures.append(f"MATERIALIZATION_CONTEXT_MISMATCH:{case_id}")
    if materialization["inputDigest"] != job["inputDigest"]:
        failures.append(f"MATERIALIZATION_INPUT_MISMATCH:{case_id}")
    if materialization["resultDigest"] != job["resultDigest"]:
        failures.append(f"MATERIALIZATION_RESULT_MISMATCH:{case_id}")
    kind = materialization["kind"]
    apply_fields = ("applyId", "applyOutcomeId")
    suggestion_fields = (
        "suggestionId",
        "suggestionTargetId",
        "sourceChangeAssessmentId",
        "generationLineageId",
        "baselineCaseVersionId",
        "releaseReceiptId",
    )
    if kind in {"APPLY_CREATED", "APPLY_REUSED"}:
        if any(materialization[name] is None for name in apply_fields) or any(
            materialization[name] is not None for name in suggestion_fields
        ):
            failures.append(f"APPLY_AUTHORITY_SHAPE_INVALID:{case_id}")
    elif kind == "SUGGESTION":
        if any(materialization[name] is not None for name in apply_fields) or any(
            materialization[name] is None for name in suggestion_fields
        ):
            failures.append(f"SUGGESTION_AUTHORITY_SHAPE_INVALID:{case_id}")
    elif any(
        materialization[name] is not None for name in (*apply_fields, *suggestion_fields)
    ):
        failures.append(f"NO_CHANGE_AUTHORITY_SHAPE_INVALID:{case_id}")
    return failures


def _record_unique_id(value: str, seen: set[str], failures: list[str]) -> int:
    if value in seen:
        failures.append(f"DUPLICATE_SCOPE_ID:{value}")
        return 1
    seen.add(value)
    return 0


def _count_repeated(values: Sequence[str]) -> int:
    return sum(max(0, count - 1) for count in Counter(values).values())


def _eligibility_failures(
    snapshot: Mapping[str, Any],
    *,
    evaluated_counts: Mapping[str, int],
    actual_distribution: Counter[str],
) -> tuple[str, ...]:
    dataset = cast(Mapping[str, Any], snapshot["dataset"])
    capture = cast(Mapping[str, Any], snapshot["capture"])
    live = cast(Mapping[str, Any], snapshot["liveGate"])
    review = cast(Mapping[str, Any], dataset["review"])
    privacy = cast(Mapping[str, Any], dataset["privacy"])
    declared_counts = cast(Mapping[str, int], dataset["declaredCounts"])
    distribution = cast(Mapping[str, int], dataset["classificationDistribution"])
    profile = snapshot["profile"]
    failures: list[str] = []
    if dict(declared_counts) != dict(evaluated_counts):
        failures.append("DATASET_COUNT_MISMATCH")
    if any(distribution[name] != actual_distribution[name] for name in CLASSIFICATIONS):
        failures.append("DATASET_DISTRIBUTION_MISMATCH")
    if capture["offlineReplay"] is not True or capture["networkAccess"] != "DISABLED":
        failures.append("OFFLINE_REPLAY_NOT_SEALED")
    if capture["providerCallsDuringEvaluation"] != 0 or capture["slackCallsDuringEvaluation"] != 0:
        failures.append("EVALUATION_EXTERNAL_CALL_OBSERVED")
    if capture["degraded"] is True:
        failures.append("DEGRADED_CAPTURE")
    if privacy["secretsScan"] != "CLEAN":
        failures.append("SECRETS_SCAN_NOT_CLEAN")
    if privacy["piiScan"] != "CLEAN":
        failures.append("PII_SCAN_NOT_CLEAN")
    if privacy["containsRawSourceText"] is not False:
        failures.append("RAW_SOURCE_TEXT_PRESENT")

    if profile == "RELEASE":
        if dataset["kind"] != "OPERATIONAL_GOLDEN" or dataset["synthetic"] is not False:
            failures.append("OPERATIONAL_GOLDEN_MISSING")
        if any(
            declared_counts[name] < minimum
            for name, minimum in MINIMUM_RELEASE_COUNTS.items()
        ):
            failures.append("OPERATIONAL_MINIMUM_NOT_MET")
        if any(distribution[name] < 2 for name in CLASSIFICATIONS):
            failures.append("CLASSIFICATION_MINIMUM_NOT_MET")
        if review["status"] != "REVIEWED" or not all(
            review[name]
            for name in (
                "reviewedBy",
                "reviewedAt",
                "labelSetId",
                "reviewProtocolVersion",
                "approvalId",
                "reviewAuthorityDigest",
                "approvalArtifactDigest",
            )
        ):
            failures.append("REVIEWED_GOLDEN_MISSING")
        if capture["environment"] == "synthetic":
            failures.append("OPERATIONAL_CAPTURE_MISSING")
        age_seconds = (
            _parse_datetime(cast(str, capture["releaseCandidateAt"]))
            - _parse_datetime(cast(str, capture["capturedAt"]))
        ).total_seconds()
        if age_seconds < 0 or age_seconds > cast(int, capture["maxSnapshotAgeSeconds"]):
            failures.append("SNAPSHOT_STALE")
        if live["status"] != "PASSED":
            failures.append("LIVE_GATE_MISSING")
        if live["providerCredentialed"] is not True or live["providerCallObserved"] is not True:
            failures.append("PROVIDER_LIVE_ATTESTATION_MISSING")
        if not all(live[name] for name in ("liveRunId", "approvedBy", "approvedAt")):
            failures.append("LIVE_GATE_APPROVAL_MISSING")
        if live["capturedSnapshotFingerprint"] != dataset["snapshotFingerprint"]:
            failures.append("LIVE_GATE_FINGERPRINT_MISMATCH")
    return tuple(dict.fromkeys(failures))


def _certification(
    *,
    profile: str,
    synthetic: bool,
    gate_passed: bool,
    live_gate: Mapping[str, Any],
    eligibility_codes: tuple[str, ...],
) -> JsonObject:
    if synthetic:
        return {
            "releaseCertified": False,
            "status": "NOT_CERTIFIED_SYNTHETIC",
            "reasonCodes": list(eligibility_codes or ("SYNTHETIC_ONLY",)),
            "liveRunId": None,
        }
    if profile == "RELEASE" and gate_passed:
        return {
            "releaseCertified": True,
            "status": "RELEASE_CERTIFIED",
            "reasonCodes": [],
            "liveRunId": live_gate["liveRunId"],
        }
    if profile == "CANDIDATE":
        status = "NOT_CERTIFIED_INELIGIBLE"
    elif "LIVE_GATE_MISSING" in eligibility_codes:
        status = "NOT_CERTIFIED_LIVE_GATE_MISSING"
    elif eligibility_codes:
        status = "NOT_CERTIFIED_INELIGIBLE"
    else:
        status = "NOT_CERTIFIED_GATE_FAILED"
    reasons = eligibility_codes or (
        "CANDIDATE_PROFILE" if profile == "CANDIDATE" else "QUALITY_GATE_FAILED",
    )
    return {
        "releaseCertified": False,
        "status": status,
        "reasonCodes": list(reasons),
        "liveRunId": live_gate["liveRunId"],
    }


def _validate_exact_pins(pins: Mapping[str, Any]) -> None:
    for name, value in pins.items():
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped != value or stripped.casefold() in _RESERVED_PIN_VALUES:
            raise QualityGateInputError(f"UNPINNED_VERSION_VALUE:{name}")
        if any(marker in value for marker in ("*", "^", "~", ">", "<")):
            raise QualityGateInputError(f"VERSION_RANGE_FORBIDDEN:{name}")
    expected_semantic_policy_digest = canonical_digest(
        {
            "algorithmVersion": pins["semanticDuplicateAlgorithmVersion"],
            "providerId": pins["embeddingProviderId"],
            "modelId": pins["embeddingModelId"],
            "modelRevision": pins["embeddingRevision"],
            "dimensions": pins["embeddingDimensions"],
            "threshold": pins["semanticDuplicateThreshold"],
        }
    )
    if pins["semanticDuplicatePolicyDigest"] != expected_semantic_policy_digest:
        raise QualityGateInputError("SEMANTIC_DUPLICATE_POLICY_DIGEST_MISMATCH")


def _validate_schema_instance(
    instance: Mapping[str, Any], schema: Mapping[str, Any], definition: str
) -> None:
    definitions = cast(Mapping[str, Any], schema.get("$defs"))
    target = definitions.get(definition)
    if not isinstance(target, Mapping):
        raise QualityGateInputError(f"SCHEMA_DEFINITION_MISSING:{definition}")
    errors: list[str] = []
    _validate_node(instance, target, schema, "$", errors)
    if errors:
        raise QualityGateInputError(f"SCHEMA_VALIDATION_FAILED:{errors[0]}")


def _validate_node(
    instance: Any,
    rule: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: str,
    errors: list[str],
) -> None:
    if errors:
        return
    reference = rule.get("$ref")
    if isinstance(reference, str):
        prefix = "#/$defs/"
        if not reference.startswith(prefix):
            errors.append(f"{path}:unsupported-ref")
            return
        target = cast(Mapping[str, Any], root_schema["$defs"]).get(reference[len(prefix) :])
        if not isinstance(target, Mapping):
            errors.append(f"{path}:missing-ref")
            return
        _validate_node(instance, target, root_schema, path, errors)
        return
    all_of = rule.get("allOf")
    if isinstance(all_of, list):
        for child in all_of:
            if isinstance(child, Mapping):
                _validate_node(instance, child, root_schema, path, errors)
    one_of = rule.get("oneOf")
    if isinstance(one_of, list):
        matches = 0
        for child in one_of:
            child_errors: list[str] = []
            _validate_node(instance, cast(Mapping[str, Any], child), root_schema, path, child_errors)
            matches += int(not child_errors)
        if matches != 1:
            errors.append(f"{path}:oneOf")
            return
    not_rule = rule.get("not")
    if isinstance(not_rule, Mapping):
        not_errors: list[str] = []
        _validate_node(instance, not_rule, root_schema, path, not_errors)
        if not not_errors:
            errors.append(f"{path}:not")
            return
    if "const" in rule and instance != rule["const"]:
        errors.append(f"{path}:const")
        return
    enum = rule.get("enum")
    if isinstance(enum, list) and instance not in enum:
        errors.append(f"{path}:enum")
        return
    expected_type = rule.get("type")
    if isinstance(expected_type, str) and not _matches_type(instance, expected_type):
        errors.append(f"{path}:type:{expected_type}")
        return
    if isinstance(instance, dict):
        required = rule.get("required", [])
        if isinstance(required, list):
            for name in required:
                if name not in instance:
                    errors.append(f"{path}.{name}:required")
                    return
        properties = rule.get("properties", {})
        properties = properties if isinstance(properties, Mapping) else {}
        if rule.get("additionalProperties") is False:
            unknown = sorted(set(instance) - set(properties))
            if unknown:
                errors.append(f"{path}.{unknown[0]}:additionalProperty")
                return
        for name, child_rule in properties.items():
            if name in instance and isinstance(child_rule, Mapping):
                _validate_node(instance[name], child_rule, root_schema, f"{path}.{name}", errors)
    if isinstance(instance, list):
        minimum_items = rule.get("minItems")
        maximum_items = rule.get("maxItems")
        if isinstance(minimum_items, int) and len(instance) < minimum_items:
            errors.append(f"{path}:minItems")
            return
        if isinstance(maximum_items, int) and len(instance) > maximum_items:
            errors.append(f"{path}:maxItems")
            return
        if rule.get("uniqueItems") is True:
            serialized = [_canonicalize(value) for value in instance]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{path}:uniqueItems")
                return
        items = rule.get("items")
        if isinstance(items, Mapping):
            for index, value in enumerate(instance):
                _validate_node(value, items, root_schema, f"{path}[{index}]", errors)
    if isinstance(instance, str):
        minimum_length = rule.get("minLength")
        maximum_length = rule.get("maxLength")
        if isinstance(minimum_length, int) and len(instance) < minimum_length:
            errors.append(f"{path}:minLength")
            return
        if isinstance(maximum_length, int) and len(instance) > maximum_length:
            errors.append(f"{path}:maxLength")
            return
        pattern = rule.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, instance) is None:
            errors.append(f"{path}:pattern")
            return
        if rule.get("format") == "date-time":
            try:
                _parse_datetime(instance)
            except QualityGateInputError:
                errors.append(f"{path}:date-time")
                return
    if isinstance(instance, int | float) and not isinstance(instance, bool):
        if not math.isfinite(float(instance)):
            errors.append(f"{path}:finite")
            return
        minimum = rule.get("minimum")
        maximum = rule.get("maximum")
        exclusive_minimum = rule.get("exclusiveMinimum")
        if isinstance(minimum, int | float) and instance < minimum:
            errors.append(f"{path}:minimum")
            return
        if isinstance(maximum, int | float) and instance > maximum:
            errors.append(f"{path}:maximum")
            return
        if isinstance(exclusive_minimum, int | float) and instance <= exclusive_minimum:
            errors.append(f"{path}:exclusiveMinimum")
            return
    conditional = rule.get("if")
    then_rule = rule.get("then")
    if isinstance(conditional, Mapping) and isinstance(then_rule, Mapping):
        conditional_errors: list[str] = []
        _validate_node(instance, conditional, root_schema, path, conditional_errors)
        if not conditional_errors:
            _validate_node(instance, then_rule, root_schema, path, errors)


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _parse_datetime(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith(("Z", "+00:00")):
        raise QualityGateInputError("TIMESTAMP_MUST_BE_UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise QualityGateInputError("INVALID_TIMESTAMP") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise QualityGateInputError("TIMESTAMP_MUST_BE_UTC")
    return parsed


def _canonicalize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise QualityGateInputError("NON_FINITE_CANONICAL_NUMBER")
        if value == 0:
            return "0"
        if value.is_integer() and abs(value) < 1e21:
            return str(int(value))
        rendered = json.dumps(value, allow_nan=False, separators=(",", ":"))
        if "e" in rendered:
            mantissa, exponent = rendered.split("e")
            sign = "+" if not exponent.startswith("-") else "-"
            digits = exponent.lstrip("+-0") or "0"
            return f"{mantissa}e{sign}{digits}"
        return rendered
    if isinstance(value, list | tuple):
        return "[" + ",".join(_canonicalize(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise QualityGateInputError("CANONICAL_OBJECT_KEY_NOT_STRING")
        return (
            "{"
            + ",".join(
                f"{_canonicalize(key)}:{_canonicalize(value[key])}" for key in sorted(value)
            )
            + "}"
        )
    raise QualityGateInputError(f"UNSUPPORTED_CANONICAL_TYPE:{type(value).__name__}")
