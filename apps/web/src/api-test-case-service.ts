import {
  ApiClientError,
  createApiClient,
  type TestCaseListQuery,
  type TestCaseResource,
  type TestCaseVersion,
} from "@tc/api-client";

import type {
  DemoTestCase,
  DemoWorkItem,
  DomainId,
  TestCaseHistoryEntry,
  TestCaseKey,
  TestCasePriority,
  WorkItemKey,
} from "./test-management-demo";
import {
  TestCaseServiceError,
  type CaseDetailResult,
  type CaseLibrarySnapshot,
  type NewTestCaseInput,
  type TestCaseQuery,
  type TestCaseService,
  type TestCaseServiceErrorCode,
} from "./test-case-service";

type ApiClient = ReturnType<typeof createApiClient>;

const CASE_ERROR_CODES = new Set<TestCaseServiceErrorCode>([
  "CURSOR_INVALID",
  "EXPECTED_RESULT_MODE_INVALID",
  "IDEMPOTENCY_CONFLICT",
  "LABEL_CONFLICT",
  "PERMANENT_DELETE_CONFLICT",
  "RESOURCE_NOT_FOUND",
  "TEST_CASE_ALREADY_DELETED",
  "TEST_CASE_COPY_CONFLICT",
  "TEST_CASE_MOVE_CONFLICT",
  "TEST_CASE_NOT_DELETED",
  "TEST_CASE_RESTORE_CONFLICT",
  "VALIDATION_FAILED",
  "VERSION_CONFLICT",
]);

export class ApiTestCaseService implements TestCaseService {
  private readonly api: ApiClient;
  private readonly resources = new Map<TestCaseKey, TestCaseResource>();
  private readonly workItemsByKey = new Map<WorkItemKey, DemoWorkItem>();

  constructor(baseUrl: string) {
    this.api = createApiClient(baseUrl.replace(/\/$/, ""));
  }

  async list(query: TestCaseQuery = {}): Promise<CaseLibrarySnapshot> {
    return this.translate(async () => {
      const response = await this.api.listTestCases(toApiQuery(query));
      const testCases = response.items.map((item) => {
        const domainId = item.categoryId as DomainId;
        const workItem: DemoWorkItem = {
          id: item.workItemId as DemoWorkItem["id"],
          key: item.workItemKey as WorkItemKey,
          summary: item.workItemSummary,
          domainId,
          description: "",
          owner: "미지정",
          labels: [],
          createdAt: item.updatedAt,
          updatedAt: item.updatedAt,
          history: [],
        };
        this.workItemsByKey.set(workItem.key, workItem);
        const cached = this.resources.get(item.key as TestCaseKey);
        return cached
          && cached.version === item.version
          && cached.resourceVersion === item.resourceVersion
          && cached.deletedAt === item.deletedAt
          ? this.mapResource(cached, workItem)
          : mapListItem(item, workItem);
      });
      return {
        testCases,
        nextCursor: response.nextCursor,
        total: response.total,
        source: "API",
      };
    });
  }

  async get(caseKey: TestCaseKey): Promise<CaseDetailResult> {
    return this.translate(async () => {
      let resource: TestCaseResource;
      try {
        resource = await this.findReference(caseKey);
      } catch (error) {
        if (!(error instanceof TestCaseServiceError) || error.code !== "RESOURCE_NOT_FOUND") throw error;
        resource = await this.findReference(caseKey, true);
      }
      this.resources.set(caseKey, resource);
      return this.withHistory(resource);
    });
  }

