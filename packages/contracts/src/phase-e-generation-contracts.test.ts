import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import generationInputFixture from "../generation/fixtures/1.0.0/input.full-context.json";
import generationFixtureManifest from "../generation/fixtures/1.0.0/manifest.json";
import generationOutputFixture from "../generation/fixtures/1.0.0/output.grounded.json";
import generationInputSchema from "../generation/input/1.0.0/schema.json";
import generationOutputSchema from "../ai-test-case-generation-output.schema.json";
import stateEnumSchema from "../state-enums.schema.json";

const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const inputVersion = "1.0.0";
const outputVersion = "1.0.0";
const schemas = [stateEnumSchema, generationInputSchema, generationOutputSchema];

type JsonObject = Record<string, unknown>;

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

function asObjects(value: unknown): JsonObject[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const objects = value.map(asObject);
  return objects.every((object) => object !== undefined) ? (objects as JsonObject[]) : undefined;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function hasContiguousRanks(items: JsonObject[]) {
  return items.every((item, index) => item.rank === index + 1);
}

function hasContiguousSteps(value: unknown) {
  const output = asObject(value);
  const cases = asObjects(output?.cases);
  if (!cases) return false;
  return cases.every((testCase) => {
    const steps = asObjects(testCase.steps);
    return Boolean(steps?.every((step, index) => step.sequence === index + 1));
  });
}

function hasCaseInsensitiveUniqueStrings(values: unknown) {
  if (!Array.isArray(values) || !values.every((value) => typeof value === "string")) return false;
  const normalized = values.map((value) => value.trim().normalize("NFKC").toLowerCase());
  return new Set(normalized).size === normalized.length;
}

function inputSemanticsAreValid(value: unknown) {
  const input = asObject(value);
  const slackWorkItem = asObject(input?.slackWorkItem);
  const activeKnowledge = asObjects(input?.activeKnowledge);
  const existingCases = asObjects(input?.existingCases);
  const executions = asObjects(input?.failedOrBlockedExecutions);
  const issues = asObjects(input?.openIssues);
  const extraSources = asObjects(input?.extraSources);
  const categories = asObjects(input?.categoryCandidates);
  if (
    !input ||
    !slackWorkItem ||
    !activeKnowledge ||
    !existingCases ||
    !executions ||
    !issues ||
    !extraSources ||
    !categories
  ) {
    return false;
  }

  const structuredFields = ["developmentItem", "requestedMenu", "featureDescription", "requestDetails"];
  const missingFields = new Set(slackWorkItem.missingFields as unknown[]);
  if (!structuredFields.every((field) => missingFields.has(field) === (slackWorkItem[field] === null))) {
    return false;
  }

  const evidenceNodeIds = [
    slackWorkItem.evidenceNodeId,
    ...activeKnowledge.map((item) => item.nodeId),
    ...existingCases.map((item) => item.nodeId),
    ...executions.map((item) => item.nodeId),
    ...issues.map((item) => item.nodeId),
    ...extraSources.map((item) => item.evidenceNodeId),
  ];
  const categoryIds = categories.map((category) => category.categoryId);
  const allIdsAreStrings = [...evidenceNodeIds, ...categoryIds].every((id) => typeof id === "string");
  if (
    !allIdsAreStrings ||
    new Set(evidenceNodeIds).size !== evidenceNodeIds.length ||
    new Set(categoryIds).size !== categoryIds.length
  ) {
    return false;
  }

  if (
    !hasContiguousRanks(activeKnowledge) ||
    !hasContiguousRanks(existingCases) ||
    !hasContiguousRanks(executions) ||
    !hasContiguousRanks(issues)
  ) {
    return false;
  }

  return existingCases.every((testCase) => {
    const steps = asObjects(testCase.steps);
    return (
      Boolean(steps?.every((step, index) => step.sequence === index + 1)) &&
      hasCaseInsensitiveUniqueStrings(testCase.labels)
    );
  });
}

function trustedEvidenceNodeIds(inputValue: unknown) {
  const input = asObject(inputValue);
  const slackWorkItem = asObject(input?.slackWorkItem);
  const ids = new Set<unknown>([slackWorkItem?.evidenceNodeId]);
  for (const field of ["activeKnowledge", "existingCases", "failedOrBlockedExecutions", "openIssues"] as const) {
    for (const item of asObjects(input?.[field]) ?? []) ids.add(item.nodeId);
  }
  for (const source of asObjects(input?.extraSources) ?? []) ids.add(source.evidenceNodeId);
  return ids;
}

function outputIsGroundedInInput(inputValue: unknown, outputValue: unknown) {
  const input = asObject(inputValue);
  const output = asObject(outputValue);
  const cases = asObjects(output?.cases);
  const category = asObject(output?.categoryCandidate);
  const alternatives = asObjects(category?.alternatives);
  const categoryCandidates = asObjects(input?.categoryCandidates);
  if (!input || !output || !cases || !category || !alternatives || !categoryCandidates) return false;
  if (output.outputSchemaVersion !== input.requestedOutputSchemaVersion) return false;

  const allowedEvidenceIds = trustedEvidenceNodeIds(input);
  const allowedCategoryIds = new Set(categoryCandidates.map((candidate) => candidate.categoryId));
  const selectedCategoryIds = [category.categoryId, ...alternatives.map((alternative) => alternative.categoryId)];
  if (
    !selectedCategoryIds.every((id) => allowedCategoryIds.has(id)) ||
    new Set(selectedCategoryIds).size !== selectedCategoryIds.length
  ) {
    return false;
  }

  const summaries = cases.map((testCase) => testCase.summary);
  if (!hasCaseInsensitiveUniqueStrings(summaries) || !hasContiguousSteps(output)) return false;
  return cases.every((testCase) => {
    const evidenceIds = testCase.evidenceNodeIds;
    return (
      Array.isArray(evidenceIds) &&
      evidenceIds.every((id) => allowedEvidenceIds.has(id)) &&
      hasCaseInsensitiveUniqueStrings(testCase.labels)
    );
  });
}

describe("Phase E Generation API 1.0.0 contracts", () => {
  it("compiles the input and output schemas and every definition in strict Draft 2020-12 mode", () => {
    const ajv = createValidator();
    const contractSchemas = [generationInputSchema, generationOutputSchema];
    const ids = new Set<string>();

    for (const schema of contractSchemas) {
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

  it("accepts a synthetic full-context request and its grounded response", () => {
    expect(generationFixtureManifest.synthetic).toBe(true);
    expect(generationFixtureManifest.input).toBe("input.full-context.json");
    expect(generationFixtureManifest.output).toBe("output.grounded.json");
    const ajv = createValidator();
    const validateInput = ajv.getSchema(generationInputSchema.$id);
    const validateOutput = ajv.getSchema(generationOutputSchema.$id);

    expect(validateInput?.(generationInputFixture), ajv.errorsText(validateInput?.errors)).toBe(true);
    expect(inputSemanticsAreValid(generationInputFixture)).toBe(true);
    expect(validateOutput?.(generationOutputFixture), ajv.errorsText(validateOutput?.errors)).toBe(true);
    expect(outputIsGroundedInInput(generationInputFixture, generationOutputFixture)).toBe(true);
  });

  it("requires every provider-neutral context section and the exact input/output version pair", () => {
    const ajv = createValidator();
    const validateInput = ajv.getSchema(generationInputSchema.$id);

    for (const section of [
      "slackWorkItem",
      "activeKnowledge",
      "existingCases",
      "failedOrBlockedExecutions",
      "openIssues",
      "extraSources",
      "categoryCandidates",
      "generationPolicy",
    ]) {
      const request = clone(generationInputFixture) as JsonObject;
      delete request[section];
      expect(validateInput?.(request), `missing section should fail: ${section}`).toBe(false);
    }

    for (const [field, value] of [
      ["inputSchemaVersion", "1.0.1"],
      ["requestedOutputSchemaVersion", "2.0.0"],
    ] as const) {
      const request = clone(generationInputFixture) as JsonObject;
      request[field] = value;
      expect(validateInput?.(request), `unsupported ${field} should fail`).toBe(false);
    }

    const response = clone(generationOutputFixture) as JsonObject;
    response.outputSchemaVersion = "1.1.0";
    const validateOutput = ajv.getSchema(generationOutputSchema.$id);
    expect(validateOutput?.(response)).toBe(false);
  });

  it("admits only ACTIVE knowledge, FAIL/BLOCKED executions, open issues and user-provided extra sources", () => {
    const ajv = createValidator();
    const validateInput = ajv.getSchema(generationInputSchema.$id);

    const inactiveKnowledge = clone(generationInputFixture) as unknown as JsonObject;
    (inactiveKnowledge.activeKnowledge as JsonObject[])[0]!.status = "SUPERSEDED";
    expect(validateInput?.(inactiveKnowledge)).toBe(false);

    const passingExecution = clone(generationInputFixture) as unknown as JsonObject;
    (passingExecution.failedOrBlockedExecutions as JsonObject[])[0]!.status = "PASS";
    expect(validateInput?.(passingExecution)).toBe(false);

    const resolvedIssue = clone(generationInputFixture) as unknown as JsonObject;
    (resolvedIssue.openIssues as JsonObject[])[0]!.status = "RESOLVED";
    expect(validateInput?.(resolvedIssue)).toBe(false);

    const implicitExtraSource = clone(generationInputFixture) as unknown as JsonObject;
    (implicitExtraSource.extraSources as JsonObject[])[0]!.providedByUser = false;
    expect(validateInput?.(implicitExtraSource)).toBe(false);
  });

  it("rejects provider-specific request controls and provider-invented trusted output metadata", () => {
    const ajv = createValidator();
    const validateInput = ajv.getSchema(generationInputSchema.$id);
    const validateOutput = ajv.getSchema(generationOutputSchema.$id);

    for (const [field, value] of [
      ["provider", "openai"],
      ["model", "provider-model-name"],
      ["messages", [{ role: "user", content: "generate" }]],
      ["temperature", 0.2],
    ] as const) {
      const request = clone(generationInputFixture) as JsonObject;
      request[field] = value;
      expect(validateInput?.(request), `provider-specific input should fail: ${field}`).toBe(false);
    }

    for (const field of [
      "caseId",
      "caseKey",
      "workItemId",
      "generationJobId",
      "contextSnapshotId",
      "sourceRevisionIds",
      "sourceMetadata",
      "createdAt",
    ]) {
      const response = clone(generationOutputFixture) as unknown as JsonObject;
      (response.cases as JsonObject[])[0]![field] = field === "sourceRevisionIds" ? ["invented_revision"] : "invented";
      expect(validateOutput?.(response), `trusted provider-invented field should fail: ${field}`).toBe(false);
    }
  });

  it("requires the trusted adapter to reject evidence and category IDs absent from the input snapshot", () => {
    const ajv = createValidator();
    const validateOutput = ajv.getSchema(generationOutputSchema.$id);

    const inventedEvidence = clone(generationOutputFixture) as unknown as JsonObject;
    (inventedEvidence.cases as JsonObject[])[0]!.evidenceNodeIds = ["invented_evidence_node"];
    expect(validateOutput?.(inventedEvidence)).toBe(true);
    expect(outputIsGroundedInInput(generationInputFixture, inventedEvidence)).toBe(false);

    const inventedCategory = clone(generationOutputFixture) as unknown as JsonObject;
    (inventedCategory.categoryCandidate as JsonObject).categoryId = "folder_provider_invented";
    expect(validateOutput?.(inventedCategory)).toBe(true);
    expect(outputIsGroundedInInput(generationInputFixture, inventedCategory)).toBe(false);
  });

  it("enforces batch size structurally and contiguous steps semantically", () => {
    const ajv = createValidator();
    const validateOutput = ajv.getSchema(generationOutputSchema.$id);

    const tooFewCases = clone(generationOutputFixture) as unknown as JsonObject;
    (tooFewCases.cases as unknown[]).pop();
    expect(validateOutput?.(tooFewCases)).toBe(false);

    const nonContiguousSteps = clone(generationOutputFixture) as unknown as JsonObject;
    ((nonContiguousSteps.cases as JsonObject[])[0]!.steps as JsonObject[])[1]!.sequence = 3;
    expect(validateOutput?.(nonContiguousSteps)).toBe(true);
    expect(outputIsGroundedInInput(generationInputFixture, nonContiguousSteps)).toBe(false);
  });

  it("publishes exact current contract identities", () => {
    expect(generationInputSchema.$id).toBe(
      `https://qa-tool.local/schemas/generation/input/${inputVersion}/schema.json`,
    );
    expect(generationOutputSchema.$id).toBe(
      `https://qa-tool.local/schemas/generation/output/${outputVersion}/schema.json`,
    );
    expect(generationInputFixture.inputSchemaVersion).toBe(inputVersion);
    expect(generationInputFixture.requestedOutputSchemaVersion).toBe(outputVersion);
    expect(generationOutputFixture.outputSchemaVersion).toBe(outputVersion);
  });
});
