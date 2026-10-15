#!/usr/bin/env node

/* global fetch, AbortSignal, setTimeout */
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { execFile as execFileCallback, spawn } from "node:child_process";
import { closeSync, constants as fsConstants, openSync } from "node:fs";
import { access, chmod, mkdir, readFile, rm, stat } from "node:fs/promises";
import { createServer } from "node:net";
import { promisify } from "node:util";
import { resolve } from "node:path";
import process from "node:process";

import {
  COMPOSE_PATH,
  MANIFEST_VALIDATOR_PATH,
  REPOSITORY_ROOT,
  SafetyError,
  assertPrivateRegularFile,
  buildPlan,
  canonicalStringify,
  expectedConfirmation,
  hashFile,
  inventoryEvidence,
  loadCanonicalManifest,
  localOrigin,
  sealEvidence,
  sha256,
  sourceInventory,
  validateCleanupEvidence,
  validateRehearsalEvidence,
  verifyEvidenceSeal,
  writeExclusivePrivateJson,
} from "./uat_browser_local_lib.mjs";
import { EXPECTED_WORKFLOW_ASSERTIONS, runPhysicalBrowserUat } from "./uat_browser_workflows.mjs";

const execFile = promisify(execFileCallback);
const CHROME_EXECUTABLE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const DATE = "20260907";
const SENSITIVE_ENV = /^(?:TC_|PG|POSTGRES|MINIO|AWS_|R2_|SLACK_|.*(?:TOKEN|SECRET|PASSWORD|DATABASE_URL|COOKIE).*)/i;

class CommandError extends Error {
  constructor(message, details = {}) {
    super(message);
    this.name = "CommandError";
    this.details = details;
  }
}

async function main() {
  const [mode = "preview", ...argv] = process.argv.slice(2);
  if (!new Set(["preview", "execute"]).has(mode)) throw new SafetyError("usage: uat_browser_local.mjs preview|execute [options]");
  const options = parseArgs(argv);
  const runId = options["run-id"] ?? randomBytes(4).toString("hex");
  const ports = await resolvePorts(options);
  const plan = buildPlan({ runId, ports, date: DATE });
  const executionCommand = [
    "pnpm phase-f:uat:browser --",
    `--run-id ${plan.runId}`,
    ...Object.entries(plan.ports).map(([name, port]) => `--${name}-port ${port}`),
    `--confirm ${plan.confirmation}`,
  ].join(" ");

  if (mode === "preview") {
    process.stdout.write(`${JSON.stringify({
      mode: "PREVIEW_ONLY",
      mutating: false,
      credentialRequired: false,
      plan,
      executionCommand,
      cleanup: "evidence-bound exact resources/processes/temp/profile only",
      externalAccess: "DENY",
    }, null, 2)}\n`);
    return;
  }
  if (options.confirm !== expectedConfirmation(plan.runId)) {
    throw new SafetyError(`execution requires exact confirmation: ${expectedConfirmation(plan.runId)}`);
  }
  await execute(plan);
}

function parseArgs(argv) {
  const options = {};
  if (argv[0] === "--") argv = argv.slice(1);
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key.startsWith("--") || !value || value.startsWith("--")) throw new SafetyError(`invalid option near ${key}`);
    const name = key.slice(2);
    if (!new Set(["run-id", "postgres-port", "minio-port", "clamav-port", "api-port", "web-port", "confirm"]).has(name)) {
      throw new SafetyError(`unknown option: ${key}`);
    }
    if (name in options) throw new SafetyError(`duplicate option: ${key}`);
    options[name] = value;
    index += 1;
  }
  return options;
}

async function resolvePorts(options) {
  const ports = {};
  const reserved = new Set();
  for (const name of ["postgres", "minio", "clamav", "api", "web"]) {
    const supplied = options[`${name}-port`];
    let port;
    if (supplied !== undefined) port = Number(supplied);
    else {
      do port = await availablePort(); while (reserved.has(port) || port < 10_240 || port > 60_999);
    }
    ports[name] = port;
    reserved.add(port);
  }
  return ports;
}

function availablePort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.unref();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0, exclusive: true }, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => error ? reject(error) : resolvePort(port));
    });
  });
}

