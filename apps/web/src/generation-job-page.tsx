import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  GenerationApplyConfirmDialog,
  GenerationJobDetailPanel,
  GenerationJobHistory,
  GenerationSetupRequiredNotice,
  type ActiveGenerationOperation,
} from "./generation-job-components";
import {
  generationJobApi,
  generationApplyAlreadyAppliedId,
  generationApplyDraftIssue,
  generationApplyRequestDraft,
  generationCandidateCount,
  generationJobErrorCode,
  generationJobErrorMessage,
  generationMutationKey,
  generationSetupDetails,
  mergeGenerationJobs,
  type GenerationApplyReceipt,
  type GenerationApplyRequestDraft,
  type GenerationJob,
  type GenerationJobApi,
  type GenerationJobDetail,
  type GenerationSetupRequiredDetails,
} from "./generation-job-service";
import { appNavigationOptions } from "./navigation-policy";

type LoadPhase = "loading" | "ready" | "error";
type DetailPhase = "idle" | "loading" | "ready" | "error";

export function GenerationJobPage({ api = generationJobApi }: { api?: GenerationJobApi }) {
  const { workItemId = "" } = useParams();
  const [jobs, setJobs] = useState<GenerationJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [detail, setDetail] = useState<GenerationJobDetail | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [phase, setPhase] = useState<LoadPhase>("loading");
  const [detailPhase, setDetailPhase] = useState<DetailPhase>("idle");
  const [loadError, setLoadError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [operationError, setOperationError] = useState("");
  const [setupRequired, setSetupRequired] = useState(false);
  const [setupDetails, setSetupDetails] = useState<GenerationSetupRequiredDetails | null>(null);
  const [activeOperation, setActiveOperation] = useState<ActiveGenerationOperation | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listRevision, setListRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const [confirmApplyJobId, setConfirmApplyJobId] = useState<string | null>(null);
  const [applyReceipts, setApplyReceipts] = useState<Record<string, GenerationApplyReceipt>>({});
  const [applyReplayBlocks, setApplyReplayBlocks] = useState<Record<string, string>>({});
  const [applyDraft, setApplyDraft] = useState<GenerationApplyRequestDraft | null>(null);
  const [applyPlanIssue, setApplyPlanIssue] = useState<string | null>(null);

  const applyDraftState = useMemo(() => {
    if (!detail) return { draft: null, issue: null };
    return {
      draft: applyDraft,
      issue: applyPlanIssue ?? generationApplyDraftIssue(detail, applyDraft),
    };
  }, [applyDraft, applyPlanIssue, detail]);

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading");
    setLoadError("");
    setJobs([]);
    setSelectedJobId(null);
    setDetail(null);
    setDetailPhase("idle");
    void api.listGenerationJobs({ slackWorkItemId: workItemId, limit: 50 }, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        const loaded = mergeGenerationJobs([], page.items);
        setJobs(loaded);
        setNextCursor(page.nextCursor);
        setSelectedJobId(loaded[0]?.id ?? null);
        setPhase("ready");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setLoadError(generationJobErrorMessage(error, "생성 작업 이력을 불러오지 못했습니다."));
        setPhase("error");
      });
    return () => controller.abort();
  }, [api, listRevision, workItemId]);

  useEffect(() => {
    if (!selectedJobId) {
      setDetail(null);
      setApplyDraft(null);
      setApplyPlanIssue(null);
      setDetailPhase("idle");
      return;
    }
    const controller = new AbortController();
    setDetail(null);
    setApplyDraft(null);
    setApplyPlanIssue(null);
    setDetailError("");
    setDetailPhase("loading");
    void api.getGenerationJob(selectedJobId, controller.signal)
      .then(async (loaded) => {
        if (controller.signal.aborted) return;
        let nextDraft: GenerationApplyRequestDraft | null = null;
        let nextPlanIssue: string | null = null;
        let committedApplyId: string | null = null;
        if (loaded.job.status === "SUCCEEDED" && loaded.job.result) {
          try {
            const plan = await api.getGenerationJobApplyPlan(loaded.job.id, controller.signal);
            nextDraft = generationApplyRequestDraft(plan);
          } catch (error) {
            if (controller.signal.aborted) return;
            committedApplyId = generationApplyAlreadyAppliedId(error);
            if (!committedApplyId) {
              nextPlanIssue = generationJobErrorMessage(
                error,
                "서버의 저장 배치 계획을 불러오지 못했습니다. 최신 상태를 새로고침한 뒤 다시 시도하세요.",
              );
            }
          }
        }
        if (controller.signal.aborted) return;
        setDetail(loaded);
        setApplyDraft(nextDraft);
        setApplyPlanIssue(nextPlanIssue);
        if (committedApplyId) {
          setApplyReplayBlocks((current) => ({
            ...current,
            [loaded.job.id]: committedApplyId,
          }));
        }
        setJobs((current) => mergeGenerationJobs(current, [loaded.job]));
        setDetailPhase("ready");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setDetailError(generationJobErrorMessage(error, "작업 상세를 불러오지 못했습니다."));
        setDetailPhase("error");
      });
    return () => controller.abort();
  }, [api, detailRevision, selectedJobId]);

  const selectJob = (jobId: string) => {
    setSelectedJobId(jobId);
    setOperationError("");
    setSetupRequired(false);
    setSetupDetails(null);
    setConfirmApplyJobId(null);
    setApplyDraft(null);
    setApplyPlanIssue(null);
  };

  const applyDetail = (nextDetail: GenerationJobDetail) => {
    setJobs((current) => mergeGenerationJobs(current, [nextDetail.job]));
    setSelectedJobId(nextDetail.job.id);
    setDetail(nextDetail);
    setDetailPhase("ready");
  };

  const createJob = async () => {
    const idempotencyKey = generationMutationKey("create");
    setActiveOperation({ kind: "create" });
    clearOperationMessage();
    try {
      const created = await api.createGenerationJob(
        { idempotencyKey, slackWorkItemId: workItemId },
        { idempotencyKey, actorLabel: "Generation Job Web UI" },
      );
      applyDetail(created);
      setPhase("ready");
    } catch (error) {
      setOperationFailure(error, "새 생성 작업을 만들지 못했습니다.");
    } finally {
      setActiveOperation(null);
    }
  };

  const executeJob = async (job: GenerationJob) => {
    const idempotencyKey = generationMutationKey("execute");
    setActiveOperation({ kind: "execute", jobId: job.id });
    clearOperationMessage();
    try {
      const executed = await api.executeGenerationJob(
        job.id,
        { idempotencyKey },
        { idempotencyKey, actorLabel: "Generation Job Web UI" },
      );
      applyDetail(executed);
      if (executed.job.status === "SUCCEEDED") {
        setDetailRevision((value) => value + 1);
      }
    } catch (error) {
      setOperationFailure(error, "생성 작업을 실행하지 못했습니다.");
      if (!isSetupRequired(error)) setDetailRevision((value) => value + 1);
    } finally {
      setActiveOperation(null);
    }
  };

  const retryJob = async (job: GenerationJob) => {
    const idempotencyKey = generationMutationKey("retry");
    setActiveOperation({ kind: "retry", jobId: job.id });
    clearOperationMessage();
    try {
      applyDetail(await api.retryGenerationJob(
        job.id,
        {
          idempotencyKey,
          contextMode: "REUSE_SEALED",
          reason: "사용자가 실패한 생성 작업을 수동 재시도함",
        },
        { idempotencyKey, actorLabel: "Generation Job Web UI" },
      ));
    } catch (error) {
      setOperationFailure(error, "재시도 작업을 만들지 못했습니다.");
      setDetailRevision((value) => value + 1);
    } finally {
      setActiveOperation(null);
    }
  };

  const openApplyConfirmation = (job: GenerationJob) => {
    if (job.status !== "SUCCEEDED" || applyDraftState.issue || !applyDraftState.draft) return;
    if (applyReceipts[job.id] || applyReplayBlocks[job.id] || activeOperation) return;
    setConfirmApplyJobId(job.id);
    clearOperationMessage();
  };

  const applyJob = async () => {
    if (!detail || detail.job.id !== confirmApplyJobId || detail.job.status !== "SUCCEEDED") return;
    if (applyDraftState.issue || !applyDraftState.draft || applyReceipts[detail.job.id] || applyReplayBlocks[detail.job.id]) return;
    const jobId = detail.job.id;
    const idempotencyKey = generationMutationKey("apply");
    setActiveOperation({ kind: "apply", jobId });
    clearOperationMessage();
    try {
      const receipt = await api.applyGenerationJob(
        jobId,
        { idempotencyKey, ...applyDraftState.draft },
        { idempotencyKey, actorLabel: "Generation Job Web UI" },
      );
      setApplyReceipts((current) => ({ ...current, [jobId]: receipt }));
      setConfirmApplyJobId(null);
    } catch (error) {
      const appliedId = generationApplyAlreadyAppliedId(error);
      if (appliedId) {
        setApplyReplayBlocks((current) => ({ ...current, [jobId]: appliedId }));
      }
      setConfirmApplyJobId(null);
      setOperationFailure(error, "테스트케이스 후보를 저장하지 못했습니다.");
      if (generationJobErrorCode(error) !== "GENERATION_APPLY_TRANSACTION_FAILED") {
        setDetailRevision((value) => value + 1);
      }
    } finally {
      setActiveOperation(null);
    }
  };

  const clearOperationMessage = () => {
    setOperationError("");
    setSetupRequired(false);
    setSetupDetails(null);
  };

  const setOperationFailure = (error: unknown, fallback: string) => {
    const setup = isSetupRequired(error);
    setSetupRequired(setup);
    setSetupDetails(setup ? generationSetupDetails(error) : null);
    setOperationError(setup ? "" : generationJobErrorMessage(error, fallback));
  };

  const loadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    setLoadError("");
    try {
      const page = await api.listGenerationJobs({
        slackWorkItemId: workItemId,
        cursor: nextCursor,
        limit: 50,
      });
      setJobs((current) => mergeGenerationJobs(current, page.items));
      setNextCursor(page.nextCursor);
    } catch (error) {
      setLoadError(generationJobErrorMessage(error, "다음 작업 이력을 불러오지 못했습니다."));
    } finally {
      setLoadingMore(false);
    }
  };

  return <section className="tm-dashboard-page tm-generation-page" aria-labelledby="generation-page-title">
    <nav className="tm-detail-breadcrumb" aria-label="현재 위치"><Link className="tm-back-button" replace={appNavigationOptions.returnToList.replace} to={`/slack-work-items/${workItemId}`}>← Slack 요청 상세</Link><span>/</span><strong>생성 작업</strong></nav>
    <header className="tm-page-header tm-generation-page__header"><div><h1 id="generation-page-title">테스트케이스 생성 작업</h1><p>Slack 요청의 현재 근거를 봉인한 뒤, 대기 작업을 사용자가 직접 실행하고 이력을 확인합니다.</p></div><div className="tm-generation-page__actions"><Link className="tm-subtle-button" to="/test-cases/new">수동 테스트케이스 만들기</Link><button className="tm-subtle-button" type="button" disabled={Boolean(activeOperation) || phase === "loading"} onClick={() => setListRevision((value) => value + 1)}>상태 새로고침</button><button className="tm-primary-button" type="button" disabled={Boolean(activeOperation) || phase !== "ready"} aria-describedby="generation-create-guidance" onClick={() => void createJob()}>{activeOperation?.kind === "create" ? "작업 만드는 중…" : "새 생성 작업 만들기"}</button></div></header>
    <p className="tm-generation-guidance" id="generation-create-guidance">작업 만들기는 컨텍스트만 저장하며 외부 AI를 호출하지 않습니다. 생성 작업 실행은 별도 버튼으로 시작합니다.</p>
    {setupRequired ? <GenerationSetupRequiredNotice details={setupDetails} /> : null}
    {operationError ? <div className="tm-api-state is-error" role="alert"><strong>요청을 완료할 수 없습니다.</strong><p>{operationError}</p></div> : null}
    {loadError && phase === "ready" ? <div className="tm-partial-state" role="status"><strong>일부 이력을 불러오지 못했습니다.</strong><span>{loadError}</span></div> : null}
    {phase === "loading" ? <div className="tm-api-state" role="status">생성 작업 이력을 불러오는 중…</div> : null}
    {phase === "error" ? <div className="tm-api-state is-error" role="alert"><strong>생성 작업 이력을 불러올 수 없습니다.</strong><p>{loadError}</p><button type="button" onClick={() => setListRevision((value) => value + 1)}>다시 시도</button></div> : null}
    {phase === "ready" ? <div className="tm-generation-layout">
      <GenerationJobHistory jobs={jobs} selectedJobId={selectedJobId} loadingMore={loadingMore} hasMore={Boolean(nextCursor)} onSelect={selectJob} onLoadMore={() => void loadMore()} />
      <GenerationJobDetailPanel detail={detail} loading={detailPhase === "loading"} error={detailError} activeOperation={activeOperation} applyReceipt={detail ? applyReceipts[detail.job.id] ?? null : null} applyReplayBlocked={detail ? Boolean(applyReplayBlocks[detail.job.id]) : false} applyUnavailableReason={detail?.job.status === "SUCCEEDED" ? applyDraftState.issue : null} onReload={() => setDetailRevision((value) => value + 1)} onExecute={(job) => void executeJob(job)} onRetry={(job) => void retryJob(job)} onApply={openApplyConfirmation} />
    </div> : null}
    <GenerationApplyConfirmDialog open={Boolean(confirmApplyJobId && detail?.job.id === confirmApplyJobId)} candidateCount={generationCandidateCount(detail?.job.result ?? null) ?? 0} busy={activeOperation?.kind === "apply"} onClose={() => { if (activeOperation?.kind !== "apply") setConfirmApplyJobId(null); }} onConfirm={() => void applyJob()} />
  </section>;
}

function isSetupRequired(error: unknown) {
  return generationJobErrorCode(error) === "GENERATION_SETUP_REQUIRED";
}
