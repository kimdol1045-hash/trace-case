import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { LabelEditor, type LabelEditorHandle } from "./label-editor";
import { CasePersistenceBanner } from "./case-persistence-banner";
import { appNavigationOptions } from "./navigation-policy";
import {
  useTestManagement,
  type DemoWorkItem,
  type DomainId,
  type WorkItemKey,
} from "./test-management-demo";

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Seoul" }).format(new Date(value));
}

export function WorkItemDetailPage() {
  const { workItemKey } = useParams();
  const { state, saveWorkItem, loadWorkItem, persistence } = useTestManagement();
  const navigate = useNavigate();
  const [loadedWorkItemKey, setLoadedWorkItemKey] = useState<WorkItemKey | null>(null);
  const [failedWorkItemKey, setFailedWorkItemKey] = useState<WorkItemKey | null>(null);
  const key = workItemKey?.startsWith("WORK-") ? workItemKey as WorkItemKey : null;
  useEffect(() => {
    if (!key) return;
    let active = true;
    setLoadedWorkItemKey(null);
    setFailedWorkItemKey(null);
    void loadWorkItem(key).then((result) => {
      if (!active) return;
      if (result) setLoadedWorkItemKey(key);
      else setFailedWorkItemKey(key);
    });
    return () => { active = false; };
  }, [key, loadWorkItem]);
  const workItem = loadedWorkItemKey === key
    ? state.workItems.find((candidate) => candidate.key === workItemKey)
    : undefined;
  if (key && loadedWorkItemKey !== key && failedWorkItemKey !== key) {
    return <section className="tm-not-found"><h1>상위 일감을 불러오는 중입니다.</h1></section>;
  }
  if (key && failedWorkItemKey === key && persistence.status === "ERROR") {
    return <section className="tm-not-found"><CasePersistenceBanner persistence={persistence} onRetry={() => { setFailedWorkItemKey(null); void loadWorkItem(key).then((result) => { if (result) setLoadedWorkItemKey(key); else setFailedWorkItemKey(key); }); }} /><Link className="tm-primary-button" replace={appNavigationOptions.returnToList.replace} to="/test-cases">Test Library로 돌아가기</Link></section>;
  }
  if (!workItem) return <section className="tm-not-found"><span className="tm-work-item-icon">▣</span><h1>상위 일감을 찾을 수 없습니다.</h1><Link className="tm-primary-button" replace={appNavigationOptions.returnToList.replace} to="/test-cases">Test Library로 돌아가기</Link></section>;
  return <><CasePersistenceBanner persistence={persistence} onRetry={() => { if (key) void loadWorkItem(key); }} /><WorkItemEditor key={workItem.key} workItem={workItem} onSave={saveWorkItem} onBack={() => navigate("/test-cases", appNavigationOptions.returnToList)} /></>;
}

