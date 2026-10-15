import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import ledgerFixture from "../../../evals/fixtures/automatic-generation-version-regression/synthetic-baseline-ledger-v1.json";
import inputFixture from "../../../evals/fixtures/automatic-generation-version-regression/synthetic-comparison-input-v1.json";
import reportFixture from "../../../evals/fixtures/automatic-generation-version-regression/synthetic-comparison-report-v1.json";
import versionRegressionSchema from "../generation/version-regression/1.0.0/schema.json";
import { generationVersionRegressionV1Schema } from "./index";

const dimensions = [
  "GENERATION_POLICY",
  "PROMPT",
  "GENERATION_PROVIDER_MODEL",
  "EMBEDDING_PROVIDER_MODEL_REVISION",
  "EMBEDDING_DIMENSION",
  "SEMANTIC_THRESHOLD_POLICY",
  "EXACT_DUPLICATE_ALGORITHM",
  "GENERATION_INPUT_SCHEMA",
  "GENERATION_OUTPUT_SCHEMA",
  "SUGGESTION_SCHEMA",
  "QUALITY_GATE_SCHEMA",
  "ONTOLOGY_SCHEMA",
  "REGRESSION_EVALUATOR_POLICY",
  "PLACEMENT_POLICY",
] as const;

interface Metrics {
  evidenceCoverage: number;
  retiredKnowledgeMisuseCount: number;
  duplicateCount: number;
  unresolvedAuthorityCount: number;
  provenanceFailureCount: number;
  invalidOrBlankResultCount: number;
  orderingFailureCount: number;
}

interface RegressionInput {
  oldBaseline: {identity: {embeddingDimensions: number}; identityDigest: string};
  newCandidate: {identity: {embeddingDimensions: number}; identityDigest: string};
  changeRequest: {oldBaselineIdentityDigest: string; proposedBaselineIdentityDigest: string};
  comparisonPopulation: {
    cohortId: string;
    cohortDigest: string;
    sourcePopulationDigest: string;
    populationMembershipDigest: string;
    oldResultPopulationDigest: string;
    newResultPopulationDigest: string;
    rootCount: number;
    resultSlots: unknown[];
  };
  oldGoldenReport: {
    cohortId: string;
    cohortDigest: string;
    sourcePopulationDigest: string;
    populationMembershipDigest: string;
    resultPopulationDigest: string;
    metrics: Metrics;
  };
  newGoldenReport: RegressionInput["oldGoldenReport"];
  compatibilityMatrix: {
    entries: Array<{
      dimension: string;
      changeType: string;
      oldReaderNewArtifact: string;
      newReaderOldArtifact: string;
      migrationPlanId: string | null;
      activationAllowed: boolean;
    }>;
    schemaMigrations: Array<{migrationPlanId: string}>;
    embeddingMigration: null | {
      oldDimensions: number;
      newDimensions: number;
      mixedDimensionsObserved: boolean;
    };
  };
}

function validator(definition: "RegressionEvaluationInput" | "RegressionEvaluationReport" | "BaselineLedgerSnapshot" | "BaselineApproval" | "RollbackReceipt") {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  ajv.addSchema(versionRegressionSchema);
  const compiled = ajv.getSchema(`${versionRegressionSchema.$id}#/$defs/${definition}`);
  expect(compiled).toBeDefined();
  return compiled!;
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonical(record[key])}`).join(",")}}`;
}

