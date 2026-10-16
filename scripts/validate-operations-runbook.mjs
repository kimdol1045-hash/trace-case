import { access, readFile } from "node:fs/promises";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const REQUIRED_SECTION_IDS = Object.freeze([
  "bootstrap",
  "configuration",
  "lifecycle",
  "health",
  "jobs",
  "slack",
  "object-storage",
  "generation",
  "knowledge",
  "postgresql",
  "alerts",
  "rollback",
  "credential-loss",
  "handoff",
]);

export const REQUIRED_SUBSECTIONS = Object.freeze([
  "사전 조건",
  "안전 명령·참조",
  "기대 증거",
  "롤백·중단 조건",
]);

const secretPatterns = Object.freeze([
  [/(?:xox[baprs]|xapp)-[A-Za-z0-9-]{8,}/g, "Slack credential literal"],
  [/\bAKIA[0-9A-Z]{16}\b/g, "AWS-style access key literal"],
  [/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g, "private key literal"],
  [/\bBearer\s+[A-Za-z0-9._~+/-]{12,}={0,2}\b/g, "bearer credential literal"],
  [
    /postgres(?:ql)?(?:\+[a-z0-9_]+)?:\/\/[^\s:/@]+:([^@\s/]+)@/gi,
    "database URL password literal",
  ],
]);

const broadFilesystemTarget = String.raw`(?:\/+|\/+\.{1,2}\/?|\/+\*|~\/?|\$\{?(?:HOME|CODEX_HOME)\}?\/?\*?|\.{1,2}\/?|\*|\.{1,2}\/\*)`;
const horizontalSpace = String.raw`[^\S\r\n]`;

const destructivePatterns = Object.freeze([
  [
    new RegExp(
      String.raw`(?<![\w-])rm\b[^\r\n]*?(?:^|${horizontalSpace})(?:--${horizontalSpace}+)?["']?${broadFilesystemTarget}["']?(?=$|[\s\x60;,|&])`,
      "gim",
    ),
    "broad rm target",
  ],
  [
    new RegExp(
      String.raw`(?<![\w-])find\b${horizontalSpace}+(?:(?:-[HLP]|-O\d+)${horizontalSpace}+|-D${horizontalSpace}+\S+${horizontalSpace}+|--${horizontalSpace}+)*["']?${broadFilesystemTarget}["']?(?=${horizontalSpace})[^\r\n]*?(?:^|${horizontalSpace})-delete\b`,
      "gim",
    ),
    "broad find -delete target",
  ],
  [/\bdocker\s+(?:system|volume)\s+prune\b/g, "broad Docker prune"],
  [/\bkubectl\s+delete\s+(?:namespace|all)\b/g, "broad Kubernetes delete"],
  [/\bgit\s+clean\s+-[^\s]*[fdx][^\s]*/g, "broad Git clean"],
  [/\bgit\s+reset\s+--hard\b/g, "broad Git reset"],
  [/\balembic\b[^\n]*\bdowngrade\s+base\b/g, "broad Alembic downgrade"],
  [/\baws\s+s3\s+rm\s+s3:\/\/[^\s]+[^\n]*--recursive\b/g, "recursive object deletion"],
  [/\brclone\s+(?:purge|delete)\b/g, "broad object deletion"],
]);

const destructiveSqlPatterns = Object.freeze([
  [
    /\b(?:DROP\s+(?:DATABASE|SCHEMA)\s+(?:IF\s+EXISTS\s+)?(?!IF\b)["\x60\[]?[A-Za-z_]|TRUNCATE\s+(?:TABLE\s+)?(?:ONLY\s+)?(?!TABLE\b|ONLY\b)["\x60\[]?[A-Za-z_])/gi,
    "broad SQL destruction",
  ],
]);

const sensitiveEnvironmentAssignment =
  /\bTC_[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|ACCESS_KEY_ID|SECRET_ACCESS_KEY|MASTER_KEY|PRIVATE_KEY|API_KEY)\b[^\S\r\n]*=[^\S\r\n]*(?:"([^"\r\n]*)"|'([^'\r\n]*)'|(<[^>\r\n]*>)|([^\s`]+))/g;

const htmlSafetyEntities = Object.freeze({
  apos: "'",
  bsol: "\\",
  equals: "=",
  nbsp: " ",
  newline: "\n",
  quot: '"',
  sol: "/",
  tab: "\t",
});

function stripHtmlTags(value) {
  let result = "";
  let insideTag = false;
  for (const character of value) {
    if (character === "<") {
      insideTag = true;
    } else if (character === ">") {
      insideTag = false;
    } else if (!insideTag) {
      result += character;
    }
  }
  return result;
}

