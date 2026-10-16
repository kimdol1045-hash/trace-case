/* global URL, document, fetch, getComputedStyle, requestAnimationFrame, window */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import { chromium, expect } from "@playwright/test";

import {
  SAFE_ROUTE_ABORT_FETCH_PATH_CLASSES,
  localOrigin,
  makePrivateFile,
} from "./uat_browser_local_lib.mjs";

const TIMEOUT = 30_000;
const SAFE_ROUTE_ABORT_FETCH_PATH_SET = new Set(SAFE_ROUTE_ABORT_FETCH_PATH_CLASSES);

export const EXPECTED_WORKFLOW_ASSERTIONS = Object.freeze({
  dashboard: ["dashboard-kpis-match-product-api", "dashboard-hard-reload-preserves-kpis"],
  "test-library": ["domain-work-items-and-cases-created-through-ui", "case-edit-version-persists-after-reload-and-deeplink", "work-item-detail-deeplink-persists"],
  "drag-move": ["invalid-drop-preserves-parent-and-order-after-reload", "same-parent-reorder-persists", "cross-work-item-move-persists"],
  "create-cancel-save": ["cancel-removes-draft-from-history-and-storage", "create-and-versioned-edit-are-durable"],
  cycles: ["scoped-preview-creates-pinned-planned-cycle", "planned-execution-controls-are-disabled", "completed-execution-is-read-only", "reopen-restores-entry-and-preserves-pinned-version", "cycle-delete-permanent-gate-and-restore"],
  "per-step-execution": ["all-platform-step-results-saved-by-product-api", "failure-and-blocked-notes-persist-after-reload"],
  "issues-attachments": ["execution-link-and-edit-persist", "two-files-one-chooser-download-byte-match-delete"],
  "deleted-permanent": ["test-case-delete-permanent-gate-and-restore", "issue-delete-permanent-gate-and-restore", "cycle-delete-permanent-gate-and-restore"],
  reports: ["reports-dashboard-api-aggregates-reconcile", "reports-ui-selected-cycle-counts-match"],
  "slack-work-items": ["local-seeded-provenance-is-credential-free"],
  knowledge: ["authoritative-local-knowledge-safe-state"],
  integrations: ["slack-disabled-boundary-visible", "object-storage-private-ready-boundaries-visible"],
  "generation-failure-safe": ["terminal-nonretryable-safe-detail-visible", "attempt-and-append-only-timeline-visible", "raw-provider-absent-and-operator-retry-boundary-visible"],
  "quality-review-safe": ["candidate-unreviewed-boundary-visible", "reviewer-token-storage-and-url-remain-empty", "release-certification-not-claimed"],
  "mobile-390": ["all-required-mobile-routes-have-no-overflow", "mobile-detail-form-execution-safe-state-controls-reachable", "mobile-modal-primary-actions-reachable"],
});

export function finalizeWorkflowAssertions(receipts, expected = EXPECTED_WORKFLOW_ASSERTIONS) {
  const expectedIds = Object.keys(expected);
  const observedIds = [...receipts.keys()];
  assert.deepEqual(observedIds.sort(), [...expectedIds].sort(), "workflow assertion receipts are incomplete or unexpected");
  return expectedIds.map((workflowId) => {
    const observed = receipts.get(workflowId) ?? [];
    assert.equal(new Set(observed).size, observed.length, `duplicate assertion receipt: ${workflowId}`);
    assert.deepEqual([...observed].sort(), [...expected[workflowId]].sort(), `missing or unexpected executed assertion: ${workflowId}`);
    return { workflowId, status: "PASS", assertions: [...expected[workflowId]] };
  });
}

export function classifyFailedRequestDisposition(observation, { webPort }) {
  const exactBrowserClass = `IPV4_LOOPBACK:${webPort}`;
  if (
    observation.errorCode !== "NET_ERR_ABORTED"
    || observation.requestClass !== exactBrowserClass
    || observation.method !== "GET"
    || observation.resourceType !== "fetch"
    || !SAFE_ROUTE_ABORT_FETCH_PATH_SET.has(observation.pathClass)
  ) return "UNRESOLVED";
  if (observation.phase === "WORKFLOW") return "CLIENT_ABORT_CONTROLLER_ROUTE_TEARDOWN";
  if (observation.phase === "HARNESS_TEARDOWN") return "HARNESS_CONTEXT_TEARDOWN";
  return "UNRESOLVED";
}

function recordAssertion(receipts, workflowId, assertionId) {
  assert(EXPECTED_WORKFLOW_ASSERTIONS[workflowId]?.includes(assertionId), `undeclared assertion receipt: ${workflowId}/${assertionId}`);
  const current = receipts.get(workflowId) ?? [];
  assert(!current.includes(assertionId), `duplicate assertion execution: ${workflowId}/${assertionId}`);
  current.push(assertionId);
  receipts.set(workflowId, current);
}

