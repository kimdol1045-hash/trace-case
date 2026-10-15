from __future__ import annotations

import uuid

from tc_backend.db.models import ExecutionPlatform
from tc_backend.db.reports import ReportRepository, ReportSourceRow


def test_report_csv_uses_the_same_source_rows() -> None:
    cycle_id = uuid.uuid4()
    source = (
        ReportSourceRow(
            cycle_id=cycle_id,
            cycle_name="Sprint 4",
            cycle_status="IN_PROGRESS",
            platform=ExecutionPlatform.WEB,
            total=2,
            unexecuted=0,
            passed=1,
            failed=1,
            blocked=0,
            issue_low=0,
            issue_medium=0,
            issue_high=1,
            issue_critical=0,
        ),
    )

    class SourceBackedReportRepository(ReportRepository):
        def source_rows(
            self, *, cycle_id: uuid.UUID | None = None
        ) -> tuple[ReportSourceRow, ...]:
            return source

    repository = object.__new__(SourceBackedReportRepository)
    csv_text = repository.to_csv(cycle_id=cycle_id)

    assert "cycle_id,cycle_name,cycle_status,platform" in csv_text
    assert f"{cycle_id},Sprint 4,IN_PROGRESS,WEB,2,0,1,1,0,0,0,1,0" in csv_text
