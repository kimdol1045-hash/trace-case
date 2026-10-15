import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import generationOutputFixture from "../generation/fixtures/1.0.0/output.grounded.json";
import generationJobApiSchema from "../generation/job-api/1.0.0/schema.json";
import generationOutputSchema from "../ai-test-case-generation-output.schema.json";
import packageDocument from "../package.json";
import sprint1ErrorSchema from "../sprint-1/error-contract.schema.json";
import stateEnumSchema from "../state-enums.schema.json";
import { generationJobApiV1Schema } from "./index";

const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const schemas = [stateEnumSchema, sprint1ErrorSchema, generationOutputSchema, generationJobApiSchema];

type JsonObject = Record<string, unknown>;

function createValidator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  for (const schema of schemas) ajv.addSchema(schema);
  return ajv;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function asObject(value: unknown): JsonObject | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : undefined;
}

function asObjects(value: unknown): JsonObject[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const objects = value.map(asObject);
  return objects.every((object) => object !== undefined) ? (objects as JsonObject[]) : undefined;
}

function detailSemanticsAreValid(value: unknown) {
  const detail = asObject(value);
  const job = asObject(detail?.job);
  const lineage = asObject(job?.lineage);
  const context = asObject(job?.context);
  const contextItems = asObjects(detail?.contextItems);
  if (!detail || !job || !lineage || !context || !contextItems) return false;

  const rootLineage =
    lineage.attemptNumber === 1 &&
    lineage.rootJobId === job.id &&
    lineage.retryOfJobId === null;
  const retryLineage =
    typeof lineage.attemptNumber === "number" &&
    lineage.attemptNumber > 1 &&
    typeof lineage.rootJobId === "string" &&
    lineage.rootJobId !== job.id &&
    typeof lineage.retryOfJobId === "string" &&
    lineage.retryOfJobId !== job.id;
  if (!rootLineage && !retryLineage) return false;

  const ranks = contextItems.map((item) => item.rank);
  const ids = contextItems.map((item) => item.id);
  const evidenceIds = contextItems.map((item) => item.knowledgeEvidenceId);
  if (
    context.itemCount !== contextItems.length ||
    !ranks.every((rank, index) => rank === index + 1) ||
    new Set(ids).size !== ids.length ||
    new Set(evidenceIds).size !== evidenceIds.length
  ) {
    return false;
  }

  const createdAt = typeof job.createdAt === "string" ? Date.parse(job.createdAt) : Number.NaN;
  const startedAt = typeof job.startedAt === "string" ? Date.parse(job.startedAt) : undefined;
  const completedAt = typeof job.completedAt === "string" ? Date.parse(job.completedAt) : undefined;
  return (
    Number.isFinite(createdAt) &&
    (startedAt === undefined || createdAt <= startedAt) &&
    (completedAt === undefined || (startedAt !== undefined && startedAt <= completedAt))
  );
}

function retrySemanticsAreValid(sourceValue: unknown, retryValue: unknown) {
  const sourceDetail = asObject(sourceValue);
  const retryDetail = asObject(retryValue);
  const source = asObject(sourceDetail?.job);
  const retry = asObject(retryDetail?.job);
  const sourceLineage = asObject(source?.lineage);
  const retryLineage = asObject(retry?.lineage);
  const sourceContext = asObject(source?.context);
  const retryContext = asObject(retry?.context);
  if (!source || !retry || !sourceLineage || !retryLineage || !sourceContext || !retryContext) return false;

  const copiedContextFields = [
    "inputSchemaVersion",
    "outputSchemaVersion",
    "sourceVersion",
    "ontologySchemaId",
    "promptVersion",
    "itemCount",
  ];
  return (
    source.status === "FAILED" &&
    retry.status === "QUEUED" &&
    retry.id !== source.id &&
    retry.slackWorkItemId === source.slackWorkItemId &&
    retryLineage.rootJobId === sourceLineage.rootJobId &&
    retryLineage.retryOfJobId === source.id &&
    retryLineage.attemptNumber === Number(sourceLineage.attemptNumber) + 1 &&
    retryContext.generationVersion === Number(sourceContext.generationVersion) + 1 &&
    retryContext.digest !== sourceContext.digest &&
    copiedContextFields.every((field) => retryContext[field] === sourceContext[field])
  );
}

