import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type SetStateAction,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import type {
  AttachmentResource,
  CycleItem,
  CyclePageResponse,
  CycleProgressResponse,
  CycleResource,
  ExecutionAttempt,
  IssueResource,
  ScopePreviewResponse,
  StepExecution,
} from "@tc/api-client";

import {
  CycleServiceError,
  cycleMockFallbackEnabled,
  cycleMutationOptions,
  deriveStepExecutionStatus,
  getDefaultCycleService,
  isCycleAvailabilityError,
  isCycleConflict,
  loadCycleDetailWithPartialProgress,
} from "./cycle-service";
import { useAccessibleDialog } from "./dialog-accessibility";
import { DeleteConfirmDialog } from "./delete-confirm-dialog";
import {
  aggregateExecutionStatuses,
  appendCursorPage,
  canEditCycleExecution,
  canRetryCyclePersistence,
  resolveKeyedCycleResource,
} from "./cycle-route-state";
import {
  getWorkItemKey,
  initialTestManagementState,
  useTestManagement,
  type CycleScopeType,
  type DemoCycle,
  type DemoExecution,
  type ExecutionStatus,
  type TestManagementDemoState,
} from "./test-management-demo";
import { appNavigationOptions } from "./navigation-policy";
import { resolveAttachmentMimeType } from "./attachment-preview-utils";
import {
  apiErrorMessage,
  mutationKey,
  sprint4Api,
  sprint4LifecycleApi,
  type DeletedCycleResource,
} from "./sprint4-service";

type Platform = "WEB" | "AOS" | "IOS";
type ApiCycleStatus = "PLANNED" | "IN_PROGRESS" | "COMPLETED";
type PersistencePhase = "loading" | "ready" | "fallback" | "error" | "conflict" | "partial" | "not-found";

interface PersistenceState {
  phase: PersistencePhase;
  message?: string;
  retryable?: boolean;
}

interface ScopeOptionModel {
  key: string;
  title: string;
  detail: string;
  caseCount: number;
  categoryId?: string;
  categoryName?: string;
  scope: CycleResource["scope"];
}

type TerminalStatus = typeof terminalStatuses[number];
type StepStatusDraft = Record<number, TerminalStatus | undefined>;

interface CycleItemModel {
  id: string;
  caseKey: string;
  summary: string;
  owner: string;
  labels: string[];
  precondition: string;
  steps: Array<{ sequence: number; action: string; expectedResult?: string; precondition?: string }>;
  pinnedVersion: number;
  pinnedVersionId: string;
  latestVersion: number;
  latestVersionId: string;
  isLatestVersion: boolean;
  statuses: Record<Platform, ExecutionStatus>;
  attempts: Partial<Record<Platform, ExecutionAttempt>>;
  raw?: CycleItem;
}

interface ExecutionIssueDraft {
  summary: string;
  precondition: string;
  reproductionStep: string;
  expectedResult: string;
  actualResult: string;
}

interface StepAutosaveDraft {
  itemId: string;
  platform: Platform;
  sequence: number;
  status: TerminalStatus | undefined;
  note: string;
}

interface DebouncedAutosaveQueue<T> {
  activate: () => void;
  hydrate: (key: string, value: T) => void;
  schedule: (key: string, value: T) => void;
  dispose: () => void;
}

// eslint-disable-next-line react-refresh/only-export-components -- exported for deterministic debounce/serialization tests.
export function createDebouncedAutosaveQueue<T>(
  persist: (value: T) => Promise<boolean | void>,
  delayMs: number,
  fingerprint: (value: T) => string = (value) => JSON.stringify(value),
): DebouncedAutosaveQueue<T> {
  const saved = new Map<string, string>();
  const pending = new Map<string, T>();
  const ready = new Set<string>();
  const timers = new Map<string, ReturnType<typeof setTimeout>>();
  const inFlight = new Map<string, string>();
  let saving = false;
  let disposed = false;

  const drain = async () => {
    if (saving || disposed) return;
    saving = true;
    try {
      while (!disposed && ready.size) {
        const key = ready.values().next().value;
        if (key === undefined) break;
        ready.delete(key);
        const value = pending.get(key);
        pending.delete(key);
        if (value === undefined) continue;
        const signature = fingerprint(value);
        if (saved.get(key) === signature) continue;
        inFlight.set(key, signature);
        try {
          const persisted = await persist(value);
          if (persisted !== false) saved.set(key, signature);
        } catch {
          // The persistence callback owns the user-facing error state. A later edit retries.
        } finally {
          inFlight.delete(key);
        }
      }
    } finally {
      saving = false;
      if (!disposed && ready.size) void drain();
    }
  };

  const cancelScheduled = (key: string) => {
    const timer = timers.get(key);
    if (timer !== undefined) clearTimeout(timer);
    timers.delete(key);
    pending.delete(key);
    ready.delete(key);
  };

  return {
    activate() {
      disposed = false;
    },
    hydrate(key, value) {
      if (disposed) return;
      saved.set(key, fingerprint(value));
    },
    schedule(key, value) {
      if (disposed) return;
      const signature = fingerprint(value);
      const activeSignature = inFlight.get(key);
      if (activeSignature === signature || (activeSignature === undefined && saved.get(key) === signature)) {
        cancelScheduled(key);
        return;
      }
      pending.set(key, value);
      ready.delete(key);
      const currentTimer = timers.get(key);
      if (currentTimer !== undefined) clearTimeout(currentTimer);
      timers.set(key, setTimeout(() => {
        timers.delete(key);
        ready.add(key);
        void drain();
      }, delayMs));
    },
    dispose() {
      disposed = true;
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
      pending.clear();
      ready.clear();
    },
  };
}

function stepAutosaveKey(itemId: string, platform: Platform, sequence: number) {
  return `${itemId}:${platform}:${sequence}`;
}

function formatAttachmentBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

const platforms: Platform[] = ["WEB", "AOS", "IOS"];
const terminalStatuses = ["PASS", "FAIL", "BLOCKED"] as const;
const issueAttachmentMimeTypes = new Set<AttachmentResource["mimeType"]>([
  "image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "video/webm",
  "text/plain", "text/markdown", "text/csv", "application/json", "application/pdf", "application/zip", "application/gzip",
]);
const emptyCounts: Record<ExecutionStatus, number> = { UNEXECUTED: 0, PASS: 0, FAIL: 0, BLOCKED: 0 };
const cycleFallbackMessage = "Cycle API 장애가 감지되어 이 화면은 sticky mock snapshot을 사용합니다.";
const cycleStatusMeta: Array<{ status: ApiCycleStatus; label: string; description: string }> = [
  { status: "PLANNED", label: "미진행", description: "범위 확정·시작 대기" },
  { status: "IN_PROGRESS", label: "진행 중", description: "실행 결과 입력 가능" },
  { status: "COMPLETED", label: "완료", description: "결과가 잠긴 사이클" },
];

function toApiStatus(status: DemoCycle["status"]): ApiCycleStatus {
  return status === "IN PROGRESS" ? "IN_PROGRESS" : status;
}

function statusLabel(status: ApiCycleStatus) {
  return cycleStatusMeta.find((item) => item.status === status)?.label ?? status;
}

function statusClass(status: ApiCycleStatus) {
  return status.toLowerCase().replace("_", "-");
}

function scopeText(scope: CycleResource["scope"]) {
  if (scope.type === "WORK_ITEM") return `${scope.workItemKey} · 상위 일감`;
  return (scope.labels?.length ? scope.labels : [scope.label]).map((label) => `#${label}`).join(", ");
}

function mockScope(cycle: DemoCycle, state: TestManagementDemoState): CycleResource["scope"] {
  if (cycle.scopeType === "LABEL") return { type: "LABEL", label: cycle.scopeValue };
  const workItem = state.workItems.find((candidate) => candidate.key === cycle.scopeValue);
  return { type: "WORK_ITEM", workItemId: workItem?.id ?? `mock_${cycle.scopeValue}`, workItemKey: cycle.scopeValue };
}

function mockScopeText(cycle: DemoCycle, state: TestManagementDemoState) {
  if (cycle.scopeType === "LABEL") return `#${cycle.scopeValue}`;
  return `${cycle.scopeValue} · ${state.workItems.find((item) => item.key === cycle.scopeValue)?.summary ?? "상위 일감"}`;
}

function getExecutionCounts(executions: DemoExecution[]) {
  return executions.flatMap((execution) => Object.values(execution.platformStatuses)).reduce<Record<ExecutionStatus, number>>((counts, status) => {
    counts[status] += 1;
    return counts;
  }, { ...emptyCounts });
}

function getItemCounts(items: CycleItemModel[]) {
  return items.flatMap((item) => platforms.map((platform) => item.statuses[platform])).reduce<Record<ExecutionStatus, number>>((counts, status) => {
    counts[status] += 1;
    return counts;
  }, { ...emptyCounts });
}

function aggregateStatuses(statuses: Record<Platform, ExecutionStatus>): ExecutionStatus {
  return aggregateExecutionStatuses(Object.values(statuses));
}

function ProgressBar({ counts }: { counts: Record<ExecutionStatus, number> }) {
  const total = Math.max(Object.values(counts).reduce((sum, count) => sum + count, 0), 1);
  return <span className="tm-cycle-progress" aria-label={`성공 ${counts.PASS}, 실패 ${counts.FAIL}, 차단 ${counts.BLOCKED}, 미실행 ${counts.UNEXECUTED}`}>{(["PASS", "FAIL", "BLOCKED"] as ExecutionStatus[]).map((status) => counts[status] ? <span key={status} className={`is-${status.toLowerCase()}`} style={{ width: `${counts[status] / total * 100}%` }} /> : null)}</span>;
}

function PersistenceBanner({ state, onRetry }: { state: PersistenceState; onRetry?: () => void }) {
  if (state.phase === "ready" || (state.phase === "loading" && !state.message)) return null;
  const copy = state.message ?? "Cycle API 상태를 확인할 수 없습니다.";
  const retry = canRetryCyclePersistence(state) ? onRetry : undefined;
  return <div className={`tm-persistence-banner is-${state.phase}`} role={state.phase === "loading" ? "status" : "alert"}><span>{state.phase === "fallback" ? "MOCK FALLBACK" : state.phase.toUpperCase()}</span><p>{copy}</p>{retry ? <button type="button" onClick={retry}>API 다시 연결</button> : null}</div>;
}

function initialPersistenceState(fallbackActive: boolean): PersistenceState {
  return fallbackActive
    ? { phase: "fallback", message: cycleFallbackMessage, retryable: true }
    : { phase: "loading" };
}

function CycleLoadingSurface({ variant }: { variant: "create" | "detail" | "execution" }) {
  return <section className={`tm-cycle-page tm-cycle-loading tm-cycle-loading--${variant}`} aria-busy="true" aria-label="사이클 화면 준비"><div className="tm-cycle-loading__line is-short" /><div className="tm-cycle-loading__line is-title" /><div className="tm-cycle-loading__cards">{[0, 1, 2, 3].map((item) => <span key={item} />)}</div><div className="tm-cycle-loading__body"><span /><span /><span /></div></section>;
}

function CycleListLoadingRows() {
  return <div className="tm-cycle-list-loading" aria-busy="true" aria-label="사이클 목록 준비">{[0, 1, 2].map((item) => <span key={item} />)}</div>;
}

function PermanentCycleDeleteDialog({ open, cycleName, busy, onClose, onConfirm }: { open: boolean; cycleName: string; busy: boolean; onClose: () => void; onConfirm: () => void }) {
  const [confirmation, setConfirmation] = useState("");
  const close = () => { setConfirmation(""); onClose(); };
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLElement>(open, close, busy);
  if (!open) return null;
  const confirmed = confirmation === cycleName;
  return <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) close(); }}>
    <section ref={dialogRef} className="tm-cycle-modal tm-permanent-delete-modal" role="alertdialog" aria-modal="true" aria-label="사이클 완전 삭제 확인" tabIndex={-1} onKeyDown={onDialogKeyDown}>
      <header><div><span>되돌릴 수 없는 작업</span><h2>사이클 완전 삭제</h2></div><button className="tm-modal-close" type="button" disabled={busy} aria-label="사이클 완전 삭제 닫기" onClick={close}>×</button></header>
      <div className="tm-permanent-delete-modal__body"><p>포함 테스트케이스 스냅샷, 실행 결과와 첨부파일이 제거됩니다. 연결된 이슈가 남아 있으면 삭제되지 않으며 감사 기록은 보존됩니다.</p><label><span>계속하려면 <strong>{cycleName}</strong>을(를) 입력하세요.</span><input autoFocus aria-label="완전 삭제 확인용 사이클명" value={confirmation} disabled={busy} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" /></label></div>
      <footer><button className="tm-subtle-button" type="button" disabled={busy} onClick={close}>취소</button><button className="tm-primary-button tm-permanent-delete-button" type="button" disabled={!confirmed || busy} onClick={onConfirm}>{busy ? "완전 삭제 중…" : "완전 삭제"}</button></footer>
    </section>
  </div>;
}

function InlineText({ label, value, onSave, multiline = false, prominent = false, disabled = false }: { label: string; value: string; onSave: (value: string) => void; multiline?: boolean; prominent?: boolean; disabled?: boolean }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const commit = () => { const next = draft.trim() || value; onSave(next); setDraft(next); setEditing(false); };
  const keyDown = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => { if (event.key === "Enter" && !multiline) commit(); if (event.key === "Escape") { setDraft(value); setEditing(false); } };
  if (editing && !disabled) return multiline ? <textarea className="tm-inline-edit-input tm-inline-edit-input--stable is-multiline" aria-label={label} rows={2} value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={commit} onKeyDown={keyDown} autoFocus /> : <input className={`tm-inline-edit-input tm-inline-edit-input--stable${prominent ? " is-prominent" : ""}`} aria-label={label} value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={commit} onKeyDown={keyDown} autoFocus />;
  return <span className={`tm-inline-edit-text tm-inline-edit-text--stable${prominent ? " is-prominent" : ""}${disabled ? " is-disabled" : ""}`} role="button" tabIndex={disabled ? -1 : 0} title={disabled ? "완료된 사이클은 수정할 수 없습니다" : "더블클릭하여 수정"} aria-label={`${label} 수정`} onDoubleClick={() => { if (!disabled) { setDraft(value); setEditing(true); } }} onKeyDown={(event) => { if (!disabled && event.key === "Enter") setEditing(true); }}>{value}<span className="tm-inline-edit-hint" aria-hidden="true">✎</span></span>;
}

