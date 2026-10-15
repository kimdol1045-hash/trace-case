import { VIEW_STATE } from "@tc/ui";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuditHistory } from "./audit-history";
import { FolderLibraryPage, WorkItemCreateModal } from "./folder-library-page";
import {
  getDomainScope,
  initialTestManagementState,
  testManagementDemoReducer,
  TestManagementProvider,
} from "./test-management-demo";
import { validateWorkItemDraft } from "./work-item-create";
import { PRIMARY_WORKSPACE_ID, type WorkspaceSettings } from "./workspace-service";
import { WorkspaceSettingsForm } from "./workspace-settings-page";

const settings: WorkspaceSettings = {
  id: PRIMARY_WORKSPACE_ID,
  teamName: "QA Platform Team",
  casePrefix: "TC",
  issuePrefix: "ISSUE",
  defaultEnvironment: "staging",
  generationQuietMinutes: 10,
  version: 3,
  createdAt: "2026-08-20T01:00:00.000Z",
  updatedAt: "2026-08-20T02:00:00.000Z",
};

describe("Sprint 1 workspace UI", () => {
  it("renders editable team, prefix and default environment settings", () => {
    const markup = renderToStaticMarkup(
      <WorkspaceSettingsForm settings={settings} onSubmit={vi.fn()} />,
    );

    expect(markup).toContain("QA Platform Team");
    expect(markup).toContain('name="casePrefix"');
    expect(markup).toContain('name="issuePrefix"');
    expect(markup).toContain('name="defaultEnvironment"');
    expect(markup).toContain('data-mobile-layout="single-column"');
    expect(markup).toContain("설정 저장");
  });

  it("renders append-only audit history and error retry", () => {
    const readyMarkup = renderToStaticMarkup(
      <AuditHistory
        onRetry={vi.fn()}
        resource={{
          status: VIEW_STATE.READY,
          data: {
            total: 1,
            nextCursor: null,
            items: [
              {
                id: "audit_settings",
                workspaceId: PRIMARY_WORKSPACE_ID,
                entityType: "WORKSPACE_SETTINGS",
                entityId: PRIMARY_WORKSPACE_ID,
                action: "UPDATE",
                before: null,
                after: { teamName: "QA Platform Team" },
                source: { type: "UI", actorLabel: "team member" },
                occurredAt: "2026-08-20T03:00:00.000Z",
                traceId: "trace_settings",
              },
            ],
          },
        }}
      />,
    );
    expect(readyMarkup).toContain("설정 변경");
    expect(readyMarkup).toContain("AUDIT · APPEND ONLY");

    const errorMarkup = renderToStaticMarkup(
      <AuditHistory
        onRetry={vi.fn()}
        resource={{ status: VIEW_STATE.ERROR, message: "offline" }}
      />,
    );
    expect(errorMarkup).toContain("다시 시도");
  });

  it("renders top-level categories with parent work items and clickable mock cases", () => {
    const folderMarkup = renderToStaticMarkup(
      <MemoryRouter>
        <TestManagementProvider enableDevelopmentFixtures>
          <FolderLibraryPage />
        </TestManagementProvider>
      </MemoryRouter>,
    );
    const settingsMarkup = renderToStaticMarkup(
      <WorkspaceSettingsForm settings={settings} onSubmit={vi.fn()} />,
    );

    expect(folderMarkup).toContain('class="tm-library-grid"');
    expect(folderMarkup).toContain('data-layout="category-work-items"');
    expect(folderMarkup).toContain("테스트 카테고리");
    expect(folderMarkup).toContain("회원");
    expect(folderMarkup).toContain("주문");
    expect(folderMarkup).toContain("WORK-466");
    expect(folderMarkup).toContain("TC-1031");
    expect(folderMarkup).toContain('aria-expanded="false"');
    expect(folderMarkup).toContain('aria-controls="work-item-cases-WORK-466"');
    expect(folderMarkup).toContain("WORK-466 테스트케이스 펼치기");
    expect(folderMarkup).toContain("일감명 클릭 시 상세 보기");
    expect(folderMarkup).not.toContain("그룹");
    expect(folderMarkup).not.toContain("활성 상태");
    expect(folderMarkup).not.toContain("선택 항목 세부 정보");
    expect(settingsMarkup).toContain('data-mobile-layout="single-column"');
  });

  it("renders an explicit parent work item save modal without creating on open", () => {
    const onCancel = vi.fn();
    const onSave = vi.fn(async () => true);
    const markup = renderToStaticMarkup(
      <WorkItemCreateModal
        categories={initialTestManagementState.domains}
        initialCategoryId="domain_members"
        workItems={initialTestManagementState.workItems}
        onCancel={onCancel}
        onSave={onSave}
      />,
    );

    expect(markup).toContain("상위일감 만들기");
    expect(markup).toContain("카테고리");
    expect(markup).toContain("WORK-531");
    expect(markup).toContain("서머리");
    expect(markup).toContain("설명");
    expect(markup).toContain("담당자");
    expect(markup).toContain("라벨");
    expect(markup).toContain("취소");
    expect(markup).toContain("상위일감 저장");
    expect(onCancel).not.toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("validates parent work item keys before API creation", () => {
    expect(validateWorkItemDraft({
      domainId: "domain_orders",
      key: "work-482",
      summary: "중복 일감",
      description: "",
      owner: "",
      labels: "",
    }, initialTestManagementState.domains, initialTestManagementState.workItems)).toMatchObject({
      key: "이미 사용 중인 상위일감 키입니다.",
    });
  });

  it("keeps category scope flat", () => {
    const accountScope = getDomainScope(initialTestManagementState.domains, "domain_members");

    expect(accountScope).toEqual(new Set(["domain_members"]));
  });

  it("keeps canonical category and work-item projections unchanged by case filters", () => {
    const category = {
      id: "folder_00000000000070008000000000000901" as const,
      parentId: null,
      name: "Payments",
      sortOrder: 0,
      resourceVersion: 3,
      activeTestCaseCount: 17,
      workItemCount: initialTestManagementState.workItems.length,
    };
    const unfiltered = testManagementDemoReducer(initialTestManagementState, {
      type: "HYDRATE_CASE_LIBRARY",
      domains: [category],
      workItems: initialTestManagementState.workItems,
      testCases: initialTestManagementState.testCases,
      append: false,
    });
    const filtered = testManagementDemoReducer(unfiltered, {
      type: "HYDRATE_CASE_LIBRARY",
      domains: [category],
      workItems: initialTestManagementState.workItems,
      testCases: initialTestManagementState.testCases.slice(0, 1),
      append: false,
    });

    expect(filtered.domains[0]).toMatchObject({
      activeTestCaseCount: 17,
      workItemCount: initialTestManagementState.workItems.length,
    });
    expect(filtered.workItems).toHaveLength(initialTestManagementState.workItems.length);
    expect(filtered.testCases).toHaveLength(1);
  });

  it("does not let a concurrent list refresh overwrite a loaded case detail", () => {
    const detailed = initialTestManagementState.testCases.find((item) => item.key === "TC-1042")!;
    const refreshed = testManagementDemoReducer(initialTestManagementState, {
      type: "HYDRATE_CASE_LIBRARY",
      domains: initialTestManagementState.domains,
      workItems: initialTestManagementState.workItems,
      testCases: [{ ...detailed, precondition: "", steps: [], history: [] }],
      append: false,
    });
    const preserved = refreshed.testCases[0]!;

    expect(preserved.steps).toEqual(detailed.steps);
    expect(preserved.precondition).toBe(detailed.precondition);
    expect(preserved.history).toEqual(detailed.history);
  });

  it("does not let a concurrent list refresh erase work-item history or child projection", () => {
    const original = initialTestManagementState.workItems.find((item) => item.key === "WORK-482")!;
    const child = initialTestManagementState.testCases.find((item) => item.key === "TC-1042")!;
    const detailed = testManagementDemoReducer(initialTestManagementState, {
      type: "UPSERT_API_WORK_ITEM",
      workItem: {
        ...original,
        resourceVersion: 2,
        history: [{
          id: "audit_work",
          action: "상위 일감 수정",
          actor: "QA",
          occurredAt: "2026-08-24T01:00:00.000Z",
        }],
      },
      testCases: [child],
    });
    const refreshed = testManagementDemoReducer(detailed, {
      type: "HYDRATE_CASE_LIBRARY",
      domains: detailed.domains,
      workItems: [{ ...original, resourceVersion: 2, history: [] }],
      testCases: [],
      append: false,
    });

    expect(refreshed.workItems[0]?.history).toHaveLength(1);
    expect(refreshed.testCases.some((testCase) => testCase.key === "TC-1042")).toBe(true);
  });
});
