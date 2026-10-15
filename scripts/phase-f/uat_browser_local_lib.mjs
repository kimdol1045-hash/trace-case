/* global Buffer */
import { createHash } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { chmod, lstat, open, readdir, readFile } from "node:fs/promises";
import { basename, relative, resolve, sep } from "node:path";

export const REPOSITORY_ROOT = resolve(import.meta.dirname, "../..");
export const EVIDENCE_ROOT = resolve(REPOSITORY_ROOT, "docs/phase-f/evidence");
export const MANIFEST_PATH = resolve(REPOSITORY_ROOT, "deploy/operations/phase-f-uat-manifest.json");
export const COMPOSE_PATH = resolve(REPOSITORY_ROOT, "deploy/operations/compose.wave10.yml");
export const MANIFEST_VALIDATOR_PATH = resolve(REPOSITORY_ROOT, "scripts/phase-f/uat_evidence.py");
export const TASK_ID = "PF-SF1-T11";
export const WAVE = "wave10";
export const MANIFEST_ID = "pf-sf1-t11-uat-v1";
export const RUN_ID_PATTERN = /^[a-z0-9]{8}$/;
export const PORT_KEYS = ["postgres", "minio", "clamav", "api", "web"];
export const WORKFLOW_IDS = [
  "dashboard", "test-library", "drag-move", "create-cancel-save", "cycles",
  "per-step-execution", "issues-attachments", "deleted-permanent", "reports",
  "slack-work-items", "knowledge", "integrations", "generation-failure-safe",
  "quality-review-safe", "mobile-390",
];
export const SAFE_ROUTE_ABORT_FETCH_PATH_CLASSES = Object.freeze([
  "/api/cycles",
  "/api/cycles/:cycleId/items",
  "/api/dashboard/overview",
  "/api/generation-failures",
  "/api/generation-quality/review-candidates",
  "/api/integrations/object-storage/observations",
  "/api/integrations/object-storage/status",
  "/api/integrations/slack/allowlist",
  "/api/integrations/slack/status",
  "/api/issues",
  "/api/knowledge/nodes",
  "/api/reports/cycles",
  "/api/slack-events",
  "/api/slack-jobs",
  "/api/slack-work-items",
  "/api/slack-work-items/:workItemId",
]);

const SOURCE_ROOTS = [
  "apps/api/src",
  "apps/web/src",
  "apps/web/vite.config.ts",
  "apps/worker/src",
  "backend/alembic.ini",
  "backend/migrations",
  "backend/src",
  "deploy/operations/compose.wave10.yml",
  "deploy/operations/phase-f-uat-manifest.json",
  "package.json",
  "pnpm-lock.yaml",
  "scripts/phase-f/uat_browser_local.mjs",
  "scripts/phase-f/uat_browser_local_lib.mjs",
  "scripts/phase-f/uat_browser_workflows.mjs",
  "scripts/phase-f/uat_evidence.py",
  "scripts/phase-f/uat_quality_review_seed.py",
];

export class SafetyError extends Error {
  constructor(message) {
    super(message);
    this.name = "SafetyError";
  }
}

export function validateRunId(value) {
  if (typeof value !== "string" || !RUN_ID_PATTERN.test(value)) {
    throw new SafetyError("run ID must contain exactly eight lowercase letters or digits");
  }
  return value;
}

export function validatePort(value, name) {
  const port = Number(value);
  if (!Number.isInteger(port) || port < 10_240 || port > 60_999) {
    throw new SafetyError(`${name} port must be an integer from 10240 through 60999`);
  }
  return port;
}

export function expectedConfirmation(runId) {
  return `${TASK_ID}-${WAVE}:${validateRunId(runId)}:LOCAL-ISOLATED`;
}