  async create(workItem: DemoWorkItem, input?: NewTestCaseInput): Promise<DemoTestCase> {
    if (!workItem.id) throw new TestCaseServiceError("VALIDATION_FAILED", "API work item ID가 필요합니다.");
    const workItemId = workItem.id;
    const steps = input?.steps ?? [{ precondition: "사전조건을 입력하세요.", action: "테스트 동작을 입력하세요.", expectedResult: "기대 결과를 입력하세요." }];
    const precondition = steps.find((step) => step.precondition?.trim())?.precondition?.trim() ?? "스텝별 사전조건을 확인하세요.";
    return this.translate(async () => {
      const resource = await this.api.createTestCase(
        {
          workItemId,
          status: "ACTIVE",
          summary: input?.summary ?? "제목 없는 테스트케이스",
          precondition,
          expectedResultMode: "PER_STEP",
          priority: input ? toApiPriority(input.priority) : "MEDIUM",
          ...(input?.owner.trim() ? { ownerLabel: input.owner.trim() } : {}),
          labels: input?.labels ?? [],
          steps: steps.map((step, index) => ({ sequence: index + 1, ...(step.precondition?.trim() ? { precondition: step.precondition.trim() } : {}), action: step.action.trim(), expectedResult: step.expectedResult.trim() })),
          source: { type: "UI", actorLabel: "Test Library UI" },
        },
        mutationOptions("case-create"),
      );
      this.resources.set(resource.key as TestCaseKey, resource);
      return this.mapResource(resource, workItem);
    });
  }

  async update(testCase: DemoTestCase): Promise<DemoTestCase> {
    const reference = await this.findReference(testCase.key);
    // Interactive Test Library edits canonicalize legacy generated CASE
    // payloads to the editable per-step representation.
    const expectedResultMode = "PER_STEP" as const;
    const legacyPrecondition = (
      testCase.steps.find((step) => step.precondition?.trim())?.precondition?.trim()
      ?? testCase.precondition.trim()
    ) || "스텝별 사전조건을 확인하세요.";
    return this.translate(async () => {
      const resource = await this.api.updateTestCase(
        reference.id,
        {
          baseVersion: testCase.version,
          status: testCase.status === "DRAFT" ? "ACTIVE" : testCase.status,
          summary: testCase.summary,
          precondition: legacyPrecondition,
          expectedResultMode,
          priority: toApiPriority(testCase.priority),
          ...(testCase.owner && testCase.owner !== "미지정" ? { ownerLabel: testCase.owner } : { ownerLabel: null }),
          labels: testCase.labels,
          steps: testCase.steps.map((step, index) => ({
            sequence: index + 1,
            ...(step.precondition?.trim() ? { precondition: step.precondition.trim() } : {}),
            action: step.action,
            expectedResult: step.expectedResult,
          })),
          changeReason: "Test Library UI에서 테스트 스크립트 수정",
          source: { type: "UI", actorLabel: "Test Library UI" },
        },
        mutationOptions("case-update"),
      );
      this.resources.set(testCase.key, resource);
      return this.mapResource(resource);
    });
  }

  async move(caseKey: TestCaseKey, targetWorkItem: DemoWorkItem, targetSortOrder: number) {
    const reference = await this.findReference(caseKey);
    if (!targetWorkItem.id) throw new TestCaseServiceError("VALIDATION_FAILED", "API work item ID가 필요합니다.");
    return this.translate(async () => {
      const response = await this.api.moveTestCase(
        reference.id,
        {
          baseResourceVersion: reference.resourceVersion,
          targetWorkItemId: targetWorkItem.id!,
          targetSortOrder,
          changeReason: "Test Library UI에서 소유 일감 또는 순서 이동",
          source: { type: "UI", actorLabel: "Test Library UI" },
        },
        mutationOptions("case-move"),
      );
      this.resources.set(caseKey, response.testCase);
      return this.mapResource(response.testCase, targetWorkItem);
    });
  }

  async copy(caseKey: TestCaseKey, targetWorkItem: DemoWorkItem) {
    const reference = await this.findReference(caseKey);
    if (!targetWorkItem.id) throw new TestCaseServiceError("VALIDATION_FAILED", "API work item ID가 필요합니다.");
    const targetSortOrder = [...this.resources.values()].filter((item) => item.workItemId === targetWorkItem.id && item.deletedAt === null).length;
    return this.translate(async () => {
      const response = await this.api.copyTestCase(
        reference.id,
        {
          sourceVersion: reference.version,
          targetWorkItemId: targetWorkItem.id!,
          targetSortOrder,
          summary: `${reference.currentVersion.summary} 복사본`,
          changeReason: "Test Library UI에서 테스트케이스 복사",
          source: { type: "UI", actorLabel: "Test Library UI" },
        },
        mutationOptions("case-copy"),
      );
      this.resources.set(response.testCase.key as TestCaseKey, response.testCase);
      return this.mapResource(response.testCase, targetWorkItem);
    });
  }

