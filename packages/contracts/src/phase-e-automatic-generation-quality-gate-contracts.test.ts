import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import syntheticInputFixture from "../../../evals/fixtures/automatic-generation-quality/synthetic-input-v1.json";
import syntheticReportFixture from "../../../evals/fixtures/automatic-generation-quality/synthetic-report-v1.json";
import generationQualityGateSchema from "../generation/quality-gate/1.0.0/schema.json";
import packageDocument from "../package.json";
import { generationQualityGateV1Schema } from "./index";

type LifecycleStatus =
  | "ACTIVE"
  | "SUPERSEDED"
  | "RETRACTED"
  | "DELETED"
  | "REJECTED"
  | "UNAVAILABLE";

interface SourceRevision {
  sourceType: string;
  sourceEntityId: string;
  sourceRevisionId: string;
  sourceContentHash: string;
}

interface ContextEntry {
  contextEntryId: string;
  knowledgeNodeId: string;
  source: SourceRevision;
  approvedEvidenceExcerptHashes: string[];
  lifecycleStatusAtSeal: LifecycleStatus;
  validFrom: string;
  validTo: string | null;
  availableAtSeal: boolean;
  conflictState: "NONE" | "UNRESOLVED";
  decisionState: "ACCEPTED" | "CANDIDATE" | "NOT_APPLICABLE";
}

interface EvidenceReference {
  evidenceReferenceId: string;
  coverageTarget: { unit: "CASE" | "STEP"; stepSequence: number | null };
  contextEntryId: string;
  knowledgeNodeId: string;
  citationSourceRevisionId: string;
  source: SourceRevision;
  citationExcerptHash: string;
}

interface GeneratedCase {
  caseIndex: number;
  caseSnapshotId: string;
  summary: { normalizedLength: number };
  caseExpectedResult: { normalizedLength: number };
  generationReason: { normalizedLength: number };
  exactContentKey: string;
  semanticDuplicateClusterId: string;
  steps: Array<{
    sequence: number;
    precondition: { normalizedLength: number };
    action: { normalizedLength: number };
    expectedResult: { normalizedLength: number };
  }>;
  evidenceReferences: EvidenceReference[];
  placement: {
    categoryDecision: "REUSE_EXISTING" | "CREATE_NEW";
    categoryCanonicalKey: string;
    parentDecision: "REUSE_EXISTING" | "CREATE_NEW";
    parentCanonicalKey: string;
  };
  materialization: {
    kind: "APPLY_CREATED" | "APPLY_REUSED" | "SUGGESTION" | "NO_CHANGE" | "NONE";
    applyId: string | null;
    applyOutcomeId: string | null;
    suggestionId: string | null;
    suggestionTargetId: string | null;
    sourceChangeAssessmentId: string | null;
    generationLineageId: string | null;
    baselineCaseVersionId: string | null;
    releaseReceiptId: string | null;
    sourceGenerationJobId: string;
    sourceRootJobId: string;
    contextDigest: string;
    resultDigest: string;
  };
}

interface GenerationRoot {
  generationRootId: string;
  sealedSourceTime: string;
  primaryClassification: string;
  contextSeal: { contextDigest: string; entries: ContextEntry[] };
  selectedJob: {
    generationJobId: string;
    rootJobId: string;
    status: string;
    inputSchemaVersion: string;
    outputSchemaVersion: string;
    contextDigest: string;
    resultDigest: string;
    result: {
      outputSchemaVersion: string;
      validationStatus: string;
      resultDigest: string;
      cases: GeneratedCase[];
    };
  };
}

