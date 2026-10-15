import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Link, useNavigate } from "react-router-dom";

import { CasePersistenceBanner } from "./case-persistence-banner";
import { useAccessibleDialog } from "./dialog-accessibility";
import { appNavigationOptions } from "./navigation-policy";
import {
  getWorkItemKey,
  normalizeWorkItemKey,
  useTestManagement,
  type DemoWorkItem,
  type DemoTestCase,
  type DomainId,
  type NewWorkItemInput,
  type TestCaseKey,
  type TestCasePriority,
  type TestDomain,
  type WorkItemKey,
} from "./test-management-demo";
import {
  validateWorkItemDraft,
  type WorkItemDraft,
  type WorkItemDraftErrors,
} from "./work-item-create";

interface PointerDragState {
  caseKey: TestCaseKey;
  summary: string;
  x: number;
  y: number;
}

type LibraryTab = "ACTIVE" | "DELETED";

const priorityLabels: Record<TestCasePriority, string> = {
  HIGHEST: "Highest",
  HIGH: "High",
  MEDIUM: "Medium",
  LOW: "Low",
};

export function WorkItemCreateModal({
  categories,
  initialCategoryId,
  workItems,
  onCancel,
  onSave,
}: {
  categories: TestDomain[];
  initialCategoryId: DomainId | null;
  workItems: DemoWorkItem[];
  onCancel: () => void;
  onSave: (input: NewWorkItemInput) => Promise<boolean>;
}) {
  const writableCategories = categories.filter((category) => !category.protected);
  const [draft, setDraft] = useState<WorkItemDraft>({
    domainId: writableCategories.some((category) => category.id === initialCategoryId)
      ? initialCategoryId ?? ""
      : writableCategories[0]?.id ?? "",
    key: "",
    summary: "",
    description: "",
    owner: "",
    labels: "",
  });
  const [errors, setErrors] = useState<WorkItemDraftErrors>({});
  const [saving, setSaving] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLFormElement>(true, onCancel, saving);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextErrors = validateWorkItemDraft(draft, writableCategories, workItems);
    setErrors(nextErrors);
    const key = normalizeWorkItemKey(draft.key);
    if (Object.keys(nextErrors).length > 0 || !draft.domainId || !key) return;
    setSaving(true);
    setRequestError(null);
    try {
      const saved = await onSave({
        domainId: draft.domainId,
        key,
        summary: draft.summary,
        description: draft.description,
        owner: draft.owner,
        labels: draft.labels.split(",").map((label) => label.trim()).filter(Boolean),
      });
      if (!saved) setRequestError("API 저장에 실패했습니다. Test Library 오류 메시지를 확인하세요.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onCancel(); }}>
      <form ref={dialogRef} className="tm-cycle-modal tm-work-item-create-modal" role="dialog" aria-modal="true" aria-labelledby="work-item-create-title" aria-describedby="work-item-create-help" onKeyDown={onDialogKeyDown} onSubmit={(event) => void submit(event)}>
        <header><div><h2 id="work-item-create-title">상위일감 만들기</h2><p id="work-item-create-help">저장하기 전에는 상위일감이 생성되지 않습니다.</p></div><button className="tm-modal-close" type="button" aria-label="상위일감 만들기 닫기" onClick={onCancel}>×</button></header>
        <div className="tm-work-item-create-modal__body">
          <label className="tm-field"><span>카테고리 <b aria-hidden="true">*</b></span><select autoFocus aria-invalid={Boolean(errors.domainId)} value={draft.domainId} onChange={(event) => { setDraft((current) => ({ ...current, domainId: event.target.value as DomainId })); setErrors((current) => ({ ...current, domainId: undefined })); }}><option value="">카테고리 선택</option>{writableCategories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select>{errors.domainId ? <small className="tm-field-error" role="alert">{errors.domainId}</small> : null}</label>
          <label className="tm-field"><span>키 <b aria-hidden="true">*</b></span><input aria-invalid={Boolean(errors.key)} value={draft.key} onChange={(event) => { setDraft((current) => ({ ...current, key: event.target.value })); setErrors((current) => ({ ...current, key: undefined })); }} placeholder="WORK-531" />{errors.key ? <small className="tm-field-error" role="alert">{errors.key}</small> : <small>WORK-숫자 형식의 고유 키</small>}</label>
          <label className="tm-field tm-work-item-create-modal__wide"><span>서머리 <b aria-hidden="true">*</b></span><input aria-invalid={Boolean(errors.summary)} value={draft.summary} onChange={(event) => { setDraft((current) => ({ ...current, summary: event.target.value })); setErrors((current) => ({ ...current, summary: undefined })); }} placeholder="상위일감 제목" />{errors.summary ? <small className="tm-field-error" role="alert">{errors.summary}</small> : null}</label>
          <label className="tm-field tm-work-item-create-modal__wide"><span>설명</span><textarea rows={4} value={draft.description} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} placeholder="검증 범위와 배경을 입력하세요." /></label>
          <label className="tm-field"><span>담당자</span><input value={draft.owner} onChange={(event) => setDraft((current) => ({ ...current, owner: event.target.value }))} placeholder="미지정" /></label>
          <label className="tm-field"><span>라벨</span><input value={draft.labels} onChange={(event) => setDraft((current) => ({ ...current, labels: event.target.value }))} placeholder="smoke, regression" /><small>쉼표로 구분</small></label>
        </div>
        {requestError ? <p className="tm-field-error" role="alert">{requestError}</p> : null}
        <footer><small>필수 항목을 확인한 뒤 명시적으로 저장하세요.</small><div><button className="tm-subtle-button" type="button" onClick={onCancel} disabled={saving}>취소</button><button className="tm-primary-button" type="submit" disabled={saving}>{saving ? "저장 중…" : "상위일감 저장"}</button></div></footer>
      </form>
    </div>
  );
}

function CategoryNameEditor({ domain }: { domain: TestDomain }) {
  const { renameDomain } = useTestManagement();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(domain.name);
  const [saving, setSaving] = useState(false);
  if (domain.protected) return <span className="tm-system-domain-badge">시스템</span>;
  const save = async () => {
    const value = name.trim();
    if (value && value !== domain.name) {
      setSaving(true);
      const saved = await renameDomain(domain.id, value);
      if (!saved) setName(domain.name);
      setSaving(false);
    } else setName(domain.name);
    setEditing(false);
  };

  if (editing) {
    return <form className="tm-category-name-form" onSubmit={(event) => { event.preventDefault(); void save(); }}><input aria-label={`${domain.name} 카테고리명`} value={name} onChange={(event) => setName(event.target.value)} disabled={saving} autoFocus /><button type="submit" aria-label="카테고리명 저장" disabled={saving}>✓</button><button type="button" aria-label="카테고리명 수정 취소" disabled={saving} onClick={() => { setName(domain.name); setEditing(false); }}>×</button></form>;
  }
  return <button className="tm-category-edit" type="button" aria-label={`${domain.name} 카테고리명 수정`} onClick={() => setEditing(true)}>✎</button>;
}

function InlineCaseSummary({ testCase, editing, onEditingChange }: { testCase: DemoTestCase; editing: boolean; onEditingChange: (editing: boolean) => void }) {
  const { saveCase } = useTestManagement();
  const [summary, setSummary] = useState(testCase.summary);
  useEffect(() => setSummary(testCase.summary), [testCase.summary]);
  const save = () => {
    const value = summary.trim();
    if (value && value !== testCase.summary) void saveCase({ ...testCase, summary: value });
    else setSummary(testCase.summary);
    onEditingChange(false);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") save();
    if (event.key === "Escape") { setSummary(testCase.summary); onEditingChange(false); }
  };
  if (editing) return <input className="tm-case-summary-input" aria-label={`${testCase.key} 서머리`} value={summary} onClick={(event) => event.stopPropagation()} onChange={(event) => setSummary(event.target.value)} onBlur={save} onKeyDown={onKeyDown} autoFocus />;
  return <span className="tm-case-summary">{testCase.summary}</span>;
}

function CaseRow({
  testCase,
  hoveredCaseKey,
  actionOpenKey,
  editingSummaryKey,
  onActionOpen,
  onEditingSummary,
  onPointerDragStart,
  onNativeReorder,
  onCopy,
  onDelete,
  onRestore,
  moving,
}: {
  testCase: DemoTestCase;
  hoveredCaseKey: TestCaseKey | null;
  actionOpenKey: TestCaseKey | null;
  editingSummaryKey: TestCaseKey | null;
  onActionOpen: (caseKey: TestCaseKey | null) => void;
  onEditingSummary: (caseKey: TestCaseKey | null) => void;
  onPointerDragStart: (event: ReactPointerEvent<HTMLButtonElement>, testCase: DemoTestCase) => void;
  onNativeReorder: (sourceKey: TestCaseKey, targetKey: TestCaseKey) => void;
  onCopy: (caseKey: TestCaseKey) => void;
  onDelete: (caseKey: TestCaseKey) => void;
  onRestore: (caseKey: TestCaseKey) => void;
  moving: boolean;
}) {
  const navigate = useNavigate();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const openDetail = () => void navigate(`/test-cases/${testCase.key}`, appNavigationOptions.openDetail);
  return (
    <div
      className={`tm-case-row tm-case-row--work-item${hoveredCaseKey === testCase.key ? " is-reorder-target" : ""}${testCase.deletedAt ? " is-deleted" : ""}${moving ? " is-moving" : ""}`}
      data-case-key={testCase.key}
      role="link"
      tabIndex={0}
      aria-label={`${testCase.key} ${testCase.summary} 상세 보기`}
      onClick={(event) => { if (!(event.target as HTMLElement).closest("button,input,a,select,textarea")) openDetail(); }}
      onKeyDown={(event) => { if (event.key === "Enter" && !(event.target as HTMLElement).closest("button,input,a,select,textarea")) openDetail(); }}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => { event.preventDefault(); event.stopPropagation(); const sourceKey = event.dataTransfer.getData("text/test-case-key") as TestCaseKey; if (sourceKey) onNativeReorder(sourceKey, testCase.key); }}
    >
      <button className="tm-drag-handle" type="button" draggable={!testCase.deletedAt && !moving} disabled={Boolean(testCase.deletedAt) || moving} aria-label={`${testCase.key} 순서 이동`} title="같은 상위 일감 안에서 순서를 바꾸거나 다른 일감으로 이동" onClick={(event) => event.stopPropagation()} onDragStart={(event) => { event.stopPropagation(); event.dataTransfer.setData("text/test-case-key", testCase.key); event.dataTransfer.effectAllowed = "move"; }} onPointerDown={(event) => { if (!testCase.deletedAt && !moving) onPointerDragStart(event, testCase); }}>⋮⋮</button>
      <span className="tm-case-type-icon" aria-hidden="true">T</span>
      <div className="tm-case-primary"><InlineCaseSummary testCase={testCase} editing={!testCase.deletedAt && editingSummaryKey === testCase.key} onEditingChange={(editing) => onEditingSummary(editing ? testCase.key : null)} /><div className="tm-case-secondary"><span className="tm-case-key">{testCase.key}</span><span>v{testCase.version}</span>{testCase.deletedAt ? <span className="tm-deleted-chip">삭제됨</span> : null}{testCase.copiedFrom ? <span>원본 {testCase.copiedFrom}</span> : null}{testCase.labels.slice(0, 3).map((label) => <span className="tm-label" key={label}>{label}</span>)}</div></div>
      <span className={`tm-priority tm-priority--${testCase.priority.toLowerCase()}`}><span aria-hidden="true">{testCase.priority === "HIGHEST" || testCase.priority === "HIGH" ? "↑" : testCase.priority === "LOW" ? "↓" : "="}</span>{priorityLabels[testCase.priority]}</span>
      <span className="tm-owner">{testCase.owner}</span>
      <div className="tm-row-actions"><button className="tm-row-more" type="button" aria-label={`${testCase.key} 작업 메뉴`} aria-expanded={actionOpenKey === testCase.key} onClick={(event) => { event.stopPropagation(); setConfirmDelete(false); onActionOpen(actionOpenKey === testCase.key ? null : testCase.key); }}>•••</button>{actionOpenKey === testCase.key ? <div className="tm-row-action-drawer" onClick={(event) => event.stopPropagation()}>{testCase.deletedAt ? <button type="button" onClick={() => { onRestore(testCase.key); onActionOpen(null); }}>삭제 복원</button> : confirmDelete ? <><span>삭제할까요?</span><button type="button" onClick={() => setConfirmDelete(false)}>취소</button><button className="is-danger" type="button" onClick={() => { onDelete(testCase.key); onActionOpen(null); }}>삭제</button></> : <><button type="button" onClick={() => { onEditingSummary(testCase.key); onActionOpen(null); }}>서머리 수정</button><button type="button" onClick={() => { onCopy(testCase.key); onActionOpen(null); }}>복사본 만들기</button><button className="is-danger" type="button" onClick={() => setConfirmDelete(true)}>삭제</button></>}</div> : null}</div>
    </div>
  );
}

