import { useEffect, useRef, useState, type FormEvent, type ReactNode, type RefObject } from "react";

import { useAccessibleDialog } from "./dialog-accessibility";
import { qualityReviewListCountLabel } from "./generation-quality-review-page-state";
import {
  qualityReviewDecisionKey,
  qualityReviewErrorCode,
  qualityReviewErrorKind,
  qualityReviewService,
  type QualityReviewCandidate,
  type QualityReviewCandidateQuery,
  type QualityReviewDecision,
  type QualityReviewDecisionRequest,
  type QualityReviewService,
} from "./generation-quality-review-service";

type LoadState = "loading" | "ready" | "error";
type MutationState = "idle" | "submitting" | "auth" | "stale" | "error";
type MobilePane = "list" | "detail";
type Classification = NonNullable<QualityReviewCandidateQuery["classification"]>;
type ReviewStatus = NonNullable<QualityReviewCandidateQuery["status"]>;
type RejectReason = NonNullable<QualityReviewDecisionRequest["reasonCode"]>;

const classificationLabels: Record<Classification, string> = {
  GROUNDED_ACTIVE: "근거 기반 활성",
  SUPERSEDED_TRAP: "대체됨 함정",
  RETRACTED_TRAP: "철회됨 함정",
  DELETED_TRAP: "삭제됨 함정",
  REJECTED_TRAP: "거절됨 함정",
  UNAVAILABLE_TRAP: "사용 불가 함정",
  UNRESOLVED_CONFLICT: "미해결 충돌",
  DECISION_CANDIDATE: "결정 후보",
  EXACT_DUPLICATE_TRAP: "완전 중복 함정",
  SEMANTIC_DUPLICATE_TRAP: "의미 중복 함정",
  CATEGORY_COLLISION_TRAP: "카테고리 충돌 함정",
  PARENT_COLLISION_TRAP: "상위 항목 충돌 함정",
  CASE_ORDERING: "케이스 순서",
  BLANK_OR_INVALID_RESULT: "빈 값·무효 결과",
  APPLY_PROVENANCE: "적용 출처",
  SUGGESTION_PROVENANCE: "제안 출처",
};

const classifications = Object.keys(classificationLabels) as Classification[];

const statusLabels: Record<QualityReviewCandidate["status"], string> = {
  UNREVIEWED: "미검토 · 0/2",
  PENDING_APPROVAL: "승인 진행 · 1/2",
  APPROVED: "검토 완료 · 2/2",
  REJECTED: "거절 · 종료",
};

const rejectReasonLabels: Record<RejectReason, string> = {
  QUALITY_EVIDENCE_INSUFFICIENT: "품질 근거 부족",
  PRIVACY_FENCE_FAILED: "개인정보 경계 실패",
  SOURCE_DRIFT: "원본 드리프트",
  TAMPER_DETECTED: "변조 감지",
  OTHER_POLICY_FAILURE: "기타 정책 실패",
};

const rejectReasons = Object.keys(rejectReasonLabels) as RejectReason[];

