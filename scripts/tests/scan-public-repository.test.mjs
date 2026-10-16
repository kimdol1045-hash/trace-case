import assert from "node:assert/strict";
import test from "node:test";

import { forbiddenTrackedPath, scanTrackedContent } from "../scan-public-repository.mjs";

test("rejects provider credentials and reports their location without printing the value", () => {
  const token = ["cfat_", "this-is-a-realistic-secret-token"].join("");
  assert.deepEqual(scanTrackedContent("src/config.ts", `safe\nconst token = "${token}";`), [
    { path: "src/config.ts", line: 2, column: 16, kind: "CLOUDFLARE_API_TOKEN" },
  ]);
});

test("rejects personal home paths", () => {
  const path = ["/Users/", "someone/private.csv"].join("");
  assert.equal(scanTrackedContent("README.md", path)[0]?.kind, "MACOS_HOME_PATH");
});

test("allows only the exact synthetic redaction fixture at its declared path", () => {
  const fixturePath = "backend/tests/unit/privacy/test_review_candidates.py";
  const allowed = ["/Users/", "private/customer.txt"].join("");
  const unexpected = ["/Users/", "someone/customer.txt"].join("");
  assert.deepEqual(scanTrackedContent(fixturePath, allowed), []);
  assert.equal(scanTrackedContent(fixturePath, unexpected)[0]?.kind, "MACOS_HOME_PATH");
});

test("rejects tracked secret containers and root source exports", () => {
  assert.equal(forbiddenTrackedPath("backend/.env"), "tracked environment file");
  assert.equal(forbiddenTrackedPath("certificates/service.key"), "tracked private-key container");
  assert.equal(forbiddenTrackedPath("customer-export.csv"), "root-level source export");
});

test("allows public templates and bounded synthetic fixtures", () => {
  assert.equal(forbiddenTrackedPath("backend/.env.example"), null);
  assert.equal(forbiddenTrackedPath("backend/tests/unit/fixtures/historical_qa_synthetic.csv"), null);
  assert.deepEqual(scanTrackedContent("docs/example.md", "TC_API_TOKEN=<secret-store reference>"), []);
});
