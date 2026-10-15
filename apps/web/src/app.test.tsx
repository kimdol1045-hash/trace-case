import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { AppRoutes } from "./app-routes-eager";

function renderRoute(route: string) {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[route]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

describe("AppRoutes", () => {
  it("renders the shared shell and overview route", () => {
    const markup = renderRoute("/");

    expect(markup).toContain("Trace Case");
    expect(markup).toContain("QA Dashboard");
    expect(markup).not.toContain("품질 지표 필터");
    expect(markup).not.toContain("상위일감 검색·선택");
    expect(markup).toContain("품질 지표를 불러오는 중");
    expect(markup).not.toContain("POSTGRESQL QUALITY LEDGER");
    expect(markup).not.toContain("사이클 실행");
    expect(markup).toContain("navigation-link--active");
    expect(markup).not.toContain("TEST OPERATIONS");
    expect(markup).not.toContain("Quality knowledge workspace");
    expect(markup).not.toContain("api-indicator");
    expect(markup).not.toContain("navigation-link__index");
  });

  it("starts the production test library empty while the API projection loads", () => {
    const markup = renderRoute("/test-cases");

    expect(markup).toContain("Test Library");
    expect(markup).toContain("테스트 카테고리");
    expect(markup).toContain('data-layout="category-work-items"');
    expect(markup).not.toContain("회원");
    expect(markup).not.toContain("주문");
    expect(markup).not.toContain("WORK-466");
    expect(markup).not.toContain("TC-1031");
    expect(markup).not.toContain("상태 필터");
    expect(markup).toContain("우선순위 필터");
    expect(markup).toContain("라벨 필터");
    expect(markup).toContain("삭제 항목 포함");
    expect(markup).toContain("+ 상위일감 만들기");
    expect(markup).not.toContain("상위일감 저장");
    expect(markup).not.toContain("그룹");
    expect(markup).not.toContain("활성 상태");
    expect(markup).not.toContain("선택 항목 세부 정보");
  });

  it("does not ghost a fixture work item while its canonical detail loads", () => {
    const markup = renderRoute("/work-items/WORK-482");

    expect(markup).toContain("상위 일감을 불러오는 중입니다");
    expect(markup).not.toContain("WORK-482 · Parent work item");
    expect(markup).not.toContain("카드 결제 승인 흐름 개선");
  });

  it("does not ghost a fixture case while its canonical detail loads", () => {
    const markup = renderRoute("/test-cases/TC-1042");

    expect(markup).toContain("테스트케이스를 확인하는 중입니다");
    expect(markup).not.toContain("카드 결제 승인 시 주문이 생성된다");
    expect(markup).not.toContain("변경사항 저장");
  });

  it("keeps new test case data as an unsaved draft until the explicit save action", () => {
    const markup = renderRoute("/test-cases/new?category=domain_members");

    expect(markup).toContain("테스트케이스 만들기");
    expect(markup).toContain("초안은 브라우저에서만 유지");
    expect(markup).toContain("테스트케이스 저장");
    expect(markup).toContain("사전조건");
    expect(markup).toContain("테스트 동작");
    expect(markup).toContain("기대 결과");
    expect(markup).toContain('class="tm-compact-summary-input"');
    expect(markup).toContain('aria-label="테스트케이스 서머리"');
    expect(markup).toContain('class="tm-step-table tm-step-table--manual"');
    expect(markup).toContain('class="tm-detail-rail tm-test-case-create-meta"');
    expect(markup).toContain('aria-label="1번 사전조건"');
    expect(markup).toContain('aria-label="1번 테스트 동작"');
    expect(markup).toContain('aria-label="1번 기대 결과"');
    expect(markup).not.toContain("tm-player-steps tm-test-case-create-steps");
    expect(markup).not.toContain("제목 없는 테스트케이스");
  });

  it("renders common issue landing and a separate Jira-like issue detail", () => {
    const issueListMarkup = renderRoute("/issues");
    const issueCreateMarkup = renderRoute("/issues/new");
    const issueDetailMarkup = renderRoute("/issues/ISSUE-104");

    expect(issueListMarkup).toContain("이슈 목록");
    expect(issueListMarkup).toContain("등록된 결함을 검색");
    expect(issueListMarkup).toContain("tm-issue-status-cards");
    expect(issueListMarkup).toContain("이슈 심각도");
    expect(issueListMarkup).toContain("PostgreSQL 이슈 원장을 불러오는 중");
    expect(issueCreateMarkup).toContain("새 이슈");
    expect(issueCreateMarkup).toContain("재현 스텝");
    expect(issueCreateMarkup).toContain("로컬 파일 선택");
    expect(issueCreateMarkup).toContain("실제 결과");
    expect(issueCreateMarkup).toContain("관련 테스트케이스");
    expect(issueCreateMarkup).toContain("테스트케이스 선택");
    expect(issueCreateMarkup).toContain("tm-title-input tm-title-input--issue tm-compact-summary-input");
    expect(issueCreateMarkup).not.toContain("PostgreSQL 이슈 원장을 불러오는 중");
    expect(issueDetailMarkup).toContain("이슈·댓글·첨부·감사 이력을 불러오는 중");
    expect(issueDetailMarkup).toContain("← 이슈 목록");
  });

  it("splits cycle discovery, creation, detail and execution into scalable screens", () => {
    const cycleListMarkup = renderRoute("/cycles");
    const cycleCreateMarkup = renderRoute("/cycles/new");
    const cycleDetailMarkup = renderRoute("/cycles/cycle_sprint_22");
    const cycleExecutionMarkup = renderRoute("/cycles/cycle_sprint_22/execute");

    expect(cycleListMarkup).toContain("Test Cycles");
    expect(cycleListMarkup).toContain("미진행");
    expect(cycleListMarkup).toContain("진행 중");
    expect(cycleListMarkup).toContain("완료");
    expect(cycleListMarkup).toContain("tm-cycle-list-loading");
    expect(cycleListMarkup).not.toContain("카드 결제 승인 QA");
    expect(cycleListMarkup).not.toContain("조건에 맞는 사이클이 없습니다");
    expect(cycleListMarkup).not.toContain("Cycle API에서 최신 원장을 불러오는 중입니다.");
    expect(cycleListMarkup).not.toContain("실행 대상 설정");
    expect(cycleCreateMarkup).toContain("tm-cycle-loading--create");
    expect(cycleDetailMarkup).toContain("tm-cycle-loading--detail");
    expect(cycleDetailMarkup).not.toContain("latest diff");
    expect(cycleExecutionMarkup).toContain("tm-cycle-loading--execution");
    for (const markup of [cycleCreateMarkup, cycleDetailMarkup, cycleExecutionMarkup]) {
      expect(markup).not.toContain("사이클을 찾을 수 없습니다");
      expect(markup).not.toContain("불러오는 중");
    }
  });

  it("does not flash fallback PLANNED data before detail and execution resolve their source", () => {
    const plannedDetail = renderRoute("/cycles/cycle_regression_18");
    const plannedPlayer = renderRoute("/cycles/cycle_regression_18/execute");

    expect(plannedDetail).toContain("tm-cycle-loading--detail");
    expect(plannedPlayer).toContain("tm-cycle-loading--execution");
    expect(plannedDetail).not.toContain("회귀 라벨 점검");
    expect(plannedPlayer).not.toContain("회귀 라벨 점검");
    expect(plannedDetail).not.toContain("사이클 시작");
    expect(plannedDetail).not.toContain("실행 대상 설정");
    expect(plannedPlayer).not.toContain("Actual result");
    expect(plannedPlayer).not.toContain("실행 대상 설정");
  });

  it("renders persistent reporting, knowledge and integration workspaces", () => {
    const reportsMarkup = renderRoute("/reports");
    expect(reportsMarkup).toContain("실행 결과와 이슈 분포를 분석");
    expect(reportsMarkup).toContain("상위일감 검색·선택");
    expect(reportsMarkup).not.toContain("CSV 다운로드");
    expect(reportsMarkup).not.toContain("POSTGRESQL QUALITY ANALYTICS");
    expect(renderRoute("/knowledge")).toContain("QA Knowledge");
    expect(renderRoute("/knowledge")).toContain("Slack 결정");
    expect(renderRoute("/integrations")).toContain("첨부파일 저장소");
    expect(renderRoute("/integrations")).toContain("직접 업로드");
    expect(renderRoute("/integrations")).toContain("Slack 연결과 수집 채널을 불러오는 중");
    expect(renderRoute("/integrations")).toContain("브라우저에서 비공개 object storage");
  });

  it("exposes the Slack ledger landing and append-only detail routes", () => {
    const landing = renderRoute("/slack-work-items");
    const detail = renderRoute("/slack-work-items/slack_work_1");
    const generationJobs = renderRoute("/slack-work-items/slack_work_1/generation-jobs");
    const comparison = renderRoute("/slack-work-items/slack_work_1/generation-jobs/generation_job_1/comparison");

    expect(landing).toContain("Slack Work Items");
    expect(landing).toContain("Slack work item 원장을 불러오는 중");
    expect(landing).toContain("Slack Work Items");
    expect(detail).toContain("Slack 요청 상세");
    expect(detail).toContain("수집된 대화, 요청 정보, 변경 이력");
    expect(detail).toContain('href="/slack-work-items/slack_work_1/generation-jobs"');
    expect(detail).toContain("테스트 생성 작업");
    expect(generationJobs).toContain("테스트케이스 생성 작업");
    expect(generationJobs).toContain("새 생성 작업 만들기");
    expect(generationJobs).toContain("상태 새로고침");
    expect(generationJobs).toContain("수동 테스트케이스 만들기");
    expect(generationJobs).toContain("생성 작업 이력을 불러오는 중");
    expect(generationJobs).not.toContain("테스트케이스 저장 확인");
    expect(generationJobs).not.toContain("생성 공급자 선택");
    expect(comparison).toContain("생성 작업 비교");
    expect(comparison).toContain("봉인된 비교 자료를 불러오는 중");
    expect(comparison).toContain("읽기 전용");
    expect(comparison).toContain('href="/slack-work-items/slack_work_1/generation-jobs"');
  });

  it("exposes quality review beside automatic-generation failures without selecting a candidate", () => {
    const markup = renderRoute("/generation/quality-review");

    expect(markup).toContain("자동 생성 품질 후보 검토");
    expect(markup).toContain("CANDIDATE / UNREVIEWED");
    expect(markup).toContain("목록을 자동 선택하지 않습니다");
    expect(markup).toContain('href="/generation/failures"');
    expect(markup).toContain('href="/generation/quality-review"');
    expect(markup.indexOf('href="/generation/failures"')).toBeLessThan(
      markup.indexOf('href="/generation/quality-review"'),
    );
  });

  it("does not expose the removed settings route", () => {
    const markup = renderRoute("/settings");

    expect(markup).toContain("페이지를 찾을 수 없습니다");
    expect(markup).not.toContain("Workspace settings");
  });

  it("provides a recovery route for unknown locations", () => {
    const markup = renderRoute("/not-a-route");

    expect(markup).toContain("페이지를 찾을 수 없습니다");
    expect(markup).toContain("Overview로 이동");
  });
});
