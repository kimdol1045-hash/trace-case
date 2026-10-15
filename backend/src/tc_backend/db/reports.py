"""Ledger-backed cycle, platform, issue-severity, and CSV reporting."""

from __future__ import annotations

import csv
import io
import uuid
from collections import Counter
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from tc_backend.db.constants import DEFAULT_WORKSPACE_ID
from tc_backend.db.errors import ResourceNotFoundError
from tc_backend.db.models import (
    CycleItem,
    ExecutionAttempt,
    ExecutionPlatform,
    ExecutionStatus,
    Issue,
    IssueLink,
    IssueSeverity,
    TestCycle,
)


@dataclass(frozen=True, slots=True)
class ExecutionReportCounts:
    total: int
    unexecuted: int
    passed: int
    failed: int
    blocked: int

    @property
    def completed(self) -> int:
        return self.passed + self.failed + self.blocked

    @property
    def completion_rate(self) -> float:
        return self.completed / self.total if self.total else 0.0

    @property
    def success_rate(self) -> float | None:
        denominator = self.passed + self.failed
        return self.passed / denominator if denominator else None


@dataclass(frozen=True, slots=True)
class IssueSeverityCount:
    severity: IssueSeverity
    count: int


@dataclass(frozen=True, slots=True)
class ReportSourceRow:
    cycle_id: uuid.UUID
    cycle_name: str
    cycle_status: str
    platform: ExecutionPlatform
    total: int
    unexecuted: int
    passed: int
    failed: int
    blocked: int
    issue_low: int
    issue_medium: int
    issue_high: int
    issue_critical: int

    @property
    def execution(self) -> ExecutionReportCounts:
        return ExecutionReportCounts(
            total=self.total,
            unexecuted=self.unexecuted,
            passed=self.passed,
            failed=self.failed,
            blocked=self.blocked,
        )

    def csv_values(self) -> dict[str, str | int]:
        values = asdict(self)
        values["cycle_id"] = str(self.cycle_id)
        values["platform"] = self.platform.value
        return values


@dataclass(frozen=True, slots=True)
class CycleReport:
    cycle_id: uuid.UUID
    cycle_name: str
    cycle_status: str
    overall: ExecutionReportCounts
    platforms: tuple[ReportSourceRow, ...]
    issue_severities: tuple[IssueSeverityCount, ...]


CSV_COLUMNS = (
    "cycle_id",
    "cycle_name",
    "cycle_status",
    "platform",
    "total",
    "unexecuted",
    "passed",
    "failed",
    "blocked",
    "issue_low",
    "issue_medium",
    "issue_high",
    "issue_critical",
)


