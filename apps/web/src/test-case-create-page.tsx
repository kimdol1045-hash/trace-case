/* eslint-disable react-refresh/only-export-components -- selection helpers are exported for focused page tests */
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import {
  useTestManagement,
  type DemoWorkItem,
  type DomainId,
  type TestDomain,
  type TestCasePriority,
  type WorkItemKey,
} from "./test-management-demo";
import { appNavigationOptions } from "./navigation-policy";

interface DraftStep {
  id: string;
  precondition: string;
  action: string;
  expectedResult: string;
}

function emptyStep(index: number): DraftStep {
  return { id: `draft-step-${index}`, precondition: "", action: "", expectedResult: "" };
}

function normalizeLabels(value: string) {
  const labels = value.split(",").map((label) => label.trim()).filter(Boolean);
  return [...new Map(labels.map((label) => [label.normalize("NFKC").toLocaleLowerCase("ko-KR"), label])).values()];
}

export function groupCreateWorkItems(categories: TestDomain[], workItems: DemoWorkItem[]) {
  return categories.map((category) => ({
    category,
    workItems: workItems.filter((workItem) => workItem.domainId === category.id),
  })).filter((group) => group.workItems.length > 0);
}

export function selectionForCategory(workItems: DemoWorkItem[], categoryId: DomainId) {
  return {
    categoryId,
    workItemKey: workItems.find((workItem) => workItem.domainId === categoryId)?.key ?? "" as const,
  };
}

export function selectionForWorkItem(workItems: DemoWorkItem[], workItemKey: WorkItemKey) {
  const workItem = workItems.find((candidate) => candidate.key === workItemKey);
  return {
    categoryId: workItem?.domainId ?? null,
    workItemKey: workItem?.key ?? "" as const,
  };
}

