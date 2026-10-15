"""Deterministic category and parent work-item mapping decisions.

The mapper operates only on caller-supplied projections.  It never loads or
writes persistence objects and deliberately has no decision that can create a
category.  Semantic vectors are likewise supplied by the caller, keeping this
boundary independent of embedding and generation providers.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

type SemanticVector = tuple[float, ...]


class MappingMethod(StrEnum):
    """The deterministic rule that produced a mapping decision."""

    EXACT_NAME = "EXACT_NAME"
    EXACT_ALIAS = "EXACT_ALIAS"
    SEMANTIC = "SEMANTIC"
    UNCATEGORIZED_FALLBACK = "UNCATEGORIZED_FALLBACK"
    SLACK_SOURCE_IDENTITY = "SLACK_SOURCE_IDENTITY"
    EXACT_KEY = "EXACT_KEY"
    EXACT_TITLE = "EXACT_TITLE"
    CREATE_NEW_PARENT = "CREATE_NEW_PARENT"


class MappingReviewReason(StrEnum):
    """Machine-readable reason a deterministic decision merits review."""

    CATEGORY_NO_SEMANTIC_INPUT = "CATEGORY_NO_SEMANTIC_INPUT"
    CATEGORY_NO_SEMANTIC_CANDIDATE = "CATEGORY_NO_SEMANTIC_CANDIDATE"
    CATEGORY_SEMANTIC_BELOW_THRESHOLD = "CATEGORY_SEMANTIC_BELOW_THRESHOLD"
    CATEGORY_SEMANTIC_TIE = "CATEGORY_SEMANTIC_TIE"
    PARENT_NO_SEMANTIC_INPUT = "PARENT_NO_SEMANTIC_INPUT"
    PARENT_NO_SEMANTIC_CANDIDATE = "PARENT_NO_SEMANTIC_CANDIDATE"
    PARENT_SEMANTIC_BELOW_THRESHOLD = "PARENT_SEMANTIC_BELOW_THRESHOLD"
    PARENT_SEMANTIC_TIE = "PARENT_SEMANTIC_TIE"


class MappingError(ValueError):
    """Base error for invalid or unsafe mapping input."""


class AmbiguousMappingError(MappingError):
    """More than one active entity owns an exact normalized value."""


class InvalidSemanticVectorError(MappingError):
    """A semantic vector cannot be compared safely."""


class MissingUncategorizedCategoryError(MappingError):
    """The supplied candidates have no unique active system fallback."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SlackSourceIdentity:
    """Immutable Slack root-message identity; all fields compare exactly."""

    workspace_id: str
    channel_id: str
    message_ts: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CategoryMappingRequest:
    """Trusted category mapping input derived from the Slack source."""

    requested_name: str | None
    semantic_vector: SemanticVector | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CategoryMappingCandidate:
    """Existing category projection supplied to the mapper."""

    category_id: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    semantic_vector: SemanticVector | None = None
    active: bool = True
    is_system_uncategorized: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class CategoryMappingDecision:
    """A decision that always points at an existing active category."""

    category_id: str
    method: MappingMethod
    score: float | None
    review_reason: MappingReviewReason | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ParentWorkItemMappingRequest:
    """Trusted Slack identity and proposed parent attributes."""

    source_identity: SlackSourceIdentity
    source_key: str
    title: str
    selected_category_id: str
    semantic_vector: SemanticVector | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ParentWorkItemCandidate:
    """Existing parent work-item projection supplied to the mapper."""

    work_item_id: str
    work_item_key: str
    title: str
    category_id: str
    aliases: tuple[str, ...] = ()
    slack_source_identity: SlackSourceIdentity | None = None
    semantic_vector: SemanticVector | None = None
    active: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class ReuseParentDecision:
    """Reuse an existing active parent work item."""

    work_item_id: str
    category_id: str
    method: MappingMethod
    score: float | None
    review_reason: MappingReviewReason | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateNewParentDecision:
    """Explicit persistence-free plan for a caller to create a parent."""

    source_identity: SlackSourceIdentity
    source_key: str
    title: str
    category_id: str
    score: float | None
    review_reason: MappingReviewReason
    method: MappingMethod = field(default=MappingMethod.CREATE_NEW_PARENT, init=False)


type ParentMappingDecision = ReuseParentDecision | CreateNewParentDecision


@dataclass(frozen=True, slots=True)
class _RankedCandidate[CandidateT]:
    score: float
    candidate_id: str
    candidate: CandidateT


