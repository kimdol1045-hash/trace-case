import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiTestCaseService,
} from "./api-test-case-service";
import {
  initialTestManagementState,
} from "./test-management-demo";

const CATEGORY_ID = "folder_00000000000070008000000000000101";
const WORK_ITEM_ID = "work_item_00000000000070008000000000000482";
const CASE_ID = "case_00000000000070008000000000001042";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiTestCaseService", () => {
  it("uses generated list query fields and maps category, work item and pagination", async () => {
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/api/test-cases?")) return json({ items: [listItem()], total: 1, nextCursor: "cursor_2" });
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);
    const service = new ApiTestCaseService("http://api.test");

    const result = await service.list({
      search: "card",
      categoryIds: [CATEGORY_ID],
      statuses: ["ACTIVE"],
      priorities: ["CRITICAL"],
      labels: ["smoke"],
      labelMatch: "ALL",
      includeDeleted: true,
      deletedOnly: true,
      cursor: "cursor_1",
      limit: 25,
    });

    expect(result).toMatchObject({ source: "API", total: 1, nextCursor: "cursor_2" });
    expect(result.testCases[0]).toMatchObject({ key: "TC-1042", priority: "HIGHEST", sortOrder: 3 });
    expect(String(fetch.mock.calls[0]?.[0])).toContain("search=card");
    expect(String(fetch.mock.calls[0]?.[0])).toContain(`categoryIds=${CATEGORY_ID}`);
    expect(String(fetch.mock.calls[0]?.[0])).toContain("labels=smoke");
    expect(String(fetch.mock.calls[0]?.[0])).toContain("includeDeleted=true");
    expect(String(fetch.mock.calls[0]?.[0])).toContain("deletedOnly=true");
    expect(String(fetch.mock.calls[0]?.[0])).toContain("cursor=cursor_1");
  });

  it("loads a deleted case through the includeDeleted detail contract", async () => {
    const deletedAt = "2026-08-21T02:00:00Z";
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/api/test-cases?search=TC-1042&includeDeleted=false&limit=50")) return json({ items: [], total: 0, nextCursor: null });
      if (url.endsWith("/api/test-cases?search=TC-1042&includeDeleted=true&limit=50")) return json({ items: [{ ...listItem(), deletedAt }], total: 1, nextCursor: null });
      if (url.endsWith(`/api/test-cases/${CASE_ID}?includeDeleted=true`)) return json({ ...resource(), deletedAt });
      if (url.endsWith(`/api/test-cases/${CASE_ID}/versions?limit=200`)) return json({ items: [version(2)], total: 1, nextCursor: null });
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);

    const detail = await new ApiTestCaseService("http://api.test").get("TC-1042");

    expect(detail.testCase).toMatchObject({ key: "TC-1042", deletedAt, sourceWorkItem: "WORK-482 · Card checkout" });
    expect(fetch.mock.calls.map(([input]) => String(input))).toContain(`http://api.test/api/test-cases/${CASE_ID}?includeDeleted=true`);
  });

  it("permanently deletes only a deleted case through the strong-delete endpoint", async () => {
    let request: RequestInit | undefined;
    const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      if (String(input).endsWith(`/api/test-cases/${CASE_ID}/permanent`)) {
        request = init;
        return new Response(null, { status: 204 });
      }
      throw new Error(`Unexpected request ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetch);
    const service = new ApiTestCaseService("http://api.test/");

    await service.permanentDelete({
      id: CASE_ID as `case_${string}`,
      key: "TC-1042",
      resourceVersion: 4,
      deletedAt: "2026-08-21T02:00:00Z",
    });

    expect(request?.method).toBe("DELETE");
    expect(request?.headers).toMatchObject({ "X-Actor-Label": "Test Library UI" });
    expect(JSON.parse(String(request?.body))).toMatchObject({ baseResourceVersion: 4 });
  });

  it("restores a deleted case through the restore endpoint", async () => {
    const deletedAt = "2026-08-21T02:00:00Z";
    let restoreBody: Record<string, unknown> | undefined;
    const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/test-cases?search=TC-1042&includeDeleted=true&limit=50")) return json({ items: [{ ...listItem(), deletedAt }], total: 1, nextCursor: null });
      if (url.endsWith(`/api/test-cases/${CASE_ID}?includeDeleted=true`)) return json({ ...resource(), deletedAt });
      if (url.endsWith(`/api/test-cases/${CASE_ID}/restore`) && init?.method === "POST") {
        restoreBody = JSON.parse(String(init.body)) as Record<string, unknown>;
        return json({ operation: "RESTORE_DELETED", testCase: resource(), auditId: "audit_restore" });
      }
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);

    const restored = await new ApiTestCaseService("http://api.test").restore("TC-1042");

    expect(restored.deletedAt).toBeNull();
    expect(restoreBody).toMatchObject({ baseResourceVersion: 4 });
  });

  it("loads immutable versions and field/step diffs for detail history", async () => {
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/api/test-cases?search=TC-1042&includeDeleted=false&limit=50")) return json({ items: [listItem()], total: 1, nextCursor: null });
      if (url.endsWith(`/api/test-cases/${CASE_ID}`)) return json(resource());
      if (url.endsWith(`/api/test-cases/${CASE_ID}/versions?limit=200`)) return json({ items: [version(1), version(2)], total: 2, nextCursor: null });
      if (url.includes(`/api/test-cases/${CASE_ID}/versions/diff?`)) return json({
        testCaseId: CASE_ID,
        fromVersion: 1,
        toVersion: 2,
        fieldChanges: [{ field: "summary", before: "Old summary", after: "Card checkout" }],
        stepChanges: [{ changeType: "MODIFIED", before: { sequence: 1, action: "pay", expectedResult: "old" }, after: { sequence: 1, action: "pay", expectedResult: "approved" } }],
      });
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);
    const service = new ApiTestCaseService("http://api.test");

    const detail = await service.get("TC-1042");

    expect(detail.partialFailure).toBeUndefined();
    expect(detail.testCase.history).toEqual(expect.arrayContaining([
      expect.objectContaining({ version: 2, changes: expect.arrayContaining([expect.objectContaining({ field: "summary" })]) }),
    ]));
    expect(detail.testCase.steps).toEqual([{ id: expect.any(String), precondition: "checkout is open", action: "pay", expectedResult: "approved" }]);
  });

  it("persists an independent precondition for every manual step", async () => {
    let updateBody: Record<string, unknown> | undefined;
    const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/test-cases?search=TC-1042&includeDeleted=false&limit=50")) return json({ items: [listItem()], total: 1, nextCursor: null });
      if (url.endsWith(`/api/test-cases/${CASE_ID}`) && init?.method === "PATCH") {
        updateBody = JSON.parse(String(init.body)) as Record<string, unknown>;
        return json(resource());
      }
      if (url.endsWith(`/api/test-cases/${CASE_ID}`)) return json(resource());
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);
    const service = new ApiTestCaseService("http://api.test");
    const testCase = { ...initialTestManagementState.testCases[0]!, id: CASE_ID as `case_${string}`, version: 2, resourceVersion: 4 };

    await service.update({
      ...testCase,
      expectedResultMode: "CASE",
      steps: [
        { id: "step_1", precondition: "signed in", action: "pay", expectedResult: "approved" },
        { id: "step_2", precondition: "approval returned", action: "open receipt", expectedResult: "receipt shown" },
      ],
    });

    expect(updateBody).toMatchObject({
      precondition: "signed in",
      expectedResultMode: "PER_STEP",
      steps: [
        { sequence: 1, precondition: "signed in", action: "pay", expectedResult: "approved" },
        { sequence: 2, precondition: "approval returned", action: "open receipt", expectedResult: "receipt shown" },
      ],
    });
  });

  it("creates the persisted case once with the completed manual draft", async () => {
    let createBody: Record<string, unknown> | undefined;
    const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      if (String(input).endsWith("/api/test-cases") && init?.method === "POST") {
        createBody = JSON.parse(String(init.body)) as Record<string, unknown>;
        return json(resource());
      }
      throw new Error(`Unexpected request ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetch);
    const service = new ApiTestCaseService("http://api.test");
    const workItem = { ...initialTestManagementState.workItems[0]!, id: WORK_ITEM_ID as `work_item_${string}` };

    await service.create(workItem, {
      summary: "카드 결제가 승인된다",
      priority: "HIGH",
      owner: "김형유",
      labels: ["payment", "smoke"],
      steps: [{ precondition: "결제 가능한 주문", action: "결제한다", expectedResult: "승인된다" }],
    });

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(createBody).toMatchObject({
      summary: "카드 결제가 승인된다",
      precondition: "결제 가능한 주문",
      priority: "HIGH",
      ownerLabel: "김형유",
      labels: ["payment", "smoke"],
      steps: [{ sequence: 1, precondition: "결제 가능한 주문", action: "결제한다", expectedResult: "승인된다" }],
    });
  });

  it("persists same-parent reorder and cross-parent move through the move endpoint", async () => {
    const moveBodies: Array<Record<string, unknown>> = [];
    const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/test-cases?") && init?.method !== "POST") {
        return json({ items: [listItem()], total: 1, nextCursor: null });
      }
      if (url.endsWith(`/api/test-cases/${CASE_ID}`) && init?.method !== "POST") {
        return json(resource());
      }
      if (url.endsWith(`/api/test-cases/${CASE_ID}/move`) && init?.method === "POST") {
        moveBodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return json({
          testCase: {
            ...resource(),
            workItemId: String(moveBodies.at(-1)?.targetWorkItemId),
            sortOrder: Number(moveBodies.at(-1)?.targetSortOrder),
            resourceVersion: 5 + moveBodies.length,
          },
        });
      }
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);
    const service = new ApiTestCaseService("http://api.test");
    const source = {
      ...initialTestManagementState.workItems[0]!,
      id: WORK_ITEM_ID as `work_item_${string}`,
    };
    const target = {
      ...initialTestManagementState.workItems[1]!,
      id: "work_item_00000000000070008000000000000466" as const,
    };

    await service.list({ limit: 50 });
    await service.move("TC-1042", source, 0);
    await service.move("TC-1042", target, 1);

    expect(moveBodies).toEqual([
      expect.objectContaining({
        baseResourceVersion: 4,
        targetWorkItemId: WORK_ITEM_ID,
        targetSortOrder: 0,
      }),
      expect.objectContaining({
        baseResourceVersion: 6,
        targetWorkItemId: target.id,
        targetSortOrder: 1,
      }),
    ]);
  });

  it("reports a partial detail instead of discarding the editable current version", async () => {
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/api/test-cases?search=")) return json({ items: [listItem()], total: 1, nextCursor: null });
      if (url.endsWith(`/api/test-cases/${CASE_ID}`)) return json(resource());
      if (url.endsWith(`/api/test-cases/${CASE_ID}/versions?limit=200`)) return json({ items: [version(1), version(2)], total: 2, nextCursor: null });
      if (url.includes("/versions/diff?")) return json({ code: "UPSTREAM_TIMEOUT", message: "diff unavailable", details: {}, traceId: "trace_diff" }, 503);
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);

    const detail = await new ApiTestCaseService("http://api.test").get("TC-1042");

    expect(detail.testCase.summary).toBe("Card checkout");
    expect(detail.partialFailure).toContain("일부 버전 diff");
  });
});

