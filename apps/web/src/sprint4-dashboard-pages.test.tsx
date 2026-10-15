import type { CycleReportPage, OverviewKpi } from "@tc/api-client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { CycleReportTable, DashboardVisualSummary, KpiFilters, OperationKpis, ReportAnalysis, WorkItemRiskSection } from "./sprint4-dashboard-pages";
import cycleWorkspaceSource from "./cycle-workspace-page.tsx?raw";

const { readFileSync } = await vi.importActual<{ readFileSync: (path: URL, encoding: "utf8") => string }>("node:fs");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function contrastRatio(foreground: string, background: string) {
  const luminance = (hex: string) => {
    const [red = 0, green = 0, blue = 0] = (hex.match(/[\da-f]{2}/gi) ?? []).map((value) => Number.parseInt(value, 16) / 255).map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return red * 0.2126 + green * 0.7152 + blue * 0.0722;
  };
  const light = Math.max(luminance(foreground), luminance(background));
  const dark = Math.min(luminance(foreground), luminance(background));
  return (light + 0.05) / (dark + 0.05);
}

const execution: OverviewKpi["execution"] = {
  PASS: 3,
  FAIL: 2,
  BLOCKED: 1,
  UNEXECUTED: 4,
  aggregateStatus: "FAIL",
  executed: 7,
  passRate: 0.5,
  terminal: 6,
  total: 11,
};

const filters: CycleReportPage["filters"] = {
  cycleIds: [],
  cycleStatuses: [],
  executionStatuses: [],
  from: null,
  issueSeverities: [],
  issueStatuses: [],
  platforms: [],
  to: null,
  workItemIds: [],
};

const report: CycleReportPage = {
  filters,
  generatedAt: "2026-08-21T01:00:00Z",
  items: [
    {
      caseCount: 4,
      cycleId: "cycle_internal_token",
      cycleName: "결제 회귀 테스트",
      cycleStatus: "IN_PROGRESS",
      execution,
      issueSeverities: [
        { severity: "HIGH", total: 2, open: 1, resolved: 1, closed: 0 },
      ],
      platformSlotCount: 11,
      platforms: [{ platform: "WEB", counts: execution }],
      workItems: [{ categoryId: "folder_payments", categoryName: "결제", key: "WORK-482", summary: "카드 결제 승인 흐름 개선", workItemId: "work_internal_token" }],
    },
  ],
  nextCursor: null,
  total: 1,
};

