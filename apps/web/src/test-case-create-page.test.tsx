import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  groupCreateWorkItems,
  selectionForCategory,
  selectionForWorkItem,
  TestCaseCreatePage,
} from "./test-case-create-page";
import {
  initialTestManagementState,
  TestManagementProvider,
} from "./test-management-demo";

function renderCreatePage(route = "/test-cases/new", enableDevelopmentFixtures = true) {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[route]}>
      <TestManagementProvider enableDevelopmentFixtures={enableDevelopmentFixtures}>
        <Routes>
          <Route path="/test-cases/new" element={<TestCaseCreatePage />} />
        </Routes>
      </TestManagementProvider>
    </MemoryRouter>,
  );
}

describe("TestCaseCreatePage parent work item selection", () => {
  it("shows every active parent work item grouped by category", () => {
    const markup = renderCreatePage("/test-cases/new?category=domain_orders");

    expect(markup).toContain('<optgroup label="회원">');
    expect(markup).toContain('<optgroup label="주문">');
    expect(markup).toContain('<optgroup label="미분류">');
    for (const workItem of initialTestManagementState.workItems) {
      expect(markup).toContain(`${workItem.key} · ${workItem.summary}`);
    }
    expect(markup).toContain('<option value="domain_orders" selected="">주문</option>');
    expect(markup).toContain('<option value="WORK-482" selected="">WORK-482 · 카드 결제 승인 흐름 개선</option>');
  });

  it("keeps the category and parent work item selections synchronized", () => {
    expect(selectionForCategory(initialTestManagementState.workItems, "domain_members")).toEqual({
      categoryId: "domain_members",
      workItemKey: "WORK-466",
    });
    expect(selectionForWorkItem(initialTestManagementState.workItems, "WORK-511")).toEqual({
      categoryId: "domain_orders",
      workItemKey: "WORK-511",
    });
    const groupedKeys = groupCreateWorkItems(
      initialTestManagementState.domains,
      initialTestManagementState.workItems,
    ).flatMap((group) => group.workItems.map((workItem) => workItem.key));
    expect(groupedKeys.sort()).toEqual(
      initialTestManagementState.workItems.map((workItem) => workItem.key).sort(),
    );
  });

  it("renders disabled empty options when no categories or parent work items are available", () => {
    const markup = renderCreatePage("/test-cases/new", false);

    expect(markup).toContain('<select aria-label="카테고리" disabled="">');
    expect(markup).toContain("선택 가능한 카테고리가 없습니다.");
    expect(markup).toContain('<select aria-label="상위일감" disabled="">');
    expect(markup).toContain("선택 가능한 상위일감이 없습니다.");
  });
});