export async function runPhysicalBrowserUat({ plan, chromeExecutable, fixturePaths, screenshotPaths }) {
  const webOrigin = localOrigin(plan.ports.web);
  const allowedPorts = new Set([plan.ports.web, plan.ports.minio]);
  const browserFindings = {
    consoleErrors: [],
    pageErrors: [],
    failedRequests: [],
    unresolvedFailedRequests: [],
    httpFailures: [],
    externalRequestAttempts: [],
    visitedRouteKinds: [],
    visitedMobileSurfaces: [],
  };
  const assertionReceipts = new Map();
  const context = await chromium.launchPersistentContext(plan.resources.browserProfile, {
    executablePath: chromeExecutable,
    headless: true,
    viewport: { width: 1440, height: 1000 },
    acceptDownloads: true,
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    serviceWorkers: "block",
    args: [
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-default-apps",
      "--disable-domain-reliability",
      "--disable-features=AutofillServerCommunication,OptimizationHints,MediaRouter",
      "--disable-sync",
      "--metrics-recording-only",
      "--no-first-run",
      "--no-default-browser-check",
      "--password-store=basic",
      "--use-mock-keychain",
      "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
    ],
  });
  let contextClosed = false;

  try {
    const pages = context.pages();
    const page = pages[0] ?? await context.newPage();
    page.setDefaultTimeout(TIMEOUT);
    page.setDefaultNavigationTimeout(TIMEOUT);
    const failureTracking = installFailureTracking(page, browserFindings, allowedPorts, plan.ports.minio, plan.ports.web);

    const routeResults = new Map();
    const visit = async (path, heading) => {
      await page.goto(`${webOrigin}${path}`, { waitUntil: "domcontentloaded" });
      await assertStablePage(page, heading);
      routeResults.set(routeKind(path), "PASS");
    };

    await visit("/", "QA Dashboard");
    const ids = await exerciseTestLibrary(page, visit, plan.runId, assertionReceipts);
    const cycle = await exerciseCycleAndIssue(page, visit, ids, plan.runId, fixturePaths, browserFindings, assertionReceipts);
    const issue = await exerciseIssueLifecycle(page, visit, cycle.issueSummary, fixturePaths, assertionReceipts);
    await exerciseCycleLifecycle(page, visit, cycle, assertionReceipts);
    const readRoutes = await exerciseReadRoutes(page, visit, cycle, assertionReceipts);
    await exerciseDashboardAndReports(page, visit, cycle, assertionReceipts);
    await exerciseIdempotencyReplay(page, plan.runId);
    await page.screenshot({ path: screenshotPaths.desktop, fullPage: false });
    await makePrivateFile(screenshotPaths.desktop);

    await page.setViewportSize({ width: 390, height: 844 });
    await exerciseMobileSurfaces(page, visit, { ...ids, ...cycle, ...issue, ...readRoutes }, browserFindings, assertionReceipts);
    await page.screenshot({ path: screenshotPaths.mobile, fullPage: false });
    await makePrivateFile(screenshotPaths.mobile);

    browserFindings.visitedRouteKinds = [...routeResults.keys()].sort();
    assert.deepEqual(browserFindings.visitedRouteKinds, [...new Set(plan.routes)].sort(), "planned route coverage is incomplete or unexpected");
    assert.deepEqual(browserFindings.externalRequestAttempts, [], "external browser request attempted");
    assert.deepEqual(browserFindings.httpFailures, [], "browser observed an HTTP failure");
    assert.deepEqual(browserFindings.consoleErrors, [], "browser console error occurred");
    assert.deepEqual(browserFindings.pageErrors, [], "uncaught browser error occurred");
    assert.deepEqual(browserFindings.unresolvedFailedRequests, [], "browser request failed without a closed-loop disposition");

    const automatedResults = finalizeWorkflowAssertions(assertionReceipts);
    const result = {
      automatedResults,
      humanResults: automatedResults.map(({ workflowId }) => ({
        workflowId,
        status: "NOT_RUN",
        reasonCode: "ACCOUNTABLE_OWNER_OR_EXTERNAL_JUDGMENT_REQUIRED",
      })),
      browser: {
        engine: "system-installed Google Chrome",
        executableSha256: await fileSha256(chromeExecutable),
        headless: true,
        networkMocking: false,
        serviceWorkers: "blocked",
        viewports: ["1440x1000", "390x844"],
      },
      findings: browserFindings,
      mutationSummary: {
        attachmentFilesSelectedInOneEvent: 2,
        attachmentsDownloaded: 2,
        attachmentsDeleted: 2,
        casesCreated: 2,
        cyclesCreated: 1,
        domainsCreated: 1,
        issuesCreatedFromExecution: 1,
        safeGenerationFailuresSeeded: 1,
        safeQualityReviewCandidatesSeeded: 1,
        workItemsCreated: 2,
      },
    };
    failureTracking.beginHarnessTeardown();
    await context.close();
    contextClosed = true;
    assert.deepEqual(browserFindings.externalRequestAttempts, [], "external browser request attempted during workflow or harness teardown");
    assert.deepEqual(browserFindings.httpFailures, [], "browser observed an HTTP failure during workflow or harness teardown");
    assert.deepEqual(browserFindings.consoleErrors, [], "browser console error occurred during workflow or harness teardown");
    assert.deepEqual(browserFindings.pageErrors, [], "uncaught browser error occurred during workflow or harness teardown");
    assert.deepEqual(browserFindings.unresolvedFailedRequests, [], "browser or harness teardown request failed without a closed-loop disposition");
    return result;
  } finally {
    if (!contextClosed) await context.close();
  }
}

function installFailureTracking(page, findings, allowedPorts, objectStoragePort, webPort) {
  let phase = "WORKFLOW";
  const classify = (rawUrl) => {
    if (rawUrl.startsWith("data:") || rawUrl.startsWith("blob:") || rawUrl === "about:blank") return "INTERNAL_SCHEME";
    const parsed = new URL(rawUrl);
    const port = Number(parsed.port || (parsed.protocol === "http:" ? 80 : 443));
    if (parsed.protocol === "http:" && parsed.hostname === "127.0.0.1" && allowedPorts.has(port)) {
      return `IPV4_LOOPBACK:${port}`;
    }
    findings.externalRequestAttempts.push({ originSha256: simpleSha256(parsed.origin), scheme: parsed.protocol.replace(":", "") });
    return "EXTERNAL_OR_UNEXPECTED";
  };
  page.on("console", (message) => {
    if (message.type() === "error") findings.consoleErrors.push({ textSha256: simpleSha256(message.text()) });
  });
  page.on("pageerror", (error) => findings.pageErrors.push({ name: error.name, messageSha256: simpleSha256(error.message) }));
  page.on("request", (request) => classify(request.url()));
  page.on("requestfailed", (request) => {
    const errorText = request.failure()?.errorText ?? "unknown";
    const parsed = new URL(request.url());
    const requestClass = classify(request.url());
    const errorCode = errorText === "net::ERR_ABORTED" ? "NET_ERR_ABORTED" : "OTHER";
    const pathClass = Number(parsed.port) === objectStoragePort
      ? "/:privateBucket/:object"
      : sanitizePathClass(parsed.pathname);
    const disposition = classifyFailedRequestDisposition({
      errorCode,
      method: request.method(),
      pathClass,
      phase,
      requestClass,
      resourceType: request.resourceType(),
    }, { webPort });
    const observation = { class: requestClass, disposition, errorCode, method: request.method(), pathClass, phase, resourceType: request.resourceType() };
    const existing = findings.failedRequests.find(({ count: _count, ...item }) => canonicalSafeText(item) === canonicalSafeText(observation));
    if (existing) existing.count += 1;
    else findings.failedRequests.push({ ...observation, count: 1 });
    if (disposition === "UNRESOLVED") findings.unresolvedFailedRequests.push({ ...observation, errorSha256: simpleSha256(errorText) });
  });
  page.on("response", async (response) => {
    if (response.status() < 400) return;
    const pathname = new URL(response.url()).pathname;
    const routeClass = pathname.startsWith("/api/attachments/") ? "ATTACHMENT_API"
      : pathname.startsWith("/api/generation/quality-review") ? "QUALITY_REVIEW_API"
        : pathname.startsWith("/api/generation") ? "GENERATION_API"
          : pathname.startsWith("/api/") ? "PRODUCT_API"
            : "WEB_ASSET_OR_ROUTE";
    const pathClass = sanitizePathClass(pathname);
    const failure = { class: classify(response.url()), pathClass, routeClass, status: response.status() };
    try {
      const body = await response.json();
      if (typeof body?.code === "string") failure.errorCode = body.code;
    } catch {
      // Only structured product error codes are retained; response bodies are not evidence-safe.
    }
    findings.httpFailures.push(failure);
  });
  return {
    beginHarnessTeardown() {
      phase = "HARNESS_TEARDOWN";
    },
  };
}

async function assertStablePage(page, heading) {
  const marker = page.getByRole("heading", { name: heading, exact: false })
    .or(page.getByText(heading, { exact: false }))
    .first();
  await expect(marker).toBeVisible({ timeout: TIMEOUT });
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const state = await page.evaluate(() => {
    const root = document.querySelector("#root");
    const primary = [...document.querySelectorAll("button.tm-primary-button, a.tm-primary-button")]
      .map((node) => {
        const style = getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return {
          label: node.textContent?.trim().slice(0, 80) ?? "",
          left: rect.left,
          right: rect.right,
          width: rect.width,
          height: rect.height,
          hidden: style.display === "none" || style.visibility === "hidden" || style.opacity === "0"
            || node.closest('[aria-hidden="true"], [hidden], [inert]') !== null,
        };
      });
    return {
      rootTextLength: root?.textContent?.trim().length ?? 0,
      frames: document.querySelectorAll("iframe,frame").length,
      visibleErrors: [...document.querySelectorAll('[role="alert"], .tm-api-state.is-error')]
        .filter((node) => {
          const style = getComputedStyle(node);
          return style.display !== "none" && style.visibility !== "hidden";
        }).map((node) => node.textContent?.trim().slice(0, 200) ?? ""),
      primary,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    };
  });
  assert(state.rootTextLength > 0, "root was empty after navigation stabilized");
  assert.equal(state.frames, 0, "ghost frame detected");
  assert.deepEqual(state.visibleErrors, [], `visible error state detected at ${new URL(page.url()).pathname} (${heading}): ${JSON.stringify(state.visibleErrors)}`);
  assert(state.scrollWidth <= state.clientWidth + 1, `horizontal document overflow: ${state.scrollWidth}/${state.clientWidth}`);
  for (const control of state.primary) {
    assert.equal(control.hidden, false, `hidden primary action detected: ${control.label}`);
    assert(control.width > 0 && control.height > 0, `zero-size primary action detected: ${control.label}`);
    assert(control.left >= -1 && control.right <= state.clientWidth + 1, `primary action clipped horizontally: ${control.label}`);
  }
}

