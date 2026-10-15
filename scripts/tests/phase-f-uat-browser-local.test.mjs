/* global process */
import assert from "node:assert/strict";
import { chmod, link, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";

import {
  SafetyError,
  assertEvidenceSafe,
  assertPrivateRegularFile,
  buildPlan,
  canonicalStringify,
  expectedConfirmation,
  loadCanonicalManifest,
  sealEvidence,
  validateCleanupEvidence,
  validateManifestBinding,
  validateRehearsalEvidence,
  verifyEvidenceSeal,
  writeExclusivePrivateJson,
} from "../phase-f/uat_browser_local_lib.mjs";
import {
  EXPECTED_WORKFLOW_ASSERTIONS,
  classifyFailedRequestDisposition,
  finalizeWorkflowAssertions,
} from "../phase-f/uat_browser_workflows.mjs";

const ports = { postgres: 31001, minio: 31002, clamav: 31003, api: 31004, web: 31005 };
const digest = "1".repeat(64);

function remainingGates() {
  return ["PF-SF1-T11", "PHASE_F", "DEPLOYED_EXACT_HTTPS_ORIGIN", "HUMAN_QA_OWNER_CHECKS", "EXTERNAL_INTEGRATIONS", "PRODUCTION", "RELEASE"]
    .map((gate) => ({ gate, status: "NOT_RUN" }));
}

function rehearsalFixture(plan, manifest) {
  const workflowIds = Object.keys(EXPECTED_WORKFLOW_ASSERTIONS);
  return sealEvidence({
    schemaVersion: 1,
    evidenceType: "LOCAL_ISOLATED_PHYSICAL_BROWSER_AUTOMATED_UAT",
    taskId: plan.taskId,
    wave: plan.wave,
    runId: plan.runId,
    status: "PASS",
    higherGateStatus: "NOT_RUN",
    manifest,
    originPolicy: {
      exactApiOrigin: plan.origins.api,
      exactBrowserOrigin: plan.origins.web,
      exactObjectStorageOrigin: plan.origins.objectStorage,
      externalRequests: 0,
      redirects: "DENY",
    },
    resources: {
      bucketCount: 1,
      containers: Object.keys(plan.resources.containers).sort(),
      databaseCount: 1,
      loopbackPorts: plan.ports,
      namespace: plan.resources.project,
      networkCount: 1,
      postgresMajor: 16,
      processKinds: ["api", "web", "worker", "system-installed-chrome"],
      volumes: Object.keys(plan.resources.volumes).sort(),
    },
    source: { count: 1, digest },
    preservation: {
      currentTcWriteCounters: { available: true, digest },
      preExistingDatabases: { available: true, count: 0, digest },
      preExistingDocker: {
        categories: {
          containers: { count: 0, digest },
          images: { count: 0, digest },
          networks: { count: 0, digest },
          volumes: { count: 0, digest },
        },
        digest,
      },
      preExistingEvidence: { count: 0, digest },
    },
    browser: {
      engine: "system-installed Google Chrome",
      executableSha256: digest,
      headless: true,
      networkMocking: false,
      serviceWorkers: "blocked",
      viewports: ["1440x1000", "390x844"],
    },
    automated: {
      assertionCount: Object.values(EXPECTED_WORKFLOW_ASSERTIONS).flat().length,
      findings: {
        consoleErrors: [],
        externalRequestAttempts: [],
        failedRequests: [
          { class: `IPV4_LOOPBACK:${plan.ports.web}`, count: 1, disposition: "CLIENT_ABORT_CONTROLLER_ROUTE_TEARDOWN", errorCode: "NET_ERR_ABORTED", method: "GET", pathClass: "/api/dashboard/overview", phase: "WORKFLOW", resourceType: "fetch" },
          { class: `IPV4_LOOPBACK:${plan.ports.web}`, count: 1, disposition: "HARNESS_CONTEXT_TEARDOWN", errorCode: "NET_ERR_ABORTED", method: "GET", pathClass: "/api/issues", phase: "HARNESS_TEARDOWN", resourceType: "fetch" },
        ],
        httpFailures: [],
        pageErrors: [],
        unresolvedFailedRequests: [],
        visitedMobileSurfaces: [...plan.routes, "detail:generation-failure", "detail:quality-review-candidate", "modal:execution-issue-create"],
        visitedRouteKinds: [...new Set(plan.routes)].sort(),
      },
      mutationSummary: {
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
      },
      notRunCount: 0,
      passCount: workflowIds.length,
      status: "PASS",
      workflowResults: workflowIds.map((workflowId) => ({ assertions: [...EXPECTED_WORKFLOW_ASSERTIONS[workflowId]], status: "PASS", workflowId })),
    },
    human: {
      notRunCount: workflowIds.length,
      passCount: 0,
      status: "NOT_RUN",
      workflowResults: workflowIds.map((workflowId) => ({ reasonCode: "ACCOUNTABLE_OWNER_OR_EXTERNAL_JUDGMENT_REQUIRED", status: "NOT_RUN", workflowId })),
    },
    databaseInvariants: {
      activeAttachments: 0,
      activeCases: 7,
      activeIssues: 1,
      auditEvents: 1,
      caseVersions: 9,
      cycleItems: 1,
      cycles: 1,
      deletedAttachments: 2,
      deletedCases: 0,
      deletedIssues: 0,
      executionAttempts: 3,
      folders: 2,
      generationFailureEvents: 1,
      generationFailures: 1,
      idempotencyProofs: 1,
      idempotencyReceipts: 0,
      publicTables: 70,
      qualityReviewCandidates: 1,
      stepResults: 9,
      workItems: 3,
      workspaces: 1,
    },
    screenshotArtifacts: [
      { kind: "DESKTOP", name: plan.outputs.desktopScreenshot.split("/").at(-1), sha256: digest },
      { kind: "MOBILE_390", name: plan.outputs.mobileScreenshot.split("/").at(-1), sha256: digest },
    ],
    timing: { durationSeconds: 1 },
    remainingGates: remainingGates(),
  });
}

function cleanupFixture(plan, rehearsalReceipt) {
  return sealEvidence({
    schemaVersion: 1,
    evidenceType: "LOCAL_ISOLATED_PHYSICAL_BROWSER_CLEANUP",
    taskId: plan.taskId,
    wave: plan.wave,
    runId: plan.runId,
    status: "PASS",
    rehearsal: { name: plan.outputs.rehearsal.split("/").at(-1), ...rehearsalReceipt },
    removed: { applicationProcesses: 3, browserProfiles: 1, buckets: 1, containers: 4, databases: 1, networks: 1, tempDirectories: 1, volumes: 2 },
    absence: { exactBucket: true, exactContainers: true, exactDatabase: true, exactNetwork: true, exactVolumes: true, loopbackPortsFree: true, matchingProcessCount: 0, ownedProcesses: true, tempAndBrowserProfile: true },
    preservation: {
      currentTcWriteCounters: { available: true, digest },
      preExistingDatabases: { available: true, count: 0, digest },
      preExistingDocker: { categories: { containers: { count: 0, digest }, images: { count: 0, digest }, networks: { count: 0, digest }, volumes: { count: 0, digest } }, digest },
      preExistingEvidence: { count: 0, digest },
    },
    externalRequests: 0,
    remainingGates: remainingGates(),
  });
}

function reseal(evidence, mutate) {
  const { seal: _seal, ...payload } = structuredClone(evidence);
  mutate(payload);
  return sealEvidence(payload);
}

test("Wave 10 plan derives exact disposable identities and 15 workflow coverage", () => {
  const plan = buildPlan({ runId: "ab12cd34", ports });
  assert.equal(plan.confirmation, "PF-SF1-T11-wave10:ab12cd34:LOCAL-ISOLATED");
  assert.equal(plan.resources.database, "pf-sf1-t11-wave10-ab12cd34-db");
  assert.equal(plan.resources.bucket, "pf-sf1-t11-wave10-ab12cd34-attachments");
  assert.equal(plan.workflows.length, 15);
  assert.equal(new Set(plan.workflows).size, 15);
});

test("unsafe run IDs, duplicate ports, and wrong confirmations fail closed", () => {
  assert.throws(() => buildPlan({ runId: "../unsafe", ports }), SafetyError);
  assert.throws(
    () => buildPlan({ runId: "ab12cd34", ports: { ...ports, web: ports.api } }),
    /distinct port/,
  );
  assert.notEqual(expectedConfirmation("ab12cd34"), expectedConfirmation("ab12cd35"));
});

test("canonical seals reject tamper and unsafe evidence values", () => {
  const evidence = sealEvidence({ schemaVersion: 1, origin: { hostClass: "IPV4_LOOPBACK", port: 31005 } });
  assert.equal(verifyEvidenceSeal(evidence), evidence.seal.canonicalSha256);
  evidence.origin.port += 1;
  assert.throws(() => verifyEvidenceSeal(evidence), /seal mismatch/);
  assert.throws(() => assertEvidenceSafe({ endpoint: "http://127.0.0.1:31005" }), /raw URLs/);
  assert.throws(() => assertEvidenceSafe({ password: "generated" }), /credential-like/);
  assert.equal(canonicalStringify({ z: 1, a: { y: 2, x: 3 } }), '{"a":{"x":3,"y":2},"z":1}');
});

test("exclusive evidence is private and refuses overwrite", async (context) => {
  const directory = resolve(tmpdir(), `pf-sf1-t11-wave10-test-${process.pid}-${Date.now()}`);
  context.after(() => rm(directory, { recursive: true, force: true }));
  await mkdir(directory, { mode: 0o700 });
  const output = resolve(directory, "evidence.json");
  const evidence = sealEvidence({ schemaVersion: 1, status: "PASS" });
  await writeExclusivePrivateJson(output, evidence);
  await assertPrivateRegularFile(output);
  await assert.rejects(() => writeExclusivePrivateJson(output, evidence), { code: "EEXIST" });
  assert.equal(JSON.parse(await readFile(output, "utf8")).status, "PASS");
});

test("private evidence verifier rejects symlinks, hardlinks, and broad modes", async (context) => {
  const directory = resolve(tmpdir(), `pf-sf1-t11-wave10-links-${process.pid}-${Date.now()}`);
  context.after(() => rm(directory, { recursive: true, force: true }));
  await mkdir(directory, { mode: 0o700 });
  const original = resolve(directory, "original.json");
  const hard = resolve(directory, "hard.json");
  const symbolic = resolve(directory, "symbolic.json");
  await writeFile(original, "{}", { mode: 0o600, flag: "wx" });
  await link(original, hard);
  assert.rejects(() => assertPrivateRegularFile(original), /single-link/);
  await symlink(original, symbolic);
  assert.rejects(() => assertPrivateRegularFile(symbolic), /regular/);
  const broad = resolve(directory, "broad.json");
  await writeFile(broad, "{}", { mode: 0o600, flag: "wx" });
  await chmod(broad, 0o644);
  assert.rejects(() => assertPrivateRegularFile(broad), /mode-0600/);
});

test("workflow PASS results require every exact named assertion receipt", () => {
  const complete = new Map(Object.entries(EXPECTED_WORKFLOW_ASSERTIONS).map(([workflowId, assertions]) => [workflowId, [...assertions]]));
  const results = finalizeWorkflowAssertions(complete);
  assert.equal(results.length, 15);
  assert(results.every((result) => result.status === "PASS"));

  const omitted = new Map(complete);
  omitted.set("dashboard", [EXPECTED_WORKFLOW_ASSERTIONS.dashboard[0]]);
  assert.throws(() => finalizeWorkflowAssertions(omitted), /missing or unexpected executed assertion/);

  const extra = new Map(complete);
  extra.set("reports", [...EXPECTED_WORKFLOW_ASSERTIONS.reports, "heading-only-pass"]);
  assert.throws(() => finalizeWorkflowAssertions(extra), /missing or unexpected executed assertion/);

  const missingWorkflow = new Map(complete);
  missingWorkflow.delete("mobile-390");
  assert.throws(() => finalizeWorkflowAssertions(missingWorkflow), /incomplete or unexpected/);
});

test("failed-request producer admits only exact browser-origin GET/fetch abort classes", () => {
  const valid = {
    errorCode: "NET_ERR_ABORTED",
    method: "GET",
    pathClass: "/api/issues",
    phase: "HARNESS_TEARDOWN",
    requestClass: `IPV4_LOOPBACK:${ports.web}`,
    resourceType: "fetch",
  };
  assert.equal(classifyFailedRequestDisposition(valid, { webPort: ports.web }), "HARNESS_CONTEXT_TEARDOWN");
  assert.equal(
    classifyFailedRequestDisposition({ ...valid, phase: "WORKFLOW" }, { webPort: ports.web }),
    "CLIENT_ABORT_CONTROLLER_ROUTE_TEARDOWN",
  );
  for (const mutation of [
    { requestClass: `IPV4_LOOPBACK:${ports.minio}` },
    { requestClass: "IPV4_LOOPBACK:31999" },
    { requestClass: "EXTERNAL_OR_UNEXPECTED" },
    { method: "PUT" },
    { resourceType: "document" },
    { errorCode: "OTHER" },
    { phase: "UNKNOWN" },
    { pathClass: "/:privateBucket/:object" },
    { pathClass: "/api/unexpected" },
  ]) {
    assert.equal(classifyFailedRequestDisposition({ ...valid, ...mutation }, { webPort: ports.web }), "UNRESOLVED");
  }
});

test("canonical manifest binding includes exact workflow/check mappings and validator digest", async () => {
  const { binding } = await loadCanonicalManifest();
  assert.equal(binding.manifestId, "pf-sf1-t11-uat-v1");
  assert.equal(binding.workflowIds.length, 15);
  assert.equal(binding.automatedCheckMappings.length, 33);
  assert.equal(binding.manualCheckMappings.length, 15);
  validateManifestBinding(binding, structuredClone(binding));

  for (const mutation of [
    (value) => { delete value.validatorSha256; },
    (value) => { value.extra = true; },
    (value) => { value.automatedCheckMappings[0].workflowIds = ["reports"]; },
    (value) => { value.canonicalSha256 = "0".repeat(64); },
  ]) {
    const tampered = structuredClone(binding);
    mutation(tampered);
    assert.throws(() => validateManifestBinding(tampered, binding));
  }
});

test("schemaVersion-1 local rehearsal and cleanup validators fail closed on omitted, extra, and tampered fields", async () => {
  const plan = buildPlan({ runId: "ab12cd34", ports });
  const { binding } = await loadCanonicalManifest();
  const validatorOptions = { plan, manifestBinding: binding, expectedAssertions: EXPECTED_WORKFLOW_ASSERTIONS };
  const validRehearsal = rehearsalFixture(plan, binding);
  assert.doesNotThrow(() => validateRehearsalEvidence(validRehearsal, validatorOptions));
  const malformedRehearsal = sealEvidence({ schemaVersion: 1 });
  assert.throws(() => validateRehearsalEvidence(malformedRehearsal, validatorOptions), /omitted or extra fields/);
  const extraRehearsal = sealEvidence({ schemaVersion: 1, unexpected: true });
  assert.throws(() => validateRehearsalEvidence(extraRehearsal, validatorOptions), /omitted or extra fields/);
  malformedRehearsal.schemaVersion = 2;
  assert.throws(() => validateRehearsalEvidence(malformedRehearsal, validatorOptions), /seal mismatch/);

  for (const mutation of [
    (value) => { delete value.automated.findings.unresolvedFailedRequests; },
    (value) => { value.automated.findings.extra = []; },
    (value) => { value.automated.findings.visitedMobileSurfaces.pop(); },
    (value) => { value.automated.findings.failedRequests[0].disposition = "HARNESS_CONTEXT_TEARDOWN"; },
    (value) => { value.automated.findings.failedRequests[1].class = `IPV4_LOOPBACK:${plan.ports.minio}`; },
    (value) => { value.automated.findings.failedRequests[1].class = "IPV4_LOOPBACK:31999"; },
    (value) => { value.automated.findings.failedRequests[1].class = "EXTERNAL_OR_UNEXPECTED"; },
    (value) => { value.automated.findings.failedRequests[1].method = "PUT"; },
    (value) => { value.automated.findings.failedRequests[1].resourceType = "document"; },
    (value) => { value.automated.findings.failedRequests[1].errorCode = "OTHER"; },
    (value) => { value.automated.findings.failedRequests[1].phase = "WORKFLOW"; },
    (value) => { value.automated.findings.failedRequests[1].pathClass = "/:privateBucket/:object"; },
    (value) => { value.automated.findings.failedRequests[1].pathClass = "/api/unexpected"; },
    (value) => { value.automated.findings.failedRequests[1].disposition = "BROWSER_NAVIGATION_REPLACED"; },
    (value) => { value.automated.findings.failedRequests[0].unexpected = true; },
    (value) => { value.automated.findings.unresolvedFailedRequests.push({ unresolved: true }); },
    (value) => { value.automated.mutationSummary.attachmentsDownloaded = 1; },
    (value) => { value.databaseInvariants.stepResults = 8; },
    (value) => { value.automated.workflowResults[0].assertions.pop(); },
  ]) {
    assert.throws(() => validateRehearsalEvidence(reseal(validRehearsal, mutation), validatorOptions));
  }

  const receipt = { bytes: 1, canonicalSha256: "1".repeat(64), fileSha256: "2".repeat(64) };
  const validCleanup = cleanupFixture(plan, receipt);
  assert.doesNotThrow(() => validateCleanupEvidence(validCleanup, { plan, rehearsalReceipt: receipt }));
  const malformedCleanup = sealEvidence({ schemaVersion: 1 });
  assert.throws(() => validateCleanupEvidence(malformedCleanup, { plan, rehearsalReceipt: receipt }), /omitted or extra fields/);
  const extraCleanup = sealEvidence({ schemaVersion: 1, unexpected: true });
  assert.throws(() => validateCleanupEvidence(extraCleanup, { plan, rehearsalReceipt: receipt }), /omitted or extra fields/);
  malformedCleanup.schemaVersion = 2;
  assert.throws(() => validateCleanupEvidence(malformedCleanup, { plan, rehearsalReceipt: receipt }), /seal mismatch/);
  for (const mutation of [
    (value) => { delete value.absence.exactBucket; },
    (value) => { value.removed.unexpected = 1; },
    (value) => { value.removed.containers = 3; },
    (value) => { value.absence.loopbackPortsFree = false; },
    (value) => { value.rehearsal.bytes = 0; },
  ]) {
    assert.throws(() => validateCleanupEvidence(reseal(validCleanup, mutation), { plan, rehearsalReceipt: receipt }));
  }
});
