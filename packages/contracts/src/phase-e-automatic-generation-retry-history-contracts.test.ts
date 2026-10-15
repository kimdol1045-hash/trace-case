import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import generationRetryHistorySchema from "../generation/retry-history/1.0.0/schema.json";
import packageDocument from "../package.json";
import { generationRetryHistoryV1Schema } from "./index";

const digest = (value: string) => {
  void value;
  return "a".repeat(64);
};

interface FailureSummary {
  code: string;
  messageLabel: string;
  retryable: boolean;
  finality:
    | "AUTOMATIC_RETRY_SCHEDULED"
    | "TERMINAL_NON_RETRYABLE"
    | "TERMINAL_EXHAUSTED"
    | "TERMINAL_REVIEW_REQUIRED";
  providerAttemptCount: number;
  failedAt: string;
  nextAttemptNotBefore: string | null;
  exhaustionReceiptId: string | null;
}

function validator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  ajv.addSchema(generationRetryHistorySchema);
  return ajv;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

const noReferences = {
  retryRequestId: null,
  applyId: null,
  suggestionMaterializationId: null,
  notificationIntentId: null,
  notificationReceiptId: null,
};

const noMaterialization = {
  mode: "AUTOMATIC_APPLY",
  state: "NOT_SCHEDULED",
  applyId: null,
  suggestionMaterializationId: null,
};

const noNotification = {
  intentId: null,
  receiptId: null,
  outcome: "NONE",
};

const sealedInput = {
  contextPolicy: "REUSE_SEALED",
  contextDigest: digest("sealed-context-v4"),
  inputSchemaVersion: "generation-input/1.0.0",
  inputDigest: digest("sealed-input-v4"),
  operatorConditions: null,
};

const firstFailure: FailureSummary = {
  code: "GENERATION_TIMEOUT",
  messageLabel: "생성 서비스 응답 시간 초과",
  retryable: true,
  finality: "AUTOMATIC_RETRY_SCHEDULED",
  providerAttemptCount: 3,
  failedAt: "2026-08-28T01:00:20Z",
  nextAttemptNotBefore: "2026-08-28T01:00:50Z",
  exhaustionReceiptId: null,
};

const finalFailure: FailureSummary = {
  code: "GENERATION_TIMEOUT",
  messageLabel: "생성 서비스 응답 시간 초과",
  retryable: true,
  finality: "TERMINAL_EXHAUSTED",
  providerAttemptCount: 3,
  failedAt: "2026-08-28T01:05:20Z",
  nextAttemptNotBefore: null,
  exhaustionReceiptId: "generation_retry_exhaustion_001",
};

const attempt1 = {
  jobId: "generation_job_001",
  rootJobId: "generation_job_001",
  retryOfJobId: null,
  jobAttemptNumber: 1,
  retryOrigin: "ROOT",
  status: "FAILED",
  executionState: "TERMINAL",
  notBefore: "2026-08-28T01:00:00Z",
  workerDeliveryAttemptCount: 1,
  input: sealedInput,
  providerAttempts: [1, 2, 3].map((number) => ({
    providerAttemptNumber: number,
    outcome: "RETRYABLE_FAILURE",
    errorCode: "GENERATION_TIMEOUT",
    safeProviderCode: "timeout",
    httpStatus: 504,
    startedAt: `2026-08-28T01:00:0${number}Z`,
    finishedAt: `2026-08-28T01:00:1${number}Z`,
  })),
  resultDigest: null,
  failure: firstFailure,
  materialization: noMaterialization,
  notification: noNotification,
  createdAt: "2026-08-28T01:00:00Z",
  startedAt: "2026-08-28T01:00:01Z",
  completedAt: "2026-08-28T01:00:20Z",
};

const attempt3 = {
  ...attempt1,
  jobId: "generation_job_003",
  retryOfJobId: "generation_job_002",
  jobAttemptNumber: 3,
  retryOrigin: "AUTOMATIC_POLICY",
  notBefore: "2026-08-28T01:04:00Z",
  workerDeliveryAttemptCount: 2,
  providerAttempts: [1, 2, 3].map((number) => ({
    providerAttemptNumber: number,
    outcome: "RETRYABLE_FAILURE",
    errorCode: "GENERATION_TIMEOUT",
    safeProviderCode: "timeout",
    httpStatus: 504,
    startedAt: `2026-08-28T01:05:0${number}Z`,
    finishedAt: `2026-08-28T01:05:1${number}Z`,
  })),
  failure: finalFailure,
  notification: {
    intentId: "generation_slack_notification_001",
    receiptId: null,
    outcome: "PENDING",
  },
  createdAt: "2026-08-28T01:04:00Z",
  startedAt: "2026-08-28T01:05:01Z",
  completedAt: "2026-08-28T01:05:20Z",
};

