import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import generationApplyApiSchema from "../generation/apply/1.0.0/schema.json";
import generationComparisonSchema from "../generation/comparison/1.0.0/schema.json";
import generationInputFixture from "../generation/fixtures/1.0.0/input.full-context.json";
import generationOutputFixture from "../generation/fixtures/1.0.0/output.grounded.json";
import generationJobApiSchema from "../generation/job-api/1.0.0/schema.json";
import generationOutputSchema from "../ai-test-case-generation-output.schema.json";
import packageDocument from "../package.json";
import sprint1ErrorSchema from "../sprint-1/error-contract.schema.json";
import stateEnumSchema from "../state-enums.schema.json";
import { generationComparisonV1Schema } from "./index";

const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const schemas = [
  stateEnumSchema,
  sprint1ErrorSchema,
  generationOutputSchema,
  generationJobApiSchema,
  generationApplyApiSchema,
  generationComparisonSchema,
];
const contextDigest = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const resultDigest = "daed45b0fd52791216aa1bc0ff082e9867d9843ff7f5708a276438bc99dab07d";
const placementDigest = "b8bec6e02720c29ffad3b1445f805447e7f2faeb8bd65c86294731759092c658";
const exactContentKeys = [
  "eecf1a73bae9665b841b4336d5c9e704f6e3e5a0ca5e501c4817f9c165aa1738",
  "e784e1da1e6e1f171f07c2e348196be587aac88ed9d09e3325da40853afe9861",
  "7d5e1ea65c67436d0017d73c767606e281f8f122fae3451a2eb0cec7b9e01d68",
  "898d4a854bd0462c2751c6232b43c2b25768f769425f977f9face0fdb4341145",
  "3e0c459d83d4b28a334ec341b9e470dd165b8e33ced91f4802f658bf8e70af60",
] as const;

type JsonObject = Record<string, unknown>;

function createValidator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  for (const schema of schemas) ajv.addSchema(schema);
  return ajv;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function asObject(value: unknown): JsonObject | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : undefined;
}

function asObjects(value: unknown): JsonObject[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const objects = value.map(asObject);
  return objects.every((object) => object !== undefined) ? (objects as JsonObject[]) : undefined;
}

const present = (value: string) => ({ state: "PRESENT", value, reason: null });
const empty = () => ({ state: "EMPTY", value: null, reason: "SEALED_EMPTY" });
const missing = (reason = "NOT_SEALED") => ({ state: "MISSING_SOURCE", value: null, reason });

function sourceReference(
  contextRank: number,
  subjectId: string,
  sourceEntityId: string,
  options: { sourceKind?: "SOURCE_REVISION" | "LEDGER_ENTITY"; sourceEntityType?: string } = {},
) {
  return {
    contextItemId: `generation_context_${String(contextRank).padStart(3, "0")}`,
    contextRank,
    knowledgeEvidenceId: `knowledge_evidence_${String(contextRank).padStart(3, "0")}`,
    subjectType: "NODE",
    subjectId,
    sourceKind: options.sourceKind ?? "SOURCE_REVISION",
    sourceEntityType: options.sourceEntityType ?? "SLACK_MESSAGE_REVISION",
    sourceEntityId,
    sourceRevision: `1787706${String(contextRank).padStart(3, "0")}.000000`,
    contentHash: `sha256:${String(contextRank).padStart(64, "0")}`,
    permalink: present(`https://synthetic.invalid/archives/C_SYNTHETIC/p${1787706000000000 + contextRank}`),
  };
}

