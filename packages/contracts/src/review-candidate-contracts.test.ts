import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import packageDocument from "../package.json";
import reviewCandidateSchema from "../review-candidate/1.0.0/schema.json";
import safePropertyNameCases from "../review-candidate/1.0.0/safe-property-name-cases.json";
import { reviewCandidateV1Schema } from "./index";

type JsonObject = Record<string, unknown>;

function clone<T>(value: T): T {
  return structuredClone(value);
}

function setAtPath(root: JsonObject, path: readonly string[], value: unknown) {
  let target: unknown = root;
  for (const segment of path.slice(0, -1)) {
    target = Array.isArray(target)
      ? target[Number(segment)]
      : (target as JsonObject)[segment];
  }
  if (Array.isArray(target)) target[Number(path.at(-1))] = value;
  else (target as JsonObject)[path.at(-1)!] = value;
}

function validator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  return ajv.compile(reviewCandidateSchema);
}

function freshCandidate(): JsonObject {
  return {
    artifactKind: "REVIEW_CANDIDATE",
    contractVersion: "review-candidate-envelope/1.0.0",
    purpose: "automatic-generation-quality-review",
    dataClass: "SYNTHETIC",
    candidate: {
      candidateKey: `review-candidate:${"1".repeat(64)}`,
      revision: 1,
      createdAt: "2026-09-01T00:00:00Z",
      predecessorArtifactFingerprint: null,
    },
    source: {
      sealAlgorithm: "HMAC-SHA-256",
      keyId: "quality-key-v1",
      privacyDomain: "quality-review",
      synthetic: true,
      fieldSeals: [
        {
          fieldDomain: "source-revision",
          seal: `hmac-sha256:${"2".repeat(64)}`,
        },
      ],
    },
    sanitization: {
      policyVersion: "review-candidate-sensitive-data-v1",
      normalizationVersion: "review-candidate-nfkc-lf-ignorables-v1",
      mode: "REDACT",
      status: "CLEAN",
      redactionCount: 0,
      findingCategories: [],
      policyFingerprint: `sha256:${"3".repeat(64)}`,
    },
    sampling: {
      strategy: "ALL",
      algorithmVersion: "review-candidate-sampling-v1",
      populationSize: 2,
      sampleSize: 2,
      seedFingerprint: `sha256:${"4".repeat(64)}`,
    },
    payload: {
      schemaVersion: "quality-projection-v1",
      projection: {
        classification: "GROUNDED_ACTIVE",
        counts: { cases: 2, evidence: 4 },
        labels: ["fixture", "grounded"],
      },
    },
    fingerprints: {
      payloadFingerprint: `sha256:${"5".repeat(64)}`,
      artifactFingerprint: `sha256:${"6".repeat(64)}`,
    },
    review: {
      status: "UNREVIEWED",
      reviewedAt: null,
      reviewAuthoritySeal: null,
      approvalArtifactFingerprint: null,
    },
    attestation: {
      status: "NOT_RUN",
      attestedAt: null,
      attestationFingerprint: null,
    },
    retention: {
      policyVersion: "review-candidate-retention-v1",
      disposition: "DELETE",
      maxAgeDays: 90,
      expiresAt: "2026-11-30T00:00:00Z",
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
        "SYNTHETIC_ONLY",
      ],
    },
  };
}

