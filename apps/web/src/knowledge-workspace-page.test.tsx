import type {
  HybridSearchResponse,
  KnowledgeNodeDetail,
  KnowledgeStatusAuditPage,
  OntologyExplorerResponse,
} from "@tc/api-client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { KnowledgeWorkspacePage } from "./knowledge-workspace-page";

const { readFileSync } = await vi.importActual<{ readFileSync: (path: URL, encoding: "utf8") => string }>("node:fs");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
const knowledgeWorkspaceSource = readFileSync(new URL("./knowledge-workspace-page.tsx", import.meta.url), "utf8");

const source = {
  sourceType: "SLACK_MESSAGE_REVISION" as const,
  sourceEntityId: "slack-thread-payment",
  sourceRevisionId: "rev-payment-7",
};
const occurredAt = "2026-08-21T01:00:00Z";
const feature = {
  id: "feature-payment",
  nodeType: "FEATURE" as const,
  canonicalKey: "FEATURE-PAYMENT",
  label: "카드 결제 승인",
  content: "카드 승인과 실패 복구 흐름",
  status: "ACTIVE" as const,
  validFrom: occurredAt,
  validTo: null,
  primarySource: source,
  evidenceIds: ["evidence-feature"],
  resourceVersion: 3,
  createdAt: occurredAt,
  updatedAt: occurredAt,
};
const currentDecision = { ...feature, id: "decision-current", nodeType: "DECISION" as const, canonicalKey: "DECISION-CURRENT", label: "승인 실패 시 결제대기 유지", evidenceIds: ["evidence-node"] };
const pastDecision = { ...currentDecision, id: "decision-past", canonicalKey: "DECISION-PAST", label: "과거 즉시 취소 정책", status: "SUPERSEDED" as const };
const testCase = { ...feature, id: "case-1042", nodeType: "TEST_CASE" as const, canonicalKey: "TC-1042", label: "TC-1042 카드 승인 실패" };
const execution = { ...feature, id: "execution-fail", nodeType: "EXECUTION" as const, canonicalKey: "EXEC-FAIL", label: "WEB 실행 실패" };
const issue = { ...feature, id: "issue-104", nodeType: "ISSUE" as const, canonicalKey: "ISSUE-104", label: "ISSUE-104 결제 상태 불일치" };
const nodeEvidence = {
  id: "evidence-node",
  subjectType: "NODE" as const,
  subjectId: currentDecision.id,
  source,
  revisionOrdinal: 7,
  contentHash: "node-hash",
  citation: { source, label: "#payment-dev thread", uri: "https://example.test/thread", excerpt: "실패하면 결제대기를 유지합니다.", occurredAt, deleted: false },
  capturedAt: occurredAt,
};
const edgeEvidence = {
  ...nodeEvidence,
  id: "evidence-edge",
  subjectType: "EDGE" as const,
  subjectId: "edge-case-issue",
  contentHash: "edge-hash",
  citation: { ...nodeEvidence.citation, label: "실행 실패 이슈 연결", excerpt: "동일 실행에서 ISSUE-104가 생성되었습니다." },
};

