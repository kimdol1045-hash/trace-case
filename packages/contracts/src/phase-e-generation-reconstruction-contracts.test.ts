import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import availableFixture from "../generation/fixtures/1.0.0/reconstruction.available.json";
import incompleteFixture from "../generation/fixtures/1.0.0/reconstruction.incomplete-unavailable.json";
import legacyFixture from "../generation/fixtures/1.0.0/reconstruction.legacy-unavailable.json";
import unsupportedVersionFixture from "../generation/fixtures/1.0.0/reconstruction.unsupported-version.json";
import generationInputFixture from "../generation/fixtures/1.0.0/input.full-context.json";
import generationInputSchema from "../generation/input/1.0.0/schema.json";
import generationReconstructionSchema from "../generation/reconstruction/1.0.0/schema.json";
import packageDocument from "../package.json";
import sprint1ErrorSchema from "../sprint-1/error-contract.schema.json";
import stateEnumSchema from "../state-enums.schema.json";
import { generationReconstructionV1Schema } from "./index";

const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const schemas = [
  stateEnumSchema,
  sprint1ErrorSchema,
  generationInputSchema,
  generationReconstructionSchema,
];

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

async function canonicalDigest(value: unknown) {
  const bytes = new TextEncoder().encode(canonicalize(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return {
    value: [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join(""),
    byteLength: bytes.byteLength,
  };
}

async function availableSemanticsAreValid(value: unknown) {
  const response = asObject(value);
  const input = asObject(response?.input);
  const provenance = asObject(response?.provenance);
  const inputDigest = asObject(provenance?.inputDigest);
  if (
    response?.availability !== "AVAILABLE"
    || !input
    || response.recordedInputSchemaVersion !== input.inputSchemaVersion
    || !inputDigest
  ) {
    return false;
  }
  const digest = await canonicalDigest(input);
  return (
    inputDigest.algorithm === "SHA-256"
    && inputDigest.canonicalization === "RFC8785"
    && inputDigest.value === digest.value
    && inputDigest.canonicalByteLength === digest.byteLength
  );
}

describe("PE-SE1-T12 generation reconstruction 1.0.0 contract", () => {
  it("compiles the schema and every definition in strict Draft 2020-12 mode", () => {
    const ajv = createValidator();

    expect(generationReconstructionSchema.$schema).toBe(draft202012);
    expect(generationReconstructionSchema.$id).toBe(
      "https://qa-tool.local/schemas/generation/reconstruction/1.0.0/schema.json",
    );
    expect(ajv.getSchema(generationReconstructionSchema.$id)).toBeTypeOf("function");
    for (const definitionName of Object.keys(generationReconstructionSchema.$defs)) {
      const definitionRef = `${generationReconstructionSchema.$id}#/$defs/${definitionName}`;
      expect(
        ajv.getSchema(definitionRef),
        `schema definition did not compile: ${definitionRef}`,
      ).toBeTypeOf("function");
    }
  });

  it("publishes the versioned package and TypeScript index surfaces", () => {
    expect(generationReconstructionV1Schema).toBe(generationReconstructionSchema);
    expect(packageDocument.exports).toHaveProperty(
      "./schemas/generation/reconstruction/1.0.0",
      "./generation/reconstruction/1.0.0/schema.json",
    );
  });

  it("returns the exact trusted input with verified RFC 8785 SHA-256 provenance", async () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationReconstructionSchema.$id}#/$defs/GetGenerationReconstructionResponse`,
    );

    expect(validate?.(availableFixture), ajv.errorsText(validate?.errors)).toBe(true);
    expect(availableFixture.input).toEqual(generationInputFixture);
    expect(await availableSemanticsAreValid(availableFixture)).toBe(true);
    expect(availableFixture.provenance.inputDigest).toEqual({
      algorithm: "SHA-256",
      canonicalization: "RFC8785",
      value: "792de6c007dd69ef8e7f7e80b2038f2c41d9c5ce9d11e4c4207a2bce72731ab3",
      canonicalByteLength: 5556,
    });
  });

  it("binds the digest to array order and JSON null/empty distinctions", async () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationReconstructionSchema.$id}#/$defs/GetGenerationReconstructionResponse`,
    );
    const reordered = clone(availableFixture) as unknown as JsonObject;
    const reorderedInput = reordered.input as JsonObject;
    (reorderedInput.activeKnowledge as unknown[]).reverse();

    expect(validate?.(reordered), ajv.errorsText(validate?.errors)).toBe(true);
    expect(await availableSemanticsAreValid(reordered)).toBe(false);
    expect(canonicalize(null)).not.toBe(canonicalize([]));
    expect(availableFixture.input.existingCases[0]?.steps[0]?.precondition).toBeNull();
    expect(availableFixture.input.failedOrBlockedExecutions[1]?.linkedIssueIds).toEqual([]);
  });

  it("exposes stable legacy, incomplete, and unsupported-version states without a live fallback", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationReconstructionSchema.$id}#/$defs/GetGenerationReconstructionResponse`,
    );

    for (const fixture of [legacyFixture, incompleteFixture, unsupportedVersionFixture]) {
      expect(validate?.(fixture), ajv.errorsText(validate?.errors)).toBe(true);
      expect(fixture.availability).toBe("UNAVAILABLE");
      expect(fixture.input).toBeNull();
      expect(fixture.policy.sealedSnapshotsOnly).toBe(true);
      expect(
        Object.entries(fixture.policy)
          .filter(([name]) => name.startsWith("live"))
          .every(([, value]) => value === false),
      ).toBe(true);
    }

    expect(legacyFixture.reason).toBe("LEGACY_INPUT_NOT_SEALED");
    expect(legacyFixture.recordedInputSchemaVersion).toBeNull();
    expect(legacyFixture.provenance.inputDigest).toBeNull();
    expect(incompleteFixture.reason).toBe("SEALED_INPUT_INCOMPLETE");
    expect(incompleteFixture.provenance.inputDigest).toBeNull();
    expect(unsupportedVersionFixture.reason).toBe("UNSUPPORTED_INPUT_SCHEMA_VERSION");
    expect(unsupportedVersionFixture.recordedInputSchemaVersion).toBe("2.0.0");
    expect(unsupportedVersionFixture.provenance.inputDigest).not.toBeNull();
  });

  it("does not let one unavailable state impersonate another", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationReconstructionSchema.$id}#/$defs/GetGenerationReconstructionResponse`,
    );

    expect(validate?.({ ...legacyFixture, recordedInputSchemaVersion: "1.0.0" })).toBe(false);
    expect(validate?.({ ...unsupportedVersionFixture, recordedInputSchemaVersion: "1.0.0" })).toBe(false);
    expect(validate?.({ ...incompleteFixture, input: generationInputFixture })).toBe(false);
    expect(validate?.({
      ...legacyFixture,
      provenance: { ...legacyFixture.provenance, inputDigest: availableFixture.provenance.inputDigest },
    })).toBe(false);
  });

  it("defines one GET boundary and forbids providers, live sources, results, applies, and mutation", () => {
    const responseDefinition = generationReconstructionSchema.$defs.GenerationReconstructionResponse;
    expect(responseDefinition.description).toContain(
      "GET /api/generation-jobs/{jobId}/reconstruction",
    );
    expect(generationReconstructionSchema.$defs.ReconstructionReadPolicy.properties).toMatchObject({
      sourceBasis: { const: "SEALED_GENERATION_INPUT" },
      readOnly: { const: true },
      sealedSnapshotsOnly: { const: true },
      liveSlackConsulted: { const: false },
      liveKnowledgeConsulted: { const: false },
      liveCasesConsulted: { const: false },
      liveIssuesConsulted: { const: false },
      liveCategoriesConsulted: { const: false },
      providerCallAttempted: { const: false },
      mutationPerformed: { const: false },
    });

    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationReconstructionSchema.$id}#/$defs/GetGenerationReconstructionResponse`,
    );
    for (const field of ["provider", "model", "result", "apply", "contextItems", "liveSource"] as const) {
      expect(validate?.({ ...availableFixture, [field]: {} }), `accepted ${field}`).toBe(false);
    }
    expect(validate?.({
      ...availableFixture,
      policy: { ...availableFixture.policy, providerCallAttempted: true },
    })).toBe(false);
    expect(validate?.({
      ...availableFixture,
      policy: { ...availableFixture.policy, mutationPerformed: true },
    })).toBe(false);
  });
});
