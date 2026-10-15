import { ApiClientError, type ApiErrorResponse } from "@tc/api-client";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export type GenerationSuggestionState = "OPEN" | "APPLIED" | "IGNORED" | "SUPERSEDED";
export type GenerationSuggestionDecision = "APPLIED" | "IGNORED";

export interface GenerationSuggestionStepSnapshot {
  sequence: number;
  precondition?: string | null;
  action: string;
  expectedResult?: string | null;
}

export interface GenerationSuggestionSemanticSnapshot {
  summary: string;
  precondition?: string;
  steps: GenerationSuggestionStepSnapshot[];
  expectedResultMode?: "PER_STEP" | "CASE";
  caseExpectedResult?: string | null;
  priority: string;
  labels: string[];
  generationReason?: string;
  evidenceNodeIds?: string[];
  openQuestions?: string[];
  inferenceOnly?: boolean;
}

export interface GenerationSuggestionResolution {
  id?: string;
  decision: GenerationSuggestionDecision;
  actor?: { type: string; actorLabel?: string; externalActorId?: string };
  reason?: string;
  observedCaseResourceVersion?: number;
  observedCaseVersionId?: string;
  appliedCaseVersionId?: string | null;
  auditId?: string;
  decidedAt?: string;
}

export interface GenerationSuggestion {
  id: string;
  testCaseId: string;
  generationLineageId: string;
  state: GenerationSuggestionState;
  baseline: {
    caseResourceVersion: number;
    caseVersionId: string;
    caseVersion: number;
    snapshot: GenerationSuggestionSemanticSnapshot;
    digest: string;
  };
  proposal: {
    snapshot: GenerationSuggestionSemanticSnapshot;
    digest: string;
  };
  diff: {
    schemaVersion: "generation-suggestion-diff/1.0.0";
    digest: string;
    changedFields: string[];
    changes: Array<{ path: string; before: unknown; after: unknown }>;
  };
  source: {
    suggestionTargetId?: string;
    generationJobId: string;
    rootGenerationJobId?: string;
    generationChangeAssessmentId?: string;
    slackWorkItemId?: string;
    sourceVersion: number;
    generationVersion: number;
    generatedCaseIndex?: number;
    contextDigest?: string;
    resultDigest?: string;
    exactContentKey?: string;
  };
  resolution: GenerationSuggestionResolution | null;
  supersededBySuggestionId: string | null;
  createdAt: string;
}

export interface ApplyGenerationSuggestionRequest {
  idempotencyKey: string;
  expectedCaseResourceVersion: number;
  expectedCaseVersionId: string;
  changeReason: string;
}

export interface IgnoreGenerationSuggestionRequest {
  idempotencyKey: string;
  reason: string;
}

export interface GenerationSuggestionDecisionReceipt {
  suggestion: GenerationSuggestion;
  resolution: GenerationSuggestionResolution;
  testCase?: unknown;
}

export interface GenerationSuggestionMutationOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
  actorLabel?: string;
}

export interface GenerationSuggestionApi {
  listTestCaseSuggestions(caseId: string, signal?: AbortSignal): Promise<GenerationSuggestion[]>;
  getSuggestion(suggestionId: string, signal?: AbortSignal): Promise<GenerationSuggestion>;
  applySuggestion(
    suggestionId: string,
    payload: ApplyGenerationSuggestionRequest,
    options?: GenerationSuggestionMutationOptions,
  ): Promise<GenerationSuggestionDecisionReceipt>;
  ignoreSuggestion(
    suggestionId: string,
    payload: IgnoreGenerationSuggestionRequest,
    options?: GenerationSuggestionMutationOptions,
  ): Promise<GenerationSuggestionDecisionReceipt>;
}

type Fetch = typeof globalThis.fetch;

