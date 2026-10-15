import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { CasePersistenceBanner } from "./case-persistence-banner";
import { DeleteConfirmDialog } from "./delete-confirm-dialog";
import { useAccessibleDialog } from "./dialog-accessibility";
import {
  generationSuggestionApi,
  generationSuggestionErrorMessage,
  generationSuggestionErrorReason,
  generationSuggestionMutationKey,
  type GenerationSuggestion,
  type GenerationSuggestionSemanticSnapshot,
} from "./generation-suggestion-service";
import { LabelEditor, type LabelEditorHandle } from "./label-editor";
import { appNavigationOptions } from "./navigation-policy";
import { createDefaultTestCaseService } from "./api-test-case-service";
import { TestCasePermanentDeleteDialog } from "./test-case-permanent-delete-dialog";
import {
  getDomainPath,
  getWorkItemKey,
  reorderTestSteps,
  useTestManagement,
  type DemoTestCase,
  type DomainId,
  type TestCaseKey,
  type TestCasePriority,
} from "./test-management-demo";

type DetailTab = "SCRIPT" | "HISTORY";
type GenerationSuggestionDecisionDialog = {
  decision: "APPLY" | "IGNORE";
  suggestion: GenerationSuggestion;
  idempotencyKey: string;
};

const suggestionStateLabels: Record<GenerationSuggestion["state"], string> = {
  OPEN: "열림",
  APPLIED: "적용됨",
  IGNORED: "무시됨",
  SUPERSEDED: "대체됨",
};

const suggestionFieldLabels: Record<string, string> = {
  summary: "서머리",
  precondition: "사전조건",
  steps: "테스트 스텝",
  expectedResultMode: "기대 결과 모드",
  caseExpectedResult: "케이스 기대 결과",
  priority: "우선순위",
  labels: "라벨",
  generationReason: "생성 이유",
  evidenceNodeIds: "근거 노드",
  openQuestions: "확인 질문",
  inferenceOnly: "추론 전용",
};

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

function updateDraft<K extends keyof DemoTestCase>(
  setDraft: React.Dispatch<React.SetStateAction<DemoTestCase>>,
  key: K,
  value: DemoTestCase[K],
) {
  setDraft((current) => ({ ...current, [key]: value }));
}

function editableDraft(testCase: DemoTestCase): DemoTestCase {
  return {
    ...testCase,
    steps: testCase.steps.map((step, index) => ({
      ...step,
      precondition: step.precondition ?? (index === 0 ? testCase.precondition : ""),
    })),
  };
}

export function TestCaseDetailPage() {
  const { caseKey } = useParams();
  const { state, persistence, loadCase, saveCase, deleteCase, restoreCase, restoreCaseVersion } = useTestManagement();
  const typedCaseKey = caseKey as TestCaseKey | undefined;
  const testCase = state.testCases.find((candidate) => candidate.key === caseKey);
  const permanentDeleteService = useMemo(() => createDefaultTestCaseService(), []);

  useEffect(() => {
    if (typedCaseKey) void loadCase(typedCaseKey);
  }, [loadCase, typedCaseKey]);

  if (!testCase) {
    return (
      <section className="tm-not-found">
        <CasePersistenceBanner persistence={persistence} onRetry={typedCaseKey ? () => void loadCase(typedCaseKey) : undefined} />
        <span className="tm-case-type-icon">T</span>
        <h1>{persistence.status === "LOADING" ? "테스트케이스를 확인하는 중입니다." : "테스트케이스를 찾을 수 없습니다."}</h1>
        <p>{caseKey ?? "알 수 없는 키"}가 삭제되었거나 잘못된 주소일 수 있습니다.</p>
        <Link className="tm-primary-button" replace={appNavigationOptions.returnToList.replace} to="/test-cases">Test Library로 돌아가기</Link>
      </section>
    );
  }

  return <TestCaseEditor key={testCase.key} testCase={testCase} persistence={persistence} onReload={() => void loadCase(testCase.key)} onSave={saveCase} onDelete={deleteCase} onRestoreDeleted={restoreCase} onPermanentDelete={(value) => permanentDeleteService.permanentDelete(value)} onRestoreVersion={restoreCaseVersion} />;
}