describe("API-only persistence", () => {
  it("surfaces availability failures without switching to a mutable local fixture", async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", fetch);

    const service = new ApiTestCaseService("http://api.test");

    await expect(service.list({ search: "TC-1042" })).rejects.toThrow("offline");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function listItem() {
  return {
    id: CASE_ID,
    key: "TC-1042",
    workItemId: WORK_ITEM_ID,
    workItemKey: "WORK-482",
    workItemSummary: "Card checkout",
    categoryId: CATEGORY_ID,
    status: "ACTIVE",
    summary: "Card checkout",
    priority: "CRITICAL",
    ownerLabel: "qa",
    labels: ["smoke"],
    version: 2,
    resourceVersion: 4,
    sortOrder: 3,
    deletedAt: null,
    updatedAt: "2026-08-20T02:00:00Z",
  };
}

function resource() {
  return {
    id: CASE_ID,
    key: "TC-1042",
    workspaceId: "workspace_00000000000070008000000000000001",
    workItemId: WORK_ITEM_ID,
    categoryId: CATEGORY_ID,
    status: "ACTIVE",
    version: 2,
    resourceVersion: 4,
    sortOrder: 3,
    deletedAt: null,
    copiedFrom: null,
    createdAt: "2026-08-20T01:00:00Z",
    updatedAt: "2026-08-20T02:00:00Z",
    currentVersion: version(2),
  };
}

function version(value: number) {
  return {
    id: `case_version_${value}`,
    testCaseId: CASE_ID,
    version: value,
    summary: value === 1 ? "Old summary" : "Card checkout",
    precondition: "signed in",
    expectedResultMode: "PER_STEP",
    priority: "CRITICAL",
    ownerLabel: "qa",
    labels: ["smoke"],
    steps: [{ sequence: 1, precondition: "checkout is open", action: "pay", expectedResult: value === 1 ? "old" : "approved" }],
    changeReason: value === 1 ? "Initial creation" : "Expected result updated",
    source: { type: "UI", actorLabel: "qa" },
    createdAt: `2026-08-20T0${value}:00:00Z`,
  };
}
