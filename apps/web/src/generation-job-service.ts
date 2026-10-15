import {
  ApiClientError,
  type ApiErrorResponse,
  type components,
} from "@tc/api-client";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export type GenerationJobStatus = components["schemas"]["GenerationJobResource"]["status"];
export type GenerationFailureCode = components["schemas"]["GenerationFailureResource"]["code"];
export type GenerationFailure = components["schemas"]["GenerationFailureResource"];
export type GenerationJobLineage = components["schemas"]["GenerationJobLineage"];
export type GenerationContextProvenance = components["schemas"]["GenerationContextProvenanceResource"];
export type GenerationContextSource = components["schemas"]["GenerationContextSourceResource"];
export type GenerationContextItem = components["schemas"]["GenerationContextItemResource"];
export type GenerationResult = NonNullable<components["schemas"]["GenerationJobResource"]["result"]>;
export type GenerationJob = components["schemas"]["GenerationJobResource"];
export type GenerationJobDetail = components["schemas"]["GenerationJobDetailResource"];
export type GenerationJobPage = components["schemas"]["GenerationJobPageResponse"];
export type GenerationFailureQueuePage = components["schemas"]["FailureQueuePage"];
export type GenerationFailureDetail = components["schemas"]["FailureDetail"];
export type GenerationAttemptHistory = components["schemas"]["FailureDetail"];
export type AutomaticGenerationRetryRequest = components["schemas"]["OperatorRetryRequest"];
export type AutomaticGenerationRetryResponse = components["schemas"]["RetryScheduledResponse"];
export type AutomaticGenerationRetryResult = AutomaticGenerationRetryResponse & { replayed: boolean };

export interface GenerationFailureListQuery {
  actionability?: string;
  finality?: string;
  errorCode?: string;
  failedBefore?: string;
  cursor?: string | null;
  limit?: number;
}

export type GenerationComparison = components["schemas"]["GenerationComparisonResponse"];
export type HistoricalTextResource = components["schemas"]["HistoricalTextResource"];
export type HistoricalExistingCaseContentResource = components["schemas"]["HistoricalExistingCaseContentResource"];
export type HistoricalSnapshotResource = components["schemas"]["HistoricalSnapshotResource"];
export type ComparisonSourceReferenceResource = components["schemas"]["ComparisonSourceReferenceResource"];
export type CandidateReferenceEdgeResource = components["schemas"]["CandidateReferenceEdgeResource"];
export type GeneratedCandidateComparisonResource = components["schemas"]["GeneratedCandidateComparisonResource"];

export interface GenerationJobListQuery {
  slackWorkItemId: string;
  statuses?: GenerationJobStatus[];
  cursor?: string | null;
  limit?: number;
}

export type CreateGenerationJobRequest = components["schemas"]["CreateGenerationJobRequest"];
export type ExecuteGenerationJobRequest = components["schemas"]["ExecuteGenerationJobRequest"];
export type RetryGenerationJobRequest = components["schemas"]["RetryGenerationJobRequest"];

export type GenerationCategoryPlacement = components["schemas"]["GenerationCategoryPlacementRequest"];
export type GenerationParentPlacement = components["schemas"]["GenerationPlacementRequest"]["parent"];
export type GenerationCasePlacement = components["schemas"]["GenerationPlacementRequest"]["cases"][number];
export type GenerationPlacement = components["schemas"]["GenerationPlacementRequest"];
export type ApplyGenerationJobRequest = components["schemas"]["ApplyGenerationJobRequest"];
export type GenerationApplyPlan = components["schemas"]["GenerationApplyPlanResource"];

export type GenerationApplyRequestDraft = Omit<ApplyGenerationJobRequest, "idempotencyKey">;

export type GenerationApplyReceipt = components["schemas"]["GenerationApplyReceiptResource"];

export type GenerationBlockingDecision =
  | "PROVIDER_SELECTION"
  | "ENDPOINT"
  | "CREDENTIALS"
  | "COST_APPROVAL";

export interface GenerationSetupRequiredDetails {
  jobId: string;
  setupState: "SETUP_REQUIRED";
  blockingDecisions: GenerationBlockingDecision[];
  externalCallAttempted: false;
  manualCaseCreationAvailable: true;
}

export interface GenerationJobErrorResponse {
  code?: string;
  message?: string;
  details?: GenerationSetupRequiredDetails | Record<string, unknown>;
}

export interface GenerationJobRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
  actorLabel?: string;
}

