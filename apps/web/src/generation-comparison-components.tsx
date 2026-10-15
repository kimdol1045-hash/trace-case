import { useState } from "react";
import { Link } from "react-router-dom";

import type {
  CandidateReferenceEdgeResource,
  ComparisonSourceReferenceResource,
  GeneratedCandidateComparisonResource,
  GenerationComparison,
  HistoricalExistingCaseContentResource,
  HistoricalSnapshotResource,
  HistoricalTextResource,
} from "./generation-job-service";

const slackFieldLabels = {
  DEVELOPMENT_ITEM: "개발 항목",
  REQUESTED_MENU: "요청 메뉴",
  FEATURE_DESCRIPTION: "기능 설명",
  REQUEST_DETAILS: "요청 상세",
} as const;

const knowledgeTypeLabels = {
  REQUIREMENT: "요구사항",
  DECISION: "결정",
  CONSTRAINT: "제약",
} as const;

const sourceKindLabels = {
  SLACK_WORK_ITEM: "Slack 원본",
  ACTIVE_KNOWLEDGE: "결정·제약",
  EXISTING_CASE: "기존 테스트케이스",
  RISK_EXECUTION: "위험 실행",
  OPEN_ISSUE: "열린 이슈",
  EXPLICIT_SOURCE: "명시적 출처",
  OTHER_SEALED: "추가 봉인 근거",
  UNKNOWN: "확인할 수 없는 근거",
} as const;

const reasonLabels: Record<string, string> = {
  SEALED_EMPTY: "봉인 시점에 값이 비어 있음",
  SENSITIVE_DATA: "민감 정보 보호",
  AUTHORIZATION_POLICY: "접근 권한 정책",
  NOT_SEALED: "작업에 봉인되지 않음",
  SOURCE_INCOMPLETE: "봉인된 원본이 불완전함",
  LEGACY_CONTEXT: "이전 형식의 컨텍스트",
  UNRESOLVED_REFERENCE: "봉인 근거를 연결할 수 없음",
  RESULT_MISSING: "봉인된 생성 결과를 찾을 수 없음",
  JOB_NOT_SUCCEEDED: "생성 작업이 완료되지 않음",
  NO_GENERATED_CANDIDATES: "생성 후보가 없음",
};

const jobStatusLabels = {
  QUEUED: "실행 대기",
  RUNNING: "실행 중",
  SUCCEEDED: "생성 완료",
  FAILED: "실행 실패",
} as const;

export function GenerationComparisonWorkspace({ comparison }: { comparison: GenerationComparison }) {
  const [openEvidenceKey, setOpenEvidenceKey] = useState<string | null>(null);
  const availableEdges = comparison.referenceEdges.availability === "AVAILABLE"
    ? comparison.referenceEdges.items
    : [];

  return <div className="tm-comparison-workspace" data-read-only="true">
    <aside className="tm-comparison-policy" aria-label="비교 정책">
      <span aria-hidden="true">🔒</span>
      <div><strong>읽기 전용 · 봉인된 생성 작업 기준</strong><p>현재 Slack, 지식 또는 테스트케이스를 다시 조회하지 않습니다. 이 화면에서는 원본과 결과를 수정하거나 다시 생성하지 않습니다.</p></div>
      <dl><div><dt>봉인 시각</dt><dd>{formatComparisonDate(comparison.job.context.sealedAt)}</dd></div><div><dt>컨텍스트</dt><dd><code>{comparison.job.context.digest}</code></dd></div></dl>
    </aside>

    <nav className="tm-comparison-section-nav" aria-label="비교 섹션 바로가기">
      <a href="#comparison-slack"><span>1</span>Slack 원본·스레드</a>
      <a href="#comparison-knowledge"><span>2</span>결정·제약</a>
      <a href="#comparison-existing-cases"><span>3</span>기존 케이스 스냅샷</a>
      <a href="#comparison-candidates"><span>4</span>생성 후보</a>
    </nav>

    <div className="tm-comparison-grid">
      <SlackComparisonSection comparison={comparison} />
      <KnowledgeComparisonSection comparison={comparison} />
      <ExistingCasesComparisonSection comparison={comparison} />
      <section className="tm-comparison-section tm-comparison-section--candidates" id="comparison-candidates" aria-labelledby="comparison-candidates-title">
        <ComparisonSectionHeader number="4" title="생성 후보" description="생성 당시 후보 순서와 이유, 질문, 추론 여부, 근거 연결을 그대로 비교합니다." />
        <GenerationState comparison={comparison} />
        {comparison.generation.availability === "AVAILABLE" ? <>
          {comparison.generation.categoryCandidate ? <div className="tm-comparison-category"><span>추천 카테고리</span><strong>{comparison.generation.categoryCandidate.categoryId}</strong><small>신뢰도 {formatPercent(comparison.generation.categoryCandidate.score)}</small></div> : null}
          <ol className="tm-comparison-candidates" aria-label="생성 후보 목록">
            {comparison.generation.candidates.map((item) => {
              const candidateEdges = availableEdges.filter((edge) => edge.candidateIndex === item.index);
              return <CandidateCard
                key={item.index}
                item={item}
                edges={candidateEdges}
                comparison={comparison}
                openEvidenceKey={openEvidenceKey}
                onToggleEvidence={(key) => setOpenEvidenceKey((current) => current === key ? null : key)}
              />;
            })}
          </ol>
        </> : null}
        <SupplementalEvidence comparison={comparison} />
      </section>
    </div>
  </div>;
}