class GenerationTargetMapper:
    """Map generated content to existing categories and parent work items.

    Exact normalized ownership is authoritative.  Semantic comparison is a
    deterministic cosine score over precomputed vectors and is attempted only
    after exact matching fails.
    """

    def __init__(
        self,
        *,
        category_semantic_threshold: float = 0.80,
        parent_semantic_threshold: float = 0.80,
    ) -> None:
        self.category_semantic_threshold = _threshold(
            category_semantic_threshold, "category_semantic_threshold"
        )
        self.parent_semantic_threshold = _threshold(
            parent_semantic_threshold, "parent_semantic_threshold"
        )

    def map_category(
        self,
        request: CategoryMappingRequest,
        candidates: Iterable[CategoryMappingCandidate],
    ) -> CategoryMappingDecision:
        """Select one existing active category using fixed precedence."""

        active = _active_categories(candidates)
        normalized_request = normalize_mapping_text(request.requested_name)
        if normalized_request:
            canonical = {
                candidate.category_id: candidate
                for candidate in active
                if normalize_mapping_text(candidate.canonical_name) == normalized_request
            }
            aliases = {
                candidate.category_id: candidate
                for candidate in active
                if normalized_request
                in {normalize_mapping_text(alias) for alias in candidate.aliases}
            }
            owners = canonical | aliases
            _reject_ambiguous(
                owners,
                kind="category exact/alias",
                normalized_value=normalized_request,
            )
            if canonical:
                candidate = next(iter(canonical.values()))
                return CategoryMappingDecision(
                    category_id=candidate.category_id,
                    method=MappingMethod.EXACT_NAME,
                    score=1.0,
                )
            if aliases:
                candidate = next(iter(aliases.values()))
                return CategoryMappingDecision(
                    category_id=candidate.category_id,
                    method=MappingMethod.EXACT_ALIAS,
                    score=1.0,
                )

        semantic_candidates = tuple(
            (
                candidate.category_id,
                candidate.semantic_vector,
                candidate,
            )
            for candidate in active
            if not candidate.is_system_uncategorized
        )
        if request.semantic_vector is None:
            fallback = _uncategorized_category(active)
            return CategoryMappingDecision(
                category_id=fallback.category_id,
                method=MappingMethod.UNCATEGORIZED_FALLBACK,
                score=None,
                review_reason=MappingReviewReason.CATEGORY_NO_SEMANTIC_INPUT,
            )

        ranked = _rank_semantic(
            request.semantic_vector,
            semantic_candidates,
            query_label="category request",
            candidate_label="category",
        )
        if not ranked:
            fallback = _uncategorized_category(active)
            return CategoryMappingDecision(
                category_id=fallback.category_id,
                method=MappingMethod.UNCATEGORIZED_FALLBACK,
                score=None,
                review_reason=MappingReviewReason.CATEGORY_NO_SEMANTIC_CANDIDATE,
            )
        best = ranked[0]
        if best.score >= self.category_semantic_threshold:
            return CategoryMappingDecision(
                category_id=best.candidate.category_id,
                method=MappingMethod.SEMANTIC,
                score=best.score,
                review_reason=(
                    MappingReviewReason.CATEGORY_SEMANTIC_TIE if _top_is_tied(ranked) else None
                ),
            )
        fallback = _uncategorized_category(active)
        return CategoryMappingDecision(
            category_id=fallback.category_id,
            method=MappingMethod.UNCATEGORIZED_FALLBACK,
            score=best.score,
            review_reason=MappingReviewReason.CATEGORY_SEMANTIC_BELOW_THRESHOLD,
        )

    def map_parent_work_item(
        self,
        request: ParentWorkItemMappingRequest,
        candidates: Iterable[ParentWorkItemCandidate],
    ) -> ParentMappingDecision:
        """Reuse an active parent or return an explicit create-new plan."""

        _validate_source_identity(request.source_identity, "parent request source_identity")
        normalized_key = _required_normalized(request.source_key, "parent request source_key")
        normalized_title = _required_normalized(request.title, "parent request title")
        _required_text(request.selected_category_id, "parent request selected_category_id")
        active = _active_parents(candidates)

        source_matches = {
            candidate.work_item_id: candidate
            for candidate in active
            if candidate.slack_source_identity == request.source_identity
        }
        key_matches = {
            candidate.work_item_id: candidate
            for candidate in active
            if normalize_mapping_text(candidate.work_item_key) == normalized_key
        }
        primary_owners = source_matches | key_matches
        _reject_ambiguous(
            primary_owners,
            kind="parent Slack source/key",
            normalized_value=normalized_key,
        )
        if primary_owners:
            candidate = next(iter(primary_owners.values()))
            method = (
                MappingMethod.SLACK_SOURCE_IDENTITY
                if candidate.work_item_id in source_matches
                else MappingMethod.EXACT_KEY
            )
            return ReuseParentDecision(
                work_item_id=candidate.work_item_id,
                category_id=candidate.category_id,
                method=method,
                score=1.0,
            )

        title_matches = {
            candidate.work_item_id: candidate
            for candidate in active
            if normalize_mapping_text(candidate.title) == normalized_title
        }
        alias_matches = {
            candidate.work_item_id: candidate
            for candidate in active
            if normalized_title in {normalize_mapping_text(alias) for alias in candidate.aliases}
        }
        title_owners = title_matches | alias_matches
        _reject_ambiguous(
            title_owners,
            kind="parent title/alias",
            normalized_value=normalized_title,
        )
        if title_matches:
            candidate = next(iter(title_matches.values()))
            return ReuseParentDecision(
                work_item_id=candidate.work_item_id,
                category_id=candidate.category_id,
                method=MappingMethod.EXACT_TITLE,
                score=1.0,
            )
        if alias_matches:
            candidate = next(iter(alias_matches.values()))
            return ReuseParentDecision(
                work_item_id=candidate.work_item_id,
                category_id=candidate.category_id,
                method=MappingMethod.EXACT_ALIAS,
                score=1.0,
            )

        semantic_candidates = tuple(
            (candidate.work_item_id, candidate.semantic_vector, candidate) for candidate in active
        )
        if request.semantic_vector is None:
            return CreateNewParentDecision(
                source_identity=request.source_identity,
                source_key=request.source_key,
                title=request.title,
                category_id=request.selected_category_id,
                score=None,
                review_reason=MappingReviewReason.PARENT_NO_SEMANTIC_INPUT,
            )

        ranked = _rank_semantic(
            request.semantic_vector,
            semantic_candidates,
            query_label="parent request",
            candidate_label="parent",
        )
        if not ranked:
            return CreateNewParentDecision(
                source_identity=request.source_identity,
                source_key=request.source_key,
                title=request.title,
                category_id=request.selected_category_id,
                score=None,
                review_reason=MappingReviewReason.PARENT_NO_SEMANTIC_CANDIDATE,
            )
        best = ranked[0]
        if best.score >= self.parent_semantic_threshold:
            return ReuseParentDecision(
                work_item_id=best.candidate.work_item_id,
                category_id=best.candidate.category_id,
                method=MappingMethod.SEMANTIC,
                score=best.score,
                review_reason=(
                    MappingReviewReason.PARENT_SEMANTIC_TIE if _top_is_tied(ranked) else None
                ),
            )
        return CreateNewParentDecision(
            source_identity=request.source_identity,
            source_key=request.source_key,
            title=request.title,
            category_id=request.selected_category_id,
            score=best.score,
            review_reason=MappingReviewReason.PARENT_SEMANTIC_BELOW_THRESHOLD,
        )


