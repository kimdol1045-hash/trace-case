"""Pure placement planning for a validated generation response.

The planner composes the deterministic duplicate detector and target mapper.  It
accepts only caller-supplied projections and optional precomputed vectors; it
does not know how to call a provider or how to read or write the QA ledger.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tc_backend.generation.deduplication import (
    DeduplicationDetector,
    DeduplicationStep,
    ExistingCaseInput,
    GeneratedCaseInput,
    ReuseCandidate,
    ReuseReason,
    SemanticVector,
)
from tc_backend.generation.mapping import (
    CategoryMappingCandidate,
    CategoryMappingDecision,
    CategoryMappingRequest,
    GenerationTargetMapper,
    ParentMappingDecision,
    ParentWorkItemCandidate,
    ParentWorkItemMappingRequest,
    SlackSourceIdentity,
)
from tc_backend.generation.models import GeneratedTestCase, ValidatedGeneration, ValidationIssue


class GenerationPlanningError(ValueError):
    """Raised when a validated generation cannot be composed safely."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationPlanningContext:
    """Trusted source attributes needed to place one generated batch.

    ``requested_category_name`` is useful when the trusted Slack projection has
    a human-readable category hint or alias.  When omitted, the planner resolves
    the validated provider-selected category ID to the supplied candidate's
    canonical name.  A missing or inactive selected candidate consequently goes
    through the mapper's existing ``미분류`` fallback.
    """

    source_identity: SlackSourceIdentity
    parent_source_key: str
    parent_title: str
    requested_category_name: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationPrecomputedVectors:
    """Optional provider-independent vectors supplied by the planning caller."""

    case_vectors: tuple[SemanticVector | None, ...] | None = None
    category_vector: SemanticVector | None = None
    parent_vector: SemanticVector | None = None


@dataclass(frozen=True, slots=True)
class GeneratedCasePlacement:
    """One generated case and its reuse candidates in original batch order."""

    generated_case_index: int
    generated_case: GeneratedTestCase
    exact_content_key: str
    reuse_candidates: tuple[ReuseCandidate, ...]


@dataclass(frozen=True, slots=True)
class GenerationPlacementDecision:
    """The single category/parent decision for a generated batch."""

    category: CategoryMappingDecision
    parent: ParentMappingDecision

    @property
    def selected_category_id(self) -> str:
        """Return the authoritative category for the selected parent intent.

        Reusing a parent preserves that parent's existing category.  Creating a
        parent uses the category selected by the category mapper.
        """

        return self.parent.category_id


@dataclass(frozen=True, slots=True)
class GenerationPlacementPlan:
    """Persistence-free output ready for later review or transactional apply."""

    output_schema_version: str
    cases: tuple[GeneratedCasePlacement, ...]
    placement: GenerationPlacementDecision
    generation_issues: tuple[ValidationIssue, ...]

    @property
    def requires_review(self) -> bool:
        """Surface validator or deterministic mapping review signals."""

        return bool(self.generation_issues) or any(
            decision.review_reason is not None
            for decision in (self.placement.category, self.placement.parent)
        )


