import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import packageDocument from "../package.json";
import schema from "../generation/quality-review/2.0.0/schema.json";
import reviewCandidateSchema from "../review-candidate/1.0.0/schema.json";
import { generationQualityReviewV2Schema } from "./index";

type JsonObject = Record<string, unknown>;

const digest = (value: string) => `sha256:${value.repeat(64)}`;
const seal = (value: string) => `hmac-sha256:${value.repeat(64)}`;

function validator(definition: string) {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  ajv.addSchema(reviewCandidateSchema);
  return ajv.compile({ ...schema, oneOf: [{ $ref: `#/$defs/${definition}` }] });
}

function distribution(): JsonObject {
  return Object.fromEntries(
    [
      "GROUNDED_ACTIVE",
      "SUPERSEDED_TRAP",
      "RETRACTED_TRAP",
      "DELETED_TRAP",
      "REJECTED_TRAP",
      "UNAVAILABLE_TRAP",
      "UNRESOLVED_CONFLICT",
      "DECISION_CANDIDATE",
      "EXACT_DUPLICATE_TRAP",
      "SEMANTIC_DUPLICATE_TRAP",
      "CATEGORY_COLLISION_TRAP",
      "PARENT_COLLISION_TRAP",
      "CASE_ORDERING",
      "BLANK_OR_INVALID_RESULT",
      "APPLY_PROVENANCE",
      "SUGGESTION_PROVENANCE",
    ].map((name) => [name, name === "GROUNDED_ACTIVE" ? 1 : 0]),
  );
}

function counts(): JsonObject {
  return {
    generationRoots: 1,
    generationJobs: 1,
    generationResults: 1,
    generatedCases: 5,
    generatedSteps: 10,
    evidenceReferences: 5,
  };
}

function candidate(): JsonObject {
  return {
    candidateId: `review-candidate:${"1".repeat(64)}`,
    status: "UNREVIEWED",
    reviewable: true,
    materialization: "COMPLETE",
    createdAt: "2026-09-01T00:00:00Z",
    capturedAt: "2026-09-01T00:00:00Z",
    captureEnvironment: "staging",
    captureRevision: "phase-e-postgresql-quality-candidate-v1",
    fingerprints: {
      payloadFingerprint: digest("2"),
      artifactFingerprint: digest("3"),
    },
    source: {
      sealAlgorithm: "HMAC-SHA-256",
      keyId: "quality-review-source-v1",
      privacyDomain: "generation-quality-review",
      fieldSeals: [
        { fieldDomain: "operational-root-set", seal: seal("4") },
        { fieldDomain: "private-t08-snapshot", seal: seal("5") },
      ],
    },
    privacy: {
      status: "CLEAN",
      policyVersion: "review-candidate-sensitive-data-v1",
      foundationPolicyVersion: "review-candidate-sensitive-data-v1",
      normalizationVersion: "review-candidate-nfkc-lf-ignorables-v1",
      mode: "REJECT",
      redactionCount: 0,
      findingCategories: [],
      containsRawSourceText: false,
    },
    envelopeContractVersion: "review-candidate-envelope/1.0.0",
    payloadSchemaVersion: "generation-quality-review-projection-v2",
    counts: counts(),
    classificationDistribution: distribution(),
    progress: { approveCount: 0, requiredApproveCount: 2, rejected: false },
    decisions: [],
    approval: null,
    certificationStatus: "NOT_REVIEWED",
    releaseCertified: false,
  };
}

function baseEnvelope(): JsonObject {
  return {
    artifactKind: "REVIEW_CANDIDATE",
    contractVersion: "review-candidate-envelope/1.0.0",
    purpose: "Operational generation quality review",
    dataClass: "ACTUAL_MINIMIZED",
    candidate: {
      candidateKey: `review-candidate:${"1".repeat(64)}`,
      revision: 1,
      createdAt: "2026-09-01T00:00:00Z",
      predecessorArtifactFingerprint: null,
    },
    source: {
      sealAlgorithm: "HMAC-SHA-256",
      keyId: "quality-review-source-v1",
      privacyDomain: "generation-quality-review",
      synthetic: false,
      fieldSeals: [
        { fieldDomain: "operational-root-set", seal: seal("4") },
        { fieldDomain: "private-t08-snapshot", seal: seal("5") },
      ],
    },
    sanitization: {
      policyVersion: "review-candidate-sensitive-data-v1",
      normalizationVersion: "review-candidate-nfkc-lf-ignorables-v1",
      mode: "REJECT",
      status: "CLEAN",
      redactionCount: 0,
      findingCategories: [],
      policyFingerprint: digest("6"),
    },
    sampling: {
      strategy: "ALL",
      algorithmVersion: "review-candidate-sampling-v1",
      populationSize: 1,
      sampleSize: 1,
      seedFingerprint: digest("7"),
    },
    payload: {
      schemaVersion: "generation-quality-review-projection-v2",
      projection: {
        captureEnvironment: "staging",
        materialization: "complete",
        releaseCertified: false,
      },
    },
    fingerprints: { payloadFingerprint: digest("2"), artifactFingerprint: digest("3") },
    review: {
      status: "UNREVIEWED",
      reviewedAt: null,
      reviewAuthoritySeal: null,
      approvalArtifactFingerprint: null,
    },
    attestation: { status: "NOT_RUN", attestedAt: null, attestationFingerprint: null },
    retention: {
      policyVersion: "review-candidate-retention-v1",
      disposition: "DELETE",
      maxAgeDays: 30,
      expiresAt: "2026-10-01T00:00:00Z",
    },
    certification: {
      status: "NOT_CERTIFIED",
      eligible: false,
      scope: null,
      certificationFingerprint: null,
      limitations: [
        "ATTESTATION_NOT_RUN",
        "NOT_A_RELEASE_CERTIFICATION",
        "REVIEW_REQUIRED",
        "ACTUAL_DATA_MINIMIZED",
      ],
    },
  };
}