const detail: KnowledgeNodeDetail = {
  node: currentDecision,
  scopeNodeIds: [feature.id],
  evidence: [nodeEvidence],
};
const search: HybridSearchResponse = {
  query: "결제 승인 실패",
  total: 2,
  nextCursor: null,
  degraded: true,
  degradationReasons: ["LIGHTRAG_UNAVAILABLE"],
  executedAt: occurredAt,
  items: [currentDecision, pastDecision].map((node, index) => ({
    rank: index + 1,
    nodeId: node.id,
    nodeType: node.nodeType,
    label: node.label,
    content: node.content,
    score: 0.97 - index * 0.1,
    reason: { summary: "기능 관계와 의미 유사도가 높습니다.", signals: ["TYPED_GRAPH", "VECTOR", "FEATURE_PROXIMITY"] },
    status: node.status,
    source,
    featureDistance: 1,
    recency: { basisAt: occurredAt, ageSeconds: 60, score: 0.99 },
    degraded: true,
    degradationReasons: ["LIGHTRAG_UNAVAILABLE"],
    citation: nodeEvidence.citation,
  })),
};
const explorer: OntologyExplorerResponse = {
  rootNodeId: feature.id,
  nodes: [feature, currentDecision, testCase, execution, issue].map((node, depth) => ({ node, depth })),
  edges: [{
    id: "edge-case-issue",
    fromNodeId: execution.id,
    toNodeId: issue.id,
    predicate: "FOUND_BY",
    status: "ACTIVE",
    validFrom: occurredAt,
    validTo: null,
    primarySource: source,
    evidenceIds: [edgeEvidence.id],
    resourceVersion: 1,
    createdAt: occurredAt,
    updatedAt: occurredAt,
  }],
  evidence: [nodeEvidence, edgeEvidence],
  truncated: false,
  nextCursor: null,
};
const audit: KnowledgeStatusAuditPage = {
  total: 1,
  items: [{
    id: "audit-1",
    targetType: "NODE",
    targetId: currentDecision.id,
    fromStatus: "AMBIGUOUS",
    toStatus: "ACTIVE",
    reason: "Slack thread 합의 확인",
    evidenceIds: [nodeEvidence.id],
    changedBy: "qa-owner",
    changedAt: occurredAt,
    targetVersion: 3,
  }],
};

