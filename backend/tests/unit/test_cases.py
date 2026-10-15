from __future__ import annotations

import pytest

from tc_backend.db.cases import (
    TestCaseContent as CaseContent,
)
from tc_backend.db.cases import (
    TestStepInput as StepInput,
)
from tc_backend.db.cases import (
    normalize_tag_name,
    validate_case_content,
)
from tc_backend.db.models import ExpectedResultMode


def test_tag_normalization_is_nfkc_whitespace_folded_and_case_insensitive() -> None:
    assert normalize_tag_name("  Regression   Suite ") == "regression suite"
    assert normalize_tag_name("ＲＥＧＲＥＳＳＩＯＮ") == "regression"


def test_per_step_content_requires_at_least_one_expected_step_result() -> None:
    validate_case_content(
        CaseContent(
            summary="로그인",
            precondition="회원 계정이 있다",
            steps=(StepInput(action="로그인한다", expected_result="성공한다"),),
        )
    )

    with pytest.raises(ValueError, match="at least one step"):
        validate_case_content(
            CaseContent(summary="로그인", precondition="회원 계정이 있다", steps=())
        )
    with pytest.raises(ValueError, match="step expected_result"):
        validate_case_content(
            CaseContent(
                summary="로그인",
                precondition="회원 계정이 있다",
                steps=(StepInput(action="로그인한다"),),
            )
        )


def test_case_mode_requires_one_case_result_and_forbids_step_results() -> None:
    validate_case_content(
        CaseContent(
            summary="생성 결과",
            precondition="생성 입력이 있다",
            steps=(StepInput(action="생성 API를 호출한다"),),
            expected_result_mode=ExpectedResultMode.CASE,
            expected_result="케이스가 생성된다",
        )
    )

    with pytest.raises(ValueError, match="CASE mode steps"):
        validate_case_content(
            CaseContent(
                summary="생성 결과",
                precondition="생성 입력이 있다",
                steps=(StepInput(action="호출", expected_result="성공"),),
                expected_result_mode=ExpectedResultMode.CASE,
                expected_result="케이스가 생성된다",
            )
        )
