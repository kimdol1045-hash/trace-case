import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { TestCaseDetailPage, TestCaseEditor } from "./test-case-detail-page";
import { initialTestManagementState, TestManagementProvider } from "./test-management-demo";

const { readFileSync } = await vi.importActual<{ readFileSync: (path: URL, encoding: "utf8") => string }>("node:fs");
const testCaseDetailSource = readFileSync(new URL("./test-case-detail-page.tsx", import.meta.url), "utf8");

function renderDetail(caseKey = "TC-1042") {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[`/test-cases/${caseKey}`]}>
      <TestManagementProvider enableDevelopmentFixtures>
        <Routes>
          <Route path="/test-cases/:caseKey" element={<TestCaseDetailPage />} />
        </Routes>
      </TestManagementProvider>
    </MemoryRouter>,
  );
}

describe("TestCaseDetailPage", () => {
  it("edits every manual step as an independent precondition, action, and expected result", () => {
    const markup = renderDetail();

    expect(markup).toContain('aria-label="1번 사전조건"');
    expect(markup).toContain('aria-label="2번 사전조건"');
    expect(markup).toContain('aria-label="1번 테스트 동작"');
    expect(markup).toContain('aria-label="1번 기대 결과"');
    expect(markup).toContain("tm-step-drag-handle");
    expect(markup).not.toContain("readonly");
    expect(markup).not.toContain("공통 사전조건");
  });

  it("keeps CASE automation compatible without exposing its mode or case-level result in the manual editor", () => {
    const markup = renderDetail();

    expect(markup).not.toContain("기대 결과 모드");
    expect(markup).not.toContain("케이스 기대 결과");
    expect(markup).not.toContain("Generation API");
  });

  it("uses the shared confirmation dialog before deleting a test case", () => {
    expect(testCaseDetailSource).toContain('<DeleteConfirmDialog open={deletePending} title="테스트케이스 삭제"');
    expect(testCaseDetailSource).not.toContain("tm-inline-confirm");
  });

  it("renders a deleted test case as read-only with restore and permanent-delete actions", () => {
    const testCase = {
      ...initialTestManagementState.testCases[0]!,
      deletedAt: "2026-08-21T02:00:00Z",
    };
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <TestManagementProvider enableDevelopmentFixtures>
          <TestCaseEditor
            testCase={testCase}
            persistence={{ status: "READY", source: "API", nextCursor: null, total: 1 }}
            onReload={vi.fn()}
            onSave={vi.fn(async () => true)}
            onDelete={vi.fn(async () => true)}
            onRestoreDeleted={vi.fn(async () => true)}
            onPermanentDelete={vi.fn(async () => undefined)}
            onRestoreVersion={vi.fn(async () => true)}
          />
        </TestManagementProvider>
      </MemoryRouter>,
    );

    expect(markup).toContain("삭제됨 · 읽기 전용");
    expect(markup).toContain("삭제 복원");
    expect(markup).toContain("완전 삭제");
    expect(markup).toContain("readOnly");
    expect(markup).toContain('aria-label="테스트케이스 라벨 읽기 전용"');
    expect(markup).not.toContain("라벨 삭제");
  });
});