export interface GenerationJobApi {
  listGenerationJobs(query: GenerationJobListQuery, signal?: AbortSignal): Promise<GenerationJobPage>;
  getGenerationJob(jobId: string, signal?: AbortSignal): Promise<GenerationJobDetail>;
  getGenerationComparison(jobId: string, signal?: AbortSignal): Promise<GenerationComparison>;
  createGenerationJob(payload: CreateGenerationJobRequest, options?: GenerationJobRequestOptions): Promise<GenerationJobDetail>;
  executeGenerationJob(jobId: string, payload: ExecuteGenerationJobRequest, options?: GenerationJobRequestOptions): Promise<GenerationJobDetail>;
  retryGenerationJob(jobId: string, payload: RetryGenerationJobRequest, options?: GenerationJobRequestOptions): Promise<GenerationJobDetail>;
  getGenerationJobApplyPlan(jobId: string, signal?: AbortSignal): Promise<GenerationApplyPlan>;
  applyGenerationJob(jobId: string, payload: ApplyGenerationJobRequest, options?: GenerationJobRequestOptions): Promise<GenerationApplyReceipt>;
}

type Fetch = typeof globalThis.fetch;

export function createGenerationJobApi(baseUrl: string, fetchImpl: Fetch = globalThis.fetch): GenerationJobApi {
  const normalizedBaseUrl = baseUrl.replace(/\/$/, "");
  const jobUrl = (jobId: string) => `${normalizedBaseUrl}/api/generation-jobs/${encodeURIComponent(jobId)}`;

  return {
    listGenerationJobs(query, signal) {
      return requestJson<GenerationJobPage>(
        fetchImpl,
        `${normalizedBaseUrl}/api/generation-jobs${generationJobQueryString(query)}`,
        { signal },
        "Generation job list request failed",
      );
    },
    getGenerationJob(jobId, signal) {
      return requestJson<GenerationJobDetail>(
        fetchImpl,
        jobUrl(jobId),
        { signal },
        "Generation job detail request failed",
      );
    },
    getGenerationComparison(jobId, signal) {
      return requestJson<GenerationComparison>(
        fetchImpl,
        `${jobUrl(jobId)}/comparison`,
        { signal },
        "Generation comparison request failed",
      );
    },
    createGenerationJob(payload, options = {}) {
      return requestJson<GenerationJobDetail>(
        fetchImpl,
        `${normalizedBaseUrl}/api/generation-jobs`,
        generationMutationRequest(payload, options),
        "Generation job create request failed",
      );
    },
    executeGenerationJob(jobId, payload, options = {}) {
      return requestJson<GenerationJobDetail>(
        fetchImpl,
        `${jobUrl(jobId)}/execute`,
        generationMutationRequest(payload, options),
        "Generation job execute request failed",
      );
    },
    retryGenerationJob(jobId, payload, options = {}) {
      return requestJson<GenerationJobDetail>(
        fetchImpl,
        `${jobUrl(jobId)}/retry`,
        generationMutationRequest(payload, options),
        "Generation job retry request failed",
      );
    },
    getGenerationJobApplyPlan(jobId, signal) {
      return requestJson<GenerationApplyPlan>(
        fetchImpl,
        `${jobUrl(jobId)}/apply-plan`,
        { signal },
        "Generation job apply plan request failed",
      );
    },
    applyGenerationJob(jobId, payload, options = {}) {
      return requestJson<GenerationApplyReceipt>(
        fetchImpl,
        `${jobUrl(jobId)}/apply`,
        generationMutationRequest(payload, options),
        "Generation job apply request failed",
      );
    },
  };
}

export function generationApplyRequestDraft(plan: GenerationApplyPlan): GenerationApplyRequestDraft {
  return {
    expectedContextDigest: plan.expectedContextDigest,
    expectedResultDigest: plan.expectedResultDigest,
    placement: plan.placement,
  };
}

export const generationJobApi = createGenerationJobApi(apiBaseUrl);

export async function listGenerationFailures(
  query: GenerationFailureListQuery = {},
  signal?: AbortSignal,
): Promise<GenerationFailureQueuePage> {
  const search = new URLSearchParams();
  if (query.actionability) search.set("actionability", query.actionability);
  if (query.finality) search.set("finality", query.finality);
  if (query.errorCode) search.set("errorCode", query.errorCode);
  if (query.failedBefore) search.set("failedBefore", query.failedBefore);
  if (query.cursor) search.set("cursor", query.cursor);
  search.set("limit", String(query.limit ?? 25));
  return requestJson<GenerationFailureQueuePage>(
    globalThis.fetch,
    `${apiBaseUrl}/api/generation-failures?${search.toString()}`,
    { signal },
    "Generation failure list request failed",
  );
}

export function getGenerationFailure(
  failedJobId: string,
  signal?: AbortSignal,
): Promise<GenerationFailureDetail> {
  return requestJson<GenerationFailureDetail>(
    globalThis.fetch,
    `${apiBaseUrl}/api/generation-failures/${encodeURIComponent(failedJobId)}`,
    { signal },
    "Generation failure detail request failed",
  );
}

