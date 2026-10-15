import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, createApiClient } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createApiClient", () => {
  it("does not expose the removed cycle-item latest diff operation", () => {
    expect(createApiClient("http://localhost:8000")).not.toHaveProperty(
      "getCycleItemLatestVersionDiff",
    );
  });

  it("does not expose the removed Reports CSV operations", () => {
    const client = createApiClient("http://localhost:8000");

    expect(client).not.toHaveProperty("downloadCycleReportCsv");
    expect(client).not.toHaveProperty("exportCycleReportCsv");
    expect(client).toHaveProperty("listCycleReports");
  });

  it("returns the typed health payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ service: "api", status: "ok", version: "0.1.0" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(createApiClient("http://localhost:8000/").getHealth()).resolves.toEqual({
      service: "api",
      status: "ok",
      version: "0.1.0",
    });
    expect(fetch).toHaveBeenCalledWith("http://localhost:8000/api/health", {
      signal: undefined,
    });
  });

  it("sends the reviewer token only in the dedicated quality-review header", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    const payload = {
      idempotencyKey: "quality-decision-001",
      decision: "APPROVE" as const,
      expectedArtifactFingerprint: `sha256:${"a".repeat(64)}`,
      reasonCode: null,
    };

    await createApiClient("http://localhost:8000").createGenerationQualityReviewDecision(
      `review-candidate:${"1".repeat(64)}`,
      payload,
      { reviewerToken: "raw-reviewer-token", actorLabel: "spoofed actor" } as {
        reviewerToken: string;
        actorLabel: string;
      },
    );

    expect(fetch).toHaveBeenCalledWith(
      `http://localhost:8000/api/generation-quality/review-candidates/review-candidate%3A${"1".repeat(64)}/decisions`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "Idempotency-Key": "quality-decision-001",
          "X-Quality-Reviewer-Token": "raw-reviewer-token",
        },
        body: JSON.stringify(payload),
        signal: undefined,
      },
    );
    const requestBody = vi.mocked(fetch).mock.calls[0]?.[1]?.body;
    expect(requestBody).not.toContain('"reviewerToken"');
  });

  it("encodes quality-review cursor and safe server filters without reviewer credentials", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ items: [], nextCursor: null }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    const candidateKey = `review-candidate:${"1".repeat(64)}`;

    await createApiClient("http://localhost:8000").listGenerationQualityReviewCandidates({
      limit: 25,
      cursor: candidateKey,
      status: "PENDING_APPROVAL",
      classification: "EXACT_DUPLICATE_TRAP",
      materialization: "COMPLETE",
      search: candidateKey,
    });

    const [requestUrl, request] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    const url = new URL(requestUrl);
    expect(url.pathname).toBe("/api/generation-quality/review-candidates");
    expect(url.searchParams.get("cursor")).toBe(candidateKey);
    expect(url.searchParams.get("status")).toBe("PENDING_APPROVAL");
    expect(url.searchParams.get("classification")).toBe("EXACT_DUPLICATE_TRAP");
    expect(url.searchParams.get("materialization")).toBe("COMPLETE");
    expect(url.searchParams.get("search")).toBe(candidateKey);
    expect(request.headers).toBeUndefined();
  });

  it("exposes non-success status codes", async () => {
    const error = {
      code: "SERVICE_UNAVAILABLE",
      message: "Database unavailable",
      details: {},
      traceId: "trace_api_001",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(error), {
          status: 503,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(createApiClient("").getHealth()).rejects.toEqual(
      new ApiClientError("API health request failed", 503, error),
    );
  });

  it("sends generated folder payloads and idempotency headers", async () => {
    const response = {
      operation: "CREATE",
      rootFolderId: "folder_members",
      affectedFolderIds: ["folder_members"],
      rootVersion: 1,
      auditId: "audit_create_members",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(response), {
          status: 201,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(
      createApiClient("http://localhost:8000").createFolder(
        { parentId: null, name: "회원", sortOrder: 0 },
        { idempotencyKey: "create-members-001", traceId: "trace_create_members" },
      ),
    ).resolves.toEqual(response);
    expect(fetch).toHaveBeenCalledWith("http://localhost:8000/api/folders", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "Idempotency-Key": "create-members-001",
        "X-Trace-Id": "trace_create_members",
      },
      body: JSON.stringify({ parentId: null, name: "회원", sortOrder: 0 }),
      signal: undefined,
    });
  });

  it("encodes audit cursor queries", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ items: [], nextCursor: null, total: 0 }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        ),
      ),
    );

    await createApiClient("http://localhost:8000").listAuditLogs({
      limit: 25,
      cursor: "cursor value",
      entityType: "FOLDER",
    });
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/audit-logs?limit=25&cursor=cursor+value&entityType=FOLDER",
      { signal: undefined },
    );
  });

  it("encodes compound test-case filters as repeated query parameters", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ items: [], nextCursor: null, total: 0 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await createApiClient("http://localhost:8000").listTestCases({
      categoryIds: ["folder_orders", "folder_payments"],
      statuses: ["ACTIVE"],
      labels: ["payment", "smoke"],
      labelMatch: "ALL",
      limit: 25,
    });
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/test-cases?categoryIds=folder_orders&categoryIds=folder_payments&statuses=ACTIVE&labels=payment&labels=smoke&labelMatch=ALL&limit=25",
      { signal: undefined },
    );
  });

  it("sends typed work-item create, detail and optimistic update requests", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )),
    );
    const client = createApiClient("http://localhost:8000");
    const create = {
      key: "WORK-531",
      categoryId: "folder_orders",
      summary: "Payment reliability",
      description: "",
    };
    await client.createWorkItem(create, { idempotencyKey: "work-create-531" });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/work-items",
      expect.objectContaining({ method: "POST", body: JSON.stringify(create) }),
    );
    await client.getWorkItem("WORK-531");
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/work-items/WORK-531",
      { signal: undefined },
    );
    await client.updateWorkItem("WORK-531", {
      baseResourceVersion: 2,
      categoryId: "folder_orders",
      summary: "Payment reliability revised",
      changeReason: "Detail edit",
      description: "",
    });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/work-items/WORK-531",
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("sends typed version restore requests with mutation headers", async () => {
    const response = {
      testCase: {},
      restoredFromVersion: 2,
      auditId: "audit_restore_2",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(response), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await createApiClient("http://localhost:8000").restoreTestCaseVersion(
      "case_checkout",
      2,
      {
        baseVersion: 4,
        changeReason: "Restore approved script.",
        source: { type: "UI" },
      },
      { idempotencyKey: "restore-case-2" },
    );
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/test-cases/case_checkout/restore/2",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "Idempotency-Key": "restore-case-2",
        },
        body: JSON.stringify({
          baseVersion: 4,
          changeReason: "Restore approved script.",
          source: { type: "UI" },
        }),
        signal: undefined,
      },
    );
  });

  it("reads deleted details and sends restore and permanent-delete mutations", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({}), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        ),
      ),
    );
    const client = createApiClient("http://localhost:8000");

    await client.getTestCase("case_deleted", undefined, true);
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/test-cases/case_deleted?includeDeleted=true",
      { signal: undefined },
    );
    await client.getCycle("cycle_deleted", undefined, true);
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/cycles/cycle_deleted?includeDeleted=true",
      { signal: undefined },
    );
    await client.listCycleItems("cycle_deleted", { includeDeleted: true });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/cycles/cycle_deleted/items?includeDeleted=true",
      { signal: undefined },
    );
    await client.getIssue("issue_deleted", undefined, true);
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/issues/issue_deleted?includeDeleted=true",
      { signal: undefined },
    );
    await client.listIssueLinks("issue_deleted", { includeDeleted: true });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/issues/issue_deleted/links?includeDeleted=true",
      { signal: undefined },
    );
    await client.listIssueComments("issue_deleted", { includeDeleted: true });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/issues/issue_deleted/comments?includeDeleted=true",
      { signal: undefined },
    );
    await client.listIssueHistory("issue_deleted", { includeDeleted: true });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/issues/issue_deleted/history?includeDeleted=true",
      { signal: undefined },
    );

    await client.restoreIssue("issue_deleted", {
      baseResourceVersion: 2,
      reason: "Restored after review.",
    });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/issues/issue_deleted/restore",
      expect.objectContaining({ method: "POST" }),
    );
    await client.permanentlyDeleteTestCase("case_deleted", {
      baseResourceVersion: 2,
      reason: "Retention period expired.",
      source: { type: "UI" },
    });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/test-cases/case_deleted/permanent",
      expect.objectContaining({ method: "DELETE" }),
    );
    await client.permanentlyDeleteCycle("cycle_deleted", {
      baseResourceVersion: 2,
      reason: "Retention period expired.",
    });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/cycles/cycle_deleted/permanent",
      expect.objectContaining({ method: "DELETE" }),
    );
    await client.permanentlyDeleteIssue("issue_deleted", {
      baseResourceVersion: 2,
      reason: "Retention period expired.",
    });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/issues/issue_deleted/permanent",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("encodes cycle filters and scope preview queries", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ items: [], nextCursor: null, total: 0 }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        ),
      ),
    );

    const client = createApiClient("http://localhost:8000");
    await client.listCycles({
      statuses: ["PLANNED", "IN_PROGRESS"],
      scopeTypes: ["WORK_ITEM"],
      deletedOnly: true,
      limit: 20,
    });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/cycles?statuses=PLANNED&statuses=IN_PROGRESS&scopeTypes=WORK_ITEM&deletedOnly=true&limit=20",
      { signal: undefined },
    );

    await client.previewCycleScope({
      scopeType: "WORK_ITEM",
      workItemId: "work_item_checkout",
      limit: 10,
    });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/cycles/scope-preview?scopeType=WORK_ITEM&workItemId=work_item_checkout&limit=10",
      { signal: undefined },
    );

    await client.previewCycleScope({
      scopeType: "LABEL",
      label: "smoke",
      labels: ["smoke", "regression"],
      limit: 50,
    });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/cycles/scope-preview?scopeType=LABEL&label=smoke&labels=smoke&labels=regression&limit=50",
      { signal: undefined },
    );
  });

  it("sends the real cycle delete mutation with version and reason", async () => {
    const response = {
      operation: "DELETE",
      cycleId: "cycle_checkout",
      deletedAt: "2026-08-21T00:00:00Z",
      auditId: "audit_cycle_delete",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ));

    await expect(createApiClient("http://localhost:8000").deleteCycle(
      "cycle_checkout",
      { baseResourceVersion: 3, reason: "사용자 요청" },
      { idempotencyKey: "delete-cycle-3" },
    )).resolves.toEqual(response);
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/cycles/cycle_checkout",
      expect.objectContaining({
        method: "DELETE",
        headers: expect.objectContaining({ "Idempotency-Key": "delete-cycle-3" }),
        body: JSON.stringify({ baseResourceVersion: 3, reason: "사용자 요청" }),
      }),
    );
  });

  it("restores a deleted cycle with optimistic concurrency metadata", async () => {
    const response = {
      operation: "RESTORE",
      cycle: {
        id: "cycle_checkout",
        name: "Checkout",
        startDate: "2026-08-25",
        status: "IN_PROGRESS",
        scope: { type: "LABEL", label: "smoke", labels: ["smoke"] },
        resourceVersion: 4,
        itemCount: 2,
        platformResultCount: 1,
        updatedAt: "2026-08-25T01:00:00Z",
        deletedAt: null,
      },
      auditId: "audit_cycle_restore",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ));

    await expect(createApiClient("http://localhost:8000").restoreCycle(
      "cycle_checkout",
      { baseResourceVersion: 3, reason: "사용자 복구" },
      { idempotencyKey: "restore-cycle-3" },
    )).resolves.toEqual(response);
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/cycles/cycle_checkout/restore",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "restore-cycle-3" }),
        body: JSON.stringify({ baseResourceVersion: 3, reason: "사용자 복구" }),
      }),
    );
  });

  it("sends append-only retry mutations with idempotency metadata", async () => {
    const response = { previousAttempt: {}, attempt: {} };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(response), {
          status: 201,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await createApiClient("http://localhost:8000").retryExecutionAttempt(
      "cycle_item_checkout",
      { previousAttemptId: "attempt_web_1", note: "Verify the fixed build." },
      { idempotencyKey: "retry-web-2", actorLabel: "QA Kim" },
    );
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/cycle-items/cycle_item_checkout/attempts/retry",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "Idempotency-Key": "retry-web-2",
          "X-Actor-Label": "QA Kim",
        },
        body: JSON.stringify({
          previousAttemptId: "attempt_web_1",
          note: "Verify the fixed build.",
        }),
        signal: undefined,
      },
    );
  });

  it("reads and records data-driven step execution results", async () => {
    const stepExecution = {
      cycleId: "cycle_checkout",
      cycleItemId: "cycle_item_checkout",
      pinnedVersionId: "case_version_checkout",
      platform: "WEB",
      attemptId: null,
      attemptNo: null,
      status: "UNEXECUTED",
      executorLabel: null,
      startedAt: null,
      completedAt: null,
      recordedStepCount: 0,
      totalStepCount: 2,
      steps: [],
    };
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify(stepExecution), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ));
    vi.stubGlobal("fetch", fetchMock);
    const client = createApiClient("http://localhost:8000");

    await client.getStepExecution("cycle_item_checkout", "WEB");
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/cycle-items/cycle_item_checkout/executions/WEB/steps",
      { signal: undefined },
    );

    await client.recordStepExecutionResults(
      "cycle_item_checkout",
      "WEB",
      {
        results: [
          { sequence: 1, status: "PASS" },
          { sequence: 2, status: "BLOCKED", note: "테스트 계정 잠김" },
        ],
        executorLabel: "QA Web UI",
      },
      { actorLabel: "QA Web UI" },
    );
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/cycle-items/cycle_item_checkout/executions/WEB/steps",
      expect.objectContaining({
        method: "PATCH",
        headers: expect.objectContaining({ "X-Actor-Label": "QA Web UI" }),
        body: JSON.stringify({
          results: [
            { sequence: 1, status: "PASS" },
            { sequence: 2, status: "BLOCKED", note: "테스트 계정 잠김" },
          ],
          executorLabel: "QA Web UI",
        }),
      }),
    );
  });

  it("connects issue create and cycle-filtered list operations", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ items: [], nextCursor: null, total: 0 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    const client = createApiClient("http://localhost:8000");
    await client.listIssues({
      cycleIds: ["cycle_regression"],
      statuses: ["OPEN"],
      severities: ["CRITICAL"],
      deletedOnly: true,
    });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/issues?cycleIds=cycle_regression&statuses=OPEN&severities=CRITICAL&deletedOnly=true",
      { signal: undefined },
    );

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ id: "issue_1" }), {
        status: 201,
        headers: { "content-type": "application/json" },
      }),
    );
    await client.createIssue({
      idempotencyKey: "issue-create-001",
      summary: "Gateway failure",
      precondition: "A cart exists.",
      steps: [{ sequence: 1, action: "Pay", expectedResult: "Approved" }],
      expectedResult: "Approved",
      actualResult: "HTTP 500",
      severity: "CRITICAL",
      initialLinks: [],
    });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/issues",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "issue-create-001" }),
      }),
    );
  });

  it("uploads private attachments directly with a short-lived object URL", async () => {
    const objectUploadResponse = new Response(null, { status: 200 });
    const objectUploadBody = vi.spyOn(objectUploadResponse, "arrayBuffer");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          uploadToken: "signed-upload-token",
          objectKey: "attachments/issue/file",
          maxSizeBytes: 52_428_800,
          upload: {
            method: "PUT",
            url: "http://storage.local/tc/attachments/issue/file?signed=1",
            headers: { "content-type": "text/plain", "x-amz-meta-sha256": "checksum" },
            expiresAt: "2026-08-24T05:00:00Z",
          },
        }), { status: 201, headers: { "content-type": "application/json" } }),
      )
      .mockResolvedValueOnce(objectUploadResponse)
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ attachment: {}, auditId: "audit_1" }), {
          status: 201,
          headers: { "content-type": "application/json" },
        }),
      );
    vi.stubGlobal(
      "fetch",
      fetchMock,
    );
    await createApiClient("http://localhost:8000").uploadIssueAttachment(
      "issue_gateway",
      {
        idempotencyKey: "attachment-upload-001",
        owner: { type: "ISSUE", issueId: "issue_gateway" },
        filename: "gateway.log",
        declaredMimeType: "text/plain",
        declaredSizeBytes: 4,
      },
      new Blob(["fail"], { type: "text/plain" }),
    );
    const firstCall = fetchMock.mock.calls[0];
    expect(firstCall).toBeDefined();
    expect(firstCall?.[0]).toBe(
      "http://localhost:8000/api/issues/issue_gateway/attachment-upload-sessions",
    );
    expect(firstCall?.[1]?.headers).toEqual(expect.objectContaining({
      "content-type": "application/json",
      "Idempotency-Key": "attachment-upload-001",
    }));
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://storage.local/tc/attachments/issue/file?signed=1",
    );
    expect(fetchMock.mock.calls[1]?.[1]).toEqual(expect.objectContaining({
      method: "PUT",
      body: expect.any(Blob),
    }));
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "http://localhost:8000/api/attachment-upload-sessions/complete",
    );
    expect(objectUploadBody).toHaveBeenCalledOnce();
  });

  it("pages the metadata-only legacy attachment inventory", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          policy: "MIGRATION_REQUIRED",
          total: 0,
          items: [],
          nextCursor: null,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createApiClient("http://localhost:8000").getLegacyAttachmentInventory({
      cursor: "page-token",
      limit: 25,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/integrations/object-storage/legacy-attachments?cursor=page-token&limit=25",
      expect.objectContaining({ signal: undefined }),
    );
  });

  it("wires Slack allowlist and work-item operations from the generated contract", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ items: [], nextCursor: null }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    const client = createApiClient("http://localhost:8000/");
    await client.listSlackWorkItems({ channelId: "CQA123", limit: 20 });
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/slack-work-items?channelId=CQA123&limit=20",
      { signal: undefined },
    );

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          workspaceId: "TQA123",
          resourceVersion: 2,
          channels: [],
          privateDefaultDeny: true,
          updatedAt: "2026-08-21T00:00:00Z",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    const payload = {
      idempotencyKey: "slack-allowlist-001",
      workspaceId: "TQA123",
      expectedVersion: 1,
      channels: [{ channelId: "CQA123", visibility: "PUBLIC" as const }],
    };
    await client.updateSlackAllowlist(payload);
    expect(fetch).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/integrations/slack/allowlist",
      expect.objectContaining({
        method: "PUT",
        headers: expect.objectContaining({ "Idempotency-Key": "slack-allowlist-001" }),
        body: JSON.stringify(payload),
      }),
    );
  });

  it("wires Slack detail, event/job logs, sync and quiet-window mutations", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ));
    vi.stubGlobal("fetch", fetchMock);
    const client = createApiClient("http://localhost:8000");
    const workItemId = "slack_work_item_001";

    await client.getSlackIntegrationStatus();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/integrations/slack/status",
      { signal: undefined },
    );
    await client.getSlackWorkItem(workItemId);
    expect(fetchMock).toHaveBeenLastCalledWith(
      `http://localhost:8000/api/slack-work-items/${workItemId}`,
      { signal: undefined },
    );
    await client.listSlackEvents({ processingState: "DEAD", limit: 10 });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/slack-events?processingState=DEAD&limit=10",
      { signal: undefined },
    );
    await client.listSlackJobs({ state: "RETRY_WAIT", limit: 10 });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/slack-jobs?state=RETRY_WAIT&limit=10",
      { signal: undefined },
    );

    const sync = {
      idempotencyKey: "slack-sync-client-001",
      workspaceId: "TQA123",
      mode: "INCREMENTAL" as const,
      sinceCursor: "cursor-1",
    };
    await client.syncSlackThread(workItemId, sync);
    expect(fetchMock).toHaveBeenLastCalledWith(
      `http://localhost:8000/api/slack-work-items/${workItemId}/sync-thread`,
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": sync.idempotencyKey }),
        body: JSON.stringify(sync),
      }),
    );

    const quiet = {
      idempotencyKey: "slack-quiet-client-001",
      workItemId,
      lastThreadChangedAt: "2026-08-21T00:00:00Z",
      quietWindowSeconds: 600,
      sourceRevisionId: "slack_revision_001",
      expectedGeneration: 2,
    };
    await client.upsertSlackQuietWindow(workItemId, quiet);
    expect(fetchMock).toHaveBeenLastCalledWith(
      `http://localhost:8000/api/slack-work-items/${workItemId}/quiet-window`,
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": quiet.idempotencyKey }),
        body: JSON.stringify(quiet),
      }),
    );

    const jobId = "slack_job_001";
    const cancellation = {
      idempotencyKey: "slack-cancel-client-001",
      jobId,
      expectedGeneration: 2,
      reason: "MANUAL_CANCEL" as const,
    };
    await client.cancelSlackQuietWindow(jobId, cancellation);
    expect(fetchMock).toHaveBeenLastCalledWith(
      `http://localhost:8000/api/slack-jobs/${jobId}/cancel`,
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": cancellation.idempotencyKey }),
        body: JSON.stringify(cancellation),
      }),
    );
  });

  it("wires generated knowledge search, explorer, evidence, patch and jobs", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ items: [], nextCursor: null, total: 0 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = createApiClient("http://localhost:8000/");
    const nodeId = "knowledge_0123456789abcdef0123456789abcdef";

    await client.listKnowledgeNodes({ statuses: ["ACTIVE"], nodeTypes: ["FEATURE"] });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/knowledge/nodes?statuses=ACTIVE&nodeTypes=FEATURE",
      { signal: undefined },
    );
    await client.getKnowledgeNode(nodeId);
    expect(fetchMock).toHaveBeenLastCalledWith(
      `http://localhost:8000/api/knowledge/nodes/${nodeId}`,
      { signal: undefined },
    );
    await client.listKnowledgeNodeEvidence(nodeId);
    expect(fetchMock).toHaveBeenLastCalledWith(
      `http://localhost:8000/api/knowledge/nodes/${nodeId}/evidence`,
      { signal: undefined },
    );

    const search = {
      query: "password reset",
      statuses: ["ACTIVE" as const],
      sourceTypes: [],
      nodeTypes: [],
      featureId: nodeId,
      limit: 20,
      cursor: null,
    };
    await client.searchKnowledge(search);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/knowledge/search",
      expect.objectContaining({ method: "POST", body: JSON.stringify(search) }),
    );

    const explorer = {
      rootNodeId: nodeId,
      direction: "BOTH" as const,
      predicates: [],
      statuses: ["ACTIVE" as const],
      maxDepth: 5,
      limit: 20,
      cursor: null,
    };
    await client.exploreKnowledgeOntology(explorer);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/knowledge/explorer",
      expect.objectContaining({ body: JSON.stringify(explorer) }),
    );

    const patch = {
      targetType: "NODE" as const,
      targetId: nodeId,
      expectedVersion: 1,
      status: "ACTIVE" as const,
      effectiveAt: "2026-08-21T00:00:00Z",
      reason: "confirmed",
      evidenceIds: ["evidence_0123456789abcdef0123456789abcdef"],
      changedBy: "qa-admin",
    };
    await client.patchKnowledgeStatus(patch);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/knowledge/status",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify(patch) }),
    );

    const projection = { idempotencyKey: "knowledge-projection-1", limit: 100 };
    await client.createKnowledgeProjectionJob(projection);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/knowledge/jobs/projection",
      expect.objectContaining({
        headers: expect.objectContaining({ "Idempotency-Key": projection.idempotencyKey }),
      }),
    );
    const reindex = { idempotencyKey: "knowledge-reindex-1", nodeIds: [nodeId] };
    await client.createKnowledgeReindexJob(reindex);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://localhost:8000/api/knowledge/jobs/reindex",
      expect.objectContaining({ body: JSON.stringify(reindex) }),
    );
  });
});
