import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import {
  GenerationApplyConfirmDialog,
  GenerationApplyReceiptPanel,
  GenerationJobDetailPanel,
  GenerationJobHistory,
  GenerationSetupRequiredNotice,
} from "./generation-job-components";
import type { GenerationApplyReceipt, GenerationJob, GenerationJobDetail } from "./generation-job-service";

function makeJob(status: GenerationJob["status"], attemptNumber = 1): GenerationJob {
  const id = `generation_job_${attemptNumber}`;
  const terminal = status === "SUCCEEDED" || status === "FAILED";
  return {
    id,
    slackWorkItemId: "slack_work_1",
    status,
    lineage: {
      rootJobId: "generation_job_1",
      retryOfJobId: attemptNumber === 1 ? null : `generation_job_${attemptNumber - 1}`,
      attemptNumber,
    },
    context: {
      inputSchemaVersion: "1.0.0",
      outputSchemaVersion: "1.0.0",
      sourceVersion: 7,
      generationVersion: attemptNumber,
      ontologySchemaId: "ontology_current",
      promptVersion: "prompt_v3",
      itemCount: 1,
      digest: String(attemptNumber).repeat(64),
      sealedAt: `2026-08-26T0${attemptNumber}:00:00Z`,
    },
    failure: status === "FAILED" ? {
      code: "GENERATION_TIMEOUT",
      message: "생성 공급자 응답 시간이 초과되었습니다.",
      retryable: true,
      attempts: 2,
      occurredAt: "2026-08-26T04:03:00Z",
    } : null,
    result: status === "SUCCEEDED" ? { outputSchemaVersion: "1.0.0", cases: [{ summary: "후보 1" }, { summary: "후보 2" }] } : null,
    createdAt: `2026-08-26T0${attemptNumber}:00:00Z`,
    startedAt: status === "QUEUED" ? null : `2026-08-26T0${attemptNumber}:01:00Z`,
    completedAt: terminal ? `2026-08-26T0${attemptNumber}:03:00Z` : null,
    updatedAt: `2026-08-26T0${attemptNumber}:03:00Z`,
  };
}

function makeDetail(job: GenerationJob): GenerationJobDetail {
  return {
    job,
    contextItems: [{
      id: "context_1",
      knowledgeEvidenceId: "evidence_1",
      subjectType: "NODE",
      subjectId: "node_1",
      source: {
        kind: "SOURCE_REVISION",
        entityType: "SLACK_MESSAGE_REVISION",
        entityId: "revision_1",
        revision: "r3",
        contentHash: "sha256:revision",
        permalink: "https://slack.example.test/archives/C1/p1",
      },
      rank: 1,
      reason: "최신 Slack 요청과 연결된 활성 근거",
      snapshot: {},
    }],
  };
}

