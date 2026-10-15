import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { GenerationComparisonWorkspace } from "./generation-comparison-components";
import type { GenerationComparison } from "./generation-job-service";

const present = (value: string) => ({ state: "PRESENT" as const, value, reason: null });

const sourceReference = {
  contextItemId: "generation_context_1",
  contextRank: 1,
  knowledgeEvidenceId: "knowledge_evidence_1",
  subjectType: "NODE" as const,
  subjectId: "knowledge_node_1",
  sourceKind: "SOURCE_REVISION" as const,
  sourceEntityType: "SLACK_MESSAGE_REVISION",
  sourceEntityId: "slack_revision_1",
  sourceRevision: "1710000000.000001",
  contentHash: "sealed-content-hash",
  permalink: present("https://slack.example.test/archives/C1/p1710000000000001"),
};

function createdOutcome(index: number) {
  return {
    generatedCaseIndex: index,
    exactContentKey: String(index + 1).repeat(64).slice(0, 64),
    outcome: "CREATED" as const,
    ledgerMutation: "CREATED_CASE_AND_VERSION" as const,
    case: {
      id: `case_${index + 1}`,
      key: `TC-00100${index + 1}`,
      workItemId: "work_item_1",
      categoryId: "folder_checkout",
      status: "ACTIVE" as const,
      resourceVersion: 1 as const,
      currentVersion: {
        id: `case_version_${index + 1}`,
        version: 1 as const,
        summary: `저장된 후보 ${index + 1}`,
        precondition: "결제 화면이 열려 있음",
        expectedResultMode: "CASE" as const,
        caseExpectedResult: "결제가 완료됨",
        priority: "HIGH" as const,
        labels: ["checkout"],
        steps: [{ sequence: 1, precondition: null, action: "결제를 요청한다" }],
        generationReason: "봉인 근거에 따른 생성",
        source: {
          sourceType: "SLACK_AI" as const,
          slackWorkItemId: "slack_work_1",
          generationJobId: "generation_job_1",
          contextDigest: "a".repeat(64),
          resultDigest: "b".repeat(64),
          outputSchemaVersion: "1.0.0" as const,
          generationVersion: 7,
          evidenceNodeIds: ["knowledge_node_1"],
          sourceRevisionIds: ["slack_revision_1"],
          inferenceOnly: index === 1,
          openQuestions: [],
        },
        createdAt: "2026-08-26T03:05:00Z",
      },
    },
  };
}

function reusedOutcome(index: number) {
  return {
    generatedCaseIndex: index,
    exactContentKey: String(index + 1).repeat(64).slice(0, 64),
    outcome: "REUSED" as const,
    ledgerMutation: "NONE" as const,
    matchReason: "EXACT" as const,
    preserved: true as const,
    case: {
      id: `case_${index + 1}`,
      key: `TC-00100${index + 1}`,
      workItemId: "work_item_1",
      categoryId: "folder_checkout",
      status: "ACTIVE" as const,
      resourceVersion: 3,
      currentVersionId: `case_version_${index + 1}`,
      currentVersion: 3,
    },
  };
}

