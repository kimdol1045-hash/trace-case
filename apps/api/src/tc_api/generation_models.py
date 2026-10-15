"""Generation job API 1.0.0 request and response models."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Annotated, Literal, Self

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from tc_api.models import ApiModel, RequestModel
from tc_backend.generation.reconstruction import GenerationInputV1

GenerationJobStatusValue = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
GenerationProviderErrorCodeValue = Literal[
    "GENERATION_UNSUPPORTED_SCHEMA_VERSION",
    "GENERATION_AUTH_REJECTED",
    "GENERATION_TIMEOUT",
    "GENERATION_TRANSPORT_UNAVAILABLE",
    "GENERATION_RATE_LIMITED",
    "GENERATION_PROVIDER_REJECTED",
    "GENERATION_PROVIDER_ERROR",
    "GENERATION_INVALID_CONTENT_TYPE",
    "GENERATION_RESPONSE_TOO_LARGE",
    "GENERATION_MALFORMED_JSON",
    "GENERATION_INVALID_RESPONSE",
]
_IDENTIFIER_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*$"
_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_SEMVER_PATTERN = (
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class CreateGenerationJobRequest(RequestModel):
    idempotencyKey: str = Field(
        min_length=8, max_length=255, pattern=_IDEMPOTENCY_PATTERN
    )
    slackWorkItemId: str = Field(
        min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )


class ExecuteGenerationJobRequest(RequestModel):
    idempotencyKey: str = Field(
        min_length=8, max_length=255, pattern=_IDEMPOTENCY_PATTERN
    )


class RetryGenerationJobRequest(RequestModel):
    idempotencyKey: str = Field(
        min_length=8, max_length=255, pattern=_IDEMPOTENCY_PATTERN
    )
    contextMode: Literal["REUSE_SEALED"]
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class GenerationJobLineage(ApiModel):
    root_job_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    retry_of_job_id: str | None = Field(max_length=128)
    attempt_number: int = Field(ge=1)


class GenerationContextProvenanceResource(ApiModel):
    input_schema_version: Literal["1.0.0"]
    output_schema_version: Literal["1.0.0"]
    source_version: int = Field(ge=1)
    generation_version: int = Field(ge=1)
    ontology_schema_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    prompt_version: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    item_count: int = Field(ge=1)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    sealed_at: datetime


class GenerationFailureResource(ApiModel):
    code: GenerationProviderErrorCodeValue
    message: str = Field(min_length=1, max_length=500, pattern=r"\S")
    retryable: bool
    attempts: int = Field(ge=1, le=5)
    occurred_at: datetime


class GenerationJobResource(ApiModel):
    id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    slack_work_item_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    status: GenerationJobStatusValue
    lineage: GenerationJobLineage
    context: GenerationContextProvenanceResource
    failure: GenerationFailureResource | None
    result: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class GenerationContextSourceResource(ApiModel):
    kind: Literal["SOURCE_REVISION", "LEDGER_ENTITY"]
    entity_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=64)
    entity_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    revision: str = Field(min_length=1, max_length=500, pattern=r"\S")
    content_hash: str = Field(min_length=1, max_length=128, pattern=r"\S")
    permalink: str | None = Field(max_length=2048)


class GenerationContextItemResource(ApiModel):
    id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    knowledge_evidence_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    subject_type: Literal["NODE", "EDGE"]
    subject_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    source: GenerationContextSourceResource
    rank: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    snapshot: dict[str, Any]


class GenerationJobDetailResource(ApiModel):
    job: GenerationJobResource
    context_items: list[GenerationContextItemResource] = Field(
        min_length=1, max_length=5000
    )


class GenerationJobPageResponse(ApiModel):
    items: list[GenerationJobResource] = Field(max_length=200)
    next_cursor: str | None = Field(max_length=1024)


GenerationReconstructionReasonValue = Literal[
    "LEGACY_INPUT_NOT_SEALED",
    "SEALED_INPUT_INCOMPLETE",
    "UNSUPPORTED_INPUT_SCHEMA_VERSION",
]
GenerationInputSchemaVersionValue = Annotated[
    str,
    Field(min_length=5, max_length=100, pattern=_SEMVER_PATTERN),
]


class GenerationInputDigestResource(ApiModel):
    algorithm: Literal["SHA-256"]
    canonicalization: Literal["RFC8785"]
    value: str = Field(pattern=r"^[a-f0-9]{64}$")
    canonical_byte_length: int = Field(ge=2, le=16_777_216)


class GenerationReconstructionProvenanceResource(ApiModel):
    sealed_context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    sealed_at: datetime
    input_digest: GenerationInputDigestResource | None


class GenerationReconstructionReadPolicyResource(ApiModel):
    source_basis: Literal["SEALED_GENERATION_INPUT"]
    read_only: Literal[True]
    sealed_snapshots_only: Literal[True]
    live_slack_consulted: Literal[False]
    live_knowledge_consulted: Literal[False]
    live_cases_consulted: Literal[False]
    live_issues_consulted: Literal[False]
    live_categories_consulted: Literal[False]
    provider_call_attempted: Literal[False]
    mutation_performed: Literal[False]
    array_order_preserved: Literal[True]
    null_empty_distinctions_preserved: Literal[True]


class GenerationReconstructionResponse(ApiModel):
    reconstruction_schema_version: Literal["1.0.0"]
    job_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    availability: Literal["AVAILABLE", "UNAVAILABLE"]
    reason: GenerationReconstructionReasonValue | None
    recorded_input_schema_version: GenerationInputSchemaVersionValue | None
    input: GenerationInputV1 | None
    provenance: GenerationReconstructionProvenanceResource
    policy: GenerationReconstructionReadPolicyResource

    @model_validator(mode="after")
    def reconstruction_state_is_complete(self) -> Self:
        if self.availability == "AVAILABLE":
            if (
                self.reason is None
                and self.recorded_input_schema_version == "1.0.0"
                and self.input is not None
                and self.provenance.input_digest is not None
            ):
                return self
            raise ValueError(
                "available reconstruction requires a complete input capture"
            )
        if self.reason is None or self.input is not None:
            raise ValueError(
                "unavailable reconstruction requires a stable reason and null input"
            )
        if self.reason == "LEGACY_INPUT_NOT_SEALED":
            if (
                self.recorded_input_schema_version is None
                and self.provenance.input_digest is None
            ):
                return self
            raise ValueError("legacy reconstruction cannot expose input provenance")
        if self.reason == "SEALED_INPUT_INCOMPLETE":
            if self.provenance.input_digest is None:
                return self
            raise ValueError("incomplete reconstruction cannot expose a suspect digest")
        if (
            self.recorded_input_schema_version not in {None, "1.0.0"}
            and self.provenance.input_digest is not None
        ):
            return self
        raise ValueError(
            "unsupported reconstruction requires a verified unsupported version"
        )


Sha256Hex = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NullableScore = Annotated[float, Field(ge=0, le=1)] | None
CategoryMappingMethodValue = Literal[
    "EXACT_NAME", "EXACT_ALIAS", "SEMANTIC", "UNCATEGORIZED_FALLBACK"
]
ParentReuseMethodValue = Literal[
    "SLACK_SOURCE_IDENTITY", "EXACT_KEY", "EXACT_TITLE", "EXACT_ALIAS", "SEMANTIC"
]
MatchReasonValue = Literal["EXACT", "SEMANTIC"]


class GenerationCategoryPlacementRequest(RequestModel):
    categoryId: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    expectedResourceVersion: int = Field(ge=1)
    method: CategoryMappingMethodValue
    score: NullableScore


class ReuseGenerationParentPlacementRequest(RequestModel):
    decision: Literal["REUSE_EXISTING"]
    workItemId: str = Field(pattern=r"^work_item_[A-Za-z0-9_-]+$", max_length=128)
    expectedResourceVersion: int = Field(ge=1)
    expectedCategoryId: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    method: ParentReuseMethodValue
    score: NullableScore


class CreateGenerationParentPlacementRequest(RequestModel):
    decision: Literal["CREATE_NEW"]
    expectedWorkItemKey: str = Field(min_length=1, max_length=100, pattern=r"\S")
    expectedSummary: str = Field(min_length=1, max_length=200, pattern=r"\S")
    categoryId: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    method: Literal["CREATE_NEW_PARENT"]


GenerationParentPlacementRequest = Annotated[
    ReuseGenerationParentPlacementRequest | CreateGenerationParentPlacementRequest,
    Field(discriminator="decision"),
]


class CreateGenerationCasePlacementRequest(RequestModel):
    generatedCaseIndex: int = Field(ge=0, le=9)
    exactContentKey: Sha256Hex
    decision: Literal["CREATE_NEW"]


class ReuseGenerationCasePlacementRequest(RequestModel):
    generatedCaseIndex: int = Field(ge=0, le=9)
    exactContentKey: Sha256Hex
    decision: Literal["REUSE_EXISTING"]
    testCaseId: str = Field(pattern=r"^case_[A-Za-z0-9_-]+$", max_length=128)
    expectedResourceVersion: int = Field(ge=1)
    expectedCurrentVersionId: str = Field(
        pattern=r"^case_version_[A-Za-z0-9_-]+$", max_length=128
    )
    expectedCurrentVersion: int = Field(ge=1)
    matchReason: MatchReasonValue


GenerationCasePlacementRequest = Annotated[
    CreateGenerationCasePlacementRequest | ReuseGenerationCasePlacementRequest,
    Field(discriminator="decision"),
]


class GenerationPlacementRequest(RequestModel):
    placementSchemaVersion: Literal["1.0.0"]
    category: GenerationCategoryPlacementRequest
    parent: GenerationParentPlacementRequest
    cases: list[GenerationCasePlacementRequest] = Field(min_length=5, max_length=10)

    @model_validator(mode="after")
    def cases_are_complete_and_ordered(self) -> Self:
        indexes = [row.generatedCaseIndex for row in self.cases]
        if indexes != list(range(len(self.cases))):
            raise ValueError(
                "placement cases must be unique and contiguous in generated result order"
            )
        return self


class ApplyGenerationJobRequest(RequestModel):
    idempotencyKey: str = Field(
        min_length=8, max_length=255, pattern=_IDEMPOTENCY_PATTERN
    )
    expectedContextDigest: Sha256Hex
    expectedResultDigest: Sha256Hex
    placement: GenerationPlacementRequest


class GenerationApplyPlanResource(ApiModel):
    generation_job_id: str = Field(
        pattern=r"^generation_job_[A-Za-z0-9_-]+$", max_length=128
    )
    job_status: Literal["SUCCEEDED"]
    expected_context_digest: Sha256Hex
    expected_result_digest: Sha256Hex
    placement: GenerationPlacementRequest


class AppliedTestStepResource(ApiModel):
    sequence: int = Field(ge=1)
    precondition: str | None
    action: str = Field(min_length=1, max_length=20000, pattern=r"\S")


class GenerationSourceProvenanceResource(ApiModel):
    source_type: Literal["SLACK_AI"]
    slack_work_item_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    generation_job_id: str = Field(
        pattern=r"^generation_job_[A-Za-z0-9_-]+$", max_length=128
    )
    context_digest: Sha256Hex
    result_digest: Sha256Hex
    output_schema_version: Literal["1.0.0"]
    generation_version: int = Field(ge=1)
    evidence_node_ids: list[str] = Field(min_length=1, max_length=100)
    source_revision_ids: list[str] = Field(min_length=1, max_length=5000)
    inference_only: bool
    open_questions: list[str] = Field(max_length=50)


class AppliedTestCaseVersionResource(ApiModel):
    id: str = Field(pattern=r"^case_version_[A-Za-z0-9_-]+$", max_length=128)
    version: Literal[1]
    summary: str = Field(min_length=1, max_length=200, pattern=r"\S")
    precondition: str = Field(min_length=1, max_length=20000, pattern=r"\S")
    expected_result_mode: Literal["CASE"]
    case_expected_result: str = Field(min_length=1, max_length=20000, pattern=r"\S")
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    labels: list[str] = Field(max_length=50)
    steps: list[AppliedTestStepResource] = Field(min_length=1, max_length=500)
    generation_reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    source: GenerationSourceProvenanceResource
    created_at: datetime


class CreatedAppliedTestCaseResource(ApiModel):
    id: str = Field(pattern=r"^case_[A-Za-z0-9_-]+$", max_length=128)
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,15}-[0-9]{6,}$", max_length=64)
    work_item_id: str = Field(pattern=r"^work_item_[A-Za-z0-9_-]+$", max_length=128)
    category_id: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    status: Literal["ACTIVE"]
    resource_version: Literal[1]
    current_version: AppliedTestCaseVersionResource


class ReusedAppliedTestCaseResource(ApiModel):
    id: str = Field(pattern=r"^case_[A-Za-z0-9_-]+$", max_length=128)
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,15}-[0-9]{6,}$", max_length=64)
    work_item_id: str = Field(pattern=r"^work_item_[A-Za-z0-9_-]+$", max_length=128)
    category_id: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    status: Literal["ACTIVE"]
    resource_version: int = Field(ge=1)
    current_version_id: str = Field(
        pattern=r"^case_version_[A-Za-z0-9_-]+$", max_length=128
    )
    current_version: int = Field(ge=1)


class CreatedGenerationCaseOutcomeResource(ApiModel):
    generated_case_index: int = Field(ge=0, le=9)
    exact_content_key: Sha256Hex
    outcome: Literal["CREATED"]
    ledger_mutation: Literal["CREATED_CASE_AND_VERSION"]
    case: CreatedAppliedTestCaseResource


class ReusedGenerationCaseOutcomeResource(ApiModel):
    generated_case_index: int = Field(ge=0, le=9)
    exact_content_key: Sha256Hex
    outcome: Literal["REUSED"]
    ledger_mutation: Literal["NONE"]
    preserved: Literal[True]
    match_reason: MatchReasonValue
    case: ReusedAppliedTestCaseResource


GenerationCaseOutcomeResource = Annotated[
    CreatedGenerationCaseOutcomeResource | ReusedGenerationCaseOutcomeResource,
    Field(discriminator="outcome"),
]


class CreatedGenerationParentOutcomeResource(ApiModel):
    outcome: Literal["CREATED"]
    ledger_mutation: Literal["CREATED_WORK_ITEM"]
    work_item_id: str = Field(pattern=r"^work_item_[A-Za-z0-9_-]+$", max_length=128)
    key: str = Field(min_length=1, max_length=100, pattern=r"\S")
    category_id: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    resource_version: Literal[1]


class ReusedGenerationParentOutcomeResource(ApiModel):
    outcome: Literal["REUSED"]
    ledger_mutation: Literal["NONE"]
    work_item_id: str = Field(pattern=r"^work_item_[A-Za-z0-9_-]+$", max_length=128)
    key: str = Field(min_length=1, max_length=100, pattern=r"\S")
    category_id: str = Field(pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128)
    resource_version: int = Field(ge=1)


GenerationParentOutcomeResource = Annotated[
    CreatedGenerationParentOutcomeResource | ReusedGenerationParentOutcomeResource,
    Field(discriminator="outcome"),
]


class GenerationApplyCountsResource(ApiModel):
    generated: int = Field(ge=5, le=10)
    created: int = Field(ge=0, le=10)
    reused: int = Field(ge=0, le=10)


class GenerationApplyReceiptResource(ApiModel):
    id: str = Field(pattern=r"^generation_apply_[A-Za-z0-9_-]+$", max_length=128)
    generation_job_id: str = Field(
        pattern=r"^generation_job_[A-Za-z0-9_-]+$", max_length=128
    )
    job_status: Literal["SUCCEEDED"]
    context_digest: Sha256Hex
    result_digest: Sha256Hex
    placement_digest: Sha256Hex
    transaction_status: Literal["COMMITTED"]
    parent: GenerationParentOutcomeResource
    cases: list[GenerationCaseOutcomeResource] = Field(min_length=5, max_length=10)
    counts: GenerationApplyCountsResource
    audit_id: str = Field(pattern=r"^audit_[A-Za-z0-9_-]+$", max_length=128)
    applied_at: datetime

    @model_validator(mode="after")
    def receipt_counts_and_order_match(self) -> Self:
        if [row.generated_case_index for row in self.cases] != list(
            range(len(self.cases))
        ):
            raise ValueError("receipt cases must preserve generated result order")
        created = sum(row.outcome == "CREATED" for row in self.cases)
        if (
            self.counts.generated != len(self.cases)
            or self.counts.created != created
            or self.counts.reused != len(self.cases) - created
        ):
            raise ValueError("receipt counts must match case outcomes")
        return self


ApplyGenerationJobResponse = GenerationApplyReceiptResource


HistoricalTextState = Literal["PRESENT", "EMPTY", "REDACTED", "MISSING_SOURCE"]
HistoricalReason = Literal[
    "SEALED_EMPTY",
    "SENSITIVE_DATA",
    "AUTHORIZATION_POLICY",
    "NOT_SEALED",
    "SOURCE_INCOMPLETE",
    "LEGACY_CONTEXT",
    "UNRESOLVED_REFERENCE",
]
CollectionAvailability = Literal["AVAILABLE", "EMPTY", "REDACTED", "MISSING_SOURCE"]


class ComparisonJobResource(ApiModel):
    id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    slack_work_item_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    status: GenerationJobStatusValue
    lineage: GenerationJobLineage
    context: GenerationContextProvenanceResource
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ComparisonReadPolicyResource(ApiModel):
    source_basis: Literal["SEALED_GENERATION_JOB"]
    read_only: Literal[True]
    live_source_substitution: Literal[False]
    generation_input_reconstructed: Literal[False]
    provider_call_attempted: Literal[False]
    mutation_performed: Literal[False]


class HistoricalTextResource(ApiModel):
    state: HistoricalTextState
    value: Annotated[str, Field(min_length=1, pattern=r"\S")] | None
    reason: HistoricalReason | None

    @model_validator(mode="after")
    def state_matches_value(self) -> Self:
        if self.state == "PRESENT" and self.value is not None and self.reason is None:
            return self
        expected_reasons: dict[str, set[str]] = {
            "EMPTY": {"SEALED_EMPTY"},
            "REDACTED": {"SENSITIVE_DATA", "AUTHORIZATION_POLICY"},
            "MISSING_SOURCE": {
                "NOT_SEALED",
                "SOURCE_INCOMPLETE",
                "LEGACY_CONTEXT",
                "UNRESOLVED_REFERENCE",
            },
        }
        if (
            self.state != "PRESENT"
            and self.value is None
            and self.reason in expected_reasons[self.state]
        ):
            return self
        raise ValueError("historical text state, value, and reason do not match")


class HistoricalSnapshotResource(ApiModel):
    state: Literal["PRESENT", "REDACTED", "MISSING_SOURCE"]
    value: dict[str, Any] | None
    reason: HistoricalReason | None

    @model_validator(mode="after")
    def state_matches_value(self) -> Self:
        if self.state == "PRESENT" and self.value is not None and self.reason is None:
            return self
        expected_reasons: dict[str, set[str]] = {
            "REDACTED": {"SENSITIVE_DATA", "AUTHORIZATION_POLICY"},
            "MISSING_SOURCE": {"NOT_SEALED", "SOURCE_INCOMPLETE", "LEGACY_CONTEXT"},
        }
        if (
            self.state != "PRESENT"
            and self.value is None
            and self.reason in expected_reasons[self.state]
        ):
            return self
        raise ValueError("historical snapshot state, value, and reason do not match")


class ComparisonSourceReferenceResource(ApiModel):
    context_item_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    context_rank: int = Field(ge=1)
    knowledge_evidence_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    subject_type: Literal["NODE", "EDGE"]
    subject_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    source_kind: Literal["SOURCE_REVISION", "LEDGER_ENTITY"]
    source_entity_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=64)
    source_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    source_revision: str = Field(min_length=1, max_length=500, pattern=r"\S")
    content_hash: str = Field(min_length=1, max_length=128, pattern=r"\S")
    permalink: HistoricalTextResource


class SlackStructuredFieldResource(ApiModel):
    field: Literal[
        "DEVELOPMENT_ITEM",
        "REQUESTED_MENU",
        "FEATURE_DESCRIPTION",
        "REQUEST_DETAILS",
    ]
    value: HistoricalTextResource
    source_references: list[ComparisonSourceReferenceResource] = Field(max_length=1000)

    @model_validator(mode="after")
    def present_value_has_a_source(self) -> Self:
        if self.value.state == "PRESENT" and not self.source_references:
            raise ValueError("present Slack field must cite sealed context")
        return self


class SlackRevisionEvidenceResource(ApiModel):
    index: int = Field(ge=0)
    source_revision_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    message_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    message_ts: str = Field(pattern=r"^[0-9]{10,12}\.[0-9]{6}$")
    revision_ts: str = Field(pattern=r"^[0-9]{10,12}\.[0-9]{6}$")
    role: Literal["ROOT", "REPLY"]
    operation: Literal["CREATE", "EDIT", "DELETE"]
    author_id: HistoricalTextResource
    body: HistoricalTextResource
    permalink: HistoricalTextResource
    recorded_at: datetime | None
    source_references: list[ComparisonSourceReferenceResource] = Field(
        min_length=1, max_length=1000
    )

    @model_validator(mode="after")
    def deletion_has_empty_body(self) -> Self:
        if self.operation == "DELETE" and self.body.state != "EMPTY":
            raise ValueError("deleted Slack revision body must be sealed empty")
        return self


class SlackThreadEvidenceResource(ApiModel):
    availability: CollectionAvailability
    items: list[SlackRevisionEvidenceResource] = Field(max_length=5000)
    reason: HistoricalReason | None

    @model_validator(mode="after")
    def availability_matches_items(self) -> Self:
        _validate_collection(self.availability, self.items, self.reason, "SEALED_EMPTY")
        return self


class SlackSourceComparisonResource(ApiModel):
    slack_work_item_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    source_version: int = Field(ge=1)
    workspace_id: HistoricalTextResource
    channel_id: HistoricalTextResource
    message_ts: HistoricalTextResource
    thread_ts: HistoricalTextResource
    permalink: HistoricalTextResource
    fields: list[SlackStructuredFieldResource] = Field(min_length=4, max_length=4)
    thread_evidence: SlackThreadEvidenceResource

    @model_validator(mode="after")
    def fields_have_fixed_order(self) -> Self:
        if [row.field for row in self.fields] != [
            "DEVELOPMENT_ITEM",
            "REQUESTED_MENU",
            "FEATURE_DESCRIPTION",
            "REQUEST_DETAILS",
        ]:
            raise ValueError("Slack structured fields must use fixed contract order")
        return self


class ActiveKnowledgeComparisonResource(ApiModel):
    evidence_node_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    node_type: Literal["REQUIREMENT", "DECISION", "CONSTRAINT"]
    status: Literal["ACTIVE"]
    active_at_seal: Literal[True]
    content: HistoricalTextResource
    valid_from: datetime | None
    valid_to: None
    selection_reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    source_references: list[ComparisonSourceReferenceResource] = Field(
        min_length=1, max_length=1000
    )


class ActiveKnowledgeCollectionResource(ApiModel):
    availability: CollectionAvailability
    items: list[ActiveKnowledgeComparisonResource] = Field(max_length=500)
    reason: HistoricalReason | None

    @model_validator(mode="after")
    def availability_matches_items(self) -> Self:
        _validate_collection(self.availability, self.items, self.reason, "SEALED_EMPTY")
        return self


class ExistingCaseStepResource(ApiModel):
    sequence: int = Field(ge=1)
    precondition: str | None
    action: str = Field(min_length=1, pattern=r"\S")
    expected_result: str | None


class ExistingCaseContentResource(ApiModel):
    summary: str = Field(min_length=1, max_length=200, pattern=r"\S")
    precondition: str | None
    steps: list[ExistingCaseStepResource] = Field(min_length=1, max_length=500)
    expected_result_mode: Literal["PER_STEP", "CASE"]
    case_expected_result: str | None
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    labels: list[str] = Field(max_length=50)


class HistoricalExistingCaseContentResource(ApiModel):
    state: Literal["PRESENT", "REDACTED", "MISSING_SOURCE"]
    value: ExistingCaseContentResource | None
    reason: HistoricalReason | None

    @model_validator(mode="after")
    def state_matches_value(self) -> Self:
        if self.state == "PRESENT" and self.value is not None and self.reason is None:
            return self
        expected_reasons: dict[str, set[str]] = {
            "REDACTED": {"SENSITIVE_DATA", "AUTHORIZATION_POLICY"},
            "MISSING_SOURCE": {"NOT_SEALED", "SOURCE_INCOMPLETE", "LEGACY_CONTEXT"},
        }
        if (
            self.state != "PRESENT"
            and self.value is None
            and self.reason in expected_reasons[self.state]
        ):
            return self
        raise ValueError(
            "historical case content state, value, and reason do not match"
        )


class ExistingCaseComparisonResource(ApiModel):
    evidence_node_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    test_case_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    test_case_version_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    case_key: HistoricalTextResource
    status: Literal["ACTIVE"]
    content: HistoricalExistingCaseContentResource
    selection_reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    source_references: list[ComparisonSourceReferenceResource] = Field(
        min_length=1, max_length=1000
    )


class ExistingCaseCollectionResource(ApiModel):
    availability: CollectionAvailability
    items: list[ExistingCaseComparisonResource] = Field(max_length=500)
    reason: HistoricalReason | None

    @model_validator(mode="after")
    def availability_matches_items(self) -> Self:
        _validate_collection(self.availability, self.items, self.reason, "SEALED_EMPTY")
        return self


class SupplementalEvidenceComparisonResource(ApiModel):
    evidence_node_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    kind: Literal["RISK_EXECUTION", "OPEN_ISSUE", "EXPLICIT_SOURCE", "OTHER_SEALED"]
    title: HistoricalTextResource
    snapshot: HistoricalSnapshotResource
    selection_reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    source_references: list[ComparisonSourceReferenceResource] = Field(
        min_length=1, max_length=1000
    )


class SupplementalEvidenceCollectionResource(ApiModel):
    availability: CollectionAvailability
    items: list[SupplementalEvidenceComparisonResource] = Field(max_length=1000)
    reason: HistoricalReason | None

    @model_validator(mode="after")
    def availability_matches_items(self) -> Self:
        _validate_collection(self.availability, self.items, self.reason, "SEALED_EMPTY")
        return self


class CandidateApplyLinkageResource(ApiModel):
    state: Literal["NOT_APPLIED", "APPLIED"]
    apply_id: str | None = Field(max_length=128)
    outcome: GenerationCaseOutcomeResource | None

    @model_validator(mode="after")
    def state_matches_outcome(self) -> Self:
        if (
            self.state == "NOT_APPLIED"
            and self.apply_id is None
            and self.outcome is None
        ):
            return self
        if (
            self.state == "APPLIED"
            and self.apply_id is not None
            and self.outcome is not None
        ):
            return self
        raise ValueError("candidate apply state and linkage do not match")


class GeneratedTestStepResource(ApiModel):
    sequence: int = Field(ge=1)
    precondition: (
        Annotated[str, Field(min_length=1, pattern=r"\S")] | SkipJsonSchema[None]
    ) = Field(default=None, exclude_if=lambda value: value is None)
    action: str = Field(min_length=1, pattern=r"\S")


class GeneratedTestCaseResource(ApiModel):
    summary: str = Field(min_length=1, max_length=200, pattern=r"\S")
    steps: list[GeneratedTestStepResource] = Field(min_length=1, max_length=500)
    case_expected_result: str = Field(min_length=1, pattern=r"\S")
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    labels: list[Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]] = (
        Field(max_length=50, json_schema_extra={"uniqueItems": True})
    )
    evidence_node_ids: list[
        Annotated[str, Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)]
    ] = Field(
        min_length=1,
        max_length=100,
        json_schema_extra={"uniqueItems": True},
    )
    generation_reason: str = Field(min_length=1, max_length=500, pattern=r"\S")
    inference_only: bool
    open_questions: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        max_length=50,
        json_schema_extra={"uniqueItems": True},
    )


class GenerationCategoryAlternativeResource(ApiModel):
    category_id: str = Field(
        min_length=8, pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128
    )
    score: float = Field(ge=0, le=1)


class GenerationCategoryCandidateResource(ApiModel):
    category_id: str = Field(
        min_length=8, pattern=r"^folder_[A-Za-z0-9_-]+$", max_length=128
    )
    score: float = Field(ge=0, le=1)
    alternatives: list[GenerationCategoryAlternativeResource] = Field(max_length=99)


class GeneratedCandidateComparisonResource(ApiModel):
    index: int = Field(ge=0, le=9)
    candidate: GeneratedTestCaseResource
    apply: CandidateApplyLinkageResource


class GenerationResultComparisonResource(ApiModel):
    availability: CollectionAvailability
    output_schema_version: Literal["1.0.0"]
    result_digest: Sha256Hex | None
    category_candidate: GenerationCategoryCandidateResource | None
    candidates: list[GeneratedCandidateComparisonResource] = Field(max_length=10)
    reason: (
        Literal[
            "JOB_NOT_SUCCEEDED",
            "SENSITIVE_DATA",
            "AUTHORIZATION_POLICY",
            "RESULT_MISSING",
            "LEGACY_CONTEXT",
        ]
        | None
    )

    @model_validator(mode="after")
    def availability_matches_candidates(self) -> Self:
        if (
            self.availability == "AVAILABLE"
            and 5 <= len(self.candidates) <= 10
            and self.result_digest is not None
            and self.category_candidate is not None
            and self.reason is None
            and [row.index for row in self.candidates]
            == list(range(len(self.candidates)))
        ):
            return self
        if (
            self.availability == "EMPTY"
            and not self.candidates
            and self.reason == "JOB_NOT_SUCCEEDED"
        ):
            return self
        if (
            self.availability == "REDACTED"
            and not self.candidates
            and self.result_digest is not None
            and self.category_candidate is None
            and self.reason in {"SENSITIVE_DATA", "AUTHORIZATION_POLICY"}
        ):
            return self
        if (
            self.availability == "MISSING_SOURCE"
            and not self.candidates
            and self.result_digest is None
            and self.category_candidate is None
            and self.reason in {"RESULT_MISSING", "LEGACY_CONTEXT"}
        ):
            return self
        raise ValueError("generation availability and payload do not match")


ReferenceSourceKind = Literal[
    "SLACK_WORK_ITEM",
    "ACTIVE_KNOWLEDGE",
    "EXISTING_CASE",
    "RISK_EXECUTION",
    "OPEN_ISSUE",
    "EXPLICIT_SOURCE",
    "OTHER_SEALED",
    "UNKNOWN",
]
ResolvedReferenceSourceKind = Literal[
    "SLACK_WORK_ITEM",
    "ACTIVE_KNOWLEDGE",
    "EXISTING_CASE",
    "RISK_EXECUTION",
    "OPEN_ISSUE",
    "EXPLICIT_SOURCE",
    "OTHER_SEALED",
]


class ResolvedCandidateReferenceTargetResource(ApiModel):
    state: Literal["RESOLVED"]
    source_kind: ResolvedReferenceSourceKind
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    context_references: list[ComparisonSourceReferenceResource] = Field(
        min_length=1, max_length=1000
    )
    reason: None


class UnavailableCandidateReferenceTargetResource(ApiModel):
    state: Literal["REDACTED", "MISSING_SOURCE"]
    source_kind: ReferenceSourceKind
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    context_references: list[ComparisonSourceReferenceResource] = Field(max_length=0)
    reason: HistoricalReason

    @model_validator(mode="after")
    def state_matches_reason(self) -> Self:
        if self.state == "REDACTED" and self.reason in {
            "SENSITIVE_DATA",
            "AUTHORIZATION_POLICY",
        }:
            return self
        if self.state == "MISSING_SOURCE" and self.reason in {
            "NOT_SEALED",
            "SOURCE_INCOMPLETE",
            "LEGACY_CONTEXT",
            "UNRESOLVED_REFERENCE",
        }:
            return self
        raise ValueError("unavailable reference target state and reason do not match")


CandidateReferenceTargetResource = Annotated[
    ResolvedCandidateReferenceTargetResource
    | UnavailableCandidateReferenceTargetResource,
    Field(discriminator="state"),
]


class CandidateReferenceEdgeResource(ApiModel):
    candidate_index: int = Field(ge=0, le=9)
    evidence_index: int = Field(ge=0, le=99)
    evidence_node_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    relation: Literal["SUPPORTED_BY"]
    target: CandidateReferenceTargetResource


class ReferenceEdgeCollectionResource(ApiModel):
    availability: CollectionAvailability
    items: list[CandidateReferenceEdgeResource] = Field(max_length=1000)
    reason: (
        Literal[
            "NO_GENERATED_CANDIDATES",
            "SENSITIVE_DATA",
            "AUTHORIZATION_POLICY",
            "RESULT_MISSING",
            "UNRESOLVED_REFERENCE",
            "LEGACY_CONTEXT",
        ]
        | None
    )

    @model_validator(mode="after")
    def availability_matches_items(self) -> Self:
        if self.availability == "AVAILABLE" and self.items and self.reason is None:
            return self
        if (
            self.availability == "EMPTY"
            and not self.items
            and self.reason == "NO_GENERATED_CANDIDATES"
        ):
            return self
        if (
            self.availability == "REDACTED"
            and not self.items
            and self.reason in {"SENSITIVE_DATA", "AUTHORIZATION_POLICY"}
        ):
            return self
        if (
            self.availability == "MISSING_SOURCE"
            and not self.items
            and self.reason
            in {"RESULT_MISSING", "UNRESOLVED_REFERENCE", "LEGACY_CONTEXT"}
        ):
            return self
        raise ValueError("reference edge availability and items do not match")


class ApplyReceiptHeaderResource(ApiModel):
    id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    generation_job_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    context_digest: Sha256Hex
    result_digest: Sha256Hex
    placement_digest: Sha256Hex
    transaction_status: Literal["COMMITTED"]
    parent: GenerationParentOutcomeResource
    counts: GenerationApplyCountsResource
    audit_id: str = Field(pattern=_IDENTIFIER_PATTERN, max_length=128)
    applied_at: datetime


class ApplyComparisonResource(ApiModel):
    state: Literal["NOT_APPLIED", "APPLIED"]
    receipt: ApplyReceiptHeaderResource | None

    @model_validator(mode="after")
    def state_matches_receipt(self) -> Self:
        if self.state == "NOT_APPLIED" and self.receipt is None:
            return self
        if self.state == "APPLIED" and self.receipt is not None:
            return self
        raise ValueError("comparison apply state and receipt do not match")


class GenerationComparisonResponse(ApiModel):
    comparison_schema_version: Literal["1.0.0"]
    job: ComparisonJobResource
    policy: ComparisonReadPolicyResource
    slack_source: SlackSourceComparisonResource
    active_knowledge: ActiveKnowledgeCollectionResource
    existing_cases: ExistingCaseCollectionResource
    supplemental_evidence: SupplementalEvidenceCollectionResource
    generation: GenerationResultComparisonResource
    reference_edges: ReferenceEdgeCollectionResource
    apply: ApplyComparisonResource


GetGenerationComparisonResponse = GenerationComparisonResponse


def _validate_collection(
    availability: CollectionAvailability,
    items: Sequence[object],
    reason: HistoricalReason | None,
    empty_reason: HistoricalReason,
) -> None:
    if availability == "AVAILABLE" and items and reason is None:
        return
    if availability == "EMPTY" and not items and reason == empty_reason:
        return
    if (
        availability == "REDACTED"
        and not items
        and reason in {"SENSITIVE_DATA", "AUTHORIZATION_POLICY"}
    ):
        return
    if (
        availability == "MISSING_SOURCE"
        and not items
        and reason in {"NOT_SEALED", "SOURCE_INCOMPLETE", "LEGACY_CONTEXT"}
    ):
        return
    raise ValueError("collection availability, items, and reason do not match")
