"""Pure exact and semantic duplicate detection for generated test cases."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from numbers import Real

SemanticVector = tuple[float, ...]


class ReuseReason(StrEnum):
    """Why an existing case is a candidate for reuse."""

    EXACT = "EXACT"
    SEMANTIC = "SEMANTIC"


class DeduplicationValidationError(ValueError):
    """Raised when semantic comparison inputs are not safe to compare."""


@dataclass(frozen=True, slots=True)
class DeduplicationStep:
    """Behavior-bearing content of one ordered test step."""

    action: str
    precondition: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedCaseInput:
    """A generated case and its optional, precomputed semantic vector."""

    summary: str
    steps: tuple[DeduplicationStep, ...]
    case_expected_result: str
    semantic_vector: SemanticVector | None = None


@dataclass(frozen=True, slots=True)
class ExistingCaseInput:
    """An existing ledger case projected into the provider-neutral detector."""

    case_id: str
    case_key: str
    summary: str
    steps: tuple[DeduplicationStep, ...]
    case_expected_result: str
    status: str = "ACTIVE"
    deleted: bool = False
    deleted_at: datetime | None = None
    semantic_vector: SemanticVector | None = None

    @property
    def is_reusable(self) -> bool:
        """Only current, non-deleted cases are eligible for reuse."""

        return self.status == "ACTIVE" and not self.deleted and self.deleted_at is None


@dataclass(frozen=True, slots=True)
class ReuseCandidate:
    """An existing case that should be reused or reviewed before creating a case."""

    case_id: str
    case_key: str
    summary: str
    reason: ReuseReason
    score: float


@dataclass(frozen=True, slots=True)
class CaseDeduplicationResult:
    """Candidates for one generated input, retained by its original batch position."""

    generated_case_index: int
    exact_content_key: str
    reuse_candidates: tuple[ReuseCandidate, ...]


class DeduplicationDetector:
    """Classify exact matches before threshold-inclusive semantic matches."""

    def __init__(self, *, semantic_threshold: float = 0.85) -> None:
        if (
            isinstance(semantic_threshold, bool)
            or not isinstance(semantic_threshold, Real)
            or not math.isfinite(float(semantic_threshold))
            or not 0.0 <= float(semantic_threshold) <= 1.0
        ):
            raise DeduplicationValidationError(
                "semantic threshold must be a finite number between 0 and 1"
            )
        self._semantic_threshold: float = float(semantic_threshold)

    @property
    def semantic_threshold(self) -> float:
        return self._semantic_threshold

    def detect(
        self,
        generated_cases: tuple[GeneratedCaseInput, ...],
        existing_cases: tuple[ExistingCaseInput, ...],
    ) -> tuple[CaseDeduplicationResult, ...]:
        """Return one deterministic candidate list for every generated batch item."""

        reusable_cases = tuple(case for case in existing_cases if case.is_reusable)
        _validate_semantic_vectors(generated_cases, reusable_cases)

        return tuple(
            CaseDeduplicationResult(
                generated_case_index=index,
                exact_content_key=exact_content_key(generated_case),
                reuse_candidates=self._find_candidates(generated_case, reusable_cases),
            )
            for index, generated_case in enumerate(generated_cases)
        )

    def find_reuse_candidates(
        self,
        generated_case: GeneratedCaseInput,
        existing_cases: tuple[ExistingCaseInput, ...],
    ) -> tuple[ReuseCandidate, ...]:
        """Convenience entry point for a single generated case."""

        result = self.detect((generated_case,), existing_cases)
        return result[0].reuse_candidates

    def _find_candidates(
        self,
        generated_case: GeneratedCaseInput,
        existing_cases: tuple[ExistingCaseInput, ...],
    ) -> tuple[ReuseCandidate, ...]:
        generated_key = exact_content_key(generated_case)
        candidates: list[ReuseCandidate] = []

        for existing_case in existing_cases:
            if exact_content_key(existing_case) == generated_key:
                candidates.append(_candidate(existing_case, ReuseReason.EXACT, 1.0))
                continue

            if generated_case.semantic_vector is None or existing_case.semantic_vector is None:
                continue
            score = cosine_similarity(
                generated_case.semantic_vector,
                existing_case.semantic_vector,
            )
            if score >= self._semantic_threshold:
                candidates.append(_candidate(existing_case, ReuseReason.SEMANTIC, score))

        candidates.sort(key=_candidate_sort_key)
        return tuple(candidates)


def normalize_exact_text(value: str) -> str:
    """Normalize Unicode, case, whitespace, and whitespace around punctuation."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = unicodedata.normalize("NFKC", normalized)
    output: list[str] = []
    whitespace_pending = False

    for character in normalized:
        if character.isspace():
            whitespace_pending = True
            continue

        punctuation = unicodedata.category(character).startswith("P")
        preceding_punctuation = bool(output and unicodedata.category(output[-1]).startswith("P"))
        if whitespace_pending and output and not punctuation and not preceding_punctuation:
            output.append(" ")
        output.append(character)
        whitespace_pending = False

    return "".join(output)


