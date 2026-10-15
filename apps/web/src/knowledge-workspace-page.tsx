import type {
  HybridSearchResponse,
  KnowledgeEvidencePage,
  KnowledgeNodeDetail,
  KnowledgeNodePage,
  KnowledgeNodeResource,
  KnowledgeStatusAuditPage,
  OntologyExplorerResponse,
} from "@tc/api-client";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import {
  exploreKnowledge,
  knowledgeApi,
  knowledgeErrorMessage,
  listKnowledgeNodes,
  patchKnowledge,
  searchKnowledge,
  type KnowledgeApiClient,
} from "./knowledge-service";

type KnowledgeStatus = KnowledgeNodeResource["status"];
type KnowledgeNodeType = KnowledgeNodeResource["nodeType"];
type Evidence = KnowledgeEvidencePage["items"][number];
type ExplorerEdge = OntologyExplorerResponse["edges"][number];
type SearchItem = HybridSearchResponse["items"][number];
type Citation = SearchItem["citation"];
type SourceReference = KnowledgeNodeResource["primarySource"];

const statuses: KnowledgeStatus[] = ["ACTIVE", "AMBIGUOUS", "SUPERSEDED", "RETRACTED"];
const explorerLanes: KnowledgeNodeType[] = ["FEATURE", "DECISION", "TEST_CASE", "EXECUTION", "ISSUE"];
const statusLabels: Record<KnowledgeStatus, string> = {
  ACTIVE: "현재",
  AMBIGUOUS: "검토 필요",
  SUPERSEDED: "과거",
  RETRACTED: "철회",
};
const nodeTypeLabels: Partial<Record<KnowledgeNodeType, string>> = {
  FEATURE: "기능",
  DECISION: "결정",
  TEST_CASE: "테스트케이스",
  EXECUTION: "실행",
  ISSUE: "이슈",
  REQUIREMENT: "요구사항",
  CONSTRAINT: "제약",
  OPEN_QUESTION: "열린 질문",
  RISK: "리스크",
};
const signalLabels: Record<SearchItem["reason"]["signals"][number], string> = {
  TYPED_GRAPH: "지식 그래프",
  VECTOR: "의미 유사도",
  LIGHTRAG: "LightRAG",
  EXACT_TEXT: "정확한 문구",
  FEATURE_PROXIMITY: "기능 근접도",
  RECENCY: "최신성",
};
const sourceTypeLabels: Record<KnowledgeNodeResource["primarySource"]["sourceType"], string> = {
  SLACK_MESSAGE_REVISION: "Slack 메시지 변경 이력",
  TEST_CASE_VERSION: "테스트케이스 버전",
  EXECUTION_ATTEMPT: "테스트 실행 결과",
  ISSUE_SNAPSHOT: "이슈 스냅샷",
  MANUAL: "직접 등록한 근거",
};
const sourceActionLabels: Record<SourceReference["sourceType"], string> = {
  SLACK_MESSAGE_REVISION: "Slack 원문 revision 열기",
  TEST_CASE_VERSION: "테스트케이스 원장 열기",
  EXECUTION_ATTEMPT: "테스트 실행 원장 열기",
  ISSUE_SNAPSHOT: "이슈 원장 열기",
  MANUAL: "등록 근거 열기",
};
const degradationReasonLabels: Record<HybridSearchResponse["degradationReasons"][number], string> = {
  LIGHTRAG_UNAVAILABLE: "LightRAG 보조 검색 사용 불가",
  VECTOR_UNAVAILABLE: "의미 유사도 검색 사용 불가",
};
const subjectTypeLabels: Record<Evidence["subjectType"], string> = {
  NODE: "지식",
  EDGE: "관계",
};

export interface KnowledgeWorkspacePageProps {
  api?: KnowledgeApiClient;
  autoLoad?: boolean;
  initialNodes?: KnowledgeNodePage;
  initialSearch?: HybridSearchResponse | null;
  initialDetail?: KnowledgeNodeDetail | null;
  initialExplorer?: OntologyExplorerResponse | null;
  initialAudit?: KnowledgeStatusAuditPage | null;
  initialSelectedEdgeId?: string | null;
}

