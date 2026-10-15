import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { basename, dirname, join, relative, resolve } from "node:path";
import { stdout } from "node:process";
import { fileURLToPath } from "node:url";

import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const contractsDirectory = join(root, "packages/contracts");
const fixturesDirectory = join(root, "evals/fixtures");
const manifestPath = join(fixturesDirectory, "manifest.json");
const generationInputSchemaPath = join(contractsDirectory, "generation/input/1.0.0/schema.json");
const generationReconstructionSchemaPath = join(
  contractsDirectory,
  "generation/reconstruction/1.0.0/schema.json",
);
const generationRetryHistorySchemaPath = join(
  contractsDirectory,
  "generation/retry-history/1.0.0/schema.json",
);
const generationQualityGateSchemaPath = join(
  contractsDirectory,
  "generation/quality-gate/1.0.0/schema.json",
);
const generationQualityReviewSchemaPath = join(
  contractsDirectory,
  "generation/quality-review/1.0.0/schema.json",
);
const generationQualityReviewV2SchemaPath = join(
  contractsDirectory,
  "generation/quality-review/2.0.0/schema.json",
);
const generationVersionRegressionSchemaPath = join(
  contractsDirectory,
  "generation/version-regression/1.0.0/schema.json",
);
const reviewCandidateSchemaPath = join(
  contractsDirectory,
  "review-candidate/1.0.0/schema.json",
);
const generationFixturesDirectory = join(contractsDirectory, "generation/fixtures/1.0.0");

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

function sameMembers(actual, expected, message) {
  assert.deepEqual([...actual].sort(), [...expected].sort(), message);
}

function validateFixtureSemantics(fixture, fixturePath) {
  const label = relative(root, fixturePath);
  const messages = [fixture.source.root, ...fixture.source.thread];
  const revisionIds = messages.map((message) => message.messageRevisionId);
  const knowledgeIds = fixture.expected.knowledgeItems.map((item) => item.knowledgeId);

  assert.equal(new Set(revisionIds).size, revisionIds.length, `${label}: duplicate message revision ID`);
  assert.equal(new Set(knowledgeIds).size, knowledgeIds.length, `${label}: duplicate knowledge ID`);

  for (const revisionId of fixture.expected.parseResult.sourceMessageRevisions) {
    assert(revisionIds.includes(revisionId), `${label}: parse result references unknown revision ${revisionId}`);
  }

  for (const item of fixture.expected.knowledgeItems) {
    for (const revisionId of item.evidenceSourceMessageRevisions) {
      assert(revisionIds.includes(revisionId), `${label}: knowledge references unknown revision ${revisionId}`);
    }
    for (const knowledgeId of item.supersedesKnowledgeIds) {
      assert(knowledgeIds.includes(knowledgeId), `${label}: supersedes references unknown knowledge ${knowledgeId}`);
    }
  }

  const activeIds = fixture.expected.knowledgeItems
    .filter((item) => item.status === "ACTIVE")
    .map((item) => item.knowledgeId);
  const inactiveIds = fixture.expected.knowledgeItems
    .filter((item) => item.status !== "ACTIVE")
    .map((item) => item.knowledgeId);

  sameMembers(
    fixture.expected.generationContext.activeKnowledgeIds,
    activeIds,
    `${label}: generation context must include exactly ACTIVE knowledge`,
  );
  sameMembers(
    fixture.expected.generationContext.excludedKnowledgeIds,
    inactiveIds,
    `${label}: generation context must exclude exactly inactive knowledge`,
  );
}

const schemaFiles = (await readdir(contractsDirectory))
  .filter((name) => name.endsWith(".schema.json"))
  .sort();
assert.equal(schemaFiles.length, 5, "expected exactly five canonical contract schemas");

