import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import coreApiSchema from "../core-api-payloads.schema.json";
import fixtureDocument from "../fixtures/sprint-3-cycle-execution-contracts.json";
import sprint1ErrorSchema from "../sprint-1/error-contract.schema.json";
import sprint2ErrorSchema from "../sprint-2/error-contract.schema.json";
import testCaseApiSchema from "../sprint-2/test-case-api.schema.json";
import cycleExecutionApiSchema from "../sprint-3/cycle-execution-api.schema.json";
import sprint3ErrorSchema from "../sprint-3/error-contract.schema.json";
import stateEnumSchema from "../state-enums.schema.json";

const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const schemas = [
  stateEnumSchema,
  sprint1ErrorSchema,
  sprint2ErrorSchema,
  testCaseApiSchema,
  cycleExecutionApiSchema,
  sprint3ErrorSchema,
  coreApiSchema,
];

type JsonObject = Record<string, unknown>;
type SemanticRule =
  | "UNIQUE_SCOPE_CASES"
  | "UNIQUE_CYCLE_CASES"
  | "PINNED_VERSION_CONSISTENCY"
  | "APPEND_ONLY_RETRY"
  | "LATEST_ATTEMPT_SEMANTICS"
  | "PROGRESS_TOTALS";
type ContractFixtureCase = {
  name: string;
  schemaRef: string;
  expectedValid: boolean;
  semanticRules?: SemanticRule[];
  payload: unknown;
};

function createValidator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  for (const schema of schemas) ajv.addSchema(schema);
  return ajv;
}

function asObject(value: unknown): JsonObject | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : undefined;
}

function uniqueCaseIds(payload: unknown) {
  const object = asObject(payload);
  if (!object || !Array.isArray(object.items)) return false;
  const caseIds = object.items.map((item) => asObject(item)?.testCaseId);
  return caseIds.every((caseId) => typeof caseId === "string") && new Set(caseIds).size === caseIds.length;
}

function pinnedVersionConsistency(payload: unknown) {
  const object = asObject(payload);
  if (!object || !Array.isArray(object.items)) return false;

  return object.items.every((item) => {
    const cycleItem = asObject(item);
    const pinned = asObject(cycleItem?.pinnedVersion);
    const latest = asObject(cycleItem?.latestVersion);
    if (!cycleItem || !pinned || !latest) return false;
    const versionsMatch = cycleItem.pinnedVersionId === pinned.id && cycleItem.testCaseId === pinned.testCaseId;
    const latestFlag = cycleItem.isLatestVersion === (cycleItem.pinnedVersionId === latest.versionId);
    return versionsMatch && latestFlag;
  });
}

function appendOnlyRetry(payload: unknown) {
  const object = asObject(payload);
  const previous = asObject(object?.previousAttempt);
  const attempt = asObject(object?.attempt);
  if (!previous || !attempt) return false;

  return (
    previous.id !== attempt.id &&
    previous.cycleItemId === attempt.cycleItemId &&
    previous.platform === attempt.platform &&
    previous.pinnedVersionId === attempt.pinnedVersionId &&
    typeof previous.attemptNo === "number" &&
    attempt.attemptNo === previous.attemptNo + 1 &&
    previous.isLatest === false &&
    attempt.isLatest === true &&
    attempt.status === "UNEXECUTED"
  );
}

function latestAttemptSemantics(payload: unknown) {
  const object = asObject(payload);
  if (!object || !Array.isArray(object.items) || !Array.isArray(object.latestAttemptIds)) return false;
  const attempts = object.items.map(asObject);
  if (attempts.some((attempt) => !attempt)) return false;

  const groups = new Map<string, JsonObject[]>();
  for (const attempt of attempts as JsonObject[]) {
    const key = `${String(attempt.cycleItemId)}:${String(attempt.platform)}`;
    groups.set(key, [...(groups.get(key) ?? []), attempt]);
  }

  const expectedLatestIds: string[] = [];
  for (const group of groups.values()) {
    const numbers = group.map((attempt) => attempt.attemptNo);
    if (!numbers.every((number) => typeof number === "number") || new Set(numbers).size !== numbers.length) {
      return false;
    }
    const maxAttemptNo = Math.max(...(numbers as number[]));
    const latest = group.filter((attempt) => attempt.isLatest === true);
    if (latest.length !== 1 || latest[0]?.attemptNo !== maxAttemptNo || typeof latest[0]?.id !== "string") return false;
    expectedLatestIds.push(latest[0].id as string);
  }

  return (
    new Set(object.latestAttemptIds).size === object.latestAttemptIds.length &&
    [...expectedLatestIds].sort().join("|") === [...(object.latestAttemptIds as string[])].sort().join("|")
  );
}