const rootReferences = [
  sourceReference(1, "work_item_password_reset", "slack_revision_password_root_1"),
  sourceReference(2, "work_item_password_reset", "slack_revision_password_reply_1"),
  sourceReference(3, "work_item_password_reset", "slack_revision_password_reply_2"),
];
const sourceReferencesByEvidence: Record<string, ReturnType<typeof sourceReference>[]> = {
  work_item_password_reset: rootReferences,
  decision_password_code_expiry: [
    sourceReference(4, "decision_password_code_expiry", "slack_revision_password_reply_1"),
  ],
  decision_password_attempt_lock: [
    sourceReference(5, "decision_password_attempt_lock", "slack_revision_password_reply_2"),
  ],
  test_case_password_reset_happy_path: [
    sourceReference(6, "test_case_password_reset_happy_path", "case_version_password_reset_3", {
      sourceKind: "LEDGER_ENTITY",
      sourceEntityType: "TEST_CASE_VERSION",
    }),
  ],
  execution_password_lock_bypass: [
    sourceReference(7, "execution_password_lock_bypass", "execution_attempt_password_lock_bypass", {
      sourceKind: "LEDGER_ENTITY",
      sourceEntityType: "EXECUTION_ATTEMPT",
    }),
  ],
  issue_node_password_lock_bypass: [
    sourceReference(8, "issue_node_password_lock_bypass", "issue_revision_password_lock_1", {
      sourceKind: "LEDGER_ENTITY",
      sourceEntityType: "ISSUE",
    }),
  ],
  extra_design_password_lock_state: [
    sourceReference(9, "extra_design_password_lock_state", "extra_source_password_lock_design", {
      sourceKind: "LEDGER_ENTITY",
      sourceEntityType: "EXTRA_SOURCE",
    }),
  ],
};
const sourceKindsByEvidence: Record<string, string> = {
  work_item_password_reset: "SLACK_WORK_ITEM",
  decision_password_code_expiry: "ACTIVE_KNOWLEDGE",
  decision_password_attempt_lock: "ACTIVE_KNOWLEDGE",
  test_case_password_reset_happy_path: "EXISTING_CASE",
  execution_password_lock_bypass: "RISK_EXECUTION",
  issue_node_password_lock_bypass: "OPEN_ISSUE",
  extra_design_password_lock_state: "EXPLICIT_SOURCE",
};

function notAppliedCandidate(candidate: (typeof generationOutputFixture.cases)[number], index: number) {
  return {
    index,
    candidate,
    apply: { state: "NOT_APPLIED", applyId: null, outcome: null },
  };
}

function reusedOutcome(generatedCaseIndex: number) {
  const suffix = String(generatedCaseIndex + 1).padStart(3, "0");
  return {
    generatedCaseIndex,
    exactContentKey: exactContentKeys[generatedCaseIndex],
    outcome: "REUSED",
    ledgerMutation: "NONE",
    preserved: true,
    matchReason: "EXACT",
    case: {
      id: `case_existing_${suffix}`,
      key: `TC-${String(generatedCaseIndex + 10001).padStart(6, "0")}`,
      workItemId: "work_item_password_reset",
      categoryId: "folder_members",
      status: "ACTIVE",
      resourceVersion: 5,
      currentVersionId: `case_version_existing_${suffix}`,
      currentVersion: 3,
    },
  };
}