describe("review-candidate-envelope/1.0.0", () => {
  it("exports and compiles the canonical strict schema", () => {
    expect(reviewCandidateV1Schema).toEqual(reviewCandidateSchema);
    expect(packageDocument.exports["./schemas/review-candidate/1.0.0"]).toBe(
      "./review-candidate/1.0.0/schema.json",
    );
    expect(validator()(freshCandidate())).toBe(true);
  });

  it("pins the artifact, version, data class, and keyed source-seal forms", () => {
    const validate = validator();
    for (const [path, value] of [
      [["artifactKind"], "QUALITY_INPUT"],
      [["contractVersion"], "review-candidate-envelope/latest"],
      [["dataClass"], "ACTUAL"],
      [["candidate", "candidateKey"], `sha256:${"1".repeat(64)}`],
      [["source", "sealAlgorithm"], "SHA-256"],
      [["source", "fieldSeals", "0", "seal"], `sha256:${"2".repeat(64)}`],
    ] as const) {
      const candidate = clone(freshCandidate());
      setAtPath(candidate, path, value);
      expect(validate(candidate), path.join(".")).toBe(false);
    }
  });

  it("requires every revision-one export to remain unreviewed, unattested, and ineligible", () => {
    const validate = validator();
    const mutations: Array<(candidate: JsonObject) => void> = [
      (candidate) => (((candidate.review as JsonObject).status = "REVIEWED")),
      (candidate) => (((candidate.attestation as JsonObject).status = "PASSED")),
      (candidate) => (((candidate.certification as JsonObject).eligible = true)),
      (candidate) => (((candidate.certification as JsonObject).status = "CERTIFIED")),
      (candidate) =>
        (((candidate.candidate as JsonObject).predecessorArtifactFingerprint =
          `sha256:${"9".repeat(64)}`)),
    ];
    for (const mutate of mutations) {
      const candidate = clone(freshCandidate());
      mutate(candidate);
      expect(validate(candidate)).toBe(false);
    }
  });

  it("fails closed on unknown properties at every envelope boundary", () => {
    const validate = validator();
    for (const field of [
      "candidate",
      "source",
      "sanitization",
      "sampling",
      "payload",
      "fingerprints",
      "review",
      "attestation",
      "retention",
      "certification",
    ]) {
      const candidate = clone(freshCandidate());
      (candidate[field] as JsonObject).unexpected = "value";
      expect(validate(candidate), field).toBe(false);
    }
    const topLevel = clone(freshCandidate());
    topLevel.unexpected = "value";
    expect(validate(topLevel)).toBe(false);

    const sourceField = clone(freshCandidate());
    const seals = (sourceField.source as JsonObject).fieldSeals as JsonObject[];
    seals[0]!.rawValue = "not-allowed";
    expect(validate(sourceField)).toBe(false);
  });

  it("rejects raw-source, URL, direct-ID, secret, and raw-content-SHA payload fields", () => {
    const validate = validator();
    for (const forbidden of [
      "rawSource",
      "sourceText",
      "sourceRevisionId",
      "sourceUrl",
      "permalink",
      "customerId",
      "orderId",
      "accountNumber",
      "personName",
      "slackChannel",
      "jiraIssue",
      "apiToken",
      "privateKey",
      "contentSha256",
      "drawCount",
    ]) {
      const candidate = clone(freshCandidate());
      const projection = (candidate.payload as JsonObject).projection as JsonObject;
      projection[forbidden] = "opaque-looking-value";
      expect(validate(candidate), forbidden).toBe(false);
    }
  });

  it("applies the exhaustive E9 SafePropertyName matrix and representative safe keys", () => {
    expect(safePropertyNameCases.e9KeyMatrix).toHaveLength(31);
    const validate = validator();
    for (const forbidden of safePropertyNameCases.e9KeyMatrix) {
      const candidate = clone(freshCandidate());
      const projection = (candidate.payload as JsonObject).projection as JsonObject;
      projection[forbidden] = "opaque-looking-value";
      expect(validate(candidate), forbidden).toBe(false);
    }
    for (const safe of safePropertyNameCases.representativeSafeKeys) {
      const candidate = clone(freshCandidate());
      const projection = (candidate.payload as JsonObject).projection as JsonObject;
      projection[safe] = 1;
      expect(validate(candidate), safe).toBe(true);
    }
  });

  it("rejects sensitive values even when hidden below otherwise safe property names", () => {
    const validate = validator();
    for (const forbidden of [
      "https://workspace.example/private",
      "person@example.com",
      "Bearer abcdefghijklmnop",
      "-----BEGIN PRIVATE KEY-----",
      `sha256:${"a".repeat(64)}`,
      "/Users/person/private/file.txt",
      "010-1234-5678",
      "900101-1234567",
      "00:11:22:33:44:55",
      "U12345678",
      "PROJ-1234",
    ]) {
      const candidate = clone(freshCandidate());
      const projection = (candidate.payload as JsonObject).projection as JsonObject;
      projection.note = forbidden;
      expect(validate(candidate), forbidden).toBe(false);
    }
  });

  it("binds data class to source provenance and limits actual retention to 30 days", () => {
    const validate = validator();
    const actual = clone(freshCandidate());
    actual.dataClass = "ACTUAL_MINIMIZED";
    (actual.source as JsonObject).synthetic = false;
    (actual.retention as JsonObject).maxAgeDays = 30;
    const limitations = (actual.certification as JsonObject).limitations as string[];
    limitations.splice(limitations.indexOf("SYNTHETIC_ONLY"), 1, "ACTUAL_DATA_MINIMIZED");
    expect(validate(actual)).toBe(true);

    (actual.retention as JsonObject).maxAgeDays = 31;
    expect(validate(actual)).toBe(false);

    const mismatched = clone(freshCandidate());
    mismatched.dataClass = "ACTUAL_MINIMIZED";
    expect(validate(mismatched)).toBe(false);
  });
});