function progressTotals(payload: unknown) {
  let checked = 0;

  function walk(value: unknown): boolean {
    if (Array.isArray(value)) return value.every(walk);
    const object = asObject(value);
    if (!object) return true;
    const counts = asObject(object.counts);
    if (counts && typeof object.executedCount === "number" && typeof object.passRate === "number") {
      checked += 1;
      const statuses = ["UNEXECUTED", "PASS", "FAIL", "BLOCKED"] as const;
      if (!statuses.every((status) => typeof counts[status] === "number") || typeof counts.total !== "number") {
        return false;
      }
      const total = statuses.reduce((sum, status) => sum + (counts[status] as number), 0);
      const executed = total - (counts.UNEXECUTED as number);
      const expectedPassRate = executed === 0 ? 0 : (counts.PASS as number) / executed;
      if (counts.total !== total || object.executedCount !== executed || Math.abs(object.passRate - expectedPassRate) > 1e-9) {
        return false;
      }
    }
    return Object.values(object).every(walk);
  }

  return walk(payload) && checked > 0;
}

const semanticValidators: Record<SemanticRule, (payload: unknown) => boolean> = {
  UNIQUE_SCOPE_CASES: uniqueCaseIds,
  UNIQUE_CYCLE_CASES: uniqueCaseIds,
  PINNED_VERSION_CONSISTENCY: pinnedVersionConsistency,
  APPEND_ONLY_RETRY: appendOnlyRetry,
  LATEST_ATTEMPT_SEMANTICS: latestAttemptSemantics,
  PROGRESS_TOTALS: progressTotals,
};