async function assertMobileLayout(page) {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    scroll: document.documentElement.scrollWidth,
    navigationVisible: [...document.querySelectorAll(".navigation-link")].some((node) => {
      const rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    }),
  }));
  assert.equal(dimensions.viewport, 390);
  assert(dimensions.scroll <= 391, `mobile horizontal overflow: ${dimensions.scroll}`);
  assert.equal(dimensions.navigationVisible, true, "mobile primary navigation is hidden");
}

async function exerciseTestLibrary(page, visit, runId, receipts) {
  const suffix = Number.parseInt(runId.slice(0, 4), 36) % 8000 + 1000;
  const domainName = `Wave 10 ${runId}`;
  const workA = `WORK-${suffix}`;
  const workB = `WORK-${suffix + 1}`;

  await visit("/test-cases", "Test Library");
  await page.getByTitle("카테고리 추가").click();
  await page.getByLabel("새 카테고리명").fill(domainName);
  await page.getByRole("button", { name: "생성", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "카테고리를 생성했습니다" })).toContainText("카테고리를 생성했습니다");

  for (const [key, summary] of [[workA, "Wave 10 primary work item"], [workB, "Wave 10 move target"]]) {
    await page.getByRole("button", { name: "+ 상위일감 만들기" }).click();
    const dialog = page.getByRole("dialog", { name: "상위일감 만들기" });
    await dialog.locator("select").first().selectOption({ label: domainName });
    await dialog.getByPlaceholder("WORK-531").fill(key);
    await dialog.getByPlaceholder("상위일감 제목").fill(summary);
    await dialog.getByPlaceholder("검증 범위와 배경을 입력하세요.").fill("Disposable local browser UAT scope");
    await dialog.getByPlaceholder("미지정").fill("wave10-owner");
    await dialog.getByPlaceholder("smoke, regression").fill("wave10, browser");
    await dialog.getByRole("button", { name: "상위일감 저장" }).click();
    await expect(page.getByRole("status").filter({ hasText: `${key} 상위일감을 생성했습니다` })).toContainText(`${key} 상위일감을 생성했습니다`);
  }

  await page.getByRole("button", { name: "+ 테스트케이스 만들기" }).click();
  await expect(page).toHaveURL(/\/test-cases\/new/);
  await page.getByLabel("테스트케이스 서머리").fill("cancelled browser draft");
  await page.getByRole("link", { name: "취소", exact: true }).click();
  await expect(page).toHaveURL(/\/test-cases$/);
  await page.goBack();
  await expect(page).not.toHaveURL(/\/test-cases\/new/);
  await visit("/test-cases", "Test Library");
  await expect(page.getByText("cancelled browser draft", { exact: true })).toHaveCount(0);
  const cancelledDraftStorage = await page.evaluate(() => ({ local: Object.keys(localStorage).filter((key) => key.includes("cancelled browser draft")), session: Object.keys(sessionStorage).filter((key) => key.includes("cancelled browser draft")) }));
  assert.deepEqual(cancelledDraftStorage, { local: [], session: [] });
  recordAssertion(receipts, "create-cancel-save", "cancel-removes-draft-from-history-and-storage");

  const caseA = await createCase(page, domainName, workA, "Wave 10 multi-step case", 3);
  const caseB = await createCase(page, domainName, workA, "Wave 10 reorder case", 2);

  await visit(`/test-cases/${caseA}`, "Test case");
  await page.getByLabel("테스트케이스 서머리").fill("Wave 10 multi-step case edited");
  await page.getByRole("button", { name: "+ 스텝 추가" }).click();
  await page.getByLabel("4번 사전조건").fill("fourth precondition");
  await page.getByLabel("4번 테스트 동작").fill("fourth action");
  await page.getByLabel("4번 기대 결과").fill("fourth expected");
  await page.getByLabel("2번 스텝 삭제").click();
  await page.getByLabel("3번 스텝 순서 이동").dragTo(page.locator(".tm-step-row").first());
  await page.getByRole("button", { name: "변경사항 저장" }).click();
  await expect(page.getByRole("status").filter({ hasText: "변경사항이 저장되었습니다" })).toContainText("변경사항이 저장되었습니다");
  const directCasePath = new URL(page.url()).pathname;
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, "Test case");
  await expect(page.locator(".tm-step-row")).toHaveCount(3);
  await page.getByRole("tab", { name: /변경 이력/ }).click();
  await expect(page.getByText("v2", { exact: false }).first()).toBeVisible();
  await page.goto(`${localOriginFromPage(page)}${directCasePath}`, { waitUntil: "domcontentloaded" });
  await assertStablePage(page, "Test case");
  recordAssertion(receipts, "test-library", "case-edit-version-persists-after-reload-and-deeplink");
  recordAssertion(receipts, "create-cancel-save", "create-and-versioned-edit-are-durable");

  await visit(`/work-items/${workA}`, workA);
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, workA);
  await expect(page.getByText(caseA, { exact: false }).first()).toBeVisible();
  recordAssertion(receipts, "test-library", "work-item-detail-deeplink-persists");

  await visit("/test-cases", "Test Library");
  await page.getByRole("button", { name: `${workA} 테스트케이스 펼치기` }).click();
  const initialOrder = await page.locator(`[data-work-item-key="${workA}"] [data-case-key]`).evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-case-key")));
  await page.getByLabel(`${caseB} 순서 이동`).dragTo(page.getByRole("heading", { name: "Test Library" }));
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, "Test Library");
  await page.getByRole("button", { name: `${workA} 테스트케이스 펼치기` }).click();
  assert.deepEqual(await page.locator(`[data-work-item-key="${workA}"] [data-case-key]`).evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-case-key"))), initialOrder);
  recordAssertion(receipts, "drag-move", "invalid-drop-preserves-parent-and-order-after-reload");
  const reorderResponse = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/move") && response.request().method() === "POST");
  await page.getByLabel(`${caseB} 순서 이동`).dragTo(page.locator(`[data-case-key="${caseA}"]`));
  assert.equal((await reorderResponse).status(), 200);
  await expect(page.locator(`[data-work-item-key="${workA}"] [data-case-key]`).first()).toHaveAttribute("data-case-key", caseB);
  recordAssertion(receipts, "drag-move", "same-parent-reorder-persists");
  const moveResponse = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/move") && response.request().method() === "POST");
  await page.getByLabel(`${caseB} 순서 이동`).dragTo(page.locator(`[data-drop-work-item-key="${workB}"]`));
  assert.equal((await moveResponse).status(), 200);
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, "Test Library");
  await page.getByRole("button", { name: `${workB} 테스트케이스 펼치기` }).click();
  await expect(page.locator(`[data-work-item-key="${workB}"] [data-case-key="${caseB}"]`)).toBeVisible();
  recordAssertion(receipts, "drag-move", "cross-work-item-move-persists");
  recordAssertion(receipts, "test-library", "domain-work-items-and-cases-created-through-ui");

  await page.locator(`[data-case-key="${caseB}"]`).hover();
  await page.getByLabel(`${caseB} 작업 메뉴`).click();
  await page.locator(`[data-case-key="${caseB}"]`).getByRole("button", { name: "삭제", exact: true }).click();
  await page.locator(`[data-case-key="${caseB}"]`).getByRole("button", { name: "삭제", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "삭제했습니다" })).toContainText("삭제했습니다");
  await page.getByRole("tab", { name: "삭제됨" }).click();
  await page.getByRole("button", { name: `${workB} 테스트케이스 펼치기` }).click();
  await page.locator(`[data-case-key="${caseB}"]`).click();
  await assertStablePage(page, "Test case");
  await expect(page.getByText("삭제됨 · 읽기 전용", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "완전 삭제", exact: true }).click();
  const permanent = page.getByRole("alertdialog", { name: "테스트케이스 완전 삭제" });
  await expect(permanent.getByRole("button", { name: "완전 삭제", exact: true })).toBeDisabled();
  await permanent.getByLabel("완전 삭제 확인용 테스트케이스 키").fill(`${caseB} `);
  await expect(permanent.getByRole("button", { name: "완전 삭제", exact: true })).toBeDisabled();
  await permanent.getByLabel("완전 삭제 확인용 테스트케이스 키").fill(caseB);
  await expect(permanent.getByRole("button", { name: "완전 삭제", exact: true })).toBeEnabled();
  await permanent.getByRole("button", { name: "취소", exact: true }).click();
  const caseRestoreResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/restore"));
  await page.getByRole("button", { name: "삭제 복원" }).click();
  assert.equal((await caseRestoreResponse).status(), 200, "test case restore API status");
  await expect(page.getByText("삭제됨 · 읽기 전용", { exact: false })).toHaveCount(0);
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, "Test case");
  await expect(page.getByText("삭제됨 · 읽기 전용", { exact: false })).toHaveCount(0);
  recordAssertion(receipts, "deleted-permanent", "test-case-delete-permanent-gate-and-restore");
  return { caseA, caseB, workA, workB, domainName };
}

