"""PE-SE2-T09 deterministic regression and PostgreSQL baseline authority.

The evaluator is pure and offline.  The services below persist only sealed
hash/identifier metadata and require callers to own the surrounding database
transaction.  Provider credentials, source text, prompts, and generated text
never cross this module boundary.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tc_backend.db.models import (
    GenerationBaselineActivation,
    GenerationBaselineApproval,
    GenerationBaselineAuditEvent,
    GenerationBaselineChangeRequest,
    GenerationBaselineRollback,
    GenerationBaselineRuntime,
    GenerationJob,
    GenerationVersionSet,
)
from tc_backend.generation.quality_gate import (
    QualityGateInputError,
    _validate_schema_instance,
    canonical_digest,
    loads_strict_json,
)

CONTRACT_VERSION: Final = "automatic-generation-version-regression/1.0.0"
EVALUATOR_VERSION: Final = "automatic-generation-version-regression-evaluator-v1"
SCHEMA_ID: Final = (
    "https://qa-tool.local/schemas/generation/version-regression/1.0.0/schema.json"
)
_SCHEMA_PATH: Final = (
    Path(__file__).resolve().parents[4]
    / "packages/contracts/generation/version-regression/1.0.0/schema.json"
)
_RESERVED_PINS: Final = {
    "current",
    "default",
    "head",
    "latest",
    "main",
    "master",
    "missing",
    "stable",
    "unconfigured",
    "unknown",
    "unpinned",
}
_MUTABLE_ALIAS_SUFFIX: Final = re.compile(
    r"(?:^|[:/@._-])(current|default|head|latest|main|master|stable)$",
    re.IGNORECASE,
)
_COMPATIBILITY_DIMENSIONS: Final = (
    "GENERATION_POLICY",
    "PROMPT",
    "GENERATION_PROVIDER_MODEL",
    "EMBEDDING_PROVIDER_MODEL_REVISION",
    "EMBEDDING_DIMENSION",
    "SEMANTIC_THRESHOLD_POLICY",
    "EXACT_DUPLICATE_ALGORITHM",
    "GENERATION_INPUT_SCHEMA",
    "GENERATION_OUTPUT_SCHEMA",
    "SUGGESTION_SCHEMA",
    "QUALITY_GATE_SCHEMA",
    "ONTOLOGY_SCHEMA",
    "REGRESSION_EVALUATOR_POLICY",
    "PLACEMENT_POLICY",
)
_SCHEMA_DIMENSION_FIELDS: Final = {
    "GENERATION_INPUT_SCHEMA": "generationInputSchemaVersion",
    "GENERATION_OUTPUT_SCHEMA": "generationOutputSchemaVersion",
    "SUGGESTION_SCHEMA": "suggestionSchemaVersion",
    "QUALITY_GATE_SCHEMA": "qualityGateSchemaVersion",
    "ONTOLOGY_SCHEMA": "ontologySchemaVersion",
}
_DIMENSION_FIELDS: Final = {
    "GENERATION_POLICY": ("generationPolicyVersion", "generationPolicyDigest"),
    "PROMPT": ("promptVersion", "promptDigest"),
    "GENERATION_PROVIDER_MODEL": ("providerId", "modelId", "modelRevision"),
    "EMBEDDING_PROVIDER_MODEL_REVISION": (
        "embeddingProviderId",
        "embeddingModelId",
        "embeddingRevision",
    ),
    "EMBEDDING_DIMENSION": ("embeddingDimensions",),
    "SEMANTIC_THRESHOLD_POLICY": (
        "semanticDuplicateAlgorithmVersion",
        "semanticDuplicateThreshold",
        "semanticDuplicatePolicyDigest",
    ),
    "EXACT_DUPLICATE_ALGORITHM": ("exactDuplicateAlgorithmVersion",),
    "GENERATION_INPUT_SCHEMA": ("generationInputSchemaVersion",),
    "GENERATION_OUTPUT_SCHEMA": ("generationOutputSchemaVersion",),
    "SUGGESTION_SCHEMA": ("suggestionSchemaVersion",),
    "QUALITY_GATE_SCHEMA": ("qualityGateSchemaVersion",),
    "ONTOLOGY_SCHEMA": ("ontologySchemaVersion", "ontologySchemaDigest"),
    "REGRESSION_EVALUATOR_POLICY": (
        "regressionEvaluatorVersion",
        "regressionPolicyVersion",
        "regressionPolicyDigest",
    ),
    "PLACEMENT_POLICY": ("placementPolicyVersion", "placementPolicyDigest"),
}
_VERSION_DIGEST_PAIRS: Final = (
    ("generationPolicyVersion", "generationPolicyDigest"),
    ("promptVersion", "promptDigest"),
    ("ontologySchemaVersion", "ontologySchemaDigest"),
    ("regressionPolicyVersion", "regressionPolicyDigest"),
    ("placementPolicyVersion", "placementPolicyDigest"),
)
_MIGRATION_REQUIRED_DIMENSIONS: Final = {
    "EMBEDDING_DIMENSION",
    "GENERATION_INPUT_SCHEMA",
    "GENERATION_OUTPUT_SCHEMA",
    "ONTOLOGY_SCHEMA",
    "QUALITY_GATE_SCHEMA",
    "SUGGESTION_SCHEMA",
}
_ZERO_METRICS: Final = (
    "retiredKnowledgeMisuseCount",
    "duplicateCount",
    "unresolvedAuthorityCount",
    "provenanceFailureCount",
    "invalidOrBlankResultCount",
    "orderingFailureCount",
)

JsonObject = dict[str, Any]


class VersionRegressionInputError(QualityGateInputError):
    """The input/report is malformed, unpinned, or fingerprint-invalid."""


class VersionRegressionReportError(ValueError):
    """A generated or cached report violates the report contract."""


class BaselineGovernanceError(RuntimeError):
    """A fail-closed change, approval, activation, or rollback rejection."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class VersionSetFenceError(BaselineGovernanceError):
    """A generation job is unpinned or does not match the active version set."""


@dataclass(frozen=True, slots=True)
class VersionRegressionOutcome:
    report: JsonObject
    reason_codes: tuple[str, ...]
    passed: bool

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1


def load_schema() -> JsonObject:
    try:
        value = loads_strict_json(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, QualityGateInputError) as error:
        raise VersionRegressionInputError("VERSION_REGRESSION_SCHEMA_UNAVAILABLE") from error
    if not isinstance(value, dict) or value.get("$id") != SCHEMA_ID:
        raise VersionRegressionInputError("VERSION_REGRESSION_SCHEMA_ID_MISMATCH")
    return cast(JsonObject, value)


