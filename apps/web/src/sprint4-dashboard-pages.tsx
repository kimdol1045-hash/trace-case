import type {
  CycleReportPage,
  CycleReportQuery,
  ObjectStorageStatus,
  OverviewKpi,
  OverviewKpiQuery,
  StorageUsageObservation,
} from "@tc/api-client";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { apiErrorMessage, sprint4Api } from "./sprint4-service";
import { SlackIntegrationPanel } from "./slack-integration-panel";

type Platform = "WEB" | "AOS" | "IOS";
type IssueSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
type CycleStatus = "PLANNED" | "IN_PROGRESS" | "COMPLETED";
type WorkItemOption = OverviewKpi["workItems"][number];
type ExecutionCounts = OverviewKpi["execution"];

const statusColors = { PASS: "#22a06b", FAIL: "#e2483d", BLOCKED: "#6e5dc6", UNEXECUTED: "#a5adba" } as const;
const cycleStatusLabels: Record<CycleStatus, string> = {
  PLANNED: "예정",
  IN_PROGRESS: "진행 중",
  COMPLETED: "완료",
};

function LedgerState({ loading, error, retry }: { loading: boolean; error: string; retry: () => void }) {
  if (loading) return <div className="tm-api-state" role="status">품질 지표를 불러오는 중…</div>;
  if (error) return <div className="tm-api-state is-error" role="alert"><strong>보고 데이터를 불러올 수 없습니다.</strong><p>{error}</p><button type="button" onClick={retry}>다시 시도</button></div>;
  return null;
}

function countIssues(severities: OverviewKpi["issueSeverities"]) {
  return severities.reduce((sum, severity) => sum + severity.total, 0);
}

function countOpenIssues(severities: OverviewKpi["issueSeverities"]) {
  return severities.reduce((sum, severity) => sum + severity.open, 0);
}

function executionProgress(execution: ExecutionCounts) {
  return Math.round(execution.executed / Math.max(execution.total, 1) * 100);
}

function defectLinkageRate(execution: ExecutionCounts, issueCount: number) {
  const riskCount = execution.FAIL + execution.BLOCKED;
  return riskCount ? Math.round(Math.min(issueCount / riskCount, 1) * 100) : null;
}

function workItemOptionLabel(workItem: WorkItemOption) {
  return `${workItem.categoryName} / ${workItem.key} · ${workItem.summary}`;
}

function mergeWorkItemOptions(current: WorkItemOption[], incoming: WorkItemOption[]) {
  const options = new Map(current.map((item) => [item.workItemId, item]));
  for (const item of incoming) options.set(item.workItemId, item);
  return [...options.values()].sort((left, right) => left.categoryName.localeCompare(right.categoryName, "ko") || left.key.localeCompare(right.key, "ko"));
}

type ReportFilterOption<T extends string> = { value: T; label: string };

function ReportFilterDropdown<T extends string>({ id, label, value, options, onChange }: {
  id: string;
  label: string;
  value: T;
  options: ReportFilterOption<T>[];
  onChange: (value: T) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selected = options.find((option) => option.value === value) ?? options[0];
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);
  return <div className="tm-report-filter-dropdown"><span id={`${id}-label`}>{label}</span><div className="tm-report-filter-dropdown__control" ref={rootRef}><button className="tm-report-filter-dropdown__trigger" type="button" aria-haspopup="listbox" aria-expanded={open} aria-labelledby={`${id}-label ${id}-value`} onClick={() => setOpen((current) => !current)}><span id={`${id}-value`}>{selected?.label}</span><i aria-hidden="true" /></button><div className="tm-report-filter-dropdown__menu" role="listbox" aria-labelledby={`${id}-label`} hidden={!open}>{options.map((option) => <button className={option.value === value ? "is-selected" : ""} type="button" role="option" aria-selected={option.value === value} key={option.value || "all"} onClick={() => { onChange(option.value); setOpen(false); }}><span>{option.label}</span><i aria-hidden="true">{option.value === value ? "✓" : ""}</i></button>)}</div></div></div>;
}

