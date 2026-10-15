import { describe, expect, it, vi } from "vitest";

import {
  aggregateExecutionStatuses,
  appendCursorPage,
  canEditCycleExecution,
  canRetryCyclePersistence,
  resolveKeyedCycleResource,
} from "./cycle-route-state";
import { deriveStepExecutionStatus } from "./cycle-service";
import { createDebouncedAutosaveQueue } from "./cycle-workspace-page";

const { readFileSync } = await vi.importActual<{ readFileSync: (path: URL, encoding: "utf8") => string }>("node:fs");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
const cycleWorkspaceSource = readFileSync(new URL("./cycle-workspace-page.tsx", import.meta.url), "utf8");

describe("cycle step execution result", () => {
  it("keeps an incomplete step set UNEXECUTED and calculates terminal precedence", () => {
    expect(deriveStepExecutionStatus([undefined, undefined])).toBe("UNEXECUTED");
    expect(deriveStepExecutionStatus(["PASS", undefined])).toBe("UNEXECUTED");
    expect(deriveStepExecutionStatus(["PASS", "PASS"])).toBe("PASS");
    expect(deriveStepExecutionStatus(["BLOCKED", undefined])).toBe("UNEXECUTED");
    expect(deriveStepExecutionStatus(["PASS", "BLOCKED"])).toBe("BLOCKED");
    expect(deriveStepExecutionStatus(["BLOCKED", "FAIL", "PASS"])).toBe("FAIL");
  });

  it("ignores unexecuted and blocked platforms and keeps executable-result precedence", () => {
    expect(aggregateExecutionStatuses(["UNEXECUTED", "UNEXECUTED", "UNEXECUTED"])).toBe("UNEXECUTED");
    expect(aggregateExecutionStatuses(["PASS", "UNEXECUTED", "UNEXECUTED"])).toBe("PASS");
    expect(aggregateExecutionStatuses(["BLOCKED", "BLOCKED", "UNEXECUTED"])).toBe("UNEXECUTED");
    expect(aggregateExecutionStatuses(["PASS", "BLOCKED", "UNEXECUTED"])).toBe("PASS");
    expect(aggregateExecutionStatuses(["PASS", "FAIL", "FAIL"])).toBe("FAIL");
  });

  it("merges ten cursor pages into a stable 1,000-row execution queue", () => {
    let rows: Array<{ id: string }> = [];
    for (let page = 0; page < 10; page += 1) {
      const next = Array.from({ length: 100 }, (_, index) => ({ id: `execution-${page * 100 + index}` }));
      rows = appendCursorPage(rows, next, 1000).items;
    }
    const completed = appendCursorPage(rows, [{ id: "execution-999" }], 1000);
    expect(completed.loaded).toBe(1000);
    expect(completed.complete).toBe(true);
    expect(new Set(completed.items.map((item) => item.id)).size).toBe(1000);
  });

  it("does not save hydration and debounces a platform step to its latest draft", async () => {
    vi.useFakeTimers();
    const persist = vi.fn(async () => true);
    const queue = createDebouncedAutosaveQueue(persist, 500);
    try {
      const hydrated = { status: "PASS", note: "persisted" };
      queue.hydrate("case-1:WEB:1", hydrated);
      await vi.advanceTimersByTimeAsync(1_000);
      expect(persist).not.toHaveBeenCalled();

      queue.schedule("case-1:WEB:1", hydrated);
      await vi.advanceTimersByTimeAsync(500);
      expect(persist).not.toHaveBeenCalled();

      queue.schedule("case-1:WEB:1", { status: "FAIL", note: "first" });
      queue.schedule("case-1:WEB:1", { status: "BLOCKED", note: "latest" });
      await vi.advanceTimersByTimeAsync(499);
      expect(persist).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);
      expect(persist).toHaveBeenCalledTimes(1);
      expect(persist).toHaveBeenLastCalledWith({ status: "BLOCKED", note: "latest" });
    } finally {
      queue.dispose();
      vi.useRealTimers();
    }
  });

  it("serializes an edit queued while an autosave request is in flight", async () => {
    vi.useFakeTimers();
    let releaseFirst: () => void = () => undefined;
    const firstRequest = new Promise<void>((resolve) => { releaseFirst = resolve; });
    let activeRequests = 0;
    let maxActiveRequests = 0;
    const persist = vi.fn(async (draft: { status: string; note: string }) => {
      activeRequests += 1;
      maxActiveRequests = Math.max(maxActiveRequests, activeRequests);
      if (draft.note === "first") await firstRequest;
      activeRequests -= 1;
      return true;
    });
    const queue = createDebouncedAutosaveQueue(persist, 500);
    try {
      queue.schedule("case-1:AOS:2", { status: "FAIL", note: "first" });
      await vi.advanceTimersByTimeAsync(500);
      expect(persist).toHaveBeenCalledTimes(1);

      queue.schedule("case-1:AOS:2", { status: "FAIL", note: "latest" });
      await vi.advanceTimersByTimeAsync(500);
      expect(persist).toHaveBeenCalledTimes(1);
      releaseFirst();
      for (let index = 0; index < 5; index += 1) await Promise.resolve();

      expect(persist).toHaveBeenCalledTimes(2);
      expect(persist).toHaveBeenLastCalledWith({ status: "FAIL", note: "latest" });
      expect(maxActiveRequests).toBe(1);
    } finally {
      queue.dispose();
      vi.useRealTimers();
    }
  });

  it("reactivates after the StrictMode setup-cleanup probe", async () => {
    vi.useFakeTimers();
    const persist = vi.fn(async () => true);
    const queue = createDebouncedAutosaveQueue(persist, 500);
    try {
      queue.dispose();
      queue.activate();
      queue.schedule("case-1:WEB:1", { status: "PASS", note: "after strict probe" });
      await vi.advanceTimersByTimeAsync(500);
      expect(persist).toHaveBeenCalledOnce();
    } finally {
      queue.dispose();
      vi.useRealTimers();
    }
  });

  it("uses autosave controls without refreshing the cycle resource", () => {
    expect(cycleWorkspaceSource).toContain("stepAutosaveQueue.schedule");
    expect(cycleWorkspaceSource).toContain("상태와 비고는 잠시 후 자동 저장됩니다.");
    expect(cycleWorkspaceSource).not.toContain("tm-save-step-results");
    expect(cycleWorkspaceSource).not.toContain("resource.retry();");
  });
});

