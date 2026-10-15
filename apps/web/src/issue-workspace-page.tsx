import {
  type AttachmentResource,
  type CycleItem,
  type CyclePageResponse,
  type IssueCommentResource,
  type IssueHistoryPage,
  type IssueLinkResource,
  type IssueListQuery,
  type IssueResource,
} from "@tc/api-client";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { uploadAttachmentBatch } from "./attachment-batch";
import { LocalAttachmentFilenamePreview, LocalAttachmentPreview, PrivateAttachmentFilenamePreview, PrivateAttachmentPreview } from "./attachment-preview";
import { resolveAttachmentMimeType } from "./attachment-preview-utils";
import { useAccessibleDialog } from "./dialog-accessibility";
import { DeleteConfirmDialog } from "./delete-confirm-dialog";
import { formatIssueHistoryValue, issueHistoryEventLabel, issueHistoryFieldLabel, issueStatusCountQuery, resolveIssueLinkCycleItemId, shouldDisplayIssueHistoryField } from "./issue-presentation";
import { appNavigationOptions } from "./navigation-policy";
import {
  apiErrorCode,
  apiErrorMessage,
  downloadBlob,
  mutationKey,
  sprint4Api,
  sprint4LifecycleApi,
  type DeletedIssueListItem,
  type DeletedIssueResource,
} from "./sprint4-service";

type IssueStatus = IssueResource["status"];
type IssueSeverity = IssueResource["severity"];
type IssueListItem = DeletedIssueListItem;
type CycleListItem = CyclePageResponse["items"][number];

const statuses: IssueStatus[] = ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED", "REOPENED"];
const severities: IssueSeverity[] = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
const issueStatusCards: Array<{ status: IssueStatus; label: string; description: string }> = [
  { status: "OPEN", label: "열림", description: "확인과 처리가 필요한 이슈" },
  { status: "IN_PROGRESS", label: "진행 중", description: "담당자가 처리 중인 이슈" },
  { status: "RESOLVED", label: "해결됨", description: "재검증을 기다리는 이슈" },
  { status: "CLOSED", label: "종료", description: "검증까지 끝난 이슈" },
];
const allowedMimeTypes = new Set<AttachmentResource["mimeType"]>([
  "image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "video/webm",
  "text/plain", "text/markdown", "text/csv", "application/json", "application/pdf", "application/zip", "application/gzip",
]);
const loadPrivateAttachmentBlob = (attachmentId: string, signal: AbortSignal) => sprint4Api.downloadAttachmentContent(attachmentId, signal);

function formatIssueTime(value: string | null | undefined) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Seoul" }).format(new Date(value));
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function newIssueDraft() {
  return {
    summary: "",
    precondition: "",
    reproductionSteps: "",
    expectedResult: "",
    actualResult: "",
    severity: "MEDIUM" as IssueSeverity,
    assigneeLabel: "",
  };
}

async function findIssueByKey(issueKey: string, signal: AbortSignal) {
  let cursor: string | null | undefined;
  do {
    const page = await sprint4Api.listIssues({ includeDeleted: true, sort: "KEY_ASC", limit: 200, ...(cursor ? { cursor } : {}) }, signal);
    const match = page.items.find((item) => item.key.toLocaleUpperCase() === issueKey.toLocaleUpperCase());
    if (match) return match;
    cursor = page.nextCursor;
  } while (cursor && !signal.aborted);
  return null;
}

function useCycleCaseCatalog(preferredCycleId = "") {
  const [cycles, setCycles] = useState<CycleListItem[]>([]);
  const [cycleId, setCycleId] = useState(preferredCycleId);
  const [items, setItems] = useState<CycleItem[]>([]);
  const [search, setSearch] = useState("");
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading");
    sprint4Api.listCycles({ limit: 200 }, controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      setCycles(page.items);
      setCycleId((current) => {
        if (preferredCycleId && page.items.some((cycle) => cycle.id === preferredCycleId)) return preferredCycleId;
        if (current && page.items.some((cycle) => cycle.id === current)) return current;
        return page.items[0]?.id ?? "";
      });
    }).catch((requestError: unknown) => {
      if (!controller.signal.aborted) {
        setError(apiErrorMessage(requestError, "사이클 목록을 불러오지 못했습니다."));
        setPhase("error");
      }
    });
    return () => controller.abort();
  }, [preferredCycleId]);

  useEffect(() => {
    if (!cycleId) {
      setItems([]);
      setPhase("ready");
      return;
    }
    const controller = new AbortController();
    setPhase("loading"); setError(""); setItems([]);
    sprint4Api.listCycleItems(cycleId, { limit: 200 }, controller.signal).then((page) => {
      if (!controller.signal.aborted) { setItems(page.items); setPhase("ready"); }
    }).catch((requestError: unknown) => {
      if (!controller.signal.aborted) {
        setError(apiErrorMessage(requestError, "사이클의 테스트케이스를 불러오지 못했습니다."));
        setPhase("error");
      }
    });
    return () => controller.abort();
  }, [cycleId]);

  const visibleItems = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase("ko-KR");
    if (!keyword) return items;
    return items.filter((item) => [item.caseKey, item.pinnedVersion.summary, item.workItemKey, ...item.labels]
      .some((value) => value.toLocaleLowerCase("ko-KR").includes(keyword)));
  }, [items, search]);

  return { cycles, cycleId, setCycleId, items, visibleItems, search, setSearch, phase, error };
}

interface CycleCasePickerProps {
  catalog: ReturnType<typeof useCycleCaseCatalog>;
  selectedItemIds: ReadonlySet<string>;
  onCycleChange: (cycleId: string) => void;
  onToggle: (item: CycleItem) => void;
  cycleLocked?: boolean;
  pendingItemId?: string;
  mode: "create" | "edit";
}

