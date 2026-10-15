import {
  AI_GENERATED_FOLDER_ID,
  PRIMARY_WORKSPACE_ID,
  WorkspaceServiceError,
  isProtectedSystemFolder,
  type AuditEntry,
  type AuditHistoryQuery,
  type AuditId,
  type AuditPageResponse,
  type CreateFolderRequest,
  type DeletedFolderTreeResponse,
  type DeletionBatchId,
  type FolderId,
  type FolderDirectoryNode,
  type FolderDirectoryResponse,
  type FolderImpact,
  type FolderMutationOperation,
  type FolderMutationResult,
  type JsonValue,
  type MoveFolderRequest,
  type RenameFolderRequest,
  type RestoreFolderRequest,
  type SoftDeleteFolderRequest,
  type UpdateWorkspaceSettingsRequest,
  type WorkspaceId,
  type WorkspaceService,
  type WorkspaceSettings,
} from "./workspace-service";

type OperationName = keyof WorkspaceService;

interface StoredFolder {
  id: FolderId;
  parentId: FolderId | null;
  name: string;
  sortOrder: number;
  directTestCaseCount: number;
  version: number;
  deletedAt: string | null;
  deletionBatchId?: DeletionBatchId;
}

export interface InMemoryWorkspaceServiceOptions {
  latencyMs?: number;
  failFirst?: Partial<Record<OperationName, number>>;
}

const INITIAL_FOLDERS: StoredFolder[] = [
  {
    id: "folder_product",
    parentId: null,
    name: "제품 영역",
    sortOrder: 0,
    directTestCaseCount: 3,
    version: 1,
    deletedAt: null,
  },
  {
    id: "folder_members",
    parentId: "folder_product",
    name: "회원",
    sortOrder: 0,
    directTestCaseCount: 4,
    version: 1,
    deletedAt: null,
  },
  {
    id: "folder_accounts",
    parentId: "folder_members",
    name: "계정 관리",
    sortOrder: 0,
    directTestCaseCount: 6,
    version: 2,
    deletedAt: null,
  },
  {
    id: "folder_dormant",
    parentId: "folder_accounts",
    name: "휴면 계정",
    sortOrder: 0,
    directTestCaseCount: 2,
    version: 1,
    deletedAt: null,
  },
  {
    id: "folder_profile",
    parentId: "folder_members",
    name: "프로필",
    sortOrder: 1,
    directTestCaseCount: 3,
    version: 1,
    deletedAt: null,
  },
  {
    id: "folder_orders",
    parentId: "folder_product",
    name: "주문",
    sortOrder: 1,
    directTestCaseCount: 5,
    version: 1,
    deletedAt: null,
  },
  {
    id: "folder_checkout",
    parentId: "folder_orders",
    name: "결제",
    sortOrder: 0,
    directTestCaseCount: 4,
    version: 1,
    deletedAt: null,
  },
  {
    id: AI_GENERATED_FOLDER_ID,
    parentId: null,
    name: "AI 생성함",
    sortOrder: 1,
    directTestCaseCount: 8,
    version: 1,
    deletedAt: null,
  },
  {
    id: "folder_empty",
    parentId: null,
    name: "정리 대기",
    sortOrder: 2,
    directTestCaseCount: 0,
    version: 1,
    deletedAt: null,
  },
];

const INITIAL_AUDIT: AuditEntry[] = [
  {
    id: "audit_seed_ai",
    workspaceId: PRIMARY_WORKSPACE_ID,
    entityType: "FOLDER",
    entityId: AI_GENERATED_FOLDER_ID,
    action: "SYSTEM_FOLDER_CREATED",
    before: null,
    after: { name: "AI 생성함", protected: true },
    source: { type: "SYSTEM", actorLabel: "generation worker" },
    occurredAt: "2026-08-20T03:02:00.000Z",
    traceId: "trace_seed_ai",
  },
  {
    id: "audit_seed_workspace",
    workspaceId: PRIMARY_WORKSPACE_ID,
    entityType: "WORKSPACE_SETTINGS",
    entityId: PRIMARY_WORKSPACE_ID,
    action: "UPDATE",
    before: { defaultEnvironment: "local" },
    after: { defaultEnvironment: "staging" },
    source: { type: "UI", actorLabel: "QA Platform Team" },
    reason: "Sprint 1 기본 환경 정렬",
    occurredAt: "2026-08-20T03:01:00.000Z",
    traceId: "trace_seed_workspace",
  },
];