function SlackComparisonSection({ comparison }: { comparison: GenerationComparison }) {
  const { slackSource } = comparison;
  return <section className="tm-comparison-section" id="comparison-slack" aria-labelledby="comparison-slack-title">
    <span className="tm-comparison-source-anchor" id="comparison-source-slack" aria-hidden="true" />
    <ComparisonSectionHeader number="1" title="Slack 원본·스레드" description={`요청 v${slackSource.sourceVersion}에 봉인된 구조화 필드와 메시지 수정 이력입니다.`} />
    <dl className="tm-comparison-identity-list">
      <HistoricalTextRow label="워크스페이스" text={slackSource.workspaceId} />
      <HistoricalTextRow label="채널" text={slackSource.channelId} />
      <HistoricalTextRow label="메시지 시각" text={slackSource.messageTs} />
      <HistoricalTextRow label="스레드 시각" text={slackSource.threadTs} />
    </dl>
    <div className="tm-comparison-field-list" aria-label="봉인된 Slack 요청 필드">
      {slackSource.fields.map((field) => <article key={field.field}>
        <h3>{slackFieldLabels[field.field]}</h3>
        <HistoricalText text={field.value} />
        <SourceReferenceSummary references={field.sourceReferences} />
      </article>)}
    </div>
    <div className="tm-comparison-subsection">
      <h3>봉인된 스레드 증거</h3>
      {slackSource.threadEvidence.availability === "AVAILABLE" ? <ol className="tm-comparison-thread" aria-label="Slack 메시지 수정 이력">
        {slackSource.threadEvidence.items.map((revision) => <li key={revision.sourceRevisionId}>
          <header><span>{revision.role === "ROOT" ? "원문" : "답글"}</span><strong>{revision.operation === "CREATE" ? "작성" : revision.operation === "EDIT" ? "수정" : "삭제"}</strong><time dateTime={revision.recordedAt ?? undefined}>{formatComparisonDate(revision.recordedAt)}</time></header>
          <HistoricalText text={revision.body} />
          <dl><HistoricalTextRow label="작성자" text={revision.authorId} /><div><dt>리비전</dt><dd>{revision.sourceRevisionId}</dd></div></dl>
          <SourceReferenceSummary references={revision.sourceReferences} />
        </li>)}
      </ol> : <CollectionUnavailable availability={slackSource.threadEvidence.availability} reason={slackSource.threadEvidence.reason} emptyText="봉인 시점에 스레드 답글이나 수정 이력이 없습니다." noun="Slack 스레드 근거" />}
    </div>
  </section>;
}

function KnowledgeComparisonSection({ comparison }: { comparison: GenerationComparison }) {
  const { activeKnowledge } = comparison;
  return <section className="tm-comparison-section" id="comparison-knowledge" aria-labelledby="comparison-knowledge-title">
    <ComparisonSectionHeader number="2" title="스냅샷 시점 결정·제약" description="작업 봉인 시점에 ACTIVE였던 요구사항, 결정, 제약만 표시합니다." />
    {activeKnowledge.availability === "AVAILABLE" ? <ol className="tm-comparison-knowledge-list" aria-label="봉인 시점 활성 지식">
      {activeKnowledge.items.map((item) => <li key={item.evidenceNodeId} id={`comparison-source-${item.evidenceNodeId}`}>
        <header><span>{knowledgeTypeLabels[item.nodeType]}</span><small>ACTIVE · {formatComparisonDate(item.validFrom)}부터</small></header>
        <HistoricalText text={item.content} />
        <p className="tm-comparison-selection-reason"><strong>선정 이유</strong>{item.selectionReason}</p>
        <SourceReferenceSummary references={item.sourceReferences} />
      </li>)}
    </ol> : <CollectionUnavailable availability={activeKnowledge.availability} reason={activeKnowledge.reason} emptyText="봉인 시점에 활성 요구사항·결정·제약이 없습니다." noun="결정·제약" />}
  </section>;
}

