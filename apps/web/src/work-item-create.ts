import {
  normalizeWorkItemKey,
  type DemoWorkItem,
  type DomainId,
  type TestDomain,
} from "./test-management-demo";

export interface WorkItemDraft {
  domainId: DomainId | "";
  key: string;
  summary: string;
  description: string;
  owner: string;
  labels: string;
}

export type WorkItemDraftErrors = Partial<Record<"domainId" | "key" | "summary", string>>;

export function validateWorkItemDraft(
  draft: WorkItemDraft,
  categories: TestDomain[],
  workItems: DemoWorkItem[],
): WorkItemDraftErrors {
  const errors: WorkItemDraftErrors = {};
  const key = normalizeWorkItemKey(draft.key);
  if (!draft.domainId || !categories.some((category) => category.id === draft.domainId)) errors.domainId = "카테고리를 선택하세요.";
  if (!key) errors.key = "키는 WORK-숫자 형식으로 입력하세요.";
  else if (workItems.some((workItem) => workItem.key.toLocaleUpperCase("ko-KR") === key)) errors.key = "이미 사용 중인 상위일감 키입니다.";
  if (!draft.summary.trim()) errors.summary = "서머리를 입력하세요.";
  return errors;
}