async function execute(plan) {
  const paths = Object.fromEntries(Object.entries(plan.outputs).map(([key, value]) => [key, resolve(REPOSITORY_ROOT, value)]));
  const temp = plan.resources.tempDirectory;
  const children = [];
  const secrets = {
    databasePassword: randomBytes(24).toString("hex"),
    accessKey: `w10${randomBytes(10).toString("hex")}`,
    secretKey: randomBytes(32).toString("base64url"),
    encryptionKey: randomBytes(32).toString("base64url"),
    reviewSourceKey: randomBytes(32).toString("base64url"),
    reviewerTokenA: randomBytes(24).toString("base64url"),
    reviewerTokenB: randomBytes(24).toString("base64url"),
  };
  let composeEnv;
  let before;
  let rehearsalReceipt;
  let runError;
  let cleanupError;
  let manifestBinding;

  try {
    before = await preflight(plan, paths);
    const canonicalManifest = await loadCanonicalManifest();
    const manifestValidation = JSON.parse((await command(pythonExecutable(), [MANIFEST_VALIDATOR_PATH, "validate-manifest"], { timeout: 60_000 })).stdout);
    if (
      manifestValidation.ok !== true
      || manifestValidation.manifestId !== canonicalManifest.binding.manifestId
      || manifestValidation.manifestSha256 !== canonicalManifest.binding.canonicalSha256
      || manifestValidation.workflowCount !== canonicalManifest.binding.workflowIds.length
      || manifestValidation.automatedCheckCount !== canonicalManifest.binding.automatedCheckMappings.length
      || manifestValidation.manualCheckCount !== canonicalManifest.binding.manualCheckMappings.length
    ) throw new SafetyError("canonical Phase F manifest validator receipt does not match the local manifest binding");
    manifestBinding = canonicalManifest.binding;
    const images = await resolveImages();
    composeEnv = createComposeEnv(plan, secrets, images);
    await command("docker", ["compose", "-f", COMPOSE_PATH, "config", "--quiet"], { env: composeEnv, secrets });
    await mkdir(temp, { mode: 0o700 });
    await chmod(temp, 0o700);
    await mkdir(resolve(temp, "object-storage"), { mode: 0o700 });
    await writeFixtureFiles(temp);

    await command("docker", ["compose", "-f", COMPOSE_PATH, "up", "-d", "--wait", "postgres", "minio", "clamav"], { env: composeEnv, secrets, timeout: 240_000 });
    await command("docker", ["compose", "-f", COMPOSE_PATH, "up", "--no-deps", "minio-init"], { env: composeEnv, secrets, timeout: 90_000 });
    await attestEmptyTarget(plan, composeEnv, secrets);

    const appEnv = createApplicationEnv(plan, secrets);
    await command(pythonExecutable(), ["-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"], { env: appEnv, secrets, timeout: 180_000 });
    const migrated = await databaseCounts(plan, composeEnv, secrets);
    if (migrated.publicTables < 70 || migrated.workspaces !== 1) {
      throw new SafetyError("migrated disposable database did not reach the expected empty application baseline");
    }
    await seedDeterministicSafeStates(plan, composeEnv, secrets);

    children.push(await startChild("api", pythonExecutable(), ["-m", "uvicorn", "tc_api.main:app", "--host", "127.0.0.1", "--port", String(plan.ports.api)], appEnv, temp));
    children.push(await startChild("worker", pythonExecutable(), ["-m", "tc_worker.main", "--poll-interval", "0.25"], appEnv, temp));
    children.push(await startChild("web", "pnpm", ["--filter", "@tc/web", "exec", "vite", "--host", "127.0.0.1", "--port", String(plan.ports.web), "--strictPort"], {
      ...appEnv,
      TC_VITE_PROXY_TARGET: localOrigin(plan.ports.api),
      VITE_API_BASE_URL: "",
      VITE_ENABLE_CYCLE_MOCK_FALLBACK: "false",
    }, temp));
    await waitForHttp(`${localOrigin(plan.ports.api)}/api/health`, children, secrets);
    await waitForHttp(`${localOrigin(plan.ports.web)}/api/ready`, children, secrets);

    const browserResult = await runPhysicalBrowserUat({
      plan,
      chromeExecutable: CHROME_EXECUTABLE,
      fixturePaths: [resolve(temp, "wave10-proof-a.txt"), resolve(temp, "wave10-proof-b.txt")],
      screenshotPaths: { desktop: paths.desktopScreenshot, mobile: paths.mobileScreenshot },
    });
    const finalCounts = await databaseCounts(plan, composeEnv, secrets);
    validateFinalCounts(finalCounts);
    const sourceAfter = await sourceInventory();
    assertEqualDigest(before.source, sourceAfter, "source inventory drifted during Wave 10");
    const priorAfter = await inventoryEvidence({ exclude: Object.values(paths) });
    assertEvidenceInventoryPreserved(before.evidence, priorAfter);
    assert.equal(manifestBinding.workflowIds.length, browserResult.automatedResults.length);
    const durationSeconds = Number(((Date.now() - before.startedAt) / 1000).toFixed(3));

    const rehearsal = sealEvidence({
      schemaVersion: 1,
      evidenceType: "LOCAL_ISOLATED_PHYSICAL_BROWSER_AUTOMATED_UAT",
      taskId: plan.taskId,
      wave: plan.wave,
      runId: plan.runId,
      status: "PASS",
      higherGateStatus: "NOT_RUN",
      manifest: manifestBinding,
      originPolicy: {
        exactBrowserOrigin: plan.origins.web,
        exactApiOrigin: plan.origins.api,
        exactObjectStorageOrigin: plan.origins.objectStorage,
        redirects: "DENY",
        externalRequests: 0,
      },
      resources: safeResourceEvidence(plan),
      source: before.source,
      preservation: {
        preExistingDocker: before.docker,
        preExistingDatabases: before.databases,
        preExistingEvidence: { count: before.evidence.count, digest: before.evidence.digest },
        currentTcWriteCounters: before.currentTc,
      },
      browser: browserResult.browser,
      automated: {
        status: "PASS",
        passCount: browserResult.automatedResults.length,
        notRunCount: 0,
        assertionCount: browserResult.automatedResults.reduce((sum, result) => sum + result.assertions.length, 0),
        workflowResults: browserResult.automatedResults,
        findings: browserResult.findings,
        mutationSummary: browserResult.mutationSummary,
      },
      human: {
        status: "NOT_RUN",
        passCount: 0,
        notRunCount: browserResult.humanResults.length,
        workflowResults: browserResult.humanResults,
      },
      databaseInvariants: finalCounts,
      screenshotArtifacts: [
        { kind: "DESKTOP", name: paths.desktopScreenshot.split("/").at(-1), sha256: await hashFile(paths.desktopScreenshot) },
        { kind: "MOBILE_390", name: paths.mobileScreenshot.split("/").at(-1), sha256: await hashFile(paths.mobileScreenshot) },
      ],
      timing: { durationSeconds },
      remainingGates: remainingGates(),
    });
    validateRehearsalEvidence(rehearsal, { plan, manifestBinding, expectedAssertions: EXPECTED_WORKFLOW_ASSERTIONS });
    const file = await writeExclusivePrivateJson(paths.rehearsal, rehearsal);
    rehearsalReceipt = { ...file, canonicalSha256: rehearsal.seal.canonicalSha256 };
  } catch (error) {
    runError = error;
    if (composeEnv) {
      const diagnostic = await command("docker", ["logs", plan.resources.containers.postgres], {
        env: composeEnv,
        secrets,
        allowFailure: true,
      });
      const constraintNames = [...`${diagnostic.stderr}\n${diagnostic.stdout}`.matchAll(/constraint "([a-z0-9_]+)"/g)]
        .map((match) => match[1])
        .filter((value, index, values) => values.indexOf(value) === index)
        .sort();
      if (constraintNames.length) runError.message += `; databaseConstraints=${constraintNames.join(",")}`;
    }
  } finally {
    try {
      await cleanup(plan, paths, children, composeEnv, secrets, before, rehearsalReceipt, runError);
    } catch (error) {
      cleanupError = error;
    }
  }
  if (runError) throw runError;
  if (cleanupError) throw cleanupError;
  const cleanupEvidence = JSON.parse(await readFile(paths.cleanup, "utf8"));
  process.stdout.write(`${JSON.stringify({
    status: "PASS",
    runId: plan.runId,
    automatedWorkflows: { pass: 15, notRun: 0 },
    humanWorkflows: { pass: 0, notRun: 15 },
    evidence: {
      rehearsal: { path: plan.outputs.rehearsal, fileSha256: await hashFile(paths.rehearsal), canonicalSha256: rehearsalReceipt.canonicalSha256 },
      cleanup: { path: plan.outputs.cleanup, fileSha256: await hashFile(paths.cleanup), canonicalSha256: cleanupEvidence.seal.canonicalSha256 },
      desktopScreenshot: { path: plan.outputs.desktopScreenshot, sha256: await hashFile(paths.desktopScreenshot) },
      mobileScreenshot: { path: plan.outputs.mobileScreenshot, sha256: await hashFile(paths.mobileScreenshot) },
    },
    remainingGates: remainingGates(),
  }, null, 2)}\n`);
}