const platformFilterOptions: ReportFilterOption<"" | Platform>[] = [
  { value: "", label: "전체 플랫폼" }, { value: "WEB", label: "WEB" }, { value: "AOS", label: "AOS" }, { value: "IOS", label: "iOS" },
];
const severityFilterOptions: ReportFilterOption<"" | IssueSeverity>[] = [
  { value: "", label: "전체 심각도" }, { value: "LOW", label: "LOW" }, { value: "MEDIUM", label: "MEDIUM" }, { value: "HIGH", label: "HIGH" }, { value: "CRITICAL", label: "CRITICAL" },
];
const cycleStatusFilterOptions: ReportFilterOption<"" | CycleStatus>[] = [
  { value: "", label: "전체 상태" }, { value: "PLANNED", label: "예정" }, { value: "IN_PROGRESS", label: "진행 중" }, { value: "COMPLETED", label: "완료" },
];

export function KpiFilters({ platform, severity, cycleStatus, workItemId, workItemSearch, workItems, onPlatform, onSeverity, onCycleStatus, onWorkItem, onWorkItemSearch }: {
  platform: "" | Platform; severity: "" | IssueSeverity; cycleStatus: "" | CycleStatus;
  workItemId: string; workItemSearch: string; workItems: WorkItemOption[];
  onPlatform: (value: "" | Platform) => void; onSeverity: (value: "" | IssueSeverity) => void; onCycleStatus: (value: "" | CycleStatus) => void;
  onWorkItem: (value: string) => void; onWorkItemSearch: (value: string) => void;
}) {
  const [workItemModalOpen, setWorkItemModalOpen] = useState(false);
  const [pickerSearch, setPickerSearch] = useState("");
  const [expandedCategoryIds, setExpandedCategoryIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!workItemModalOpen) return;
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setWorkItemModalOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [workItemModalOpen]);
  const selectedWorkItem = workItems.find((item) => item.workItemId === workItemId);
  const normalizedSearch = pickerSearch.trim().toLocaleLowerCase("ko-KR");
  const visibleWorkItems = workItems.filter((item) => !normalizedSearch || workItemOptionLabel(item).toLocaleLowerCase("ko-KR").includes(normalizedSearch));
  const workItemGroups = [...visibleWorkItems.reduce((groups, item) => {
    const group = groups.get(item.categoryId) ?? { categoryId: item.categoryId, categoryName: item.categoryName, items: [] as WorkItemOption[] };
    group.items.push(item);
    groups.set(item.categoryId, group);
    return groups;
  }, new Map<string, { categoryId: string; categoryName: string; items: WorkItemOption[] }>()).values()]
    .sort((left, right) => left.categoryName.localeCompare(right.categoryName, "ko"));
  const clearWorkItem = () => { onWorkItem(""); onWorkItemSearch(""); };
  const openWorkItemPicker = () => {
    setPickerSearch("");
    setExpandedCategoryIds(selectedWorkItem ? new Set([selectedWorkItem.categoryId]) : new Set());
    setWorkItemModalOpen(true);
  };
  const toggleCategory = (categoryId: string) => setExpandedCategoryIds((current) => {
    const next = new Set(current);
    if (next.has(categoryId)) next.delete(categoryId);
    else next.add(categoryId);
    return next;
  });
  return <>
    <div className="tm-report-filters tm-report-filters--operations" aria-label="품질 지표 필터"><div className="tm-report-work-item-filter"><span>상위일감</span><div className="tm-report-work-item-picker"><button className="tm-report-work-item-trigger" type="button" aria-label="상위일감 검색·선택" onClick={openWorkItemPicker}><span>{selectedWorkItem ? workItemOptionLabel(selectedWorkItem) : workItemSearch || "전체 상위일감"}</span><i aria-hidden="true" /></button>{workItemId ? <button className="tm-report-work-item-clear" type="button" aria-label="상위일감 선택 해제" onClick={clearWorkItem}>×</button> : null}</div></div><ReportFilterDropdown id="report-platform" label="플랫폼" value={platform} options={platformFilterOptions} onChange={onPlatform} /><ReportFilterDropdown id="report-severity" label="이슈 심각도" value={severity} options={severityFilterOptions} onChange={onSeverity} /><ReportFilterDropdown id="report-cycle-status" label="사이클 상태" value={cycleStatus} options={cycleStatusFilterOptions} onChange={onCycleStatus} /></div>
    {workItemModalOpen ? <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) setWorkItemModalOpen(false); }}><section className="tm-cycle-modal tm-work-item-filter-modal" role="dialog" aria-modal="true" aria-labelledby="work-item-filter-title"><header><div><h2 id="work-item-filter-title">상위일감 선택</h2><p>현재 사이클에 포함된 상위일감만 카테고리별로 표시합니다.</p></div><button className="tm-modal-close" type="button" aria-label="상위일감 선택 닫기" onClick={() => setWorkItemModalOpen(false)}>×</button></header><label className="tm-search tm-cycle-modal-search"><span aria-hidden="true">⌕</span><input autoFocus aria-label="상위일감 모달 검색" value={pickerSearch} onChange={(event) => setPickerSearch(event.target.value)} placeholder="카테고리, 상위일감 키 또는 제목 검색" /></label><div className="tm-scope-selection-list tm-work-item-filter-list" role="listbox" aria-label="상위일감 목록"><button className={!workItemId ? "is-selected" : ""} type="button" role="option" aria-selected={!workItemId} onClick={() => { clearWorkItem(); setWorkItemModalOpen(false); }}><span className="tm-work-item-icon">▣</span><span><strong>전체 상위일감</strong><small>현재 사이클 범위 전체를 표시합니다.</small></span><i>{!workItemId ? "✓" : ""}</i></button>{workItemGroups.map((group) => { const expanded = Boolean(normalizedSearch) || expandedCategoryIds.has(group.categoryId); return <section className={`tm-work-item-filter-group${expanded ? " is-expanded" : ""}`} aria-label={`${group.categoryName} 카테고리`} key={group.categoryId}><button className="tm-work-item-filter-group__toggle" type="button" aria-expanded={expanded} onClick={() => toggleCategory(group.categoryId)}><span aria-hidden="true">◆</span><strong>{group.categoryName}</strong><small>{group.items.length}개</small><i aria-hidden="true" /></button>{expanded ? <div>{group.items.map((item) => <button className={`tm-work-item-filter-option${workItemId === item.workItemId ? " is-selected" : ""}`} type="button" role="option" aria-selected={workItemId === item.workItemId} key={item.workItemId} onClick={() => { onWorkItem(item.workItemId); onWorkItemSearch(workItemOptionLabel(item)); setWorkItemModalOpen(false); }}><span className="tm-work-item-icon">▣</span><span><strong>{item.key}</strong><small>{item.summary}</small></span><i>{workItemId === item.workItemId ? "✓" : ""}</i></button>)}</div> : null}</section>; })}{visibleWorkItems.length === 0 ? <p className="tm-empty-inline">검색 조건에 맞는 상위일감이 없습니다.</p> : null}</div></section></div> : null}
  </>;
}