interface QualityInput {
  profile: "CANDIDATE" | "RELEASE";
  dataset: {
    kind: "SYNTHETIC_FIXTURE" | "OPERATIONAL_GOLDEN";
    synthetic: boolean;
    review: {
      status: string;
      reviewedBy: string | null;
      reviewedAt: string | null;
      labelSetId: string | null;
      reviewProtocolVersion: string | null;
      approvalId: string | null;
    };
    declaredCounts: {
      generationRoots: number;
      generationJobs: number;
      generationResults: number;
      generatedCases: number;
      generatedSteps: number;
      evidenceReferences: number;
    };
    classificationDistribution: Record<string, number>;
    snapshotFingerprint: string;
    privacy: {
      secretsScan: string;
      piiScan: string;
    };
  };
  capture: {
    capturedAt: string;
    releaseCandidateAt: string;
    maxSnapshotAgeSeconds: number;
    environment: string;
    degraded: boolean;
  };
  liveGate: {
    status: string;
    liveRunId: string | null;
    environment: string | null;
    providerCredentialed: boolean;
    providerCallObserved: boolean;
    approvedBy: string | null;
    approvedAt: string | null;
    capturedSnapshotFingerprint: string | null;
  };
  referenceInventory: {
    existingCases: Array<{
      activeAtSeal: boolean;
      exactContentKey: string;
      semanticDuplicateClusterId: string;
    }>;
    categories: Array<{ activeAtSeal: boolean; canonicalKey: string }>;
    parents: Array<{ activeAtSeal: boolean; canonicalKey: string }>;
  };
  generationRoots: GenerationRoot[];
}

interface QualityMetrics {
  evidenceCoverage: { numerator: number; denominator: number; rate: number };
  retiredKnowledgeMisuse: Record<LifecycleStatus | "total", number>;
  duplicates: {
    total: number;
    exactKey: number;
    semanticCluster: number;
    categoryCollision: number;
    parentCollision: number;
  };
  unresolvedAuthorityCount: number;
  provenanceFailureCount: number;
  invalidOrBlankResultCount: number;
  orderingFailureCount: number;
}

const retiredStatuses: LifecycleStatus[] = [
  "SUPERSEDED",
  "RETRACTED",
  "DELETED",
  "REJECTED",
  "UNAVAILABLE",
];

function createValidator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  ajv.addSchema(generationQualityGateSchema);
  return ajv;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function sameSource(left: SourceRevision, right: SourceRevision) {
  return (
    left.sourceType === right.sourceType &&
    left.sourceEntityId === right.sourceEntityId &&
    left.sourceRevisionId === right.sourceRevisionId &&
    left.sourceContentHash === right.sourceContentHash
  );
}

function validAtSeal(entry: ContextEntry, sealedSourceTime: string) {
  return (
    entry.lifecycleStatusAtSeal === "ACTIVE" &&
    entry.availableAtSeal &&
    entry.conflictState === "NONE" &&
    entry.decisionState !== "CANDIDATE" &&
    entry.validFrom <= sealedSourceTime &&
    (entry.validTo === null || sealedSourceTime < entry.validTo)
  );
}

function countRepeated(values: string[]) {
  const counts = new Map<string, number>();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  return [...counts.values()].reduce((total, count) => total + Math.max(0, count - 1), 0);
}