export function buildPlan({ runId, ports, date = "20260907" }) {
  validateRunId(runId);
  if (!/^20\d{6}$/.test(date)) throw new SafetyError("date must use YYYYMMDD form");
  const normalizedPorts = Object.fromEntries(
    PORT_KEYS.map((name) => [name, validatePort(ports[name], name)]),
  );
  if (new Set(Object.values(normalizedPorts)).size !== PORT_KEYS.length) {
    throw new SafetyError("every Wave 10 loopback service must have a distinct port");
  }
  const prefix = `pf-sf1-t11-wave10-${runId}`;
  const resources = {
    project: prefix,
    database: `${prefix}-db`,
    databaseComment: `${prefix}:disposable-empty-browser-uat`,
    databaseUser: `${prefix}-owner`,
    bucket: `${prefix}-attachments`,
    containers: {
      postgres: `${prefix}-postgres`,
      minio: `${prefix}-minio`,
      clamav: `${prefix}-clamav`,
      mc: `${prefix}-mc`,
    },
    volumes: {
      postgres: `${prefix}-postgres-data`,
      objectStorage: `${prefix}-object-data`,
    },
    network: `${prefix}-network`,
    tempDirectory: `/tmp/${prefix}`,
    browserProfile: `/tmp/${prefix}/chrome-profile`,
  };
  const outputs = {
    rehearsal: `docs/phase-f/evidence/${prefix}-local-physical-browser-${date}.json`,
    cleanup: `docs/phase-f/evidence/${prefix}-cleanup-${date}.json`,
    desktopScreenshot: `docs/phase-f/evidence/${prefix}-desktop-${date}.png`,
    mobileScreenshot: `docs/phase-f/evidence/${prefix}-mobile-390-${date}.png`,
  };
  return {
    schemaVersion: 1,
    taskId: TASK_ID,
    wave: WAVE,
    runId,
    confirmation: expectedConfirmation(runId),
    ports: normalizedPorts,
    resources,
    outputs,
    origins: {
      api: loopbackOriginClassification(normalizedPorts.api),
      web: loopbackOriginClassification(normalizedPorts.web),
      objectStorage: loopbackOriginClassification(normalizedPorts.minio),
    },
    routes: [
      "/", "/test-cases", "/test-cases/new", "/work-items/:workItemKey",
      "/test-cases/:caseKey", "/cycles", "/cycles/new", "/cycles/:cycleId",
      "/cycles/:cycleId/execute", "/issues", "/issues/new", "/issues/:issueKey",
      "/reports", "/slack-work-items", "/slack-work-items/:workItemId", "/generation/failures",
      "/generation/quality-review", "/knowledge", "/integrations",
    ],
    workflows: [...WORKFLOW_IDS],
    processes: ["postgres", "minio", "clamav", "minio-init", "api", "web", "worker", "system-installed-chrome"],
  };
}

export function loopbackOriginClassification(port) {
  return { scheme: "http", hostClass: "IPV4_LOOPBACK", port: validatePort(port, "origin") };
}

export function localOrigin(port) {
  return `http://127.0.0.1:${validatePort(port, "origin")}`;
}

export function assertOutputPath(path, expectedBasename) {
  const absolute = resolve(path);
  const rootPrefix = `${EVIDENCE_ROOT}${sep}`;
  if (!absolute.startsWith(rootPrefix) || basename(absolute) !== expectedBasename) {
    throw new SafetyError("Wave 10 output must use its exact derived name under the evidence directory");
  }
  return absolute;
}

export function canonicalStringify(value) {
  return JSON.stringify(sortValue(value));
}

function sortValue(value) {
  if (Array.isArray(value)) return value.map(sortValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortValue(value[key])]));
  }
  return value;
}

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function sealEvidence(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || "seal" in value) {
    throw new SafetyError("evidence must be an unsealed object");
  }
  return {
    ...value,
    seal: {
      algorithm: "SHA-256",
      canonicalSha256: sha256(canonicalStringify(value)),
    },
  };
}

export function verifyEvidenceSeal(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new SafetyError("invalid evidence object");
  const { seal, ...payload } = value;
  if (
    !seal
    || seal.algorithm !== "SHA-256"
    || !/^[a-f0-9]{64}$/.test(seal.canonicalSha256 ?? "")
    || seal.canonicalSha256 !== sha256(canonicalStringify(payload))
  ) {
    throw new SafetyError("evidence canonical seal mismatch");
  }
  assertEvidenceSafe(value);
  return seal.canonicalSha256;
}

export function assertEvidenceSafe(value) {
  const serialized = canonicalStringify(value);
  if (/postgres(?:ql)?(?:\+[^:]*)?:\/\//i.test(serialized)) {
    throw new SafetyError("database URLs are forbidden in Wave 10 evidence");
  }
  if (/https?:\/\//i.test(serialized)) {
    throw new SafetyError("raw URLs are forbidden; store safe loopback-origin classification only");
  }
  if (/"(?:password|secret|token|authorization|cookie|set-cookie|permalink|databaseUrl)"\s*:/i.test(serialized)) {
    throw new SafetyError("credential-like or raw-record fields are forbidden in Wave 10 evidence");
  }
  return value;
}