def validate_input(
    snapshot: Mapping[str, Any], *, schema: Mapping[str, Any] | None = None
) -> None:
    try:
        _validate_schema_instance(snapshot, schema or load_schema(), "RegressionEvaluationInput")
    except QualityGateInputError as error:
        raise VersionRegressionInputError(str(error)) from error
    for side in ("oldBaseline", "newCandidate"):
        baseline = _mapping(snapshot[side], f"{side.upper()}_INVALID")
        identity = _mapping(baseline["identity"], f"{side.upper()}_IDENTITY_INVALID")
        _validate_pins(identity)
        if baseline["identityDigest"] != canonical_digest(identity):
            raise VersionRegressionInputError(f"{side.upper()}_IDENTITY_DIGEST_MISMATCH")
    old = _mapping(snapshot["oldBaseline"], "OLD_BASELINE_INVALID")
    new = _mapping(snapshot["newCandidate"], "NEW_BASELINE_INVALID")
    request = _mapping(snapshot["changeRequest"], "CHANGE_REQUEST_INVALID")
    matrix = _mapping(snapshot["compatibilityMatrix"], "COMPATIBILITY_MATRIX_INVALID")
    if request["oldBaselineIdentityDigest"] != old["identityDigest"] or request[
        "proposedBaselineIdentityDigest"
    ] != new["identityDigest"]:
        raise VersionRegressionInputError("CHANGE_REQUEST_IDENTITY_MISMATCH")
    if request["compatibilityMatrixId"] != matrix["matrixId"]:
        raise VersionRegressionInputError("CHANGE_REQUEST_MATRIX_MISMATCH")
    if matrix["oldBaselineIdentityDigest"] != old["identityDigest"] or matrix[
        "newBaselineIdentityDigest"
    ] != new["identityDigest"]:
        raise VersionRegressionInputError("COMPATIBILITY_MATRIX_IDENTITY_MISMATCH")
    _validate_declared_digest(request, "requestDigest", "CHANGE_REQUEST_DIGEST_MISMATCH")
    _validate_declared_digest(matrix, "matrixDigest", "COMPATIBILITY_MATRIX_DIGEST_MISMATCH")
    for side, report in (
        ("OLD", _mapping(snapshot["oldGoldenReport"], "OLD_REPORT_INVALID")),
        ("NEW", _mapping(snapshot["newGoldenReport"], "NEW_REPORT_INVALID")),
    ):
        expected = old["identityDigest"] if side == "OLD" else new["identityDigest"]
        if report["baselineIdentityDigest"] != expected:
            raise VersionRegressionInputError(f"{side}_REPORT_IDENTITY_MISMATCH")


def validate_report(
    report: Mapping[str, Any], *, schema: Mapping[str, Any] | None = None
) -> None:
    try:
        _validate_schema_instance(report, schema or load_schema(), "RegressionEvaluationReport")
    except QualityGateInputError as error:
        raise VersionRegressionReportError(str(error)) from error
    checks = cast(Sequence[Mapping[str, Any]], report["checks"])
    codes = [str(check["code"]) for check in checks]
    if len(codes) != len(set(codes)):
        raise VersionRegressionReportError("REPORT_DUPLICATE_CHECK_CODE")
    disposition = report["candidateDisposition"]
    eligible = bool(report["approvalEligible"])
    if disposition == "BASELINE_CANDIDATE" and not eligible:
        raise VersionRegressionReportError("REPORT_BASELINE_CANDIDATE_NOT_ELIGIBLE")
    if report["synthetic"] and (
        eligible or disposition not in {"CONTRACT_ONLY", "REJECTED"}
    ):
        raise VersionRegressionReportError("REPORT_SYNTHETIC_APPROVAL_SPOOF")
    failed = any(check["passed"] is not True for check in checks)
    if disposition in {"CONTRACT_ONLY", "BASELINE_CANDIDATE"} and failed:
        raise VersionRegressionReportError("REPORT_SUCCESS_CONTAINS_FAILED_CHECK")
    if disposition == "REJECTED" and not failed:
        raise VersionRegressionReportError("REPORT_REJECTION_HAS_NO_FAILED_CHECK")


