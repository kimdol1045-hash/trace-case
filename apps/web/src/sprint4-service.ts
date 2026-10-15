import {
  ApiClientError,
  createApiClient,
  type CycleItemListQuery,
  type CycleItemPageResponse,
  type CycleResource,
  type IssueCommentPage,
  type IssueHistoryPage,
  type IssueLinkPage,
  type IssueListQuery,
  type IssuePage,
  type IssueResource,
  type PageQuery,
  type PermanentDeleteCycleRequest,
  type PermanentDeleteCycleResponse,
  type PermanentDeleteIssueRequest,
  type PermanentDeleteIssueResponse,
  type RequestOptions,
  type RestoreIssueRequest,
  type RestoreIssueResponse,
} from "@tc/api-client";

export type Sprint4ApiClient = ReturnType<typeof createApiClient>;

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export const sprint4Api = createApiClient(apiBaseUrl);

export type DeletedCycleResource = CycleResource;
export type DeletedIssueResource = IssueResource;
export type DeletedIssueListItem = IssuePage["items"][number];

/**
 * Deleted-resource boundary. All calls delegate to the generated client so
 * the list/detail/restore/permanent-delete contracts cannot drift from OpenAPI.
 */
export const sprint4LifecycleApi = {
  listDeletedIssues(query: IssueListQuery, signal?: AbortSignal): Promise<IssuePage> {
    return sprint4Api.listIssues({ ...query, includeDeleted: true, deletedOnly: true }, signal);
  },

  getCycle(cycleId: string, signal?: AbortSignal): Promise<DeletedCycleResource> {
    return sprint4Api.getCycle(cycleId, signal, true);
  },

  listCycleItems(cycleId: string, query: CycleItemListQuery = {}, signal?: AbortSignal): Promise<CycleItemPageResponse> {
    return sprint4Api.listCycleItems(cycleId, { ...query, includeDeleted: true }, signal);
  },

  getIssue(issueId: string, signal?: AbortSignal): Promise<DeletedIssueResource> {
    return sprint4Api.getIssue(issueId, signal, true);
  },

  listIssueLinks(issueId: string, query: PageQuery = {}, signal?: AbortSignal): Promise<IssueLinkPage> {
    return sprint4Api.listIssueLinks(issueId, { ...query, includeDeleted: true }, signal);
  },

  listIssueComments(issueId: string, query: PageQuery = {}, signal?: AbortSignal): Promise<IssueCommentPage> {
    return sprint4Api.listIssueComments(issueId, { ...query, includeDeleted: true }, signal);
  },

  listIssueHistory(issueId: string, query: PageQuery = {}, signal?: AbortSignal): Promise<IssueHistoryPage> {
    return sprint4Api.listIssueHistory(issueId, { ...query, includeDeleted: true }, signal);
  },

  restoreIssue(issueId: string, payload: RestoreIssueRequest, options: RequestOptions = {}): Promise<RestoreIssueResponse> {
    return sprint4Api.restoreIssue(issueId, payload, options);
  },

  permanentlyDeleteCycle(cycleId: string, payload: PermanentDeleteCycleRequest, options: RequestOptions = {}): Promise<PermanentDeleteCycleResponse> {
    return sprint4Api.permanentlyDeleteCycle(cycleId, payload, options);
  },

  permanentlyDeleteIssue(issueId: string, payload: PermanentDeleteIssueRequest, options: RequestOptions = {}): Promise<PermanentDeleteIssueResponse> {
    return sprint4Api.permanentlyDeleteIssue(issueId, payload, options);
  },
};

export function mutationKey(operation: string) {
  const nonce = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${operation}-${nonce}`;
}

export function apiErrorCode(error: unknown) {
  if (!(error instanceof ApiClientError)) return null;
  const response = error.response as { code?: string } | undefined;
  return response?.code ?? null;
}

export function apiErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiClientError) {
    const response = error.response as { code?: string; message?: string } | undefined;
    if (response?.code === "VERSION_CONFLICT") {
      return "다른 사용자가 먼저 변경했습니다. 최신 데이터를 다시 불러온 뒤 재시도하세요.";
    }
    if (response?.code === "SETUP_REQUIRED") {
      return "비공개 첨부 저장소 설정이 필요합니다. Integrations에서 상태를 확인하세요.";
    }
    if (response?.code === "PERMANENT_DELETE_CONFLICT") {
      return "연결된 사이클·테스트케이스·이슈 또는 첨부파일이 남아 있어 완전 삭제할 수 없습니다. 연결 데이터를 먼저 정리한 뒤 다시 시도하세요.";
    }
    return response?.message ?? error.message;
  }
  if (error instanceof Error && error.name !== "AbortError") return error.message;
  return fallback;
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