function WorkItemCard({
  workItemKey,
  summary,
  testCases,
  hoveredWorkItemKey,
  hoveredCaseKey,
  actionOpenKey,
  editingSummaryKey,
  onActionOpen,
  onEditingSummary,
  onPointerDragStart,
  onNativeMove,
  onNativeReorder,
  onCopy,
  onDelete,
  onRestore,
  movingCaseKey,
  selected,
}: {
  workItemKey: WorkItemKey;
  summary: string;
  testCases: DemoTestCase[];
  hoveredWorkItemKey: WorkItemKey | null;
  hoveredCaseKey: TestCaseKey | null;
  actionOpenKey: TestCaseKey | null;
  editingSummaryKey: TestCaseKey | null;
  onActionOpen: (caseKey: TestCaseKey | null) => void;
  onEditingSummary: (caseKey: TestCaseKey | null) => void;
  onPointerDragStart: (event: ReactPointerEvent<HTMLButtonElement>, testCase: DemoTestCase) => void;
  onNativeMove: (caseKey: TestCaseKey, workItemKey: WorkItemKey) => void;
  onNativeReorder: (sourceKey: TestCaseKey, targetKey: TestCaseKey) => void;
  onCopy: (caseKey: TestCaseKey) => void;
  onDelete: (caseKey: TestCaseKey) => void;
  onRestore: (caseKey: TestCaseKey) => void;
  movingCaseKey: TestCaseKey | null;
  selected: boolean;
}) {
  const [collapsed, setCollapsed] = useState(true);
  const caseTableId = `work-item-cases-${workItemKey}`;
  return (
    <section
      className={`tm-work-item-block tm-work-item-block--interactive${hoveredWorkItemKey === workItemKey ? " is-drop-target" : ""}${selected ? " is-selected" : ""}${testCases.length === 0 ? " tm-work-item-block--empty" : ""}${collapsed ? " is-collapsed" : ""}`}
      data-drop-work-item-key={workItemKey}
      data-work-item-key={workItemKey}
      aria-current={selected ? "true" : undefined}
      tabIndex={selected ? -1 : undefined}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => { event.preventDefault(); const caseKey = event.dataTransfer.getData("text/test-case-key") as TestCaseKey; if (caseKey) onNativeMove(caseKey, workItemKey); }}
    >
      <div className="tm-work-item-header" onClick={(event) => { if (!(event.target as HTMLElement).closest("a,button")) setCollapsed((value) => !value); }}>
        <span className="tm-work-item-icon" aria-hidden="true">▣</span>
        <div>
          <Link className="tm-work-item-header--link" to={`/work-items/${workItemKey}`} onClick={(event) => event.stopPropagation()}><strong>{workItemKey} · {summary}</strong></Link>
          <small>상위 일감 · {testCases.length} test cases · 일감명 클릭 시 상세 보기</small>
        </div>
        <button className={`tm-work-item-collapse${collapsed ? " is-collapsed" : ""}`} type="button" aria-expanded={!collapsed} aria-controls={caseTableId} aria-label={`${workItemKey} 테스트케이스 ${collapsed ? "펼치기" : "접기"}`} title={collapsed ? "테스트케이스 펼치기" : "테스트케이스 접기"} onClick={() => setCollapsed((value) => !value)}><span aria-hidden="true" /></button>
      </div>
      <div id={caseTableId} className="tm-case-table tm-case-table--work-item" role="table" aria-label={`${workItemKey} 테스트케이스`} hidden={collapsed}>
        {testCases.map((testCase) => <CaseRow key={testCase.key} testCase={testCase} hoveredCaseKey={hoveredCaseKey} actionOpenKey={actionOpenKey} editingSummaryKey={editingSummaryKey} onActionOpen={onActionOpen} onEditingSummary={onEditingSummary} onPointerDragStart={onPointerDragStart} onNativeReorder={onNativeReorder} onCopy={onCopy} onDelete={onDelete} onRestore={onRestore} moving={movingCaseKey === testCase.key} />)}
        {testCases.length === 0 ? <div className="tm-work-item-empty-row" role="row"><span className="tm-work-item-empty-row__icon" aria-hidden="true">↳</span><span className="tm-work-item-empty-row__copy" role="cell"><strong>이 상위 일감에 테스트케이스가 없습니다.</strong><small>테스트케이스를 이 영역으로 드래그해 이동할 수 있습니다.</small></span></div> : null}
      </div>
    </section>
  );
}