async function preflight(plan, paths) {
  await access(CHROME_EXECUTABLE, fsConstants.X_OK);
  for (const path of Object.values(paths)) {
    try {
      await stat(path);
      throw new SafetyError(`refusing to overwrite existing Wave 10 output: ${path.split("/").at(-1)}`);
    } catch (error) {
      if (error instanceof SafetyError) throw error;
      if (error.code !== "ENOENT") throw error;
    }
  }
  await assertAbsent(plan.resources.tempDirectory, "temp directory");
  for (const name of Object.values(plan.resources.containers)) await dockerResourceAbsent("container", name);
  for (const name of Object.values(plan.resources.volumes)) await dockerResourceAbsent("volume", name);
  await dockerResourceAbsent("network", plan.resources.network);
  for (const port of Object.values(plan.ports)) await assertPortFree(port);
  const [source, evidence, docker, databases, currentTc] = await Promise.all([
    sourceInventory(), inventoryEvidence({ exclude: Object.values(paths) }), dockerInventory(plan), currentDatabaseInventory(), currentTcWriteCounters(),
  ]);
  return { source, evidence, docker, databases, currentTc, startedAt: Date.now() };
}

async function resolveImages() {
  const tags = {
    postgres: "pgvector/pgvector:0.8.1-pg16",
    minio: "minio/minio:RELEASE.2025-09-07T16-13-09Z",
    clamav: "clamav/clamav-debian:1.4.3",
    mc: "minio/mc:RELEASE.2025-08-13T08-35-41Z",
  };
  return Object.fromEntries(await Promise.all(Object.entries(tags).map(async ([name, tag]) => {
    const { stdout } = await command("docker", ["image", "inspect", tag, "--format", "{{.Id}}"]);
    const id = stdout.trim();
    if (!/^sha256:[a-f0-9]{64}$/.test(id)) throw new SafetyError(`required pre-existing ${name} image is missing or unpinned`);
    return [name, id];
  })));
}

function createComposeEnv(plan, secrets, images) {
  return cleanEnvironment({
    TC_PHASE_F_T11_WAVE10_PROJECT: plan.resources.project,
    TC_PHASE_F_T11_WAVE10_POSTGRES_IMAGE: images.postgres,
    TC_PHASE_F_T11_WAVE10_MINIO_IMAGE: images.minio,
    TC_PHASE_F_T11_WAVE10_CLAMAV_IMAGE: images.clamav,
    TC_PHASE_F_T11_WAVE10_MC_IMAGE: images.mc,
    TC_PHASE_F_T11_WAVE10_POSTGRES_CONTAINER: plan.resources.containers.postgres,
    TC_PHASE_F_T11_WAVE10_MINIO_CONTAINER: plan.resources.containers.minio,
    TC_PHASE_F_T11_WAVE10_CLAMAV_CONTAINER: plan.resources.containers.clamav,
    TC_PHASE_F_T11_WAVE10_MC_CONTAINER: plan.resources.containers.mc,
    TC_PHASE_F_T11_WAVE10_DATABASE: plan.resources.database,
    TC_PHASE_F_T11_WAVE10_USER: plan.resources.databaseUser,
    TC_PHASE_F_T11_WAVE10_PASSWORD: secrets.databasePassword,
    TC_PHASE_F_T11_WAVE10_ACCESS_KEY: secrets.accessKey,
    TC_PHASE_F_T11_WAVE10_SECRET_KEY: secrets.secretKey,
    TC_PHASE_F_T11_WAVE10_BUCKET: plan.resources.bucket,
    TC_PHASE_F_T11_WAVE10_POSTGRES_PORT: String(plan.ports.postgres),
    TC_PHASE_F_T11_WAVE10_MINIO_PORT: String(plan.ports.minio),
    TC_PHASE_F_T11_WAVE10_CLAMAV_PORT: String(plan.ports.clamav),
    TC_PHASE_F_T11_WAVE10_WEB_PORT: String(plan.ports.web),
    TC_PHASE_F_T11_WAVE10_POSTGRES_VOLUME: plan.resources.volumes.postgres,
    TC_PHASE_F_T11_WAVE10_OBJECT_VOLUME: plan.resources.volumes.objectStorage,
    TC_PHASE_F_T11_WAVE10_NETWORK: plan.resources.network,
  });
}

