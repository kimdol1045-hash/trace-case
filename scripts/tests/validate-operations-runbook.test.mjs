import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { githubSlug, validateRunbook } from "../validate-operations-runbook.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const runbookPath = resolve(root, "deploy/operations/README.md");
const validMarkdown = await readFile(runbookPath, "utf8");

async function errorsFor(markdown) {
  return validateRunbook({ markdown, runbookPath, root });
}

test("normalizes nested heading markup without leaving an executable tag", () => {
  assert.equal(githubSlug("<<script>alert(1)</script> 안전"), "alert1-안전");
});

test("authoritative operations runbook satisfies the structural and safety contract", async () => {
  assert.deepEqual(await errorsFor(validMarkdown), []);
});

for (const heading of ["사전 조건", "안전 명령·참조", "기대 증거", "롤백·중단 조건"]) {
  test(`rejects a required section without ${heading}`, async () => {
    const invalid = validMarkdown.replace(`### ${heading}`, `### 누락된 ${heading}`);
    assert((await errorsFor(invalid)).some((error) => error.includes(`missing subsection: ${heading}`)));
  });
}

test("rejects a missing canonical operations section", async () => {
  const invalid = validMarkdown.replace("<!-- ops-section:alerts -->", "<!-- removed-alerts-section -->");
  assert((await errorsFor(invalid)).some((error) => error.includes("section alerts must appear exactly once")));
});

test("rejects a stale local heading reference", async () => {
  const invalid = validMarkdown.replace(
    "../../README.md#처음-한-번-준비",
    "../../README.md#없는-운영-제목",
  );
  assert((await errorsFor(invalid)).some((error) => error.includes("local reference heading does not exist")));
});

const sensitiveLiteralCases = [
  ["unquoted Slack token", `TC_SLACK_BOT_TOKEN=${["xoxb", "example-credential-literal"].join("-")}`],
  ["quoted API token", 'TC_API_TOKEN="example-api-token-literal"'],
  ["single-quoted API token with export and spacing", "export TC_API_TOKEN = 'example-api-token-literal'"],
  [
    "quoted object-storage secret access key",
    'TC_OBJECT_STORAGE_SECRET_ACCESS_KEY="example-secret-access-key-literal"',
  ],
  [
    "HTML-encoded quotes and equals",
    "TC_ENCRYPTION_MASTER_KEY&#61;&quot;example-master-key-literal&quot;",
  ],
];

for (const [name, assignment] of sensitiveLiteralCases) {
  test(`rejects a sensitive environment literal: ${name}`, async () => {
    const invalid = `${validMarkdown}\n\`${assignment}\`\n`;
    assert(
      (await errorsFor(invalid)).some((error) =>
        error.includes(name === "unquoted Slack token" ? "Slack credential literal" : "sensitive environment literal"),
      ),
    );
  });
}

const multilineSensitiveLiteralCases = [
  ["LF shell continuation", 'export TC_API_TOKEN\\\n="example-api-token-literal"'],
  ["CRLF shell continuation with spacing", "export TC_API_TOKEN \\\r\n = 'example-api-token-literal'"],
  [
    "numeric-entity encoded shell continuation",
    "export TC_API_TOKEN&#92;&#10;&#61;&quot;example-api-token-literal&quot;",
  ],
  [
    "named-entity encoded shell continuation",
    "export TC_API_TOKEN&bsol;&NewLine;&equals;&quot;example-api-token-literal&quot;",
  ],
];

for (const [name, assignment] of multilineSensitiveLiteralCases) {
  test(`rejects a multiline sensitive environment literal: ${name}`, async () => {
    const invalid = `${validMarkdown}\n\`${assignment}\`\n`;
    assert((await errorsFor(invalid)).some((error) => error.includes("sensitive environment literal")));
  });
}

test("allows empty values, placeholders, and variable references for sensitive environment names", async () => {
  const safeAssignments = [
    "TC_API_TOKEN=",
    "TC_API_TOKEN=<secret-store reference>",
    'TC_API_TOKEN="<secret-store reference>"',
    "TC_API_TOKEN='replace-with-secret-store-value'",
    "TC_API_TOKEN=$RUNTIME_API_TOKEN",
    'TC_API_TOKEN="${RUNTIME_API_TOKEN}"',
    "TC_OBJECT_STORAGE_SECRET_ACCESS_KEY=${RUNTIME_SECRET_ACCESS_KEY}",
  ].join("\n");
  assert.deepEqual(await errorsFor(`${validMarkdown}\n${safeAssignments}\n`), []);
});

test("allows placeholders and variable references across valid shell continuations", async () => {
  const safeAssignments = [
    "export TC_API_TOKEN\\\n=$RUNTIME_API_TOKEN",
    'export TC_API_TOKEN\\\r\n="${RUNTIME_API_TOKEN}"',
    "export TC_API_TOKEN\\\n=<secret-store reference>",
    "export TC_API_TOKEN&bsol;&NewLine;&equals;replace-with-secret-store-value",
    'export TC_API_TOKEN\\ \n="example-api-token-literal"',
  ].join("\n");
  assert.deepEqual(await errorsFor(`${validMarkdown}\n${safeAssignments}\n`), []);
});