const comparisonResponse = {
  comparisonSchemaVersion: "1.0.0",
  job: {
    id: "generation_job_001",
    slackWorkItemId: "slack_work_item_password_reset",
    status: "SUCCEEDED",
    lineage: {
      rootJobId: "generation_job_001",
      retryOfJobId: null,
      attemptNumber: 1,
    },
    context: {
      inputSchemaVersion: "1.0.0",
      outputSchemaVersion: "1.0.0",
      sourceVersion: 3,
      generationVersion: 1,
      ontologySchemaId: "ontology_schema_001",
      promptVersion: "generation_prompt_v1",
      itemCount: 9,
      digest: contextDigest,
      sealedAt: "2026-08-26T01:00:00Z",
    },
    createdAt: "2026-08-26T01:00:00Z",
    startedAt: "2026-08-26T01:01:00Z",
    completedAt: "2026-08-26T01:01:05Z",
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
    slackWorkItemId: "slack_work_item_password_reset",
    sourceVersion: 3,
    workspaceId: present("T_SYNTHETIC"),
    channelId: present("C_SYNTHETIC"),
    messageTs: present("1787706000.000000"),
    threadTs: present("1787706000.000000"),
    permalink: present("https://synthetic.invalid/archives/C_SYNTHETIC/p1787706000000000"),
    fields: [
      {
        field: "DEVELOPMENT_ITEM",
        value: present(generationInputFixture.slackWorkItem.developmentItem),
        sourceReferences: [rootReferences[0]],
      },
      {
        field: "REQUESTED_MENU",
        value: present(generationInputFixture.slackWorkItem.requestedMenu),
        sourceReferences: [rootReferences[0]],
      },
      {
        field: "FEATURE_DESCRIPTION",
        value: present(generationInputFixture.slackWorkItem.featureDescription),
        sourceReferences: [rootReferences[0]],
      },
      {
        field: "REQUEST_DETAILS",
        value: present(generationInputFixture.slackWorkItem.requestDetails),
        sourceReferences: rootReferences,
      },
    ],
    threadEvidence: {
      availability: "AVAILABLE",
      reason: null,
      items: [
        {
          index: 0,
          sourceRevisionId: "slack_revision_password_root_1",
          messageId: "slack_message_password_root",
          messageTs: "1787706000.000000",
          revisionTs: "1787706000.000000",
          role: "ROOT",
          operation: "CREATE",
          authorId: present("U_SYNTHETIC_1"),
          body: present("비밀번호 재설정 기능 개선 요청"),
          permalink: present("https://synthetic.invalid/archives/C_SYNTHETIC/p1787706000000000"),
          recordedAt: "2026-08-26T00:00:00Z",
          sourceReferences: [rootReferences[0]],
        },
        {
          index: 1,
          sourceRevisionId: "slack_revision_password_reply_1",
          messageId: "slack_message_password_reply_1",
          messageTs: "1787706001.000000",
          revisionTs: "1787706001.000000",
          role: "REPLY",
          operation: "CREATE",
          authorId: present("U_SYNTHETIC_2"),
          body: present("인증번호 유효시간은 5분입니다."),
          permalink: present("https://synthetic.invalid/archives/C_SYNTHETIC/p1787706001000000"),
          recordedAt: "2026-08-26T00:00:01Z",
          sourceReferences: [rootReferences[1]],
        },
        {
          index: 2,
          sourceRevisionId: "slack_revision_password_reply_2",
          messageId: "slack_message_password_reply_2",
          messageTs: "1787706002.000000",
          revisionTs: "1787706002.000000",
          role: "REPLY",
          operation: "CREATE",
          authorId: empty(),
          body: present("인증번호 5회 오류 시 인증 요청을 잠급니다."),
          permalink: present("https://synthetic.invalid/archives/C_SYNTHETIC/p1787706002000000"),
          recordedAt: "2026-08-26T00:00:02Z",
          sourceReferences: [rootReferences[2]],
        },
      ],
    },
  },
  activeKnowledge: {
    availability: "AVAILABLE",
    reason: null,
    items: generationInputFixture.activeKnowledge.map((item) => ({
      evidenceNodeId: item.nodeId,
      nodeType: item.nodeType,
      status: item.status,
      activeAtSeal: true,
      content: present(item.content),
      validFrom: item.validFrom,
      validTo: item.validTo,
      selectionReason: item.selectionReason,
      sourceReferences: sourceReferencesByEvidence[item.nodeId],
    })),
  },
  existingCases: {
    availability: "AVAILABLE",
    reason: null,
    items: generationInputFixture.existingCases.map((item) => ({
      evidenceNodeId: item.nodeId,
      testCaseId: item.testCaseId,
      testCaseVersionId: item.testCaseVersionId,
      caseKey: present(item.caseKey),
      status: item.status,
      content: {
        state: "PRESENT",
        value: {
          summary: item.summary,
          precondition: item.precondition,
          steps: item.steps,
          expectedResultMode: item.expectedResultMode,
          caseExpectedResult: item.caseExpectedResult,
          priority: item.priority,
          labels: item.labels,
        },
        reason: null,
      },
      selectionReason: item.selectionReason,
      sourceReferences: sourceReferencesByEvidence[item.nodeId],
    })),
  },
  supplementalEvidence: {
    availability: "AVAILABLE",
    reason: null,
    items: [
      {
        evidenceNodeId: "execution_password_lock_bypass",
        kind: "RISK_EXECUTION",
        title: present("최근 FAIL 실행"),
        snapshot: { state: "PRESENT", value: generationInputFixture.failedOrBlockedExecutions[0], reason: null },
        selectionReason: generationInputFixture.failedOrBlockedExecutions[0]!.selectionReason,
        sourceReferences: sourceReferencesByEvidence.execution_password_lock_bypass,
      },
      {
        evidenceNodeId: "issue_node_password_lock_bypass",
        kind: "OPEN_ISSUE",
        title: present(generationInputFixture.openIssues[0]!.summary),
        snapshot: { state: "PRESENT", value: generationInputFixture.openIssues[0], reason: null },
        selectionReason: generationInputFixture.openIssues[0]!.selectionReason,
        sourceReferences: sourceReferencesByEvidence.issue_node_password_lock_bypass,
      },
      {
        evidenceNodeId: "extra_design_password_lock_state",
        kind: "EXPLICIT_SOURCE",
        title: present(generationInputFixture.extraSources[0]!.title),
        snapshot: { state: "PRESENT", value: generationInputFixture.extraSources[0], reason: null },
        selectionReason: "사용자가 이 생성 요청에 명시적으로 제공한 근거다.",
        sourceReferences: sourceReferencesByEvidence.extra_design_password_lock_state,
      },
    ],
  },
  generation: {
    availability: "AVAILABLE",
    outputSchemaVersion: generationOutputFixture.outputSchemaVersion,
    resultDigest,
    categoryCandidate: generationOutputFixture.categoryCandidate,
    candidates: generationOutputFixture.cases.map(notAppliedCandidate),
    reason: null,
  },
  referenceEdges: {
    availability: "AVAILABLE",
    reason: null,
    items: generationOutputFixture.cases.flatMap((candidate, candidateIndex) =>
      candidate.evidenceNodeIds.map((evidenceNodeId, evidenceIndex) => ({
        candidateIndex,
        evidenceIndex,
        evidenceNodeId,
        relation: "SUPPORTED_BY",
        target: {
          state: "RESOLVED",
          sourceKind: sourceKindsByEvidence[evidenceNodeId],
          sourceId: evidenceNodeId,
          contextReferences: sourceReferencesByEvidence[evidenceNodeId],
          reason: null,
        },
      })),
    ),
  },
  apply: { state: "NOT_APPLIED", receipt: null },
};