async function createCase(page, domainName, workItemKey, summary, stepCount) {
  await page.goto(`${localOriginFromPage(page)}/test-cases/new`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "테스트케이스 만들기" })).toBeVisible();
  await page.getByLabel("테스트케이스 서머리").fill(summary);
  await page.getByLabel("카테고리").selectOption({ label: domainName });
  const parentSelect = page.getByLabel("상위일감");
  const parentValue = await parentSelect.locator("option").filter({ hasText: workItemKey }).getAttribute("value");
  assert(parentValue, `missing parent work item option: ${workItemKey}`);
  await parentSelect.selectOption(parentValue);
  for (let index = 1; index <= stepCount; index += 1) {
    if (index > 1) await page.getByRole("button", { name: "+ 스텝 추가" }).click();
    await page.getByLabel(`${index}번 사전조건`).fill(`precondition ${index}`);
    await page.getByLabel(`${index}번 테스트 동작`).fill(`action ${index}`);
    await page.getByLabel(`${index}번 기대 결과`).fill(`expected ${index}`);
  }
  await page.getByRole("button", { name: "테스트케이스 저장" }).click();
  await expect(page).toHaveURL(/\/test-cases\/TC-\d+$/);
  await assertStablePage(page, "Test case");
  return new URL(page.url()).pathname.split("/").at(-1);
}

async function exerciseCycleAndIssue(page, visit, ids, runId, fixturePaths, browserFindings, receipts) {
  const cycleName = `Wave 10 cycle ${runId}`;
  await visit("/cycles", "Test Cycles");
  await page.getByRole("button", { name: "+ 새 테스트 사이클" }).click();
  await page.getByLabel("사이클명").fill(cycleName);
  await page.getByLabel("시작일").fill("2026-09-07");
  await page.getByRole("button", { name: "상위 일감 선택" }).click();
  const picker = page.getByRole("dialog", { name: "상위 일감 선택" });
  await picker.getByRole("option", { name: new RegExp(ids.workA) }).click();
  await expect(page.locator(".tm-preview-case-list").getByText(ids.caseA, { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "사이클 만들기" })).toBeEnabled();
  await page.getByRole("button", { name: "사이클 만들기" }).click();
  await expect(page).toHaveURL(/\/cycles\/cycle_/);
  const cyclePath = new URL(page.url()).pathname;
  const cycleId = cyclePath.split("/").at(-1);
  await assertStablePage(page, cycleName);
  const planned = await productGet(page, `/api/cycles/${cycleId}`);
  const plannedItems = await productGet(page, `/api/cycles/${cycleId}/items?limit=100`);
  assert.equal(planned.status, "PLANNED");
  assert.equal(planned.scopeLocked, false);
  assert.equal(plannedItems.items.length, 1);
  const pinnedVersion = plannedItems.items[0].pinnedVersion.version;
  const pinnedVersionId = plannedItems.items[0].pinnedVersionId;
  recordAssertion(receipts, "cycles", "scoped-preview-creates-pinned-planned-cycle");

  await visit(`${cyclePath}/execute`, cycleName);
  await expect(page.getByText("미진행 사이클은 실행 결과를 입력할 수 없습니다.", { exact: false })).toBeVisible();
  for (const tab of await page.getByRole("tab").all()) await expect(tab).toBeDisabled();
  await expect(page.getByRole("button", { name: "+ 이슈 생성" })).toBeDisabled();
  recordAssertion(receipts, "cycles", "planned-execution-controls-are-disabled");

  await visit(`/test-cases/${ids.caseA}`, "Test case");
  await page.getByLabel("테스트케이스 서머리").fill("Wave 10 post-snapshot library edit");
  await page.getByRole("button", { name: "변경사항 저장" }).click();
  await expect(page.getByRole("status").filter({ hasText: "변경사항이 저장되었습니다" })).toContainText("변경사항이 저장되었습니다");
  await visit(cyclePath, cycleName);
  await expect(page.getByText(new RegExp(`pinned v${pinnedVersion} / latest v`))).toBeVisible();
  await page.getByRole("button", { name: "사이클 시작" }).click();
  await expect(page).toHaveURL(/\/cycles\/cycle_[^/]+\/execute$/);
  await assertStablePage(page, cycleName);

  for (let step = 1; step <= 3; step += 1) {
    await page.locator('.tm-player-steps article[role="button"]').nth(step - 1).click();
    const statuses = step === 1
      ? [["WEB", "PASS"], ["AOS", "FAIL"], ["iOS", "BLOCKED"]]
      : [["WEB", "PASS"], ["AOS", "PASS"], ["iOS", "PASS"]];
    for (const [platform, status] of statuses) {
      await page.getByRole("tab", { name: new RegExp(`^${platform}`) }).click();
      const saveResponse = waitForStepSave(page);
      await page.getByRole("option", { name: status, exact: true }).click();
      await expect(page.getByRole("tab", { name: new RegExp(`^${platform}`) })).toContainText(status);
      assert.equal((await saveResponse).status(), 200);
      if (step === 1 && platform === "AOS") {
        const noteResponse = waitForStepSave(page);
        await page.getByLabel(`${ids.caseA} 비고`).fill("Wave 10 FAIL autosave note");
        assert.equal((await noteResponse).status(), 200);
      }
      if (step === 1 && platform === "iOS") {
        const noteResponse = waitForStepSave(page);
        await page.getByLabel(`${ids.caseA} 비고`).fill("Wave 10 BLOCKED autosave note");
        assert.equal((await noteResponse).status(), 200);
      }
    }
  }
  const executionPath = new URL(page.url()).pathname;
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, cycleName);
  await expect(page.getByRole("tab", { name: /^WEB/ })).toContainText("PASS");
  await expect(page.getByRole("tab", { name: /^AOS/ })).toContainText("FAIL");
  await expect(page.getByRole("tab", { name: /^iOS/ })).toContainText("BLOCKED");
  await page.getByRole("tab", { name: /^AOS/ }).click();
  await expect(page.getByLabel(`${ids.caseA} 비고`)).toHaveValue("Wave 10 FAIL autosave note");
  await page.getByRole("tab", { name: /^iOS/ }).click();
  await expect(page.getByLabel(`${ids.caseA} 비고`)).toHaveValue("Wave 10 BLOCKED autosave note");
  const savedItems = await productGet(page, `/api/cycles/${cycleId}/items?limit=100`);
  assert.equal(savedItems.items[0].pinnedVersion.version, pinnedVersion);
  assert.equal(savedItems.items[0].pinnedVersionId, pinnedVersionId);
  assert.equal(savedItems.items[0].latestVersion.version > pinnedVersion, true);
  const savedStatuses = savedItems.items[0].platformExecutions.map((item) => item.status).sort();
  assert.deepEqual(savedStatuses, ["BLOCKED", "FAIL", "PASS"]);
  recordAssertion(receipts, "per-step-execution", "all-platform-step-results-saved-by-product-api");
  recordAssertion(receipts, "per-step-execution", "failure-and-blocked-notes-persist-after-reload");

  await page.getByRole("tab", { name: /^AOS/ }).click();
  await page.getByRole("button", { name: "+ 이슈 생성" }).click();
  const dialog = page.getByRole("dialog", { name: "이슈 생성" });
  const issueSummary = `Wave 10 execution issue ${runId}`;
  await dialog.locator("input").first().fill(issueSummary);
  await dialog.getByLabel("이슈 첨부파일 선택").setInputFiles(fixturePaths);
  await expect(dialog.getByText("wave10-proof-a.txt")).toBeVisible();
  await expect(dialog.getByText("wave10-proof-b.txt")).toBeVisible();
  await dialog.getByRole("button", { name: "이슈 생성", exact: true }).click();
  await expect(dialog).toHaveCount(0, { timeout: 60_000 });
  const issueStatus = await page.locator(".tm-player-issue").innerText();
  if (!issueStatus.includes("첨부파일 2개")) {
    await page.waitForTimeout(250);
    assert.fail(`execution issue attachment flow failed; HTTP=${JSON.stringify(browserFindings.httpFailures)}`);
  }
  await page.goto(`${localOriginFromPage(page)}${executionPath}`, { waitUntil: "domcontentloaded" });
  await assertStablePage(page, cycleName);
  return { cycleName, cycleId, cyclePath, executionPath, issueSummary, pinnedVersion, pinnedVersionId };
}