export function OperationKpis({ execution, issueCount, cycleCount }: { execution: ExecutionCounts; issueCount: number; cycleCount: number }) {
  const riskCount = execution.FAIL + execution.BLOCKED;
  const linkageRate = defectLinkageRate(execution, issueCount);
  return <section className="tm-operation-kpis" aria-label="실행 핵심 지표"><article className="is-progress"><span>실행 진행률</span><strong>{executionProgress(execution)}%</strong><small>{execution.executed} / {execution.total}개 실행 시작</small></article><article><span>미실행</span><strong>{execution.UNEXECUTED}</strong><small>아직 시작하지 않은 실행 항목</small></article><article className={execution.FAIL ? "is-danger" : ""}><span>실패</span><strong>{execution.FAIL}</strong><small>재현·분석이 필요한 결과</small></article><article className={execution.BLOCKED ? "is-warning" : ""}><span>BLOCKED</span><strong>{execution.BLOCKED}</strong><small>진행을 막는 조건이 있는 결과</small></article><article><span>결함 연결률</span><strong>{linkageRate === null ? "—" : `${linkageRate}%`}</strong><small>{linkageRate === null ? `위험 결과 없음 · ${cycleCount}개 사이클` : `연결 이슈 ${issueCount} / 위험 결과 ${riskCount} · ${cycleCount}개 사이클`}</small></article></section>;
}

function executionChartGradient(execution: ExecutionCounts) {
  const total = Math.max(execution.total, 1);
  let offset = 0;
  const segments = (["PASS", "FAIL", "BLOCKED", "UNEXECUTED"] as const).map((status) => {
    const start = offset;
    offset += execution[status] / total * 100;
    return `${statusColors[status]} ${start}% ${offset}%`;
  });
  return execution.total ? `conic-gradient(${segments.join(", ")})` : "#e9ece9";
}