export async function writeExclusivePrivateJson(path, value) {
  assertEvidenceSafe(value);
  const body = `${canonicalStringify(value)}\n`;
  const handle = await open(path, fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_WRONLY, 0o600);
  try {
    await handle.writeFile(body, "utf8");
    await handle.sync();
    await handle.chmod(0o600);
  } finally {
    await handle.close();
  }
  await assertPrivateRegularFile(path);
  return { bytes: Buffer.byteLength(body), fileSha256: sha256(body) };
}

export async function assertPrivateRegularFile(path) {
  const stat = await lstat(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || (stat.mode & 0o777) !== 0o600) {
    throw new SafetyError("evidence must be a regular non-symlink single-link mode-0600 file");
  }
  return stat;
}

export async function makePrivateFile(path) {
  const stat = await lstat(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) {
    throw new SafetyError("browser artifacts must be regular non-symlink single-link files");
  }
  await chmod(path, 0o600);
  return assertPrivateRegularFile(path);
}

export async function hashFile(path) {
  return sha256(await readFile(path));
}

export async function inventoryEvidence({ exclude = [] } = {}) {
  const excluded = new Set(exclude.map((path) => resolve(path)));
  const entries = [];
  for (const name of (await readdir(EVIDENCE_ROOT)).sort()) {
    const path = resolve(EVIDENCE_ROOT, name);
    if (excluded.has(path)) continue;
    const stat = await lstat(path);
    if (!stat.isFile() || stat.isSymbolicLink()) continue;
    entries.push({ name, bytes: stat.size, sha256: await hashFile(path) });
  }
  return { count: entries.length, digest: sha256(canonicalStringify(entries)), entries };
}

export async function sourceInventory() {
  const files = [];
  for (const source of SOURCE_ROOTS) {
    const absolute = resolve(REPOSITORY_ROOT, source);
    const stat = await lstat(absolute);
    if (stat.isDirectory()) await collectFiles(absolute, files);
    else if (stat.isFile()) files.push(absolute);
  }
  const entries = [];
  for (const path of [...new Set(files)].sort()) {
    const name = relative(REPOSITORY_ROOT, path);
    if (name.split(sep).some((part) => part.startsWith(".env")) || name.includes("__pycache__")) continue;
    entries.push({ path: name, sha256: await hashFile(path) });
  }
  return { count: entries.length, digest: sha256(canonicalStringify(entries)) };
}

async function collectFiles(directory, files) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isSymbolicLink()) throw new SafetyError(`source inventory refuses symlink: ${relative(REPOSITORY_ROOT, path)}`);
    if (entry.isDirectory()) await collectFiles(path, files);
    else if (entry.isFile()) files.push(path);
  }
}

export async function loadCanonicalManifest() {
  const text = await readFile(MANIFEST_PATH, "utf8");
  let manifest;
  try { manifest = JSON.parse(text); } catch { throw new SafetyError("canonical PF-SF1-T11 manifest must be valid JSON"); }
  assertExactKeys(manifest, [
    "automatedChecks", "manifestId", "manualChecks", "originPolicy", "schemaVersion", "taskId", "workflows",
  ], "manifest");
  if (manifest.schemaVersion !== 1 || manifest.manifestId !== MANIFEST_ID || manifest.taskId !== TASK_ID) {
    throw new SafetyError("canonical PF-SF1-T11 manifest identity mismatch");
  }
  const workflowIds = manifest.workflows.map((workflow, index) => {
    assertExactKeys(workflow, ["acceptance", "id", "title"], `manifest.workflows[${index}]`);
    assertString(workflow.id, `manifest.workflows[${index}].id`);
    assertString(workflow.title, `manifest.workflows[${index}].title`);
    assertStringArray(workflow.acceptance, `manifest.workflows[${index}].acceptance`, { minimum: 1 });
    return workflow.id;
  });
  if (canonicalStringify(workflowIds) !== canonicalStringify(WORKFLOW_IDS)) {
    throw new SafetyError("canonical PF-SF1-T11 workflow IDs/order mismatch");
  }
  const known = new Set(WORKFLOW_IDS);
  const validateChecks = (checks, kind) => {
    assertArray(checks, `manifest.${kind}Checks`);
    const ids = [];
    for (const [index, check] of checks.entries()) {
      const context = `manifest.${kind}Checks[${index}]`;
      assertExactKeys(check, kind === "automated"
        ? ["expect", "id", "request", "title", "workflowIds"]
        : ["defaultStatus", "id", "ownerRole", "requiredArtifactKinds", "steps", "title", "workflowIds"], context);
      assertString(check.id, `${context}.id`);
      assertString(check.title, `${context}.title`);
      assertStringArray(check.workflowIds, `${context}.workflowIds`, { minimum: 1 });
      if (check.workflowIds.some((id) => !known.has(id))) throw new SafetyError(`${context} references an unknown workflow`);
      ids.push(check.id);
    }
    if (new Set(ids).size !== ids.length) throw new SafetyError(`manifest ${kind} check IDs must be unique`);
    return checks.map((check) => ({ checkId: check.id, workflowIds: [...check.workflowIds] }));
  };
  const automatedCheckMappings = validateChecks(manifest.automatedChecks, "automated");
  const manualCheckMappings = validateChecks(manifest.manualChecks, "manual");
  if (automatedCheckMappings.length !== 33 || manualCheckMappings.length !== 15) {
    throw new SafetyError("canonical PF-SF1-T11 check counts mismatch");
  }
  const canonicalSha256 = sha256(canonicalStringify(manifest));
  const validatorSha256 = await hashFile(MANIFEST_VALIDATOR_PATH);
  const binding = {
    schemaVersion: 1,
    manifestId: MANIFEST_ID,
    canonicalSha256,
    validatorSha256,
    workflowIds,
    automatedCheckMappings,
    manualCheckMappings,
  };
  return { manifest, sha256: canonicalSha256, rawSha256: sha256(text), binding };
}