export function makeGenerationComparison(): GenerationComparison {
  return {
    comparisonSchemaVersion: "1.0.0",
    job: {
      id: "generation_job_1",
      slackWorkItemId: "slack_work_1",
      status: "SUCCEEDED",
      lineage: { rootJobId: "generation_job_1", retryOfJobId: null, attemptNumber: 1 },
      context: {
        inputSchemaVersion: "1.0.0",
        outputSchemaVersion: "1.0.0",
        sourceVersion: 4,
        generationVersion: 7,
        ontologySchemaId: "ontology_1",
        promptVersion: "prompt_1",
        itemCount: 3,
        digest: "a".repeat(64),
        sealedAt: "2026-08-26T03:00:00Z",
      },
      createdAt: "2026-08-26T03:00:00Z",
      startedAt: "2026-08-26T03:01:00Z",
      completedAt: "2026-08-26T03:04:00Z",
    },
    policy: {
      sourceBasis: "SEALED_GENERATION_JOB",
      readOnly: true,
      liveSourceSubstitution: false,
      generationInputReconstructed: false,
      providerCallAttempted: false,
      mutationPerformed: false,
    },
    slackSource: {
      slackWorkItemId: "slack_work_1",
      sourceVersion: 4,
      workspaceId: present("workspace_1"),
      channelId: present("channel_1"),
      messageTs: present("1710000000.000001"),
      threadTs: present("1710000000.000001"),
      permalink: sourceReference.permalink,
      fields: [
        { field: "DEVELOPMENT_ITEM", value: present("체크아웃 결제"), sourceReferences: [sourceReference] },
        { field: "REQUESTED_MENU", value: present("주문 > 결제"), sourceReferences: [sourceReference] },
        { field: "FEATURE_DESCRIPTION", value: present("카드 결제 실패 복구"), sourceReferences: [sourceReference] },
        { field: "REQUEST_DETAILS", value: present("재시도 시 중복 승인 방지"), sourceReferences: [sourceReference] },
      ],
      threadEvidence: {
        availability: "AVAILABLE",
        reason: null,
        items: [{
          index: 0,
          sourceRevisionId: "slack_revision_1",
          messageId: "slack_message_1",
          messageTs: "1710000000.000001",
          revisionTs: "1710000000.000001",
          role: "ROOT",
          operation: "CREATE",
          authorId: present("U-QA"),
          body: present("결제 재시도 시 중복 승인이 없어야 합니다."),
          permalink: sourceReference.permalink,
          recordedAt: "2026-08-26T02:50:00Z",
          sourceReferences: [sourceReference],
        }],
      },
    },
    activeKnowledge: {
      availability: "AVAILABLE",
      reason: null,
      items: [{
        evidenceNodeId: "knowledge_node_1",
        nodeType: "CONSTRAINT",
        status: "ACTIVE",
        activeAtSeal: true,
        content: present("동일 결제 키는 한 번만 승인되어야 한다."),
        validFrom: "2026-08-20T00:00:00Z",
        validTo: null,
        selectionReason: "결제 중복 방지 제약",
        sourceReferences: [sourceReference],
      }],
    },
    existingCases: {
      availability: "AVAILABLE",
      reason: null,
      items: [{
        evidenceNodeId: "case_node_1",
        testCaseId: "case_existing_1",
        testCaseVersionId: "case_version_existing_3",
        caseKey: present("TC-000321"),
        status: "ACTIVE",
        content: {
          state: "PRESENT",
          reason: null,
          value: {
            summary: "카드 결제 승인 성공",
            precondition: "유효한 카드가 등록됨",
            steps: [{ sequence: 1, precondition: null, action: "결제를 승인한다", expectedResult: "승인 번호가 저장된다" }],
            expectedResultMode: "CASE",
            caseExpectedResult: "결제가 완료된다",
            priority: "HIGH",
            labels: ["payments"],
          },
        },
        selectionReason: "동일 메뉴의 활성 테스트케이스",
        sourceReferences: [sourceReference],
      }],
    },
    supplementalEvidence: {
      availability: "AVAILABLE",
      reason: null,
      items: [{
        evidenceNodeId: "issue_node_1",
        kind: "OPEN_ISSUE",
        title: present("간헐적 결제 타임아웃"),
        snapshot: { state: "PRESENT", value: { severity: "HIGH", status: "OPEN" }, reason: null },
        selectionReason: "결제 위험 보강",
        sourceReferences: [sourceReference],
      }],
    },
    generation: {
      availability: "AVAILABLE",
      outputSchemaVersion: "1.0.0",
      resultDigest: "b".repeat(64),
      categoryCandidate: { categoryId: "folder_checkout", score: 0.94, alternatives: [] },
      reason: null,
      candidates: Array.from({ length: 5 }, (_, index) => ({
        index,
        candidate: {
          summary: `결제 재시도 후보 ${index + 1}`,
          steps: [{ sequence: 1, precondition: "첫 승인 요청이 타임아웃됨", action: "동일 결제 키로 다시 요청한다" }],
          caseExpectedResult: "중복 승인 없이 기존 결과를 반환한다",
          priority: "HIGH",
          labels: ["checkout", "idempotency"],
          evidenceNodeIds: [index === 4 ? "missing_node_1" : "knowledge_node_1"],
          generationReason: "봉인된 중복 승인 제약과 Slack 요청을 검증합니다.",
          inferenceOnly: index === 1,
          openQuestions: index === 2 ? ["타임아웃 기준은 몇 초인가요?"] : [],
        },
        apply: { state: "APPLIED", applyId: "generation_apply_1", outcome: createdOutcome(index) },
      })),
    },
    referenceEdges: {
      availability: "AVAILABLE",
      reason: null,
      items: Array.from({ length: 5 }, (_, index) => index === 4 ? {
        candidateIndex: index,
        evidenceIndex: 0,
        evidenceNodeId: "missing_node_1",
        relation: "SUPPORTED_BY" as const,
        target: {
          state: "MISSING_SOURCE" as const,
          sourceKind: "UNKNOWN" as const,
          sourceId: "missing_node_1",
          contextReferences: [] as [],
          reason: "UNRESOLVED_REFERENCE" as const,
        },
      } : {
        candidateIndex: index,
        evidenceIndex: 0,
        evidenceNodeId: "knowledge_node_1",
        relation: "SUPPORTED_BY" as const,
        target: {
          state: "RESOLVED" as const,
          sourceKind: "ACTIVE_KNOWLEDGE" as const,
          sourceId: "knowledge_node_1",
          contextReferences: [sourceReference],
          reason: null,
        },
      }),
    },
    apply: {
      state: "APPLIED",
      receipt: {
        id: "generation_apply_1",
        generationJobId: "generation_job_1",
        contextDigest: "a".repeat(64),
        resultDigest: "b".repeat(64),
        placementDigest: "c".repeat(64),
        transactionStatus: "COMMITTED",
        parent: { outcome: "REUSED", ledgerMutation: "NONE", workItemId: "work_item_1", key: "WORK-1", categoryId: "folder_checkout", resourceVersion: 2 },
        counts: { generated: 5, created: 5, reused: 0 },
        auditId: "audit_1",
        appliedAt: "2026-08-26T03:05:00Z",
      },
    },
  };
}