function CycleCasePicker({ catalog, selectedItemIds, onCycleChange, onToggle, cycleLocked = false, pendingItemId, mode }: CycleCasePickerProps) {
  const selectedCycle = catalog.cycles.find((cycle) => cycle.id === catalog.cycleId);
  return <section className="tm-issue-link-picker" aria-labelledby={`issue-link-picker-${mode}`}>
    <div className="tm-issue-link-steps" id={`issue-link-picker-${mode}`}><span className="is-complete"><i>1</i><b>사이클 선택</b></span><em aria-hidden="true">→</em><span className={catalog.cycleId ? "is-complete" : ""}><i>2</i><b>테스트케이스 선택</b></span><strong>{selectedItemIds.size}개 선택</strong></div>
    <div className="tm-issue-link-picker__controls">
      <label><span>1. 어떤 사이클인가요?</span><select aria-label="연결할 사이클" value={catalog.cycleId} disabled={cycleLocked || catalog.phase === "loading"} onChange={(event) => onCycleChange(event.target.value)}><option value="">사이클 선택</option>{catalog.cycles.map((cycle) => <option key={cycle.id} value={cycle.id}>{cycle.name}</option>)}</select></label>
      <label><span>2. 테스트케이스 찾기</span><input aria-label="연결할 테스트케이스 검색" value={catalog.search} onChange={(event) => catalog.setSearch(event.target.value)} placeholder="케이스 키나 서머리로 검색" /></label>
    </div>
    {cycleLocked ? <p className="tm-issue-link-guidance">한 이슈의 실행 근거는 하나의 사이클 안에서 관리됩니다. 다른 사이클로 바꾸려면 현재 연결을 먼저 해제하세요.</p> : null}
    {catalog.error ? <div className="tm-api-state is-error" role="alert">{catalog.error}</div> : null}
    <div className="tm-issue-link-case-list" role="listbox" aria-label={`${selectedCycle?.name ?? "선택한 사이클"} 테스트케이스`} aria-multiselectable="true">
      {catalog.phase === "loading" ? <p className="tm-muted">테스트케이스를 확인하는 중…</p> : catalog.visibleItems.map((item) => {
        const selected = selectedItemIds.has(item.id);
        return <button className={selected ? "is-selected" : ""} key={item.id} type="button" role="option" aria-selected={selected} disabled={pendingItemId === item.id || (mode === "edit" && selected)} onClick={() => onToggle(item)}>
          <span className="tm-issue-link-check" aria-hidden="true">{selected ? "✓" : "+"}</span>
          <span><strong>{item.pinnedVersion.summary}</strong><small>{item.caseKey} · {item.workItemKey}{item.labels.length ? ` · ${item.labels.map((label) => `#${label}`).join(" ")}` : ""}</small></span>
          <span>{pendingItemId === item.id ? "연결 중…" : selected ? "연결됨" : mode === "create" ? "선택" : "연결"}</span>
        </button>;
      })}
      {catalog.phase === "ready" && catalog.visibleItems.length === 0 ? <p className="tm-muted">조건에 맞는 테스트케이스가 없습니다.</p> : null}
    </div>
  </section>;
}

function CycleCasePickerModal(props: CycleCasePickerProps & { onClose: () => void }) {
  const { onClose, ...pickerProps } = props;
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLElement>(true, onClose);
  return <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section ref={dialogRef} className="tm-cycle-modal tm-issue-link-modal" role="dialog" aria-modal="true" aria-labelledby="issue-link-modal-title" onKeyDown={onDialogKeyDown}><header><div><h2 id="issue-link-modal-title">관련 테스트케이스 선택</h2><p>사이클 하나를 고르고 필요한 테스트케이스를 여러 개 선택할 수 있습니다.</p></div><button className="tm-modal-close" type="button" aria-label="관련 테스트케이스 선택 닫기" onClick={onClose}>×</button></header><div className="tm-issue-link-modal__body"><CycleCasePicker {...pickerProps} /></div><footer><small>선택한 테스트케이스는 이슈에서 함께 추적됩니다.</small><button className="tm-primary-button" type="button" onClick={onClose}>{pickerProps.mode === "create" ? "선택 완료" : "닫기"}</button></footer></section></div>;
}

function IssueConnectionSummary({ catalog, selectedItemIds, onOpen }: { catalog: ReturnType<typeof useCycleCaseCatalog>; selectedItemIds: string[]; onOpen: () => void }) {
  const cycle = catalog.cycles.find((candidate) => candidate.id === catalog.cycleId);
  const selectedItems = catalog.items.filter((item) => selectedItemIds.includes(item.id));
  return <section className="tm-issue-connection-summary"><div className="tm-section-heading"><div><h2>관련 테스트케이스</h2><p>선택 사항 · 이 이슈가 발견된 사이클의 테스트케이스를 연결합니다.</p></div><button className="tm-subtle-button tm-compact-button" type="button" onClick={onOpen}>{selectedItems.length ? "선택 변경" : "+ 테스트케이스 선택"}</button></div>{selectedItems.length ? <div className="tm-issue-connection-summary__selection"><strong>{cycle?.name ?? "선택한 사이클"}</strong><span>{selectedItems.length}개</span><div>{selectedItems.map((item) => <span key={item.id}><b>{item.caseKey}</b>{item.pinnedVersion.summary}</span>)}</div></div> : <button className="tm-issue-connection-empty" type="button" onClick={onOpen}><span aria-hidden="true">T</span><span><strong>연결된 테스트케이스가 없습니다.</strong><small>클릭해서 사이클과 테스트케이스를 선택하세요.</small></span><i aria-hidden="true">›</i></button>}</section>;
}

export function IssueWorkspacePage() {
  const navigate = useNavigate();
  const [viewMode, setViewMode] = useState<"ACTIVE" | "DELETED">("ACTIVE");
  const [issues, setIssues] = useState<IssueListItem[]>([]);
  const [cycles, setCycles] = useState<CycleListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [cycleId, setCycleId] = useState("");
  const [status, setStatus] = useState<"" | IssueStatus>("");
  const [severity, setSeverity] = useState<"" | IssueSeverity>("");
  const [search, setSearch] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [statusCounts, setStatusCounts] = useState<Record<IssueStatus, number>>({ OPEN: 0, IN_PROGRESS: 0, RESOLVED: 0, CLOSED: 0, REOPENED: 0 });

  const query = useMemo<IssueListQuery>(() => ({
    ...(appliedSearch ? { search: appliedSearch } : {}),
    ...(status ? { statuses: [status] } : {}),
    ...(severity ? { severities: [severity] } : {}),
    ...(cycleId ? { cycleIds: [cycleId] } : {}),
    sort: "UPDATED_AT_DESC", limit: 25,
  }), [appliedSearch, cycleId, severity, status]);

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading"); setError("");
    const request = viewMode === "DELETED"
      ? sprint4LifecycleApi.listDeletedIssues(query, controller.signal)
      : sprint4Api.listIssues(query, controller.signal);
    request.then((page) => {
      if (!controller.signal.aborted) { setIssues(page.items); setTotal(page.total); setNextCursor(page.nextCursor); setPhase("ready"); }
    }).catch((requestError: unknown) => {
      if (!controller.signal.aborted) { setError(apiErrorMessage(requestError, "이슈 원장을 불러오지 못했습니다.")); setPhase("error"); }
    });
    return () => controller.abort();
  }, [query, revision, viewMode]);

  useEffect(() => {
    if (viewMode === "DELETED") return;
    const controller = new AbortController();
    const sharedQuery: IssueListQuery = {
      ...(appliedSearch ? { search: appliedSearch } : {}),
      ...(severity ? { severities: [severity] } : {}),
      ...(cycleId ? { cycleIds: [cycleId] } : {}),
      sort: "UPDATED_AT_DESC",
      limit: 1,
    };
    Promise.all(statuses.map(async (value) => [value, (await sprint4Api.listIssues(issueStatusCountQuery(sharedQuery, value), controller.signal)).total] as const))
      .then((entries) => { if (!controller.signal.aborted) setStatusCounts(Object.fromEntries(entries) as Record<IssueStatus, number>); })
      .catch(() => { /* The main list keeps its own visible error and retry path. */ });
    return () => controller.abort();
  }, [appliedSearch, cycleId, revision, severity, viewMode]);

  useEffect(() => {
    const controller = new AbortController();
    const request = viewMode === "DELETED"
      ? Promise.all([sprint4Api.listCycles({ limit: 200 }, controller.signal), sprint4Api.listCycles({ deletedOnly: true, limit: 200 }, controller.signal)]).then(([active, deleted]) => ({ items: [...active.items, ...deleted.items] }))
      : sprint4Api.listCycles({ limit: 200 }, controller.signal);
    request
      .then((page) => { if (!controller.signal.aborted) setCycles(page.items); })
      .catch(() => { if (!controller.signal.aborted) setCycles([]); });
    return () => controller.abort();
  }, [revision, viewMode]);

  const cycleNameById = useMemo(
    () => new Map(cycles.map((cycle) => [cycle.id, cycle.name])),
    [cycles],
  );

  const loadMore = async () => {
    if (!nextCursor) return;
    try {
      const page = viewMode === "DELETED"
        ? await sprint4LifecycleApi.listDeletedIssues({ ...query, cursor: nextCursor })
        : await sprint4Api.listIssues({ ...query, cursor: nextCursor });
      setIssues((current) => [...current, ...page.items]); setNextCursor(page.nextCursor);
    } catch (requestError) { setError(apiErrorMessage(requestError, "다음 이슈를 불러오지 못했습니다.")); }
  };

  const switchView = (next: "ACTIVE" | "DELETED") => {
    setViewMode(next);
    setStatus("");
    setCycleId("");
    setError("");
  };

  return <section className="tm-issue-page tm-entity-landing" aria-labelledby="issue-page-title">
    <header className="tm-page-header"><div><h1 id="issue-page-title">Issues</h1><p>등록된 결함을 검색하고 상태·심각도·연결 사이클로 추적합니다.</p></div>{viewMode === "ACTIVE" ? <button className="tm-primary-button" type="button" onClick={() => void navigate("/issues/new", appNavigationOptions.openCreate)}>+ 이슈 만들기</button> : null}</header>
    <div className="tm-cycle-view-tabs tm-issue-view-tabs" role="tablist" aria-label="이슈 목록 구분">
      <button type="button" role="tab" aria-selected={viewMode === "ACTIVE"} className={viewMode === "ACTIVE" ? "is-active" : ""} onClick={() => switchView("ACTIVE")}>활성</button>
      <button type="button" role="tab" aria-selected={viewMode === "DELETED"} className={viewMode === "DELETED" ? "is-active" : ""} onClick={() => switchView("DELETED")}>삭제됨</button>
    </div>
    {viewMode === "ACTIVE" ? <section className="tm-landing-status-cards tm-issue-status-cards" aria-label="이슈 상태 필터">{issueStatusCards.map((item) => <button className={`is-${item.status.toLowerCase().replace("_", "-")}${status === item.status ? " is-selected" : ""}`} type="button" key={item.status} onClick={() => setStatus(status === item.status ? "" : item.status)}><span>{item.label}</span><strong>{statusCounts[item.status]}</strong><small>{item.description}</small></button>)}</section> : <section className="tm-deleted-cycle-guide tm-deleted-issue-guide" aria-label="삭제된 이슈 안내"><div><strong>삭제된 이슈</strong><p>본문, 첨부, 댓글과 변경 이력을 읽기 전용으로 확인할 수 있습니다.</p></div><span>{total}개</span></section>}
    <section className="tm-list-surface" aria-busy={phase === "loading"}><form className="tm-list-toolbar" onSubmit={(event) => { event.preventDefault(); setAppliedSearch(search.trim()); }}><label className="tm-search"><span aria-hidden="true">⌕</span><input aria-label="이슈 검색" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="키, 서머리, 담당자 검색" /></label><label className="tm-compact-filter"><span>사이클</span><select aria-label="이슈 목록 사이클" value={cycleId} onChange={(event) => setCycleId(event.target.value)}><option value="">전체 사이클</option>{cycles.map((cycle) => <option key={cycle.id} value={cycle.id}>{cycle.name}</option>)}</select></label><label className="tm-compact-filter"><span>상태</span><select aria-label="이슈 상태" value={status} onChange={(event) => setStatus(event.target.value as "" | IssueStatus)}><option value="">전체 상태</option>{statuses.map((value) => <option key={value}>{value}</option>)}</select></label><label className="tm-compact-filter"><span>심각도</span><select aria-label="이슈 심각도" value={severity} onChange={(event) => setSeverity(event.target.value as "" | IssueSeverity)}><option value="">전체 심각도</option>{severities.map((value) => <option key={value}>{value}</option>)}</select></label><button className="tm-subtle-button" type="submit">검색</button></form>
      {error ? <div className="tm-api-state is-error" role="alert"><strong>이슈 원장 오류</strong><p>{error}</p><button type="button" onClick={() => setRevision((value) => value + 1)}>다시 시도</button></div> : null}{phase === "loading" ? <div className="tm-api-state" role="status">PostgreSQL 이슈 원장을 불러오는 중…</div> : null}
      <div className={`tm-issue-list-table${viewMode === "DELETED" ? " is-deleted" : ""}`} role="table" aria-label={viewMode === "DELETED" ? "삭제된 이슈 목록" : "이슈 목록"}><div className="tm-issue-list-table__header" role="row"><span>이슈</span><span>사이클</span><span>심각도</span><span>담당자</span><span>상태</span><span>버전</span><span>{viewMode === "DELETED" ? "삭제" : "수정"}</span></div>{issues.map((issue) => <Link className={`tm-issue-list-table__row${viewMode === "DELETED" ? " tm-deleted-issue-row" : ""}`} role="row" to={`/issues/${issue.key}`} key={issue.id}><span><i className="tm-issue-type-icon" aria-hidden="true">!</i><span><strong>{issue.summary}</strong><small>{issue.key}{viewMode === "DELETED" ? " · 읽기 전용 상세" : ""}</small></span></span><span>{issue.linkedCycleIds.length ? issue.linkedCycleIds.map((id) => cycleNameById.get(id) ?? "알 수 없는 사이클").join(", ") : "—"}</span><span className={`tm-priority-label is-${issue.severity.toLowerCase()}`}>↑ {issue.severity}</span><span>{issue.assigneeLabel ?? "미지정"}</span><span className={`tm-issue-status-badge is-${issue.status.toLowerCase().replace("_", "-")}`}>{issue.status}</span><span>v{issue.resourceVersion}</span><span>{formatIssueTime(viewMode === "DELETED" ? issue.deletedAt : issue.updatedAt)}</span></Link>)}{phase === "ready" && issues.length === 0 ? <div className="tm-empty-state"><span>!</span><strong>{viewMode === "DELETED" ? "삭제된 이슈가 없습니다." : "조건에 맞는 이슈가 없습니다."}</strong><p>{viewMode === "DELETED" ? "삭제한 이슈가 이곳에 표시됩니다." : "검색어, 사이클, 상태 또는 심각도 필터를 변경해보세요."}</p></div> : null}</div><footer className="tm-ledger-pagination"><span>총 {total}건 · 현재 {issues.length}건</span>{nextCursor ? <button type="button" onClick={() => void loadMore()}>다음 페이지</button> : null}</footer>
    </section>
  </section>;
}

