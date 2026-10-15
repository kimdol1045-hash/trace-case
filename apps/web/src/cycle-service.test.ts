import { ApiClientError } from "@tc/api-client";
import { describe, expect, it, vi } from "vitest";

import {
  CycleService,
  CycleServiceError,
  isCycleMockFallbackEnabled,
  loadCycleDetailWithPartialProgress,
  type CycleApiClient,
} from "./cycle-service";

function clientWithList(listCycles: CycleApiClient["listCycles"]) {
  return { listCycles } as CycleApiClient;
}

describe("CycleService", () => {
  it("returns generated-client responses without reshaping the public contract", async () => {
    const response = { items: [], nextCursor: null, total: 0 };
    const listCycles = vi.fn().mockResolvedValue(response);
    const service = new CycleService(clientWithList(listCycles));

    await expect(service.call((api) => api.listCycles({ statuses: ["PLANNED"] }))).resolves.toBe(response);
    expect(listCycles).toHaveBeenCalledWith({ statuses: ["PLANNED"] });
  });

  it("keeps canonical typed cycle errors for conflict UI", async () => {
    const listCycles = vi.fn().mockRejectedValue(new ApiClientError("conflict", 409, {
      code: "VERSION_CONFLICT",
      message: "resourceVersion mismatch",
      details: { expected: 4, actual: 5 },
      traceId: "trace-cycle-conflict",
    }));
    const service = new CycleService(clientWithList(listCycles));

    await expect(service.call((api) => api.listCycles())).rejects.toMatchObject({
      name: "CycleServiceError",
      code: "VERSION_CONFLICT",
      details: { expected: 4, actual: 5 },
    });
    expect(service.fallbackActive).toBe(false);
  });

  it("latches availability failures into sticky fallback until explicit retry", async () => {
    const listCycles = vi.fn()
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce({ items: [], nextCursor: null, total: 0 });
    const service = new CycleService(clientWithList(listCycles), true);

    await expect(service.call((api) => api.listCycles())).rejects.toMatchObject({ code: "API_UNAVAILABLE" });
    await expect(service.call((api) => api.listCycles())).rejects.toEqual(expect.objectContaining({
      code: "FALLBACK_ACTIVE",
    }));
    expect(listCycles).toHaveBeenCalledTimes(1);

    service.resetFallback();
    await expect(service.call((api) => api.listCycles())).resolves.toMatchObject({ total: 0 });
    expect(listCycles).toHaveBeenCalledTimes(2);
  });

  it("never activates or serves fake data after production availability failures", async () => {
    const listCycles = vi.fn().mockRejectedValue(new TypeError("fetch failed"));
    const service = new CycleService(clientWithList(listCycles));

    await expect(service.call((api) => api.listCycles())).rejects.toMatchObject({
      code: "API_UNAVAILABLE",
      message: expect.stringContaining("가짜 데이터를 표시하지 않습니다"),
    });
    await expect(service.call((api) => api.listCycles())).rejects.toMatchObject({
      code: "API_UNAVAILABLE",
    });
    expect(service.fallbackActive).toBe(false);
    expect(listCycles).toHaveBeenCalledTimes(2);
  });

  it("accepts the mock flag only in explicit development mode", () => {
    expect(isCycleMockFallbackEnabled("production", "true")).toBe(false);
    expect(isCycleMockFallbackEnabled("development", undefined)).toBe(false);
    expect(isCycleMockFallbackEnabled("development", "false")).toBe(false);
    expect(isCycleMockFallbackEnabled("development", "true")).toBe(true);
  });

  it("uses a CycleServiceError for non-HTTP failures", async () => {
    const service = new CycleService(clientWithList(vi.fn().mockRejectedValue(new Error("boom"))));

    await expect(service.call((api) => api.listCycles())).rejects.toBeInstanceOf(CycleServiceError);
  });

  it("retains cycle and queue data when only the progress aggregate fails", async () => {
    const detail = await loadCycleDetailWithPartialProgress({
      cycle: async () => ({ status: "IN_PROGRESS" as const, id: "cycle-1" }),
      items: async () => ({ items: [{ id: "item-1" }], total: 1 }),
      progress: async () => { throw new Error("aggregate timeout"); },
    });

    expect(detail.cycle.id).toBe("cycle-1");
    expect(detail.items.total).toBe(1);
    expect(detail.progress).toBeUndefined();
    expect(detail.partialFailure).toContain("aggregate timeout");
  });

  it("does not request active-only progress for a deleted cycle detail", async () => {
    const progress = vi.fn();
    const detail = await loadCycleDetailWithPartialProgress({
      cycle: async () => ({ status: "COMPLETED" as const, deletedAt: "2026-09-07T00:00:00Z", id: "cycle-1" }),
      items: async () => ({ items: [{ id: "item-1" }], total: 1 }),
      progress,
    });

    expect(progress).not.toHaveBeenCalled();
    expect(detail.progress).toBeUndefined();
    expect(detail.partialFailure).toBeUndefined();
  });
});
