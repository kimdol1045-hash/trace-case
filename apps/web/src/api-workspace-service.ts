import {
  ApiClientError,
  createApiClient,
  type AuditPageResponse as ApiAuditPageResponse,
  type FolderMutationResponse as ApiFolderMutationResponse,
  type FolderTreeResponse as ApiFolderTreeResponse,
} from "@tc/api-client";

import {
  PRIMARY_WORKSPACE_ID,
  WorkspaceServiceError,
  type AuditPageResponse,
  type CreateFolderRequest,
  type DeletedFolderItem,
  type DeletedFolderTreeResponse,
  type FolderDirectoryNode,
  type FolderDirectoryResponse,
  type FolderId,
  type FolderImpact,
  type FolderMutationResult,
  type MoveFolderRequest,
  type RenameFolderRequest,
  type RestoreFolderRequest,
  type SoftDeleteFolderRequest,
  type UpdateWorkspaceSettingsRequest,
  type WorkspaceId,
  type WorkspaceService,
  type WorkspaceServiceErrorCode,
  type WorkspaceSettings,
} from "./workspace-service";

type ApiFolderNode = ApiFolderTreeResponse["items"][number];
type ApiClient = ReturnType<typeof createApiClient>;

const SERVICE_ERROR_CODES: ReadonlySet<string> = new Set<WorkspaceServiceErrorCode>([
  "AUDIT_CURSOR_INVALID",
  "DATA_CONFLICT",
  "FOLDER_ALIAS_CONFLICT",
  "FOLDER_ALIAS_TARGET_DELETED",
  "FOLDER_CONTENT_WARNING",
  "FOLDER_CYCLE",
  "FOLDER_NAME_CONFLICT",
  "FOLDER_NOT_EMPTY",
  "FOLDER_PARENT_DELETED",
  "FOLDER_RESTORE_CONFLICT",
  "IDEMPOTENCY_CONFLICT",
  "KEY_ALLOCATION_FAILED",
  "RESOURCE_NOT_FOUND",
  "SYSTEM_FOLDER_PROTECTED",
  "VALIDATION_FAILED",
  "VERSION_CONFLICT",
]);

/**
 * Maps the generated OpenAPI client to the frontend port. The API does not yet
 * expose a deleted-folder collection, so restore candidates are retained for
 * deletes performed in this browser session while the durable restore command
 * still goes through the real endpoint.
 */
export class ApiWorkspaceService implements WorkspaceService {
  private readonly api: ApiClient;
  private readonly nodes = new Map<FolderId, FolderDirectoryNode>();
  private readonly childrenByParent = new Map<FolderId | null, FolderId[]>();
  private readonly deletedFolders = new Map<FolderId, DeletedFolderItem>();

  constructor(baseUrl: string) {
    this.api = createApiClient(baseUrl);
  }

  async getWorkspaceSettings(workspaceId: WorkspaceId): Promise<WorkspaceSettings> {
    assertPrimaryWorkspace(workspaceId);
    return this.translate(() => this.api.getWorkspaceSettings()) as Promise<WorkspaceSettings>;
  }

  async updateWorkspaceSettings(
    workspaceId: WorkspaceId,
    request: UpdateWorkspaceSettingsRequest,
  ): Promise<WorkspaceSettings> {
    assertPrimaryWorkspace(workspaceId);
    return this.translate(() =>
      this.api.updateWorkspaceSettings(request, mutationOptions("workspace-settings")),
    ) as Promise<WorkspaceSettings>;
  }