function createApplicationEnv(plan, secrets) {
  const username = encodeURIComponent(plan.resources.databaseUser);
  const password = encodeURIComponent(secrets.databasePassword);
  const database = encodeURIComponent(plan.resources.database);
  return cleanEnvironment({
    PYTHONUNBUFFERED: "1",
    TC_ENVIRONMENT: "local",
    TC_DATABASE_URL: `postgresql+psycopg://${username}:${password}@127.0.0.1:${plan.ports.postgres}/${database}`,
    TC_ENCRYPTION_MASTER_KEY: secrets.encryptionKey,
    TC_SLACK_MODE: "disabled",
    TC_AUTOMATIC_GENERATION_ENABLED: "false",
    TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED: "false",
    TC_LIGHT_RAG_ENABLED: "false",
    TC_QUALITY_REVIEWER_PRINCIPALS: JSON.stringify(["wave10_reviewer_a", "wave10_reviewer_b"]),
    TC_QUALITY_REVIEWER_TOKEN_HASHES: JSON.stringify([
      `sha256:${sha256(secrets.reviewerTokenA)}`,
      `sha256:${sha256(secrets.reviewerTokenB)}`,
    ]),
    TC_QUALITY_REVIEW_SOURCE_SEAL_KEY: secrets.reviewSourceKey,
    TC_QUALITY_REVIEW_SOURCE_SEAL_KEY_ID: "wave10-local-review-source-v1",
    TC_OBJECT_STORAGE_ENABLED: "true",
    TC_OBJECT_STORAGE_PROVIDER: "MINIO",
    TC_OBJECT_STORAGE_ENDPOINT_URL: localOrigin(plan.ports.minio),
    TC_OBJECT_STORAGE_BROWSER_ENDPOINT_URL: localOrigin(plan.ports.minio),
    TC_OBJECT_STORAGE_BUCKET: plan.resources.bucket,
    TC_OBJECT_STORAGE_REGION: "us-east-1",
    TC_OBJECT_STORAGE_ACCESS_KEY_ID: secrets.accessKey,
    TC_OBJECT_STORAGE_SECRET_ACCESS_KEY: secrets.secretKey,
    TC_OBJECT_STORAGE_CLAMAV_HOST: "127.0.0.1",
    TC_OBJECT_STORAGE_CLAMAV_PORT: String(plan.ports.clamav),
    TC_OBJECT_STORAGE_CLAMAV_TIMEOUT_SECONDS: "30",
    TC_OBJECT_STORAGE_TEMP_DIRECTORY: resolve(plan.resources.tempDirectory, "object-storage"),
    TC_WORKER_METRICS_PATH: resolve(plan.resources.tempDirectory, "worker.prom"),
    NO_PROXY: "127.0.0.1,localhost",
    no_proxy: "127.0.0.1,localhost",
  });
}

