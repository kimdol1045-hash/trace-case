import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import coreApiSchema from "../core-api-payloads.schema.json";
import fixtureDocument from "../fixtures/sprint-2-test-case-contracts.json";
import sprint1ErrorSchema from "../sprint-1/error-contract.schema.json";
import sprint2ErrorSchema from "../sprint-2/error-contract.schema.json";
import testCaseApiSchema from "../sprint-2/test-case-api.schema.json";
import stateEnumSchema from "../state-enums.schema.json";

const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const schemas = [stateEnumSchema, sprint1ErrorSchema, testCaseApiSchema, sprint2ErrorSchema, coreApiSchema];

type JsonObject = Record<string, unknown>;
type SemanticRule =
  | "CASE_INSENSITIVE_LABELS"
  | "CONTIGUOUS_STEPS"
  | "RESOURCE_VERSION_CONSISTENCY"
  | "RESTORE_APPENDS_VERSION"
  | "COPY_HISTORY_ISOLATION";
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
  for (const schema of schemas) {
    ajv.addSchema(schema);
  }
  return ajv;
}

function walkObjects(value: unknown, visit: (object: JsonObject) => void) {
  if (Array.isArray(value)) {
    for (const item of value) walkObjects(item, visit);
    return;
  }
  if (typeof value !== "object" || value === null) return;
  const object = value as JsonObject;
  visit(object);
  for (const child of Object.values(object)) walkObjects(child, visit);
}

function hasCaseInsensitiveUniqueLabels(payload: unknown) {
  let valid = true;
  walkObjects(payload, (object) => {
    if (!Array.isArray(object.labels)) return;
    const normalized = object.labels.map((label) =>
      typeof label === "string" ? label.trim().normalize("NFKC").toLowerCase() : label,
    );
    if (new Set(normalized).size !== normalized.length) valid = false;
  });
  return valid;
}

function hasContiguousSteps(payload: unknown) {
  let valid = true;
  walkObjects(payload, (object) => {
    if (!Array.isArray(object.steps)) return;
    const sequences = object.steps.map((step) =>
      typeof step === "object" && step !== null ? (step as JsonObject).sequence : undefined,
    );
    if (!sequences.every((sequence, index) => sequence === index + 1)) valid = false;
  });
  return valid;
}

function hasConsistentResourceVersions(payload: unknown) {
  let valid = true;
  walkObjects(payload, (object) => {
    if (!("currentVersion" in object) || typeof object.currentVersion !== "object" || object.currentVersion === null) {
      return;
    }
    const currentVersion = object.currentVersion as JsonObject;
    if (currentVersion.testCaseId !== object.id || currentVersion.version !== object.version) valid = false;
    if (typeof object.resourceVersion !== "number" || object.resourceVersion < (object.version as number)) valid = false;
  });
  return valid;
}

function restoreAppendsVersion(payload: unknown) {
  if (typeof payload !== "object" || payload === null) return false;
  const response = payload as JsonObject;
  if (typeof response.testCase !== "object" || response.testCase === null) return false;
  const testCase = response.testCase as JsonObject;
  if (typeof testCase.currentVersion !== "object" || testCase.currentVersion === null) return false;
  const currentVersion = testCase.currentVersion as JsonObject;
  return (
    currentVersion.restoredFromVersion === response.restoredFromVersion &&
    typeof currentVersion.version === "number" &&
    typeof response.restoredFromVersion === "number" &&
    currentVersion.version > response.restoredFromVersion
  );
}

function copyHistoryIsIsolated(payload: unknown) {
  if (typeof payload !== "object" || payload === null) return false;
  const response = payload as JsonObject;
  if (typeof response.testCase !== "object" || response.testCase === null) return false;
  if (typeof response.copiedFrom !== "object" || response.copiedFrom === null) return false;
  const testCase = response.testCase as JsonObject;
  const copiedFrom = response.copiedFrom as JsonObject;
  if (typeof testCase.currentVersion !== "object" || testCase.currentVersion === null) return false;
  const currentVersion = testCase.currentVersion as JsonObject;
  return (
    testCase.id !== copiedFrom.testCaseId &&
    testCase.version === 1 &&
    testCase.resourceVersion === 1 &&
    currentVersion.version === 1 &&
    currentVersion.testCaseId === testCase.id &&
    JSON.stringify(testCase.copiedFrom) === JSON.stringify(copiedFrom)
  );
}

const semanticValidators: Record<SemanticRule, (payload: unknown) => boolean> = {
  CASE_INSENSITIVE_LABELS: hasCaseInsensitiveUniqueLabels,
  CONTIGUOUS_STEPS: hasContiguousSteps,
  RESOURCE_VERSION_CONSISTENCY: hasConsistentResourceVersions,
  RESTORE_APPENDS_VERSION: restoreAppendsVersion,
  COPY_HISTORY_ISOLATION: copyHistoryIsIsolated,
};

