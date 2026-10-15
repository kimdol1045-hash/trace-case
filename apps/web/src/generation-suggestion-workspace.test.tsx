import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { TestCaseEditor } from "./test-case-detail-page";
import {
  initialTestManagementState,
  TestManagementProvider,
  type DemoTestCase,
} from "./test-management-demo";

const { readFileSync } = await vi.importActual<{
  readFileSync: (path: URL, encoding: "utf8") => string;
}>("node:fs");
const detailSource = readFileSync(
  new URL("./test-case-detail-page.tsx", import.meta.url),
  "utf8",
);
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

const openSuggestion = {
  id: "generation_suggestion_00000000000070008000000000000001",
  testCaseId: "case_00000000000070008000000000000001",
  generationLineageId: "generation_lineage_00000000000070008000000000000001",
  state: "OPEN",
  baseline: {
    caseResourceVersion: 4,
    caseVersionId: "case_version_00000000000070008000000000000004",
    caseVersion: 4,
    snapshot: {
      summary: "사람이 수정한 결제 재시도",
      priority: "CRITICAL",
      labels: ["checkout", "human-reviewed"],
      steps: [{ sequence: 1, action: "현재 결제 결과를 확인한다" }],
    },
    digest: "a".repeat(64),
  },
  proposal: {
    snapshot: {
      summary: "결제 재시도 중복 승인 방지",
      priority: "HIGH",
      labels: ["checkout", "idempotency"],
      steps: [
        { sequence: 1, action: "동일 결제 키로 다시 요청한다" },
        { sequence: 2, action: "기존 승인 결과를 확인한다" },
      ],
    },
    digest: "b".repeat(64),
  },
  diff: {
    schemaVersion: "generation-suggestion-diff/1.0.0",
    digest: "c".repeat(64),
    changedFields: ["summary", "steps", "priority", "labels"],
    changes: [
      {
        path: "summary",
        before: "사람이 수정한 결제 재시도",
        after: "결제 재시도 중복 승인 방지",
      },
      {
        path: "steps",
        before: [{ sequence: 1, action: "현재 결제 결과를 확인한다" }],
        after: [
          { sequence: 1, action: "동일 결제 키로 다시 요청한다" },
          { sequence: 2, action: "기존 승인 결과를 확인한다" },
        ],
      },
    ],
  },
  source: {
    generationJobId: "generation_job_00000000000070008000000000000005",
    generationVersion: 5,
    sourceVersion: 7,
  },
  resolution: null,
  supersededBySuggestionId: null,
  createdAt: "2026-08-27T04:00:00Z",
};

function testCaseWithSuggestions() {
  return {
    ...initialTestManagementState.testCases[0]!,
    generationManagement: {
      managed: false,
      lineageId: openSuggestion.generationLineageId,
      releasedAt: "2026-08-27T03:00:00Z",
    },
    generationSuggestions: [
      openSuggestion,
      {
        ...openSuggestion,
        id: "generation_suggestion_00000000000070008000000000000002",
        state: "IGNORED",
        resolution: { decision: "IGNORED", reason: "사람 수정 유지" },
      },
      {
        ...openSuggestion,
        id: "generation_suggestion_00000000000070008000000000000003",
        state: "SUPERSEDED",
        resolution: null,
        supersededBySuggestionId: openSuggestion.id,
      },
    ],
  } as unknown as DemoTestCase;
}

function renderSuggestionWorkspace(testCase = testCaseWithSuggestions()) {
  return renderToStaticMarkup(
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
}

describe("PE-SE2-T05 generation suggestion workspace", () => {
  it("renders immutable before/after diff and terminal states on the case detail page", () => {
    const markup = renderSuggestionWorkspace();

    expect(markup).toContain('aria-labelledby="generation-suggestions-heading"');
    expect(markup).toContain('id="generation-suggestions-heading"');
    expect(markup).toContain("생성 변경 제안");
    expect(markup).toContain("열림");
    expect(markup).toContain("무시됨");
    expect(markup).toContain("대체됨");
    expect(markup).toContain("사람이 수정한 결제 재시도");
    expect(markup).toContain("결제 재시도 중복 승인 방지");
    expect(markup).toContain("기준 v4");
    expect(markup).toContain("생성 v5");
    expect(markup).toContain("현재 케이스를 자동으로 덮어쓰지 않았습니다");
  });

  it("uses native keyboard controls and accessible confirmation/status semantics", () => {
    const markup = renderSuggestionWorkspace();

    expect(markup).toContain(
      `aria-controls="generation-suggestion-diff-${openSuggestion.id}"`,
    );
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain("변경 내용 펼치기");
    expect(markup).toContain('<button type="button"');
    expect(markup).toContain("제안 반영");
    expect(markup).toContain("무시");
    expect(detailSource).toContain("GenerationSuggestionApplyDialog");
    expect(detailSource).toContain("GenerationSuggestionIgnoreDialog");
    expect(detailSource).toContain('role="alert"');
    expect(detailSource).toContain('key === "Escape"');
    expect(detailSource).not.toContain("onDoubleClick");
  });

  it("removes decision controls from terminal suggestions", () => {
    const terminalOnly = {
      ...testCaseWithSuggestions(),
      generationSuggestions: [
        {
          ...openSuggestion,
          state: "APPLIED",
          resolution: {
            decision: "APPLIED",
            appliedCaseVersionId: "case_version_applied",
          },
        },
      ],
    } as unknown as DemoTestCase;
    const markup = renderSuggestionWorkspace(terminalOnly);

    expect(markup).toContain("적용됨");
    expect(markup).not.toContain("제안 반영");
    expect(markup).not.toContain("제안 무시");
  });

  it("stacks diff/actions at 390px with 44px targets and no page overflow", () => {
    expect(styles).toContain(".tm-generation-suggestion-card");
    expect(styles).toContain(".tm-generation-suggestion-diff");
    expect(styles).toContain(".tm-generation-suggestion-actions");
    expect(styles).toMatch(
      /@media[^{}]*\(max-width:\s*\d+px\)[\s\S]*\.tm-generation-suggestion-diff[^{}]*\{[^}]*grid-template-columns:\s*1fr/,
    );
    expect(styles).toMatch(
      /\.tm-generation-suggestion-actions[^{}]*button[^{}]*\{[^}]*min-height:\s*44px/,
    );
    expect(styles).toMatch(
      /\.tm-generation-suggestion-(?:card|diff)[^{}]*\{[^}]*(?:overflow-wrap:\s*anywhere|word-break:\s*break-word)/,
    );
    expect(styles).not.toMatch(
      /\.tm-generation-suggestion-(?:card|diff)[^{}]*\{[^}]*overflow-x:\s*(?:auto|scroll)/,
    );
  });
});