export function FolderLibraryPage() {
  const {
    state,
    persistence,
    loadLibrary,
    copyCase,
    deleteCase,
    restoreCase,
    createWorkItem,
    createDomain,
    deleteDomain,
    moveCaseToWorkItem,
    reorderCase,
  } = useTestManagement();
  const navigate = useNavigate();
  const categories = useMemo(() => [...state.domains].filter((domain) => domain.parentId === null).sort((a, b) => a.sortOrder - b.sortOrder), [state.domains]);
  const canonicalCaseCount = useMemo(
    () => categories.reduce((total, category) => total + (category.activeTestCaseCount ?? 0), 0),
    [categories],
  );
  const [selectedCategoryId, setSelectedCategoryId] = useState<DomainId | null>(null);
  const [search, setSearch] = useState("");
  const [priorityFilter, setPriorityFilter] = useState<"" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL">("");
  const [labelFilter, setLabelFilter] = useState("");
  const [libraryTab, setLibraryTab] = useState<LibraryTab>("ACTIVE");
  const [notice, setNotice] = useState<string | null>(null);
  const [addingCategory, setAddingCategory] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState("");
  const [pointerDrag, setPointerDrag] = useState<PointerDragState | null>(null);
  const [hoveredWorkItemKey, setHoveredWorkItemKey] = useState<WorkItemKey | null>(null);
  const [hoveredCaseKey, setHoveredCaseKey] = useState<TestCaseKey | null>(null);
  const [actionOpenKey, setActionOpenKey] = useState<TestCaseKey | null>(null);
  const [editingSummaryKey, setEditingSummaryKey] = useState<TestCaseKey | null>(null);
  const [movingCaseKey, setMovingCaseKey] = useState<TestCaseKey | null>(null);
  const [workItemModalOpen, setWorkItemModalOpen] = useState(false);
  const [selectedWorkItemKey, setSelectedWorkItemKey] = useState<WorkItemKey | null>(null);
  const moveInFlightRef = useRef(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const deletedOnly = libraryTab === "DELETED";

  const query = useMemo(() => ({
    ...(search.trim() ? { search: search.trim() } : {}),
    ...(selectedCategoryId ? { categoryIds: [selectedCategoryId] } : {}),
    ...(priorityFilter ? { priorities: [priorityFilter] } : {}),
    ...(labelFilter.trim() ? { labels: [labelFilter.trim()], labelMatch: "ANY" as const } : {}),
    ...(deletedOnly ? { includeDeleted: true, deletedOnly: true } : {}),
    limit: 50,
  }), [deletedOnly, labelFilter, priorityFilter, search, selectedCategoryId]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => void loadLibrary(query), 250);
    return () => window.clearTimeout(timeoutId);
  }, [loadLibrary, query]);

  const filteredCases = useMemo(() => {
    const value = search.trim().toLocaleLowerCase("ko-KR");
    return state.testCases.filter((testCase) => {
      if (selectedCategoryId && testCase.domainId !== selectedCategoryId) return false;
      if (priorityFilter && (testCase.priority === "HIGHEST" ? "CRITICAL" : testCase.priority) !== priorityFilter) return false;
      if (labelFilter.trim() && !testCase.labels.some((label) => label.toLocaleLowerCase("ko-KR") === labelFilter.trim().toLocaleLowerCase("ko-KR"))) return false;
      if (deletedOnly ? !testCase.deletedAt : Boolean(testCase.deletedAt)) return false;
      return !value || [testCase.key, testCase.summary, testCase.owner, testCase.sourceWorkItem, ...testCase.labels].some((part) => part.toLocaleLowerCase("ko-KR").includes(value));
    });
  }, [deletedOnly, labelFilter, priorityFilter, search, selectedCategoryId, state.testCases]);

  const hasCaseFilters = Boolean(search.trim() || priorityFilter || labelFilter.trim() || deletedOnly);
  const visibleWorkItems = useMemo(
    () => state.workItems.filter((workItem) => (
      (!selectedCategoryId || workItem.domainId === selectedCategoryId)
      && (!hasCaseFilters || filteredCases.some((testCase) => getWorkItemKey(testCase.sourceWorkItem) === workItem.key))
    )),
    [filteredCases, hasCaseFilters, selectedCategoryId, state.workItems],
  );

  useEffect(() => {
    if (!selectedWorkItemKey) return;
    const element = document.querySelector<HTMLElement>(`[data-work-item-key="${selectedWorkItemKey}"]`);
    element?.focus({ preventScroll: true });
    element?.scrollIntoView({ block: "nearest" });
  }, [selectedWorkItemKey, visibleWorkItems]);

  const moveCaseOnce = useCallback(async (
    caseKey: TestCaseKey,
    target: { type: "WORK_ITEM"; workItemKey: WorkItemKey } | { type: "CASE"; targetCaseKey: TestCaseKey },
  ) => {
    if (moveInFlightRef.current || (target.type === "CASE" && target.targetCaseKey === caseKey)) return;
    moveInFlightRef.current = true;
    setMovingCaseKey(caseKey);
    try {
      if (target.type === "CASE") await reorderCase(caseKey, target.targetCaseKey);
      else await moveCaseToWorkItem(caseKey, target.workItemKey);
    } finally {
      moveInFlightRef.current = false;
      setMovingCaseKey(null);
    }
  }, [moveCaseToWorkItem, reorderCase]);

  const activePointerCaseKey = pointerDrag?.caseKey ?? null;
  useEffect(() => {
    if (!activePointerCaseKey) return;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    const readTargets = (x: number, y: number) => {
      const element = document.elementFromPoint(x, y);
      const caseValue = element?.closest<HTMLElement>("[data-case-key]")?.dataset.caseKey;
      const workItemValue = element?.closest<HTMLElement>("[data-drop-work-item-key]")?.dataset.dropWorkItemKey;
      return {
        caseKey: caseValue?.startsWith("TC-") ? caseValue as TestCaseKey : null,
        workItemKey: workItemValue?.startsWith("WORK-") ? workItemValue as WorkItemKey : null,
      };
    };
    const move = (event: PointerEvent) => {
      event.preventDefault();
      const targets = readTargets(event.clientX, event.clientY);
      setPointerDrag((current) => {
        if (!current) return null;
        const next = { ...current, x: event.clientX, y: event.clientY };
        return next;
      });
      setHoveredCaseKey(targets.caseKey === activePointerCaseKey ? null : targets.caseKey);
      setHoveredWorkItemKey(targets.workItemKey);
    };
    const clear = () => {
      setPointerDrag(null);
      setHoveredCaseKey(null);
      setHoveredWorkItemKey(null);
    };
    const up = (event: PointerEvent) => {
      const targets = readTargets(event.clientX, event.clientY);
      const source = state.testCases.find((testCase) => testCase.key === activePointerCaseKey);
      const target = targets.caseKey ? state.testCases.find((testCase) => testCase.key === targets.caseKey) : undefined;
      if (source && target && source.key !== target.key) {
        void moveCaseOnce(source.key, { type: "CASE", targetCaseKey: target.key });
      } else if (targets.workItemKey) {
        void moveCaseOnce(activePointerCaseKey, { type: "WORK_ITEM", workItemKey: targets.workItemKey });
      }
      clear();
    };
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", up, { once: true });
    window.addEventListener("pointercancel", clear, { once: true });
    window.addEventListener("blur", clear, { once: true });
    return () => { document.body.style.userSelect = previousUserSelect; window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", clear); window.removeEventListener("blur", clear); };
  }, [activePointerCaseKey, moveCaseOnce, state.testCases]);

  const startPointerDrag = (event: ReactPointerEvent<HTMLButtonElement>, testCase: DemoTestCase) => {
    if (event.button !== 0 || moveInFlightRef.current) return;
    event.preventDefault();
    const next = { caseKey: testCase.key, summary: testCase.summary, x: event.clientX, y: event.clientY };
    setPointerDrag(next);
  };

  const submitCategory = async () => {
    const value = newCategoryName.trim();
    if (!value) return;
    const id = await createDomain(value);
    if (!id) return;
    setSelectedCategoryId(id); setNewCategoryName(""); setAddingCategory(false); setNotice(`${value} 카테고리를 생성했습니다.`);
  };

  const deleteCategory = async (domain: TestDomain) => {
    if (domain.protected) { setNotice("AI 생성함은 자동 생성 결과를 보관하는 시스템 카테고리라 삭제할 수 없습니다."); return; }
    const hasContent = (domain.workItemCount ?? state.workItems.filter((workItem) => workItem.domainId === domain.id).length) > 0
      || (domain.activeTestCaseCount ?? 0) > 0;
    if (hasContent) { setNotice(`${domain.name} 카테고리에 상위 일감 또는 테스트케이스가 있어 삭제할 수 없습니다.`); return; }
    if (!await deleteDomain(domain.id)) return;
    if (selectedCategoryId === domain.id) setSelectedCategoryId(categories.find((candidate) => candidate.id !== domain.id)?.id ?? null);
    setNotice(`${domain.name} 카테고리를 삭제했습니다.`);
  };

  const copyTestCase = async (caseKey: TestCaseKey) => {
    const copiedKey = await copyCase(caseKey);
    if (copiedKey) setNotice(`${caseKey}를 ${copiedKey}(으)로 복사했습니다.`);
  };

  const removeTestCase = async (caseKey: TestCaseKey) => {
    if (await deleteCase(caseKey)) {
      setNotice(`${caseKey}를 삭제했습니다. 삭제됨 탭에서 읽거나 복원할 수 있습니다.`);
    }
  };

  const restoreDeletedCase = async (caseKey: TestCaseKey) => {
    if (await restoreCase(caseKey)) {
      setNotice(`${caseKey}를 복원했습니다.`);
      await loadLibrary(query);
    }
  };

  const resetFilters = () => {
    setSearch("");
    setPriorityFilter("");
    setLabelFilter("");
  };

  const saveNewWorkItem = async (input: NewWorkItemInput) => {
    const key = await createWorkItem(input);
    if (!key) return false;
    resetFilters();
    setSelectedCategoryId(input.domainId);
    setSelectedWorkItemKey(key);
    setWorkItemModalOpen(false);
    setNotice(`${key} 상위일감을 생성했습니다.`);
    return true;
  };

  return (
    <section className="tm-library-page" aria-labelledby="test-library-title">
      <header className="tm-page-header"><div><h1 id="test-library-title">Test Library</h1><p>카테고리별 상위 일감과 하위 테스트케이스를 한 화면에서 관리합니다.</p></div><div className="tm-page-header__actions"><button className="tm-subtle-button" type="button" onClick={() => setWorkItemModalOpen(true)}>+ 상위일감 만들기</button><button className="tm-primary-button" type="button" onClick={() => void navigate(`/test-cases/new${selectedCategoryId ? `?category=${selectedCategoryId}` : ""}`, appNavigationOptions.openCreate)}>+ 테스트케이스 만들기</button></div></header>
      {persistence.status !== "LOADING" ? <CasePersistenceBanner persistence={persistence} onRetry={() => void loadLibrary(query)} /> : null}
      {notice ? <div className="tm-notice" role="status"><span>{notice}</span><button type="button" aria-label="알림 닫기" onClick={() => setNotice(null)}>×</button></div> : null}
      <div className="tm-library-grid" data-layout="category-work-items">
        <aside className="tm-domain-panel" aria-label="테스트 카테고리">
          <div className="tm-panel-heading"><div><h2>테스트 카테고리</h2><p>클릭하여 일감 목록 전환</p></div><button type="button" title="카테고리 추가" onClick={() => setAddingCategory(true)}>+</button></div>
          {addingCategory ? <form className="tm-create-domain-form" onSubmit={(event) => { event.preventDefault(); void submitCategory(); }}><input aria-label="새 카테고리명" value={newCategoryName} onChange={(event) => setNewCategoryName(event.target.value)} placeholder="카테고리명" autoFocus /><button className="tm-primary-button tm-create-domain-form__submit" type="submit" disabled={!newCategoryName.trim()}>생성</button><button className="tm-subtle-button tm-create-domain-form__cancel" type="button" onClick={() => { setNewCategoryName(""); setAddingCategory(false); }}>취소</button></form> : null}
          <button className={`tm-all-cases${selectedCategoryId === null ? " is-selected" : ""}`} type="button" onClick={() => setSelectedCategoryId(null)}><span aria-hidden="true">▦</span><span>전체 테스트케이스</span><strong>{canonicalCaseCount}</strong></button>
          <div className="tm-root-domain-list">{categories.map((domain) => <div className={`tm-root-domain${selectedCategoryId === domain.id ? " is-selected" : ""}${domain.protected ? " is-system" : ""}`} key={domain.id}><button className="tm-root-domain__select" type="button" aria-label={`${domain.name} 카테고리 선택`} onClick={() => setSelectedCategoryId(domain.id)}><span className="tm-folder-icon" aria-hidden="true">◆</span><span>{domain.name}</span><strong>{domain.activeTestCaseCount ?? 0}</strong></button><CategoryNameEditor domain={domain} />{domain.protected ? null : <button className="tm-root-domain__delete" type="button" aria-label={`${domain.name} 카테고리 삭제`} onClick={() => void deleteCategory(domain)}>×</button>}</div>)}</div>
          <div className="tm-domain-help"><span aria-hidden="true">↕</span><p><strong>테스트케이스 드래그</strong><br />같은 일감 안에서 순서를 바꾸거나 다른 일감으로 이동합니다.</p></div>
        </aside>
        <main className="tm-case-panel">
          <div className="tm-case-panel__heading"><div><p className="tm-breadcrumb">Test Library / {categories.find((domain) => domain.id === selectedCategoryId)?.name ?? "전체"}</p><h2>{categories.find((domain) => domain.id === selectedCategoryId)?.name ?? "전체 카테고리"}</h2></div><span className="tm-result-count">{persistence.total} results · {visibleWorkItems.length} work items</span></div>
          <div className="tm-tabs" role="tablist" aria-label="테스트케이스 목록 상태">
            <button className={libraryTab === "ACTIVE" ? "is-active" : ""} type="button" role="tab" aria-selected={libraryTab === "ACTIVE"} onClick={() => setLibraryTab("ACTIVE")}>활성</button>
            <button className={libraryTab === "DELETED" ? "is-active" : ""} type="button" role="tab" aria-selected={libraryTab === "DELETED"} title="삭제 항목 포함 대신 삭제된 항목만 표시" onClick={() => setLibraryTab("DELETED")}>삭제됨</button>
          </div>
          <div className="tm-toolbar tm-toolbar--compound"><label className="tm-search"><span aria-hidden="true">⌕</span><input ref={searchInputRef} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="키, 서머리, 상위 일감, 담당자, 라벨 검색" aria-label="테스트케이스 검색" /></label><label className="tm-filter-field"><span>우선순위</span><select aria-label="우선순위 필터" value={priorityFilter} onChange={(event) => setPriorityFilter(event.target.value as typeof priorityFilter)}><option value="">전체</option><option value="CRITICAL">Highest</option><option value="HIGH">High</option><option value="MEDIUM">Medium</option><option value="LOW">Low</option></select></label><label className="tm-filter-field"><span>라벨</span><input aria-label="라벨 필터" value={labelFilter} onChange={(event) => setLabelFilter(event.target.value)} placeholder="예: smoke" /></label><button className="tm-subtle-button" type="button" onClick={resetFilters}>필터 초기화</button></div>
          <div className="tm-work-item-library" aria-busy={persistence.status === "LOADING"}>{visibleWorkItems.length > 0 ? visibleWorkItems.map((workItem) => <WorkItemCard key={workItem.key} workItemKey={workItem.key} summary={workItem.summary} testCases={filteredCases.filter((testCase) => getWorkItemKey(testCase.sourceWorkItem) === workItem.key)} hoveredWorkItemKey={hoveredWorkItemKey} hoveredCaseKey={hoveredCaseKey} actionOpenKey={actionOpenKey} editingSummaryKey={editingSummaryKey} onActionOpen={setActionOpenKey} onEditingSummary={setEditingSummaryKey} onPointerDragStart={startPointerDrag} onNativeMove={(caseKey, workItemKey) => void moveCaseOnce(caseKey, { type: "WORK_ITEM", workItemKey })} onNativeReorder={(caseKey, targetKey) => void moveCaseOnce(caseKey, { type: "CASE", targetCaseKey: targetKey })} onCopy={(caseKey) => void copyTestCase(caseKey)} onDelete={(caseKey) => void removeTestCase(caseKey)} onRestore={(caseKey) => void restoreDeletedCase(caseKey)} movingCaseKey={movingCaseKey} selected={selectedWorkItemKey === workItem.key} />) : <div className="tm-empty-state"><span aria-hidden="true">⌕</span><strong>{persistence.status === "LOADING" ? "테스트케이스를 불러오는 중입니다." : deletedOnly ? "삭제된 테스트케이스가 없습니다." : "표시할 상위 일감이 없습니다."}</strong><p>{deletedOnly ? "삭제된 항목은 이 탭에서만 표시됩니다." : "다른 카테고리를 선택하거나 검색어를 지워보세요."}</p><button type="button" onClick={() => searchInputRef.current?.focus()}>검색하기</button></div>}</div>
          <footer className="tm-case-footer"><span>{filteredCases.length} / {persistence.total} cases</span>{persistence.nextCursor ? <button className="tm-subtle-button tm-compact-button" type="button" onClick={() => void loadLibrary({ ...query, cursor: persistence.nextCursor ?? undefined }, true)}>다음 50개 불러오기</button> : <span>상위 일감 제목을 클릭하면 상세 화면으로 이동합니다.</span>}</footer>
        </main>
      </div>
      {pointerDrag ? <div className="tm-drag-preview" style={{ left: pointerDrag.x + 14, top: pointerDrag.y + 14 }}><span className="tm-case-type-icon">T</span><strong>{pointerDrag.caseKey}</strong><span>{pointerDrag.summary}</span></div> : null}
      {workItemModalOpen ? <WorkItemCreateModal categories={categories} initialCategoryId={selectedCategoryId} workItems={state.workItems} onCancel={() => setWorkItemModalOpen(false)} onSave={saveNewWorkItem} /> : null}
    </section>
  );
}