export function validateManifestBinding(value, expected) {
  assertExactKeys(value, [
    "automatedCheckMappings", "canonicalSha256", "manifestId", "manualCheckMappings",
    "schemaVersion", "validatorSha256", "workflowIds",
  ], "manifest binding");
  if (canonicalStringify(value) !== canonicalStringify(expected)) {
    throw new SafetyError("local evidence manifest binding does not match the canonical manifest/check mapping/validator digest");
  }
  return value;
}

export function validateRehearsalEvidence(value, { plan, manifestBinding, expectedAssertions }) {
  verifyEvidenceSeal(value);
  assertExactKeys(value, [
    "automated", "browser", "databaseInvariants", "evidenceType", "higherGateStatus", "human",
    "manifest", "originPolicy", "preservation", "remainingGates", "resources", "runId", "schemaVersion",
    "screenshotArtifacts", "seal", "source", "status", "taskId", "timing", "wave",
  ], "rehearsal evidence");
  assertIdentity(value, plan, "LOCAL_ISOLATED_PHYSICAL_BROWSER_AUTOMATED_UAT");
  if (value.status !== "PASS" || value.higherGateStatus !== "NOT_RUN") throw new SafetyError("rehearsal status boundary is invalid");
  validateManifestBinding(value.manifest, manifestBinding);
  assertExactKeys(value.originPolicy, ["exactApiOrigin", "exactBrowserOrigin", "exactObjectStorageOrigin", "externalRequests", "redirects"], "rehearsal originPolicy");
  for (const [key, expected] of [["exactApiOrigin", plan.origins.api], ["exactBrowserOrigin", plan.origins.web], ["exactObjectStorageOrigin", plan.origins.objectStorage]]) {
    if (canonicalStringify(value.originPolicy[key]) !== canonicalStringify(expected)) throw new SafetyError(`rehearsal originPolicy.${key} mismatch`);
  }
  if (value.originPolicy.redirects !== "DENY" || value.originPolicy.externalRequests !== 0) throw new SafetyError("rehearsal external-origin policy mismatch");
  assertExactKeys(value.browser, ["engine", "executableSha256", "headless", "networkMocking", "serviceWorkers", "viewports"], "rehearsal browser");
  assertDigest(value.browser.executableSha256, "rehearsal browser executableSha256");
  if (value.browser.engine !== "system-installed Google Chrome" || value.browser.headless !== true || value.browser.networkMocking !== false || value.browser.serviceWorkers !== "blocked" || canonicalStringify(value.browser.viewports) !== canonicalStringify(["1440x1000", "390x844"])) {
    throw new SafetyError("rehearsal browser contract mismatch");
  }
  assertExactKeys(value.automated, ["assertionCount", "findings", "mutationSummary", "notRunCount", "passCount", "status", "workflowResults"], "rehearsal automated");
  if (value.automated.status !== "PASS" || value.automated.notRunCount !== 0 || value.automated.passCount !== WORKFLOW_IDS.length) throw new SafetyError("rehearsal automated totals mismatch");
  assertArray(value.automated.workflowResults, "rehearsal automated workflowResults");
  if (value.automated.workflowResults.length !== WORKFLOW_IDS.length) throw new SafetyError("rehearsal must contain exactly one automated result per workflow");
  let assertionCount = 0;
  for (const [index, workflowId] of WORKFLOW_IDS.entries()) {
    const result = value.automated.workflowResults[index];
    assertExactKeys(result, ["assertions", "status", "workflowId"], `rehearsal automated workflowResults[${index}]`);
    const expected = expectedAssertions[workflowId];
    if (result.workflowId !== workflowId || result.status !== "PASS" || canonicalStringify(result.assertions) !== canonicalStringify(expected)) {
      throw new SafetyError(`rehearsal workflow assertion receipt mismatch: ${workflowId}`);
    }
    assertionCount += result.assertions.length;
  }
  if (value.automated.assertionCount !== assertionCount) throw new SafetyError("rehearsal assertionCount mismatch");
  validateFindings(value.automated.findings, plan);
  validateIntegerObject(value.automated.mutationSummary, [
    "attachmentFilesSelectedInOneEvent", "attachmentsDeleted", "attachmentsDownloaded", "casesCreated",
    "cyclesCreated", "domainsCreated", "issuesCreatedFromExecution", "safeGenerationFailuresSeeded", "safeQualityReviewCandidatesSeeded",
    "workItemsCreated",
  ], "rehearsal mutationSummary");
  if (canonicalStringify(value.automated.mutationSummary) !== canonicalStringify({
    attachmentFilesSelectedInOneEvent: 2,
    attachmentsDeleted: 2,
    attachmentsDownloaded: 2,
    casesCreated: 2,
    cyclesCreated: 1,
    domainsCreated: 1,
    issuesCreatedFromExecution: 1,
    safeGenerationFailuresSeeded: 1,
    safeQualityReviewCandidatesSeeded: 1,
    workItemsCreated: 2,
  })) throw new SafetyError("rehearsal mutationSummary value mismatch");
  assertExactKeys(value.human, ["notRunCount", "passCount", "status", "workflowResults"], "rehearsal human");
  if (value.human.status !== "NOT_RUN" || value.human.passCount !== 0 || value.human.notRunCount !== WORKFLOW_IDS.length) throw new SafetyError("rehearsal human boundary mismatch");
  assertArray(value.human.workflowResults, "rehearsal human workflowResults");
  if (value.human.workflowResults.length !== WORKFLOW_IDS.length) throw new SafetyError("rehearsal human result count mismatch");
  for (const [index, workflowId] of WORKFLOW_IDS.entries()) {
    const result = value.human.workflowResults[index];
    assertExactKeys(result, ["reasonCode", "status", "workflowId"], `rehearsal human workflowResults[${index}]`);
    if (result.workflowId !== workflowId || result.status !== "NOT_RUN" || result.reasonCode !== "ACCOUNTABLE_OWNER_OR_EXTERNAL_JUDGMENT_REQUIRED") throw new SafetyError("rehearsal human result mismatch");
  }
  validateIntegerObject(value.databaseInvariants, [
    "activeAttachments", "activeCases", "activeIssues", "auditEvents", "caseVersions", "cycleItems", "cycles",
    "deletedAttachments", "deletedCases", "deletedIssues", "executionAttempts", "folders", "generationFailureEvents",
    "generationFailures", "idempotencyProofs", "idempotencyReceipts", "publicTables", "qualityReviewCandidates",
    "stepResults", "workItems", "workspaces",
  ], "rehearsal databaseInvariants");
  const counts = value.databaseInvariants;
  const countAssertions = [
    [counts.activeAttachments === 0, "activeAttachments"], [counts.activeCases === 7, "activeCases"],
    [counts.activeIssues === 1, "activeIssues"], [counts.auditEvents >= 1, "auditEvents"],
    [counts.caseVersions >= 9, "caseVersions"], [counts.cycleItems === 1, "cycleItems"],
    [counts.cycles === 1, "cycles"], [counts.deletedAttachments === 2, "deletedAttachments"],
    [counts.deletedCases === 0, "deletedCases"], [counts.deletedIssues === 0, "deletedIssues"],
    [counts.executionAttempts === 3, "executionAttempts"], [counts.folders === 2, "folders"],
    [counts.generationFailureEvents >= 1, "generationFailureEvents"], [counts.generationFailures === 1, "generationFailures"],
    [counts.idempotencyProofs + counts.idempotencyReceipts >= 1, "idempotency ledger"],
    [counts.publicTables >= 70, "publicTables"], [counts.qualityReviewCandidates === 1, "qualityReviewCandidates"],
    [counts.stepResults === 9, "stepResults"], [counts.workItems === 3, "workItems"], [counts.workspaces === 1, "workspaces"],
  ];
  const invalidCounts = countAssertions.filter(([valid]) => !valid).map(([, name]) => name);
  if (invalidCounts.length) throw new SafetyError(`rehearsal database invariant value mismatch: ${invalidCounts.join(", ")}`);
  validateDigestCount(value.source, "rehearsal source");
  if (value.source.count < 1) throw new SafetyError("rehearsal source inventory must not be empty");
  validatePreservation(value.preservation, "rehearsal preservation");
  validateResources(value.resources, plan);
  assertArray(value.screenshotArtifacts, "rehearsal screenshotArtifacts");
  if (value.screenshotArtifacts.length !== 2) throw new SafetyError("rehearsal screenshot artifact count mismatch");
  for (const [index, artifact] of value.screenshotArtifacts.entries()) {
    assertExactKeys(artifact, ["kind", "name", "sha256"], `rehearsal screenshotArtifacts[${index}]`);
    assertDigest(artifact.sha256, `rehearsal screenshotArtifacts[${index}].sha256`);
  }
  if (value.screenshotArtifacts[0].kind !== "DESKTOP" || value.screenshotArtifacts[1].kind !== "MOBILE_390") throw new SafetyError("rehearsal screenshot artifact kinds/order mismatch");
  if (value.screenshotArtifacts[0].name !== basename(plan.outputs.desktopScreenshot) || value.screenshotArtifacts[1].name !== basename(plan.outputs.mobileScreenshot)) throw new SafetyError("rehearsal screenshot artifact names mismatch");
  assertExactKeys(value.timing, ["durationSeconds"], "rehearsal timing");
  if (typeof value.timing.durationSeconds !== "number" || !Number.isFinite(value.timing.durationSeconds) || value.timing.durationSeconds <= 0) throw new SafetyError("rehearsal duration must be positive");
  validateRemainingGates(value.remainingGates);
  return value;
}