function cleanEnvironment(overrides) {
  const allowed = ["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "VIRTUAL_ENV", "PNPM_HOME", "SHELL"];
  const result = Object.fromEntries(allowed.filter((key) => process.env[key] !== undefined).map((key) => [key, process.env[key]]));
  for (const [key, value] of Object.entries(overrides)) {
    if (SENSITIVE_ENV.test(key) || key.startsWith("VITE_") || key === "PYTHONUNBUFFERED" || key.toLowerCase() === "no_proxy") result[key] = value;
    else result[key] = value;
  }
  return result;
}

async function attestEmptyTarget(plan, composeEnv, secrets) {
  const baseArgs = ["exec", plan.resources.containers.postgres, "psql", "-X", "-U", plan.resources.databaseUser, "-d", plan.resources.database, "-At", "-v", "ON_ERROR_STOP=1", "-c"];
  const version = (await command("docker", [...baseArgs, "SHOW server_version_num"], { env: composeEnv, secrets })).stdout.trim();
  if (!/^16\d{4}$/.test(version)) throw new SafetyError("disposable target must be PostgreSQL 16");
  const count = (await command("docker", [...baseArgs, "SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')"], { env: composeEnv, secrets })).stdout.trim();
  if (count !== "0") throw new SafetyError("disposable PostgreSQL target is not empty");
  const sql = `COMMENT ON DATABASE "${plan.resources.database}" IS '${plan.resources.databaseComment}'`;
  await command("docker", [...baseArgs, sql], { env: composeEnv, secrets });
  const identitySql = "SELECT current_database() || '|' || coalesce((SELECT description FROM pg_shdescription d JOIN pg_database x ON x.oid=d.objoid WHERE x.datname=current_database()), '')";
  const identity = (await command("docker", [...baseArgs, identitySql], { env: composeEnv, secrets })).stdout.trim();
  if (identity !== `${plan.resources.database}|${plan.resources.databaseComment}`) throw new SafetyError("disposable database identity/comment mismatch");
}

async function seedDeterministicSafeStates(plan, composeEnv, secrets) {
  const sql = `
BEGIN;
INSERT INTO slack_channel_allowlist (id, workspace_id, slack_workspace_id, channel_id, channel_name, is_private, include_private, enabled)
SELECT '10000000-0000-0000-0000-000000000001', id, 'WAVE10_LOCAL', 'CWAVE10LOCAL', 'Wave 10 deterministic local channel', false, false, true
FROM workspace_settings;
INSERT INTO slack_events (id, workspace_id, slack_workspace_id, channel_id, event_id, event_type, event_ts, event_ts_key, payload, status, received_at, processed_at)
SELECT '10000000-0000-0000-0000-000000000002', id, 'WAVE10_LOCAL', 'CWAVE10LOCAL', 'EvWave10LocalRoot', 'ROOT', '1788750000.000001', 1788750000000001,
  '{"team_id":"WAVE10_LOCAL","api_app_id":"AWAVE10LOCAL","type":"event_callback","event_id":"EvWave10LocalRoot","event_time":1788750000,"event":{"type":"message","user":"U_WAVE10_LOCAL","text":"Wave 10 deterministic disposable local request","ts":"1788750000.000001","event_ts":"1788750000.000001","channel":"CWAVE10LOCAL","channel_type":"channel"}}'::jsonb,
  'PROCESSED', '2026-09-07T00:00:00Z', '2026-09-07T00:00:00Z'
FROM workspace_settings;
INSERT INTO slack_work_items (id, workspace_id, slack_workspace_id, channel_id, message_ts, message_ts_key, permalink, root_seen, source_version, parse_status, parse_result, created_event_id)
SELECT '10000000-0000-0000-0000-000000000003', id, 'WAVE10_LOCAL', 'CWAVE10LOCAL', '1788750000.000001', 1788750000000001, NULL, true, 1, 'PENDING', NULL, '10000000-0000-0000-0000-000000000002'
FROM workspace_settings;
INSERT INTO slack_threads (id, slack_work_item_id, thread_ts, thread_ts_key, sync_status, sync_attempts, max_sync_attempts)
VALUES ('10000000-0000-0000-0000-000000000004', '10000000-0000-0000-0000-000000000003', '1788750000.000001', 1788750000000001, 'PENDING', 0, 5);
INSERT INTO slack_messages (id, thread_id, message_ts, message_ts_key, author_id, permalink, is_root, first_event_id)
VALUES ('10000000-0000-0000-0000-000000000005', '10000000-0000-0000-0000-000000000004', '1788750000.000001', 1788750000000001, 'U_WAVE10_LOCAL', NULL, true, '10000000-0000-0000-0000-000000000002');
INSERT INTO slack_message_revisions (id, message_id, source_event_id, source_kind, revision, revision_ts, body, is_deleted, raw_payload, received_at)
VALUES ('10000000-0000-0000-0000-000000000006', '10000000-0000-0000-0000-000000000005', '10000000-0000-0000-0000-000000000002', 'EVENT', 1788750000000001, '1788750000.000001', 'Wave 10 deterministic disposable local request', false, '{}'::jsonb, '2026-09-07T00:00:00Z');
INSERT INTO generation_jobs (
  id, slack_work_item_id, source_version, generation_version, generation_trigger, status, provider, model,
  prompt_version, schema_version, ontology_schema_id, context_item_count, context_sealed_at, context_digest,
  generation_input_capture_state, generation_input_schema_version, generation_input, generation_input_digest,
  root_job_id, retry_of_job_id, attempt_number, error, error_code, error_retryable, error_attempts,
  started_at, completed_at, created_at, updated_at
)
SELECT
  '20000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000003', 1, 1, 'AUTOMATIC', 'FAILED',
  'LOCAL_DISABLED', 'NO_EXTERNAL_CALL', 'wave10-safe-state-v1', '1.0.0', id, 1,
  '2026-09-07T00:00:00Z', repeat('0', 64), 'SEALED', '1.0.0', '{}'::jsonb,
  '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a',
  '20000000-0000-0000-0000-000000000001', NULL, 1, 'Safe local deterministic failure; no provider invoked.',
  'GENERATION_CONFIG_INVALID', false, 1, '2026-09-07T00:00:01Z', '2026-09-07T00:00:02Z', '2026-09-07T00:00:00Z', '2026-09-07T00:00:02Z'
FROM ontology_schemas WHERE active = true;
INSERT INTO generation_retry_exhaustions (
  id, workspace_id, root_job_id, terminal_failed_job_id, finality, retryable, provider_call_budget,
  generation_job_budget, error_code, exhausted_at
)
SELECT '20000000-0000-0000-0000-000000000002', id, '20000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001', 'TERMINAL_NON_RETRYABLE', false, 3, 3,
  'GENERATION_CONFIG_INVALID', '2026-09-07T00:00:02Z'
FROM workspace_settings;
INSERT INTO generation_attempt_events (id, workspace_id, root_job_id, generation_job_id, sequence, event_type, error_code, occurred_at)
SELECT '20000000-0000-0000-0000-000000000003', id, '20000000-0000-0000-0000-000000000001',
  '20000000-0000-0000-0000-000000000001', 1, 'JOB_FAILED', 'GENERATION_CONFIG_INVALID', '2026-09-07T00:00:02Z'
FROM workspace_settings;
SET CONSTRAINTS ALL IMMEDIATE;
COMMIT;`;
  const args = ["exec", plan.resources.containers.postgres, "psql", "-X", "-U", plan.resources.databaseUser, "-d", plan.resources.database, "-v", "ON_ERROR_STOP=1", "-c", sql];
  await command("docker", args, { env: composeEnv, secrets });
  await command(pythonExecutable(), ["scripts/phase-f/uat_quality_review_seed.py"], {
    env: createApplicationEnv(plan, secrets),
    secrets,
    timeout: 120_000,
  });
}

async function databaseCounts(plan, composeEnv, secrets) {
  const sql = `SELECT json_build_object(
    'publicTables',(SELECT count(*) FROM pg_tables WHERE schemaname='public'),
    'workspaces',(SELECT count(*) FROM workspace_settings),
    'folders',(SELECT count(*) FROM folders),
    'workItems',(SELECT count(*) FROM work_items),
    'activeCases',(SELECT count(*) FROM test_cases WHERE deleted_at IS NULL),
    'deletedCases',(SELECT count(*) FROM test_cases WHERE deleted_at IS NOT NULL),
    'caseVersions',(SELECT count(*) FROM test_case_versions),
    'cycles',(SELECT count(*) FROM test_cycles),
    'cycleItems',(SELECT count(*) FROM cycle_items),
    'executionAttempts',(SELECT count(*) FROM execution_attempts),
    'stepResults',(SELECT count(*) FROM execution_step_results),
    'activeIssues',(SELECT count(*) FROM issues WHERE deleted_at IS NULL),
    'deletedIssues',(SELECT count(*) FROM issues WHERE deleted_at IS NOT NULL),
    'activeAttachments',(SELECT count(*) FROM attachments WHERE deleted_at IS NULL),
    'deletedAttachments',(SELECT count(*) FROM attachments WHERE deleted_at IS NOT NULL),
    'generationFailures',(SELECT count(*) FROM generation_retry_exhaustions),
    'generationFailureEvents',(SELECT count(*) FROM generation_attempt_events WHERE event_type='JOB_FAILED'),
    'qualityReviewCandidates',(SELECT count(*) FROM generation_quality_candidates),
    'auditEvents',(SELECT count(*) FROM audit_logs),
    'idempotencyReceipts',(SELECT count(*) FROM api_idempotency_receipts),
    'idempotencyProofs',(SELECT count(*) FROM audit_logs WHERE idempotency_key LIKE 'wave10-replay-%' AND after ? '_idempotency')
  )`;
  const args = ["exec", plan.resources.containers.postgres, "psql", "-X", "-U", plan.resources.databaseUser, "-d", plan.resources.database, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql];
  const output = (await command("docker", args, { env: composeEnv, secrets })).stdout.trim();
  return JSON.parse(output);
}

function validateFinalCounts(counts) {
  const assertions = [
    [counts.publicTables >= 70, "public table baseline"],
    [counts.workspaces === 1, "one isolated workspace"],
    [counts.folders >= 2, "system plus Wave 10 domain"],
    [counts.workItems === 3, "one safe-state and two browser-created parent work items"],
    [counts.activeCases === 7 && counts.deletedCases === 0, "five safe-state and two restored browser cases"],
    [counts.caseVersions >= 9, "safe-state cases plus versioned browser case edit"],
    [counts.cycles === 1 && counts.cycleItems >= 1, "one restored cycle with pinned items"],
    [counts.executionAttempts >= 3 && counts.stepResults >= 3, "per-platform execution attempts and step results"],
    [counts.activeIssues === 1 && counts.deletedIssues === 0, "one restored execution-linked issue"],
    [counts.activeAttachments === 0 && counts.deletedAttachments === 2, "both disposable attachments soft-deleted"],
    [counts.generationFailures === 1 && counts.generationFailureEvents === 1, "one deterministic safe generation failure and append-only event"],
    [counts.qualityReviewCandidates === 1, "one sealed deterministic UNREVIEWED quality-review candidate"],
    [counts.auditEvents >= 12, "mutation audit ledger"],
    [counts.idempotencyReceipts + counts.idempotencyProofs >= 1, "observable request replay ledger"],
  ];
  const failed = assertions.filter(([ok]) => !ok).map(([, name]) => name);
  if (failed.length) throw new SafetyError(`final database invariant failed: ${failed.join(", ")}`);
}

async function cleanup(plan, paths, children, composeEnv, secrets, before, rehearsalReceipt, runError) {
  for (const child of [...children].reverse()) await stopChild(child);
  if (composeEnv) {
    await command("docker", ["compose", "-f", COMPOSE_PATH, "down", "--volumes", "--remove-orphans", "--timeout", "3"], { env: composeEnv, secrets, timeout: 120_000 });
  }
  await rm(plan.resources.tempDirectory, { recursive: true, force: true });
  for (const child of children) {
    if (isProcessAlive(child.pid)) throw new SafetyError(`owned ${child.label} process survived cleanup`);
  }
  for (const name of Object.values(plan.resources.containers)) await dockerResourceAbsent("container", name);
  for (const name of Object.values(plan.resources.volumes)) await dockerResourceAbsent("volume", name);
  await dockerResourceAbsent("network", plan.resources.network);
  await assertAbsent(plan.resources.tempDirectory, "temp directory/browser profile");
  for (const port of Object.values(plan.ports)) await assertPortFree(port);
  const matchingProcesses = await matchingProcessCount(plan.resources.project);
  if (matchingProcesses !== 0) throw new SafetyError("Wave 10 namespaced processes remain after cleanup");

  if (!before) {
    if (!rehearsalReceipt) {
      for (const path of [paths.desktopScreenshot, paths.mobileScreenshot]) await rm(path, { force: true });
    }
    if (runError) return;
    throw new SafetyError("cleanup cannot verify preservation without a preflight baseline");
  }
  const [dockerAfter, databasesAfter, currentTcAfter, evidenceAfter] = await Promise.all([
    dockerInventory(plan), currentDatabaseInventory(), currentTcWriteCounters(), inventoryEvidence({ exclude: [paths.cleanup, paths.rehearsal, paths.desktopScreenshot, paths.mobileScreenshot] }),
  ]);
  assertEqualDigest(before.docker, dockerAfter, "pre-existing Docker inventory changed");
  assertEqualDigest(before.databases, databasesAfter, "pre-existing database inventory changed");
  assertEqualDigest(before.currentTc, currentTcAfter, "current tc write counters changed");
  assertEvidenceInventoryPreserved(before.evidence, evidenceAfter);
  if (runError || !rehearsalReceipt) {
    for (const path of [paths.desktopScreenshot, paths.mobileScreenshot]) await rm(path, { force: true });
    return;
  }
  await assertPrivateRegularFile(paths.rehearsal);
  const rehearsal = JSON.parse(await readFile(paths.rehearsal, "utf8"));
  if (verifyEvidenceSeal(rehearsal) !== rehearsalReceipt.canonicalSha256 || await hashFile(paths.rehearsal) !== rehearsalReceipt.fileSha256) {
    throw new SafetyError("rehearsal evidence changed before cleanup seal");
  }
  const cleanupEvidence = sealEvidence({
    schemaVersion: 1,
    evidenceType: "LOCAL_ISOLATED_PHYSICAL_BROWSER_CLEANUP",
    taskId: plan.taskId,
    wave: plan.wave,
    runId: plan.runId,
    status: "PASS",
    rehearsal: { name: paths.rehearsal.split("/").at(-1), ...rehearsalReceipt },
    removed: {
      containers: Object.keys(plan.resources.containers).length,
      volumes: Object.keys(plan.resources.volumes).length,
      networks: 1,
      databases: 1,
      buckets: 1,
      tempDirectories: 1,
      browserProfiles: 1,
      applicationProcesses: children.length,
    },
    absence: {
      exactContainers: true,
      exactVolumes: true,
      exactNetwork: true,
      exactDatabase: true,
      exactBucket: true,
      tempAndBrowserProfile: true,
      ownedProcesses: true,
      loopbackPortsFree: true,
      matchingProcessCount: 0,
    },
    preservation: {
      preExistingDocker: before.docker,
      preExistingDatabases: before.databases,
      currentTcWriteCounters: before.currentTc,
      preExistingEvidence: { count: before.evidence.count, digest: before.evidence.digest },
    },
    externalRequests: 0,
    remainingGates: remainingGates(),
  });
  validateCleanupEvidence(cleanupEvidence, { plan, rehearsalReceipt });
  await writeExclusivePrivateJson(paths.cleanup, cleanupEvidence);
}

async function startChild(label, executable, args, env, directory) {
  const logPath = resolve(directory, `${label}.log`);
  const fd = openSync(logPath, "wx", 0o600);
  const child = spawn(executable, args, {
    cwd: REPOSITORY_ROOT,
    env,
    detached: true,
    stdio: ["ignore", fd, fd],
  });
  closeSync(fd);
  await new Promise((resolveStart, reject) => {
    child.once("spawn", resolveStart);
    child.once("error", reject);
  });
  return { label, pid: child.pid, child, logPath };
}

async function stopChild(entry) {
  if (!entry?.pid || !isProcessAlive(entry.pid)) return;
  try { process.kill(-entry.pid, "SIGTERM"); } catch (error) { if (error.code !== "ESRCH") throw error; }
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline && isProcessAlive(entry.pid)) await delay(100);
  if (isProcessAlive(entry.pid)) {
    try { process.kill(-entry.pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; }
  }
}

function isProcessAlive(pid) {
  if (!pid) return false;
  try { process.kill(pid, 0); return true; } catch (error) { return error.code !== "ESRCH"; }
}

async function waitForHttp(url, children, secrets) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    for (const child of children) {
      if (!isProcessAlive(child.pid)) throw new CommandError(`${child.label} exited before readiness`, { logDigest: await safeLogDigest(child.logPath, secrets) });
    }
    try {
      const response = await fetch(url, { redirect: "manual", signal: AbortSignal.timeout(2_000) });
      if (response.status === 200 && !response.headers.has("location")) return;
    } catch {
      // Readiness polling intentionally retries bounded local connection errors.
    }
    await delay(250);
  }
  throw new CommandError("local service readiness timeout", { originClass: "IPV4_LOOPBACK" });
}