  async getFolderTree(
    workspaceId: WorkspaceId,
    parentId: FolderId | null = null,
  ): Promise<FolderDirectoryResponse> {
    assertPrimaryWorkspace(workspaceId);
    if (parentId === null) {
      const response = await this.translate(() => this.api.getFolderTree());
      this.indexTree(response.items);
      return {
        workspaceId: response.workspaceId as WorkspaceId,
        generatedAt: response.generatedAt,
        items: response.items.map((node) => this.toLazyNode(node)),
      };
    }

    if (!this.nodes.has(parentId)) {
      const tree = await this.translate(() => this.api.getFolderTree());
      this.indexTree(tree.items);
    }
    const response = await this.translate(() => this.api.getFolderChildren(parentId));
    for (const item of response.items) this.indexNode(item);
    return {
      workspaceId: response.workspaceId as WorkspaceId,
      generatedAt: response.generatedAt,
      items: response.items.map((node) => this.toLazyNode(node)),
    };
  }

  async getDeletedFolders(workspaceId: WorkspaceId): Promise<DeletedFolderTreeResponse> {
    assertPrimaryWorkspace(workspaceId);
    return {
      workspaceId,
      generatedAt: new Date().toISOString(),
      items: [...this.deletedFolders.values()].map((item) => structuredClone(item)),
    };
  }

  async getFolderImpact(
    workspaceId: WorkspaceId,
    folderId: FolderId,
  ): Promise<FolderImpact> {
    assertPrimaryWorkspace(workspaceId);
    const preview = await this.translate(() => this.api.previewFolderDelete(folderId));
    const direct = this.nodes.get(folderId)?.directTestCaseCount ?? 0;
    return {
      folderId,
      directTestCaseCount: direct,
      descendantTestCaseCount: Math.max(0, preview.activeTestCaseCount - direct),
      affectedFolderCount: preview.affectedFolderCount,
    };
  }

  async createFolder(
    workspaceId: WorkspaceId,
    request: CreateFolderRequest,
  ): Promise<FolderMutationResult> {
    assertPrimaryWorkspace(workspaceId);
    const result = await this.translate(() =>
      this.api.createFolder(request, mutationOptions("folder-create")),
    );
    this.clearTopology();
    return mapMutation(result);
  }

