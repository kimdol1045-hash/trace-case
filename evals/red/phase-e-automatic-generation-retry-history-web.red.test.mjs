/**
 * PE-SE2-T07 focused Web RED. This file lives outside apps/web/src and is not
 * part of the default Vitest gate. Run it directly with Node after T07 UI work.
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../../", import.meta.url);
const read = (path) => readFile(new URL(path, root), "utf8");

test("failure queue service exposes list, detail, history, and conditional retry", async () => {
  const service = await read("apps/web/src/generation-job-service.ts");
  for (const symbol of [
    "GenerationFailureQueuePage",
    "GenerationFailureDetail",
    "listGenerationFailures",
    "getGenerationFailure",
    "getGenerationAttemptHistory",
    "retryAutomaticGenerationFailure",
  ]) {
    assert.match(service, new RegExp(`\\b${symbol}\\b`), `RED T07: missing Web service ${symbol}`);
  }
  assert.match(service, /\/api\/generation-failures/);
});

test("Korean failure workspace is routed separately from the manual workspace", async () => {
  const [routes, page] = await Promise.all([
    read("apps/web/src/app-routes-eager.tsx"),
    read("apps/web/src/generation-failure-page.tsx").catch(() => ""),
  ]);
  assert.match(routes, /path="\/generation\/failures"/, "RED T07: missing /generation/failures route");
  for (const label of ["자동 생성 실패", "시도 이력", "추가 조건", "조건을 추가해 재시도"])
    assert.match(page, new RegExp(label), `RED T07: missing Korean label ${label}`);
});

test("failure workspace declares mobile and accessible interaction seams", async () => {
  const [page, styles] = await Promise.all([
    read("apps/web/src/generation-failure-page.tsx").catch(() => ""),
    read("apps/web/src/styles.css"),
  ]);
  assert.match(page, /aria-live="polite"/);
  assert.match(page, /aria-describedby=/);
  assert.match(page, /role="dialog"/);
  assert.match(styles, /tm-generation-failure/);
  assert.match(styles, /min-height:\s*44px/);
  assert.match(styles, /max-width:\s*390px/);
});