export function validateCleanupEvidence(value, { plan, rehearsalReceipt }) {
  verifyEvidenceSeal(value);
  assertExactKeys(value, [
    "absence", "evidenceType", "externalRequests", "preservation", "rehearsal", "remainingGates", "removed",
    "runId", "schemaVersion", "seal", "status", "taskId", "wave",
  ], "cleanup evidence");
  assertIdentity(value, plan, "LOCAL_ISOLATED_PHYSICAL_BROWSER_CLEANUP");
  if (value.status !== "PASS" || value.externalRequests !== 0) throw new SafetyError("cleanup status/external request boundary mismatch");
  assertExactKeys(value.rehearsal, ["bytes", "canonicalSha256", "fileSha256", "name"], "cleanup rehearsal receipt");
  if (canonicalStringify(value.rehearsal) !== canonicalStringify({ name: plan.outputs.rehearsal.split("/").at(-1), ...rehearsalReceipt })) throw new SafetyError("cleanup rehearsal receipt mismatch");
  for (const key of ["canonicalSha256", "fileSha256"]) assertDigest(value.rehearsal[key], `cleanup rehearsal.${key}`);
  if (!Number.isInteger(value.rehearsal.bytes) || value.rehearsal.bytes < 1) throw new SafetyError("cleanup rehearsal.bytes must be positive");
  validateIntegerObject(value.removed, ["applicationProcesses", "browserProfiles", "buckets", "containers", "databases", "networks", "tempDirectories", "volumes"], "cleanup removed");
  if (canonicalStringify(value.removed) !== canonicalStringify({ applicationProcesses: 3, browserProfiles: 1, buckets: 1, containers: 4, databases: 1, networks: 1, tempDirectories: 1, volumes: 2 })) throw new SafetyError("cleanup removed counts mismatch");
  assertExactKeys(value.absence, ["exactBucket", "exactContainers", "exactDatabase", "exactNetwork", "exactVolumes", "loopbackPortsFree", "matchingProcessCount", "ownedProcesses", "tempAndBrowserProfile"], "cleanup absence");
  for (const [key, item] of Object.entries(value.absence)) {
    if (key === "matchingProcessCount" ? item !== 0 : item !== true) throw new SafetyError(`cleanup absence.${key} is not proved`);
  }
  validatePreservation(value.preservation, "cleanup preservation");
  validateRemainingGates(value.remainingGates);
  return value;
}