export function DashboardVisualSummary({ execution, reports }: { execution: ExecutionCounts; reports: CycleReportPage }) {
  const executionStatuses = (["PASS", "FAIL", "BLOCKED", "UNEXECUTED"] as const);
  const cycleStatuses = (["PLANNED", "IN_PROGRESS", "COMPLETED"] as const).map((status) => ({
    status,
    count: reports.items.filter((cycle) => cycle.cycleStatus === status).length,
  }));
  const maxCycleCount = Math.max(...cycleStatuses.map((item) => item.count), 1);
  return <section className="tm-dashboard-visual-summary" aria-label="실행 및 사이클 그래프"><article className="tm-dashboard-widget tm-dashboard-execution-chart"><header><div><h2>전체 실행 결과</h2><p>현재 필터 범위의 상태 구성</p></div></header><div className="tm-dashboard-execution-chart__body"><div className="tm-dashboard-donut" style={{ background: executionChartGradient(execution) }}><span><strong>{execution.total}</strong><small>실행 항목</small></span></div><div className="tm-dashboard-chart-legend">{executionStatuses.map((status) => <article key={status}><span><i style={{ background: statusColors[status] }} />{status}</span><strong>{execution[status]}</strong><small>{Math.round(execution[status] / Math.max(execution.total, 1) * 100)}%</small></article>)}</div></div></article><article className="tm-dashboard-widget tm-dashboard-cycle-chart"><header><div><h2>최근 사이클 상태</h2><p>표시 중인 {reports.items.length}개 사이클</p></div><Link to="/cycles">사이클 보기 →</Link></header><div>{cycleStatuses.map((item) => <article key={item.status}><span>{cycleStatusLabels[item.status]}</span><div><i className={`is-${item.status.toLowerCase().replace("_", "-")}`} style={{ width: `${item.count / maxCycleCount * 100}%` }} /></div><strong>{item.count}</strong></article>)}</div></article></section>;
}

function DashboardCycleTable({ reports }: { reports: CycleReportPage }) {
  return <section className="tm-dashboard-widget tm-dashboard-widget--wide tm-dashboard-cycle-table"><header><div><h2>사이클 진행 비교</h2><p>진행률과 위험 결과를 한 행에서 비교합니다.</p></div><Link to="/reports">전체 리포트 →</Link></header><div role="table" aria-label="사이클 진행 비교"><div className="tm-dashboard-cycle-table__header" role="row"><span>사이클</span><span>상태</span><span>진행률</span><span>위험</span></div>{reports.items.map((cycle) => <Link className="tm-dashboard-cycle-table__row" role="row" to={`/cycles/${cycle.cycleId}`} key={cycle.cycleId}><span><strong>{cycle.cycleName}</strong><small>{cycle.caseCount}개 케이스</small></span><CycleStatusBadge status={cycle.cycleStatus} /><span><span className="tm-mini-stack">{(["PASS", "FAIL", "BLOCKED", "UNEXECUTED"] as const).map((status) => cycle.execution[status] ? <i key={status} style={{ width: `${cycle.execution[status] / Math.max(cycle.execution.total, 1) * 100}%`, background: statusColors[status] }} /> : null)}</span><small>{executionProgress(cycle.execution)}%</small></span><strong className={cycle.execution.FAIL + cycle.execution.BLOCKED ? "is-risk" : ""}>{cycle.execution.FAIL + cycle.execution.BLOCKED}</strong></Link>)}</div></section>;
}