export function getGenerationAttemptHistory(
  failedJobId: string,
  signal?: AbortSignal,
): Promise<GenerationAttemptHistory> {
  return requestJson<GenerationAttemptHistory>(
    globalThis.fetch,
    `${apiBaseUrl}/api/generation-failures/${encodeURIComponent(failedJobId)}/attempt-history`,
    { signal },
    "Generation attempt history request failed",
  );
}

export async function retryAutomaticGenerationFailure(
  failedJobId: string,
  payload: AutomaticGenerationRetryRequest,
  signal?: AbortSignal,
): Promise<AutomaticGenerationRetryResult> {
  const response = await globalThis.fetch(
    `${apiBaseUrl}/api/generation-failures/${encodeURIComponent(failedJobId)}/retry`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "Idempotency-Key": payload.idempotencyKey,
      },
      body: JSON.stringify(payload),
      signal,
    },
  );
  const hasJson = response.headers.get("content-type")?.includes("application/json") ?? false;
  const body = hasJson ? await response.json() as unknown : undefined;
  if (!response.ok) {
    throw new ApiClientError(
      "Automatic generation retry request failed",
      response.status,
      body as ApiErrorResponse | undefined,
    );
  }
  return { ...(body as AutomaticGenerationRetryResponse), replayed: response.status === 200 };
}

export function generationFailureRetryKey(rootJobId: string, failedJobId: string) {
  const storageKey = `tc:generation-failure-retry:${rootJobId}:${failedJobId}`;
  try {
    const stored = globalThis.sessionStorage?.getItem(storageKey);
    if (stored) return stored;
  } catch {
    // Storage can be unavailable in SSR/privacy modes; the in-memory dialog
    // state below still keeps double-clicks on one mount idempotent.
  }
  const nonce = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const value = `generation-failure-retry:${rootJobId}:${failedJobId}:${nonce}`;
  try {
    globalThis.sessionStorage?.setItem(storageKey, value);
  } catch {
    // See the read fallback above.
  }
  return value;
}

export function generationMutationKey(operation: "create" | "execute" | "retry" | "apply") {
  const nonce = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `generation-job-${operation}-${nonce}`;
}

export function generationJobErrorCode(error: unknown) {
  if (!(error instanceof ApiClientError)) return null;
  return (error.response as GenerationJobErrorResponse | undefined)?.code ?? null;
}

export function generationSetupDetails(error: unknown): GenerationSetupRequiredDetails | null {
  if (!(error instanceof ApiClientError)) return null;
  const response = error.response as GenerationJobErrorResponse | undefined;
  if (response?.code !== "GENERATION_SETUP_REQUIRED") {
    return null;
  }
  const details = response.details as Partial<GenerationSetupRequiredDetails> | undefined;
  if (!details || details.setupState !== "SETUP_REQUIRED") return null;
  return {
    jobId: typeof details.jobId === "string" ? details.jobId : "",
    setupState: "SETUP_REQUIRED",
    blockingDecisions: Array.isArray(details.blockingDecisions)
      ? details.blockingDecisions.filter(isBlockingDecision)
      : [],
    externalCallAttempted: false,
    manualCaseCreationAvailable: true,
  };
}

export function generationJobErrorMessage(error: unknown, fallback: string) {
  const code = generationJobErrorCode(error);
  if (code === "GENERATION_SETUP_REQUIRED") {
    return "AI 생성 공급자 설정이 아직 완료되지 않았습니다. 외부 호출 없이 현재 대기 작업 상태가 유지되며, 실패 작업의 재시도는 새 대기 작업만 추가하고 자동 실행하지 않습니다. 수동 테스트케이스 만들기는 계속 사용할 수 있습니다.";
  }
  if (code === "GENERATION_JOB_STATE_CONFLICT") {
    return "생성 작업 상태가 먼저 변경되었습니다. 최신 작업 이력과 저장 배치 계획을 다시 불러온 뒤 재시도하세요.";
  }
  if (code === "IDEMPOTENCY_CONFLICT") {
    return "같은 요청 키가 다른 작업에 사용되었습니다. 작업 이력을 새로고침한 뒤 다시 시도하세요.";
  }
  if (code === "VALIDATION_FAILED") {
    return "테스트케이스 저장 요청의 배치 정보가 올바르지 않습니다. 최신 생성 작업과 저장 배치 계획을 다시 불러온 뒤 확인하세요.";
  }
  if (code === "GENERATION_JOB_ALREADY_APPLIED") {
    return "이 생성 작업의 후보는 이미 테스트케이스 원장에 저장되었습니다. 중복 저장하지 않고 기존 저장 결과를 확인하세요.";
  }
  if (code === "GENERATION_STORED_RESULT_INVALID") {
    return "저장 직전 후보 검증을 통과하지 못했습니다. 원장은 변경되지 않았습니다. 생성 작업을 새로 만들거나 수동 테스트케이스 만들기를 사용하세요.";
  }
  if (code === "GENERATION_PLACEMENT_CONFLICT") {
    return "카테고리·상위 일감 또는 중복 후보가 먼저 변경되었습니다. 원장은 변경되지 않았습니다. 최신 저장 배치 계획을 다시 불러온 뒤 재시도하세요.";
  }
  if (code === "GENERATION_APPLY_TRANSACTION_FAILED") {
    return "테스트케이스 저장 트랜잭션이 롤백되었습니다. 후보는 하나도 생성되거나 변경되지 않았습니다. 잠시 후 전체 후보 저장을 다시 시도하세요.";
  }
  if (code === "RESOURCE_NOT_FOUND") {
    return "생성 작업 또는 저장 대상이 더 이상 존재하지 않습니다. 최신 상태를 다시 불러온 뒤 확인하세요.";
  }
  if (error instanceof ApiClientError) {
    const response = error.response as GenerationJobErrorResponse | undefined;
    return response?.message ?? error.message;
  }
  if (error instanceof Error && error.name !== "AbortError") return error.message;
  return fallback;
}