function validateFindings(value, plan) {
  assertExactKeys(value, ["consoleErrors", "externalRequestAttempts", "failedRequests", "httpFailures", "pageErrors", "unresolvedFailedRequests", "visitedMobileSurfaces", "visitedRouteKinds"], "rehearsal findings");
  for (const key of ["consoleErrors", "externalRequestAttempts", "httpFailures", "pageErrors", "unresolvedFailedRequests"]) {
    assertArray(value[key], `rehearsal findings.${key}`);
    if (value[key].length !== 0) throw new SafetyError(`rehearsal findings.${key} must be empty for PASS`);
  }
  assertArray(value.failedRequests, "rehearsal findings.failedRequests");
  const failedRequestKeys = new Set();
  const expectedPortClass = `IPV4_LOOPBACK:${plan.ports.web}`;
  const allowedFetchPaths = new Set(SAFE_ROUTE_ABORT_FETCH_PATH_CLASSES);
  for (const [index, item] of value.failedRequests.entries()) {
    const context = `rehearsal findings.failedRequests[${index}]`;
    assertExactKeys(item, ["class", "count", "disposition", "errorCode", "method", "pathClass", "phase", "resourceType"], context);
    if (item.class !== expectedPortClass || !["CLIENT_ABORT_CONTROLLER_ROUTE_TEARDOWN", "HARNESS_CONTEXT_TEARDOWN"].includes(item.disposition) || item.errorCode !== "NET_ERR_ABORTED") throw new SafetyError(`${context} is not an adjudicated exact-browser-origin abort`);
    if (!Number.isInteger(item.count) || item.count < 1 || typeof item.method !== "string" || !/^[A-Z]+$/.test(item.method) || typeof item.pathClass !== "string" || !item.pathClass.startsWith("/") || typeof item.resourceType !== "string" || !item.resourceType) throw new SafetyError(`${context} shape mismatch`);
    const failedRequestKey = canonicalStringify(Object.fromEntries(Object.entries(item).filter(([key]) => key !== "count")));
    if (failedRequestKeys.has(failedRequestKey)) throw new SafetyError(`${context} duplicates an earlier failed-request class`);
    failedRequestKeys.add(failedRequestKey);
    const expectedDisposition = item.phase === "WORKFLOW"
      ? "CLIENT_ABORT_CONTROLLER_ROUTE_TEARDOWN"
      : item.phase === "HARNESS_TEARDOWN"
        ? "HARNESS_CONTEXT_TEARDOWN"
        : null;
    if (
      item.disposition !== expectedDisposition
      || item.method !== "GET"
      || item.resourceType !== "fetch"
      || !allowedFetchPaths.has(item.pathClass)
    ) throw new SafetyError(`${context} disposition does not match its exact phase/request shape`);
  }
  assertStringArray(value.visitedRouteKinds, "rehearsal findings.visitedRouteKinds", { minimum: 1 });
  assertStringArray(value.visitedMobileSurfaces, "rehearsal findings.visitedMobileSurfaces", { minimum: 1 });
  const expectedRoutes = [...new Set(plan.routes)].sort();
  if (canonicalStringify(value.visitedRouteKinds) !== canonicalStringify(expectedRoutes)) throw new SafetyError("rehearsal route coverage is incomplete or unexpected");
  const expectedMobileSurfaces = [...plan.routes, "detail:generation-failure", "detail:quality-review-candidate", "modal:execution-issue-create"];
  if (canonicalStringify([...value.visitedMobileSurfaces].sort()) !== canonicalStringify(expectedMobileSurfaces.sort())) throw new SafetyError("rehearsal mobile route/detail/form/modal coverage is incomplete or unexpected");
}