interface EditDraft {
  status: KnowledgeStatus;
  scope: string;
  validFrom: string;
  validTo: string;
  reason: string;
  changedBy: string;
}

function formatDate(value: string | null) {
  if (!value) return "계속 유효";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function dateTimeInput(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function statusClass(status: KnowledgeStatus) {
  return `is-${status.toLowerCase()}`;
}

function sourceReferenceLabel(source: SourceReference) {
  return `${sourceTypeLabels[source.sourceType]} · ${source.sourceRevisionId ? "개별 revision" : "불변 원장 항목"}`;
}

function freshnessLabel(ageSeconds: number, score: number) {
  let age: string;
  if (ageSeconds < 60) age = "1분 미만";
  else if (ageSeconds < 3_600) age = `${Math.floor(ageSeconds / 60)}분`;
  else if (ageSeconds < 86_400) age = `${Math.floor(ageSeconds / 3_600)}시간`;
  else age = `${Math.floor(ageSeconds / 86_400)}일`;
  return `기준 시점부터 ${age} 경과 · 최신성 ${Math.round(score * 100)}점`;
}

function searchReasonLabel(item: SearchItem) {
  const signals = new Set(item.reason.signals);
  const reasons = ["지식 그래프 후보"];
  if (signals.has("EXACT_TEXT")) reasons.push("검색어와 정확히 일치");
  if (signals.has("VECTOR")) reasons.push("의미 유사도 반영");
  if (signals.has("FEATURE_PROXIMITY") && item.featureDistance !== null) {
    reasons.push(`선택 기능과 ${item.featureDistance}단계 연결`);
  } else {
    reasons.push("기능 미선택 또는 연결 경로 없음으로 기능 거리는 미반영");
  }
  if (signals.has("LIGHTRAG")) {
    reasons.push("LightRAG는 점수 가산 없이 보조 신호로 확인");
  }
  reasons.push(`최신성 ${Math.round(item.recency.score * 100)}점 반영`);
  return `${item.rank}위 · 관련도 ${Math.round(item.score * 100)}점: ${reasons.join(" · ")}`;
}

function availableFallbackSignals(reasons: HybridSearchResponse["degradationReasons"]) {
  const labels = ["지식 그래프", "정확 문구", "최신성"];
  if (!reasons.includes("VECTOR_UNAVAILABLE")) labels.splice(1, 0, "의미 유사도");
  if (!reasons.includes("LIGHTRAG_UNAVAILABLE")) labels.push("LightRAG 보조 신호");
  return `${labels.join(" · ")} 사용 가능`;
}

function SourceAction({
  source,
  citation,
  compact = false,
}: {
  source: SourceReference;
  citation?: Citation;
  compact?: boolean;
}) {
  const classes = `tm-k-source-action${compact ? " is-compact" : ""}`;
  if (citation?.deleted) {
    return (
      <span className={`${classes} is-unavailable`} data-source-state="deleted" aria-disabled="true">
        삭제된 출처 · 보존된 근거만 확인 가능
      </span>
    );
  }
  if (citation?.uri) {
    const external = !citation.uri.startsWith("/");
    return (
      <a
        className={classes}
        data-source-state="available"
        href={citation.uri}
        target={external ? "_blank" : undefined}
        rel={external ? "noreferrer" : undefined}
        aria-label={`${citation.label} · ${sourceActionLabels[source.sourceType]}`}
      >
        {sourceActionLabels[source.sourceType]} ↗
      </a>
    );
  }
  const unavailableLabel = citation
    ? source.sourceType === "MANUAL"
      ? "직접 등록 근거 · 연결된 원문 없음"
      : `${sourceTypeLabels[source.sourceType]} · 원장 식별 정보 확인 불가`
    : "상세 근거에서 원문 링크 확인";
  return (
    <span
      className={`${classes} is-unavailable`}
      data-source-state={citation ? "unresolved" : "detail-required"}
      aria-disabled="true"
    >
      {unavailableLabel}
    </span>
  );
}

function groupDecisionNodes(nodes: KnowledgeNodeResource[]) {
  const decisions = nodes.filter((node) => node.nodeType === "DECISION");
  return {
    current: decisions.filter((node) => node.status === "ACTIVE"),
    past: decisions.filter((node) => node.status !== "ACTIVE"),
  };
}

function groupExplorerNodes(explorer: OntologyExplorerResponse | null) {
  return explorerLanes.map((type) => ({
    type,
    nodes: explorer?.nodes.filter((item) => item.node.nodeType === type) ?? [],
  }));
}

function EvidenceCard({ evidence }: { evidence: Evidence }) {
  return (
    <article className={`tm-k-evidence-card${evidence.citation.deleted ? " is-deleted" : ""}`}>
      <header>
        <span className="tm-k-subject-badge">{subjectTypeLabels[evidence.subjectType]} 근거</span>
        <strong>{evidence.citation.label}</strong>
        <span>근거 버전 {evidence.revisionOrdinal}</span>
      </header>
      <p>{evidence.citation.deleted
        ? "원문이 삭제되어 캡처된 식별 정보와 변경 이력만 보존합니다."
        : evidence.citation.excerpt || "원문 발췌가 없습니다."}</p>
      <footer>
        <span>{sourceTypeLabels[evidence.source.sourceType]}</span>
        <span>{formatDate(evidence.citation.occurredAt)} 기준</span>
        <SourceAction source={evidence.source} citation={evidence.citation} />
      </footer>
    </article>
  );
}

function StatusBadge({ status }: { status: KnowledgeStatus }) {
  return <span className={`tm-k-status ${statusClass(status)}`}>{statusLabels[status]} · {status}</span>;
}

function SearchResultRow({
  item,
  selected,
  onSelect,
}: {
  item: SearchItem;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <article
      className={`tm-k-result-row${selected ? " is-selected" : ""}`}
    >
      <span className="tm-k-result-type">{nodeTypeLabels[item.nodeType] ?? item.nodeType}</span>
      <button
        type="button"
        className="tm-k-result-select"
        aria-label={`${item.label} 상세 근거 보기`}
        aria-pressed={selected}
        onClick={onSelect}
      >
        <span className="tm-k-result-heading"><strong>{item.label}</strong><StatusBadge status={item.status} /></span>
        <span className="tm-k-result-content">{item.content}</span>
        <span className="tm-k-result-reason"><b>검색 이유</b> {searchReasonLabel(item)}</span>
        <span className="tm-k-signal-row">
          {item.reason.signals.map((signal) => <i key={signal}>{signalLabels[signal]}</i>)}
        </span>
        <span className="tm-k-detail-cta">상세 근거 보기 →</span>
      </button>
      <aside className="tm-k-result-source">
        <strong>{item.rank}위 · 관련도 {Math.round(item.score * 100)}점</strong>
        <span>{item.citation.label}</span>
        <span>{sourceReferenceLabel(item.source)}</span>
        <span>최신성 기준 {formatDate(item.recency.basisAt)}</span>
        <span>{freshnessLabel(item.recency.ageSeconds, item.recency.score)}</span>
        <span>{item.featureDistance === null
          ? "기능 거리 미반영 · 기능 미선택 또는 연결 경로 없음"
          : `기능과 ${item.featureDistance}단계 연결`}</span>
        {item.degraded && <span className="tm-k-result-degraded">{item.degradationReasons.map((reason) => degradationReasonLabels[reason]).join(" · ")}</span>}
        <SourceAction source={item.source} citation={item.citation} />
      </aside>
    </article>
  );
}

function NodeResultRow({
  node,
  selected,
  onSelect,
}: {
  node: KnowledgeNodeResource;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <article
      className={`tm-k-result-row${selected ? " is-selected" : ""}`}
    >
      <span className="tm-k-result-type">{nodeTypeLabels[node.nodeType] ?? node.nodeType}</span>
      <button
        type="button"
        className="tm-k-result-select"
        aria-label={`${node.label} 상세 근거 보기`}
        aria-pressed={selected}
        onClick={onSelect}
      >
        <span className="tm-k-result-heading"><strong>{node.label}</strong><StatusBadge status={node.status} /></span>
        <span className="tm-k-result-content">{node.content}</span>
        <span className="tm-k-result-reason"><b>목록 이유</b> 선택한 상태·유형 필터와 일치</span>
        <span className="tm-k-detail-cta">상세 근거 보기 →</span>
      </button>
      <aside className="tm-k-result-source">
        <span>{sourceReferenceLabel(node.primarySource)}</span>
        <span>지식 갱신 {formatDate(node.updatedAt)}</span>
        <SourceAction source={node.primarySource} />
      </aside>
    </article>
  );
}

function Explorer({
  explorer,
  selectedEdgeId,
  onNodeSelect,
  onEdgeSelect,
}: {
  explorer: OntologyExplorerResponse | null;
  selectedEdgeId: string | null;
  onNodeSelect: (node: KnowledgeNodeResource) => void;
  onEdgeSelect: (edge: ExplorerEdge) => void;
}) {
  if (!explorer) {
    return <div className="tm-k-empty">기능(FEATURE) 노드를 선택하면 결정 → 케이스 → 실행 → 이슈 관계를 펼칩니다.</div>;
  }

  const nodeMap = new Map(explorer.nodes.map((item) => [item.node.id, item.node]));
  const evidenceById = new Map(explorer.evidence.map((item) => [item.id, item]));
  const citationForNode = (node: KnowledgeNodeResource) => (
    node.evidenceIds.map((id) => evidenceById.get(id)).find(Boolean)
    ?? explorer.evidence.find((item) => item.subjectType === "NODE" && item.subjectId === node.id)
  )?.citation;
  return (
    <>
      <div className="tm-k-explorer-lanes">
        {groupExplorerNodes(explorer).map((lane) => (
          <section key={lane.type}>
            <header><span>{nodeTypeLabels[lane.type]}</span><b>{lane.nodes.length}</b></header>
            <div>
              {lane.nodes.map(({ node, depth }) => {
                const citation = citationForNode(node);
                return (
                  <article className="tm-k-explorer-node" key={node.id}>
                    <button type="button" aria-label={`${node.label} 상세 근거 보기`} onClick={() => onNodeSelect(node)}>
                      <StatusBadge status={node.status} />
                      <strong>{node.label}</strong>
                      <small>{depth}단계 · {sourceTypeLabels[node.primarySource.sourceType]}</small>
                      <span className="tm-k-detail-cta">상세 근거 보기 →</span>
                    </button>
                    <SourceAction source={node.primarySource} citation={citation} compact />
                  </article>
                );
              })}
              {lane.nodes.length === 0 && <span className="tm-k-lane-empty">연결 없음</span>}
            </div>
          </section>
        ))}
      </div>
      <div className="tm-k-edge-list" aria-label="관계와 edge evidence">
        {explorer.edges.map((edge) => (
          <button
            key={edge.id}
            type="button"
            className={selectedEdgeId === edge.id ? "is-selected" : ""}
            onClick={() => onEdgeSelect(edge)}
          >
            <span>{nodeMap.get(edge.fromNodeId)?.label ?? edge.fromNodeId}</span>
            <strong>— {edge.predicate} →</strong>
            <span>{nodeMap.get(edge.toNodeId)?.label ?? edge.toNodeId}</span>
            <small>{edge.evidenceIds.length} evidence</small>
          </button>
        ))}
      </div>
      {explorer.truncated && <p className="tm-k-info">관계가 많아 일부만 표시했습니다. 필터를 좁혀 다시 탐색하세요.</p>}
    </>
  );
}

function EditPanel({
  detail,
  draft,
  saving,
  onChange,
  onCancel,
  onSave,
}: {
  detail: KnowledgeNodeDetail;
  draft: EditDraft;
  saving: boolean;
  onChange: (draft: EditDraft) => void;
  onCancel: () => void;
  onSave: (event: FormEvent) => void;
}) {
  return (
    <form className="tm-k-edit-panel" onSubmit={onSave}>
      <label><span>상태</span><select value={draft.status} onChange={(event) => onChange({ ...draft, status: event.target.value as KnowledgeStatus })}>{statuses.map((status) => <option key={status} value={status}>{statusLabels[status]} · {status}</option>)}</select></label>
      <label className="is-wide"><span>적용 범위 node ID</span><textarea value={draft.scope} onChange={(event) => onChange({ ...draft, scope: event.target.value })} placeholder="쉼표 또는 줄바꿈으로 구분" /></label>
      <label><span>유효 시작</span><input type="datetime-local" value={draft.validFrom} onChange={(event) => onChange({ ...draft, validFrom: event.target.value })} required /></label>
      <label><span>유효 종료</span><input type="datetime-local" value={draft.validTo} onChange={(event) => onChange({ ...draft, validTo: event.target.value })} /></label>
      <label><span>변경자</span><input value={draft.changedBy} onChange={(event) => onChange({ ...draft, changedBy: event.target.value })} required /></label>
      <label className="is-wide"><span>변경 사유</span><textarea value={draft.reason} onChange={(event) => onChange({ ...draft, reason: event.target.value })} placeholder="감사이력에 남을 근거를 입력하세요" required /></label>
      <p className="tm-k-edit-help">evidence {detail.node.evidenceIds.length}건을 변경 근거로 보존하며 resource version {detail.node.resourceVersion}에 적용합니다.</p>
      <div className="tm-k-edit-actions"><button type="button" onClick={onCancel}>취소</button><button className="tm-primary-button" type="submit" disabled={saving}>{saving ? "저장 중…" : "변경 저장"}</button></div>
    </form>
  );
}

export function KnowledgeWorkspacePage({
  api = knowledgeApi,
  autoLoad = true,
  initialNodes = { items: [], total: 0, nextCursor: null },
  initialSearch = null,
  initialDetail = null,
  initialExplorer = null,
  initialAudit = null,
  initialSelectedEdgeId = null,
}: KnowledgeWorkspacePageProps = {}) {
  const [selectedStatuses, setSelectedStatuses] = useState<KnowledgeStatus[]>(["ACTIVE"]);
  const [query, setQuery] = useState(initialSearch?.query ?? "");
  const [nodes, setNodes] = useState(initialNodes);
  const [searchResult, setSearchResult] = useState(initialSearch);
  const [detail, setDetail] = useState(initialDetail);
  const [audit, setAudit] = useState(initialAudit);
  const [explorer, setExplorer] = useState(initialExplorer);
  const [selectedEdge, setSelectedEdge] = useState<ExplorerEdge | null>(
    initialExplorer?.edges.find((edge) => edge.id === initialSelectedEdgeId) ?? null,
  );
  const [loading, setLoading] = useState(autoLoad);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft | null>(null);
  const [saving, setSaving] = useState(false);

  const loadList = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const page = await listKnowledgeNodes({ statuses: selectedStatuses, limit: 100 }, signal, api);
      setNodes(page);
      setSearchResult(null);
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") setError(knowledgeErrorMessage(caught, "지식 목록을 불러오지 못했습니다."));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [api, selectedStatuses]);

  useEffect(() => {
    if (!autoLoad) return;
    const controller = new AbortController();
    void loadList(controller.signal);
    return () => controller.abort();
  }, [autoLoad, loadList]);

  const selectNode = useCallback(async (node: Pick<KnowledgeNodeResource, "id" | "nodeType">) => {
    setDetailLoading(true);
    setError(null);
    setSelectedEdge(null);
    setEditDraft(null);
    try {
      const [nextDetail, nextAudit] = await Promise.all([
        api.getKnowledgeNode(node.id),
        api.listKnowledgeNodeAudit(node.id),
      ]);
      setDetail(nextDetail);
      setAudit(nextAudit);
      if (node.nodeType === "FEATURE") {
        setExplorer(await exploreKnowledge({
          rootNodeId: node.id,
          direction: "BOTH",
          predicates: [],
          statuses: selectedStatuses,
          maxDepth: 5,
          limit: 200,
        }, undefined, api));
      }
    } catch (caught) {
      setError(knowledgeErrorMessage(caught, "지식 상세를 불러오지 못했습니다."));
    } finally {
      setDetailLoading(false);
    }
  }, [api, selectedStatuses]);

  async function submitSearch(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) {
      await loadList();
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await searchKnowledge({
        query: query.trim(),
        statuses: selectedStatuses,
        sourceTypes: [],
        nodeTypes: [],
        featureId: detail?.node.nodeType === "FEATURE" ? detail.node.id : null,
        limit: 100,
      }, undefined, api);
      setSearchResult(result);
    } catch (caught) {
      setError(knowledgeErrorMessage(caught, "지식 검색에 실패했습니다."));
    } finally {
      setLoading(false);
    }
  }

  function toggleStatus(status: KnowledgeStatus) {
    setSelectedStatuses((current) => current.includes(status)
      ? (current.length === 1 ? current : current.filter((item) => item !== status))
      : [...current, status]);
  }

  function beginEdit() {
    if (!detail) return;
    setEditDraft({
      status: detail.node.status,
      scope: detail.scopeNodeIds.join("\n"),
      validFrom: dateTimeInput(detail.node.validFrom),
      validTo: dateTimeInput(detail.node.validTo),
      reason: "",
      changedBy: "knowledge-ui",
    });
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!detail || !editDraft) return;
    setSaving(true);
    setError(null);
    try {
      await patchKnowledge({
        targetType: "NODE",
        targetId: detail.node.id,
        expectedVersion: detail.node.resourceVersion,
        status: editDraft.status,
        scopeNodeIds: editDraft.scope.split(/[\n,]/).map((value) => value.trim()).filter(Boolean),
        validFrom: new Date(editDraft.validFrom).toISOString(),
        ...(editDraft.validTo ? { validTo: new Date(editDraft.validTo).toISOString() } : {}),
        effectiveAt: new Date().toISOString(),
        reason: editDraft.reason.trim(),
        evidenceIds: detail.node.evidenceIds,
        changedBy: editDraft.changedBy.trim(),
      }, api);
      setEditDraft(null);
      await Promise.all([loadList(), selectNode(detail.node)]);
    } catch (caught) {
      setError(knowledgeErrorMessage(caught, "지식 상태를 저장하지 못했습니다."));
    } finally {
      setSaving(false);
    }
  }

  const visibleNodes = searchResult ? [] : nodes.items;
  const decisions = useMemo(() => groupDecisionNodes(searchResult
    ? searchResult.items.map((item) => ({
      id: item.nodeId,
      nodeType: item.nodeType,
      canonicalKey: item.nodeId,
      label: item.label,
      content: item.content,
      status: item.status,
      validFrom: item.recency.basisAt,
      validTo: null,
      primarySource: item.source,
      evidenceIds: [],
      resourceVersion: 0,
      createdAt: item.recency.basisAt,
      updatedAt: item.recency.basisAt,
    })) : nodes.items), [nodes.items, searchResult]);
  const selectedEvidence = selectedEdge
    ? explorer?.evidence.filter((evidence) => evidence.subjectType === "EDGE" && evidence.subjectId === selectedEdge.id) ?? []
    : detail?.evidence ?? [];
  const total = searchResult?.total ?? nodes.total;

  return (
    <section className="tm-dashboard-page tm-knowledge-workspace" aria-labelledby="knowledge-title">
      <header className="tm-page-header">
        <div><h1 id="knowledge-title">QA Knowledge</h1><p>Slack 결정, 테스트케이스, 실행 실패, 이슈를 근거와 연결해 탐색하고 관리합니다.</p></div>
      </header>

      <form className="tm-k-search" onSubmit={submitSearch}>
        <label><span className="sr-only">지식 검색</span><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="기능을 검색하면 관련 결정 · 케이스 · 실패 실행 · 이슈를 함께 찾습니다" aria-label="지식 검색" /></label>
        <button className="tm-primary-button" type="submit" disabled={loading}>검색</button>
      </form>

      <section className="tm-k-filterbar" aria-label="지식 상태 필터">
        <strong>상태</strong>
        {statuses.map((status) => <button key={status} type="button" className={selectedStatuses.includes(status) ? "is-selected" : ""} aria-pressed={selectedStatuses.includes(status)} onClick={() => toggleStatus(status)}>{statusLabels[status]} <small>{status}</small></button>)}
        <span>기본 화면에서는 현재 유효한 지식만 표시합니다.</span>
      </section>

      {searchResult?.degraded && (
        <aside className="tm-k-degraded" role="status">
          <span>검색 제한</span><div><strong>일부 검색 채널이 중단되어 사용 가능한 순위 신호로 결과를 제공합니다.</strong><p>{availableFallbackSignals(searchResult.degradationReasons)} · {searchResult.degradationReasons.map((reason) => degradationReasonLabels[reason]).join(" · ")}</p></div>
        </aside>
      )}
      {error && <aside className="tm-k-error" role="alert"><strong>실제 API 응답을 받지 못했습니다.</strong><span>{error}</span><button type="button" onClick={() => void loadList()}>다시 시도</button></aside>}

      <section className="tm-k-metrics" aria-label="현재 지식 요약">
        <article><strong>{total}</strong><span>{searchResult ? "검색 결과" : "필터된 노드"}</span></article>
        <article><strong>{decisions.current.length}</strong><span>현재 결정</span></article>
        <article><strong>{decisions.past.length}</strong><span>과거 결정</span></article>
        <article><strong>{explorer?.edges.length ?? 0}</strong><span>연결된 관계</span></article>
      </section>

      <section className="tm-k-decision-split" aria-label="현재 결정과 과거 결정 구분">
        <article>
          <header><strong>현재 결정</strong><span>지금 유효한 기준</span></header>
          <div>{decisions.current.map((node) => <button type="button" key={node.id} onClick={() => void selectNode(node)}>{node.label}</button>)}{decisions.current.length === 0 && <small>현재 필터에 ACTIVE 결정이 없습니다.</small>}</div>
        </article>
        <article className="is-past">
          <header><strong>과거 결정</strong><span>검토 필요 · 대체됨 · 철회됨</span></header>
          <div>{decisions.past.map((node) => <button type="button" key={node.id} onClick={() => void selectNode(node)}>{node.label}<StatusBadge status={node.status} /></button>)}{decisions.past.length === 0 && <small>과거 상태 필터를 켜면 변경 전 결정을 분리해 표시합니다.</small>}</div>
        </article>
      </section>

      <div className="tm-k-layout">
        <main className="tm-k-main">
          <section className="tm-k-panel tm-k-results">
            <header><div><h2>{searchResult ? `“${searchResult.query}” 검색 결과` : "지식 목록"}</h2><p>검색된 이유와 근거 출처를 함께 표시합니다.</p></div><span>{loading ? "불러오는 중…" : `${total}건`}</span></header>
            <div className="tm-k-result-list" aria-live="polite">
              {searchResult?.items.map((item) => <SearchResultRow key={item.nodeId} item={item} selected={detail?.node.id === item.nodeId} onSelect={() => void selectNode({ id: item.nodeId, nodeType: item.nodeType })} />)}
              {visibleNodes.map((node) => <NodeResultRow key={node.id} node={node} selected={detail?.node.id === node.id} onSelect={() => void selectNode(node)} />)}
              {!loading && total === 0 && <div className="tm-k-empty">조건에 맞는 지식이 없습니다. 상태 필터 또는 검색어를 조정하세요.</div>}
              {loading && <div className="tm-k-empty">PostgreSQL 지식 원장을 불러오는 중…</div>}
            </div>
          </section>

          <section className="tm-k-panel tm-k-explorer">
            <header><div><h2>기능 → 결정 → 케이스 → 실행 → 이슈</h2><p>관계를 선택하면 연결을 뒷받침하는 근거를 확인할 수 있습니다.</p></div>{explorer && <span>{explorer.nodes.length}개 지식 · {explorer.edges.length}개 관계</span>}</header>
            <Explorer explorer={explorer} selectedEdgeId={selectedEdge?.id ?? null} onNodeSelect={(node) => void selectNode(node)} onEdgeSelect={setSelectedEdge} />
          </section>
        </main>

        <aside className="tm-k-detail" aria-label="지식 상세와 감사이력">
          <header><div><h2>{detail?.node.label ?? "지식을 선택하세요"}</h2></div>{detail && <button type="button" onClick={beginEdit}>편집</button>}</header>
          {detailLoading && <div className="tm-k-empty">상세와 감사이력을 불러오는 중…</div>}
          {!detailLoading && !detail && <div className="tm-k-empty">목록의 노드를 선택하면 근거, 적용 범위, 유효 기간, 변경 이력을 표시합니다.</div>}
          {detail && !detailLoading && (
            <>
              <section className="tm-k-detail-summary" onDoubleClick={beginEdit} title="더블클릭하여 상태·범위·유효기간 편집">
                <StatusBadge status={detail.node.status} />
                <p>{detail.node.content}</p>
                <dl>
                  <div><dt>유형</dt><dd>{nodeTypeLabels[detail.node.nodeType] ?? detail.node.nodeType}</dd></div>
                  <div><dt>정책 시점</dt><dd>{formatDate(detail.node.validFrom)} → {formatDate(detail.node.validTo)}</dd></div>
                  <div><dt>적용 범위</dt><dd>{detail.scopeNodeIds.length ? detail.scopeNodeIds.join(", ") : "전체"}</dd></div>
                  <div><dt>근거 출처</dt><dd>{sourceReferenceLabel(detail.node.primarySource)}</dd></div>
                  <div><dt>지식 최신화</dt><dd>{formatDate(detail.node.updatedAt)}</dd></div>
                  <div><dt>원문 이동</dt><dd><SourceAction source={detail.node.primarySource} citation={detail.evidence[0]?.citation} /></dd></div>
                  <div><dt>변경 버전</dt><dd>v{detail.node.resourceVersion}</dd></div>
                </dl>
                <small>상태·범위·유효기간 영역을 더블클릭해도 편집할 수 있습니다.</small>
              </section>
              {editDraft && <EditPanel detail={detail} draft={editDraft} saving={saving} onChange={setEditDraft} onCancel={() => setEditDraft(null)} onSave={(event) => void saveEdit(event)} />}
            </>
          )}

          <section className="tm-k-evidence">
            <header><div><h3>{selectedEdge ? `${selectedEdge.predicate} 관계 근거` : "지식 근거"}</h3><p>{selectedEdge ? `${explorer?.nodes.find((item) => item.node.id === selectedEdge.fromNodeId)?.node.label ?? "출발 지식"} → ${explorer?.nodes.find((item) => item.node.id === selectedEdge.toNodeId)?.node.label ?? "연결 지식"}` : "원문에서 확인한 근거"}</p></div><span>{selectedEvidence.length}건</span></header>
            {selectedEdge && (
              <dl className="tm-k-edge-summary">
                <div><dt>관계 상태</dt><dd><StatusBadge status={selectedEdge.status} /></dd></div>
                <div><dt>유효 기간</dt><dd>{formatDate(selectedEdge.validFrom)} → {formatDate(selectedEdge.validTo)}</dd></div>
                <div><dt>근거 출처</dt><dd>{sourceTypeLabels[selectedEdge.primarySource.sourceType]}</dd></div>
                <div><dt>원문 이동</dt><dd><SourceAction source={selectedEdge.primarySource} citation={selectedEvidence[0]?.citation} /></dd></div>
                <div><dt>변경 버전</dt><dd>v{selectedEdge.resourceVersion}</dd></div>
              </dl>
            )}
            {selectedEvidence.map((evidence) => <EvidenceCard key={evidence.id} evidence={evidence} />)}
            {detail && selectedEvidence.length === 0 && <div className="tm-k-empty">연결된 evidence가 없습니다.</div>}
          </section>

          <section className="tm-k-audit">
            <header><div><h3>변경 이력</h3><p>상태와 사유를 시간순으로 보관합니다.</p></div><span>{audit?.total ?? 0}건</span></header>
            {audit?.items.map((item) => (
              <article key={item.id}>
                <i />
                <div><strong>{statusLabels[item.fromStatus]} → {statusLabels[item.toStatus]}</strong><p>{item.reason}</p><small>{item.changedBy} · {formatDate(item.changedAt)} · 변경 버전 {item.targetVersion}</small></div>
              </article>
            ))}
            {detail && audit?.items.length === 0 && <div className="tm-k-empty">아직 수동 변경 이력이 없습니다.</div>}
          </section>
        </aside>
      </div>
    </section>
  );
}
