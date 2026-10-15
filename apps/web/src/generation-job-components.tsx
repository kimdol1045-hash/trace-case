import { useEffect, useId, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { useAccessibleDialog } from "./dialog-accessibility";
import {
  generationCandidateCount,
  type GenerationBlockingDecision,
  type GenerationApplyReceipt,
  type GenerationJob,
  type GenerationJobDetail,
  type GenerationSetupRequiredDetails,
} from "./generation-job-service";

const statusPresentation = {
  QUEUED: { label: "실행 대기", description: "사용자가 실행할 때까지 외부 호출 없이 대기합니다." },
  RUNNING: { label: "실행 중", description: "생성 결과를 확인하고 있습니다." },
  SUCCEEDED: { label: "생성 완료", description: "후보가 생성되었으며 테스트케이스 원장에는 저장되지 않았습니다." },
  FAILED: { label: "실행 실패", description: "실패 기록은 보존되며 새 작업으로 재시도할 수 있습니다." },
} as const;

const blockingDecisionLabels: Record<GenerationBlockingDecision, string> = {
  PROVIDER_SELECTION: "생성 공급자 선택",
  ENDPOINT: "API 엔드포인트",
  CREDENTIALS: "접속 자격 증명",
  COST_APPROVAL: "비용 승인",
};

export type GenerationOperation = "create" | "execute" | "retry" | "apply";

export interface ActiveGenerationOperation {
  kind: GenerationOperation;
  jobId?: string;
}

export function GenerationSetupRequiredNotice({ details }: { details: GenerationSetupRequiredDetails | null }) {
  return <section className="tm-generation-setup" role="alert" aria-labelledby="generation-setup-title">
    <span aria-hidden="true">!</span>
    <div>
      <strong id="generation-setup-title">AI 생성 공급자 설정이 필요합니다.</strong>
      <p>공급자·엔드포인트·자격 증명이 준비되지 않아 외부 호출은 없었고 현재 대기 작업 상태가 유지됩니다. 실패 작업의 재시도는 새 대기 작업만 추가하며 자동 실행하지 않습니다.</p>
      {details?.blockingDecisions.length ? <p className="tm-generation-setup__decisions">남은 설정: {details.blockingDecisions.map((decision) => blockingDecisionLabels[decision]).join(", ")}</p> : null}
      <p>기존 수동 테스트케이스 만들기는 영향을 받지 않으며 계속 사용할 수 있습니다.</p>
    </div>
    <Link className="tm-subtle-button" to="/test-cases/new">수동으로 만들기</Link>
  </section>;
}

export function GenerationJobHistory({
  jobs,
  selectedJobId,
  loadingMore = false,
  hasMore = false,
  onSelect,
  onLoadMore,
}: {
  jobs: GenerationJob[];
  selectedJobId: string | null;
  loadingMore?: boolean;
  hasMore?: boolean;
  onSelect: (jobId: string) => void;
  onLoadMore?: () => void;
}) {
  return <section className="tm-generation-history" aria-labelledby="generation-history-title">
    <header><div><h2 id="generation-history-title">생성 작업 이력</h2><p>재시도는 기존 실패를 덮어쓰지 않고 새 시도로 추가됩니다.</p></div><span>{jobs.length}건</span></header>
    {jobs.length ? <ol aria-label="생성 작업 목록">{jobs.map((job) => {
      const presentation = statusPresentation[job.status];
      return <li key={job.id}><button className={selectedJobId === job.id ? "is-selected" : ""} type="button" aria-pressed={selectedJobId === job.id} onClick={() => onSelect(job.id)}>
        <span className={`tm-generation-status is-${job.status.toLowerCase()}`}>{presentation.label}</span>
        <span className="tm-generation-history__identity"><strong>생성 v{job.context.generationVersion} · 시도 {job.lineage.attemptNumber}</strong><small>{formatGenerationDate(job.createdAt)}</small></span>
        <span aria-hidden="true">›</span>
      </button></li>;
    })}</ol> : <div className="tm-generation-empty"><strong>아직 생성 작업이 없습니다.</strong><p>‘새 생성 작업 만들기’를 눌러 현재 근거 컨텍스트를 봉인하면 실행 대기 작업이 생성됩니다.</p></div>}
    {hasMore ? <button className="tm-generation-load-more" type="button" disabled={loadingMore} onClick={onLoadMore}>{loadingMore ? "이력 불러오는 중…" : "이력 더 보기"}</button> : null}
  </section>;
}

export function GenerationJobDetailPanel({
  detail,
  loading = false,
  error = "",
  activeOperation = null,
  applyReceipt = null,
  applyUnavailableReason = null,
  applyReplayBlocked = false,
  onReload,
  onExecute,
  onRetry,
  onApply,
}: {
  detail: GenerationJobDetail | null;
  loading?: boolean;
  error?: string;
  activeOperation?: ActiveGenerationOperation | null;
  applyReceipt?: GenerationApplyReceipt | null;
  applyUnavailableReason?: string | null;
  applyReplayBlocked?: boolean;
  onReload?: () => void;
  onExecute: (job: GenerationJob) => void;
  onRetry: (job: GenerationJob) => void;
  onApply?: (job: GenerationJob) => void;
}) {
  if (loading) return <section className="tm-generation-detail tm-generation-detail--state" role="status">작업 컨텍스트를 불러오는 중…</section>;
  if (error) return <section className="tm-generation-detail tm-generation-detail--state is-error" role="alert"><strong>작업 상세를 불러올 수 없습니다.</strong><p>{error}</p>{onReload ? <button type="button" onClick={onReload}>다시 시도</button> : null}</section>;
  if (!detail) return <section className="tm-generation-detail tm-generation-detail--state"><strong>확인할 작업을 선택하세요.</strong><p>왼쪽 이력에서 생성 작업을 선택하면 상태와 봉인된 근거를 확인할 수 있습니다.</p></section>;

  const { job, contextItems } = detail;
  const presentation = statusPresentation[job.status];
  const executing = activeOperation?.kind === "execute" && activeOperation.jobId === job.id;
  const retrying = activeOperation?.kind === "retry" && activeOperation.jobId === job.id;
  const applying = activeOperation?.kind === "apply" && activeOperation.jobId === job.id;
  const candidateCount = generationCandidateCount(job.result);
  const resolvedApplyUnavailableReason = applyUnavailableReason
    ?? (candidateCount === null ? "저장할 생성 후보를 확인할 수 없습니다." : null)
    ?? (!onApply ? "서버의 저장 배치 계획 연결이 필요합니다." : null);
  const applyDisabled = Boolean(activeOperation)
    || Boolean(applyReceipt)
    || applyReplayBlocked
    || Boolean(resolvedApplyUnavailableReason);
  const applyGuidanceId = `generation-apply-guidance-${job.id}`;

  return <section className="tm-generation-detail" aria-labelledby="generation-detail-title">
    <header className="tm-generation-detail__header"><div><span className={`tm-generation-status is-${job.status.toLowerCase()}`}>{presentation.label}</span><h2 id="generation-detail-title">생성 작업 v{job.context.generationVersion}</h2><p>{presentation.description}</p></div><div className="tm-generation-detail__actions">
      <a className="tm-subtle-button tm-generation-comparison-link" aria-label={`생성 작업 v${job.context.generationVersion}의 봉인 근거와 후보 비교`} href={`/slack-work-items/${job.slackWorkItemId}/generation-jobs/${job.id}/comparison`}>봉인 근거·후보 비교</a>
      {job.status === "QUEUED" ? <button className="tm-primary-button" type="button" disabled={Boolean(activeOperation)} onClick={() => onExecute(job)}>{executing ? "실행 요청 중…" : "대기 작업 실행"}</button> : null}
      {job.status === "FAILED" ? <button className="tm-primary-button" type="button" disabled={Boolean(activeOperation)} onClick={() => onRetry(job)}>{retrying ? "재시도 작업 생성 중…" : "새 작업으로 재시도"}</button> : null}
      {job.status === "SUCCEEDED" ? <button className="tm-primary-button tm-generation-apply-button" type="button" disabled={applyDisabled} aria-describedby={applyGuidanceId} onClick={() => onApply?.(job)}>{applying ? "전체 후보 저장 중…" : applyReceipt ? "테스트케이스 저장 완료" : applyReplayBlocked ? "이미 저장된 작업" : `후보 ${candidateCount ?? "전체"}개 테스트케이스로 저장`}</button> : null}
    </div></header>

    {job.status === "RUNNING" ? <div className="tm-generation-inline-state" role="status">이 작업은 실행 중입니다. 상태 새로고침으로 완료 여부를 확인하세요.</div> : null}
    {job.failure ? <section className="tm-generation-failure" role="alert"><strong>{job.failure.code}</strong><p>{job.failure.message}</p><span>{job.failure.retryable ? "재시도 가능" : "설정 또는 입력 확인 필요"} · 실행 시도 {job.failure.attempts}회 · {formatGenerationDate(job.failure.occurredAt)}</span></section> : null}
    {job.status === "SUCCEEDED" ? <section className="tm-generation-result"><strong>생성 후보 {candidateCount === null ? "확인 가능" : `${candidateCount}개`}</strong><p>이 결과는 후보 데이터입니다. 자동으로 테스트케이스를 만들거나 기존 QA 원장을 변경하지 않습니다.</p><p id={applyGuidanceId} className={resolvedApplyUnavailableReason ? "tm-generation-apply-guidance is-unavailable" : "tm-generation-apply-guidance"}>{applyReceipt ? "전체 후보가 하나의 트랜잭션으로 저장되었습니다. 같은 작업을 다시 저장할 수 없습니다." : applyReplayBlocked ? "이미 커밋된 저장 작업은 다시 실행할 수 없습니다." : resolvedApplyUnavailableReason ?? "저장을 선택하면 확인 후 전체 후보를 하나의 트랜잭션으로 적용합니다. 일부 후보만 따로 저장할 수 없습니다."}</p></section> : null}
    {applyReceipt ? <GenerationApplyReceiptPanel receipt={applyReceipt} /> : null}

    <section className="tm-generation-metadata" aria-labelledby="generation-context-title"><header><div><h3 id="generation-context-title">봉인된 컨텍스트</h3><p>작업 생성 시점의 출처와 버전을 읽기 전용으로 표시합니다.</p></div><span>근거 {job.context.itemCount}개</span></header>
      <dl>
        <div><dt>입력 / 출력 스키마</dt><dd>{job.context.inputSchemaVersion} / {job.context.outputSchemaVersion}</dd></div>
        <div><dt>원본 / 생성 버전</dt><dd>v{job.context.sourceVersion} / v{job.context.generationVersion}</dd></div>
        <div><dt>온톨로지</dt><dd>{job.context.ontologySchemaId}</dd></div>
        <div><dt>프롬프트</dt><dd>{job.context.promptVersion}</dd></div>
        <div><dt>봉인 시각</dt><dd>{formatGenerationDate(job.context.sealedAt)}</dd></div>
        <div><dt>시도 계보</dt><dd>#{job.lineage.attemptNumber}{job.lineage.retryOfJobId ? ` · ${job.lineage.retryOfJobId} 재시도` : " · 최초 작업"}</dd></div>
        <div className="tm-generation-metadata__digest"><dt>SHA-256</dt><dd><code>{job.context.digest}</code></dd></div>
      </dl>
    </section>

    <section className="tm-generation-context-items" aria-labelledby="generation-context-items-title"><header><h3 id="generation-context-items-title">근거 스냅샷</h3><span>{contextItems.length}개 항목</span></header>
      {contextItems.length ? <ol>{[...contextItems].sort((left, right) => left.rank - right.rank).map((item) => <li key={item.id}><details><summary><span>#{item.rank}</span><strong>{item.source.entityType} · {item.source.entityId}</strong><small>{item.subjectType} {item.subjectId}</small></summary><dl><div><dt>선정 이유</dt><dd>{item.reason}</dd></div><div><dt>출처 버전</dt><dd>{item.source.revision}</dd></div><div><dt>근거 ID</dt><dd>{item.knowledgeEvidenceId}</dd></div><div><dt>콘텐츠 해시</dt><dd><code>{item.source.contentHash}</code></dd></div>{item.source.permalink ? <div><dt>원문</dt><dd><a href={item.source.permalink} rel="noreferrer" target="_blank">새 창에서 보기</a></dd></div> : null}</dl></details></li>)}</ol> : <p className="tm-generation-empty-inline">표시할 근거 스냅샷이 없습니다.</p>}
    </section>
  </section>;
}

export function GenerationApplyConfirmDialog({
  open,
  candidateCount,
  busy = false,
  onClose,
  onConfirm,
}: {
  open: boolean;
  candidateCount: number;
  busy?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const [confirmed, setConfirmed] = useState(false);
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLFormElement>(open, onClose, busy);

  useEffect(() => {
    if (!open) setConfirmed(false);
  }, [open]);

  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  if (!open) return null;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (confirmed && !busy) onConfirm();
  };

  return <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target && !busy) onClose(); }}>
    <form ref={dialogRef} className="tm-cycle-modal tm-generation-apply-modal" role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} onKeyDown={onDialogKeyDown} onSubmit={submit}>
      <header><h2 id={titleId}>테스트케이스 저장 확인</h2><button className="tm-modal-close" disabled={busy} type="button" aria-label="테스트케이스 저장 확인 닫기" onClick={onClose}>×</button></header>
      <div className="tm-generation-apply-modal__body"><span aria-hidden="true">{candidateCount}</span><div><strong>생성 후보 {candidateCount}개를 전부 저장합니다.</strong><p id={descriptionId}>모든 후보를 원래 순서대로 하나의 트랜잭션에 적용합니다. 새 테스트케이스는 생성하고 유효한 기존 테스트케이스는 변경 없이 재사용하며, 하나라도 실패하면 전체 작업을 롤백합니다.</p><label className="tm-generation-apply-confirmation"><input autoFocus type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} /><span>후보 {candidateCount}개 전체를 트랜잭션으로 저장하는 것을 확인했습니다.</span></label></div></div>
      <footer><button className="tm-subtle-button" disabled={busy} type="button" onClick={onClose}>취소</button><button className="tm-primary-button" disabled={!confirmed || busy} type="submit">{busy ? "전체 후보 저장 중…" : `후보 ${candidateCount}개 전부 저장`}</button></footer>
    </form>
  </div>;
}

