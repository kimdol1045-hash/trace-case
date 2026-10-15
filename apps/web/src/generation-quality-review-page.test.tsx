import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import {
  GenerationQualityReviewPage,
  QualityReviewBoundaryWarning,
  QualityReviewDecisionDialog,
  QualityReviewDetail,
} from "./generation-quality-review-page";
import { qualityReviewListCountLabel } from "./generation-quality-review-page-state";
import type { QualityReviewCandidate, QualityReviewService } from "./generation-quality-review-service";

const { readFileSync } = await vi.importActual<{
  readFileSync: (path: URL, encoding: "utf8") => string;
}>("node:fs");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

const classificationDistribution = {
  GROUNDED_ACTIVE: 2,
  SUPERSEDED_TRAP: 1,
  RETRACTED_TRAP: 1,
  DELETED_TRAP: 1,
  REJECTED_TRAP: 1,
  UNAVAILABLE_TRAP: 1,
  UNRESOLVED_CONFLICT: 1,
  DECISION_CANDIDATE: 1,
  EXACT_DUPLICATE_TRAP: 2,
  SEMANTIC_DUPLICATE_TRAP: 2,
  CATEGORY_COLLISION_TRAP: 1,
  PARENT_COLLISION_TRAP: 1,
  CASE_ORDERING: 2,
  BLANK_OR_INVALID_RESULT: 1,
  APPLY_PROVENANCE: 2,
  SUGGESTION_PROVENANCE: 1,
} as const;

const candidate: QualityReviewCandidate = {
  candidateId: `review-candidate:${"1".repeat(64)}`,
  captureEnvironment: "staging",
  captureRevision: "quality-export-v4",
  capturedAt: "2026-09-01T01:00:00Z",
  certificationStatus: "NOT_REVIEWED",
  classificationDistribution,
  counts: {
    evidenceReferences: 32,
    generatedCases: 16,
    generatedSteps: 48,
    generationJobs: 32,
    generationResults: 32,
    generationRoots: 32,
  },
  createdAt: "2026-09-01T01:05:00Z",
  decisions: [],
  envelopeContractVersion: "review-candidate-envelope/1.0.0",
  fingerprints: {
    artifactFingerprint: `sha256:${"a".repeat(64)}`,
    payloadFingerprint: `sha256:${"b".repeat(64)}`,
  },
  materialization: "COMPLETE",
  privacy: {
    containsRawSourceText: false,
    foundationPolicyVersion: "review-candidate-sensitive-data-v1",
    findingCategories: [],
    mode: "REJECT",
    normalizationVersion: "review-candidate-nfkc-lf-ignorables-v1",
    policyVersion: "review-candidate-sensitive-data-v1",
    redactionCount: 0,
    status: "CLEAN",
  },
  payloadSchemaVersion: "generation-quality-review-projection-v2",
  progress: { approveCount: 0, rejected: false, requiredApproveCount: 2 },
  releaseCertified: false,
  reviewable: true,
  source: {
    fieldSeals: [
      { fieldDomain: "operational-root-set", seal: `hmac-sha256:${"c".repeat(64)}` },
      { fieldDomain: "private-t08-snapshot", seal: `hmac-sha256:${"d".repeat(64)}` },
    ],
    keyId: "quality-review-source-v1",
    privacyDomain: "generation-quality-review",
    sealAlgorithm: "HMAC-SHA-256",
  },
  status: "UNREVIEWED",
  approval: null,
};

const service: QualityReviewService = {
  list: async () => ({ items: [], nextCursor: null }),
  get: async () => candidate,
  decide: async () => candidate,
};

