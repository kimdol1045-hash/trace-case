import assert from "node:assert/strict";
import { readFile, readdir, stat } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const adrDirectory = join(root, "docs/adr");
const ledgerPath = join(adrDirectory, "README.md");
const coveragePath = join(adrDirectory, "decision-coverage.json");
const sprintZeroReportPath = join(root, "docs/sprint-0/sprint-0-report.md");
const sprintOneReportPath = join(root, "docs/sprint-1/sprint-1-report.md");
const contractsDirectory = join(root, "packages/contracts");
const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const requiredSections = [
  "Context",
  "Decision Drivers",
  "Considered Options",
  "Decision",
  "Consequences",
  "Risks and Mitigations",
  "Validation Evidence",
  "Revisit Triggers",
  "Related Documents",
];
const sprintOneDecisionRequirements = new Map([
  [
    "postgresql-adjacency-list-folder-hierarchy",
    {
      canonicalAdr: "0013",
      markers: [
        "adjacency list",
        "WITH RECURSIVE",
        "MOVE_SUBTREE",
        "normalized_name",
        "partial unique index",
        "folder_aliases",
        "directTestCaseCount",
        "subtreeTestCaseCount",
        "SOFT_DELETE_SUBTREE",
        "RESTORE_SUBTREE",
        "AI_GENERATED_INBOX",
        "SYSTEM_FOLDER_PROTECTED",
      ],
      schemas: ["folder-tree.schema.json", "folder-mutation.schema.json", "error-contract.schema.json"],
    },
  ],
  [
    "workspace-key-allocation-append-only-audit-transaction",
    {
      canonicalAdr: "0014",
      markers: [
        "workspace_key_counters",
        "UPDATE ... RETURNING",
        "Idempotency-Key",
        "audit_logs",
        "append-only",
        "INSERT",
        "UPDATE",
        "DELETE",
        "before",
        "after",
        "같은 PostgreSQL transaction",
      ],
      schemas: ["workspace.schema.json", "audit.schema.json", "error-contract.schema.json"],
    },
  ],
]);

async function read(path) {
  return readFile(path, "utf8");
}

function section(markdown, heading) {
  const start = markdown.indexOf(`## ${heading}\n`);
  assert(start >= 0, `missing section: ${heading}`);
  const bodyStart = start + `## ${heading}\n`.length;
  const next = markdown.indexOf("\n## ", bodyStart);
  return markdown.slice(bodyStart, next < 0 ? markdown.length : next);
}

async function validateLocalLinks(path, markdown) {
  const linkPattern = /\[[^\]]*\]\(([^)]+)\)/g;
  for (const match of markdown.matchAll(linkPattern)) {
    const rawTarget = match[1].trim().replace(/^<|>$/g, "");
    if (/^(?:https?:|mailto:|#)/.test(rawTarget)) {
      continue;
    }
    const target = decodeURIComponent(rawTarget.split("#", 1)[0]);
    if (!target) {
      continue;
    }
    const resolved = resolve(dirname(path), target);
    try {
      await stat(resolved);
    } catch {
      assert.fail(`${relative(root, path)}: broken local link ${rawTarget}`);
    }
  }
}

const adrFiles = (await readdir(adrDirectory))
  .filter((name) => /^\d{4}-.*\.md$/.test(name) && !name.startsWith("0000-"))
  .sort();
const adrNumbers = adrFiles.map((name) => name.slice(0, 4));
assert.deepEqual(
  adrNumbers,
  Array.from({ length: adrFiles.length }, (_, index) => String(index + 1).padStart(4, "0")),
  "ADR numbers must be unique and contiguous from 0001",
);
assert(adrNumbers.includes("0013") && adrNumbers.includes("0014"), "Sprint 1 canonical ADRs 0013 and 0014 are required");