export function IssueCreatePage() {
  const navigate = useNavigate();
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const cycleCatalog = useCycleCaseCatalog();
  const [draft, setDraft] = useState(newIssueDraft);
  const [selectedCycleItemIds, setSelectedCycleItemIds] = useState<string[]>([]);
  const [linkModalOpen, setLinkModalOpen] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [creating, setCreating] = useState(false);
  const [createProgress, setCreateProgress] = useState("");
  const [createdIssue, setCreatedIssue] = useState<IssueResource | null>(null);
  const [error, setError] = useState("");

  const addPendingFiles = (files: File[]) => {
    setError("");
    const accepted: File[] = [];
    for (const file of files) {
      if (!file.name.trim() || file.name.includes("/") || file.name.includes("\\")) { setError("안전한 파일 이름을 사용하세요."); continue; }
      const mimeType = resolveAttachmentMimeType(file) as AttachmentResource["mimeType"];
      if (!allowedMimeTypes.has(mimeType)) { setError(`지원하지 않는 파일 형식입니다: ${file.name}`); continue; }
      if (file.size <= 0 || file.size > 50 * 1024 * 1024) { setError(`${file.name}: 파일은 1 byte 이상 50 MB 이하여야 합니다.`); continue; }
      accepted.push(file);
    }
    setPendingFiles((current) => {
      const existing = new Set(current.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
      return [...current, ...accepted.filter((file) => !existing.has(`${file.name}:${file.size}:${file.lastModified}`))];
    });
  };
  const savePendingAttachments = async (issue: IssueResource, files: File[]) => {
    const result = await uploadAttachmentBatch(files, async (file, index, total) => {
      setCreateProgress(`첨부파일 저장 중 ${index + 1}/${total}…`);
      const attachmentKey = mutationKey("issue-attachment");
      await sprint4Api.uploadIssueAttachment(issue.id, { idempotencyKey: attachmentKey, owner: { type: "ISSUE", issueId: issue.id }, filename: file.name, declaredMimeType: resolveAttachmentMimeType(file) as AttachmentResource["mimeType"], declaredSizeBytes: file.size }, file, { idempotencyKey: attachmentKey, actorLabel: "Issue Web UI" });
    });
    const failedFiles = result.failed.map(({ item }) => item);
    setPendingFiles(failedFiles);
    if (failedFiles.length) {
      setError(`이슈 ${issue.key}은 생성됐습니다. 첨부 ${result.completed.length}개 저장, ${failedFiles.length}개 실패 — 실패 파일만 재시도할 수 있습니다. ${apiErrorMessage(result.failed[0]?.error, "")}`.trim());
      return false;
    }
    return true;
  };
  const retryAttachments = async () => {
    if (!createdIssue || pendingFiles.length === 0) return;
    setCreating(true); setError("");
    const saved = await savePendingAttachments(createdIssue, pendingFiles);
    setCreating(false); setCreateProgress("");
    if (saved) void navigate(`/issues/${createdIssue.key}`, appNavigationOptions.createSuccess);
  };
  const createIssue = async () => {
    if (!draft.summary.trim() || !draft.precondition.trim() || !draft.reproductionSteps.trim() || !draft.expectedResult.trim() || !draft.actualResult.trim()) { setError("서머리, 사전 조건, 재현 스텝, 기대 결과, 실제 결과를 모두 입력하세요."); return; }
    setCreating(true); setError(""); setCreateProgress("이슈를 생성하는 중…");
    const idempotencyKey = mutationKey("issue-create");
    let created: IssueResource;
    try {
      created = await sprint4Api.createIssue({
        summary: draft.summary.trim(),
        precondition: draft.precondition.trim(),
        steps: [{ sequence: 1, action: draft.reproductionSteps.trim(), expectedResult: draft.expectedResult.trim() }],
        expectedResult: draft.expectedResult.trim(),
        actualResult: draft.actualResult.trim(),
        severity: draft.severity,
        assigneeLabel: draft.assigneeLabel.trim() || null,
        initialLinks: selectedCycleItemIds.map((cycleItemId) => ({ type: "CYCLE_ITEM" as const, cycleItemId })),
        idempotencyKey,
      }, { idempotencyKey, actorLabel: "Issue Web UI" });
    } catch (requestError) {
      setError(apiErrorMessage(requestError, "이슈를 만들지 못했습니다."));
      setCreating(false); setCreateProgress("");
      return;
    }
    setCreatedIssue(created);
    const saved = await savePendingAttachments(created, pendingFiles);
    if (!saved) {
      setCreating(false); setCreateProgress("");
      return;
    }
    void navigate(`/issues/${created.key}`, appNavigationOptions.createSuccess);
  };

  const changeCycle = (nextCycleId: string) => {
    cycleCatalog.setCycleId(nextCycleId);
    cycleCatalog.setSearch("");
    setSelectedCycleItemIds([]);
  };
  const toggleCycleItem = (item: CycleItem) => setSelectedCycleItemIds((current) => current.includes(item.id)
    ? current.filter((id) => id !== item.id)
    : [...current, item.id]);

  return <section className="tm-issue-page tm-issue-detail-page tm-issue-create-page" aria-labelledby="issue-create-title">
    <nav className="tm-detail-breadcrumb"><Link className="tm-back-button" replace={appNavigationOptions.cancelCreate.replace} to="/issues">← 이슈 목록</Link><span>/</span><strong>새 이슈</strong></nav>
    <main className="tm-issue-detail tm-issue-detail--create">
      <div className="tm-issue-detail__topline"><p><span className="tm-issue-type-icon" aria-hidden="true">!</span><strong id="issue-create-title">새 이슈</strong></p><div>{createProgress ? <span className="tm-saved-message" role="status">{createProgress}</span> : null}<Link className="tm-subtle-button" replace={appNavigationOptions.cancelCreate.replace} to="/issues">취소</Link>{createdIssue ? <><Link className="tm-subtle-button" replace to={`/issues/${createdIssue.key}`}>이슈 상세 열기</Link>{pendingFiles.length ? <button className="tm-primary-button" disabled={creating} type="button" onClick={() => void retryAttachments()}>{creating ? "재시도 중…" : `실패 첨부 ${pendingFiles.length}개 재시도`}</button> : null}</> : <button className="tm-primary-button" disabled={creating} type="button" onClick={() => void createIssue()}>{creating ? "저장 중…" : "이슈 생성"}</button>}</div></div>
      <label className="tm-create-title-field" htmlFor="issue-create-summary"><span>서머리</span><input className="tm-title-input tm-title-input--issue tm-compact-summary-input" id="issue-create-summary" autoFocus value={draft.summary} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} placeholder="문제를 한 문장으로 요약하세요" /></label>
      {error ? <div className="tm-api-state is-error tm-create-error" role="alert"><strong>입력 내용을 확인하세요.</strong><p>{error}</p></div> : null}
      <div className="tm-issue-detail-layout"><div className="tm-issue-description-column"><label className="tm-field tm-field--large"><span>사전 조건</span><textarea rows={3} value={draft.precondition} onChange={(event) => setDraft({ ...draft, precondition: event.target.value })} placeholder="재현 전에 필요한 계정, 데이터, 환경을 입력하세요" /></label><label className="tm-field tm-field--large"><span>재현 스텝</span><textarea rows={5} value={draft.reproductionSteps} onChange={(event) => setDraft({ ...draft, reproductionSteps: event.target.value })} placeholder="문제가 발생하는 순서와 동작을 입력하세요" /></label><div className="tm-issue-result-grid"><label className="tm-field tm-field--expected"><span>기대 결과</span><textarea rows={5} value={draft.expectedResult} onChange={(event) => setDraft({ ...draft, expectedResult: event.target.value })} /></label><label className="tm-field tm-field--actual"><span>실제 결과</span><textarea rows={5} value={draft.actualResult} onChange={(event) => setDraft({ ...draft, actualResult: event.target.value })} /></label></div><IssueConnectionSummary catalog={cycleCatalog} selectedItemIds={selectedCycleItemIds} onOpen={() => setLinkModalOpen(true)} /><section className="tm-create-attachments"><div className="tm-section-heading"><div><h2>첨부파일</h2><p>선택한 파일은 이슈 생성 후 비공개 저장소에 직접 업로드됩니다.</p></div><button className="tm-subtle-button tm-compact-button" type="button" onClick={() => attachmentInputRef.current?.click()}>+ 로컬 파일 선택</button></div><div className="tm-upload-dropzone" role="button" tabIndex={0} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); addPendingFiles(Array.from(event.dataTransfer.files)); }} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") attachmentInputRef.current?.click(); }} onClick={() => attachmentInputRef.current?.click()}><strong>파일을 선택하거나 이곳에 끌어놓으세요</strong><span>여러 파일 선택 가능 · 파일당 최대 50 MB</span></div><input ref={attachmentInputRef} hidden multiple type="file" aria-label="새 이슈 첨부파일 선택" onChange={(event) => { addPendingFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} /><div className="tm-pending-attachment-list">{pendingFiles.map((file) => <article key={`${file.name}:${file.size}:${file.lastModified}`}><LocalAttachmentPreview file={file} /><div><LocalAttachmentFilenamePreview file={file} /><small>{formatBytes(file.size)} · 생성 후 비공개 저장</small></div><button className="tm-subtle-button tm-danger-button tm-compact-button" type="button" aria-label={`${file.name} 선택 취소`} onClick={() => setPendingFiles((current) => current.filter((candidate) => candidate !== file))}>제거</button></article>)}</div></section></div><aside className="tm-detail-rail tm-detail-rail--issue" aria-label="이슈 속성"><div className="tm-rail-heading"><h2>세부 정보</h2><span>신규</span></div><div className="tm-rail-field tm-create-status-field"><span>상태</span><strong>OPEN</strong></div><label className="tm-rail-field"><span>심각도</span><select value={draft.severity} onChange={(event) => setDraft({ ...draft, severity: event.target.value as IssueSeverity })}>{severities.map((value) => <option key={value}>{value}</option>)}</select></label><label className="tm-rail-field"><span>담당자</span><input value={draft.assigneeLabel} onChange={(event) => setDraft({ ...draft, assigneeLabel: event.target.value })} placeholder="미지정" /></label><dl className="tm-metadata"><div><dt>원본</dt><dd>수동</dd></div><div><dt>첨부 저장</dt><dd>비공개 객체 저장소 (R2 / 로컬 MinIO)</dd></div></dl></aside></div>
    </main>
    {linkModalOpen ? <CycleCasePickerModal catalog={cycleCatalog} selectedItemIds={new Set(selectedCycleItemIds)} mode="create" onCycleChange={changeCycle} onToggle={toggleCycleItem} onClose={() => setLinkModalOpen(false)} /> : null}
  </section>;
}