class GenerationPlacementPlanner:
    """Compose reuse candidates and one deterministic placement decision."""

    def __init__(
        self,
        *,
        case_semantic_threshold: float = 0.85,
        category_semantic_threshold: float = 0.80,
        parent_semantic_threshold: float = 0.80,
    ) -> None:
        self._duplicate_detector = DeduplicationDetector(
            semantic_threshold=case_semantic_threshold
        )
        self._target_mapper = GenerationTargetMapper(
            category_semantic_threshold=category_semantic_threshold,
            parent_semantic_threshold=parent_semantic_threshold,
        )

    def plan(
        self,
        generation: ValidatedGeneration,
        *,
        context: GenerationPlanningContext,
        existing_cases: Iterable[ExistingCaseInput],
        category_candidates: Iterable[CategoryMappingCandidate],
        parent_work_item_candidates: Iterable[ParentWorkItemCandidate],
        vectors: GenerationPrecomputedVectors | None = None,
    ) -> GenerationPlacementPlan:
        """Return a deterministic plan without persistence or provider calls."""

        if not isinstance(generation, ValidatedGeneration):
            raise GenerationPlanningError("generation must be a ValidatedGeneration")
        if not isinstance(context, GenerationPlanningContext):
            raise GenerationPlanningError("context must be a GenerationPlanningContext")
        if vectors is not None and not isinstance(vectors, GenerationPrecomputedVectors):
            raise GenerationPlanningError("vectors must be GenerationPrecomputedVectors")

        existing_case_values = tuple(existing_cases)
        category_values = tuple(category_candidates)
        parent_values = tuple(parent_work_item_candidates)
        supplied_vectors = vectors or GenerationPrecomputedVectors()
        case_vectors = _case_vectors(supplied_vectors.case_vectors, len(generation.cases))

        generated_inputs = tuple(
            _deduplication_input(generated_case, semantic_vector)
            for generated_case, semantic_vector in zip(
                generation.cases,
                case_vectors,
                strict=True,
            )
        )
        duplicate_results = self._duplicate_detector.detect(
            generated_inputs,
            existing_case_values,
        )
        case_plans = tuple(
            GeneratedCasePlacement(
                generated_case_index=result.generated_case_index,
                generated_case=generation.cases[result.generated_case_index],
                exact_content_key=result.exact_content_key,
                reuse_candidates=_exact_candidates_first(result.reuse_candidates),
            )
            for result in duplicate_results
        )

        requested_category_name = context.requested_category_name
        if requested_category_name is None:
            requested_category_name = _selected_category_name(generation, category_values)
        category_decision = self._target_mapper.map_category(
            CategoryMappingRequest(
                requested_name=requested_category_name,
                semantic_vector=supplied_vectors.category_vector,
            ),
            category_values,
        )
        parent_decision = self._target_mapper.map_parent_work_item(
            ParentWorkItemMappingRequest(
                source_identity=context.source_identity,
                source_key=context.parent_source_key,
                title=context.parent_title,
                selected_category_id=category_decision.category_id,
                semantic_vector=supplied_vectors.parent_vector,
            ),
            parent_values,
        )

        return GenerationPlacementPlan(
            output_schema_version=generation.output_schema_version,
            cases=case_plans,
            placement=GenerationPlacementDecision(category_decision, parent_decision),
            generation_issues=generation.issues,
        )


def _case_vectors(
    vectors: tuple[SemanticVector | None, ...] | None,
    case_count: int,
) -> tuple[SemanticVector | None, ...]:
    if vectors is None:
        return (None,) * case_count
    if len(vectors) != case_count:
        raise GenerationPlanningError(
            "case_vectors must contain one entry for every generated case"
        )
    return vectors


def _deduplication_input(
    generated_case: GeneratedTestCase,
    semantic_vector: SemanticVector | None,
) -> GeneratedCaseInput:
    return GeneratedCaseInput(
        summary=generated_case.summary,
        steps=tuple(
            DeduplicationStep(action=step.action, precondition=step.precondition)
            for step in generated_case.steps
        ),
        case_expected_result=generated_case.case_expected_result,
        semantic_vector=semantic_vector,
    )


def _exact_candidates_first(
    candidates: tuple[ReuseCandidate, ...],
) -> tuple[ReuseCandidate, ...]:
    """Give exact matches absolute precedence without disturbing engine ties."""

    return tuple(candidate for candidate in candidates if candidate.reason is ReuseReason.EXACT) + tuple(
        candidate for candidate in candidates if candidate.reason is ReuseReason.SEMANTIC
    )


def _selected_category_name(
    generation: ValidatedGeneration,
    candidates: tuple[CategoryMappingCandidate, ...],
) -> str:
    selected_category_id = generation.category_candidate.category_id
    for candidate in candidates:
        if candidate.active is True and candidate.category_id == selected_category_id:
            return candidate.canonical_name
    return selected_category_id
