import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { GenerationComparisonWorkspace } from "./generation-comparison-components";
import {
  generationComparisonErrorMessage,
  generationJobApi,
  type GenerationComparison,
  type GenerationJobApi,
} from "./generation-job-service";
import { appNavigationOptions } from "./navigation-policy";

type ComparisonPhase = "loading" | "ready" | "error";

export function GenerationComparisonPage({ api = generationJobApi }: { api?: GenerationJobApi }) {
  const { workItemId = "", jobId = "" } = useParams();
  const [comparison, setComparison] = useState<GenerationComparison | null>(null);
  const [phase, setPhase] = useState<ComparisonPhase>("loading");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading");
    setError("");
    setComparison(null);
    void api.getGenerationComparison(jobId, controller.signal)
      .then((loaded) => {
        if (controller.signal.aborted) return;
        setComparison(loaded);
        setPhase("ready");
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(generationComparisonErrorMessage(caught));
        setPhase("error");
      });
    return () => controller.abort();
  }, [api, jobId, revision]);

  return <section className="tm-dashboard-page tm-comparison-page" aria-labelledby="comparison-page-title">
    <nav className="tm-detail-breadcrumb" aria-label="현재 위치">
      <Link className="tm-back-button" replace={appNavigationOptions.returnToList.replace} to={`/slack-work-items/${workItemId}/generation-jobs`}>← 생성 작업</Link><span>/</span><strong>봉인 근거 비교</strong>
    </nav>
    <header className="tm-page-header tm-comparison-page__header"><div><span className="tm-comparison-read-only">읽기 전용</span><h1 id="comparison-page-title">생성 작업 비교</h1><p>Slack 원본부터 생성 후보까지 한 작업에 봉인된 근거 연결을 검토합니다.</p></div><div className="tm-comparison-page__actions"><Link className="tm-subtle-button" to="/test-cases/new">수동 테스트케이스 만들기</Link><Link className="tm-subtle-button" to={`/slack-work-items/${workItemId}/generation-jobs`}>작업 상세로 돌아가기</Link></div></header>
    {phase === "loading" ? <div className="tm-comparison-loading" role="status" aria-live="polite"><span aria-hidden="true" /><strong>봉인된 비교 자료를 불러오는 중…</strong><p>현재 Slack이나 테스트케이스를 다시 조회하지 않습니다.</p></div> : null}
    {phase === "error" ? <div className="tm-comparison-error" role="alert"><strong>비교 자료를 표시할 수 없습니다.</strong><p>{error}</p><div><button className="tm-primary-button" type="button" onClick={() => setRevision((value) => value + 1)}>다시 시도</button><Link className="tm-subtle-button" to={`/slack-work-items/${workItemId}/generation-jobs`}>생성 작업 이력</Link></div></div> : null}
    {phase === "ready" && comparison ? <GenerationComparisonWorkspace comparison={comparison} /> : null}
  </section>;
}