const canonicalSchemas = await Promise.all(schemaFiles.map((name) => readJson(join(contractsDirectory, name))));
const generationInputSchema = await readJson(generationInputSchemaPath);
const generationReconstructionSchema = await readJson(generationReconstructionSchemaPath);
const generationRetryHistorySchema = await readJson(generationRetryHistorySchemaPath);
const generationQualityGateSchema = await readJson(generationQualityGateSchemaPath);
const generationQualityReviewSchema = await readJson(generationQualityReviewSchemaPath);
const generationQualityReviewV2Schema = await readJson(generationQualityReviewV2SchemaPath);
const generationVersionRegressionSchema = await readJson(generationVersionRegressionSchemaPath);
const reviewCandidateSchema = await readJson(reviewCandidateSchemaPath);
const schemas = [
  ...canonicalSchemas,
  generationInputSchema,
  generationReconstructionSchema,
  generationRetryHistorySchema,
  generationQualityGateSchema,
  generationQualityReviewSchema,
  generationQualityReviewV2Schema,
  generationVersionRegressionSchema,
  reviewCandidateSchema,
];
const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);
for (const schema of schemas) {
  ajv.addSchema(schema);
}
for (const schema of schemas) {
  assert(ajv.getSchema(schema.$id), `schema did not compile: ${schema.$id}`);
}

const generationOutputSchema = canonicalSchemas[schemaFiles.indexOf("ai-test-case-generation-output.schema.json")];
assert(generationOutputSchema, "generation output schema is missing");
const validateGenerationInput = ajv.getSchema(generationInputSchema.$id);
const validateGenerationOutput = ajv.getSchema(generationOutputSchema.$id);
const validateGenerationReconstruction = ajv.getSchema(
  `${generationReconstructionSchema.$id}#/$defs/GetGenerationReconstructionResponse`,
);
assert(validateGenerationInput, "generation input schema did not compile");
assert(validateGenerationOutput, "generation output schema did not compile");
assert(validateGenerationReconstruction, "generation reconstruction schema did not compile");
const generationFixtureManifest = await readJson(join(generationFixturesDirectory, "manifest.json"));
assert.equal(generationFixtureManifest.synthetic, true, "generation fixtures must identify synthetic data");
const generationInput = await readJson(join(generationFixturesDirectory, generationFixtureManifest.input));
const generationOutput = await readJson(join(generationFixturesDirectory, generationFixtureManifest.output));
assert(
  validateGenerationInput(generationInput),
  `generation input fixture is invalid: ${ajv.errorsText(validateGenerationInput.errors)}`,
);
assert(
  validateGenerationOutput(generationOutput),
  `generation output fixture is invalid: ${ajv.errorsText(validateGenerationOutput.errors)}`,
);
for (const fixtureName of Object.values(generationFixtureManifest.reconstruction)) {
  const reconstruction = await readJson(join(generationFixturesDirectory, fixtureName));
  assert(
    validateGenerationReconstruction(reconstruction),
    `${fixtureName}: generation reconstruction fixture is invalid: ${ajv.errorsText(validateGenerationReconstruction.errors)}`,
  );
}

const allowedEvidenceNodeIds = new Set([
  generationInput.slackWorkItem.evidenceNodeId,
  ...generationInput.activeKnowledge.map((item) => item.nodeId),
  ...generationInput.existingCases.map((item) => item.nodeId),
  ...generationInput.failedOrBlockedExecutions.map((item) => item.nodeId),
  ...generationInput.openIssues.map((item) => item.nodeId),
  ...generationInput.extraSources.map((item) => item.evidenceNodeId),
]);
const allowedCategoryIds = new Set(generationInput.categoryCandidates.map((item) => item.categoryId));
assert.equal(
  generationOutput.outputSchemaVersion,
  generationInput.requestedOutputSchemaVersion,
  "generation output must echo the exact requested output version",
);
for (const testCase of generationOutput.cases) {
  assert(
    testCase.evidenceNodeIds.every((id) => allowedEvidenceNodeIds.has(id)),
    "generation output references evidence absent from the trusted input snapshot",
  );
  assert(
    testCase.steps.every((step, index) => step.sequence === index + 1),
    "generation output steps must be contiguous and one-based",
  );
}
for (const category of [generationOutput.categoryCandidate, ...generationOutput.categoryCandidate.alternatives]) {
  assert(
    allowedCategoryIds.has(category.categoryId),
    "generation output references a category absent from the trusted input snapshot",
  );
}

