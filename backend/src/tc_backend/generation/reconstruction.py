"""Immutable Generation Input capture and read-only reconstruction semantics."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from tc_backend.db.models import GenerationJob
from tc_backend.generation.models import GENERATION_INPUT_SCHEMA_VERSION

GENERATION_INPUT_CAPTURE_LIMIT = 16 * 1024 * 1024
_IDENTIFIER_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*$"
_CATEGORY_IDENTIFIER_PATTERN = r"^folder_[A-Za-z0-9_-]+$"
_SEMVER_PATTERN = (
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

Identifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN),
]
CategoryIdentifier = Annotated[
    str,
    Field(min_length=8, max_length=128, pattern=_CATEGORY_IDENTIFIER_PATTERN),
]
NonBlankString = Annotated[str, Field(min_length=1, max_length=20_000, pattern=r"\S")]
ShortString = Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
NullableNonBlankString = NonBlankString | None
PositiveInt = Annotated[StrictInt, Field(ge=1)]
UriString = Annotated[str, Field(max_length=2_048, json_schema_extra={"format": "uri"})]
UriReferenceString = Annotated[
    str,
    Field(max_length=2_048, json_schema_extra={"format": "uri-reference"}),
]


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class GenerationInputContractModel(BaseModel):
    """Strict camel-case contract model shared by persistence and FastAPI."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


def _require_unique(values: Sequence[str], field: str) -> Sequence[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must contain unique values")
    return values


def _require_json_datetime(value: object, field: str) -> object:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a JSON date-time string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an RFC 3339 date-time") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return value


def _require_uri(value: str, field: str, *, absolute: bool) -> str:
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        raise ValueError(f"{field} must be a valid URI")
    if absolute and re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value) is None:
        raise ValueError(f"{field} must be an absolute URI")
    return value


class SlackWorkItemInput(GenerationInputContractModel):
    work_item_id: Identifier
    evidence_node_id: Identifier
    source_version: PositiveInt
    source_revision_ids: list[Identifier] = Field(min_length=1, max_length=1_000)
    permalink: UriString
    development_item: NullableNonBlankString
    requested_menu: NullableNonBlankString
    feature_description: NullableNonBlankString
    request_details: NullableNonBlankString
    missing_fields: list[
        Literal[
            "developmentItem",
            "requestedMenu",
            "featureDescription",
            "requestDetails",
        ]
    ] = Field(max_length=4)
    open_questions: list[ShortString] = Field(max_length=50)

    @field_validator("source_revision_ids", "missing_fields", "open_questions")
    @classmethod
    def values_are_unique(cls, value: list[str], info: Any) -> list[str]:
        return list(_require_unique(value, info.field_name))

    @field_validator("permalink")
    @classmethod
    def permalink_is_uri(cls, value: str) -> str:
        return _require_uri(value, "permalink", absolute=True)


class ActiveKnowledgeInput(GenerationInputContractModel):
    node_id: Identifier
    node_type: Literal["REQUIREMENT", "DECISION", "CONSTRAINT"]
    status: Literal["ACTIVE"]
    content: NonBlankString
    valid_from: datetime
    valid_to: None
    evidence_source_revision_ids: list[Identifier] = Field(min_length=1, max_length=100)
    rank: PositiveInt
    selection_reason: ShortString

    @field_validator("evidence_source_revision_ids")
    @classmethod
    def evidence_is_unique(cls, value: list[str]) -> list[str]:
        return list(_require_unique(value, "evidenceSourceRevisionIds"))

    @field_validator("valid_from", mode="before")
    @classmethod
    def valid_from_is_json_datetime(cls, value: object) -> object:
        return _require_json_datetime(value, "validFrom")


class ExistingCaseStepInput(GenerationInputContractModel):
    sequence: PositiveInt
    precondition: NullableNonBlankString = None
    action: NonBlankString
    expected_result: NullableNonBlankString


