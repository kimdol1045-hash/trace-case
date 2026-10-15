from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tc_backend.generation.deduplication import (
    CaseDeduplicationResult,
    DeduplicationDetector,
    DeduplicationStep,
    DeduplicationValidationError,
    ExistingCaseInput,
    GeneratedCaseInput,
    ReuseReason,
    exact_content_key,
)


def _generated(
    *,
    summary: str = "활성 계정으로 로그인한다",
    actions: tuple[str, ...] = ("이메일을 입력한다", "로그인 버튼을 누른다"),
    expected: str = "대시보드가 표시된다",
    vector: tuple[float, ...] | None = None,
) -> GeneratedCaseInput:
    return GeneratedCaseInput(
        summary=summary,
        steps=tuple(DeduplicationStep(action) for action in actions),
        case_expected_result=expected,
        semantic_vector=vector,
    )


def _existing(
    case_id: str,
    case_key: str,
    *,
    summary: str = "활성 계정으로 로그인한다",
    actions: tuple[str, ...] = ("이메일을 입력한다", "로그인 버튼을 누른다"),
    expected: str = "대시보드가 표시된다",
    vector: tuple[float, ...] | None = None,
    status: str = "ACTIVE",
    deleted: bool = False,
    deleted_at: datetime | None = None,
) -> ExistingCaseInput:
    return ExistingCaseInput(
        case_id=case_id,
        case_key=case_key,
        summary=summary,
        steps=tuple(DeduplicationStep(action) for action in actions),
        case_expected_result=expected,
        status=status,
        deleted=deleted,
        deleted_at=deleted_at,
        semantic_vector=vector,
    )


def test_korean_exact_key_normalizes_unicode_case_whitespace_and_punctuation_spacing() -> None:
    generated = _generated(
        summary="  Ａ계정으로\t로그인 한다 ! ",
        actions=("이메일을  입력한다 .", "로그인 버튼을 누른다"),
    )
    existing = _existing(
        "case-1",
        "TC-1",
        summary="a계정으로 로그인 한다!",
        actions=("이메일을 입력한다.", "로그인 버튼을 누른다"),
    )

    candidates = DeduplicationDetector().find_reuse_candidates(generated, (existing,))

    assert exact_content_key(generated) == exact_content_key(existing)
    assert candidates[0].reason is ReuseReason.EXACT
    assert candidates[0].score == 1.0


def test_same_summary_with_different_steps_or_result_is_not_exact() -> None:
    generated = _generated(vector=None)
    changed_step = _existing(
        "case-step",
        "TC-2",
        actions=("이메일을 입력한다", "로그아웃 버튼을 누른다"),
    )
    changed_result = _existing(
        "case-result",
        "TC-3",
        expected="오류 메시지가 표시된다",
    )

    candidates = DeduplicationDetector().find_reuse_candidates(
        generated,
        (changed_step, changed_result),
    )

    assert candidates == ()
    assert exact_content_key(generated) != exact_content_key(changed_step)
    assert exact_content_key(generated) != exact_content_key(changed_result)


def test_step_order_is_part_of_the_exact_key() -> None:
    generated = _generated(actions=("첫 번째", "두 번째"))
    reversed_steps = _existing(
        "case-reversed",
        "TC-4",
        actions=("두 번째", "첫 번째"),
    )

    assert exact_content_key(generated) != exact_content_key(reversed_steps)


def test_semantic_threshold_is_inclusive_and_exact_wins_over_semantic() -> None:
    generated = _generated(vector=(1.0, 0.0))
    exact = _existing("exact", "TC-10", vector=(1.0, 0.0))
    boundary = _existing(
        "boundary",
        "TC-11",
        summary="로그인 정상 동작",
        vector=(0.8, 0.6),
    )
    below = _existing(
        "below",
        "TC-12",
        summary="다른 로그인 동작",
        vector=(0.799, (1.0 - 0.799**2) ** 0.5),
    )

    candidates = DeduplicationDetector(semantic_threshold=0.8).find_reuse_candidates(
        generated,
        (boundary, below, exact),
    )

    assert [(item.case_id, item.reason, item.score) for item in candidates] == [
        ("exact", ReuseReason.EXACT, 1.0),
        ("boundary", ReuseReason.SEMANTIC, 0.8),
    ]