export class InMemoryWorkspaceService implements WorkspaceService {
  private workspace: WorkspaceSettings = {
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

  private readonly folders = new Map<FolderId, StoredFolder>(
    INITIAL_FOLDERS.map((folder) => [folder.id, { ...folder }]),
  );
  private readonly audit: AuditEntry[] = INITIAL_AUDIT.map((entry) => structuredClone(entry));
  private readonly failures: Partial<Record<OperationName, number>>;
  private readonly latencyMs: number;
  private sequence = 10;

  constructor(options: InMemoryWorkspaceServiceOptions = {}) {
    this.latencyMs = options.latencyMs ?? 140;
    this.failures = { ...options.failFirst };
  }

  failNext(operation: OperationName, count = 1) {
    this.failures[operation] = count;
  }

  async getWorkspaceSettings(workspaceId: WorkspaceId) {
    await this.before("getWorkspaceSettings");
    this.assertWorkspace(workspaceId);
    return structuredClone(this.workspace);
  }

  async updateWorkspaceSettings(
    workspaceId: WorkspaceId,
    request: UpdateWorkspaceSettingsRequest,
  ) {
    await this.before("updateWorkspaceSettings");
    this.assertWorkspace(workspaceId);
    if (request.baseVersion !== this.workspace.version) {
      throw new WorkspaceServiceError(
        "VERSION_CONFLICT",
        "워크스페이스 설정이 로드된 뒤 변경되었습니다.",
      );
    }
    this.validateWorkspaceUpdate(request);
    const before = this.workspaceSnapshot();
    const { baseVersion: _baseVersion, ...changes } = request;
    void _baseVersion;
    this.workspace = {
      ...this.workspace,
      ...changes,
      version: this.workspace.version + 1,
      updatedAt: this.nextTime(),
    };
    this.addAudit("WORKSPACE_SETTINGS", workspaceId, "UPDATE", before, this.workspaceSnapshot());
    return structuredClone(this.workspace);
  }

  async getFolderTree(workspaceId: WorkspaceId, parentId: FolderId | null = null) {
    await this.before("getFolderTree");
    this.assertWorkspace(workspaceId);
    if (parentId !== null) {
      this.requireFolder(parentId);
    }
    const items = this.childrenOf(parentId, false).map((folder) => this.toNode(folder, false));
    return this.treeResponse(items);
  }

  async getDeletedFolders(workspaceId: WorkspaceId) {
    await this.before("getDeletedFolders");
    this.assertWorkspace(workspaceId);
    const deletedRoots = [...this.folders.values()]
      .filter((folder) => {
        if (folder.deletedAt === null) return false;
        if (folder.parentId === null) return true;
        const parent = this.folders.get(folder.parentId);
        return !parent || parent.deletionBatchId !== folder.deletionBatchId;
      })
      .sort(compareFolders)
      .map((folder) => ({
        node: this.toNode(folder, true, true),
        deletionBatchId: folder.deletionBatchId as DeletionBatchId,
      }));
    return {
      workspaceId: this.workspace.id,
      generatedAt: this.nextTime(false),
      items: deletedRoots,
    } satisfies DeletedFolderTreeResponse;
  }

  async getFolderImpact(workspaceId: WorkspaceId, folderId: FolderId) {
    await this.before("getFolderImpact");
    this.assertWorkspace(workspaceId);
    const folder = this.requireFolder(folderId);
    const descendants = this.descendantsOf(folderId).filter((item) => item.deletedAt === null);
    return {
      folderId,
      directTestCaseCount: folder.directTestCaseCount,
      descendantTestCaseCount: descendants.reduce(
        (sum, item) => sum + item.directTestCaseCount,
        0,
      ),
      affectedFolderCount: descendants.length + 1,
    } satisfies FolderImpact;
  }

  async createFolder(workspaceId: WorkspaceId, request: CreateFolderRequest) {
    await this.before("createFolder");
    this.assertWorkspace(workspaceId);
    this.validateName(request.name);
    if (request.parentId !== null) {
      const parent = this.requireActiveFolder(request.parentId);
      this.assertMutable(parent.id);
    }
    const id = `folder_created_${String(++this.sequence).padStart(3, "0")}` as FolderId;
    const siblings = this.childrenOf(request.parentId, false);
    const sortOrder = Math.max(0, Math.min(request.sortOrder ?? siblings.length, siblings.length));
    const folder: StoredFolder = {
      id,
      parentId: request.parentId,
      name: request.name.trim(),
      sortOrder,
      directTestCaseCount: 0,
      version: 1,
      deletedAt: null,
    };
    this.folders.set(id, folder);
    this.normalizeSiblings(request.parentId, id, sortOrder);
    const auditId = this.addAudit("FOLDER", id, "CREATE", null, this.folderSnapshot(folder));
    return this.mutationResult("CREATE", folder, [id], auditId);
  }

  async renameFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: RenameFolderRequest,
  ) {
    await this.before("renameFolder");
    this.assertWorkspace(workspaceId);
    const folder = this.requireActiveFolder(folderId);
    this.assertMutable(folderId);
    this.assertVersion(folder, request.baseVersion);
    this.validateName(request.name);
    const before = this.folderSnapshot(folder);
    folder.name = request.name.trim();
    folder.version += 1;
    const affected = [folderId, ...this.descendantsOf(folderId).map((item) => item.id)];
    const auditId = this.addAudit("FOLDER", folderId, "RENAME", before, this.folderSnapshot(folder));
    return this.mutationResult("RENAME", folder, affected, auditId);
  }