def normalize_mapping_text(value: str | None) -> str:
    """NFKC-normalize, whitespace-fold, and case-fold a mapping value."""

    if value is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _active_categories(
    candidates: Iterable[CategoryMappingCandidate],
) -> tuple[CategoryMappingCandidate, ...]:
    active: list[CategoryMappingCandidate] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate.active, bool):
            raise MappingError("category active flag must be boolean")
        if not candidate.active:
            continue
        _required_text(candidate.category_id, "category_id")
        _required_normalized(candidate.canonical_name, "category canonical_name")
        _validate_aliases(candidate.aliases, "category aliases")
        if candidate.category_id in seen_ids:
            raise AmbiguousMappingError(
                f"duplicate active category_id ownership: {candidate.category_id}"
            )
        seen_ids.add(candidate.category_id)
        active.append(candidate)
    return tuple(sorted(active, key=lambda candidate: candidate.category_id))


def _active_parents(
    candidates: Iterable[ParentWorkItemCandidate],
) -> tuple[ParentWorkItemCandidate, ...]:
    active: list[ParentWorkItemCandidate] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate.active, bool):
            raise MappingError("parent active flag must be boolean")
        if not candidate.active:
            continue
        _required_text(candidate.work_item_id, "parent work_item_id")
        _required_normalized(candidate.work_item_key, "parent work_item_key")
        _required_normalized(candidate.title, "parent title")
        _required_text(candidate.category_id, "parent category_id")
        _validate_aliases(candidate.aliases, "parent aliases")
        if candidate.slack_source_identity is not None:
            _validate_source_identity(
                candidate.slack_source_identity,
                f"parent {candidate.work_item_id} slack_source_identity",
            )
        if candidate.work_item_id in seen_ids:
            raise AmbiguousMappingError(
                f"duplicate active work_item_id ownership: {candidate.work_item_id}"
            )
        seen_ids.add(candidate.work_item_id)
        active.append(candidate)
    return tuple(sorted(active, key=lambda candidate: candidate.work_item_id))


