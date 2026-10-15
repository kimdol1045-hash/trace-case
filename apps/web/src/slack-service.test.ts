import type { SlackEventLogPage, SlackJobPage, SlackWorkItemPage } from "@tc/api-client";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  dedupeEventLogs,
  dedupeJobs,
  dedupeWorkItems,
  listSlackWorkItemPage,
} from "./slack-service";

afterEach(() => vi.unstubAllGlobals());

describe("Slack generated-client boundary", () => {
  it("keeps one newest work-item row when the same event is delivered three times", () => {
    const older = { id: "wi-1", updatedAt: "2026-08-21T01:00:00Z" } as SlackWorkItemPage["items"][number];
    const newer = { id: "wi-1", updatedAt: "2026-08-21T02:00:00Z" } as SlackWorkItemPage["items"][number];

    expect(dedupeWorkItems([older, newer, older])).toEqual([newer]);
  });

  it("deduplicates event IDs and job IDs at the UI boundary", () => {
    const event = { id: "delivery-1", eventId: "Ev-1", receivedAt: "2026-08-21T01:00:00Z" } as SlackEventLogPage["items"][number];
    const duplicate = { ...event, id: "delivery-duplicate" };
    const job = { id: "job-1", updatedAt: "2026-08-21T01:00:00Z" } as SlackJobPage["items"][number];

    expect(dedupeEventLogs([event, duplicate])).toHaveLength(1);
    expect(dedupeJobs([job, job])).toHaveLength(1);
  });

  it("uses the generated API client without supplying a mock fallback", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [],
      nextCursor: "cursor-2",
    }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listSlackWorkItemPage({ limit: 25 }, "cursor-1")).resolves.toEqual({
      items: [],
      nextCursor: "cursor-2",
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/slack-work-items?limit=25&cursor=cursor-1");
  });
});