export function TestCaseEditor({
  testCase,
  persistence,
  onReload,
  onSave,
  onDelete,
  onRestoreDeleted,
  onPermanentDelete,
  onRestoreVersion,
}: {
  testCase: DemoTestCase;
  persistence: ReturnType<typeof useTestManagement>["persistence"];
  onReload: () => void;
  onSave: (value: DemoTestCase) => Promise<boolean>;
  onDelete: (key: DemoTestCase["key"]) => Promise<boolean>;
  onRestoreDeleted: (key: DemoTestCase["key"]) => Promise<boolean>;
  onPermanentDelete: (value: DemoTestCase) => Promise<void>;
  onRestoreVersion: (key: DemoTestCase["key"], version: number) => Promise<boolean>;
}) {
  const { state } = useTestManagement();
  const navigate = useNavigate();
  const [draft, setDraft] = useState(() => editableDraft(testCase));
  const [activeTab, setActiveTab] = useState<DetailTab>("SCRIPT");
  const [draggedStepId, setDraggedStepId] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [deletePending, setDeletePending] = useState(false);
  const [permanentDeletePending, setPermanentDeletePending] = useState(false);
  const [permanentDeleting, setPermanentDeleting] = useState(false);
  const [permanentDeleteError, setPermanentDeleteError] = useState<string | null>(null);
  const [restoringVersion, setRestoringVersion] = useState<number | null>(null);
  const [generationSuggestions, setGenerationSuggestions] = useState<GenerationSuggestion[]>(
    () => testCase.generationSuggestions ?? [],
  );
  const [suggestionLoadState, setSuggestionLoadState] = useState<"IDLE" | "LOADING" | "READY" | "ERROR">(
    testCase.generationSuggestions ? "READY" : "IDLE",
  );
  const [suggestionLoadError, setSuggestionLoadError] = useState<string | null>(null);
  const [suggestionRevision, setSuggestionRevision] = useState(0);
  const [suggestionDecisionDialog, setSuggestionDecisionDialog] = useState<GenerationSuggestionDecisionDialog | null>(null);
  const [suggestionDecisionBusy, setSuggestionDecisionBusy] = useState(false);
  const [suggestionDecisionError, setSuggestionDecisionError] = useState<string | null>(null);
  const [suggestionDecisionErrorReason, setSuggestionDecisionErrorReason] = useState<string | null>(null);
  const [suggestionStatus, setSuggestionStatus] = useState<string | null>(null);
  const labelEditorRef = useRef<LabelEditorHandle>(null);
  const readOnly = Boolean(draft.deletedAt);
  const hasReleasedGenerationLineage = Boolean(
    testCase.generationManagement?.managed === false
      && testCase.generationManagement.lineageId
      && testCase.generationManagement.releasedAt,
  );

  useEffect(() => {
    setDraft(editableDraft(testCase));
  }, [testCase]);

  useEffect(() => {
    if (testCase.generationSuggestions) {
      setGenerationSuggestions(testCase.generationSuggestions);
      setSuggestionLoadState("READY");
    }
  }, [testCase.generationSuggestions]);

  useEffect(() => {
    if (!testCase.id || !hasReleasedGenerationLineage) return;
    const controller = new AbortController();
    setSuggestionLoadState("LOADING");
    setSuggestionLoadError(null);
    void generationSuggestionApi.listTestCaseSuggestions(testCase.id, controller.signal)
      .then((items) => {
        setGenerationSuggestions(items);
        setSuggestionLoadState("READY");
      })
      .catch((error: unknown) => {
        if (error instanceof Error && error.name === "AbortError") return;
        setSuggestionLoadError(generationSuggestionErrorMessage(error));
        setSuggestionLoadState("ERROR");
      });
    return () => controller.abort();
  }, [hasReleasedGenerationLineage, suggestionRevision, testCase.id]);

  const domainPath = useMemo(
    () => getDomainPath(state.domains, draft.domainId).map((domain) => domain.name).join(" / "),
    [draft.domainId, state.domains],
  );

  const handleSave = async () => {
    if (readOnly) return;
    setSaved(false);
    const committedLabels = labelEditorRef.current?.commitPending() ?? draft.labels;
    const nextDraft = committedLabels === draft.labels ? draft : { ...draft, labels: committedLabels };
    if (nextDraft !== draft) setDraft(nextDraft);
    if (await onSave(nextDraft)) setSaved(true);
  };

  const handleRestoreVersion = async (version: number) => {
    if (readOnly) return;
    setRestoringVersion(version);
    const restored = await onRestoreVersion(draft.key, version);
    setRestoringVersion(null);
    if (restored) setSaved(true);
  };

  const openSuggestionDecision = (
    decision: GenerationSuggestionDecisionDialog["decision"],
    suggestion: GenerationSuggestion,
  ) => {
    if (suggestion.state !== "OPEN") return;
    setSuggestionDecisionError(null);
    setSuggestionDecisionErrorReason(null);
    setSuggestionDecisionDialog({
      decision,
      suggestion,
      idempotencyKey: generationSuggestionMutationKey(decision === "APPLY" ? "apply" : "ignore"),
    });
  };

  const refreshSuggestions = () => {
    setSuggestionRevision((current) => current + 1);
    onReload();
  };

  const settleSuggestion = async (reason: string) => {
    const dialog = suggestionDecisionDialog;
    if (!dialog || dialog.suggestion.state !== "OPEN") return;
    setSuggestionDecisionBusy(true);
    setSuggestionDecisionError(null);
    setSuggestionDecisionErrorReason(null);
    setSuggestionStatus(dialog.decision === "APPLY" ? "제안을 반영하는 중입니다." : "제안을 무시 처리하는 중입니다.");
    try {
      const receipt = dialog.decision === "APPLY"
        ? await generationSuggestionApi.applySuggestion(
          dialog.suggestion.id,
          {
            idempotencyKey: dialog.idempotencyKey,
            expectedCaseResourceVersion: dialog.suggestion.baseline.caseResourceVersion,
            expectedCaseVersionId: dialog.suggestion.baseline.caseVersionId,
            changeReason: reason,
          },
          { idempotencyKey: dialog.idempotencyKey, actorLabel: "Test Library UI" },
        )
        : await generationSuggestionApi.ignoreSuggestion(
          dialog.suggestion.id,
          { idempotencyKey: dialog.idempotencyKey, reason },
          { idempotencyKey: dialog.idempotencyKey, actorLabel: "Test Library UI" },
        );
      setGenerationSuggestions((current) => current.map((item) => (
        item.id === receipt.suggestion.id ? receipt.suggestion : item
      )));
      setSuggestionStatus(
        dialog.decision === "APPLY"
          ? "생성 변경 제안을 새 테스트케이스 버전으로 반영했습니다."
          : "생성 변경 제안을 무시 처리했습니다. 테스트케이스는 변경되지 않았습니다.",
      );
      setSuggestionDecisionDialog(null);
      refreshSuggestions();
    } catch (error) {
      setSuggestionDecisionError(generationSuggestionErrorMessage(error));
      setSuggestionDecisionErrorReason(generationSuggestionErrorReason(error));
      setSuggestionStatus(null);
    } finally {
      setSuggestionDecisionBusy(false);
    }
  };

  const handleDelete = async () => {
    const deleted = await onDelete(draft.key);
    if (deleted) void navigate("/test-cases", appNavigationOptions.deleteSuccess);
    else setDeletePending(false);
  };

  const handlePermanentDelete = async () => {
    setPermanentDeleting(true);
    setPermanentDeleteError(null);
    try {
      await onPermanentDelete(draft);
      void navigate("/test-cases", appNavigationOptions.deleteSuccess);
    } catch (error) {
      setPermanentDeleteError(error instanceof Error ? error.message : "테스트케이스를 완전 삭제하지 못했습니다.");
      setPermanentDeleting(false);
    }
  };

  const addStep = () => {
    if (readOnly) return;
    updateDraft(setDraft, "steps", [
      ...draft.steps,
      { id: `step_local_${Date.now()}`, precondition: "", action: "", expectedResult: "" },
    ]);
  };

  const handleStepDrop = (event: DragEvent<HTMLDivElement>, targetId: string) => {
    event.preventDefault();
    if (readOnly) return;
    const sourceId = event.dataTransfer.getData("text/test-step-id") || draggedStepId;
    if (!sourceId) return;
    updateDraft(setDraft, "steps", reorderTestSteps(draft.steps, sourceId, targetId));
    setDraggedStepId(null);
  };

  return (
    <section className="tm-detail-page" aria-labelledby="test-case-summary">
      <DeleteConfirmDialog open={deletePending} title="테스트케이스 삭제" entityName={`${draft.key} · ${draft.summary}`} description="테스트 라이브러리에서 숨겨집니다. 변경 이력과 사이클에 고정된 스냅샷은 보존됩니다." busy={persistence.status === "SAVING"} onClose={() => setDeletePending(false)} onConfirm={() => void handleDelete()} />
      <TestCasePermanentDeleteDialog open={permanentDeletePending} caseKey={draft.key} summary={draft.summary} busy={permanentDeleting} error={permanentDeleteError} onClose={() => { setPermanentDeletePending(false); setPermanentDeleteError(null); }} onConfirm={() => void handlePermanentDelete()} />
      <GenerationSuggestionApplyDialog
        open={suggestionDecisionDialog?.decision === "APPLY"}
        testCase={draft}
        suggestion={suggestionDecisionDialog?.decision === "APPLY" ? suggestionDecisionDialog.suggestion : null}
        busy={suggestionDecisionBusy}
        error={suggestionDecisionError}
        stale={suggestionDecisionErrorReason === "CASE_VERSION_STALE"}
        onClose={() => { setSuggestionDecisionDialog(null); setSuggestionDecisionError(null); }}
        onConfirm={(changeReason) => void settleSuggestion(changeReason)}
        onReload={refreshSuggestions}
      />
      <GenerationSuggestionIgnoreDialog
        open={suggestionDecisionDialog?.decision === "IGNORE"}
        testCase={draft}
        suggestion={suggestionDecisionDialog?.decision === "IGNORE" ? suggestionDecisionDialog.suggestion : null}
        busy={suggestionDecisionBusy}
        error={suggestionDecisionError}
        onClose={() => { setSuggestionDecisionDialog(null); setSuggestionDecisionError(null); }}
        onConfirm={(reason) => void settleSuggestion(reason)}
      />
      <CasePersistenceBanner persistence={persistence} onRetry={onReload} />
      {readOnly ? <div className="tm-notice" role="status"><span><strong>삭제됨 · 읽기 전용</strong> 이 테스트케이스는 수정할 수 없습니다. 복원하거나 완전 삭제할 수 있습니다.</span></div> : null}
      <nav className="tm-detail-breadcrumb" aria-label="현재 위치">
        <button className="tm-back-button" type="button" onClick={() => void navigate("/test-cases", appNavigationOptions.returnToList)} aria-label="뒤로가기">← 뒤로</button>
        <Link replace={appNavigationOptions.returnToList.replace} to="/test-cases">Test Library</Link>
        <span>/</span>
        <span>{domainPath}</span>
        <span>/</span>
        <strong>{draft.key}</strong>
      </nav>

      <header className="tm-detail-header">
        <div className="tm-detail-identity">
          <span className="tm-case-type-icon" aria-hidden="true">T</span>
          <div>
            <p>{draft.key} · Test case</p>
            <input
              id="test-case-summary"
              className="tm-title-input"
              aria-label="테스트케이스 서머리"
              value={draft.summary}
              readOnly={readOnly}
              onChange={(event) => updateDraft(setDraft, "summary", event.target.value)}
              onFocus={() => setSaved(false)}
            />
          </div>
        </div>
        <div className="tm-detail-actions">
          {saved ? <span className="tm-saved-message" role="status">✓ 변경사항이 저장되었습니다.</span> : null}
          <button className="tm-subtle-button" type="button">공유</button>
          {draft.deletedAt ? <><button className="tm-subtle-button" type="button" disabled={persistence.status === "SAVING"} onClick={() => void onRestoreDeleted(draft.key)}>삭제 복원</button><button className="tm-subtle-button tm-danger-button" type="button" disabled={persistence.status === "SAVING"} onClick={() => setPermanentDeletePending(true)}>완전 삭제</button></> : <button className="tm-subtle-button tm-danger-button" type="button" onClick={() => setDeletePending(true)}>삭제</button>}
          <button className="tm-primary-button" type="button" disabled={persistence.status === "LOADING" || persistence.status === "SAVING" || Boolean(draft.deletedAt)} onClick={() => void handleSave()}>변경사항 저장</button>
        </div>
      </header>

      <div className="tm-parent-work-item">
        <span>상위 일감</span>
        <Link to={`/work-items/${getWorkItemKey(draft.sourceWorkItem)}`}><strong>{draft.sourceWorkItem}</strong></Link>
        <span className="tm-parent-chip">Internal work item</span>
      </div>

      <div className="tm-detail-layout">
        <main className="tm-editor-card">
          <div className="tm-tabs" role="tablist" aria-label="테스트케이스 탭">
            <button className={activeTab === "SCRIPT" ? "is-active" : ""} type="button" role="tab" aria-selected={activeTab === "SCRIPT"} onClick={() => setActiveTab("SCRIPT")}>세부 정보 · 테스트 스크립트</button>
            <button className={activeTab === "HISTORY" ? "is-active" : ""} type="button" role="tab" aria-selected={activeTab === "HISTORY"} onClick={() => setActiveTab("HISTORY")}>변경 이력 <span>{draft.history.length}</span></button>
          </div>

          {activeTab === "SCRIPT" ? (
            <div className="tm-tab-content">
              <section className="tm-case-overview-section" aria-labelledby="case-details-heading">
                <div className="tm-section-heading"><div><h2 id="case-details-heading">세부 정보</h2><p>테스트 목적과 실행 조건을 확인합니다.</p></div></div>
                <label className="tm-field tm-field--large"><span>설명</span><textarea rows={4} value={draft.description} readOnly={readOnly} onChange={(event) => updateDraft(setDraft, "description", event.target.value)} /></label>
              </section>

              <section className="tm-script-section" aria-labelledby="test-steps-heading">
                <div className="tm-section-heading">
                  <div>
                    <h2 id="test-steps-heading">테스트 스텝</h2>
                    <p>{readOnly ? "삭제된 테스트케이스의 실행 스크립트는 읽기 전용입니다." : "핸들을 드래그해 실행 순서를 바꿀 수 있습니다. 버전마다 최소 1개 스텝을 유지합니다."}</p>
                  </div>
                  <button className="tm-subtle-button tm-compact-button" type="button" disabled={readOnly} onClick={addStep}>+ 스텝 추가</button>
                </div>
                <div className="tm-step-table tm-step-table--manual">
                  <div className="tm-step-table__header">
                    <span>#</span>
                    <span>사전조건</span>
                    <span>테스트 동작</span>
                    <span>기대 결과</span>
                    <span aria-hidden="true" />
                  </div>
                  {draft.steps.map((step, index) => (
                    <div
                      className="tm-step-row"
                      data-step-id={step.id}
                      key={step.id}
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={(event) => handleStepDrop(event, step.id)}
                    >
                      <span className="tm-step-number"><button className="tm-step-drag-handle" type="button" draggable={!readOnly} disabled={readOnly} aria-label={`${index + 1}번 스텝 순서 이동`} onDragStart={(event) => { if (readOnly) return; event.dataTransfer.setData("text/test-step-id", step.id); event.dataTransfer.effectAllowed = "move"; setDraggedStepId(step.id); }} onDragEnd={() => setDraggedStepId(null)}>⋮⋮</button>{index + 1}</span>
                      <textarea
                        className="tm-step-precondition tm-step-cell--precondition"
                        aria-label={`${index + 1}번 사전조건`}
                        rows={2}
                        value={step.precondition ?? ""}
                        readOnly={readOnly}
                        onChange={(event) => updateDraft(setDraft, "steps", draft.steps.map((candidate) => candidate.id === step.id ? { ...candidate, precondition: event.target.value } : candidate))}
                        placeholder="이 스텝의 사전조건"
                      />
                      <textarea
                        className="tm-step-cell--action"
                        aria-label={`${index + 1}번 테스트 동작`}
                        rows={2}
                        value={step.action}
                        readOnly={readOnly}
                        onChange={(event) => updateDraft(setDraft, "steps", draft.steps.map((candidate) => candidate.id === step.id ? { ...candidate, action: event.target.value } : candidate))}
                      />
                      <textarea
                        className="tm-step-cell--expected"
                        aria-label={`${index + 1}번 기대 결과`}
                        rows={2}
                        value={step.expectedResult}
                        readOnly={readOnly}
                        onChange={(event) => updateDraft(setDraft, "steps", draft.steps.map((candidate) => candidate.id === step.id ? { ...candidate, expectedResult: event.target.value } : candidate))}
                      />
                      <button
                        className="tm-icon-button"
                        type="button"
                        disabled={readOnly || draft.steps.length === 1}
                        aria-label={`${index + 1}번 스텝 삭제`}
                        onClick={() => updateDraft(setDraft, "steps", draft.steps.filter((candidate) => candidate.id !== step.id))}
                      >×</button>
                    </div>
                  ))}
                </div>
              </section>

              {hasReleasedGenerationLineage || generationSuggestions.length ? (
                <GenerationSuggestionWorkspace
                  caseResourceVersion={draft.resourceVersion}
                  deleted={readOnly}
                  loadError={suggestionLoadError}
                  loading={suggestionLoadState === "LOADING"}
                  onApply={(suggestion) => openSuggestionDecision("APPLY", suggestion)}
                  onIgnore={(suggestion) => openSuggestionDecision("IGNORE", suggestion)}
                  onReload={refreshSuggestions}
                  suggestions={generationSuggestions}
                  status={suggestionStatus}
                />
              ) : null}

            </div>
          ) : null}

          {activeTab === "HISTORY" ? (
            <div className="tm-tab-content tm-history-list">
              {draft.history.map((entry) => (
                <article key={entry.id}>
                  <span className="tm-history-avatar">{entry.actor.slice(0, 1)}</span>
                  <div>
                    <strong>{entry.version ? `v${entry.version} · ` : ""}{entry.action}</strong>
                    <p>{entry.actor} · {formatTimestamp(entry.occurredAt)}</p>
                    {entry.restoredFromVersion ? <small className="tm-restored-version">v{entry.restoredFromVersion}에서 복원됨</small> : null}
                    {entry.changes?.length ? <div className="tm-history-changes">{entry.changes.map((change) => <div key={`${entry.id}-${change.field}`}><span>{change.field}</span><del>{change.before}</del><ins>{change.after}</ins></div>)}</div> : <small className="tm-muted">초기 생성 기록</small>}
                    {entry.version && entry.version < draft.version && !readOnly ? <button className="tm-history-restore" type="button" disabled={restoringVersion !== null} onClick={() => void handleRestoreVersion(entry.version!)}>{restoringVersion === entry.version ? "복원 중…" : `v${entry.version} 복원`}</button> : null}
                  </div>
                </article>
              ))}
            </div>
          ) : null}
        </main>

        <aside className="tm-detail-rail" aria-label="테스트케이스 속성">
          <div className="tm-rail-heading">
            <h2>세부 정보</h2>
            <span>v{draft.version}</span>
          </div>
          <label className="tm-rail-field">
            <span>카테고리</span>
            <select aria-label="카테고리" value={draft.domainId} disabled={readOnly} onChange={(event) => updateDraft(setDraft, "domainId", event.target.value as DomainId)}>
              {state.domains.filter((domain) => !domain.protected || domain.id === draft.domainId).map((domain) => <option key={domain.id} value={domain.id}>{getDomainPath(state.domains, domain.id).map((part) => part.name).join(" / ")}</option>)}
            </select>
          </label>
          <label className="tm-rail-field">
            <span>우선순위</span>
            <select value={draft.priority} disabled={readOnly} onChange={(event) => updateDraft(setDraft, "priority", event.target.value as TestCasePriority)}>
              <option value="HIGHEST">Highest</option>
              <option value="HIGH">High</option>
              <option value="MEDIUM">Medium</option>
              <option value="LOW">Low</option>
            </select>
          </label>
          <label className="tm-rail-field">
            <span>담당자</span>
            <input value={draft.owner} readOnly={readOnly} onChange={(event) => updateDraft(setDraft, "owner", event.target.value)} />
          </label>
          <div className="tm-rail-field tm-rail-field--stacked"><span>라벨</span>{readOnly ? <div className="tm-label-editor" aria-label="테스트케이스 라벨 읽기 전용">{draft.labels.length ? draft.labels.map((label) => <span className="tm-label-chip" key={label}>{label}</span>) : <span className="tm-muted">라벨 없음</span>}</div> : <LabelEditor ref={labelEditorRef} ariaLabel="테스트케이스 라벨" labels={draft.labels} onChange={(labels) => updateDraft(setDraft, "labels", labels)} />}</div>
          <dl className="tm-metadata">
            <div><dt>생성</dt><dd>{formatTimestamp(draft.createdAt)}</dd></div>
            <div><dt>수정</dt><dd>{formatTimestamp(draft.updatedAt)}</dd></div>
            <div><dt>형식</dt><dd>Step-by-step</dd></div>
          </dl>
        </aside>
      </div>
    </section>
  );
}