export function WorkItemRiskSection({ reports }: { reports: CycleReportPage }) {
  const risks = new Map<string, { workItem: WorkItemOption; cycles: Set<string>; fail: number; blocked: number; unexecuted: number; issues: number }>();
  for (const cycle of reports.items) {
    for (const workItem of cycle.workItems) {
      const current = risks.get(workItem.workItemId) ?? { workItem, cycles: new Set<string>(), fail: 0, blocked: 0, unexecuted: 0, issues: 0 };
      current.cycles.add(cycle.cycleId);
      current.fail += cycle.execution.FAIL;
      current.blocked += cycle.execution.BLOCKED;
      current.unexecuted += cycle.execution.UNEXECUTED;
      current.issues += countIssues(cycle.issueSeverities);
      risks.set(workItem.workItemId, current);
    }
  }
  const rows = [...risks.values()].sort((left, right) => (right.fail * 3 + right.blocked * 2 + right.unexecuted) - (left.fail * 3 + left.blocked * 2 + left.unexecuted));
  return <section className="tm-dashboard-widget tm-dashboard-widget--full tm-work-item-risk"><header><div><h2>상위일감별 위험</h2><p>해당 상위일감을 포함한 사이클의 FAIL, BLOCKED, 미실행 결과를 합산합니다.</p></div><span>{rows.length}개 상위일감</span></header><div>{rows.map((row) => <article className={row.fail + row.blocked ? "is-risk" : ""} key={row.workItem.workItemId}><div><strong>{row.workItem.key} · {row.workItem.summary}</strong><small>{row.cycles.size}개 사이클 · 연결 이슈 {row.issues}건</small></div><dl><div><dt>미실행</dt><dd>{row.unexecuted}</dd></div><div><dt>FAIL</dt><dd>{row.fail}</dd></div><div><dt>BLOCKED</dt><dd>{row.blocked}</dd></div></dl></article>)}{rows.length === 0 ? <p className="tm-empty-inline">필터 조건에 해당하는 상위일감 위험 데이터가 없습니다.</p> : null}</div></section>;
}

function CycleStatusBadge({ status }: { status: CycleStatus }) {
  return <span className={`tm-report-status is-${status.toLowerCase().replace("_", "-")}`}>{cycleStatusLabels[status]}</span>;
}

function CycleLink({ cycleId, children }: { cycleId: string; children: ReactNode }) {
  return <Link to={`/cycles/${cycleId}`}>{children}</Link>;
}

export function CycleReportTable({ data }: { data: CycleReportPage }) {
  return <section className="tm-dashboard-widget tm-dashboard-widget--full tm-cycle-quality-report"><header><div><h2>사이클별 품질 현황</h2><p>{new Date(data.generatedAt).toLocaleString("ko-KR")} 기준</p></div></header><div className="tm-report-scroll"><table className="tm-report-table"><thead><tr><th>상위일감</th><th>사이클</th><th>상태</th><th>케이스</th><th>진행률</th><th>미실행</th><th>PASS</th><th>FAIL</th><th>BLOCKED</th><th>열린 이슈</th></tr></thead><tbody>{data.items.map((row) => <tr key={row.cycleId}><td><div className="tm-report-work-items">{row.workItems.map((item) => <span key={item.workItemId}><b>{item.key}</b>{item.summary}</span>)}{row.workItems.length === 0 ? <span>연결 없음</span> : null}</div></td><td><CycleLink cycleId={row.cycleId}>{row.cycleName}</CycleLink></td><td><CycleStatusBadge status={row.cycleStatus} /></td><td>{row.caseCount}</td><td><strong>{executionProgress(row.execution)}%</strong><small className="tm-table-hint">{row.execution.executed}/{row.execution.total}</small></td><td>{row.execution.UNEXECUTED}</td><td>{row.execution.PASS}</td><td>{row.execution.FAIL}</td><td>{row.execution.BLOCKED}</td><td>{countOpenIssues(row.issueSeverities)}</td></tr>)}</tbody></table></div></section>;
}