interface IssueDetailData {
  issue: DeletedIssueResource;
  linkedCycleIds: string[];
  links: IssueLinkResource[];
  comments: IssueCommentResource[];
  history: IssueHistoryPage["items"];
  attachments: AttachmentResource[];
  partial: boolean;
}

function PermanentIssueDeleteDialog({ open, issueKey, busy, onClose, onConfirm }: { open: boolean; issueKey: string; busy: boolean; onClose: () => void; onConfirm: () => void }) {
  const [confirmation, setConfirmation] = useState("");
  const close = () => { setConfirmation(""); onClose(); };
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLElement>(open, close, busy);
  if (!open) return null;
  const confirmed = confirmation === issueKey;
  return <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) close(); }}>
    <section ref={dialogRef} className="tm-cycle-modal tm-permanent-delete-modal" role="alertdialog" aria-modal="true" aria-label="이슈 완전 삭제 확인" tabIndex={-1} onKeyDown={onDialogKeyDown}>
      <header><div><span>되돌릴 수 없는 작업</span><h2>이슈 완전 삭제</h2></div><button className="tm-modal-close" type="button" disabled={busy} aria-label="이슈 완전 삭제 닫기" onClick={close}>×</button></header>
      <div className="tm-permanent-delete-modal__body"><p>이슈 본문, 댓글, 첨부파일과 연결 정보가 제거되어 복구할 수 없습니다. 보안 감사 기록은 보존됩니다.</p><label><span>계속하려면 <strong>{issueKey}</strong>을(를) 입력하세요.</span><input autoFocus aria-label="완전 삭제 확인용 이슈 키" value={confirmation} disabled={busy} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" /></label></div>
      <footer><button className="tm-subtle-button" type="button" disabled={busy} onClick={close}>취소</button><button className="tm-primary-button tm-permanent-delete-button" type="button" disabled={!confirmed || busy} onClick={onConfirm}>{busy ? "완전 삭제 중…" : "완전 삭제"}</button></footer>
    </section>
  </div>;
}