const ledger = await read(ledgerPath);
await validateLocalLinks(ledgerPath, ledger);
const states = new Map();
const adrMarkdownByNumber = new Map();
for (const file of adrFiles) {
  const path = join(adrDirectory, file);
  const number = file.slice(0, 4);
  const markdown = await read(path);
  assert(markdown.startsWith(`# ADR-${number}: `), `${file}: title must start with ADR number`);
  assert(markdown.includes(`| ADR 번호 | ${number} |`), `${file}: metadata ADR number differs`);
  const stateMatch = markdown.match(/\| 상태 \| (Accepted|Proposed|Superseded) \|/);
  assert(stateMatch, `${file}: state must be Accepted, Proposed or Superseded`);
  const state = stateMatch[1];
  states.set(number, state);
  adrMarkdownByNumber.set(number, markdown);

  for (const heading of requiredSections) {
    assert.equal(
      markdown.match(new RegExp(`^## ${heading}$`, "gm"))?.length,
      1,
      `${file}: expected exactly one ${heading} section`,
    );
  }
  const evidence = section(markdown, "Validation Evidence");
  const triggers = section(markdown, "Revisit Triggers");
  assert((triggers.match(/^- /gm) ?? []).length >= 2, `${file}: needs at least two revisit triggers`);
  if (state === "Accepted") {
    assert((evidence.match(/\[[^\]]+\]\([^)]+\)/g) ?? []).length >= 2, `${file}: Accepted ADR needs linked evidence`);
  } else if (state === "Proposed") {
    assert(/아직|없(?:고|다|음)|미구현|미검증/.test(evidence), `${file}: Proposed ADR must name missing evidence`);
  } else {
    assert(/대체|Superseded|0015/.test(markdown), `${file}: Superseded ADR must name its replacement`);
  }
  const indexPattern = new RegExp(`^\\| \\[${number}\\]\\(\\./${file.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\) \\|[^\\n]+\\| ${state} \\|`, "m");
  assert(indexPattern.test(ledger), `${file}: ledger index state/link is missing or stale`);
  await validateLocalLinks(path, markdown);
}

const coverage = JSON.parse(await read(coveragePath));
assert.equal(coverage.schemaVersion, 2, "unsupported ADR coverage schema version");
const specificationPath = resolve(adrDirectory, coverage.source);
const specification = await read(specificationPath);
await validateLocalLinks(specificationPath, specification);
await validateLocalLinks(sprintZeroReportPath, await read(sprintZeroReportPath));
const sprintOneReport = await read(sprintOneReportPath);
await validateLocalLinks(sprintOneReportPath, sprintOneReport);
assert(
  sprintOneReport.includes("## 9. ADR coverage와 최종 Sprint 1 판정"),
  "Sprint 1 report must include final ADR coverage and verdict",
);
const decisionIds = coverage.decisions.map((decision) => decision.id);
assert.equal(new Set(decisionIds).size, decisionIds.length, "duplicate decision ID in ADR coverage");
const coveredAdrs = coverage.decisions.map((decision) => decision.canonicalAdr).sort();
const activeAdrNumbers = adrNumbers.filter((number) => states.get(number) !== "Superseded");
assert.deepEqual(coveredAdrs, activeAdrNumbers, "every non-superseded ADR must be canonical for exactly one major decision");
const validatedContractSchemas = new Map();
for (const decision of coverage.decisions) {
  assert(states.has(decision.canonicalAdr), `${decision.id}: canonical ADR does not exist`);
  assert.notEqual(states.get(decision.canonicalAdr), "Superseded", `${decision.id}: canonical ADR cannot be Superseded`);
  assert(decision.summary.trim(), `${decision.id}: summary is required`);
  assert(decision.specSections.length > 0, `${decision.id}: at least one spec section is required`);
  for (const heading of decision.specSections) {
    assert(
      specification.includes(`## ${heading}`) || specification.includes(`### ${heading}`),
      `${decision.id}: development specification section not found: ${heading}`,
    );
  }

  if (decision.contractSchemas !== undefined) {
    assert(Array.isArray(decision.contractSchemas), `${decision.id}: contractSchemas must be an array`);
    assert(decision.contractSchemas.length > 0, `${decision.id}: contractSchemas must not be empty`);
    assert.equal(
      new Set(decision.contractSchemas).size,
      decision.contractSchemas.length,
      `${decision.id}: duplicate contract schema path`,
    );
    for (const target of decision.contractSchemas) {
      assert.equal(typeof target, "string", `${decision.id}: contract schema path must be a string`);
      const schemaPath = resolve(adrDirectory, target);
      assert(
        schemaPath.startsWith(`${contractsDirectory}${sep}`),
        `${decision.id}: contract schema must stay under packages/contracts: ${target}`,
      );
      await stat(schemaPath);
      const schema = JSON.parse(await read(schemaPath));
      assert.equal(schema.$schema, draft202012, `${decision.id}: contract must use Draft 2020-12: ${target}`);
      assert.equal(typeof schema.$id, "string", `${decision.id}: contract schema needs an $id: ${target}`);
      const previousPath = validatedContractSchemas.get(schema.$id);
      assert(
        previousPath === undefined || previousPath === schemaPath,
        `${decision.id}: duplicate contract $id across files: ${schema.$id}`,
      );
      validatedContractSchemas.set(schema.$id, schemaPath);
    }
  }

  if (decision.implementationEvidence !== undefined) {
    assert(Array.isArray(decision.implementationEvidence), `${decision.id}: implementationEvidence must be an array`);
    assert(decision.implementationEvidence.length > 0, `${decision.id}: implementationEvidence must not be empty`);
    assert.equal(
      new Set(decision.implementationEvidence).size,
      decision.implementationEvidence.length,
      `${decision.id}: duplicate implementation evidence path`,
    );
    for (const target of decision.implementationEvidence) {
      assert.equal(typeof target, "string", `${decision.id}: implementation evidence path must be a string`);
      const evidencePath = resolve(adrDirectory, target);
      assert(
        evidencePath === root || evidencePath.startsWith(`${root}${sep}`),
        `${decision.id}: implementation evidence must stay inside repository: ${target}`,
      );
      await stat(evidencePath);
      const markdown = adrMarkdownByNumber.get(decision.canonicalAdr);
      assert(
        markdown?.includes(`](${target})`),
        `${decision.id}: canonical ADR must link implementation evidence: ${target}`,
      );
    }
  }
}