function comparisonSemanticsAreValid(value: unknown) {
  const response = asObject(value);
  const job = asObject(response?.job);
  const generation = asObject(response?.generation);
  const candidates = asObjects(generation?.candidates);
  const references = asObject(response?.referenceEdges);
  const edges = asObjects(references?.items);
  const apply = asObject(response?.apply);
  const receipt = asObject(apply?.receipt);
  if (!response || !job || !generation || !candidates || !references || !edges || !apply) return false;

  if (generation.availability === "AVAILABLE") {
    if (
      job.status !== "SUCCEEDED"
      || references.availability !== "AVAILABLE"
      || !candidates.every((candidate, index) => candidate.index === index)
    ) {
      return false;
    }
    const expectedEdges = candidates.flatMap((candidate, candidateIndex) => {
      const generated = asObject(candidate.candidate);
      const evidence = Array.isArray(generated?.evidenceNodeIds) ? generated.evidenceNodeIds : [];
      return evidence.map((evidenceNodeId, evidenceIndex) => ({ candidateIndex, evidenceIndex, evidenceNodeId }));
    });
    if (expectedEdges.length !== edges.length) return false;
    for (const [index, expected] of expectedEdges.entries()) {
      const edge = edges[index];
      const target = asObject(edge?.target);
      const targetReferences = asObjects(target?.contextReferences);
      if (
        edge?.candidateIndex !== expected.candidateIndex
        || edge.evidenceIndex !== expected.evidenceIndex
        || edge.evidenceNodeId !== expected.evidenceNodeId
        || target?.sourceId !== expected.evidenceNodeId
        || !targetReferences?.length
        || !targetReferences.every(
          (reference) => reference.subjectId === expected.evidenceNodeId,
        )
      ) {
        return false;
      }
    }
  } else if (candidates.length !== 0 || edges.length !== 0) {
    return false;
  }

  const applied = apply.state === "APPLIED";
  if (applied !== Boolean(receipt)) return false;
  if (applied) {
    if (!receipt) return false;
    const context = asObject(job.context);
    const counts = asObject(receipt.counts);
    if (
      receipt.generationJobId !== job.id
      || receipt.contextDigest !== context?.digest
      || receipt.resultDigest !== generation.resultDigest
      || counts?.generated !== candidates.length
    ) {
      return false;
    }
  }
  return candidates.every((candidate, index) => {
    const linkage = asObject(candidate.apply);
    const outcome = asObject(linkage?.outcome);
    if (!applied) return linkage?.state === "NOT_APPLIED" && outcome === undefined;
    return (
      linkage?.state === "APPLIED"
      && linkage.applyId === receipt?.id
      && outcome?.generatedCaseIndex === index
    );
  });
}

