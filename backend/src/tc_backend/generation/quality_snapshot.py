"""Read-only PostgreSQL exporter for Phase-E automatic-generation candidates.

The exporter deliberately supports only fully materialized automatic APPLY
roots.  It reads sealed source/context/result and QA authority rows, validates
their joins, and returns the existing T08 evaluator input contract with every
operational identifier pseudonymized and every text field reduced to a digest
and normalized length.  Review, approval, and live attestations are not
inferred from unrelated ledgers: exported snapshots are always unreviewed
``CANDIDATE`` artifacts with ``liveGate.status=NOT_RUN``.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Final, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from tc_backend.db.models import (
    AuditLog,
    GenerationApply,
    GenerationApplyOutcome,
    GenerationJob,
    GenerationVersionSet,
    KnowledgeEvidence,
    KnowledgeSourceRecord,
    SlackWorkItem,
    TestCaseVersion,
    TestStep,
    WorkspaceSettings,
)
from tc_backend.db.models import (
    GenerationContext as StoredGenerationContext,
)
from tc_backend.generation.application import canonical_sha256
from tc_backend.generation.deduplication import (
    DeduplicationStep,
    ExistingCaseInput,
    GeneratedCaseInput,
    cosine_similarity,
    exact_content_key,
    normalize_exact_text,
)
from tc_backend.generation.models import (
    ContextEvidence,
    GenerationContext,
    UnsupportedClaimPolicy,
)
from tc_backend.generation.quality_gate import (
    CLASSIFICATIONS,
    CONTRACT_VERSION,
    canonical_digest,
    compute_fingerprints,
    validate_input,
)
from tc_backend.generation.reconstruction import GenerationInputV1
from tc_backend.generation.semantic_duplicates import (
    build_sealed_semantic_duplicate_input,
    semantic_duplicate_policy_seal,
)
from tc_backend.generation.validator import GenerationResponseValidator
from tc_backend.privacy.review_candidates import (
    build_review_candidate,
    verify_review_candidate,
    verify_source_seals,
)
from tc_backend.repositories.generation_repository import (
    GenerationRepository,
    UngroundedGenerationContext,
)

EXPORTER_REVISION: Final = "phase-e-postgresql-quality-candidate-v1"
REDACTION_POLICY_VERSION: Final = "hash-only-operational-candidate-v1"
REVIEW_PAYLOAD_SCHEMA_VERSION: Final = "generation-quality-review-projection-v2"
EXACT_DUPLICATE_ALGORITHM_VERSION: Final = "generation-exact-content-v1"
_HEX_64 = re.compile(r"^[a-f0-9]{64}$")
_RESERVED_PIN_VALUES: Final = frozenset({"latest", "unknown", "unconfigured", "unpinned"})
_SAFE_SOURCE_TYPES: Final = frozenset(
    {
        "SLACK_MESSAGE_REVISION",
        "TEST_CASE_VERSION",
        "EXECUTION_ATTEMPT",
        "ISSUE",
        "WORK_ITEM",
        "LEDGER_ENTITY",
    }
)
_URL = re.compile(r"(?i)\b(?:https?|postgres(?:ql)?|redis|amqp|s3)://")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_KR_MOBILE = re.compile(r"\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b")
_SECRET = re.compile(
    r"(?i)(?:\b(?:authorization|api[_-]?token|api[_-]?key|client[_-]?secret)\b|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}|sk-[A-Za-z0-9_-]{12,}|"
    r"(?:bearer|password|secret|api[_-]?key|auth[_-]?token)\s*[:= ]\s*\S+)"
)


class QualitySnapshotExportError(ValueError):
    """Fail-closed, source-text-free candidate export error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def export_quality_candidate_snapshot(
    session: Session,
    *,
    root_job_ids: Sequence[uuid.UUID],
    capture_environment: str,
    report_outputs: Mapping[str, str],
) -> dict[str, Any]:
    """Build one deterministic hash-only T08 candidate from a DB snapshot.

    The caller owns a PostgreSQL REPEATABLE READ, READ ONLY transaction.  This
    function performs SELECTs only and never imports a provider or Slack client.
    """

    roots_requested = tuple(sorted(set(root_job_ids), key=lambda value: value.hex))
    if not roots_requested:
        raise QualitySnapshotExportError("ROOT_SELECTION_EMPTY")
    if len(roots_requested) != len(root_job_ids):
        raise QualitySnapshotExportError("ROOT_SELECTION_DUPLICATE")
    if capture_environment not in {"staging", "production"}:
        raise QualitySnapshotExportError("CAPTURE_ENVIRONMENT_INVALID")
    outputs = _validate_report_outputs(report_outputs)

    jobs = tuple(
        session.scalars(
            select(GenerationJob)
            .where(GenerationJob.root_job_id.in_(roots_requested))
            .order_by(GenerationJob.root_job_id, GenerationJob.attempt_number, GenerationJob.id)
        )
    )
    grouped: dict[uuid.UUID, list[GenerationJob]] = {root_id: [] for root_id in roots_requested}
    for job in jobs:
        grouped.setdefault(job.root_job_id, []).append(job)
    if any(not grouped[root_id] for root_id in roots_requested):
        raise QualitySnapshotExportError("ROOT_NOT_FOUND")

    built_roots: list[dict[str, Any]] = []
    roots: list[dict[str, Any]] = []
    semantic_members: dict[str, tuple[tuple[float, ...], str]] = {}
    semantic_policy_digest: str | None = None
    semantic_threshold: float | None = None
    reference_cases: dict[str, dict[str, Any]] = {}
    reference_categories: dict[str, dict[str, Any]] = {}
    reference_parents: dict[str, dict[str, Any]] = {}
    version_pins: dict[str, Any] | None = None
    version_identity_digest: str | None = None
    capture_times: list[datetime] = []

    for root_id in roots_requested:
        built = _build_root(session, grouped[root_id])
        built_roots.append(built)
        roots.append(built["root"])
        capture_times.append(cast(datetime, built["captured_at"]))
        for key, member in cast(
            Mapping[str, tuple[tuple[float, ...], str]], built["semantic_members"]
        ).items():
            current = semantic_members.get(key)
            if current is not None and current != member:
                raise QualitySnapshotExportError("SEMANTIC_VECTOR_COHORT_MISMATCH")
            semantic_members[key] = member
        root_policy_digest = cast(str, built["semantic_policy_digest"])
        root_threshold = cast(float, built["semantic_threshold"])
        if semantic_policy_digest is None:
            semantic_policy_digest = root_policy_digest
            semantic_threshold = root_threshold
        elif (
            root_policy_digest != semantic_policy_digest
            or root_threshold != semantic_threshold
        ):
            raise QualitySnapshotExportError("SEMANTIC_POLICY_COHORT_MISMATCH")
        pins = cast(dict[str, Any], built["version_pins"])
        identity_digest = cast(str, built["version_identity_digest"])
        if version_pins is None:
            version_pins = pins
            version_identity_digest = identity_digest
        elif pins != version_pins or identity_digest != version_identity_digest:
            raise QualitySnapshotExportError("VERSION_SET_COHORT_MISMATCH")

    assert version_pins is not None and version_identity_digest is not None
    assert semantic_policy_digest is not None and semantic_threshold is not None
    semantic_clusters = _semantic_clusters(
        semantic_members,
        policy_digest=semantic_policy_digest,
        threshold=semantic_threshold,
    )
    for built in built_roots:
        root = cast(dict[str, Any], built["root"])
        selected_job = cast(dict[str, Any], root["selectedJob"])
        result = cast(dict[str, Any], selected_job["result"])
        for case in cast(Sequence[dict[str, Any]], result["cases"]):
            member_key = f"generated:{case['caseSnapshotId']}"
            case["semanticDuplicateClusterId"] = semantic_clusters[member_key]
        cases = cast(Sequence[dict[str, Any]], built["cases"])
        for case in cases:
            member_key = f"existing:{case['caseReferenceId']}"
            case["semanticDuplicateClusterId"] = semantic_clusters[member_key]
        _merge_inventory(reference_cases, cases)
        _merge_inventory(
            reference_categories, cast(Sequence[dict[str, Any]], built["categories"])
        )
        _merge_inventory(reference_parents, cast(Sequence[dict[str, Any]], built["parents"]))
    roots.sort(key=lambda row: cast(str, row["generationRootId"]))
    captured_at = max(capture_times)
    inventory = {
        "existingCases": _inventory_values(reference_cases),
        "categories": _inventory_values(reference_categories),
        "parents": _inventory_values(reference_parents),
    }
    counts = _actual_counts(roots)
    distribution = Counter(cast(str, row["primaryClassification"]) for row in roots)
    selection_digest = canonical_digest(
        {
            "rootJobIds": [row["generationRootId"] for row in roots],
            "versionIdentityDigest": version_identity_digest,
        }
    )
    snapshot: dict[str, Any] = {
        "artifactKind": "AUTOMATIC_GENERATION_QUALITY_EVALUATION_INPUT",
        "contractVersion": CONTRACT_VERSION,
        "profile": "CANDIDATE",
        "dataset": {
            "datasetId": f"operational_candidate_{selection_digest.removeprefix('sha256:')}",
            "kind": "OPERATIONAL_GOLDEN",
            "synthetic": False,
            "description": (
                "Hash-only PostgreSQL operational candidate; requires independent review, "
                "approval, and live attestation before release use."
            ),
            "review": {
                "status": "UNREVIEWED",
                "reviewedBy": None,
                "reviewedAt": None,
                "labelSetId": None,
                "reviewProtocolVersion": None,
                "approvalId": None,
                "reviewAuthorityDigest": None,
                "approvalArtifactDigest": None,
            },
            "declaredCounts": counts,
            "classificationDistribution": {
                name: distribution[name] for name in CLASSIFICATIONS
            },
            "datasetFingerprint": _placeholder_digest(),
            "snapshotFingerprint": _placeholder_digest(),
            "privacy": {
                "containsRawSourceText": False,
                "containsProductionData": True,
                "secretsScan": "CLEAN",
                "piiScan": "CLEAN",
                "redactionPolicyVersion": REDACTION_POLICY_VERSION,
            },
        },
        "capture": {
            "capturedAt": _iso(captured_at),
            "releaseCandidateAt": _iso(captured_at),
            "maxSnapshotAgeSeconds": 604800,
            "environment": capture_environment,
            "sourceSnapshotId": f"postgres_snapshot_{selection_digest.removeprefix('sha256:')}",
            "sourceSnapshotFingerprint": _placeholder_digest(),
            "captureRevision": EXPORTER_REVISION,
            "offlineReplay": True,
            "networkAccess": "DISABLED",
            "providerCallsDuringEvaluation": 0,
            "slackCallsDuringEvaluation": 0,
            "degraded": False,
        },
        "liveGate": {
            "status": "NOT_RUN",
            "liveRunId": None,
            "environment": None,
            "providerCredentialed": False,
            "providerCallObserved": False,
            "approvedBy": None,
            "approvedAt": None,
            "capturedSnapshotFingerprint": None,
        },
        "versionPins": version_pins,
        "thresholds": {
            "evidenceCoverage": 1,
            "retiredKnowledgeMisuseCount": 0,
            "duplicateCount": 0,
            "unresolvedAuthorityCount": 0,
            "provenanceFailureCount": 0,
            "invalidOrBlankResultCount": 0,
            "orderingFailureCount": 0,
        },
        "reportOutputs": outputs,
        "referenceInventory": inventory,
        "generationRoots": roots,
    }
    fingerprints = compute_fingerprints(snapshot)
    snapshot["dataset"]["datasetFingerprint"] = fingerprints["datasetFingerprint"]
    snapshot["capture"]["sourceSnapshotFingerprint"] = fingerprints[
        "sourceSnapshotFingerprint"
    ]
    snapshot["dataset"]["snapshotFingerprint"] = fingerprints["snapshotFingerprint"]
    validate_input(snapshot)
    _assert_public_artifact(snapshot)
    return snapshot


