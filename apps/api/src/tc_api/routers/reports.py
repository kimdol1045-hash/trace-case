"""Sprint 4 dashboard and cycle/platform/issue KPI endpoints."""

from __future__ import annotations

import base64
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Query
from sqlalchemy import select

from tc_api.dependencies import SessionDependency
from tc_api.ids import decode_identifier, encode_identifier
from tc_api.models import ErrorResponse
from tc_api.sprint4_models import (
    CycleStatusValue,
    CycleKpi,
    CycleReportPageResponse,
    ExecutionStatusCounts,
    ExecutionStatusValue,
    IssueSeverityKpi,
    IssueSeverityValue,
    IssueStatusValue,
    KpiFilterEcho,
    OverviewKpiResponse,
    PlatformKpi,
    ExecutionPlatformValue,
    WorkItemKpiRef,
)
from tc_backend.db.models import CycleItem, Folder, Issue, IssueLink, TestCycle, WorkItem
from tc_backend.db.reports import ReportRepository, ReportSourceRow

router = APIRouter(tags=["reports"])
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Cycle not found"},
    422: {"model": ErrorResponse, "description": "Invalid report filter"},
}


@router.get(
    "/api/dashboard/overview",
    operation_id="getOverviewKpis",
    response_model=OverviewKpiResponse,
    responses=ERROR_RESPONSES,
)
def overview_kpis(
    session: SessionDependency,
    cycle_ids: Annotated[list[str] | None, Query(alias="cycleIds")] = None,
    work_item_ids: Annotated[list[str] | None, Query(alias="workItemIds")] = None,
    cycle_statuses: Annotated[
        list[CycleStatusValue] | None, Query(alias="cycleStatuses")
    ] = None,
    platforms: Annotated[list[ExecutionPlatformValue] | None, Query()] = None,
    execution_statuses: Annotated[
        list[ExecutionStatusValue] | None, Query(alias="executionStatuses")
    ] = None,
    issue_severities: Annotated[
        list[IssueSeverityValue] | None, Query(alias="issueSeverities")
    ] = None,
    issue_statuses: Annotated[
        list[IssueStatusValue] | None, Query(alias="issueStatuses")
    ] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
) -> OverviewKpiResponse:
    filters = _filters(
        cycle_ids,
        work_item_ids,
        cycle_statuses,
        platforms,
        execution_statuses,
        issue_severities,
        issue_statuses,
        from_,
        to,
    )
    cycle_kpis = _cycle_kpis(session, filters)
    platform_rows = [
        PlatformKpi(
            platform=cast(Any, platform),
            counts=_sum_counts([row.platforms[index].counts for row in cycle_kpis]),
        )
        for index, platform in enumerate(("WEB", "AOS", "IOS"))
    ]
    issue_rows = [
        IssueSeverityKpi(
            severity=cast(Any, severity),
            total=sum(row.issue_severities[index].total for row in cycle_kpis),
            open=sum(row.issue_severities[index].open for row in cycle_kpis),
            resolved=sum(row.issue_severities[index].resolved for row in cycle_kpis),
            closed=sum(row.issue_severities[index].closed for row in cycle_kpis),
        )
        for index, severity in enumerate(("LOW", "MEDIUM", "HIGH", "CRITICAL"))
    ]
    return OverviewKpiResponse(
        filters=filters,
        cycle_count=len(cycle_kpis),
        case_count=sum(row.case_count for row in cycle_kpis),
        platform_slot_count=sum(row.platform_slot_count for row in cycle_kpis),
        work_items=_unique_work_items(cycle_kpis),
        execution=_sum_counts([row.execution for row in cycle_kpis]),
        platforms=platform_rows,
        issue_severities=issue_rows,
        generated_at=datetime.now(UTC),
    )


