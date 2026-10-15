import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { useAccessibleDialog } from "./dialog-accessibility";
import {
  generationFailureRetryKey,
  getGenerationAttemptHistory,
  getGenerationFailure,
  listGenerationFailures,
  retryAutomaticGenerationFailure,
  type GenerationFailureDetail,
  type GenerationFailureQueuePage,
} from "./generation-job-service";

type LoadState = "loading" | "ready" | "error";
type RetryState = "idle" | "submitting" | "success" | "replay" | "error";
type FailureQueueItem = GenerationFailureQueuePage["items"][number];

const failureErrorCodes = [
  "GENERATION_CONFIG_INVALID",
  "GENERATION_UNSUPPORTED_SCHEMA_VERSION",
  "GENERATION_AUTH_REQUIRED",
  "GENERATION_AUTH_REJECTED",
  "GENERATION_TIMEOUT",
  "GENERATION_TRANSPORT_UNAVAILABLE",
  "GENERATION_RATE_LIMITED",
  "GENERATION_PROVIDER_REJECTED",
  "GENERATION_PROVIDER_ERROR",
  "GENERATION_INVALID_CONTENT_TYPE",
  "GENERATION_RESPONSE_TOO_LARGE",
  "GENERATION_MALFORMED_JSON",
  "GENERATION_INVALID_RESPONSE",
  "GENERATION_SOURCE_STALE",
  "GENERATION_SOURCE_UNAVAILABLE",
  "GENERATION_WORK_ITEM_DELETED",
  "GENERATION_EXECUTION_OUTCOME_UNKNOWN",
  "GENERATION_WORKER_DELIVERY_EXHAUSTED",
] as const;

const actionabilityLabels: Record<FailureQueueItem["actionability"], string> = {
  AUTOMATIC_RETRY_SCHEDULED: "자동 재시도 예정",
  OPERATOR_RETRY_ALLOWED: "운영자 재시도 가능",
  REASSESS_CURRENT_SOURCE: "현재 원본 재평가",
  REVIEW_ONLY: "검토 필요",
};

const finalityLabels: Record<FailureQueueItem["failure"]["finality"], string> = {
  AUTOMATIC_RETRY_SCHEDULED: "자동 재시도 예정",
  TERMINAL_NON_RETRYABLE: "재시도 불가",
  TERMINAL_EXHAUSTED: "재시도 소진",
  TERMINAL_REVIEW_REQUIRED: "결과 검토 필요",
};

const attemptStatusLabels = {
  QUEUED: "대기",
  RUNNING: "진행 중",
  SUCCEEDED: "성공",
  FAILED: "실패",
} as const;

const eventLabels: Record<string, string> = {
  INPUT_SEALED: "입력 봉인",
  EXECUTION_QUEUED: "실행 대기열 등록",
  WORKER_LEASED: "작업자 할당",
  PROVIDER_ATTEMPT_STARTED: "공급자 호출 시작",
  PROVIDER_ATTEMPT_FAILED: "공급자 호출 실패",
  PROVIDER_ATTEMPT_SUCCEEDED: "공급자 호출 성공",
  JOB_FAILED: "생성 작업 실패",
  RETRY_SCHEDULED: "재시도 예약",
  JOB_SUCCEEDED: "생성 작업 성공",
  APPLY_SCHEDULED: "적용 예약",
  APPLY_COMMITTED: "적용 완료",
  SUGGESTION_SCHEDULED: "제안 생성 예약",
  SUGGESTION_COMMITTED: "제안 생성 완료",
  MATERIALIZATION_SKIPPED: "결과 반영 건너뜀",
  SLACK_INTENT_CAPTURED: "Slack 알림 등록",
  SLACK_DELIVERED: "Slack 알림 전송",
  SLACK_SUPPRESSED: "Slack 알림 생략",
  SLACK_DEAD: "Slack 알림 실패",
};

