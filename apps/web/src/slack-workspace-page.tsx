import type { SlackJobPage, SlackWorkItemDetail } from "@tc/api-client";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { appNavigationOptions } from "./navigation-policy";
import {
  dedupeJobs,
  dedupeWorkItems,
  formatSlackDate,
  formatSlackTimestamp,
  isSlackConflict,
  listSlackWorkItemPage,
  slackApi,
  slackErrorMessage,
  slackMutationKey,
  type SlackJob,
  type SlackWorkItem,
} from "./slack-service";

const PARSE_FIELDS = [
  ["developmentItem", "개발 항목"],
  ["requestedMenu", "요청 메뉴"],
  ["featureDescription", "기능 설명"],
  ["requestDetails", "요청 상세"],
] as const;

type WorkItemStatus = SlackWorkItem["status"];
type LedgerPhase = "loading" | "ready" | "error";

function parseTimestamp(value: string) {
  const numeric = Number.parseFloat(value);
  return Number.isFinite(numeric) ? numeric : Number.POSITIVE_INFINITY;
}

function readableResult(result: SlackWorkItemDetail["parse"] extends infer Parse
  ? Parse extends { result: infer Result }
    ? Result
    : never
  : never) {
  return (result ?? {}) as Record<string, unknown>;
}

function stringArray(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function displayValue(value: unknown) {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

const workItemStatusLabels: Record<WorkItemStatus, string> = {
  ACTIVE: "처리 중",
  ROOT_PENDING: "원문 확인 중",
  DELETED: "삭제됨",
  ARCHIVED: "보관됨",
};

const revisionKindLabels = {
  CREATED: "작성됨",
  EDITED: "수정됨",
  DELETED: "삭제됨",
} as const;

const parseStatusLabels: Record<string, string> = {
  PENDING: "분석 대기",
  PROCESSING: "분석 중",
  COMPLETE: "분석 완료",
  COMPLETED: "분석 완료",
  NEEDS_INPUT: "추가 입력 필요",
  FAILED: "분석 실패",
};

const jobTypeLabels: Record<string, string> = {
  QUIET_WINDOW_PARSE: "대화 종료 후 요청 분석",
  THREAD_SYNC: "Slack 대화 동기화",
  ROOT_MESSAGE_SYNC: "원문 메시지 동기화",
};

function readableParseStatus(status: string | null | undefined) {
  if (!status) return "결과 없음";
  return parseStatusLabels[status] ?? status.replaceAll("_", " ");
}

function sourceRevisionSummary(revisionId: string, revisions: SlackWorkItemDetail["revisions"]) {
  const revision = revisions.find((candidate) => candidate.id === revisionId);
  if (!revision) return "관련 메시지 변경본";
  const body = revision.body?.trim();
  const excerpt = body ? ` · ${body.slice(0, 42)}${body.length > 42 ? "…" : ""}` : "";
  return `r${revision.revision} · ${revisionKindLabels[revision.kind]}${excerpt}`;
}

function WorkItemState({ phase, error, retry }: { phase: LedgerPhase; error: string; retry: () => void }) {
  if (phase === "loading") return <div className="tm-api-state" role="status">Slack work item 원장을 불러오는 중…</div>;
  if (phase === "error") return <div className="tm-api-state is-error" role="alert"><strong>Slack 원장을 불러올 수 없습니다.</strong><p>{error}</p><button type="button" onClick={retry}>다시 시도</button></div>;
  return null;
}

export function SlackWorkItemsPage() {
  const [items, setItems] = useState<SlackWorkItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [phase, setPhase] = useState<LedgerPhase>("loading");
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [partial, setPartial] = useState(false);
  const [search, setSearch] = useState("");
  const [channelId, setChannelId] = useState("");
  const [status, setStatus] = useState<"" | WorkItemStatus>("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading");
    setError("");
    setPartial(false);
    void listSlackWorkItemPage({ limit: 50, ...(channelId.trim() ? { channelId: channelId.trim() } : {}) }, null, controller.signal)
      .then((page) => {
        setItems(dedupeWorkItems(page.items));
        setCursor(page.nextCursor ?? null);
        setPhase("ready");
      })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted) return;
        setError(slackErrorMessage(requestError, "Slack work item 목록을 불러오지 못했습니다."));
        setPhase("error");
      });
    return () => controller.abort();
  }, [channelId, revision]);

  const visibleItems = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return items.filter((item) => {
      if (status && item.status !== status) return false;
      if (!needle) return true;
      return [item.id, item.workspaceId, item.channelId, item.rootMessageTs, item.threadId]
        .some((value) => value.toLocaleLowerCase().includes(needle));
    });
  }, [items, search, status]);

  const loadMore = async () => {
    if (!cursor) return;
    setLoadingMore(true);
    setPartial(false);
    setError("");
    try {
      const page = await listSlackWorkItemPage({ limit: 50, ...(channelId.trim() ? { channelId: channelId.trim() } : {}) }, cursor);
      setItems((current) => dedupeWorkItems([...current, ...page.items]));
      setCursor(page.nextCursor ?? null);
    } catch (requestError) {
      setPartial(true);
      setError(slackErrorMessage(requestError, "다음 cursor를 불러오지 못했습니다."));
    } finally {
      setLoadingMore(false);
    }
  };

  return <section className="tm-dashboard-page tm-slack-workspace" aria-labelledby="slack-work-items-title">
    <header className="tm-page-header"><div><h1 id="slack-work-items-title">Slack Work Items</h1><p>Slack에서 수집한 요청을 대화 단위로 모아 처리 상태와 분석 결과를 확인합니다.</p></div><Link className="tm-subtle-button" to="/integrations">연동 설정</Link></header>
    <div className="tm-slack-filter-bar" aria-label="Slack work item 검색과 필터">
      <label><span>검색</span><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="ID, channel, Slack ts" /></label>
      <label><span>Channel ID</span><input value={channelId} onChange={(event) => setChannelId(event.target.value)} placeholder="C012ABCDEF" /></label>
      <label><span>상태</span><select value={status} onChange={(event) => setStatus(event.target.value as "" | WorkItemStatus)}><option value="">전체</option><option value="ACTIVE">ACTIVE</option><option value="ROOT_PENDING">ROOT_PENDING</option><option value="DELETED">DELETED</option><option value="ARCHIVED">ARCHIVED</option></select></label>
      <button type="button" onClick={() => setRevision((value) => value + 1)}>새로고침</button>
    </div>
    <WorkItemState phase={phase} error={error} retry={() => setRevision((value) => value + 1)} />
    {partial ? <div className="tm-partial-state" role="status"><strong>부분 로드 상태</strong><span>{error} 기존 행은 유지했습니다.</span></div> : null}
    {phase === "ready" ? <section className="tm-slack-work-item-list" aria-label="Slack work item 목록">
      <header><span>요청 {visibleItems.length}건</span><small>같은 Slack 대화는 하나의 요청으로 표시됩니다.</small></header>
      {visibleItems.map((item) => <Link className="tm-slack-work-item-row" to={`/slack-work-items/${item.id}`} key={item.id}>
        <span className={`tm-ledger-state is-${item.status.toLowerCase()}`}>{workItemStatusLabels[item.status]}</span>
        <div><strong>{item.channelId} 채널의 Slack 요청</strong><small>원문 수신 {formatSlackTimestamp(item.rootMessageTs)}</small></div>
        <dl><div><dt>대화</dt><dd>{item.threadId ? "스레드 연결됨" : "원문만 수신"}</dd></div><div><dt>요청 분석</dt><dd>{item.latestParseId ? "분석 결과 있음" : "분석 대기"}</dd></div></dl>
        <time dateTime={item.updatedAt}>{formatSlackDate(item.updatedAt)}</time>
      </Link>)}
      {visibleItems.length === 0 ? <div className="tm-slack-empty"><strong>조건에 맞는 Slack work item이 없습니다.</strong><p>allowlist와 event 수신 상태를 확인하거나 검색 조건을 지워보세요.</p></div> : null}
      {cursor ? <button className="tm-load-more" disabled={loadingMore} type="button" onClick={() => void loadMore()}>{loadingMore ? "다음 cursor 로드 중…" : "다음 cursor 불러오기"}</button> : null}
    </section> : null}
  </section>;
}