export function IssueDetailPage() {
  const { issueKey = "" } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<IssueDetailData | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setPhase("loading"); setError(""); setData(null);
    findIssueByKey(issueKey, controller.signal).then(async (match) => {
      if (!match) throw new Error("이슈를 찾을 수 없습니다.");
      const issue = await sprint4LifecycleApi.getIssue(match.id, controller.signal);
      const parts = await Promise.allSettled([
        sprint4LifecycleApi.listIssueLinks(issue.id, { limit: 100 }, controller.signal), sprint4LifecycleApi.listIssueComments(issue.id, { limit: 100 }, controller.signal), sprint4LifecycleApi.listIssueHistory(issue.id, { limit: 100 }, controller.signal), sprint4Api.listAttachments({ ownerTypes: ["ISSUE"], issueIds: [issue.id], limit: 100 }, controller.signal),
      ]);
      if (controller.signal.aborted) return;
      setData({ issue, linkedCycleIds: match.linkedCycleIds, links: parts[0].status === "fulfilled" ? parts[0].value.items : [], comments: parts[1].status === "fulfilled" ? parts[1].value.items : [], history: parts[2].status === "fulfilled" ? parts[2].value.items : [], attachments: parts[3].status === "fulfilled" ? parts[3].value.items : [], partial: parts.some((part) => part.status === "rejected") });
      setPhase("ready");
    }).catch((requestError: unknown) => { if (!controller.signal.aborted) { setError(apiErrorMessage(requestError, "이슈를 불러오지 못했습니다.")); setPhase("error"); } });
    return () => controller.abort();
  }, [issueKey, revision]);
  return <section className="tm-issue-page tm-issue-detail-page"><nav className="tm-detail-breadcrumb"><Link className="tm-back-button" replace={appNavigationOptions.returnToList.replace} to="/issues">← 이슈 목록</Link><span>/</span><strong>{issueKey}</strong></nav>{phase === "loading" ? <div className="tm-api-state" role="status"><h1>이슈 상세</h1><p>이슈·댓글·첨부·감사 이력을 불러오는 중…</p></div> : null}{phase === "error" ? <div className="tm-api-state is-error" role="alert"><h1>이슈를 불러올 수 없습니다.</h1><p>{error}</p><button type="button" onClick={() => setRevision((value) => value + 1)}>다시 시도</button></div> : null}{data ? <IssueEditor data={data} reload={() => setRevision((value) => value + 1)} onDeleted={() => void navigate("/issues", appNavigationOptions.deleteSuccess)} /> : null}</section>;
}