function validateResources(value, plan) {
  assertExactKeys(value, ["bucketCount", "containers", "databaseCount", "loopbackPorts", "namespace", "networkCount", "postgresMajor", "processKinds", "volumes"], "rehearsal resources");
  if (value.namespace !== plan.resources.project || value.postgresMajor !== 16 || value.databaseCount !== 1 || value.bucketCount !== 1 || value.networkCount !== 1 || canonicalStringify(value.loopbackPorts) !== canonicalStringify(plan.ports)) throw new SafetyError("rehearsal resource identity mismatch");
  assertStringArray(value.containers, "rehearsal resources.containers", { minimum: 1 });
  assertStringArray(value.volumes, "rehearsal resources.volumes", { minimum: 1 });
  assertStringArray(value.processKinds, "rehearsal resources.processKinds", { minimum: 1 });
  if (canonicalStringify(value.containers) !== canonicalStringify(Object.keys(plan.resources.containers).sort())
    || canonicalStringify(value.volumes) !== canonicalStringify(Object.keys(plan.resources.volumes).sort())
    || canonicalStringify(value.processKinds) !== canonicalStringify(["api", "web", "worker", "system-installed-chrome"])) {
    throw new SafetyError("rehearsal exact disposable resource list mismatch");
  }
}

function validatePreservation(value, context) {
  assertExactKeys(value, ["currentTcWriteCounters", "preExistingDatabases", "preExistingDocker", "preExistingEvidence"], context);
  validateAvailableDigest(value.currentTcWriteCounters, `${context}.currentTcWriteCounters`, false);
  validateAvailableDigest(value.preExistingDatabases, `${context}.preExistingDatabases`, true);
  validateDigestCount(value.preExistingEvidence, `${context}.preExistingEvidence`);
  assertExactKeys(value.preExistingDocker, ["categories", "digest"], `${context}.preExistingDocker`);
  assertDigest(value.preExistingDocker.digest, `${context}.preExistingDocker.digest`);
  assertExactKeys(value.preExistingDocker.categories, ["containers", "images", "networks", "volumes"], `${context}.preExistingDocker.categories`);
  for (const [key, item] of Object.entries(value.preExistingDocker.categories)) validateDigestCount(item, `${context}.preExistingDocker.categories.${key}`);
}

