from __future__ import annotations

import unicodedata

import pytest

from tc_backend.generation import (
    GeneratedStep,
    GeneratedTestCase,
    GenerationPlacementPlanner,
    GenerationPlanningContext,
    GenerationPlanningError,
    GenerationPrecomputedVectors,
    Priority,
    ValidatedGeneration,
)
from tc_backend.generation.deduplication import (
    DeduplicationStep,
    ExistingCaseInput,
    ReuseReason,
)
from tc_backend.generation.mapping import (
    CategoryMappingCandidate,
    CreateNewParentDecision,
    MappingMethod,
    MappingReviewReason,
    ParentWorkItemCandidate,
    ReuseParentDecision,
    SlackSourceIdentity,
)
from tc_backend.generation.models import CategoryCandidate


def _generated_case(
    summary: str,
    *,
    action: str = "기능을 실행한다",
    expected: str = "성공 결과가 표시된다",
) -> GeneratedTestCase:
    return GeneratedTestCase(
        summary=summary,
        steps=(GeneratedStep(sequence=1, action=action),),
        case_expected_result=expected,
        priority=Priority.MEDIUM,
        labels=("generated",),
        evidence_node_ids=("node-active",),
        generation_reason="현재 근거를 검증한다",
        inference_only=False,
        open_questions=(),
    )


def _generation(
    *cases: GeneratedTestCase,
    category_id: str = "folder_members",
) -> ValidatedGeneration:
    return ValidatedGeneration(
        output_schema_version="1.0.0",
        cases=cases,
        category_candidate=CategoryCandidate(
            category_id=category_id,
            score=0.9,
            alternatives=(),
        ),
    )


def _source() -> SlackSourceIdentity:
    return SlackSourceIdentity(
        workspace_id="T_TEST",
        channel_id="C_TEST",
        message_ts="1787706000.000001",
    )


def _context(
    *,
    title: str = "비밀번호 재설정 개선",
    requested_category_name: str | None = None,
) -> GenerationPlanningContext:
    return GenerationPlanningContext(
        source_identity=_source(),
        parent_source_key="SLACK-100001",
        parent_title=title,
        requested_category_name=requested_category_name,
    )


def _existing_case(
    case_id: str,
    case_key: str,
    summary: str,
    *,
    action: str = "기능을 실행한다",
    expected: str = "성공 결과가 표시된다",
    vector: tuple[float, ...] | None = None,
) -> ExistingCaseInput:
    return ExistingCaseInput(
        case_id=case_id,
        case_key=case_key,
        summary=summary,
        steps=(DeduplicationStep(action=action),),
        case_expected_result=expected,
        semantic_vector=vector,
    )


def _category(
    category_id: str,
    name: str,
    *,
    aliases: tuple[str, ...] = (),
    vector: tuple[float, ...] | None = None,
    fallback: bool = False,
) -> CategoryMappingCandidate:
    return CategoryMappingCandidate(
        category_id=category_id,
        canonical_name=name,
        aliases=aliases,
        semantic_vector=vector,
        is_system_uncategorized=fallback,
    )


def _parent(
    work_item_id: str,
    *,
    key: str,
    title: str,
    category_id: str = "folder_members",
    aliases: tuple[str, ...] = (),
    vector: tuple[float, ...] | None = None,
) -> ParentWorkItemCandidate:
    return ParentWorkItemCandidate(
        work_item_id=work_item_id,
        work_item_key=key,
        title=title,
        category_id=category_id,
        aliases=aliases,
        semantic_vector=vector,
    )


def test_korean_exact_case_and_alias_category_parent_flow_preserves_case_order() -> None:
    decomposed_summary = unicodedata.normalize("NFD", "비밀번호 재설정 성공")
    first = _generated_case("비밀번호 재설정 성공", action="인증번호를 입력한다")
    second = _generated_case("비밀번호 재설정 실패", action="잘못된 번호를 입력한다")
    plan = GenerationPlacementPlanner().plan(
        _generation(first, second),
        context=_context(
            title=" 비밀번호   초기화 작업 ",
            requested_category_name=" 회원   계정 관리 ",
        ),
        existing_cases=(
            _existing_case(
                "case-exact",
                "TC-10",
                f"  {decomposed_summary} ",
                action="인증번호를 입력한다",
            ),
        ),
        category_candidates=(
            _category(
                "folder_members",
                "회원 관리",
                aliases=("회원 계정 관리",),
            ),
            _category("folder_uncategorized", "미분류", fallback=True),
        ),
        parent_work_item_candidates=(
            _parent(
                "work-password",
                key="OTHER-1",
                title="계정 복구",
                aliases=("비밀번호 초기화 작업",),
            ),
        ),
    )

    assert [case.generated_case.summary for case in plan.cases] == [
        "비밀번호 재설정 성공",
        "비밀번호 재설정 실패",
    ]
    assert plan.cases[0].reuse_candidates[0].reason is ReuseReason.EXACT
    assert plan.cases[1].reuse_candidates == ()
    assert plan.placement.category.method is MappingMethod.EXACT_ALIAS
    assert isinstance(plan.placement.parent, ReuseParentDecision)
    assert plan.placement.parent.method is MappingMethod.EXACT_ALIAS
    assert plan.placement.selected_category_id == "folder_members"
    assert plan.requires_review is False