export function generationComparisonErrorMessage(error: unknown) {
  const code = generationJobErrorCode(error);
  if (code === "RESOURCE_NOT_FOUND") {
    return "비교할 생성 작업을 찾을 수 없습니다. 생성 작업 이력으로 돌아가 최신 상태를 확인하세요.";
  }
  if (code === "GENERATION_JOB_STATE_CONFLICT" || code === "GENERATION_COMPARISON_NOT_READY") {
    return "생성 작업 상태가 변경되어 비교 자료를 확정할 수 없습니다. 작업 상세에서 상태를 새로고침한 뒤 다시 시도하세요.";
  }
  return "봉인된 비교 자료를 불러오지 못했습니다. 현재 원본으로 대신 채우지 않았습니다. 잠시 후 다시 시도하세요.";
}

export function generationApplyAlreadyAppliedId(error: unknown) {
  if (!(error instanceof ApiClientError)) return null;
  const response = error.response as GenerationJobErrorResponse | undefined;
  if (response?.code !== "GENERATION_JOB_ALREADY_APPLIED") return null;
  const details = response.details as { applyId?: unknown } | undefined;
  return typeof details?.applyId === "string" ? details.applyId : null;
}

export function generationCandidateCount(result: GenerationResult | null) {
  return Array.isArray(result?.cases) ? result.cases.length : null;
}

export function generationApplyDraftIssue(
  detail: GenerationJobDetail,
  draft: GenerationApplyRequestDraft | null,
) {
  if (detail.job.status !== "SUCCEEDED" || !detail.job.result) {
    return "생성 완료 작업만 테스트케이스로 저장할 수 있습니다.";
  }
  const candidateCount = generationCandidateCount(detail.job.result);
  if (candidateCount === null || candidateCount === 0) {
    return "저장할 생성 후보를 확인할 수 없습니다. 생성 작업을 다시 불러오세요.";
  }
  if (!draft) {
    return "서버의 저장 배치 계획이 아직 준비되지 않았습니다. 최신 상태를 새로고침한 뒤 다시 확인하세요.";
  }
  if (draft.expectedContextDigest !== detail.job.context.digest) {
    return "저장 배치 계획의 컨텍스트가 현재 작업과 다릅니다. 최신 상태를 다시 불러오세요.";
  }
  if (draft.placement.cases.length !== candidateCount
    || draft.placement.cases.some((item, index) => item.generatedCaseIndex !== index)) {
    return "저장 배치 계획이 전체 후보 순서와 일치하지 않습니다. 최신 상태를 다시 불러오세요.";
  }
  return null;
}

export function mergeGenerationJobs(current: GenerationJob[], incoming: GenerationJob[]) {
  const jobs = new Map(current.map((job) => [job.id, job]));
  for (const job of incoming) jobs.set(job.id, job);
  return [...jobs.values()].sort((left, right) => {
    const byCreatedAt = Date.parse(right.createdAt) - Date.parse(left.createdAt);
    return byCreatedAt || right.id.localeCompare(left.id);
  });
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

function generationMutationRequest(
  payload: { idempotencyKey: string },
  options: GenerationJobRequestOptions,
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

function generationJobQueryString(query: GenerationJobListQuery) {
  const search = new URLSearchParams();
  search.set("slackWorkItemId", query.slackWorkItemId);
  for (const status of query.statuses ?? []) search.append("statuses", status);
  if (query.cursor) search.set("cursor", query.cursor);
  search.set("limit", String(query.limit ?? 50));
  return `?${search.toString()}`;
}

function isBlockingDecision(value: unknown): value is GenerationBlockingDecision {
  return value === "PROVIDER_SELECTION"
    || value === "ENDPOINT"
    || value === "CREDENTIALS"
    || value === "COST_APPROVAL";
}