describe("generation quality review workspace", () => {
  it("renders the lazy-route surface with persistent release warning, server filters and no automatic selection", () => {
    const markup = renderToStaticMarkup(<MemoryRouter><GenerationQualityReviewPage service={service} /></MemoryRouter>);

    expect(markup).toContain("자동 생성 품질 후보 검토");
    expect(markup).toContain("CANDIDATE / UNREVIEWED");
    expect(markup).toContain("release 승인이나 provider live 인증이 아닙니다");
    expect(markup).toContain("검토 진행·상태");
    expect(markup).toContain("안전 후보 키 검색");
    expect(markup).toContain("물질화");
    expect(markup).toContain("목록을 자동 선택하지 않습니다");
    expect(markup).toContain("이전 페이지");
    expect(markup).toContain('data-mobile-pane="list"');
  });

  it("renders only safe candidate proof, exact fingerprints, in-memory token input and terminal controls", () => {
    const markup = renderToStaticMarkup(<QualityReviewDetail candidate={candidate} reviewerToken="memory-only" setReviewerToken={() => undefined} reviewEnabled actionOpenerRef={createRef<HTMLButtonElement>()} onDecision={() => undefined} />);

    expect(markup).toContain("VERIFIED E4 REVIEW ENVELOPE");
    expect(markup).toContain(candidate.fingerprints.artifactFingerprint);
    expect(markup).toContain("분류 분포");
    expect(markup).toContain("중복 증명");
    expect(markup).toContain("근거·물질화 증명");
    expect(markup).toContain("append-only 결정 원장");
    expect(markup).toContain('type="password"');
    expect(markup).toContain("localStorage/sessionStorage에 저장하지 않습니다");
    expect(markup).not.toContain("href=");
    expect(markup).not.toContain("permalink");
    expect(markup).not.toContain("workspaceId");
    expect(markup).not.toContain("providerEndpoint");
  });

  it("requires a coded reject reason and confirms the immutable fingerprint in an accessible dialog", () => {
    const markup = renderToStaticMarkup(<QualityReviewDecisionDialog candidate={candidate} decision="REJECT" reviewerToken="memory-only" service={service} onClose={() => undefined} onAuthExpired={() => undefined} onStale={() => undefined} onSuccess={() => undefined} />);

    expect(markup).toContain('role="dialog"');
    expect(markup).toContain('aria-modal="true"');
    expect(markup).toContain('aria-describedby="quality-review-dialog-description"');
    expect(markup).toContain("artifact fingerprint");
    expect(markup).toContain(candidate.fingerprints.artifactFingerprint);
    expect(markup).toContain("거절 사유");
    expect(markup).toContain("QUALITY_EVIDENCE_INSUFFICIENT");
    expect(markup).toContain("이 작업은 release 승인이 아닙니다");
    expect(markup).toContain("disabled=\"\"");
    expect(markup).toContain('aria-live="assertive"');
  });

  it("keeps reviewed candidates explicitly outside the release certification boundary", () => {
    const markup = renderToStaticMarkup(<QualityReviewBoundaryWarning status="REVIEWED_NOT_RELEASE_CERTIFIED" />);
    expect(markup).toContain("CANDIDATE / REVIEWED_NOT_RELEASE_CERTIFIED");
    expect(markup).toContain("release 승인");
  });

  it("shows an unavailable header after an errored list settles without a page", () => {
    expect(qualityReviewListCountLabel(null, "error")).toBe("목록 이용 불가");
    expect(qualityReviewListCountLabel(null, "error")).not.toBe("불러오는 중");
  });

  it("keeps every quality-review select at 44px with 16px mobile input text", () => {
    const productDefault = styles.indexOf(".app-content select:not([multiple])");
    const qualityOverride = styles.indexOf(
      ".app-content .tm-quality-review-workspace select:not([multiple])",
    );

    expect(productDefault).toBeGreaterThanOrEqual(0);
    expect(qualityOverride).toBeGreaterThan(productDefault);
    expect(styles.slice(qualityOverride)).toMatch(
      /^\.app-content \.tm-quality-review-workspace select:not\(\[multiple\]\) \{[^}]*min-height:\s*44px/s,
    );
    expect(styles).toMatch(
      /@media \(max-width: 768px\)[\s\S]*?\.tm-quality-review-filters input,[\s\S]*?\.tm-quality-review-dialog select \{[^}]*font-size:\s*16px/,
    );
    expect(styles).toMatch(
      /\.tm-quality-review-workspace \{[^}]*min-width:\s*0;[^}]*overflow-x:\s*hidden/,
    );
  });
});