def test_semantic_reuse_and_mapping_are_composed_from_precomputed_vectors() -> None:
    generated = _generated_case("잠금 상태 확인")
    plan = GenerationPlacementPlanner(
        case_semantic_threshold=0.8,
        category_semantic_threshold=0.8,
        parent_semantic_threshold=0.8,
    ).plan(
        _generation(generated, category_id="folder_missing"),
        context=_context(requested_category_name="새 계정 흐름"),
        existing_cases=(
            _existing_case(
                "case-semantic",
                "TC-20",
                "인증 오류 잠금 검증",
                vector=(0.9, 0.435889894),
            ),
        ),
        category_candidates=(
            _category("folder_members", "회원 관리", vector=(1.0, 0.0)),
            _category("folder_uncategorized", "미분류", fallback=True),
        ),
        parent_work_item_candidates=(
            _parent(
                "work-semantic",
                key="OTHER-20",
                title="회원 잠금 정책",
                vector=(1.0, 0.0),
            ),
        ),
        vectors=GenerationPrecomputedVectors(
            case_vectors=((1.0, 0.0),),
            category_vector=(1.0, 0.0),
            parent_vector=(1.0, 0.0),
        ),
    )

    assert plan.cases[0].reuse_candidates[0].reason is ReuseReason.SEMANTIC
    assert plan.placement.category.method is MappingMethod.SEMANTIC
    assert isinstance(plan.placement.parent, ReuseParentDecision)
    assert plan.placement.parent.method is MappingMethod.SEMANTIC


def test_exact_reuse_candidates_dominate_equally_scored_semantic_candidates() -> None:
    generated = _generated_case("결제 성공", action="결제를 승인한다")
    plan = GenerationPlacementPlanner(case_semantic_threshold=1.0).plan(
        _generation(generated),
        context=_context(),
        existing_cases=(
            _existing_case("semantic", "TC-01", "결제 승인", vector=(1.0, 0.0)),
            _existing_case(
                "exact",
                "TC-99",
                "결제 성공",
                action="결제를 승인한다",
                vector=(0.0, 1.0),
            ),
        ),
        category_candidates=(
            _category("folder_members", "회원 관리"),
            _category("folder_uncategorized", "미분류", fallback=True),
        ),
        parent_work_item_candidates=(),
        vectors=GenerationPrecomputedVectors(case_vectors=((1.0, 0.0),)),
    )

    assert [candidate.case_id for candidate in plan.cases[0].reuse_candidates] == [
        "exact",
        "semantic",
    ]
    assert [candidate.reason for candidate in plan.cases[0].reuse_candidates] == [
        ReuseReason.EXACT,
        ReuseReason.SEMANTIC,
    ]


def test_category_miss_uses_existing_uncategorized_and_parent_miss_is_create_intent() -> None:
    plan = GenerationPlacementPlanner().plan(
        _generation(_generated_case("알 수 없는 흐름"), category_id="folder_removed"),
        context=_context(title="신규 상위 일감"),
        existing_cases=(),
        category_candidates=(
            _category("folder_other", "기타"),
            _category("folder_uncategorized", "미분류", fallback=True),
        ),
        parent_work_item_candidates=(),
    )

    assert plan.placement.category.category_id == "folder_uncategorized"
    assert plan.placement.category.method is MappingMethod.UNCATEGORIZED_FALLBACK
    assert isinstance(plan.placement.parent, CreateNewParentDecision)
    assert plan.placement.parent.method is MappingMethod.CREATE_NEW_PARENT
    assert plan.placement.parent.category_id == "folder_uncategorized"
    assert (
        plan.placement.parent.review_reason
        is MappingReviewReason.PARENT_NO_SEMANTIC_INPUT
    )
    assert plan.requires_review is True


def test_deterministic_replay_is_independent_of_candidate_iteration_order() -> None:
    generation = _generation(_generated_case("잠금 확인"))
    context = _context()
    existing_cases = (
        _existing_case("case-z", "TC-2", "잠금 검증 Z", vector=(1.0, 0.0)),
        _existing_case("case-a", "TC-1", "잠금 검증 A", vector=(1.0, 0.0)),
    )
    categories = (
        _category("folder_members", "회원 관리"),
        _category("folder_uncategorized", "미분류", fallback=True),
    )
    parents = (
        _parent("work-z", key="OTHER-Z", title="Z", vector=(1.0, 0.0)),
        _parent("work-a", key="OTHER-A", title="A", vector=(1.0, 0.0)),
    )
    vectors = GenerationPrecomputedVectors(
        case_vectors=((1.0, 0.0),),
        parent_vector=(1.0, 0.0),
    )
    planner = GenerationPlacementPlanner()

    first = planner.plan(
        generation,
        context=context,
        existing_cases=existing_cases,
        category_candidates=categories,
        parent_work_item_candidates=parents,
        vectors=vectors,
    )
    replay = planner.plan(
        generation,
        context=context,
        existing_cases=tuple(reversed(existing_cases)),
        category_candidates=tuple(reversed(categories)),
        parent_work_item_candidates=tuple(reversed(parents)),
        vectors=vectors,
    )

    assert first == replay
    assert [item.case_id for item in first.cases[0].reuse_candidates] == [
        "case-a",
        "case-z",
    ]
    assert isinstance(first.placement.parent, ReuseParentDecision)
    assert first.placement.parent.work_item_id == "work-a"


def test_case_vectors_must_align_with_the_validated_case_order() -> None:
    with pytest.raises(GenerationPlanningError, match="one entry for every"):
        GenerationPlacementPlanner().plan(
            _generation(_generated_case("첫 번째"), _generated_case("두 번째")),
            context=_context(),
            existing_cases=(),
            category_candidates=(
                _category("folder_members", "회원 관리"),
                _category("folder_uncategorized", "미분류", fallback=True),
            ),
            parent_work_item_candidates=(),
            vectors=GenerationPrecomputedVectors(case_vectors=((1.0, 0.0),)),
        )