function evaluateFixture(input: QualityInput): QualityMetrics {
  let denominator = 0;
  let numerator = 0;
  const retiredKnowledgeMisuse: Record<LifecycleStatus | "total", number> = {
    total: 0,
    ACTIVE: 0,
    SUPERSEDED: 0,
    RETRACTED: 0,
    DELETED: 0,
    REJECTED: 0,
    UNAVAILABLE: 0,
  };
  let unresolvedAuthorityCount = 0;
  let provenanceFailureCount = 0;
  let invalidOrBlankResultCount = 0;
  let orderingFailureCount = 0;
  const exactKeys: string[] = [];
  const semanticClusters: string[] = [];
  let categoryCollision = 0;
  let parentCollision = 0;
  const activeExistingExact = new Set(
    input.referenceInventory.existingCases
      .filter((item) => item.activeAtSeal)
      .map((item) => item.exactContentKey),
  );
  const activeExistingSemantic = new Set(
    input.referenceInventory.existingCases
      .filter((item) => item.activeAtSeal)
      .map((item) => item.semanticDuplicateClusterId),
  );
  const activeCategories = new Set(
    input.referenceInventory.categories
      .filter((item) => item.activeAtSeal)
      .map((item) => item.canonicalKey),
  );
  const activeParents = new Set(
    input.referenceInventory.parents
      .filter((item) => item.activeAtSeal)
      .map((item) => item.canonicalKey),
  );

  for (const root of input.generationRoots) {
    const job = root.selectedJob;
    const result = job.result;
    const entries = new Map(root.contextSeal.entries.map((entry) => [entry.contextEntryId, entry]));
    if (
      job.status !== "SUCCEEDED" ||
      result.validationStatus !== "VALID" ||
      result.cases.length < 5 ||
      result.cases.length > 10 ||
      job.outputSchemaVersion !== result.outputSchemaVersion
    ) {
      invalidOrBlankResultCount += 1;
    }
    if (
      job.rootJobId !== root.generationRootId ||
      job.contextDigest !== root.contextSeal.contextDigest ||
      job.resultDigest !== result.resultDigest
    ) {
      provenanceFailureCount += 1;
    }
    if (!result.cases.every((item, index) => item.caseIndex === index)) orderingFailureCount += 1;

    for (const generatedCase of result.cases) {
      denominator += 1 + generatedCase.steps.length;
      exactKeys.push(generatedCase.exactContentKey);
      semanticClusters.push(generatedCase.semanticDuplicateClusterId);
      if (
        generatedCase.summary.normalizedLength === 0 ||
        generatedCase.caseExpectedResult.normalizedLength === 0 ||
        generatedCase.generationReason.normalizedLength === 0 ||
        generatedCase.steps.length === 0 ||
        generatedCase.steps.some(
          (step) =>
            step.action.normalizedLength === 0 || step.expectedResult.normalizedLength === 0,
        )
      ) {
        invalidOrBlankResultCount += 1;
      }
      if (!generatedCase.steps.every((step, index) => step.sequence === index + 1)) {
        orderingFailureCount += 1;
      }
      const validCoverage = new Set<string>();
      for (const reference of generatedCase.evidenceReferences) {
        const entry = entries.get(reference.contextEntryId);
        const provenanceMatches =
          entry !== undefined &&
          entry.knowledgeNodeId === reference.knowledgeNodeId &&
          entry.source.sourceRevisionId === reference.citationSourceRevisionId &&
          entry.approvedEvidenceExcerptHashes.includes(reference.citationExcerptHash) &&
          sameSource(entry.source, reference.source);
        if (!provenanceMatches) {
          provenanceFailureCount += 1;
          continue;
        }
        if (retiredStatuses.includes(entry.lifecycleStatusAtSeal)) {
          retiredKnowledgeMisuse[entry.lifecycleStatusAtSeal] += 1;
          retiredKnowledgeMisuse.total += 1;
        }
        if (entry.conflictState === "UNRESOLVED" || entry.decisionState === "CANDIDATE") {
          unresolvedAuthorityCount += 1;
        }
        if (validAtSeal(entry, root.sealedSourceTime)) {
          validCoverage.add(
            reference.coverageTarget.unit === "CASE"
              ? "CASE"
              : `STEP:${reference.coverageTarget.stepSequence}`,
          );
        }
      }
      if (validCoverage.has("CASE")) numerator += 1;
      numerator += generatedCase.steps.filter((step) => validCoverage.has(`STEP:${step.sequence}`)).length;

      const materialization = generatedCase.materialization;
      if (
        materialization.sourceGenerationJobId !== job.generationJobId ||
        materialization.sourceRootJobId !== root.generationRootId ||
        materialization.contextDigest !== job.contextDigest ||
        materialization.resultDigest !== job.resultDigest ||
        (materialization.kind === "APPLY_CREATED" &&
          (materialization.applyId === null || materialization.applyOutcomeId === null)) ||
        (materialization.kind === "SUGGESTION" &&
          (materialization.suggestionId === null ||
            materialization.suggestionTargetId === null ||
            materialization.sourceChangeAssessmentId === null ||
            materialization.generationLineageId === null ||
            materialization.baselineCaseVersionId === null ||
            materialization.releaseReceiptId === null))
      ) {
        provenanceFailureCount += 1;
      }
      if (
        materialization.kind === "APPLY_CREATED" &&
        (activeExistingExact.has(generatedCase.exactContentKey) ||
          activeExistingSemantic.has(generatedCase.semanticDuplicateClusterId))
      ) {
        exactKeys.push(generatedCase.exactContentKey);
        semanticClusters.push(generatedCase.semanticDuplicateClusterId);
      }
      if (
        generatedCase.placement.categoryDecision === "CREATE_NEW" &&
        activeCategories.has(generatedCase.placement.categoryCanonicalKey)
      ) {
        categoryCollision += 1;
      }
      if (
        generatedCase.placement.parentDecision === "CREATE_NEW" &&
        activeParents.has(generatedCase.placement.parentCanonicalKey)
      ) {
        parentCollision += 1;
      }
    }
  }

  const exactKey = countRepeated(exactKeys);
  const semanticCluster = countRepeated(semanticClusters);
  return {
    evidenceCoverage: {
      numerator,
      denominator,
      rate: denominator === 0 ? 0 : numerator / denominator,
    },
    retiredKnowledgeMisuse,
    duplicates: {
      total: exactKey + semanticCluster + categoryCollision + parentCollision,
      exactKey,
      semanticCluster,
      categoryCollision,
      parentCollision,
    },
    unresolvedAuthorityCount,
    provenanceFailureCount,
    invalidOrBlankResultCount,
    orderingFailureCount,
  };
}