function errorState(error: unknown, fallbackAvailable = false): PersistenceState {
  if (isCycleAvailabilityError(error)) return fallbackAvailable
    ? { phase: "fallback", message: cycleFallbackMessage, retryable: true }
    : { phase: "error", message: "Cycle API에 연결할 수 없어 사이클 상세를 표시할 수 없습니다.", retryable: true };
  if (error instanceof CycleServiceError && (error.status === 404 || error.code === "RESOURCE_NOT_FOUND")) return { phase: "not-found" };
  if (isCycleConflict(error)) return { phase: "conflict", message: error instanceof Error ? error.message : "동시 수정 충돌이 발생했습니다." };
  if (error instanceof CycleServiceError && error.code === "CYCLE_EXECUTION_LOCKED") return { phase: "error", message: "완료된 사이클의 기본 정보는 수정할 수 없습니다." };
  return { phase: "error", message: `${error instanceof Error ? error.message : "Cycle API 오류"}${fallbackAvailable ? " · 화면에는 마지막 mock snapshot을 유지합니다." : ""}` };
}

function mockItemModels(cycle: DemoCycle, state: TestManagementDemoState): CycleItemModel[] {
  return cycle.executions.map((execution) => {
    // The seeded cycle is a snapshot fixture: prefer its original case version
    // so a later library reload/edit cannot mutate an in-flight mock script.
    const testCase = initialTestManagementState.testCases.find((candidate) => candidate.key === execution.testCaseKey)
      ?? state.testCases.find((candidate) => candidate.key === execution.testCaseKey);
    return {
      id: execution.id,
      caseKey: execution.testCaseKey,
      summary: testCase?.summary ?? execution.testCaseKey,
      owner: testCase?.owner ?? "미지정",
      labels: testCase?.labels ?? [],
      precondition: testCase?.precondition ?? "",
      steps: testCase?.steps.map((step, index) => ({ sequence: index + 1, action: step.action, expectedResult: step.expectedResult, precondition: step.precondition })) ?? [],
      pinnedVersion: testCase?.version ?? 1,
      pinnedVersionId: `mock_${execution.testCaseKey}_v${testCase?.version ?? 1}`,
      latestVersion: testCase?.version ?? 1,
      latestVersionId: `mock_${execution.testCaseKey}_v${testCase?.version ?? 1}`,
      isLatestVersion: true,
      statuses: { ...execution.platformStatuses },
      attempts: {},
    };
  });
}

function apiItemModels(items: CycleItem[]): CycleItemModel[] {
  return items.map((item) => ({
    id: item.id,
    caseKey: item.caseKey,
    summary: item.pinnedVersion.summary,
    owner: item.ownerLabel ?? "미지정",
    labels: [...item.labels],
    precondition: item.pinnedVersion.precondition,
    steps: item.pinnedVersion.steps,
    pinnedVersion: item.pinnedVersion.version,
    pinnedVersionId: item.pinnedVersionId,
    latestVersion: item.latestVersion.version,
    latestVersionId: item.latestVersion.versionId,
    isLatestVersion: item.isLatestVersion,
    statuses: Object.fromEntries(platforms.map((platform) => [platform, item.platformExecutions.find((execution) => execution.platform === platform)?.status ?? "UNEXECUTED"])) as Record<Platform, ExecutionStatus>,
    attempts: Object.fromEntries(item.platformExecutions.filter((execution) => execution.latestAttempt).map((execution) => [execution.platform, execution.latestAttempt])) as Partial<Record<Platform, ExecutionAttempt>>,
    raw: item,
  }));
}

interface CycleDetailData {
  cycle: DeletedCycleResource;
  items: CycleItem[];
  itemNextCursor: string | null;
  itemTotal: number;
  progress?: CycleProgressResponse;
}
interface CycleDetailLoad {
  key: string;
  data?: CycleDetailData;
  state: PersistenceState;
}

function useCycleDetailData(cycleId: string | undefined, hasMock: boolean) {
  const service = getDefaultCycleService();
  const [load, setLoad] = useState<CycleDetailLoad>();
  const [revision, setRevision] = useState(0);
  const current = resolveKeyedCycleResource(cycleId, load, { phase: "loading" } as PersistenceState);
  useEffect(() => {
    if (!cycleId) return;
    let active = true;
    void (async () => {
      try {
        const detail = await loadCycleDetailWithPartialProgress({
          cycle: () => service.call(() => sprint4LifecycleApi.getCycle(cycleId)),
          items: () => service.call(() => sprint4LifecycleApi.listCycleItems(cycleId, { limit: 100 })),
          progress: () => service.call((api) => api.getCycleProgress(cycleId)),
        });
        const { cycle, items: itemPage, progress, partialFailure } = detail;
        if (!active) return;
        setLoad({ key: cycleId, data: { cycle, items: itemPage.items, itemNextCursor: itemPage.nextCursor, itemTotal: itemPage.total, ...(progress ? { progress } : {}) }, state: partialFailure ? { phase: "partial", message: partialFailure } : { phase: "ready" } });
      } catch (error) { if (active) setLoad((existing) => ({ key: cycleId, ...(existing?.key === cycleId && existing.data ? { data: existing.data } : {}), state: errorState(error, hasMock) })); }
    })();
    return () => { active = false; };
  }, [cycleId, hasMock, revision, service]);
  const retry = useCallback(() => {
    service.resetFallback();
    if (cycleId) setLoad((existing) => ({ key: cycleId, ...(existing?.key === cycleId && existing.data ? { data: existing.data } : {}), state: { phase: "loading" } }));
    setRevision((value) => value + 1);
  }, [cycleId, service]);
  const setData = useCallback((update: SetStateAction<CycleDetailData | undefined>) => {
    if (!cycleId) return;
    setLoad((existing) => {
      const currentData = existing?.key === cycleId ? existing.data : undefined;
      const data = typeof update === "function" ? update(currentData) : update;
      return { key: cycleId, ...(data ? { data } : {}), state: existing?.key === cycleId ? existing.state : { phase: "ready" } };
    });
  }, [cycleId]);
  const setState = useCallback((state: PersistenceState) => {
    if (!cycleId) return;
    setLoad((existing) => ({ key: cycleId, ...(existing?.key === cycleId && existing.data ? { data: existing.data } : {}), state }));
  }, [cycleId]);
  const loadMoreItems = useCallback(async () => {
    const cursor = current.data?.itemNextCursor;
    if (!cycleId || !cursor) return;
    try {
      const page = await service.call(() => sprint4LifecycleApi.listCycleItems(cycleId, { cursor, limit: 100 }));
      setData((existing) => {
        if (!existing) return existing;
        const merged = appendCursorPage(existing.items, page.items, page.total);
        return {
          ...existing,
          items: merged.items,
          itemNextCursor: merged.complete ? null : page.nextCursor,
          itemTotal: merged.total,
        };
      });
    } catch (error) {
      setState({ phase: "partial", message: `다음 실행 페이지를 불러오지 못했습니다: ${error instanceof Error ? error.message : "unknown"}` });
    }
  }, [current.data?.itemNextCursor, cycleId, service, setData, setState]);
  return { data: current.data, setData, state: current.state, setState, retry, loadMoreItems };
}

export function CycleWorkspacePage() {
  const { state: demo } = useTestManagement();
  const navigate = useNavigate();
  const service = getDefaultCycleService();
  const [viewMode, setViewMode] = useState<"ACTIVE" | "DELETED">("ACTIVE");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"ALL" | ApiCycleStatus>("ALL");
  const [resource, setResource] = useState<{ key: string; page: CyclePageResponse }>();
  const [counts, setCounts] = useState<Record<ApiCycleStatus, number>>();
  const [persistence, setPersistence] = useState<PersistenceState>(() => initialPersistenceState(service.fallbackActive));
  const [restorePendingId, setRestorePendingId] = useState<string>();
  const [restoreNotice, setRestoreNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const queryKey = `${viewMode}|${search.trim()}|${statusFilter}|${revision}`;
  const fallbackCycles = useMemo(() => demo.cycles.filter((cycle) => {
    if (statusFilter !== "ALL" && toApiStatus(cycle.status) !== statusFilter) return false;
    const needle = search.trim().toLocaleLowerCase("ko-KR");
    return !needle || [cycle.name, cycle.scopeValue, mockScopeText(cycle, demo)].some((value) => value.toLocaleLowerCase("ko-KR").includes(needle));
  }), [demo, search, statusFilter]);
  const fallbackCounts = useMemo<Record<ApiCycleStatus, number>>(() => ({
    PLANNED: demo.cycles.filter((cycle) => cycle.status === "PLANNED").length,
    IN_PROGRESS: demo.cycles.filter((cycle) => cycle.status === "IN PROGRESS").length,
    COMPLETED: demo.cycles.filter((cycle) => cycle.status === "COMPLETED").length,
  }), [demo.cycles]);
  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => { void (async () => {
      try {
        const page = await service.call((api) => api.listCycles({
          ...(search.trim() ? { search: search.trim() } : {}),
          ...(viewMode === "ACTIVE" && statusFilter !== "ALL" ? { statuses: [statusFilter] } : {}),
          ...(viewMode === "DELETED" ? { deletedOnly: true } : {}),
          sort: "UPDATED_AT_DESC",
          limit: 10,
        }));
        if (!active) return;
        setResource({ key: queryKey, page });
        if (viewMode === "ACTIVE") setCounts(page.statusCounts);
        setPersistence({ phase: "ready" });
      } catch (error) { if (active) setPersistence(errorState(error, cycleMockFallbackEnabled)); }
    })(); }, 180);
    return () => { active = false; window.clearTimeout(timer); };
  }, [queryKey, search, service, statusFilter, viewMode]);
  const page = resource?.key === queryKey ? resource.page : undefined;
  const fallbackActive = persistence.phase === "fallback";
  const displayCounts = fallbackActive ? fallbackCounts : counts;
  const fallbackVisible = fallbackActive && viewMode === "ACTIVE";
  const listLoading = !page && !fallbackVisible && persistence.phase !== "error" && persistence.phase !== "conflict" && persistence.phase !== "fallback";
  const retry = () => { service.resetFallback(); setResource(undefined); setCounts(undefined); setPersistence({ phase: "loading" }); setRevision((value) => value + 1); };
  const loadMore = async () => {
    if (!page?.nextCursor) return;
    try {
      const next = await service.call((api) => api.listCycles({
        ...(search.trim() ? { search: search.trim() } : {}),
        ...(viewMode === "ACTIVE" && statusFilter !== "ALL" ? { statuses: [statusFilter] } : {}),
        ...(viewMode === "DELETED" ? { deletedOnly: true } : {}),
        sort: "UPDATED_AT_DESC",
        cursor: page.nextCursor ?? undefined,
        limit: 10,
      }));
      setResource({ key: queryKey, page: { ...next, items: [...page.items, ...next.items] } });
    } catch (error) { setPersistence(errorState(error, cycleMockFallbackEnabled)); }
  };
  const visibleCycles = page?.items ?? (fallbackVisible ? fallbackCycles : []);
  const showEmpty = !listLoading && (page !== undefined || fallbackVisible) && visibleCycles.length === 0;
  const switchView = (next: "ACTIVE" | "DELETED") => {
    setViewMode(next);
    setStatusFilter("ALL");
    setRestoreNotice("");
  };
  const restore = async (cycle: CyclePageResponse["items"][number]) => {
    setRestorePendingId(cycle.id);
    setRestoreNotice("");
    setPersistence({ phase: "loading", message: "사이클과 실행 이력을 복구하는 중입니다." });
    try {
      await service.call((api) => api.restoreCycle(cycle.id, {
        baseResourceVersion: cycle.resourceVersion,
        reason: "Cycle Web UI에서 복구",
      }, cycleMutationOptions("cycle-restore")));
      setResource((current) => current?.key === queryKey ? {
        key: current.key,
        page: {
          ...current.page,
          items: current.page.items.filter((item) => item.id !== cycle.id),
          total: Math.max(0, current.page.total - 1),
        },
      } : current);
      setRestoreNotice(`‘${cycle.name}’ 사이클을 ${statusLabel(cycle.status)} 상태로 복구했습니다.`);
      setPersistence({ phase: "ready" });
    } catch (error) {
      setPersistence(errorState(error, false));
    } finally {
      setRestorePendingId(undefined);
    }
  };

  return <section className="tm-cycle-page tm-entity-landing" aria-labelledby="cycle-page-title">
    <header className="tm-page-header">
      <div><h1 id="cycle-page-title">Test Cycles</h1><p>테스트 케이스를 고정해 실행하고 결과를 집계하는 QA 사이클을 관리하세요.</p></div>
      <button className="tm-primary-button" type="button" onClick={() => void navigate("/cycles/new", appNavigationOptions.openCreate)}>+ 새 테스트 사이클</button>
    </header>
    <PersistenceBanner state={persistence} onRetry={persistence.phase === "ready" ? undefined : retry} />
    {restoreNotice ? <div className="tm-cycle-restore-notice" role="status">✓ {restoreNotice}</div> : null}
    <div className="tm-cycle-view-tabs" role="tablist" aria-label="사이클 목록 구분">
      <button type="button" role="tab" aria-selected={viewMode === "ACTIVE"} className={viewMode === "ACTIVE" ? "is-active" : ""} onClick={() => switchView("ACTIVE")}>활성</button>
      <button type="button" role="tab" aria-selected={viewMode === "DELETED"} className={viewMode === "DELETED" ? "is-active" : ""} onClick={() => switchView("DELETED")}>삭제됨</button>
    </div>
    {viewMode === "ACTIVE" ? <section className="tm-landing-status-cards" aria-label="Cycle status filter">
      {cycleStatusMeta.map((item) => <button className={`is-${statusClass(item.status)}${statusFilter === item.status ? " is-selected" : ""}`} type="button" key={item.status} onClick={() => setStatusFilter(statusFilter === item.status ? "ALL" : item.status)}><span>{item.label}</span><strong>{displayCounts?.[item.status] ?? "—"}</strong><small>{item.description}</small></button>)}
    </section> : <section className="tm-deleted-cycle-guide" aria-label="삭제된 사이클 안내"><div><strong>삭제된 사이클</strong><p>사이클 구성, 실행 결과와 연결된 이슈 이력을 보존합니다.</p></div><span>{page?.total ?? "—"}개</span></section>}
    <section className="tm-list-surface">
      <div className="tm-list-toolbar">
        <label className="tm-search"><span aria-hidden="true">⌕</span><input aria-label="Search test cycles" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={viewMode === "DELETED" ? "삭제된 사이클 검색" : "Cycle name, parent issue, label"} /></label>
        {viewMode === "ACTIVE" ? <button className={`tm-filter-chip${statusFilter === "ALL" ? " is-selected" : ""}`} type="button" onClick={() => setStatusFilter("ALL")}>All {displayCounts ? Object.values(displayCounts).reduce((sum, count) => sum + count, 0) : "—"}</button> : null}
      </div>
      <div className={`tm-cycle-list-table${viewMode === "DELETED" ? " is-deleted" : ""}`} role="table" aria-label={viewMode === "DELETED" ? "Deleted test cycle list" : "Test cycle list"}>
        {viewMode === "DELETED" ? <div className="tm-cycle-list-table__header" role="row"><span>Cycle</span><span>Scope</span><span>Cases</span><span>Previous status</span><span>Deleted</span><span>Actions</span></div> : <div className="tm-cycle-list-table__header" role="row"><span>Cycle</span><span>Scope</span><span>Cases</span><span>Progress</span><span>Start Date</span><span>Status</span></div>}
        {listLoading ? <CycleListLoadingRows /> : visibleCycles.map((cycle) => {
          if ("itemCount" in cycle) {
            if (viewMode === "DELETED") return <div className="tm-cycle-list-table__row tm-deleted-cycle-row" role="row" key={cycle.id}>
              <span><Link className="tm-deleted-entity-link" to={`/cycles/${cycle.id}`}><strong>{cycle.name}</strong><small>ledger v{cycle.resourceVersion} · 읽기 전용 상세</small></Link></span>
              <span>{scopeText(cycle.scope)}</span>
              <span>{cycle.itemCount}</span>
              <span className="tm-cycle-state-cell"><span className={`tm-cycle-state-badge is-${statusClass(cycle.status)}`}>{statusLabel(cycle.status)}</span></span>
              <span>{cycle.deletedAt ? new Date(cycle.deletedAt).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }) : "-"}</span>
              <span className="tm-deleted-row-actions"><Link className="tm-subtle-button tm-compact-button" to={`/cycles/${cycle.id}`}>상세</Link><button className="tm-subtle-button tm-compact-button tm-cycle-restore-button" type="button" disabled={Boolean(restorePendingId)} onClick={() => void restore(cycle)}>{restorePendingId === cycle.id ? "복구 중…" : "복구"}</button></span>
            </div>;
            const total = cycle.itemCount * platforms.length;
            return <Link className="tm-cycle-list-table__row" role="row" to={`/cycles/${cycle.id}`} key={cycle.id}><span><strong>{cycle.name}</strong><small>ledger v{cycle.resourceVersion} · {new Date(cycle.updatedAt).toLocaleDateString("ko-KR")}</small></span><span>{scopeText(cycle.scope)}</span><span>{cycle.itemCount}</span><span><progress className="tm-cycle-completion-progress" aria-label={`저장된 플랫폼 결과 ${cycle.platformResultCount}/${total}`} max={total || 1} value={cycle.platformResultCount} /><small>Latest results {cycle.platformResultCount} / {total}</small></span><span>{cycle.startDate}</span><span className="tm-cycle-state-cell"><span className={`tm-cycle-state-badge is-${statusClass(cycle.status)}`}>{statusLabel(cycle.status)}</span></span></Link>;
          }
          const itemCounts = getExecutionCounts(cycle.executions);
          return <Link className="tm-cycle-list-table__row" role="row" to={`/cycles/${cycle.id}`} key={cycle.id}><span><strong>{cycle.name}</strong><small>{cycle.description}</small></span><span>{mockScopeText(cycle, demo)}</span><span>{cycle.executions.length}</span><span><ProgressBar counts={itemCounts} /><small>PASS {itemCounts.PASS} · FAIL {itemCounts.FAIL} · BLOCKED {itemCounts.BLOCKED}</small></span><span>{cycle.startDate}</span><span className="tm-cycle-state-cell"><span className={`tm-cycle-state-badge is-${statusClass(toApiStatus(cycle.status))}`}>{statusLabel(toApiStatus(cycle.status))}</span></span></Link>;
        })}
        {showEmpty ? <div className="tm-empty-state"><span>⌕</span><strong>{viewMode === "DELETED" ? "삭제된 사이클이 없습니다." : "조건에 맞는 사이클이 없습니다."}</strong><p>{viewMode === "DELETED" ? "삭제한 사이클이 이곳에 표시됩니다." : "검색어나 상태 필터를 변경해 보세요."}</p></div> : null}
      </div>
      {page?.nextCursor ? <button className="tm-pagination-button" type="button" onClick={() => void loadMore()}>10개 더 보기</button> : null}
    </section>
  </section>;
}

