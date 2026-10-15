"""Credentialed, append-only review workflow for T08 quality candidates.

The service never trusts caller labels.  Raw root UUIDs are accepted only at
the operator import boundary and are persisted in the private root table.  All
public projections use the verified E4 candidate key, aggregate counts,
classifications, domain-separated keyed seals, and sanitized-artifact digests.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final, Literal, cast

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from tc_backend.db.models import (
    GenerationJob,
    GenerationQualityApproval,
    GenerationQualityCandidate,
    GenerationQualityCandidateRoot,
    GenerationQualityReviewDecision,
    SlackWorkItem,
    WorkspaceSettings,
)
from tc_backend.generation.quality_gate import (
    CLASSIFICATIONS,
    canonical_digest,
    validate_input,
)
from tc_backend.generation.quality_snapshot import (
    QualitySnapshotExportError,
    export_quality_review_candidate,
    render_candidate_snapshot,
)
from tc_backend.privacy.review_candidates import (
    CONTRACT_VERSION as REVIEW_ENVELOPE_CONTRACT_VERSION,
)
from tc_backend.privacy.review_candidates import (
    NORMALIZATION_VERSION,
    SANITIZATION_POLICY_VERSION,
    SensitiveDataError,
    canonical_json_bytes,
    scan_sensitive_data,
    seal_source_field,
    verify_review_candidate,
    verify_source_seals,
)

REVIEW_PROTOCOL_VERSION: Final = "automatic-generation-quality-review-v1"
REVIEW_CONTRACT_VERSION: Final = "automatic-generation-quality-reviewed-export/2.0.0"
REVIEWED_CERTIFICATION_STATUS: Final = "REVIEWED_NOT_RELEASE_CERTIFIED"
REQUIRED_APPROVALS: Final = 2

type ReviewDecisionValue = Literal["APPROVE", "REJECT"]
type ReviewReasonCode = Literal[
    "QUALITY_EVIDENCE_INSUFFICIENT",
    "PRIVACY_FENCE_FAILED",
    "SOURCE_DRIFT",
    "TAMPER_DETECTED",
    "OTHER_POLICY_FAILURE",
]
type ReviewStatus = Literal["UNREVIEWED", "PENDING_APPROVAL", "APPROVED", "REJECTED"]

_TOKEN_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
_PRINCIPAL = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")


class QualityReviewError(ValueError):
    """Stable, source-free review error suitable for API mapping."""

    def __init__(self, code: str, *, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class ReviewerAuthority:
    """Constant-work token-to-principal mapping over configured token hashes."""

    def __init__(self, credentials: Sequence[tuple[str, str]]) -> None:
        if len(credentials) < REQUIRED_APPROVALS:
            raise QualityReviewError("QUALITY_REVIEW_AUTHORITY_NOT_CONFIGURED", status_code=503)
        principals = [principal for principal, _ in credentials]
        hashes = [token_hash for _, token_hash in credentials]
        if (
            any(_PRINCIPAL.fullmatch(principal) is None for principal in principals)
            or any(_TOKEN_HASH.fullmatch(token_hash) is None for token_hash in hashes)
            or len(set(principals)) != len(principals)
            or len(set(hashes)) != len(hashes)
        ):
            raise QualityReviewError("QUALITY_REVIEW_AUTHORITY_INVALID", status_code=503)
        self._credentials = tuple(credentials)

    def resolve(self, token: str | None) -> str:
        return self.authorize(token)[0]

    def authorize(self, token: str | None) -> tuple[str, str]:
        """Return the installed principal and its non-plaintext credential hash."""

        supplied = token if isinstance(token, str) else ""
        candidate = hash_reviewer_token(supplied)
        matched_index = -1
        for index, (_, configured_hash) in enumerate(self._credentials):
            matched = hmac.compare_digest(candidate, configured_hash)
            if matched:
                matched_index = index
        if matched_index < 0 or not supplied:
            raise QualityReviewError("QUALITY_REVIEWER_UNAUTHORIZED", status_code=401)
        return self._credentials[matched_index]


def hash_reviewer_token(token: str) -> str:
    """Hash a provisioned token without retaining or returning the token itself."""

    return f"sha256:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"


class GenerationQualityReviewService:
    """Authoritative import, decision, and reviewed-export transaction service."""

    def __init__(
        self,
        session: Session,
        *,
        reviewer_authority: ReviewerAuthority,
        source_seal_key: bytes,
        source_seal_key_id: str,
    ) -> None:
        if len(source_seal_key) < 32:
            raise QualityReviewError("QUALITY_REVIEW_SOURCE_SEAL_NOT_CONFIGURED", status_code=503)
        if _PRINCIPAL.fullmatch(source_seal_key_id) is None:
            raise QualityReviewError("QUALITY_REVIEW_SOURCE_SEAL_INVALID", status_code=503)
        self.session = session
        self.reviewer_authority = reviewer_authority
        self.source_seal_key = source_seal_key
        self.source_seal_key_id = source_seal_key_id
        self._repeatable_read_set = False

    def import_candidate(
        self,
        *,
        idempotency_key: str,
        root_job_ids: Sequence[uuid.UUID],
        capture_environment: str,
        report_outputs: Mapping[str, str],
    ) -> GenerationQualityCandidate:
        self._set_repeatable_read()
        self._lock_import_writes()
        workspace_id = self._workspace_id()
        roots = tuple(sorted(root_job_ids, key=lambda value: value.hex))
        if not roots or len(roots) != len(set(roots)):
            raise QualityReviewError("QUALITY_REVIEW_ROOT_SET_INVALID", status_code=422)
        try:
            exported, envelope = export_quality_review_candidate(
                self.session,
                root_job_ids=roots,
                capture_environment=capture_environment,
                report_outputs=report_outputs,
                key=self.source_seal_key,
                key_id=self.source_seal_key_id,
            )
        except QualitySnapshotExportError as error:
            raise QualityReviewError("QUALITY_REVIEW_SOURCE_INVALID", status_code=422) from error
        normalized = self._validate_import_snapshot(exported)
        self._verify_envelope(envelope, roots=roots, snapshot=normalized)
        artifact_digest = _artifact_digest(normalized)
        dataset = cast(Mapping[str, Any], normalized["dataset"])
        capture = cast(Mapping[str, Any], normalized["capture"])
        source = cast(Mapping[str, Any], envelope["source"])
        field_seals = cast(Sequence[Mapping[str, Any]], source["fieldSeals"])
        root_set_seal = next(
            cast(str, field["seal"])
            for field in field_seals
            if field["fieldDomain"] == "operational-root-set"
        )
        candidate_section = cast(Mapping[str, Any], envelope["candidate"])
        envelope_fingerprints = cast(Mapping[str, Any], envelope["fingerprints"])
        candidate_key = cast(str, candidate_section["candidateKey"])
        request_digest = canonical_digest(
            {
                "candidateKey": candidate_key,
                "rootSetSeal": root_set_seal,
                "envelopeArtifactFingerprint": envelope_fingerprints["artifactFingerprint"],
            }
        )
        replay = self.session.scalar(
            select(GenerationQualityCandidate).where(
                GenerationQualityCandidate.workspace_id == workspace_id,
                GenerationQualityCandidate.import_idempotency_key == idempotency_key,
            )
        )
        if replay is not None:
            if replay.request_digest != request_digest:
                raise QualityReviewError("QUALITY_REVIEW_IMPORT_IDEMPOTENCY_CONFLICT")
            self._rebuild_and_compare(replay)
            return replay
        existing = self.session.scalar(
            select(GenerationQualityCandidate).where(
                GenerationQualityCandidate.workspace_id == workspace_id,
                GenerationQualityCandidate.candidate_key == candidate_key,
            )
        )
        if existing is not None:
            raise QualityReviewError("QUALITY_REVIEW_CANDIDATE_ALREADY_IMPORTED")

        self._assert_roots_owned(workspace_id, roots)

        candidate = GenerationQualityCandidate(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            candidate_key=candidate_key,
            import_idempotency_key=idempotency_key,
            request_digest=request_digest,
            base_artifact_digest=artifact_digest,
            base_dataset_fingerprint=cast(str, dataset["datasetFingerprint"]),
            base_source_snapshot_fingerprint=cast(
                str, capture["sourceSnapshotFingerprint"]
            ),
            base_snapshot_fingerprint=cast(str, envelope_fingerprints["artifactFingerprint"]),
            root_set_seal=root_set_seal,
            capture_environment=cast(str, capture["environment"]),
            capture_revision=cast(str, capture["captureRevision"]),
            privacy_policy_version=cast(
                str, cast(Mapping[str, Any], dataset["privacy"])["redactionPolicyVersion"]
            ),
            privacy_scan_status="CLEAN",
            snapshot=normalized,
            review_envelope=envelope,
            envelope_payload_fingerprint=cast(
                str, envelope_fingerprints["payloadFingerprint"]
            ),
            envelope_artifact_fingerprint=cast(
                str, envelope_fingerprints["artifactFingerprint"]
            ),
            declared_counts=cast(dict[str, Any], copy.deepcopy(dataset["declaredCounts"])),
            classification_distribution=cast(
                dict[str, Any], copy.deepcopy(dataset["classificationDistribution"])
            ),
        )
        self.session.add(candidate)
        self.session.flush()
        for position, root_id in enumerate(roots):
            self.session.add(
                GenerationQualityCandidateRoot(
                    id=uuid.uuid4(),
                    workspace_id=workspace_id,
                    candidate_id=candidate.id,
                    position=position,
                    root_job_id=root_id,
                )
            )
        self.session.flush()
        return candidate

    def decide(
        self,
        candidate_id: uuid.UUID | str,
        *,
        reviewer_token: str | None,
        idempotency_key: str,
        decision: ReviewDecisionValue,
        expected_artifact_fingerprint: str,
        reason_code: ReviewReasonCode | None,
    ) -> GenerationQualityCandidate:
        principal_id, credential_hash = self.reviewer_authority.authorize(reviewer_token)
        self._set_repeatable_read()
        self._lock_review_writes()
        workspace_id = self._workspace_id()
        if isinstance(candidate_id, str):
            if re.fullmatch(r"review-candidate:[a-f0-9]{64}", candidate_id) is None:
                raise QualityReviewError("QUALITY_REVIEW_CANDIDATE_ID_INVALID", status_code=422)
            candidate_identity = GenerationQualityCandidate.candidate_key == candidate_id
        else:
            candidate_identity = GenerationQualityCandidate.id == candidate_id
        candidate = self.session.scalar(
            select(GenerationQualityCandidate)
            .where(
                candidate_identity,
                GenerationQualityCandidate.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if candidate is None:
            raise QualityReviewError("QUALITY_REVIEW_CANDIDATE_NOT_FOUND", status_code=404)
        request_digest = _database_digest(
            "decision-request",
            candidate.candidate_key,
            decision,
            reason_code,
            expected_artifact_fingerprint,
        )
        replay = self.session.scalar(
            select(GenerationQualityReviewDecision).where(
                GenerationQualityReviewDecision.workspace_id == workspace_id,
                GenerationQualityReviewDecision.principal_id == principal_id,
                GenerationQualityReviewDecision.idempotency_key == idempotency_key,
            )
        )
        if replay is not None:
            if replay.candidate_id != candidate.id or replay.request_digest != request_digest:
                raise QualityReviewError("QUALITY_REVIEW_DECISION_IDEMPOTENCY_CONFLICT")
            self._rebuild_and_compare(candidate)
            return candidate
        if expected_artifact_fingerprint != candidate.envelope_artifact_fingerprint:
            raise QualityReviewError("QUALITY_REVIEW_STALE_SNAPSHOT")
        if decision == "APPROVE" and reason_code is not None:
            raise QualityReviewError("QUALITY_REVIEW_DECISION_SHAPE_INVALID", status_code=422)
        if decision == "REJECT" and reason_code is None:
            raise QualityReviewError("QUALITY_REVIEW_DECISION_SHAPE_INVALID", status_code=422)

        decisions = self._decisions(candidate.id)
        if self._approval(candidate.id) is not None or any(
            row.decision == "REJECT" for row in decisions
        ):
            raise QualityReviewError("QUALITY_REVIEW_TERMINAL")
        if any(row.principal_id == principal_id for row in decisions):
            raise QualityReviewError("QUALITY_REVIEW_PRINCIPAL_ALREADY_DECIDED")
        self._rebuild_and_compare(candidate)
        decision_digest = _database_digest(
            "decision",
            candidate.candidate_key,
            principal_id,
            decision,
            reason_code,
            candidate.envelope_artifact_fingerprint,
        )
        if self.session.get_bind().dialect.name == "postgresql":
            self._record_decision_at_database_boundary(
                candidate=candidate,
                principal_id=principal_id,
                credential_hash=credential_hash,
                decision=decision,
                reason_code=reason_code,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                decision_digest=decision_digest,
            )
        else:
            row = GenerationQualityReviewDecision(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                candidate_id=candidate.id,
                principal_id=principal_id,
                decision=decision,
                reason_code=reason_code,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                observed_base_snapshot_fingerprint=candidate.envelope_artifact_fingerprint,
                decision_digest=decision_digest,
            )
            self.session.add(row)
            self.session.flush()
            if decision == "APPROVE":
                approvals = sorted(
                    self._decisions(candidate.id, decision="APPROVE"),
                    key=lambda item: item.principal_id,
                )
                if len(approvals) == REQUIRED_APPROVALS:
                    self._create_approval(candidate, approvals)
        return candidate

    def get(self, candidate_id: uuid.UUID) -> GenerationQualityCandidate:
        workspace_id = self._workspace_id()
        candidate = self.session.scalar(
            select(GenerationQualityCandidate).where(
                GenerationQualityCandidate.id == candidate_id,
                GenerationQualityCandidate.workspace_id == workspace_id,
            )
        )
        if candidate is None:
            raise QualityReviewError("QUALITY_REVIEW_CANDIDATE_NOT_FOUND", status_code=404)
        return candidate

    def get_by_key(self, candidate_key: str) -> GenerationQualityCandidate:
        if re.fullmatch(r"review-candidate:[a-f0-9]{64}", candidate_key) is None:
            raise QualityReviewError("QUALITY_REVIEW_CANDIDATE_ID_INVALID", status_code=422)
        self._set_repeatable_read()
        workspace_id = self._workspace_id()
        candidate = self.session.scalar(
            select(GenerationQualityCandidate).where(
                GenerationQualityCandidate.candidate_key == candidate_key,
                GenerationQualityCandidate.workspace_id == workspace_id,
            )
        )
        if candidate is None:
            raise QualityReviewError("QUALITY_REVIEW_CANDIDATE_NOT_FOUND", status_code=404)
        self._verify_stored_envelope(candidate)
        return candidate

    def list_candidates(
        self,
        *,
        limit: int,
        cursor_key: str | None = None,
        status_filter: ReviewStatus | None = None,
        classification_filter: str | None = None,
        search: str | None = None,
    ) -> tuple[list[GenerationQualityCandidate], str | None]:
        workspace_id = self._workspace_id()
        statement: Select[tuple[GenerationQualityCandidate]] = select(
            GenerationQualityCandidate
        ).where(GenerationQualityCandidate.workspace_id == workspace_id)
        if cursor_key is not None:
            cursor = self.session.scalar(
                select(GenerationQualityCandidate).where(
                    GenerationQualityCandidate.candidate_key == cursor_key,
                    GenerationQualityCandidate.workspace_id == workspace_id,
                )
            )
            if cursor is None:
                raise QualityReviewError("QUALITY_REVIEW_CURSOR_INVALID", status_code=422)
            statement = statement.where(
                (GenerationQualityCandidate.created_at < cursor.created_at)
                | (
                    (GenerationQualityCandidate.created_at == cursor.created_at)
                    & (GenerationQualityCandidate.id < cursor.id)
                )
            )
        candidates = list(
            self.session.scalars(
                statement.order_by(
                    GenerationQualityCandidate.created_at.desc(),
                    GenerationQualityCandidate.id.desc(),
                )
            )
        )
        normalized_search = search.casefold() if search is not None else None
        rows = [
            candidate
            for candidate in candidates
            if (
                status_filter is None
                or _derived_status(self._decisions(candidate.id), self._approval(candidate.id))
                == status_filter
            )
            and (
                classification_filter is None
                or int(candidate.classification_distribution[classification_filter]) > 0
            )
            and (
                normalized_search is None
                or normalized_search in candidate.candidate_key.casefold()
            )
        ]
        next_cursor = rows[limit - 1].candidate_key if len(rows) > limit else None
        return rows[:limit], next_cursor

    def safe_detail(self, candidate: GenerationQualityCandidate) -> dict[str, Any]:
        self._verify_stored_envelope(candidate)
        envelope = cast(Mapping[str, Any], candidate.review_envelope)
        payload = cast(Mapping[str, Any], cast(Mapping[str, Any], envelope["payload"])["projection"])
        source = cast(Mapping[str, Any], envelope["source"])
        sanitization = cast(Mapping[str, Any], envelope["sanitization"])
        decisions = self._decisions(candidate.id)
        approval = self._approval(candidate.id)
        root_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(GenerationQualityCandidateRoot)
                .where(GenerationQualityCandidateRoot.candidate_id == candidate.id)
            )
            or 0
        )
        approved_count = sum(row.decision == "APPROVE" for row in decisions)
        status = _derived_status(decisions, approval)
        return {
            "candidateId": candidate.candidate_key,
            "status": status,
            "reviewable": root_count > 0 and status in {"UNREVIEWED", "PENDING_APPROVAL"},
            "materialization": "COMPLETE",
            "createdAt": candidate.created_at,
            "capturedAt": payload["capturedAt"],
            "captureEnvironment": payload["captureEnvironment"],
            "captureRevision": payload["captureRevision"],
            "fingerprints": {
                "payloadFingerprint": candidate.envelope_payload_fingerprint,
                "artifactFingerprint": candidate.envelope_artifact_fingerprint,
            },
            "source": {
                "sealAlgorithm": source["sealAlgorithm"],
                "keyId": source["keyId"],
                "privacyDomain": source["privacyDomain"],
                "fieldSeals": copy.deepcopy(source["fieldSeals"]),
            },
            "privacy": {
                "status": sanitization["status"],
                "policyVersion": sanitization["policyVersion"],
                "foundationPolicyVersion": SANITIZATION_POLICY_VERSION,
                "normalizationVersion": NORMALIZATION_VERSION,
                "mode": sanitization["mode"],
                "redactionCount": sanitization["redactionCount"],
                "findingCategories": copy.deepcopy(sanitization["findingCategories"]),
                "containsRawSourceText": False,
            },
            "envelopeContractVersion": REVIEW_ENVELOPE_CONTRACT_VERSION,
            "payloadSchemaVersion": cast(Mapping[str, Any], envelope["payload"])[
                "schemaVersion"
            ],
            "counts": copy.deepcopy(payload["declaredCounts"]),
            "classificationDistribution": copy.deepcopy(
                dict(
                    zip(
                        CLASSIFICATIONS,
                        cast(Sequence[int], payload["classificationCounts"]),
                        strict=True,
                    )
                )
            ),
            "progress": {
                "approveCount": approved_count,
                "requiredApproveCount": REQUIRED_APPROVALS,
                "rejected": status == "REJECTED",
            },
            "decisions": [
                self._safe_decision(row, reviewer_slot=index)
                for index, row in enumerate(decisions, start=1)
            ],
            "approval": (
                self._safe_approval(candidate, decisions, approval)
                if approval is not None
                else None
            ),
            "certificationStatus": (
                REVIEWED_CERTIFICATION_STATUS if approval is not None else "NOT_REVIEWED"
            ),
            "releaseCertified": False,
        }

    def reviewed_snapshot(self, candidate_id: uuid.UUID) -> dict[str, Any]:
        self._set_repeatable_read()
        candidate = self.get(candidate_id)
        approval = self._approval(candidate.id)
        if approval is None:
            raise QualityReviewError("QUALITY_REVIEW_APPROVAL_REQUIRED")
        self._rebuild_and_compare(candidate)
        decisions = self._decisions(candidate.id)
        safe_approval = self._safe_approval(candidate, decisions, approval)
        envelope = copy.deepcopy(candidate.review_envelope)
        verify_review_candidate(envelope, expected_revision=1)
        return {
            "artifactKind": "AUTOMATIC_GENERATION_QUALITY_REVIEWED_EXPORT",
            "contractVersion": REVIEW_CONTRACT_VERSION,
            "candidateKey": candidate.candidate_key,
            "baseEnvelope": envelope,
            "review": {
                "status": "REVIEWED",
                "reviewedAt": _timestamp(approval.approved_at),
                "requiredApprovals": REQUIRED_APPROVALS,
                "reviewerAuthoritySeals": [
                    self._reviewer_seal(row.principal_id) for row in decisions
                ],
                "decisionFingerprints": [
                    cast(str, self._safe_decision(row, reviewer_slot=index)["decisionFingerprint"])
                    for index, row in enumerate(decisions, start=1)
                ],
                **safe_approval,
            },
            "attestation": {"status": "NOT_RUN"},
            "certification": {
                "status": REVIEWED_CERTIFICATION_STATUS,
                "eligible": False,
                "releaseCertified": False,
                "limitations": [
                    "ACTUAL_32_ROOT_16_CLASS_SAMPLE_NOT_ATTESTED",
                    "PROVIDER_LIVE_ATTESTATION_NOT_RUN",
                    "NOT_A_RELEASE_CERTIFICATION",
                ],
            },
        }

    def _create_approval(
        self,
        candidate: GenerationQualityCandidate,
        approvals: Sequence[GenerationQualityReviewDecision],
    ) -> GenerationQualityApproval:
        if len(approvals) != REQUIRED_APPROVALS:
            raise QualityReviewError("QUALITY_REVIEW_APPROVAL_COUNT_INVALID")
        first, second = approvals
        if first.principal_id == second.principal_id:
            raise QualityReviewError("QUALITY_REVIEW_DISTINCT_PRINCIPALS_REQUIRED")
        decision_digests = sorted([first.decision_digest, second.decision_digest])
        authority_digest = _database_digest(
            "approval-authority",
            candidate.candidate_key,
            candidate.envelope_artifact_fingerprint,
            decision_digests[0],
            decision_digests[1],
            REVIEW_PROTOCOL_VERSION,
        )
        approval_digest = _database_digest(
            "approval-artifact",
            candidate.base_artifact_digest,
            authority_digest,
            REVIEWED_CERTIFICATION_STATUS,
        )
        approval = GenerationQualityApproval(
            id=uuid.uuid4(),
            workspace_id=candidate.workspace_id,
            candidate_id=candidate.id,
            decision_one_id=first.id,
            decision_two_id=second.id,
            approver_one_principal_id=first.principal_id,
            approver_two_principal_id=second.principal_id,
            protected_base_artifact_digest=candidate.base_artifact_digest,
            protected_base_snapshot_fingerprint=candidate.envelope_artifact_fingerprint,
            review_authority_digest=authority_digest,
            approval_artifact_digest=approval_digest,
        )
        self.session.add(approval)
        self.session.flush()
        return approval

    def _validate_import_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        try:
            validate_input(snapshot)
            render_candidate_snapshot(snapshot)
        except (QualitySnapshotExportError, ValueError, TypeError, KeyError) as error:
            raise QualityReviewError("QUALITY_REVIEW_SNAPSHOT_INVALID", status_code=422) from error
        normalized = cast(dict[str, Any], copy.deepcopy(dict(snapshot)))
        dataset = cast(Mapping[str, Any], normalized["dataset"])
        review = cast(Mapping[str, Any], dataset["review"])
        live_gate = cast(Mapping[str, Any], normalized["liveGate"])
        privacy = cast(Mapping[str, Any], dataset["privacy"])
        capture = cast(Mapping[str, Any], normalized["capture"])
        if (
            normalized["profile"] != "CANDIDATE"
            or dataset["kind"] != "OPERATIONAL_GOLDEN"
            or dataset["synthetic"] is not False
            or review
            != {
                "status": "UNREVIEWED",
                "reviewedBy": None,
                "reviewedAt": None,
                "labelSetId": None,
                "reviewProtocolVersion": None,
                "approvalId": None,
                "reviewAuthorityDigest": None,
                "approvalArtifactDigest": None,
            }
            or live_gate
            != {
                "status": "NOT_RUN",
                "liveRunId": None,
                "environment": None,
                "providerCredentialed": False,
                "providerCallObserved": False,
                "approvedBy": None,
                "approvedAt": None,
                "capturedSnapshotFingerprint": None,
            }
            or privacy["containsRawSourceText"] is not False
            or privacy["secretsScan"] != "CLEAN"
            or privacy["piiScan"] != "CLEAN"
            or capture["environment"] not in {"staging", "production"}
        ):
            raise QualityReviewError("QUALITY_REVIEW_IMPORT_BOUNDARY_INVALID", status_code=422)
        safe_text_projection = {
            "descriptor": dataset["description"],
            "environment": capture["environment"],
            "revision": capture["captureRevision"],
            "artifacts": sorted(cast(Mapping[str, str], normalized["reportOutputs"]).values()),
        }
        try:
            findings = scan_sensitive_data(safe_text_projection)
        except (SensitiveDataError, ValueError, TypeError) as error:
            raise QualityReviewError(
                "QUALITY_REVIEW_PRIVACY_SCAN_FAILED", status_code=422
            ) from error
        if findings:
            raise QualityReviewError("QUALITY_REVIEW_PRIVACY_SCAN_FAILED", status_code=422)
        return cast(dict[str, Any], _canonical_copy(normalized))

    def _rebuild_and_compare(self, candidate: GenerationQualityCandidate) -> dict[str, Any]:
        try:
            stored = self._validate_import_snapshot(candidate.snapshot)
        except QualityReviewError as error:
            raise QualityReviewError("QUALITY_REVIEW_CANDIDATE_TAMPERED") from error
        stored_dataset = cast(Mapping[str, Any], stored["dataset"])
        stored_capture = cast(Mapping[str, Any], stored["capture"])
        if (
            _artifact_digest(stored) != candidate.base_artifact_digest
            or stored_dataset["datasetFingerprint"] != candidate.base_dataset_fingerprint
            or stored_capture["sourceSnapshotFingerprint"]
            != candidate.base_source_snapshot_fingerprint
            or stored_dataset["declaredCounts"] != candidate.declared_counts
            or stored_dataset["classificationDistribution"] != candidate.classification_distribution
        ):
            raise QualityReviewError("QUALITY_REVIEW_CANDIDATE_TAMPERED")
        self._verify_stored_envelope(candidate)
        roots = tuple(
            self.session.scalars(
                select(GenerationQualityCandidateRoot.root_job_id)
                .where(
                    GenerationQualityCandidateRoot.candidate_id == candidate.id,
                    GenerationQualityCandidateRoot.workspace_id == candidate.workspace_id,
                )
                .order_by(GenerationQualityCandidateRoot.position)
            )
        )
        if not roots:
            raise QualityReviewError("QUALITY_REVIEW_ROOTLESS_CANDIDATE")
        expected_root_seal = seal_source_field(
            [str(root) for root in roots],
            key=self.source_seal_key,
            key_id=self.source_seal_key_id,
            privacy_domain="generation-quality-review",
            field_domain="operational-root-set",
        )
        if not hmac.compare_digest(expected_root_seal, candidate.root_set_seal):
            raise QualityReviewError("QUALITY_REVIEW_ROOT_MAPPING_TAMPERED")
        self._assert_roots_owned(candidate.workspace_id, roots)
        try:
            rebuilt, envelope = self._export(roots, stored)
        except QualityReviewError:
            raise
        except Exception as error:
            raise QualityReviewError("QUALITY_REVIEW_SOURCE_DRIFT") from error
        rebuilt_dataset = cast(Mapping[str, Any], rebuilt["dataset"])
        rebuilt_capture = cast(Mapping[str, Any], rebuilt["capture"])
        if (
            _artifact_digest(rebuilt) != candidate.base_artifact_digest
            or rebuilt_dataset["datasetFingerprint"] != candidate.base_dataset_fingerprint
            or rebuilt_capture["sourceSnapshotFingerprint"]
            != candidate.base_source_snapshot_fingerprint
        ):
            raise QualityReviewError("QUALITY_REVIEW_SOURCE_DRIFT")
        if envelope != candidate.review_envelope:
            raise QualityReviewError("QUALITY_REVIEW_SOURCE_DRIFT")
        return rebuilt

    def _export(
        self, roots: Sequence[uuid.UUID], snapshot: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        capture = cast(Mapping[str, Any], snapshot["capture"])
        try:
            return export_quality_review_candidate(
                self.session,
                root_job_ids=roots,
                capture_environment=cast(str, capture["environment"]),
                report_outputs=cast(Mapping[str, str], snapshot["reportOutputs"]),
                key=self.source_seal_key,
                key_id=self.source_seal_key_id,
            )
        except QualitySnapshotExportError as error:
            raise QualityReviewError("QUALITY_REVIEW_SOURCE_DRIFT") from error

    def _verify_envelope(
        self,
        envelope: Mapping[str, Any],
        *,
        roots: Sequence[uuid.UUID],
        snapshot: Mapping[str, Any],
    ) -> None:
        try:
            verify_review_candidate(envelope, expected_revision=1)
            verify_source_seals(
                envelope,
                source_fields={
                    "operational-root-set": [str(root) for root in roots],
                    "private-t08-snapshot": snapshot,
                },
                key=self.source_seal_key,
            )
        except (SensitiveDataError, ValueError, TypeError, KeyError) as error:
            raise QualityReviewError("QUALITY_REVIEW_ENVELOPE_INVALID", status_code=422) from error

    def _verify_stored_envelope(self, candidate: GenerationQualityCandidate) -> None:
        envelope = candidate.review_envelope
        if not isinstance(envelope, Mapping):
            raise QualityReviewError("QUALITY_REVIEW_LEGACY_CANDIDATE_REJECTED")
        try:
            verify_review_candidate(envelope, expected_revision=1)
        except (SensitiveDataError, ValueError, TypeError, KeyError) as error:
            raise QualityReviewError("QUALITY_REVIEW_CANDIDATE_TAMPERED") from error
        fingerprints = cast(Mapping[str, Any], envelope["fingerprints"])
        candidate_section = cast(Mapping[str, Any], envelope["candidate"])
        if (
            candidate_section["candidateKey"] != candidate.candidate_key
            or fingerprints["payloadFingerprint"] != candidate.envelope_payload_fingerprint
            or fingerprints["artifactFingerprint"] != candidate.envelope_artifact_fingerprint
            or candidate.base_snapshot_fingerprint != candidate.envelope_artifact_fingerprint
        ):
            raise QualityReviewError("QUALITY_REVIEW_CANDIDATE_TAMPERED")

    def _reviewer_seal(self, principal_id: str) -> str:
        return seal_source_field(
            principal_id,
            key=self.source_seal_key,
            key_id=self.source_seal_key_id,
            privacy_domain="generation-quality-review",
            field_domain="reviewer-principal",
        )

    def _safe_decision(
        self, row: GenerationQualityReviewDecision, *, reviewer_slot: int
    ) -> dict[str, Any]:
        reviewer_seal = self._reviewer_seal(row.principal_id)
        projection = {
            "decision": row.decision,
            "observedArtifactFingerprint": row.observed_base_snapshot_fingerprint,
            "reasonCode": row.reason_code,
            "reviewerSeal": reviewer_seal,
            "reviewerSlot": reviewer_slot,
        }
        return {
            **projection,
            "decisionFingerprint": _metadata_fingerprint(
                "quality-review-decision", projection
            ),
            "decidedAt": row.decided_at,
        }

    def _safe_approval(
        self,
        candidate: GenerationQualityCandidate,
        decisions: Sequence[GenerationQualityReviewDecision],
        approval: GenerationQualityApproval,
    ) -> dict[str, Any]:
        safe_decisions = [
            self._safe_decision(row, reviewer_slot=index)
            for index, row in enumerate(decisions, start=1)
        ]
        authority_projection = {
            "candidateKey": candidate.candidate_key,
            "decisionFingerprints": sorted(
                cast(str, row["decisionFingerprint"]) for row in safe_decisions
            ),
            "reviewerAuthoritySeals": sorted(
                cast(str, row["reviewerSeal"]) for row in safe_decisions
            ),
        }
        review_authority_fingerprint = _metadata_fingerprint(
            "quality-review-authority", authority_projection
        )
        approval_projection = {
            "baseEnvelopeArtifactFingerprint": candidate.envelope_artifact_fingerprint,
            "reviewAuthorityFingerprint": review_authority_fingerprint,
            "status": REVIEWED_CERTIFICATION_STATUS,
        }
        return {
            "reviewAuthorityFingerprint": review_authority_fingerprint,
            "approvalArtifactFingerprint": _metadata_fingerprint(
                "quality-review-approval", approval_projection
            ),
            "approvedAt": approval.approved_at,
        }

    def _record_decision_at_database_boundary(
        self,
        *,
        candidate: GenerationQualityCandidate,
        principal_id: str,
        credential_hash: str,
        decision: ReviewDecisionValue,
        reason_code: ReviewReasonCode | None,
        idempotency_key: str,
        request_digest: str,
        decision_digest: str,
    ) -> None:
        try:
            self.session.execute(text("SET LOCAL ROLE tc_quality_review_writer"))
            self.session.execute(
                text(
                    "SELECT tc_record_generation_quality_decision("
                    ":workspace_id, :candidate_id, :principal_id, :credential_hash, "
                    ":decision, :reason_code, :idempotency_key, :expected_artifact_fingerprint, "
                    ":request_digest, :decision_digest)"
                ),
                {
                    "workspace_id": candidate.workspace_id,
                    "candidate_id": candidate.id,
                    "principal_id": principal_id,
                    "credential_hash": credential_hash,
                    "decision": decision,
                    "reason_code": reason_code,
                    "idempotency_key": idempotency_key,
                    "expected_artifact_fingerprint": candidate.envelope_artifact_fingerprint,
                    "request_digest": request_digest,
                    "decision_digest": decision_digest,
                },
            )
        except Exception as error:
            message = str(error)
            if "reviewer authority is not installed" in message:
                raise QualityReviewError(
                    "QUALITY_REVIEW_AUTHORITY_NOT_INSTALLED", status_code=503
                ) from error
            if "quality review decision integrity mismatch" in message:
                raise QualityReviewError("QUALITY_REVIEW_DECISION_INTEGRITY_FAILED") from error
            raise

    def _assert_roots_owned(self, workspace_id: uuid.UUID, roots: Sequence[uuid.UUID]) -> None:
        owned = tuple(
            self.session.scalars(
                select(GenerationJob.id)
                .join(SlackWorkItem, SlackWorkItem.id == GenerationJob.slack_work_item_id)
                .where(
                    GenerationJob.id.in_(roots),
                    GenerationJob.root_job_id == GenerationJob.id,
                    SlackWorkItem.workspace_id == workspace_id,
                )
                .order_by(GenerationJob.id)
            )
        )
        if tuple(sorted(owned, key=lambda value: value.hex)) != tuple(
            sorted(roots, key=lambda value: value.hex)
        ):
            raise QualityReviewError("QUALITY_REVIEW_ROOT_OWNERSHIP_INVALID", status_code=422)

    def _workspace_id(self) -> uuid.UUID:
        workspace_id = self.session.scalar(
            select(WorkspaceSettings.id).where(WorkspaceSettings.singleton_key == 1)
        )
        if workspace_id is None:
            raise QualityReviewError("QUALITY_REVIEW_WORKSPACE_UNAVAILABLE", status_code=503)
        return workspace_id

    def _set_repeatable_read(self) -> None:
        if self._repeatable_read_set:
            return
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            self.session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        self._repeatable_read_set = True

    def _lock_review_writes(self) -> None:
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            # LOCK does not read a snapshot. It serializes the tiny no-login
            # review team before the first authoritative read, so a waiter
            # establishes its REPEATABLE READ snapshot only after the winner
            # commits its append-only decision.
            self.session.execute(
                text("LOCK TABLE generation_quality_review_decisions IN SHARE ROW EXCLUSIVE MODE")
            )

    def _lock_import_writes(self) -> None:
        bind = self.session.get_bind()
        if bind.dialect.name == "postgresql":
            self.session.execute(
                text("LOCK TABLE generation_quality_candidates IN SHARE ROW EXCLUSIVE MODE")
            )

    def _decisions(
        self,
        candidate_id: uuid.UUID,
        *,
        decision: ReviewDecisionValue | None = None,
    ) -> list[GenerationQualityReviewDecision]:
        statement = select(GenerationQualityReviewDecision).where(
            GenerationQualityReviewDecision.candidate_id == candidate_id
        )
        if decision is not None:
            statement = statement.where(GenerationQualityReviewDecision.decision == decision)
        return list(
            self.session.scalars(
                statement.order_by(
                    GenerationQualityReviewDecision.decided_at,
                    GenerationQualityReviewDecision.id,
                )
            )
        )

    def _approval(self, candidate_id: uuid.UUID) -> GenerationQualityApproval | None:
        return self.session.scalar(
            select(GenerationQualityApproval).where(
                GenerationQualityApproval.candidate_id == candidate_id
            )
        )

def _derived_status(
    decisions: Sequence[GenerationQualityReviewDecision],
    approval: GenerationQualityApproval | None,
) -> ReviewStatus:
    if any(row.decision == "REJECT" for row in decisions):
        return "REJECTED"
    if approval is not None:
        return "APPROVED"
    if any(row.decision == "APPROVE" for row in decisions):
        return "PENDING_APPROVAL"
    return "UNREVIEWED"


def _artifact_digest(value: Mapping[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def _database_digest(domain: str, *parts: str | None) -> str:
    framed = bytearray(domain.encode("utf-8"))
    for part in parts:
        if part is None:
            framed.extend((-1).to_bytes(8, "big", signed=True))
        else:
            encoded = part.encode("utf-8")
            framed.extend(len(encoded).to_bytes(8, "big", signed=True))
            framed.extend(encoded)
    return f"sha256:{hashlib.sha256(framed).hexdigest()}"


def _metadata_fingerprint(field_domain: str, value: Mapping[str, Any]) -> str:
    """Hash explicit sanitized metadata that may itself contain allowed seals."""

    domain = field_domain.encode("utf-8")
    canonical = canonical_json_bytes(value)
    message = (
        len(b"tc.quality-review.public-metadata.v1").to_bytes(8, "big")
        + b"tc.quality-review.public-metadata.v1"
        + len(domain).to_bytes(8, "big")
        + domain
        + len(canonical).to_bytes(8, "big")
        + canonical
    )
    return f"sha256:{hashlib.sha256(message).hexdigest()}"


def _canonical_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    import json

    return cast(dict[str, Any], json.loads(canonical_json_bytes(value)))


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise QualityReviewError("QUALITY_REVIEW_TIMESTAMP_INVALID")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