function ExistingCasesComparisonSection({ comparison }: { comparison: GenerationComparison }) {
  const { existingCases } = comparison;
  return <section className="tm-comparison-section" id="comparison-existing-cases" aria-labelledby="comparison-existing-cases-title">
    <ComparisonSectionHeader number="3" title="기존 테스트케이스·버전 스냅샷" description="현재 버전으로 바꾸지 않고 생성 작업이 참조한 케이스·버전 식별자를 고정해 표시합니다." />
    {existingCases.availability === "AVAILABLE" ? <ol className="tm-comparison-existing-list" aria-label="참조된 기존 테스트케이스">
      {existingCases.items.map((item) => <li key={item.evidenceNodeId} id={`comparison-source-${item.evidenceNodeId}`}>
        <header><div><HistoricalText text={item.caseKey} fallback="케이스 키 확인 불가" /></div><span>버전 스냅샷</span></header>
        <dl className="tm-comparison-case-identity"><div><dt>테스트케이스 ID</dt><dd>{item.testCaseId}</dd></div><div><dt>버전 ID</dt><dd>{item.testCaseVersionId}</dd></div></dl>
        <ExistingCaseContent content={item.content} />
        <p className="tm-comparison-selection-reason"><strong>선정 이유</strong>{item.selectionReason}</p>
        <SourceReferenceSummary references={item.sourceReferences} />
      </li>)}
    </ol> : <CollectionUnavailable availability={existingCases.availability} reason={existingCases.reason} emptyText="봉인 시점에 참조된 기존 테스트케이스가 없습니다." noun="기존 테스트케이스 스냅샷" />}
  </section>;
}

function ExistingCaseContent({ content }: { content: HistoricalExistingCaseContentResource }) {
  const value = content.state === "PRESENT" ? content.value : null;
  if (!value) {
    return <UnavailableValue state={content.state === "REDACTED" ? "REDACTED" : "MISSING_SOURCE"} reason={content.reason} noun="기존 케이스 본문" />;
  }
  return <div className="tm-comparison-existing-content">
    <h3>{value.summary}</h3>
    {value.precondition ? <p><strong>사전조건</strong>{value.precondition}</p> : null}
    <ol>{value.steps.map((step) => <li key={step.sequence}><strong>{step.sequence}</strong><div>{step.precondition ? <small>조건 · {step.precondition}</small> : null}<span>{step.action}</span>{step.expectedResult ? <small>기대 · {step.expectedResult}</small> : null}</div></li>)}</ol>
    {value.caseExpectedResult ? <p><strong>케이스 기대 결과</strong>{value.caseExpectedResult}</p> : null}
    <div className="tm-comparison-tags"><span>{value.priority}</span>{value.labels.map((label) => <span key={label}>{label}</span>)}</div>
  </div>;
}