function WorkItemEditor({ workItem, onSave, onBack }: { workItem: DemoWorkItem; onSave: (workItem: DemoWorkItem) => Promise<boolean>; onBack: () => void }) {
  const { state } = useTestManagement();
  const [draft, setDraft] = useState(workItem);
  const [tab, setTab] = useState<"DETAIL" | "HISTORY">("DETAIL");
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const labelEditorRef = useRef<LabelEditorHandle>(null);
  useEffect(() => { setDraft(workItem); setSaved(false); }, [workItem]);
  const cases = state.testCases.filter((testCase) => testCase.sourceWorkItem.startsWith(`${workItem.key} ·`));
  const update = <K extends keyof DemoWorkItem>(key: K, value: DemoWorkItem[K]) => { setDraft((current) => ({ ...current, [key]: value })); setSaved(false); };
  return (
    <section className="tm-detail-page tm-work-item-detail" aria-labelledby="work-item-summary">
      <nav className="tm-detail-breadcrumb" aria-label="현재 위치"><button className="tm-back-button" type="button" onClick={onBack}>← 뒤로</button><Link replace={appNavigationOptions.returnToList.replace} to="/test-cases">Test Library</Link><span>/</span><strong>{draft.key}</strong></nav>
      <header className="tm-detail-header"><div className="tm-detail-identity"><span className="tm-work-item-icon tm-work-item-icon--large" aria-hidden="true">▣</span><div><p>{draft.key} · Parent work item</p><input id="work-item-summary" className="tm-title-input" aria-label="상위 일감 서머리" value={draft.summary} onChange={(event) => update("summary", event.target.value)} /></div></div><div className="tm-detail-actions">{saved ? <span className="tm-saved-message">✓ 변경사항이 저장되었습니다.</span> : null}<button className="tm-primary-button" type="button" disabled={saving} onClick={() => { const committedLabels = labelEditorRef.current?.commitPending() ?? draft.labels; const nextDraft = committedLabels === draft.labels ? draft : { ...draft, labels: committedLabels }; if (nextDraft !== draft) setDraft(nextDraft); setSaving(true); void onSave(nextDraft).then(setSaved).finally(() => setSaving(false)); }}>{saving ? "저장 중…" : "변경사항 저장"}</button></div></header>
      <div className="tm-detail-layout"><main className="tm-editor-card"><div className="tm-tabs" role="tablist"><button className={tab === "DETAIL" ? "is-active" : ""} type="button" onClick={() => setTab("DETAIL")}>세부 정보 · 테스트케이스</button><button className={tab === "HISTORY" ? "is-active" : ""} type="button" onClick={() => setTab("HISTORY")}>변경 이력 <span>{draft.history.length}</span></button></div>
        {tab === "DETAIL" ? <div className="tm-tab-content"><section className="tm-case-overview-section"><div className="tm-section-heading"><div><h2>세부 정보</h2><p>상위 일감의 설명과 포함된 테스트 범위를 관리합니다.</p></div></div><label className="tm-field tm-field--large"><span>설명</span><textarea rows={5} value={draft.description} onChange={(event) => update("description", event.target.value)} /></label></section><section className="tm-script-section"><div className="tm-section-heading"><div><h2>하위 테스트케이스</h2><p>{cases.length}개의 테스트케이스가 이 일감에 포함되어 있습니다.</p></div><Link className="tm-subtle-button tm-compact-button" to="/test-cases">Library에서 관리</Link></div><div className="tm-work-item-case-list">{cases.map((testCase) => <Link to={`/test-cases/${testCase.key}`} key={testCase.key}><span className="tm-case-type-icon">T</span><div><strong>{testCase.summary}</strong><small>{testCase.key} · v{testCase.version}</small></div><span>→</span></Link>)}</div></section></div> : null}
        {tab === "HISTORY" ? <div className="tm-tab-content tm-history-list">{draft.history.map((entry) => <article key={entry.id}><span className="tm-history-avatar">{entry.actor.slice(0, 1)}</span><div><strong>{entry.action}</strong><p>{entry.actor} · {formatTimestamp(entry.occurredAt)}</p>{entry.changes?.length ? <div className="tm-history-changes">{entry.changes.map((change) => <div key={`${entry.id}-${change.field}`}><span>{change.field}</span><del>{change.before}</del><ins>{change.after}</ins></div>)}</div> : <small className="tm-muted">초기 생성 기록</small>}</div></article>)}</div> : null}
      </main><aside className="tm-detail-rail"><div className="tm-rail-heading"><h2>세부 정보</h2><span>{draft.key}</span></div><label className="tm-rail-field"><span>카테고리</span><select value={draft.domainId} onChange={(event) => update("domainId", event.target.value as DomainId)}>{state.domains.filter((domain) => !domain.protected || domain.id === draft.domainId).map((domain) => <option value={domain.id} key={domain.id}>{domain.name}</option>)}</select></label><label className="tm-rail-field"><span>담당자</span><input value={draft.owner} onChange={(event) => update("owner", event.target.value)} /></label><div className="tm-rail-field tm-rail-field--stacked"><span>라벨</span><LabelEditor ref={labelEditorRef} ariaLabel="상위 일감 라벨" labels={draft.labels} onChange={(labels) => update("labels", labels)} /></div><dl className="tm-metadata"><div><dt>생성</dt><dd>{formatTimestamp(draft.createdAt)}</dd></div><div><dt>수정</dt><dd>{formatTimestamp(draft.updatedAt)}</dd></div><div><dt>하위 케이스</dt><dd>{cases.length}개</dd></div></dl></aside></div>
    </section>
  );
}
