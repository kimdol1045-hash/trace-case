import { ApiClientError, createApiClient } from "@tc/api-client";

import type {
  DemoWorkItem,
  DomainId,
  TestDomain,
} from "./test-management-demo";
import { loadAllWorkItemResources, mapWorkItem } from "./work-item-service";

type ApiClient = ReturnType<typeof createApiClient>;

export interface LibraryProjection {
  domains: TestDomain[];
  workItems: DemoWorkItem[];
}

export interface CategoryService {
  loadProjection(): Promise<LibraryProjection>;
  create(name: string, sortOrder: number): Promise<DomainId>;
  rename(category: TestDomain, name: string): Promise<void>;
  delete(category: TestDomain): Promise<void>;
}

export class CategoryServiceError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "CategoryServiceError";
  }
}

/**
 * Loads categories and parent work items together from their PostgreSQL-backed
 * API projections. Case search filters never participate in this projection,
 * so category counts cannot shrink when the visible case query changes.
 */
export class ApiCategoryService implements CategoryService {
  private readonly api: ApiClient;

  constructor(baseUrl: string) {
    this.api = createApiClient(baseUrl);
  }

  async loadProjection(): Promise<LibraryProjection> {
    return this.translate(async () => {
      const [tree, workItemResources] = await Promise.all([
        this.api.getFolderTree(),
        loadAllWorkItemResources(this.api),
      ]);
      const workItems = workItemResources.map((workItem) => mapWorkItem(workItem));
      const domains = tree.items
        .filter((folder) => folder.parentId === null && folder.deletedAt === null)
        .map<TestDomain>((folder) => {
          const workItemCount = workItems.filter((item) => item.domainId === folder.id).length;
          return {
            id: folder.id as DomainId,
            parentId: null,
            name: folder.name,
            sortOrder: folder.sortOrder,
            resourceVersion: folder.version,
            activeTestCaseCount: folder.directTestCaseCount,
            workItemCount,
            protected: folder.id.endsWith("00000000000070008000000000000101"),
          };
        })
        .filter((domain) => !domain.protected
          || (domain.activeTestCaseCount ?? 0) > 0
          || (domain.workItemCount ?? 0) > 0);
      return { domains, workItems };
    });
  }

  async create(name: string, sortOrder: number): Promise<DomainId> {
    return this.translate(async () => {
      const result = await this.api.createFolder(
        { parentId: null, name, sortOrder },
        mutationOptions("category-create"),
      );
      return result.rootFolderId as DomainId;
    });
  }

  async rename(category: TestDomain, name: string): Promise<void> {
    if (!category.resourceVersion) {
      throw new CategoryServiceError(
        "VALIDATION_FAILED",
        "카테고리 resourceVersion이 없어 이름을 변경할 수 없습니다.",
      );
    }
    await this.translate(() => this.api.renameFolder(
      category.id,
      { baseVersion: category.resourceVersion!, name },
      mutationOptions("category-rename"),
    ));
  }

  async delete(category: TestDomain): Promise<void> {
    if (!category.resourceVersion) {
      throw new CategoryServiceError(
        "VALIDATION_FAILED",
        "카테고리 resourceVersion이 없어 삭제할 수 없습니다.",
      );
    }
    const preview = await this.translate(() => this.api.previewFolderDelete(category.id));
    await this.translate(() => this.api.softDeleteFolder(
      category.id,
      {
        baseVersion: category.resourceVersion!,
        mode: "SUBTREE",
        reason: "Test Library UI에서 빈 카테고리 삭제",
      },
      preview.requiresConfirmation,
      mutationOptions("category-delete"),
    ));
  }

  private async translate<Result>(action: () => Promise<Result>): Promise<Result> {
    try {
      return await action();
    } catch (error) {
      if (error instanceof ApiClientError) {
        const code = error.response?.code ?? "CATEGORY_REQUEST_FAILED";
        throw new CategoryServiceError(
          code,
          code === "SYSTEM_FOLDER_PROTECTED"
            ? "AI 생성함은 자동 생성 결과를 보관하는 시스템 카테고리라 수정하거나 삭제할 수 없습니다."
            : error.response?.message ?? error.message,
          error.response?.details ?? {},
        );
      }
      throw error;
    }
  }
}

export function createDefaultCategoryService() {
  return new ApiCategoryService(
    import.meta.env.VITE_API_BASE_URL ?? "",
  );
}

function mutationOptions(operation: string) {
  const randomPart = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return {
    actorLabel: "Test Library UI",
    idempotencyKey: `${operation}-${randomPart}`,
  };
}