class ExistingCaseInput(GenerationInputContractModel):
    node_id: Identifier
    test_case_id: Identifier
    test_case_version_id: Identifier
    case_key: str = Field(min_length=3, max_length=64, pattern=r"^[A-Z][A-Z0-9]*-[0-9]+$")
    status: Literal["ACTIVE"]
    summary: str = Field(min_length=1, max_length=200, pattern=r"\S")
    precondition: NullableNonBlankString
    steps: list[ExistingCaseStepInput] = Field(min_length=1, max_length=500)
    expected_result_mode: Literal["PER_STEP", "CASE"]
    case_expected_result: NullableNonBlankString
    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    labels: list[Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]] = Field(
        max_length=50
    )
    rank: PositiveInt
    selection_reason: ShortString

    @field_validator("labels")
    @classmethod
    def labels_are_unique(cls, value: list[str]) -> list[str]:
        return list(_require_unique(value, "labels"))

    @model_validator(mode="after")
    def expected_result_shape(self) -> Self:
        if self.expected_result_mode == "CASE":
            if self.case_expected_result is None:
                raise ValueError("CASE mode requires caseExpectedResult")
        elif self.case_expected_result is not None or any(
            step.expected_result is None for step in self.steps
        ):
            raise ValueError("PER_STEP mode requires step results and null caseExpectedResult")
        return self


class FailedOrBlockedExecutionInput(GenerationInputContractModel):
    node_id: Identifier
    execution_attempt_id: Identifier
    test_case_version_id: Identifier
    status: Literal["FAIL", "BLOCKED"]
    platform: Literal["WEB", "AOS", "IOS"]
    actual_result: NonBlankString
    occurred_at: datetime
    linked_issue_ids: list[Identifier] = Field(max_length=100)
    rank: PositiveInt
    selection_reason: ShortString

    @field_validator("linked_issue_ids")
    @classmethod
    def issues_are_unique(cls, value: list[str]) -> list[str]:
        return list(_require_unique(value, "linkedIssueIds"))

    @field_validator("occurred_at", mode="before")
    @classmethod
    def occurred_at_is_json_datetime(cls, value: object) -> object:
        return _require_json_datetime(value, "occurredAt")


class OpenIssueInput(GenerationInputContractModel):
    node_id: Identifier
    issue_id: Identifier
    issue_key: str = Field(min_length=3, max_length=64, pattern=r"^[A-Z][A-Z0-9]*-[0-9]+$")
    status: Literal["OPEN", "IN_PROGRESS", "REOPENED"]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    summary: str = Field(min_length=1, max_length=200, pattern=r"\S")
    expected_result: NonBlankString
    actual_result: NonBlankString
    linked_execution_attempt_ids: list[Identifier] = Field(min_length=1, max_length=100)
    updated_at: datetime
    rank: PositiveInt
    selection_reason: ShortString

    @field_validator("linked_execution_attempt_ids")
    @classmethod
    def executions_are_unique(cls, value: list[str]) -> list[str]:
        return list(_require_unique(value, "linkedExecutionAttemptIds"))

    @field_validator("updated_at", mode="before")
    @classmethod
    def updated_at_is_json_datetime(cls, value: object) -> object:
        return _require_json_datetime(value, "updatedAt")


class ExtraSourceInput(GenerationInputContractModel):
    source_id: Identifier
    evidence_node_id: Identifier
    source_type: Literal["PRODUCT_SPEC", "DESIGN", "CODE_CHANGE", "OTHER"]
    title: ShortString
    content: NonBlankString
    uri: UriReferenceString | None
    content_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    provided_by_user: Literal[True]

    @field_validator("uri")
    @classmethod
    def uri_is_reference(cls, value: str | None) -> str | None:
        return None if value is None else _require_uri(value, "uri", absolute=False)