export function createGenerationSuggestionApi(
  baseUrl: string,
  fetchImpl: Fetch = globalThis.fetch,
): GenerationSuggestionApi {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");
  const suggestionUrl = (suggestionId: string) =>
    `${normalizedBaseUrl}/api/generation-suggestions/${encodeURIComponent(suggestionId)}`;

  return {
    async listTestCaseSuggestions(caseId, signal) {
      const response = await requestJson<
        GenerationSuggestion[] | { items: GenerationSuggestion[] }
      >(
        fetchImpl,
        `${normalizedBaseUrl}/api/test-cases/${encodeURIComponent(caseId)}/generation-suggestions`,
        { signal },
        "Generation suggestion list request failed",
      );
      return Array.isArray(response) ? response : response.items;
    },
    getSuggestion(suggestionId, signal) {
      return requestJson<GenerationSuggestion>(
        fetchImpl,
        suggestionUrl(suggestionId),
        { signal },
        "Generation suggestion detail request failed",
      );
    },
    applySuggestion(suggestionId, payload, options = {}) {
      return requestJson<GenerationSuggestionDecisionReceipt>(
        fetchImpl,
        `${suggestionUrl(suggestionId)}/apply`,
        suggestionMutationRequest(payload, options),
        "Generation suggestion apply request failed",
      );
    },
    ignoreSuggestion(suggestionId, payload, options = {}) {
      return requestJson<GenerationSuggestionDecisionReceipt>(
        fetchImpl,
        `${suggestionUrl(suggestionId)}/ignore`,
        suggestionMutationRequest(payload, options),
        "Generation suggestion ignore request failed",
      );
    },
  };
}

export const generationSuggestionApi = createGenerationSuggestionApi(apiBaseUrl);

export function generationSuggestionMutationKey(decision: "apply" | "ignore") {
  const nonce = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `generation-suggestion-${decision}-${nonce}`;
}

export function generationSuggestionErrorReason(error: unknown) {
  if (!(error instanceof ApiClientError)) return null;
  const response = error.response as {
    code?: string;
    details?: { reason?: unknown };
  } | undefined;
  return typeof response?.details?.reason === "string" ? response.details.reason : response?.code ?? null;
}

export function generationSuggestionErrorMessage(error: unknown) {
  const reason = generationSuggestionErrorReason(error);
  if (reason === "CASE_VERSION_STALE") {
    return "현재 테스트케이스가 제안 기준 버전 이후 변경되어 이 제안을 반영할 수 없습니다. 최신 상태를 다시 불러오세요.";
  }
  if (reason === "CASE_DELETED") {
    return "삭제된 테스트케이스에는 제안을 반영할 수 없습니다. 복원 후에도 기존 제안은 자동으로 재조정되지 않습니다.";
  }
  if (reason === "SUPERSEDED") {
    return "더 최신 생성 제안이 있어 이 제안은 더 이상 처리할 수 없습니다. 목록을 새로고침하세요.";
  }
  if (reason === "NOT_OPEN") {
    return "이미 처리된 제안입니다. 최신 제안 상태를 다시 불러오세요.";
  }
  if (reason === "LINEAGE_MISMATCH" || reason === "SOURCE_PROVENANCE_UNPROVEN" || reason === "DIFF_SEAL_INVALID") {
    return "봉인된 제안 근거를 검증할 수 없어 처리하지 않았습니다. 최신 상태를 다시 불러오세요.";
  }
  if (reason === "IDEMPOTENCY_CONFLICT") {
    return "같은 요청 키가 다른 결정에 사용되었습니다. 창을 닫고 다시 시도하세요.";
  }
  if (error instanceof ApiClientError) {
    const response = error.response as { message?: string } | undefined;
    return response?.message ?? error.message;
  }
  if (error instanceof Error && error.name !== "AbortError") return error.message;
  return "생성 변경 제안을 처리하지 못했습니다. 잠시 후 다시 시도하세요.";
}

async function requestJson<Response>(
  fetchImpl: Fetch,
  url: string,
  init: RequestInit,
  failureMessage: string,
): Promise<Response> {
  const response = await fetchImpl(url, init);
  const hasJson = response.headers.get("content-type")?.includes("application/json") ?? false;
  const body = hasJson ? await response.json() as unknown : undefined;
  if (!response.ok) {
    throw new ApiClientError(
      failureMessage,
      response.status,
      body as ApiErrorResponse | undefined,
    );
  }
  return body as Response;
}

function suggestionMutationRequest(
  payload: ApplyGenerationSuggestionRequest | IgnoreGenerationSuggestionRequest,
  options: GenerationSuggestionMutationOptions,
): RequestInit {
  if (options.idempotencyKey && options.idempotencyKey !== payload.idempotencyKey) {
    throw new Error("Request idempotencyKey must match options.idempotencyKey");
  }
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "Idempotency-Key": payload.idempotencyKey,
  };
  if (options.actorLabel) headers["X-Actor-Label"] = options.actorLabel;
  return {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal: options.signal,
  };
}