async function exerciseIssueLifecycle(page, visit, issueSummary, fixturePaths, receipts) {
  await visit("/issues", "Issues");
  await page.getByRole("row").filter({ hasText: issueSummary }).click();
  await expect(page).toHaveURL(/\/issues\/ISSUE-\d+$/);
  await assertStablePage(page, "ISSUE-");
  const issuePath = new URL(page.url()).pathname;
  await expect(page.locator(".tm-linked-evidence-row")).toHaveCount(1);
  await expect(page.locator(".tm-linked-evidence-row").first()).toContainText(/실행 근거|테스트케이스/);
  await expect(page.locator(".tm-attachment-list article")).toHaveCount(2);
  await page.getByLabel("서머리").fill(`${issueSummary} edited`);
  const issueSave = page.waitForResponse((response) => response.request().method() === "PATCH" && new URL(response.url()).pathname.startsWith("/api/issues/"));
  await page.getByRole("button", { name: "변경사항 저장" }).click();
  const issueSaveResponse = await issueSave;
  if (issueSaveResponse.status() !== 200) {
    const body = await issueSaveResponse.json().catch(() => ({}));
    assert.fail(`issue edit API status ${issueSaveResponse.status()}; code=${String(body.code ?? "UNKNOWN")}; details=${JSON.stringify(body.details ?? {})}`);
  }
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, "ISSUE-");
  await expect(page.getByLabel("서머리")).toHaveValue(`${issueSummary} edited`);
  recordAssertion(receipts, "issues-attachments", "execution-link-and-edit-persist");

  for (const fixturePath of fixturePaths) {
    const name = fixturePath.split("/").at(-1);
    const download = page.waitForEvent("download");
    await page.getByLabel(`${name} 다운로드`).click();
    const received = await download;
    assert.equal(received.suggestedFilename(), name);
    const receivedPath = await received.path();
    assert(receivedPath, `download path missing: ${name}`);
    assert.deepEqual(await readFile(receivedPath), await readFile(fixturePath));
  }
  for (const name of fixturePaths.map((path) => path.split("/").at(-1))) {
    await page.getByLabel(`${name} 삭제`).click();
    await expect(page.getByLabel(`${name} 삭제`)).toHaveCount(0);
  }
  recordAssertion(receipts, "issues-attachments", "two-files-one-chooser-download-byte-match-delete");
  await page.getByRole("button", { name: "삭제", exact: true }).click();
  const deleteDialog = page.getByRole("alertdialog", { name: "이슈 삭제" });
  await deleteDialog.getByRole("button", { name: "삭제", exact: true }).click();
  await expect(page).toHaveURL(/\/issues$/);
  await page.getByRole("tab", { name: "삭제됨" }).click();
  await page.getByRole("row").filter({ hasText: `${issueSummary} edited` }).click();
  await expect(page.getByText("이 이슈는 읽기 전용입니다.")).toBeVisible();
  await expect(page.getByRole("button", { name: "완전 삭제" })).toBeVisible();
  await page.getByRole("button", { name: "완전 삭제", exact: true }).click();
  const permanent = page.getByRole("alertdialog", { name: "이슈 완전 삭제 확인" });
  const permanentAction = permanent.getByRole("button", { name: "완전 삭제", exact: true });
  await expect(permanentAction).toBeDisabled();
  const issueKey = issuePath.split("/").at(-1);
  await permanent.getByLabel("완전 삭제 확인용 이슈 키").fill(`${issueKey} `);
  await expect(permanentAction).toBeDisabled();
  await permanent.getByLabel("완전 삭제 확인용 이슈 키").fill(issueKey.toLowerCase());
  await expect(permanentAction).toBeDisabled();
  await permanent.getByLabel("완전 삭제 확인용 이슈 키").fill(issueKey);
  await expect(permanentAction).toBeEnabled();
  await permanent.getByRole("button", { name: "취소", exact: true }).click();
  const restoreResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/restore"));
  await page.getByRole("button", { name: "복구", exact: true }).click();
  assert.equal((await restoreResponse).status(), 200, "issue restore API status");
  await expect(page.getByText("이 이슈는 읽기 전용입니다.")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "변경사항 저장" })).toBeVisible();
  await page.goto(`${localOriginFromPage(page)}${issuePath}`, { waitUntil: "domcontentloaded" });
  await assertStablePage(page, "ISSUE-");
  recordAssertion(receipts, "deleted-permanent", "issue-delete-permanent-gate-and-restore");
  return { issuePath, issueKey };
}