describe("cycle route loading state", () => {
  it("does not expose detail data that belongs to the previous route", () => {
    const resolved = resolveKeyedCycleResource("cycle-new", {
      key: "cycle-previous",
      state: { phase: "ready" },
    }, { phase: "loading" });

    expect(resolved.data).toBeUndefined();
    expect(resolved.state).toEqual({ phase: "loading" });
  });

  it("keeps data only when a same-route detail is revalidated", () => {
    const data = { name: "same cycle" };
    const resolved = resolveKeyedCycleResource("cycle-current", {
      key: "cycle-current",
      data,
      state: { phase: "loading" },
    }, { phase: "loading" });

    expect(resolved.data).toBe(data);
    expect(resolved.state).toEqual({ phase: "loading" });
  });
});

describe("cycle persistence recovery", () => {
  it("offers API reconnect only for availability failures", () => {
    expect(canRetryCyclePersistence({ retryable: true })).toBe(true);
    expect(canRetryCyclePersistence({ retryable: false })).toBe(false);
    expect(canRetryCyclePersistence({})).toBe(false);
  });
});

describe("cycle execution gate", () => {
  it("allows result input only while the cycle is in progress", () => {
    expect(canEditCycleExecution("PLANNED")).toBe(false);
    expect(canEditCycleExecution("IN_PROGRESS")).toBe(true);
    expect(canEditCycleExecution("COMPLETED")).toBe(false);
  });
});

