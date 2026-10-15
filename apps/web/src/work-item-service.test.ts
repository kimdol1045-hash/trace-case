import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiWorkItemService,
  type WorkItemServiceError,
} from "./work-item-service";

const WORK_ITEM_ID = "work_item_00000000000070008000000000000531";
const CATEGORY_ID = "folder_00000000000070008000000000000101";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiWorkItemService", () => {
  it("loads persisted fields, child projection and append-only history", async () => {
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/api/work-items/WORK-531")) {
        return json({ workItem: resource(), testCases: [childCase()] });
      }
      if (url.includes("/api/audit-logs?")) {
        return json({
          items: [{
            id: "audit_work_update",
            workspaceId: "workspace_default",
            entityType: "WORK_ITEM",
            entityId: WORK_ITEM_ID,
            action: "UPDATE",
            before: { summary: "Before", labels: ["payment"] },
            after: { summary: "Card payment reliability", labels: ["payment", "smoke"] },
            source: { type: "API", actorLabel: "QA Kim" },
            occurredAt: "2026-08-24T01:00:00.000Z",
            traceId: "trace_work_update",
          }],
          nextCursor: null,
          total: 1,
        });
      }
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);

    const detail = await new ApiWorkItemService("http://api.test").get("WORK-531");

    expect(detail.workItem).toMatchObject({
      key: "WORK-531",
      description: "Persisted scope",
      owner: "QA Kim",
      labels: ["payment", "smoke"],
      resourceVersion: 2,
      history: [expect.objectContaining({ action: "상위 일감 수정", actor: "QA Kim" })],
    });
    expect(detail.testCases[0]).toMatchObject({
      key: "TC-000001",
      domainId: CATEGORY_ID,
      sourceWorkItem: "WORK-531 · Card payment reliability",
    });
    expect(String(fetch.mock.calls[1]?.[0])).toContain(`entityId=${WORK_ITEM_ID}`);
  });

  it("sends resourceVersion updates and surfaces API failure without local success", async () => {
    let body: Record<string, unknown> | undefined;
    vi.stubGlobal("fetch", vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return json({
        code: "VERSION_CONFLICT",
        message: "The resource changed after it was loaded.",
        details: { expectedVersion: 1, actualVersion: 2 },
        traceId: "trace_conflict",
      }, 409);
    }));

    const service = new ApiWorkItemService("http://api.test");
    await expect(service.update({
      id: WORK_ITEM_ID,
      key: "WORK-531",
      domainId: CATEGORY_ID,
      summary: "Changed",
      description: "Changed scope",
      owner: "QA Lee",
      labels: ["regression"],
      resourceVersion: 1,
      createdAt: "2026-08-24T00:00:00.000Z",
      updatedAt: "2026-08-24T00:00:00.000Z",
      history: [],
    })).rejects.toEqual(expect.objectContaining<Partial<WorkItemServiceError>>({
      code: "VERSION_CONFLICT",
      message: "The resource changed after it was loaded.",
    }));
    expect(body).toMatchObject({
      baseResourceVersion: 1,
      categoryId: CATEGORY_ID,
      summary: "Changed",
      description: "Changed scope",
      ownerLabel: "QA Lee",
      labels: ["regression"],
    });
  });
});

function resource() {
  return {
    id: WORK_ITEM_ID,
    key: "WORK-531",
    categoryId: CATEGORY_ID,
    summary: "Card payment reliability",
    description: "Persisted scope",
    ownerLabel: "QA Kim",
    labels: ["payment", "smoke"],
    resourceVersion: 2,
    createdAt: "2026-08-24T00:00:00.000Z",
    updatedAt: "2026-08-24T01:00:00.000Z",
  };
}

function childCase() {
  return {
    id: "case_00000000000070008000000000000001",
    key: "TC-000001",
    workItemId: WORK_ITEM_ID,
    workItemKey: "WORK-531",
    workItemSummary: "Card payment reliability",
    categoryId: CATEGORY_ID,
    sortOrder: 0,
    status: "ACTIVE",
    summary: "Payment succeeds",
    priority: "HIGH",
    ownerLabel: "QA Kim",
    labels: ["smoke"],
    resourceVersion: 1,
    version: 1,
    source: { type: "UI" },
    updatedAt: "2026-08-24T01:00:00.000Z",
    deletedAt: null,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