interface SlackWorkItemDetailViewProps {
  detail: SlackWorkItemDetail;
  jobs: SlackJob[];
  partial?: boolean;
  mutationError?: string;
  conflict?: boolean;
  syncing?: boolean;
  onSync?: () => void;
}

export function SlackWorkItemDetailView({ detail, jobs, partial = false, mutationError = "", conflict = false, syncing = false, onSync }: SlackWorkItemDetailViewProps) {
  const { workItem, thread, messages, revisions, parse } = detail;
  const orderedMessages = [...messages].sort((left, right) => parseTimestamp(left.messageTs) - parseTimestamp(right.messageTs));
  const revisionsByMessage = new Map<string, typeof revisions>();
  for (const revision of revisions) {
    const group = revisionsByMessage.get(revision.messageId) ?? [];
    group.push(revision);
    revisionsByMessage.set(revision.messageId, group);
  }
  for (const group of revisionsByMessage.values()) {
    group.sort((left, right) => left.sourceEventTime - right.sourceEventTime || left.revision - right.revision);
  }
  const result = readableResult(parse?.result ?? null);
  const missingFields = new Set(stringArray(result.missingFields));
  const confidence = typeof result.confidence === "number" ? result.confidence : null;
  const sourceRevisions = stringArray(result.sourceMessageRevisions);
  const activeJobs = dedupeJobs(jobs).filter((job) => job.workItemId === workItem.id);
  const quietJobs = activeJobs.filter((job) => job.jobType === "QUIET_WINDOW_PARSE");
  const syncJobs = activeJobs.filter((job) => job.jobType !== "QUIET_WINDOW_PARSE");

  return <>
    {partial ? <div className="tm-partial-state" role="status">상세 원장은 표시했지만 job 상태 일부를 불러오지 못했습니다.</div> : null}
    {conflict ? <div className="tm-conflict-state" role="alert"><strong>동기화 충돌</strong><span>다른 작업이 먼저 원장을 갱신했습니다. 새로고침 후 재시도하세요.</span></div> : null}
    {mutationError ? <div className="tm-api-state is-error" role="alert">{mutationError}</div> : null}
    <section className="tm-slack-detail-summary" aria-label="Slack work item 요약">
      <article><span>요청 상태</span><strong>{workItemStatusLabels[workItem.status]}</strong><small>{workItem.channelId} 채널</small></article>
      <article><span>Slack 대화</span><strong>{thread.messageCount}개 메시지</strong><small>{thread.rootResolved ? "원문 확인 완료" : "원문 확인 중"}</small></article>
      <article><span>요청 분석</span><strong>{readableParseStatus(parse?.status ?? thread.parseState)}</strong><small>{parse?.openQuestions.length ? `확인 질문 ${parse.openQuestions.length}개` : "추가 질문 없음"}</small></article>
      <article><span>최근 변경</span><strong>{formatSlackDate(thread.latestChangedAt)}</strong><small>동기화 {formatSlackDate(thread.lastSyncedAt)}</small></article>
    </section>
    <div className="tm-slack-detail-grid">
      <section className="tm-dashboard-widget tm-slack-conversation" aria-labelledby="slack-thread-title"><header><div><h2 id="slack-thread-title">Slack 대화</h2><p>원문과 답글, 수정 이력을 시간순으로 표시합니다.</p></div></header>
        {orderedMessages.map((message) => {
          const messageRevisions = revisionsByMessage.get(message.id) ?? [];
          const original = messageRevisions.find((revision) => revision.kind === "CREATED") ?? messageRevisions[0];
          return <article className={`tm-slack-message${message.deleted ? " is-deleted" : ""}`} key={message.id}>
            <header><span className={`tm-ledger-state is-${message.position.toLowerCase()}`}>{message.position === "ROOT" ? "원문" : "답글"}</span><div><strong>{original?.authorId ? `작성자 ${original.authorId}` : "작성자 미상"}</strong><small>변경 이력 {messageRevisions.length}건</small></div><time>{formatSlackTimestamp(message.messageTs)}</time></header>
            <div className="tm-slack-original"><span>최초 수신 내용</span><p>{original?.body ?? (message.deleted ? "삭제 이전 원문이 수신되지 않았습니다." : "원문이 비어 있습니다.")}</p><small>{formatSlackDate(original?.observedAt ?? message.firstObservedAt)} 수신</small></div>
            <ol className="tm-slack-revision-list" aria-label={`${message.messageTs} 변경 이력`}>{messageRevisions.map((revision) => <li className={`is-${revision.kind.toLowerCase()}`} id={`revision-${revision.id}`} key={revision.id}><span>r{revision.revision} · {revisionKindLabels[revision.kind]}</span><p>{revision.kind === "DELETED" ? "Slack에서 삭제됨 — 이전 내용은 변경 이력에 보존됩니다." : revision.body ?? "본문 없음"}</p><small>{formatSlackDate(revision.occurredAt)} · {revision.authorId ? `작성자 ${revision.authorId}` : "작성자 미상"}</small></li>)}</ol>
          </article>;
        })}
        {orderedMessages.length === 0 ? <div className="tm-slack-empty"><strong>저장된 root/reply가 없습니다.</strong><p>reply 선도착 상태라면 thread sync로 누락 root를 보완할 수 있습니다.</p></div> : null}
      </section>
      <aside className="tm-slack-detail-aside">
        <section className="tm-dashboard-widget tm-slack-parse" aria-labelledby="slack-parse-title"><header><div><h2 id="slack-parse-title">요청 정보</h2><p>{readableParseStatus(parse?.status)}</p></div>{confidence === null ? null : <strong>{Math.round(confidence * 100)}%</strong>}</header>
          {parse ? <><dl>{PARSE_FIELDS.map(([field, label]) => { const value = displayValue(result[field]); const missing = missingFields.has(field) || value === null; return <div className={missing ? "is-missing" : ""} key={field}><dt>{label}</dt><dd>{missing ? <span>추가 입력 필요</span> : value}</dd></div>; })}</dl><div className="tm-slack-parse-source"><strong>분석에 사용한 메시지</strong>{sourceRevisions.length ? <ul>{sourceRevisions.map((revisionId) => <li key={revisionId}><a href={`#revision-${revisionId}`}>{sourceRevisionSummary(revisionId, revisions)}</a></li>)}</ul> : <p>연결된 메시지가 없습니다.</p>}{parse.openQuestions.length ? <><strong>확인이 필요한 내용</strong><ul>{parse.openQuestions.map((question, index) => <li key={`${index}-${question}`}>{question}</li>)}</ul></> : null}</div></> : <div className="tm-slack-empty"><strong>요청 분석을 기다리고 있습니다.</strong><p>대화가 잠시 멈추면 최신 메시지를 기준으로 분석을 시작합니다.</p></div>}
        </section>
        <section className="tm-dashboard-widget tm-slack-sync" aria-labelledby="slack-sync-title"><header><div><h2 id="slack-sync-title">동기화 상태</h2><p>Slack 대화 수집과 분석 작업을 확인합니다.</p></div><button disabled={syncing || !onSync} type="button" onClick={onSync}>{syncing ? "동기화 중…" : "대화 다시 동기화"}</button></header><dl className="tm-contract-grid"><span>동기화 위치</span><strong>{thread.syncCursor ? "저장됨" : "첫 동기화 대기"}</strong><span>마지막 동기화</span><strong>{formatSlackDate(thread.lastSyncedAt)}</strong><span>분석 예정</span><strong>{formatSlackDate(quietJobs[0]?.runAfter)}</strong><span>분석 상태</span><strong>{quietJobs[0] ? readableParseStatus(quietJobs[0].state) : "예약 없음"}</strong></dl><div className="tm-slack-job-list">{[...quietJobs, ...syncJobs].map((job) => <article key={job.id}><span className={`tm-ledger-state is-${job.state.toLowerCase()}`}>{readableParseStatus(job.state)}</span><div><strong>{jobTypeLabels[job.jobType] ?? job.jobType.replaceAll("_", " ")}</strong><small>시도 {job.attempt}/{job.maxAttempts}{job.retry?.reason ? ` · ${job.retry.reason}` : ""}</small></div><time>{formatSlackDate(job.updatedAt)}</time></article>)}{activeJobs.length === 0 ? <p className="tm-empty-inline">진행 중인 동기화 작업이 없습니다.</p> : null}</div></section>
      </aside>
    </div>
  </>;
}