function GenerationSuggestionWorkspace({
  caseResourceVersion,
  deleted,
  loadError,
  loading,
  onApply,
  onIgnore,
  onReload,
  status,
  suggestions,
}: {
  caseResourceVersion?: number;
  deleted: boolean;
  loadError: string | null;
  loading: boolean;
  onApply: (suggestion: GenerationSuggestion) => void;
  onIgnore: (suggestion: GenerationSuggestion) => void;
  onReload: () => void;
  status: string | null;
  suggestions: GenerationSuggestion[];
}) {
  const [expandedSuggestions, setExpandedSuggestions] = useState<Set<string>>(() => new Set());
  const ordered = useMemo(() => [...suggestions].sort((left, right) => {
    const byCreatedAt = Date.parse(right.createdAt) - Date.parse(left.createdAt);
    return byCreatedAt || right.id.localeCompare(left.id);
  }), [suggestions]);

  const toggleExpanded = (suggestionId: string) => {
    setExpandedSuggestions((current) => {
      const next = new Set(current);
      if (next.has(suggestionId)) next.delete(suggestionId);
      else next.add(suggestionId);
      return next;
    });
  };

  return (
    <section className="tm-generation-suggestions" aria-labelledby="generation-suggestions-heading">
      <div className="tm-section-heading tm-generation-suggestions__heading">
        <div>
          <h2 id="generation-suggestions-heading">생성 변경 제안</h2>
          <p>현재 케이스를 자동으로 덮어쓰지 않았습니다. 봉인된 기준과 생성 결과를 검토한 뒤 직접 결정하세요.</p>
        </div>
        <button className="tm-subtle-button tm-compact-button" type="button" onClick={onReload}>새로고침</button>
      </div>

      {status ? <div className="tm-generation-suggestion-status" role="status" aria-live="polite">{status}</div> : null}
      {loadError ? <div className="tm-generation-suggestion-error" role="alert"><span>{loadError}</span><button className="tm-subtle-button" type="button" onClick={onReload}>다시 불러오기</button></div> : null}
      {loading && !ordered.length ? <p className="tm-generation-suggestion-empty" role="status">생성 변경 제안을 불러오는 중입니다.</p> : null}
      {!loading && !loadError && !ordered.length ? <p className="tm-generation-suggestion-empty">아직 검토할 생성 변경 제안이 없습니다.</p> : null}

      <div className="tm-generation-suggestion-list">
        {ordered.map((suggestion) => {
          const expanded = expandedSuggestions.has(suggestion.id);
          const stale = suggestion.state === "OPEN"
            && caseResourceVersion !== undefined
            && caseResourceVersion !== suggestion.baseline.caseResourceVersion;
          const diffId = `generation-suggestion-diff-${suggestion.id}`;
          const sourcePath = suggestion.source.slackWorkItemId
            ? `/slack-work-items/${suggestion.source.slackWorkItemId}/generation-jobs`
            : `/knowledge?generationJobId=${encodeURIComponent(suggestion.source.generationJobId)}`;
          return (
            <article className={`tm-generation-suggestion-card is-${suggestion.state.toLowerCase()}`} key={suggestion.id}>
              <header>
                <div>
                  <span className={`tm-generation-suggestion-state is-${suggestion.state.toLowerCase()}`}>{suggestionStateLabels[suggestion.state]}</span>
                  <strong>기준 v{suggestion.baseline.caseVersion} → 생성 v{suggestion.source.generationVersion}</strong>
                </div>
                <time dateTime={suggestion.createdAt}>{formatTimestamp(suggestion.createdAt)}</time>
              </header>

              <div className="tm-generation-suggestion-summary">
                <p>{suggestion.diff.changedFields.map((field) => suggestionFieldLabels[field] ?? field).join(" · ")} 변경</p>
                <div className="tm-generation-suggestion-version-seals">
                  <span>기준 v{suggestion.baseline.caseVersion}</span>
                  <span>생성 v{suggestion.source.generationVersion}</span>
                  <span>source v{suggestion.source.sourceVersion}</span>
                </div>
              </div>

              {stale ? <div className="tm-generation-suggestion-stale" role="alert"><span>현재 케이스가 기준 resource version {suggestion.baseline.caseResourceVersion} 이후 변경되어 이 제안은 stale 상태입니다. 자동 재조정하거나 재시도하지 않습니다.</span><button className="tm-subtle-button" type="button" onClick={onReload}>최신 상태 다시 불러오기</button></div> : null}
              {deleted && suggestion.state === "OPEN" ? <p className="tm-generation-suggestion-stale">삭제된 케이스에는 반영할 수 없습니다. 무시는 케이스를 변경하지 않고 계속 가능합니다.</p> : null}
              {suggestion.state === "SUPERSEDED" && suggestion.supersededBySuggestionId ? <p className="tm-generation-suggestion-terminal-note">최신 제안 <code>{suggestion.supersededBySuggestionId}</code>으로 대체되었습니다.</p> : null}
              {suggestion.resolution ? <p className="tm-generation-suggestion-terminal-note">{suggestion.resolution.decision === "APPLIED" ? "반영" : "무시"} 결정{suggestion.resolution.reason ? ` · ${suggestion.resolution.reason}` : ""}</p> : null}

              <button
                className="tm-generation-suggestion-disclosure"
                type="button"
                aria-controls={diffId}
                aria-expanded={expanded}
                onClick={() => toggleExpanded(suggestion.id)}
              >{expanded ? "변경 내용 접기" : "변경 내용 펼치기"}</button>

              <div className="tm-generation-suggestion-diff-panel" id={diffId} hidden={!expanded}>
                <div className="tm-generation-suggestion-diff-intro">
                  <strong>immutable before / after</strong>
                  <span>저장된 diff {suggestion.diff.schemaVersion}</span>
                </div>
                {suggestion.diff.changes.map((change) => (
                  <section className="tm-generation-suggestion-change" key={change.path} aria-label={`${suggestionFieldLabels[change.path] ?? change.path} 변경`}>
                    <h3>{suggestionFieldLabels[change.path] ?? change.path}</h3>
                    <div className="tm-generation-suggestion-diff">
                      <div><strong>이전 · 기준 v{suggestion.baseline.caseVersion}</strong><SuggestionDiffValue path={change.path} value={change.before} /></div>
                      <div><strong>이후 · 생성 v{suggestion.source.generationVersion}</strong><SuggestionDiffValue path={change.path} value={change.after} /></div>
                    </div>
                  </section>
                ))}
                <div className="tm-generation-suggestion-provenance">
                  <h3>근거와 source</h3>
                  <div className="tm-generation-suggestion-source-links">
                    <Link to={sourcePath}>생성 작업 봉인 근거</Link>
                    {suggestion.source.slackWorkItemId ? <Link to={`/slack-work-items/${suggestion.source.slackWorkItemId}`}>Slack 요청 원문</Link> : null}
                    {(suggestion.proposal.snapshot.evidenceNodeIds ?? []).map((evidenceId) => <Link key={evidenceId} to={`/knowledge?nodeId=${encodeURIComponent(evidenceId)}`}>근거 {evidenceId}</Link>)}
                  </div>
                  {suggestion.proposal.snapshot.generationReason ? <p><strong>생성 이유</strong>{suggestion.proposal.snapshot.generationReason}</p> : null}
                  {suggestion.proposal.snapshot.openQuestions?.length ? <div><strong>확인 질문</strong><ul>{suggestion.proposal.snapshot.openQuestions.map((question) => <li key={question}>{question}</li>)}</ul></div> : null}
                  <dl>
                    <div><dt>제안 ID</dt><dd><code>{suggestion.id}</code></dd></div>
                    <div><dt>생성 작업</dt><dd><code>{suggestion.source.generationJobId}</code></dd></div>
                    <div><dt>diff digest</dt><dd><code>{suggestion.diff.digest}</code></dd></div>
                  </dl>
                </div>
              </div>

              {suggestion.state === "OPEN" ? (
                <div className="tm-generation-suggestion-actions">
                  <button className="tm-primary-button" type="button" disabled={deleted || stale} onClick={() => onApply(suggestion)}>제안 반영</button>
                  <button className="tm-subtle-button" type="button" onClick={() => onIgnore(suggestion)}>무시</button>
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function SuggestionDiffValue({ path, value }: { path: string; value: unknown }) {
  if (value === null || value === undefined || value === "") return <p className="tm-muted">비어 있음</p>;
  if (path === "steps" && Array.isArray(value)) {
    return <ol className="tm-generation-suggestion-step-value">{value.map((step, index) => {
      const item = step as Partial<GenerationSuggestionSemanticSnapshot["steps"][number]>;
      return <li key={`${item.sequence ?? index + 1}-${index}`}><strong>{item.sequence ?? index + 1}. {item.action ?? "동작 없음"}</strong>{item.precondition ? <span>사전조건 · {item.precondition}</span> : null}{item.expectedResult ? <span>기대 결과 · {item.expectedResult}</span> : null}</li>;
    })}</ol>;
  }
  if (Array.isArray(value)) {
    return value.length ? <ul>{value.map((item, index) => <li key={`${String(item)}-${index}`}>{String(item)}</li>)}</ul> : <p className="tm-muted">없음</p>;
  }
  if (typeof value === "boolean") return <p>{value ? "예" : "아니요"}</p>;
  if (typeof value === "object") return <pre>{JSON.stringify(value, null, 2)}</pre>;
  return <p>{String(value)}</p>;
}

export function GenerationSuggestionApplyDialog({
  busy,
  error,
  onClose,
  onConfirm,
  onReload,
  open,
  stale,
  suggestion,
  testCase,
}: {
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: (changeReason: string) => void;
  onReload: () => void;
  open: boolean;
  stale: boolean;
  suggestion: GenerationSuggestion | null;
  testCase: DemoTestCase;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const [changeReason, setChangeReason] = useState("");
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLElement>(open, onClose, busy);

  useEffect(() => {
    if (open && suggestion) setChangeReason(`생성 변경 제안 ${suggestion.id} 검토 후 반영`);
  }, [open, suggestion]);

  if (!open || !suggestion) return null;
  const handleKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !busy) {
      event.preventDefault();
      onClose();
      return;
    }
    onDialogKeyDown(event);
  };
  const validReason = Boolean(changeReason.trim());

  return <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target && !busy) onClose(); }}>
    <section ref={dialogRef} className="tm-cycle-modal tm-generation-suggestion-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1} onKeyDown={handleKeyDown}>
      <header><h2 id={titleId}>생성 변경 제안 반영</h2><button className="tm-modal-close" disabled={busy} type="button" aria-label="생성 변경 제안 반영 닫기" onClick={onClose}>×</button></header>
      <div className="tm-generation-suggestion-dialog__body">
        <p id={descriptionId}><strong>{testCase.key} · {testCase.summary}</strong>의 기준 v{suggestion.baseline.caseVersion}에서 새 v{suggestion.baseline.caseVersion + 1}을 만듭니다.</p>
        <p>확인 전에는 현재 케이스를 변경하지 않습니다. 반영하면 봉인된 after 스냅샷으로 하나의 immutable 버전이 추가됩니다.</p>
        <label><span>변경 사유</span><textarea autoFocus rows={3} value={changeReason} disabled={busy} onChange={(event) => setChangeReason(event.target.value)} /></label>
        {error ? <div className="tm-generation-suggestion-dialog__error" role="alert">{error}{stale ? <button className="tm-subtle-button" type="button" onClick={onReload}>최신 상태 다시 불러오기</button> : null}</div> : null}
      </div>
      <footer><button className="tm-subtle-button" disabled={busy} type="button" onClick={onClose}>취소</button><button className="tm-primary-button" disabled={busy || !validReason || stale} type="button" onClick={() => onConfirm(changeReason.trim())}>{busy ? "반영 중…" : "제안 반영"}</button></footer>
    </section>
  </div>;
}

export function GenerationSuggestionIgnoreDialog({
  busy,
  error,
  onClose,
  onConfirm,
  open,
  suggestion,
  testCase,
}: {
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: (reason: string) => void;
  open: boolean;
  suggestion: GenerationSuggestion | null;
  testCase: DemoTestCase;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const [reason, setReason] = useState("");
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLElement>(open, onClose, busy);

  useEffect(() => {
    if (open) setReason("");
  }, [open, suggestion?.id]);

  if (!open || !suggestion) return null;
  const handleKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !busy) {
      event.preventDefault();
      onClose();
      return;
    }
    onDialogKeyDown(event);
  };

  return <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target && !busy) onClose(); }}>
    <section ref={dialogRef} className="tm-cycle-modal tm-generation-suggestion-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1} onKeyDown={handleKeyDown}>
      <header><h2 id={titleId}>생성 변경 제안 무시</h2><button className="tm-modal-close" disabled={busy} type="button" aria-label="생성 변경 제안 무시 닫기" onClick={onClose}>×</button></header>
      <div className="tm-generation-suggestion-dialog__body">
        <p id={descriptionId}><strong>{testCase.key} · {testCase.summary}</strong>의 제안을 무시합니다.</p>
        <p>무시 결정은 append-only 기록으로 남지만 테스트케이스 내용과 버전은 변경하지 않습니다.</p>
        <label><span>무시 사유 · 필수</span><textarea autoFocus rows={3} value={reason} disabled={busy} onChange={(event) => setReason(event.target.value)} placeholder="사람이 작성한 동작을 유지하는 이유를 입력하세요." /></label>
        {error ? <div className="tm-generation-suggestion-dialog__error" role="alert">{error}</div> : null}
      </div>
      <footer><button className="tm-subtle-button" disabled={busy} type="button" onClick={onClose}>취소</button><button className="tm-primary-button" disabled={busy || !reason.trim()} type="button" onClick={() => onConfirm(reason.trim())}>{busy ? "무시 처리 중…" : "제안 무시"}</button></footer>
    </section>
  </div>;
}
