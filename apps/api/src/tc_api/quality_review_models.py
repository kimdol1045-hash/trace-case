"""Strict API models for the automatic-generation quality review workspace."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from tc_api.models import ApiModel, RequestModel

Digest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Seal = Annotated[str, Field(pattern=r"^hmac-sha256:[a-f0-9]{64}$")]
OpaqueToken = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9._:-]*$")
]
QualityReviewStatus = Literal["UNREVIEWED", "PENDING_APPROVAL", "APPROVED", "REJECTED"]
QualityReviewReason = Literal[
    "QUALITY_EVIDENCE_INSUFFICIENT",
    "PRIVACY_FENCE_FAILED",
    "SOURCE_DRIFT",
    "TAMPER_DETECTED",
    "OTHER_POLICY_FAILURE",
]
QualityReviewClassification = Literal[
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
]
_DIGEST_PATTERN = r"^sha256:[a-f0-9]{64}$"
_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


class ImportGenerationQualityCandidateRequest(RequestModel):
    idempotencyKey: str = Field(
        min_length=8, max_length=255, pattern=_IDEMPOTENCY_PATTERN
    )
    rootJobIds: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    captureEnvironment: Literal["staging", "production"]
    reportOutputs: dict[Literal["json", "junit", "markdown"], str]

    @model_validator(mode="after")
    def roots_are_distinct(self) -> Self:
        if len(self.rootJobIds) != len(set(self.rootJobIds)):
            raise ValueError("rootJobIds must be unique")
        return self


class CreateGenerationQualityDecisionRequest(RequestModel):
    idempotencyKey: str = Field(
        min_length=8, max_length=255, pattern=_IDEMPOTENCY_PATTERN
    )
    decision: Literal["APPROVE", "REJECT"]
    expectedArtifactFingerprint: Digest = Field(pattern=_DIGEST_PATTERN)
    reasonCode: QualityReviewReason | None

    @model_validator(mode="after")
    def decision_reason_shape(self) -> Self:
        if self.decision == "APPROVE" and self.reasonCode is not None:
            raise ValueError("APPROVE must not include reasonCode")
        if self.decision == "REJECT" and self.reasonCode is None:
            raise ValueError("REJECT requires reasonCode")
        return self


class GenerationQualityFingerprintsResource(ApiModel):
    payload_fingerprint: Digest = Field(pattern=_DIGEST_PATTERN)
    artifact_fingerprint: Digest = Field(pattern=_DIGEST_PATTERN)


class GenerationQualitySourceFieldSealResource(ApiModel):
    field_domain: Literal["operational-root-set", "private-t08-snapshot"]
    seal: Seal


class GenerationQualitySourceResource(ApiModel):
    seal_algorithm: Literal["HMAC-SHA-256"]
    key_id: OpaqueToken
    privacy_domain: Literal["generation-quality-review"]
    field_seals: list[GenerationQualitySourceFieldSealResource] = Field(
        min_length=2, max_length=2
    )

    @model_validator(mode="after")
    def exact_field_domains(self) -> Self:
        if {item.field_domain for item in self.field_seals} != {
            "operational-root-set",
            "private-t08-snapshot",
        }:
            raise ValueError("both T08 source-seal domains are required")
        return self


class GenerationQualityPrivacyResource(ApiModel):
    status: Literal["CLEAN"]
    policy_version: Literal["review-candidate-sensitive-data-v1"]
    foundation_policy_version: Literal["review-candidate-sensitive-data-v1"]
    normalization_version: Literal["review-candidate-nfkc-lf-ignorables-v1"]
    mode: Literal["REJECT"]
    redaction_count: Literal[0]
    finding_categories: list[str] = Field(max_length=0)
    contains_raw_source_text: Literal[False]


class GenerationQualityCountsResource(ApiModel):
    generation_roots: int = Field(ge=1)
    generation_jobs: int = Field(ge=1)
    generation_results: int = Field(ge=1)
    generated_cases: int = Field(ge=0)
    generated_steps: int = Field(ge=0)
    evidence_references: int = Field(ge=0)


class GenerationQualityClassificationResource(ApiModel):
    GROUNDED_ACTIVE: int = Field(ge=0, alias="GROUNDED_ACTIVE")
    SUPERSEDED_TRAP: int = Field(ge=0, alias="SUPERSEDED_TRAP")
    RETRACTED_TRAP: int = Field(ge=0, alias="RETRACTED_TRAP")
    DELETED_TRAP: int = Field(ge=0, alias="DELETED_TRAP")
    REJECTED_TRAP: int = Field(ge=0, alias="REJECTED_TRAP")
    UNAVAILABLE_TRAP: int = Field(ge=0, alias="UNAVAILABLE_TRAP")
    UNRESOLVED_CONFLICT: int = Field(ge=0, alias="UNRESOLVED_CONFLICT")
    DECISION_CANDIDATE: int = Field(ge=0, alias="DECISION_CANDIDATE")
    EXACT_DUPLICATE_TRAP: int = Field(ge=0, alias="EXACT_DUPLICATE_TRAP")
    SEMANTIC_DUPLICATE_TRAP: int = Field(ge=0, alias="SEMANTIC_DUPLICATE_TRAP")
    CATEGORY_COLLISION_TRAP: int = Field(ge=0, alias="CATEGORY_COLLISION_TRAP")
    PARENT_COLLISION_TRAP: int = Field(ge=0, alias="PARENT_COLLISION_TRAP")
    CASE_ORDERING: int = Field(ge=0, alias="CASE_ORDERING")
    BLANK_OR_INVALID_RESULT: int = Field(ge=0, alias="BLANK_OR_INVALID_RESULT")
    APPLY_PROVENANCE: int = Field(ge=0, alias="APPLY_PROVENANCE")
    SUGGESTION_PROVENANCE: int = Field(ge=0, alias="SUGGESTION_PROVENANCE")


class GenerationQualityProgressResource(ApiModel):
    approve_count: int = Field(ge=0, le=2)
    required_approve_count: Literal[2]
    rejected: bool


class GenerationQualityDecisionResource(ApiModel):
    reviewer_slot: int = Field(ge=1, le=2)
    reviewer_seal: Seal
    decision: Literal["APPROVE", "REJECT"]
    reason_code: QualityReviewReason | None
    decision_fingerprint: Digest = Field(pattern=_DIGEST_PATTERN)
    observed_artifact_fingerprint: Digest = Field(pattern=_DIGEST_PATTERN)
    decided_at: datetime


class GenerationQualityApprovalResource(ApiModel):
    review_authority_fingerprint: Digest = Field(pattern=_DIGEST_PATTERN)
    approval_artifact_fingerprint: Digest = Field(pattern=_DIGEST_PATTERN)
    approved_at: datetime


class GenerationQualityCandidateResource(ApiModel):
    candidate_id: str = Field(pattern=r"^review-candidate:[a-f0-9]{64}$")
    status: QualityReviewStatus
    reviewable: bool
    materialization: Literal["COMPLETE"]
    created_at: datetime
    captured_at: datetime
    capture_environment: Literal["staging", "production"]
    capture_revision: OpaqueToken
    fingerprints: GenerationQualityFingerprintsResource
    source: GenerationQualitySourceResource
    privacy: GenerationQualityPrivacyResource
    envelope_contract_version: Literal["review-candidate-envelope/1.0.0"]
    payload_schema_version: Literal["generation-quality-review-projection-v2"]
    counts: GenerationQualityCountsResource
    classification_distribution: GenerationQualityClassificationResource
    progress: GenerationQualityProgressResource
    decisions: list[GenerationQualityDecisionResource] = Field(max_length=2)
    approval: GenerationQualityApprovalResource | None
    certification_status: Literal["NOT_REVIEWED", "REVIEWED_NOT_RELEASE_CERTIFIED"]
    release_certified: Literal[False]


class GenerationQualityCandidatePage(ApiModel):
    items: list[GenerationQualityCandidateResource] = Field(max_length=200)
    next_cursor: str | None = Field(
        default=None, pattern=r"^review-candidate:[a-f0-9]{64}$"
    )


class GenerationQualityReviewedReviewResource(ApiModel):
    status: Literal["REVIEWED"]
    reviewed_at: datetime
    required_approvals: Literal[2]
    reviewer_authority_seals: list[Seal] = Field(min_length=2, max_length=2)
    decision_fingerprints: list[Digest] = Field(min_length=2, max_length=2)
    review_authority_fingerprint: Digest = Field(pattern=_DIGEST_PATTERN)
    approval_artifact_fingerprint: Digest = Field(pattern=_DIGEST_PATTERN)
    approved_at: datetime


class GenerationQualityReviewedAttestationResource(ApiModel):
    status: Literal["NOT_RUN"]


class GenerationQualityReviewedCertificationResource(ApiModel):
    status: Literal["REVIEWED_NOT_RELEASE_CERTIFIED"]
    eligible: Literal[False]
    release_certified: Literal[False]
    limitations: list[
        Literal[
            "ACTUAL_32_ROOT_16_CLASS_SAMPLE_NOT_ATTESTED",
            "PROVIDER_LIVE_ATTESTATION_NOT_RUN",
            "NOT_A_RELEASE_CERTIFICATION",
        ]
    ] = Field(min_length=3, max_length=3)


class GenerationQualityEnvelopeIdentityResource(ApiModel):
    candidate_key: str = Field(pattern=r"^review-candidate:[a-f0-9]{64}$")
    revision: Literal[1]
    created_at: datetime
    predecessor_artifact_fingerprint: None


class GenerationQualityEnvelopeSourceResource(GenerationQualitySourceResource):
    synthetic: Literal[False]


class GenerationQualityEnvelopeSanitizationResource(ApiModel):
    policy_version: Literal["review-candidate-sensitive-data-v1"]
    normalization_version: Literal["review-candidate-nfkc-lf-ignorables-v1"]
    mode: Literal["REJECT"]
    status: Literal["CLEAN"]
    redaction_count: Literal[0]
    finding_categories: list[str] = Field(max_length=0)
    policy_fingerprint: Digest


class GenerationQualityEnvelopeSamplingResource(ApiModel):
    strategy: Literal["ALL"]
    algorithm_version: Literal["review-candidate-sampling-v1"]
    population_size: int = Field(ge=1)
    sample_size: int = Field(ge=1)
    seed_fingerprint: Digest

    @model_validator(mode="after")
    def all_sampling_uses_the_population(self) -> Self:
        if self.population_size != self.sample_size:
            raise ValueError("ALL sampling requires the full population")
        return self


class GenerationQualityEnvelopeProjectionResource(ApiModel):
    capture_environment: Literal["staging", "production"]
    capture_revision: OpaqueToken
    captured_at: datetime
    classification_counts: list[Annotated[int, Field(ge=0)]] = Field(
        min_length=16, max_length=16
    )
    dataset_kind: Literal["operational-golden"]
    declared_counts: GenerationQualityCountsResource
    live_gate_status: Literal["not-run"]
    materialization: Literal["complete"]
    profile: Literal["candidate"]
    release_certified: Literal[False]


class GenerationQualityEnvelopePayloadResource(ApiModel):
    schema_version: Literal["generation-quality-review-projection-v2"]
    projection: GenerationQualityEnvelopeProjectionResource


class GenerationQualityEnvelopeReviewResource(ApiModel):
    status: Literal["UNREVIEWED"]
    reviewed_at: None
    review_authority_seal: None
    approval_artifact_fingerprint: None


class GenerationQualityEnvelopeAttestationResource(ApiModel):
    status: Literal["NOT_RUN"]
    attested_at: None
    attestation_fingerprint: None


class GenerationQualityEnvelopeRetentionResource(ApiModel):
    policy_version: Literal["review-candidate-retention-v1"]
    disposition: Literal["DELETE"]
    max_age_days: int = Field(ge=1, le=30)
    expires_at: datetime


class GenerationQualityEnvelopeCertificationResource(ApiModel):
    status: Literal["NOT_CERTIFIED"]
    eligible: Literal[False]
    scope: None
    certification_fingerprint: None
    limitations: list[
        Literal[
            "ATTESTATION_NOT_RUN",
            "NOT_A_RELEASE_CERTIFICATION",
            "REVIEW_REQUIRED",
            "ACTUAL_DATA_MINIMIZED",
        ]
    ] = Field(min_length=4, max_length=4)


class GenerationQualityBaseEnvelopeResource(ApiModel):
    artifact_kind: Literal["REVIEW_CANDIDATE"]
    contract_version: Literal["review-candidate-envelope/1.0.0"]
    purpose: Literal["Operational generation quality review"]
    data_class: Literal["ACTUAL_MINIMIZED"]
    candidate: GenerationQualityEnvelopeIdentityResource
    source: GenerationQualityEnvelopeSourceResource
    sanitization: GenerationQualityEnvelopeSanitizationResource
    sampling: GenerationQualityEnvelopeSamplingResource
    payload: GenerationQualityEnvelopePayloadResource
    fingerprints: GenerationQualityFingerprintsResource
    review: GenerationQualityEnvelopeReviewResource
    attestation: GenerationQualityEnvelopeAttestationResource
    retention: GenerationQualityEnvelopeRetentionResource
    certification: GenerationQualityEnvelopeCertificationResource


class GenerationQualityReviewedSnapshotResource(ApiModel):
    artifact_kind: Literal["AUTOMATIC_GENERATION_QUALITY_REVIEWED_EXPORT"]
    contract_version: Literal["automatic-generation-quality-reviewed-export/2.0.0"]
    candidate_key: str = Field(pattern=r"^review-candidate:[a-f0-9]{64}$")
    base_envelope: GenerationQualityBaseEnvelopeResource
    review: GenerationQualityReviewedReviewResource
    attestation: GenerationQualityReviewedAttestationResource
    certification: GenerationQualityReviewedCertificationResource

    @model_validator(mode="after")
    def candidate_key_matches_envelope(self) -> Self:
        if self.candidate_key != self.base_envelope.candidate.candidate_key:
            raise ValueError("reviewed candidate key must match its base envelope")
        return self
