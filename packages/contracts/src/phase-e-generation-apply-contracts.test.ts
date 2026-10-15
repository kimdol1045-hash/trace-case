import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import generationApplyApiSchema from "../generation/apply/1.0.0/schema.json";
import generationOutputFixture from "../generation/fixtures/1.0.0/output.grounded.json";
import packageDocument from "../package.json";
import sprint1ErrorSchema from "../sprint-1/error-contract.schema.json";
import stateEnumSchema from "../state-enums.schema.json";
import { generationApplyApiV1Schema } from "./index";

const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const schemas = [stateEnumSchema, sprint1ErrorSchema, generationApplyApiSchema];
const jobId = "generation_job_001";
const contextDigest = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const resultDigest = "daed45b0fd52791216aa1bc0ff082e9867d9843ff7f5708a276438bc99dab07d";
const placementDigest = "b8bec6e02720c29ffad3b1445f805447e7f2faeb8bd65c86294731759092c658";
const exactContentKeys = [
  "eecf1a73bae9665b841b4336d5c9e704f6e3e5a0ca5e501c4817f9c165aa1738",
  "e784e1da1e6e1f171f07c2e348196be587aac88ed9d09e3325da40853afe9861",
  "7d5e1ea65c67436d0017d73c767606e281f8f122fae3451a2eb0cec7b9e01d68",
  "898d4a854bd0462c2751c6232b43c2b25768f769425f977f9face0fdb4341145",
  "3e0c459d83d4b28a334ec341b9e470dd165b8e33ced91f4802f658bf8e70af60",
] as const;

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