async function command(executable, args, { env = cleanEnvironment({}), secrets = {}, timeout = 30_000, allowFailure = false } = {}) {
  try {
    const result = await execFile(executable, args, { cwd: REPOSITORY_ROOT, env, timeout, maxBuffer: 8 * 1024 * 1024, encoding: "utf8" });
    return result;
  } catch (error) {
    if (allowFailure) return { stdout: error.stdout ?? "", stderr: error.stderr ?? "", code: error.code ?? 1 };
    const safe = redact(`${error.stderr ?? ""}\n${error.stdout ?? ""}`, secrets);
    throw new CommandError(`${executable} command failed`, { exitCode: error.code ?? null, outputDigest: sha256(safe), outputTail: safe.slice(-800) });
  }
}

function redact(value, secrets) {
  let result = String(value);
  for (const secret of Object.values(secrets)) if (secret) result = result.split(secret).join("<redacted>");
  result = result.replace(/postgres(?:ql)?(?:\+[^:]*)?:\/\/\S+/gi, "<database-url-redacted>");
  result = result.replace(/https?:\/\/\S+[?&](?:X-Amz-[^=]+|token|signature)=[^\s]+/gi, "<signed-url-redacted>");
  return result;
}

async function safeLogDigest(path, secrets) {
  try { return sha256(redact(await readFile(path, "utf8"), secrets)); } catch { return null; }
}