export function GenerationApplyReceiptPanel({ receipt }: { receipt: GenerationApplyReceipt }) {
  return <section className="tm-generation-apply-receipt" aria-labelledby={`generation-apply-receipt-${receipt.id}`}>
    <header><div><span className="tm-generation-apply-receipt__committed">저장 완료</span><h3 id={`generation-apply-receipt-${receipt.id}`}>후보 {receipt.counts.generated}개 트랜잭션 결과</h3><p>새로 생성 {receipt.counts.created}개 · 기존 케이스 재사용 {receipt.counts.reused}개</p></div><small>{formatGenerationDate(receipt.appliedAt)}</small></header>
    <p className="tm-generation-apply-receipt__parent">상위 일감: {receipt.parent.outcome === "CREATED" ? "새로 생성" : "기존 일감 재사용"} · <strong>{receipt.parent.key}</strong></p>
    <ol aria-label="테스트케이스 저장 결과">{receipt.cases.map((outcome) => <li key={`${outcome.generatedCaseIndex}:${outcome.case.id}`}>
      <span className={outcome.outcome === "CREATED" ? "is-created" : "is-reused"}>{outcome.outcome === "CREATED" ? "새로 생성" : "기존 케이스 재사용"}</span>
      <div><strong>{outcome.case.key}</strong><small>{outcome.outcome === "CREATED" ? outcome.case.currentVersion.summary : `${outcome.matchReason === "EXACT" ? "내용 일치" : "유사 후보"} · 기존 버전 보존`}</small></div>
      <Link to={`/test-cases/${outcome.case.key}`}>{outcome.outcome === "CREATED" ? "새 테스트케이스 상세" : "기존 테스트케이스 상세"} →</Link>
    </li>)}</ol>
    <footer><span>커밋 영수증 {receipt.id}</span><span>감사 기록 {receipt.auditId}</span></footer>
  </section>;
}

function formatGenerationDate(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("ko-KR");
}