export function SlackWorkItemDetailPage() {
  const { workItemId = "" } = useParams();
  const [detail, setDetail] = useState<SlackWorkItemDetail | null>(null);
  const [jobs, setJobs] = useState<SlackJobPage["items"]>([]);
  const [phase, setPhase] = useState<LedgerPhase>("loading");
  const [error, setError] = useState("");
  const [partial, setPartial] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading");
    setError("");
    setPartial(false);
    setConflict(false);
    void Promise.allSettled([
      slackApi.getSlackWorkItem(workItemId, controller.signal),
      slackApi.listSlackJobs({ limit: 200 }, controller.signal),
    ]).then(([detailResult, jobResult]) => {
      if (controller.signal.aborted) return;
      if (detailResult.status === "rejected") {
        setError(slackErrorMessage(detailResult.reason, "Slack work item 상세를 불러오지 못했습니다."));
        setPhase("error");
        return;
      }
      setDetail(detailResult.value);
      setJobs(jobResult.status === "fulfilled" ? dedupeJobs(jobResult.value.items) : []);
      setPartial(jobResult.status === "rejected");
      setPhase("ready");
    });
    return () => controller.abort();
  }, [revision, workItemId]);

  const syncThread = async () => {
    if (!detail) return;
    setSyncing(true);
    setError("");
    setConflict(false);
    const idempotencyKey = slackMutationKey("slack-thread-sync");
    try {
      await slackApi.syncSlackThread(workItemId, {
        idempotencyKey,
        workspaceId: detail.workItem.workspaceId,
        mode: "INCREMENTAL",
        sinceCursor: detail.thread.syncCursor,
      }, { idempotencyKey, actorLabel: "Slack Work Item Web UI" });
      setRevision((value) => value + 1);
    } catch (requestError) {
      setConflict(isSlackConflict(requestError));
      setError(slackErrorMessage(requestError, "Thread sync를 완료하지 못했습니다."));
    } finally {
      setSyncing(false);
    }
  };

  return <section className="tm-dashboard-page tm-slack-workspace" aria-labelledby="slack-work-item-title">
    <header className="tm-page-header"><div><h1 id="slack-work-item-title">Slack 요청 상세</h1><p>수집된 대화, 요청 정보, 변경 이력을 한 화면에서 확인합니다.</p></div><div className="tm-slack-detail-actions"><Link className="tm-primary-button" aria-label="이 Slack 요청의 테스트케이스 생성 작업 열기" to={`/slack-work-items/${workItemId}/generation-jobs`}>테스트 생성 작업</Link><Link className="tm-subtle-button" replace={appNavigationOptions.returnToList.replace} to="/slack-work-items">목록으로</Link></div></header>
    <WorkItemState phase={phase} error={error} retry={() => setRevision((value) => value + 1)} />
    {phase === "ready" && detail ? <SlackWorkItemDetailView detail={detail} jobs={jobs} partial={partial} mutationError={error} conflict={conflict} syncing={syncing} onSync={() => void syncThread()} /> : null}
  </section>;
}
