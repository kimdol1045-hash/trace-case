import { ApiClientError, type ApiErrorResponse } from "@tc/api-client";
import { describe, expect, it, vi } from "vitest";

import {
  createGenerationJobApi,
  generationApplyAlreadyAppliedId,
  generationApplyDraftIssue,
  generationApplyRequestDraft,
  generationComparisonErrorMessage,
  generationFailureRetryKey,
  generationJobErrorMessage,
  generationSetupDetails,
  mergeGenerationJobs,
  type ApplyGenerationJobRequest,
  type GenerationJob,
  type GenerationJobDetail,
} from "./generation-job-service";

const job: GenerationJob = {
  id: "generation_job_1",
  slackWorkItemId: "slack_work_1",
  status: "QUEUED",
  lineage: { rootJobId: "generation_job_1", retryOfJobId: null, attemptNumber: 1 },
  context: {
    inputSchemaVersion: "1.0.0",
    outputSchemaVersion: "1.0.0",
    sourceVersion: 4,
    generationVersion: 2,
    ontologySchemaId: "ontology_1",
    promptVersion: "prompt_1",
    itemCount: 1,
    digest: "a".repeat(64),
    sealedAt: "2026-08-26T01:00:00Z",
  },
  failure: null,
  result: null,
  createdAt: "2026-08-26T01:00:00Z",
  startedAt: null,
  completedAt: null,
  updatedAt: "2026-08-26T01:00:00Z",
};

