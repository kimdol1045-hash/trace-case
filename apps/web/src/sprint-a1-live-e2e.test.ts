import { createApiClient } from "@tc/api-client";
import { describe, expect, it } from "vitest";

import { ApiTestCaseService } from "./api-test-case-service";
import { ApiCategoryService } from "./category-service";
import { ApiWorkItemService } from "./work-item-service";

const liveApiBaseUrl = import.meta.env.VITE_SPRINT_A1_E2E_API_BASE_URL;
const liveDescribe = liveApiBaseUrl ? describe : describe.skip;

liveDescribe("Sprint A1 live Web/API/PostgreSQL journey", () => {
  it("creates category → work item → case → cycle and reloads through fresh Web adapters", async () => {
    const nonce = `${Date.now()}`.slice(-8);
    const workItemKey = `WORK-${nonce}` as const;
    const categoryName = `Web reload E2E ${nonce}`;
    const categories = new ApiCategoryService(liveApiBaseUrl);
    const workItems = new ApiWorkItemService(liveApiBaseUrl);
    const cases = new ApiTestCaseService(liveApiBaseUrl);
    const cycles = createApiClient(liveApiBaseUrl);

    const categoryId = await categories.create(categoryName, 0);
    const workItem = await workItems.create({
      domainId: categoryId,
      key: workItemKey,
      summary: "Web adapter reload chain",
      description: "Created only after the explicit create service call",
      owner: "QA Web E2E",
      labels: ["sprint-a1", "reload"],
    });
    const testCase = await cases.create(workItem, {
      summary: "Canonical Web case survives reload",
      priority: "HIGH",
      owner: "QA Web E2E",
      labels: ["sprint-a1", "reload"],
      steps: [{
        precondition: "The API and PostgreSQL are available.",
        action: "Reload the Test Library route.",
        expectedResult: "The canonical case is returned.",
      }],
    });
    const cycle = await cycles.createCycle({
      name: `Web reload cycle ${nonce}`,
      description: "Created through the generated Web client",
      startDate: "2026-08-24",
      scope: {
        type: "WORK_ITEM",
        workItemId: workItem.id!,
        workItemKey: workItem.key,
      },
    });

    // Fresh adapters model a hard browser reload: no provider cache or prior
    // service-local resource map is available to satisfy these reads.
    const reloadedCategories = new ApiCategoryService(liveApiBaseUrl);
    const reloadedWorkItems = new ApiWorkItemService(liveApiBaseUrl);
    const reloadedCases = new ApiTestCaseService(liveApiBaseUrl);
    const reloadedCycles = createApiClient(liveApiBaseUrl);
    const projection = await reloadedCategories.loadProjection();
    const reloadedWorkItem = await reloadedWorkItems.get(workItem.key);
    const reloadedCase = await reloadedCases.get(testCase.key);
    const reloadedCycle = await reloadedCycles.getCycle(cycle.id);
    const reloadedCycleItems = await reloadedCycles.listCycleItems(cycle.id);

    expect(projection.domains).toContainEqual(expect.objectContaining({
      id: categoryId,
      name: categoryName,
    }));
    expect(reloadedWorkItem.workItem).toMatchObject({
      key: workItem.key,
      domainId: categoryId,
    });
    expect(reloadedWorkItem.testCases.map((item) => item.key)).toContain(testCase.key);
    expect(reloadedCase.testCase).toMatchObject({
      key: testCase.key,
      summary: "Canonical Web case survives reload",
    });
    expect(reloadedCycle).toMatchObject({ id: cycle.id, status: "PLANNED" });
    expect(reloadedCycleItems.items).toHaveLength(1);
    expect(reloadedCycleItems.items[0]?.caseKey).toBe(testCase.key);
  });
});
