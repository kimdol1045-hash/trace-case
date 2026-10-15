import { ApiClientError } from "@tc/api-client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiErrorCode, apiErrorMessage, sprint4Api, sprint4LifecycleApi } from "./sprint4-service";

afterEach(() => vi.unstubAllGlobals());

describe("Sprint 4 generated-client boundary", () => {
  it("never substitutes demo issues when the ledger is unavailable", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network unavailable"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(sprint4Api.listIssues({ search: "ISSUE-104" })).rejects.toThrow("network unavailable");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("turns typed optimistic concurrency errors into actionable UI copy", () => {
    const error = new ApiClientError("conflict", 409, {
      code: "VERSION_CONFLICT",
      message: "stale resource version",
      details: {},
      traceId: "trace-test",
    });

    expect(apiErrorCode(error)).toBe("VERSION_CONFLICT");
    expect(apiErrorMessage(error, "fallback")).toContain("최신 데이터");
  });

  it("turns permanent-delete reference conflicts into actionable UI copy", () => {
    const error = new ApiClientError("conflict", 409, {
      code: "PERMANENT_DELETE_CONFLICT",
      message: "resource still has references",
      details: {},
      traceId: "trace-delete-conflict",
    });

    expect(apiErrorMessage(error, "fallback")).toContain("연결 데이터");
  });
});

describe("deleted entity lifecycle boundary", () => {
  it("requests only deleted issues through the generated client", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0, nextCursor: null }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await sprint4LifecycleApi.listDeletedIssues({ search: "결제", statuses: ["CLOSED"], limit: 25 });

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/issues?search=%EA%B2%B0%EC%A0%9C&statuses=CLOSED&limit=25&includeDeleted=true&deletedOnly=true");
  });

  it("opens deleted cycle data and its pinned test cases with includeDeleted", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "cycle_01", deletedAt: "2026-08-25T01:00:00Z" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], total: 0, nextCursor: null }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await sprint4LifecycleApi.getCycle("cycle_01");
    await sprint4LifecycleApi.listCycleItems("cycle_01", { limit: 100 });

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/cycles/cycle_01?includeDeleted=true");
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("/api/cycles/cycle_01/items?limit=100&includeDeleted=true");
  });

  it("sends restore and permanent-delete mutations with concurrency and audit headers", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ issue: { id: "issue_01" } }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ issueId: "issue_01", auditId: "audit_01", permanentlyDeleted: true }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await sprint4LifecycleApi.restoreIssue("issue_01", { baseResourceVersion: 4, reason: "restore" }, { idempotencyKey: "restore-key", actorLabel: "Issue Web UI" });
    await sprint4LifecycleApi.permanentlyDeleteIssue("issue_01", { baseResourceVersion: 5, reason: "permanent" }, { idempotencyKey: "delete-key", actorLabel: "Issue Web UI" });

    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "POST", headers: expect.objectContaining({ "Idempotency-Key": "restore-key", "X-Actor-Label": "Issue Web UI" }) });
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: "DELETE", body: JSON.stringify({ baseResourceVersion: 5, reason: "permanent" }) });
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("/api/issues/issue_01/permanent");
  });
});
