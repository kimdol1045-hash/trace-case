import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import slackGoldenManifest from "../../../evals/fixtures/manifest.json";
import sprint6EvalManifest from "../../../evals/fixtures/sprint-6-knowledge/manifest.json";
import deleteRetractionEval from "../../../evals/fixtures/sprint-6-knowledge/sprint6-delete-retraction-01.json";
import evidenceRevisionEval from "../../../evals/fixtures/sprint-6-knowledge/sprint6-evidence-revision-01.json";
import fixtureDocument from "../fixtures/sprint-6-knowledge-contracts.json";
import packageDocument from "../package.json";
import knowledgeApiSchema from "../sprint-6/knowledge-api.schema.json";
import slackBundleExtractionSchema from "../sprint-6/slack-bundle-extraction.schema.json";
import stateEnumSchema from "../state-enums.schema.json";
import { sprint6KnowledgeApiSchema, sprint6SlackBundleExtractionSchema } from "./index";

const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const schemas = [stateEnumSchema, knowledgeApiSchema, slackBundleExtractionSchema];

type JsonObject = Record<string, unknown>;
type SemanticRule =
  | "TEMPORAL_STATUS"
  | "NO_SELF_EDGE"
  | "EVIDENCE_SOURCE_MATCH"
  | "HYBRID_DEGRADATION_ALIGNMENT"
  | "ONTOLOGY_REFERENTIAL_INTEGRITY";
type ContractFixtureCase = {
  name: string;
  schemaRef: string;
  expectedValid: boolean;
  semanticRules?: SemanticRule[];
  payload: unknown;
};
type EvalManifest = {
  synthetic: boolean;
  counts: Record<string, number> & { total: number };
  fixtures: string[];
};

