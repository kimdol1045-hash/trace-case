"""Strict shape, grounding, and deterministic unsupported-claim validation."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import NoReturn

from tc_backend.generation.errors import (
    GenerationErrorCode,
    GenerationValidationError,
)
from tc_backend.generation.models import (
    CategoryAlternative,
    CategoryCandidate,
    ContextEvidence,
    GeneratedStep,
    GeneratedTestCase,
    GenerationContext,
    Priority,
    UnsupportedClaimPolicy,
    ValidatedGeneration,
    ValidationIssue,
    ValidationIssueCode,
)

_CATEGORY_ID = re.compile(r"^folder_[A-Za-z0-9_-]+$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_NUMERIC_CLAIM = re.compile(
    r"(?<![\w])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
    r"(?:\s?(?:%|퍼센트|ms|초|분|시간|일|주|개월|년|회|개|명|kb|mb|gb|"
    r"seconds?|minutes?|hours?|days?|weeks?|months?|years?|times?))?(?![\w])",
    re.IGNORECASE,
)
_POLICY_TERMS = re.compile(
    r"(?:\bmust\b|\bshall\b|\brequired\b|\bprohibited\b|\bforbidden\b|"
    r"\bonly\b|\bnever\b|\bpolicy\b|반드시|필수|금지|정책|허용되지|제한(?:한다|됨|된다)?)",
    re.IGNORECASE,
)
_CLAUSE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|[\n\r]+")
_INACTIVE_EVIDENCE_STATUSES = frozenset(
    {
        "AMBIGUOUS",
        "SUPERSEDED",
        "RETRACTED",
        "INACTIVE",
        "DEPRECATED",
        "DELETED",
        "CLOSED",
        "RESOLVED",
        "CANCELLED",
    }
)

_TOP_LEVEL_FIELDS = frozenset({"outputSchemaVersion", "cases", "categoryCandidate"})
_CASE_FIELDS = frozenset(
    {
        "summary",
        "steps",
        "caseExpectedResult",
        "priority",
        "labels",
        "evidenceNodeIds",
        "generationReason",
        "inferenceOnly",
        "openQuestions",
    }
)
_STEP_REQUIRED = frozenset({"sequence", "action"})
_STEP_FIELDS = _STEP_REQUIRED | {"precondition"}
_CATEGORY_FIELDS = frozenset({"categoryId", "score", "alternatives"})
_ALTERNATIVE_FIELDS = frozenset({"categoryId", "score"})


class GenerationResponseValidator:
    """Convert untrusted JSON to canonical values using only server context IDs."""

    def __init__(
        self,
        *,
        unsupported_claim_policy: UnsupportedClaimPolicy = UnsupportedClaimPolicy.FLAG,
    ) -> None:
        self._unsupported_claim_policy = UnsupportedClaimPolicy(unsupported_claim_policy)

    def validate(
        self,
        payload: Mapping[str, object],
        context: GenerationContext,
    ) -> ValidatedGeneration:
        raw = _object(payload, "response")
        _exact_fields(raw, _TOP_LEVEL_FIELDS, _TOP_LEVEL_FIELDS, "response")
        evidence_by_id = _context_evidence(context)
        category_ids = _context_categories(context)
        output_schema_version = _text(
            raw["outputSchemaVersion"],
            "outputSchemaVersion",
            maximum=128,
        )
        if output_schema_version != context.output_schema_version:
            _invalid("outputSchemaVersion does not match the requested context version")

        raw_cases = _array(raw["cases"], "cases", minimum=5, maximum=10)
        cases: list[GeneratedTestCase] = []
        issues: list[ValidationIssue] = []
        for index, item in enumerate(raw_cases):
            case, case_issues = self._case(
                _object(item, f"cases[{index}]"),
                index=index,
                evidence_by_id=evidence_by_id,
                supported_claims=context.supported_claims,
            )
            cases.append(case)
            issues.extend(case_issues)
        _unique_normalized(
            tuple(case.summary for case in cases),
            "cases summaries",
        )

        category = self._category(_object(raw["categoryCandidate"], "categoryCandidate"))
        all_category_ids = (category.category_id,) + tuple(
            alternative.category_id for alternative in category.alternatives
        )
        if any(category_id not in category_ids for category_id in all_category_ids):
            _invalid("categoryCandidate references a category outside the supplied context")

        if issues and self._unsupported_claim_policy is UnsupportedClaimPolicy.REJECT:
            _invalid("generation response contains unsupported numeric or policy claims")
        return ValidatedGeneration(output_schema_version, tuple(cases), category, tuple(issues))

    def _case(
        self,
        raw: Mapping[str, object],
        *,
        index: int,
        evidence_by_id: Mapping[str, ContextEvidence],
        supported_claims: frozenset[str],
    ) -> tuple[GeneratedTestCase, list[ValidationIssue]]:
        path = f"cases[{index}]"
        _exact_fields(raw, _CASE_FIELDS, _CASE_FIELDS, path)
        summary = _text(raw["summary"], f"{path}.summary", maximum=200)
        expected = _text(raw["caseExpectedResult"], f"{path}.caseExpectedResult")
        reason = _text(raw["generationReason"], f"{path}.generationReason", maximum=500)
        raw_priority = raw["priority"]
        if not isinstance(raw_priority, str):
            _invalid(f"{path}.priority is invalid")
        try:
            priority = Priority(raw_priority)
        except ValueError:
            _invalid(f"{path}.priority is invalid")
        inference_only = raw["inferenceOnly"]
        if not isinstance(inference_only, bool):
            _invalid(f"{path}.inferenceOnly must be a boolean")

        raw_labels = _array(raw["labels"], f"{path}.labels", maximum=50)
        labels = tuple(
            _text(label, f"{path}.labels[{label_index}]", maximum=100)
            for label_index, label in enumerate(raw_labels)
        )
        _unique_normalized(labels, f"{path}.labels")

        raw_questions = _array(raw["openQuestions"], f"{path}.openQuestions", maximum=50)
        questions = tuple(
            _text(question, f"{path}.openQuestions[{question_index}]")
            for question_index, question in enumerate(raw_questions)
        )
        _unique(questions, f"{path}.openQuestions")

        raw_ids = _array(
            raw["evidenceNodeIds"],
            f"{path}.evidenceNodeIds",
            minimum=1,
            maximum=100,
        )
        evidence_ids = tuple(
            _identifier(node_id, f"{path}.evidenceNodeIds[{node_index}]")
            for node_index, node_id in enumerate(raw_ids)
        )
        _unique(evidence_ids, f"{path}.evidenceNodeIds")
        cited_evidence: list[ContextEvidence] = []
        for node_id in evidence_ids:
            evidence = evidence_by_id.get(node_id)
            if evidence is None:
                _invalid(f"{path}.evidenceNodeIds references missing context evidence")
            if evidence.active is not True:
                _invalid(f"{path}.evidenceNodeIds references inactive context evidence")
            cited_evidence.append(evidence)

        raw_steps = _array(raw["steps"], f"{path}.steps", minimum=1, maximum=500)
        steps: list[GeneratedStep] = []
        issues: list[ValidationIssue] = []
        claim_fields: list[tuple[str, str]] = [
            (f"{path}.summary", summary),
            (f"{path}.caseExpectedResult", expected),
            (f"{path}.generationReason", reason),
        ]
        for step_index, item in enumerate(raw_steps):
            step_path = f"{path}.steps[{step_index}]"
            step_raw = _object(item, step_path)
            _exact_fields(step_raw, _STEP_REQUIRED, _STEP_FIELDS, step_path)
            sequence = step_raw["sequence"]
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                _invalid(f"{step_path}.sequence must be a positive integer")
            if sequence != step_index + 1:
                _invalid(f"{path}.steps must use contiguous one-based sequence values")
            action = _text(step_raw["action"], f"{step_path}.action")
            precondition = (
                _text(step_raw["precondition"], f"{step_path}.precondition")
                if "precondition" in step_raw
                else None
            )
            steps.append(GeneratedStep(sequence, action, precondition))
            claim_fields.append((f"{step_path}.action", action))
            if precondition is not None:
                claim_fields.append((f"{step_path}.precondition", precondition))

        evidence_text = "\n".join(item.content for item in cited_evidence)
        for field, text in claim_fields:
            issues.extend(
                _unsupported_claims(
                    text,
                    field=field,
                    case_index=index,
                    evidence_text=evidence_text,
                    supported_claims=supported_claims,
                )
            )
        return (
            GeneratedTestCase(
                summary=summary,
                steps=tuple(steps),
                case_expected_result=expected,
                priority=priority,
                labels=labels,
                evidence_node_ids=evidence_ids,
                generation_reason=reason,
                inference_only=inference_only,
                open_questions=questions,
            ),
            issues,
        )

    def _category(self, raw: Mapping[str, object]) -> CategoryCandidate:
        _exact_fields(raw, _CATEGORY_FIELDS, _CATEGORY_FIELDS, "categoryCandidate")
        category_id = _category_id(raw["categoryId"], "categoryCandidate.categoryId")
        score = _score(raw["score"], "categoryCandidate.score")
        alternatives: list[CategoryAlternative] = []
        for index, item in enumerate(
            _array(raw["alternatives"], "categoryCandidate.alternatives", maximum=99)
        ):
            path = f"categoryCandidate.alternatives[{index}]"
            alternative = _object(item, path)
            _exact_fields(alternative, _ALTERNATIVE_FIELDS, _ALTERNATIVE_FIELDS, path)
            alternatives.append(
                CategoryAlternative(
                    _category_id(alternative["categoryId"], f"{path}.categoryId"),
                    _score(alternative["score"], f"{path}.score"),
                )
            )
        _unique(
            (category_id, *(alternative.category_id for alternative in alternatives)),
            "categoryCandidate category IDs",
        )
        return CategoryCandidate(category_id, score, tuple(alternatives))


# Concise alias for composition sites.
GenerationValidator = GenerationResponseValidator


def _context_evidence(context: GenerationContext) -> dict[str, ContextEvidence]:
    if not isinstance(context, GenerationContext):
        _invalid("generation context is invalid")
    result: dict[str, ContextEvidence] = {}
    for evidence in context.evidence:
        if not isinstance(evidence, ContextEvidence):
            _invalid("generation context contains invalid evidence")
        node_id = _identifier(evidence.node_id, "context evidence node_id")
        if node_id in result:
            _invalid("generation context contains duplicate evidence IDs")
        status = _text(evidence.status, "context evidence status", maximum=50)
        content = _text(evidence.content, "context evidence content")
        active = evidence.active
        if active is not None and not isinstance(active, bool):
            _invalid("generation context evidence active flag must be a boolean")
        lifecycle_active = status.upper() not in _INACTIVE_EVIDENCE_STATUSES
        active = lifecycle_active and active is not False
        result[node_id] = ContextEvidence(node_id, status, content, active)
    return result


def _context_categories(context: GenerationContext) -> frozenset[str]:
    if not context.category_ids:
        _invalid("generation context must supply at least one category ID")
    return frozenset(
        _category_id(category_id, "context category ID") for category_id in context.category_ids
    )


def _unsupported_claims(
    text: str,
    *,
    field: str,
    case_index: int,
    evidence_text: str,
    supported_claims: frozenset[str],
) -> list[ValidationIssue]:
    normalized_supported = {_normalize(claim) for claim in supported_claims}
    supported_numbers = {
        _normalize_number(match.group()) for match in _NUMERIC_CLAIM.finditer(evidence_text)
    }
    supported_numbers.update(
        _normalize_number(match.group())
        for supported in supported_claims
        for match in _NUMERIC_CLAIM.finditer(supported)
    )
    normalized_evidence = _normalize(evidence_text)
    issues: list[ValidationIssue] = []
    seen: set[tuple[ValidationIssueCode, str]] = set()
    for numeric in _NUMERIC_CLAIM.finditer(text):
        claim = _normalize_number(numeric.group())
        key = (ValidationIssueCode.UNSUPPORTED_NUMERIC_CLAIM, claim)
        if key in seen:
            continue
        seen.add(key)
        if claim not in supported_numbers:
            issues.append(
                ValidationIssue(
                    ValidationIssueCode.UNSUPPORTED_NUMERIC_CLAIM,
                    case_index,
                    field,
                    numeric.group(),
                )
            )
    for clause in _CLAUSE_SPLIT.split(text):
        stripped = clause.strip()
        if not stripped or _POLICY_TERMS.search(stripped) is None:
            continue
        claim = _normalize(stripped)
        key = (ValidationIssueCode.UNSUPPORTED_POLICY_CLAIM, claim)
        if key in seen:
            continue
        seen.add(key)
        if claim not in normalized_evidence and claim not in normalized_supported:
            issues.append(
                ValidationIssue(
                    ValidationIssueCode.UNSUPPORTED_POLICY_CLAIM,
                    case_index,
                    field,
                    stripped,
                )
            )
    return issues


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _normalize_number(value: str) -> str:
    return re.sub(r"[,\s]", "", unicodedata.normalize("NFKC", value).casefold())


def _object(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        _invalid(f"{path} must be an object")
    return value


def _array(
    value: object,
    path: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> Sequence[object]:
    if not isinstance(value, list):
        _invalid(f"{path} must be an array")
    if len(value) < minimum:
        _invalid(f"{path} must contain at least {minimum} items")
    if maximum is not None and len(value) > maximum:
        _invalid(f"{path} must contain at most {maximum} items")
    return value


def _exact_fields(
    value: Mapping[str, object],
    required: frozenset[str],
    allowed: frozenset[str],
    path: str,
) -> None:
    missing = required - value.keys()
    if missing:
        _invalid(f"{path} is missing required fields: {', '.join(sorted(missing))}")
    extra = value.keys() - allowed
    if extra:
        _invalid(f"{path} contains unsupported fields: {', '.join(sorted(extra))}")


def _text(value: object, path: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"{path} must be a non-blank string")
    normalized = value.strip()
    if maximum is not None and len(normalized) > maximum:
        _invalid(f"{path} exceeds its maximum length")
    return normalized


def _unique(values: Sequence[str], path: str) -> None:
    if len(set(values)) != len(values):
        _invalid(f"{path} must contain unique values")


def _unique_normalized(values: Sequence[str], path: str) -> None:
    normalized = tuple(unicodedata.normalize("NFKC", value.strip()).casefold() for value in values)
    if len(set(normalized)) != len(normalized):
        _invalid(f"{path} must contain unique normalized values")


def _score(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _invalid(f"{path} must be a number between 0 and 1")
    score = float(value)
    if not math.isfinite(score) or not 0 <= score <= 1:
        _invalid(f"{path} must be a number between 0 and 1")
    return score


def _category_id(value: object, path: str) -> str:
    category_id = _text(value, path, maximum=128)
    if _CATEGORY_ID.fullmatch(category_id) is None:
        _invalid(f"{path} is invalid")
    return category_id


def _identifier(value: object, path: str) -> str:
    identifier = _text(value, path, maximum=128)
    if _IDENTIFIER.fullmatch(identifier) is None:
        _invalid(f"{path} is invalid")
    return identifier


def _invalid(message: str) -> NoReturn:
    raise GenerationValidationError(GenerationErrorCode.INVALID_RESPONSE, message)