class ReportRepository:
    """Every aggregate and CSV is derived from the same source-row method."""

    def __init__(self, session: Session, *, workspace_id: uuid.UUID = DEFAULT_WORKSPACE_ID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def source_rows(self, *, cycle_id: uuid.UUID | None = None) -> tuple[ReportSourceRow, ...]:
        cycle_statement = select(TestCycle).where(
            TestCycle.workspace_id == self.workspace_id,
            TestCycle.deleted_at.is_(None),
        )
        if cycle_id is not None:
            cycle_statement = cycle_statement.where(TestCycle.id == cycle_id)
        cycles = tuple(
            self.session.scalars(
                cycle_statement.order_by(TestCycle.starts_at, TestCycle.created_at, TestCycle.id)
            )
        )
        if cycle_id is not None and not cycles:
            raise ResourceNotFoundError("TEST_CYCLE", cycle_id)
        cycle_ids = tuple(cycle.id for cycle in cycles)
        items = tuple(
            self.session.scalars(
                select(CycleItem)
                .where(CycleItem.cycle_id.in_(cycle_ids))
                .order_by(CycleItem.cycle_id, CycleItem.id)
            )
        ) if cycle_ids else ()
        items_by_cycle: dict[uuid.UUID, list[CycleItem]] = {}
        for item in items:
            items_by_cycle.setdefault(item.cycle_id, []).append(item)
        item_ids = tuple(item.id for item in items)
        latest: dict[tuple[uuid.UUID, str], ExecutionAttempt] = {}
        if item_ids:
            attempts = tuple(
                self.session.scalars(
                    select(ExecutionAttempt)
                    .where(ExecutionAttempt.cycle_item_id.in_(item_ids))
                    .order_by(
                        ExecutionAttempt.cycle_item_id,
                        ExecutionAttempt.platform,
                        ExecutionAttempt.attempt_no.desc(),
                    )
                )
            )
            for attempt in attempts:
                latest.setdefault((attempt.cycle_item_id, attempt.platform), attempt)
        severity_by_cycle: dict[uuid.UUID, Counter[str]] = {
            value: Counter() for value in cycle_ids
        }
        if cycle_ids:
            issue_rows = self.session.execute(
                select(CycleItem.cycle_id, Issue.id, Issue.severity)
                .join(IssueLink, IssueLink.cycle_item_id == CycleItem.id)
                .join(Issue, Issue.id == IssueLink.issue_id)
                .where(
                    CycleItem.cycle_id.in_(cycle_ids),
                    Issue.workspace_id == self.workspace_id,
                    Issue.deleted_at.is_(None),
                )
                .distinct()
            )
            for linked_cycle_id, _, severity in issue_rows:
                severity_by_cycle[linked_cycle_id][severity] += 1
        rows: list[ReportSourceRow] = []
        for cycle in cycles:
            cycle_items = items_by_cycle.get(cycle.id, [])
            severity_counts = severity_by_cycle[cycle.id]
            for platform in ExecutionPlatform:
                statuses: list[str] = []
                for item in cycle_items:
                    latest_attempt = latest.get((item.id, platform.value))
                    statuses.append(
                        latest_attempt.status
                        if latest_attempt is not None
                        else ExecutionStatus.UNEXECUTED.value
                    )
                counts = Counter(statuses)
                rows.append(
                    ReportSourceRow(
                        cycle_id=cycle.id,
                        cycle_name=cycle.name,
                        cycle_status=cycle.status,
                        platform=platform,
                        total=len(statuses),
                        unexecuted=counts[ExecutionStatus.UNEXECUTED.value],
                        passed=counts[ExecutionStatus.PASS.value],
                        failed=counts[ExecutionStatus.FAIL.value],
                        blocked=counts[ExecutionStatus.BLOCKED.value],
                        issue_low=severity_counts[IssueSeverity.LOW.value],
                        issue_medium=severity_counts[IssueSeverity.MEDIUM.value],
                        issue_high=severity_counts[IssueSeverity.HIGH.value],
                        issue_critical=severity_counts[IssueSeverity.CRITICAL.value],
                    )
                )
        return tuple(rows)

    query_source = source_rows
    csv_source_query = source_rows

    def cycle_report(self, cycle_id: uuid.UUID) -> CycleReport:
        rows = self.source_rows(cycle_id=cycle_id)
        first = rows[0]
        overall = ExecutionReportCounts(
            total=sum(row.total for row in rows),
            unexecuted=sum(row.unexecuted for row in rows),
            passed=sum(row.passed for row in rows),
            failed=sum(row.failed for row in rows),
            blocked=sum(row.blocked for row in rows),
        )
        issue_counts = {
            IssueSeverity.LOW: first.issue_low,
            IssueSeverity.MEDIUM: first.issue_medium,
            IssueSeverity.HIGH: first.issue_high,
            IssueSeverity.CRITICAL: first.issue_critical,
        }
        return CycleReport(
            cycle_id=first.cycle_id,
            cycle_name=first.cycle_name,
            cycle_status=first.cycle_status,
            overall=overall,
            platforms=rows,
            issue_severities=tuple(
                IssueSeverityCount(severity=severity, count=issue_counts[severity])
                for severity in IssueSeverity
            ),
        )

    get_cycle_report = cycle_report
    report = cycle_report

    def to_csv(self, *, cycle_id: uuid.UUID | None = None) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in self.source_rows(cycle_id=cycle_id):
            writer.writerow(row.csv_values())
        return output.getvalue()

    export_csv = to_csv
    csv = to_csv