function IssueEditor({ data, reload, onDeleted }: { data: IssueDetailData; reload: () => void; onDeleted: () => void }) {
  const [draft, setDraft] = useState(data.issue);
  const [reproductionSteps, setReproductionSteps] = useState(() => data.issue.steps.map((step) => step.action).join("\n"));
  const [links, setLinks] = useState(data.links);
  const [linkModalOpen, setLinkModalOpen] = useState(false);
  const [mutation, setMutation] = useState<"idle" | "saving" | "deleting" | "restoring" | "permanent-deleting">("idle");
  const [deletePending, setDeletePending] = useState(false);
  const [permanentDeletePending, setPermanentDeletePending] = useState(false);
  const [linkMutation, setLinkMutation] = useState("");
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const [comment, setComment] = useState("");
  const [commenting, setCommenting] = useState(false);
  const preferredCycleId = data.linkedCycleIds[0] ?? data.issue.reproductionSnapshot?.cycleId ?? "";
  const isDeleted = Boolean(draft.deletedAt);
  const cycleCatalog = useCycleCaseCatalog(preferredCycleId);
  useEffect(() => {
    setDraft(data.issue);
    setReproductionSteps(data.issue.steps.map((step) => step.action).join("\n"));
    setLinks(data.links);
  }, [data.issue, data.links]);
  const linkedCycleItemIds = useMemo(() => new Set(links.map((link) => resolveIssueLinkCycleItemId(link, cycleCatalog.items, draft.reproductionSnapshot)).filter((value): value is string => Boolean(value))), [cycleCatalog.items, draft.reproductionSnapshot, links]);
  const cycleLocked = links.length > 0 && linkedCycleItemIds.size > 0;
  const save = async () => {
    if (isDeleted) return;
    if (!draft.summary.trim() || !draft.precondition.trim() || !reproductionSteps.trim() || !draft.expectedResult.trim() || !draft.actualResult.trim()) {
      setMessage("서머리, 사전 조건, 재현 스텝, 기대 결과, 실제 결과를 모두 입력하세요.");
      return;
    }
    setMutation("saving"); setMessage(""); setConflict(false);
    const originalSteps = data.issue.steps.map((step) => step.action).join("\n").trim();
    try { await sprint4Api.updateIssue(draft.id, { baseResourceVersion: draft.resourceVersion, summary: draft.summary.trim(), precondition: draft.precondition.trim(), ...(reproductionSteps.trim() !== originalSteps ? { steps: [{ sequence: 1, action: reproductionSteps.trim(), expectedResult: draft.expectedResult.trim() }] } : {}), expectedResult: draft.expectedResult.trim(), actualResult: draft.actualResult.trim(), status: draft.status, severity: draft.severity, assigneeLabel: draft.assigneeLabel, reason: "Issue detail web edit" }, { actorLabel: "Issue Web UI" }); setMessage("변경사항이 원장에 저장되었습니다."); reload(); }
    catch (requestError) {
      if (apiErrorCode(requestError) === "VERSION_CONFLICT") {
        setConflict(true);
        setMessage("다른 사용자가 먼저 변경했습니다. 현재 입력은 저장되지 않았습니다.");
      } else setMessage(apiErrorMessage(requestError, "변경사항을 저장하지 못했습니다."));
    }
    finally { setMutation("idle"); }
  };
  const remove = async () => {
    if (isDeleted) return;
    setMutation("deleting"); setMessage("");
    try { await sprint4Api.deleteIssue(draft.id, { baseResourceVersion: draft.resourceVersion, reason: "Issue detail web delete" }, { actorLabel: "Issue Web UI" }); onDeleted(); }
    catch (requestError) { setMessage(apiErrorMessage(requestError, "이슈를 삭제하지 못했습니다.")); setDeletePending(false); setMutation("idle"); }
  };
  const restore = async () => {
    if (!isDeleted) return;
    setMutation("restoring"); setMessage("");
    try {
      await sprint4LifecycleApi.restoreIssue(draft.id, { baseResourceVersion: draft.resourceVersion, reason: "Issue deleted detail restore" }, { idempotencyKey: mutationKey("issue-restore"), actorLabel: "Issue Web UI" });
      setMessage("이슈가 복구되었습니다. 편집 가능한 최신 원장을 불러옵니다.");
      reload();
    } catch (requestError) {
      setMessage(apiErrorMessage(requestError, "이슈를 복구하지 못했습니다."));
    } finally {
      setMutation("idle");
    }
  };
  const permanentlyDelete = async () => {
    if (!isDeleted) return;
    setMutation("permanent-deleting"); setMessage("");
    try {
      await sprint4LifecycleApi.permanentlyDeleteIssue(draft.id, {
        baseResourceVersion: draft.resourceVersion,
        reason: "Issue deleted detail permanent delete",
      }, { idempotencyKey: mutationKey("issue-permanent-delete"), actorLabel: "Issue Web UI" });
      onDeleted();
    } catch (requestError) {
      setMessage(apiErrorMessage(requestError, "이슈를 완전 삭제하지 못했습니다."));
      setPermanentDeletePending(false);
      setMutation("idle");
    }
  };
  const addComment = async () => {
    if (isDeleted || !comment.trim()) return;
    setCommenting(true); setMessage(""); const idempotencyKey = mutationKey("issue-comment");
    try { await sprint4Api.createIssueComment(draft.id, { body: comment.trim(), idempotencyKey }, { idempotencyKey, actorLabel: "Issue Web UI" }); setComment(""); reload(); }
    catch (requestError) { setMessage(apiErrorMessage(requestError, "댓글을 저장하지 못했습니다.")); }
    finally { setCommenting(false); }
  };
  const addLink = async (item: CycleItem) => {
    if (isDeleted) return;
    setLinkMutation(item.id); setMessage("");
    const idempotencyKey = mutationKey("issue-link");
    try {
      const link = await sprint4Api.createIssueLink(draft.id, { idempotencyKey, target: { type: "CYCLE_ITEM", cycleItemId: item.id } }, { idempotencyKey, actorLabel: "Issue Web UI" });
      setLinks((current) => [...current, link]);
      setMessage(`${item.caseKey} 테스트케이스를 연결했습니다.`);
    } catch (requestError) { setMessage(apiErrorMessage(requestError, "테스트케이스를 연결하지 못했습니다.")); }
    finally { setLinkMutation(""); }
  };
  const unlink = async (link: IssueLinkResource) => {
    if (isDeleted) return;
    setLinkMutation(link.id); setMessage("");
    try {
      const response = await sprint4Api.deleteIssueLink(draft.id, link.id, { baseIssueResourceVersion: draft.resourceVersion, reason: "Issue detail cycle case unlink" }, { actorLabel: "Issue Web UI" });
      setLinks((current) => current.filter((candidate) => candidate.id !== link.id));
      setDraft((current) => ({ ...current, resourceVersion: response.issueResourceVersion }));
      setMessage("사이클 테스트케이스 연결을 해제했습니다.");
    } catch (requestError) { setMessage(apiErrorMessage(requestError, "연결을 해제하지 못했습니다.")); if (apiErrorCode(requestError) === "VERSION_CONFLICT") reload(); }
    finally { setLinkMutation(""); }
  };
  const changeCycle = (nextCycleId: string) => { cycleCatalog.setCycleId(nextCycleId); cycleCatalog.setSearch(""); };

  return <main className={`tm-issue-detail tm-issue-detail--create${isDeleted ? " is-readonly-deleted" : ""}`}>
    <DeleteConfirmDialog open={deletePending} title="이슈 삭제" entityName={`${draft.key} · ${draft.summary}`} description="이슈는 목록에서 숨겨집니다. 연결된 사이클·테스트케이스와 변경 이력은 보존됩니다." busy={mutation === "deleting"} onClose={() => setDeletePending(false)} onConfirm={() => void remove()} />
    <PermanentIssueDeleteDialog open={permanentDeletePending} issueKey={draft.key} busy={mutation === "permanent-deleting"} onClose={() => setPermanentDeletePending(false)} onConfirm={() => void permanentlyDelete()} />
    {data.partial ? <div className="tm-partial-state" role="status">일부 연결 데이터가 지연되고 있습니다. 이슈 본문은 최신 원장 값입니다. <button type="button" onClick={reload}>다시 불러오기</button></div> : null}
    {isDeleted ? <div className="tm-deleted-readonly-banner" role="status"><span>삭제됨</span><div><strong>이 이슈는 읽기 전용입니다.</strong><p>기존 생성·상세 폼에서 저장된 값과 이력을 확인한 뒤 복구하거나 완전 삭제할 수 있습니다.</p></div></div> : null}
    <div className="tm-issue-detail__topline"><p><span className="tm-issue-type-icon" aria-hidden="true">!</span><strong>{draft.key}</strong>{isDeleted ? <span className="tm-deleted-state-badge">삭제됨</span> : null}</p><div>{message ? <span className="tm-saved-message" role={conflict ? "alert" : "status"}>{message}</span> : null}{conflict ? <button className="tm-subtle-button" type="button" onClick={() => { setConflict(false); setMessage(""); reload(); }}>최신 원장 다시 불러오기</button> : null}{isDeleted ? <><button className="tm-subtle-button" disabled={mutation !== "idle"} type="button" onClick={() => void restore()}>{mutation === "restoring" ? "복구 중…" : "복구"}</button><button className="tm-subtle-button tm-danger-button" disabled={mutation !== "idle"} type="button" onClick={() => setPermanentDeletePending(true)}>완전 삭제</button></> : <><button className="tm-subtle-button tm-danger-button" disabled={mutation !== "idle" || Boolean(linkMutation)} type="button" onClick={() => setDeletePending(true)}>삭제</button><button className="tm-primary-button" disabled={mutation !== "idle" || Boolean(linkMutation)} type="button" onClick={() => void save()}>{mutation === "saving" ? "저장 중…" : "변경사항 저장"}</button></>}</div></div>
    <label className="tm-create-title-field" htmlFor="issue-detail-summary"><span>서머리</span><input className="tm-title-input tm-title-input--issue tm-compact-summary-input" id="issue-detail-summary" value={draft.summary} disabled={isDeleted} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} /></label>
    <div className="tm-issue-detail-layout">
      <div className="tm-issue-description-column">
        <label className="tm-field tm-field--large"><span>사전 조건</span><textarea rows={3} value={draft.precondition} disabled={isDeleted} onChange={(event) => setDraft({ ...draft, precondition: event.target.value })} /></label>
        <label className="tm-field tm-field--large"><span>재현 스텝</span><textarea aria-label="재현 스텝" rows={5} value={reproductionSteps} disabled={isDeleted} onChange={(event) => setReproductionSteps(event.target.value)} /></label>
        <div className="tm-issue-result-grid"><label className="tm-field tm-field--expected"><span>기대 결과</span><textarea rows={5} value={draft.expectedResult} disabled={isDeleted} onChange={(event) => setDraft({ ...draft, expectedResult: event.target.value })} /></label><label className="tm-field tm-field--actual"><span>실제 결과</span><textarea rows={5} value={draft.actualResult} disabled={isDeleted} onChange={(event) => setDraft({ ...draft, actualResult: event.target.value })} /></label></div>
        {draft.reproductionSnapshot ? <SnapshotPanel issue={draft} /> : null}
        <LinkedResources links={links} snapshot={draft.reproductionSnapshot} cycles={cycleCatalog.cycles} items={cycleCatalog.items} pendingLinkId={linkMutation} readOnly={isDeleted} onAdd={() => setLinkModalOpen(true)} onUnlink={unlink} />
        <AttachmentPanel issue={draft} attachments={data.attachments} reload={reload} readOnly={isDeleted} />
        <section className="tm-comments"><div className="tm-section-heading"><div><h2>댓글</h2><p>append-only 원장 · {data.comments.length} comments</p></div></div>{isDeleted ? <p className="tm-readonly-section-note">삭제된 이슈에서는 기존 댓글만 확인할 수 있습니다.</p> : <div className="tm-comment-composer"><textarea className="tm-comment-composer__input" aria-label="새 댓글" rows={3} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="조사 내용이나 재검증 결과를 남기세요." /><button className="tm-primary-button" disabled={commenting || !comment.trim()} type="button" onClick={() => void addComment()}>{commenting ? "저장 중…" : "댓글 추가"}</button></div>}{data.comments.map((item) => <article key={item.id}><header><strong>{item.authorLabel ?? "알 수 없는 사용자"}</strong><time>{formatIssueTime(item.createdAt)}</time></header><p>{item.body}</p></article>)}</section>
        <HistoryPanel history={data.history} />
      </div>
      <aside className="tm-detail-rail tm-detail-rail--issue" aria-label="이슈 속성"><div className="tm-rail-heading"><h2>세부 정보</h2><span>v{draft.resourceVersion}</span></div><label className="tm-rail-field"><span>상태</span><select value={draft.status} disabled={isDeleted} onChange={(event) => setDraft({ ...draft, status: event.target.value as IssueStatus })}>{statuses.map((value) => <option key={value}>{value}</option>)}</select></label><label className="tm-rail-field"><span>심각도</span><select value={draft.severity} disabled={isDeleted} onChange={(event) => setDraft({ ...draft, severity: event.target.value as IssueSeverity })}>{severities.map((value) => <option key={value}>{value}</option>)}</select></label><label className="tm-rail-field"><span>담당자</span><input value={draft.assigneeLabel ?? ""} disabled={isDeleted} onChange={(event) => setDraft({ ...draft, assigneeLabel: event.target.value || null })} /></label><dl className="tm-metadata"><div><dt>생성</dt><dd>{formatIssueTime(draft.createdAt)}</dd></div><div><dt>수정</dt><dd>{formatIssueTime(draft.updatedAt)}</dd></div><div><dt>삭제</dt><dd>{formatIssueTime(draft.deletedAt)}</dd></div><div><dt>원본</dt><dd>{draft.origin.type === "EXECUTION_ATTEMPT" ? "실행 결과" : "수동"}</dd></div></dl></aside>
    </div>
    {!isDeleted && linkModalOpen ? <CycleCasePickerModal catalog={cycleCatalog} selectedItemIds={linkedCycleItemIds} mode="edit" cycleLocked={cycleLocked} pendingItemId={linkMutation} onCycleChange={changeCycle} onToggle={(item) => void addLink(item)} onClose={() => setLinkModalOpen(false)} /> : null}
  </main>;
}

