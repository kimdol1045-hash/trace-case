export type WorkspaceId = `workspace_${string}`;
export type FolderId = `folder_${string}`;
export type AuditId = `audit_${string}`;
export type DeletionBatchId = `folder_delete_${string}`;

export interface WorkspaceSettings {
  id: WorkspaceId;
  teamName: string;
  casePrefix: string;
  issuePrefix: string;
  defaultEnvironment: string;
  generationQuietMinutes: number;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface UpdateWorkspaceSettingsRequest {
  baseVersion: number;
  teamName?: string;
  casePrefix?: string;
  issuePrefix?: string;
  defaultEnvironment?: string;
  generationQuietMinutes?: number;
}

export interface FolderPathSegment {
  id: FolderId;
  name: string;
}

export interface FolderNode {
  id: FolderId;
  parentId: FolderId | null;
  name: string;
  sortOrder: number;
  depth: number;
  path: FolderPathSegment[];
  directTestCaseCount: number;
  subtreeTestCaseCount: number;
  version: number;
  deletedAt: string | null;
  children: FolderNode[];
}

export interface FolderTreeResponse {
  workspaceId: WorkspaceId;
  generatedAt: string;
  items: FolderNode[];
}

/** Lazy-tree view model derived from the canonical node without changing it. */
export interface FolderDirectoryNode extends FolderNode {
  hasChildren: boolean;
}

export interface FolderDirectoryResponse extends Omit<FolderTreeResponse, "items"> {
  items: FolderDirectoryNode[];
}

/** Frontend trash read model; `node` remains the canonical FolderNode payload. */
export interface DeletedFolderItem {
  node: FolderNode;
  deletionBatchId: DeletionBatchId;
}

export interface DeletedFolderTreeResponse {
  workspaceId: WorkspaceId;
  generatedAt: string;
  items: DeletedFolderItem[];
}

export interface CreateFolderRequest {
  parentId: FolderId | null;
  name: string;
  sortOrder?: number;
}

export interface RenameFolderRequest {
  baseVersion: number;
  name: string;
}

export interface MoveFolderRequest {
  baseVersion: number;
  mode: "SUBTREE";
  targetParentId: FolderId | null;
  sortOrder: number;
}

export interface SoftDeleteFolderRequest {
  baseVersion: number;
  mode: "SUBTREE";
  reason?: string;
}

export interface RestoreFolderRequest {
  baseVersion: number;
  deletionBatchId: DeletionBatchId;
  mode: "SUBTREE";
  targetParentId?: FolderId | null;
  reason?: string;
}

export type FolderMutationOperation =
  | "CREATE"
  | "RENAME"
  | "MOVE_SUBTREE"
  | "SORT"
  | "SOFT_DELETE_SUBTREE"
  | "RESTORE_SUBTREE";

export interface FolderMutationResult {
  operation: FolderMutationOperation;
  rootFolderId: FolderId;
  affectedFolderIds: FolderId[];
  rootVersion: number;
  auditId: AuditId;
  deletionBatchId?: DeletionBatchId;
}

export interface FolderImpact {
  folderId: FolderId;
  directTestCaseCount: number;
  descendantTestCaseCount: number;
  affectedFolderCount: number;
}

export type JsonValue = null | boolean | number | string | JsonValue[] | {
  [key: string]: JsonValue;
};

export interface AuditSource {
  type: "UI" | "API" | "SYSTEM" | "SLACK" | "WORKER";
  actorLabel?: string;
  externalActorId?: string;
}

export interface AuditEntry {
  id: AuditId;
  workspaceId: WorkspaceId;
  entityType: string;
  entityId: string;
  action: string;
  before: Record<string, JsonValue> | null;
  after: Record<string, JsonValue> | null;
  source: AuditSource;
  reason?: string;
  occurredAt: string;
  traceId: string;
  idempotencyKey?: string;
}

export interface AuditPageResponse {
  items: AuditEntry[];
  nextCursor: string | null;
  total: number;
}

export interface AuditHistoryQuery {
  limit?: number;
  cursor?: string;
}

/**
 * Frontend-owned port that mirrors the canonical Sprint 1 payloads. A generated
 * HTTP client can implement this interface without changing consuming views.
 */
export interface WorkspaceService {
  getWorkspaceSettings(workspaceId: WorkspaceId): Promise<WorkspaceSettings>;
  updateWorkspaceSettings(
    workspaceId: WorkspaceId,
    request: UpdateWorkspaceSettingsRequest,
  ): Promise<WorkspaceSettings>;
  getFolderTree(
    workspaceId: WorkspaceId,
    parentId?: FolderId | null,
  ): Promise<FolderDirectoryResponse>;
  getDeletedFolders(workspaceId: WorkspaceId): Promise<DeletedFolderTreeResponse>;
  getFolderImpact(workspaceId: WorkspaceId, folderId: FolderId): Promise<FolderImpact>;
  createFolder(
    workspaceId: WorkspaceId,
    request: CreateFolderRequest,
  ): Promise<FolderMutationResult>;
  renameFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: RenameFolderRequest,
  ): Promise<FolderMutationResult>;
  moveFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: MoveFolderRequest,
  ): Promise<FolderMutationResult>;
  softDeleteFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: SoftDeleteFolderRequest,
  ): Promise<FolderMutationResult>;
  restoreFolder(
    workspaceId: WorkspaceId,
    folderId: FolderId,
    request: RestoreFolderRequest,
  ): Promise<FolderMutationResult>;
  getAuditHistory(
    workspaceId: WorkspaceId,
    query?: AuditHistoryQuery,
  ): Promise<AuditPageResponse>;
}

export const PRIMARY_WORKSPACE_ID: WorkspaceId =
  "workspace_00000000000070008000000000000100";
export const AI_GENERATED_FOLDER_ID: FolderId =
  "folder_00000000000070008000000000000101";

export function isProtectedSystemFolder(folderId: FolderId) {
  return folderId === AI_GENERATED_FOLDER_ID;
}

export type WorkspaceServiceErrorCode =
  | "AUDIT_CURSOR_INVALID"
  | "DATA_CONFLICT"
  | "FOLDER_ALIAS_CONFLICT"
  | "FOLDER_ALIAS_TARGET_DELETED"
  | "FOLDER_CONTENT_WARNING"
  | "FOLDER_CYCLE"
  | "FOLDER_NAME_CONFLICT"
  | "FOLDER_NOT_EMPTY"
  | "FOLDER_PARENT_DELETED"
  | "FOLDER_RESTORE_CONFLICT"
  | "IDEMPOTENCY_CONFLICT"
  | "KEY_ALLOCATION_FAILED"
  | "NOT_FOUND"
  | "RESOURCE_NOT_FOUND"
  | "SYSTEM_FOLDER_PROTECTED"
  | "VALIDATION_ERROR"
  | "VALIDATION_FAILED"
  | "VERSION_CONFLICT";

export class WorkspaceServiceError extends Error {
  constructor(
    public readonly code: WorkspaceServiceErrorCode,
    message: string,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "WorkspaceServiceError";
  }
}