def _uncategorized_category(
    candidates: tuple[CategoryMappingCandidate, ...],
) -> CategoryMappingCandidate:
    matches = tuple(candidate for candidate in candidates if candidate.is_system_uncategorized)
    if not matches:
        raise MissingUncategorizedCategoryError(
            "an active system 미분류 category is required for fallback"
        )
    if len(matches) > 1:
        ids = ", ".join(candidate.category_id for candidate in matches)
        raise AmbiguousMappingError(f"multiple active system 미분류 categories: {ids}")
    return matches[0]


def _validate_aliases(aliases: tuple[str, ...], label: str) -> None:
    for alias in aliases:
        _required_normalized(alias, label)


def _validate_source_identity(identity: SlackSourceIdentity, label: str) -> None:
    _required_text(identity.workspace_id, f"{label}.workspace_id")
    _required_text(identity.channel_id, f"{label}.channel_id")
    _required_text(identity.message_ts, f"{label}.message_ts")


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MappingError(f"{label} must be a non-blank string")
    return value


def _required_normalized(value: str, label: str) -> str:
    _required_text(value, label)
    normalized = normalize_mapping_text(value)
    if not normalized:
        raise MappingError(f"{label} must contain visible normalized text")
    return normalized


def _threshold(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MappingError(f"{label} must be a finite number between 0 and 1")
    threshold = float(value)
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise MappingError(f"{label} must be a finite number between 0 and 1")
    return threshold


def _reject_ambiguous(
    owners: Mapping[str, object],
    *,
    kind: str,
    normalized_value: str,
) -> None:
    if len(owners) <= 1:
        return
    owner_ids = ", ".join(sorted(owners))
    raise AmbiguousMappingError(f"ambiguous {kind} ownership for {normalized_value!r}: {owner_ids}")


def _rank_semantic[CandidateT](
    query_vector: SemanticVector,
    candidates: Iterable[tuple[str, SemanticVector | None, CandidateT]],
    *,
    query_label: str,
    candidate_label: str,
) -> tuple[_RankedCandidate[CandidateT], ...]:
    query = _validated_vector(query_vector, f"{query_label} semantic vector")
    ranked: list[_RankedCandidate[CandidateT]] = []
    for candidate_id, candidate_vector, candidate in candidates:
        if candidate_vector is None:
            continue
        vector = _validated_vector(
            candidate_vector,
            f"{candidate_label} {candidate_id} semantic vector",
        )
        if len(vector) != len(query):
            raise InvalidSemanticVectorError(
                f"{candidate_label} {candidate_id} semantic vector dimension "
                f"{len(vector)} does not match query dimension {len(query)}"
            )
        query_norm = _vector_norm(query)
        candidate_norm = _vector_norm(vector)
        score = math.fsum(
            (left / query_norm) * (right / candidate_norm)
            for left, right in zip(query, vector, strict=True)
        )
        if not math.isfinite(score):
            raise InvalidSemanticVectorError(
                f"{candidate_label} {candidate_id} semantic score is not finite"
            )
        # Round-off can put a cosine a few ulps outside its mathematical range.
        score = min(1.0, max(-1.0, score))
        ranked.append(_RankedCandidate(score, candidate_id, candidate))
    ranked.sort(key=lambda item: (-item.score, item.candidate_id))
    return tuple(ranked)


def _validated_vector(vector: SemanticVector, label: str) -> SemanticVector:
    if not isinstance(vector, tuple) or not vector:
        raise InvalidSemanticVectorError(f"{label} must be a non-empty tuple")
    values: list[float] = []
    for component in vector:
        if isinstance(component, bool) or not isinstance(component, int | float):
            raise InvalidSemanticVectorError(f"{label} components must be finite numbers")
        value = float(component)
        if not math.isfinite(value):
            raise InvalidSemanticVectorError(f"{label} components must be finite numbers")
        values.append(value)
    validated = tuple(values)
    if _vector_norm(validated) == 0:
        raise InvalidSemanticVectorError(f"{label} must not be a zero vector")
    return validated


def _vector_norm(vector: SemanticVector) -> float:
    return math.hypot(*vector)


def _top_is_tied[CandidateT](ranked: tuple[_RankedCandidate[CandidateT], ...]) -> bool:
    return len(ranked) > 1 and ranked[0].score == ranked[1].score