const detail: GenerationJobDetail = {
  job,
  contextItems: [{
    id: "context_1",
    knowledgeEvidenceId: "evidence_1",
    subjectType: "NODE",
    subjectId: "node_1",
    source: {
      kind: "SOURCE_REVISION",
      entityType: "SLACK_MESSAGE_REVISION",
      entityId: "revision_1",
      revision: "r2",
      contentHash: "content-hash",
      permalink: null,
    },
    rank: 1,
    reason: "현재 Slack 요청의 최신 근거",
    snapshot: {},
  }],
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("generation job service", () => {
  it("keeps one conditional-retry key across dialog reopen and lost response", () => {
    const values = new Map<string, string>();
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    });

    const first = generationFailureRetryKey("generation_job_root", "generation_job_failed");
    const reopened = generationFailureRetryKey("generation_job_root", "generation_job_failed");
    const otherFailure = generationFailureRetryKey("generation_job_root", "generation_job_other");

    expect(reopened).toBe(first);
    expect(otherFailure).not.toBe(first);
    vi.unstubAllGlobals();
  });

  it("uses the contract list query and performs no phantom create on construction", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [job], nextCursor: null }));
    const api = createGenerationJobApi("https://api.example.test/", fetchMock as unknown as typeof fetch);

    expect(fetchMock).not.toHaveBeenCalled();
    await expect(api.listGenerationJobs({
      slackWorkItemId: "slack_work_1",
      statuses: ["QUEUED", "FAILED"],
      cursor: "cursor-1",
      limit: 25,
    })).resolves.toEqual({ items: [job], nextCursor: null });

    const url = new URL(String(fetchMock.mock.calls[0]?.[0]));
    expect(url.pathname).toBe("/api/generation-jobs");
    expect(url.searchParams.get("slackWorkItemId")).toBe("slack_work_1");
    expect(url.searchParams.getAll("statuses")).toEqual(["QUEUED", "FAILED"]);
    expect(url.searchParams.get("cursor")).toBe("cursor-1");
    expect(url.searchParams.get("limit")).toBe("25");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBeUndefined();
  });

  it("sends explicit create, execute and append-only retry payloads with matching idempotency headers", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(detail)));
    const api = createGenerationJobApi("https://api.example.test", fetchMock as unknown as typeof fetch);

    await api.createGenerationJob(
      { idempotencyKey: "create-key-123", slackWorkItemId: "slack_work_1" },
      { idempotencyKey: "create-key-123", actorLabel: "Generation Job Web UI" },
    );
    await api.executeGenerationJob(
      "generation_job_1",
      { idempotencyKey: "execute-key-123" },
      { idempotencyKey: "execute-key-123" },
    );
    await api.retryGenerationJob(
      "generation_job_1",
      { idempotencyKey: "retry-key-123", contextMode: "REUSE_SEALED", reason: "사용자 재시도" },
      { idempotencyKey: "retry-key-123" },
    );

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [createUrl, createInit] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const [executeUrl, executeInit] = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    const [retryUrl, retryInit] = fetchMock.mock.calls[2] as unknown as [string, RequestInit];
    expect(createUrl).toBe("https://api.example.test/api/generation-jobs");
    expect(executeUrl).toBe("https://api.example.test/api/generation-jobs/generation_job_1/execute");
    expect(retryUrl).toBe("https://api.example.test/api/generation-jobs/generation_job_1/retry");
    expect(createInit.method).toBe("POST");
    expect(createInit.headers).toMatchObject({ "Idempotency-Key": "create-key-123", "X-Actor-Label": "Generation Job Web UI" });
    expect(JSON.parse(String(createInit.body))).toEqual({ idempotencyKey: "create-key-123", slackWorkItemId: "slack_work_1" });
    expect(executeInit.headers).toMatchObject({ "Idempotency-Key": "execute-key-123" });
    expect(JSON.parse(String(executeInit.body))).toEqual({ idempotencyKey: "execute-key-123" });
    expect(retryInit.headers).toMatchObject({ "Idempotency-Key": "retry-key-123" });
    expect(JSON.parse(String(retryInit.body))).toEqual({ idempotencyKey: "retry-key-123", contextMode: "REUSE_SEALED", reason: "사용자 재시도" });
  });

  it("posts the complete placement contract to the explicit apply endpoint without adding candidate content", async () => {
    const receipt = {
      id: "generation_apply_1",
      generationJobId: "generation_job_1",
      jobStatus: "SUCCEEDED",
      contextDigest: "a".repeat(64),
      resultDigest: "b".repeat(64),
      placementDigest: "c".repeat(64),
      transactionStatus: "COMMITTED",
      parent: { outcome: "REUSED", ledgerMutation: "NONE", workItemId: "work_item_1", key: "WORK-1", categoryId: "folder_1", resourceVersion: 2 },
      cases: [],
      counts: { generated: 5, created: 5, reused: 0 },
      auditId: "audit_1",
      appliedAt: "2026-08-26T07:00:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(receipt, 201));
    const api = createGenerationJobApi("https://api.example.test", fetchMock as unknown as typeof fetch);
    const payload: ApplyGenerationJobRequest = {
      idempotencyKey: "apply-key-123",
      expectedContextDigest: "a".repeat(64),
      expectedResultDigest: "b".repeat(64),
      placement: {
        placementSchemaVersion: "1.0.0",
        category: { categoryId: "folder_1", expectedResourceVersion: 2, method: "EXACT_NAME", score: 1 },
        parent: { decision: "REUSE_EXISTING", workItemId: "work_item_1", expectedResourceVersion: 2, expectedCategoryId: "folder_1", method: "SLACK_SOURCE_IDENTITY", score: 1 },
        cases: Array.from({ length: 5 }, (_, generatedCaseIndex) => ({
          generatedCaseIndex,
          exactContentKey: String(generatedCaseIndex + 1).repeat(64),
          decision: "CREATE_NEW" as const,
        })),
      },
    };

    await expect(api.applyGenerationJob("generation_job_1", payload, {
      idempotencyKey: "apply-key-123",
      actorLabel: "Generation Job Web UI",
    })).resolves.toEqual(receipt);

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example.test/api/generation-jobs/generation_job_1/apply");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "Idempotency-Key": "apply-key-123", "X-Actor-Label": "Generation Job Web UI" });
    expect(JSON.parse(String(init.body))).toEqual(payload);
    expect(String(init.body)).not.toContain("caseExpectedResult");
    expect(String(init.body)).not.toContain("steps");
  });

  it("loads the authoritative server apply plan without fabricating digests or ledger versions", async () => {
    const plan = {
      generationJobId: "generation_job_1",
      jobStatus: "SUCCEEDED" as const,
      expectedContextDigest: "a".repeat(64),
      expectedResultDigest: "b".repeat(64),
      placement: {
        placementSchemaVersion: "1.0.0" as const,
        category: { categoryId: "folder_1", expectedResourceVersion: 4, method: "EXACT_NAME" as const, score: 1 },
        parent: { decision: "REUSE_EXISTING" as const, workItemId: "work_item_1", expectedResourceVersion: 7, expectedCategoryId: "folder_1", method: "SLACK_SOURCE_IDENTITY" as const, score: 1 },
        cases: Array.from({ length: 5 }, (_, generatedCaseIndex) => ({
          generatedCaseIndex,
          exactContentKey: String(generatedCaseIndex + 1).repeat(64),
          decision: "CREATE_NEW" as const,
        })),
      },
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(plan));
    const api = createGenerationJobApi("https://api.example.test", fetchMock as unknown as typeof fetch);

    await expect(api.getGenerationJobApplyPlan("generation_job_1")).resolves.toEqual(plan);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/generation-jobs/generation_job_1/apply-plan",
      { signal: undefined },
    );
    expect(generationApplyRequestDraft(plan)).toEqual({
      expectedContextDigest: plan.expectedContextDigest,
      expectedResultDigest: plan.expectedResultDigest,
      placement: plan.placement,
    });
  });

  it("loads the read-only comparison endpoint without mutation headers or a request body", async () => {
    const comparison = { comparisonSchemaVersion: "1.0.0", job: { id: "generation_job_1" } };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(comparison));
    const api = createGenerationJobApi("https://api.example.test", fetchMock as unknown as typeof fetch);

    await expect(api.getGenerationComparison("generation_job_1")).resolves.toEqual(comparison);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/generation-jobs/generation_job_1/comparison",
      { signal: undefined },
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBeUndefined();
    expect(init.body).toBeUndefined();
    expect(init.headers).toBeUndefined();
  });

  it("maps comparison 404 and 409 recovery without suggesting live reconstruction", () => {
    const missing = new ApiClientError("missing", 404, {
      code: "RESOURCE_NOT_FOUND",
      message: "missing",
      details: {},
    } as unknown as ApiErrorResponse);
    const conflict = new ApiClientError("conflict", 409, {
      code: "GENERATION_JOB_STATE_CONFLICT",
      message: "conflict",
      details: {},
    } as unknown as ApiErrorResponse);

    expect(generationComparisonErrorMessage(missing)).toContain("비교할 생성 작업을 찾을 수 없습니다");
    expect(generationComparisonErrorMessage(conflict)).toContain("작업 상세에서 상태를 새로고침");
    expect(generationComparisonErrorMessage(conflict)).not.toContain("현재 원본으로 복원");
  });

  it("renders the canonical setup-required error in Korean and proves manual creation remains available", () => {
    const error = new ApiClientError("setup", 503, {
      code: "GENERATION_SETUP_REQUIRED",
      message: "provider is not configured",
      details: {
        jobId: "generation_job_1",
        setupState: "SETUP_REQUIRED",
        blockingDecisions: ["PROVIDER_SELECTION", "CREDENTIALS"],
        externalCallAttempted: false,
        manualCaseCreationAvailable: true,
      },
    } as unknown as ApiErrorResponse);

    expect(generationJobErrorMessage(error, "fallback")).toContain("생성 공급자 설정이 아직 완료되지 않았습니다");
    expect(generationJobErrorMessage(error, "fallback")).toContain("외부 호출 없이 현재 대기 작업 상태가 유지");
    expect(generationJobErrorMessage(error, "fallback")).toContain("새 대기 작업만 추가하고 자동 실행하지 않습니다");
    expect(generationJobErrorMessage(error, "fallback")).toContain("수동 테스트케이스 만들기");
    expect(generationSetupDetails(error)).toEqual({
      jobId: "generation_job_1",
      setupState: "SETUP_REQUIRED",
      blockingDecisions: ["PROVIDER_SELECTION", "CREDENTIALS"],
      externalCallAttempted: false,
      manualCaseCreationAvailable: true,
    });
  });

  it("deduplicates refreshed rows and keeps newest-first append-only retry history", () => {
    const retry = {
      ...job,
      id: "generation_job_2",
      status: "FAILED" as const,
      lineage: { rootJobId: job.id, retryOfJobId: job.id, attemptNumber: 2 },
      failure: {
        code: "GENERATION_TIMEOUT" as const,
        message: "시간 제한을 초과했습니다.",
        retryable: true,
        attempts: 2,
        occurredAt: "2026-08-26T02:00:00Z",
      },
      startedAt: "2026-08-26T01:59:00Z",
      completedAt: "2026-08-26T02:00:00Z",
      createdAt: "2026-08-26T01:30:00Z",
      updatedAt: "2026-08-26T02:00:00Z",
    };

    expect(mergeGenerationJobs([job], [job, retry])).toEqual([retry, job]);
  });

  it("provides stable Korean recovery for validation, placement conflict and transactional rollback", () => {
    const makeError = (code: string, details: Record<string, unknown> = {}) => new ApiClientError("apply", 409, {
      code,
      message: "native message must not lead recovery",
      details,
    } as unknown as ApiErrorResponse);

    expect(generationJobErrorMessage(makeError("VALIDATION_FAILED"), "fallback")).toContain("배치 정보가 올바르지 않습니다");
    expect(generationJobErrorMessage(makeError("GENERATION_PLACEMENT_CONFLICT"), "fallback")).toContain("원장은 변경되지 않았습니다");
    expect(generationJobErrorMessage(makeError("GENERATION_APPLY_TRANSACTION_FAILED"), "fallback")).toContain("하나도 생성되거나 변경되지 않았습니다");
    expect(generationJobErrorMessage(makeError("GENERATION_JOB_ALREADY_APPLIED"), "fallback")).toContain("이미 테스트케이스 원장에 저장");
    expect(generationApplyAlreadyAppliedId(makeError("GENERATION_JOB_ALREADY_APPLIED", { applyId: "generation_apply_1" }))).toBe("generation_apply_1");
  });

  it("keeps apply disabled until a complete authoritative plan matches every candidate in order", () => {
    const succeeded: GenerationJobDetail = {
      ...detail,
      job: {
        ...job,
        status: "SUCCEEDED",
        result: { cases: Array.from({ length: 5 }, (_, index) => ({ summary: `후보 ${index + 1}` })) },
        startedAt: "2026-08-26T01:01:00Z",
        completedAt: "2026-08-26T01:03:00Z",
      },
    };
    const draft = {
      expectedContextDigest: succeeded.job.context.digest,
      expectedResultDigest: "b".repeat(64),
      placement: {
        placementSchemaVersion: "1.0.0" as const,
        category: { categoryId: "folder_1", expectedResourceVersion: 2, method: "EXACT_NAME" as const, score: 1 },
        parent: { decision: "REUSE_EXISTING" as const, workItemId: "work_item_1", expectedResourceVersion: 2, expectedCategoryId: "folder_1", method: "SLACK_SOURCE_IDENTITY" as const, score: 1 },
        cases: Array.from({ length: 5 }, (_, generatedCaseIndex) => ({ generatedCaseIndex, exactContentKey: "d".repeat(64), decision: "CREATE_NEW" as const })),
      },
    };

    expect(generationApplyDraftIssue(succeeded, null)).toContain("저장 배치 계획이 아직 준비되지 않았습니다");
    expect(generationApplyDraftIssue(succeeded, draft)).toBeNull();
    expect(generationApplyDraftIssue(succeeded, { ...draft, placement: { ...draft.placement, cases: draft.placement.cases.slice(1) } })).toContain("전체 후보 순서와 일치하지 않습니다");
  });
});
