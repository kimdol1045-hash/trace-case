from __future__ import annotations

import pytest

from tc_backend.db.errors import InvalidIssueTransitionError
from tc_backend.db.issues import IssueStepInput, validate_issue_transition
from tc_backend.db.models import IssueStatus


def test_issue_status_transition_graph() -> None:
    assert validate_issue_transition("OPEN", "IN_PROGRESS") is IssueStatus.IN_PROGRESS
    assert validate_issue_transition("IN_PROGRESS", "RESOLVED") is IssueStatus.RESOLVED
    assert validate_issue_transition("RESOLVED", "CLOSED") is IssueStatus.CLOSED
    assert validate_issue_transition("CLOSED", "REOPENED") is IssueStatus.REOPENED
    assert validate_issue_transition("REOPENED", "OPEN") is IssueStatus.OPEN

    with pytest.raises(InvalidIssueTransitionError):
        validate_issue_transition("OPEN", "CLOSED")


def test_issue_step_input_preserves_ordered_reproduction_content() -> None:
    steps = (
        IssueStepInput("로그인한다", "대시보드가 열린다"),
        IssueStepInput("저장한다", "저장 완료가 표시된다"),
    )
    assert [step.action for step in steps] == ["로그인한다", "저장한다"]
