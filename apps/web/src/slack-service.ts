import {
  ApiClientError,
  createApiClient,
  type SlackEventLogPage,
  type SlackJobPage,
  type SlackWorkItemPage,
  type SlackWorkItemQuery,
} from "@tc/api-client";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export const slackApi = createApiClient(apiBaseUrl);

export type SlackWorkItem = SlackWorkItemPage["items"][number];
export type SlackJob = SlackJobPage["items"][number];
export type SlackEventLog = SlackEventLogPage["items"][number];

export function slackMutationKey(operation: string) {
  const nonce = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${operation}-${nonce}`;
}

export function slackErrorCode(error: unknown) {
  if (!(error instanceof ApiClientError)) return null;
  const response = error.response as { code?: string } | undefined;
  return response?.code ?? null;
}

export function slackErrorMessage(error: unknown, fallback: string) {
  if (error instanceof ApiClientError) {
    const response = error.response as { code?: string; message?: string } | undefined;
    if (error.status === 409 || response?.code?.includes("CONFLICT")) {
      return "원장 상태가 먼저 변경되었습니다. 최신 상태를 다시 불러온 뒤 재시도하세요.";
    }
    if (response?.code === "SLACK_SETUP_REQUIRED") {
      return "Slack credential 설정이 필요합니다. 서버 설정을 확인하세요.";
    }
    if (response?.code === "SLACK_DISABLED") {
      return "Slack 연동이 비활성화되어 있습니다.";
    }
    return response?.message ?? error.message;
  }
  if (error instanceof Error && error.name !== "AbortError") return error.message;
  return fallback;
}

export function isSlackConflict(error: unknown) {
  return error instanceof ApiClientError
    && (error.status === 409 || slackErrorCode(error)?.includes("CONFLICT") === true);
}

export function dedupeWorkItems(items: SlackWorkItem[]) {
  const unique = new Map<string, SlackWorkItem>();
  for (const item of items) {
    const existing = unique.get(item.id);
    if (existing && Date.parse(item.updatedAt) < Date.parse(existing.updatedAt)) continue;
    unique.set(item.id, item);
  }
  return [...unique.values()].sort(
    (left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt),
  );
}

export function dedupeEventLogs(items: SlackEventLog[]) {
  const unique = new Map<string, SlackEventLog>();
  for (const item of items) {
    if (!unique.has(item.eventId)) unique.set(item.eventId, item);
  }
  return [...unique.values()].sort(
    (left, right) => Date.parse(right.receivedAt) - Date.parse(left.receivedAt),
  );
}

export function dedupeJobs(items: SlackJob[]) {
  return [...new Map(items.map((item) => [item.id, item])).values()].sort(
    (left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt),
  );
}

export function listSlackWorkItemPage(
  query: SlackWorkItemQuery,
  cursor: string | null,
  signal?: AbortSignal,
) {
  const cursorQuery = {
    ...query,
    ...(cursor ? { cursor } : {}),
  } as SlackWorkItemQuery;
  return slackApi.listSlackWorkItems(cursorQuery, signal);
}

export function formatSlackDate(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("ko-KR");
}

export function formatSlackTimestamp(value: string) {
  const seconds = Number.parseFloat(value);
  if (!Number.isFinite(seconds)) return value;
  return new Date(seconds * 1000).toLocaleString("ko-KR");
}