describe("Knowledge workspace", () => {
  it("renders readable rank, freshness, degradation and source navigation for search and explorer results", () => {
    const markup = renderToStaticMarkup(<KnowledgeWorkspacePage
      autoLoad={false}
      initialSearch={search}
      initialDetail={detail}
      initialExplorer={explorer}
      initialAudit={audit}
      initialSelectedEdgeId="edge-case-issue"
    />);

    expect(markup).toContain("지식 그래프 · 의미 유사도 · 정확 문구 · 최신성 사용 가능");
    expect(markup).toContain("LightRAG 보조 검색 사용 불가");
    expect(markup).not.toContain("LIGHTRAG_UNAVAILABLE");
    expect(markup).toContain("검색 이유");
    expect(markup).toContain("1위 · 관련도 97점: 지식 그래프 후보 · 의미 유사도 반영 · 선택 기능과 1단계 연결 · 최신성 99점 반영");
    expect(markup).not.toContain("기능 관계와 의미 유사도가 높습니다");
    expect(markup).toContain("1위 · 관련도 97점");
    expect(markup).toContain("최신성 기준");
    expect(markup).toContain("기준 시점부터 1분 경과 · 최신성 99점");
    expect(markup).toContain("기능과 1단계 연결");
    expect(markup).toContain("Slack 메시지 변경 이력 · 개별 revision");
    expect(markup).toContain("근거 버전 7");
    expect(markup).not.toContain("rev-payment-7");
    expect(markup).toContain('href="https://example.test/thread"');
    expect(markup).toContain('target="_blank"');
    expect(markup).toContain('rel="noreferrer"');
    expect(markup).toContain('data-source-state="available"');
    expect(markup).toContain("Slack 원문 revision 열기");
    expect(markup).toContain('aria-label="승인 실패 시 결제대기 유지 상세 근거 보기"');
    expect(markup).toContain("기능 → 결정 → 케이스 → 실행 → 이슈");
    expect(markup).toContain("WEB 실행 실패");
    expect(markup).toContain("ISSUE-104 결제 상태 불일치");
    expect(markup).toContain("FOUND_BY 관계 근거");
    expect(markup).toContain("동일 실행에서 ISSUE-104가 생성되었습니다");
    expect(markup).toContain("Slack thread 합의 확인");
    expect(markup).toContain("편집");
    expect(markup).toContain("현재 결정");
    expect(markup).toContain("과거 결정");
    expect(markup).toContain("기능");
    expect(markup).toContain("결정");
    expect(markup).toContain("테스트케이스");
    expect(markup).toContain("실행");
    expect(markup).toContain("이슈");
    expect(markup).not.toContain("KNOWLEDGE · POSTGRESQL EVIDENCE LEDGER");
    expect(markup).not.toContain("LIVE API");
    expect(markup).not.toContain("mock fallback 없음");
  });

  it("replaces a deleted source URI with an explicit tombstone notice", () => {
    const deletedUri = "https://example.test/deleted-thread";
    const deletedEvidence = {
      ...nodeEvidence,
      id: "evidence-deleted",
      citation: {
        ...nodeEvidence.citation,
        uri: deletedUri,
        excerpt: null,
        deleted: true,
      },
    };
    const deletedDetail: KnowledgeNodeDetail = {
      ...detail,
      node: { ...detail.node, evidenceIds: [deletedEvidence.id] },
      evidence: [deletedEvidence],
    };
    const deletedSearch: HybridSearchResponse = {
      ...search,
      items: [{ ...search.items[0]!, citation: deletedEvidence.citation }],
      total: 1,
    };
    const markup = renderToStaticMarkup(<KnowledgeWorkspacePage
      autoLoad={false}
      initialSearch={deletedSearch}
      initialDetail={deletedDetail}
    />);

    expect(markup).toContain('data-source-state="deleted"');
    expect(markup).toContain("삭제된 출처 · 보존된 근거만 확인 가능");
    expect(markup).toContain("원문이 삭제되어 캡처된 식별 정보와 변경 이력만 보존합니다.");
    expect(markup).toContain('class="tm-k-evidence-card is-deleted"');
    expect(markup).not.toContain(`href="${deletedUri}"`);
  });

  it("renders authoritative app links for every QA ledger source type", () => {
    const sources = {
      case: {
        sourceType: "TEST_CASE_VERSION" as const,
        sourceEntityId: "case-source-1042",
        sourceRevisionId: "case-revision-4",
      },
      execution: {
        sourceType: "EXECUTION_ATTEMPT" as const,
        sourceEntityId: "attempt-source-2",
        sourceRevisionId: null,
      },
      issue: {
        sourceType: "ISSUE_SNAPSHOT" as const,
        sourceEntityId: "issue-source-104",
        sourceRevisionId: null,
      },
    };
    const uris = [
      "/test-cases/CASE-001042",
      "/cycles/cycle_knowledge_1/execute?caseKey=CASE-001042&platform=IOS&attempt=2&run=1",
      "/issues/ISSUE-104",
    ];
    const qaSearch: HybridSearchResponse = {
      ...search,
      degraded: false,
      degradationReasons: [],
      total: 3,
      items: ([sources.case, sources.execution, sources.issue] as const).map((qaSource, index) => ({
        ...search.items[0]!,
        nodeId: `qa-result-${index}`,
        nodeType: (["TEST_CASE", "EXECUTION", "ISSUE"] as const)[index]!,
        label: (["CASE-001042 카드 승인 실패", "iOS 실행 실패", "ISSUE-104 결제 오류"] as const)[index]!,
        source: qaSource,
        recency: { basisAt: occurredAt, ageSeconds: 172_800, score: 0.72 },
        degraded: false,
        citation: {
          source: qaSource,
          label: `QA ledger source ${index + 1}`,
          uri: uris[index]!,
          excerpt: "카드 승인 실패를 검증합니다.",
          occurredAt,
          deleted: false,
        },
      })),
    };
    const markup = renderToStaticMarkup(<KnowledgeWorkspacePage autoLoad={false} initialSearch={qaSearch} />);

    for (const uri of uris) expect(markup).toContain(`href="${uri.replaceAll("&", "&amp;")}"`);
    expect(markup).toContain("테스트케이스 원장 열기");
    expect(markup).toContain("테스트 실행 원장 열기");
    expect(markup).toContain("이슈 원장 열기");
    expect(markup).toContain("기준 시점부터 2일 경과 · 최신성 72점");
    expect(markup).not.toContain('href="/test-cases/CASE-001042" target="_blank"');
    expect(markup).not.toContain("case-source-1042");
  });

  it("shows a clear unavailable state when a QA ledger citation is genuinely unresolved", () => {
    const unresolvedSource = {
      sourceType: "EXECUTION_ATTEMPT" as const,
      sourceEntityId: "attempt-source-missing",
      sourceRevisionId: null,
    };
    const unresolved: HybridSearchResponse = {
      ...search,
      degraded: false,
      degradationReasons: [],
      total: 1,
      items: [{
        ...search.items[0]!,
        source: unresolvedSource,
        degraded: false,
        citation: {
          ...search.items[0]!.citation,
          source: unresolvedSource,
          uri: null,
        },
      }],
    };
    const markup = renderToStaticMarkup(<KnowledgeWorkspacePage autoLoad={false} initialSearch={unresolved} />);

    expect(markup).toContain('data-source-state="unresolved"');
    expect(markup).toContain("테스트 실행 결과 · 원장 식별 정보 확인 불가");
    expect(markup).not.toContain("attempt-source-missing");
  });

  it("explains null feature distance, typed vector fallback, non-active status and absolute page ranks", () => {
    const paged: HybridSearchResponse = {
      ...search,
      degraded: true,
      degradationReasons: ["VECTOR_UNAVAILABLE"],
      total: 4,
      items: [
        {
          ...search.items[0]!,
          rank: 3,
          score: 0.72,
          reason: {
            summary: "API English summary must not be used as generic UI copy.",
            signals: ["TYPED_GRAPH", "EXACT_TEXT", "LIGHTRAG", "RECENCY"],
          },
          status: "SUPERSEDED",
          featureDistance: null,
          degraded: true,
          degradationReasons: ["VECTOR_UNAVAILABLE"],
        },
        {
          ...search.items[1]!,
          rank: 4,
          score: 0.7,
          reason: {
            summary: "Another API summary.",
            signals: ["TYPED_GRAPH", "RECENCY"],
          },
          status: "RETRACTED",
          featureDistance: null,
          degraded: true,
          degradationReasons: ["VECTOR_UNAVAILABLE"],
        },
      ],
    };
    const markup = renderToStaticMarkup(<KnowledgeWorkspacePage autoLoad={false} initialSearch={paged} />);

    expect(markup).toContain("3위 · 관련도 72점");
    expect(markup).toContain("4위 · 관련도 70점");
    expect(markup).toContain("기능 거리 미반영 · 기능 미선택 또는 연결 경로 없음");
    expect(markup).toContain("기능 미선택 또는 연결 경로 없음으로 기능 거리는 미반영");
    expect(markup).toContain("LightRAG는 점수 가산 없이 보조 신호로 확인");
    expect(markup).toContain("의미 유사도 검색 사용 불가");
    expect(markup).toContain("과거 · SUPERSEDED");
    expect(markup).toContain("철회 · RETRACTED");
    expect(markup).not.toContain("API English summary");
    expect(markup).not.toContain("Another API summary");
  });

  it("keeps selection and source navigation as separate click targets with mobile-safe actions", () => {
    const markup = renderToStaticMarkup(<KnowledgeWorkspacePage autoLoad={false} initialSearch={search} />);

    expect(markup).toMatch(/<article class="tm-k-result-row"><span[^>]*>결정<\/span><button type="button" class="tm-k-result-select"/);
    expect(markup).toContain('class="tm-k-source-action"');
    expect(knowledgeWorkspaceSource).toContain('onClick={onSelect}');
    expect(knowledgeWorkspaceSource).toContain('target={external ? "_blank" : undefined}');
    expect(styles).toMatch(/@media \(max-width: 760px\)[\s\S]*?\.tm-k-result-source \.tm-k-source-action\s*\{[^}]*width: 100%;[^}]*min-height: 44px;/s);
    expect(styles).toMatch(/@media \(max-width: 760px\)[\s\S]*?\.tm-k-source-action\.is-compact\s*\{[^}]*min-height: 44px;/s);
    expect(styles).toMatch(/@media \(max-width: 760px\)[\s\S]*?\.tm-k-evidence-card footer\s*\{[^}]*grid-template-columns: 1fr;/s);
  });
});