function CandidateCard({
  item,
  edges,
  comparison,
  openEvidenceKey,
  onToggleEvidence,
}: {
  item: GeneratedCandidateComparisonResource;
  edges: CandidateReferenceEdgeResource[];
  comparison: GenerationComparison;
  openEvidenceKey: string | null;
  onToggleEvidence: (key: string) => void;
}) {
  const { candidate } = item;
  return <li className="tm-comparison-candidate">
    <article aria-labelledby={`comparison-candidate-${item.index}`}>
      <header><span className="tm-comparison-candidate__index">후보 {item.index + 1}</span><div className="tm-comparison-tags"><span>{candidate.priority}</span><span className={candidate.inferenceOnly ? "is-inference" : "is-grounded"}>{candidate.inferenceOnly ? "추론 포함" : "직접 근거 기반"}</span></div></header>
      <h3 id={`comparison-candidate-${item.index}`}>{candidate.summary}</h3>
      <p className="tm-comparison-generation-reason"><strong>생성 이유</strong>{candidate.generationReason}</p>
      <ol className="tm-comparison-candidate-steps" aria-label={`${candidate.summary} 테스트 단계`}>{candidate.steps.map((step) => <li key={step.sequence}><strong>{step.sequence}</strong><div>{step.precondition ? <small>조건 · {step.precondition}</small> : null}<span>{step.action}</span></div></li>)}</ol>
      <p className="tm-comparison-expected"><strong>기대 결과</strong>{candidate.caseExpectedResult}</p>
      <div className="tm-comparison-tags" aria-label="후보 라벨">{candidate.labels.length ? candidate.labels.map((label) => <span key={label}>{label}</span>) : <span>라벨 없음</span>}</div>
      <div className="tm-comparison-open-questions"><h4>열린 질문</h4>{candidate.openQuestions.length ? <ul>{candidate.openQuestions.map((question) => <li key={question}>{question}</li>)}</ul> : <p>열린 질문이 없습니다.</p>}</div>
      <div className="tm-comparison-evidence"><h4>후보 근거</h4>{edges.length ? <div className="tm-comparison-evidence__chips">{edges.map((edge) => {
        const key = `${edge.candidateIndex}:${edge.evidenceIndex}`;
        const panelId = `comparison-evidence-panel-${edge.candidateIndex}-${edge.evidenceIndex}`;
        const open = openEvidenceKey === key;
        return <button key={key} className={`tm-comparison-evidence-chip is-${edge.target.state.toLowerCase()}`} type="button" aria-expanded={open} aria-controls={panelId} onClick={() => onToggleEvidence(key)}>
          <span>{sourceKindLabels[edge.target.sourceKind]}{edge.target.state === "REDACTED" ? " · 가림" : edge.target.state === "MISSING_SOURCE" ? " · 원본 누락" : ""}</span><strong>{edge.evidenceNodeId}</strong><span aria-hidden="true">{open ? "−" : "+"}</span>
        </button>;
      })}</div> : <p className="tm-comparison-empty-inline">연결된 후보 근거를 확인할 수 없습니다.</p>}
        {edges.map((edge) => {
          const key = `${edge.candidateIndex}:${edge.evidenceIndex}`;
          if (openEvidenceKey !== key) return null;
          return <EvidenceDisclosure key={key} edge={edge} comparison={comparison} />;
        })}
      </div>
      <CandidateApplyOutcome item={item} />
    </article>
  </li>;
}

function EvidenceDisclosure({ edge, comparison }: { edge: CandidateReferenceEdgeResource; comparison: GenerationComparison }) {
  const panelId = `comparison-evidence-panel-${edge.candidateIndex}-${edge.evidenceIndex}`;
  if (edge.target.state !== "RESOLVED") {
    return <div className="tm-comparison-evidence-panel is-unavailable" id={panelId} role="region" aria-live="polite" aria-label={`${edge.evidenceNodeId} 근거 상세`}>
      <strong>{sourceKindLabels[edge.target.sourceKind]}</strong>
      <UnavailableValue state={edge.target.state} reason={edge.target.reason} noun="후보 근거 원본" />
    </div>;
  }

  const sourceSummary = resolvedSourceSummary(comparison, edge);
  const anchor = resolvedSourceAnchor(edge);
  return <div className="tm-comparison-evidence-panel" id={panelId} role="region" aria-live="polite" aria-label={`${edge.evidenceNodeId} 근거 상세`}>
    <header><div><span>{sourceKindLabels[edge.target.sourceKind]}</span><strong>{edge.target.sourceId}</strong></div>{anchor ? <a href={`#${anchor}`}>해당 봉인 근거로 이동</a> : null}</header>
    {sourceSummary ? <p>{sourceSummary}</p> : <p>봉인된 식별자와 출처 메타데이터로 연결된 근거입니다.</p>}
    <SourceReferenceSummary references={edge.target.contextReferences} expanded />
  </div>;
}

function CandidateApplyOutcome({ item }: { item: GeneratedCandidateComparisonResource }) {
  if (item.apply.state === "NOT_APPLIED") {
    return <footer className="tm-comparison-candidate__apply"><span>미적용</span><p>이 비교 화면에서는 후보를 저장하거나 원장을 변경하지 않습니다.</p></footer>;
  }
  const { applyId, outcome } = item.apply;
  if (!applyId || !outcome) {
    return <footer className="tm-comparison-candidate__apply"><span>적용 연결 확인 불가</span><p>봉인된 적용 영수증의 후보 연결이 불완전합니다.</p></footer>;
  }
  return <footer className="tm-comparison-candidate__apply is-applied"><span>{outcome.outcome === "CREATED" ? "새 케이스 생성됨" : "기존 케이스 재사용됨"}</span><p>적용 영수증 {applyId}</p><Link to={`/test-cases/${outcome.case.key}`}>{outcome.case.key} 상세 보기 →</Link></footer>;
}

