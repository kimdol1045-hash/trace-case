import {
  ApiClientError,
  createApiClient,
  type CreateGenerationQualityDecisionRequest,
  type GenerationQualityCandidate,
  type GenerationQualityCandidatePage,
  type GenerationQualityCandidateQuery,
} from "@tc/api-client";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

type GeneratedQualityReviewClient = Pick<
  ReturnType<typeof createApiClient>,
  | "listGenerationQualityReviewCandidates"
  | "getGenerationQualityReviewCandidate"
  | "createGenerationQualityReviewDecision"
>;

export type QualityReviewCandidate = GenerationQualityCandidate;
export type QualityReviewCandidatePage = GenerationQualityCandidatePage;
export type QualityReviewCandidateQuery = GenerationQualityCandidateQuery;
export type QualityReviewDecisionRequest = CreateGenerationQualityDecisionRequest;
export type QualityReviewDecision = QualityReviewDecisionRequest["decision"];

export interface QualityReviewService {
  list(
    query?: QualityReviewCandidateQuery,
    signal?: AbortSignal,
  ): Promise<QualityReviewCandidatePage>;
  get(candidateId: string, signal?: AbortSignal): Promise<QualityReviewCandidate>;
  decide(
    candidateId: string,
    payload: QualityReviewDecisionRequest,
    reviewerToken: string,
    signal?: AbortSignal,
  ): Promise<QualityReviewCandidate>;
}

export function createQualityReviewService(
  client: GeneratedQualityReviewClient,
): QualityReviewService {
  return {
    list(query = {}, signal) {
      return client.listGenerationQualityReviewCandidates(query, signal);
    },
    get(candidateId, signal) {
      return client.getGenerationQualityReviewCandidate(candidateId, signal);
    },
    decide(candidateId, payload, reviewerToken, signal) {
      return client.createGenerationQualityReviewDecision(candidateId, payload, {
        reviewerToken,
        idempotencyKey: payload.idempotencyKey,
        signal,
      });
    },
  };
}

export const qualityReviewService = createQualityReviewService(createApiClient(apiBaseUrl));

export type QualityReviewErrorKind =
  | "auth"
  | "stale"
  | "unavailable"
  | "validation"
  | "unknown";

export function qualityReviewErrorKind(error: unknown): QualityReviewErrorKind {
  if (!(error instanceof ApiClientError)) return "unknown";
  if (error.status === 401) return "auth";
  if (error.status === 409) return "stale";
  if (error.status === 503) return "unavailable";
  if (error.status === 422) return "validation";
  return "unknown";
}

export function qualityReviewErrorCode(error: unknown) {
  if (!(error instanceof ApiClientError)) return undefined;
  return error.response?.code;
}

/** Creates a request-scoped key without writing reviewer or decision state to browser storage. */
export function qualityReviewDecisionKey(
  candidateId: string,
  decision: QualityReviewDecision,
) {
  const nonce = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `quality-review-${decision.toLowerCase()}-${candidateId}-${nonce}`;
}