function releaseMetadataEligible(input: QualityInput) {
  const review = input.dataset.review;
  const live = input.liveGate;
  const actualCounts = {
    generationRoots: input.generationRoots.length,
    generationJobs: input.generationRoots.length,
    generationResults: input.generationRoots.length,
    generatedCases: input.generationRoots.reduce(
      (total, root) => total + root.selectedJob.result.cases.length,
      0,
    ),
    generatedSteps: input.generationRoots.reduce(
      (total, root) =>
        total +
        root.selectedJob.result.cases.reduce(
          (caseTotal, generatedCase) => caseTotal + generatedCase.steps.length,
          0,
        ),
      0,
    ),
    evidenceReferences: input.generationRoots.reduce(
      (total, root) =>
        total +
        root.selectedJob.result.cases.reduce(
          (caseTotal, generatedCase) =>
            caseTotal + generatedCase.evidenceReferences.length,
          0,
        ),
      0,
    ),
  };
  const actualDistribution = input.generationRoots.reduce<Record<string, number>>((counts, root) => {
    counts[root.primaryClassification] = (counts[root.primaryClassification] ?? 0) + 1;
    return counts;
  }, {});
  const captureAgeSeconds =
    (Date.parse(input.capture.releaseCandidateAt) - Date.parse(input.capture.capturedAt)) / 1000;
  return (
    input.profile === "RELEASE" &&
    input.dataset.kind === "OPERATIONAL_GOLDEN" &&
    input.dataset.synthetic === false &&
    input.dataset.declaredCounts.generationRoots >= 32 &&
    input.dataset.declaredCounts.generationJobs >= 32 &&
    input.dataset.declaredCounts.generationResults >= 32 &&
    input.dataset.declaredCounts.generatedCases >= 160 &&
    input.dataset.declaredCounts.generatedSteps >= 160 &&
    input.dataset.declaredCounts.evidenceReferences >= 320 &&
    Object.entries(input.dataset.declaredCounts).every(
      ([key, count]) => count === actualCounts[key as keyof typeof actualCounts],
    ) &&
    Object.values(input.dataset.classificationDistribution).every((count) => count >= 2) &&
    Object.entries(input.dataset.classificationDistribution).every(
      ([classification, count]) => count === (actualDistribution[classification] ?? 0),
    ) &&
    review.status === "REVIEWED" &&
    Boolean(
      review.reviewedBy &&
        review.reviewedAt &&
        review.labelSetId &&
        review.reviewProtocolVersion &&
        review.approvalId,
    ) &&
    input.dataset.privacy.secretsScan === "CLEAN" &&
    input.dataset.privacy.piiScan === "CLEAN" &&
    input.capture.environment !== "synthetic" &&
    input.capture.degraded === false &&
    captureAgeSeconds >= 0 &&
    captureAgeSeconds <= input.capture.maxSnapshotAgeSeconds &&
    live.status === "PASSED" &&
    live.providerCredentialed &&
    live.providerCallObserved &&
    Boolean(live.liveRunId && live.approvedBy && live.approvedAt) &&
    live.capturedSnapshotFingerprint === input.dataset.snapshotFingerprint
  );
}

