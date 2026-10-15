import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiWorkspaceService,
} from "./api-workspace-service";
import {
  PRIMARY_WORKSPACE_ID,
  type FolderId,
  type WorkspaceServiceError,
} from "./workspace-service";

const ROOT_ID = "folder_00000000000070008000000000000200" as FolderId;
const CHILD_ID = "folder_00000000000070008000000000000201" as FolderId;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiWorkspaceService", () => {
  it("uses generated workspace, tree, children and audit endpoints", async () => {
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/api/workspace/settings")) {
        return json({
          id: PRIMARY_WORKSPACE_ID,
          teamName: "API QA",
          casePrefix: "TC",
          issuePrefix: "ISSUE",
          defaultEnvironment: "staging",
          generationQuietMinutes: 10,
          version: 1,
          createdAt: "2026-08-20T00:00:00Z",
          updatedAt: "2026-08-20T00:00:00Z",
        });
      }
      if (url.endsWith("/api/folders/tree")) {
        return json(folderTree());
      }
      if (url.includes("/api/folders/children")) {
        return json({
          workspaceId: PRIMARY_WORKSPACE_ID,
          parentId: ROOT_ID,
          generatedAt: "2026-08-20T00:00:00Z",
          items: [folderTree().items[0]?.children[0]],
        });
      }
      if (url.endsWith("/api/audit-logs?limit=8")) {
        return json({ items: [], nextCursor: null, total: 0 });
      }
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);
    const service = new ApiWorkspaceService("http://api.test");

    await expect(service.getWorkspaceSettings(PRIMARY_WORKSPACE_ID)).resolves.toMatchObject({
      teamName: "API QA",
    });
    const roots = await service.getFolderTree(PRIMARY_WORKSPACE_ID);
    expect(roots.items[0]).toMatchObject({ id: ROOT_ID, hasChildren: true, children: [] });
    const children = await service.getFolderTree(PRIMARY_WORKSPACE_ID, ROOT_ID);
    expect(children.items[0]).toMatchObject({ id: CHILD_ID, depth: 1 });
    await expect(service.getAuditHistory(PRIMARY_WORKSPACE_ID, { limit: 8 })).resolves.toEqual({
      items: [],
      nextCursor: null,
      total: 0,
    });

    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it("retains a real-API delete as a session restore candidate", async () => {
    const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/folders/tree")) return json(folderTree());
      if (url.endsWith(`${ROOT_ID}/delete-preview`)) {
        return json({
          folderId: ROOT_ID,
          affectedFolderCount: 2,
          activeTestCaseCount: 3,
          requiresConfirmation: true,
        });
      }
      if (url.includes(`/api/folders/${ROOT_ID}?confirm=true`) && init?.method === "DELETE") {
        return json({
          operation: "SOFT_DELETE_SUBTREE",
          rootFolderId: ROOT_ID,
          affectedFolderIds: [ROOT_ID, CHILD_ID],
          rootVersion: 2,
          auditId: "audit_00000000000070008000000000000900",
          deletionBatchId: "folder_delete_00000000000070008000000000000800",
        });
      }
      if (url.endsWith(`${ROOT_ID}/restore`)) {
        return json({
          operation: "RESTORE_SUBTREE",
          rootFolderId: ROOT_ID,
          affectedFolderIds: [ROOT_ID, CHILD_ID],
          rootVersion: 3,
          auditId: "audit_00000000000070008000000000000901",
        });
      }
      throw new Error(`Unexpected request ${url}`);
    });
    vi.stubGlobal("fetch", fetch);
    const service = new ApiWorkspaceService("http://api.test");
    await service.getFolderTree(PRIMARY_WORKSPACE_ID);

    const deleted = await service.softDeleteFolder(PRIMARY_WORKSPACE_ID, ROOT_ID, {
      baseVersion: 1,
      mode: "SUBTREE",
    });
    const trash = await service.getDeletedFolders(PRIMARY_WORKSPACE_ID);
    expect(trash.items[0]).toMatchObject({
      node: { id: ROOT_ID, version: 2 },
      deletionBatchId: deleted.deletionBatchId,
    });

    await service.restoreFolder(PRIMARY_WORKSPACE_ID, ROOT_ID, {
      baseVersion: 2,
      deletionBatchId: deleted.deletionBatchId!,
      mode: "SUBTREE",
    });
    await expect(service.getDeletedFolders(PRIMARY_WORKSPACE_ID)).resolves.toMatchObject({
      items: [],
    });
  });

  it("does not hide stable API conflicts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json(
          {
            code: "VERSION_CONFLICT",
            message: "stale version",
            details: {},
            traceId: "trace_conflict",
          },
          409,
        ),
      ),
    );
    const service = new ApiWorkspaceService("http://api.test");

    await expect(
      service.updateWorkspaceSettings(PRIMARY_WORKSPACE_ID, { baseVersion: 0, teamName: "X" }),
    ).rejects.toEqual(expect.objectContaining<Partial<WorkspaceServiceError>>({
      code: "VERSION_CONFLICT",
    }));
  });

  it("preserves canonical duplicate-folder codes and details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json(
          {
            code: "FOLDER_NAME_CONFLICT",
            message: "An active sibling already uses that normalized name.",
            details: {
              folderId: ROOT_ID,
              conflictFolderId: CHILD_ID,
            },
            traceId: "trace_duplicate",
          },
          409,
        ),
      ),
    );
    const service = new ApiWorkspaceService("http://api.test");

    await expect(
      service.createFolder(PRIMARY_WORKSPACE_ID, {
        parentId: ROOT_ID,
        name: "Accounts",
        sortOrder: 0,
      }),
    ).rejects.toEqual(expect.objectContaining<Partial<WorkspaceServiceError>>({
      code: "FOLDER_NAME_CONFLICT",
      details: { folderId: ROOT_ID, conflictFolderId: CHILD_ID },
    }));
  });
});

function folderTree() {
  return {
    workspaceId: PRIMARY_WORKSPACE_ID,
    generatedAt: "2026-08-20T00:00:00Z",
    items: [
      {
        id: ROOT_ID,
        parentId: null,
        name: "Product",
        sortOrder: 0,
        depth: 0,
        path: [{ id: ROOT_ID, name: "Product" }],
        directTestCaseCount: 1,
        subtreeTestCaseCount: 3,
        version: 1,
        deletedAt: null,
        children: [
          {
            id: CHILD_ID,
            parentId: ROOT_ID,
            name: "Accounts",
            sortOrder: 0,
            depth: 1,
            path: [
              { id: ROOT_ID, name: "Product" },
              { id: CHILD_ID, name: "Accounts" },
            ],
            directTestCaseCount: 2,
            subtreeTestCaseCount: 2,
            version: 1,
            deletedAt: null,
            children: [],
          },
        ],
      },
    ],
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