  async renameFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: RenameFolderRequest,
  ): Promise<FolderMutationResult> {
    assertPrimaryWorkspace(workspaceId);
    const result = await this.translate(() =>
      this.api.renameFolder(folderId, request, mutationOptions("folder-rename")),
    );
    this.clearTopology();
    return mapMutation(result);
  }

  async moveFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: MoveFolderRequest,
  ): Promise<FolderMutationResult> {
    assertPrimaryWorkspace(workspaceId);
    const existing = this.nodes.get(folderId);
    const action = existing?.parentId === request.targetParentId
      ? () =>
          this.api.sortFolder(
            folderId,
            { baseVersion: request.baseVersion, sortOrder: request.sortOrder },
            mutationOptions("folder-sort"),
          )
      : () => this.api.moveFolder(folderId, request, mutationOptions("folder-move"));
    const result = await this.translate(action);
    this.clearTopology();
    return mapMutation(result);
  }

  async softDeleteFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: SoftDeleteFolderRequest,
  ): Promise<FolderMutationResult> {
    assertPrimaryWorkspace(workspaceId);
    const folder = this.nodes.get(folderId);
    const preview = await this.translate(() => this.api.previewFolderDelete(folderId));
    const result = await this.translate(() =>
      this.api.softDeleteFolder(
        folderId,
        request,
        preview.requiresConfirmation,
        mutationOptions("folder-delete"),
      ),
    );
    if (folder && result.deletionBatchId) {
      this.deletedFolders.set(folderId, {
        node: {
          ...structuredClone(folder),
          version: result.rootVersion,
          deletedAt: new Date().toISOString(),
          children: this.deletedChildren(folderId),
        },
        deletionBatchId: result.deletionBatchId as DeletedFolderItem["deletionBatchId"],
      });
    }
    this.clearTopology();
    return mapMutation(result);
  }

  async restoreFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: RestoreFolderRequest,
  ): Promise<FolderMutationResult> {
    assertPrimaryWorkspace(workspaceId);
    const result = await this.translate(() =>
      this.api.restoreFolder(folderId, request, mutationOptions("folder-restore")),
    );
    this.deletedFolders.delete(folderId);
    this.clearTopology();
    return mapMutation(result);
  }

  async getAuditHistory(
    workspaceId: WorkspaceId,
    query: { limit?: number; cursor?: string } = {},
  ): Promise<AuditPageResponse> {
    assertPrimaryWorkspace(workspaceId);
    const result = await this.translate(() => this.api.listAuditLogs(query));
    return result as ApiAuditPageResponse as AuditPageResponse;
  }

  private indexTree(items: ApiFolderNode[]) {
    this.clearTopology();
    const visit = (node: ApiFolderNode) => {
      this.indexNode(node);
      this.childrenByParent.set(
        node.id as FolderId,
        node.children.map((child) => child.id as FolderId),
      );
      for (const child of node.children) visit(child);
    };
    this.childrenByParent.set(null, items.map((node) => node.id as FolderId));
    for (const item of items) visit(item);
  }

  private indexNode(node: ApiFolderNode) {
    const folder = mapFolderNode(node, node.children.length > 0);
    this.nodes.set(folder.id, folder);
  }

  private toLazyNode(node: ApiFolderNode): FolderDirectoryNode {
    const mapped = this.nodes.get(node.id as FolderId) ?? mapFolderNode(node, false);
    return {
      ...structuredClone(mapped),
      hasChildren: (this.childrenByParent.get(mapped.id)?.length ?? 0) > 0,
      children: [],
    };
  }

  private deletedChildren(parentId: FolderId): FolderDirectoryNode[] {
    return (this.childrenByParent.get(parentId) ?? [])
      .map((id) => this.nodes.get(id))
      .filter((node): node is FolderDirectoryNode => Boolean(node))
      .map((node) => ({
        ...structuredClone(node),
        deletedAt: new Date().toISOString(),
        children: this.deletedChildren(node.id),
      }));
  }

  private clearTopology() {
    this.nodes.clear();
    this.childrenByParent.clear();
  }

  private async translate<Result>(action: () => Promise<Result>): Promise<Result> {
    try {
      return await action();
    } catch (error) {
      if (error instanceof ApiClientError) {
        const code = error.response?.code;
        if (code && SERVICE_ERROR_CODES.has(code)) {
          throw new WorkspaceServiceError(
            code as WorkspaceServiceErrorCode,
            error.response?.message ?? error.message,
            error.response?.details,
          );
        }
      }
      throw error;
    }
  }
}

export function createDefaultWorkspaceService() {
  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
  return new ApiWorkspaceService(baseUrl);
}

function mapFolderNode(node: ApiFolderNode, hasChildren: boolean): FolderDirectoryNode {
  return {
    ...node,
    id: node.id as FolderId,
    parentId: node.parentId as FolderId | null,
    path: node.path.map((segment) => ({ ...segment, id: segment.id as FolderId })),
    hasChildren,
    children: node.children.map((child) => mapFolderNode(child, child.children.length > 0)),
  };
}

function mapMutation(result: ApiFolderMutationResponse): FolderMutationResult {
  return {
    ...result,
    operation: result.operation,
    rootFolderId: result.rootFolderId as FolderId,
    affectedFolderIds: result.affectedFolderIds as FolderId[],
    auditId: result.auditId as FolderMutationResult["auditId"],
    deletionBatchId: result.deletionBatchId as FolderMutationResult["deletionBatchId"],
  };
}

function mutationOptions(operation: string) {
  const randomPart = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return {
    actorLabel: "Test Library UI",
    idempotencyKey: `${operation}-${randomPart}`,
  };
}

function assertPrimaryWorkspace(workspaceId: WorkspaceId) {
  if (workspaceId !== PRIMARY_WORKSPACE_ID) {
    throw new WorkspaceServiceError("NOT_FOUND", "워크스페이스를 찾을 수 없습니다.");
  }
}