async function identityDigest(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(canonical(value));
  const digestBytes = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes));
  return `sha256:${[...digestBytes].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

function exactQuality(metrics: Metrics): boolean {
  return metrics.evidenceCoverage === 1
    && metrics.retiredKnowledgeMisuseCount === 0
    && metrics.duplicateCount === 0
    && metrics.unresolvedAuthorityCount === 0
    && metrics.provenanceFailureCount === 0
    && metrics.invalidOrBlankResultCount === 0
    && metrics.orderingFailureCount === 0;
}

function assertInputSemantics(input: RegressionInput): void {
  expect(input.oldBaseline.identityDigest).toBe("sha256:b54e24bfcf93f04f63d09024b9c061636942057b6e976cb6d918726afcec25c1");
  expect(input.newCandidate.identityDigest).toBe("sha256:5cf55f52797f8d425c575fa2842cf034f45e9897d2d423388bf1c2dd8100ed4b");
  expect(input.changeRequest.oldBaselineIdentityDigest).toBe(input.oldBaseline.identityDigest);
  expect(input.changeRequest.proposedBaselineIdentityDigest).toBe(input.newCandidate.identityDigest);

  const population = input.comparisonPopulation;
  for (const report of [input.oldGoldenReport, input.newGoldenReport]) {
    if (report.cohortId !== population.cohortId
      || report.cohortDigest !== population.cohortDigest
      || report.sourcePopulationDigest !== population.sourcePopulationDigest
      || report.populationMembershipDigest !== population.populationMembershipDigest) {
      throw new Error("OLD_NEW_POPULATION_MISMATCH");
    }
    if (!exactQuality(report.metrics)) throw new Error("QUALITY_100_0_0_RELAXED");
  }
  if (input.oldGoldenReport.resultPopulationDigest !== population.oldResultPopulationDigest
    || input.newGoldenReport.resultPopulationDigest !== population.newResultPopulationDigest
    || population.rootCount !== population.resultSlots.length) {
    throw new Error("RESULT_POPULATION_SEAL_MISMATCH");
  }

  const entries = input.compatibilityMatrix.entries;
  expect(new Set(entries.map((entry) => entry.dimension))).toEqual(new Set(dimensions));
  if (entries.some((entry) => entry.changeType === "INCOMPATIBLE"
    || entry.oldReaderNewArtifact === "REJECTED"
    || entry.newReaderOldArtifact === "REJECTED"
    || !entry.activationAllowed)) {
    throw new Error("FORWARD_OR_DOWNGRADE_INCOMPATIBLE");
  }
  for (const entry of entries.filter((item) => item.changeType === "MIGRATION_REQUIRED")) {
    if (!entry.migrationPlanId
      || !input.compatibilityMatrix.schemaMigrations.some((plan) => plan.migrationPlanId === entry.migrationPlanId)) {
      throw new Error("SCHEMA_MIGRATION_RECEIPT_MISSING");
    }
  }
  if (input.oldBaseline.identity.embeddingDimensions !== input.newCandidate.identity.embeddingDimensions) {
    const migration = input.compatibilityMatrix.embeddingMigration;
    if (!migration
      || migration.oldDimensions !== input.oldBaseline.identity.embeddingDimensions
      || migration.newDimensions !== input.newCandidate.identity.embeddingDimensions
      || migration.mixedDimensionsObserved) {
      throw new Error("SEMANTIC_VECTOR_DIMENSION_DRIFT_UNMIGRATED");
    }
  }
}

function assertApprovalSemantics(approval: {requestedBy: string; approvals: Array<{principalId: string; role: string}>}): void {
  const principals = approval.approvals.map((item) => item.principalId);
  const roles = approval.approvals.map((item) => item.role);
  if (new Set(principals).size !== 2 || principals.includes(approval.requestedBy)) {
    throw new Error("TWO_PERSON_APPROVAL_INVALID");
  }
  if (new Set(roles).size !== 2) throw new Error("APPROVAL_ROLE_SEPARATION_INVALID");
}

describe("PE-SE2-T09 automatic generation version regression contract", () => {
  it("exports and strictly compiles the versioned schema", () => {
    expect(generationVersionRegressionV1Schema).toEqual(versionRegressionSchema);
    expect(() => validator("RegressionEvaluationInput")).not.toThrow();
  });

  it("validates deterministic input, report, and non-approvable synthetic ledger fixtures", () => {
    expect(validator("RegressionEvaluationInput")(inputFixture)).toBe(true);
    expect(validator("RegressionEvaluationReport")(reportFixture)).toBe(true);
    expect(validator("BaselineLedgerSnapshot")(ledgerFixture)).toBe(true);
    assertInputSemantics(inputFixture as unknown as RegressionInput);
    expect(reportFixture.approvalEligible).toBe(false);
    expect(reportFixture.candidateDisposition).toBe("CONTRACT_ONLY");
    expect(ledgerFixture.activeBaselineId).toBeNull();
    expect(ledgerFixture.approvals).toEqual([]);
    expect(ledgerFixture.activations).toEqual([]);
    expect(ledgerFixture.rollbacks).toEqual([]);
  });

  it("recomputes both complete baseline identity digests deterministically", async () => {
    await expect(identityDigest(inputFixture.oldBaseline.identity)).resolves.toBe(inputFixture.oldBaseline.identityDigest);
    await expect(identityDigest(inputFixture.newCandidate.identity)).resolves.toBe(inputFixture.newCandidate.identityDigest);
    const {requestDigest, ...requestBody} = inputFixture.changeRequest;
    await expect(identityDigest(requestBody)).resolves.toBe(requestDigest);
    const {matrixDigest, ...matrixBody} = inputFixture.compatibilityMatrix;
    await expect(identityDigest(matrixBody)).resolves.toBe(matrixDigest);
    const {snapshotDigest, ...ledgerBody} = ledgerFixture;
    await expect(identityDigest(ledgerBody)).resolves.toBe(snapshotDigest);
  });

  it("keeps 100/0/0 immutable and rejects unknown or missing identity pins", () => {
    const relaxed = structuredClone(inputFixture);
    relaxed.thresholds.evidenceCoverage = 0.99;
    expect(validator("RegressionEvaluationInput")(relaxed)).toBe(false);

    const unknown = structuredClone(inputFixture);
    unknown.newCandidate.identity.modelRevision = "unknown";
    expect(validator("RegressionEvaluationInput")(unknown)).toBe(false);

    const missing = structuredClone(inputFixture) as unknown as {newCandidate: {identity: Record<string, unknown>}};
    delete missing.newCandidate.identity.placementPolicyVersion;
    expect(validator("RegressionEvaluationInput")(missing)).toBe(false);
  });

  it("requires the same cohort and immutable source/result slot population", () => {
    const drifted = structuredClone(inputFixture);
    drifted.newGoldenReport.cohortId = "cohort_fixture_other";
    expect(validator("RegressionEvaluationInput")(drifted)).toBe(true);
    expect(() => assertInputSemantics(drifted as unknown as RegressionInput)).toThrow("OLD_NEW_POPULATION_MISMATCH");
  });

  it("fails closed on compatibility, schema migration, and semantic-vector dimension drift", () => {
    const incompatible = structuredClone(inputFixture) as unknown as RegressionInput;
    incompatible.compatibilityMatrix.entries[1]!.newReaderOldArtifact = "REJECTED";
    incompatible.compatibilityMatrix.entries[1]!.activationAllowed = false;
    expect(() => assertInputSemantics(incompatible)).toThrow("FORWARD_OR_DOWNGRADE_INCOMPATIBLE");

    const schemaDrift = structuredClone(inputFixture) as unknown as RegressionInput;
    schemaDrift.compatibilityMatrix.entries[7]!.changeType = "MIGRATION_REQUIRED";
    schemaDrift.compatibilityMatrix.entries[7]!.migrationPlanId = "migration_input_v2";
    expect(() => assertInputSemantics(schemaDrift)).toThrow("SCHEMA_MIGRATION_RECEIPT_MISSING");

    const dimensionDrift = structuredClone(inputFixture) as unknown as RegressionInput;
    dimensionDrift.newCandidate.identity.embeddingDimensions = 384;
    dimensionDrift.newCandidate.identityDigest = "sha256:5cf55f52797f8d425c575fa2842cf034f45e9897d2d423388bf1c2dd8100ed4b";
    expect(() => assertInputSemantics(dimensionDrift)).toThrow("SEMANTIC_VECTOR_DIMENSION_DRIFT_UNMIGRATED");
  });

  it("forbids synthetic production approval and every partial rollout shape", () => {
    const productionSpoof = structuredClone(inputFixture);
    productionSpoof.profile = "PRODUCTION_APPROVAL";
    expect(validator("RegressionEvaluationInput")(productionSpoof)).toBe(false);

    const partial = structuredClone(inputFixture);
    partial.concurrencyPolicy.trafficPercent = 10;
    partial.concurrencyPolicy.partialRolloutAllowed = true;
    expect(validator("RegressionEvaluationInput")(partial)).toBe(false);

    const activeSyntheticLedger = structuredClone(ledgerFixture) as unknown as {activeBaselineId: string | null};
    activeSyntheticLedger.activeBaselineId = "baseline_fixture_v2_candidate";
    expect(validator("BaselineLedgerSnapshot")(activeSyntheticLedger)).toBe(false);
  });

  it("requires two distinct people and role separation for an approval", () => {
    const approval = {
      approvalId: "approval_1",
      candidateId: "candidate_1",
      requestedBy: "requester_1",
      policyVersion: "automatic-generation-baseline-approval/1.0.0",
      decision: "APPROVED",
      approvals: [
        {principalId: "approver_1", role: "QUALITY_OWNER", approvedAt: "2026-08-31T01:00:00Z", decisionDigest: "sha256:5151515151515151515151515151515151515151515151515151515151515151"},
        {principalId: "approver_1", role: "RELEASE_OWNER", approvedAt: "2026-08-31T01:01:00Z", decisionDigest: "sha256:5252525252525252525252525252525252525252525252525252525252525252"},
      ],
      approvalArtifactDigest: "sha256:5353535353535353535353535353535353535353535353535353535353535353",
    };
    expect(validator("BaselineApproval")(approval)).toBe(true);
    expect(() => assertApprovalSemantics(approval)).toThrow("TWO_PERSON_APPROVAL_INVALID");
  });

  it("makes rollback a sealed atomic restoration with reproduced 100/0/0", () => {
    const rollback = {
      rollbackReceiptId: "rollback_1",
      idempotencyKey: "rollback-key-1",
      requestDigest: "sha256:6161616161616161616161616161616161616161616161616161616161616161",
      failedBaselineId: "baseline_v2",
      targetBaselineId: "baseline_v1",
      targetIdentityDigest: inputFixture.oldBaseline.identityDigest,
      targetOriginalActivationReceiptId: "activation_v1",
      expectedLedgerVersion: 2,
      committedLedgerVersion: 3,
      rollbackMode: "ATOMIC_ALL_WORKSPACES",
      trafficPercent: 100,
      partialRolloutAllowed: false,
      reproductionStatus: "VERIFIED",
      reproductionReportId: "reproduction_v1",
      reproductionArtifactDigest: "sha256:6262626262626262626262626262626262626262626262626262626262626262",
      restoredQualityMetrics: inputFixture.thresholds,
      rolledBackAt: "2026-08-31T02:00:00Z",
      receiptDigest: "sha256:6363636363636363636363636363636363636363636363636363636363636363",
    };
    expect(validator("RollbackReceipt")(rollback)).toBe(true);
    const relaxed = structuredClone(rollback);
    relaxed.restoredQualityMetrics.duplicateCount = 1;
    expect(validator("RollbackReceipt")(relaxed)).toBe(false);
  });
});