export function TestCaseCreatePage() {
  const { state, persistence, createCase } = useTestManagement();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedCategory = searchParams.get("category") as DomainId | null;
  const categories = useMemo(() => state.domains.filter((domain) => domain.parentId === null && !domain.protected).sort((left, right) => left.sortOrder - right.sortOrder), [state.domains]);
  const initialCategory = categories.some((category) => category.id === requestedCategory) ? requestedCategory : categories[0]?.id ?? null;
  const [categoryId, setCategoryId] = useState<DomainId | null>(initialCategory);
  const [workItemKey, setWorkItemKey] = useState<WorkItemKey | "">(initialCategory ? selectionForCategory(state.workItems, initialCategory).workItemKey : "");
  const [summary, setSummary] = useState("");
  const [priority, setPriority] = useState<TestCasePriority>("MEDIUM");
  const [owner, setOwner] = useState("");
  const [labels, setLabels] = useState("");
  const [steps, setSteps] = useState<DraftStep[]>([emptyStep(1)]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const workItemGroups = useMemo(() => groupCreateWorkItems(categories, state.workItems), [categories, state.workItems]);
  const workItems = useMemo(() => workItemGroups.flatMap((group) => group.workItems), [workItemGroups]);

  useEffect(() => {
    if (categories.length === 0 && persistence.status === "LOADING") return;
    const selectedWorkItem = workItems.find((workItem) => workItem.key === workItemKey);
    if (selectedWorkItem) {
      if (categoryId !== selectedWorkItem.domainId) setCategoryId(selectedWorkItem.domainId);
      return;
    }
    const nextCategoryId = categories.some((category) => category.id === categoryId)
      ? categoryId
      : categories.some((category) => category.id === requestedCategory)
        ? requestedCategory
        : categories[0]?.id ?? null;
    const nextSelection = nextCategoryId ? selectionForCategory(workItems, nextCategoryId) : { categoryId: null, workItemKey: "" as const };
    if (categoryId !== nextSelection.categoryId) setCategoryId(nextSelection.categoryId);
    if (workItemKey !== nextSelection.workItemKey) setWorkItemKey(nextSelection.workItemKey);
  }, [categories, categoryId, persistence.status, requestedCategory, workItemKey, workItems]);

  const selectCategory = (nextCategoryId: DomainId) => {
    const nextSelection = selectionForCategory(workItems, nextCategoryId);
    setCategoryId(nextSelection.categoryId);
    setWorkItemKey(nextSelection.workItemKey);
  };
  const selectWorkItem = (nextWorkItemKey: WorkItemKey) => {
    const nextSelection = selectionForWorkItem(workItems, nextWorkItemKey);
    setCategoryId(nextSelection.categoryId);
    setWorkItemKey(nextSelection.workItemKey);
  };

  const updateStep = (id: string, field: "precondition" | "action" | "expectedResult", value: string) => {
    setSteps((current) => current.map((step) => step.id === id ? { ...step, [field]: value } : step));
  };
  const removeStep = (id: string) => setSteps((current) => current.length === 1 ? current : current.filter((step) => step.id !== id));
  const save = async () => {
    const normalizedSteps = steps.map((step) => ({ precondition: step.precondition.trim(), action: step.action.trim(), expectedResult: step.expectedResult.trim() }));
    if (!workItemKey) { setError("상위일감을 선택하세요."); return; }
    if (!summary.trim()) { setError("서머리를 입력하세요."); return; }
    if (normalizedSteps.some((step) => !step.precondition || !step.action || !step.expectedResult)) { setError("각 스텝의 사전조건, 테스트 동작, 기대 결과를 모두 입력하세요."); return; }
    setSubmitting(true); setError("");
    const key = await createCase(workItemKey, {
      summary: summary.trim(),
      priority,
      owner: owner.trim(),
      labels: normalizeLabels(labels),
      steps: normalizedSteps,
    });
    if (key) void navigate(`/test-cases/${key}`, appNavigationOptions.createSuccess);
    else setSubmitting(false);
  };

  const appendStep = () => setSteps((current) => {
    const nextIndex = Math.max(0, ...current.map((step) => Number(step.id.replace("draft-step-", "")) || 0)) + 1;
    return [...current, emptyStep(nextIndex)];
  });

  return <section className="tm-library-page tm-test-case-create-page" aria-labelledby="test-case-create-title">
    <nav className="tm-detail-breadcrumb"><Link className="tm-back-button" replace={appNavigationOptions.cancelCreate.replace} to="/test-cases">← 테스트 라이브러리</Link><span>/</span><strong>새 테스트케이스</strong></nav>
    <header className="tm-page-header"><div><h1 id="test-case-create-title">테스트케이스 만들기</h1><p>초안은 브라우저에서만 유지되며 저장 버튼을 누를 때 한 번만 생성됩니다.</p></div><div className="tm-detail-actions"><Link className="tm-subtle-button" replace={appNavigationOptions.cancelCreate.replace} to="/test-cases">취소</Link><button className="tm-primary-button" type="button" disabled={submitting} onClick={() => void save()}>{submitting ? "저장 중…" : "테스트케이스 저장"}</button></div></header>
    {error || persistence.status === "ERROR" ? <div className="tm-api-state is-error" role="alert"><strong>저장할 수 없습니다.</strong><p>{error || persistence.message}</p></div> : null}
    <label className="tm-test-case-create-summary">
      <span>서머리</span>
      <input className="tm-compact-summary-input" aria-label="테스트케이스 서머리" autoFocus value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="검증할 동작을 한 문장으로 입력하세요" />
    </label>
    <div className="tm-detail-layout tm-test-case-create-layout">
      <main className="tm-editor-card tm-test-case-create-form">
        <section className="tm-test-case-create-steps" aria-labelledby="test-case-create-steps-heading">
          <div className="tm-section-heading">
            <div><h2 id="test-case-create-steps-heading">테스트 스텝</h2><p>사전조건, 테스트 동작, 기대 결과를 스텝마다 입력합니다.</p></div>
            <button className="tm-subtle-button tm-compact-button" type="button" onClick={appendStep}>+ 스텝 추가</button>
          </div>
          <div className="tm-step-table tm-step-table--manual">
            <div className="tm-step-table__header"><span>#</span><span>사전조건</span><span>테스트 동작</span><span>기대 결과</span><span aria-hidden="true" /></div>
            {steps.map((step, index) => (
              <div className="tm-step-row" key={step.id}>
                <span className="tm-step-number">{index + 1}</span>
                <textarea className="tm-step-precondition tm-step-cell--precondition" aria-label={`${index + 1}번 사전조건`} rows={2} value={step.precondition} onChange={(event) => updateStep(step.id, "precondition", event.target.value)} placeholder="이 스텝의 사전조건" />
                <textarea className="tm-step-cell--action" aria-label={`${index + 1}번 테스트 동작`} rows={2} value={step.action} onChange={(event) => updateStep(step.id, "action", event.target.value)} placeholder="실행할 동작" />
                <textarea className="tm-step-cell--expected" aria-label={`${index + 1}번 기대 결과`} rows={2} value={step.expectedResult} onChange={(event) => updateStep(step.id, "expectedResult", event.target.value)} placeholder="확인할 결과" />
                <button className="tm-icon-button tm-danger-button" disabled={steps.length === 1} type="button" aria-label={`${index + 1}번 스텝 삭제`} onClick={() => removeStep(step.id)}>×</button>
              </div>
            ))}
          </div>
        </section>
      </main>
      <aside className="tm-detail-rail tm-test-case-create-meta" aria-label="테스트케이스 속성">
        <div className="tm-rail-heading"><h2>세부 정보</h2><span>신규</span></div>
        <label className="tm-rail-field"><span>카테고리</span><select aria-label="카테고리" value={categoryId ?? ""} disabled={categories.length === 0} onChange={(event) => selectCategory(event.target.value as DomainId)}>{categories.length === 0 ? <option value="">선택 가능한 카테고리가 없습니다.</option> : categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label>
        <label className="tm-rail-field"><span>상위일감</span><select aria-label="상위일감" value={workItemKey} disabled={workItems.length === 0} onChange={(event) => selectWorkItem(event.target.value as WorkItemKey)}>{workItems.length === 0 ? <option value="">선택 가능한 상위일감이 없습니다.</option> : <><option value="" disabled>상위일감을 선택하세요.</option>{workItemGroups.map((group) => <optgroup key={group.category.id} label={group.category.name}>{group.workItems.map((workItem) => <option key={workItem.key} value={workItem.key}>{workItem.key} · {workItem.summary}</option>)}</optgroup>)}</>}</select></label>
        <label className="tm-rail-field"><span>우선순위</span><select value={priority} onChange={(event) => setPriority(event.target.value as TestCasePriority)}><option>HIGHEST</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option></select></label>
        <label className="tm-rail-field"><span>담당자</span><input value={owner} onChange={(event) => setOwner(event.target.value)} placeholder="미지정" /></label>
        <label className="tm-rail-field"><span>라벨</span><input value={labels} onChange={(event) => setLabels(event.target.value)} placeholder="smoke, regression" /></label>
        <div className="tm-create-guidance"><strong>저장 정책</strong><p>이 화면을 열거나 취소하는 동작은 데이터를 만들지 않습니다. ‘테스트케이스 저장’을 눌러 검증을 통과한 경우에만 생성됩니다.</p></div>
      </aside>
    </div>
  </section>;
}