  async softDelete(caseKey: TestCaseKey) {
    const reference = await this.findReference(caseKey);
    return this.translate(async () => {
      const response = await this.api.softDeleteTestCase(
        reference.id,
        {
          baseResourceVersion: reference.resourceVersion,
          reason: "Test Library UI에서 soft delete",
          source: { type: "UI", actorLabel: "Test Library UI" },
        },
        mutationOptions("case-delete"),
      );
      this.resources.set(caseKey, response.testCase);
      return this.mapResource(response.testCase);
    });
  }

  async restore(caseKey: TestCaseKey) {
    const reference = await this.findReference(caseKey, true);
    return this.translate(async () => {
      const response = await this.api.restoreDeletedTestCase(
        reference.id,
        {
          baseResourceVersion: reference.resourceVersion,
          reason: "Test Library UI에서 삭제 복원",
          source: { type: "UI", actorLabel: "Test Library UI" },
        },
        mutationOptions("case-restore-deleted"),
      );
      this.resources.set(caseKey, response.testCase);
      return this.mapResource(response.testCase);
    });
  }

  async permanentDelete(testCase: Pick<DemoTestCase, "id" | "key" | "resourceVersion" | "deletedAt">) {
    if (!testCase.deletedAt) {
      throw new TestCaseServiceError("TEST_CASE_NOT_DELETED", "삭제된 테스트케이스만 완전 삭제할 수 있습니다.");
    }
    const caseId = testCase.id;
    const baseResourceVersion = testCase.resourceVersion;
    if (!caseId || baseResourceVersion === undefined) {
      throw new TestCaseServiceError("VALIDATION_FAILED", "API 테스트케이스 ID와 리소스 버전이 필요합니다.");
    }
    return this.translate(async () => {
      await this.api.permanentlyDeleteTestCase(
        caseId,
        {
          baseResourceVersion,
          reason: "Test Library UI에서 테스트케이스 완전 삭제",
          source: { type: "UI", actorLabel: "Test Library UI" },
        },
        mutationOptions("case-permanent-delete"),
      );
      this.resources.delete(testCase.key);
    });
  }

  async restoreVersion(caseKey: TestCaseKey, version: number): Promise<CaseDetailResult> {
    const reference = await this.findReference(caseKey);
    return this.translate(async () => {
      const response = await this.api.restoreTestCaseVersion(
        reference.id,
        version,
        {
          baseVersion: reference.version,
          changeReason: `Test Library UI에서 v${version} 복원`,
          source: { type: "UI", actorLabel: "Test Library UI" },
        },
        mutationOptions("case-restore-version"),
      );
      this.resources.set(caseKey, response.testCase);
      return this.withHistory(response.testCase);
    });
  }

  private async findReference(caseKey: TestCaseKey, includeDeleted = false) {
    const cached = this.resources.get(caseKey);
    if (cached && (includeDeleted || cached.deletedAt === null)) return cached;
    const response = await this.api.listTestCases({ search: caseKey, includeDeleted, limit: 50 });
    const exact = response.items.find((item) => item.key === caseKey);
    if (!exact) throw new TestCaseServiceError("RESOURCE_NOT_FOUND", "테스트케이스를 찾을 수 없습니다.");
    this.workItemsByKey.set(exact.workItemKey as WorkItemKey, {
      id: exact.workItemId as DemoWorkItem["id"],
      key: exact.workItemKey as WorkItemKey,
      summary: exact.workItemSummary,
      domainId: exact.categoryId as DomainId,
      description: "",
      owner: "미지정",
      labels: [],
      createdAt: exact.updatedAt,
      updatedAt: exact.updatedAt,
      history: [],
    });
    const resource = includeDeleted && exact.deletedAt
      ? await this.api.getTestCase(exact.id, undefined, true)
      : await this.api.getTestCase(exact.id);
    this.resources.set(caseKey, resource);
    return resource;
  }