@router.get(
    "/api/reports/cycles",
    operation_id="listCycleReports",
    response_model=CycleReportPageResponse,
    responses=ERROR_RESPONSES,
)
def list_cycle_reports(
    session: SessionDependency,
    cycle_ids: Annotated[list[str] | None, Query(alias="cycleIds")] = None,
    work_item_ids: Annotated[list[str] | None, Query(alias="workItemIds")] = None,
    cycle_statuses: Annotated[
        list[CycleStatusValue] | None, Query(alias="cycleStatuses")
    ] = None,
    platforms: Annotated[list[ExecutionPlatformValue] | None, Query()] = None,
    execution_statuses: Annotated[
        list[ExecutionStatusValue] | None, Query(alias="executionStatuses")
    ] = None,
    issue_severities: Annotated[
        list[IssueSeverityValue] | None, Query(alias="issueSeverities")
    ] = None,
    issue_statuses: Annotated[
        list[IssueStatusValue] | None, Query(alias="issueStatuses")
    ] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    sort: str = "UPDATED_AT_DESC",
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CycleReportPageResponse:
    filters = _filters(
        cycle_ids,
        work_item_ids,
        cycle_statuses,
        platforms,
        execution_statuses,
        issue_severities,
        issue_statuses,
        from_,
        to,
    )
    rows = _cycle_kpis(session, filters)
    if sort == "NAME_ASC":
        rows.sort(key=lambda row: (row.cycle_name.casefold(), row.cycle_id))
    elif sort == "UPDATED_AT_ASC":
        rows.reverse()
    elif sort != "UPDATED_AT_DESC":
        raise ValueError("sort must be UPDATED_AT_DESC, UPDATED_AT_ASC, or NAME_ASC")
    offset = _cursor_offset(cursor)
    return CycleReportPageResponse(
        filters=filters,
        items=rows[offset : offset + limit],
        next_cursor=_cursor(offset + limit) if offset + limit < len(rows) else None,
        total=len(rows),
        generated_at=datetime.now(UTC),
    )


def _cycle_kpis(session: Any, filters: KpiFilterEcho) -> list[CycleKpi]:
    decoded_cycle_ids = {
        decode_identifier(value, prefix="cycle", field="cycleIds")
        for value in filters.cycle_ids
    }
    decoded_work_item_ids = {
        decode_identifier(value, prefix="work_item", field="workItemIds")
        for value in filters.work_item_ids
    }
    source_rows = ReportRepository(session).source_rows()
    grouped: dict[uuid.UUID, list[ReportSourceRow]] = defaultdict(list)
    for row in source_rows:
        if decoded_cycle_ids and row.cycle_id not in decoded_cycle_ids:
            continue
        if filters.cycle_statuses and row.cycle_status not in filters.cycle_statuses:
            continue
        if filters.platforms and row.platform.value not in filters.platforms:
            continue
        grouped[row.cycle_id].append(row)
    cycle_rows = {
        row.id: row
        for row in session.scalars(
            select(TestCycle)
            .where(
                TestCycle.id.in_(tuple(grouped)),
                TestCycle.deleted_at.is_(None),
            )
            .order_by(TestCycle.updated_at.desc(), TestCycle.id.desc())
        )
    }
    work_items_by_cycle: dict[uuid.UUID, list[WorkItemKpiRef]] = defaultdict(list)
    if grouped:
        seen_work_items: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for cycle_id, work_item, category in session.execute(
            select(CycleItem.cycle_id, WorkItem, Folder)
            .join(WorkItem, WorkItem.id == CycleItem.work_item_id)
            .join(Folder, Folder.id == WorkItem.category_id)
            .where(CycleItem.cycle_id.in_(tuple(grouped)))
            .order_by(CycleItem.cycle_id, WorkItem.work_item_key, WorkItem.id)
        ):
            identity = (cycle_id, work_item.id)
            if identity in seen_work_items:
                continue
            seen_work_items.add(identity)
            work_items_by_cycle[cycle_id].append(
                WorkItemKpiRef(
                    work_item_id=encode_identifier("work_item", work_item.id),
                    category_id=encode_identifier("folder", category.id),
                    category_name=category.name,
                    key=work_item.work_item_key,
                    summary=work_item.summary,
                )
            )
    output: list[CycleKpi] = []
    issue_values = _issue_values_by_cycle(session, tuple(grouped))
    for cycle_id, rows in grouped.items():
        cycle = cycle_rows[cycle_id]
        cycle_work_items = work_items_by_cycle[cycle_id]
        if decoded_work_item_ids and not decoded_work_item_ids.intersection(
            decode_identifier(
                value.work_item_id, prefix="work_item", field="workItemId"
            )
            for value in cycle_work_items
        ):
            continue
        if filters.from_ is not None and cycle.updated_at < filters.from_:
            continue
        if filters.to is not None and cycle.updated_at > filters.to:
            continue
        by_platform = {row.platform.value: row for row in rows}
        full_rows = [by_platform.get(value) for value in ("WEB", "AOS", "IOS")]
        if filters.execution_statuses and not any(
            _row_has_status(row, status)
            for row in full_rows
            if row is not None
            for status in filters.execution_statuses
        ):
            continue
        raw_issues = issue_values.get(cycle_id, {})
        if filters.issue_severities and not any(
            severity in filters.issue_severities and sum(counts.values()) > 0
            for severity, counts in raw_issues.items()
        ):
            continue
        if filters.issue_statuses and not any(
            counts[status]
            for counts in raw_issues.values()
            for status in filters.issue_statuses
        ):
            continue
        platform_kpis = [
            PlatformKpi(
                platform=cast(Any, platform),
                counts=_row_counts(by_platform.get(platform)),
            )
            for platform in ("WEB", "AOS", "IOS")
        ]
        execution = _sum_counts([row.counts for row in platform_kpis])
        issue_kpis = _issue_kpis(raw_issues)
        case_count = max((row.total for row in full_rows if row is not None), default=0)
        output.append(
            CycleKpi(
                cycle_id=encode_identifier("cycle", cycle_id),
                cycle_name=cycle.name,
                cycle_status=cycle.status,
                case_count=case_count,
                platform_slot_count=case_count * 3,
                work_items=cycle_work_items,
                execution=execution,
                platforms=platform_kpis,
                issue_severities=issue_kpis,
            )
        )
    output.sort(key=lambda row: cycle_rows[decode_identifier(row.cycle_id, prefix="cycle", field="cycleId")].updated_at, reverse=True)
    return output


def _issue_values_by_cycle(
    session: Any, cycle_ids: tuple[uuid.UUID, ...]
) -> dict[uuid.UUID, dict[str, Counter[str]]]:
    if not cycle_ids:
        return {}
    statement = (
        select(CycleItem.cycle_id, Issue.id, Issue.severity, Issue.status)
        .join(IssueLink, IssueLink.cycle_item_id == CycleItem.id)
        .join(Issue, Issue.id == IssueLink.issue_id)
        .where(
            CycleItem.cycle_id.in_(cycle_ids),
            Issue.deleted_at.is_(None),
        )
        .distinct()
    )
    values: dict[uuid.UUID, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    for cycle_id, _, severity, issue_status in session.execute(statement):
        values[cycle_id][severity][issue_status] += 1
    return values


def _issue_kpis(values: dict[str, Counter[str]]) -> list[IssueSeverityKpi]:
    output: list[IssueSeverityKpi] = []
    for severity in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        counts = values.get(severity, Counter())
        opened = counts["OPEN"] + counts["IN_PROGRESS"] + counts["REOPENED"]
        output.append(
            IssueSeverityKpi(
                severity=cast(Any, severity),
                total=sum(counts.values()),
                open=opened,
                resolved=counts["RESOLVED"],
                closed=counts["CLOSED"],
            )
        )
    return output


def _row_has_status(row: ReportSourceRow, status: str) -> bool:
    return {
        "UNEXECUTED": row.unexecuted,
        "PASS": row.passed,
        "FAIL": row.failed,
        "BLOCKED": row.blocked,
    }[status] > 0


def _row_counts(row: ReportSourceRow | None) -> ExecutionStatusCounts:
    if row is None:
        return _counts(0, 0, 0, 0)
    return _counts(
        row.unexecuted,
        row.passed,
        row.failed,
        row.blocked,
    )


def _counts(
    unexecuted: int, passed: int, failed: int, blocked: int
) -> ExecutionStatusCounts:
    total = unexecuted + passed + failed + blocked
    executed = passed + failed
    terminal = passed + failed + blocked
    aggregate_status = "FAIL" if failed else "PASS" if passed else "UNEXECUTED"
    return ExecutionStatusCounts(
        UNEXECUTED=unexecuted,
        PASS=passed,
        FAIL=failed,
        BLOCKED=blocked,
        total=total,
        executed=executed,
        terminal=terminal,
        pass_rate=passed / executed if executed else 0,
        aggregate_status=cast(Any, aggregate_status),
    )


def _sum_counts(rows: list[ExecutionStatusCounts]) -> ExecutionStatusCounts:
    return _counts(
        sum(row.UNEXECUTED for row in rows),
        sum(row.PASS for row in rows),
        sum(row.FAIL for row in rows),
        sum(row.BLOCKED for row in rows),
    )


def _filters(
    cycle_ids: list[str] | None,
    work_item_ids: list[str] | None,
    cycle_statuses: list[CycleStatusValue] | None,
    platforms: list[ExecutionPlatformValue] | None,
    execution_statuses: list[ExecutionStatusValue] | None,
    issue_severities: list[IssueSeverityValue] | None,
    issue_statuses: list[IssueStatusValue] | None,
    from_: datetime | None,
    to: datetime | None,
) -> KpiFilterEcho:
    values = [
        ("cycleIds", cycle_ids),
        ("workItemIds", work_item_ids),
        ("cycleStatuses", cycle_statuses),
        ("platforms", platforms),
        ("executionStatuses", execution_statuses),
        ("issueSeverities", issue_severities),
        ("issueStatuses", issue_statuses),
    ]
    for field, entries in values:
        if entries is not None and (not entries or len(entries) != len(set(entries))):
            raise ValueError(f"{field} must contain unique values")
    if from_ is not None and to is not None and from_ > to:
        raise ValueError("from must not be later than to")
    return KpiFilterEcho(
        cycle_ids=sorted(cycle_ids or []),
        work_item_ids=sorted(work_item_ids or []),
        cycle_statuses=sorted(cycle_statuses or []),
        platforms=sorted(platforms or []),
        execution_statuses=sorted(execution_statuses or []),
        issue_severities=sorted(issue_severities or []),
        issue_statuses=sorted(issue_statuses or []),
        **{"from": from_, "to": to},
    )


def _unique_work_items(cycles: list[CycleKpi]) -> list[WorkItemKpiRef]:
    values: dict[str, WorkItemKpiRef] = {}
    for cycle in cycles:
        for work_item in cycle.work_items:
            values.setdefault(work_item.work_item_id, work_item)
    return sorted(values.values(), key=lambda value: (value.key, value.work_item_id))


def _cursor(value: int) -> str:
    return base64.urlsafe_b64encode(str(value).encode()).decode().rstrip("=")


def _cursor_offset(value: str | None) -> int:
    if value is None:
        return 0
    try:
        offset = int(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("report cursor is invalid") from error
    if offset < 0:
        raise ValueError("report cursor is invalid")
    return offset