def render_candidate_snapshot(snapshot: Mapping[str, Any]) -> bytes:
    """Return the one canonical on-disk representation used by the CLI."""

    validate_input(snapshot)
    _assert_public_artifact(snapshot)
    return (
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def build_quality_review_envelope(
    snapshot: Mapping[str, Any],
    *,
    root_job_ids: Sequence[uuid.UUID],
    key: bytes,
    key_id: str,
) -> dict[str, Any]:
    """Wrap a private T08 snapshot in the normative minimized E4 envelope.

    The legacy T08 snapshot remains a private source-drift input.  Only its
    aggregate, scanner-clean projection and domain-separated source seals are
    admitted to the review boundary.
    """

    validate_input(snapshot)
    _assert_public_artifact(snapshot)
    roots = tuple(sorted(set(root_job_ids), key=lambda value: value.hex))
    if not roots or len(roots) != len(root_job_ids):
        raise QualitySnapshotExportError("ROOT_SELECTION_INVALID")
    dataset = cast(Mapping[str, Any], snapshot["dataset"])
    capture = cast(Mapping[str, Any], snapshot["capture"])
    live_gate = cast(Mapping[str, Any], snapshot["liveGate"])
    if (
        snapshot["profile"] != "CANDIDATE"
        or dataset["kind"] != "OPERATIONAL_GOLDEN"
        or dataset["synthetic"] is not False
        or cast(Mapping[str, Any], dataset["review"])["status"] != "UNREVIEWED"
        or live_gate["status"] != "NOT_RUN"
    ):
        raise QualitySnapshotExportError("REVIEW_ENVELOPE_SOURCE_BOUNDARY_INVALID")
    captured_at = datetime.fromisoformat(cast(str, capture["capturedAt"]).replace("Z", "+00:00"))
    if captured_at.tzinfo is None:
        raise QualitySnapshotExportError("CAPTURE_TIMESTAMP_INVALID")
    projection = {
        "captureEnvironment": capture["environment"],
        "captureRevision": capture["captureRevision"],
        "capturedAt": capture["capturedAt"],
        "classificationCounts": [
            cast(Mapping[str, Any], dataset["classificationDistribution"])[name]
            for name in CLASSIFICATIONS
        ],
        "datasetKind": "operational-golden",
        "declaredCounts": dataset["declaredCounts"],
        "liveGateStatus": "not-run",
        "materialization": "complete",
        "profile": "candidate",
        "releaseCertified": False,
    }
    source_fields = {
        "operational-root-set": [str(root) for root in roots],
        "private-t08-snapshot": snapshot,
    }
    envelope = build_review_candidate(
        purpose="Operational generation quality review",
        data_class="ACTUAL_MINIMIZED",
        source_is_synthetic=False,
        source_fields=source_fields,
        payload=projection,
        payload_schema_version=REVIEW_PAYLOAD_SCHEMA_VERSION,
        key=key,
        key_id=key_id,
        privacy_domain="generation-quality-review",
        created_at=captured_at,
        sanitization_mode="REJECT",
        sampling_strategy="ALL",
        population_size=len(roots),
        sample_size=len(roots),
    )
    verify_review_candidate(envelope, expected_revision=1)
    verify_source_seals(envelope, source_fields=source_fields, key=key)
    return envelope


def export_quality_review_candidate(
    session: Session,
    *,
    root_job_ids: Sequence[uuid.UUID],
    capture_environment: str,
    report_outputs: Mapping[str, str],
    key: bytes,
    key_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the private reconstruction input and its verified public envelope."""

    snapshot = export_quality_candidate_snapshot(
        session,
        root_job_ids=root_job_ids,
        capture_environment=capture_environment,
        report_outputs=report_outputs,
    )
    return snapshot, build_quality_review_envelope(
        snapshot,
        root_job_ids=root_job_ids,
        key=key,
        key_id=key_id,
    )


def _build_root(session: Session, attempts: Sequence[GenerationJob]) -> dict[str, Any]:
    if [row.attempt_number for row in attempts] != list(range(1, len(attempts) + 1)):
        raise QualitySnapshotExportError("ATTEMPT_HISTORY_NOT_CONTIGUOUS")
    if any(row.generation_trigger != "AUTOMATIC" for row in attempts):
        raise QualitySnapshotExportError("NON_AUTOMATIC_ROOT")
    selected = attempts[-1]
    if selected.status != "SUCCEEDED" or selected.result is None:
        raise QualitySnapshotExportError("ROOT_HAS_NO_TERMINAL_SUCCESS")
    if any(row.status == "SUCCEEDED" for row in attempts[:-1]):
        raise QualitySnapshotExportError("ROOT_HAS_FILTERED_SUCCESS")
    if any(row.status not in {"FAILED", "SUCCEEDED"} for row in attempts):
        raise QualitySnapshotExportError("ROOT_ATTEMPT_NOT_TERMINAL")
    expected_retry: uuid.UUID | None = None
    selected_contexts: tuple[StoredGenerationContext, ...] | None = None
    generation_repository = GenerationRepository(session)
    for row in attempts:
        if row.retry_of_job_id != expected_retry:
            raise QualitySnapshotExportError("ATTEMPT_RETRY_CHAIN_INVALID")
        expected_retry = row.id
        if (
            row.generation_input_capture_state != "SEALED"
            or row.generation_input is None
            or row.generation_input_schema_version != "1.0.0"
            or row.generation_input_digest is None
            or row.context_digest is None
            or row.context_sealed_at is None
        ):
            raise QualitySnapshotExportError("GENERATION_SEAL_MISSING")
        if canonical_sha256(row.generation_input) != row.generation_input_digest:
            raise QualitySnapshotExportError("GENERATION_INPUT_DIGEST_MISMATCH")
        try:
            persisted_context = generation_repository.load_context(row.id)
        except UngroundedGenerationContext as error:
            raise QualitySnapshotExportError("GENERATION_CONTEXT_DIGEST_MISMATCH") from error
        if row.id == selected.id:
            selected_contexts = persisted_context.entries
    if any(
        row.slack_work_item_id != selected.slack_work_item_id
        or row.source_version != selected.source_version
        or row.context_digest != selected.context_digest
        or row.generation_input_digest != selected.generation_input_digest
        for row in attempts
    ):
        raise QualitySnapshotExportError("ATTEMPT_SOURCE_SEAL_DRIFT")

    slack_item = session.get(SlackWorkItem, selected.slack_work_item_id)
    if (
        slack_item is None
        or not slack_item.root_seen
        or slack_item.source_version < selected.source_version
    ):
        raise QualitySnapshotExportError("SLACK_PROVENANCE_MISSING")
    workspace = session.get(WorkspaceSettings, slack_item.workspace_id)
    if workspace is None:
        raise QualitySnapshotExportError("WORKSPACE_PROVENANCE_MISSING")
    if selected_contexts is None:
        raise QualitySnapshotExportError("GENERATION_CONTEXT_MISSING")
    contexts = selected_contexts
    if len(contexts) != selected.context_item_count or not contexts:
        raise QualitySnapshotExportError("CONTEXT_INVENTORY_MISMATCH")

    context_by_result_id: dict[str, tuple[StoredGenerationContext, KnowledgeEvidence]] = {}
    context_output: list[dict[str, Any]] = []
    validation_evidence: list[ContextEvidence] = []
    for context in contexts:
        evidence = session.get(KnowledgeEvidence, context.knowledge_evidence_id)
        source = session.get(KnowledgeSourceRecord, context.source_record_id)
        if evidence is None or source is None:
            raise QualitySnapshotExportError("KNOWLEDGE_PROVENANCE_MISSING")
        excerpt_digest = _digest(
            evidence.excerpt_hash, "EVIDENCE_EXCERPT_HASH_INVALID"
        )
        _digest(source.content_hash, "SOURCE_CONTENT_HASH_INVALID")
        _digest(context.source_content_hash, "SOURCE_CONTENT_HASH_INVALID")
        if (
            evidence.source_record_id != context.source_record_id
            or evidence.knowledge_node_id != context.node_id
            or evidence.knowledge_edge_id != context.edge_id
            or evidence.ontology_schema_id != selected.ontology_schema_id
            or evidence.source_type != source.entity_type
            or evidence.source_id != context.source_entity_id
            or evidence.source_revision != context.source_revision
            or evidence.excerpt_hash != source.content_hash
            or evidence.permalink != source.permalink
            or source.id != context.source_record_id
            or source.source_kind != context.source_kind
            or source.entity_type != context.source_entity_type
            or source.entity_id != context.source_entity_id
            or source.revision_key != context.source_revision
            or source.content_hash != context.source_content_hash
            or source.permalink != context.source_permalink
        ):
            raise QualitySnapshotExportError("KNOWLEDGE_PROVENANCE_MISMATCH")
        if context.node_id is None:
            _validate_edge_snapshot(context, ontology_schema_id=selected.ontology_schema_id)
            continue
        if context.node_snapshot is None:
            raise QualitySnapshotExportError("KNOWLEDGE_NODE_SNAPSHOT_MISSING")
        if context.node_snapshot.get("ontologySchemaId") != str(selected.ontology_schema_id):
            raise QualitySnapshotExportError("KNOWLEDGE_NODE_SNAPSHOT_MISMATCH")
        result_node_id = f"knowledge_node_{context.node_id.hex}"
        if result_node_id in context_by_result_id:
            raise QualitySnapshotExportError("CONTEXT_NODE_DUPLICATE")
        node = _node_seal(context, sealed_at=cast(datetime, selected.context_sealed_at))
        source_seal = _source_seal(context)
        output_id = _opaque("context", context.id)
        context_by_result_id[result_node_id] = (context, evidence)
        context_output.append(
            {
                "contextEntryId": output_id,
                "knowledgeNodeId": _opaque("knowledge", context.node_id),
                "source": source_seal,
                "approvedEvidenceExcerptHashes": [excerpt_digest],
                **node,
            }
        )
        snapshot = context.node_snapshot
        properties = snapshot.get("properties")
        content = snapshot.get("label")
        if isinstance(properties, Mapping) and isinstance(properties.get("content"), str):
            content = properties["content"]
        if not isinstance(content, str) or not content.strip():
            raise QualitySnapshotExportError("KNOWLEDGE_SNAPSHOT_CONTENT_MISSING")
        validation_evidence.append(
            ContextEvidence(
                node_id=result_node_id,
                status=cast(str, snapshot.get("status")),
                content=content,
                active=node["availableAtSeal"] is True,
            )
        )

    raw_result = selected.result
    assert raw_result is not None
    category_ids = _result_category_ids(raw_result)
    validated = GenerationResponseValidator(
        unsupported_claim_policy=UnsupportedClaimPolicy.FLAG
    ).validate(
        raw_result,
        GenerationContext(
            evidence=tuple(validation_evidence),
            category_ids=frozenset(category_ids),
        ),
    )
    if selected.schema_version != validated.output_schema_version:
        raise QualitySnapshotExportError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    result_digest = canonical_sha256(raw_result)

    apply = session.scalar(
        select(GenerationApply).where(GenerationApply.generation_job_id == selected.id)
    )
    if apply is None:
        raise QualitySnapshotExportError("GENERATION_APPLY_MISSING")
    if (
        apply.context_digest != selected.context_digest
        or apply.result_digest != result_digest
        or not isinstance(apply.receipt, Mapping)
    ):
        raise QualitySnapshotExportError("GENERATION_APPLY_SEAL_MISMATCH")
    outcomes = tuple(
        session.scalars(
            select(GenerationApplyOutcome)
            .where(GenerationApplyOutcome.generation_apply_id == apply.id)
            .order_by(GenerationApplyOutcome.generated_case_index, GenerationApplyOutcome.id)
        )
    )
    if [row.generated_case_index for row in outcomes] != list(range(len(validated.cases))):
        raise QualitySnapshotExportError("APPLY_OUTCOME_INVENTORY_MISMATCH")
    placement = _validate_apply_receipt(apply, selected, outcomes)

    try:
        sealed_input = GenerationInputV1.model_validate(selected.generation_input)
    except Exception as error:
        raise QualitySnapshotExportError("SEALED_GENERATION_INPUT_INVALID") from error
    _validate_sealed_slack_input(sealed_input, selected, slack_item, contexts)
    existing_inputs = tuple(
        ExistingCaseInput(
            case_id=row.test_case_id,
            case_key=row.case_key,
            summary=row.summary,
            steps=tuple(
                DeduplicationStep(action=step.action, precondition=step.precondition)
                for step in row.steps
            ),
            case_expected_result=row.case_expected_result or "",
        )
        for row in sealed_input.existing_cases
    )
    sealed_existing_by_case_id = {
        row.test_case_id: row for row in sealed_input.existing_cases
    }
    generated_inputs = tuple(
        GeneratedCaseInput(
            summary=case.summary,
            steps=tuple(
                DeduplicationStep(action=step.action, precondition=step.precondition)
                for step in case.steps
            ),
            case_expected_result=case.case_expected_result,
        )
        for case in validated.cases
    )
    semantic = build_sealed_semantic_duplicate_input(
        generated_inputs,
        existing_inputs,
        source_workspace_id=f"workspace_{slack_item.workspace_id.hex}",
        source_workspace_version=workspace.version,
        context_digest=selected.context_digest,
        result_digest=result_digest,
    )
    audit = session.get(AuditLog, apply.audit_id)
    receipt = cast(Mapping[str, Any], apply.receipt)
    if (
        audit is None
        or not isinstance(audit.after, Mapping)
        or audit.workspace_id != slack_item.workspace_id
        or audit.entity_type != "GENERATION_APPLY"
        or audit.entity_id != apply.id
        or audit.action != "APPLY_GENERATION_JOB"
        or audit.before is not None
        or audit.occurred_at.astimezone(UTC) != apply.applied_at.astimezone(UTC)
        or audit.after.get("generationJobId") != f"generation_job_{selected.id.hex}"
        or audit.after.get("contextDigest") != selected.context_digest
        or audit.after.get("resultDigest") != result_digest
        or audit.after.get("placementDigest") != apply.placement_digest
        or audit.after.get("counts") != receipt.get("counts")
        or audit.after.get("transactionStatus") != "COMMITTED"
        or audit.after.get("semanticDuplicateSeal") != semantic.public_seal()
    ):
        raise QualitySnapshotExportError("SEMANTIC_INVENTORY_SEAL_MISMATCH")
    existing_by_case_id = {row.case_id: row for row in existing_inputs}
    existing_vectors = dict(semantic.existing_vectors)
    existing_vector_digests = dict(semantic.existing_vector_digests)
    semantic_members: dict[str, tuple[tuple[float, ...], str]] = {}
    output_cases: list[dict[str, Any]] = []
    case_inventory: list[dict[str, Any]] = []
    for existing_input in existing_inputs:
        reference_id = _opaque("case", existing_input.case_id)
        case_inventory.append(
            {
                "caseReferenceId": reference_id,
                "activeAtSeal": True,
                "exactContentKey": f"sha256:{exact_content_key(existing_input)}",
                "semanticDuplicateClusterId": "pending_global_cluster",
            }
        )
        semantic_members[f"existing:{reference_id}"] = (
            existing_vectors[existing_input.case_id],
            existing_vector_digests[existing_input.case_id],
        )
    for index, (case, case_input, outcome) in enumerate(
        zip(
            validated.cases,
            generated_inputs,
            outcomes,
            strict=True,
        )
    ):
        computed_exact = exact_content_key(case_input)
        if outcome.exact_content_key != computed_exact:
            raise QualitySnapshotExportError("APPLY_EXACT_KEY_MISMATCH")
        _validate_apply_outcome(session, outcome, case, selected, result_digest)
        cited = []
        for evidence_id in case.evidence_node_ids:
            context_pair = context_by_result_id.get(evidence_id)
            if context_pair is None:
                raise QualitySnapshotExportError("RESULT_EVIDENCE_NOT_IN_CONTEXT")
            cited.append(context_pair)
        references: list[dict[str, Any]] = []
        targets = [("CASE", None), *[("STEP", step.sequence) for step in case.steps]]
        for unit, sequence in targets:
            for context, evidence in cited:
                references.append(
                    _evidence_reference(
                        root_id=selected.root_job_id,
                        case_index=index,
                        unit=unit,
                        sequence=sequence,
                        context=context,
                        evidence=evidence,
                    )
                )
        case_snapshot_id = _opaque("case_snapshot", outcome.id)
        semantic_members[f"generated:{case_snapshot_id}"] = (
            semantic.generated_vectors[index],
            semantic.generated_vector_digests[index],
        )
        if outcome.outcome == "REUSED":
            reused_case_id = f"case_{outcome.test_case_id.hex}"
            reused = existing_by_case_id.get(reused_case_id)
            sealed_reused = sealed_existing_by_case_id.get(reused_case_id)
            if reused is None or sealed_reused is None:
                raise QualitySnapshotExportError("REUSED_CASE_NOT_IN_SEALED_INPUT")
            if (
                outcome.match_reason not in {"EXACT", "SEMANTIC"}
                or sealed_reused.test_case_version_id
                != f"case_version_{outcome.test_case_version_id.hex}"
            ):
                raise QualitySnapshotExportError("REUSED_CASE_VERSION_MISMATCH")
            if outcome.match_reason == "EXACT" and exact_content_key(reused) != computed_exact:
                raise QualitySnapshotExportError("REUSED_EXACT_CASE_MISMATCH")
            if (
                outcome.match_reason == "SEMANTIC"
                and cosine_similarity(
                    semantic.generated_vectors[index], existing_vectors[reused_case_id]
                )
                < semantic.policy.threshold
            ):
                raise QualitySnapshotExportError("REUSED_SEMANTIC_CASE_MISMATCH")
        elif outcome.match_reason is not None:
            raise QualitySnapshotExportError("CREATED_CASE_MATCH_REASON_INVALID")
        output_cases.append(
            {
                "caseIndex": index,
                "caseSnapshotId": case_snapshot_id,
                "summary": _text_seal(case.summary),
                "caseExpectedResult": _text_seal(case.case_expected_result),
                "generationReason": _text_seal(case.generation_reason),
                "exactContentKey": f"sha256:{computed_exact}",
                "semanticDuplicateClusterId": "pending_global_cluster",
                "steps": [
                    {
                        "sequence": step.sequence,
                        "precondition": _text_seal(step.precondition or ""),
                        "action": _text_seal(step.action),
                        # Production v1 stores a case-level oracle.  The quality
                        # projection binds that same oracle to each covered step.
                        "expectedResult": _text_seal(case.case_expected_result),
                    }
                    for step in case.steps
                ],
                "evidenceReferences": references,
                "placement": {
                    "categoryDecision": "REUSE_EXISTING",
                    "categoryCanonicalKey": placement["category_key"],
                    "parentDecision": placement["parent_decision"],
                    "parentCanonicalKey": placement["parent_key"],
                },
                "materialization": {
                    "kind": f"APPLY_{outcome.outcome}",
                    "applyId": _opaque("apply", apply.id),
                    "applyOutcomeId": _opaque("apply_outcome", outcome.id),
                    "suggestionId": None,
                    "suggestionTargetId": None,
                    "sourceChangeAssessmentId": None,
                    "generationLineageId": None,
                    "baselineCaseVersionId": None,
                    "releaseReceiptId": None,
                    "sourceWorkspaceId": _opaque("workspace", slack_item.workspace_id),
                    "sourceGenerationJobId": _opaque("generation_job", selected.id),
                    "sourceRootJobId": _opaque("generation_root", selected.root_job_id),
                    "sourceVersion": selected.source_version,
                    "contextDigest": _digest(selected.context_digest, "CONTEXT_DIGEST_INVALID"),
                    "inputDigest": _digest(
                        selected.generation_input_digest, "INPUT_DIGEST_INVALID"
                    ),
                    "resultDigest": f"sha256:{result_digest}",
                },
            }
        )

    pins, identity_digest = _version_pins(session, selected, attempts)
    workspace_id = _opaque("workspace", slack_item.workspace_id)
    root_output_id = _opaque("generation_root", selected.root_job_id)
    attempt_history = [
        {
            "generationJobId": _opaque("generation_job", row.id),
            "rootJobId": root_output_id,
            "workspaceId": workspace_id,
            "sourceVersion": row.source_version,
            "retryOfJobId": (
                _opaque("generation_job", row.retry_of_job_id)
                if row.retry_of_job_id is not None
                else None
            ),
            "attemptNumber": row.attempt_number,
            "status": row.status,
            "contextDigest": _digest(row.context_digest, "CONTEXT_DIGEST_INVALID"),
            "inputDigest": _digest(row.generation_input_digest, "INPUT_DIGEST_INVALID"),
            "resultDigest": (
                f"sha256:{canonical_sha256(row.result)}" if row.result is not None else None
            ),
            "rejectionCode": (
                _opaque("rejection", row.error_code) if row.error_code is not None else None
            ),
        }
        for row in attempts
    ]
    selected_job_id = _opaque("generation_job", selected.id)
    sealed_at = cast(datetime, selected.context_sealed_at)
    root = {
        "generationRootId": root_output_id,
        "workspaceId": workspace_id,
        "slackWorkItemId": _opaque("slack_work_item", selected.slack_work_item_id),
        "sourceVersion": selected.source_version,
        "inputDigest": _digest(selected.generation_input_digest, "INPUT_DIGEST_INVALID"),
        "sealedSourceTime": _iso(sealed_at),
        "primaryClassification": "GROUNDED_ACTIVE",
        "contextSeal": {
            "contextSnapshotId": _opaque("context_snapshot", selected.id),
            "contextDigest": _digest(selected.context_digest, "CONTEXT_DIGEST_INVALID"),
            "sealedAt": _iso(sealed_at),
            "entries": context_output,
        },
        "attemptHistory": attempt_history,
        "selectedJob": {
            "generationJobId": selected_job_id,
            "rootJobId": root_output_id,
            "workspaceId": workspace_id,
            "sourceVersion": selected.source_version,
            "retryOfJobId": (
                _opaque("generation_job", selected.retry_of_job_id)
                if selected.retry_of_job_id is not None
                else None
            ),
            "attemptNumber": selected.attempt_number,
            "trigger": "AUTOMATIC",
            "status": "SUCCEEDED",
            "inputSchemaVersion": "1.0.0",
            "outputSchemaVersion": "1.0.0",
            "contextDigest": _digest(selected.context_digest, "CONTEXT_DIGEST_INVALID"),
            "inputDigest": _digest(selected.generation_input_digest, "INPUT_DIGEST_INVALID"),
            "resultDigest": f"sha256:{result_digest}",
            "result": {
                "resultSnapshotId": _opaque("generation_result", selected.id),
                "workspaceId": workspace_id,
                "outputSchemaVersion": "1.0.0",
                "validationStatus": "VALID",
                "resultDigest": f"sha256:{result_digest}",
                "cases": output_cases,
            },
        },
    }
    category_inventory = [
        {
            "referenceId": _opaque("category", row.category_id),
            "activeAtSeal": True,
            "canonicalKey": canonical_digest({"category": row.category_id}),
        }
        for row in sealed_input.category_candidates
    ]
    if placement["category_identity"] not in {
        row.category_id for row in sealed_input.category_candidates
    }:
        raise QualitySnapshotExportError("APPLY_CATEGORY_NOT_IN_SEALED_INPUT")
    parent_inventory = []
    if placement["parent_decision"] == "REUSE_EXISTING":
        parent_inventory.append(
            {
                "referenceId": _opaque("parent", placement["parent_identity"]),
                "activeAtSeal": True,
                "canonicalKey": placement["parent_key"],
            }
        )
    captured_at = max(cast(datetime, selected.completed_at), apply.applied_at)
    return {
        "root": root,
        "cases": case_inventory,
        "categories": category_inventory,
        "parents": parent_inventory,
        "version_pins": pins,
        "version_identity_digest": identity_digest,
        "semantic_members": semantic_members,
        "semantic_policy_digest": semantic.policy.digest,
        "semantic_threshold": semantic.policy.threshold,
        "captured_at": captured_at,
    }


def _version_pins(
    session: Session,
    selected: GenerationJob,
    attempts: Sequence[GenerationJob],
) -> tuple[dict[str, Any], str]:
    if selected.version_set_id is None or selected.version_set_digest is None:
        raise QualitySnapshotExportError("VERSION_SET_PIN_MISSING")
    version_set = session.get(GenerationVersionSet, selected.version_set_id)
    if version_set is None or not isinstance(version_set.identity, Mapping):
        raise QualitySnapshotExportError("VERSION_SET_AUTHORITY_MISSING")
    if (
        version_set.identity_digest != selected.version_set_digest
        or canonical_digest(version_set.identity) != version_set.identity_digest
        or any(
            row.version_set_id != selected.version_set_id
            or row.version_set_digest != selected.version_set_digest
            for row in attempts
        )
    ):
        raise QualitySnapshotExportError("VERSION_SET_PIN_MISMATCH")
    identity = version_set.identity
    required = {
        "generationPolicyVersion",
        "generationPolicyDigest",
        "promptVersion",
        "promptDigest",
        "providerId",
        "modelId",
        "modelRevision",
        "embeddingProviderId",
        "embeddingModelId",
        "embeddingRevision",
        "embeddingDimensions",
        "semanticDuplicateAlgorithmVersion",
        "semanticDuplicateThreshold",
        "semanticDuplicatePolicyDigest",
        "exactDuplicateAlgorithmVersion",
        "generationInputSchemaVersion",
        "generationOutputSchemaVersion",
        "suggestionSchemaVersion",
        "qualityGateSchemaVersion",
        "ontologySchemaVersion",
    }
    if not required.issubset(identity):
        raise QualitySnapshotExportError("VERSION_SET_IDENTITY_INCOMPLETE")
    for name in required - {"embeddingDimensions", "semanticDuplicateThreshold"}:
        value = identity[name]
        if (
            not isinstance(value, str)
            or not value
            or value.strip() != value
            or value.casefold() in _RESERVED_PIN_VALUES
            or any(marker in value for marker in ("*", "^", "~", ">", "<"))
        ):
            raise QualitySnapshotExportError("VERSION_SET_IDENTITY_MALFORMED")
    if (
        selected.provider != identity["providerId"]
        or selected.model != identity["modelId"]
        or selected.prompt_version != identity["promptVersion"]
        or selected.generation_input_schema_version != identity["generationInputSchemaVersion"]
        or selected.schema_version != identity["generationOutputSchemaVersion"]
    ):
        raise QualitySnapshotExportError("JOB_VERSION_IDENTITY_MISMATCH")
    policy = semantic_duplicate_policy_seal()
    if (
        identity["embeddingProviderId"] != policy.provider_id
        or identity["embeddingModelId"] != policy.model_id
        or str(identity["embeddingRevision"]) != policy.model_revision
        or identity["embeddingDimensions"] != policy.dimensions
        or identity["semanticDuplicateAlgorithmVersion"] != policy.version
        or identity["semanticDuplicateThreshold"] != policy.threshold
        or identity["semanticDuplicatePolicyDigest"] != policy.digest
    ):
        raise QualitySnapshotExportError("SEMANTIC_POLICY_PIN_MISMATCH")
    for name in ("generationPolicyDigest", "promptDigest"):
        _digest(identity[name], "VERSION_SET_DIGEST_INVALID")
    if identity["qualityGateSchemaVersion"] != CONTRACT_VERSION:
        raise QualitySnapshotExportError("QUALITY_CONTRACT_PIN_MISMATCH")
    suggestion = identity["suggestionSchemaVersion"]
    if suggestion not in {"1.0.0", "generation-suggestion/1.0.0"}:
        raise QualitySnapshotExportError("SUGGESTION_SCHEMA_PIN_MISMATCH")
    safe_semantic_digest = canonical_digest(
        {
            "algorithmVersion": policy.version,
            "providerId": policy.provider_id,
            "modelId": policy.model_id,
            "modelRevision": policy.model_revision,
            "dimensions": policy.dimensions,
            "threshold": policy.threshold,
        }
    )
    return (
        {
            "generationPolicyVersion": _opaque("policy", identity["generationPolicyVersion"]),
            "generationPolicyDigest": identity["generationPolicyDigest"],
            "promptVersion": _opaque("prompt", identity["promptVersion"]),
            "promptDigest": identity["promptDigest"],
            "providerId": _opaque("provider", identity["providerId"]),
            "modelId": _opaque("model", identity["modelId"]),
            "modelRevision": _opaque("model_revision", identity["modelRevision"]),
            "embeddingProviderId": policy.provider_id,
            "embeddingModelId": policy.model_id,
            "embeddingRevision": policy.model_revision,
            "embeddingDimensions": policy.dimensions,
            "semanticDuplicateThreshold": policy.threshold,
            "semanticDuplicateAlgorithmVersion": policy.version,
            "semanticDuplicatePolicyDigest": safe_semantic_digest,
            "exactDuplicateAlgorithmVersion": _opaque(
                "exact_algorithm", identity["exactDuplicateAlgorithmVersion"]
            ),
            "inputSchemaVersion": "1.0.0",
            "outputSchemaVersion": "1.0.0",
            "suggestionSchemaVersion": "1.0.0",
            "qualityGateSchemaVersion": CONTRACT_VERSION,
            "ontologySchemaVersion": _opaque("ontology", identity["ontologySchemaVersion"]),
        },
        version_set.identity_digest,
    )


def _validate_sealed_slack_input(
    sealed: GenerationInputV1,
    job: GenerationJob,
    slack_item: SlackWorkItem,
    contexts: Sequence[StoredGenerationContext],
) -> None:
    sealed_at = job.context_sealed_at
    if sealed_at is None:
        raise QualitySnapshotExportError("SEALED_SLACK_INPUT_MISMATCH")
    root_contexts = [
        context
        for context in contexts
        if context.node_id is not None
        and isinstance(context.node_snapshot, Mapping)
        and context.node_snapshot.get("nodeType") == "WORK_ITEM"
        and context.node_snapshot.get("entityType") == "SLACK_WORK_ITEM"
        and context.node_snapshot.get("entityId") == str(slack_item.id)
    ]
    if not root_contexts:
        raise QualitySnapshotExportError("SEALED_SLACK_ROOT_MISSING")
    root_node_id = root_contexts[0].node_id
    if root_node_id is None or any(context.node_id != root_node_id for context in root_contexts):
        raise QualitySnapshotExportError("SEALED_SLACK_ROOT_AMBIGUOUS")
    expected_revisions = list(
        dict.fromkeys(_source_revision_identifier(context) for context in root_contexts)
    )
    sealed_permalinks = {
        context.source_permalink
        for context in root_contexts
        if context.source_permalink is not None
    }
    slack = sealed.slack_work_item
    if (
        sealed.request_id != f"generation_request_{job.id.hex}"
        or sealed.requested_output_schema_version != job.schema_version
        or sealed.created_at.astimezone(UTC) != sealed_at.astimezone(UTC)
        or slack.work_item_id != f"slack_work_item_{slack_item.id.hex}"
        or slack.evidence_node_id != f"knowledge_node_{root_node_id.hex}"
        or slack.source_version != job.source_version
        or slack.source_revision_ids != expected_revisions
        or slack.permalink not in sealed_permalinks
    ):
        raise QualitySnapshotExportError("SEALED_SLACK_INPUT_MISMATCH")

    sealed_node_ids = {
        f"knowledge_node_{context.node_id.hex}"
        for context in contexts
        if context.node_id is not None
    }
    referenced_node_ids = {
        *(row.node_id for row in sealed.active_knowledge),
        *(row.node_id for row in sealed.existing_cases),
        *(row.node_id for row in sealed.failed_or_blocked_executions),
        *(row.node_id for row in sealed.open_issues),
    }
    if not referenced_node_ids.issubset(sealed_node_ids) or sealed.extra_sources:
        raise QualitySnapshotExportError("SEALED_INPUT_CONTEXT_INVENTORY_MISMATCH")


def _source_revision_identifier(context: StoredGenerationContext) -> str:
    prefixes = {
        "SLACK_MESSAGE_REVISION": "slack_revision",
        "TEST_CASE_VERSION": "case_version",
        "EXECUTION_ATTEMPT": "execution_attempt",
        "OUTBOX_EVENT": "outbox_event",
    }
    prefix = prefixes.get(context.source_entity_type, "source_revision")
    return f"{prefix}_{context.source_entity_id.hex}"


def _validate_apply_receipt(
    apply: GenerationApply,
    job: GenerationJob,
    outcomes: Sequence[GenerationApplyOutcome],
) -> dict[str, str]:
    receipt = apply.receipt
    cases = receipt.get("cases")
    counts = receipt.get("counts")
    parent = receipt.get("parent")
    expected_counts = {
        "generated": len(outcomes),
        "created": sum(outcome.outcome == "CREATED" for outcome in outcomes),
        "reused": sum(outcome.outcome == "REUSED" for outcome in outcomes),
    }
    _digest(apply.placement_digest, "APPLY_PLACEMENT_DIGEST_INVALID")
    if (
        apply.generation_job_id != job.id
        or receipt.get("id") != f"generation_apply_{apply.id.hex}"
        or receipt.get("generationJobId") != f"generation_job_{job.id.hex}"
        or receipt.get("jobStatus") != "SUCCEEDED"
        or receipt.get("contextDigest") != job.context_digest
        or receipt.get("resultDigest") != apply.result_digest
        or receipt.get("placementDigest") != apply.placement_digest
        or receipt.get("transactionStatus") != "COMMITTED"
        or receipt.get("auditId") != f"audit_{apply.audit_id.hex}"
        or counts != expected_counts
        or not isinstance(parent, Mapping)
        or parent.get("workItemId") != f"work_item_{apply.parent_work_item_id.hex}"
        or not isinstance(cases, list)
        or cases != [outcome.snapshot for outcome in outcomes]
        or any(outcome.generation_apply_id != apply.id for outcome in outcomes)
    ):
        raise QualitySnapshotExportError("APPLY_RECEIPT_SEAL_MISMATCH")
    applied_at = _parse_timestamp(
        receipt.get("appliedAt"), "APPLY_RECEIPT_APPLIED_AT_INVALID"
    )
    if applied_at != apply.applied_at.astimezone(UTC):
        raise QualitySnapshotExportError("APPLY_RECEIPT_APPLIED_AT_MISMATCH")
    return _placement_from_receipt(receipt)


def _validate_apply_outcome(
    session: Session,
    outcome: GenerationApplyOutcome,
    generated: Any,
    job: GenerationJob,
    result_digest: str,
) -> None:
    if outcome.outcome not in {"CREATED", "REUSED"}:
        raise QualitySnapshotExportError("APPLY_OUTCOME_INVALID")
    snapshot = outcome.snapshot
    if not isinstance(snapshot, Mapping):
        raise QualitySnapshotExportError("APPLY_OUTCOME_SNAPSHOT_INVALID")
    if (
        snapshot.get("generatedCaseIndex") != outcome.generated_case_index
        or snapshot.get("exactContentKey") != outcome.exact_content_key
        or snapshot.get("outcome") != outcome.outcome
    ):
        raise QualitySnapshotExportError("APPLY_OUTCOME_SNAPSHOT_MISMATCH")
    version = session.get(TestCaseVersion, outcome.test_case_version_id)
    if version is None or version.test_case_id != outcome.test_case_id:
        raise QualitySnapshotExportError("QA_VERSION_PROVENANCE_MISSING")
    if outcome.outcome == "CREATED":
        steps = tuple(
            session.scalars(
                select(TestStep)
                .where(TestStep.test_case_version_id == version.id)
                .order_by(TestStep.sequence, TestStep.id)
            )
        )
        if (
            version.summary != generated.summary
            or version.expected_result != generated.case_expected_result
            or [row.sequence for row in steps]
            != [row.sequence for row in generated.steps]
            or any(
                persisted.action != proposed.action
                or persisted.precondition != proposed.precondition
                for persisted, proposed in zip(steps, generated.steps, strict=True)
            )
        ):
            raise QualitySnapshotExportError("QA_VERSION_CONTENT_MISMATCH")
        source = version.source_metadata
        if not isinstance(source, Mapping) or (
            source.get("generationJobId") != f"generation_job_{job.id.hex}"
            or source.get("contextDigest") != job.context_digest
            or source.get("resultDigest") != result_digest
        ):
            raise QualitySnapshotExportError("QA_VERSION_SOURCE_SEAL_MISMATCH")


def _placement_from_receipt(receipt: Mapping[str, Any]) -> dict[str, str]:
    parent = receipt.get("parent")
    if not isinstance(parent, Mapping):
        raise QualitySnapshotExportError("APPLY_PLACEMENT_RECEIPT_MISSING")
    outcome = parent.get("outcome")
    category = parent.get("categoryId")
    parent_id = parent.get("workItemId")
    if outcome not in {"CREATED", "REUSED"} or not isinstance(category, str) or not category:
        raise QualitySnapshotExportError("APPLY_PLACEMENT_RECEIPT_INVALID")
    if not isinstance(parent_id, str) or not parent_id:
        raise QualitySnapshotExportError("APPLY_PARENT_IDENTITY_MISSING")
    if outcome == "CREATED":
        raise QualitySnapshotExportError("PARENT_CREATE_INVENTORY_UNSEALED")
    return {
        "category_identity": category,
        "category_key": canonical_digest({"category": category}),
        "parent_identity": parent_id,
        "parent_key": canonical_digest({"parent": parent_id}),
        "parent_decision": "CREATE_NEW" if outcome == "CREATED" else "REUSE_EXISTING",
    }


def _semantic_clusters(
    members: Mapping[str, tuple[tuple[float, ...], str]],
    *,
    policy_digest: str,
    threshold: float,
) -> dict[str, str]:
    """Build global deterministic components across every selected root."""

    nodes = [(key, *member) for key, member in sorted(members.items())]
    parents = list(range(len(nodes)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    for left in range(len(nodes)):
        for right in range(left + 1, len(nodes)):
            if (
                cosine_similarity(nodes[left][1], nodes[right][1]) >= threshold
            ):
                union(left, right)

    component_members: dict[int, list[str]] = {}
    for index, (_, _, digest) in enumerate(nodes):
        component_members.setdefault(find(index), []).append(digest)
    cluster_by_root = {
        root: "semantic_"
        + canonical_digest(
            {
                "policyDigest": policy_digest,
                "memberVectorDigests": sorted(digests),
            }
        ).removeprefix("sha256:")
        for root, digests in component_members.items()
    }
    return {key: cluster_by_root[find(index)] for index, (key, _, _) in enumerate(nodes)}


def _evidence_reference(
    *,
    root_id: uuid.UUID,
    case_index: int,
    unit: str,
    sequence: int | None,
    context: StoredGenerationContext,
    evidence: KnowledgeEvidence,
) -> dict[str, Any]:
    return {
        "evidenceReferenceId": _opaque(
            "evidence_reference",
            (root_id, case_index, unit, sequence, context.id),
        ),
        "coverageTarget": {"unit": unit, "stepSequence": sequence},
        "contextEntryId": _opaque("context", context.id),
        "knowledgeNodeId": _opaque("knowledge", context.node_id),
        "citationSourceRevisionId": _opaque(
            "source_revision", (context.source_entity_id, context.source_revision)
        ),
        "source": _source_seal(context),
        "citationExcerptHash": _digest(
            evidence.excerpt_hash, "EVIDENCE_EXCERPT_HASH_INVALID"
        ),
    }


def _source_seal(context: StoredGenerationContext) -> dict[str, str]:
    source_type = context.source_entity_type
    if source_type not in _SAFE_SOURCE_TYPES:
        source_type = "LEDGER_ENTITY"
    return {
        "sourceType": source_type,
        "sourceEntityId": _opaque("source_entity", context.source_entity_id),
        "sourceRevisionId": _opaque(
            "source_revision", (context.source_entity_id, context.source_revision)
        ),
        "sourceContentHash": _digest(
            context.source_content_hash, "SOURCE_CONTENT_HASH_INVALID"
        ),
    }


def _validate_edge_snapshot(
    context: StoredGenerationContext, *, ontology_schema_id: uuid.UUID
) -> None:
    snapshot = context.edge_snapshot
    if (
        context.edge_id is None
        or not isinstance(snapshot, Mapping)
        or snapshot.get("id") != str(context.edge_id)
        or snapshot.get("ontologySchemaId") != str(ontology_schema_id)
    ):
        raise QualitySnapshotExportError("KNOWLEDGE_EDGE_SNAPSHOT_MISMATCH")


def _node_seal(
    context: StoredGenerationContext, *, sealed_at: datetime
) -> dict[str, Any]:
    snapshot = context.node_snapshot
    if not isinstance(snapshot, Mapping):
        raise QualitySnapshotExportError("KNOWLEDGE_NODE_SNAPSHOT_MISSING")
    if snapshot.get("id") != str(context.node_id):
        raise QualitySnapshotExportError("KNOWLEDGE_NODE_SNAPSHOT_MISMATCH")
    status = snapshot.get("status")
    status_map = {
        "ACTIVE": "ACTIVE",
        "AMBIGUOUS": "REJECTED",
        "SUPERSEDED": "SUPERSEDED",
        "RETRACTED": "RETRACTED",
    }
    if status not in status_map:
        raise QualitySnapshotExportError("KNOWLEDGE_LIFECYCLE_INVALID")
    recorded = _parse_timestamp(snapshot.get("recordedAt"), "KNOWLEDGE_RECORDED_AT_INVALID")
    valid_from = _parse_timestamp(
        snapshot.get("validFrom") or snapshot.get("recordedAt"),
        "KNOWLEDGE_VALID_FROM_INVALID",
    )
    valid_to_value = snapshot.get("validTo")
    valid_to = (
        _parse_timestamp(valid_to_value, "KNOWLEDGE_VALID_TO_INVALID")
        if valid_to_value is not None
        else None
    )
    available = (
        status == "ACTIVE"
        and valid_from <= sealed_at
        and (valid_to is None or sealed_at < valid_to)
        and recorded <= sealed_at
    )
    return {
        "lifecycleStatusAtSeal": status_map[cast(str, status)],
        "validFrom": _iso(valid_from),
        "validTo": _iso(valid_to) if valid_to is not None else None,
        "availableAtSeal": available,
        "conflictState": "UNRESOLVED" if status == "AMBIGUOUS" else "NONE",
        "decisionState": "CANDIDATE" if status == "AMBIGUOUS" else "ACCEPTED",
    }


def _result_category_ids(result: Mapping[str, Any]) -> tuple[str, ...]:
    category = result.get("categoryCandidate")
    if not isinstance(category, Mapping) or not isinstance(category.get("categoryId"), str):
        raise QualitySnapshotExportError("RESULT_CATEGORY_INVALID")
    values = [cast(str, category["categoryId"])]
    alternatives = category.get("alternatives")
    if not isinstance(alternatives, list):
        raise QualitySnapshotExportError("RESULT_CATEGORY_INVALID")
    for row in alternatives:
        if not isinstance(row, Mapping) or not isinstance(row.get("categoryId"), str):
            raise QualitySnapshotExportError("RESULT_CATEGORY_INVALID")
        values.append(cast(str, row["categoryId"]))
    return tuple(values)


def _text_seal(value: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise QualitySnapshotExportError("TEXT_SEAL_INPUT_INVALID")
    normalized = normalize_exact_text(unicodedata.normalize("NFKC", value))
    return {
        "normalizedLength": len(normalized),
        "digest": f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}",
    }


def _actual_counts(roots: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    cases = [
        case
        for root in roots
        for case in cast(
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
        "generatedSteps": sum(len(cast(Sequence[Any], row["steps"])) for row in cases),
        "evidenceReferences": sum(
            len(cast(Sequence[Any], row["evidenceReferences"])) for row in cases
        ),
    }


def _validate_report_outputs(outputs: Mapping[str, str]) -> dict[str, str]:
    if set(outputs) != {"json", "junit", "markdown"}:
        raise QualitySnapshotExportError("REPORT_OUTPUT_SET_INVALID")
    suffixes = {"json": ".json", "junit": ".xml", "markdown": ".md"}
    result: dict[str, str] = {}
    for name, suffix in suffixes.items():
        value = outputs[name]
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or path.is_absolute()
            or ".." in path.parts
            or path.suffix != suffix
            or len(value) > 1024
            or _URL.search(value)
            or _EMAIL.search(value)
            or _IPV4.search(value)
            or _KR_MOBILE.search(value)
            or _SECRET.search(value)
        ):
            raise QualitySnapshotExportError(f"REPORT_OUTPUT_PATH_INVALID:{name}")
        result[name] = value
    if len(set(result.values())) != 3:
        raise QualitySnapshotExportError("REPORT_OUTPUT_PATH_COLLISION")
    return result


def _merge_inventory(target: dict[str, dict[str, Any]], rows: Sequence[dict[str, Any]]) -> None:
    for row in rows:
        key_name = "caseReferenceId" if "caseReferenceId" in row else "referenceId"
        key = cast(str, row[key_name])
        previous = target.get(key)
        if previous is not None and previous != row:
            raise QualitySnapshotExportError("REFERENCE_INVENTORY_CONFLICT")
        target[key] = row


def _inventory_values(rows: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [rows[key] for key in sorted(rows)]


def _assert_public_artifact(snapshot: Mapping[str, Any]) -> None:
    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if _URL.search(serialized):
        raise QualitySnapshotExportError("ARTIFACT_ENDPOINT_DISCLOSURE")
    if _EMAIL.search(serialized):
        raise QualitySnapshotExportError("ARTIFACT_PII_DISCLOSURE")
    if _IPV4.search(serialized) or _KR_MOBILE.search(serialized):
        raise QualitySnapshotExportError("ARTIFACT_PII_DISCLOSURE")
    if _SECRET.search(serialized):
        raise QualitySnapshotExportError("ARTIFACT_SECRET_DISCLOSURE")


def _digest(value: Any, code: str) -> str:
    if not isinstance(value, str):
        raise QualitySnapshotExportError(code)
    raw = value.removeprefix("sha256:")
    if _HEX_64.fullmatch(raw) is None:
        raise QualitySnapshotExportError(code)
    return f"sha256:{raw}"


def _opaque(prefix: str, value: Any) -> str:
    payload = json.dumps(value, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _placeholder_digest() -> str:
    return f"sha256:{'0' * 64}"


def _parse_timestamp(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        raise QualitySnapshotExportError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise QualitySnapshotExportError(code) from error
    if parsed.tzinfo is None:
        raise QualitySnapshotExportError(code)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise QualitySnapshotExportError("NAIVE_TIMESTAMP")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