function SupplementalEvidence({ comparison }: { comparison: GenerationComparison }) {
  const { supplementalEvidence } = comparison;
  if (supplementalEvidence.availability !== "AVAILABLE") return null;
  return <aside className="tm-comparison-supplemental" aria-labelledby="comparison-supplemental-title">
    <header><h3 id="comparison-supplemental-title">후보가 참조한 추가 봉인 근거</h3><span>{supplementalEvidence.items.length}개</span></header>
    <ol>{supplementalEvidence.items.map((item) => <li key={item.evidenceNodeId} id={`comparison-source-${item.evidenceNodeId}`}>
      <div><span>{sourceKindLabels[item.kind]}</span><HistoricalText text={item.title} fallback={item.evidenceNodeId} /></div>
      <SupplementalSnapshot snapshot={item.snapshot} />
      <p className="tm-comparison-selection-reason"><strong>선정 이유</strong>{item.selectionReason}</p>
      <SourceReferenceSummary references={item.sourceReferences} />
    </li>)}</ol>
  </aside>;
}

function SupplementalSnapshot({ snapshot }: { snapshot: HistoricalSnapshotResource }) {
  if (snapshot.state === "PRESENT" && snapshot.value) {
    return <pre>{JSON.stringify(snapshot.value, null, 2)}</pre>;
  }
  return <UnavailableValue state={snapshot.state === "REDACTED" ? "REDACTED" : "MISSING_SOURCE"} reason={snapshot.reason} noun="추가 근거 스냅샷" />;
}

function GenerationState({ comparison }: { comparison: GenerationComparison }) {
  if (comparison.generation.availability === "AVAILABLE") return null;
  if (comparison.generation.availability === "EMPTY") {
    const statusMessage = {
      QUEUED: "실행 대기 중이라 생성 결과가 아직 없습니다. 작업 상세에서 실행할 수 있습니다.",
      RUNNING: "생성 작업이 실행 중이라 결과가 아직 없습니다. 작업 상세에서 상태를 새로고침하세요.",
      FAILED: "생성에 실패하여 비교할 후보가 없습니다. 실패 기록과 재시도는 작업 상세에서 확인하세요.",
      SUCCEEDED: "완료된 작업에 생성 후보가 없습니다.",
    }[comparison.job.status];
    return <div className="tm-comparison-state is-empty" role="status"><strong>{jobStatusLabels[comparison.job.status]}</strong><p>{statusMessage}</p></div>;
  }
  return <div className={`tm-comparison-state is-${comparison.generation.availability.toLowerCase()}`} role="status"><strong>{comparison.generation.availability === "REDACTED" ? "생성 결과가 가려져 있습니다." : "봉인된 생성 결과를 찾을 수 없습니다."}</strong><p>{comparison.generation.availability === "REDACTED" ? "보호 정책에 따라 후보 내용을 표시하지 않습니다." : "현재 데이터로 대체하지 않습니다. 작업 상세에서 상태를 확인하거나 관리자에게 문의하세요."} · {reasonLabel(comparison.generation.reason)}</p></div>;
}

function ComparisonSectionHeader({ number, title, description }: { number: string; title: string; description: string }) {
  return <header className="tm-comparison-section__header"><span aria-hidden="true">{number}</span><div><h2 id={`comparison-${number === "1" ? "slack" : number === "2" ? "knowledge" : number === "3" ? "existing-cases" : "candidates"}-title`}>{title}</h2><p>{description}</p></div></header>;
}

function HistoricalTextRow({ label, text }: { label: string; text: HistoricalTextResource }) {
  return <div><dt>{label}</dt><dd><HistoricalText text={text} /></dd></div>;
}

function HistoricalText({ text, fallback }: { text: HistoricalTextResource; fallback?: string }) {
  if (text.state === "PRESENT" && text.value !== null) return <>{text.value}</>;
  if (text.state === "EMPTY") return <span className="tm-comparison-value-state is-empty">봉인 시점에 비어 있음</span>;
  if (text.state === "REDACTED") return <span className="tm-comparison-value-state is-redacted">보호 정책에 따라 가려짐 · {reasonLabel(text.reason)}</span>;
  return <span className="tm-comparison-value-state is-missing">{fallback ?? "봉인된 원본에서 확인할 수 없음"} · {reasonLabel(text.reason)}</span>;
}