  private async withHistory(resource: TestCaseResource): Promise<CaseDetailResult> {
    const workItem = this.workItemsByKey.get(findWorkItemKey(resource.workItemId, this.workItemsByKey));
    const testCase = this.mapResource(resource, workItem);
    try {
      const page = await this.api.listTestCaseVersions(resource.id, { limit: 200 });
      const versions = page.items;
      const settled = await Promise.allSettled(
        versions.slice(1).map((version, index) => this.api.diffTestCaseVersions(resource.id, {
          fromVersion: versions[index]!.version,
          toVersion: version.version,
        })),
      );
      const history = versions.map((version, index) => {
        const diff = index === 0 ? undefined : settled[index - 1];
        return mapHistory(version, diff?.status === "fulfilled" ? diff.value : undefined);
      }).reverse();
      return {
        testCase: {
          ...testCase,
          history,
          versions: versions.map((version) => ({
            version: version.version,
            changeReason: version.changeReason,
            createdAt: version.createdAt,
            ...(version.restoredFromVersion ? { restoredFromVersion: version.restoredFromVersion } : {}),
          })).reverse(),
        },
        ...(settled.some((item) => item.status === "rejected")
          ? { partialFailure: "일부 버전 diff를 불러오지 못했습니다. 버전 원문은 표시됩니다." }
          : {}),
      };
    } catch {
      return {
        testCase,
        partialFailure: "버전 이력을 불러오지 못했습니다. 현재 버전 편집은 계속할 수 있습니다.",
      };
    }
  }

  private mapResource(resource: TestCaseResource, knownWorkItem?: DemoWorkItem) {
    const workItem = knownWorkItem
      ?? [...this.workItemsByKey.values()].find((item) => item.id === resource.workItemId)
      ?? placeholderWorkItem(resource.workItemId, resource.categoryId);
    this.workItemsByKey.set(workItem.key, workItem);
    return mapResource(resource, workItem);
  }

  private async translate<Result>(action: () => Promise<Result>): Promise<Result> {
    try {
      return await action();
    } catch (error) {
      if (error instanceof ApiClientError) {
        const code = error.response?.code as TestCaseServiceErrorCode | undefined;
        if (code && CASE_ERROR_CODES.has(code)) {
          const message = code === "PERMANENT_DELETE_CONFLICT"
            ? "연결된 사이클·이슈 또는 복사본이 남아 있어 완전 삭제할 수 없습니다. 연결 데이터를 먼저 정리한 뒤 다시 시도하세요."
            : error.response?.message ?? error.message;
          throw new TestCaseServiceError(code, message, error.response?.details);
        }
      }
      throw error;
    }
  }
}

export function createDefaultTestCaseService() {
  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
  return new ApiTestCaseService(baseUrl);
}

