"""Immutable values returned by deterministic generation validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

GENERATION_INPUT_SCHEMA_VERSION = "1.0.0"
GENERATION_OUTPUT_SCHEMA_VERSION = "1.0.0"


class EvidenceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    REOPENED = "REOPENED"
    PROVIDED = "PROVIDED"
    AMBIGUOUS = "AMBIGUOUS"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"


class Priority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class UnsupportedClaimPolicy(StrEnum):
    FLAG = "FLAG"
    REJECT = "REJECT"


class ValidationIssueCode(StrEnum):
    UNSUPPORTED_NUMERIC_CLAIM = "UNSUPPORTED_NUMERIC_CLAIM"
    UNSUPPORTED_POLICY_CLAIM = "UNSUPPORTED_POLICY_CLAIM"


@dataclass(frozen=True, slots=True)
class ContextEvidence:
    node_id: str
    status: EvidenceStatus | str
    content: str
    active: bool | None = None


@dataclass(frozen=True, slots=True)
class GenerationContext:
    evidence: tuple[ContextEvidence, ...]
    category_ids: frozenset[str]
    output_schema_version: str = GENERATION_OUTPUT_SCHEMA_VERSION
    supported_claims: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class GeneratedStep:
    sequence: int
    action: str
    precondition: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedTestCase:
    summary: str
    steps: tuple[GeneratedStep, ...]
    case_expected_result: str
    priority: Priority
    labels: tuple[str, ...]
    evidence_node_ids: tuple[str, ...]
    generation_reason: str
    inference_only: bool
    open_questions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CategoryAlternative:
    category_id: str
    score: float


@dataclass(frozen=True, slots=True)
class CategoryCandidate:
    category_id: str
    score: float
    alternatives: tuple[CategoryAlternative, ...]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: ValidationIssueCode
    case_index: int
    field: str
    claim: str


@dataclass(frozen=True, slots=True)
class ValidatedGeneration:
    output_schema_version: str
    cases: tuple[GeneratedTestCase, ...]
    category_candidate: CategoryCandidate
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def requires_review(self) -> bool:
        return bool(self.issues)