def evaluate_version_regression(
    snapshot: Mapping[str, Any],
    *,
    artifacts: Mapping[str, str] | None = None,
    schema: Mapping[str, Any] | None = None,
) -> VersionRegressionOutcome:
    validate_input(snapshot, schema=schema)
    old = _mapping(snapshot["oldBaseline"], "OLD_BASELINE_INVALID")
    new = _mapping(snapshot["newCandidate"], "NEW_BASELINE_INVALID")
    old_identity = _mapping(old["identity"], "OLD_IDENTITY_INVALID")
    new_identity = _mapping(new["identity"], "NEW_IDENTITY_INVALID")
    request = _mapping(snapshot["changeRequest"], "CHANGE_REQUEST_INVALID")
    population = _mapping(snapshot["comparisonPopulation"], "POPULATION_INVALID")
    old_report = _mapping(snapshot["oldGoldenReport"], "OLD_REPORT_INVALID")
    new_report = _mapping(snapshot["newGoldenReport"], "NEW_REPORT_INVALID")
    matrix = _mapping(snapshot["compatibilityMatrix"], "COMPATIBILITY_MATRIX_INVALID")

    reasons: list[str] = []
    checks: list[JsonObject] = []

    identity_changed = old["identityDigest"] != new["identityDigest"]
    _check(checks, "BASELINE_IDENTITY_CHANGED", identity_changed, old["identityDigest"], new["identityDigest"])
    if not identity_changed:
        reasons.append("BASELINE_IDENTITY_UNCHANGED")
    if request["oldBaselineIdentityDigest"] != old["identityDigest"] or request[
        "proposedBaselineIdentityDigest"
    ] != new["identityDigest"]:
        reasons.append("CHANGE_REQUEST_IDENTITY_MISMATCH")

    population_ok = _same_population(population, old_report, new_report)
    _check(
        checks,
        "SAME_APPROVED_COHORT_AND_POPULATION",
        population_ok,
        old_report["populationMembershipDigest"],
        new_report["populationMembershipDigest"],
    )
    if not population_ok:
        reasons.append("OLD_NEW_POPULATION_MISMATCH")

    old_quality = _quality_passes(_mapping(old_report["metrics"], "OLD_METRICS_INVALID"))
    new_quality = _quality_passes(_mapping(new_report["metrics"], "NEW_METRICS_INVALID"))
    _check(checks, "OLD_QUALITY_100_0_0", old_quality, True, old_quality)
    _check(checks, "NEW_QUALITY_100_0_0", new_quality, True, new_quality)
    if not old_quality:
        reasons.append("OLD_QUALITY_GATE_FAILED")
    if not new_quality:
        reasons.append("NEW_QUALITY_GATE_FAILED")

    compatibility_passed, compatibility_reasons = _compatibility(
        matrix, old_identity, new_identity, population, request
    )
    reasons.extend(compatibility_reasons)
    schema_migration_passed = not any(
        "SCHEMA_MIGRATION" in code or code == "SCHEMA_CHANGE_MATRIX_MISMATCH"
        for code in compatibility_reasons
    )
    embedding_migration_passed = not any(
        code
        in {
            "SEMANTIC_VECTOR_DIMENSION_DRIFT_UNMIGRATED",
            "EMBEDDING_MIGRATION_RECEIPT_MISSING",
            "UNEXPECTED_EMBEDDING_MIGRATION",
        }
        for code in compatibility_reasons
    )
    _check(checks, "COMPATIBILITY_MATRIX", compatibility_passed, True, compatibility_passed)
    _check(checks, "SCHEMA_MIGRATIONS", schema_migration_passed, True, schema_migration_passed)
    _check(checks, "EMBEDDING_MIGRATION", embedding_migration_passed, True, embedding_migration_passed)

    synthetic = bool(snapshot["synthetic"])
    production = snapshot["profile"] == "PRODUCTION_APPROVAL"
    live = _mapping(snapshot["liveAttestation"], "LIVE_ATTESTATION_INVALID")
    live_passed = live["status"] == "PASSED" and _live_matches(live, old, new, population)
    if production and not live_passed:
        reasons.append("LIVE_ATTESTATION_MISSING_OR_MISMATCHED")
    if synthetic:
        reasons.extend(("SYNTHETIC_NOT_APPROVABLE", "LIVE_ATTESTATION_SEPARATE_GATE"))
    live_requirement_passed = not production or live_passed
    _check(
        checks,
        "LIVE_ATTESTATION",
        live_requirement_passed,
        production,
        live_passed,
    )

    passed = (
        identity_changed
        and population_ok
        and old_quality
        and new_quality
        and compatibility_passed
        and schema_migration_passed
        and embedding_migration_passed
        and (not production or live_passed)
    )
    approval_eligible = passed and production and not synthetic
    disposition = (
        "CONTRACT_ONLY"
        if synthetic and passed
        else "BASELINE_CANDIDATE"
        if approval_eligible
        else "REJECTED"
    )
    if not reasons:
        reasons.append("PRODUCTION_BASELINE_CANDIDATE")
    reason_codes = tuple(dict.fromkeys(reasons))
    paths = artifacts or cast(Mapping[str, str], snapshot["reportOutputs"])
    report: JsonObject = {
        "artifactKind": "AUTOMATIC_GENERATION_VERSION_REGRESSION_REPORT",
        "contractVersion": CONTRACT_VERSION,
        "profile": snapshot["profile"],
        "synthetic": synthetic,
        "changeRequestId": request["changeRequestId"],
        "oldBaselineIdentityDigest": old["identityDigest"],
        "newBaselineIdentityDigest": new["identityDigest"],
        "cohortId": population["cohortId"],
        "cohortDigest": population["cohortDigest"],
        "sourcePopulationDigest": population["sourcePopulationDigest"],
        "populationMembershipDigest": population["populationMembershipDigest"],
        "oldResultPopulationDigest": population["oldResultPopulationDigest"],
        "newResultPopulationDigest": population["newResultPopulationDigest"],
        "thresholds": snapshot["thresholds"],
        "checks": checks,
        "unknownPinCount": 0,
        "missingPinCount": 0,
        "compatibilityPassed": compatibility_passed,
        "schemaMigrationPassed": schema_migration_passed,
        "embeddingMigrationPassed": embedding_migration_passed,
        "partialRolloutDetected": False,
        "liveGatePassed": live_passed,
        "approvalEligible": approval_eligible,
        "candidateDisposition": disposition,
        "reasonCodes": list(reason_codes),
        "artifacts": dict(paths),
        "retention": snapshot["retention"],
    }
    validate_report(report, schema=schema)
    return VersionRegressionOutcome(report=report, reason_codes=reason_codes, passed=passed)


class VersionRegressionEvaluator:
    """Stable production symbol around the pure evaluator."""

    @staticmethod
    def evaluate(snapshot: Mapping[str, Any]) -> VersionRegressionOutcome:
        return evaluate_version_regression(snapshot)

    @staticmethod
    def validate_report(report: Mapping[str, Any]) -> None:
        validate_report(report)