describe("Sprint 3 cycle and execution public contracts", () => {
  it("compiles every Sprint 3 schema and definition in strict Draft 2020-12 mode", () => {
    const ajv = createValidator();
    const ids = new Set<string>();

    for (const schema of [cycleExecutionApiSchema, sprint3ErrorSchema]) {
      expect(schema.$schema).toBe(draft202012);
      expect(ids.has(schema.$id), `duplicate schema ID: ${schema.$id}`).toBe(false);
      ids.add(schema.$id);
      expect(ajv.getSchema(schema.$id), `schema did not compile: ${schema.$id}`).toBeTypeOf("function");
      for (const definitionName of Object.keys(schema.$defs)) {
        const definitionRef = `${schema.$id}#/$defs/${definitionName}`;
        expect(ajv.getSchema(definitionRef), `schema definition did not compile: ${definitionRef}`).toBeTypeOf(
          "function",
        );
      }
    }
  });

  it("accepts and rejects every synthetic fixture including normative ledger semantics", () => {
    expect(fixtureDocument.synthetic).toBe(true);
    const ajv = createValidator();

    for (const testCase of fixtureDocument.cases as ContractFixtureCase[]) {
      const validate = ajv.getSchema(testCase.schemaRef);
      expect(validate, `unknown schema ref for ${testCase.name}`).toBeTypeOf("function");
      const schemaValid = validate?.(testCase.payload) ?? false;
      const semanticValid = (testCase.semanticRules ?? []).every((rule) => semanticValidators[rule](testCase.payload));
      expect(
        schemaValid && semanticValid,
        `${testCase.name}: ${ajv.errorsText(validate?.errors)}; semantic=${String(semanticValid)}`,
      ).toBe(testCase.expectedValid);
    }
  });

  it("publishes every Sprint 3 endpoint wire family explicitly", () => {
    const requiredDefinitions = [
      "CreateCycleRequest",
      "CreateCycleResponse",
      "CycleListQuery",
      "ListCyclesResponse",
      "GetCycleResponse",
      "UpdateCycleRequest",
      "UpdateCycleResponse",
      "DeleteCycleRequest",
      "DeleteCycleResponse",
      "RestoreCycleRequest",
      "RestoreCycleResponse",
      "PermanentDeleteCycleRequest",
      "PermanentDeleteCycleResponse",
      "ScopeOptionListQuery",
      "ScopeOptionPageResponse",
      "ScopePreviewQuery",
      "ScopePreviewResponse",
      "UpdateCycleScopeRequest",
      "UpdateCycleScopeResponse",
      "StartCycleRequest",
      "StartCycleResponse",
      "CompleteCycleRequest",
      "CompleteCycleResponse",
      "ReopenCycleRequest",
      "ReopenCycleResponse",
      "PinnedCycleItemListQuery",
      "PinnedCycleItemPageResponse",
      "CycleItemListQuery",
      "CycleItemPageResponse",
      "RefreshCycleItemSnapshotRequest",
      "RefreshCycleItemSnapshotResponse",
      "StartExecutionAttemptRequest",
      "StartExecutionAttemptResponse",
      "CompleteExecutionAttemptRequest",
      "CompleteExecutionAttemptResponse",
      "RecordExecutionAttemptResultRequest",
      "RecordExecutionAttemptResultResponse",
      "RetryExecutionAttemptRequest",
      "RetryExecutionAttemptResponse",
      "ExecutionAttemptHistoryQuery",
      "ExecutionAttemptHistoryPageResponse",
      "RecordStepResultsRequest",
      "GetStepExecutionResponse",
      "RecordStepResultsResponse",
      "CycleProgressQuery",
      "CycleProgressResponse",
    ];
    expect(Object.keys(cycleExecutionApiSchema.$defs)).toEqual(expect.arrayContaining(requiredDefinitions));
    expect(cycleExecutionApiSchema.$defs).not.toHaveProperty("CycleItemLatestVersionDiffResponse");
    expect(cycleExecutionApiSchema.$defs).not.toHaveProperty("GetCycleItemLatestVersionDiffResponse");
    expect(cycleExecutionApiSchema.$defs).not.toHaveProperty("UpdateExecutionAttemptRequest");
  });

  it("pins the canonical cycle, scope, platform and execution enums", () => {
    expect(stateEnumSchema.$defs.CycleStatus.enum).toEqual(["PLANNED", "IN_PROGRESS", "COMPLETED"]);
    expect(stateEnumSchema.$defs.CycleScopeType.enum).toEqual(["WORK_ITEM", "LABEL"]);
    expect(stateEnumSchema.$defs.ExecutionPlatform.enum).toEqual(["WEB", "AOS", "IOS"]);
    expect(stateEnumSchema.$defs.ExecutionStatus.enum).toEqual(["UNEXECUTED", "PASS", "FAIL", "BLOCKED"]);
  });

  it("makes lifecycle gates, immutable pins, latest attempts and incomplete completion normative", () => {
    expect(cycleExecutionApiSchema.$defs.UpdateCycleScopeRequest.description).toContain("PLANNED-only");
    expect(cycleExecutionApiSchema.$defs.StartExecutionAttemptRequest.description).toContain("IN_PROGRESS-only");
    expect(cycleExecutionApiSchema.$defs.RecordStepResultsRequest.description).toContain("IN_PROGRESS-only");
    expect(cycleExecutionApiSchema.$defs.RecordStepResultsRequest.description).toContain("PLANNED");
    expect(cycleExecutionApiSchema.$defs.RecordStepResultsRequest.description).toContain("FAIL > BLOCKED > PASS");
    expect(cycleExecutionApiSchema.$defs.RefreshCycleItemSnapshotRequest.properties.confirm.const).toBe(true);
    expect(cycleExecutionApiSchema.$defs.ExecutionAttemptHistoryPageResponse.description).toContain("greatest attemptNo");
    expect(cycleExecutionApiSchema.$defs.CompleteCycleRequest.description).toContain("CYCLE_INCOMPLETE_WARNING");
    expect(sprint3ErrorSchema.$defs.CycleIncompleteWarningError.description).toContain("not a terminal prohibition");
  });

  it("rejects duplicate cycle cases, platform slots and attempt numbers", () => {
    const fixtures = fixtureDocument.cases as ContractFixtureCase[];
    const startPayload = structuredClone(
      fixtures.find((testCase) => testCase.name === "start cycle pins one immutable case version")?.payload,
    ) as JsonObject;
    const startItems = startPayload.items as unknown[];
    startItems.push(structuredClone(startItems[0]));
    expect(uniqueCaseIds(startPayload)).toBe(false);

    const cycleItem = asObject(startItems[0]);
    const platformExecutions = cycleItem?.platformExecutions as JsonObject[];
    platformExecutions[1] = structuredClone(platformExecutions[0] as JsonObject);
    const ajv = createValidator();
    const validatePlatformSet = ajv.getSchema(
      `${cycleExecutionApiSchema.$id}#/$defs/PlatformExecutionSet`,
    );
    expect(validatePlatformSet?.(platformExecutions), ajv.errorsText(validatePlatformSet?.errors)).toBe(false);

    const historyPayload = structuredClone(
      fixtures.find((testCase) => testCase.name === "attempt history identifies greatest attempt number as latest")
        ?.payload,
    ) as JsonObject;
    const historyItems = historyPayload.items as JsonObject[];
    historyItems[1] = { ...historyItems[1], attemptNo: 2 };
    expect(latestAttemptSemantics(historyPayload)).toBe(false);
  });

  it("keeps Sprint 1 VERSION_CONFLICT and Sprint 2 cursor errors wire-compatible", () => {
    const ajv = createValidator();
    const versionConflict = {
      code: "VERSION_CONFLICT",
      message: "Cycle was changed by another request.",
      details: { resourceType: "CYCLE", resourceId: "cycle_checkout", expectedVersion: 2, actualVersion: 3 },
      traceId: "trace_cycle_conflict",
    };
    expect(ajv.getSchema(`${sprint1ErrorSchema.$id}#/$defs/VersionConflictError`)?.(versionConflict)).toBe(true);
    expect(ajv.getSchema(`${sprint3ErrorSchema.$id}#/$defs/VersionConflictError`)?.(versionConflict)).toBe(true);

    const cursorFixture = (fixtureDocument.cases as ContractFixtureCase[]).find(
      (testCase) => testCase.name === "cursor invalid error remains typed",
    );
    expect(ajv.getSchema(`${sprint2ErrorSchema.$id}#/$defs/CursorInvalidError`)?.(cursorFixture?.payload)).toBe(true);
    expect(ajv.getSchema(`${sprint3ErrorSchema.$id}#/$defs/CursorInvalidError`)?.(cursorFixture?.payload)).toBe(true);
  });
});