class CategoryCandidateInput(GenerationInputContractModel):
    category_id: CategoryIdentifier
    label: ShortString
    score: float = Field(ge=0, le=1)
    selection_reason: ShortString

    @field_validator("score", mode="before")
    @classmethod
    def score_is_json_number(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("score must be a JSON number")
        return value


class GenerationBatchInput(GenerationInputContractModel):
    batch_id: Identifier
    feature_group: ShortString
    batch_number: PositiveInt
    is_final_batch: bool


class GenerationPolicyInput(GenerationInputContractModel):
    policy_version: Identifier
    batch: GenerationBatchInput
    minimum_case_count: Literal[5]
    maximum_case_count: Literal[10]
    expected_result_mode: Literal["CASE"]
    prioritize_happy_path: Literal[True]
    include_applicable_crud: Literal[True]
    allow_unsupported_edge_cases: Literal[False]
    require_evidence_references: Literal[True]
    unknown_fact_handling: Literal["OPEN_QUESTION"]
    sensitive_data_handling: Literal["REDACT"]
    language: str = Field(
        min_length=2,
        max_length=35,
        pattern=r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$",
    )


class GenerationInputV1(GenerationInputContractModel):
    input_schema_version: Literal["1.0.0"]
    requested_output_schema_version: Literal["1.0.0"]
    request_id: Identifier
    created_at: datetime
    slack_work_item: SlackWorkItemInput
    active_knowledge: list[ActiveKnowledgeInput] = Field(max_length=500)
    existing_cases: list[ExistingCaseInput] = Field(max_length=500)
    failed_or_blocked_executions: list[FailedOrBlockedExecutionInput] = Field(max_length=500)
    open_issues: list[OpenIssueInput] = Field(max_length=500)
    extra_sources: list[ExtraSourceInput] = Field(max_length=100)
    category_candidates: list[CategoryCandidateInput] = Field(min_length=1, max_length=100)
    generation_policy: GenerationPolicyInput

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_is_json_datetime(cls, value: object) -> object:
        return _require_json_datetime(value, "createdAt")


class GenerationInputCaptureState(StrEnum):
    LEGACY_NOT_CAPTURED = "LEGACY_NOT_CAPTURED"
    SEALED = "SEALED"


class GenerationReconstructionReason(StrEnum):
    LEGACY_INPUT_NOT_SEALED = "LEGACY_INPUT_NOT_SEALED"
    SEALED_INPUT_INCOMPLETE = "SEALED_INPUT_INCOMPLETE"
    UNSUPPORTED_INPUT_SCHEMA_VERSION = "UNSUPPORTED_INPUT_SCHEMA_VERSION"


@dataclass(frozen=True, slots=True)
class CanonicalInputDigest:
    value: str
    canonical_byte_length: int


@dataclass(frozen=True, slots=True)
class GenerationReconstruction:
    job: GenerationJob
    availability: Literal["AVAILABLE", "UNAVAILABLE"]
    reason: GenerationReconstructionReason | None
    recorded_input_schema_version: str | None
    generation_input: dict[str, Any] | None
    input_digest: CanonicalInputDigest | None


class GenerationInputCaptureError(ValueError):
    """A trusted Generation Input cannot be sealed without losing exactness."""


class GenerationInputUnavailable(RuntimeError):
    """Execution was requested for a job without a trusted sealed input."""

    def __init__(self, job_id: Any, reason: GenerationReconstructionReason) -> None:
        self.job_id = job_id
        self.reason = reason
        super().__init__("The generation job has no trusted executable input.")


def seal_generation_input(
    payload: Mapping[str, object],
) -> tuple[dict[str, Any], CanonicalInputDigest]:
    """Validate, normalize once, and hash the exact JSON value persisted for a new job."""

    try:
        validated = GenerationInputV1.model_validate(payload)
    except ValidationError as error:
        raise GenerationInputCaptureError("Generation Input 1.0.0 validation failed") from error
    normalized = validated.model_dump(mode="json", by_alias=True, exclude_none=False)
    digest = canonical_input_digest(normalized)
    if digest.canonical_byte_length > GENERATION_INPUT_CAPTURE_LIMIT:
        raise GenerationInputCaptureError("Generation Input exceeds the canonical byte limit")
    return normalized, digest


def reconstruct_generation_input(
    job: GenerationJob,
    *,
    context_valid: bool,
) -> GenerationReconstruction:
    """Classify only immutable fields already loaded with the generation job."""

    if not context_valid:
        return _unavailable(job, GenerationReconstructionReason.SEALED_INPUT_INCOMPLETE)
    state = job.generation_input_capture_state
    version = job.generation_input_schema_version
    payload = job.generation_input
    stored_digest = job.generation_input_digest
    if state == GenerationInputCaptureState.LEGACY_NOT_CAPTURED.value:
        if version is None and payload is None and stored_digest is None:
            return _unavailable(job, GenerationReconstructionReason.LEGACY_INPUT_NOT_SEALED)
        return _unavailable(job, GenerationReconstructionReason.SEALED_INPUT_INCOMPLETE)
    if (
        state != GenerationInputCaptureState.SEALED.value
        or not isinstance(version, str)
        or re.fullmatch(_SEMVER_PATTERN, version) is None
        or not isinstance(payload, dict)
        or not isinstance(stored_digest, str)
    ):
        return _unavailable(job, GenerationReconstructionReason.SEALED_INPUT_INCOMPLETE)
    try:
        digest = canonical_input_digest(payload)
    except (TypeError, ValueError, UnicodeError):
        return _unavailable(job, GenerationReconstructionReason.SEALED_INPUT_INCOMPLETE)
    if (
        digest.value != stored_digest
        or digest.canonical_byte_length > GENERATION_INPUT_CAPTURE_LIMIT
        or payload.get("inputSchemaVersion") != version
        or payload.get("requestedOutputSchemaVersion") != job.schema_version
    ):
        return _unavailable(job, GenerationReconstructionReason.SEALED_INPUT_INCOMPLETE)
    if version != GENERATION_INPUT_SCHEMA_VERSION:
        return GenerationReconstruction(
            job=job,
            availability="UNAVAILABLE",
            reason=GenerationReconstructionReason.UNSUPPORTED_INPUT_SCHEMA_VERSION,
            recorded_input_schema_version=version,
            generation_input=None,
            input_digest=digest,
        )
    try:
        GenerationInputV1.model_validate(payload)
    except ValidationError:
        return _unavailable(job, GenerationReconstructionReason.SEALED_INPUT_INCOMPLETE)
    return GenerationReconstruction(
        job=job,
        availability="AVAILABLE",
        reason=None,
        recorded_input_schema_version=version,
        generation_input=copy.deepcopy(payload),
        input_digest=digest,
    )


def canonical_input_digest(value: object) -> CanonicalInputDigest:
    canonical = canonical_json(value).encode("utf-8")
    return CanonicalInputDigest(
        value=hashlib.sha256(canonical).hexdigest(),
        canonical_byte_length=len(canonical),
    )


def canonical_json(value: object) -> str:
    """Render the JSON Canonicalization Scheme representation from RFC 8785."""

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, list | tuple):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        ordered_keys = sorted(value, key=_utf16_sort_key)
        return (
            "{"
            + ",".join(
                f"{canonical_json(key)}:{canonical_json(value[key])}" for key in ordered_keys
            )
            + "}"
        )
    raise TypeError(f"value is not JSON-canonicalizable: {type(value).__name__}")


