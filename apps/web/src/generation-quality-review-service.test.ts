import { ApiClientError } from "@tc/api-client";
import { describe, expect, it, vi } from "vitest";

import {
  createQualityReviewService,
  qualityReviewDecisionKey,
  qualityReviewErrorCode,
  qualityReviewErrorKind,
  type QualityReviewCandidate,
} from "./generation-quality-review-service";

const candidate = { candidateId: `review-candidate:${"1".repeat(64)}` } as QualityReviewCandidate;

describe("generation quality review service", () => {
  it("delegates server filters, E4 candidate-key detail, cursor and token mutation to the generated client", async () => {
    const listGenerationQualityReviewCandidates = vi.fn().mockResolvedValue({ items: [candidate], nextCursor: null });
    const getGenerationQualityReviewCandidate = vi.fn().mockResolvedValue(candidate);
    const createGenerationQualityReviewDecision = vi.fn().mockResolvedValue(candidate);
    const service = createQualityReviewService({
      listGenerationQualityReviewCandidates,
      getGenerationQualityReviewCandidate,
      createGenerationQualityReviewDecision,
    });
    const controller = new AbortController();

    await service.list({
      status: "PENDING_APPROVAL",
      classification: "EXACT_DUPLICATE_TRAP",
      materialization: "COMPLETE",
      search: candidate.candidateId,
      cursor: candidate.candidateId,
      limit: 25,
    }, controller.signal);
    await service.get(candidate.candidateId, controller.signal);
    await service.decide(candidate.candidateId, {
      decision: "APPROVE",
      expectedArtifactFingerprint: `sha256:${"a".repeat(64)}`,
      idempotencyKey: "quality-review-approve-001",
      reasonCode: null,
    }, "reviewer-token-in-memory", controller.signal);

    expect(listGenerationQualityReviewCandidates).toHaveBeenCalledWith(expect.objectContaining({
      status: "PENDING_APPROVAL",
      classification: "EXACT_DUPLICATE_TRAP",
      materialization: "COMPLETE",
      search: candidate.candidateId,
      cursor: candidate.candidateId,
    }), controller.signal);
    expect(getGenerationQualityReviewCandidate).toHaveBeenCalledWith(candidate.candidateId, controller.signal);
    expect(createGenerationQualityReviewDecision).toHaveBeenCalledWith(
      candidate.candidateId,
      expect.objectContaining({ expectedArtifactFingerprint: `sha256:${"a".repeat(64)}` }),
      {
        reviewerToken: "reviewer-token-in-memory",
        idempotencyKey: "quality-review-approve-001",
        signal: controller.signal,
      },
    );
  });

  it("creates ephemeral decision keys without touching local or session storage", () => {
    const storage = { getItem: vi.fn(), setItem: vi.fn(() => { throw new Error("storage must not be used"); }) };
    vi.stubGlobal("localStorage", storage);
    vi.stubGlobal("sessionStorage", storage);

    const first = qualityReviewDecisionKey(candidate.candidateId, "APPROVE");
    const second = qualityReviewDecisionKey(candidate.candidateId, "APPROVE");

    expect(first).toMatch(/^quality-review-approve-review-candidate:/);
    expect(second).not.toBe(first);
    expect(storage.getItem).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("classifies auth expiry, stale conflicts and setup failures without exposing token data", () => {
    const response = (code: string) => ({ code, message: "safe", details: {}, traceId: "trace_quality" });
    const auth = new ApiClientError("review", 401, response("QUALITY_REVIEWER_UNAUTHORIZED"));
    const stale = new ApiClientError("review", 409, response("QUALITY_REVIEW_STALE_SNAPSHOT"));
    const unavailable = new ApiClientError("review", 503, response("QUALITY_REVIEW_AUTHORITY_NOT_CONFIGURED"));

    expect(qualityReviewErrorKind(auth)).toBe("auth");
    expect(qualityReviewErrorKind(stale)).toBe("stale");
    expect(qualityReviewErrorKind(unavailable)).toBe("unavailable");
    expect(qualityReviewErrorCode(stale)).toBe("QUALITY_REVIEW_STALE_SNAPSHOT");
  });
});
