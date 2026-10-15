import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { GenerationJobPage } from "./generation-job-page";
import type { GenerationJobApi } from "./generation-job-service";

describe("GenerationJobPage", () => {
  it("renders explicit manual controls without creating a phantom job or testcase draft", () => {
    const api: GenerationJobApi = {
      listGenerationJobs: vi.fn(),
      getGenerationJob: vi.fn(),
      getGenerationComparison: vi.fn(),
      createGenerationJob: vi.fn(),
      executeGenerationJob: vi.fn(),
      retryGenerationJob: vi.fn(),
      getGenerationJobApplyPlan: vi.fn(),
      applyGenerationJob: vi.fn(),
    };
    const markup = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/slack-work-items/slack_work_1/generation-jobs"]}>
        <Routes><Route path="/slack-work-items/:workItemId/generation-jobs" element={<GenerationJobPage api={api} />} /></Routes>
      </MemoryRouter>,
    );

    expect(markup).toContain("테스트케이스 생성 작업");
    expect(markup).toContain("새 생성 작업 만들기");
    expect(markup).toContain("상태 새로고침");
    expect(markup).toContain("작업 만들기는 컨텍스트만 저장하며 외부 AI를 호출하지 않습니다");
    expect(markup).toContain("생성 작업 이력을 불러오는 중");
    expect(markup).toContain('href="/test-cases/new"');
    expect(markup).toContain("수동 테스트케이스 만들기");
    expect(markup).toContain('href="/slack-work-items/slack_work_1"');
    expect(api.createGenerationJob).not.toHaveBeenCalled();
    expect(api.executeGenerationJob).not.toHaveBeenCalled();
    expect(api.retryGenerationJob).not.toHaveBeenCalled();
    expect(api.applyGenerationJob).not.toHaveBeenCalled();
  });
});
