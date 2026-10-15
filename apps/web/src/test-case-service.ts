import type {
  DemoTestCase,
  DemoWorkItem,
  DomainId,
  TestCaseKey,
  TestCasePriority,
  TestStep,
} from "./test-management-demo";

export interface NewTestCaseInput {
  summary: string;
  priority: TestCasePriority;
  owner: string;
  labels: string[];
  steps: Array<Pick<TestStep, "precondition" | "action" | "expectedResult">>;
}

export interface TestCaseQuery {
  search?: string;
  categoryIds?: DomainId[];
  statuses?: Array<"ACTIVE" | "DEPRECATED">;
  priorities?: Array<"LOW" | "MEDIUM" | "HIGH" | "CRITICAL">;
  labels?: string[];
  labelMatch?: "ANY" | "ALL";
  includeDeleted?: boolean;
  deletedOnly?: boolean;
  cursor?: string;
  limit?: number;
}

export interface CaseLibrarySnapshot {
  testCases: DemoTestCase[];
  nextCursor: string | null;
  total: number;
  source: "API";
}

export interface CaseDetailResult {
  testCase: DemoTestCase;
  partialFailure?: string;
}

export interface TestCaseService {
  list(query?: TestCaseQuery): Promise<CaseLibrarySnapshot>;
  get(caseKey: TestCaseKey): Promise<CaseDetailResult>;
  create(workItem: DemoWorkItem, input?: NewTestCaseInput): Promise<DemoTestCase>;
  update(testCase: DemoTestCase): Promise<DemoTestCase>;
  move(
    caseKey: TestCaseKey,
    targetWorkItem: DemoWorkItem,
    targetSortOrder: number,
  ): Promise<DemoTestCase>;
  copy(caseKey: TestCaseKey, targetWorkItem: DemoWorkItem): Promise<DemoTestCase>;
  softDelete(caseKey: TestCaseKey): Promise<DemoTestCase>;
  restore(caseKey: TestCaseKey): Promise<DemoTestCase>;
  restoreVersion(caseKey: TestCaseKey, version: number): Promise<CaseDetailResult>;
}

export type TestCaseServiceErrorCode =
  | "CURSOR_INVALID"
  | "EXPECTED_RESULT_MODE_INVALID"
  | "IDEMPOTENCY_CONFLICT"
  | "LABEL_CONFLICT"
  | "PERMANENT_DELETE_CONFLICT"
  | "RESOURCE_NOT_FOUND"
  | "TEST_CASE_ALREADY_DELETED"
  | "TEST_CASE_COPY_CONFLICT"
  | "TEST_CASE_MOVE_CONFLICT"
  | "TEST_CASE_NOT_DELETED"
  | "TEST_CASE_RESTORE_CONFLICT"
  | "VALIDATION_FAILED"
  | "VERSION_CONFLICT";

export class TestCaseServiceError extends Error {
  constructor(
    public readonly code: TestCaseServiceErrorCode,
    message: string,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "TestCaseServiceError";
  }
}