describe("Sprint 2 test case public contracts", () => {
  it("compiles every schema and definition in strict Draft 2020-12 mode", () => {
    const ajv = createValidator();
    const ids = new Set<string>();

    for (const schema of [testCaseApiSchema, sprint2ErrorSchema]) {
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

  it("accepts and rejects every synthetic API fixture including normative semantic rules", () => {
    expect(fixtureDocument.synthetic).toBe(true);
    const ajv = createValidator();

    for (const testCase of fixtureDocument.cases as ContractFixtureCase[]) {
      const validate = ajv.getSchema(testCase.schemaRef);
      expect(validate, `unknown schema ref for ${testCase.name}`).toBeTypeOf("function");
      const schemaValid = validate?.(testCase.payload) ?? false;
      const semanticValid = (testCase.semanticRules ?? []).every((rule) => semanticValidators[rule](testCase.payload));
      const actual = schemaValid && semanticValid;
      expect(
        actual,
        `${testCase.name}: ${ajv.errorsText(validate?.errors)}; semantic=${String(semanticValid)}`,
      ).toBe(testCase.expectedValid);
    }
  });

  it("defines every Sprint 2 endpoint payload and response without ambiguous restore names", () => {
    const requiredDefinitions = [
      "CreateWorkItemRequest",
      "UpdateWorkItemRequest",
      "WorkItemResource",
      "WorkItemPageResponse",
      "WorkItemDetailResponse",
      "CreateTestCaseRequest",
      "CreateTestCaseResponse",
      "GetTestCaseResponse",
      "GetTestCaseVersionResponse",
      "UpdateTestCaseRequest",
      "UpdateTestCaseResponse",
      "TestCaseResource",
      "TestCaseListQuery",
      "TestCasePageResponse",
      "TestCaseVersionListQuery",
      "TestCaseVersionPageResponse",
      "TestCaseVersionDiffQuery",
      "TestCaseVersionDiffResponse",
      "RestoreTestCaseVersionRequest",
      "RestoreTestCaseVersionResponse",
      "MoveTestCaseRequest",
      "MoveTestCaseResponse",
      "CopyTestCaseRequest",
      "CopyTestCaseResponse",
      "SoftDeleteTestCaseRequest",
      "SoftDeleteTestCaseResponse",
      "RestoreDeletedTestCaseRequest",
      "RestoreDeletedTestCaseResponse",
      "PermanentDeleteTestCaseRequest",
      "PermanentDeleteTestCaseResponse",
    ];
    expect(Object.keys(testCaseApiSchema.$defs)).toEqual(expect.arrayContaining(requiredDefinitions));
    expect(testCaseApiSchema.$defs).not.toHaveProperty("RestoreTestCaseRequest");
    expect(testCaseApiSchema.$defs.StepSnapshot.properties).toHaveProperty("precondition");
  });

  it("keeps work-item creation canonical while reading persisted external keys", () => {
    const ajv = createValidator();
    const create = ajv.getSchema(`${testCaseApiSchema.$id}#/$defs/CreateWorkItemRequest`);
    const resource = ajv.getSchema(`${testCaseApiSchema.$id}#/$defs/WorkItemResource`);
    const persisted = {
      id: "work_item_77777777777747778777777777777777",
      key: "LIVE-301",
      categoryId: "folder_77777777777747778777777777777777",
      summary: "Sprint 3 live smoke",
      description: "",
      ownerLabel: null,
      labels: [],
      resourceVersion: 1,
      createdAt: "2026-08-20T00:00:00Z",
      updatedAt: "2026-08-20T00:00:00Z",
    };

    expect(resource?.(persisted)).toBe(true);
    expect(create?.({
      key: persisted.key,
      categoryId: persisted.categoryId,
      summary: persisted.summary,
    })).toBe(false);
  });

  it("keeps the legacy core create/update names valid while namespacing the Sprint 2 contract by schema ID", () => {
    const ajv = createValidator();
    const legacyCreateRef = `${coreApiSchema.$id}#/$defs/CreateTestCaseRequest`;
    const sprint2CreateRef = `${testCaseApiSchema.$id}#/$defs/CreateTestCaseRequest`;
    expect(legacyCreateRef).not.toBe(sprint2CreateRef);

    const legacyCreate = ajv.getSchema(legacyCreateRef);
    expect(
      legacyCreate?.({
        folderId: "folder_orders",
        status: "ACTIVE",
        summary: "Legacy case payload",
        precondition: "A cart exists.",
        steps: [{ sequence: 1, action: "Open checkout." }],
        expectedResult: "Checkout opens.",
        source: { type: "MANUAL" },
      }),
      ajv.errorsText(legacyCreate?.errors),
    ).toBe(true);
  });

  it("reuses the exact Sprint 1 VERSION_CONFLICT wire shape", () => {
    const ajv = createValidator();
    const conflict = (fixtureDocument.cases as ContractFixtureCase[]).find(
      (testCase) => testCase.name === "test case VERSION_CONFLICT error",
    );
    expect(conflict).toBeDefined();
    const legacy = ajv.getSchema(`${sprint1ErrorSchema.$id}#/$defs/VersionConflictError`);
    const sprint2 = ajv.getSchema(`${sprint2ErrorSchema.$id}#/$defs/VersionConflictError`);
    expect(legacy?.(conflict?.payload)).toBe(true);
    expect(sprint2?.(conflict?.payload)).toBe(true);
  });

  it("pins the initial key/status, result modes, source channels and query semantics", () => {
    const initial = (fixtureDocument.cases as ContractFixtureCase[]).find(
      (testCase) => testCase.name === "create response allocates TC-000001 ACTIVE",
    )?.payload as JsonObject;
    expect(initial.key).toBe("TC-000001");
    expect(initial.status).toBe("ACTIVE");
    expect(stateEnumSchema.$defs.ExpectedResultMode.enum).toEqual(["PER_STEP", "CASE"]);
    expect(stateEnumSchema.$defs.TestCaseVersionSourceType.enum).toEqual(["UI", "API", "SYSTEM", "SLACK", "WORKER"]);
    expect(testCaseApiSchema.$defs.TestCaseListQuery.description).toContain("different supplied filters are ANDed");
    expect(testCaseApiSchema.$defs.Label.description).toContain("case-folded");
  });
});
