import { afterEach, describe, expect, it, vi } from "vitest";

import {
  knowledgeApi,
  listKnowledgeNodes,
  patchKnowledge,
  searchKnowledge,
} from "./knowledge-service";

afterEach(() => vi.unstubAllGlobals());

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("Knowledge generated-client boundary", () => {
  it("loads ACTIVE nodes from the real knowledge endpoint without a mock fallback", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [], total: 0, nextCursor: null }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listKnowledgeNodes({ statuses: ["ACTIVE"], limit: 100 })).resolves.toEqual({
      items: [],
      total: 0,
      nextCursor: null,
    });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("/api/knowledge/nodes");
    expect(url).toContain("statuses=ACTIVE");
  });

  it("sends hybrid-search filters and preserves typed degraded metadata", async () => {
    const source = {
      sourceType: "MANUAL" as const,
      sourceEntityId: "source-payment",
      sourceRevisionId: null,
    };
    const response = {
      query: "결제",
      items: [{
        rank: 3,
        nodeId: "decision-payment",
        nodeType: "DECISION",
        label: "결제 승인 정책",
        content: "결제 승인 정책 내용",
        score: 0.72,
        reason: {
          summary: "Rank 3 with score 0.720 from typed graph and recency signals.",
          signals: ["TYPED_GRAPH", "RECENCY"],
        },
        status: "SUPERSEDED",
        source,
        featureDistance: null,
        recency: { basisAt: "2026-08-20T01:00:00Z", ageSeconds: 86_400, score: 0.5 },
        degraded: true,
        degradationReasons: ["LIGHTRAG_UNAVAILABLE"],
        citation: {
          source,
          label: "결제 승인 정책 근거",
          uri: null,
          excerpt: "결제 승인 정책 내용",
          occurredAt: "2026-08-20T01:00:00Z",
          deleted: false,
        },
      }],
      nextCursor: null,
      total: 3,
      degraded: true,
      degradationReasons: ["LIGHTRAG_UNAVAILABLE"],
      executedAt: "2026-08-21T01:00:00Z",
    } as const;
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal("fetch", fetchMock);

    const result = await searchKnowledge({
      query: "결제",
      statuses: ["SUPERSEDED"],
      sourceTypes: [],
      nodeTypes: ["DECISION"],
      featureId: null,
      limit: 1,
      cursor: "eyJvZmZzZXQiOjJ9",
    });
    expect(result).toEqual(response);
    expect(result.items[0]).toMatchObject({
      rank: 3,
      status: "SUPERSEDED",
      featureDistance: null,
      degraded: true,
      degradationReasons: ["LIGHTRAG_UNAVAILABLE"],
    });
    const [, request] = fetchMock.mock.calls[0] ?? [];
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/knowledge/search");
    expect(JSON.parse(String((request as RequestInit).body))).toMatchObject({
      query: "결제",
      statuses: ["SUPERSEDED"],
      nodeTypes: ["DECISION"],
      featureId: null,
      cursor: "eyJvZmZzZXQiOjJ9",
    });
  });

  it("posts evidence-backed optimistic status patches through the generated client", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ target: {}, audit: {}, projectionQueued: true }));
    vi.stubGlobal("fetch", fetchMock);

    await patchKnowledge({
      targetType: "NODE",
      targetId: "decision-1",
      expectedVersion: 4,
      status: "SUPERSEDED",
      scopeNodeIds: ["feature-1"],
      validFrom: "2026-08-21T01:00:00Z",
      validTo: "2026-08-22T01:00:00Z",
      effectiveAt: "2026-08-21T02:00:00Z",
      reason: "정책 변경",
      evidenceIds: ["evidence-1"],
      changedBy: "qa-owner",
    });
    const [url, request] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toContain("/api/knowledge/status");
    expect((request as RequestInit).method).toBe("PATCH");
    expect(JSON.parse(String((request as RequestInit).body))).toMatchObject({
      expectedVersion: 4,
      evidenceIds: ["evidence-1"],
      changedBy: "qa-owner",
    });
  });

  it("surfaces network failure instead of substituting demo knowledge", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("postgres unavailable"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(knowledgeApi.listKnowledgeNodes({ statuses: ["ACTIVE"] })).rejects.toThrow("postgres unavailable");
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