export function githubSlug(heading) {
  const withoutTags = stripHtmlTags(heading
    .toLowerCase()
    .trim()
    .replaceAll("\u0000", "")
    .split("\n", 1)[0]);
  return withoutTags
    .replace(/[`*_~]/g, "")
    .replace(/[^\p{Letter}\p{Number}\s_-]/gu, "")
    .replace(/\s/g, "-");
}

function findSubsection(section, heading) {
  const pattern = new RegExp(`^### ${heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`, "m");
  const match = pattern.exec(section);
  if (match === null) return null;
  const contentStart = match.index + match[0].length;
  const rest = section.slice(contentStart);
  const nextHeading = /^###?\s+/m.exec(rest);
  const contentEnd = nextHeading === null ? section.length : contentStart + nextHeading.index;
  return section.slice(contentStart, contentEnd).trim();
}

function isPlaceholder(value) {
  const candidate = value.trim();
  return (
    candidate.length === 0 ||
    /^<[^<>\r\n]+>$/.test(candidate) ||
    /^replace[-_][A-Za-z0-9._-]+$/i.test(candidate) ||
    /^\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})$/.test(candidate)
  );
}

function decodeSafetyCharacters(markdown) {
  return markdown
    .replace(/&([A-Za-z]+);/g, (entity, name) => htmlSafetyEntities[name.toLowerCase()] ?? entity)
    .replace(/&#(?:x([0-9A-F]+)|(\d+));/gi, (entity, hexadecimal, decimal) => {
      const codePoint = Number.parseInt(hexadecimal ?? decimal, hexadecimal === undefined ? 10 : 16);
      if (![9, 10, 13, 32, 34, 39, 47, 61, 92, 160].includes(codePoint)) return entity;
      return codePoint === 160 ? " " : String.fromCodePoint(codePoint);
    })
    .replace(/[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]/g, " ")
    .replaceAll("／", "/")
    .replaceAll("＝", "=")
    .replaceAll(String.raw`\/`, "/");
}

function maskSqlComment(comment) {
  return comment.replace(/[^\r\n]/g, " ");
}

function buildCanonicalSafetyScan(markdown) {
  const decoded = decodeSafetyCharacters(markdown);
  const shell = decoded.replace(/\\\r?\n/g, "");
  const sql = shell
    .replace(/\/\*[\s\S]*?\*\//g, maskSqlComment)
    .replace(/--[^\r\n]*(?:\r\n|\r|\n|$)/g, maskSqlComment);
  return { shell, sql };
}

function scanSensitiveEnvironmentAssignments(markdown, errors) {
  sensitiveEnvironmentAssignment.lastIndex = 0;
  let match;
  while ((match = sensitiveEnvironmentAssignment.exec(markdown)) !== null) {
    const value = match[1] ?? match[2] ?? match[3] ?? match[4];
    if (isPlaceholder(value)) continue;
    const line = markdown.slice(0, match.index).split("\n").length;
    errors.push(`sensitive environment literal at line ${line}`);
  }
}

function scanPatterns(markdown, patterns, errors) {
  for (const [pattern, label] of patterns) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(markdown)) !== null) {
      if (label === "database URL password literal" && isPlaceholder(match[1])) continue;
      const line = markdown.slice(0, match.index).split("\n").length;
      errors.push(`${label} at line ${line}`);
      if (!pattern.global) break;
    }
  }
}

function extractLocalLinks(markdown) {
  const links = [];
  const pattern = /\[[^\]]+\]\(([^)]+)\)/g;
  let match;
  while ((match = pattern.exec(markdown)) !== null) {
    const target = match[1].trim().replace(/^<|>$/g, "");
    if (/^(?:https?:|mailto:)/i.test(target)) continue;
    links.push(target);
  }
  return links;
}