const contextItem = {
  id: "generation_context_item_001",
  knowledgeEvidenceId: "knowledge_evidence_001",
  subjectType: "NODE",
  subjectId: "knowledge_node_001",
  source: {
    kind: "SOURCE_REVISION",
    entityType: "SLACK_MESSAGE",
    entityId: "slack_message_001",
    revision: "1787706000.000000",
    contentHash: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    permalink: "https://synthetic.invalid/archives/C_SYNTHETIC/p1787706000000000",
  },
  rank: 1,
  reason: "ACTIVE_WORK_ITEM",
  snapshot: {
    status: "ACTIVE",
    sourceVersion: 3,
  },
};

const queuedDetail = {
  job: {
    id: "generation_job_001",
    slackWorkItemId: "slack_work_item_001",
    status: "QUEUED",
    lineage: {
      rootJobId: "generation_job_001",
      retryOfJobId: null,
      attemptNumber: 1,
    },
    context: {
      inputSchemaVersion: "1.0.0",
      outputSchemaVersion: "1.0.0",
      sourceVersion: 3,
      generationVersion: 1,
      ontologySchemaId: "ontology_schema_001",
      promptVersion: "generation_prompt_v1",
      itemCount: 1,
      digest: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      sealedAt: "2026-08-26T03:00:00Z",
    },
    failure: null,
    result: null,
    createdAt: "2026-08-26T03:00:00Z",
    startedAt: null,
    completedAt: null,
    updatedAt: "2026-08-26T03:00:00Z",
  },
  contextItems: [contextItem],
};

function failedDetail() {
  const detail = clone(queuedDetail) as unknown as JsonObject;
  const job = detail.job as JsonObject;
  job.status = "FAILED";
  job.startedAt = "2026-08-26T03:01:00Z";
  job.completedAt = "2026-08-26T03:01:05Z";
  job.updatedAt = "2026-08-26T03:01:05Z";
  job.failure = {
    code: "GENERATION_TIMEOUT",
    message: "Generation provider timed out.",
    retryable: true,
    attempts: 3,
    occurredAt: "2026-08-26T03:01:05Z",
  };
  return detail;
}

function retryDetail() {
  const detail = clone(queuedDetail) as unknown as JsonObject;
  const job = detail.job as JsonObject;
  const lineage = job.lineage as JsonObject;
  const context = job.context as JsonObject;
  job.id = "generation_job_002";
  lineage.rootJobId = "generation_job_001";
  lineage.retryOfJobId = "generation_job_001";
  lineage.attemptNumber = 2;
  context.generationVersion = 2;
  context.digest = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
  (detail.contextItems as JsonObject[])[0]!.id = "generation_context_item_002";
  return detail;
}