export function ReportAnalysis({ execution, issueSeverities, reports }: {
  execution: ExecutionCounts;
  issueSeverities: OverviewKpi["issueSeverities"];
  reports: CycleReportPage;
}) {
  const caseCount = reports.items.reduce((sum, report) => sum + report.caseCount, 0);
  const openIssueCount = countOpenIssues(issueSeverities);
  const statuses = (["PASS", "FAIL", "BLOCKED", "UNEXECUTED"] as const).map((status) => ({
    status,
    count: execution[status],
    rate: Math.round(execution[status] / Math.max(execution.total, 1) * 100),
  }));
  return <><section className="tm-report-scope-summary" aria-label="필터 결과 요약"><article><span>사이클</span><strong>{reports.total}</strong><small>필터에 포함된 사이클</small></article><article><span>테스트케이스</span><strong>{caseCount}</strong><small>사이클 snapshot 합계</small></article><article><span>PASS</span><strong>{execution.PASS}</strong><small>정상 통과한 실행 결과</small></article><article><span>열린 이슈</span><strong>{openIssueCount}</strong><small>현재 조치가 필요한 결함</small></article></section><div className="tm-report-analysis-grid"><section className="tm-dashboard-widget tm-report-distribution"><header><div><h2>실행 결과 분포</h2><p>필터 범위의 전체 실행 항목 {execution.total}건을 상태별로 비교합니다.</p></div><strong>{executionProgress(execution)}% 실행</strong></header><div className="tm-report-distribution__bar" aria-label="실행 결과 상태 분포">{statuses.map((item) => item.count ? <i key={item.status} title={`${item.status} ${item.count}건`} style={{ width: `${item.count / Math.max(execution.total, 1) * 100}%`, background: statusColors[item.status] }} /> : null)}</div><div className="tm-report-distribution__legend">{statuses.map((item) => <article key={item.status}><span><i style={{ background: statusColors[item.status] }} />{item.status}</span><strong>{item.count}</strong><small>{item.rate}%</small></article>)}</div></section><section className="tm-dashboard-widget tm-report-severity-analysis"><header><div><h2>이슈 심각도 분석</h2><p>심각도별 전체 이슈와 현재 열린 이슈를 비교합니다.</p></div><strong>{openIssueCount}건 열림</strong></header><div>{issueSeverities.map((item) => <article key={item.severity}><span className={`is-${item.severity.toLowerCase()}`}>{item.severity}</span><div><i style={{ width: `${item.total / Math.max(countIssues(issueSeverities), 1) * 100}%` }} /></div><strong>{item.total}</strong><small>열림 {item.open}</small></article>)}{issueSeverities.length === 0 ? <p className="tm-empty-inline">필터 범위에 연결된 이슈가 없습니다.</p> : null}</div></section></div></>;
}

export function OverviewDashboardPage() {
  const [data, setData] = useState<{ overview: OverviewKpi; reports: CycleReportPage } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    Promise.all([sprint4Api.getOverviewKpis({}, controller.signal), sprint4Api.listCycleReports({ limit: 8 }, controller.signal)]).then(([overview, reports]) => { setData({ overview, reports }); setLoading(false); }).catch((requestError: unknown) => { if (!controller.signal.aborted) { setError(apiErrorMessage(requestError, "Overview 데이터를 불러오지 못했습니다.")); setLoading(false); } });
    return () => controller.abort();
  }, [revision]);
  const overview = data?.overview;
  const maxSeverity = Math.max(...(overview?.issueSeverities.map((item) => item.total) ?? [0]), 1);
  return <section className="tm-dashboard-page tm-operations-dashboard" aria-labelledby="dashboard-title"><header className="tm-page-header tm-dashboard-hero"><div><h1 id="dashboard-title">QA Dashboard</h1><p>실행 현황과 위험 결과, 연결된 결함을 상위일감 기준으로 확인합니다.</p></div><Link className="tm-subtle-button" to="/reports">상세 리포트</Link></header><LedgerState loading={loading} error={error} retry={() => setRevision((value) => value + 1)} />
    {overview ? <><OperationKpis execution={overview.execution} issueCount={countIssues(overview.issueSeverities)} cycleCount={overview.cycleCount} /><DashboardVisualSummary execution={overview.execution} reports={data.reports} />
      <div className="tm-dashboard-grid"><WorkItemRiskSection reports={data.reports} /><DashboardCycleTable reports={data.reports} /><section className="tm-dashboard-widget tm-dashboard-severity-chart"><header><div><h2>이슈 심각도</h2><p>연결된 이슈의 현재 상태</p></div><Link to="/issues">이슈 보기 →</Link></header><div>{overview.issueSeverities.map((item) => <article key={item.severity}><span>{item.severity}</span><div><i className={`is-${item.severity.toLowerCase()}`} style={{ width: `${item.total / maxSeverity * 100}%` }} /></div><strong>{item.total}</strong><small>열림 {item.open}</small></article>)}</div></section></div></> : null}
  </section>;
}