const boardAlignedGenerationOutput = {
  outputSchemaVersion: "1.0.0",
  cases: Array.from({ length: 5 }, (_, index) => ({
    summary: `합성 QA 보드 테스트케이스 ${index + 1}`,
    steps: [
      {
        sequence: 1,
        precondition: "검증 계정과 데이터가 준비되어 있다.",
        action: "대상 기능을 실행한다.",
      },
    ],
    caseExpectedResult: "근거에 명시된 결과가 표시된다.",
    priority: "MEDIUM",
    labels: ["slack-generated"],
    evidenceNodeIds: [`knowledge_${index + 1}`],
    generationReason: "Slack 원문과 현재 ACTIVE 지식에 근거한 정상 흐름을 검증한다.",
    inferenceOnly: false,
    openQuestions: [],
  })),
  categoryCandidate: {
    categoryId: "folder_members",
    score: 0.94,
    alternatives: [],
  },
};
assert(
  validateGenerationOutput(boardAlignedGenerationOutput),
  `board-aligned generation output is invalid: ${ajv.errorsText(validateGenerationOutput.errors)}`,
);
assert.equal(
  validateGenerationOutput({
    ...boardAlignedGenerationOutput,
    folderCandidate: boardAlignedGenerationOutput.categoryCandidate,
  }),
  false,
  "legacy folder/Excel-shaped generation fields must be rejected",
);
assert.equal(
  validateGenerationOutput({
    ...boardAlignedGenerationOutput,
    generationJobId: "provider_invented_generation_job",
  }),
  false,
  "provider-invented trusted generation metadata must be rejected",
);

const manifest = await readJson(manifestPath);
assert.equal(manifest.synthetic, true, "Sprint 0 fixture manifest must identify synthetic data");
assert.equal(manifest.fixtures.length, manifest.counts.total, "fixture manifest total does not match list");
assert.equal(new Set(manifest.fixtures).size, manifest.fixtures.length, "fixture manifest contains duplicate paths");

const fixtureFolder = join(fixturesDirectory, "slack-work-items");
const actualFixturePaths = (await readdir(fixtureFolder))
  .filter((name) => name.endsWith(".json"))
  .map((name) => `slack-work-items/${name}`);
sameMembers(actualFixturePaths, manifest.fixtures, "fixture manifest and directory contents differ");

const fixtureSchemaId = schemas.find((schema) => basename(schema.$id) === "slack-golden-fixture.schema.json")?.$id;
assert(fixtureSchemaId, "golden fixture schema is missing");
const validateFixture = ajv.getSchema(fixtureSchemaId);
assert(validateFixture, "golden fixture schema did not compile");

const fixtureIds = new Set();
const categoryCounts = {};
for (const fixtureName of manifest.fixtures) {
  const fixturePath = resolve(fixturesDirectory, fixtureName);
  assert.equal(fixturePath.startsWith(`${fixturesDirectory}/`), true, `fixture escapes fixture directory: ${fixtureName}`);
  const fixture = await readJson(fixturePath);
  if (!validateFixture(fixture)) {
    throw new Error(`${fixtureName}: ${ajv.errorsText(validateFixture.errors, { separator: "\n" })}`);
  }
  assert.equal(fixture.synthetic, true, `${fixtureName}: fixture must be explicitly synthetic`);
  assert.equal(`${fixture.fixtureId}.json`, basename(fixturePath), `${fixtureName}: fixture ID and filename differ`);
  assert(!fixtureIds.has(fixture.fixtureId), `${fixtureName}: duplicate fixture ID`);
  fixtureIds.add(fixture.fixtureId);
  categoryCounts[fixture.category] = (categoryCounts[fixture.category] ?? 0) + 1;
  validateFixtureSemantics(fixture, fixturePath);
}

for (const [category, count] of Object.entries(manifest.counts)) {
  if (category !== "total") {
    assert.equal(categoryCounts[category], count, `${category}: fixture count differs from manifest`);
  }
}

stdout.write(
  `Validated ${schemas.length} strict Draft 2020-12 schemas, the versioned generation contracts and ${fixtureIds.size} Slack fixtures.\n`,
);