const destructiveCases = [
  ["rm option separator with root target", "rm -rf -- /", "broad rm target"],
  ["rm split flags, spacing, and quoted root", 'rm  -r  -f  --  "/"', "broad rm target"],
  ["rm shell-escaped root", String.raw`rm -rf -- \/`, "broad rm target"],
  ["rm HTML-encoded root", "rm -rf -- &#47;", "broad rm target"],
  ["rm home-directory variable", 'rm -rf -- "${HOME}/"', "broad rm target"],
  ["rm current directory", "rm -rf -- .", "broad rm target"],
  ["find root delete", "find / -delete", "broad find -delete target"],
  ["find quoted root with depth", 'find  "/"  -depth  -delete', "broad find -delete target"],
  ["find HTML-encoded root", "find &#47; -type f -delete", "broad find -delete target"],
  ["find home delete", "find ~/ -delete", "broad find -delete target"],
  ["drop database", "DROP DATABASE appdb;", "broad SQL destruction"],
  ["drop database spacing and quoted identifier", 'drop   database   "appdb";', "broad SQL destruction"],
  ["drop database with HTML-encoded spacing", "DROP&#32;DATABASE&#32;appdb;", "broad SQL destruction"],
  ["drop schema with an SQL comment separator", "DROP/**/SCHEMA/**/public CASCADE;", "broad SQL destruction"],
  ["truncate table", "TRUNCATE TABLE app_events;", "broad SQL destruction"],
  ["drop database across LF", "DROP\nDATABASE appdb;", "broad SQL destruction"],
  ["truncate table across LF", "TRUNCATE\nTABLE app_events;", "broad SQL destruction"],
  [
    "drop database across an SQL line comment",
    "DROP -- preserve lexical SQL whitespace\nDATABASE appdb;",
    "broad SQL destruction",
  ],
  ["drop database across CRLF with spacing and casing", "dRoP \r\n  DaTaBaSe appdb;", "broad SQL destruction"],
  [
    "drop schema across a multiline block comment",
    "DROP/* bounded\r\ncomment */SCHEMA public CASCADE;",
    "broad SQL destruction",
  ],
  [
    "truncate table with encoded whitespace",
    "TRUNCATE&#10;TABLE&#9;app_events;",
    "broad SQL destruction",
  ],
  [
    "drop database with named newline entity",
    "DrOp&NewLine;DaTaBaSe appdb;",
    "broad SQL destruction",
  ],
  [
    "drop database across a CRLF SQL line comment",
    "DROP -- comment with mixed CASE\r\n DATABASE  IF  EXISTS \r\n appdb;",
    "broad SQL destruction",
  ],
];

for (const [name, command, expectedError] of destructiveCases) {
  test(`rejects destructive command variant: ${name}`, async () => {
    const invalid = `${validMarkdown}\n\`${command}\`\n`;
    assert((await errorsFor(invalid)).some((error) => error.includes(expectedError)));
  });
}

test("allows bounded filesystem cleanup and non-destructive database commands", async () => {
  const safeCommands = [
    "rm -rf -- ./build/cache",
    'rm -rf -- "$HOME/.cache/tc"',
    "find ./tmp/session -type f -delete",
    "find /srv/tc/quarantine -type f -mtime +30 -delete",
    'find "${HOME}/tmp/tc" -type f -delete',
    "DELETE FROM app_events WHERE id = 42;",
    "pg_dump --dbname appdb --format custom",
  ].join("\n");
  assert.deepEqual(await errorsFor(`${validMarkdown}\n${safeCommands}\n`), []);
});

test("allows SQL placeholders, bounded commands, comments, and ordinary multiline Korean prose", async () => {
  const safeCommandsAndProse = [
    "DROP\nDATABASE <database-name>;",
    "DROP -- target comes from the runtime\nDATABASE IF EXISTS ${TC_DATABASE_NAME};",
    'TRUNCATE\nTABLE "$TC_TABLE_NAME";',
    "DROP TABLE app_events;",
    "ALTER DATABASE appdb SET statement_timeout = '5s';",
    "CREATE SCHEMA app_reporting;",
    "-- DROP DATABASE appdb;",
    "/* TRUNCATE\r\nTABLE app_events; */",
    "DROP은 SQL 문맥에서 제거를 뜻하지만 이 문장은 명령이 아니다.",
    "DATABASE와 SCHEMA의 운영 범위를 설명하는 평문이다.",
    "TRUNCATE라는 표현을 다음 줄의 한국어 문장과 함께 설명한다.",
    "TABLE 데이터는 이 검증 과정에서 변경하지 않는다.",
  ].join("\n");
  assert.deepEqual(await errorsFor(`${validMarkdown}\n${safeCommandsAndProse}\n`), []);
});

test("rejects completion language when operator handoff evidence is removed", async () => {
  const invalid = validMarkdown.replace("`PF-SF1-T12`는 **미완료**", "`PF-SF1-T12` 완료");
  assert((await errorsFor(invalid)).some((error) => error.includes("explicitly keep PF-SF1-T12 incomplete")));
});