async function exerciseCycleLifecycle(page, visit, cycle, receipts) {
  const { cyclePath, cycleName, executionPath, cycleId, pinnedVersion, pinnedVersionId } = cycle;
  await visit(cyclePath, cycleName);
  await page.getByRole("button", { name: "사이클 완료", exact: true }).click();
  await expect(page.getByRole("button", { name: "다시 열기", exact: true })).toBeVisible();
  assert.equal((await productGet(page, `/api/cycles/${cycleId}`)).status, "COMPLETED");
  await visit(executionPath, cycleName);
  await expect(page.getByText("완료된 사이클은 스텝 결과와 이슈 연결을 읽기 전용으로 표시합니다.", { exact: false })).toBeVisible();
  for (const tab of await page.getByRole("tab").all()) await expect(tab).toBeDisabled();
  await expect(page.getByRole("button", { name: "+ 이슈 생성" })).toBeDisabled();
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, cycleName);
  recordAssertion(receipts, "cycles", "completed-execution-is-read-only");

  await visit(cyclePath, cycleName);
  await page.getByRole("button", { name: "다시 열기", exact: true }).click();
  await expect(page.getByRole("button", { name: "사이클 완료", exact: true })).toBeVisible();
  assert.equal((await productGet(page, `/api/cycles/${cycleId}`)).status, "IN_PROGRESS");
  await visit(executionPath, cycleName);
  await expect(page.getByRole("tab", { name: /^WEB/ })).toBeEnabled();
  const reopenedItems = await productGet(page, `/api/cycles/${cycleId}/items?limit=100`);
  assert.equal(reopenedItems.items[0].pinnedVersion.version, pinnedVersion);
  assert.equal(reopenedItems.items[0].pinnedVersionId, pinnedVersionId);
  assert.equal(reopenedItems.items[0].latestVersion.version > pinnedVersion, true);
  recordAssertion(receipts, "cycles", "reopen-restores-entry-and-preserves-pinned-version");

  await visit(cyclePath, cycleName);
  await expect(page.getByRole("button", { name: "삭제", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "삭제", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "삭제", exact: true }).click();
  await expect(page).toHaveURL(/\/cycles$/);
  await page.getByRole("tab", { name: "삭제됨" }).click();
  await page.getByRole("link", { name: "상세", exact: true }).click();
  await assertStablePage(page, cycleName);
  await expect(page.getByText("이 사이클은 읽기 전용입니다.")).toBeVisible();
  await page.getByRole("button", { name: "완전 삭제", exact: true }).click();
  const permanent = page.getByRole("alertdialog", { name: "사이클 완전 삭제 확인" });
  const permanentAction = permanent.getByRole("button", { name: "완전 삭제", exact: true });
  await expect(permanentAction).toBeDisabled();
  await permanent.getByLabel("완전 삭제 확인용 사이클명").fill(`${cycleName} `);
  await expect(permanentAction).toBeDisabled();
  await permanent.getByLabel("완전 삭제 확인용 사이클명").fill(cycleName);
  await expect(permanentAction).toBeEnabled();
  await permanent.getByRole("button", { name: "취소", exact: true }).click();
  const cycleRestoreResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/restore"));
  await page.getByRole("button", { name: "복구", exact: true }).click();
  assert.equal((await cycleRestoreResponse).status(), 200, "cycle restore API status");
  await expect(page).toHaveURL(/\/cycles$/);
  await page.goto(`${localOriginFromPage(page)}${cyclePath}`, { waitUntil: "domcontentloaded" });
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, cycleName);
  await expect(page.getByText("이 사이클은 읽기 전용입니다.")).toHaveCount(0);
  recordAssertion(receipts, "cycles", "cycle-delete-permanent-gate-and-restore");
  recordAssertion(receipts, "deleted-permanent", "cycle-delete-permanent-gate-and-restore");
}