describe("Phase E generation comparison read-model 1.0.0 contracts", () => {
  it("compiles the schema and every definition in strict Draft 2020-12 mode", () => {
    const ajv = createValidator();

    expect(generationComparisonSchema.$schema).toBe(draft202012);
    expect(generationComparisonSchema.$id).toBe(
      "https://qa-tool.local/schemas/generation/comparison/1.0.0/schema.json",
    );
    expect(ajv.getSchema(generationComparisonSchema.$id)).toBeTypeOf("function");
    for (const definitionName of Object.keys(generationComparisonSchema.$defs)) {
      const definitionRef = `${generationComparisonSchema.$id}#/$defs/${definitionName}`;
      expect(ajv.getSchema(definitionRef), `schema definition did not compile: ${definitionRef}`).toBeTypeOf(
        "function",
      );
    }
  });

  it("publishes the versioned package and TypeScript index surfaces", () => {
    expect(generationComparisonV1Schema).toBe(generationComparisonSchema);
    expect(packageDocument.exports).toHaveProperty(
      "./schemas/generation/comparison/1.0.0",
      "./generation/comparison/1.0.0/schema.json",
    );
  });

  it("accepts the authoritative historical comparison and preserves candidate/reference order", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationComparisonSchema.$id}#/$defs/GetGenerationComparisonResponse`,
    );

    expect(validate?.(comparisonResponse), ajv.errorsText(validate?.errors)).toBe(true);
    expect(comparisonSemanticsAreValid(comparisonResponse)).toBe(true);
    expect(comparisonResponse.generation.candidates.map((item) => item.candidate)).toEqual(
      generationOutputFixture.cases,
    );
    expect(comparisonResponse.referenceEdges.items.map((item) => [
      item.candidateIndex,
      item.evidenceIndex,
      item.evidenceNodeId,
    ])).toEqual(
      generationOutputFixture.cases.flatMap((candidate, candidateIndex) =>
        candidate.evidenceNodeIds.map((evidenceNodeId, evidenceIndex) => [
          candidateIndex,
          evidenceIndex,
          evidenceNodeId,
        ]),
      ),
    );
  });

  it("requires exact sealed Slack fields, chronological revision evidence, and ACTIVE knowledge", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationComparisonSchema.$id}#/$defs/GetGenerationComparisonResponse`,
    );
    const fieldNames = comparisonResponse.slackSource.fields.map((field) => field.field);
    expect(fieldNames).toEqual([
      "DEVELOPMENT_ITEM",
      "REQUESTED_MENU",
      "FEATURE_DESCRIPTION",
      "REQUEST_DETAILS",
    ]);
    expect(comparisonResponse.slackSource.threadEvidence.items.map((item) => item.index)).toEqual([0, 1, 2]);
    expect(comparisonResponse.activeKnowledge.items.every((item) => (
      item.status === "ACTIVE" && item.activeAtSeal && item.validTo === null
    ))).toBe(true);

    const liveSlackSubstitution = clone(comparisonResponse) as unknown as JsonObject;
    (liveSlackSubstitution.slackSource as JsonObject).liveThread = [{ body: "current text" }];
    expect(validate?.(liveSlackSubstitution)).toBe(false);

    const nonActiveKnowledge = clone(comparisonResponse) as unknown as JsonObject;
    (((nonActiveKnowledge.activeKnowledge as JsonObject).items as JsonObject[])[0]!).status = "SUPERSEDED";
    expect(validate?.(nonActiveKnowledge)).toBe(false);
  });

  it("keeps immutable existing-case identity even when historical display content is unavailable", () => {
    const ajv = createValidator();
    const validateCase = ajv.getSchema(
      `${generationComparisonSchema.$id}#/$defs/ExistingCaseComparison`,
    );
    const existing = clone(comparisonResponse.existingCases.items[0]) as unknown as JsonObject;
    const identity = [existing.testCaseId, existing.testCaseVersionId];

    existing.caseKey = missing("LEGACY_CONTEXT");
    existing.content = { state: "MISSING_SOURCE", value: null, reason: "LEGACY_CONTEXT" };
    expect(validateCase?.(existing), createValidator().errorsText(validateCase?.errors)).toBe(true);
    expect([existing.testCaseId, existing.testCaseVersionId]).toEqual(identity);

    const currentVersionSubstitution = clone(existing) as JsonObject;
    currentVersionSubstitution.currentVersion = { id: "case_version_live", summary: "live" };
    expect(validateCase?.(currentVersionSubstitution)).toBe(false);
  });

  it("distinguishes EMPTY, REDACTED, and MISSING_SOURCE without nullable ambiguity", () => {
    const ajv = createValidator();
    const validateText = ajv.getSchema(`${generationComparisonSchema.$id}#/$defs/HistoricalText`);
    const validateKnowledge = ajv.getSchema(
      `${generationComparisonSchema.$id}#/$defs/ActiveKnowledgeCollection`,
    );
    const validateThread = ajv.getSchema(
      `${generationComparisonSchema.$id}#/$defs/SlackThreadEvidence`,
    );

    expect(validateText?.(empty())).toBe(true);
    expect(validateText?.({ state: "REDACTED", value: null, reason: "SENSITIVE_DATA" })).toBe(true);
    expect(validateText?.(missing("NOT_SEALED"))).toBe(true);
    expect(validateText?.({ state: "MISSING_SOURCE", value: "live fallback", reason: "NOT_SEALED" })).toBe(false);
    expect(validateText?.({ state: "REDACTED", value: null, reason: null })).toBe(false);

    expect(validateKnowledge?.({ availability: "EMPTY", items: [], reason: "SEALED_EMPTY" })).toBe(true);
    expect(validateKnowledge?.({ availability: "REDACTED", items: [], reason: "AUTHORIZATION_POLICY" })).toBe(true);
    expect(validateKnowledge?.({ availability: "MISSING_SOURCE", items: [], reason: "LEGACY_CONTEXT" })).toBe(true);
    expect(validateKnowledge?.({ availability: "EMPTY", items: [], reason: null })).toBe(false);
    expect(validateThread?.({ availability: "MISSING_SOURCE", items: [], reason: "SOURCE_INCOMPLETE" })).toBe(true);
  });

  it("returns a stable empty generation/reference view for a job that has no stored result", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationComparisonSchema.$id}#/$defs/GetGenerationComparisonResponse`,
    );
    const queued = clone(comparisonResponse) as unknown as JsonObject;
    const job = queued.job as JsonObject;
    job.status = "QUEUED";
    job.startedAt = null;
    job.completedAt = null;
    queued.generation = {
      availability: "EMPTY",
      outputSchemaVersion: "1.0.0",
      resultDigest: null,
      categoryCandidate: null,
      candidates: [],
      reason: "JOB_NOT_SUCCEEDED",
    };
    queued.referenceEdges = {
      availability: "EMPTY",
      items: [],
      reason: "NO_GENERATED_CANDIDATES",
    };

    expect(validate?.(queued), ajv.errorsText(validate?.errors)).toBe(true);
    expect(comparisonSemanticsAreValid(queued)).toBe(true);

    const illegalAvailableResult = clone(queued) as JsonObject;
    illegalAvailableResult.generation = comparisonResponse.generation;
    illegalAvailableResult.referenceEdges = comparisonResponse.referenceEdges;
    expect(validate?.(illegalAvailableResult)).toBe(true);
    expect(comparisonSemanticsAreValid(illegalAvailableResult)).toBe(false);
  });

  it("uses UNKNOWN only for an unresolved unavailable reference", () => {
    const ajv = createValidator();
    const validateUnavailable = ajv.getSchema(
      `${generationComparisonSchema.$id}#/$defs/UnavailableReferenceTarget`,
    );
    const validateResolved = ajv.getSchema(
      `${generationComparisonSchema.$id}#/$defs/ResolvedReferenceTarget`,
    );
    const unresolved = {
      state: "MISSING_SOURCE",
      sourceKind: "UNKNOWN",
      sourceId: "knowledge_node_unresolved",
      contextReferences: [],
      reason: "UNRESOLVED_REFERENCE",
    };
    expect(validateUnavailable?.(unresolved), ajv.errorsText(validateUnavailable?.errors)).toBe(
      true,
    );
    expect(
      validateResolved?.({ ...unresolved, state: "RESOLVED", reason: null }),
    ).toBe(false);
  });

  it("links every generated candidate to its created/reused receipt outcome when applied", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationComparisonSchema.$id}#/$defs/GetGenerationComparisonResponse`,
    );
    const applied = clone(comparisonResponse) as unknown as JsonObject;
    const generation = applied.generation as JsonObject;
    const candidates = generation.candidates as JsonObject[];
    const outcomes = candidates.map((_, index) => reusedOutcome(index));
    for (const [index, candidate] of candidates.entries()) {
      candidate.apply = {
        state: "APPLIED",
        applyId: "generation_apply_001",
        outcome: outcomes[index],
      };
    }
    applied.apply = {
      state: "APPLIED",
      receipt: {
        id: "generation_apply_001",
        generationJobId: "generation_job_001",
        contextDigest,
        resultDigest,
        placementDigest,
        transactionStatus: "COMMITTED",
        parent: {
          outcome: "REUSED",
          ledgerMutation: "NONE",
          workItemId: "work_item_password_reset",
          key: "WORK-1042",
          categoryId: "folder_members",
          resourceVersion: 3,
        },
        counts: { generated: 5, created: 0, reused: 5 },
        auditId: "audit_generation_apply_001",
        appliedAt: "2026-08-26T07:00:00Z",
      },
    };

    expect(validate?.(applied), ajv.errorsText(validate?.errors)).toBe(true);
    expect(comparisonSemanticsAreValid(applied)).toBe(true);

    const wrongIndex = clone(applied) as JsonObject;
    const firstLinkage = ((wrongIndex.generation as JsonObject).candidates as JsonObject[])[0]!.apply as JsonObject;
    (firstLinkage.outcome as JsonObject).generatedCaseIndex = 1;
    expect(validate?.(wrongIndex)).toBe(true);
    expect(comparisonSemanticsAreValid(wrongIndex)).toBe(false);
  });

  it("defines one authoritative GET and forbids T12 reconstruction, provider calls, and mutations", () => {
    const definitionText = JSON.stringify(generationComparisonSchema.$defs.GenerationComparisonResponse);
    expect(generationComparisonSchema.$defs.GenerationComparisonResponse.description).toContain(
      "GET /api/generation-jobs/{jobId}/comparison",
    );
    expect(generationComparisonSchema.$defs.ComparisonReadPolicy.properties).toMatchObject({
      sourceBasis: { const: "SEALED_GENERATION_JOB" },
      readOnly: { const: true },
      liveSourceSubstitution: { const: false },
      generationInputReconstructed: { const: false },
      providerCallAttempted: { const: false },
      mutationPerformed: { const: false },
    });
    expect(definitionText).not.toContain("providerId");
    expect(definitionText).not.toContain("modelId");

    const ajv = createValidator();
    const validate = ajv.getSchema(
      `${generationComparisonSchema.$id}#/$defs/GetGenerationComparisonResponse`,
    );
    for (const field of ["provider", "model", "reconstructedInput", "liveSource", "mutationReceipt"] as const) {
      expect(validate?.({ ...comparisonResponse, [field]: {} }), `accepted ${field}`).toBe(false);
    }
    expect(validate?.({
      ...comparisonResponse,
      policy: { ...comparisonResponse.policy, providerCallAttempted: true },
    })).toBe(false);
    expect(validate?.({
      ...comparisonResponse,
      policy: { ...comparisonResponse.policy, generationInputReconstructed: true },
    })).toBe(false);
  });
});