  async moveFolder(workspaceId: WorkspaceId, folderId: FolderId, request: MoveFolderRequest) {
    await this.before("moveFolder");
    this.assertWorkspace(workspaceId);
    const folder = this.requireActiveFolder(folderId);
    this.assertMutable(folderId);
    this.assertVersion(folder, request.baseVersion);
    if (request.targetParentId !== null) {
      this.requireActiveFolder(request.targetParentId);
      this.assertMutable(request.targetParentId);
    }
    const descendantIds = new Set(this.descendantsOf(folderId).map((item) => item.id));
    if (request.targetParentId === folderId || (request.targetParentId && descendantIds.has(request.targetParentId))) {
      throw new WorkspaceServiceError(
        "FOLDER_CYCLE",
        "폴더를 자기 자신의 하위 경로로 이동할 수 없습니다.",
      );
    }
    const before = this.folderSnapshot(folder);
    const previousParentId = folder.parentId;
    folder.parentId = request.targetParentId;
    folder.version += 1;
    this.normalizeSiblings(previousParentId);
    this.normalizeSiblings(request.targetParentId, folderId, request.sortOrder);
    const affected = [folderId, ...descendantIds];
    const auditId = this.addAudit(
      "FOLDER",
      folderId,
      "MOVE_SUBTREE",
      before,
      this.folderSnapshot(folder),
    );
    return this.mutationResult("MOVE_SUBTREE", folder, affected, auditId);
  }