async function validateLocalLink(target, runbookPath, root, errors) {
  const [rawPath, rawFragment] = target.split("#", 2);
  if (!rawPath) {
    errors.push(`local reference must name a file: ${target}`);
    return;
  }
  const linkedPath = resolve(dirname(runbookPath), decodeURIComponent(rawPath));
  const relativeTarget = relative(root, linkedPath);
  if (relativeTarget.startsWith("..") || resolve(root, relativeTarget) !== linkedPath) {
    errors.push(`local reference escapes repository: ${target}`);
    return;
  }
  try {
    await access(linkedPath);
  } catch {
    errors.push(`local reference does not exist: ${target}`);
    return;
  }
  if (rawFragment === undefined) return;
  const linkedMarkdown = await readFile(linkedPath, "utf8");
  const slugs = linkedMarkdown
    .split("\n")
    .filter((line) => /^#{1,6}\s+/.test(line))
    .map((line) => githubSlug(line.replace(/^#{1,6}\s+/, "")));
  if (!slugs.includes(decodeURIComponent(rawFragment))) {
    errors.push(`local reference heading does not exist: ${target}`);
  }
}

export async function validateRunbook({ markdown, runbookPath, root }) {
  const errors = [];
  const markerPattern = /<!-- ops-section:([a-z0-9-]+) -->/g;
  const markers = [...markdown.matchAll(markerPattern)];
  const actualIds = markers.map((match) => match[1]);

  for (const id of REQUIRED_SECTION_IDS) {
    const occurrences = actualIds.filter((candidate) => candidate === id).length;
    if (occurrences !== 1) errors.push(`section ${id} must appear exactly once (found ${occurrences})`);
  }
  for (const id of actualIds) {
    if (!REQUIRED_SECTION_IDS.includes(id)) errors.push(`unknown operations section: ${id}`);
  }
  if (actualIds.join(",") !== REQUIRED_SECTION_IDS.join(",")) {
    errors.push("operations sections must use the required canonical order");
  }

  for (let index = 0; index < markers.length; index += 1) {
    const marker = markers[index];
    const id = marker[1];
    if (!REQUIRED_SECTION_IDS.includes(id)) continue;
    const start = marker.index + marker[0].length;
    const end = index + 1 < markers.length ? markers[index + 1].index : markdown.length;
    const section = markdown.slice(start, end);
    if (!/^\s*## OPS-\d{2} — /m.test(section)) {
      errors.push(`section ${id} must start with an OPS-numbered level-two heading`);
    }
    for (const heading of REQUIRED_SUBSECTIONS) {
      const content = findSubsection(section, heading);
      if (content === null) {
        errors.push(`section ${id} is missing subsection: ${heading}`);
      } else if (content.length < 40) {
        errors.push(`section ${id} subsection is too short: ${heading}`);
      }
    }
    const referenceContent = findSubsection(section, "안전 명령·참조");
    if (referenceContent !== null && extractLocalLinks(referenceContent).length === 0) {
      errors.push(`section ${id} safe reference must include a repository-local link`);
    }
    const evidenceContent = findSubsection(section, "기대 증거");
    if (evidenceContent !== null && !evidenceContent.includes("증거:")) {
      errors.push(`section ${id} expected evidence must include an explicit 증거: statement`);
    }
    const stopContent = findSubsection(section, "롤백·중단 조건");
    if (stopContent !== null && !stopContent.includes("중단:")) {
      errors.push(`section ${id} rollback/stop content must include an explicit 중단: statement`);
    }
  }

  if (!markdown.includes("FOUNDATION_READY / OPERATOR_HANDOFF_REQUIRED")) {
    errors.push("runbook must retain the operator-handoff-required status");
  }
  if (!markdown.includes("`PF-SF1-T12`는 **미완료**")) {
    errors.push("runbook must explicitly keep PF-SF1-T12 incomplete without handoff evidence");
  }

  const safetyScan = buildCanonicalSafetyScan(markdown);
  scanPatterns(safetyScan.shell, secretPatterns, errors);
  scanSensitiveEnvironmentAssignments(safetyScan.shell, errors);
  scanPatterns(safetyScan.shell, destructivePatterns, errors);
  scanPatterns(safetyScan.sql, destructiveSqlPatterns, errors);

  for (const target of new Set(extractLocalLinks(markdown))) {
    await validateLocalLink(target, runbookPath, root, errors);
  }
  return errors;
}

async function main() {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const runbookPath = resolve(root, "deploy/operations/README.md");
  const rootReadmePath = resolve(root, "README.md");
  const [markdown, rootReadme] = await Promise.all([
    readFile(runbookPath, "utf8"),
    readFile(rootReadmePath, "utf8"),
  ]);
  const errors = await validateRunbook({ markdown, runbookPath, root });
  if (!rootReadme.includes("](./deploy/operations/README.md)")) {
    errors.push("root README must link the authoritative operations entrypoint");
  }
  if (errors.length > 0) {
    throw new Error(`Operations runbook validation failed:\n- ${errors.join("\n- ")}`);
  }
  process.stdout.write(
    `Validated ${REQUIRED_SECTION_IDS.length} operations sections, local references, secret guards, and destructive-target guards.\n`,
  );
}

if (resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  await main();
}