describe("cycle deletion", () => {
  it("allows soft deletion in every lifecycle state and explains preserved history", () => {
    expect(cycleWorkspaceSource).not.toContain('disabled={status !== "PLANNED"}');
    expect(cycleWorkspaceSource).toContain("실행 결과와 연결된 이슈 이력은 보존됩니다.");
    expect(cycleWorkspaceSource).toContain('<DeleteConfirmDialog open={deletePending} title="사이클 삭제"');
    expect(cycleWorkspaceSource).toContain('disabled={mutationState.phase === "loading"} onClick={() => setDeletePending(true)}');
  });

  it("exposes a deleted view and restores the original lifecycle state", () => {
    expect(cycleWorkspaceSource).toContain('role="tab" aria-selected={viewMode === "DELETED"}');
    expect(cycleWorkspaceSource).toContain('{ deletedOnly: true }');
    expect(cycleWorkspaceSource).toContain("api.restoreCycle(cycle.id");
    expect(cycleWorkspaceSource).toContain("상태로 복구했습니다.");
  });

  it("opens a deleted cycle as read-only with its pinned cases and destructive lifecycle actions", () => {
    expect(cycleWorkspaceSource).toContain("sprint4LifecycleApi.getCycle(cycleId)");
    expect(cycleWorkspaceSource).toContain("sprint4LifecycleApi.listCycleItems(cycleId, { limit: 100 })");
    expect(cycleWorkspaceSource).toContain('className="tm-deleted-readonly-banner"');
    expect(cycleWorkspaceSource).toContain('disabled={status === "COMPLETED" || isDeleted}');
    expect(cycleWorkspaceSource).toContain("삭제 시점에 보존된 pinned 테스트케이스입니다.");
    expect(cycleWorkspaceSource).toContain("void restoreDeleted()");
    expect(cycleWorkspaceSource).toContain("sprint4LifecycleApi.permanentlyDeleteCycle");
  });

  it("requires the exact cycle name before permanent deletion", () => {
    expect(cycleWorkspaceSource).toContain("confirmation === cycleName");
    expect(cycleWorkspaceSource).not.toContain("confirmation.trim() === cycleName");
    expect(cycleWorkspaceSource).toContain('aria-label="완전 삭제 확인용 사이클명"');
    expect(cycleWorkspaceSource).toContain('disabled={!confirmed || busy}');
    expect(styles).toContain(".tm-permanent-delete-modal");
    expect(styles).toContain(".tm-deleted-readonly-banner");
  });
});

describe("cycle progress palette", () => {
  it("keeps list and detail progress indicators on semantic non-blue product tokens", () => {
    const completionProgressRule = styles.match(/\.tm-cycle-completion-progress\s*\{([^}]*)\}/s)?.[1];

    expect(styles).toContain("--tm-cycle-progress-primary: var(--tm-product-primary);");
    expect(styles).toContain("--tm-cycle-progress-fail: #c9372c;");
    expect(styles).toContain("--tm-cycle-progress-blocked: #e2b203;");
    expect(styles).toContain("--tm-cycle-progress-neutral: #a5adba;");
    expect(styles).toContain("--tm-cycle-progress-track: #ebecf0;");
    expect(styles).toMatch(/\.tm-cycle-progress > span\.is-pass\s*\{\s*background: var\(--tm-cycle-progress-primary\);\s*\}/);
    expect(styles).toMatch(/\.tm-cycle-progress > span\.is-fail\s*\{\s*background: var\(--tm-cycle-progress-fail\);\s*\}/);
    expect(styles).toMatch(/\.tm-cycle-progress > span\.is-wip,\s*\.tm-cycle-progress > span\.is-blocked\s*\{\s*background: var\(--tm-cycle-progress-blocked\);\s*\}/);
    expect(styles).not.toMatch(/\.tm-cycle-progress > span\.is-unexecuted/);
    expect(completionProgressRule).toContain("accent-color: var(--tm-cycle-progress-primary);");
    expect(completionProgressRule).toContain("background: var(--tm-cycle-progress-track);");
    expect(completionProgressRule).not.toMatch(/#3867d6|\bblue\b/i);
    expect(styles).toMatch(/\.tm-cycle-completion-progress::-webkit-progress-value\s*\{\s*background: var\(--tm-cycle-progress-primary\);\s*\}/);
    expect(styles).toMatch(/\.tm-cycle-completion-progress::-moz-progress-bar\s*\{\s*background: var\(--tm-cycle-progress-primary\);\s*\}/);
  });
});