@pytest.mark.parametrize(
    "vector",
    [(), (0.0, 0.0), (float("nan"), 1.0), (float("inf"), 1.0)],
)
def test_rejects_empty_zero_or_non_finite_vectors(vector: tuple[float, ...]) -> None:
    with pytest.raises(DeduplicationValidationError, match="semantic vectors"):
        DeduplicationDetector().detect((_generated(vector=vector),), ())


def test_rejects_unequal_vector_dimensions_and_invalid_thresholds() -> None:
    with pytest.raises(DeduplicationValidationError, match="equal dimensions"):
        DeduplicationDetector().detect(
            (_generated(vector=(1.0, 0.0)),),
            (_existing("case", "TC-20", vector=(1.0, 0.0, 0.0)),),
        )

    for threshold in (-0.01, 1.01, float("nan"), float("inf")):
        with pytest.raises(DeduplicationValidationError, match="threshold"):
            DeduplicationDetector(semantic_threshold=threshold)


def test_excludes_non_active_and_deleted_cases_before_vector_validation() -> None:
    inactive = _existing(
        "inactive",
        "TC-30",
        status="DEPRECATED",
        vector=(float("nan"),),
    )
    deleted_flag = _existing("deleted", "TC-31", deleted=True)
    deleted_time = _existing(
        "deleted-at",
        "TC-32",
        deleted_at=datetime(2026, 8, 26, tzinfo=UTC),
    )

    assert (
        DeduplicationDetector().find_reuse_candidates(
            _generated(),
            (inactive, deleted_flag, deleted_time),
        )
        == ()
    )


def test_candidates_are_score_sorted_with_stable_case_key_and_id_ties() -> None:
    generated = _generated(vector=(1.0, 0.0))
    cases = (
        _existing("id-z", "TC-2", summary="second", vector=(0.9, 0.435889894)),
        _existing("id-b", "TC-1", summary="first-b", vector=(0.9, 0.435889894)),
        _existing("id-a", "TC-1", summary="first-a", vector=(0.9, 0.435889894)),
        _existing("id-high", "TC-9", summary="high", vector=(0.95, 0.3122499)),
    )

    first = DeduplicationDetector(semantic_threshold=0.8).find_reuse_candidates(
        generated,
        cases,
    )
    second = DeduplicationDetector(semantic_threshold=0.8).find_reuse_candidates(
        generated,
        tuple(reversed(cases)),
    )

    assert [candidate.case_id for candidate in first] == [
        "id-high",
        "id-a",
        "id-b",
        "id-z",
    ]
    assert first == second


def test_duplicate_generated_inputs_keep_distinct_stable_batch_results() -> None:
    generated = _generated()
    existing = _existing("same", "TC-40")

    results = DeduplicationDetector().detect(
        (generated, generated),
        (existing,),
    )

    assert results == (
        CaseDeduplicationResult(
            generated_case_index=0,
            exact_content_key=exact_content_key(generated),
            reuse_candidates=results[0].reuse_candidates,
        ),
        CaseDeduplicationResult(
            generated_case_index=1,
            exact_content_key=exact_content_key(generated),
            reuse_candidates=results[0].reuse_candidates,
        ),
    )
    assert results[0].reuse_candidates[0].case_id == "same"


def test_missing_vectors_disable_only_semantic_detection() -> None:
    generated = _generated(vector=None)
    semantic_only = _existing(
        "semantic",
        "TC-50",
        summary="로그인 성공",
        vector=(1.0, 0.0),
    )

    assert (
        DeduplicationDetector(semantic_threshold=0.0).find_reuse_candidates(
            generated,
            (semantic_only,),
        )
        == ()
    )