export function GenerationFailurePage() {
  const [page, setPage] = useState<GenerationFailureQueuePage | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<GenerationFailureDetail | null>(null);
  const [listState, setListState] = useState<LoadState>("loading");
  const [detailState, setDetailState] = useState<LoadState>("loading");
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [revision, setRevision] = useState(0);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [actionability, setActionability] = useState("");
  const [finality, setFinality] = useState("");
  const [errorCode, setErrorCode] = useState("");
  const retryOpenerRef = useRef<HTMLButtonElement>(null);
  const selectedRowRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setListState("loading");
    setListError("");
    void listGenerationFailures(
      {
        limit: 25,
        actionability: actionability || undefined,
        finality: finality || undefined,
        errorCode: errorCode || undefined,
      },
      controller.signal,
    )
      .then((loaded) => {
        if (controller.signal.aborted) return;
        setPage(loaded);
        setSelectedId((current) => (
          current && loaded.items.some((item) => item.failedJobId === current)
            ? current
            : loaded.items[0]?.failedJobId ?? null
        ));
        setListState("ready");
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setListError("자동 생성 실패 목록을 불러오지 못했습니다.");
        setListState("error");
      });
    return () => controller.abort();
  }, [actionability, errorCode, finality, revision]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailState("ready");
      setDetailError("");
      return;
    }
    const controller = new AbortController();
    setDetailState("loading");
    setDetailError("");
    void Promise.all([
      getGenerationFailure(selectedId, controller.signal),
      getGenerationAttemptHistory(selectedId, controller.signal),
    ])
      .then(([, history]) => {
        if (controller.signal.aborted) return;
        setDetail(history);
        setDetailState("ready");
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setDetailError("실패 상세와 시도 이력을 불러오지 못했습니다.");
        setDetailState("error");
      });
    return () => controller.abort();
  }, [selectedId, revision]);

  const clearSelectionForFilter = () => {
    setSelectedId(null);
    setDetail(null);
    setDialogOpen(false);
  };
  const refresh = () => setRevision((value) => value + 1);
  const closeRetryDialog = () => {
    setDialogOpen(false);
    window.requestAnimationFrame(() => {
      (retryOpenerRef.current ?? selectedRowRef.current)?.focus({ preventScroll: true });
    });
  };
  const selected = page?.items.find((item) => item.failedJobId === selectedId) ?? null;
  const countLabel = page ? `${page.items.length}건` : "—";

  return (
    <section
      className="tm-generation-failure-workspace tm-dashboard-page"
      aria-labelledby="generation-failure-title"
    >
      <header className="tm-page-header tm-generation-failure__header">
        <div>
          <h1 id="generation-failure-title">자동 생성 실패</h1>
          <p>최종 실패한 자동 생성 작업의 원인과 시도 이력을 확인하고, 허용된 작업만 조건을 추가해 재시도합니다.</p>
        </div>
        <span
          className="tm-generation-failure__count"
          aria-label={page ? `실패 ${page.items.length}건` : "실패 건수 불러오는 중"}
          aria-live="polite"
        >
          {countLabel}
        </span>
      </header>

      <section className="tm-list-surface tm-list-toolbar tm-toolbar--compound tm-generation-failure__filters" aria-label="실패 목록 필터">
        <label className="tm-filter-field">
          <span>처리 상태</span>
          <select value={actionability} onChange={(event) => { clearSelectionForFilter(); setActionability(event.target.value); }}>
            <option value="">전체</option>
            <option value="OPERATOR_RETRY_ALLOWED">운영자 재시도 가능</option>
            <option value="REASSESS_CURRENT_SOURCE">현재 원본 재평가</option>
            <option value="REVIEW_ONLY">검토 필요</option>
          </select>
        </label>
        <label className="tm-filter-field">
          <span>최종 상태</span>
          <select value={finality} onChange={(event) => { clearSelectionForFilter(); setFinality(event.target.value); }}>
            <option value="">전체</option>
            <option value="TERMINAL_EXHAUSTED">재시도 소진</option>
            <option value="TERMINAL_NON_RETRYABLE">재시도 불가</option>
            <option value="TERMINAL_REVIEW_REQUIRED">결과 검토 필요</option>
          </select>
        </label>
        <label className="tm-filter-field tm-generation-failure__error-filter">
          <span>오류 코드</span>
          <select value={errorCode} onChange={(event) => { clearSelectionForFilter(); setErrorCode(event.target.value); }}>
            <option value="">전체</option>
            {failureErrorCodes.map((code) => <option key={code} value={code}>{code}</option>)}
          </select>
        </label>
        <button className="tm-subtle-button" type="button" disabled={listState === "loading"} onClick={refresh}>
          {listState === "loading" ? "불러오는 중…" : "새로고침"}
        </button>
      </section>

      <div className="tm-generation-failure__layout">
        <section
          className="tm-list-surface tm-generation-failure__list"
          aria-labelledby="failure-list-title"
          aria-busy={listState === "loading"}
        >
          <header>
            <div>
              <h2 id="failure-list-title">실패 목록</h2>
              <p>최종 실패를 선택해 봉인된 시도 원장을 확인합니다.</p>
            </div>
            <span>{page?.items.length ?? 0}개 항목</span>
          </header>
          {listState === "loading" ? <FailureListLoading /> : null}
          {listState === "error" ? (
            <FailureState
              kind="error"
              title="실패 목록을 표시할 수 없습니다."
              description={listError}
              action={<button className="tm-subtle-button" type="button" onClick={refresh}>다시 시도</button>}
            />
          ) : null}
          {listState === "ready" && !page?.items.length ? (
            <FailureState
              kind="empty"
              title="현재 관리할 최종 실패가 없습니다."
              description="필터를 변경하거나 잠시 후 새로고침해 보세요."
            />
          ) : null}
          {listState === "ready" && page?.items.length ? (
            <ol>
              {page.items.map((item) => {
                const selectedItem = item.failedJobId === selectedId;
                return (
                  <li key={item.failedJobId}>
                    <button
                      ref={selectedItem ? selectedRowRef : undefined}
                      type="button"
                      className={selectedItem ? "is-selected" : ""}
                      aria-pressed={selectedItem}
                      onClick={() => setSelectedId(item.failedJobId)}
                    >
                      <span className="tm-generation-failure__list-meta">
                        <span className={`tm-ledger-state ${actionabilityClass(item.actionability)}`}>{actionabilityLabels[item.actionability]}</span>
                        <code>{item.failure.code}</code>
                      </span>
                      <strong>{item.failure.messageLabel}</strong>
                      <span className="tm-generation-failure__list-context">
                        생성 시도 {item.jobAttemptNumber}차 · source v{item.sourceVersion}
                      </span>
                      <small title={item.failedJobId}>{shortIdentifier(item.failedJobId)} · {formatDate(item.failure.failedAt)}</small>
                    </button>
                  </li>
                );
              })}
            </ol>
          ) : null}
        </section>

        <section
          className="tm-list-surface tm-generation-failure__detail"
          aria-labelledby="failure-detail-title"
          aria-busy={detailState === "loading"}
        >
          <header>
            <div>
              <h2 id="failure-detail-title">시도 이력</h2>
              <p>{selected ? selected.failure.messageLabel : "실패 목록에서 확인할 항목을 선택하세요."}</p>
            </div>
            {selected?.actionability === "OPERATOR_RETRY_ALLOWED" ? (
              <button
                ref={retryOpenerRef}
                className="tm-primary-button"
                type="button"
                aria-haspopup="dialog"
                aria-controls="generation-retry-dialog"
                onClick={() => setDialogOpen(true)}
              >
                조건을 추가해 재시도
              </button>
            ) : null}
          </header>
          {!selectedId && listState !== "loading" ? (
            <FailureState kind="empty" title="선택한 실패가 없습니다." description="왼쪽 목록에서 실패 한 건을 선택하세요." compact />
          ) : null}
          {selectedId && detailState === "loading" ? <FailureDetailLoading /> : null}
          {selectedId && detailState === "error" ? (
            <FailureState
              kind="error"
              title="시도 이력을 표시할 수 없습니다."
              description={detailError}
              action={<button className="tm-subtle-button" type="button" onClick={refresh}>다시 시도</button>}
              compact
            />
          ) : null}
          {selected && detailState === "ready" && detail ? <FailureHistory detail={detail} selected={selected} /> : null}
        </section>
      </div>

      {dialogOpen && detail ? (
        <RetryDialog
          detail={detail}
          onClose={closeRetryDialog}
          onCompleted={refresh}
        />
      ) : null}
    </section>
  );
}