async function writeFixtureFiles(directory) {
  const fixtures = [
    ["wave10-proof-a.txt", "PF-SF1-T11 Wave 10 synthetic attachment A\n"],
    ["wave10-proof-b.txt", "PF-SF1-T11 Wave 10 synthetic attachment B\n"],
  ];
  for (const [name, content] of fixtures) {
    const path = resolve(directory, name);
    const handle = await import("node:fs/promises").then(({ open }) => open(path, "wx", 0o600));
    try { await handle.writeFile(content); await handle.chmod(0o600); } finally { await handle.close(); }
  }
}

async function dockerInventory(plan) {
  const excluded = new Set([
    ...Object.values(plan.resources.containers),
    ...Object.values(plan.resources.volumes),
    plan.resources.network,
  ]);
  const commands = [
    ["containers", ["ps", "-a", "--format", "{{.ID}}|{{.Names}}|{{.Image}}"]],
    ["volumes", ["volume", "ls", "--format", "{{.Name}}"]],
    ["networks", ["network", "ls", "--format", "{{.ID}}|{{.Name}}|{{.Driver}}"]],
    ["images", ["image", "ls", "--no-trunc", "--format", "{{.ID}}|{{.Repository}}:{{.Tag}}"]],
  ];
  const categories = {};
  for (const [name, args] of commands) {
    const output = (await command("docker", args)).stdout.split("\n").filter(Boolean).filter((line) => ![...excluded].some((item) => line.includes(item))).sort();
    categories[name] = { count: output.length, digest: sha256(canonicalStringify(output)) };
  }
  return { categories, digest: sha256(canonicalStringify(categories)) };
}