function validateAvailableDigest(value, context, withCount) {
  assertExactKeys(value, withCount ? ["available", "count", "digest"] : ["available", "digest"], context);
  if (typeof value.available !== "boolean") throw new SafetyError(`${context}.available must be boolean`);
  if (withCount) assertNonnegativeInteger(value.count, `${context}.count`);
  assertDigest(value.digest, `${context}.digest`);
}

function validateDigestCount(value, context) {
  assertExactKeys(value, ["count", "digest"], context);
  assertNonnegativeInteger(value.count, `${context}.count`);
  assertDigest(value.digest, `${context}.digest`);
}

function validateIntegerObject(value, keys, context) {
  assertExactKeys(value, keys, context);
  for (const key of keys) assertNonnegativeInteger(value[key], `${context}.${key}`);
}

function validateRemainingGates(value) {
  const gates = ["PF-SF1-T11", "PHASE_F", "DEPLOYED_EXACT_HTTPS_ORIGIN", "HUMAN_QA_OWNER_CHECKS", "EXTERNAL_INTEGRATIONS", "PRODUCTION", "RELEASE"];
  assertArray(value, "remainingGates");
  if (value.length !== gates.length) throw new SafetyError("remainingGates length mismatch");
  for (const [index, gate] of gates.entries()) {
    assertExactKeys(value[index], ["gate", "status"], `remainingGates[${index}]`);
    if (value[index].gate !== gate || value[index].status !== "NOT_RUN") throw new SafetyError(`remainingGates[${index}] mismatch`);
  }
}

function assertIdentity(value, plan, evidenceType) {
  if (value.schemaVersion !== 1 || value.evidenceType !== evidenceType || value.taskId !== TASK_ID || value.wave !== WAVE || value.runId !== plan.runId) throw new SafetyError("local evidence identity mismatch");
}

function assertExactKeys(value, keys, context) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new SafetyError(`${context} must be an object`);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (canonicalStringify(actual) !== canonicalStringify(expected)) throw new SafetyError(`${context} has omitted or extra fields`);
}

function assertArray(value, context) {
  if (!Array.isArray(value)) throw new SafetyError(`${context} must be an array`);
}

function assertString(value, context) {
  if (typeof value !== "string" || !value.trim()) throw new SafetyError(`${context} must be a non-blank string`);
}

function assertStringArray(value, context, { minimum = 0 } = {}) {
  assertArray(value, context);
  if (value.length < minimum || value.some((item) => typeof item !== "string" || !item)) throw new SafetyError(`${context} must contain strings`);
  if (new Set(value).size !== value.length) throw new SafetyError(`${context} must not contain duplicates`);
}

function assertNonnegativeInteger(value, context) {
  if (!Number.isInteger(value) || value < 0) throw new SafetyError(`${context} must be a nonnegative integer`);
}

function assertDigest(value, context) {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) throw new SafetyError(`${context} must be a lowercase SHA-256 digest`);
}
