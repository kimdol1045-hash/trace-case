import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import generationInputSchema from "../generation/input/1.0.0/schema.json";
import generationOutputSchema from "../ai-test-case-generation-output.schema.json";
import generationInputFixture from "../generation/fixtures/1.0.0/input.full-context.json";
import generationOutputFixture from "../generation/fixtures/1.0.0/output.grounded.json";
import suggestionSchema from "../generation/suggestion/1.0.0/schema.json";
import stateEnumSchema from "../state-enums.schema.json";

const targetRef = `generation_suggestion_target_${"a".repeat(32)}`;
const target = {
  suggestionTargetRef: targetRef,
  testCaseId: "case_password_reset",
  testCaseVersionId: "case_version_password_reset_3",
  generationLineageId: "generation_lineage_password_reset",
  targetDigest: "b".repeat(64),
};

function validator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  ajv.addSchema(stateEnumSchema);
  ajv.addSchema(generationInputSchema);
  ajv.addSchema(generationOutputSchema);
  ajv.addSchema(suggestionSchema);
  return ajv;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

describe("PE-SE2-T05 server-issued suggestion target contract", () => {
  it("accepts a sealed input envelope and an output that only echoes its opaque target ref", () => {
    const ajv = validator();
    const input = {
      inputSchemaVersion: "generation-suggestion/1.0.0",
      requestedOutputSchemaVersion: "generation-suggestion/1.0.0",
      generationJobId: "generation_job_password_reset_4",
      generationChangeAssessmentId: "generation_change_assessment_password_reset_4",
      baseGenerationInput: generationInputFixture,
      suggestionTargets: [target],
    };
    const output = {
      outputSchemaVersion: "generation-suggestion/1.0.0",
      suggestions: [
        {
          suggestionTargetRef: targetRef,
          candidate: generationOutputFixture.cases[0],
        },
      ],
    };

    expect(
      ajv.validate(
        "https://qa-tool.local/schemas/generation/suggestion/1.0.0/schema.json#/$defs/GenerationSuggestionInput",
        input,
      ),
      JSON.stringify(ajv.errors),
    ).toBe(true);
    expect(
      ajv.validate(
        "https://qa-tool.local/schemas/generation/suggestion/1.0.0/schema.json#/$defs/GenerationSuggestionOutput",
        output,
      ),
      JSON.stringify(ajv.errors),
    ).toBe(true);
  });

  it("rejects exact/semantic candidate content when the server target ref is missing", () => {
    const ajv = validator();
    const missingRef = {
      outputSchemaVersion: "generation-suggestion/1.0.0",
      suggestions: [{ candidate: generationOutputFixture.cases[0] }],
    };
    const spoofedAuthority = {
      outputSchemaVersion: "generation-suggestion/1.0.0",
      suggestions: [
        {
          suggestionTargetRef: targetRef,
          candidate: generationOutputFixture.cases[0],
          testCaseId: target.testCaseId,
          generationLineageId: target.generationLineageId,
          matchReason: "SEMANTIC",
        },
      ],
    };

    expect(
      ajv.validate(
        "https://qa-tool.local/schemas/generation/suggestion/1.0.0/schema.json#/$defs/GenerationSuggestionOutput",
        missingRef,
      ),
    ).toBe(false);
    expect(
      ajv.validate(
        "https://qa-tool.local/schemas/generation/suggestion/1.0.0/schema.json#/$defs/GenerationSuggestionOutput",
        spoofedAuthority,
      ),
    ).toBe(false);
  });

  it("does not let the provider alter or duplicate the server-issued target set", () => {
    const ajv = validator();
    const duplicate = {
      inputSchemaVersion: "generation-suggestion/1.0.0",
      requestedOutputSchemaVersion: "generation-suggestion/1.0.0",
      generationJobId: "generation_job_password_reset_4",
      generationChangeAssessmentId: "generation_change_assessment_password_reset_4",
      baseGenerationInput: generationInputFixture,
      suggestionTargets: [target, clone(target)],
    };
    const targetWithCallerAuthority = {
      ...target,
      source: { type: "WORKER" },
      similarity: 1,
    };
    const spoofed = { ...duplicate, suggestionTargets: [targetWithCallerAuthority] };

    expect(
      ajv.validate(
        "https://qa-tool.local/schemas/generation/suggestion/1.0.0/schema.json#/$defs/GenerationSuggestionInput",
        duplicate,
      ),
    ).toBe(false);

    const duplicateEcho = {
      outputSchemaVersion: "generation-suggestion/1.0.0",
      suggestions: [
        {
          suggestionTargetRef: targetRef,
          candidate: generationOutputFixture.cases[0],
        },
        {
          suggestionTargetRef: targetRef,
          candidate: generationOutputFixture.cases[0],
        },
      ],
    };
    expect(
      ajv.validate(
        "https://qa-tool.local/schemas/generation/suggestion/1.0.0/schema.json#/$defs/GenerationSuggestionOutput",
        duplicateEcho,
      ),
    ).toBe(false);
    expect(
      ajv.validate(
        "https://qa-tool.local/schemas/generation/suggestion/1.0.0/schema.json#/$defs/GenerationSuggestionInput",
        spoofed,
      ),
    ).toBe(false);
  });

  it("keeps ordinary generation output 1.0.0 separate from suggestion output", () => {
    const ajv = validator();
    expect(
      ajv.validate(
        "https://qa-tool.local/schemas/generation/suggestion/1.0.0/schema.json#/$defs/GenerationSuggestionOutput",
        generationOutputFixture,
      ),
    ).toBe(false);
    expect(generationOutputFixture.outputSchemaVersion).toBe("1.0.0");
    expect(generationOutputFixture).not.toHaveProperty("suggestions");
  });
});