function canonicalize(value: unknown): string {
  if (value === null || typeof value !== "object") {
    const serialized = JSON.stringify(value);
    if (serialized === undefined) throw new TypeError("value is not JSON-canonicalizable");
    return serialized;
  }
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(",")}]`;
  const object = value as JsonObject;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalize(object[key])}`)
    .join(",")}}`;
}

async function sha256Hex(value: unknown) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalize(value)));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
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

function normalizedPrecondition(stepsValue: unknown) {
  const steps = asObjects(stepsValue) ?? [];
  const values: string[] = [];
  const seen = new Set<string>();
  for (const step of steps) {
    if (typeof step.precondition !== "string") continue;
    const normalized = step.precondition.trim().normalize("NFKC").toLowerCase();
    if (!seen.has(normalized)) {
      seen.add(normalized);
      values.push(step.precondition.trim());
    }
  }
  return values.length > 0 ? values.join("\n") : "[생성 결과에 명시된 공통 사전조건 없음]";
}

const revisionIdsByEvidence: Record<string, string[]> = {
  work_item_password_reset: [
    "slack_revision_password_root_1",
    "slack_revision_password_reply_1",
    "slack_revision_password_reply_2",
  ],
  test_case_password_reset_happy_path: ["case_version_password_reset_3"],
  decision_password_code_expiry: ["slack_revision_password_reply_1"],
  decision_password_attempt_lock: ["slack_revision_password_reply_2"],
  execution_password_lock_bypass: ["execution_attempt_password_lock_bypass"],
  issue_node_password_lock_bypass: ["issue_revision_password_lock_1"],
  extra_design_password_lock_state: ["extra_source_password_lock_design"],
};

function orderedSourceRevisionIds(evidenceValue: unknown) {
  if (!Array.isArray(evidenceValue)) return [];
  const values: string[] = [];
  const seen = new Set<string>();
  for (const evidenceId of evidenceValue) {
    if (typeof evidenceId !== "string") continue;
    for (const revisionId of revisionIdsByEvidence[evidenceId] ?? []) {
      if (!seen.has(revisionId)) {
        seen.add(revisionId);
        values.push(revisionId);
      }
    }
  }
  return values;
}

const applyRequest = {
  idempotencyKey: "generation-apply-001",
  expectedContextDigest: contextDigest,
  expectedResultDigest: resultDigest,
  placement: {
    placementSchemaVersion: "1.0.0",
    category: {
      categoryId: "folder_members",
      expectedResourceVersion: 4,
      method: "EXACT_NAME",
      score: 1,
    },
    parent: {
      decision: "REUSE_EXISTING",
      workItemId: "work_item_password_reset",
      expectedResourceVersion: 3,
      expectedCategoryId: "folder_members",
      method: "SLACK_SOURCE_IDENTITY",
      score: 1,
    },
    cases: exactContentKeys.map((exactContentKey, generatedCaseIndex) =>
      generatedCaseIndex === 0
        ? {
            generatedCaseIndex,
            exactContentKey,
            decision: "REUSE_EXISTING",
            testCaseId: "case_password_reset",
            expectedResourceVersion: 5,
            expectedCurrentVersionId: "case_version_password_reset_3",
            expectedCurrentVersion: 3,
            matchReason: "SEMANTIC",
          }
        : { generatedCaseIndex, exactContentKey, decision: "CREATE_NEW" },
    ),
  },
};

function createdOutcome(generatedCaseIndex: number) {
  const generated = generationOutputFixture.cases[generatedCaseIndex]!;
  const suffix = String(generatedCaseIndex + 1).padStart(3, "0");
  return {
    generatedCaseIndex,
    exactContentKey: exactContentKeys[generatedCaseIndex],
    outcome: "CREATED",
    ledgerMutation: "CREATED_CASE_AND_VERSION",
    case: {
      id: `case_generated_${suffix}`,
      key: `TC-${String(generatedCaseIndex + 10001).padStart(6, "0")}`,
      workItemId: "work_item_password_reset",
      categoryId: "folder_members",
      status: "ACTIVE",
      resourceVersion: 1,
      currentVersion: {
        id: `case_version_generated_${suffix}`,
        version: 1,
        summary: generated.summary,
        precondition: normalizedPrecondition(generated.steps),
        expectedResultMode: "CASE",
        caseExpectedResult: generated.caseExpectedResult,
        priority: generated.priority,
        labels: generated.labels,
        steps: generated.steps.map((step) => ({
          sequence: step.sequence,
          precondition: "precondition" in step ? step.precondition : null,
          action: step.action,
        })),
        generationReason: generated.generationReason,
        source: {
          sourceType: "SLACK_AI",
          slackWorkItemId: "slack_work_item_password_reset",
          generationJobId: jobId,
          contextDigest,
          resultDigest,
          outputSchemaVersion: "1.0.0",
          generationVersion: 1,
          evidenceNodeIds: generated.evidenceNodeIds,
          sourceRevisionIds: orderedSourceRevisionIds(generated.evidenceNodeIds),
          inferenceOnly: generated.inferenceOnly,
          openQuestions: generated.openQuestions,
        },
        createdAt: "2026-08-26T07:00:00Z",
      },
    },
  };
}

const applyResponse = {
  id: "generation_apply_001",
  generationJobId: jobId,
  jobStatus: "SUCCEEDED",
  contextDigest,
  resultDigest,
  placementDigest,
  transactionStatus: "COMMITTED",
  parent: {
    outcome: "REUSED",
    ledgerMutation: "NONE",
    workItemId: "work_item_password_reset",
    key: "WORK-1042",
    categoryId: "folder_members",
    resourceVersion: 3,
  },
  cases: [
    {
      generatedCaseIndex: 0,
      exactContentKey: exactContentKeys[0],
      outcome: "REUSED",
      ledgerMutation: "NONE",
      preserved: true,
      matchReason: "SEMANTIC",
      case: {
        id: "case_password_reset",
        key: "TC-001042",
        workItemId: "work_item_password_reset",
        categoryId: "folder_members",
        status: "ACTIVE",
        resourceVersion: 5,
        currentVersionId: "case_version_password_reset_3",
        currentVersion: 3,
      },
    },
    ...[1, 2, 3, 4].map(createdOutcome),
  ],
  counts: { generated: 5, created: 4, reused: 1 },
  auditId: "audit_generation_apply_001",
  appliedAt: "2026-08-26T07:00:00Z",
};

function placementMatchesStoredResult(requestValue: unknown) {
  const request = asObject(requestValue);
  const placement = asObject(request?.placement);
  const cases = asObjects(placement?.cases);
  if (!request || !placement || !cases || cases.length !== generationOutputFixture.cases.length) return false;
  return cases.every(
    (item, index) =>
      item.generatedCaseIndex === index && item.exactContentKey === exactContentKeys[index],
  );
}

function receiptIsAtomicAndFaithful(requestValue: unknown, responseValue: unknown) {
  const request = asObject(requestValue);
  const placement = asObject(request?.placement);
  const requestedCases = asObjects(placement?.cases);
  const response = asObject(responseValue);
  const outcomes = asObjects(response?.cases);
  const counts = asObject(response?.counts);
  if (!request || !requestedCases || !response || !outcomes || !counts) return false;
  if (
    response.generationJobId !== jobId ||
    response.contextDigest !== request.expectedContextDigest ||
    response.resultDigest !== request.expectedResultDigest ||
    outcomes.length !== generationOutputFixture.cases.length ||
    outcomes.length !== requestedCases.length
  ) {
    return false;
  }

  const created = outcomes.filter((outcome) => outcome.outcome === "CREATED").length;
  const reused = outcomes.filter((outcome) => outcome.outcome === "REUSED").length;
  if (counts.generated !== outcomes.length || counts.created !== created || counts.reused !== reused) return false;
  if (created + reused !== outcomes.length) return false;

  return outcomes.every((outcome, index) => {
    const requested = requestedCases[index]!;
    if (
      outcome.generatedCaseIndex !== index ||
      outcome.generatedCaseIndex !== requested.generatedCaseIndex ||
      outcome.exactContentKey !== requested.exactContentKey
    ) {
      return false;
    }
    if (outcome.outcome === "REUSED") {
      const existing = asObject(outcome.case);
      return (
        requested.decision === "REUSE_EXISTING" &&
        outcome.ledgerMutation === "NONE" &&
        outcome.preserved === true &&
        existing !== undefined &&
        existing.id === requested.testCaseId &&
        existing.currentVersionId === requested.expectedCurrentVersionId &&
        existing.currentVersion === requested.expectedCurrentVersion &&
        !("currentVersion" in outcome && typeof outcome.currentVersion === "object")
      );
    }

    const generated = generationOutputFixture.cases[index];
    const createdCase = asObject(outcome.case);
    const version = asObject(createdCase?.currentVersion);
    const source = asObject(version?.source);
    const steps = asObjects(version?.steps);
    return (
      requested.decision === "CREATE_NEW" &&
      outcome.ledgerMutation === "CREATED_CASE_AND_VERSION" &&
      createdCase?.status === "ACTIVE" &&
      createdCase.resourceVersion === 1 &&
      version?.version === 1 &&
      version.summary === generated?.summary &&
      version.precondition === normalizedPrecondition(generated?.steps) &&
      version.expectedResultMode === "CASE" &&
      version.caseExpectedResult === generated?.caseExpectedResult &&
      version.priority === generated?.priority &&
      JSON.stringify(version.labels) === JSON.stringify(generated?.labels) &&
      JSON.stringify(steps) ===
        JSON.stringify(
          generated?.steps.map((step) => ({
            sequence: step.sequence,
            precondition: "precondition" in step ? step.precondition : null,
            action: step.action,
          })),
        ) &&
      source?.sourceType === "SLACK_AI" &&
      source.generationJobId === jobId &&
      JSON.stringify(source.evidenceNodeIds) === JSON.stringify(generated?.evidenceNodeIds) &&
      JSON.stringify(source.sourceRevisionIds) ===
        JSON.stringify(orderedSourceRevisionIds(generated?.evidenceNodeIds))
    );
  });
}

function isSameApplyReplay(
  originalJobId: string,
  originalRequest: unknown,
  replayJobId: string,
  replayRequest: unknown,
) {
  return originalJobId === replayJobId && JSON.stringify(originalRequest) === JSON.stringify(replayRequest);
}

const rollbackError = {
  code: "GENERATION_APPLY_TRANSACTION_FAILED",
  message: "The generation apply transaction was rolled back.",
  details: {
    jobId,
    stage: "CREATE_VERSION",
    transactionStatus: "ROLLED_BACK",
  },
  traceId: "trace_generation_apply_rollback_001",
};

const rollbackEvidence = {
  transactionStatus: "ROLLED_BACK",
  rowDeltas: {
    workItems: 0,
    testCases: 0,
    testCaseVersions: 0,
    testSteps: 0,
    testCaseTags: 0,
    testCaseVersionTags: 0,
    generationApplyReceipts: 0,
    generationApplyOutcomes: 0,
    keyAllocations: 0,
    auditLogs: 0,
    outboxEvents: 0,
  },
};

describe("Phase E generation apply API 1.0.0 contracts", () => {
  it("compiles the schema and every definition in strict Draft 2020-12 mode", () => {
    const ajv = createValidator();

    expect(generationApplyApiSchema.$schema).toBe(draft202012);
    expect(generationApplyApiSchema.$id).toBe(
      "https://qa-tool.local/schemas/generation/apply/1.0.0/schema.json",
    );
    expect(ajv.getSchema(generationApplyApiSchema.$id)).toBeTypeOf("function");
    for (const definitionName of Object.keys(generationApplyApiSchema.$defs)) {
      const definitionRef = `${generationApplyApiSchema.$id}#/$defs/${definitionName}`;
      expect(ajv.getSchema(definitionRef), `schema definition did not compile: ${definitionRef}`).toBeTypeOf(
        "function",
      );
    }
  });

  it("publishes an immutable package and TypeScript index surface without changing the T09 schema", () => {
    expect(generationApplyApiV1Schema).toBe(generationApplyApiSchema);
    expect(packageDocument.exports).toHaveProperty(
      "./schemas/generation/apply/1.0.0",
      "./generation/apply/1.0.0/schema.json",
    );
    expect(JSON.stringify(generationApplyApiSchema.$defs.ApplyGenerationJobRequest)).toContain(
      "POST /api/generation-jobs/{jobId}/apply",
    );
  });

  it("accepts only a complete provider-neutral placement for the sealed result", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(`${generationApplyApiSchema.$id}#/$defs/ApplyGenerationJobRequest`);

    expect(validate?.(applyRequest), ajv.errorsText(validate?.errors)).toBe(true);
    expect(placementMatchesStoredResult(applyRequest)).toBe(true);

    for (const [field, value] of [
      ["provider", "unapproved-provider"],
      ["model", "unapproved-model"],
      ["endpoint", "https://provider.invalid/generate"],
      ["generatedCases", generationOutputFixture.cases],
    ] as const) {
      expect(validate?.({ ...applyRequest, [field]: value }), `apply accepted ${field}`).toBe(false);
    }

    const updateExisting = clone(applyRequest) as unknown as JsonObject;
    (asObjects(asObject(updateExisting.placement)?.cases) ?? [])[0]!.decision = "UPDATE_EXISTING";
    expect(validate?.(updateExisting)).toBe(false);

    const missingCase = clone(applyRequest) as unknown as JsonObject;
    (asObject(missingCase.placement)?.cases as unknown[]).pop();
    expect(validate?.(missingCase)).toBe(false);
    expect(placementMatchesStoredResult(missingCase)).toBe(false);

    const reordered = clone(applyRequest) as unknown as JsonObject;
    (asObject(reordered.placement)?.cases as unknown[]).reverse();
    expect(validate?.(reordered)).toBe(true);
    expect(placementMatchesStoredResult(reordered)).toBe(false);
  });

  it("pins RFC 8785-compatible result and normalized placement digests", async () => {
    expect(await sha256Hex(generationOutputFixture)).toBe(resultDigest);
    expect(await sha256Hex(applyRequest.placement)).toBe(placementDigest);
  });

  it("returns one committed created-or-reused outcome for every generated case", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(`${generationApplyApiSchema.$id}#/$defs/ApplyGenerationJobResponse`);

    expect(validate?.(applyResponse), ajv.errorsText(validate?.errors)).toBe(true);
    expect(receiptIsAtomicAndFaithful(applyRequest, applyResponse)).toBe(true);

    const partial = clone(applyResponse) as unknown as JsonObject;
    (partial.cases as unknown[]).pop();
    (partial.counts as JsonObject).generated = 4;
    (partial.counts as JsonObject).created = 3;
    expect(validate?.(partial)).toBe(false);
    expect(receiptIsAtomicAndFaithful(applyRequest, partial)).toBe(false);
  });

  it("preserves ordered steps, evidence and source provenance on newly created SLACK_AI versions", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(`${generationApplyApiSchema.$id}#/$defs/ApplyGenerationJobResponse`);

    const reorderedSteps = clone(applyResponse) as unknown as JsonObject;
    const created = (reorderedSteps.cases as JsonObject[])[1]!;
    const createdVersion = asObject(asObject(created.case)?.currentVersion)!;
    (createdVersion.steps as unknown[]).reverse();
    expect(validate?.(reorderedSteps)).toBe(true);
    expect(receiptIsAtomicAndFaithful(applyRequest, reorderedSteps)).toBe(false);

    const reorderedEvidence = clone(applyResponse) as unknown as JsonObject;
    const multiEvidenceVersion = asObject(
      asObject((reorderedEvidence.cases as JsonObject[])[3]!.case)?.currentVersion,
    )!;
    const source = asObject(multiEvidenceVersion.source)!;
    (source.evidenceNodeIds as unknown[]).reverse();
    expect(validate?.(reorderedEvidence)).toBe(true);
    expect(receiptIsAtomicAndFaithful(applyRequest, reorderedEvidence)).toBe(false);

    const wrongSource = clone(applyResponse) as unknown as JsonObject;
    const wrongVersion = asObject(asObject((wrongSource.cases as JsonObject[])[1]!.case)?.currentVersion)!;
    asObject(wrongVersion.source)!.sourceType = "WORKER";
    expect(validate?.(wrongSource)).toBe(false);
  });

  it("models reuse as a recorded relationship with zero mutation of the existing human-owned case", () => {
    const ajv = createValidator();
    const validateRequest = ajv.getSchema(
      `${generationApplyApiSchema.$id}#/$defs/ApplyGenerationJobRequest`,
    );
    const validateResponse = ajv.getSchema(
      `${generationApplyApiSchema.$id}#/$defs/ApplyGenerationJobResponse`,
    );
    const reused = applyResponse.cases[0] as JsonObject;

    expect(reused.outcome).toBe("REUSED");
    expect(reused.ledgerMutation).toBe("NONE");
    expect(reused.preserved).toBe(true);
    expect(reused).not.toHaveProperty("newVersion");

    const appendVersionRequest = clone(applyRequest) as unknown as JsonObject;
    (asObjects(asObject(appendVersionRequest.placement)?.cases) ?? [])[0]!.decision = "APPEND_VERSION";
    expect(validateRequest?.(appendVersionRequest)).toBe(false);

    const mutatingReceipt = clone(applyResponse) as unknown as JsonObject;
    (mutatingReceipt.cases as JsonObject[])[0]!.ledgerMutation = "APPENDED_VERSION";
    (mutatingReceipt.cases as JsonObject[])[0]!.newVersionId = "case_version_human_overwritten";
    expect(validateResponse?.(mutatingReceipt)).toBe(false);
  });

  it("allows only SUCCEEDED and publishes stable state, stored-result and placement conflict shapes", () => {
    const ajv = createValidator();
    const stateConflict = ajv.getSchema(
      `${generationApplyApiSchema.$id}#/$defs/GenerationApplyStateConflictError`,
    );
    const storedInvalid = ajv.getSchema(
      `${generationApplyApiSchema.$id}#/$defs/GenerationStoredResultInvalidError`,
    );
    const placementConflict = ajv.getSchema(
      `${generationApplyApiSchema.$id}#/$defs/GenerationPlacementConflictError`,
    );

    expect(stateConflict?.({
      code: "GENERATION_JOB_STATE_CONFLICT",
      message: "The generation job is not eligible for apply.",
      details: {
        jobId,
        operation: "APPLY",
        actualStatus: "RUNNING",
        allowedStatuses: ["SUCCEEDED"],
        ledgerWriteStarted: false,
      },
      traceId: "trace_generation_apply_state_001",
    })).toBe(true);
    expect(storedInvalid?.({
      code: "GENERATION_STORED_RESULT_INVALID",
      message: "The stored result failed trusted validation.",
      details: {
        jobId,
        stage: "GROUNDING",
        reason: "EVIDENCE_NOT_IN_SEALED_CONTEXT",
        ledgerWriteStarted: false,
      },
      traceId: "trace_generation_apply_result_001",
    })).toBe(true);
    expect(placementConflict?.({
      code: "GENERATION_PLACEMENT_CONFLICT",
      message: "The selected reuse case changed before apply.",
      details: {
        jobId,
        reason: "REUSE_CANDIDATE_STALE",
        generatedCaseIndex: 0,
        resourceType: "TEST_CASE",
        resourceId: "case_password_reset",
        ledgerWriteStarted: false,
      },
      traceId: "trace_generation_apply_placement_001",
    })).toBe(true);
  });

  it("defines exact replay semantics and rejects a second key for an already-applied job", () => {
    const ajv = createValidator();
    const alreadyApplied = ajv.getSchema(
      `${generationApplyApiSchema.$id}#/$defs/GenerationJobAlreadyAppliedError`,
    );

    expect(isSameApplyReplay(jobId, applyRequest, jobId, clone(applyRequest))).toBe(true);
    expect(isSameApplyReplay(jobId, applyRequest, "generation_job_002", applyRequest)).toBe(false);
    expect(
      isSameApplyReplay(jobId, applyRequest, jobId, {
        ...applyRequest,
        idempotencyKey: "generation-apply-002",
      }),
    ).toBe(false);

    expect(alreadyApplied?.({
      code: "GENERATION_JOB_ALREADY_APPLIED",
      message: "The generation job already has a committed apply receipt.",
      details: { jobId, applyId: "generation_apply_001" },
      traceId: "trace_generation_already_applied_001",
    })).toBe(true);
  });

  it("requires rollback evidence to show zero committed partial rows across every apply-owned table", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationApplyApiSchema.$id}#/$defs/GenerationApplyTransactionFailedError`,
    );
    const validateEvidence = ajv.getSchema(
      `${generationApplyApiSchema.$id}#/$defs/RollbackEvidence`,
    );

    expect(validate?.(rollbackError), ajv.errorsText(validate?.errors)).toBe(true);
    expect(validateEvidence?.(rollbackEvidence), ajv.errorsText(validateEvidence?.errors)).toBe(true);
    const partialWrite = clone(rollbackEvidence) as unknown as JsonObject;
    asObject(partialWrite.rowDeltas)!.testCaseVersions = 1;
    expect(validateEvidence?.(partialWrite)).toBe(false);
  });
});