def _canonical_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("canonical JSON accepts only finite numbers")
    if value == 0:
        return "0"
    absolute = abs(value)
    rendered = repr(value).lower()
    if 1e-6 <= absolute < 1e21:
        fixed = format(Decimal(rendered), "f")
        if "." in fixed:
            fixed = fixed.rstrip("0").rstrip(".")
        return fixed
    if "e" not in rendered:
        rendered = format(value, ".15e")
    mantissa, exponent = rendered.split("e")
    mantissa = mantissa.rstrip("0").rstrip(".")
    exponent_value = int(exponent)
    sign = "+" if exponent_value >= 0 else "-"
    return f"{mantissa}e{sign}{abs(exponent_value)}"


def _utf16_sort_key(value: str) -> bytes:
    value.encode("utf-8", errors="strict")
    return value.encode("utf-16be", errors="strict")


def _unavailable(
    job: GenerationJob,
    reason: GenerationReconstructionReason,
) -> GenerationReconstruction:
    return GenerationReconstruction(
        job=job,
        availability="UNAVAILABLE",
        reason=reason,
        recorded_input_schema_version=(
            None
            if reason is GenerationReconstructionReason.LEGACY_INPUT_NOT_SEALED
            or not isinstance(job.generation_input_schema_version, str)
            or re.fullmatch(_SEMVER_PATTERN, job.generation_input_schema_version) is None
            else job.generation_input_schema_version
        ),
        generation_input=None,
        input_digest=None,
    )