const attempt2 = {
  ...attempt1,
  jobId: "generation_job_002",
  retryOfJobId: "generation_job_001",
  jobAttemptNumber: 2,
  retryOrigin: "AUTOMATIC_POLICY",
  notBefore: "2026-08-28T01:00:50Z",
  workerDeliveryAttemptCount: 1,
  failure: {
    ...firstFailure,
    failedAt: "2026-08-28T01:02:20Z",
    nextAttemptNotBefore: "2026-08-28T01:04:00Z",
  },
  createdAt: "2026-08-28T01:00:20Z",
  startedAt: "2026-08-28T01:02:01Z",
  completedAt: "2026-08-28T01:02:20Z",
};

const failureItem = {
  rootJobId: "generation_job_001",
  failedJobId: "generation_job_003",
  slackWorkItemId: "slack_work_item_001",
  sourceVersion: 4,
  jobAttemptNumber: 3,
  failure: finalFailure,
  actionability: "OPERATOR_RETRY_ALLOWED",
  latestRetryJobId: null,
};

const failureDetail = {
  contractVersion: "automatic-generation-retry-history/1.0.0",
  failure: failureItem,
  attemptHistory: [attempt1, attempt2, attempt3],
  timeline: [
    {
      sequence: 1,
      eventType: "INPUT_SEALED",
      jobId: "generation_job_001",
      jobAttemptNumber: 1,
      providerAttemptNumber: null,
      errorCode: null,
      occurredAt: "2026-08-28T01:00:00Z",
      references: noReferences,
    },
    {
      sequence: 2,
      eventType: "JOB_FAILED",
      jobId: "generation_job_003",
      jobAttemptNumber: 3,
      providerAttemptNumber: null,
      errorCode: "GENERATION_TIMEOUT",
      occurredAt: "2026-08-28T01:05:20Z",
      references: {
        ...noReferences,
        notificationIntentId: "generation_slack_notification_001",
      },
    },
  ],
};

function historySemanticsAreValid(value: typeof failureDetail) {
  const attempts = value.attemptHistory;
  const events = value.timeline;
  return (
    attempts.every((attempt, index) => attempt.jobAttemptNumber === index + 1) &&
    attempts.every(
      (attempt, index) =>
        attempt.rootJobId === attempts[0]?.rootJobId &&
        (index === 0
          ? attempt.retryOfJobId === null && attempt.retryOrigin === "ROOT"
          : attempt.retryOfJobId === attempts[index - 1]?.jobId),
    ) &&
    attempts.every((attempt) =>
      attempt.providerAttempts.every(
        (providerAttempt, index) => providerAttempt.providerAttemptNumber === index + 1,
      ),
    ) &&
    events.every((event, index) => event.sequence === index + 1) &&
    events.every(
      (event, index) => index === 0 || event.occurredAt >= events[index - 1]!.occurredAt,
    )
  );
}

function failureFinalityIsValid(value: FailureSummary) {
  if (value.finality === "AUTOMATIC_RETRY_SCHEDULED") {
    return value.retryable && value.nextAttemptNotBefore !== null && value.exhaustionReceiptId === null;
  }
  if (value.finality === "TERMINAL_EXHAUSTED") {
    return value.retryable && value.nextAttemptNotBefore === null && value.exhaustionReceiptId !== null;
  }
  if (value.finality === "TERMINAL_NON_RETRYABLE") {
    return !value.retryable && value.nextAttemptNotBefore === null && value.exhaustionReceiptId === null;
  }
  return value.nextAttemptNotBefore === null;
}

