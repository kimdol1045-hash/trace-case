import { describe, expect, it } from "vitest";

import { createInMemoryWorkspaceService } from "./in-memory-workspace-service";
import {
  AI_GENERATED_FOLDER_ID,
  PRIMARY_WORKSPACE_ID,
  type FolderId,
  type FolderDirectoryNode,
  type WorkspaceServiceError,
} from "./workspace-service";

describe("InMemoryWorkspaceService", () => {
  it("loads a directory lazily beyond three levels", async () => {
    const service = createInMemoryWorkspaceService({ latencyMs: 0 });

    const roots = await service.getFolderTree(PRIMARY_WORKSPACE_ID);
    const product = findFolder(roots.items, "folder_product");
    expect(product.children).toEqual([]);
    expect(product.subtreeTestCaseCount).toBe(27);

    const productChildren = await service.getFolderTree(PRIMARY_WORKSPACE_ID, product.id);
    const members = findFolder(productChildren.items, "folder_members");
    const memberChildren = await service.getFolderTree(PRIMARY_WORKSPACE_ID, members.id);
    const accounts = findFolder(memberChildren.items, "folder_accounts");
    const accountChildren = await service.getFolderTree(PRIMARY_WORKSPACE_ID, accounts.id);
    const dormant = findFolder(accountChildren.items, "folder_dormant");

    expect(dormant.depth).toBe(3);
    expect(dormant.path.map((segment) => segment.name)).toEqual([
      "제품 영역",
      "회원",
      "계정 관리",
      "휴면 계정",
    ]);
  });

  it("refetches the canonical topology after moving an entire subtree", async () => {
    const service = createInMemoryWorkspaceService({ latencyMs: 0 });
    const members = findFolder(
      (await service.getFolderTree(PRIMARY_WORKSPACE_ID, "folder_product")).items,
      "folder_members",
    );

    const result = await service.moveFolder(PRIMARY_WORKSPACE_ID, members.id, {
      baseVersion: members.version,
      mode: "SUBTREE",
      targetParentId: "folder_checkout",
      sortOrder: 0,
    });

    expect(result.affectedFolderIds).toEqual(
      expect.arrayContaining(["folder_members", "folder_accounts", "folder_dormant"]),
    );
    expect(
      (await service.getFolderTree(PRIMARY_WORKSPACE_ID, "folder_product")).items.map(
        (folder) => folder.id,
      ),
    ).not.toContain("folder_members");

    const checkoutChildren = await service.getFolderTree(PRIMARY_WORKSPACE_ID, "folder_checkout");
    const movedMembers = findFolder(checkoutChildren.items, "folder_members");
    const movedAccounts = findFolder(
      (await service.getFolderTree(PRIMARY_WORKSPACE_ID, movedMembers.id)).items,
      "folder_accounts",
    );
    expect(movedAccounts.depth).toBe(4);
    expect(movedAccounts.path.map((segment) => segment.id)).toEqual([
      "folder_product",
      "folder_orders",
      "folder_checkout",
      "folder_members",
      "folder_accounts",
    ]);
  });

  it("renames a folder and returns the new path on the next read", async () => {
    const service = createInMemoryWorkspaceService({ latencyMs: 0 });
    const accounts = findFolder(
      (await service.getFolderTree(PRIMARY_WORKSPACE_ID, "folder_members")).items,
      "folder_accounts",
    );

    await service.renameFolder(PRIMARY_WORKSPACE_ID, accounts.id, {
      baseVersion: accounts.version,
      name: "로그인 계정",
    });

    const renamed = findFolder(
      (await service.getFolderTree(PRIMARY_WORKSPACE_ID, "folder_members")).items,
      "folder_accounts",
    );
    expect(renamed.name).toBe("로그인 계정");
    expect(renamed.version).toBe(accounts.version + 1);
    const descendant = findFolder(
      (await service.getFolderTree(PRIMARY_WORKSPACE_ID, renamed.id)).items,
      "folder_dormant",
    );
    expect(descendant.path.at(-2)?.name).toBe("로그인 계정");
  });

  it("reports delete impact, soft-deletes a subtree and restores its batch", async () => {
    const service = createInMemoryWorkspaceService({ latencyMs: 0 });
    const members = findFolder(
      (await service.getFolderTree(PRIMARY_WORKSPACE_ID, "folder_product")).items,
      "folder_members",
    );
    const impact = await service.getFolderImpact(PRIMARY_WORKSPACE_ID, members.id);

    expect(impact).toEqual({
      folderId: "folder_members",
      directTestCaseCount: 4,
      descendantTestCaseCount: 11,
      affectedFolderCount: 4,
    });

    const deleted = await service.softDeleteFolder(PRIMARY_WORKSPACE_ID, members.id, {
      baseVersion: members.version,
      mode: "SUBTREE",
      reason: "정보 구조 정리",
    });
    expect(deleted.deletionBatchId).toMatch(/^folder_delete_/);
    expect(deleted.affectedFolderIds).toHaveLength(4);
    expect(
      (await service.getFolderTree(PRIMARY_WORKSPACE_ID, "folder_product")).items.map(
        (folder) => folder.id,
      ),
    ).not.toContain(members.id);

    const trash = await service.getDeletedFolders(PRIMARY_WORKSPACE_ID);
    expect(trash.items[0]?.node.children[0]?.children[0]?.depth).toBe(3);
    const deletedRoot = trash.items[0];
    expect(deletedRoot).toBeDefined();
    if (!deletedRoot) throw new Error("deleted root missing");

    await service.restoreFolder(PRIMARY_WORKSPACE_ID, deletedRoot.node.id, {
      baseVersion: deletedRoot.node.version,
      deletionBatchId: deletedRoot.deletionBatchId,
      mode: "SUBTREE",
      reason: "잘못된 삭제 복구",
    });
    expect(
      (await service.getFolderTree(PRIMARY_WORKSPACE_ID, "folder_product")).items.map(
        (folder) => folder.id,
      ),
    ).toContain(members.id);
    expect((await service.getDeletedFolders(PRIMARY_WORKSPACE_ID)).items).toEqual([]);
  });

  it("updates workspace defaults with optimistic versioning and writes audit history", async () => {
    const service = createInMemoryWorkspaceService({ latencyMs: 0 });
    const current = await service.getWorkspaceSettings(PRIMARY_WORKSPACE_ID);
    const updated = await service.updateWorkspaceSettings(PRIMARY_WORKSPACE_ID, {
      baseVersion: current.version,
      teamName: "Commerce QA",
      casePrefix: "CASE",
      issuePrefix: "BUG",
      defaultEnvironment: "production",
      generationQuietMinutes: 25,
    });

    expect(updated).toMatchObject({
      teamName: "Commerce QA",
      casePrefix: "CASE",
      issuePrefix: "BUG",
      defaultEnvironment: "production",
      generationQuietMinutes: 25,
      version: current.version + 1,
    });
    const history = await service.getAuditHistory(PRIMARY_WORKSPACE_ID);
    expect(history.items[0]).toMatchObject({
      entityType: "WORKSPACE_SETTINGS",
      action: "UPDATE",
      source: { type: "UI" },
    });
  });

  it("protects the AI system folder and supports deterministic retry tests", async () => {
    const service = createInMemoryWorkspaceService({ latencyMs: 0 });
    const systemFolder = findFolder(
      (await service.getFolderTree(PRIMARY_WORKSPACE_ID)).items,
      AI_GENERATED_FOLDER_ID,
    );

    await expect(
      service.renameFolder(PRIMARY_WORKSPACE_ID, systemFolder.id, {
        baseVersion: systemFolder.version,
        name: "다른 이름",
      }),
    ).rejects.toMatchObject({ code: "SYSTEM_FOLDER_PROTECTED" } satisfies Partial<WorkspaceServiceError>);

    service.failNext("getFolderTree");
    await expect(service.getFolderTree(PRIMARY_WORKSPACE_ID)).rejects.toThrow(
      "deterministic failure",
    );
    await expect(service.getFolderTree(PRIMARY_WORKSPACE_ID)).resolves.toMatchObject({
      workspaceId: PRIMARY_WORKSPACE_ID,
    });
  });
});

function findFolder(items: FolderDirectoryNode[], id: FolderId) {
  const folder = items.find((item) => item.id === id);
  if (!folder) throw new Error(`Folder ${id} was not found`);
  return folder;
}