async function exerciseReadRoutes(page, visit, cycle, receipts) {
  await visit("/slack-work-items", "Slack Work Items");
  const slack = await productGet(page, "/api/slack-work-items?limit=25");
  assert.equal(slack.items.length, 2, "expected the two deterministic local safe-state Slack items");
  const slackJson = canonicalSafeText(slack);
  assert(!/(?:https?:\/\/|Bearer\s|xox[baprs]-|sk-[A-Za-z0-9_-]{12,})/i.test(slackJson), "Slack safe-state response exposed a URL or credential-like value");
  await page.locator(".tm-slack-work-item-row").first().click();
  await assertStablePage(page, "Slack 요청 상세");
  const slackDetailPath = new URL(page.url()).pathname;
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, "Slack 요청 상세");
  recordAssertion(receipts, "slack-work-items", "local-seeded-provenance-is-credential-free");

  await visit("/knowledge", "QA Knowledge");
  const knowledge = await productGet(page, "/api/knowledge/nodes?limit=25");
  assert.equal(knowledge.items.length, 1, "expected the quality candidate's deterministic local knowledge node");
  assert(!/(?:https?:\/\/|Bearer\s|xox[baprs]-|sk-[A-Za-z0-9_-]{12,})/i.test(canonicalSafeText(knowledge)), "knowledge safe-state response exposed a URL or credential-like value");
  await expect(page.getByText("Wave 10 local quality candidate", { exact: false }).first()).toBeVisible();
  recordAssertion(receipts, "knowledge", "authoritative-local-knowledge-safe-state");

  await visit("/integrations", "Integrations");
  const [storage, slackStatus] = await Promise.all([
    productGet(page, "/api/integrations/object-storage/status"),
    productGet(page, "/api/integrations/slack/status"),
  ]);
  assert.deepEqual({ privacy: storage.privacy, status: storage.status, uploadTransport: storage.uploadTransport, downloadTransport: storage.downloadTransport, scanPolicy: storage.scanPolicy }, {
    privacy: "PRIVATE", status: "READY", uploadTransport: "PRESIGNED_PUT", downloadTransport: "BACKEND_PROXY", scanPolicy: "FAIL_CLOSED_CLAMAV",
  });
  await expect(page.getByText("비공개", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("FAIL_CLOSED_CLAMAV", { exact: true })).toBeVisible();
  recordAssertion(receipts, "integrations", "object-storage-private-ready-boundaries-visible");
  assert.equal(slackStatus.connectionStatus, "DISCONNECTED");
  assert.equal(slackStatus.privateDefaultDeny, true);
  await expect(page.getByText("연결 해제됨", { exact: true })).toBeVisible();
  recordAssertion(receipts, "integrations", "slack-disabled-boundary-visible");

  await visit("/generation/failures", "자동 생성 실패");
  const failures = await productGet(page, "/api/generation-failures?limit=25");
  assert.equal(failures.items.length, 1, "deterministic terminal generation failure seed is missing");
  const failed = failures.items[0];
  assert.equal(failed.failure.code, "GENERATION_CONFIG_INVALID");
  assert.equal(failed.failure.finality, "TERMINAL_NON_RETRYABLE");
  assert.equal(failed.failure.retryable, false);
  assert.equal(failed.actionability, "OPERATOR_RETRY_ALLOWED");
  await expect(page.locator(".tm-generation-failure-workspace code").filter({ hasText: "GENERATION_CONFIG_INVALID" }).first()).toBeVisible();
  await expect(page.locator(".tm-generation-failure__finality").filter({ hasText: "재시도 불가" }).first()).toBeVisible();
  recordAssertion(receipts, "generation-failure-safe", "terminal-nonretryable-safe-detail-visible");
  const detail = await productGet(page, `/api/generation-failures/${failed.failedJobId}/attempt-history`);
  assert.equal(detail.attemptHistory.length, 1);
  assert.equal(detail.timeline.length, 1);
  assert.equal(detail.timeline[0].eventType, "JOB_FAILED");
  await expect(page.locator(".tm-generation-failure__attempts tbody tr")).toHaveCount(1);
  await expect(page.locator(".tm-generation-failure__timeline li")).toHaveCount(1);
  recordAssertion(receipts, "generation-failure-safe", "attempt-and-append-only-timeline-visible");
  const failureText = await page.locator(".tm-generation-failure-workspace").innerText();
  assert(!failureText.includes("LOCAL_DISABLED") && !failureText.includes("NO_EXTERNAL_CALL") && !failureText.includes("Safe local deterministic failure"));
  await expect(page.getByRole("button", { name: "조건을 추가해 재시도" })).toBeVisible();
  recordAssertion(receipts, "generation-failure-safe", "raw-provider-absent-and-operator-retry-boundary-visible");

  await visit("/generation/quality-review", "품질 후보 검토");
  const candidates = await productGet(page, "/api/generation-quality/review-candidates?limit=25");
  assert.equal(candidates.items.length, 1, "deterministic local quality-review candidate seed is missing");
  const candidate = candidates.items[0];
  assert.equal(candidate.status, "UNREVIEWED");
  assert.equal(candidate.reviewable, true);
  assert.equal(candidate.materialization, "COMPLETE");
  assert.equal(candidate.certificationStatus, "NOT_REVIEWED");
  assert.equal(candidate.releaseCertified, false);
  assert.equal(candidate.privacy.status, "CLEAN");
  assert.equal(candidate.privacy.containsRawSourceText, false);
  assert.equal(candidate.counts.generationRoots, 1);
  assert.equal(candidate.counts.generatedCases, 5);
  await page.locator(".tm-quality-review-list li button").first().click();
  await expect(page.locator(".tm-quality-review-sealed")).toContainText("VERIFIED E4 REVIEW ENVELOPE");
  await expect(page.getByText("미검토 · 0/2", { exact: true }).last()).toBeVisible();
  await expect(page.getByLabel("Reviewer token")).toHaveValue("");
  await expect(page.getByRole("button", { name: "승인 검토" })).toBeDisabled();
  const boundary = page.getByRole("note", { name: "release 인증 경계" });
  await expect(boundary).toContainText("CANDIDATE / UNREVIEWED");
  recordAssertion(receipts, "quality-review-safe", "candidate-unreviewed-boundary-visible");
  const storageKeys = await page.evaluate(() => ({
    local: Object.keys(localStorage).filter((key) => /token|review/i.test(key)),
    session: Object.keys(sessionStorage).filter((key) => /token|review/i.test(key)),
    search: window.location.search,
  }));
  assert.deepEqual(storageKeys, { local: [], session: [], search: "" });
  recordAssertion(receipts, "quality-review-safe", "reviewer-token-storage-and-url-remain-empty");
  await expect(boundary).toContainText("release 승인이나 provider live 인증이 아닙니다");
  const qualityText = await page.locator(".tm-quality-review-workspace").innerText();
  assert(!qualityText.includes("releaseCertified=true"));
  assert(!qualityText.includes("LOCAL_DISABLED") && !qualityText.includes("NO_EXTERNAL_CALL"));
  recordAssertion(receipts, "quality-review-safe", "release-certification-not-claimed");

  await visit(cycle.cyclePath, cycle.cycleName);
  await visit("/reports", "Reports");
  await visit("/", "QA Dashboard");
  return { slackDetailPath };
}

async function exerciseDashboardAndReports(page, visit, cycle, receipts) {
  const loadAggregates = async () => {
    const [overview, reports] = await Promise.all([
      productGet(page, "/api/dashboard/overview"),
      productGet(page, "/api/reports/cycles?limit=200"),
    ]);
    const totals = reports.items.reduce((sum, row) => {
      sum.caseCount += row.caseCount;
      sum.platformSlotCount += row.platformSlotCount;
      for (const status of ["PASS", "FAIL", "BLOCKED", "UNEXECUTED"]) sum.execution[status] += row.execution[status];
      assert.equal(row.execution.total, row.execution.PASS + row.execution.FAIL + row.execution.BLOCKED + row.execution.UNEXECUTED);
      assert.equal(row.execution.executed, row.execution.PASS + row.execution.FAIL);
      assert.equal(row.execution.terminal, row.execution.PASS + row.execution.FAIL + row.execution.BLOCKED);
      assert.equal(row.execution.passRate, row.execution.executed ? row.execution.PASS / row.execution.executed : 0);
      return sum;
    }, { caseCount: 0, platformSlotCount: 0, execution: { PASS: 0, FAIL: 0, BLOCKED: 0, UNEXECUTED: 0 } });
    const expectedExecution = {
      ...totals.execution,
      aggregateStatus: totals.execution.FAIL ? "FAIL" : totals.execution.PASS ? "PASS" : "UNEXECUTED",
      executed: totals.execution.PASS + totals.execution.FAIL,
      passRate: totals.execution.PASS / Math.max(totals.execution.PASS + totals.execution.FAIL, 1),
      terminal: totals.execution.PASS + totals.execution.FAIL + totals.execution.BLOCKED,
      total: totals.execution.PASS + totals.execution.FAIL + totals.execution.BLOCKED + totals.execution.UNEXECUTED,
    };
    assert.equal(overview.cycleCount, reports.total);
    assert.equal(overview.caseCount, totals.caseCount);
    assert.equal(overview.platformSlotCount, totals.platformSlotCount);
    assert.deepEqual(overview.execution, expectedExecution);
    const selected = reports.items.find((row) => row.cycleId === cycle.cycleId);
    assert(selected, "created cycle is absent from Reports API");
    assert.equal(selected.cycleName, cycle.cycleName);
    return { overview, reports, selected };
  };

  await visit("/", "QA Dashboard");
  const beforeReload = await loadAggregates();
  await assertDashboardKpis(page, beforeReload.overview);
  recordAssertion(receipts, "dashboard", "dashboard-kpis-match-product-api");
  await page.reload({ waitUntil: "domcontentloaded" });
  await assertStablePage(page, "QA Dashboard");
  const afterReload = await loadAggregates();
  await assertDashboardKpis(page, afterReload.overview);
  assert.deepEqual(withoutGeneratedAt(afterReload.overview), withoutGeneratedAt(beforeReload.overview));
  assert.deepEqual(withoutGeneratedAt(afterReload.reports), withoutGeneratedAt(beforeReload.reports));
  recordAssertion(receipts, "dashboard", "dashboard-hard-reload-preserves-kpis");
  recordAssertion(receipts, "reports", "reports-dashboard-api-aggregates-reconcile");

  await visit("/reports", "Reports");
  const reportsRow = page.locator(".tm-report-table tbody tr").filter({ hasText: cycle.cycleName });
  await expect(reportsRow).toHaveCount(1);
  const cells = await reportsRow.locator("td").allTextContents();
  const selected = afterReload.selected;
  assert.equal(Number(cells[3].trim()), selected.caseCount);
  assert.equal(Number(cells[5].trim()), selected.execution.UNEXECUTED);
  assert.equal(Number(cells[6].trim()), selected.execution.PASS);
  assert.equal(Number(cells[7].trim()), selected.execution.FAIL);
  assert.equal(Number(cells[8].trim()), selected.execution.BLOCKED);
  const summary = page.getByRole("region", { name: "필터 결과 요약" });
  await expect(summary.getByText(String(afterReload.reports.total), { exact: true }).first()).toBeVisible();
  await expect(summary.getByText(String(afterReload.overview.execution.PASS), { exact: true }).first()).toBeVisible();
  recordAssertion(receipts, "reports", "reports-ui-selected-cycle-counts-match");
}

