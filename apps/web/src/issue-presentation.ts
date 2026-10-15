import type { CycleItem, IssueHistoryPage, IssueLinkResource, IssueListQuery, IssueResource } from "@tc/api-client";

const issueHistoryEventLabels: Record<IssueHistoryPage["items"][number]["eventType"], string> = {
  CREATED: "이슈 생성",
  UPDATED: "이슈 내용 변경",
  STATUS_CHANGED: "상태 변경",
  LINK_ADDED: "연결 추가",
  LINK_REMOVED: "연결 해제",
  COMMENT_ADDED: "댓글 추가",
  ATTACHMENT_ADDED: "첨부파일 추가",
  ATTACHMENT_DELETED: "첨부파일 삭제",
  DELETED: "이슈 삭제",
};

const issueHistoryFieldLabels: Record<string, string> = {
  summary: "서머리",
  precondition: "사전 조건",
  steps: "재현 스텝",
  expectedResult: "기대 결과",
  actualResult: "실제 결과",
  status: "상태",
  severity: "심각도",
  assigneeLabel: "담당자",
  linkedCycleIds: "연결된 사이클",
  deletedAt: "삭제 상태",
  createdAt: "생성 시각",
  updatedAt: "수정 시각",
  actorLabel: "처리자",
  reason: "변경 사유",
};

const hiddenIssueHistoryFields = new Set([
  "id",
  "key",
  "issuekey",
  "ordinal",
  "resourceversion",
  "workspaceid",
]);

const issueHistoryValueLabels: Record<string, string> = {
  OPEN: "열림",
  IN_PROGRESS: "진행 중",
  RESOLVED: "해결됨",
  CLOSED: "종료",
  REOPENED: "다시 열림",
  LOW: "낮음",
  MEDIUM: "보통",
  HIGH: "높음",
  CRITICAL: "매우 높음",
};

function readableObjectValue(value: Record<string, unknown>) {
  for (const key of ["label", "name", "summary", "title", "filename", "status", "severity"]) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim()) return candidate;
  }
  if (typeof value.action === "string") {
    const expected = typeof value.expectedResult === "string" && value.expectedResult.trim()
      ? ` → ${value.expectedResult}`
      : "";
    return `${value.action}${expected}`;
  }
  return "세부 정보 변경";
}

export function formatIssueHistoryValue(field: string, value: unknown) {
  if (value === null || value === undefined || value === "") return "없음";
  if (Array.isArray(value)) {
    if (value.length === 0) return "없음";
    if (field === "steps") return `재현 스텝 ${value.length}개`;
    if (field.toLocaleLowerCase().includes("id")) return `${value.length}개 연결`;
    const summaries = value.map((item) => typeof item === "object" && item !== null
      ? readableObjectValue(item as Record<string, unknown>)
      : String(item));
    return summaries.join(", ");
  }
  if (typeof value === "object") return readableObjectValue(value as Record<string, unknown>);
  if (typeof value === "boolean") return value ? "예" : "아니요";
  if (typeof value === "string" && field.toLocaleLowerCase().endsWith("at")) {
    const timestamp = Date.parse(value);
    if (!Number.isNaN(timestamp)) {
      return new Intl.DateTimeFormat("ko-KR", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "Asia/Seoul",
      }).format(new Date(timestamp));
    }
  }
  if (field.toLocaleLowerCase().endsWith("id")) return "연결됨";
  return issueHistoryValueLabels[String(value)] ?? String(value);
}

export function issueHistoryEventLabel(eventType: IssueHistoryPage["items"][number]["eventType"]) {
  return issueHistoryEventLabels[eventType];
}

export function issueHistoryFieldLabel(field: string) {
  return issueHistoryFieldLabels[field] ?? "세부 정보";
}

export function shouldDisplayIssueHistoryField(field: string) {
  const normalized = field.replaceAll("_", "").toLocaleLowerCase();
  if (field === "linkedCycleIds") return true;
  return !hiddenIssueHistoryFields.has(normalized) && !normalized.endsWith("id");
}

export function issueStatusCountQuery(query: IssueListQuery, status: IssueResource["status"]): IssueListQuery {
  const sharedFilters = { ...query };
  delete sharedFilters.statuses;
  delete sharedFilters.cursor;
  return { ...sharedFilters, statuses: [status], limit: 1 };
}

export function resolveIssueLinkCycleItemId(
  link: IssueLinkResource,
  items: CycleItem[],
  snapshot: IssueResource["reproductionSnapshot"],
) {
  if (link.target.type === "CYCLE_ITEM") return link.target.cycleItemId;
  if (link.target.type === "EXECUTION_ATTEMPT") {
    const attemptId = link.target.executionAttemptId;
    const matchedItem = items.find((item) => item.platformExecutions.some((execution) => execution.latestAttempt?.id === attemptId));
    const snapshotItemId = snapshot && snapshot.sourceAttemptId === attemptId ? snapshot.cycleItemId : undefined;
    return matchedItem?.id ?? snapshotItemId;
  }
  if (link.target.type === "TEST_CASE") {
    return items.find((item) => item.testCaseId === link.target.testCaseId)?.id;
  }
  return undefined;
}