function SnapshotPanel({ issue }: { issue: IssueResource }) {
  const snapshot = issue.reproductionSnapshot;
  if (!snapshot) return null;
  return <section className="tm-snapshot-panel"><div className="tm-section-heading"><div><h2>생성 시점 실행 스냅샷</h2><p>이슈 생성 당시의 실행 근거는 변경되지 않습니다.</p></div><span>{snapshot.sourceAttemptStatus}</span></div><dl><div><dt>플랫폼</dt><dd>{snapshot.platform}</dd></div><div><dt>실행 결과</dt><dd>{snapshot.sourceAttemptStatus}</dd></div><div><dt>고정 케이스 버전</dt><dd>v{snapshot.pinnedVersion}</dd></div><div><dt>근거</dt><dd>사이클 실행 시점 스냅샷</dd></div></dl></section>;
}

export function LinkedResources({ links, snapshot, cycles, items, pendingLinkId, readOnly = false, onAdd, onUnlink }: { links: IssueLinkResource[]; snapshot: IssueResource["reproductionSnapshot"]; cycles: CycleListItem[]; items: CycleItem[]; pendingLinkId: string; readOnly?: boolean; onAdd: () => void; onUnlink: (link: IssueLinkResource) => Promise<void> }) {
  const cycle = cycles.find((candidate) => candidate.id === (snapshot?.cycleId ?? items[0]?.cycleId));
  return <section className="tm-linked-section"><div className="tm-section-heading"><div><h2>관련 테스트케이스</h2><p>이 이슈를 어느 사이클의 어떤 테스트케이스에서 추적하는지 보여줍니다.</p></div><div className="tm-issue-link-heading-actions"><span className="tm-scope-chip">{links.length}개</span>{!readOnly ? <button className="tm-subtle-button tm-compact-button" type="button" onClick={onAdd}>+ 연결 추가</button> : null}</div></div>{cycle ? <div className="tm-bidirectional-link"><Link draggable={false} to={`/cycles/${cycle.id}/execute`}>{cycle.name} 실행 화면 ↗</Link>{snapshot ? <span>{snapshot.platform} · {snapshot.sourceAttemptStatus}</span> : <span>사이클 연결</span>}</div> : null}<div className="tm-ledger-links tm-issue-linked-list">{links.map((link) => {
    const itemId = resolveIssueLinkCycleItemId(link, items, snapshot);
    const item = items.find((candidate) => candidate.id === itemId);
    const attemptId = link.target.executionAttemptId;
    const execution = item?.platformExecutions.find((candidate) => candidate.latestAttempt?.id === attemptId);
    const relation = link.target.type === "EXECUTION_ATTEMPT" ? `${execution?.platform ?? snapshot?.platform ?? "실행"} 실행 근거` : "사이클 테스트케이스";
    return <article className={`tm-linked-evidence-row${link.target.type === "EXECUTION_ATTEMPT" ? " is-execution-evidence" : ""}${readOnly ? " is-readonly" : ""}`} key={link.id}><span>{relation}</span><div>{item ? <><Link draggable={false} to={`/test-cases/${item.caseKey}`}>{item.pinnedVersion.summary}</Link><small>{cycle?.name ?? "사이클"} · {item.caseKey} · {item.workItemKey}</small></> : <><strong>연결된 테스트케이스</strong><small>{cycle?.name ?? "사이클 정보를 확인하는 중"}</small></>}</div><small>{formatIssueTime(link.createdAt)}</small>{!readOnly ? <button className="tm-subtle-button tm-danger-button tm-compact-button" disabled={Boolean(pendingLinkId)} type="button" onClick={() => void onUnlink(link)}>{pendingLinkId === link.id ? "해제 중…" : "연결 해제"}</button> : <span className="tm-readonly-label">읽기 전용</span>}</article>;
  })}{links.length === 0 ? readOnly ? <div className="tm-issue-connection-empty is-readonly"><span aria-hidden="true">T</span><span><strong>연결된 테스트케이스가 없습니다.</strong><small>삭제 시점에 연결된 테스트케이스가 없었습니다.</small></span></div> : <button className="tm-issue-connection-empty" type="button" onClick={onAdd}><span aria-hidden="true">T</span><span><strong>연결된 테스트케이스가 없습니다.</strong><small>클릭해서 사이클과 테스트케이스를 선택하세요.</small></span><i aria-hidden="true">›</i></button> : null}</div></section>;
}

