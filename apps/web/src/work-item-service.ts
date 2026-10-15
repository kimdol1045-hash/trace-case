import {
  ApiClientError,
  createApiClient,
  type WorkItemResource,
} from "@tc/api-client";

import type {
  DemoTestCase,
  DemoWorkItem,
  DomainId,
  NewWorkItemInput,
  TestCaseHistoryEntry,
  TestCaseKey,
  WorkItemKey,
} from "./test-management-demo";

type ApiClient = ReturnType<typeof createApiClient>;
type WorkItemListClient = Pick<ApiClient, "listWorkItems">;

export async function loadAllWorkItemResources(api: WorkItemListClient) {
  const limit = 200;
  const items: WorkItemResource[] = [];
  let offset = 0;
  while (true) {
    const page = await api.listWorkItems(limit, offset);
    items.push(...page.items);
    if (items.length >= page.total) return items;
    if (page.items.length === 0) {
      throw new Error("Work Item projection pagination stopped before reaching total.");
    }
    offset += page.items.length;
  }
}

export interface WorkItemDetailResult {
  workItem: DemoWorkItem;
  testCases: DemoTestCase[];
}

export interface WorkItemService {
  list(): Promise<DemoWorkItem[]>;
  get(workItemKey: WorkItemKey): Promise<WorkItemDetailResult>;
  create(input: NewWorkItemInput): Promise<DemoWorkItem>;
  update(workItem: DemoWorkItem): Promise<DemoWorkItem>;
}

export class WorkItemServiceError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "WorkItemServiceError";
  }
}

export class ApiWorkItemService implements WorkItemService {
  private readonly api: ApiClient;

  constructor(baseUrl: string) {
    this.api = createApiClient(baseUrl);
  }

  async list() {
    return this.translate(async () => {
      const items = await loadAllWorkItemResources(this.api);
      return items.map((item) => mapWorkItem(item));
    });
  }

  async get(workItemKey: WorkItemKey) {
    return this.translate(async () => {
      const detail = await this.api.getWorkItem(workItemKey);
      const audit = await this.api.listAuditLogs({
        entityType: "WORK_ITEM",
        entityId: detail.workItem.id,
        limit: 50,
      });
      const workItem = mapWorkItem(
        detail.workItem,
        audit.items.map(mapHistory),
      );
      return {
        workItem,
        testCases: detail.testCases.map((item) => mapChildCase(item, workItem)),
      };
    });
  }

  async create(input: NewWorkItemInput) {
    return this.translate(async () => mapWorkItem(await this.api.createWorkItem(
      {
        key: input.key as WorkItemKey,
        categoryId: input.domainId,
        summary: input.summary.trim(),
        description: input.description.trim(),
        ...(input.owner.trim() ? { ownerLabel: input.owner.trim() } : {}),
        labels: input.labels,
      },
      mutationOptions("work-item-create"),
    )));
  }

  async update(workItem: DemoWorkItem) {
    if (!workItem.resourceVersion) {
      throw new WorkItemServiceError(
        "VALIDATION_FAILED",
        "상위 일감의 resourceVersion이 없어 저장할 수 없습니다.",
      );
    }
    return this.translate(async () => mapWorkItem(await this.api.updateWorkItem(
      workItem.key,
      {
        baseResourceVersion: workItem.resourceVersion!,
        categoryId: workItem.domainId,
        summary: workItem.summary.trim(),
        description: workItem.description.trim(),
        ownerLabel: workItem.owner.trim() && workItem.owner !== "미지정"
          ? workItem.owner.trim()
          : null,
        labels: workItem.labels,
        changeReason: "Work Item 상세 화면에서 수정",
      },
      mutationOptions("work-item-update"),
    )));
  }

  private async translate<T>(call: () => Promise<T>): Promise<T> {
    try {
      return await call();
    } catch (error) {
      if (error instanceof ApiClientError) {
        throw new WorkItemServiceError(
          error.response?.code ?? "WORK_ITEM_REQUEST_FAILED",
          error.response?.message ?? error.message,
          error.response?.details ?? {},
        );
      }
      throw error;
    }
  }
}

export function createDefaultWorkItemService() {
  return new ApiWorkItemService(
    import.meta.env.VITE_API_BASE_URL ?? "",
  );
}

export function mapWorkItem(
  resource: WorkItemResource,
  history: TestCaseHistoryEntry[] = [],
): DemoWorkItem {
  return {
    id: resource.id as DemoWorkItem["id"],
    key: resource.key as WorkItemKey,
    summary: resource.summary,
    domainId: resource.categoryId as DomainId,
    description: resource.description,
    owner: resource.ownerLabel ?? "미지정",
    labels: [...resource.labels],
    resourceVersion: resource.resourceVersion,
    createdAt: resource.createdAt,
    updatedAt: resource.updatedAt,
    history,
  };
}

function mapChildCase(
  item: Awaited<ReturnType<ApiClient["getWorkItem"]>>["testCases"][number],
  workItem: DemoWorkItem,
): DemoTestCase {
  return {
    id: item.id as DemoTestCase["id"],
    key: item.key as TestCaseKey,
    summary: item.summary,
    domainId: item.categoryId as DomainId,
    status: item.status,
    priority: item.priority === "CRITICAL" ? "HIGHEST" : item.priority,
    owner: item.ownerLabel ?? "미지정",
    labels: [...item.labels],
    precondition: "",
    steps: [],
    expectedResult: "",
    description: "",
    sourceWorkItem: `${workItem.key} · ${workItem.summary}`,
    linkedIssueKeys: [],
    version: item.version,
    resourceVersion: item.resourceVersion,
    workItemId: item.workItemId as DemoTestCase["workItemId"],
    sortOrder: item.sortOrder,
    deletedAt: item.deletedAt,
    createdAt: item.updatedAt,
    updatedAt: item.updatedAt,
    history: [],
  };
}

function mapHistory(
  entry: Awaited<ReturnType<ApiClient["listAuditLogs"]>>["items"][number],
): TestCaseHistoryEntry {
  const before = entry.before ?? {};
  const after = entry.after ?? {};
  const fields = ["summary", "description", "categoryId", "ownerLabel", "labels"] as const;
  const changes = fields.flatMap((field) => {
    const previous = before[field];
    const next = after[field];
    if (JSON.stringify(previous) === JSON.stringify(next)) return [];
    return [{
      field,
      before: displayValue(previous),
      after: displayValue(next),
    }];
  });
  return {
    id: entry.id,
    action: entry.action === "CREATE" ? "상위 일감 생성" : "상위 일감 수정",
    actor: entry.source.actorLabel ?? entry.source.type,
    occurredAt: entry.occurredAt,
    ...(changes.length ? { changes } : {}),
  };
}

function displayValue(value: unknown) {
  if (value == null || value === "") return "비어 있음";
  return Array.isArray(value) ? value.join(", ") : String(value);
}

function mutationOptions(prefix: string) {
  return {
    idempotencyKey: `${prefix}-${crypto.randomUUID()}`,
    actorLabel: "Test Library UI",
  };
}
