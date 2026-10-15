from __future__ import annotations

import pytest

from tc_backend.db.cycles import (
    ProgressDimension,
    aggregate_execution_statuses,
    build_progress_bucket,
    derive_attempt_result,
    validate_execution_result,
    validate_step_result,
)
from tc_backend.db.errors import (
    ActualResultRequiredError,
    ExecutionValidationError,
)
from tc_backend.db.models import ExecutionStatus, ExecutionStepResult


def test_fail_requires_actual_result_and_blocked_allows_optional_note() -> None:
    with pytest.raises(ActualResultRequiredError):
        validate_execution_result(
            status=ExecutionStatus.FAIL,
            actual_result="  ",
            note="observed during checkout",
        )
    blocked = validate_execution_result(
        status=ExecutionStatus.BLOCKED,
        actual_result=None,
        note=None,
    )
    assert blocked == (ExecutionStatus.BLOCKED, None, None)

    status, actual, note = validate_execution_result(
        status=ExecutionStatus.FAIL,
        actual_result="  server returned 500  ",
        note=None,
    )
    assert (status, actual, note) == (
        ExecutionStatus.FAIL,
        "server returned 500",
        None,
    )


def test_attempt_completion_rejects_nonterminal_status() -> None:
    with pytest.raises(ExecutionValidationError, match="only finish"):
        validate_execution_result(
            status=ExecutionStatus.UNEXECUTED,
            actual_result=None,
            note=None,
        )


def test_progress_uses_terminal_results_for_rates() -> None:
    progress = build_progress_bucket(
        dimension=ProgressDimension.OVERALL,
        key="ALL",
        statuses=(
            ExecutionStatus.UNEXECUTED,
            ExecutionStatus.PASS,
            ExecutionStatus.PASS,
            ExecutionStatus.FAIL,
            ExecutionStatus.BLOCKED,
        ),
        include_success_rate=True,
    )

    assert progress.total_count == 5
    assert progress.completed_count == 4
    assert progress.completion_rate == pytest.approx(4 / 5)
    assert progress.success_rate == pytest.approx(2 / 3)
    assert progress.executed_count == 3


def test_shared_aggregate_excludes_unexecuted_and_blocked_with_fail_priority() -> None:
    assert aggregate_execution_statuses(["UNEXECUTED", "BLOCKED"]) is ExecutionStatus.UNEXECUTED
    assert aggregate_execution_statuses(["BLOCKED", "PASS"]) is ExecutionStatus.PASS
    assert aggregate_execution_statuses(["PASS", "FAIL", "BLOCKED"]) is ExecutionStatus.FAIL


def test_planned_progress_has_no_success_rate() -> None:
    progress = build_progress_bucket(
        dimension=ProgressDimension.PLATFORM,
        key="WEB",
        statuses=(ExecutionStatus.PASS,),
        include_success_rate=False,
    )

    assert progress.pass_count == 1
    assert progress.success_rate is None


def test_step_results_allow_optional_blocked_note_and_derive_fail_precedence() -> None:
    assert validate_step_result(ExecutionStatus.BLOCKED, " ") == (
        ExecutionStatus.BLOCKED,
        None,
    )

    status, actual, note = derive_attempt_result(
        (
            ExecutionStepResult(sequence=1, status="PASS", note="ready"),
            ExecutionStepResult(sequence=2, status="BLOCKED", note="sandbox down"),
            ExecutionStepResult(sequence=3, status="FAIL", note=None),
        )
    )
    assert status is ExecutionStatus.FAIL
    assert actual == "Step 3: FAIL"
    assert note == "Step 1: ready; Step 2: sandbox down"