def exact_content_key(case: GeneratedCaseInput | ExistingCaseInput) -> str:
    """Hash canonical behavior content without provider or ledger metadata."""

    content = {
        "summary": normalize_exact_text(case.summary),
        "steps": [
            {
                "precondition": (
                    normalize_exact_text(step.precondition)
                    if step.precondition is not None
                    else None
                ),
                "action": normalize_exact_text(step.action),
            }
            for step in case.steps
        ],
        "caseExpectedResult": normalize_exact_text(case.case_expected_result),
    }
    serialized = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def cosine_similarity(left: SemanticVector, right: SemanticVector) -> float:
    """Validate two vectors and return their deterministic cosine similarity."""

    _validate_vector(left)
    _validate_vector(right)
    if len(left) != len(right):
        raise DeduplicationValidationError("semantic vectors must have equal dimensions")
    left_unit = _unit_vector(left)
    right_unit = _unit_vector(right)
    score = math.fsum(a * b for a, b in zip(left_unit, right_unit, strict=True))
    return min(1.0, max(-1.0, score))


def _validate_semantic_vectors(
    generated_cases: tuple[GeneratedCaseInput, ...],
    existing_cases: tuple[ExistingCaseInput, ...],
) -> None:
    dimensions: set[int] = set()
    vectors = (
        *(case.semantic_vector for case in generated_cases),
        *(case.semantic_vector for case in existing_cases),
    )
    for vector in vectors:
        if vector is None:
            continue
        _validate_vector(vector)
        dimensions.add(len(vector))

    if len(dimensions) > 1:
        raise DeduplicationValidationError("semantic vectors must have equal dimensions")


def _validate_vector(vector: SemanticVector) -> None:
    if not vector:
        raise DeduplicationValidationError("semantic vectors must not be empty")
    if any(
        isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value))
        for value in vector
    ):
        raise DeduplicationValidationError("semantic vectors must contain finite numbers")
    if max(abs(float(value)) for value in vector) == 0.0:
        raise DeduplicationValidationError("semantic vectors must have non-zero magnitude")


def _unit_vector(vector: SemanticVector) -> tuple[float, ...]:
    scale = max(abs(float(value)) for value in vector)
    scaled = tuple(float(value) / scale for value in vector)
    norm = math.hypot(*scaled)
    return tuple(value / norm for value in scaled)


def _candidate(
    case: ExistingCaseInput,
    reason: ReuseReason,
    score: float,
) -> ReuseCandidate:
    return ReuseCandidate(
        case_id=case.case_id,
        case_key=case.case_key,
        summary=case.summary,
        reason=reason,
        score=score,
    )


def _candidate_sort_key(
    candidate: ReuseCandidate,
) -> tuple[float, str, str, str, str, str]:
    return (
        -candidate.score,
        normalize_exact_text(candidate.case_key),
        candidate.case_key,
        candidate.case_id,
        normalize_exact_text(candidate.summary),
        candidate.summary,
    )


# Readable aliases for callers that use shorter domain names.
CaseStep = DeduplicationStep
GeneratedCase = GeneratedCaseInput
ExistingCase = ExistingCaseInput
DuplicateReason = ReuseReason
DuplicateDetector = DeduplicationDetector