describe("manual generation job components", () => {
  it("shows every lifecycle state as selectable append-only history", () => {
    const jobs = [makeJob("FAILED", 4), makeJob("SUCCEEDED", 3), makeJob("RUNNING", 2), makeJob("QUEUED", 1)];
    const markup = renderToStaticMarkup(<GenerationJobHistory jobs={jobs} selectedJobId="generation_job_4" onSelect={vi.fn()} />);

    expect(markup).toContain("실행 대기");
    expect(markup).toContain("실행 중");
    expect(markup).toContain("생성 완료");
    expect(markup).toContain("실행 실패");
    expect(markup).toContain("재시도는 기존 실패를 덮어쓰지 않고 새 시도로 추가");
    expect(markup).toContain('aria-label="생성 작업 목록"');
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain("시도 4");
  });

  it("shows immutable context metadata, evidence and candidate-only result without writing QA cases", () => {
    const markup = renderToStaticMarkup(
      <GenerationJobDetailPanel detail={makeDetail(makeJob("SUCCEEDED", 3))} onExecute={vi.fn()} onRetry={vi.fn()} onApply={vi.fn()} />,
    );

    expect(markup).toContain("봉인된 컨텍스트");
    expect(markup).toContain("입력 / 출력 스키마");
    expect(markup).toContain("1.0.0 / 1.0.0");
    expect(markup).toContain("온톨로지");
    expect(markup).toContain("ontology_current");
    expect(markup).toContain("프롬프트");
    expect(markup).toContain("SHA-256");
    expect(markup).toContain("SLACK_MESSAGE_REVISION");
    expect(markup).toContain("최신 Slack 요청과 연결된 활성 근거");
    expect(markup).toContain("생성 후보 2개");
    expect(markup).toContain("봉인 근거·후보 비교");
    expect(markup).toContain('href="/slack-work-items/slack_work_1/generation-jobs/generation_job_3/comparison"');
    expect(markup).toContain("후보 2개 테스트케이스로 저장");
    expect(markup).toContain("전체 후보를 하나의 트랜잭션으로 적용");
    expect(markup).toContain("자동으로 테스트케이스를 만들거나 기존 QA 원장을 변경하지 않습니다");
    expect(markup).not.toContain("대기 작업 실행");
    expect(markup).not.toContain("새 작업으로 재시도");
  });

  it("keeps the explicit save action disabled when an authoritative placement is unavailable", () => {
    const markup = renderToStaticMarkup(<GenerationJobDetailPanel
      detail={makeDetail(makeJob("SUCCEEDED", 3))}
      applyUnavailableReason="서버의 저장 배치 계획이 아직 준비되지 않았습니다."
      onExecute={vi.fn()}
      onRetry={vi.fn()}
      onApply={vi.fn()}
    />);

    expect(markup).toContain("후보 2개 테스트케이스로 저장");
    expect(markup).toContain("disabled");
    expect(markup).toContain("서버의 저장 배치 계획이 아직 준비되지 않았습니다");
  });

  it("requires an explicit all-candidate transactional confirmation before apply", () => {
    const markup = renderToStaticMarkup(<GenerationApplyConfirmDialog open candidateCount={5} onClose={vi.fn()} onConfirm={vi.fn()} />);

    expect(markup).toContain('role="alertdialog"');
    expect(markup).toContain('aria-modal="true"');
    expect(markup).toContain("생성 후보 5개를 전부 저장합니다");
    expect(markup).toContain("하나라도 실패하면 전체 작업을 롤백합니다");
    expect(markup).toContain('type="checkbox"');
    expect(markup).toContain("후보 5개 전체를 트랜잭션으로 저장하는 것을 확인했습니다");
    expect(markup).toContain("후보 5개 전부 저장");
  });

  it("surfaces created and reused receipt outcomes with links to each testcase detail", () => {
    const receipt: GenerationApplyReceipt = {
      id: "generation_apply_1",
      generationJobId: "generation_job_3",
      jobStatus: "SUCCEEDED",
      contextDigest: "a".repeat(64),
      resultDigest: "b".repeat(64),
      placementDigest: "c".repeat(64),
      transactionStatus: "COMMITTED",
      parent: { outcome: "REUSED", ledgerMutation: "NONE", workItemId: "work_item_1", key: "WORK-1", categoryId: "folder_1", resourceVersion: 3 },
      cases: [{
        generatedCaseIndex: 0,
        exactContentKey: "d".repeat(64),
        outcome: "CREATED",
        ledgerMutation: "CREATED_CASE_AND_VERSION",
        case: {
          id: "case_1",
          key: "TC-001001",
          workItemId: "work_item_1",
          categoryId: "folder_1",
          status: "ACTIVE",
          resourceVersion: 1,
          currentVersion: {
            id: "case_version_1",
            version: 1,
            summary: "새 로그인 케이스",
            precondition: "로그인 화면이 열려 있음",
            expectedResultMode: "CASE",
            caseExpectedResult: "로그인에 성공함",
            priority: "HIGH",
            labels: ["login"],
            steps: [{ sequence: 1, precondition: null, action: "로그인한다" }],
            generationReason: "봉인된 Slack 근거로 생성",
            source: {
              sourceType: "SLACK_AI",
              slackWorkItemId: "slack_work_item_1",
              generationJobId: "generation_job_3",
              contextDigest: "a".repeat(64),
              resultDigest: "b".repeat(64),
              outputSchemaVersion: "1.0.0",
              generationVersion: 3,
              evidenceNodeIds: ["knowledge_node_1"],
              sourceRevisionIds: ["slack_message_revision_1"],
              inferenceOnly: false,
              openQuestions: [],
            },
            createdAt: "2026-08-26T07:00:00Z",
          },
        },
      }, {
        generatedCaseIndex: 1,
        exactContentKey: "e".repeat(64),
        outcome: "REUSED",
        ledgerMutation: "NONE",
        preserved: true,
        matchReason: "EXACT",
        case: { id: "case_2", key: "TC-001002", workItemId: "work_item_1", categoryId: "folder_1", status: "ACTIVE", resourceVersion: 4, currentVersionId: "case_version_4", currentVersion: 4 },
      }],
      counts: { generated: 2, created: 1, reused: 1 },
      auditId: "audit_1",
      appliedAt: "2026-08-26T07:00:00Z",
    };
    const markup = renderToStaticMarkup(<MemoryRouter><GenerationApplyReceiptPanel receipt={receipt} /></MemoryRouter>);

    expect(markup).toContain("새로 생성 1개 · 기존 케이스 재사용 1개");
    expect(markup).toContain("새 테스트케이스 상세");
    expect(markup).toContain("기존 테스트케이스 상세");
    expect(markup).toContain('href="/test-cases/TC-001001"');
    expect(markup).toContain('href="/test-cases/TC-001002"');
    expect(markup).toContain("기존 버전 보존");
  });

  it("offers only legal manual actions for queued and failed jobs", () => {
    const queued = renderToStaticMarkup(<GenerationJobDetailPanel detail={makeDetail(makeJob("QUEUED"))} onExecute={vi.fn()} onRetry={vi.fn()} />);
    const failed = renderToStaticMarkup(<GenerationJobDetailPanel detail={makeDetail(makeJob("FAILED", 2))} onExecute={vi.fn()} onRetry={vi.fn()} />);

    expect(queued).toContain("대기 작업 실행");
    expect(queued).not.toContain("새 작업으로 재시도");
    expect(queued).not.toContain("테스트케이스로 저장");
    expect(failed).toContain("새 작업으로 재시도");
    expect(failed).not.toContain("대기 작업 실행");
    expect(failed).not.toContain("테스트케이스로 저장");
    expect(failed).toContain("GENERATION_TIMEOUT");
    expect(failed).toContain("실행 시도 2회");
  });

  it("disables loading and replayed save actions for a succeeded job", () => {
    const succeeded = makeDetail(makeJob("SUCCEEDED", 3));
    const loading = renderToStaticMarkup(<GenerationJobDetailPanel detail={succeeded} activeOperation={{ kind: "apply", jobId: succeeded.job.id }} onExecute={vi.fn()} onRetry={vi.fn()} onApply={vi.fn()} />);
    const replayed = renderToStaticMarkup(<GenerationJobDetailPanel detail={succeeded} applyReplayBlocked onExecute={vi.fn()} onRetry={vi.fn()} onApply={vi.fn()} />);

    expect(loading).toContain("전체 후보 저장 중…");
    expect(loading).toContain("disabled");
    expect(replayed).toContain("이미 저장된 작업");
    expect(replayed).toContain("이미 커밋된 저장 작업은 다시 실행할 수 없습니다");
    expect(replayed).toContain("disabled");
  });

  it("explains unconfigured provider setup in Korean while preserving the default manual flow", () => {
    const markup = renderToStaticMarkup(<MemoryRouter><GenerationSetupRequiredNotice details={{
      jobId: "generation_job_1",
      setupState: "SETUP_REQUIRED",
      blockingDecisions: ["PROVIDER_SELECTION", "ENDPOINT", "CREDENTIALS"],
      externalCallAttempted: false,
      manualCaseCreationAvailable: true,
    }} /></MemoryRouter>);

    expect(markup).toContain('role="alert"');
    expect(markup).toContain("AI 생성 공급자 설정이 필요합니다");
    expect(markup).toContain("외부 호출은 없었고 현재 대기 작업 상태가 유지");
    expect(markup).toContain("새 대기 작업만 추가하며 자동 실행하지 않습니다");
    expect(markup).toContain("생성 공급자 선택, API 엔드포인트, 접속 자격 증명");
    expect(markup).toContain("기존 수동 테스트케이스 만들기는 영향을 받지 않으며 계속 사용할 수 있습니다");
    expect(markup).toContain('href="/test-cases/new"');
    expect(markup).toContain("수동으로 만들기");
  });
});