function createValidator() {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  for (const schema of schemas) ajv.addSchema(schema);
  return ajv;
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

function temporalStatus(payload: unknown) {
  const resource = asObject(payload);
  if (!resource || typeof resource.validFrom !== "string") return false;
  if (resource.status === "ACTIVE") return resource.validTo === null;
  if (resource.status === "SUPERSEDED" || resource.status === "RETRACTED") {
    return (
      typeof resource.validTo === "string" &&
      Date.parse(resource.validFrom) <= Date.parse(resource.validTo)
    );
  }
  return resource.status === "AMBIGUOUS";
}

function noSelfEdge(payload: unknown) {
  const edge = asObject(payload);
  return Boolean(edge && edge.fromNodeId !== edge.toNodeId);
}

function evidenceSourceMatch(payload: unknown) {
  const evidence = asObject(payload);
  const citation = asObject(evidence?.citation);
  return Boolean(evidence && citation && JSON.stringify(evidence.source) === JSON.stringify(citation.source));
}

function hybridDegradationAlignment(payload: unknown) {
  const response = asObject(payload);
  const items = asObjects(response?.items);
  const reasons = response?.degradationReasons;
  if (!response || !items || !Array.isArray(reasons)) return false;
  if (!items.every((item) => item.degraded === response.degraded)) return false;
  if ((response.degraded === false) !== (reasons.length === 0)) return false;
  if (!items.every((item) => JSON.stringify(item.degradationReasons) === JSON.stringify(reasons))) return false;
  const ranks = items.map((item) => item.rank);
  const scores = items.map((item) => item.score);
  if (!ranks.every((rank) => Number.isInteger(rank) && Number(rank) >= 1)) return false;
  if (!scores.every((score) => typeof score === "number")) return false;
  if (ranks.some((rank, index) => index > 0 && rank !== Number(ranks[index - 1]) + 1)) return false;
  if (scores.some((score, index) => index > 0 && Number(scores[index - 1]) < Number(score))) return false;
  return items.every((item) => {
    const reason = asObject(item.reason);
    const signals = reason?.signals;
    const citation = asObject(item.citation);
    if (!Array.isArray(signals) || !citation) return false;
    const featureAligned = signals.includes("FEATURE_PROXIMITY")
      ? Number.isInteger(item.featureDistance) && Number(item.featureDistance) >= 0
      : item.featureDistance === null;
    return (
      featureAligned &&
      JSON.stringify(item.source) === JSON.stringify(citation.source) &&
      (!reasons.includes("LIGHTRAG_UNAVAILABLE") || !signals.includes("LIGHTRAG")) &&
      (!reasons.includes("VECTOR_UNAVAILABLE") || !signals.includes("VECTOR"))
    );
  });
}

function ontologyReferentialIntegrity(payload: unknown) {
  const response = asObject(payload);
  const wrappedNodes = asObjects(response?.nodes);
  const edges = asObjects(response?.edges);
  const evidence = asObjects(response?.evidence);
  if (!response || !wrappedNodes || !edges || !evidence) return false;
  const nodes = wrappedNodes.map((wrapped) => asObject(wrapped.node));
  if (nodes.some((node) => !node)) return false;
  const nodeIds = new Set(nodes.map((node) => node?.id));
  const edgeIds = new Set(edges.map((edge) => edge.id));
  const evidenceIds = new Set(evidence.map((item) => item.id));
  return (
    nodeIds.has(response.rootNodeId) &&
    edges.every((edge) => nodeIds.has(edge.fromNodeId) && nodeIds.has(edge.toNodeId)) &&
    evidence.every((item) => {
      const targets = item.subjectType === "NODE" ? nodeIds : edgeIds;
      return targets.has(item.subjectId) && evidenceSourceMatch(item);
    }) &&
    nodes.every(
      (node) => Array.isArray(node?.evidenceIds) && node.evidenceIds.every((id) => evidenceIds.has(id)),
    ) &&
    edges.every(
      (edge) => Array.isArray(edge.evidenceIds) && edge.evidenceIds.every((id) => evidenceIds.has(id)),
    )
  );
}

const semanticValidators: Record<SemanticRule, (payload: unknown) => boolean> = {
  TEMPORAL_STATUS: temporalStatus,
  NO_SELF_EDGE: noSelfEdge,
  EVIDENCE_SOURCE_MATCH: evidenceSourceMatch,
  HYBRID_DEGRADATION_ALIGNMENT: hybridDegradationAlignment,
  ONTOLOGY_REFERENTIAL_INTEGRITY: ontologyReferentialIntegrity,
};

function readEvalManifest() {
  return sprint6EvalManifest as EvalManifest;
}

function readEvalFixture(name: string) {
  const fixtures: Record<string, unknown> = {
    "sprint6-delete-retraction-01.json": deleteRetractionEval,
    "sprint6-evidence-revision-01.json": evidenceRevisionEval,
  };
  return fixtures[name];
}

function validateExtractionSemantics(value: unknown) {
  const evalCase = asObject(value);
  const request = asObject(evalCase?.request);
  const bundle = asObject(request?.bundle);
  const priorKnowledge = asObject(request?.priorKnowledge);
  const priorNodes = asObjects(priorKnowledge?.nodes);
  const result = asObject(evalCase?.expectedResult);
  const revisions = asObjects(bundle?.revisions);
  const candidates = asObjects(result?.candidates);
  const edges = asObjects(result?.edges);
  if (!evalCase || !request || !bundle || !priorNodes || !result || !revisions || !candidates || !edges) {
    return false;
  }

  if (result.bundleId !== bundle.bundleId || result.workItemId !== bundle.workItemId) return false;
  const revisionIds = revisions.map((revision) => revision.sourceRevisionId);
  if (JSON.stringify(result.inputRevisionIds) !== JSON.stringify(revisionIds)) return false;
  if (new Set(revisionIds).size !== revisionIds.length) return false;

  const revisionsById = new Map(revisions.map((revision) => [revision.sourceRevisionId, revision]));
  const revisionsByMessage = new Map<string, JsonObject[]>();
  for (const revision of revisions) {
    if (typeof revision.messageId !== "string") return false;
    revisionsByMessage.set(revision.messageId, [...(revisionsByMessage.get(revision.messageId) ?? []), revision]);
  }
  for (const messageRevisions of revisionsByMessage.values()) {
    for (const [index, revision] of messageRevisions.entries()) {
      if (
        revision.revision !== index + 1 ||
        revision.previousRevisionId !== (index === 0 ? null : messageRevisions[index - 1]?.sourceRevisionId) ||
        (index > 0 &&
          Date.parse(String(revision.occurredAt)) < Date.parse(String(messageRevisions[index - 1]?.occurredAt)))
      ) {
        return false;
      }
    }
  }

  const candidateIds = candidates.map((candidate) => candidate.candidateId);
  const edgeIds = edges.map((edge) => edge.candidateId);
  if (
    new Set(candidateIds).size !== candidateIds.length ||
    new Set(edgeIds).size !== edgeIds.length ||
    JSON.stringify(candidateIds) !== JSON.stringify([...candidateIds].sort()) ||
    JSON.stringify(edgeIds) !== JSON.stringify([...edgeIds].sort())
  ) {
    return false;
  }

  const validateEvidence = (owner: JsonObject) => {
    const evidence = asObjects(owner.evidence);
    if (!evidence) return false;
    return evidence.every((span) => {
      const revision = revisionsById.get(span.sourceRevisionId);
      if (!revision) return false;
      if (revision.kind === "DELETED") {
        return span.quote === null && span.startOffset === null && span.endOffset === null;
      }
      if (
        typeof revision.body !== "string" ||
        typeof span.quote !== "string" ||
        typeof span.startOffset !== "number" ||
        typeof span.endOffset !== "number"
      ) {
        return false;
      }
      return (
        span.startOffset < span.endOffset &&
        revision.body.slice(span.startOffset, span.endOffset) === span.quote
      );
    });
  };
  if (!candidates.every(validateEvidence) || !edges.every(validateEvidence)) return false;

  const candidateIdSet = new Set(candidateIds);
  if (
    !candidates.every(
      (candidate) =>
        Array.isArray(candidate.supersedesCandidateIds) &&
        candidate.supersedesCandidateIds.every((id) => candidateIdSet.has(id) && id !== candidate.candidateId),
    ) ||
    !edges.every(
      (edge) =>
        candidateIdSet.has(edge.fromCandidateId) &&
        candidateIdSet.has(edge.toCandidateId) &&
        edge.fromCandidateId !== edge.toCandidateId,
    )
  ) {
    return false;
  }

  const latestRevisionIds = new Set(
    [...revisionsByMessage.values()].map((messageRevisions) => messageRevisions.at(-1)?.sourceRevisionId),
  );
  for (const candidate of candidates.filter((item) => item.status === "ACTIVE")) {
    const evidence = asObjects(candidate.evidence);
    if (!evidence?.every((span) => latestRevisionIds.has(span.sourceRevisionId))) return false;
  }

  if (evalCase.scenario === "DELETE_RETRACTION") {
    const deleted = revisions.filter((revision) => revision.kind === "DELETED");
    if (deleted.length === 0) return false;
    for (const priorNode of priorNodes.filter((node) => node.status === "ACTIVE")) {
      const retraction = candidates.find(
        (candidate) => candidate.candidateId === priorNode.id && candidate.operation === "RETRACT",
      );
      const retractionEvidence = asObjects(retraction?.evidence);
      if (
        !retraction ||
        retraction.status !== "RETRACTED" ||
        !retractionEvidence?.some((span) => deleted.some((revision) => revision.sourceRevisionId === span.sourceRevisionId))
      ) {
        return false;
      }
    }
  }

  if (evalCase.scenario === "EVIDENCE_REVISION") {
    const active = candidates.filter((candidate) => candidate.status === "ACTIVE");
    const superseded = candidates.filter((candidate) => candidate.status === "SUPERSEDED");
    if (
      active.length === 0 ||
      superseded.length === 0 ||
      !active.every(
        (candidate) =>
          Array.isArray(candidate.supersedesCandidateIds) &&
          candidate.supersedesCandidateIds.some((id) => superseded.some((item) => item.candidateId === id)),
      )
    ) {
      return false;
    }
  }

  return Date.parse(String(result.generatedAt)) >= Date.parse(String(bundle.snapshotAt));
}

describe("Sprint 6 shared knowledge contracts", () => {
  it("compiles every schema and definition in strict Draft 2020-12 mode", () => {
    const ajv = createValidator();
    const ids = new Set<string>();

    for (const schema of [knowledgeApiSchema, slackBundleExtractionSchema]) {
      expect(schema.$schema).toBe(draft202012);
      expect(ids.has(schema.$id), `duplicate schema ID: ${schema.$id}`).toBe(false);
      ids.add(schema.$id);
      expect(ajv.getSchema(schema.$id), `schema did not compile: ${schema.$id}`).toBeTypeOf("function");
      for (const definitionName of Object.keys(schema.$defs)) {
        const definitionRef = `${schema.$id}#/$defs/${definitionName}`;
        expect(ajv.getSchema(definitionRef), `schema definition did not compile: ${definitionRef}`).toBeTypeOf(
          "function",
        );
      }
    }
  });

  it("accepts and rejects deterministic API fixtures with cross-resource semantic rules", () => {
    const ajv = createValidator();
    expect(fixtureDocument.synthetic).toBe(true);

    for (const testCase of fixtureDocument.cases as ContractFixtureCase[]) {
      const validate = ajv.getSchema(testCase.schemaRef);
      expect(validate, `unknown schema ref for ${testCase.name}`).toBeTypeOf("function");
      const schemaValid = validate?.(testCase.payload) ?? false;
      const semanticValid = (testCase.semanticRules ?? []).every((rule) => semanticValidators[rule](testCase.payload));
      expect(
        schemaValid && semanticValid,
        `${testCase.name}: ${ajv.errorsText(validate?.errors)}; semantic=${String(semanticValid)}`,
      ).toBe(testCase.expectedValid);
    }
  });

  it("rejects hybrid pages whose absolute ranks, score order or item degradation metadata drift", () => {
    const fixture = (fixtureDocument.cases as ContractFixtureCase[]).find(
      (item) => item.name === "hybrid pagination page keeps absolute consecutive ranks and descending scores",
    );
    expect(fixture).toBeDefined();
    expect(hybridDegradationAlignment(fixture?.payload)).toBe(true);

    const rankDrift = structuredClone(fixture?.payload) as JsonObject;
    const rankItems = asObjects(rankDrift.items);
    if (rankItems?.[1]) rankItems[1].rank = 6;
    expect(hybridDegradationAlignment(rankDrift)).toBe(false);

    const scoreDrift = structuredClone(fixture?.payload) as JsonObject;
    const scoreItems = asObjects(scoreDrift.items);
    if (scoreItems?.[1]) scoreItems[1].score = 0.99;
    expect(hybridDegradationAlignment(scoreDrift)).toBe(false);

    const degradationDrift = structuredClone(fixture?.payload) as JsonObject;
    const degradationItems = asObjects(degradationDrift.items);
    if (degradationItems?.[0]) degradationItems[0].degradationReasons = ["VECTOR_UNAVAILABLE"];
    expect(hybridDegradationAlignment(degradationDrift)).toBe(false);
  });

  it("covers navigable test-case execution and issue citations plus unresolved safety", () => {
    const fixtures = fixtureDocument.cases as ContractFixtureCase[];
    const expected = new Map([
      ["TEST_CASE_VERSION", "/test-cases/CASE-000003"],
      [
        "EXECUTION_ATTEMPT",
        "/cycles/cycle_00000000000070008000000000000003/execute?caseKey=CASE-000003&platform=IOS&attempt=2&run=1",
      ],
      ["ISSUE_SNAPSHOT", "/issues/ISSUE-7"],
    ]);
    for (const [sourceType, uri] of expected) {
      const fixture = fixtures.find((item) => {
        const citation = asObject(item.payload);
        return asObject(citation?.source)?.sourceType === sourceType && citation?.uri === uri;
      });
      expect(fixture?.expectedValid, `${sourceType} navigable fixture`).toBe(true);
    }
    const unresolved = fixtures.find((item) => item.name === "unresolved QA citation has an explicit null URI");
    expect(asObject(unresolved?.payload)?.uri).toBeNull();
  });

  it("validates deterministic deletion retraction and exact evidence revision evals", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      "https://qa-tool.local/schemas/sprint-6/slack-bundle-extraction.schema.json#/$defs/SlackBundleExtractionEvalCase",
    );
    const manifest = readEvalManifest();
    expect(manifest.synthetic).toBe(true);
    expect(manifest.fixtures).toHaveLength(manifest.counts.total);
    expect(new Set(manifest.fixtures).size).toBe(manifest.fixtures.length);

    const scenarios: string[] = [];
    for (const fixtureName of manifest.fixtures) {
      const evalCase = readEvalFixture(fixtureName);
      const object = asObject(evalCase);
      const schemaValid = validate?.(evalCase) ?? false;
      expect(schemaValid, `${fixtureName}: ${ajv.errorsText(validate?.errors)}`).toBe(true);
      expect(validateExtractionSemantics(evalCase), `${fixtureName}: semantic validation`).toBe(true);
      expect(`${String(object?.evalId)}.json`).toBe(fixtureName);
      scenarios.push(String(object?.scenario));
    }

    for (const [scenario, count] of Object.entries(manifest.counts)) {
      if (scenario === "total") continue;
      expect(scenarios.filter((value) => value === scenario), scenario).toHaveLength(count);
    }
  });

  it("rejects stale or unknown evidence revisions even when the JSON shape remains valid", () => {
    const ajv = createValidator();
    const validate = ajv.getSchema(
      "https://qa-tool.local/schemas/sprint-6/slack-bundle-extraction.schema.json#/$defs/SlackBundleExtractionEvalCase",
    );

    const revisionCase = structuredClone(
      readEvalFixture("sprint6-evidence-revision-01.json"),
    ) as JsonObject;
    const revisionResult = asObject(revisionCase.expectedResult);
    const candidates = asObjects(revisionResult?.candidates);
    const activeEvidence = asObjects(candidates?.find((candidate) => candidate.status === "ACTIVE")?.evidence);
    if (activeEvidence?.[0]) activeEvidence[0].sourceRevisionId = "rev_timeout_policy_1";
    expect(validate?.(revisionCase)).toBe(true);
    expect(validateExtractionSemantics(revisionCase)).toBe(false);

    const deletionCase = structuredClone(
      readEvalFixture("sprint6-delete-retraction-01.json"),
    ) as JsonObject;
    const deletionResult = asObject(deletionCase.expectedResult);
    const deletionCandidates = asObjects(deletionResult?.candidates);
    const deletionEvidence = asObjects(deletionCandidates?.[0]?.evidence);
    if (deletionEvidence?.[0]) deletionEvidence[0].sourceRevisionId = "rev_unknown_tombstone";
    expect(validate?.(deletionCase)).toBe(true);
    expect(validateExtractionSemantics(deletionCase)).toBe(false);
  });

  it("keeps the original twelve normal missing reversal and conflict fixtures unchanged in scope", () => {
    const manifest = slackGoldenManifest as EvalManifest;
    expect(manifest.counts).toEqual({ total: 12, NORMAL: 3, MISSING: 3, REVERSAL: 3, CONFLICT: 3 });
    expect(manifest.fixtures).toHaveLength(12);
    expect(manifest.fixtures).toEqual([
      "slack-work-items/slack-normal-01.json",
      "slack-work-items/slack-normal-02.json",
      "slack-work-items/slack-normal-03.json",
      "slack-work-items/slack-missing-01.json",
      "slack-work-items/slack-missing-02.json",
      "slack-work-items/slack-missing-03.json",
      "slack-work-items/slack-reversal-01.json",
      "slack-work-items/slack-reversal-02.json",
      "slack-work-items/slack-reversal-03.json",
      "slack-work-items/slack-conflict-01.json",
      "slack-work-items/slack-conflict-02.json",
      "slack-work-items/slack-conflict-03.json",
    ]);
  });

  it("publishes all Sprint 6 schema families and package exports", () => {
    expect(Object.keys(knowledgeApiSchema.$defs)).toEqual(
      expect.arrayContaining([
        "KnowledgeNodeResource",
        "KnowledgeEdgeResource",
        "KnowledgeEvidenceResource",
        "KnowledgeStatusPatchRequest",
        "KnowledgeStatusPatchResponse",
        "HybridSearchRequest",
        "HybridSearchResultReason",
        "HybridSearchResultItem",
        "HybridSearchResponse",
        "OntologyExplorerRequest",
        "OntologyExplorerResponse",
      ]),
    );
    expect(Object.keys(slackBundleExtractionSchema.$defs)).toEqual(
      expect.arrayContaining([
        "SlackSourceBundle",
        "SlackBundleExtractionCandidate",
        "SlackBundleExtractionRequest",
        "SlackBundleExtractionResult",
      ]),
    );
    expect(sprint6KnowledgeApiSchema.$id).toBe(knowledgeApiSchema.$id);
    expect(sprint6SlackBundleExtractionSchema.$id).toBe(slackBundleExtractionSchema.$id);
    expect(packageDocument.exports["./schemas/sprint-6/knowledge-api"]).toBe(
      "./sprint-6/knowledge-api.schema.json",
    );
    expect(packageDocument.exports["./schemas/sprint-6/slack-bundle-extraction"]).toBe(
      "./sprint-6/slack-bundle-extraction.schema.json",
    );
  });
});