async function currentDatabaseInventory() {
  if (!await dockerResourceExists("container", "tc-dev-postgres-1")) return { available: false, count: 0, digest: sha256("UNAVAILABLE") };
  const result = await command("docker", ["exec", "tc-dev-postgres-1", "psql", "-X", "-U", "tc", "-d", "postgres", "-At", "-c", "SELECT datname FROM pg_database ORDER BY datname"], { allowFailure: true });
  if (result.code) return { available: false, count: 0, digest: sha256("UNAVAILABLE") };
  const names = result.stdout.split("\n").filter(Boolean);
  return { available: true, count: names.length, digest: sha256(canonicalStringify(names)) };
}

async function currentTcWriteCounters() {
  if (!await dockerResourceExists("container", "tc-dev-postgres-1")) return { available: false, digest: sha256("UNAVAILABLE") };
  const result = await command("docker", ["exec", "tc-dev-postgres-1", "psql", "-X", "-U", "tc", "-d", "postgres", "-At", "-c", "SELECT coalesce(tup_inserted,0)||'|'||coalesce(tup_updated,0)||'|'||coalesce(tup_deleted,0) FROM pg_stat_database WHERE datname='tc'"], { allowFailure: true });
  if (result.code || !/^\d+\|\d+\|\d+\s*$/.test(result.stdout)) return { available: false, digest: sha256("UNAVAILABLE") };
  return { available: true, digest: sha256(result.stdout.trim()) };
}

async function dockerResourceAbsent(type, name) {
  if (await dockerResourceExists(type, name)) throw new SafetyError(`unexpected or foreign ${type} collision: ${name}`);
}

async function dockerResourceExists(type, name) {
  const noun = type === "container" ? "container" : type;
  const result = await command("docker", [noun, "inspect", name], { allowFailure: true });
  return !result.code;
}

async function assertPortFree(port) {
  await new Promise((resolveCheck, reject) => {
    const server = createServer();
    server.once("error", () => reject(new SafetyError(`loopback port ${port} is occupied`)));
    server.listen({ host: "127.0.0.1", port, exclusive: true }, () => server.close(resolveCheck));
  });
}

async function assertAbsent(path, label) {
  try { await stat(path); } catch (error) { if (error.code === "ENOENT") return; throw error; }
  throw new SafetyError(`${label} already exists or survived cleanup: ${path}`);
}

async function matchingProcessCount(project) {
  const result = await command("pgrep", ["-f", project], { allowFailure: true });
  if (result.code === 1) return 0;
  if (result.code) throw new CommandError("could not prove Wave 10 process absence", { exitCode: result.code });
  return result.stdout.split("\n").filter(Boolean).filter((pid) => Number(pid) !== process.pid).length;
}

function assertEqualDigest(before, after, message) {
  if (before.digest !== after.digest || before.count !== undefined && before.count !== after.count) throw new SafetyError(message);
}

function assertEvidenceInventoryPreserved(before, after) {
  if (before.count !== after.count || before.digest !== after.digest) throw new SafetyError("pre-existing Phase F evidence inventory changed");
  const afterByName = new Map(after.entries.map((entry) => [entry.name, entry]));
  for (const entry of before.entries) {
    const current = afterByName.get(entry.name);
    if (!current || current.sha256 !== entry.sha256 || current.bytes !== entry.bytes) throw new SafetyError(`pre-existing evidence drift: ${entry.name}`);
  }
}

function safeResourceEvidence(plan) {
  return {
    namespace: plan.resources.project,
    postgresMajor: 16,
    containers: Object.keys(plan.resources.containers).sort(),
    volumes: Object.keys(plan.resources.volumes).sort(),
    networkCount: 1,
    databaseCount: 1,
    bucketCount: 1,
    processKinds: ["api", "web", "worker", "system-installed-chrome"],
    loopbackPorts: plan.ports,
  };
}

function remainingGates() {
  return [
    "PF-SF1-T11", "PHASE_F", "DEPLOYED_EXACT_HTTPS_ORIGIN", "HUMAN_QA_OWNER_CHECKS",
    "EXTERNAL_INTEGRATIONS", "PRODUCTION", "RELEASE",
  ].map((gate) => ({ gate, status: "NOT_RUN" }));
}

function pythonExecutable() {
  return resolve(REPOSITORY_ROOT, ".venv/bin/python");
}

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

main().catch((error) => {
  const details = error instanceof CommandError ? error.details : {};
  process.stderr.write(`${JSON.stringify({ status: "FAIL", error: error.name, message: error.message, details }, null, 2)}\n`);
  process.exitCode = 1;
});