function FailureListLoading() {
  return (
    <div className="tm-generation-failure__list-loading" role="status" aria-live="polite">
      <span className="tm-visually-hidden">실패 목록을 불러오는 중입니다.</span>
      {[0, 1, 2, 3, 4, 5].map((item) => <i key={item} aria-hidden="true" />)}
    </div>
  );
}

function FailureDetailLoading() {
  return (
    <div className="tm-generation-failure__detail-loading" role="status" aria-live="polite">
      <span className="tm-visually-hidden">시도 이력을 불러오는 중입니다.</span>
      <i aria-hidden="true" />
      <i aria-hidden="true" />
      <i aria-hidden="true" />
    </div>
  );
}

function FailureState({ kind, title, description, action, compact = false }: {
  kind: "empty" | "error";
  title: string;
  description: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={`tm-generation-failure__state is-${kind}${compact ? " is-compact" : ""}`}
      role={kind === "error" ? "alert" : "status"}
    >
      <span aria-hidden="true">{kind === "error" ? "!" : "⌕"}</span>
      <strong>{title}</strong>
      <p>{description}</p>
      {action}
    </div>
  );
}

function FailureHistory({ detail, selected }: { detail: GenerationFailureDetail; selected: FailureQueueItem }) {
  return (
    <>
      <dl className="tm-generation-failure__summary" aria-label="선택한 실패 요약">
        <div><dt>처리 상태</dt><dd><span className={`tm-ledger-state ${actionabilityClass(selected.actionability)}`}>{actionabilityLabels[selected.actionability]}</span></dd></div>
        <div><dt>최종 상태</dt><dd><span className="tm-generation-failure__finality">{finalityLabels[selected.failure.finality]}</span></dd></div>
        <div><dt>오류 코드</dt><dd><code>{selected.failure.code}</code></dd></div>
        <div><dt>실패 시각</dt><dd>{formatDate(selected.failure.failedAt)}</dd></div>
        <div><dt>실패 작업 ID</dt><dd><code title={selected.failedJobId}>{selected.failedJobId}</code></dd></div>
        <div><dt>Slack 요청</dt><dd><Link to={`/slack-work-items/${selected.slackWorkItemId}`}>{selected.slackWorkItemId}</Link></dd></div>
      </dl>

      <section className="tm-generation-failure__attempts" aria-labelledby="attempt-table-title">
        <header>
          <div>
            <h3 id="attempt-table-title">생성 시도</h3>
            <p>봉인된 입력부터 결과 반영과 알림까지 시도별로 비교합니다.</p>
          </div>
          <span>{detail.attemptHistory.length}회</span>
        </header>
        {detail.attemptHistory.length ? (
          <div className="tm-generation-failure__table-scroll">
            <table>
              <thead>
                <tr><th>시도</th><th>상태</th><th>실행</th><th>적용·알림</th><th>링크</th></tr>
              </thead>
              <tbody>
                {detail.attemptHistory.map((attempt) => (
                  <tr key={attempt.jobId}>
                    <td data-label="시도">
                      <strong>생성 시도 {attempt.jobAttemptNumber}차</strong>
                      <code title={attempt.jobId}>{shortIdentifier(attempt.jobId)}</code>
                      <small title={attempt.input.inputDigest}>입력 {shortDigest(attempt.input.inputDigest)}</small>
                      <small title={attempt.resultDigest ?? undefined}>결과 {attempt.resultDigest ? shortDigest(attempt.resultDigest) : "없음"}</small>
                    </td>
                    <td data-label="상태"><span className={`tm-ledger-state is-${attempt.status.toLowerCase()}`}>{attemptStatusLabels[attempt.status]}</span></td>
                    <td data-label="실행"><strong>공급자 {attempt.providerAttempts.length}회</strong><small>작업 전달 {attempt.workerDeliveryAttemptCount}회</small></td>
                    <td data-label="적용·알림"><strong>{readableToken(attempt.materialization.mode)}</strong><small>{readableToken(attempt.materialization.state)}</small><small>Slack · {readableToken(attempt.notification.outcome)}</small></td>
                    <td data-label="링크">
                      <nav aria-label={`생성 시도 ${attempt.jobAttemptNumber}차 관련 링크`}>
                        <Link to={`/slack-work-items/${detail.failure.slackWorkItemId}/generation-jobs`}>생성 작업</Link>
                        {attempt.status === "SUCCEEDED" ? <Link to={`/slack-work-items/${detail.failure.slackWorkItemId}/generation-jobs/${attempt.jobId}/comparison`}>비교</Link> : null}
                      </nav>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="tm-generation-failure__empty-inline">저장된 생성 시도가 없습니다.</p>}
      </section>

      <section className="tm-generation-failure__timeline" aria-labelledby="timeline-title">
        <header>
          <div>
            <h3 id="timeline-title">원장 타임라인</h3>
            <p>삭제하거나 덮어쓰지 않는 이벤트를 발생 순서로 표시합니다.</p>
          </div>
          <span>{detail.timeline.length}개 이벤트</span>
        </header>
        {detail.timeline.length ? (
          <ol>
            {detail.timeline.map((event) => (
              <li key={event.sequence}>
                <span aria-hidden="true">{event.sequence}</span>
                <div>
                  <strong>{eventLabel(event.eventType)}</strong>
                  <small>생성 시도 {event.jobAttemptNumber}차{event.providerAttemptNumber ? ` · 공급자 호출 ${event.providerAttemptNumber}회` : ""}</small>
                  <code title={event.jobId}>{shortIdentifier(event.jobId)}</code>
                </div>
                <time dateTime={event.occurredAt}>{formatDate(event.occurredAt)}</time>
              </li>
            ))}
          </ol>
        ) : <p className="tm-generation-failure__empty-inline">기록된 원장 이벤트가 없습니다.</p>}
      </section>
    </>
  );
}

export function RetryDialog({
  detail,
  onClose,
  onCompleted,
}: {
  detail: GenerationFailureDetail;
  onClose: () => void;
  onCompleted: () => void;
}) {
  const [reason, setReason] = useState("");
  const [conditions, setConditions] = useState("");
  const [state, setState] = useState<RetryState>("idle");
  const [message, setMessage] = useState("");
  const [idempotencyKey] = useState(() => generationFailureRetryKey(
    detail.failure.rootJobId,
    detail.failure.failedJobId,
  ));
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLElement>(
    true,
    onClose,
    state === "submitting",
  );
  const normalized = useMemo(() => conditions.normalize("NFKC").trim(), [conditions]);
  const codePoints = [...normalized].length;
  const utf8Bytes = new TextEncoder().encode(normalized).length;
  const invalid = !reason.trim() || codePoints < 1 || codePoints > 1000 || utf8Bytes > 4000;

  const submit = async () => {
    if (invalid || state === "submitting") return;
    setState("submitting");
    setMessage("재시도 요청을 처리하는 중입니다.");
    try {
      const result = await retryAutomaticGenerationFailure(
        detail.failure.failedJobId,
        {
          idempotencyKey,
          expectedRootJobId: detail.failure.rootJobId,
          expectedFailedJobId: detail.failure.failedJobId,
          expectedAttemptNumber: detail.failure.jobAttemptNumber,
          contextMode: "REUSE_SEALED",
          reason,
          additionalConditions: conditions,
        },
      );
      setState(result.replayed ? "replay" : "success");
      setMessage(result.replayed ? "이미 완료된 같은 요청 결과를 다시 확인했습니다." : "새 재시도 작업을 예약했습니다.");
      onCompleted();
    } catch {
      setState("error");
      setMessage("재시도를 예약하지 못했습니다. 최신 시도 이력을 확인한 뒤 다시 시도하세요.");
    }
  };

  return (
    <div
      className="tm-cycle-modal-backdrop tm-generation-failure__backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && state !== "submitting") onClose();
      }}
    >
      <section
        id="generation-retry-dialog"
        ref={dialogRef}
        className="tm-cycle-modal tm-generation-failure__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="retry-dialog-title"
        aria-describedby="retry-dialog-description"
        tabIndex={-1}
        onKeyDown={onDialogKeyDown}
      >
        <header>
          <div><span>운영자 조건부 재시도</span><h2 id="retry-dialog-title">조건을 추가해 재시도</h2></div>
          <button className="tm-modal-close" type="button" aria-label="조건부 재시도 닫기" disabled={state === "submitting"} onClick={onClose}>×</button>
        </header>
        <div className="tm-generation-failure__dialog-body">
          <p id="retry-dialog-description">봉인된 입력과 근거는 그대로 사용하며 현재 Slack 원문을 다시 읽지 않습니다.</p>
          <p id="retry-dialog-warning" className="tm-generation-failure__warning">추가 조건은 참고 데이터이며 시스템 규칙·근거·적용 정책을 변경할 수 없습니다.</p>
          <label htmlFor="retry-reason">재시도 사유 <span>필수 · 최대 500자</span></label>
          <input id="retry-reason" autoFocus required maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} />
          <label htmlFor="retry-conditions">추가 조건 <span>필수 · 최대 1,000자 / 4,000 bytes</span></label>
          <textarea id="retry-conditions" required maxLength={1000} rows={7} value={conditions} onChange={(event) => setConditions(event.target.value)} aria-describedby="retry-condition-limit" />
          <p id="retry-condition-limit" className="tm-generation-failure__limit" aria-live="polite">{codePoints}/1000자 · {utf8Bytes}/4000 bytes</p>
          <p className={`tm-generation-failure__result is-${state}`} role={state === "error" ? "alert" : "status"} aria-live="polite">{message}</p>
        </div>
        <footer>
          <button className="tm-subtle-button" type="button" disabled={state === "submitting"} onClick={onClose}>취소</button>
          <button className="tm-primary-button" type="button" disabled={invalid || state === "submitting" || state === "success" || state === "replay"} onClick={() => void submit()}>{state === "submitting" ? "처리 중…" : "재시도 예약"}</button>
        </footer>
      </section>
    </div>
  );
}

function actionabilityClass(value: FailureQueueItem["actionability"]) {
  if (value === "OPERATOR_RETRY_ALLOWED") return "is-failed_retryable";
  if (value === "AUTOMATIC_RETRY_SCHEDULED") return "is-running";
  if (value === "REASSESS_CURRENT_SOURCE") return "is-pending";
  return "is-review";
}

function shortIdentifier(value: string) {
  return value.length > 30 ? `${value.slice(0, 18)}…${value.slice(-8)}` : value;
}

function shortDigest(value: string) {
  return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
}

function readableToken(value: string) {
  return value.replaceAll("_", " ");
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function eventLabel(value: string) {
  return eventLabels[value] ?? readableToken(value);
}