async function assertDashboardKpis(page, overview) {
  const kpis = page.getByRole("region", { name: "실행 핵심 지표" });
  await expect(kpis).toBeVisible();
  const values = {
    "미실행": overview.execution.UNEXECUTED,
    "실패": overview.execution.FAIL,
    "BLOCKED": overview.execution.BLOCKED,
  };
  for (const [label, expected] of Object.entries(values)) {
    const card = kpis.locator("article").filter({ has: page.getByText(label, { exact: true }) });
    await expect(card.locator("strong")).toHaveText(String(expected));
  }
}

async function exerciseMobileSurfaces(page, visit, state, findings, receipts) {
  const surfaces = [
    ["/", "QA Dashboard"],
    ["/test-cases", "Test Library"],
    [`/work-items/${state.workA}`, state.workA],
    [`/test-cases/${state.caseA}`, "Test case"],
    ["/test-cases/new", "테스트케이스 만들기"],
    ["/cycles", "Test Cycles"],
    ["/cycles/new", "테스트 사이클"],
    [state.cyclePath, state.cycleName],
    [state.executionPath, state.cycleName],
    ["/issues", "Issues"],
    [state.issuePath, state.issueKey],
    ["/issues/new", "이슈"],
    ["/reports", "Reports"],
    ["/slack-work-items", "Slack Work Items"],
    [state.slackDetailPath, "Slack 요청 상세"],
    ["/generation/failures", "자동 생성 실패"],
    ["/generation/quality-review", "품질 후보 검토"],
    ["/knowledge", "QA Knowledge"],
    ["/integrations", "Integrations"],
  ];
  for (const [path, heading] of surfaces) {
    await visit(path, heading);
    await assertMobileLayout(page);
    findings.visitedMobileSurfaces.push(routeKind(path));
    if (path === "/generation/failures") {
      await page.locator(".tm-generation-failure__list li button").first().click();
      await expect(page.locator(".tm-generation-failure__attempts tbody tr")).toHaveCount(1);
      await assertMobileLayout(page);
      findings.visitedMobileSurfaces.push("detail:generation-failure");
    }
    if (path === "/generation/quality-review") {
      await page.locator(".tm-quality-review-list li button").first().click();
      await expect(page.locator(".tm-quality-review-sealed")).toBeVisible();
      await assertMobileLayout(page);
      findings.visitedMobileSurfaces.push("detail:quality-review-candidate");
    }
  }
  assert.deepEqual([...new Set(findings.visitedMobileSurfaces)].sort(), [
    ...surfaces.map(([path]) => routeKind(path)),
    "detail:generation-failure",
    "detail:quality-review-candidate",
  ].sort());
  recordAssertion(receipts, "mobile-390", "all-required-mobile-routes-have-no-overflow");
  recordAssertion(receipts, "mobile-390", "mobile-detail-form-execution-safe-state-controls-reachable");

  await visit(state.executionPath, state.cycleName);
  await page.locator('.tm-player-steps article[role="button"]').first().click();
  await page.getByRole("tab", { name: /^WEB/ }).click();
  await page.getByRole("button", { name: "+ 이슈 생성" }).click();
  const issueDialog = page.getByRole("dialog", { name: "이슈 생성" });
  await expect(issueDialog).toBeVisible();
  await assertMobileLayout(page);
  await expect(issueDialog.getByRole("button", { name: "이슈 생성", exact: true })).toBeEnabled();
  await issueDialog.getByLabel("이슈 생성 닫기").click();
  findings.visitedMobileSurfaces.push("modal:execution-issue-create");
  recordAssertion(receipts, "mobile-390", "mobile-modal-primary-actions-reachable");
}

async function productGet(page, path) {
  const result = await page.evaluate(async (requestPath) => {
    const response = await fetch(requestPath, { headers: { accept: "application/json" } });
    return { status: response.status, contentType: response.headers.get("content-type"), body: await response.json() };
  }, path);
  assert.equal(result.status, 200, `product GET failed: ${path}`);
  assert(result.contentType?.startsWith("application/json"), `product GET returned non-JSON: ${path}`);
  return result.body;
}

function withoutGeneratedAt(value) {
  if (Array.isArray(value)) return value.map(withoutGeneratedAt);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).filter(([key]) => key !== "generatedAt").map(([key, item]) => [key, withoutGeneratedAt(item)]));
  return value;
}

function canonicalSafeText(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalSafeText).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalSafeText(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

async function exerciseIdempotencyReplay(page, runId) {
  const result = await page.evaluate(async (keySuffix) => {
    const currentResponse = await fetch("/api/workspace/settings");
    const current = await currentResponse.json();
    const idempotencyKey = `wave10-replay-${keySuffix}`;
    const body = JSON.stringify({
      baseVersion: current.version,
      generationQuietMinutes: current.generationQuietMinutes,
    });
    const request = () => fetch("/api/workspace/settings", {
      method: "PATCH",
      headers: {
        "content-type": "application/json",
        "Idempotency-Key": idempotencyKey,
        "X-Actor-Label": "Wave 10 Browser UAT",
      },
      body,
    });
    const first = await request();
    const firstBody = await first.json();
    const second = await request();
    const secondBody = await second.json();
    return {
      firstStatus: first.status,
      secondStatus: second.status,
      sameId: firstBody.id === secondBody.id,
      sameVersion: firstBody.version === secondBody.version,
    };
  }, runId);
  assert.equal(result.firstStatus, 200);
  assert.equal(result.secondStatus, 200);
  assert.equal(result.sameId, true);
  assert.equal(result.sameVersion, true);
}

function routeKind(path) {
  return path
    .replace(/cycle_[^/]+/g, ":cycleId")
    .replace(/TC-\d+/g, ":caseKey")
    .replace(/ISSUE-\d+/g, ":issueKey")
    .replace(/WORK-\d+/g, ":workItemKey")
    .replace(/slack_work_item_[a-f0-9]+/g, ":workItemId");
}

function sanitizePathClass(pathname) {
  return routeKind(pathname)
    .replace(/\b([a-z][a-z0-9_]*)_[a-f0-9]{24,}\b/g, (_, prefix) => `:${prefix}Id`)
    .replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/g, ":uuid");
}

function waitForStepSave(page) {
  return page.waitForResponse((response) => {
    const request = response.request();
    const path = new URL(response.url()).pathname;
    return request.method() !== "GET" && path.includes("/executions/") && path.endsWith("/steps");
  }, { timeout: TIMEOUT });
}

function localOriginFromPage(page) {
  return new URL(page.url()).origin;
}

async function fileSha256(path) {
  return simpleSha256(await readFile(path));
}

function simpleSha256(value) {
  return createHash("sha256").update(value).digest("hex");
}