describe("automatic-generation-quality-review/2.0.0", () => {
  it("exports and compiles the canonical safe schema", () => {
    expect(generationQualityReviewV2Schema).toEqual(schema);
    expect(packageDocument.exports["./schemas/generation/quality-review/2.0.0"]).toBe(
      "./generation/quality-review/2.0.0/schema.json",
    );
    expect(
      Object.hasOwn(packageDocument.exports, "./schemas/generation/quality-review/1.0.0"),
    ).toBe(false);
    expect(validator("Candidate")(candidate())).toBe(true);
  });

  it("rejects the legacy snapshot import and source-derived public fingerprints", () => {
    const request = {
      idempotencyKey: "quality-review-import-1",
      rootJobIds: ["00000000-0000-7000-8000-000000000201"],
      captureEnvironment: "staging",
      reportOutputs: {
        json: "artifacts/candidate.json",
        junit: "artifacts/candidate.xml",
        markdown: "artifacts/candidate.md",
      },
    };
    const validateImport = validator("ImportRequest");
    expect(validateImport(request)).toBe(true);
    for (const legacy of [
      "snapshot",
      "expectedBaseArtifactDigest",
      "expectedDatasetFingerprint",
      "expectedSourceSnapshotFingerprint",
      "expectedSnapshotFingerprint",
    ]) {
      expect(validateImport({ ...request, [legacy]: {} }), legacy).toBe(false);
    }

    const validateCandidate = validator("Candidate");
    for (const legacy of [
      "baseArtifactDigest",
      "datasetFingerprint",
      "sourceSnapshotFingerprint",
      "snapshotFingerprint",
      "reviewerAlias",
      "decisionId",
    ]) {
      expect(validateCandidate({ ...candidate(), [legacy]: digest("9") }), legacy).toBe(false);
    }
    const duplicateSourceDomain = structuredClone(candidate());
    const source = duplicateSourceDomain.source as JsonObject;
    const fieldSeals = source.fieldSeals as JsonObject[];
    fieldSeals[1]!.fieldDomain = "operational-root-set";
    expect(validateCandidate(duplicateSourceDomain)).toBe(false);
  });

  it("requires the E4 artifact fingerprint and rejects authority fields in the body", () => {
    const approve = {
      idempotencyKey: "quality-review-decision-1",
      decision: "APPROVE",
      expectedArtifactFingerprint: digest("3"),
      reasonCode: null,
    };
    const validate = validator("DecisionRequest");
    expect(validate(approve)).toBe(true);
    for (const forged of ["principalId", "actorLabel", "reviewerToken", "expectedSnapshotFingerprint"]) {
      expect(validate({ ...approve, [forged]: "spoofed" }), forged).toBe(false);
    }
  });

  it("pins reviewed export to a revision-one E4 envelope and explicit non-certification", () => {
    const reviewed = {
      artifactKind: "AUTOMATIC_GENERATION_QUALITY_REVIEWED_EXPORT",
      contractVersion: "automatic-generation-quality-reviewed-export/2.0.0",
      candidateKey: `review-candidate:${"1".repeat(64)}`,
      baseEnvelope: baseEnvelope(),
      review: {
        status: "REVIEWED",
        reviewedAt: "2026-09-01T01:00:00Z",
        requiredApprovals: 2,
        reviewerAuthoritySeals: [seal("8"), seal("9")],
        decisionFingerprints: [digest("a"), digest("b")],
        reviewAuthorityFingerprint: digest("c"),
        approvalArtifactFingerprint: digest("d"),
        approvedAt: "2026-09-01T01:00:00Z",
      },
      attestation: { status: "NOT_RUN" },
      certification: {
        status: "REVIEWED_NOT_RELEASE_CERTIFIED",
        eligible: false,
        releaseCertified: false,
        limitations: [
          "ACTUAL_32_ROOT_16_CLASS_SAMPLE_NOT_ATTESTED",
          "PROVIDER_LIVE_ATTESTATION_NOT_RUN",
          "NOT_A_RELEASE_CERTIFICATION",
        ],
      },
    };
    const validate = validator("ReviewedExport");
    expect(validate(reviewed)).toBe(true);
    expect(validate({ ...reviewed, snapshot: { profile: "CANDIDATE" } })).toBe(false);
    expect(
      validate({
        ...reviewed,
        baseEnvelope: {
          ...baseEnvelope(),
          sourceSnapshotFingerprint: digest("e"),
        },
      }),
    ).toBe(false);
    expect(
      validate({
        ...reviewed,
        review: { ...reviewed.review, principalId: "quality-principal-alpha" },
      }),
    ).toBe(false);
    expect(
      validate({
        ...reviewed,
        certification: { ...reviewed.certification, releaseCertified: true },
      }),
    ).toBe(false);
  });
});
