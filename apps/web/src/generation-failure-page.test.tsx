import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { GenerationFailurePage, RetryDialog } from "./generation-failure-page";
import type { GenerationFailureDetail } from "./generation-job-service";

const detail = {
  contractVersion: "automatic-generation-retry-history/1.0.0",
  failure: {
    rootJobId: "generation_job_001",
    failedJobId: "generation_job_003",
    slackWorkItemId: "slack_work_item_001",
    sourceVersion: 4,
    jobAttemptNumber: 3,
    failure: {
      code: "GENERATION_TIMEOUT",
      messageLabel: "생성 서비스 응답 시간이 초과되었습니다",
      retryable: true,
      finality: "TERMINAL_EXHAUSTED",
      providerAttemptCount: 3,
      failedAt: "2026-08-28T01:05:20Z",
      nextAttemptNotBefore: null,
      exhaustionReceiptId: "generation_retry_exhaustion_001",
    },
    actionability: "OPERATOR_RETRY_ALLOWED",
    latestRetryJobId: null,
  },
  attemptHistory: [],
  timeline: [],
} as unknown as GenerationFailureDetail;

describe("automatic generation failure workspace", () => {
  it("keeps the automatic workspace distinct from manual generation", () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter><GenerationFailurePage /></MemoryRouter>,
    );
    expect(markup).toContain("자동 생성 실패");
    expect(markup).toContain("실패 목록");
    expect(markup).toContain("시도 이력");
    expect(markup).toContain('<section class="tm-generation-failure-workspace tm-dashboard-page"');
    expect(markup).toContain('class="tm-page-header tm-generation-failure__header"');
    expect(markup).toContain('class="tm-list-surface tm-list-toolbar tm-toolbar--compound tm-generation-failure__filters"');
    expect(markup).not.toContain('<main class="tm-generation-failure"');
    expect(markup).not.toContain("공급자 원문 응답");
  });

  it("renders a labelled keyboard dialog with live limits and advisory warning", () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <RetryDialog detail={detail} onClose={() => undefined} onCompleted={() => undefined} />
      </MemoryRouter>,
    );
    expect(markup).toContain('role="dialog"');
    expect(markup).toContain('aria-describedby="retry-dialog-description"');
    expect(markup).toContain('class="tm-cycle-modal tm-generation-failure__dialog"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain("추가 조건");
    expect(markup).toContain("시스템 규칙·근거·적용 정책을 변경할 수 없습니다");
  });

  it("renders the bounded retry input and explicit cancel action", () => {
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <RetryDialog detail={detail} onClose={() => undefined} onCompleted={() => undefined} />
      </MemoryRouter>,
    );
    expect(markup).toContain('maxLength="1000"');
    expect(markup).toContain("취소");
    expect(markup).toContain("조건을 추가해 재시도");
  });
});
