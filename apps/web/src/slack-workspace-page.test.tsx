import type { SlackWorkItemDetail } from "@tc/api-client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SlackWorkItemDetailView } from "./slack-workspace-page";

const detail: SlackWorkItemDetail = {
  workItem: {
    id: "wi-1",
    workspaceId: "T-1",
    channelId: "C-PUBLIC",
    rootMessageTs: "1724200000.000100",
    threadId: "thread-1",
    status: "ACTIVE",
    latestParseId: "parse-1",
    createdAt: "2026-08-21T01:00:00Z",
    updatedAt: "2026-08-21T01:04:00Z",
  },
  thread: {
    id: "thread-1",
    workItemId: "wi-1",
    rootMessageTs: "1724200000.000100",
    rootResolved: true,
    messageCount: 2,
    latestChangedAt: "2026-08-21T01:03:00Z",
    lastSyncedAt: "2026-08-21T01:04:00Z",
    syncCursor: "cursor-7",
    parseState: "COMPLETE",
  },
  messages: [
    {
      id: "message-reply",
      workspaceId: "T-1",
      channelId: "C-PUBLIC",
      threadId: "thread-1",
      messageTs: "1724200001.000200",
      threadTs: "1724200000.000100",
      position: "REPLY",
      currentRevisionId: "revision-reply-delete",
      revisionCount: 2,
      deleted: true,
      firstObservedAt: "2026-08-21T01:01:00Z",
      latestChangedAt: "2026-08-21T01:03:00Z",
    },
    {
      id: "message-root",
      workspaceId: "T-1",
      channelId: "C-PUBLIC",
      threadId: "thread-1",
      messageTs: "1724200000.000100",
      threadTs: "1724200000.000100",
      position: "ROOT",
      currentRevisionId: "revision-root-edit",
      revisionCount: 2,
      deleted: false,
      firstObservedAt: "2026-08-21T01:00:00Z",
      latestChangedAt: "2026-08-21T01:02:00Z",
    },
  ],
  revisions: [
    { id: "revision-root-edit", messageId: "message-root", revision: 2, previousRevisionId: "revision-root", kind: "EDITED", body: "수정된 요청", authorId: "U-1", sourceEventId: "Ev-edit", sourceEventTime: 1724200002, occurredAt: "2026-08-21T01:02:00Z", observedAt: "2026-08-21T01:02:01Z" },
    { id: "revision-root", messageId: "message-root", revision: 1, previousRevisionId: null, kind: "CREATED", body: "과거 원문 요청", authorId: "U-1", sourceEventId: "Ev-root", sourceEventTime: 1724200000, occurredAt: "2026-08-21T01:00:00Z", observedAt: "2026-08-21T01:00:01Z" },
    { id: "revision-reply-delete", messageId: "message-reply", revision: 2, previousRevisionId: "revision-reply", kind: "DELETED", body: null, authorId: "U-2", sourceEventId: "Ev-delete", sourceEventTime: 1724200003, occurredAt: "2026-08-21T01:03:00Z", observedAt: "2026-08-21T01:03:01Z" },
    { id: "revision-reply", messageId: "message-reply", revision: 1, previousRevisionId: null, kind: "CREATED", body: "보존된 reply 원문", authorId: "U-2", sourceEventId: "Ev-reply", sourceEventTime: 1724200001, occurredAt: "2026-08-21T01:01:00Z", observedAt: "2026-08-21T01:01:01Z" },
  ],
  parse: {
    id: "parse-1",
    workItemId: "wi-1",
    status: "NEEDS_INPUT",
    sourceRevisionIds: ["revision-root-edit"],
    result: {
      developmentItem: "로그인 개선",
      requestedMenu: "회원 > 로그인",
      featureDescription: "잠금 정책",
      missingFields: ["requestDetails"],
      confidence: 0.75,
      sourceMessageRevisions: ["revision-root-edit"],
    },
    openQuestions: ["요청 상세를 입력해 주세요"],
    errorCode: null,
    createdAt: "2026-08-21T01:03:00Z",
    completedAt: "2026-08-21T01:04:00Z",
  },
};

describe("SlackWorkItemDetailView", () => {
  it("renders chronological messages, immutable revisions, parse sources and jobs", () => {
    const markup = renderToStaticMarkup(<SlackWorkItemDetailView detail={detail} jobs={[{
      id: "job-quiet",
      workspaceId: "T-1",
      workItemId: "wi-1",
      channelId: "C-PUBLIC",
      jobType: "QUIET_WINDOW_PARSE",
      state: "RETRY_WAIT",
      runAfter: "2026-08-21T01:10:00Z",
      attempt: 2,
      maxAttempts: 5,
      retry: { reason: "RATE_LIMIT", attempt: 2, maxAttempts: 5, lastErrorCode: "slack_rate_limited", lastErrorMessage: "Retry later", lastHttpStatus: 429, retryAfterSeconds: 30, nextAttemptAt: "2026-08-21T01:10:00Z" },
      completedAt: null,
      createdAt: "2026-08-21T01:03:00Z",
      updatedAt: "2026-08-21T01:04:00Z",
    }]} />);

    expect(markup.indexOf("1724200000.000100")).toBeLessThan(markup.indexOf("1724200001.000200"));
    expect(markup).toContain("과거 원문 요청");
    expect(markup).toContain("수정된 요청");
    expect(markup).toContain("Slack에서 삭제됨");
    expect(markup).toContain("보존된 reply 원문");
    expect(markup).toContain("요청 상세");
    expect(markup).not.toContain("requestDetails");
    expect(markup).toContain("75%");
    expect(markup).toContain("r2 · 수정됨 · 수정된 요청");
    expect(markup).toContain("RATE_LIMIT");
    expect(markup).toContain("동기화 위치");
    expect(markup).toContain("저장됨");
    expect(markup).not.toContain("cursor-7");
  });

  it("escapes stored Slack and parsed text instead of interpreting active markup", () => {
    const payload = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    const attacked = structuredClone(detail);
    attacked.revisions[0]!.body = payload;
    attacked.revisions[0]!.authorId = payload;
    if (!attacked.parse) throw new Error("test fixture requires a parse result");
    attacked.parse.result = {
      ...(attacked.parse.result ?? {}),
      featureDescription: payload,
    };
    attacked.parse.openQuestions = [payload];

    const markup = renderToStaticMarkup(
      <SlackWorkItemDetailView detail={attacked} jobs={[]} />,
    );

    expect(markup).not.toContain(payload);
    expect(markup).not.toContain("<script");
    expect(markup).not.toContain("<img");
    expect(markup).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
    expect(markup).toContain("&lt;script&gt;alert(2)&lt;/script&gt;");
  });
});