export function GenerationQualityReviewPage({
  service = qualityReviewService,
}: {
  service?: QualityReviewService;
}) {
  const [page, setPage] = useState<Awaited<ReturnType<QualityReviewService["list"]>> | null>(null);
  const [listState, setListState] = useState<LoadState>("loading");
  const [listError, setListError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<QualityReviewCandidate | null>(null);
  const [detailState, setDetailState] = useState<LoadState>("ready");
  const [detailError, setDetailError] = useState("");
  const [status, setStatus] = useState<ReviewStatus | "">("");
  const [classification, setClassification] = useState<Classification | "">("");
  const [materialization, setMaterialization] = useState<"COMPLETE" | "">("");
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [revision, setRevision] = useState(0);
  const [mobilePane, setMobilePane] = useState<MobilePane>("list");
  const [reviewerToken, setReviewerToken] = useState("");
  const [dialogDecision, setDialogDecision] = useState<QualityReviewDecision | null>(null);
  const [liveNotice, setLiveNotice] = useState("");
  const selectedRowRef = useRef<HTMLButtonElement>(null);
  const actionOpenerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setListState("loading");
    setListError("");
    void service.list({
      limit: 25,
      cursor,
      status: status || undefined,
      classification: classification || undefined,
      materialization: materialization || undefined,
      search: search || undefined,
    }, controller.signal).then((loaded) => {
      if (controller.signal.aborted) return;
      setPage(loaded);
      setSelectedId((current) => {
        if (!current || !loaded.items.some((item) => item.candidateId === current)) {
          setDetail(null);
          setDetailState("ready");
          return null;
        }
        return current;
      });
      setListState("ready");
    }).catch(() => {
      if (controller.signal.aborted) return;
      setListError("품질 후보 목록을 불러오지 못했습니다.");
      setListState("error");
    });
    return () => controller.abort();
  }, [classification, cursor, materialization, revision, search, service, status]);

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
    void service.get(selectedId, controller.signal).then((loaded) => {
      if (controller.signal.aborted) return;
      setDetail(loaded);
      setDetailState("ready");
    }).catch(() => {
      if (controller.signal.aborted) return;
      setDetailError("후보의 봉인 증거와 검토 원장을 불러오지 못했습니다.");
      setDetailState("error");
    });
    return () => controller.abort();
  }, [revision, selectedId, service]);

  const resetBrowse = () => {
    setCursor(null);
    setCursorHistory([]);
    setSelectedId(null);
    setDetail(null);
    setDialogDecision(null);
    setMobilePane("list");
  };
  const applySearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    resetBrowse();
    setSearch(searchDraft.trim());
  };
  const refresh = (notice?: string) => {
    if (notice) setLiveNotice(notice);
    setRevision((value) => value + 1);
  };
  const selectCandidate = (candidateId: string) => {
    setSelectedId(candidateId);
    setMobilePane("detail");
    setDialogDecision(null);
    setLiveNotice("선택한 후보의 봉인 증거를 불러옵니다.");
  };
  const showPreviousPage = () => {
    const previous = cursorHistory.at(-1) ?? null;
    setCursorHistory((current) => current.slice(0, -1));
    setCursor(previous);
    setSelectedId(null);
    setDetail(null);
    setMobilePane("list");
  };
  const showNextPage = () => {
    if (!page?.nextCursor) return;
    setCursorHistory((current) => [...current, cursor]);
    setCursor(page.nextCursor ?? null);
    setSelectedId(null);
    setDetail(null);
    setMobilePane("list");
  };
  const reviewEnabled = Boolean(detail?.reviewable && reviewerToken.trim());
  const boundaryStatus = detail?.certificationStatus === "REVIEWED_NOT_RELEASE_CERTIFIED"
    ? "REVIEWED_NOT_RELEASE_CERTIFIED"
    : "UNREVIEWED";

  return (
    <section className="tm-quality-review-workspace tm-dashboard-page" aria-labelledby="quality-review-title">
      <header className="tm-page-header tm-quality-review-header">
        <div>
          <h1 id="quality-review-title">자동 생성 품질 후보 검토</h1>
          <p>봉인된 안전 DTO의 품질 근거를 확인하고 서로 다른 두 검토자의 결정을 append-only 원장에 남깁니다.</p>
        </div>
        <span className="tm-quality-review-count" aria-live="polite">
          {qualityReviewListCountLabel(page, listState)}
        </span>
      </header>

      <QualityReviewBoundaryWarning status={boundaryStatus} />

      <form className="tm-list-surface tm-quality-review-filters" aria-label="품질 후보 서버 필터" onSubmit={applySearch}>
        <label><span>검토 진행·상태</span><select value={status} onChange={(event) => { resetBrowse(); setStatus(event.target.value as ReviewStatus | ""); }}><option value="">전체</option><option value="UNREVIEWED">미검토 · 0/2</option><option value="PENDING_APPROVAL">승인 진행 · 1/2</option><option value="APPROVED">검토 완료 · 2/2</option><option value="REJECTED">거절 · 종료</option></select></label>
        <label><span>분류</span><select value={classification} onChange={(event) => { resetBrowse(); setClassification(event.target.value as Classification | ""); }}><option value="">전체 16개 분류</option>{classifications.map((item) => <option key={item} value={item}>{classificationLabels[item]}</option>)}</select></label>
        <label><span>물질화</span><select value={materialization} onChange={(event) => { resetBrowse(); setMaterialization(event.target.value as "COMPLETE" | ""); }}><option value="">전체</option><option value="COMPLETE">완료 증거 있음</option></select></label>
        <label className="tm-quality-review-search"><span>안전 후보 키 검색</span><input type="search" value={searchDraft} maxLength={81} autoComplete="off" spellCheck={false} placeholder="review-candidate:…" onChange={(event) => setSearchDraft(event.target.value)} /></label>
        <button className="tm-subtle-button" type="submit">검색</button>
        <button className="tm-subtle-button" type="button" disabled={listState === "loading"} onClick={() => refresh("품질 후보와 선택 상세를 새로고침합니다.")}>{listState === "loading" ? "불러오는 중…" : "새로고침"}</button>
      </form>

      <p className="tm-quality-review-live" role="status" aria-live="polite">{liveNotice}</p>

      <div className="tm-quality-review-layout" data-mobile-pane={mobilePane}>
        <section className="tm-list-surface tm-quality-review-list" aria-labelledby="quality-review-list-title" aria-busy={listState === "loading"}>
          <header><div><h2 id="quality-review-list-title">검토 후보</h2><p>항목을 선택해야 상세 요청이 시작됩니다.</p></div><span>{page?.items.length ?? 0}개 항목</span></header>
          {listState === "loading" ? <QualityReviewLoading label="품질 후보 목록을 불러오는 중입니다." rows={5} /> : null}
          {listState === "error" ? <QualityReviewState kind="error" title="품질 후보 목록을 표시할 수 없습니다." description={listError} action={<button className="tm-subtle-button" type="button" onClick={() => refresh()}>다시 시도</button>} /> : null}
          {listState === "ready" && !page?.items.length ? <QualityReviewState kind="empty" title="조건에 맞는 품질 후보가 없습니다." description="서버 필터나 안전 후보 별칭을 바꾼 뒤 다시 검색하세요." /> : null}
          {listState === "ready" && page?.items.length ? <ol>{page.items.map((candidate) => {
            const selected = candidate.candidateId === selectedId;
            return <li key={candidate.candidateId}><button ref={selected ? selectedRowRef : undefined} type="button" className={selected ? "is-selected" : ""} aria-pressed={selected} onClick={() => selectCandidate(candidate.candidateId)}><span className="tm-quality-review-list-heading"><code>{candidate.candidateId}</code><span className={`tm-quality-review-status is-${candidate.status.toLowerCase()}`}>{statusLabels[candidate.status]}</span></span><span className="tm-quality-review-distribution">{classificationSummary(candidate)}</span><span className="tm-quality-review-counts">root {candidate.counts.generationRoots} · case {candidate.counts.generatedCases} · step {candidate.counts.generatedSteps} · evidence {candidate.counts.evidenceReferences}</span><small>{candidate.captureEnvironment} / {candidate.captureRevision} · {formatDate(candidate.capturedAt)}</small></button></li>;
          })}</ol> : null}
          <nav className="tm-quality-review-pagination" aria-label="품질 후보 페이지"><button className="tm-subtle-button" type="button" disabled={!cursorHistory.length || listState === "loading"} onClick={showPreviousPage}>이전 페이지</button><span>{cursorHistory.length + 1}페이지</span><button className="tm-subtle-button" type="button" disabled={!page?.nextCursor || listState === "loading"} onClick={showNextPage}>다음 페이지</button></nav>
        </section>

        <section className="tm-list-surface tm-quality-review-detail" aria-labelledby="quality-review-detail-title" aria-busy={detailState === "loading"}>
          <header><button className="tm-quality-review-mobile-back" type="button" onClick={() => { setMobilePane("list"); window.requestAnimationFrame(() => selectedRowRef.current?.focus({ preventScroll: true })); }}>← 목록</button><div><h2 id="quality-review-detail-title">봉인 후보 상세</h2><p>{detail?.candidateId ?? "목록에서 품질 후보를 선택하세요."}</p></div></header>
          {!selectedId ? <QualityReviewState compact kind="empty" title="선택한 품질 후보가 없습니다." description="목록을 자동 선택하지 않습니다. 검토할 후보를 직접 선택하세요." /> : null}
          {selectedId && detailState === "loading" ? <QualityReviewLoading label="봉인 후보 상세를 불러오는 중입니다." rows={4} /> : null}
          {selectedId && detailState === "error" ? <QualityReviewState compact kind="error" title="봉인 후보 상세를 표시할 수 없습니다." description={detailError} action={<button className="tm-subtle-button" type="button" onClick={() => refresh()}>다시 시도</button>} /> : null}
          {selectedId && detailState === "ready" && detail ? <QualityReviewDetail candidate={detail} reviewerToken={reviewerToken} setReviewerToken={setReviewerToken} reviewEnabled={reviewEnabled} actionOpenerRef={actionOpenerRef} onDecision={setDialogDecision} /> : null}
        </section>
      </div>

      {dialogDecision && detail ? <QualityReviewDecisionDialog candidate={detail} decision={dialogDecision} reviewerToken={reviewerToken} service={service} onClose={() => setDialogDecision(null)} onAuthExpired={() => { setReviewerToken(""); setLiveNotice("검토자 인증이 만료되었거나 유효하지 않습니다. token을 다시 입력하세요."); }} onStale={(message) => refresh(message)} onSuccess={(updated) => { setDetail(updated); setDialogDecision(null); setLiveNotice(updated.status === "PENDING_APPROVAL" ? "첫 번째 승인이 기록되었습니다. 다른 검토자의 승인이 필요합니다 (1/2)." : updated.status === "APPROVED" ? "서로 다른 두 검토자의 승인이 기록되었습니다 (2/2). 이 결과는 release 승인이 아닙니다." : "거절 결정이 append-only 원장에 기록되어 후보가 종료되었습니다."); refresh(); }} /> : null}
    </section>
  );
}