  async softDeleteFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: SoftDeleteFolderRequest,
  ) {
    await this.before("softDeleteFolder");
    this.assertWorkspace(workspaceId);
    const folder = this.requireActiveFolder(folderId);
    this.assertMutable(folderId);
    this.assertVersion(folder, request.baseVersion);
    const before = this.folderSnapshot(folder);
    const deletedAt = this.nextTime();
    const batch = `folder_delete_${String(this.sequence).padStart(3, "0")}` as DeletionBatchId;
    const affectedFolders = [folder, ...this.descendantsOf(folderId).filter((item) => item.deletedAt === null)];
    for (const item of affectedFolders) {
      item.deletedAt = deletedAt;
      item.deletionBatchId = batch;
      item.version += 1;
    }
    this.normalizeSiblings(folder.parentId);
    const auditId = this.addAudit(
      "FOLDER",
      folderId,
      "SOFT_DELETE_SUBTREE",
      before,
      this.folderSnapshot(folder),
      request.reason,
    );
    return this.mutationResult(
      "SOFT_DELETE_SUBTREE",
      folder,
      affectedFolders.map((item) => item.id),
      auditId,
      batch,
    );
  }

  async restoreFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: RestoreFolderRequest,
  ) {
    await this.before("restoreFolder");
    this.assertWorkspace(workspaceId);
    const folder = this.requireFolder(folderId);
    this.assertMutable(folderId);
    this.assertVersion(folder, request.baseVersion);
    if (folder.deletedAt === null || folder.deletionBatchId !== request.deletionBatchId) {
      throw new WorkspaceServiceError("VALIDATION_ERROR", "복원 배치 정보가 일치하지 않습니다.");
    }
    const before = this.folderSnapshot(folder);
    const batch = folder.deletionBatchId;
    const affectedFolders = [...this.folders.values()].filter(
      (item) => item.deletionBatchId === batch,
    );
    if (request.targetParentId !== undefined) {
      if (request.targetParentId !== null) {
        this.requireActiveFolder(request.targetParentId);
        this.assertMutable(request.targetParentId);
      }
      folder.parentId = request.targetParentId;
    } else if (folder.parentId && this.requireFolder(folder.parentId).deletedAt !== null) {
      folder.parentId = null;
    }
    for (const item of affectedFolders) {
      item.deletedAt = null;
      delete item.deletionBatchId;
      item.version += 1;
    }
    this.normalizeSiblings(folder.parentId);
    const auditId = this.addAudit(
      "FOLDER",
      folderId,
      "RESTORE_SUBTREE",
      before,
      this.folderSnapshot(folder),
      request.reason,
    );
    return this.mutationResult(
      "RESTORE_SUBTREE",
      folder,
      affectedFolders.map((item) => item.id),
      auditId,
      batch,
    );
  }

  async getAuditHistory(workspaceId: WorkspaceId, query: AuditHistoryQuery = {}) {
    await this.before("getAuditHistory");
    this.assertWorkspace(workspaceId);
    const offset = query.cursor ? Number.parseInt(query.cursor, 10) : 0;
    const limit = Math.min(Math.max(query.limit ?? 20, 1), 200);
    const sorted = [...this.audit].sort((left, right) => right.occurredAt.localeCompare(left.occurredAt));
    const items = sorted.slice(offset, offset + limit).map((entry) => structuredClone(entry));
    return {
      items,
      nextCursor: offset + limit < sorted.length ? String(offset + limit) : null,
      total: sorted.length,
    } satisfies AuditPageResponse;
  }

  private async before(operation: OperationName) {
    if (this.latencyMs > 0) {
      await new Promise<void>((resolve) => {
        globalThis.setTimeout(resolve, this.latencyMs);
      });
    }
    const remaining = this.failures[operation] ?? 0;
    if (remaining > 0) {
      this.failures[operation] = remaining - 1;
      throw new Error(`${operation} deterministic failure`);
    }
  }

  private assertWorkspace(workspaceId: WorkspaceId) {
    if (workspaceId !== this.workspace.id) {
      throw new WorkspaceServiceError("NOT_FOUND", "워크스페이스를 찾을 수 없습니다.");
    }
  }

  private requireFolder(folderId: FolderId) {
    const folder = this.folders.get(folderId);
    if (!folder) {
      throw new WorkspaceServiceError("NOT_FOUND", "폴더를 찾을 수 없습니다.");
    }
    return folder;
  }

  private requireActiveFolder(folderId: FolderId) {
    const folder = this.requireFolder(folderId);
    if (folder.deletedAt !== null) {
      throw new WorkspaceServiceError("NOT_FOUND", "활성 폴더를 찾을 수 없습니다.");
    }
    return folder;
  }

  private assertMutable(folderId: FolderId) {
    if (isProtectedSystemFolder(folderId)) {
      throw new WorkspaceServiceError(
        "SYSTEM_FOLDER_PROTECTED",
        "AI 생성함은 시스템 폴더이므로 이름 변경, 이동, 삭제할 수 없습니다.",
      );
    }
  }

  private assertVersion(folder: StoredFolder, baseVersion: number) {
    if (folder.version !== baseVersion) {
      throw new WorkspaceServiceError(
        "VERSION_CONFLICT",
        "폴더가 로드된 뒤 변경되었습니다. 새로고침 후 다시 시도해 주세요.",
      );
    }
  }

  private validateName(name: string) {
    if (!name.trim() || name.length > 200) {
      throw new WorkspaceServiceError("VALIDATION_ERROR", "폴더 이름을 확인해 주세요.");
    }
  }

  private validateWorkspaceUpdate(request: UpdateWorkspaceSettingsRequest) {
    for (const prefix of [request.casePrefix, request.issuePrefix]) {
      if (prefix !== undefined && !/^[A-Z][A-Z0-9]{1,15}$/.test(prefix)) {
        throw new WorkspaceServiceError(
          "VALIDATION_ERROR",
          "Prefix는 2~16자의 영문 대문자와 숫자로 입력해 주세요.",
        );
      }
    }
    if (request.teamName !== undefined && (!request.teamName.trim() || request.teamName.length > 200)) {
      throw new WorkspaceServiceError("VALIDATION_ERROR", "팀 이름을 확인해 주세요.");
    }
    if (request.defaultEnvironment !== undefined && !request.defaultEnvironment.trim()) {
      throw new WorkspaceServiceError("VALIDATION_ERROR", "기본 환경을 입력해 주세요.");
    }
    if (
      request.generationQuietMinutes !== undefined &&
      (!Number.isInteger(request.generationQuietMinutes) ||
        request.generationQuietMinutes < 0 ||
        request.generationQuietMinutes > 1440)
    ) {
      throw new WorkspaceServiceError("VALIDATION_ERROR", "생성 대기 시간은 0~1440분입니다.");
    }
  }

  private childrenOf(parentId: FolderId | null, deleted: boolean) {
    return [...this.folders.values()]
      .filter((folder) => folder.parentId === parentId && (folder.deletedAt !== null) === deleted)
      .sort(compareFolders);
  }

  private descendantsOf(folderId: FolderId) {
    const result: StoredFolder[] = [];
    const visit = (parentId: FolderId) => {
      const children = [...this.folders.values()].filter((folder) => folder.parentId === parentId);
      for (const child of children) {
        result.push(child);
        visit(child.id);
      }
    };
    visit(folderId);
    return result;
  }

  private toNode(folder: StoredFolder, deleted: boolean, recursive = false): FolderDirectoryNode {
    const children = recursive
      ? this.childrenOf(folder.id, deleted).map((child) => this.toNode(child, deleted, true))
      : [];
    const pathFolders: StoredFolder[] = [];
    let cursor: StoredFolder | undefined = folder;
    const seen = new Set<FolderId>();
    while (cursor) {
      if (seen.has(cursor.id)) break;
      seen.add(cursor.id);
      pathFolders.unshift(cursor);
      cursor = cursor.parentId ? this.folders.get(cursor.parentId) : undefined;
    }
    const subtreeTestCaseCount = [folder, ...this.descendantsOf(folder.id)]
      .filter((item) => (item.deletedAt !== null) === deleted)
      .reduce((sum, item) => sum + item.directTestCaseCount, 0);
    return {
      id: folder.id,
      parentId: folder.parentId,
      name: folder.name,
      sortOrder: folder.sortOrder,
      depth: pathFolders.length - 1,
      path: pathFolders.map((item) => ({ id: item.id, name: item.name })),
      directTestCaseCount: folder.directTestCaseCount,
      subtreeTestCaseCount,
      version: folder.version,
      deletedAt: folder.deletedAt,
      hasChildren: this.childrenOf(folder.id, deleted).length > 0,
      children,
    };
  }

  private normalizeSiblings(
    parentId: FolderId | null,
    prioritizedId?: FolderId,
    requestedOrder?: number,
  ) {
    const siblings = this.childrenOf(parentId, false).filter((folder) => folder.id !== prioritizedId);
    if (prioritizedId) {
      const prioritized = this.requireFolder(prioritizedId);
      const index = Math.max(0, Math.min(requestedOrder ?? siblings.length, siblings.length));
      siblings.splice(index, 0, prioritized);
    }
    siblings.forEach((folder, index) => {
      folder.sortOrder = index;
    });
  }

  private treeResponse(items: FolderDirectoryNode[]): FolderDirectoryResponse {
    return {
      workspaceId: this.workspace.id,
      generatedAt: this.nextTime(false),
      items,
    };
  }

  private mutationResult(
    operation: FolderMutationOperation,
    root: StoredFolder,
    affectedFolderIds: FolderId[],
    auditId: AuditId,
    deletionBatchId?: DeletionBatchId,
  ): FolderMutationResult {
    return {
      operation,
      rootFolderId: root.id,
      affectedFolderIds,
      rootVersion: root.version,
      auditId,
      ...(deletionBatchId ? { deletionBatchId } : {}),
    };
  }

  private addAudit(
    entityType: string,
    entityId: string,
    action: string,
    before: Record<string, JsonValue> | null,
    after: Record<string, JsonValue> | null,
    reason?: string,
  ) {
    const suffix = String(++this.sequence).padStart(3, "0");
    const id = `audit_memory_${suffix}` as AuditId;
    const entry: AuditEntry = {
      id,
      workspaceId: this.workspace.id,
      entityType,
      entityId,
      action,
      before,
      after,
      source: { type: "UI", actorLabel: this.workspace.teamName },
      ...(reason ? { reason } : {}),
      occurredAt: this.nextTime(),
      traceId: `trace_memory_${suffix}`,
    };
    this.audit.push(entry);
    return id;
  }

  private folderSnapshot(folder: StoredFolder): Record<string, JsonValue> {
    return {
      id: folder.id,
      parentId: folder.parentId,
      name: folder.name,
      sortOrder: folder.sortOrder,
      version: folder.version,
      deletedAt: folder.deletedAt,
    };
  }

  private workspaceSnapshot(): Record<string, JsonValue> {
    return {
      teamName: this.workspace.teamName,
      casePrefix: this.workspace.casePrefix,
      issuePrefix: this.workspace.issuePrefix,
      defaultEnvironment: this.workspace.defaultEnvironment,
      generationQuietMinutes: this.workspace.generationQuietMinutes,
      version: this.workspace.version,
    };
  }

  private nextTime(advance = true) {
    if (advance) this.sequence += 1;
    return new Date(Date.UTC(2026, 7, 20, 3, this.sequence)).toISOString();
  }
}

function compareFolders(left: StoredFolder, right: StoredFolder) {
  return left.sortOrder - right.sortOrder || left.id.localeCompare(right.id);
}

export function createInMemoryWorkspaceService(options?: InMemoryWorkspaceServiceOptions) {
  return new InMemoryWorkspaceService(options);
}

export const workspaceService: WorkspaceService = createInMemoryWorkspaceService();