describe("PE-SE2-T08 automatic generation quality gate contract", () => {
  it("compiles every strict definition and publishes the versioned package surface", () => {
    const ajv = createValidator();

    expect(generationQualityGateSchema.$schema).toBe(
      "https://json-schema.org/draft/2020-12/schema",
    );
    expect(ajv.getSchema(generationQualityGateSchema.$id)).toBeTypeOf("function");
    for (const name of Object.keys(generationQualityGateSchema.$defs)) {
      expect(
        ajv.getSchema(`${generationQualityGateSchema.$id}#/$defs/${name}`),
        `definition did not compile: ${name}`,
      ).toBeTypeOf("function");
    }
    expect(generationQualityGateV1Schema).toBe(generationQualityGateSchema);
    expect(packageDocument.exports).toHaveProperty(
      "./schemas/generation/quality-gate/1.0.0",
      "./generation/quality-gate/1.0.0/schema.json",
    );
  });

  it("validates the deterministic fixture and fixes evidence denominator plus 100/0/0 metrics", () => {
    const ajv = createValidator();
    const validateInput = ajv.getSchema(
      `${generationQualityGateSchema.$id}#/$defs/EvaluationInput`,
    );
    const validateReport = ajv.getSchema(
      `${generationQualityGateSchema.$id}#/$defs/EvaluationReport`,
    );

    expect(validateInput?.(syntheticInputFixture), ajv.errorsText(validateInput?.errors)).toBe(true);
    expect(validateReport?.(syntheticReportFixture), ajv.errorsText(validateReport?.errors)).toBe(true);
    expect(syntheticReportFixture.versionPins).toEqual(syntheticInputFixture.versionPins);
    expect(syntheticReportFixture.checks.every((check) => check.passed)).toBe(true);
    const metrics = evaluateFixture(syntheticInputFixture as unknown as QualityInput);
    expect(metrics.evidenceCoverage).toEqual({ numerator: 10, denominator: 10, rate: 1 });
    expect(metrics.retiredKnowledgeMisuse.total).toBe(0);
    expect(metrics.duplicates.total).toBe(0);
    expect(metrics.unresolvedAuthorityCount).toBe(0);
    expect(metrics.provenanceFailureCount).toBe(0);
    expect(metrics.invalidOrBlankResultCount).toBe(0);
    expect(metrics.orderingFailureCount).toBe(0);
  });

  it("fails coverage and provenance for non-member, wrong-revision, retired, or unresolved evidence", () => {
    const fixture = clone(syntheticInputFixture) as unknown as QualityInput;
    const root = fixture.generationRoots[0]!;
    const generatedCase = root.selectedJob.result.cases[0]!;
    const nextGeneratedCase = root.selectedJob.result.cases[1]!;
    const entry = root.contextSeal.entries[0]!;
    entry.lifecycleStatusAtSeal = "SUPERSEDED";
    entry.conflictState = "UNRESOLVED";
    generatedCase.evidenceReferences[0]!.citationSourceRevisionId = "slack_revision_wrong";
    generatedCase.evidenceReferences[1]!.contextEntryId = "context_entry_missing";
    nextGeneratedCase.evidenceReferences[0]!.citationExcerptHash =
      "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff";

    const metrics = evaluateFixture(fixture);

    expect(metrics.evidenceCoverage.numerator).toBe(0);
    expect(metrics.evidenceCoverage.denominator).toBe(10);
    expect(metrics.retiredKnowledgeMisuse.total).toBe(7);
    expect(metrics.unresolvedAuthorityCount).toBe(7);
    expect(metrics.provenanceFailureCount).toBe(3);

    generatedCase.evidenceReferences[0]!.citationSourceRevisionId = entry.source.sourceRevisionId;
    generatedCase.evidenceReferences[1]!.contextEntryId = entry.contextEntryId;
    nextGeneratedCase.evidenceReferences[0]!.citationExcerptHash =
      entry.approvedEvidenceExcerptHashes[2]!;
    const lifecycleMetrics = evaluateFixture(fixture);
    expect(lifecycleMetrics.retiredKnowledgeMisuse.SUPERSEDED).toBe(10);
    expect(lifecycleMetrics.unresolvedAuthorityCount).toBe(10);
    expect(lifecycleMetrics.evidenceCoverage.numerator).toBe(0);

    for (const status of retiredStatuses) {
      const retiredFixture = clone(syntheticInputFixture) as unknown as QualityInput;
      retiredFixture.generationRoots[0]!.contextSeal.entries[0]!.lifecycleStatusAtSeal = status;
      const retiredMetrics = evaluateFixture(retiredFixture);
      expect(retiredMetrics.retiredKnowledgeMisuse[status], status).toBe(10);
      expect(retiredMetrics.retiredKnowledgeMisuse.total, status).toBe(10);
      expect(retiredMetrics.evidenceCoverage.numerator, status).toBe(0);
    }
  });

  it("detects result/case/step ordering, blank values, exact/semantic duplicates, and placement collisions", () => {
    const fixture = clone(syntheticInputFixture) as unknown as QualityInput;
    const cases = fixture.generationRoots[0]!.selectedJob.result.cases;
    cases[1]!.caseIndex = 9;
    cases[1]!.steps[0]!.sequence = 2;
    cases[1]!.summary.normalizedLength = 0;
    cases[1]!.exactContentKey = cases[0]!.exactContentKey;
    cases[1]!.semanticDuplicateClusterId = cases[0]!.semanticDuplicateClusterId;
    cases[1]!.placement.categoryDecision = "CREATE_NEW";
    cases[1]!.placement.parentDecision = "CREATE_NEW";

    const metrics = evaluateFixture(fixture);

    expect(metrics.orderingFailureCount).toBe(2);
    expect(metrics.invalidOrBlankResultCount).toBe(1);
    expect(metrics.duplicates).toEqual({
      total: 4,
      exactKey: 1,
      semanticCluster: 1,
      categoryCollision: 1,
      parentCollision: 1,
    });
  });

  it("keeps synthetic candidate success distinct from operational release certification", () => {
    const fixture = syntheticInputFixture as unknown as QualityInput;
    expect(releaseMetadataEligible(fixture)).toBe(false);
    expect(syntheticReportFixture.gate.passed).toBe(true);
    expect(syntheticReportFixture.certification.releaseCertified).toBe(false);
    expect(syntheticReportFixture.certification.status).toBe("NOT_CERTIFIED_SYNTHETIC");

    const ajv = createValidator();
    const validateReport = ajv.getSchema(
      `${generationQualityGateSchema.$id}#/$defs/EvaluationReport`,
    );
    const spoofed = clone(syntheticReportFixture);
    spoofed.profile = "RELEASE";
    spoofed.certification.releaseCertified = true;
    spoofed.certification.status = "RELEASE_CERTIFIED";
    expect(validateReport?.(spoofed)).toBe(false);
  });

  it("requires reviewed 32-root operational labels, clean scans, freshness, and a matching live attestation", () => {
    const fixture = clone(syntheticInputFixture) as unknown as QualityInput;
    fixture.profile = "RELEASE";
    fixture.dataset.kind = "OPERATIONAL_GOLDEN";
    fixture.dataset.synthetic = false;
    fixture.dataset.review = {
      status: "REVIEWED",
      reviewedBy: "generation-quality-reviewers",
      reviewedAt: "2026-08-28T00:10:00Z",
      labelSetId: "generation_quality_labels_v1",
      reviewProtocolVersion: "two-person-review-v1",
      approvalId: "operations_approval_001",
    };
    fixture.dataset.declaredCounts = {
      generationRoots: 32,
      generationJobs: 32,
      generationResults: 32,
      generatedCases: 160,
      generatedSteps: 160,
      evidenceReferences: 320,
    };
    for (const classification of Object.keys(fixture.dataset.classificationDistribution)) {
      fixture.dataset.classificationDistribution[classification] = 2;
    }
    const rootTemplate = fixture.generationRoots[0]!;
    fixture.generationRoots = Object.keys(fixture.dataset.classificationDistribution).flatMap(
      (classification) =>
        Array.from({ length: 2 }, () => {
          const root = clone(rootTemplate);
          root.primaryClassification = classification;
          return root;
        }),
    );
    fixture.capture.environment = "staging";
    fixture.capture.releaseCandidateAt = "2026-08-28T12:00:00Z";
    fixture.liveGate = {
      status: "PASSED",
      liveRunId: "generation_quality_live_001",
      environment: "staging",
      providerCredentialed: true,
      providerCallObserved: true,
      approvedBy: "release-approvers",
      approvedAt: "2026-08-28T12:05:00Z",
      capturedSnapshotFingerprint: fixture.dataset.snapshotFingerprint,
    };

    expect(releaseMetadataEligible(fixture)).toBe(true);
    fixture.dataset.declaredCounts.evidenceReferences = 319;
    expect(releaseMetadataEligible(fixture)).toBe(false);
    fixture.dataset.declaredCounts.evidenceReferences = 320;
    fixture.dataset.classificationDistribution.GROUNDED_ACTIVE = 1;
    expect(releaseMetadataEligible(fixture)).toBe(false);
    fixture.dataset.classificationDistribution.GROUNDED_ACTIVE = 2;
    fixture.capture.degraded = true;
    expect(releaseMetadataEligible(fixture)).toBe(false);
    fixture.capture.degraded = false;
    fixture.liveGate.capturedSnapshotFingerprint =
      "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff";
    expect(releaseMetadataEligible(fixture)).toBe(false);
  });

  it("rejects secret-bearing or unknown fields while reports contain only opaque IDs, counts, and digests", () => {
    const ajv = createValidator();
    const validateInput = ajv.getSchema(
      `${generationQualityGateSchema.$id}#/$defs/EvaluationInput`,
    );
    const secretBearing = clone(syntheticInputFixture) as unknown as Record<string, unknown>;
    secretBearing.apiToken = "must-never-enter-the-evaluation-artifact";
    expect(validateInput?.(secretBearing)).toBe(false);

    const reportText = JSON.stringify(syntheticReportFixture);
    for (const forbidden of ["sourceText", "promptText", "apiToken", "authorization", "rawProviderBody"]) {
      expect(reportText).not.toContain(forbidden);
    }
  });
});