class GenerationBaselineLedgerService:
    """Create immutable candidates and capture/fence job version-set pins."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_candidate(
        self,
        snapshot: Mapping[str, Any],
        *,
        requester_principal_id: str,
        idempotency_key: str,
        protected_manifest_digest: str,
    ) -> GenerationVersionSet:
        outcome = evaluate_version_regression(snapshot)
        requester = _principal(requester_principal_id)
        key = _identifier(idempotency_key, "IDEMPOTENCY_KEY_INVALID")
        manifest_digest = _digest(protected_manifest_digest, "PROTECTED_MANIFEST_DIGEST_INVALID")
        request = _mapping(snapshot["changeRequest"], "CHANGE_REQUEST_INVALID")
        request_digest = str(request["requestDigest"])
        replay = self.session.scalar(
            select(GenerationBaselineChangeRequest).where(
                GenerationBaselineChangeRequest.requester_principal_id == requester,
                GenerationBaselineChangeRequest.idempotency_key == key,
            )
        )
        if replay is not None:
            if replay.request_digest != request_digest:
                raise BaselineGovernanceError("IDEMPOTENCY_CONFLICT")
            result = self.session.get(GenerationVersionSet, replay.version_set_id)
            if result is None:
                raise BaselineGovernanceError("BASELINE_LEDGER_CORRUPT")
            return result

        runtime = self._locked_runtime()
        expected_active = _uuid(request["expectedActiveBaselineId"], "EXPECTED_BASELINE_INVALID")
        if runtime.ledger_version > 0 and (
            runtime.active_version_set_id != expected_active
            or runtime.ledger_version != int(request["expectedLedgerVersion"])
        ):
            raise BaselineGovernanceError("STALE_BASELINE")
        next_version = (
            self.session.scalar(select(func.max(GenerationVersionSet.version))) or 0
        ) + 1
        old = _mapping(snapshot["oldBaseline"], "OLD_BASELINE_INVALID")
        new = _mapping(snapshot["newCandidate"], "NEW_BASELINE_INVALID")
        population = _mapping(snapshot["comparisonPopulation"], "POPULATION_INVALID")
        change = GenerationBaselineChangeRequest(
            id=uuid.uuid4(),
            version_set_id=uuid.uuid4(),
            requester_principal_id=requester,
            idempotency_key=key,
            request_digest=request_digest,
            expected_active_version_set_id=runtime.active_version_set_id,
            expected_ledger_version=runtime.ledger_version,
            old_identity_digest=str(old["identityDigest"]),
            proposed_identity_digest=str(new["identityDigest"]),
            change_request=cast(JsonObject, snapshot["changeRequest"]),
            regression_input_digest=canonical_digest(snapshot),
            regression_report=outcome.report,
            regression_report_digest=canonical_digest(outcome.report),
            compatibility_matrix=cast(JsonObject, snapshot["compatibilityMatrix"]),
            compatibility_matrix_digest=str(
                _mapping(
                    snapshot["compatibilityMatrix"], "COMPATIBILITY_MATRIX_INVALID"
                )["matrixDigest"]
            ),
            status="CONTRACT_ONLY" if snapshot["synthetic"] else (
                "CANDIDATE" if outcome.report["candidateDisposition"] == "BASELINE_CANDIDATE" else "REJECTED"
            ),
        )
        version_set = GenerationVersionSet(
            id=change.version_set_id,
            version=next_version,
            synthetic=bool(snapshot["synthetic"]),
            reviewed=population["cohortStatus"] == "APPROVED",
            identity=cast(JsonObject, new["identity"]),
            identity_digest=str(new["identityDigest"]),
            cohort_id=str(population["cohortId"]),
            cohort_digest=str(population["cohortDigest"]),
            population_membership_digest=str(population["populationMembershipDigest"]),
            regression_report_digest=change.regression_report_digest,
            artifact_manifest_digest=manifest_digest,
            live_attestation_digest=(
                canonical_digest(snapshot["liveAttestation"])
                if _mapping(snapshot["liveAttestation"], "LIVE_ATTESTATION_INVALID")["status"] == "PASSED"
                else None
            ),
            created_by=requester,
        )
        self.session.add_all((change, version_set))
        self.session.flush()
        self._event(
            runtime,
            event_type="CANDIDATE_RECORDED",
            version_set_id=version_set.id,
            principal_id=requester,
            idempotency_key=key,
            request_digest=request_digest,
            before_active=runtime.active_version_set_id,
            after_active=runtime.active_version_set_id,
        )
        return version_set

    def capture_job_pin(self, job: GenerationJob) -> GenerationVersionSet | None:
        runtime = self._locked_runtime()
        baseline_count = self.session.scalar(select(func.count(GenerationVersionSet.id))) or 0
        if job.retry_of_job_id is not None:
            predecessor = self.session.get(GenerationJob, job.retry_of_job_id)
            if (
                predecessor is None
                or predecessor.root_job_id != job.root_job_id
                or predecessor.version_set_id is None
                or predecessor.version_set_digest is None
            ):
                if baseline_count:
                    raise VersionSetFenceError("GENERATION_RETRY_BASELINE_PIN_MISSING")
                return None
            predecessor_set = self.session.get(
                GenerationVersionSet, predecessor.version_set_id
            )
            if (
                predecessor_set is None
                or predecessor_set.identity_digest != predecessor.version_set_digest
            ):
                raise VersionSetFenceError("GENERATION_RETRY_BASELINE_PIN_CORRUPT")
            if job.version_set_id is None:
                job.version_set_id = predecessor.version_set_id
                job.version_set_digest = predecessor.version_set_digest
            elif (
                job.version_set_id != predecessor.version_set_id
                or job.version_set_digest != predecessor.version_set_digest
            ):
                raise VersionSetFenceError("GENERATION_RETRY_BASELINE_PIN_MISMATCH")
            self.session.flush()
            return predecessor_set
        if runtime.active_version_set_id is None:
            if baseline_count:
                raise VersionSetFenceError("GENERATION_BASELINE_NOT_ACTIVE")
            return None
        active = self.session.get(GenerationVersionSet, runtime.active_version_set_id)
        if active is None:
            raise VersionSetFenceError("GENERATION_BASELINE_LEDGER_CORRUPT")
        if job.version_set_id is None:
            job.version_set_id = active.id
            job.version_set_digest = active.identity_digest
        elif job.version_set_id != active.id or job.version_set_digest != active.identity_digest:
            raise VersionSetFenceError("GENERATION_BASELINE_PIN_MISMATCH")
        self.session.flush()
        return active

    def assert_current_job_pin(self, job: GenerationJob) -> GenerationVersionSet | None:
        baseline_count = self.session.scalar(select(func.count(GenerationVersionSet.id))) or 0
        if job.version_set_id is None or job.version_set_digest is None:
            if baseline_count:
                raise VersionSetFenceError("GENERATION_BASELINE_PIN_MISSING")
            return None
        pinned = self.session.get(GenerationVersionSet, job.version_set_id)
        if pinned is None or job.version_set_digest != pinned.identity_digest:
            raise VersionSetFenceError("GENERATION_BASELINE_PIN_MISMATCH")
        if job.retry_of_job_id is not None:
            predecessor = self.session.get(GenerationJob, job.retry_of_job_id)
            if predecessor is None or (
                predecessor.version_set_id,
                predecessor.version_set_digest,
            ) != (job.version_set_id, job.version_set_digest):
                raise VersionSetFenceError("GENERATION_RETRY_BASELINE_PIN_MISMATCH")
        return pinned

    def _locked_runtime(self) -> GenerationBaselineRuntime:
        runtime = self.session.scalar(
            select(GenerationBaselineRuntime)
            .where(GenerationBaselineRuntime.scope == "GLOBAL")
            .with_for_update()
        )
        if runtime is None:
            runtime = GenerationBaselineRuntime(scope="GLOBAL", ledger_version=0)
            self.session.add(runtime)
            self.session.flush()
        return runtime

    def _event(
        self,
        runtime: GenerationBaselineRuntime,
        *,
        event_type: str,
        version_set_id: uuid.UUID,
        principal_id: str,
        idempotency_key: str,
        request_digest: str,
        before_active: uuid.UUID | None,
        after_active: uuid.UUID | None,
        expected_ledger_version: int | None = None,
        committed_ledger_version: int | None = None,
    ) -> GenerationBaselineAuditEvent:
        sequence = (
            self.session.scalar(select(func.max(GenerationBaselineAuditEvent.sequence))) or 0
        ) + 1
        expected_version = (
            runtime.ledger_version
            if expected_ledger_version is None
            else expected_ledger_version
        )
        committed_version = (
            runtime.ledger_version
            if committed_ledger_version is None
            else committed_ledger_version
        )
        event = GenerationBaselineAuditEvent(
            id=uuid.uuid4(),
            sequence=sequence,
            event_type=event_type,
            version_set_id=version_set_id,
            principal_id=principal_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            expected_ledger_version=expected_version,
            committed_ledger_version=committed_version,
            before_active_version_set_id=before_active,
            after_active_version_set_id=after_active,
            receipt_digest=canonical_digest(
                {
                    "sequence": sequence,
                    "eventType": event_type,
                    "versionSetId": str(version_set_id),
                    "principalId": principal_id,
                    "idempotencyKey": idempotency_key,
                    "requestDigest": request_digest,
                    "expectedLedgerVersion": expected_version,
                    "committedLedgerVersion": committed_version,
                    "before": str(before_active) if before_active else None,
                    "after": str(after_active) if after_active else None,
                }
            ),
        )
        self.session.add(event)
        self.session.flush()
        return event


class TwoPersonBaselineApprovalService(GenerationBaselineLedgerService):
    """Seal approval by exactly two independent authorized principals."""

    def approve(
        self,
        version_set_id: uuid.UUID,
        *,
        approvals: Sequence[Mapping[str, Any]],
        protected_manifest_digest: str,
        idempotency_key: str,
    ) -> GenerationBaselineApproval:
        version_set = self.session.scalar(
            select(GenerationVersionSet)
            .where(GenerationVersionSet.id == version_set_id)
            .with_for_update()
        )
        if version_set is None:
            raise BaselineGovernanceError("BASELINE_NOT_FOUND")
        change = self.session.scalar(
            select(GenerationBaselineChangeRequest).where(
                GenerationBaselineChangeRequest.version_set_id == version_set.id
            )
        )
        if change is None:
            raise BaselineGovernanceError("BASELINE_LEDGER_CORRUPT")
        manifest = _digest(protected_manifest_digest, "PROTECTED_MANIFEST_DIGEST_INVALID")
        if version_set.synthetic or not version_set.reviewed or change.status != "CANDIDATE":
            raise BaselineGovernanceError("BASELINE_NOT_APPROVABLE")
        if manifest != version_set.artifact_manifest_digest:
            raise BaselineGovernanceError("PROTECTED_MANIFEST_MISMATCH")
        normalized = [_approval(item) for item in approvals]
        principals = {item["principalId"] for item in normalized}
        roles = {item["role"] for item in normalized}
        if len(normalized) != 2 or len(principals) != 2 or roles != {
            "QUALITY_OWNER",
            "RELEASE_OWNER",
        } or change.requester_principal_id in principals:
            raise BaselineGovernanceError("TWO_PERSON_APPROVAL_INVALID")
        approval_digest = canonical_digest(
            {
                "versionSetId": str(version_set.id),
                "manifestDigest": manifest,
                "approvals": sorted(normalized, key=lambda item: item["role"]),
            }
        )
        existing_by_key = self.session.scalar(
            select(GenerationBaselineApproval).where(
                GenerationBaselineApproval.idempotency_key
                == _identifier(idempotency_key, "IDEMPOTENCY_KEY_INVALID")
            )
        )
        if existing_by_key is not None and existing_by_key.version_set_id != version_set.id:
            raise BaselineGovernanceError("IDEMPOTENCY_CONFLICT")
        existing = self.session.scalar(
            select(GenerationBaselineApproval).where(
                GenerationBaselineApproval.version_set_id == version_set.id
            )
        )
        if existing is not None:
            if existing.approval_digest != approval_digest:
                raise BaselineGovernanceError("APPROVAL_CONFLICT")
            return existing
        first, second = sorted(normalized, key=lambda item: item["role"])
        approval = GenerationBaselineApproval(
            id=uuid.uuid4(),
            version_set_id=version_set.id,
            idempotency_key=_identifier(idempotency_key, "IDEMPOTENCY_KEY_INVALID"),
            protected_manifest_digest=manifest,
            approver_one_principal_id=first["principalId"],
            approver_one_role=first["role"],
            approver_one_decision_digest=first["decisionDigest"],
            approver_two_principal_id=second["principalId"],
            approver_two_role=second["role"],
            approver_two_decision_digest=second["decisionDigest"],
            approval_digest=approval_digest,
        )
        self.session.add(approval)
        self.session.flush()
        runtime = self._locked_runtime()
        self._event(
            runtime,
            event_type="APPROVAL_RECORDED",
            version_set_id=version_set.id,
            principal_id="two-person-approval",
            idempotency_key=approval.idempotency_key,
            request_digest=approval_digest,
            before_active=runtime.active_version_set_id,
            after_active=runtime.active_version_set_id,
        )
        return approval


class AtomicBaselineActivationService(GenerationBaselineLedgerService):
    """Atomically switch the singleton runtime projection and append a receipt."""

    def activate(
        self,
        version_set_id: uuid.UUID,
        *,
        expected_active_version_set_id: uuid.UUID | None,
        expected_ledger_version: int,
        protected_manifest_digest: str,
        principal_id: str,
        idempotency_key: str,
    ) -> GenerationBaselineActivation:
        runtime = self._locked_runtime()
        key = _identifier(idempotency_key, "IDEMPOTENCY_KEY_INVALID")
        principal = _principal(principal_id)
        manifest = _digest(protected_manifest_digest, "PROTECTED_MANIFEST_DIGEST_INVALID")
        request_digest = canonical_digest(
            {
                "operation": "ACTIVATE",
                "versionSetId": str(version_set_id),
                "expectedActiveVersionSetId": (
                    str(expected_active_version_set_id) if expected_active_version_set_id else None
                ),
                "expectedLedgerVersion": expected_ledger_version,
                "protectedManifestDigest": manifest,
                "principalId": principal,
            }
        )
        replay = self.session.scalar(
            select(GenerationBaselineActivation).where(
                GenerationBaselineActivation.idempotency_key == key
            )
        )
        if replay is not None:
            if replay.request_digest != request_digest:
                raise BaselineGovernanceError("IDEMPOTENCY_CONFLICT")
            return replay
        if runtime.active_version_set_id != expected_active_version_set_id or runtime.ledger_version != expected_ledger_version:
            raise BaselineGovernanceError("STALE_BASELINE")
        version_set = self.session.get(GenerationVersionSet, version_set_id)
        approval = self.session.scalar(
            select(GenerationBaselineApproval).where(
                GenerationBaselineApproval.version_set_id == version_set_id
            )
        )
        if version_set is None or approval is None:
            raise BaselineGovernanceError("BASELINE_NOT_APPROVED")
        if version_set.synthetic or not version_set.reviewed or version_set.live_attestation_digest is None:
            raise BaselineGovernanceError("BASELINE_NOT_ACTIVATABLE")
        if manifest != version_set.artifact_manifest_digest or manifest != approval.protected_manifest_digest:
            raise BaselineGovernanceError("PROTECTED_MANIFEST_MISMATCH")
        if version_set.id == runtime.active_version_set_id:
            raise BaselineGovernanceError("BASELINE_ALREADY_ACTIVE")
        committed_version = runtime.ledger_version + 1
        receipt_digest = canonical_digest(
            {
                "requestDigest": request_digest,
                "previousVersionSetId": (
                    str(runtime.active_version_set_id) if runtime.active_version_set_id else None
                ),
                "newVersionSetId": str(version_set.id),
                "approvalDigest": approval.approval_digest,
                "committedLedgerVersion": committed_version,
            }
        )
        activation = GenerationBaselineActivation(
            id=uuid.uuid4(),
            version_set_id=version_set.id,
            previous_version_set_id=runtime.active_version_set_id,
            approval_id=approval.id,
            principal_id=principal,
            idempotency_key=key,
            request_digest=request_digest,
            protected_manifest_digest=manifest,
            expected_ledger_version=runtime.ledger_version,
            committed_ledger_version=committed_version,
            receipt_digest=receipt_digest,
        )
        before = runtime.active_version_set_id
        runtime.active_version_set_id = version_set.id
        runtime.ledger_version = committed_version
        runtime.updated_at = datetime.now(UTC)
        self.session.add(activation)
        self.session.flush()
        self._event(
            runtime,
            event_type="BASELINE_ACTIVATED",
            version_set_id=version_set.id,
            principal_id=activation.principal_id,
            idempotency_key=key,
            request_digest=request_digest,
            before_active=before,
            after_active=version_set.id,
            expected_ledger_version=activation.expected_ledger_version,
            committed_ledger_version=activation.committed_ledger_version,
        )
        return activation


class BaselineRollbackService(GenerationBaselineLedgerService):
    """Restore only a previously activated immutable version set."""

    def rollback(
        self,
        *,
        target_version_set_id: uuid.UUID,
        expected_failed_version_set_id: uuid.UUID,
        expected_ledger_version: int,
        target_artifact_manifest_digest: str,
        reproduction_report: Mapping[str, Any],
        reproduction_artifact_digest: str,
        principal_id: str,
        idempotency_key: str,
    ) -> GenerationBaselineRollback:
        runtime = self._locked_runtime()
        key = _identifier(idempotency_key, "IDEMPOTENCY_KEY_INVALID")
        manifest = _digest(target_artifact_manifest_digest, "PROTECTED_MANIFEST_DIGEST_INVALID")
        reproduction_digest = _digest(reproduction_artifact_digest, "REPRODUCTION_DIGEST_INVALID")
        principal = _principal(principal_id)
        request_digest = canonical_digest(
            {
                "operation": "ROLLBACK",
                "targetVersionSetId": str(target_version_set_id),
                "failedVersionSetId": str(expected_failed_version_set_id),
                "expectedLedgerVersion": expected_ledger_version,
                "targetArtifactManifestDigest": manifest,
                "reproductionArtifactDigest": reproduction_digest,
                "principalId": principal,
            }
        )
        replay = self.session.scalar(
            select(GenerationBaselineRollback).where(
                GenerationBaselineRollback.idempotency_key == key
            )
        )
        if replay is not None:
            if replay.request_digest != request_digest:
                raise BaselineGovernanceError("IDEMPOTENCY_CONFLICT")
            return replay
        if runtime.active_version_set_id != expected_failed_version_set_id or runtime.ledger_version != expected_ledger_version:
            raise BaselineGovernanceError("STALE_BASELINE")
        if target_version_set_id == expected_failed_version_set_id:
            raise BaselineGovernanceError("ROLLBACK_TARGET_IS_FAILED_BASELINE")
        target = self.session.get(GenerationVersionSet, target_version_set_id)
        original_activation = self.session.scalar(
            select(GenerationBaselineActivation)
            .where(GenerationBaselineActivation.version_set_id == target_version_set_id)
            .order_by(GenerationBaselineActivation.committed_ledger_version)
            .limit(1)
        )
        if target is None or original_activation is None or target.synthetic:
            raise BaselineGovernanceError("ROLLBACK_TARGET_NOT_ACTIVATED")
        if target.artifact_manifest_digest != manifest:
            raise BaselineGovernanceError("ROLLBACK_ARTIFACT_MISMATCH")
        if canonical_digest(reproduction_report) != reproduction_digest:
            raise BaselineGovernanceError("ROLLBACK_REPRODUCTION_DIGEST_MISMATCH")
        if reproduction_report.get("reproductionStatus") != "VERIFIED":
            raise BaselineGovernanceError("ROLLBACK_REPRODUCTION_FAILED")
        thresholds = _mapping(
            reproduction_report.get("restoredQualityMetrics"),
            "ROLLBACK_REPRODUCTION_INVALID",
        )
        if thresholds.get("evidenceCoverage") != 1 or any(thresholds.get(name) != 0 for name in _ZERO_METRICS):
            raise BaselineGovernanceError("ROLLBACK_REPRODUCTION_FAILED")
        if reproduction_report.get("targetIdentityDigest") != target.identity_digest:
            raise BaselineGovernanceError("ROLLBACK_IDENTITY_MISMATCH")
        if (
            reproduction_report.get("targetArtifactManifestDigest")
            != target.artifact_manifest_digest
            or reproduction_report.get("targetRegressionReportDigest")
            != target.regression_report_digest
            or reproduction_report.get("targetCohortDigest") != target.cohort_digest
            or reproduction_report.get("targetPopulationMembershipDigest")
            != target.population_membership_digest
            or reproduction_report.get("compatibilityPassed") is not True
            or reproduction_report.get("schemaMigrationPassed") is not True
            or reproduction_report.get("embeddingMigrationPassed") is not True
        ):
            raise BaselineGovernanceError("ROLLBACK_REPRODUCTION_MISMATCH")
        committed_version = runtime.ledger_version + 1
        receipt_digest = canonical_digest(
            {
                "requestDigest": request_digest,
                "targetIdentityDigest": target.identity_digest,
                "originalActivationReceiptDigest": original_activation.receipt_digest,
                "reproductionArtifactDigest": reproduction_digest,
                "committedLedgerVersion": committed_version,
            }
        )
        rollback = GenerationBaselineRollback(
            id=uuid.uuid4(),
            failed_version_set_id=expected_failed_version_set_id,
            target_version_set_id=target.id,
            target_activation_id=original_activation.id,
            principal_id=principal,
            idempotency_key=key,
            request_digest=request_digest,
            target_artifact_manifest_digest=manifest,
            reproduction_report=dict(reproduction_report),
            reproduction_artifact_digest=reproduction_digest,
            expected_ledger_version=runtime.ledger_version,
            committed_ledger_version=committed_version,
            receipt_digest=receipt_digest,
        )
        before = runtime.active_version_set_id
        runtime.active_version_set_id = target.id
        runtime.ledger_version = committed_version
        runtime.updated_at = datetime.now(UTC)
        self.session.add(rollback)
        self.session.flush()
        self._event(
            runtime,
            event_type="ROLLBACK_ACTIVATED",
            version_set_id=target.id,
            principal_id=rollback.principal_id,
            idempotency_key=key,
            request_digest=request_digest,
            before_active=before,
            after_active=target.id,
            expected_ledger_version=rollback.expected_ledger_version,
            committed_ledger_version=rollback.committed_ledger_version,
        )
        return rollback


def _validate_pins(identity: Mapping[str, Any]) -> None:
    for name, value in identity.items():
        if not isinstance(value, str):
            continue
        folded = value.casefold()
        if (
            value != value.strip()
            or any(character.isspace() for character in value)
            or folded in _RESERVED_PINS
            or _MUTABLE_ALIAS_SUFFIX.search(value)
        ):
            raise VersionRegressionInputError(f"UNPINNED_VERSION_VALUE:{name}")
        if any(marker in value for marker in ("*", "^", "~", ">", "<", "||")):
            raise VersionRegressionInputError(f"VERSION_RANGE_FORBIDDEN:{name}")


def _validate_declared_digest(
    value: Mapping[str, Any], field: str, code: str
) -> None:
    body = dict(value)
    declared = body.pop(field, None)
    if declared != canonical_digest(body):
        raise VersionRegressionInputError(code)


def _same_population(
    population: Mapping[str, Any], old: Mapping[str, Any], new: Mapping[str, Any]
) -> bool:
    shared = ("cohortId", "cohortDigest", "sourcePopulationDigest", "populationMembershipDigest")
    if any(old[name] != population[name] or new[name] != population[name] for name in shared):
        return False
    slots = cast(Sequence[Mapping[str, Any]], population["resultSlots"])
    if int(population["rootCount"]) != len(slots):
        return False
    slot_ids = [str(slot["slotId"]) for slot in slots]
    root_ids = [str(slot["generationRootId"]) for slot in slots]
    if len(slot_ids) != len(set(slot_ids)) or len(root_ids) != len(set(root_ids)):
        return False
    return (
        old["resultPopulationDigest"] == population["oldResultPopulationDigest"]
        and new["resultPopulationDigest"] == population["newResultPopulationDigest"]
        and old["baselineIdentityDigest"] is not None
        and new["baselineIdentityDigest"] is not None
    )


def _quality_passes(metrics: Mapping[str, Any]) -> bool:
    return metrics.get("evidenceCoverage") == 1 and all(
        metrics.get(name) == 0 for name in _ZERO_METRICS
    )


def _compatibility(
    matrix: Mapping[str, Any],
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    population: Mapping[str, Any],
    request: Mapping[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    entries = cast(Sequence[Mapping[str, Any]], matrix["entries"])
    by_dimension = {str(entry["dimension"]): entry for entry in entries}
    reasons: list[str] = []
    if len(entries) != len(by_dimension) or set(by_dimension) != set(_COMPATIBILITY_DIMENSIONS):
        reasons.append("COMPATIBILITY_DIMENSION_SET_INVALID")
    changed_dimensions = {
        dimension
        for dimension, fields in _DIMENSION_FIELDS.items()
        if any(old[field] != new[field] for field in fields)
    }
    declared_dimensions = {str(value) for value in request["changedDimensions"]}
    if declared_dimensions != changed_dimensions:
        reasons.append("CHANGE_REQUEST_DIMENSION_SET_MISMATCH")
    for version_field, digest_field in _VERSION_DIGEST_PAIRS:
        if (old[version_field] != new[version_field]) != (
            old[digest_field] != new[digest_field]
        ):
            reasons.append("VERSION_CONTENT_PIN_MISMATCH")
    semantic_config_changed = any(
        old[field] != new[field]
        for field in (
            "semanticDuplicateAlgorithmVersion",
            "semanticDuplicateThreshold",
        )
    )
    if semantic_config_changed != (
        old["semanticDuplicatePolicyDigest"]
        != new["semanticDuplicatePolicyDigest"]
    ):
        reasons.append("SEMANTIC_POLICY_PIN_MISMATCH")
    for entry in entries:
        if (
            entry["changeType"] == "INCOMPATIBLE"
            or entry["oldReaderNewArtifact"] == "REJECTED"
            or entry["newReaderOldArtifact"] == "REJECTED"
            or entry["activationAllowed"] is not True
        ):
            reasons.append("FORWARD_OR_DOWNGRADE_INCOMPATIBLE")
        changed = str(entry["dimension"]) in changed_dimensions
        if changed == (entry["changeType"] == "UNCHANGED"):
            reasons.append("COMPATIBILITY_CHANGE_CLASSIFICATION_MISMATCH")
        if (
            changed
            and entry["dimension"] in _MIGRATION_REQUIRED_DIMENSIONS
            and entry["changeType"] != "MIGRATION_REQUIRED"
        ):
            reasons.append("REQUIRED_MIGRATION_CLASSIFICATION_MISSING")
        if not changed and entry["migrationPlanId"] is not None:
            reasons.append("UNCHANGED_DIMENSION_HAS_MIGRATION")
    migration_items = cast(Sequence[Mapping[str, Any]], matrix["schemaMigrations"])
    migrations = {str(item["migrationPlanId"]): item for item in migration_items}
    if len(migration_items) != len(migrations):
        reasons.append("DUPLICATE_SCHEMA_MIGRATION_PLAN")
    referenced_migrations: set[str] = set()
    for entry in entries:
        if entry["changeType"] != "MIGRATION_REQUIRED":
            continue
        if entry["dimension"] == "EMBEDDING_DIMENSION":
            embedding_plan = matrix["embeddingMigration"]
            if (
                not entry["migrationPlanId"]
                or not isinstance(embedding_plan, Mapping)
                or embedding_plan.get("migrationPlanId") != entry["migrationPlanId"]
            ):
                reasons.append("EMBEDDING_MIGRATION_RECEIPT_MISSING")
            continue
        if (
            not entry["migrationPlanId"]
            or str(entry["migrationPlanId"]) not in migrations
        ):
            reasons.append("SCHEMA_MIGRATION_RECEIPT_MISSING")
        else:
            plan_id = str(entry["migrationPlanId"])
            referenced_migrations.add(plan_id)
            migration = migrations[plan_id]
            if migration["dimension"] != entry["dimension"]:
                reasons.append("SCHEMA_MIGRATION_DIMENSION_MISMATCH")
            if (
                migration.get("sourcePopulationDigest")
                != population["sourcePopulationDigest"]
                or migration.get("populationMembershipDigest")
                != population["populationMembershipDigest"]
            ):
                reasons.append("SCHEMA_MIGRATION_POPULATION_MISMATCH")
    if set(migrations) != referenced_migrations:
        reasons.append("UNREFERENCED_SCHEMA_MIGRATION")
    for dimension, field in _SCHEMA_DIMENSION_FIELDS.items():
        changed = old[field] != new[field]
        schema_entry = by_dimension.get(dimension)
        if changed and (
            schema_entry is None or schema_entry["changeType"] == "UNCHANGED"
        ):
            reasons.append("SCHEMA_CHANGE_MATRIX_MISMATCH")
        if (
            changed
            and schema_entry is not None
            and schema_entry["changeType"] == "MIGRATION_REQUIRED"
        ):
            schema_migration = migrations.get(str(schema_entry["migrationPlanId"]))
            if schema_migration is not None and (
                schema_migration["fromVersion"] != old[field]
                or schema_migration["toVersion"] != new[field]
            ):
                reasons.append("SCHEMA_MIGRATION_VERSION_MISMATCH")
    if old["embeddingDimensions"] != new["embeddingDimensions"]:
        embedding_migration = matrix["embeddingMigration"]
        if not isinstance(embedding_migration, Mapping) or (
            embedding_migration.get("oldDimensions") != old["embeddingDimensions"]
            or embedding_migration.get("newDimensions") != new["embeddingDimensions"]
            or embedding_migration.get("mixedDimensionsObserved") is not False
            or embedding_migration.get("verificationStatus") != "VERIFIED"
            or embedding_migration.get("sourcePopulationDigest")
            != population["sourcePopulationDigest"]
        ):
            reasons.append("SEMANTIC_VECTOR_DIMENSION_DRIFT_UNMIGRATED")
    elif matrix["embeddingMigration"] is not None:
        reasons.append("UNEXPECTED_EMBEDDING_MIGRATION")
    rejected_old = sum(entry["oldReaderNewArtifact"] == "REJECTED" for entry in entries)
    rejected_new = sum(entry["newReaderOldArtifact"] == "REJECTED" for entry in entries)
    if (
        matrix["downgradeIncompatibilityCount"] != rejected_old
        or matrix["forwardIncompatibilityCount"] != rejected_new
    ):
        reasons.append("COMPATIBILITY_COUNT_MISMATCH")
    if matrix["unknownCompatibilityCount"] != 0 or matrix["missingMigrationCount"] != 0:
        reasons.append("COMPATIBILITY_UNKNOWN_OR_MISSING")
    return not reasons, tuple(dict.fromkeys(reasons))


def _live_matches(
    live: Mapping[str, Any],
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    population: Mapping[str, Any],
) -> bool:
    return (
        live.get("providerCredentialed") is True
        and live.get("oldProviderCallObserved") is True
        and live.get("newProviderCallObserved") is True
        and live.get("oldBaselineIdentityDigest") == old["identityDigest"]
        and live.get("newBaselineIdentityDigest") == new["identityDigest"]
        and live.get("populationMembershipDigest") == population["populationMembershipDigest"]
    )


def _check(
    checks: list[JsonObject], code: str, passed: bool, old_value: object, new_value: object
) -> None:
    checks.append(
        {"code": code, "passed": passed, "oldValue": old_value, "newValue": new_value}
    )


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VersionRegressionInputError(code)
    return cast(Mapping[str, Any], value)


def _identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise BaselineGovernanceError(code)
    return value.strip()


def _principal(value: object) -> str:
    return _identifier(value, "PRINCIPAL_INVALID")


def _digest(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise BaselineGovernanceError(code)
    try:
        int(value[7:], 16)
    except ValueError as error:
        raise BaselineGovernanceError(code) from error
    return value


def _uuid(value: object, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as error:
        raise BaselineGovernanceError(code) from error


def _approval(value: Mapping[str, Any]) -> dict[str, str]:
    principal = _principal(value.get("principalId"))
    role = value.get("role")
    if role not in {"QUALITY_OWNER", "RELEASE_OWNER"}:
        raise BaselineGovernanceError("APPROVAL_ROLE_INVALID")
    return {
        "principalId": principal,
        "role": str(role),
        "decisionDigest": _digest(value.get("decisionDigest"), "APPROVAL_DIGEST_INVALID"),
    }


def read_json_object(path: Path) -> JsonObject:
    try:
        value = loads_strict_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, QualityGateInputError) as error:
        raise VersionRegressionInputError("VERSION_REGRESSION_INPUT_UNREADABLE") from error
    if not isinstance(value, dict):
        raise VersionRegressionInputError("VERSION_REGRESSION_INPUT_NOT_OBJECT")
    return cast(JsonObject, value)


def canonical_json(value: Mapping[str, Any]) -> str:
    """Stable human-independent JSON serialization for artifacts."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