describe("dashboard and report information architecture", () => {
  it("offers one consistent work item, platform, severity and cycle status filter row", () => {
    const markup = renderToStaticMarkup(
      <KpiFilters
        platform=""
        severity=""
        cycleStatus=""
        workItemId=""
        workItemSearch=""
        workItems={report.items[0]!.workItems}
        onPlatform={() => undefined}
        onSeverity={() => undefined}
        onCycleStatus={() => undefined}
        onWorkItem={() => undefined}
        onWorkItemSearch={() => undefined}
      />,
    );

    expect(markup).toContain("상위일감 검색·선택");
    expect(markup).toContain("전체 상위일감");
    expect(markup).toContain("tm-report-work-item-trigger");
    expect(markup).not.toContain("tm-report-work-item-clear");
    expect(markup).not.toContain("datalist");
    expect(markup).toContain("전체 플랫폼");
    expect(markup).toContain("전체 심각도");
    expect(markup).toContain("전체 상태");
    expect(markup).toContain("tm-report-filter-dropdown__trigger");
    expect(markup).not.toContain("<select");
    expect(markup).toContain("예정");
    expect(markup).toContain("진행 중");
    expect(markup).toContain("완료");
  });

  it("shows actionable execution and defect-linkage KPIs", () => {
    const markup = renderToStaticMarkup(<OperationKpis execution={execution} issueCount={2} cycleCount={1} />);

    expect(markup).toContain("실행 진행률");
    expect(markup).toContain("64%");
    expect(markup).toContain("미실행");
    expect(markup).toContain("실패");
    expect(markup).toContain("BLOCKED");
    expect(markup).toContain("결함 연결률");
    expect(markup).toContain("67%");
  });

  it("does not report a misleading linkage percentage when there are no risky results", () => {
    const safeExecution = { ...execution, FAIL: 0, BLOCKED: 0 };
    const markup = renderToStaticMarkup(<OperationKpis execution={safeExecution} issueCount={0} cycleCount={1} />);

    expect(markup).toContain("위험 결과 없음");
    expect(markup).toContain("<strong>—</strong>");
  });

  it("uses charts on the dashboard and analytical summaries on reports", () => {
    const issueSeverities: OverviewKpi["issueSeverities"] = [
      { severity: "HIGH", total: 2, open: 1, resolved: 1, closed: 0 },
    ];
    const markup = renderToStaticMarkup(<MemoryRouter><DashboardVisualSummary execution={execution} reports={report} /><ReportAnalysis execution={execution} issueSeverities={issueSeverities} reports={report} /></MemoryRouter>);

    expect(markup).toContain("전체 실행 결과");
    expect(markup).toContain("tm-dashboard-donut");
    expect(markup).toContain("최근 사이클 상태");
    expect(markup).toContain("필터 결과 요약");
    expect(markup).toContain("<span>PASS</span><strong>3</strong><small>정상 통과한 실행 결과</small>");
    expect(markup).not.toContain("케이스 × 플랫폼");
    expect(markup).toContain("실행 결과 분포");
    expect(markup).toContain("이슈 심각도 분석");
  });

  it("summarizes work-item risk and reports cycles with human-readable labels", () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <WorkItemRiskSection reports={report} />
        <CycleReportTable data={report} />
      </MemoryRouter>,
    );

    expect(markup).toContain("상위일감별 위험");
    expect(markup).toContain("WORK-482 · 카드 결제 승인 흐름 개선");
    expect(markup).toContain("사이클별 품질 현황");
    expect(markup).toContain("결제 회귀 테스트");
    expect(markup).toContain("진행 중");
    expect(markup).toContain("<th>PASS</th>");
    expect(markup).toContain("<td>3</td><td>2</td><td>1</td>");
    expect(markup).not.toContain("실행 항목");
    expect(markup).not.toContain("케이스 × 플랫폼");
    expect(markup).toContain('href="/cycles/cycle_internal_token"');
    expect(markup).not.toContain("view-transition");
    expect(markup).not.toContain("tm-cycle-navigation-pending");
    expect(markup).not.toContain("사이클 원장 상세");
    expect(markup).not.toContain("플랫폼 슬롯");
    expect(markup).not.toContain("<small>cycle_internal_token</small>");
  });

  it("locks status badge geometry and execution-link interaction states across viewports", () => {
    expect(styles).toContain("--tm-status-badge-inline-size: 4rem");
    expect(styles).toContain("--tm-status-badge-padding-inline: 0.55rem");
    expect(styles).toMatch(/\.tm-report-status\s*\{[^}]*inline-size: var\(--tm-status-badge-inline-size\)[^}]*padding-inline: var\(--tm-status-badge-padding-inline\)/s);
    expect(styles).toMatch(/\.tm-cycle-state-badge\s*\{[^}]*inline-size: var\(--tm-status-badge-inline-size\)[^}]*padding-inline: var\(--tm-status-badge-padding-inline\)/s);
    expect(cycleWorkspaceSource).toContain('className="tm-utility-link tm-execution-open-link"');
    expect(styles).toMatch(/\.tm-execution-open-link\s*\{[^}]*color: #fff;[^}]*background: var\(--tm-product-primary\);[^}]*border-color: var\(--tm-product-primary\)/s);
    expect(styles).toMatch(/\.tm-execution-open-link:hover\s*\{[^}]*color: #fff;[^}]*background: var\(--tm-product-primary-hover\)/s);
    expect(styles).toMatch(/\.tm-execution-open-link:active\s*\{[^}]*color: #fff;[^}]*background: #713321/s);
    expect(styles).toMatch(/\.tm-execution-open-link:focus-visible\s*\{[^}]*outline: 3px solid var\(--tm-product-focus\)/s);
    expect(contrastRatio("#ffffff", "#a95237")).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio("#ffffff", "#873d29")).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio("#ffffff", "#713321")).toBeGreaterThanOrEqual(4.5);
    expect(styles).toMatch(/@media \(max-width: 700px\)[\s\S]*?\.tm-execution-open-link\s*\{\s*width: 100%;/);
    expect(styles).not.toContain(".tm-snapshot-actions a,");
    expect(styles).not.toContain(".tm-linked-section a,\n\n.tm-integration-logo.is-api");
  });
});
