from __future__ import annotations

import uuid

from tc_api.routers.reports import _counts, _row_counts
from tc_backend.db.models import ExecutionPlatform
from tc_backend.db.reports import ReportSourceRow


def test_report_counts_use_shared_verdict_and_exclude_blocked_from_pass_rate() -> None:
    counts = _counts(unexecuted=3, passed=2, failed=1, blocked=4)

    assert counts.total == 10
    assert counts.executed == 3
    assert counts.terminal == 7
    assert counts.pass_rate == 2 / 3
    assert counts.aggregate_status == "FAIL"


def test_source_breakdown_is_not_rewritten_when_used_for_status_filtering() -> None:
    row = ReportSourceRow(
        cycle_id=uuid.uuid4(),
        cycle_name="Stable source counts",
        cycle_status="IN_PROGRESS",
        platform=ExecutionPlatform.WEB,
        total=7,
        unexecuted=1,
        passed=2,
        failed=1,
        blocked=3,
        issue_low=0,
        issue_medium=0,
        issue_high=0,
        issue_critical=0,
    )

    counts = _row_counts(row)
    assert (counts.UNEXECUTED, counts.PASS, counts.FAIL, counts.BLOCKED) == (1, 2, 1, 3)