function ScopePickerModal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="tm-cycle-modal" role="dialog" aria-modal="true" aria-labelledby="cycle-scope-modal-title"><header><h2 id="cycle-scope-modal-title">{title}</h2><button className="tm-modal-close" type="button" onClick={onClose} aria-label={`${title} 닫기`}>×</button></header>{children}</section></div>;
}

export function CycleCreatePage() {
  const { state: demo, createCycle } = useTestManagement();
  const service = getDefaultCycleService();
  const navigate = useNavigate();
  const fallbackLabels = useMemo(() => [...new Set(demo.testCases.flatMap((testCase) => testCase.labels))].sort(), [demo.testCases]);
  const fallbackCatalog = useMemo<{ workItems: ScopeOptionModel[]; labels: ScopeOptionModel[] }>(() => cycleMockFallbackEnabled ? ({
    workItems: demo.workItems.map<ScopeOptionModel>((workItem) => ({
      key: workItem.key,
      title: `${workItem.key} · ${workItem.summary}`,
      detail: workItem.owner,
      caseCount: demo.testCases.filter((testCase) => getWorkItemKey(testCase.sourceWorkItem) === workItem.key).length,
      categoryId: workItem.domainId,
      categoryName: demo.domains.find((domain) => domain.id === workItem.domainId)?.name ?? "기타",
      scope: { type: "WORK_ITEM", workItemId: workItem.id ?? `mock_${workItem.key}`, workItemKey: workItem.key },
    })),
    labels: fallbackLabels.map<ScopeOptionModel>((label) => ({
      key: label,
      title: `#${label}`,
      detail: "테스트케이스 라벨",
      caseCount: demo.testCases.filter((testCase) => testCase.labels.includes(label)).length,
      scope: { type: "LABEL", label },
    })),
  }) : { workItems: [], labels: [] }, [demo, fallbackLabels]);
  const [scopeType, setScopeType] = useState<CycleScopeType>("WORK_ITEM");
  const [selectedWorkItemKey, setSelectedWorkItemKey] = useState(service.fallbackActive ? demo.workItems[0]?.key ?? "" : "");
  const [selectedLabels, setSelectedLabels] = useState<string[]>([]);
  const [name, setName] = useState("새 테스트 사이클");
  const [description, setDescription] = useState("");
  const [startDate, setStartDate] = useState(new Date().toISOString().slice(0, 10));
  const [apiCatalog, setApiCatalog] = useState<typeof fallbackCatalog>();
  const [previewResource, setPreviewResource] = useState<{ key: string; preview: ScopePreviewResponse }>();
  const [labelPreviews, setLabelPreviews] = useState<Record<string, ScopePreviewResponse>>({});
  const [workItemModalOpen, setWorkItemModalOpen] = useState(false);
  const [labelModalOpen, setLabelModalOpen] = useState(false);
  const [pickerSearch, setPickerSearch] = useState("");
  const [expandedLabel, setExpandedLabel] = useState<string>();
  const [persistence, setPersistence] = useState<PersistenceState>(() => initialPersistenceState(service.fallbackActive));
  const [submitting, setSubmitting] = useState(false);
  const [revision, setRevision] = useState(0);
  const fallbackActive = persistence.phase === "fallback";
  const catalog = apiCatalog ?? (fallbackActive ? fallbackCatalog : { workItems: [], labels: [] });
  const selectedWorkItem = catalog.workItems.find((option) => option.key === selectedWorkItemKey) ?? catalog.workItems[0];
  const selectedScope = useMemo<CycleResource["scope"] | undefined>(() => {
    if (scopeType === "WORK_ITEM") return selectedWorkItem?.scope;
    const first = selectedLabels[0];
    return first ? { type: "LABEL", label: first, labels: selectedLabels } : undefined;
  }, [scopeType, selectedLabels, selectedWorkItem]);
  const selectedScopeKey = selectedScope?.type === "WORK_ITEM"
    ? `WORK_ITEM:${selectedScope.workItemId}`
    : selectedScope ? `LABEL:${(selectedScope.labels ?? [selectedScope.label]).join(",")}` : "";
  const preview = previewResource?.key === selectedScopeKey ? previewResource.preview : undefined;
  const fallbackPreview = useMemo(() => demo.testCases.filter((testCase) => {
    if (!selectedScope) return false;
    if (selectedScope.type === "WORK_ITEM") return getWorkItemKey(testCase.sourceWorkItem) === selectedScope.workItemKey;
    return (selectedScope.labels ?? [selectedScope.label]).some((label) => testCase.labels.includes(label));
  }), [demo.testCases, selectedScope]);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const [workItemPage, labelPage, folderTree, casePage] = await Promise.all([
          service.call((api) => api.listCycleScopeOptions({ type: "WORK_ITEM", limit: 200 })),
          service.call((api) => api.listCycleScopeOptions({ type: "LABEL", limit: 200 })),
          service.call((api) => api.getFolderTree()),
          service.call((api) => api.listTestCases({ limit: 200 })),
        ]);
        if (!active) return;
        const categoryByWorkItemId = new Map(casePage.items.map((item) => [item.workItemId, item.categoryId]));
        const categoryNames = new Map(folderTree.items.map((item) => [item.id, item.name]));
        const workItems = workItemPage.items.flatMap<ScopeOptionModel>((option) => {
          if (option.type !== "WORK_ITEM") return [];
          const categoryId = categoryByWorkItemId.get(option.workItemId);
          return [{
            key: option.workItemKey,
            title: `${option.workItemKey} · ${option.summary}`,
            detail: option.ownerLabel ?? "미지정",
            caseCount: option.caseCount,
            ...(categoryId ? { categoryId, categoryName: categoryNames.get(categoryId) ?? "기타" } : { categoryName: "기타" }),
            scope: { type: "WORK_ITEM", workItemId: option.workItemId, workItemKey: option.workItemKey },
          }];
        });
        const labels = labelPage.items.flatMap<ScopeOptionModel>((option) => option.type === "LABEL" ? [{
          key: option.label,
          title: `#${option.label}`,
          detail: "테스트케이스 라벨",
          caseCount: option.caseCount,
          scope: { type: "LABEL", label: option.label },
        }] : []);
        setApiCatalog({ workItems, labels });
        setSelectedWorkItemKey((current) => workItems.some((option) => option.key === current) ? current : workItems[0]?.key ?? "");
        setPersistence({ phase: "ready" });
      } catch (error) {
        if (active) {
          setApiCatalog(undefined);
          setSelectedWorkItemKey((current) => fallbackCatalog.workItems.some((option) => option.key === current) ? current : fallbackCatalog.workItems[0]?.key ?? "");
          setPersistence(errorState(error, cycleMockFallbackEnabled));
        }
      }
    })();
    return () => { active = false; };
  }, [fallbackCatalog, revision, service]);

  useEffect(() => {
    if (!selectedScope || !apiCatalog) {
      setPreviewResource(undefined);
      return;
    }
    let active = true;
    const query = selectedScope.type === "WORK_ITEM"
      ? { scopeType: "WORK_ITEM" as const, workItemId: selectedScope.workItemId, limit: 200 }
      : { scopeType: "LABEL" as const, label: selectedScope.label, labels: selectedScope.labels, limit: 200 };
    void service.call((api) => api.previewCycleScope(query)).then((response) => {
      if (active) setPreviewResource({ key: selectedScopeKey, preview: response });
    }).catch((error: unknown) => {
      if (active) setPersistence(errorState(error, cycleMockFallbackEnabled));
    });
    return () => { active = false; };
  }, [apiCatalog, selectedScope, selectedScopeKey, service]);

  const retry = () => {
    service.resetFallback();
    setApiCatalog(undefined);
    setPreviewResource(undefined);
    setSelectedWorkItemKey("");
    setPersistence({ phase: "loading" });
    setRevision((value) => value + 1);
  };
  const toggleLabel = (label: string) => {
    setSelectedLabels((current) => current.includes(label) ? current.filter((item) => item !== label) : [...current, label]);
  };
  const toggleLabelCases = (label: string) => {
    if (expandedLabel === label) {
      setExpandedLabel(undefined);
      return;
    }
    setExpandedLabel(label);
    if (!apiCatalog || labelPreviews[label]) return;
    void service.call((api) => api.previewCycleScope({ scopeType: "LABEL", label, labels: [label], limit: 200 })).then((response) => {
      setLabelPreviews((current) => ({ ...current, [label]: response }));
    }).catch((error: unknown) => setPersistence(errorState(error, cycleMockFallbackEnabled)));
  };
  const submit = async () => {
    if (!selectedScope) return;
    const scopeName = selectedScope.type === "WORK_ITEM" ? selectedScope.workItemKey : (selectedScope.labels ?? [selectedScope.label]).map((label) => `#${label}`).join(", ");
    setSubmitting(true);
    try {
      const cycle = await service.call((api) => api.createCycle({
        name: name.trim() || "새 테스트 사이클",
        description: description.trim() || `${scopeName} 범위 QA`,
        startDate,
        scope: selectedScope,
      }, cycleMutationOptions("cycle-create")));
      void navigate(`/cycles/${cycle.id}`, appNavigationOptions.createSuccess);
    } catch (error) {
      if (isCycleAvailabilityError(error) && cycleMockFallbackEnabled) {
        const id = createCycle({
          name: name.trim() || "새 테스트 사이클",
          description: description.trim() || `${scopeName} 범위 QA`,
          scopeType,
          scopeValue: selectedScope.type === "WORK_ITEM" ? selectedScope.workItemKey : selectedScope.label,
        });
        void navigate(`/cycles/${id}`, appNavigationOptions.createSuccess);
      } else setPersistence(errorState(error));
    } finally {
      setSubmitting(false);
    }
  };
  const normalizedSearch = pickerSearch.trim().toLocaleLowerCase("ko-KR");
  const visibleWorkItems = catalog.workItems.filter((option) => !normalizedSearch || [option.title, option.detail, option.categoryName ?? ""].some((value) => value.toLocaleLowerCase("ko-KR").includes(normalizedSearch)));
  const workItemsByCategory = [...visibleWorkItems.reduce((groups, option) => {
    const category = option.categoryName ?? "기타";
    groups.set(category, [...(groups.get(category) ?? []), option]);
    return groups;
  }, new Map<string, ScopeOptionModel[]>())];
  const visibleLabels = catalog.labels.filter((option) => !normalizedSearch || option.key.toLocaleLowerCase("ko-KR").includes(normalizedSearch));
  const selectedScopeName = scopeType === "WORK_ITEM"
    ? selectedWorkItem?.title ?? "선택되지 않음"
    : selectedScope ? scopeText(selectedScope) : "선택되지 않음";
  const previewItems = fallbackActive ? undefined : preview?.items;
  const previewTotal = fallbackActive ? fallbackPreview.length : preview?.total;

  if (persistence.phase === "loading" && !apiCatalog) return <CycleLoadingSurface variant="create" />;

  return <section className="tm-cycle-page tm-create-page" aria-labelledby="cycle-create-title">
    <nav className="tm-detail-breadcrumb"><Link className="tm-back-button" replace={appNavigationOptions.cancelCreate.replace} to="/cycles">← 사이클 목록</Link><span>/</span><strong>새 사이클</strong></nav>
    <header className="tm-page-header"><div><h1 id="cycle-create-title">새 테스트 사이클</h1><p>실행 대상은 생성할 때 확정하며, 라벨 범위는 여러 라벨의 케이스 합집합으로 구성됩니다.</p></div><div className="tm-detail-actions"><Link className="tm-subtle-button" replace={appNavigationOptions.cancelCreate.replace} to="/cycles">취소</Link><button className="tm-primary-button" type="button" onClick={() => void submit()} disabled={!selectedScope || previewTotal === undefined || previewTotal === 0 || submitting}>{submitting ? "생성 중…" : "사이클 만들기"}</button></div></header>
    <PersistenceBanner state={persistence} onRetry={persistence.phase === "ready" ? undefined : retry} />
    <div className="tm-create-layout"><main className="tm-create-card">
      <section><span className="tm-step-kicker">1</span><div><h2>기본 정보</h2><p>사이클 목록에서 구분하기 쉬운 이름을 입력하세요.</p></div></section>
      <label className="tm-field tm-field--large"><span>사이클명</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label className="tm-field tm-field--large"><span>시작일</span><input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
      <label className="tm-field tm-field--large"><span>설명</span><textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="검증 목적과 범위를 간단히 기록" /></label>
      <section><span className="tm-step-kicker">2</span><div><h2>실행 대상 설정</h2><p>상위 일감 하나 또는 라벨 여러 개를 선택합니다.</p></div></section>
      <div className="tm-scope-type-tabs"><button className={scopeType === "WORK_ITEM" ? "is-selected" : ""} type="button" onClick={() => setScopeType("WORK_ITEM")}>▣ 상위 일감</button><button className={scopeType === "LABEL" ? "is-selected" : ""} type="button" onClick={() => setScopeType("LABEL")}># 라벨</button></div>
      <div className="tm-cycle-scope-selection"><div><span>선택 대상</span><strong>{selectedScopeName}</strong><small>{scopeType === "WORK_ITEM" ? selectedWorkItem?.categoryName ?? "기타" : `${selectedLabels.length}개 라벨 · 중복 케이스 제거`}</small></div><button className="tm-subtle-button tm-compact-button" type="button" onClick={() => { setPickerSearch(""); if (scopeType === "WORK_ITEM") setWorkItemModalOpen(true); else setLabelModalOpen(true); }}>{scopeType === "WORK_ITEM" ? "상위 일감 선택" : "라벨 복수 선택"}</button></div>
    </main><aside className="tm-create-summary tm-cycle-preview-summary"><span>즉시 case preview</span><strong>{previewTotal ?? "—"}</strong><p>개의 테스트케이스가 생성 시 pinned snapshot으로 고정됩니다.</p><div className="tm-preview-case-list">{previewItems ? previewItems.map((item) => <article key={item.testCaseId}><b>{item.caseKey}</b><span>{item.summary}</span><small>v{item.currentVersion} · {item.ownerLabel ?? "미지정"}</small></article>) : fallbackActive ? fallbackPreview.map((item) => <article key={item.key}><b>{item.key}</b><span>{item.summary}</span><small>v{item.version} · {item.owner}</small></article>) : <CycleListLoadingRows />}</div><dl><div><dt>범위 형식</dt><dd>{scopeType === "WORK_ITEM" ? "상위 일감" : "라벨 합집합"}</dd></div><div><dt>선택 대상</dt><dd>{selectedScopeName}</dd></div><div><dt>초기 상태</dt><dd>미진행</dd></div></dl></aside></div>
    {workItemModalOpen ? <ScopePickerModal title="상위 일감 선택" onClose={() => setWorkItemModalOpen(false)}><label className="tm-search tm-cycle-modal-search"><span aria-hidden="true">⌕</span><input autoFocus aria-label="상위 일감 검색" value={pickerSearch} onChange={(event) => setPickerSearch(event.target.value)} placeholder="카테고리, 일감 키 또는 제목 검색" /></label><div className="tm-cycle-category-options">{workItemsByCategory.map(([category, options]) => <section key={category}><header><strong>{category}</strong><span>{options.length}</span></header><div className="tm-scope-selection-list" role="listbox" aria-label={`${category} 상위 일감`}>{options.map((option) => <button className={selectedWorkItem?.key === option.key ? "is-selected" : ""} type="button" role="option" aria-selected={selectedWorkItem?.key === option.key} key={option.key} onClick={() => { setSelectedWorkItemKey(option.key); setWorkItemModalOpen(false); }}><span className="tm-work-item-icon">▣</span><span><strong>{option.title}</strong><small>{option.detail} · {option.caseCount} cases</small></span><i>{selectedWorkItem?.key === option.key ? "✓" : ""}</i></button>)}</div></section>)}</div></ScopePickerModal> : null}
    {labelModalOpen ? <ScopePickerModal title="라벨 복수 선택" onClose={() => setLabelModalOpen(false)}><label className="tm-search tm-cycle-modal-search"><span aria-hidden="true">⌕</span><input autoFocus aria-label="라벨 검색" value={pickerSearch} onChange={(event) => setPickerSearch(event.target.value)} placeholder="라벨 검색" /></label><p className="tm-cycle-modal-help">라벨별 포함 케이스를 펼쳐 확인할 수 있습니다. 여러 라벨에 속한 케이스는 한 번만 포함됩니다.</p><div className="tm-label-scope-options">{visibleLabels.map((option) => { const expanded = expandedLabel === option.key; const fallbackCases = demo.testCases.filter((testCase) => testCase.labels.includes(option.key)); const labelCases = labelPreviews[option.key]?.items; return <article key={option.key}><div><label><input type="checkbox" checked={selectedLabels.includes(option.key)} onChange={() => toggleLabel(option.key)} /><span className="tm-label-mark">#</span><strong>{option.key}</strong><small>{option.caseCount} cases</small></label><button className="tm-subtle-button tm-compact-button" type="button" aria-expanded={expanded} onClick={() => toggleLabelCases(option.key)}>{expanded ? "케이스 접기" : "포함 케이스 보기"}</button></div>{expanded ? <div className="tm-label-case-list">{labelCases ? labelCases.map((item) => <span key={item.testCaseId}><b>{item.caseKey}</b>{item.summary}</span>) : fallbackCases.map((item) => <span key={item.key}><b>{item.key}</b>{item.summary}</span>)}</div> : null}</article>; })}</div><footer><span>{selectedLabels.length}개 라벨 선택</span><button className="tm-primary-button" type="button" disabled={!selectedLabels.length} onClick={() => setLabelModalOpen(false)}>선택 완료</button></footer></ScopePickerModal> : null}
  </section>;
}

function ProgressDimensions({ progress, fallbackCounts, planned = false }: { progress?: CycleProgressResponse; fallbackCounts: Record<ExecutionStatus, number>; planned?: boolean }) {
  if (planned) return <section className="tm-progress-dimensions"><header><h2>진행률</h2><small>PLANNED · 집계 제외</small></header><p>시작 전 실행 결과는 진행률 분모에 포함하지 않습니다.</p></section>;
  if (!progress) return <section className="tm-progress-dimensions"><header><h2>진행률</h2><small>현재 snapshot · 전체</small></header><ProgressBar counts={fallbackCounts} /></section>;
  const groups = [{ label: "전체", items: [progress.overall] }, { label: "플랫폼", items: progress.byPlatform }, { label: "담당자", items: progress.byOwner }, { label: "상위 일감", items: progress.byWorkItem }, { label: "라벨", items: progress.byLabel }];
  return <section className="tm-progress-dimensions"><header><h2>최신 attempt 진행률</h2><small>{new Date(progress.calculatedAt).toLocaleString("ko-KR")} · PLANNED 제외</small></header>{groups.map((group) => <details key={group.label} open={group.label === "전체" || group.label === "플랫폼"}><summary>{group.label}<span>{group.items.length}</span></summary><div>{group.items.map((item) => <article key={`${item.dimension}-${item.key}`}><strong>{item.label}</strong><ProgressBar counts={item.values.counts} /><small>실행 {item.values.executedCount}/{item.values.counts.total} · PASS {Math.round(item.values.passRate * 100)}%</small></article>)}</div></details>)}</section>;
}

export function CycleDetailPage() {
  const { cycleId } = useParams();
  const { state: demo, saveCycle, deleteCycle } = useTestManagement();
  const navigate = useNavigate();
  const service = getDefaultCycleService();
  const mock = cycleMockFallbackEnabled
    ? demo.cycles.find((candidate) => candidate.id === cycleId)
    : undefined;
  const resource = useCycleDetailData(cycleId, Boolean(mock));
  const [deletePending, setDeletePending] = useState(false);
  const [permanentDeletePending, setPermanentDeletePending] = useState(false);
  const [mutationState, setMutationState] = useState<PersistenceState>({ phase: "ready" });
  const [incompletePending, setIncompletePending] = useState(false);
  const fallbackMock = resource.state.phase === "fallback" ? mock : undefined;
  const cycle = resource.data?.cycle;
  const isDeleted = Boolean(cycle?.deletedAt);
  const status = cycle?.status ?? (fallbackMock ? toApiStatus(fallbackMock.status) : undefined);
  const items = resource.data ? apiItemModels(resource.data.items) : fallbackMock ? mockItemModels(fallbackMock, demo) : [];
  const counts = resource.data?.progress?.overall.values.counts ?? getItemCounts(items);
  const effectiveState = mutationState.phase === "ready" ? resource.state : mutationState;
  const applyMutation = async (apiAction: () => Promise<CycleResource>, fallbackAction?: () => void) => {
    setMutationState({ phase: "loading", message: "원장에 변경을 저장하는 중입니다." });
    try { const next = await apiAction(); resource.setData((current) => current ? { ...current, cycle: next } : current); setMutationState({ phase: "ready" }); return true; }
    catch (error) { if (isCycleAvailabilityError(error) && fallbackAction) fallbackAction(); setMutationState(errorState(error, Boolean(mock))); return false; }
  };
  const updateField = (field: "name" | "description", value: string) => {
    if (cycle) void applyMutation(() => service.call((api) => api.updateCycle(cycle.id, { baseResourceVersion: cycle.resourceVersion, [field]: value }, cycleMutationOptions(`cycle-update-${field}`))), mock ? () => saveCycle({ ...mock, [field]: value }) : undefined);
    else if (mock) saveCycle({ ...mock, [field]: value });
  };
  const start = async () => {
    if (!cycle) {
      if (mock) {
        saveCycle({ ...mock, status: "IN PROGRESS" });
        void navigate(`/cycles/${cycleId}/execute`, appNavigationOptions.openExecution);
      }
      return;
    }
    setMutationState({ phase: "loading", message: "사이클을 시작하고 실행 범위를 잠그는 중입니다." });
    try {
      const response = await service.call((api) => api.startCycle(cycle.id, {
        baseResourceVersion: cycle.resourceVersion,
        actorLabel: "Cycle Web UI",
      }, cycleMutationOptions("cycle-start")));
      resource.setData((current) => current ? { ...current, cycle: response.cycle, items: response.items, itemNextCursor: null, itemTotal: response.items.length } : current);
      setMutationState({ phase: "ready" });
      void navigate(`/cycles/${cycleId}/execute`, appNavigationOptions.openExecution);
    } catch (error) {
      setMutationState(errorState(error, Boolean(mock)));
    }
  };
  const complete = async (acknowledgeIncomplete: boolean) => {
    if (!cycle) { if (mock) saveCycle({ ...mock, status: "COMPLETED" }); return; }
    setMutationState({ phase: "loading", message: "최신 attempt를 확인하고 완료 처리 중입니다." });
    try { const response = await service.call((api) => api.completeCycle(cycle.id, { baseResourceVersion: cycle.resourceVersion, acknowledgeIncomplete, actorLabel: "Cycle Web UI" }, cycleMutationOptions("cycle-complete"))); resource.setData((current) => current ? { ...current, cycle: response.cycle } : current); setIncompletePending(false); setMutationState(response.warnings.length ? { phase: "partial", message: `완료됨 · 미실행 경고 ${response.warnings.map((warning) => `${warning.unexecutedCount} UNEXECUTED`).join(", ")}` } : { phase: "ready" }); }
    catch (error) { if (error instanceof CycleServiceError && error.code === "CYCLE_INCOMPLETE_WARNING") { setIncompletePending(true); setMutationState({ phase: "partial", message: `${error.message} 확인 후 미실행 포함 완료를 선택하세요.` }); } else { if (isCycleAvailabilityError(error) && mock) saveCycle({ ...mock, status: "COMPLETED" }); setMutationState(errorState(error, Boolean(mock))); } }
  };
  const reopen = async () => {
    if (!cycle) { if (mock) saveCycle({ ...mock, status: "IN PROGRESS" }); return; }
    await applyMutation(() => service.call((api) => api.reopenCycle(cycle.id, { baseResourceVersion: cycle.resourceVersion, reason: "Cycle Web UI에서 실행 재개", actorLabel: "Cycle Web UI" }, cycleMutationOptions("cycle-reopen"))).then((response) => response.cycle), mock ? () => saveCycle({ ...mock, status: "IN PROGRESS" }) : undefined);
  };
  const remove = async () => {
    if (!cycle) { if (mock) deleteCycle(mock.id); void navigate("/cycles", appNavigationOptions.deleteSuccess); return; }
    setMutationState({ phase: "loading", message: "사이클을 삭제하는 중입니다." });
    try { await service.call((api) => api.deleteCycle(cycle.id, { baseResourceVersion: cycle.resourceVersion, reason: "Cycle Web UI에서 삭제" }, cycleMutationOptions("cycle-delete"))); void navigate("/cycles", appNavigationOptions.deleteSuccess); }
    catch (error) { if (isCycleAvailabilityError(error) && mock) { deleteCycle(mock.id); void navigate("/cycles", appNavigationOptions.deleteSuccess); } else setMutationState(errorState(error, Boolean(mock))); }
  };
  const restoreDeleted = async () => {
    if (!cycle || !isDeleted) return;
    setMutationState({ phase: "loading", message: "사이클과 실행 이력을 복구하는 중입니다." });
    try {
      await service.call((api) => api.restoreCycle(cycle.id, { baseResourceVersion: cycle.resourceVersion, reason: "Cycle deleted detail restore" }, cycleMutationOptions("cycle-detail-restore")));
      void navigate("/cycles", appNavigationOptions.returnToList);
    } catch (error) {
      setMutationState(errorState(error, false));
    }
  };
  const permanentlyDelete = async () => {
    if (!cycle || !isDeleted) return;
    setMutationState({ phase: "loading", message: "사이클을 완전 삭제하는 중입니다." });
    try {
      await sprint4LifecycleApi.permanentlyDeleteCycle(cycle.id, {
        baseResourceVersion: cycle.resourceVersion,
        reason: "Cycle deleted detail permanent delete",
      }, { idempotencyKey: mutationKey("cycle-permanent-delete"), actorLabel: "Cycle Web UI" });
      void navigate("/cycles", appNavigationOptions.deleteSuccess);
    } catch (error) {
      setPermanentDeletePending(false);
      setMutationState(errorState(error, false));
    }
  };
  if (resource.state.phase === "loading" && !resource.data) return <CycleLoadingSurface variant="detail" />;
  if (!status && resource.state.phase === "not-found") return <section className="tm-not-found"><h1>사이클을 찾을 수 없습니다.</h1><Link className="tm-primary-button" replace={appNavigationOptions.returnToList.replace} to="/cycles">사이클 목록</Link></section>;
  if (!status) return <section className="tm-cycle-page tm-not-found"><h1>사이클 상세를 표시할 수 없습니다.</h1><PersistenceBanner state={resource.state} onRetry={resource.retry} /><Link className="tm-primary-button" replace={appNavigationOptions.returnToList.replace} to="/cycles">사이클 목록</Link></section>;
  const name = cycle?.name ?? fallbackMock?.name ?? "사이클";
  const description = cycle?.description ?? fallbackMock?.description ?? "";
  const scope = cycle?.scope ?? (fallbackMock ? mockScope(fallbackMock, demo) : { type: "LABEL" as const, label: "-" });
  const scopeLabel = cycle ? scopeText(scope) : fallbackMock ? mockScopeText(fallbackMock, demo) : "-";
  return <section className={`tm-cycle-page tm-cycle-detail-page${isDeleted ? " is-readonly-deleted" : ""}`} aria-labelledby="cycle-detail-title">
    <DeleteConfirmDialog open={deletePending} title="사이클 삭제" entityName={name} description="사이클은 목록과 대시보드에서 숨겨집니다. 실행 결과와 연결된 이슈 이력은 보존됩니다." busy={mutationState.phase === "loading"} onClose={() => setDeletePending(false)} onConfirm={() => void remove()} />
    <PermanentCycleDeleteDialog open={permanentDeletePending} cycleName={name} busy={mutationState.phase === "loading"} onClose={() => setPermanentDeletePending(false)} onConfirm={() => void permanentlyDelete()} />
    <nav className="tm-detail-breadcrumb"><Link className="tm-back-button" replace={appNavigationOptions.returnToList.replace} to="/cycles">← 사이클 목록</Link><span>/</span><strong>{name}</strong></nav>
    <PersistenceBanner state={effectiveState} onRetry={effectiveState.phase === "ready" ? undefined : resource.retry} />
    {isDeleted ? <div className="tm-deleted-readonly-banner" role="status"><span>삭제됨</span><div><strong>이 사이클은 읽기 전용입니다.</strong><p>포함 테스트케이스와 저장된 실행 결과를 확인한 뒤 복구하거나 완전 삭제할 수 있습니다.</p></div></div> : null}
    <header className="tm-cycle-detail-hero"><div><span className="tm-cycle-state-cell"><span className={`tm-cycle-state-badge is-${statusClass(status)}`}>{statusLabel(status)}</span>{isDeleted ? <span className="tm-deleted-state-badge">삭제됨</span> : null}</span><InlineText prominent label="사이클명" value={name} onSave={(value) => updateField("name", value)} disabled={status === "COMPLETED" || isDeleted} /><p>{scopeLabel} · {isDeleted ? `삭제 ${cycle?.deletedAt ? new Date(cycle.deletedAt).toLocaleString("ko-KR") : "시각 미상"}` : cycle?.scopeLocked ? "범위 잠김" : "사이클 시작 시 범위 잠김"}</p></div><div className="tm-cycle-header-actions">{isDeleted ? <><button className="tm-subtle-button" type="button" disabled={mutationState.phase === "loading"} onClick={() => void restoreDeleted()}>복구</button><button className="tm-subtle-button tm-danger-button" type="button" disabled={mutationState.phase === "loading"} onClick={() => setPermanentDeletePending(true)}>완전 삭제</button></> : <><button className="tm-subtle-button tm-danger-button" type="button" disabled={mutationState.phase === "loading"} onClick={() => setDeletePending(true)}>삭제</button>{status === "PLANNED" ? <button className="tm-primary-button" type="button" disabled={mutationState.phase === "loading"} onClick={() => void start()}>사이클 시작</button> : null}{status === "IN_PROGRESS" ? <button className="tm-primary-button" type="button" onClick={() => void complete(incompletePending)}>{incompletePending ? "미실행 포함 완료" : "사이클 완료"}</button> : null}{status === "COMPLETED" ? <button className="tm-subtle-button" type="button" onClick={() => void reopen()}>다시 열기</button> : null}{status !== "PLANNED" ? <Link className="tm-primary-button" to={`/cycles/${cycleId}/execute`}>{status === "COMPLETED" ? "결과 보기" : "테스트 실행"} →</Link> : null}</>}</div></header>
    <section className="tm-cycle-detail-overview"><article><span>실행 대상</span><strong>{scopeLabel}</strong><small>{status === "PLANNED" ? "생성 시 확정" : "사이클 시작 시 범위 잠김"}</small></article><article><span>테스트케이스</span><strong>{resource.data?.itemTotal ?? items.length}</strong><small>pinned version</small></article><article><span>플랫폼 결과</span><strong>{(resource.data?.itemTotal ?? items.length) * 3}</strong><small>WEB · AOS · iOS</small></article><article className={counts.FAIL + counts.BLOCKED ? "is-alert" : ""}><span>위험 결과</span><strong>{counts.FAIL + counts.BLOCKED}</strong><small>FAIL {counts.FAIL} · BLOCKED {counts.BLOCKED}</small></article></section>
    <section className="tm-cycle-detail-body"><main className="tm-list-surface"><header><div><h2>포함된 테스트케이스</h2><p>{isDeleted ? "삭제 시점에 보존된 pinned 테스트케이스입니다." : "케이스를 클릭하면 pinned 원본의 라이브러리 상세로 이동합니다."}</p></div>{!isDeleted && status !== "PLANNED" ? <div className="tm-snapshot-actions"><Link className="tm-utility-link tm-execution-open-link" to={`/cycles/${cycleId}/execute`}>{status === "COMPLETED" ? "결과 화면 열기" : "실행 화면 열기"} →</Link></div> : null}</header><ProgressBar counts={counts} /><div className="tm-cycle-case-preview">{items.map((item, index) => { const aggregate = aggregateStatuses(item.statuses); return <article className="tm-pinned-cycle-item" key={item.id}><span>{index + 1}</span><span className="tm-case-type-icon">T</span><div><Link className="tm-cycle-case-link" to={`/test-cases/${item.caseKey}`}>{item.summary}</Link><small>{item.caseKey} · {item.owner} · pinned v{item.pinnedVersion}{!item.isLatestVersion ? ` / latest v${item.latestVersion}` : " · 최신"}</small></div><span className={`tm-execution-aggregate is-${aggregate.toLowerCase()}`}>{aggregate}</span></article>; })}</div>{resource.data?.itemNextCursor ? <button className="tm-pagination-button" type="button" onClick={() => void resource.loadMoreItems()}>다음 100개 불러오기 · {items.length}/{resource.data.itemTotal}</button> : null}</main><aside className="tm-cycle-detail-info"><h2>사이클 정보</h2><dl><div><dt>상태</dt><dd>{statusLabel(status)}</dd></div><div><dt>실행 대상</dt><dd>{scopeLabel}</dd></div><div><dt>계획 시작일</dt><dd>{cycle?.startDate ?? mock?.startDate}</dd></div><div><dt>실제 시작</dt><dd>{cycle?.startedAt ? new Date(cycle.startedAt).toLocaleString("ko-KR") : "아직 시작하지 않음"}</dd></div><div><dt>완료</dt><dd>{cycle?.completedAt ? new Date(cycle.completedAt).toLocaleString("ko-KR") : "-"}</dd></div>{isDeleted ? <div><dt>삭제</dt><dd>{cycle?.deletedAt ? new Date(cycle.deletedAt).toLocaleString("ko-KR") : "-"}</dd></div> : null}<div><dt>포함 케이스</dt><dd>{resource.data?.itemTotal ?? items.length}</dd></div></dl><div><span>설명</span><InlineText multiline label="설명" value={description} onSave={(value) => updateField("description", value)} disabled={status === "COMPLETED" || isDeleted} /></div><ProgressDimensions progress={resource.data?.progress} fallbackCounts={counts} planned={Boolean(cycle && cycle.status === "PLANNED")} /></aside></section>
  </section>;
}

function patchAttempt(item: CycleItem, attempt: ExecutionAttempt): CycleItem {
  return { ...item, platformExecutions: item.platformExecutions.map((execution) => execution.platform === attempt.platform ? { platform: attempt.platform, status: attempt.status, latestAttempt: attempt } : execution) };
}

export function CycleExecutionPage() {
  const { cycleId } = useParams();
  const { state: demo, saveCycle } = useTestManagement();
  const service = getDefaultCycleService();
  const mock = cycleMockFallbackEnabled
    ? demo.cycles.find((candidate) => candidate.id === cycleId)
    : undefined;
  const mockAvailable = Boolean(mock);
  const resource = useCycleDetailData(cycleId, Boolean(mock));
  const fallbackMock = resource.state.phase === "fallback" ? mock : undefined;
  const rawItems = resource.data?.items;
  const items = useMemo(
    () => rawItems ? apiItemModels(rawItems) : fallbackMock ? mockItemModels(fallbackMock, demo) : [],
    [demo, fallbackMock, rawItems],
  );
  const cycleStatus = resource.data?.cycle.status ?? (fallbackMock ? toApiStatus(fallbackMock.status) : undefined);
  const [selectedItemId, setSelectedItemId] = useState("");
  const [selectedPlatform, setSelectedPlatform] = useState<Platform>("WEB");
  const [selectedStepSequence, setSelectedStepSequence] = useState<number>();
  const [openResultPlatform, setOpenResultPlatform] = useState<Platform>();
  const [search, setSearch] = useState("");
  const [stepExecution, setStepExecution] = useState<StepExecution>();
  const [stepExecutions, setStepExecutions] = useState<Partial<Record<Platform, StepExecution>>>({});
  const [stepDrafts, setStepDrafts] = useState<Partial<Record<Platform, StepStatusDraft>>>({});
  const [stepNoteDrafts, setStepNoteDrafts] = useState<Partial<Record<Platform, Record<number, string>>>>({});
  const [stepLoading, setStepLoading] = useState(false);
  const [resultError, setResultError] = useState("");
  const [mutationState, setMutationState] = useState<PersistenceState>({ phase: "ready" });
  const [history, setHistory] = useState<ExecutionAttempt[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [linkedIssues, setLinkedIssues] = useState<Record<string, IssueResource[]>>({});
  const issueRequests = useRef<Record<string, Promise<IssueResource | undefined>>>({});
  const [issueSeverity, setIssueSeverity] = useState<IssueResource["severity"]>("HIGH");
  const [issueCreateModalOpen, setIssueCreateModalOpen] = useState(false);
  const [issueDraft, setIssueDraft] = useState<ExecutionIssueDraft>({ summary: "", precondition: "", reproductionStep: "", expectedResult: "", actualResult: "" });
  const [issueDraftError, setIssueDraftError] = useState("");
  const [issuePendingFiles, setIssuePendingFiles] = useState<File[]>([]);
  const issueAttachmentInputRef = useRef<HTMLInputElement>(null);
  const stepExecutionCacheRef = useRef(new Map<string, StepExecution>());
  const selectedItemIdRef = useRef<string | undefined>(undefined);
  const selectedPlatformRef = useRef<Platform>("WEB");
  const persistStepAutosaveRef = useRef<(draft: StepAutosaveDraft) => Promise<boolean | void>>(async () => false);
  const stepAutosaveQueue = useMemo(() => createDebouncedAutosaveQueue(
    (draft: StepAutosaveDraft) => persistStepAutosaveRef.current(draft),
    500,
    (draft) => `${draft.status ?? "UNEXECUTED"}\u0000${draft.note.trim()}`,
  ), []);
  const [issueMutation, setIssueMutation] = useState<{ phase: "idle" | "loading" | "error"; message: string }>({ phase: "idle", message: "" });
  const { dialogRef: issueDialogRef, onDialogKeyDown: onIssueDialogKeyDown } = useAccessibleDialog<HTMLElement>(issueCreateModalOpen, () => setIssueCreateModalOpen(false), issueMutation.phase === "loading");
  const selectedItem = items.find((item) => item.id === selectedItemId) ?? items[0];
  const selectedStep = selectedItem?.steps.find((step) => step.sequence === selectedStepSequence) ?? selectedItem?.steps[0];
  selectedItemIdRef.current = selectedItem?.id;
  selectedPlatformRef.current = selectedPlatform;
  const stepDraft = stepDrafts[selectedPlatform] ?? {};
  const note = selectedStep ? stepNoteDrafts[selectedPlatform]?.[selectedStep.sequence] ?? "" : "";
  const setStepDraft = useCallback((next: SetStateAction<StepStatusDraft>) => {
    setStepDrafts((current) => {
      const currentPlatformDraft = current[selectedPlatform] ?? {};
      const nextPlatformDraft = typeof next === "function" ? next(currentPlatformDraft) : next;
      return { ...current, [selectedPlatform]: nextPlatformDraft };
    });
  }, [selectedPlatform]);
  const selectedIndex = items.findIndex((item) => item.id === selectedItem?.id);
  const selectedAttempt = selectedItem?.attempts[selectedPlatform];
  const platformStatus = selectedItem?.statuses[selectedPlatform] ?? "UNEXECUTED";
  const enabled = canEditCycleExecution(cycleStatus);
  const resultInputEnabled = enabled && !stepLoading;
  const filteredItems = items.filter((item) => [item.caseKey, item.summary].some((value) => value.toLocaleLowerCase("ko-KR").includes(search.trim().toLocaleLowerCase("ko-KR"))));
  const counts = resource.data?.progress?.overall.values.counts ?? getItemCounts(items);
  const effectiveState = mutationState.phase === "ready" ? resource.state : mutationState;
  const selectedItemIsApi = Boolean(selectedItem?.raw);
  useEffect(() => {
    // React StrictMode deliberately runs one setup/cleanup probe in development.
    // React retains the memoized queue across that probe, so reactivate it before
    // accepting edits and dispose only for the corresponding cleanup.
    stepAutosaveQueue.activate();
    return () => stepAutosaveQueue.dispose();
  }, [stepAutosaveQueue]);
  useEffect(() => { if (!selectedItemId && items[0]) setSelectedItemId(items[0].id); }, [items, selectedItemId]);
  useEffect(() => {
    if (!selectedItem) {
      setSelectedStepSequence(undefined);
      return;
    }
    if (!selectedItem.steps.length) {
      setSelectedStepSequence(undefined);
      return;
    }
    const firstStep = selectedItem.steps[0];
    if (!firstStep) {
      setSelectedStepSequence(undefined);
      return;
    }
    setSelectedStepSequence((current) => current !== undefined && selectedItem.steps.some((step) => step.sequence === current)
      ? current
      : firstStep.sequence);
  }, [selectedItem]);
  useEffect(() => { setOpenResultPlatform(undefined); }, [selectedItem?.id, selectedStepSequence]);
  useEffect(() => {
    const itemId = selectedItem?.id;
    if (!selectedItemIsApi || !itemId) {
      setStepExecutions({});
      setStepDrafts({});
      setStepNoteDrafts({});
      setStepLoading(false);
      return;
    }
    let active = true;
    setStepLoading(true);
    setStepExecutions({});
    setStepDrafts({});
    setStepNoteDrafts({});
    void Promise.all(platforms.map((platform) => service.call((api) => api.getStepExecution(itemId, platform)))).then((responses) => {
      if (!active) return;
      setStepExecutions(Object.fromEntries(responses.map((response) => [response.platform, response])) as Partial<Record<Platform, StepExecution>>);
      setStepDrafts(Object.fromEntries(responses.map((response) => [response.platform, Object.fromEntries(response.steps.map((step) => [step.sequence, step.status === "PASS" || step.status === "FAIL" || step.status === "BLOCKED" ? step.status : undefined]))])) as Partial<Record<Platform, StepStatusDraft>>);
      setStepNoteDrafts(Object.fromEntries(responses.map((response) => [response.platform, Object.fromEntries(response.steps.map((step) => [step.sequence, step.note ?? ""]))])) as Partial<Record<Platform, Record<number, string>>>);
      for (const response of responses) {
        stepExecutionCacheRef.current.set(`${response.cycleItemId}:${response.platform}`, response);
        for (const step of response.steps) {
          stepAutosaveQueue.hydrate(stepAutosaveKey(response.cycleItemId, response.platform, step.sequence), {
            itemId: response.cycleItemId,
            platform: response.platform,
            sequence: step.sequence,
            status: step.status === "PASS" || step.status === "FAIL" || step.status === "BLOCKED" ? step.status : undefined,
            note: step.note ?? "",
          });
        }
      }
    }).catch((error: unknown) => {
      if (active) setMutationState(errorState(error, mockAvailable));
    }).finally(() => {
      if (active) setStepLoading(false);
    });
    return () => { active = false; };
  }, [mockAvailable, selectedItem?.id, selectedItemIsApi, service, stepAutosaveQueue]);
  useEffect(() => {
    if (!selectedItem) return;
    setResultError("");
    if (!selectedItemIsApi) {
      const fallbackStatus = platformStatus === "PASS" || platformStatus === "FAIL" || platformStatus === "BLOCKED" ? platformStatus : undefined;
      setStepDraft((current) => Object.keys(current).length
        ? current
        : Object.fromEntries(selectedItem.steps.map((step) => [step.sequence, fallbackStatus])));
      const fallbackNote = mock?.executions.find((execution) => execution.id === selectedItem.id)?.note ?? "";
      setStepNoteDrafts((current) => current[selectedPlatform]
        ? current
        : { ...current, [selectedPlatform]: Object.fromEntries(selectedItem.steps.map((step) => [step.sequence, fallbackNote])) });
      for (const step of selectedItem.steps) {
        stepAutosaveQueue.hydrate(stepAutosaveKey(selectedItem.id, selectedPlatform, step.sequence), {
          itemId: selectedItem.id,
          platform: selectedPlatform,
          sequence: step.sequence,
          status: fallbackStatus,
          note: fallbackNote,
        });
      }
      setStepExecution(undefined);
      return;
    }
    const response = stepExecutions[selectedPlatform];
    if (!response || response.cycleItemId !== selectedItem.id) {
      setStepExecution(undefined);
      return;
    }
    setStepExecution(response);
  }, [mock, platformStatus, selectedItem, selectedItemIsApi, selectedPlatform, selectedStep?.sequence, setStepDraft, stepAutosaveQueue, stepExecutions]);
  useEffect(() => {
    const itemId = selectedItem?.id;
    if (!selectedItemIsApi || !itemId) { setHistory([]); return; }
    let active = true; setHistoryLoading(true);
    void service.call((api) => api.listExecutionAttemptHistory(itemId, { platforms: [selectedPlatform], limit: 50 })).then((response) => { if (active) setHistory(response.items); }).catch((error: unknown) => { if (active) setMutationState(errorState(error)); }).finally(() => { if (active) setHistoryLoading(false); });
    return () => { active = false; };
  }, [selectedItem?.id, selectedItemIsApi, selectedPlatform, service]);
  const currentAttemptId = stepExecution?.attemptId ?? selectedAttempt?.id;
  const currentAttemptStatus = stepExecution?.status ?? selectedAttempt?.status;
  useEffect(() => {
    if (!currentAttemptId || !selectedItemIsApi) {
      setIssueMutation({ phase: "idle", message: "" });
      return;
    }
    if (linkedIssues[currentAttemptId] || issueRequests.current[currentAttemptId]) return;
    setIssueMutation({ phase: "loading", message: "연결된 원장 이슈를 확인하는 중…" });
    const request = sprint4Api.listIssues({ executionAttemptIds: [currentAttemptId], limit: 100 }).then(async (page) => {
      const issues = await Promise.all(page.items.map((item) => sprint4Api.getIssue(item.id)));
      setLinkedIssues((current) => ({ ...current, [currentAttemptId]: issues }));
      return issues[0];
    }).then((issue) => {
      setIssueMutation({ phase: "idle", message: "" });
      return issue;
    }).catch((requestError: unknown) => {
      setIssueMutation({ phase: "error", message: apiErrorMessage(requestError, "연결 이슈를 확인하지 못했습니다.") });
      return undefined;
    }).finally(() => {
      delete issueRequests.current[currentAttemptId];
    });
    issueRequests.current[currentAttemptId] = request;
  }, [currentAttemptId, currentAttemptStatus, linkedIssues, selectedItemIsApi]);
  const selectRelative = useCallback((offset: number) => { if (!items.length) return; const next = items[(Math.max(selectedIndex, 0) + offset + items.length) % items.length]; if (next) setSelectedItemId(next.id); }, [items, selectedIndex]);
  useEffect(() => {
    const listener = (event: globalThis.KeyboardEvent) => { const target = event.target as HTMLElement | null; if (target?.matches("input, textarea, select")) return; if (event.key === "ArrowLeft" || event.key === "[") selectRelative(-1); if (event.key === "ArrowRight" || event.key === "]") selectRelative(1); };
    window.addEventListener("keydown", listener); return () => window.removeEventListener("keydown", listener);
  }, [selectRelative]);
  const updateApiAttempt = (attempt: ExecutionAttempt) => {
    resource.setData((current) => current ? { ...current, items: current.items.map((item) => item.id === attempt.cycleItemId ? patchAttempt(item, attempt) : item) } : current);
    if (selectedItemIdRef.current === attempt.cycleItemId && selectedPlatformRef.current === attempt.platform) {
      setHistory((current) => [attempt, ...current.filter((candidate) => candidate.id !== attempt.id)]);
    }
  };
  const createIssueForAttempt = async (attemptId: string, draft: ExecutionIssueDraft, files: File[]) => {
    const pending = issueRequests.current[attemptId];
    if (pending) return pending;
    const request = (async () => {
      setIssueMutation({ phase: "loading", message: "이슈를 생성하고 현재 실행 시도에 연결하는 중…" });
      try {
        const idempotencyKey = mutationKey(`attempt-issue-${attemptId}`);
        const issue = await sprint4Api.createIssue({
          idempotencyKey,
          summary: draft.summary.trim(),
          precondition: draft.precondition.trim(),
          steps: [{ sequence: 1, action: draft.reproductionStep.trim(), expectedResult: draft.expectedResult.trim() }],
          expectedResult: draft.expectedResult.trim(),
          actualResult: draft.actualResult.trim(),
          severity: issueSeverity,
          assigneeLabel: null,
          initialLinks: [{ type: "EXECUTION_ATTEMPT", executionAttemptId: attemptId }],
        }, { idempotencyKey, actorLabel: "Cycle Execution Web UI" });
        setLinkedIssues((current) => ({ ...current, [attemptId]: [issue, ...(current[attemptId] ?? []).filter((candidate) => candidate.id !== issue.id)] }));
        try {
          for (const [index, file] of files.entries()) {
            setIssueMutation({ phase: "loading", message: `첨부파일 저장 중 ${index + 1}/${files.length}…` });
            const attachmentKey = mutationKey(`cycle-issue-attachment-${issue.id}`);
            await sprint4Api.uploadIssueAttachment(issue.id, {
              idempotencyKey: attachmentKey,
              owner: { type: "ISSUE", issueId: issue.id },
              filename: file.name,
              declaredMimeType: resolveAttachmentMimeType(file) as AttachmentResource["mimeType"],
              declaredSizeBytes: file.size,
            }, file, { idempotencyKey: attachmentKey, actorLabel: "Cycle Execution Web UI" });
          }
          setIssueMutation({ phase: "idle", message: `${issue.key}가 생성되어 자동 연결되었습니다.${files.length ? ` 첨부파일 ${files.length}개를 비공개 저장소에 저장했습니다.` : ""}` });
        } catch (attachmentError) {
          setIssueMutation({ phase: "error", message: `${issue.key}는 생성됐지만 첨부파일 저장에 실패했습니다. 이슈 상세에서 다시 추가하세요. ${apiErrorMessage(attachmentError, "")}`.trim() });
        }
        setIssuePendingFiles([]);
        return issue;
      } catch (requestError) {
        setIssueMutation({ phase: "error", message: apiErrorMessage(requestError, "실행 이슈를 생성하지 못했습니다.") });
        return undefined;
      } finally {
        delete issueRequests.current[attemptId];
      }
    })();
    issueRequests.current[attemptId] = request;
    return request;
  };
  const persistStepAutosave = async (draft: StepAutosaveDraft): Promise<boolean> => {
    const item = items.find((candidate) => candidate.id === draft.itemId);
    if (!item || !enabled) return false;
    const selectedStatus = draft.status;
    const isCurrentDraft = selectedItemIdRef.current === draft.itemId
      && selectedPlatformRef.current === draft.platform
      && selectedStep?.sequence === draft.sequence;
    if (!selectedStatus) {
      if (isCurrentDraft) setResultError(`${draft.sequence}번 스텝의 PASS, FAIL 또는 BLOCKED를 선택하세요.`);
      return false;
    }
    const nextDraft = { ...(stepDrafts[draft.platform] ?? {}), [draft.sequence]: selectedStatus };
    const derivedStatus = deriveStepExecutionStatus(item.steps.map((step) => nextDraft[step.sequence]));
    if (!item.raw) {
      if (!mock || derivedStatus === "UNEXECUTED") return false;
      saveCycle({
        ...mock,
        status: mock.status,
        executions: mock.executions.map((execution) => execution.id === item.id ? {
          ...execution,
          note: draft.note.trim(),
          platformStatuses: { ...execution.platformStatuses, [draft.platform]: derivedStatus },
        } : execution),
      });
      if (isCurrentDraft) setResultError("");
      setMutationState({ phase: "fallback", message: `mock fallback에 ${draft.sequence}번 스텝 결과를 자동 저장했습니다.` });
      return true;
    }
    setMutationState({ phase: "loading", message: `${draft.sequence}번 스텝 결과를 자동 저장합니다.` });
    try {
      const previousExecution = stepExecutionCacheRef.current.get(`${item.id}:${draft.platform}`);
      const previousAttemptId = previousExecution?.attemptId;
      if (previousAttemptId && (previousExecution.status === "PASS" || previousExecution.status === "FAIL" || previousExecution.status === "BLOCKED")) {
        await service.call((api) => api.retryExecutionAttempt(item.id, {
          previousAttemptId,
          executorLabel: "Cycle Web UI",
          note: "Step result 화면에서 재실행",
        }, cycleMutationOptions("execution-retry")));
      }
      const sharedNote = draft.note.trim();
      const response = await service.call((api) => api.recordStepExecutionResults(item.id, draft.platform, {
        results: [{
          sequence: draft.sequence,
          status: selectedStatus,
          ...(sharedNote ? { note: sharedNote } : {}),
        }],
        executorLabel: "Cycle Web UI",
      }, cycleMutationOptions("step-execution-results")));
      stepExecutionCacheRef.current.set(`${response.cycleItemId}:${response.platform}`, response);
      if (selectedItemIdRef.current === response.cycleItemId) {
        const savedStep = response.steps.find((step) => step.sequence === draft.sequence);
        const savedStatus = savedStep?.status === "PASS" || savedStep?.status === "FAIL" || savedStep?.status === "BLOCKED" ? savedStep.status : undefined;
        setStepExecutions((current) => ({ ...current, [response.platform]: response }));
        setStepDrafts((current) => ({
          ...current,
          [response.platform]: { ...(current[response.platform] ?? {}), [draft.sequence]: savedStatus },
        }));
        setStepNoteDrafts((current) => ({
          ...current,
          [response.platform]: { ...(current[response.platform] ?? {}), [draft.sequence]: savedStep?.note ?? sharedNote },
        }));
        if (selectedPlatformRef.current === response.platform) setStepExecution(response);
      }
      if (response.attemptId && response.attemptNo && response.startedAt && response.status !== "UNEXECUTED") {
        updateApiAttempt({
          id: response.attemptId,
          cycleId: response.cycleId,
          cycleItemId: response.cycleItemId,
          pinnedVersionId: response.pinnedVersionId,
          platform: response.platform,
          attemptNo: response.attemptNo,
          status: response.status,
          actualResult: response.status === "FAIL" ? sharedNote || "Step FAIL 결과" : null,
          note: sharedNote || null,
          executorLabel: response.executorLabel,
          startedAt: response.startedAt,
          completedAt: response.completedAt,
          isLatest: true,
        });
      }
      if (isCurrentDraft) setResultError("");
      setMutationState({ phase: "ready" });
      return true;
    } catch (error) {
      setMutationState(errorState(error, Boolean(mock)));
      throw error;
    }
  };
  persistStepAutosaveRef.current = persistStepAutosave;
  if (resource.state.phase === "loading" && !resource.data) return <CycleLoadingSurface variant="execution" />;
  if ((!cycleStatus || !selectedItem) && resource.state.phase === "not-found") return <section className="tm-not-found"><h1>사이클을 찾을 수 없습니다.</h1><Link className="tm-primary-button" replace={appNavigationOptions.returnToList.replace} to="/cycles">사이클 목록</Link></section>;
  if (!cycleStatus || !selectedItem) return <section className="tm-cycle-page tm-not-found"><h1>사이클 실행을 표시할 수 없습니다.</h1><PersistenceBanner state={resource.state} onRetry={resource.retry} /><Link className="tm-primary-button" replace={appNavigationOptions.returnToList.replace} to="/cycles">사이클 목록</Link></section>;
  const cycleName = resource.data?.cycle.name ?? fallbackMock?.name ?? "사이클";
  const cycleScope = resource.data?.cycle.scope ? scopeText(resource.data.cycle.scope) : fallbackMock ? mockScopeText(fallbackMock, demo) : "-";
  const integratedStatus = aggregateExecutionStatuses(platforms.map((platform) => {
    const platformDraft = stepDrafts[platform];
    return platformDraft
      ? deriveStepExecutionStatus(selectedItem.steps.map((step) => platformDraft[step.sequence]))
      : selectedItem.statuses[platform];
  }));
  const integratedStepStatus = (sequence: number) => aggregateExecutionStatuses(platforms.map((platform) => {
    const platformDraft = stepDrafts[platform];
    if (platformDraft && Object.prototype.hasOwnProperty.call(platformDraft, sequence)) {
      return platformDraft[sequence] ?? "UNEXECUTED";
    }
    const persisted = stepExecutions[platform]?.steps.find((step) => step.sequence === sequence)?.status;
    if (persisted) return persisted;
    return selectedItem.raw ? "UNEXECUTED" : selectedItem.statuses[platform];
  }));
  const linkedIssueList = currentAttemptId ? linkedIssues[currentAttemptId] ?? [] : [];
  const cleanedCycleName = cycleName
    .replace(/^Cycle\s+/i, "")
    .replace(/^사이클\s*/i, "")
    .replace(/^Test\s*Cycle\s*/i, "")
    .trim();
  const cycleExecutionTitle = `Cycle Execution${cleanedCycleName ? ` · ${cleanedCycleName}` : ""}`;
  const setPlatformStepResult = (platform: Platform, status: TerminalStatus | undefined) => {
    if (!selectedStep) return;
    setSelectedPlatform(platform);
    setStepDrafts((current) => ({
      ...current,
      [platform]: { ...(current[platform] ?? {}), [selectedStep.sequence]: status },
    }));
    stepAutosaveQueue.schedule(stepAutosaveKey(selectedItem.id, platform, selectedStep.sequence), {
      itemId: selectedItem.id,
      platform,
      sequence: selectedStep.sequence,
      status,
      note: stepNoteDrafts[platform]?.[selectedStep.sequence] ?? "",
    });
  };
  const setSelectedStepNote = (value: string) => {
    if (!selectedStep) return;
    setStepNoteDrafts((current) => ({
      ...current,
      [selectedPlatform]: { ...(current[selectedPlatform] ?? {}), [selectedStep.sequence]: value },
    }));
    stepAutosaveQueue.schedule(stepAutosaveKey(selectedItem.id, selectedPlatform, selectedStep.sequence), {
      itemId: selectedItem.id,
      platform: selectedPlatform,
      sequence: selectedStep.sequence,
      status: stepDraft[selectedStep.sequence],
      note: value,
    });
  };
  const addIssuePendingFiles = (files: File[]) => {
    const accepted: File[] = [];
    let validationMessage = "";
    for (const file of files) {
      if (!file.name.trim() || file.name.includes("/") || file.name.includes("\\")) { validationMessage = "안전한 파일 이름을 사용하세요."; continue; }
      if (!issueAttachmentMimeTypes.has(resolveAttachmentMimeType(file) as AttachmentResource["mimeType"])) { validationMessage = `지원하지 않는 파일 형식입니다: ${file.name}`; continue; }
      if (file.size <= 0 || file.size > 50 * 1024 * 1024) { validationMessage = `${file.name}: 파일은 1 byte 이상 50 MB 이하여야 합니다.`; continue; }
      accepted.push(file);
    }
    setIssueDraftError(validationMessage);
    setIssuePendingFiles((current) => {
      const existing = new Set(current.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
      return [...current, ...accepted.filter((file) => !existing.has(`${file.name}:${file.size}:${file.lastModified}`))];
    });
  };
  const openIssueCreateModal = () => {
    if (!selectedItem?.raw || !selectedStep) {
      setIssueMutation({ phase: "error", message: "선택한 스텝에서 이슈를 생성할 수 없습니다." });
      return;
    }
    const selectedStatus = stepDraft[selectedStep.sequence] ?? "UNEXECUTED";
    setIssueDraft({
      summary: `${selectedItem.summary} · ${selectedPlatform === "IOS" ? "iOS" : selectedPlatform} ${selectedStep.sequence}번 스텝 이슈`.slice(0, 200),
      precondition: (selectedStep.precondition || selectedItem.precondition || "사전조건 없음").trim(),
      reproductionStep: (selectedStep.action || "재현 동작 없음").trim(),
      expectedResult: (selectedStep.expectedResult || "기대 결과 없음").trim(),
      actualResult: note.trim() || `${selectedPlatform === "IOS" ? "iOS" : selectedPlatform} ${selectedStep.sequence}번 스텝 실행 결과: ${selectedStatus}`,
    });
    setIssueDraftError("");
    setIssuePendingFiles([]);
    setIssueMutation({ phase: "idle", message: "" });
    setIssueCreateModalOpen(true);
  };
  const createLinkedIssue = async () => {
    if (!selectedItem?.raw) {
      setIssueMutation({ phase: "error", message: "현재 화면은 fallback 모드라서 이슈 기능이 비활성화됩니다." });
      return;
    }
    if (!issueDraft.summary.trim() || !issueDraft.precondition.trim() || !issueDraft.reproductionStep.trim() || !issueDraft.expectedResult.trim() || !issueDraft.actualResult.trim()) {
      setIssueDraftError("서머리, 사전 조건, 재현 스텝, 기대 결과, 실제 결과를 모두 입력하세요.");
      return;
    }

    const ensureAttempt = async (): Promise<string | undefined> => {
      if (stepExecution?.attemptId) return stepExecution.attemptId;
      if (selectedAttempt?.id) return selectedAttempt.id;

      setIssueMutation({ phase: "loading", message: "이슈 연결을 위해 실행 시도를 시작합니다." });
      try {
        const attempt = await service.call((api) => api.startExecutionAttempt(selectedItem.id, {
          platform: selectedPlatform,
          executorLabel: "Cycle Web UI",
          note: `${selectedItem.caseKey} 이슈 생성용 실행 시도`,
        }, cycleMutationOptions("execution-start-for-issue")));
        updateApiAttempt({
          id: attempt.id,
          cycleId: attempt.cycleId,
          cycleItemId: attempt.cycleItemId,
          pinnedVersionId: attempt.pinnedVersionId,
          platform: attempt.platform,
          attemptNo: attempt.attemptNo,
          status: attempt.status,
          actualResult: attempt.actualResult,
          note: attempt.note,
          executorLabel: attempt.executorLabel,
          startedAt: attempt.startedAt,
          completedAt: attempt.completedAt,
          isLatest: attempt.isLatest,
        });
        setHistory((current) => [attempt, ...current.filter((candidate) => candidate.id !== attempt.id)]);
        return attempt.id;
      } catch (error) {
        setIssueMutation({ phase: "error", message: apiErrorMessage(error, "이슈 생성용 실행 시도를 시작하지 못했습니다.") });
        return undefined;
      }
    };

    if (currentAttemptId) {
      const issue = await createIssueForAttempt(currentAttemptId, issueDraft, issuePendingFiles);
      if (issue) setIssueCreateModalOpen(false);
      return;
    }

    const attemptId = await ensureAttempt();
    if (!attemptId) {
      return;
    }
    const issue = await createIssueForAttempt(attemptId, issueDraft, issuePendingFiles);
    if (issue) setIssueCreateModalOpen(false);
  };
  return (
    <section className="tm-execution-player" aria-labelledby="execution-player-title">
      <PersistenceBanner state={effectiveState} onRetry={effectiveState.phase === "ready" ? undefined : resource.retry} />
      <header className="tm-player-header">
        <div className="tm-player-title-row">
          <Link className="tm-subtle-button tm-player-detail-button" replace={appNavigationOptions.returnToList.replace} to={`/cycles/${cycleId}`}>← 사이클 상세</Link>
          <div className="tm-player-title-block">
            <h1 id="execution-player-title">{cycleExecutionTitle}</h1>
            <p>{cycleScope} · {items.length}개 케이스</p>
          </div>
        </div>
        <div className="tm-player-header-right">
          <span className="tm-cycle-state-cell">
            <span className={`tm-cycle-state-badge is-${statusClass(cycleStatus)}`}>{statusLabel(cycleStatus)}</span>
          </span>
          <div className="tm-player-progress">
            <span>PASS {counts.PASS}</span>
            <span>FAIL {counts.FAIL}</span>
            <span>BLOCKED {counts.BLOCKED}</span>
            <ProgressBar counts={counts} />
          </div>
        </div>
      </header>
      {cycleStatus === "PLANNED" ? <div className="tm-player-lock-banner" role="status">미진행 사이클은 실행 결과를 입력할 수 없습니다. 사이클 상세에서 시작한 뒤 진행하세요.</div> : null}
      {cycleStatus === "COMPLETED" ? <div className="tm-player-lock-banner is-completed" role="status">완료된 사이클은 스텝 결과와 이슈 연결을 읽기 전용으로 표시합니다.</div> : null}
      <div className="tm-player-layout">
        <aside className="tm-player-queue">
          <header>
            <div className="tm-player-queue-heading">
              <h2>테스트 큐</h2>
              <span aria-label={`${filteredItems.length}개 테스트케이스`}>{filteredItems.length}개</span>
            </div>
            <label className="tm-search">
              <span>⌕</span>
              <input aria-label="실행 케이스 검색" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="키 또는 서머리" />
            </label>
          </header>
          <div>
            {filteredItems.map((item, index) => (
              <button
                className={selectedItem.id === item.id ? "is-selected" : ""}
                type="button"
                key={item.id}
                onClick={() => setSelectedItemId(item.id)}
              >
                <span>{index + 1}</span>
                <div>
                  <strong>{item.summary}</strong>
                  <small>{item.caseKey} · pinned v{item.pinnedVersion}</small>
                </div>
                <i className={`is-${aggregateStatuses(item.statuses).toLowerCase()}`}>{aggregateStatuses(item.statuses)}</i>
              </button>
            ))}
          </div>
          {resource.data?.itemNextCursor ? <button className="tm-pagination-button" type="button" onClick={() => void resource.loadMoreItems()}>다음 100개 · {items.length}/{resource.data.itemTotal}</button> : null}
        </aside>
        <main className="tm-player-case">
          <header>
            <div>
              <span className="tm-case-type-icon">T</span>
              <div>
                <p>{selectedItem.caseKey} · pinned v{selectedItem.pinnedVersion}</p>
                <h2>{selectedItem.summary}</h2>
              </div>
            </div>
            <Link className="tm-utility-link" to={`/test-cases/${selectedItem.caseKey}`}>라이브러리 원본 ↗</Link>
          </header>
          {!selectedItem.isLatestVersion ? <div className="tm-pinned-warning">library latest v{selectedItem.latestVersion}과 다릅니다. 실행 script는 pinned v{selectedItem.pinnedVersion}을 유지합니다.</div> : null}
          <section className="tm-player-steps tm-player-steps--results">
            <header><h3>테스트 스텝</h3><span>{stepLoading ? "결과 불러오는 중…" : `${selectedItem.steps.length}개 스텝 · 통합 ${integratedStatus}`}</span></header>
            {selectedItem.steps.map((step) => {
              const aggregate = integratedStepStatus(step.sequence);
              return (
                <article
                  className={selectedStep?.sequence === step.sequence ? "is-selected" : ""}
                  role="button"
                  tabIndex={0}
                  key={step.sequence}
                  onClick={() => setSelectedStepSequence(step.sequence)}
                  onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") setSelectedStepSequence(step.sequence); }}
                >
                  <span>{step.sequence}</span>
                  <div><strong>사전조건</strong><p>{step.precondition || "사전조건 없음"}</p></div>
                  <div><strong>테스트 동작</strong><p>{step.action || "동작 없음"}</p></div>
                  <div><strong>기대 결과</strong><p>{step.expectedResult || "기대 결과 없음"}</p></div>
                  <div className="tm-player-step-aggregate"><strong>통합 결과</strong><span className={`tm-execution-aggregate is-${aggregate.toLowerCase()}`}>{aggregate}</span></div>
                </article>
              );
            })}
          </section>
        </main>
        <aside className="tm-player-result">
          <header><span>실행 결과</span><strong>{selectedItem.caseKey}</strong></header>
          <div className="tm-platform-tabs" role="tablist">
            {platforms.map((platform) => {
              const statusValue = selectedStep ? stepDrafts[platform]?.[selectedStep.sequence] : undefined;
              const displayedStatus = statusValue ?? "UNEXECUTED";
              const menuOpen = openResultPlatform === platform;
              return <div className={`tm-platform-result-menu is-${displayedStatus.toLowerCase()}${selectedPlatform === platform ? " is-selected" : ""}`} key={platform} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpenResultPlatform(undefined); }}><button type="button" role="tab" disabled={!resultInputEnabled} aria-selected={selectedPlatform === platform} aria-haspopup="listbox" aria-expanded={menuOpen} onClick={() => { setSelectedPlatform(platform); setOpenResultPlatform((current) => resultInputEnabled && selectedStep && current !== platform ? platform : undefined); }}><span>{platform === "IOS" ? "iOS" : platform}</span><span><b>{displayedStatus}</b><i aria-hidden="true" /></span></button>{menuOpen ? <div className="tm-platform-result-menu__options" role="listbox" aria-label={`${platform === "IOS" ? "iOS" : platform} ${selectedStep?.sequence ?? "-"}번 스텝 실행 결과`}>{([undefined, ...terminalStatuses] as Array<TerminalStatus | undefined>).map((status) => { const label = status ?? "UNEXECUTED"; return <button className={`is-${label.toLowerCase()}${statusValue === status ? " is-selected" : ""}`} type="button" role="option" aria-selected={statusValue === status} key={label} onClick={() => { setPlatformStepResult(platform, status); setOpenResultPlatform(undefined); }}><i aria-hidden="true" /><span>{label}</span>{statusValue === status ? <b aria-hidden="true">✓</b> : null}</button>; })}</div> : null}</div>;
            })}
          </div>
          <section className="tm-attempt-control">
            <div><span>{stepExecution?.attemptNo ? `시도 #${stepExecution.attemptNo}` : "새 시도"}</span><strong>{selectedStep?.sequence ? `${selectedStep.sequence}번 스텝` : "스텝 선택"}</strong></div>
            <small role="status">{mutationState.phase === "loading" ? "변경사항 자동 저장 중…" : stepExecution?.attemptId && stepExecution.completedAt ? "변경 시 새 시도로 자동 저장" : "상태와 비고는 잠시 후 자동 저장됩니다."}</small>
          </section>
          <label className="tm-player-note">
            <span>비고 · {selectedStep?.sequence ?? "-"}번 스텝</span>
            <textarea aria-label={`${selectedItem.caseKey} 비고`} rows={5} value={note} disabled={!resultInputEnabled} placeholder="실행 참고사항" onChange={(event) => setSelectedStepNote(event.target.value)} />
          </label>
          {resultError ? <p className="tm-result-validation" role="alert">{resultError}</p> : null}
          <section className="tm-player-issue">
            <div className="tm-player-issue-heading">
              <span>이슈</span>
              {selectedItem.raw ? <button className="tm-subtle-button tm-open-issue-modal" type="button" disabled={!enabled || issueMutation.phase === "loading"} onClick={openIssueCreateModal}>+ 이슈 생성</button> : null}
            </div>
            {linkedIssueList.length ? <div className="tm-created-issue-list">{linkedIssueList.map((issue) => <div className="tm-created-issue" key={issue.id}><Link to={`/issues/${issue.key}`}><strong>{issue.key}</strong><span>{issue.summary}</span></Link><small>현재 실행 시도에 자동 연결됨</small></div>)}</div> : null}
            {selectedItem.raw ? <p className="tm-player-issue-help">여러 이슈를 생성해 현재 실행에 자동 연결할 수 있습니다.</p> : <p className="tm-player-issue-help">개발용 fallback snapshot에서는 이슈를 만들지 않습니다. API 연결 후 재시도하세요.</p>}
            {issueMutation.message ? <small className={issueMutation.phase === "error" ? "tm-result-validation" : ""} role={issueMutation.phase === "error" ? "alert" : "status"}>{issueMutation.message}</small> : null}
          </section>
          <details className="tm-attempt-history" open>
            <summary>시도 기록 <span>{history.length || (selectedAttempt ? 1 : 0)}</span></summary>
            {historyLoading ? <p>이력 불러오는 중…</p> : <ol>{(history.length ? history : selectedAttempt ? [selectedAttempt] : []).map((attempt) => <li key={attempt.id}><b>#{attempt.attemptNo} {attempt.status}</b><span>{attempt.executorLabel ?? "미지정"}</span><small>{new Date(attempt.startedAt).toLocaleString("ko-KR")}{attempt.isLatest ? " · 최신" : ""}</small></li>)}</ol>}
          </details>
        </aside>
      </div>
      {issueCreateModalOpen ? (
        <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && issueMutation.phase !== "loading") setIssueCreateModalOpen(false); }}>
          <section ref={issueDialogRef} className="tm-cycle-modal tm-execution-issue-modal" role="dialog" aria-modal="true" aria-labelledby="execution-issue-modal-title" onKeyDown={onIssueDialogKeyDown}>
            <header>
              <div><h2 id="execution-issue-modal-title">이슈 생성</h2><p>선택한 실행 스텝의 정보를 확인하고 필요한 내용을 수정하세요.</p></div>
              <button className="tm-modal-close" type="button" aria-label="이슈 생성 닫기" disabled={issueMutation.phase === "loading"} onClick={() => setIssueCreateModalOpen(false)}>×</button>
            </header>
            <div className="tm-execution-issue-modal__body">
              <div className="tm-execution-issue-context"><span>{selectedItem.caseKey}</span><strong>{selectedPlatform === "IOS" ? "iOS" : selectedPlatform} · {selectedStep?.sequence ?? "-"}번 스텝</strong><small>생성 즉시 현재 실행 시도에 자동 연결됩니다.</small></div>
              <label className="tm-execution-issue-field tm-execution-issue-field--wide"><span>서머리 <b>*</b></span><input autoFocus maxLength={200} value={issueDraft.summary} onChange={(event) => setIssueDraft((current) => ({ ...current, summary: event.target.value }))} /></label>
              <label className="tm-execution-issue-field"><span>심각도</span><select value={issueSeverity} onChange={(event) => setIssueSeverity(event.target.value as IssueResource["severity"])}><option>LOW</option><option>MEDIUM</option><option>HIGH</option><option>CRITICAL</option></select></label>
              <label className="tm-execution-issue-field tm-execution-issue-field--wide"><span>사전 조건 <b>*</b></span><textarea rows={3} value={issueDraft.precondition} onChange={(event) => setIssueDraft((current) => ({ ...current, precondition: event.target.value }))} /></label>
              <label className="tm-execution-issue-field tm-execution-issue-field--wide"><span>재현 스텝 <b>*</b></span><textarea rows={4} value={issueDraft.reproductionStep} onChange={(event) => setIssueDraft((current) => ({ ...current, reproductionStep: event.target.value }))} /></label>
              <label className="tm-execution-issue-field"><span>기대 결과 <b>*</b></span><textarea rows={4} value={issueDraft.expectedResult} onChange={(event) => setIssueDraft((current) => ({ ...current, expectedResult: event.target.value }))} /></label>
              <label className="tm-execution-issue-field"><span>실제 결과 <b>*</b></span><textarea rows={4} value={issueDraft.actualResult} onChange={(event) => setIssueDraft((current) => ({ ...current, actualResult: event.target.value }))} /></label>
              <section className="tm-execution-issue-attachments">
                <header><div><strong>첨부파일</strong><small>로컬 파일을 선택하면 생성 후 비공개 저장소에 직접 업로드됩니다.</small></div><button className="tm-subtle-button tm-compact-button" type="button" onClick={() => issueAttachmentInputRef.current?.click()}>+ 파일 선택</button></header>
                <div className="tm-execution-issue-dropzone" role="button" tabIndex={0} onClick={() => issueAttachmentInputRef.current?.click()} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") issueAttachmentInputRef.current?.click(); }} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); addIssuePendingFiles(Array.from(event.dataTransfer.files)); }}><strong>파일을 끌어놓거나 클릭해서 선택하세요.</strong><span>여러 파일 가능 · 파일당 최대 50 MB</span></div>
                <input ref={issueAttachmentInputRef} hidden multiple type="file" aria-label="이슈 첨부파일 선택" onChange={(event) => { addIssuePendingFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
                {issuePendingFiles.length ? <div className="tm-execution-issue-file-list">{issuePendingFiles.map((file) => <article key={`${file.name}:${file.size}:${file.lastModified}`}><div><strong>{file.name}</strong><small>{formatAttachmentBytes(file.size)}</small></div><button type="button" aria-label={`${file.name} 제거`} onClick={() => setIssuePendingFiles((current) => current.filter((candidate) => candidate !== file))}>×</button></article>)}</div> : null}
              </section>
              {issueDraftError ? <p className="tm-execution-issue-modal__error" role="alert">{issueDraftError}</p> : null}
              {issueMutation.phase === "error" && issueMutation.message ? <p className="tm-execution-issue-modal__error" role="alert">{issueMutation.message}</p> : null}
            </div>
            <footer><small>동일 실행 시도에 여러 이슈를 생성할 수 있습니다.</small><div><button className="tm-subtle-button" type="button" disabled={issueMutation.phase === "loading"} onClick={() => setIssueCreateModalOpen(false)}>취소</button><button className="tm-primary-button" type="button" disabled={issueMutation.phase === "loading"} onClick={() => void createLinkedIssue()}>{issueMutation.phase === "loading" ? "생성 중…" : "이슈 생성"}</button></div></footer>
          </section>
        </div>
      ) : null}
    </section>
  );
}