export function ReportsPage() {
  const [platform, setPlatform] = useState<"" | Platform>("");
  const [severity, setSeverity] = useState<"" | IssueSeverity>("");
  const [cycleStatus, setCycleStatus] = useState<"" | CycleStatus>("");
  const [workItemId, setWorkItemId] = useState("");
  const [workItemSearch, setWorkItemSearch] = useState("");
  const [workItemOptions, setWorkItemOptions] = useState<WorkItemOption[]>([]);
  const [data, setData] = useState<{ overview: OverviewKpi; reports: CycleReportPage } | null>(null);
  const [error, setError] = useState(""); const [loading, setLoading] = useState(true); const [revision, setRevision] = useState(0);
  const kpiQuery = useMemo<OverviewKpiQuery>(() => ({ ...(workItemId ? { workItemIds: [workItemId] } : {}), ...(platform ? { platforms: [platform] } : {}), ...(severity ? { issueSeverities: [severity] } : {}), ...(cycleStatus ? { cycleStatuses: [cycleStatus] } : {}) }), [cycleStatus, platform, severity, workItemId]);
  const query = useMemo<CycleReportQuery>(() => ({ ...kpiQuery, sort: "NAME_ASC", limit: 100 }), [kpiQuery]);
  useEffect(() => { const controller = new AbortController(); setLoading(true); setError(""); Promise.all([sprint4Api.getOverviewKpis(kpiQuery, controller.signal), sprint4Api.listCycleReports(query, controller.signal)]).then(([overview, reports]) => { setData({ overview, reports }); setWorkItemOptions((current) => mergeWorkItemOptions(current, [...overview.workItems, ...reports.items.flatMap((item) => item.workItems)])); setLoading(false); }).catch((requestError: unknown) => { if (!controller.signal.aborted) { setError(apiErrorMessage(requestError, "리포트를 불러오지 못했습니다.")); setLoading(false); } }); return () => controller.abort(); }, [kpiQuery, query, revision]);
  return <section className="tm-dashboard-page tm-operations-report" aria-labelledby="reports-title"><header className="tm-page-header"><div><h1 id="reports-title">Reports</h1><p>필터 범위의 실행 결과와 이슈 분포를 분석하고 사이클별 상세 데이터를 비교합니다.</p></div></header><KpiFilters platform={platform} severity={severity} cycleStatus={cycleStatus} workItemId={workItemId} workItemSearch={workItemSearch} workItems={workItemOptions} onPlatform={setPlatform} onSeverity={setSeverity} onCycleStatus={setCycleStatus} onWorkItem={setWorkItemId} onWorkItemSearch={setWorkItemSearch} /><LedgerState loading={loading} error={error} retry={() => setRevision((value) => value + 1)} />{data ? <><ReportAnalysis execution={data.overview.execution} issueSeverities={data.overview.issueSeverities} reports={data.reports} /><CycleReportTable data={data.reports} /></> : null}</section>;
}