function AttachmentPanel({ issue, attachments, reload, readOnly = false }: { issue: IssueResource; attachments: AttachmentResource[]; reload: () => void; readOnly?: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [upload, setUpload] = useState<{ phase: "idle" | "validating" | "uploading" | "error"; progress: number; message: string }>({ phase: "idle", progress: 0, message: "" });
  const [deleting, setDeleting] = useState<string | null>(null);
  const uploadFile = useCallback(async (file: File) => {
    if (readOnly) return;
    setUpload({ phase: "validating", progress: 10, message: "파일 이름·크기·MIME를 검사하는 중…" });
    if (!file.name.trim() || file.name.includes("/") || file.name.includes("\\")) { setUpload({ phase: "error", progress: 0, message: "안전한 파일 이름을 사용하세요." }); return; }
    const mimeType = resolveAttachmentMimeType(file) as AttachmentResource["mimeType"];
    if (!allowedMimeTypes.has(mimeType)) { setUpload({ phase: "error", progress: 0, message: `지원하지 않는 MIME입니다: ${mimeType || "unknown"}` }); return; }
    if (file.size <= 0 || file.size > 50 * 1024 * 1024) { setUpload({ phase: "error", progress: 0, message: "파일은 1 byte 이상 50 MB 이하여야 합니다." }); return; }
    const idempotencyKey = mutationKey("issue-attachment"); setUpload({ phase: "uploading", progress: 35, message: "파일을 비공개 저장소에 직접 업로드하는 중…" });
    try { await sprint4Api.uploadIssueAttachment(issue.id, { idempotencyKey, owner: { type: "ISSUE", issueId: issue.id }, filename: file.name, declaredMimeType: mimeType, declaredSizeBytes: file.size }, file, { idempotencyKey, actorLabel: "Issue Web UI" }); setUpload({ phase: "idle", progress: 100, message: "첨부파일이 저장되었습니다." }); reload(); }
    catch (requestError) { setUpload({ phase: "error", progress: 0, message: apiErrorMessage(requestError, "첨부파일 업로드에 실패했습니다. 다시 시도하세요.") }); }
  }, [issue.id, readOnly, reload]);
  const onDrop = (event: DragEvent<HTMLDivElement>) => { event.preventDefault(); if (readOnly) return; const file = event.dataTransfer.files[0]; if (file) void uploadFile(file); };
  const download = async (attachment: AttachmentResource) => { setUpload({ phase: "uploading", progress: 50, message: `${attachment.filename}을(를) private proxy로 받는 중…` }); try { downloadBlob(await sprint4Api.downloadAttachmentContent(attachment.id), attachment.filename); setUpload({ phase: "idle", progress: 100, message: "다운로드가 시작되었습니다." }); } catch (requestError) { setUpload({ phase: "error", progress: 0, message: apiErrorMessage(requestError, "다운로드에 실패했습니다.") }); } };
  const remove = async (attachment: AttachmentResource) => { if (readOnly) return; setDeleting(attachment.id); try { await sprint4Api.deleteAttachment(attachment.id, { baseResourceVersion: attachment.resourceVersion, reason: "Issue detail attachment delete" }, { actorLabel: "Issue Web UI" }); reload(); } catch (requestError) { setUpload({ phase: "error", progress: 0, message: apiErrorMessage(requestError, "첨부파일 삭제에 실패했습니다. reconcile 상태를 확인하세요.") }); } finally { setDeleting(null); } };
  return <section className="tm-attachments tm-create-attachments" aria-labelledby="attachment-heading">
    <div className="tm-section-heading"><div><h2 id="attachment-heading">첨부파일</h2><p>{readOnly ? "삭제 시점에 보존된 첨부파일을 다운로드할 수 있습니다." : "내 컴퓨터에서 선택한 파일은 비공개 저장소에 직접 업로드됩니다."}</p></div>{!readOnly ? <button className="tm-subtle-button tm-compact-button" type="button" onClick={() => inputRef.current?.click()}>+ 로컬 파일 선택</button> : <span className="tm-readonly-label">읽기 전용</span>}</div>
    {!readOnly ? <><div className="tm-upload-dropzone" role="button" tabIndex={0} onDragOver={(event) => event.preventDefault()} onDrop={onDrop} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") inputRef.current?.click(); }} onClick={() => inputRef.current?.click()}><strong>내 컴퓨터에서 파일 선택</strong><span>끌어놓기도 가능 · 최대 50 MB · 서버를 거치지 않고 직접 저장</span></div><input ref={inputRef} hidden type="file" aria-label="이슈 첨부파일 선택" onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadFile(file); event.target.value = ""; }} /></> : null}
    {upload.message ? <div className={`tm-upload-state is-${upload.phase}`} role={upload.phase === "error" ? "alert" : "status"}><progress max={100} value={upload.progress} /><span>{upload.message}</span>{upload.phase === "error" ? <button className="tm-subtle-button tm-compact-button" type="button" onClick={() => inputRef.current?.click()}>다시 선택</button> : null}</div> : null}
    <div className="tm-attachment-list">{attachments.map((attachment) => {
      const isDeleting = deleting === attachment.id;
      return <article className={isDeleting ? "is-deleting" : undefined} aria-busy={isDeleting} key={attachment.id}>
        <PrivateAttachmentPreview attachment={attachment} loadBlob={loadPrivateAttachmentBlob} />
        <div><PrivateAttachmentFilenamePreview attachment={attachment} loadBlob={loadPrivateAttachmentBlob} /><small>{formatBytes(attachment.sizeBytes)} · {attachment.mimeType} · 비공개 저장</small></div>
        <div className="tm-attachment-actions">
          <button className="tm-subtle-button tm-compact-button" disabled={isDeleting} type="button" aria-label={`${attachment.filename} 다운로드`} onClick={() => void download(attachment)}>다운로드</button>
          {!readOnly ? <button className="tm-subtle-button tm-danger-button tm-compact-button" disabled={isDeleting} type="button" aria-label={`${attachment.filename} 삭제`} onClick={() => void remove(attachment)}>{isDeleting ? "삭제 중…" : "삭제"}</button> : null}
        </div>
      </article>;
    })}{attachments.length === 0 ? <p className="tm-muted">저장된 첨부파일이 없습니다.</p> : null}</div>
  </section>;
}

function HistoryPanel({ history }: { history: IssueHistoryPage["items"] }) {
  return <section className="tm-history"><div className="tm-section-heading"><div><h2>변경 이력</h2><p>{history.length}건의 활동</p></div></div><ol>{history.map((event) => <li key={event.id}><header><strong>{issueHistoryEventLabel(event.eventType)}</strong><time>{formatIssueTime(event.occurredAt)}</time></header><small>{event.actorLabel ?? "시스템"}</small>{event.changes.some((change) => shouldDisplayIssueHistoryField(change.field)) ? <ul>{event.changes.filter((change) => shouldDisplayIssueHistoryField(change.field)).map((change, index) => <li key={change.field + "-" + index}><b>{issueHistoryFieldLabel(change.field)}</b><span>{formatIssueHistoryValue(change.field, change.before)} → {formatIssueHistoryValue(change.field, change.after)}</span></li>)}</ul> : null}</li>)}</ol></section>;
}