describe("Phase E generation job API 1.0.0 contracts", () => {
  it("compiles the schema and every definition in strict Draft 2020-12 mode", () => {
    const ajv = createValidator();

    expect(generationJobApiSchema.$schema).toBe(draft202012);
    expect(generationJobApiSchema.$id).toBe(
      "https://qa-tool.local/schemas/generation/job-api/1.0.0/schema.json",
    );
    expect(ajv.getSchema(generationJobApiSchema.$id)).toBeTypeOf("function");
    for (const definitionName of Object.keys(generationJobApiSchema.$defs)) {
      const definitionRef = `${generationJobApiSchema.$id}#/$defs/${definitionName}`;
      expect(ajv.getSchema(definitionRef), `schema definition did not compile: ${definitionRef}`).toBeTypeOf(
        "function",
      );
    }
  });

  it("publishes the versioned package and TypeScript index surfaces", () => {
    expect(generationJobApiV1Schema).toBe(generationJobApiSchema);
    expect(packageDocument.exports).toHaveProperty(
      "./schemas/generation/job-api/1.0.0",
      "./generation/job-api/1.0.0/schema.json",
    );
  });

  it("uses the canonical generation job state enum without redefining it", () => {
    expect(generationJobApiSchema.$defs.GenerationJobStatus.$ref).toBe(
      "../../../state-enums.schema.json#/$defs/GenerationJobStatus",
    );
    expect(stateEnumSchema.$defs.GenerationJobStatus.enum).toEqual([
      "QUEUED",
      "RUNNING",
      "SUCCEEDED",
      "FAILED",
    ]);
  });

  it("enforces QUEUED/RUNNING/SUCCEEDED/FAILED lifecycle payloads and sealed context semantics", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(`${generationJobApiSchema.$id}#/$defs/GenerationJobDetailResource`);

    expect(validate?.(queuedDetail), ajv.errorsText(validate?.errors)).toBe(true);
    expect(detailSemanticsAreValid(queuedDetail)).toBe(true);

    const running = clone(queuedDetail) as unknown as JsonObject;
    (running.job as JsonObject).status = "RUNNING";
    (running.job as JsonObject).startedAt = "2026-08-26T03:01:00Z";
    expect(validate?.(running), ajv.errorsText(validate?.errors)).toBe(true);
    expect(detailSemanticsAreValid(running)).toBe(true);

    const succeeded = clone(running) as unknown as JsonObject;
    (succeeded.job as JsonObject).status = "SUCCEEDED";
    (succeeded.job as JsonObject).completedAt = "2026-08-26T03:01:05Z";
    (succeeded.job as JsonObject).updatedAt = "2026-08-26T03:01:05Z";
    (succeeded.job as JsonObject).result = generationOutputFixture;
    expect(validate?.(succeeded), ajv.errorsText(validate?.errors)).toBe(true);
    expect(detailSemanticsAreValid(succeeded)).toBe(true);

    const failed = failedDetail();
    expect(validate?.(failed), ajv.errorsText(validate?.errors)).toBe(true);
    expect(detailSemanticsAreValid(failed)).toBe(true);

    const queuedWithFailure = clone(queuedDetail) as unknown as JsonObject;
    (queuedWithFailure.job as JsonObject).failure = (failed.job as JsonObject).failure;
    expect(validate?.(queuedWithFailure)).toBe(false);

    const succeededWithoutResult = clone(running) as unknown as JsonObject;
    (succeededWithoutResult.job as JsonObject).status = "SUCCEEDED";
    (succeededWithoutResult.job as JsonObject).completedAt = "2026-08-26T03:01:05Z";
    expect(validate?.(succeededWithoutResult)).toBe(false);

    const failedWithoutFailure = clone(failed) as unknown as JsonObject;
    (failedWithoutFailure.job as JsonObject).failure = null;
    expect(validate?.(failedWithoutFailure)).toBe(false);

    const nonContiguousContext = clone(queuedDetail) as unknown as JsonObject;
    (nonContiguousContext.contextItems as JsonObject[])[0]!.rank = 2;
    expect(validate?.(nonContiguousContext)).toBe(true);
    expect(detailSemanticsAreValid(nonContiguousContext)).toBe(false);
  });

  it("keeps create and execute provider-neutral, explicit and idempotent", () => {
    const ajv = createValidator();
    const create = ajv.getSchema(`${generationJobApiSchema.$id}#/$defs/CreateGenerationJobRequest`);
    const execute = ajv.getSchema(`${generationJobApiSchema.$id}#/$defs/ExecuteGenerationJobRequest`);
    const createRequest: JsonObject = {
      idempotencyKey: "generation-create-001",
      slackWorkItemId: "slack_work_item_001",
    };
    const executeRequest: JsonObject = { idempotencyKey: "generation-execute-001" };

    expect(create?.(createRequest), ajv.errorsText(create?.errors)).toBe(true);
    expect(execute?.(executeRequest), ajv.errorsText(execute?.errors)).toBe(true);
    for (const [field, value] of [
      ["provider", "unapproved-provider"],
      ["model", "unapproved-model"],
      ["endpoint", "https://provider.invalid/generate"],
      ["credentials", "secret"],
      ["cost", 1],
      ["temperature", 0.2],
    ] as const) {
      expect(create?.({ ...createRequest, [field]: value }), `create accepted ${field}`).toBe(false);
      expect(execute?.({ ...executeRequest, [field]: value }), `execute accepted ${field}`).toBe(false);
    }

    expect(create?.({ slackWorkItemId: "slack_work_item_001" })).toBe(false);
    expect(execute?.({})).toBe(false);
    expect(execute?.({ idempotencyKey: "short" })).toBe(false);
  });

  it("defines append-only retry lineage without mutating the failed source job", () => {
    const ajv = createValidator();
    const validateDetail = ajv.getSchema(`${generationJobApiSchema.$id}#/$defs/GenerationJobDetailResource`);
    const validateRetry = ajv.getSchema(`${generationJobApiSchema.$id}#/$defs/RetryGenerationJobRequest`);
    const source = failedDetail();
    const sourceBefore = clone(source);
    const retry = retryDetail();

    expect(validateRetry?.({
      idempotencyKey: "generation-retry-001",
      contextMode: "REUSE_SEALED",
      reason: "Retry after a transient timeout.",
    })).toBe(true);
    expect(validateRetry?.({
      idempotencyKey: "generation-retry-002",
      contextMode: "REBUILD_LATEST",
      reason: "Silently replace context.",
    })).toBe(false);
    expect(validateDetail?.(retry), ajv.errorsText(validateDetail?.errors)).toBe(true);
    expect(detailSemanticsAreValid(retry)).toBe(true);
    expect(retrySemanticsAreValid(source, retry)).toBe(true);
    expect(source).toEqual(sourceBefore);

    const retryFromRunning = clone(source) as unknown as JsonObject;
    (retryFromRunning.job as JsonObject).status = "RUNNING";
    (retryFromRunning.job as JsonObject).completedAt = null;
    (retryFromRunning.job as JsonObject).failure = null;
    expect(retrySemanticsAreValid(retryFromRunning, retry)).toBe(false);
  });

  it("exposes stable fail-closed setup and redacted provider error codes", () => {
    const ajv = createValidator();
    const setup = ajv.getSchema(`${generationJobApiSchema.$id}#/$defs/GenerationSetupRequiredError`);
    const provider = ajv.getSchema(`${generationJobApiSchema.$id}#/$defs/GenerationProviderExecutionError`);
    const setupError = {
      code: "GENERATION_SETUP_REQUIRED",
      message: "Generation provider setup is required.",
      details: {
        jobId: "generation_job_001",
        setupState: "SETUP_REQUIRED",
        blockingDecisions: ["PROVIDER_SELECTION", "ENDPOINT", "CREDENTIALS", "COST_APPROVAL"],
        externalCallAttempted: false,
        manualCaseCreationAvailable: true,
      },
      traceId: "trace_generation_setup_001",
    };

    expect(setup?.(setupError), ajv.errorsText(setup?.errors)).toBe(true);
    const unsafeSetup = clone(setupError) as unknown as JsonObject;
    (unsafeSetup.details as JsonObject).externalCallAttempted = true;
    expect(setup?.(unsafeSetup)).toBe(false);

    const errorCodes = generationJobApiSchema.$defs.GenerationProviderErrorCode.enum;
    expect(errorCodes).toEqual([
      "GENERATION_UNSUPPORTED_SCHEMA_VERSION",
      "GENERATION_AUTH_REJECTED",
      "GENERATION_TIMEOUT",
      "GENERATION_TRANSPORT_UNAVAILABLE",
      "GENERATION_RATE_LIMITED",
      "GENERATION_PROVIDER_REJECTED",
      "GENERATION_PROVIDER_ERROR",
      "GENERATION_INVALID_CONTENT_TYPE",
      "GENERATION_RESPONSE_TOO_LARGE",
      "GENERATION_MALFORMED_JSON",
      "GENERATION_INVALID_RESPONSE",
    ]);
    for (const code of errorCodes) {
      const error = {
        code,
        message: "Generation provider execution failed.",
        details: { jobId: "generation_job_001", retryable: false, attempts: 1 },
        traceId: "trace_generation_provider_001",
      };
      expect(provider?.(error), `${code}: ${ajv.errorsText(provider?.errors)}`).toBe(true);
      expect(JSON.stringify(error)).not.toMatch(/credential|authorization|responseBody|endpoint/i);
    }
  });

  it("publishes only the canonical job route family and never implies a QA-ledger write", () => {
    const definitions = generationJobApiSchema.$defs;
    const requiredDefinitions = [
      "CreateGenerationJobRequest",
      "CreateGenerationJobResponse",
      "GenerationJobListQuery",
      "GenerationJobPageResponse",
      "GetGenerationJobResponse",
      "ExecuteGenerationJobRequest",
      "ExecuteGenerationJobResponse",
      "RetryGenerationJobRequest",
      "RetryGenerationJobResponse",
    ];
    expect(Object.keys(definitions)).toEqual(expect.arrayContaining(requiredDefinitions));

    const descriptions = requiredDefinitions
      .map((name) => JSON.stringify(definitions[name as keyof typeof definitions]))
      .join(" ");
    expect(JSON.stringify(definitions.CreateGenerationJobRequest)).toContain("POST /api/generation-jobs");
    expect(JSON.stringify(definitions.GenerationJobListQuery)).toContain("GET /api/generation-jobs");
    expect(JSON.stringify(definitions.ExecuteGenerationJobRequest)).toContain(
      "POST /api/generation-jobs/{jobId}/execute",
    );
    expect(JSON.stringify(definitions.RetryGenerationJobRequest)).toContain(
      "POST /api/generation-jobs/{jobId}/retry",
    );
    expect(descriptions).not.toContain("/slack-work-items/{id}/generate");

    const ajv = createValidator();
    const validateJob = ajv.getSchema(`${generationJobApiSchema.$id}#/$defs/GenerationJobResource`);
    const ledgerWritingJob = clone(queuedDetail.job) as unknown as JsonObject;
    ledgerWritingJob.createdCaseIds = ["test_case_provider_written"];
    ledgerWritingJob.auditId = "audit_generation_001";
    expect(validateJob?.(ledgerWritingJob)).toBe(false);
  });
});