describe("PE-SE2-T07 automatic generation retry/history contract", () => {
  it("compiles every strict definition and publishes the versioned package surface", () => {
    const ajv = validator();

    expect(generationRetryHistorySchema.$schema).toBe(
      "https://json-schema.org/draft/2020-12/schema",
    );
    expect(ajv.getSchema(generationRetryHistorySchema.$id)).toBeTypeOf("function");
    for (const name of Object.keys(generationRetryHistorySchema.$defs)) {
      expect(
        ajv.getSchema(`${generationRetryHistorySchema.$id}#/$defs/${name}`),
        `definition did not compile: ${name}`,
      ).toBeTypeOf("function");
    }
    expect(generationRetryHistoryV1Schema).toBe(generationRetryHistorySchema);
    expect(packageDocument.exports).toHaveProperty(
      "./schemas/generation/retry-history/1.0.0",
      "./generation/retry-history/1.0.0/schema.json",
    );
  });

  it("keeps job, provider, and worker-delivery attempts separate in a canonical history", () => {
    const ajv = validator();
    const validate = ajv.getSchema(
      `${generationRetryHistorySchema.$id}#/$defs/FailureDetail`,
    );

    expect(validate?.(failureDetail), ajv.errorsText(validate?.errors)).toBe(true);
    expect(historySemanticsAreValid(failureDetail)).toBe(true);
    expect(failureDetail.attemptHistory).toHaveLength(3);
    expect(failureDetail.attemptHistory[2]?.providerAttempts).toHaveLength(3);
    expect(failureDetail.attemptHistory[2]?.workerDeliveryAttemptCount).toBe(2);
  });

  it("validates the failure queue cursor without leaking a provider response", () => {
    const ajv = validator();
    const validate = ajv.getSchema(
      `${generationRetryHistorySchema.$id}#/$defs/FailureQueuePage`,
    );
    const page = {
      contractVersion: "automatic-generation-retry-history/1.0.0",
      items: [failureItem],
      nextCursor: "eyJmYWlsZWRBdCI6IjIwMjYtMDgtMjhUMDE6MDU6MjBaIn0",
    };

    expect(validate?.(page), ajv.errorsText(validate?.errors)).toBe(true);
    expect(validate?.({ ...page, rawProviderBody: "do not expose" })).toBe(false);
    expect(validate?.({ ...page, items: [{ ...failureItem, prompt: "secret" }] })).toBe(false);
  });

  it("accepts only an immutable, server-attributed conditional retry request", () => {
    const ajv = validator();
    const validateRequest = ajv.getSchema(
      `${generationRetryHistorySchema.$id}#/$defs/OperatorRetryRequest`,
    );
    const validateResponse = ajv.getSchema(
      `${generationRetryHistorySchema.$id}#/$defs/RetryScheduledResponse`,
    );
    const request = {
      idempotencyKey: "retry:root-001:operator-001:01",
      expectedRootJobId: "generation_job_001",
      expectedFailedJobId: "generation_job_003",
      expectedAttemptNumber: 3,
      contextMode: "REUSE_SEALED",
      reason: "타임아웃 이후 운영자 확인",
      additionalConditions: "경계값 사례를 우선 검증하세요.",
    };
    const normalized = request.additionalConditions.normalize("NFKC").trim();
    const response = {
      contractVersion: "automatic-generation-retry-history/1.0.0",
      retryRequestId: "generation_retry_request_001",
      requestDigest: digest(JSON.stringify(request)),
      actor: {
        type: "AUTHENTICATED_OPERATOR",
        principalId: "operator_001",
        displayLabel: "운영자 1",
      },
      sourceFailedJobId: "generation_job_003",
      retryJobId: "generation_job_004",
      jobAttemptNumber: 4,
      retryOrigin: "OPERATOR_CONDITIONS",
      contextMode: "REUSE_SEALED",
      notBefore: "2026-08-28T01:10:00Z",
      additionalConditions: {
        text: normalized,
        unicodeCodePoints: [...normalized].length,
        utf8Bytes: new TextEncoder().encode(normalized).length,
        digest: digest(normalized),
      },
    };

    expect(validateRequest?.(request), ajv.errorsText(validateRequest?.errors)).toBe(true);
    expect(validateResponse?.(response), ajv.errorsText(validateResponse?.errors)).toBe(true);
    for (const forbidden of ["actor", "systemPrompt", "providerBody", "sourceSnapshot"]) {
      expect(validateRequest?.({ ...request, [forbidden]: "forbidden" }), forbidden).toBe(false);
    }
    expect(validateRequest?.({ ...request, contextMode: "REBUILD_CURRENT" })).toBe(false);
    expect(validateRequest?.({ ...request, additionalConditions: "x".repeat(1001) })).toBe(false);
  });

  it("rejects raw provider, prompt, secret, and exception fields at every public seam", () => {
    const ajv = validator();
    const validateAttempt = ajv.getSchema(
      `${generationRetryHistorySchema.$id}#/$defs/ProviderAttempt`,
    );
    const validateFailure = ajv.getSchema(
      `${generationRetryHistorySchema.$id}#/$defs/FailureSummary`,
    );
    const canonicalAttempt = attempt1.providerAttempts[0]!;

    expect(validateAttempt?.(canonicalAttempt)).toBe(true);
    for (const forbidden of [
      "rawProviderBody",
      "exceptionMessage",
      "authorizationHeader",
      "requestPrompt",
      "endpoint",
    ]) {
      expect(validateAttempt?.({ ...canonicalAttempt, [forbidden]: "secret" }), forbidden).toBe(false);
      expect(validateFailure?.({ ...finalFailure, [forbidden]: "secret" }), forbidden).toBe(false);
    }
  });

  it("requires deterministic retry finality and a receipt before exhausted Slack failure", () => {
    expect(failureFinalityIsValid(firstFailure)).toBe(true);
    expect(failureFinalityIsValid(finalFailure)).toBe(true);
    expect(
      failureFinalityIsValid({ ...finalFailure, exhaustionReceiptId: null }),
    ).toBe(false);
    expect(
      failureFinalityIsValid({
        ...finalFailure,
        retryable: false,
        finality: "TERMINAL_NON_RETRYABLE",
        exhaustionReceiptId: null,
      }),
    ).toBe(true);
  });

  it("detects non-contiguous lineage and timeline ordering beyond structural JSON Schema", () => {
    const broken = clone(failureDetail);
    broken.attemptHistory[1]!.jobAttemptNumber = 7;
    expect(historySemanticsAreValid(broken)).toBe(false);

    const outOfOrder = clone(failureDetail);
    outOfOrder.timeline[1]!.occurredAt = "2026-08-27T01:05:20Z";
    expect(historySemanticsAreValid(outOfOrder)).toBe(false);
  });
});
