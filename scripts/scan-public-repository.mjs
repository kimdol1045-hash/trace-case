import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const detectors = Object.freeze([
  ["CLOUDFLARE_API_TOKEN", /\bcfat_[A-Za-z0-9_-]{20,}\b/g],
  ["CLOUDFLARE_R2_ACCOUNT_ENDPOINT", /\b[0-9a-f]{32}\.r2\.cloudflarestorage\.com\b/gi],
  ["AWS_ACCESS_KEY_ID", /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g],
  ["SLACK_TOKEN", /\b(?:xox[baprs]|xapp)-[A-Za-z0-9-]{8,}\b/g],
  ["GITHUB_TOKEN", /\b(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b/g],
  ["OPENAI_API_KEY", /\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b/g],
  ["GOOGLE_API_KEY", /\bAIza[A-Za-z0-9_-]{20,}\b/g],
  ["PRIVATE_KEY", /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g],
  ["MACOS_HOME_PATH", /\/Users\/[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._ ()-]+)*/g],
  ["LINUX_HOME_PATH", /\/home\/[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._ ()-]+)*/g],
  ["WINDOWS_HOME_PATH", /\b[A-Za-z]:\\Users\\[A-Za-z0-9._-]+(?:\\[A-Za-z0-9._ ()-]+)*/g],
]);

function joined(...parts) {
  return parts.join("");
}

// These are deliberately synthetic privacy/redaction fixtures. Both the path and
// exact value must match; another credential-shaped value in the same test fails.
const allowedSyntheticMatches = new Map([
  [
    "apps/api/tests/test_phase_e_human_release_to_suggestion_e2e_postgresql.py",
    new Set([joined("xoxb-", "pe-se2-t11-test")]),
  ],
  [
    "apps/api/tests/test_phase_e_root_to_slack_e2e_postgresql.py",
    new Set([joined("xoxb-", "pe-se2-t10-test")]),
  ],
  [
    "apps/api/tests/test_sprint5_slack_api_postgresql.py",
    new Set([joined("xoxb-", "deterministic-test")]),
  ],
  [
    "apps/worker/tests/test_worker.py",
    new Set([
      joined("AKIA", "1234567890ABCDEF"),
      joined("sk-proj-", "abcdefghijklmnopqrstuvwxyz"),
      joined("xoxb-", "1234567890-abcdefghijklmnop"),
      joined("ghp_", "abcdefghijklmnopqrstuvwxyz123456"),
      joined("AIza", "abcdefghijklmnopqrstuvwxyz123456"),
    ]),
  ],
  [
    "backend/tests/repository/test_generation_quality_snapshot_postgresql.py",
    new Set([joined("sk-", "PROVIDERSECRET123456789"), joined("xoxb-", "RAW-SLACK-TOKEN")]),
  ],
  [
    "backend/tests/unit/privacy/test_review_candidates.py",
    new Set([joined("-----BEGIN ", "PRIVATE KEY-----"), joined("/Users/", "private/customer.txt")]),
  ],
  [
    "packages/contracts/src/review-candidate-contracts.test.ts",
    new Set([joined("-----BEGIN ", "PRIVATE KEY-----"), joined("/Users/", "person/private/file.txt")]),
  ],
  [
    "scripts/tests/test_build_knowledge_golden_sample.py",
    new Set([joined("xoxb-", "123456-secret")]),
  ],
]);

export function forbiddenTrackedPath(path) {
  const normalized = path.replaceAll("\\", "/");
  const name = normalized.split("/").at(-1) ?? normalized;
  if (name === ".env" || (name.startsWith(".env.") && name !== ".env.example")) {
    return "tracked environment file";
  }
  if (/\.(?:pem|key|p12|pfx)$/i.test(name)) return "tracked private-key container";
  if (!normalized.includes("/") && normalized.toLowerCase().endsWith(".csv")) {
    return "root-level source export";
  }
  return null;
}

function lineAndColumn(content, index) {
  const preceding = content.slice(0, index);
  const line = preceding.split("\n").length;
  const lastNewline = preceding.lastIndexOf("\n");
  return { line, column: index - lastNewline };
}

export function scanTrackedContent(path, content) {
  if (content.includes("\0")) return [];
  const allowed = allowedSyntheticMatches.get(path) ?? new Set();
  const findings = [];
  for (const [kind, detector] of detectors) {
    detector.lastIndex = 0;
    for (const match of content.matchAll(detector)) {
      if (allowed.has(match[0])) continue;
      const { line, column } = lineAndColumn(content, match.index ?? 0);
      findings.push({ path, line, column, kind });
    }
  }
  return findings;
}

export async function scanRepository(root = repositoryRoot) {
  const tracked = execFileSync("git", ["ls-files", "-z"], {
    cwd: root,
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  })
    .split("\0")
    .filter(Boolean);
  const findings = [];
  for (const path of tracked) {
    const pathFinding = forbiddenTrackedPath(path);
    if (pathFinding !== null) findings.push({ path, line: 1, column: 1, kind: pathFinding });
    const content = await readFile(resolve(root, path), "utf8");
    findings.push(...scanTrackedContent(path, content));
  }
  return findings;
}

async function main() {
  const findings = await scanRepository(repositoryRoot);
  if (findings.length === 0) {
    console.log("Public repository scan passed: no unapproved credentials, private paths, or source exports found.");
    return;
  }
  console.error("Public repository scan failed:");
  for (const finding of findings) {
    console.error(`- ${finding.path}:${finding.line}:${finding.column} [${finding.kind}]`);
  }
  process.exitCode = 1;
}

const invokedPath = process.argv[1] === undefined ? "" : resolve(process.argv[1]);
if (invokedPath === fileURLToPath(import.meta.url)) await main();
