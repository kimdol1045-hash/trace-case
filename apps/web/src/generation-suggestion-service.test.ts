import { describe, expect, it, vi } from "vitest";

import {
  createGenerationSuggestionApi,
  type GenerationSuggestion,
} from "./generation-suggestion-service";

const suggestion = {
  id: "generation_suggestion_1",
  testCaseId: "case_1",
  generationLineageId: "generation_lineage_1",
  state: "OPEN",
  baseline: {
    caseResourceVersion: 4,
    caseVersionId: "case_version_4",
    caseVersion: 4,
    snapshot: { summary: "기존", steps: [], priority: "HIGH", labels: [] },
    digest: "a".repeat(64),
  },
  proposal: {
    snapshot: { summary: "제안", steps: [], priority: "HIGH", labels: [] },
    digest: "b".repeat(64),
  },
  diff: {
    schemaVersion: "generation-suggestion-diff/1.0.0",
    digest: "c".repeat(64),
    changedFields: ["summary"],
    changes: [{ path: "summary", before: "기존", after: "제안" }],
  },
  source: { generationJobId: "generation_job_5", sourceVersion: 7, generationVersion: 5 },
  resolution: null,
  supersededBySuggestionId: null,
  createdAt: "2026-08-27T04:00:00Z",
} satisfies GenerationSuggestion;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("generation suggestion service", () => {
  it("uses the additive list and detail wire paths without mutating on read", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [suggestion] }))
      .mockResolvedValueOnce(jsonResponse(suggestion));
    const api = createGenerationSuggestionApi("https://api.example.test/", fetchMock as unknown as typeof fetch);

    await expect(api.listTestCaseSuggestions("case_1")).resolves.toEqual([suggestion]);
    await expect(api.getSuggestion(suggestion.id)).resolves.toEqual(suggestion);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "https://api.example.test/api/test-cases/case_1/generation-suggestions",
      { signal: undefined },
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "https://api.example.test/api/generation-suggestions/generation_suggestion_1",
      { signal: undefined },
    );
  });

  it("sends only decision pins/reason with matching idempotency headers", async () => {
    const receipt = {
      suggestion: { ...suggestion, state: "APPLIED" as const },
      resolution: { decision: "APPLIED" as const },
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(receipt));
    const api = createGenerationSuggestionApi("https://api.example.test", fetchMock as unknown as typeof fetch);
    const payload = {
      idempotencyKey: "suggestion-apply-1",
      expectedCaseResourceVersion: 4,
      expectedCaseVersionId: "case_version_4",
      changeReason: "검토한 제안 반영",
    };

    await api.applySuggestion(suggestion.id, payload, {
      idempotencyKey: payload.idempotencyKey,
      actorLabel: "Test Library UI",
    });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example.test/api/generation-suggestions/generation_suggestion_1/apply");
    expect(init.headers).toMatchObject({
      "Idempotency-Key": payload.idempotencyKey,
      "X-Actor-Label": "Test Library UI",
    });
    expect(JSON.parse(String(init.body))).toEqual(payload);
    expect(String(init.body)).not.toContain("proposal");
    expect(String(init.body)).not.toContain("steps");
    expect(String(init.body)).not.toContain("generationReason");
  });

  it("requires the body and header idempotency keys to match", () => {
    const fetchMock = vi.fn();
    const api = createGenerationSuggestionApi("https://api.example.test", fetchMock as unknown as typeof fetch);

    expect(() => api.ignoreSuggestion(
      suggestion.id,
      { idempotencyKey: "body-key", reason: "사람 수정 유지" },
      { idempotencyKey: "header-key" },
    )).toThrow("Request idempotencyKey must match options.idempotencyKey");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