export function QualityReviewBoundaryWarning({ status }: { status: "UNREVIEWED" | "REVIEWED_NOT_RELEASE_CERTIFIED" }) {
  return <aside className="tm-quality-review-boundary" role="note" aria-label="release 인증 경계"><strong>CANDIDATE / {status}</strong><p>이 검토는 후보 snapshot의 사람 검토일 뿐 release 승인이나 provider live 인증이 아닙니다.</p></aside>;
}

export function QualityReviewDetail({ candidate, reviewerToken, setReviewerToken, reviewEnabled, actionOpenerRef, onDecision }: {
  candidate: QualityReviewCandidate;
  reviewerToken: string;
  setReviewerToken: (value: string) => void;
  reviewEnabled: boolean;
  actionOpenerRef: RefObject<HTMLButtonElement | null>;
  onDecision: (decision: QualityReviewDecision) => void;
}) {
  return <>
    <section className="tm-quality-review-sealed" aria-label="봉인 envelope"><span>VERIFIED E4 REVIEW ENVELOPE</span><strong>{candidate.fingerprints.artifactFingerprint}</strong><p>캡처 {formatDate(candidate.capturedAt)} · 원장 등록 {formatDate(candidate.createdAt)} · 원본 텍스트 없이 고정된 안전 projection입니다.</p></section>
    <dl className="tm-quality-review-summary">
      <div><dt>안전 후보 키</dt><dd><code>{candidate.candidateId}</code></dd></div>
      <div><dt>검토 진행</dt><dd><span className={`tm-quality-review-status is-${candidate.status.toLowerCase()}`}>{statusLabels[candidate.status]}</span></dd></div>
      <div><dt>캡처 bucket</dt><dd>{candidate.captureEnvironment}</dd></div>
      <div><dt>캡처 revision</dt><dd><code>{candidate.captureRevision}</code></dd></div>
      <div><dt>개인정보 scan</dt><dd>{candidate.privacy.status} · raw source {candidate.privacy.containsRawSourceText ? "포함" : "미포함"}</dd></div>
      <div><dt>privacy policy</dt><dd><code>{candidate.privacy.policyVersion}</code><small>{candidate.privacy.foundationPolicyVersion}</small></dd></div>
      <div><dt>물질화</dt><dd>{candidate.materialization}</dd></div>
      <div><dt>인증 경계</dt><dd>{candidate.certificationStatus} · releaseCertified={String(candidate.releaseCertified)}</dd></div>
    </dl>
    <section className="tm-quality-review-proof-grid">
      <article><h3>허용된 envelope 증명</h3><dl><div><dt>payload</dt><dd><code>{candidate.fingerprints.payloadFingerprint}</code></dd></div><div><dt>artifact</dt><dd><code>{candidate.fingerprints.artifactFingerprint}</code></dd></div>{candidate.source.fieldSeals.map((entry) => <div key={entry.fieldDomain}><dt>{entry.fieldDomain}</dt><dd><code>{entry.seal}</code></dd></div>)}</dl></article>
      <article><h3>근거·물질화 증명</h3><dl><div><dt>generation root</dt><dd>{candidate.counts.generationRoots}</dd></div><div><dt>job / result</dt><dd>{candidate.counts.generationJobs} / {candidate.counts.generationResults}</dd></div><div><dt>case / step</dt><dd>{candidate.counts.generatedCases} / {candidate.counts.generatedSteps}</dd></div><div><dt>evidence reference</dt><dd>{candidate.counts.evidenceReferences}</dd></div></dl></article>
      <article><h3>중복 증명</h3><dl><div><dt>완전 중복 함정</dt><dd>{candidate.classificationDistribution.EXACT_DUPLICATE_TRAP}</dd></div><div><dt>의미 중복 함정</dt><dd>{candidate.classificationDistribution.SEMANTIC_DUPLICATE_TRAP}</dd></div><div><dt>카테고리 충돌 함정</dt><dd>{candidate.classificationDistribution.CATEGORY_COLLISION_TRAP}</dd></div><div><dt>상위 항목 충돌 함정</dt><dd>{candidate.classificationDistribution.PARENT_COLLISION_TRAP}</dd></div></dl></article>
    </section>
    <section className="tm-quality-review-classifications"><header><div><h3>분류 분포</h3><p>safe DTO에 포함된 16개 분류별 수량입니다.</p></div><span>16 classifications</span></header><dl>{classifications.map((item) => <div key={item}><dt>{classificationLabels[item]}<small>{item}</small></dt><dd>{candidate.classificationDistribution[item]}</dd></div>)}</dl></section>
    <section className="tm-quality-review-ledger"><header><div><h3>append-only 결정 원장</h3><p>결정은 수정하거나 삭제하지 않으며 keyed reviewer seal만 표시합니다.</p></div><span>{candidate.decisions.length}개 결정</span></header>{candidate.decisions.length ? <ol>{candidate.decisions.map((entry) => <li key={entry.decisionFingerprint}><span className={`tm-quality-review-status is-${entry.decision.toLowerCase()}`}>{entry.decision}</span><div><strong>검토 슬롯 {entry.reviewerSlot}</strong><code>{entry.reviewerSeal}</code><small>{entry.reasonCode ? rejectReasonLabels[entry.reasonCode] : "승인"} · {formatDate(entry.decidedAt)}</small></div><dl><div><dt>decision fingerprint</dt><dd><code>{entry.decisionFingerprint}</code></dd></div><div><dt>envelope artifact</dt><dd><code>{entry.observedArtifactFingerprint}</code></dd></div></dl></li>)}</ol> : <p className="tm-quality-review-inline-empty">아직 기록된 결정이 없습니다.</p>}{candidate.approval ? <aside><strong>2/2 approval proof</strong><code>{candidate.approval.approvalArtifactFingerprint}</code><code>{candidate.approval.reviewAuthorityFingerprint}</code><time dateTime={candidate.approval.approvedAt}>{formatDate(candidate.approval.approvedAt)}</time></aside> : null}</section>
    <section className="tm-quality-review-token-panel" aria-labelledby="quality-review-token-title"><div><h3 id="quality-review-token-title">검토자 token</h3><p>현재 React 화면 메모리에만 유지되며 localStorage/sessionStorage에 저장하지 않습니다.</p></div><label><span>Reviewer token</span><input type="password" value={reviewerToken} autoComplete="off" spellCheck={false} inputMode="text" onChange={(event) => setReviewerToken(event.target.value)} /></label>{reviewerToken ? <button className="tm-subtle-button" type="button" onClick={() => setReviewerToken("")}>token 지우기</button> : null}</section>
    <footer className="tm-quality-review-actions"><p>{candidate.reviewable ? reviewerToken.trim() ? `현재 결정 가능 · 승인 ${candidate.progress.approveCount}/${candidate.progress.requiredApproveCount}` : "결정하려면 reviewer token을 입력하세요." : candidate.status === "REJECTED" ? "거절로 종료된 후보입니다." : "이미 종료되어 추가 결정을 남길 수 없습니다."}</p><button ref={actionOpenerRef} className="tm-subtle-button" type="button" disabled={!reviewEnabled} aria-haspopup="dialog" onClick={() => onDecision("REJECT")}>거절 검토</button><button className="tm-primary-button" type="button" disabled={!reviewEnabled} aria-haspopup="dialog" onClick={() => onDecision("APPROVE")}>승인 검토</button></footer>
  </>;
}

