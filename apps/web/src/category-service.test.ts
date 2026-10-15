import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiCategoryService } from "./category-service";

const CATEGORY_ID = "folder_00000000000070008000000000000901";
const SYSTEM_CATEGORY_ID = "folder_00000000000070008000000000000101";
const WORK_ITEM_ID = "work_item_00000000000070008000000000000531";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiCategoryService", () => {
  it("loads immutable category counts and all work items outside the case filter query", async () => {
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/api/folders/tree")) return json(folderTree());
      if (url.endsWith("/api/work-items?limit=200&offset=0")) {
        return json({
          items: [
            workItem(),
            ...Array.from({ length: 199 }, (_, index) => ({
              ...workItem(),
              id: `work_item_page_${index}`,
              key: `WORK-${1000 + index}`,
            })),
          ],
          total: 201,
        });
      }
      if (url.endsWith("/api/work-items?limit=200&offset=200")) {
        return json({
          items: [{
            ...workItem(),
            id: "work_item_00000000000070008000000000000532",
            key: "LIVE-301",
          }],
          total: 201,
        });
      }
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);

    const projection = await new ApiCategoryService("http://api.test").loadProjection();

    expect(projection.domains).toEqual([
      expect.objectContaining({
        id: CATEGORY_ID,
        resourceVersion: 4,
        activeTestCaseCount: 17,
        workItemCount: 201,
      }),
    ]);
    expect(projection.domains.some((domain) => domain.id === SYSTEM_CATEGORY_ID)).toBe(false);
    expect(projection.workItems).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: WORK_ITEM_ID, key: "WORK-531", domainId: CATEGORY_ID }),
      expect.objectContaining({ key: "LIVE-301", domainId: CATEGORY_ID }),
    ]));
    expect(projection.workItems).toHaveLength(201);
    expect(fetch.mock.calls.every(([input]) => !String(input).includes("/api/test-cases"))).toBe(true);
  });

  it("persists create, rename and delete through generated category-folder calls", async () => {
    const requests: Array<{ url: string; method: string; body?: unknown }> = [];
    const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      requests.push({
        url,
        method,
        ...(init?.body ? { body: JSON.parse(String(init.body)) as unknown } : {}),
      });
      if (url.endsWith("/api/folders") && method === "POST") {
        return json(mutation("CREATE", 1), 201);
      }
      if (url.endsWith(`/api/folders/${CATEGORY_ID}`) && method === "PATCH") {
        return json(mutation("RENAME", 5));
      }
      if (url.endsWith(`/api/folders/${CATEGORY_ID}/delete-preview`)) {
        return json({ folderId: CATEGORY_ID, affectedFolderCount: 1, activeTestCaseCount: 0, requiresConfirmation: false });
      }
      if (url.endsWith(`/api/folders/${CATEGORY_ID}?confirm=false`) && method === "DELETE") {
        return json(mutation("SOFT_DELETE_SUBTREE", 5));
      }
      throw new Error(`Unexpected request ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetch);
    const service = new ApiCategoryService("http://api.test");
    const category = {
      id: CATEGORY_ID as `folder_${string}`,
      parentId: null,
      name: "Payments",
      sortOrder: 0,
      resourceVersion: 4,
      activeTestCaseCount: 0,
      workItemCount: 0,
    };

    await expect(service.create("Refunds", 2)).resolves.toBe(CATEGORY_ID);
    await service.rename(category, "Card payments");
    await service.delete(category);

    expect(requests.map(({ method, url }) => `${method} ${url}`)).toEqual([
      "POST http://api.test/api/folders",
      `PATCH http://api.test/api/folders/${CATEGORY_ID}`,
      `GET http://api.test/api/folders/${CATEGORY_ID}/delete-preview`,
      `DELETE http://api.test/api/folders/${CATEGORY_ID}?confirm=false`,
    ]);
    expect(requests[0]?.body).toMatchObject({ parentId: null, name: "Refunds", sortOrder: 2 });
    expect(requests[1]?.body).toMatchObject({ baseVersion: 4, name: "Card payments" });
    expect(requests[3]?.body).toMatchObject({ baseVersion: 4, mode: "SUBTREE" });
  });

  it("surfaces production API failure instead of returning a local projection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));

    await expect(
      new ApiCategoryService("http://api.test").loadProjection(),
    ).rejects.toThrow("offline");
  });
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function folderTree() {
  return {
    workspaceId: "workspace_00000000000070008000000000000100",
    generatedAt: "2026-08-24T01:00:00Z",
    items: [
      {
        id: CATEGORY_ID,
        parentId: null,
        name: "Payments",
        sortOrder: 0,
        depth: 0,
        path: [{ id: CATEGORY_ID, name: "Payments" }],
        directTestCaseCount: 17,
        subtreeTestCaseCount: 17,
        version: 4,
        deletedAt: null,
        children: [],
      },
      {
        id: SYSTEM_CATEGORY_ID,
        parentId: null,
        name: "AI 생성함",
        sortOrder: 2_147_483_647,
        depth: 0,
        path: [{ id: SYSTEM_CATEGORY_ID, name: "AI 생성함" }],
        directTestCaseCount: 0,
        subtreeTestCaseCount: 0,
        version: 1,
        deletedAt: null,
        children: [],
      },
    ],
  };
}

function workItem() {
  return {
    id: WORK_ITEM_ID,
    key: "WORK-531",
    categoryId: CATEGORY_ID,
    summary: "Card payment reliability",
    description: "Checkout coverage",
    ownerLabel: "QA Kim",
    labels: ["payment"],
    resourceVersion: 3,
    createdAt: "2026-08-24T01:00:00Z",
    updatedAt: "2026-08-24T02:00:00Z",
  };
}

function mutation(operation: "CREATE" | "RENAME" | "SOFT_DELETE_SUBTREE", rootVersion: number) {
  return {
    operation,
    rootFolderId: CATEGORY_ID,
    affectedFolderIds: [CATEGORY_ID],
    rootVersion,
    auditId: "audit_00000000000070008000000000000999",
  };
}
