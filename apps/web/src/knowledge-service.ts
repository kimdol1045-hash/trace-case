import {
  ApiClientError,
  createApiClient,
  type HybridSearchRequest,
  type KnowledgeNodeListQuery,
  type KnowledgeStatusPatchRequest,
  type OntologyExplorerRequest,
} from "@tc/api-client";

export type KnowledgeApiClient = Pick<
  ReturnType<typeof createApiClient>,
  | "listKnowledgeNodes"
  | "getKnowledgeNode"
  | "listKnowledgeNodeEvidence"
  | "patchKnowledgeStatus"
  | "listKnowledgeNodeAudit"
  | "searchKnowledge"
  | "exploreKnowledgeOntology"
>;

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export const knowledgeApi: KnowledgeApiClient = createApiClient(apiBaseUrl);

export function knowledgeErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiClientError) {
    const response = error.response as { code?: string; message?: string } | undefined;
    if (response?.code === "VERSION_CONFLICT") {
      return "다른 사용자가 먼저 변경했습니다. 최신 지식을 다시 불러온 뒤 재시도하세요.";
    }
    return response?.message ?? error.message;
  }
  if (error instanceof Error && error.name !== "AbortError") return error.message;
  return fallback;
}

export function listKnowledgeNodes(
  query: KnowledgeNodeListQuery,
  signal?: AbortSignal,
  api: KnowledgeApiClient = knowledgeApi,
) {
  return api.listKnowledgeNodes(query, signal);
}

export function searchKnowledge(
  payload: HybridSearchRequest,
  signal?: AbortSignal,
  api: KnowledgeApiClient = knowledgeApi,
) {
  return api.searchKnowledge(payload, { signal });
}

export function exploreKnowledge(
  payload: OntologyExplorerRequest,
  signal?: AbortSignal,
  api: KnowledgeApiClient = knowledgeApi,
) {
  return api.exploreKnowledgeOntology(payload, { signal });
}

export function patchKnowledge(
  payload: KnowledgeStatusPatchRequest,
  api: KnowledgeApiClient = knowledgeApi,
) {
  return api.patchKnowledgeStatus(payload);
}