export function QualityReviewDecisionDialog({ candidate, decision, reviewerToken, service, onClose, onAuthExpired, onStale, onSuccess }: {
  candidate: QualityReviewCandidate;
  decision: QualityReviewDecision;
  reviewerToken: string;
  service: QualityReviewService;
  onClose: () => void;
  onAuthExpired: () => void;
  onStale: (message: string) => void;
  onSuccess: (candidate: QualityReviewCandidate) => void;
}) {
  const [reasonCode, setReasonCode] = useState<RejectReason | "">("");
  const [state, setState] = useState<MutationState>("idle");
  const [message, setMessage] = useState("");
  const [idempotencyKey] = useState(() => qualityReviewDecisionKey(candidate.candidateId, decision));
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLElement>(true, onClose, state === "submitting");
  const valid = Boolean(reviewerToken.trim() && candidate.reviewable && (decision === "APPROVE" || reasonCode));
  const submit = async () => {
    if (!valid || state === "submitting") return;
    setState("submitting");
    setMessage("봉인 snapshot을 서버에서 다시 확인하고 결정을 기록하는 중입니다.");
    try {
      const updated = await service.decide(candidate.candidateId, {
        decision,
        expectedArtifactFingerprint: candidate.fingerprints.artifactFingerprint,
        idempotencyKey,
        reasonCode: decision === "REJECT" ? reasonCode || null : null,
      }, reviewerToken);
      onSuccess(updated);
    } catch (error) {
      const kind = qualityReviewErrorKind(error);
      const code = qualityReviewErrorCode(error);
      if (kind === "auth") {
        setState("auth");
        setMessage("검토자 token이 만료되었거나 유효하지 않습니다. token을 다시 입력하세요.");
        onAuthExpired();
        return;
      }
      if (kind === "stale") {
        setState("stale");
        const staleMessage = code === "QUALITY_REVIEW_PRINCIPAL_ALREADY_DECIDED"
          ? "같은 검토자는 두 번째 승인 슬롯을 채울 수 없습니다. 다른 검토자 token이 필요하며 후보를 새로고침했습니다."
          : "후보 snapshot 또는 원장 상태가 바뀌어 결정을 기록하지 않았습니다. 최신 상태로 새로고침했습니다.";
        setMessage(staleMessage);
        onStale(staleMessage);
        return;
      }
      setState("error");
      setMessage(kind === "unavailable" ? "검토 권한 서비스가 준비되지 않았습니다. 운영 설정을 확인하세요." : kind === "validation" ? "결정 요청이 서버 계약을 충족하지 않습니다." : "결정을 기록하지 못했습니다. 잠시 후 다시 시도하세요.");
    }
  };
  return <div className="tm-quality-review-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && state !== "submitting") onClose(); }}><section ref={dialogRef} className="tm-quality-review-dialog" role="dialog" aria-modal="true" aria-labelledby="quality-review-dialog-title" aria-describedby="quality-review-dialog-description" tabIndex={-1} onKeyDown={onDialogKeyDown}><header><div><span>{decision === "APPROVE" ? "APPROVE" : "REJECT"}</span><h2 id="quality-review-dialog-title">품질 후보 {decision === "APPROVE" ? "승인" : "거절"} 확인</h2></div><button className="tm-quality-review-dialog-close" type="button" aria-label="검토 확인 닫기" disabled={state === "submitting"} onClick={onClose}>×</button></header><div className="tm-quality-review-dialog-body"><p id="quality-review-dialog-description">아래 fingerprint의 봉인 envelope에 append-only 결정을 남깁니다. 이 작업은 release 승인이 아닙니다.</p><dl><div><dt>후보 키</dt><dd><code>{candidate.candidateId}</code></dd></div><div><dt>artifact fingerprint</dt><dd><code>{candidate.fingerprints.artifactFingerprint}</code></dd></div><div><dt>현재 진행</dt><dd>{candidate.progress.approveCount}/{candidate.progress.requiredApproveCount}</dd></div></dl>{decision === "REJECT" ? <label><span>거절 사유</span><select autoFocus value={reasonCode} onChange={(event) => setReasonCode(event.target.value as RejectReason | "")}><option value="">사유를 선택하세요</option>{rejectReasons.map((reason) => <option key={reason} value={reason}>{rejectReasonLabels[reason]}</option>)}</select></label> : null}<p className={`tm-quality-review-mutation is-${state}`} role={state === "error" || state === "auth" || state === "stale" ? "alert" : "status"} aria-live="assertive">{message}</p></div><footer><button className="tm-subtle-button" type="button" disabled={state === "submitting"} onClick={onClose}>취소</button><button autoFocus={decision === "APPROVE"} className={decision === "REJECT" ? "tm-quality-review-danger" : "tm-primary-button"} type="button" disabled={!valid || state === "submitting" || state === "stale" || state === "auth"} onClick={() => void submit()}>{state === "submitting" ? "기록 중…" : `${decision === "APPROVE" ? "승인" : "거절"} 결정 기록`}</button></footer></section></div>;
}

function QualityReviewLoading({ label, rows }: { label: string; rows: number }) {
  return <div className="tm-quality-review-loading" role="status" aria-live="polite"><span className="tm-quality-review-visually-hidden">{label}</span>{Array.from({ length: rows }, (_, index) => <i key={index} aria-hidden="true" />)}</div>;
}

function QualityReviewState({ kind, title, description, action, compact = false }: { kind: "empty" | "error"; title: string; description: string; action?: ReactNode; compact?: boolean }) {
  return <div className={`tm-quality-review-state is-${kind}${compact ? " is-compact" : ""}`} role={kind === "error" ? "alert" : "status"}><span aria-hidden="true">{kind === "error" ? "!" : "⌕"}</span><strong>{title}</strong><p>{description}</p>{action}</div>;
}

function classificationSummary(candidate: QualityReviewCandidate) {
  const populated = classifications.filter((item) => candidate.classificationDistribution[item] > 0);
  if (!populated.length) return "분류 수량 없음";
  const summary = populated.slice(0, 3).map((item) => `${classificationLabels[item]} ${candidate.classificationDistribution[item]}`).join(" · ");
  return populated.length > 3 ? `${summary} 외 ${populated.length - 3}개` : summary;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