function CollectionUnavailable({ availability, reason, emptyText, noun }: { availability: "EMPTY" | "REDACTED" | "MISSING_SOURCE"; reason: string | null; emptyText: string; noun: string }) {
  if (availability === "EMPTY") return <div className="tm-comparison-state is-empty" role="status"><strong>{noun} 없음</strong><p>{emptyText}</p></div>;
  return <div className={`tm-comparison-state is-${availability.toLowerCase()}`} role="status"><strong>{availability === "REDACTED" ? `${noun}이(가) 가려져 있습니다.` : `${noun} 원본을 확인할 수 없습니다.`}</strong><p>{availability === "REDACTED" ? "보호 정책에 따라 표시하지 않습니다." : "현재 원본으로 대신 채우지 않습니다."} · {reasonLabel(reason)}</p></div>;
}

function UnavailableValue({ state, reason, noun }: { state: "REDACTED" | "MISSING_SOURCE"; reason: string | null; noun: string }) {
  return <div className={`tm-comparison-unavailable is-${state.toLowerCase()}`}><strong>{state === "REDACTED" ? `${noun} 가림` : `${noun} 누락`}</strong><span>{reasonLabel(reason)}</span></div>;
}

function SourceReferenceSummary({ references, expanded = false }: { references: ComparisonSourceReferenceResource[]; expanded?: boolean }) {
  if (!references.length) return null;
  return <details className="tm-comparison-source-references" open={expanded || undefined}><summary>봉인 출처 {references.length}개</summary><ol>{references.map((reference) => <li key={reference.contextItemId}><span>#{reference.contextRank}</span><div><strong>{reference.sourceEntityType} · {reference.sourceEntityId}</strong><small>리비전 {reference.sourceRevision}</small></div>{reference.permalink.state === "PRESENT" && reference.permalink.value ? <a href={reference.permalink.value} rel="noreferrer" target="_blank">봉인 링크 ↗</a> : <HistoricalText text={reference.permalink} />}</li>)}</ol></details>;
}

function resolvedSourceSummary(comparison: GenerationComparison, edge: CandidateReferenceEdgeResource) {
  if (edge.target.state !== "RESOLVED") return null;
  if (edge.target.sourceKind === "SLACK_WORK_ITEM") {
    const field = comparison.slackSource.fields.find((item) => item.value.state === "PRESENT" && item.value.value !== null);
    return field?.value.state === "PRESENT" && field.value.value !== null ? field.value.value : `Slack 요청 ${comparison.slackSource.slackWorkItemId}`;
  }
  if (edge.target.sourceKind === "ACTIVE_KNOWLEDGE" && comparison.activeKnowledge.availability === "AVAILABLE") {
    const source = comparison.activeKnowledge.items.find((item) => item.evidenceNodeId === edge.target.sourceId);
    return source?.content.state === "PRESENT" && source.content.value !== null ? source.content.value : source?.selectionReason ?? null;
  }
  if (edge.target.sourceKind === "EXISTING_CASE" && comparison.existingCases.availability === "AVAILABLE") {
    const source = comparison.existingCases.items.find((item) => item.evidenceNodeId === edge.target.sourceId);
    if (source?.content.state === "PRESENT" && source.content.value) return source.content.value.summary;
    return source?.caseKey.state === "PRESENT" && source.caseKey.value !== null ? source.caseKey.value : source?.testCaseVersionId ?? null;
  }
  if (comparison.supplementalEvidence.availability === "AVAILABLE") {
    const source = comparison.supplementalEvidence.items.find((item) => item.evidenceNodeId === edge.target.sourceId);
    return source?.title.state === "PRESENT" && source.title.value !== null ? source.title.value : source?.selectionReason ?? null;
  }
  return null;
}

function resolvedSourceAnchor(edge: CandidateReferenceEdgeResource) {
  if (edge.target.state !== "RESOLVED") return null;
  if (edge.target.sourceKind === "SLACK_WORK_ITEM") return "comparison-slack";
  return `comparison-source-${edge.target.sourceId}`;
}

function reasonLabel(reason: string | null) {
  if (!reason) return "사유 미기록";
  return reasonLabels[reason] ?? reason;
}

function formatComparisonDate(value: string | null | undefined) {
  if (!value) return "기록 없음";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ko-KR");
}

function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}