const sprintOneAdrs = new Set();
for (const [decisionId, requirement] of sprintOneDecisionRequirements) {
  const decision = coverage.decisions.find((candidate) => candidate.id === decisionId);
  assert(decision, `missing Sprint 1 canonical decision: ${decisionId}`);
  assert.equal(
    decision.canonicalAdr,
    requirement.canonicalAdr,
    `${decisionId}: expected canonical ADR ${requirement.canonicalAdr}`,
  );
  assert(!sprintOneAdrs.has(decision.canonicalAdr), `${decisionId}: Sprint 1 decisions must use independent ADRs`);
  sprintOneAdrs.add(decision.canonicalAdr);
  assert.equal(states.get(decision.canonicalAdr), "Accepted", `${decisionId}: implemented Sprint 1 ADR must be Accepted`);
  assert(
    (decision.implementationEvidence ?? []).length >= 4,
    `${decisionId}: Accepted Sprint 1 ADR needs at least four implementation evidence links`,
  );

  const markdown = adrMarkdownByNumber.get(decision.canonicalAdr);
  assert(markdown, `${decisionId}: canonical ADR body is missing`);
  for (const marker of requirement.markers) {
    assert(markdown.includes(marker), `${decisionId}: canonical ADR must state invariant marker: ${marker}`);
  }

  const schemaNames = (decision.contractSchemas ?? []).map((target) => target.split("/").at(-1));
  assert.deepEqual(
    [...schemaNames].sort(),
    [...requirement.schemas].sort(),
    `${decisionId}: canonical contract schema coverage differs`,
  );
}
assert.deepEqual(
  [...sprintOneAdrs].sort(),
  ["0013", "0014"],
  "Sprint 1 canonical coverage must resolve to exactly ADR-0013 and ADR-0014",
);

console.log(
  `Validated ${adrFiles.length} ADRs, ${coverage.decisions.length} canonical decisions, ` +
    `${[...states.values()].filter((state) => state === "Accepted").length} Accepted, ` +
    `${[...states.values()].filter((state) => state === "Proposed").length} Proposed, ` +
    `${[...states.values()].filter((state) => state === "Superseded").length} Superseded, ` +
    `${validatedContractSchemas.size} linked contract schemas.`,
);