function mapListItem(
  item: Awaited<ReturnType<ApiClient["listTestCases"]>>["items"][number],
  workItem: DemoWorkItem,
): DemoTestCase {
  return {
    id: item.id as DemoTestCase["id"],
    key: item.key as TestCaseKey,
    summary: item.summary,
    domainId: item.categoryId as DomainId,
    status: item.status,
    priority: fromApiPriority(item.priority),
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

function mapResource(resource: TestCaseResource, workItem: DemoWorkItem): DemoTestCase {
  const snapshot = resource.currentVersion;
  return {
    id: resource.id as DemoTestCase["id"],
    key: resource.key as TestCaseKey,
    summary: snapshot.summary,
    domainId: resource.categoryId as DomainId,
    status: resource.status,
    priority: fromApiPriority(snapshot.priority),
    owner: snapshot.ownerLabel ?? "미지정",
    labels: [...snapshot.labels],
    precondition: snapshot.precondition,
    steps: snapshot.steps.map((step, index) => ({
      id: `${snapshot.id}_step_${step.sequence}`,
      precondition: step.precondition ?? (index === 0 ? snapshot.precondition : ""),
      action: step.action,
      expectedResult: step.expectedResult ?? "",
    })),
    expectedResult: snapshot.caseExpectedResult ?? "",
    expectedResultMode: snapshot.expectedResultMode,
    generationManagement: resource.generationManagement,
    description: "",
    sourceWorkItem: `${workItem.key} · ${workItem.summary}`,
    linkedIssueKeys: [],
    version: resource.version,
    resourceVersion: resource.resourceVersion,
    workItemId: resource.workItemId as DemoTestCase["workItemId"],
    sortOrder: resource.sortOrder,
    deletedAt: resource.deletedAt,
    copiedFrom: resource.copiedFrom?.caseKey as TestCaseKey | undefined,
    createdAt: resource.createdAt,
    updatedAt: resource.updatedAt,
    history: [{
      id: snapshot.id,
      action: snapshot.changeReason,
      actor: snapshot.source.actorLabel ?? snapshot.source.type,
      occurredAt: snapshot.createdAt,
      version: snapshot.version,
      ...(snapshot.restoredFromVersion ? { restoredFromVersion: snapshot.restoredFromVersion } : {}),
    }],
  };
}

function mapHistory(
  version: TestCaseVersion,
  diff?: Awaited<ReturnType<ApiClient["diffTestCaseVersions"]>>,
): TestCaseHistoryEntry {
  const changes = [
    ...(diff?.fieldChanges.map((change) => ({
      field: change.field,
      before: displayValue(change.before),
      after: displayValue(change.after),
    })) ?? []),
    ...(diff?.stepChanges.map((change) => ({
      field: `step ${change.after?.sequence ?? change.before?.sequence ?? "?"} · ${change.changeType}`,
      before: displayValue(change.before),
      after: displayValue(change.after),
    })) ?? []),
  ];
  return {
    id: version.id,
    action: version.changeReason,
    actor: version.source.actorLabel ?? version.source.type,
    occurredAt: version.createdAt,
    version: version.version,
    ...(version.restoredFromVersion ? { restoredFromVersion: version.restoredFromVersion } : {}),
    ...(changes.length ? { changes } : {}),
  };
}

function toApiQuery(query: TestCaseQuery): TestCaseListQuery {
  return {
    ...(query.search ? { search: query.search } : {}),
    ...(query.categoryIds?.length ? { categoryIds: query.categoryIds.filter((id): id is `folder_${string}` => id.startsWith("folder_")) } : {}),
    ...(query.statuses?.length ? { statuses: query.statuses } : {}),
    ...(query.priorities?.length ? { priorities: query.priorities } : {}),
    ...(query.labels?.length ? { labels: query.labels, labelMatch: query.labelMatch ?? "ANY" } : {}),
    ...(query.includeDeleted || query.deletedOnly ? { includeDeleted: true } : {}),
    ...(query.deletedOnly ? { deletedOnly: true } : {}),
    ...(query.cursor ? { cursor: query.cursor } : {}),
    limit: query.limit ?? 50,
  };
}

function placeholderWorkItem(workItemId: string, categoryId: string): DemoWorkItem {
  const suffix = workItemId.slice(-6);
  return {
    id: workItemId as DemoWorkItem["id"],
    key: `WORK-${Number.parseInt(suffix, 16) || 0}`,
    summary: "API work item",
    domainId: categoryId as DomainId,
    description: "",
    owner: "미지정",
    labels: [],
    createdAt: new Date(0).toISOString(),
    updatedAt: new Date(0).toISOString(),
    history: [],
  };
}

function findWorkItemKey(workItemId: string, workItems: Map<WorkItemKey, DemoWorkItem>) {
  return [...workItems.values()].find((item) => item.id === workItemId)?.key ?? "WORK-0";
}

function fromApiPriority(priority: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"): TestCasePriority {
  return priority === "CRITICAL" ? "HIGHEST" : priority;
}

function toApiPriority(priority: TestCasePriority) {
  return priority === "HIGHEST" ? "CRITICAL" as const : priority;
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "비어 있음";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function mutationOptions(operation: string) {
  const randomPart = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return { actorLabel: "Test Library UI", idempotencyKey: `${operation}-${randomPart}` };
}