export function IntegrationsPage() {
  const [storage, setStorage] = useState<ObjectStorageStatus | null>(null);
  const [observation, setObservation] = useState<StorageUsageObservation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    Promise.all([
      sprint4Api.getObjectStorageStatus(controller.signal),
      sprint4Api.getObjectStorageObservations(controller.signal),
    ])
      .then(([status, usage]) => {
        if (!controller.signal.aborted) {
          setStorage(status);
          setObservation(usage);
        }
      })
      .catch((requestError: unknown) => { if (!controller.signal.aborted) setError(apiErrorMessage(requestError, "첨부 저장소 상태를 불러오지 못했습니다.")); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision]);
  const ready = storage?.status === "READY";
  const warning = observation?.warning.level ?? "NONE";
  return <section className="tm-dashboard-page tm-integrations-page" aria-labelledby="integrations-title"><header className="tm-page-header"><div><h1 id="integrations-title">Integrations</h1><p>Slack 요청 수집과 비공개 첨부 저장소의 운영 상태를 확인합니다.</p></div><button className="tm-subtle-button" type="button" onClick={() => setRevision((value) => value + 1)}>상태 새로고침</button></header><div className="tm-integration-section-heading"><div><h2>Slack</h2><p>요청을 수집할 공개 채널과 대화 동기화 상태를 관리합니다.</p></div></div><SlackIntegrationPanel /><div className="tm-integration-section-heading"><div><h2>첨부파일 저장소</h2><p>브라우저에서 비공개 object storage로 직접 업로드합니다.</p></div></div>{loading ? <div className="tm-api-state" role="status">첨부 저장소 상태를 확인하는 중…</div> : null}{error ? <div className="tm-api-state is-error" role="alert"><strong>첨부 저장소 오류</strong><p>{error}</p></div> : null}{storage ? <div className="tm-integration-grid"><article className="tm-integration-card tm-storage-card"><header><span className="tm-integration-logo is-storage">R2</span><div><h2>Cloudflare R2</h2><p>이슈·실행 첨부파일 비공개 저장소</p></div><span className={`tm-connection-status ${ready ? "is-connected" : "is-pending"}`}>{ready ? "사용 가능" : "설정 필요"}</span></header><dl><div><dt>실행 공급자</dt><dd>{storage.provider === "MINIO" ? "MinIO (로컬)" : "Cloudflare R2"}</dd></div><div><dt>버킷</dt><dd>{storage.bucket ?? "미설정"}</dd></div><div><dt>파일 제한</dt><dd>{Math.round(storage.maxFileBytes / 1024 / 1024)} MB</dd></div><div><dt>공개 범위</dt><dd>{storage.privacy === "PRIVATE" ? "비공개" : storage.privacy}</dd></div><div><dt>악성 파일 정책</dt><dd>{storage.scanPolicy}</dd></div><div><dt>Legacy row</dt><dd>{storage.legacyRowCount}건 · {storage.legacyRowPolicy}</dd></div></dl>{!ready ? <div className="tm-storage-setup"><strong>배포 환경 설정이 필요합니다.</strong><p>사용자 계정 연결은 필요하지 않습니다. 서버의 R2 자격 증명, private 버킷과 ClamAV만 설정하세요.</p></div> : null}</article><article className={`tm-integration-card tm-quota-card is-${warning.toLowerCase()}`}><header><span className="tm-integration-logo is-db">%</span><div><h2>저장소 관측</h2><p>저장량·요청량·설정 rate 비용 추정</p></div><span className={`tm-connection-status ${warning === "NONE" ? "is-connected" : "is-pending"}`}>{warning}</span></header>{observation ? <dl><div><dt>저장량</dt><dd>{(observation.storageBytes / 1024 / 1024).toFixed(2)} MB / {(observation.storageBudgetBytes / 1024 / 1024).toFixed(0)} MB</dd></div><div><dt>Class A 요청</dt><dd>{observation.classARequests.toLocaleString()} / {observation.classARequestBudget.toLocaleString()}</dd></div><div><dt>Class B 요청</dt><dd>{observation.classBRequests.toLocaleString()} / {observation.classBRequestBudget.toLocaleString()}</dd></div><div><dt>월 비용 추정</dt><dd>${observation.estimatedMonthlyCostUsd.toFixed(6)}</dd></div><div><dt>관측 범위</dt><dd>프로세스 시작 {new Date(observation.observedSince).toLocaleString("ko-KR")}</dd></div></dl> : null}<p className="tm-card-note">70% · 85% · 95% 경고. Process-local 추정은 R2 analytics와 대조해야 합니다.</p></article><article className="tm-integration-card tm-quota-card"><header><span className="tm-integration-logo is-db">⇧</span><div><h2>직접 업로드</h2><p>API 서버의 파일 중계 없이 저장</p></div><span className="tm-connection-status is-connected">Private</span></header><dl><div><dt>업로드</dt><dd>5분 제한 PUT URL</dd></div><div><dt>무결성</dt><dd>실제 bytes SHA-256·MIME 검증</dd></div><div><dt>다운로드</dt><dd>비공개 API 프록시</dd></div><div><dt>로컬 개발</dt><dd>MinIO + ClamAV</dd></div></dl></article></div> : null}<section className="tm-dashboard-widget tm-dashboard-widget--full"><header><div><h2>데이터 보호</h2><p>저장소 비밀키와 원본 object URL은 브라우저에 노출하지 않습니다.</p></div></header><div className="tm-contract-grid"><span>Slack 채널</span><strong>허용한 공개 채널만 수집</strong><span>대화 변경</span><strong>수정·삭제 이력 보존</strong><span>업로드 권한</span><strong>파일별 5분 제한</strong><span>첨부 다운로드</span><strong>비공개 API 경유</strong></div></section></section>;
}