function renderComparison(comparison = makeGenerationComparison()) {
  return renderToStaticMarkup(<MemoryRouter><GenerationComparisonWorkspace comparison={comparison} /></MemoryRouter>);
}

describe("GenerationComparisonWorkspace", () => {
  it("renders the four ordered review sections and no editing controls", () => {
    const markup = renderComparison();

    expect(markup).toContain("1</span>Slack 원본·스레드");
    expect(markup).toContain("2</span>결정·제약");
    expect(markup).toContain("3</span>기존 케이스 스냅샷");
    expect(markup).toContain("4</span>생성 후보");
    expect(markup).toContain("읽기 전용 · 봉인된 생성 작업 기준");
    expect(markup).toContain("현재 Slack, 지식 또는 테스트케이스를 다시 조회하지 않습니다");
    expect(markup).not.toContain("<input");
    expect(markup).not.toContain("<textarea");
    expect(markup).not.toContain("<form");
    expect(markup).not.toContain("contenteditable");
  });

  it("keeps candidate evidence keyboard-operable and reveals source in an in-card controlled region", () => {
    const markup = renderComparison();

    expect(markup).toContain('class="tm-comparison-evidence-chip is-resolved" type="button" aria-expanded="false" aria-controls="comparison-evidence-panel-0-0"');
    expect(markup).toContain("결정·제약");
    expect(markup).toContain("원본 누락");
    expect(markup).toContain('id="comparison-source-knowledge_node_1"');
    expect(markup).not.toContain('target="_blank">해당 봉인 근거로 이동');
  });

  it("shows immutable existing-case versions and applied candidate links", () => {
    const comparison = makeGenerationComparison();
    const first = comparison.generation.availability === "AVAILABLE"
      ? comparison.generation.candidates[0]
      : null;
    if (!first) throw new Error("fixture must contain generated candidates");
    first.apply = {
      state: "APPLIED",
      applyId: "generation_apply_1",
      outcome: reusedOutcome(0),
    };
    const markup = renderComparison(comparison);

    expect(markup).toContain("case_existing_1");
    expect(markup).toContain("case_version_existing_3");
    expect(markup).toContain("버전 스냅샷");
    expect(markup).toContain("기존 케이스 재사용됨");
    expect(markup).toContain("새 케이스 생성됨");
    expect(markup).toContain('href="/test-cases/TC-001001"');
    expect(markup).toContain("TC-001001 상세 보기");
  });

  it.each([
    ["QUEUED", "실행 대기 중이라 생성 결과가 아직 없습니다"],
    ["RUNNING", "생성 작업이 실행 중이라 결과가 아직 없습니다"],
    ["FAILED", "생성에 실패하여 비교할 후보가 없습니다"],
  ] as const)("explains the %s no-result state in Korean", (status, message) => {
    const comparison = makeGenerationComparison();
    const markup = renderComparison({
      ...comparison,
      job: { ...comparison.job, status },
      generation: { availability: "EMPTY", outputSchemaVersion: "1.0.0", resultDigest: null, categoryCandidate: null, candidates: [], reason: "JOB_NOT_SUCCEEDED" },
      referenceEdges: { availability: "EMPTY", items: [], reason: "NO_GENERATED_CANDIDATES" },
      apply: { state: "NOT_APPLIED", receipt: null },
    });

    expect(markup).toContain(message);
    expect(markup).not.toContain("현재 원본으로 후보를 복원");
  });

  it("distinguishes redacted and missing historical sources in Korean", () => {
    const comparison = makeGenerationComparison();
    const markup = renderComparison({
      ...comparison,
      activeKnowledge: { availability: "REDACTED", items: [], reason: "AUTHORIZATION_POLICY" },
      existingCases: { availability: "MISSING_SOURCE", items: [], reason: "LEGACY_CONTEXT" },
      generation: { availability: "MISSING_SOURCE", outputSchemaVersion: "1.0.0", resultDigest: null, categoryCandidate: null, candidates: [], reason: "RESULT_MISSING" },
      referenceEdges: { availability: "MISSING_SOURCE", items: [], reason: "RESULT_MISSING" },
      apply: { state: "NOT_APPLIED", receipt: null },
    });

    expect(markup).toContain("결정·제약이(가) 가려져 있습니다");
    expect(markup).toContain("접근 권한 정책");
    expect(markup).toContain("기존 테스트케이스 스냅샷 원본을 확인할 수 없습니다");
    expect(markup).toContain("이전 형식의 컨텍스트");
    expect(markup).toContain("봉인된 생성 결과를 찾을 수 없습니다");
    expect(markup).toContain("현재 데이터로 대체하지 않습니다");
  });

  it("labels deleted and redacted sealed evidence without substituting a live value", () => {
    const comparison = makeGenerationComparison();
    const threadEvidence = comparison.slackSource.threadEvidence;
    if (threadEvidence.availability !== "AVAILABLE") throw new Error("fixture must contain thread evidence");
    const revision = threadEvidence.items[0];
    if (!revision) throw new Error("fixture must contain a thread revision");
    threadEvidence.items[0] = {
      ...revision,
      operation: "DELETE",
      authorId: { state: "REDACTED", value: null, reason: "SENSITIVE_DATA" },
      body: { state: "EMPTY", value: null, reason: "SEALED_EMPTY" },
    };
    if (comparison.referenceEdges.availability !== "AVAILABLE") throw new Error("fixture must contain evidence edges");
    const edge = comparison.referenceEdges.items[0];
    if (!edge) throw new Error("fixture must contain an evidence edge");
    comparison.referenceEdges.items[0] = {
      ...edge,
      target: {
        state: "REDACTED",
        sourceKind: "ACTIVE_KNOWLEDGE",
        sourceId: "knowledge_node_1",
        contextReferences: [],
        reason: "AUTHORIZATION_POLICY",
      },
    };
    const markup = renderComparison(comparison);

    expect(markup).toContain("삭제");
    expect(markup).toContain("봉인 시점에 비어 있음");
    expect(markup).toContain("민감 정보 보호");
    expect(markup).toContain("결정·제약 · 가림");
    expect(markup).not.toContain("현재 Slack에서 복원");
  });

  it("renders stable empty labels for every optional historical section", () => {
    const comparison = makeGenerationComparison();
    const markup = renderComparison({
      ...comparison,
      slackSource: {
        ...comparison.slackSource,
        threadEvidence: { availability: "EMPTY", items: [], reason: "SEALED_EMPTY" },
      },
      activeKnowledge: { availability: "EMPTY", items: [], reason: "SEALED_EMPTY" },
      existingCases: { availability: "EMPTY", items: [], reason: "SEALED_EMPTY" },
      supplementalEvidence: { availability: "EMPTY", items: [], reason: "SEALED_EMPTY" },
    });

    expect(markup).toContain("봉인 시점에 스레드 답글이나 수정 이력이 없습니다");
    expect(markup).toContain("결정·제약 없음");
    expect(markup).toContain("기존 테스트케이스 스냅샷 없음");
    expect(markup).not.toContain("후보가 참조한 추가 봉인 근거");
  });
});
