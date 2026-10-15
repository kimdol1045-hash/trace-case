/* eslint-disable react-refresh/only-export-components -- demo fixture, reducer and provider intentionally share one in-memory module */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useState,
  type PropsWithChildren,
} from "react";

import { createDefaultTestCaseService } from "./api-test-case-service";
import {
  CategoryServiceError,
  createDefaultCategoryService,
  type CategoryService,
} from "./category-service";
import {
  TestCaseServiceError,
  type NewTestCaseInput,
  type TestCaseQuery,
  type TestCaseService,
} from "./test-case-service";
import {
  createDefaultWorkItemService,
  WorkItemServiceError,
  type WorkItemDetailResult,
  type WorkItemService,
} from "./work-item-service";
import type { GenerationSuggestion } from "./generation-suggestion-service";

export type DomainId = `domain_${string}` | `folder_${string}`;
export type TestCaseKey = `${string}-${number}`;
export type IssueKey = `ISSUE-${number}`;
export type CycleId = `cycle_${string}`;
export type WorkItemKey = `WORK-${number}`;
export type TestCaseStatus = "DRAFT" | "ACTIVE" | "DEPRECATED";
export type TestCasePriority = "HIGHEST" | "HIGH" | "MEDIUM" | "LOW";
export type ExecutionStatus = "UNEXECUTED" | "PASS" | "FAIL" | "BLOCKED";
export type CycleStatus = "PLANNED" | "IN PROGRESS" | "COMPLETED";
export type CycleScopeType = "WORK_ITEM" | "LABEL";

export interface TestDomain {
  id: DomainId;
  parentId: DomainId | null;
  name: string;
  sortOrder: number;
  resourceVersion?: number;
  activeTestCaseCount?: number;
  workItemCount?: number;
  protected?: boolean;
}

export interface TestStep {
  id: string;
  precondition?: string;
  action: string;
  expectedResult: string;
}

export interface TestCaseHistoryEntry {
  id: string;
  action: string;
  actor: string;
  occurredAt: string;
  changes?: Array<{ field: string; before: string; after: string }>;
  version?: number;
  restoredFromVersion?: number;
}

export interface DemoWorkItem {
  id?: `work_item_${string}`;
  key: WorkItemKey;
  summary: string;
  domainId: DomainId;
  description: string;
  owner: string;
  labels: string[];
  resourceVersion?: number;
  detailLoaded?: boolean;
  createdAt: string;
  updatedAt: string;
  history: TestCaseHistoryEntry[];
}

export interface NewWorkItemInput {
  domainId: DomainId;
  key: string;
  summary: string;
  description: string;
  owner: string;
  labels: string[];
}

export interface DemoTestCase {
  id?: `case_${string}`;
  key: TestCaseKey;
  summary: string;
  domainId: DomainId;
  status: TestCaseStatus;
  priority: TestCasePriority;
  owner: string;
  labels: string[];
  precondition: string;
  steps: TestStep[];
  expectedResult: string;
  description: string;
  sourceWorkItem: string;
  linkedIssueKeys: IssueKey[];
  version: number;
  resourceVersion?: number;
  workItemId?: `work_item_${string}`;
  sortOrder?: number;
  deletedAt?: string | null;
  expectedResultMode?: "PER_STEP" | "CASE";
  generationManagement?: {
    managed: boolean;
    lineageId?: string;
    releasedAt?: string;
    releaseReason?: string;
    releaseMutation?: string;
  };
  generationSuggestions?: GenerationSuggestion[];
  copiedFrom?: TestCaseKey;
  versions?: Array<{
    version: number;
    changeReason: string;
    createdAt: string;
    restoredFromVersion?: number;
  }>;
  createdAt: string;
  updatedAt: string;
  history: TestCaseHistoryEntry[];
}

export interface DemoAttachment {
  id: string;
  name: string;
  size: string;
}

export interface DemoIssue {
  key: IssueKey;
  cycleId: CycleId;
  summary: string;
  status: "TO DO" | "IN PROGRESS" | "DONE";
  priority: TestCasePriority;
  assignee: string;
  reporter: string;
  labels: string[];
  precondition: string;
  testSteps: string;
  expectedResult: string;
  actualResult: string;
  attachments: DemoAttachment[];
  linkedTestCaseKeys: TestCaseKey[];
  createdAt: string;
  updatedAt: string;
}

export interface DemoExecution {
  id: string;
  testCaseKey: TestCaseKey;
  status: ExecutionStatus;
  platformStatuses: Record<"WEB" | "AOS" | "IOS", ExecutionStatus>;
  executedBy: string;
  note: string;
  issueKey?: IssueKey;
}

export interface DemoCycle {
  id: CycleId;
  name: string;
  status: CycleStatus;
  startDate: string;
  description: string;
  scopeType: CycleScopeType;
  scopeValue: string;
  executions: DemoExecution[];
}

export interface TestManagementDemoState {
  domains: TestDomain[];
  workItems: DemoWorkItem[];
  testCases: DemoTestCase[];
  issues: DemoIssue[];
  cycles: DemoCycle[];
  nextCaseNumber: number;
  nextIssueNumber: number;
  nextCycleNumber: number;
}

/**
 * Production routes start empty and are hydrated only from the API. The populated
 * fixture below is retained solely for the explicitly enabled development cycle
 * fallback and focused component tests.
 */
export const emptyTestManagementState: TestManagementDemoState = {
  domains: [],
  workItems: [],
  testCases: [],
  issues: [],
  cycles: [],
  nextCaseNumber: 1,
  nextIssueNumber: 1,
  nextCycleNumber: 1,
};

export const initialTestManagementState: TestManagementDemoState = {
  domains: [
    { id: "domain_members", parentId: null, name: "회원", sortOrder: 0 },
    { id: "domain_orders", parentId: null, name: "주문", sortOrder: 1 },
    { id: "domain_unassigned", parentId: null, name: "미분류", sortOrder: 2 },
  ],
  workItems: [
    { key: "WORK-482", summary: "카드 결제 승인 흐름 개선", domainId: "domain_orders", description: "카드 결제 승인·실패·재시도 흐름의 품질 범위를 관리합니다.", owner: "박서연", labels: ["payment", "sprint-22"], createdAt: "2026-08-10T02:00:00.000Z", updatedAt: "2026-08-20T05:42:00.000Z", history: [{ id: "work_history_482_1", action: "실패·재시도 범위 추가", actor: "박서연", occurredAt: "2026-08-20T05:42:00.000Z", changes: [{ field: "설명", before: "결제 승인 흐름", after: "승인·실패·재시도 흐름" }] }] },
    { key: "WORK-466", summary: "이메일 인증 개편", domainId: "domain_members", description: "회원가입부터 이메일 인증과 최초 로그인까지 검증합니다.", owner: "이도윤", labels: ["account", "regression"], createdAt: "2026-08-03T01:00:00.000Z", updatedAt: "2026-08-18T02:25:00.000Z", history: [{ id: "work_history_466_1", action: "상위 일감 생성", actor: "이도윤", occurredAt: "2026-08-03T01:00:00.000Z" }] },
    { key: "WORK-390", summary: "휴면 정책 이전", domainId: "domain_members", description: "기존 휴면 정책의 복구 안내 회귀 범위를 관리합니다.", owner: "김형유", labels: ["account", "legacy"], createdAt: "2026-07-01T01:00:00.000Z", updatedAt: "2026-08-14T01:10:00.000Z", history: [{ id: "work_history_390_1", action: "정책 이전 일정 변경", actor: "김형유", occurredAt: "2026-08-14T01:10:00.000Z" }] },
    { key: "WORK-511", summary: "배송비 정책 확장", domainId: "domain_orders", description: "지역별 배송비와 안내 문구 정책을 검증합니다.", owner: "김형유", labels: ["delivery", "regression"], createdAt: "2026-08-19T01:00:00.000Z", updatedAt: "2026-08-20T00:30:00.000Z", history: [{ id: "work_history_511_1", action: "제주 정책 조건 추가", actor: "김형유", occurredAt: "2026-08-20T00:30:00.000Z" }] },
    { key: "WORK-530", summary: "쿠폰 정책 정리", domainId: "domain_unassigned", description: "쿠폰 중복 적용 결정 전 테스트 후보를 정리합니다.", owner: "박서연", labels: ["coupon", "policy"], createdAt: "2026-08-20T04:00:00.000Z", updatedAt: "2026-08-20T04:20:00.000Z", history: [{ id: "work_history_530_1", action: "상위 일감 생성", actor: "박서연", occurredAt: "2026-08-20T04:00:00.000Z" }] },
  ],
  testCases: [
    {
      key: "TC-1042",
      summary: "카드 결제 승인 시 주문이 생성된다",
      domainId: "domain_orders",
      status: "ACTIVE",
      priority: "HIGHEST",
      owner: "김형유",
      labels: ["payment", "smoke"],
      precondition: "로그인한 사용자의 장바구니에 판매 중인 상품이 1개 이상 담겨 있다.",
      steps: [
        { id: "step_1042_1", action: "주문서에서 신용카드를 결제수단으로 선택한다.", expectedResult: "카드 결제 입력 영역이 활성화된다." },
        { id: "step_1042_2", action: "필수 약관에 동의하고 결제하기를 누른다.", expectedResult: "PG 승인 요청이 한 번 전송된다." },
        { id: "step_1042_3", action: "PG 승인 응답 이후 주문 완료 화면을 확인한다.", expectedResult: "주문번호와 결제완료 상태가 표시된다." },
      ],
      expectedResult: "결제가 승인되고 주문번호가 발급되며 주문 상태가 결제완료로 저장된다.",
      description: "결제 승인과 주문 생성의 핵심 happy path를 검증한다.",
      sourceWorkItem: "WORK-482 · 카드 결제 승인 흐름 개선",
      linkedIssueKeys: ["ISSUE-104"],
      version: 4,
      createdAt: "2026-08-11T02:10:00.000Z",
      updatedAt: "2026-08-20T05:42:00.000Z",
      history: [
        { id: "history_1042_2", action: "테스트 동작과 기대결과 수정", actor: "김형유", occurredAt: "2026-08-20T05:42:00.000Z", changes: [{ field: "2번 테스트 동작", before: "결제하기를 누른다", after: "필수 약관에 동의하고 결제하기를 누른다" }, { field: "2번 기대 결과", before: "PG 승인 요청 전송", after: "PG 승인 요청이 한 번 전송된다" }] },
        { id: "history_1042_1", action: "테스트케이스 생성", actor: "김형유", occurredAt: "2026-08-11T02:10:00.000Z" },
      ],
    },
    {
      key: "TC-1043",
      summary: "잔액 부족 카드 결제는 실패 안내를 표시한다",
      domainId: "domain_orders",
      status: "ACTIVE",
      priority: "HIGH",
      owner: "박서연",
      labels: ["payment", "negative"],
      precondition: "장바구니와 주문서가 생성되어 있고 잔액 부족 응답용 테스트 카드를 사용할 수 있다.",
      steps: [
        { id: "step_1043_1", action: "잔액 부족 테스트 카드로 결제를 요청한다.", expectedResult: "결제 실패 응답을 수신한다." },
        { id: "step_1043_2", action: "결제 실패 안내와 주문 상태를 확인한다.", expectedResult: "실패 사유가 표시되고 결제대기를 유지한다." },
      ],
      expectedResult: "실패 사유가 사용자에게 표시되고 주문은 결제대기 상태를 유지한다.",
      description: "PG 승인 실패 시 주문 정합성과 사용자 안내를 검증한다.",
      sourceWorkItem: "WORK-482 · 카드 결제 승인 흐름 개선",
      linkedIssueKeys: ["ISSUE-104"],
      version: 2,
      createdAt: "2026-08-12T03:20:00.000Z",
      updatedAt: "2026-08-19T08:10:00.000Z",
      history: [
        { id: "history_1043_1", action: "테스트케이스 생성", actor: "박서연", occurredAt: "2026-08-12T03:20:00.000Z" },
      ],
    },
    {
      key: "TC-1031",
      summary: "이메일 가입 사용자는 인증 후 로그인할 수 있다",
      domainId: "domain_members",
      status: "ACTIVE",
      priority: "HIGH",
      owner: "이도윤",
      labels: ["account", "regression"],
      precondition: "미가입 이메일 주소와 수신 가능한 테스트 메일함이 준비되어 있다.",
      steps: [
        { id: "step_1031_1", action: "이메일로 회원가입을 완료한다.", expectedResult: "인증 대기 계정과 인증 메일이 생성된다." },
        { id: "step_1031_2", action: "인증 메일 링크를 열고 로그인을 시도한다.", expectedResult: "이메일 인증 후 로그인에 성공한다." },
      ],
      expectedResult: "이메일 인증 상태가 저장되고 최초 로그인에 성공한다.",
      description: "회원가입부터 이메일 인증, 로그인까지의 연결 흐름을 검증한다.",
      sourceWorkItem: "WORK-466 · 이메일 인증 개편",
      linkedIssueKeys: [],
      version: 3,
      createdAt: "2026-08-04T01:00:00.000Z",
      updatedAt: "2026-08-18T02:25:00.000Z",
      history: [
        { id: "history_1031_1", action: "테스트케이스 생성", actor: "이도윤", occurredAt: "2026-08-04T01:00:00.000Z" },
      ],
    },
    {
      key: "TC-1024",
      summary: "휴면 계정은 로그인 시 복구 안내를 받는다",
      domainId: "domain_members",
      status: "DEPRECATED",
      priority: "MEDIUM",
      owner: "김형유",
      labels: ["account", "legacy"],
      precondition: "휴면 전환된 테스트 계정이 존재한다.",
      steps: [{ id: "step_1024_1", action: "휴면 계정으로 로그인을 시도한다.", expectedResult: "계정 복구 안내가 표시된다." }],
      expectedResult: "로그인 대신 본인확인 기반 계정 복구 안내가 표시된다.",
      description: "구형 휴면 정책 회귀 테스트이며 신규 정책 전환 후 폐기 예정이다.",
      sourceWorkItem: "WORK-390 · 휴면 정책 이전",
      linkedIssueKeys: [],
      version: 6,
      createdAt: "2026-07-02T01:00:00.000Z",
      updatedAt: "2026-08-14T01:10:00.000Z",
      history: [{ id: "history_1024_1", action: "상태를 Deprecated로 변경", actor: "김형유", occurredAt: "2026-08-14T01:10:00.000Z" }],
    },
    {
      key: "TC-1018",
      summary: "배송지 변경 시 배송비를 다시 계산한다",
      domainId: "domain_orders",
      status: "DRAFT",
      priority: "MEDIUM",
      owner: "미지정",
      labels: ["delivery", "regression"],
      precondition: "배송 상품이 주문서에 포함되어 있다.",
      steps: [{ id: "step_1018_1", action: "제주 지역 배송지로 변경한다.", expectedResult: "도서산간 추가 배송비가 합계에 반영된다." }],
      expectedResult: "도서산간 추가 배송비가 합계 금액에 반영된다.",
      description: "배송 정책 결정사항을 반영하는 수동 작성 초안이다.",
      sourceWorkItem: "WORK-511 · 배송비 정책 확장",
      linkedIssueKeys: [],
      version: 1,
      createdAt: "2026-08-20T00:30:00.000Z",
      updatedAt: "2026-08-20T00:30:00.000Z",
      history: [{ id: "history_1018_1", action: "테스트케이스 초안 생성", actor: "김형유", occurredAt: "2026-08-20T00:30:00.000Z" }],
    },
    {
      key: "TC-1099",
      summary: "쿠폰 중복 적용 제한을 확인한다",
      domainId: "domain_unassigned",
      status: "DRAFT",
      priority: "LOW",
      owner: "미지정",
      labels: ["coupon", "policy"],
      precondition: "동일 주문에 적용 가능한 장바구니 쿠폰과 상품 쿠폰이 존재한다.",
      steps: [{ id: "step_1099_1", action: "두 쿠폰을 순서대로 선택한다.", expectedResult: "중복 적용 정책에 따라 제한 사유가 표시된다." }],
      expectedResult: "결정된 중복 적용 정책에 따라 허용 또는 제한 사유가 표시된다.",
      description: "쿠폰 중복 적용 정책을 확인하기 위한 수동 작성 초안이다.",
      sourceWorkItem: "WORK-530 · 쿠폰 정책 정리",
      linkedIssueKeys: [],
      version: 1,
      createdAt: "2026-08-20T04:20:00.000Z",
      updatedAt: "2026-08-20T04:20:00.000Z",
      history: [{ id: "history_1099_1", action: "테스트케이스 초안 생성", actor: "박서연", occurredAt: "2026-08-20T04:20:00.000Z" }],
    },
  ],
  issues: [
    {
      key: "ISSUE-104",
      cycleId: "cycle_sprint_22",
      summary: "잔액 부족 응답 이후 결제 버튼이 비활성 상태로 남는다",
      status: "IN PROGRESS",
      priority: "HIGHEST",
      assignee: "오지훈",
      reporter: "박서연",
      labels: ["payment", "regression"],
      precondition: "로그인한 사용자의 주문서가 열려 있고 잔액 부족 테스트 카드를 사용할 수 있다.",
      testSteps: "1. 잔액 부족 테스트 카드로 결제를 요청한다.\n2. 실패 안내를 닫는다.\n3. 다른 카드로 결제를 다시 시도한다.",
      expectedResult: "실패 안내를 닫으면 결제 버튼이 다시 활성화되어 재시도할 수 있어야 한다.",
      actualResult: "실패 안내를 닫은 뒤에도 결제 버튼이 disabled 상태로 유지되어 새로고침 전까지 재시도할 수 없다.",
      attachments: [
        { id: "attachment_1", name: "payment-button-disabled.png", size: "412 KB" },
        { id: "attachment_2", name: "pg-insufficient-funds.json", size: "8 KB" },
      ],
      linkedTestCaseKeys: ["TC-1042", "TC-1043"],
      createdAt: "2026-08-19T07:30:00.000Z",
      updatedAt: "2026-08-20T02:12:00.000Z",
    },
    {
      key: "ISSUE-105",
      cycleId: "cycle_regression_18",
      summary: "제주 배송지 선택 시 배송비 안내 문구가 갱신되지 않는다",
      status: "TO DO",
      priority: "MEDIUM",
      assignee: "미지정",
      reporter: "김형유",
      labels: ["delivery"],
      precondition: "배송 상품이 포함된 주문서가 열려 있다.",
      testSteps: "1. 기본 배송지를 서울로 선택한다.\n2. 배송지를 제주로 변경한다.\n3. 배송비 합계와 안내 문구를 확인한다.",
      expectedResult: "추가 배송비와 도서산간 안내 문구가 함께 갱신된다.",
      actualResult: "합계 금액은 변경되지만 안내 문구는 기본 배송비 문구로 남는다.",
      attachments: [{ id: "attachment_3", name: "delivery-copy.mov", size: "1.8 MB" }],
      linkedTestCaseKeys: ["TC-1018"],
      createdAt: "2026-08-20T01:00:00.000Z",
      updatedAt: "2026-08-20T01:00:00.000Z",
    },
  ],
  cycles: [
    {
      id: "cycle_sprint_22",
      name: "카드 결제 승인 QA",
      status: "IN PROGRESS",
      startDate: "2026-08-18",
      description: "WORK-482의 승인·실패·재시도 범위를 검증합니다.",
      scopeType: "WORK_ITEM",
      scopeValue: "WORK-482",
      executions: [
        { id: "exec_1", testCaseKey: "TC-1042", status: "PASS", platformStatuses: { WEB: "PASS", AOS: "PASS", IOS: "UNEXECUTED" }, executedBy: "김형유", note: "iOS 결제 완료 화면 확인 중" },
        { id: "exec_2", testCaseKey: "TC-1043", status: "FAIL", platformStatuses: { WEB: "FAIL", AOS: "PASS", IOS: "BLOCKED" }, executedBy: "박서연", note: "iOS 테스트 카드 발급 지연으로 BLOCKED", issueKey: "ISSUE-104" },
      ],
    },
    {
      id: "cycle_regression_18",
      name: "회귀 라벨 점검",
      status: "PLANNED",
      startDate: "2026-08-23",
      description: "regression 라벨을 가진 케이스의 회귀 범위를 실행합니다.",
      scopeType: "LABEL",
      scopeValue: "regression",
      executions: [
        { id: "exec_3", testCaseKey: "TC-1031", status: "UNEXECUTED", platformStatuses: { WEB: "UNEXECUTED", AOS: "UNEXECUTED", IOS: "UNEXECUTED" }, executedBy: "미지정", note: "" },
        { id: "exec_4", testCaseKey: "TC-1018", status: "UNEXECUTED", platformStatuses: { WEB: "UNEXECUTED", AOS: "UNEXECUTED", IOS: "UNEXECUTED" }, executedBy: "미지정", note: "" },
      ],
    },
    {
      id: "cycle_hotfix_17",
      name: "이메일 인증 완료 검증",
      status: "COMPLETED",
      startDate: "2026-08-08",
      description: "WORK-466 이메일 인증 흐름 검증을 완료했습니다.",
      scopeType: "WORK_ITEM",
      scopeValue: "WORK-466",
      executions: [
        { id: "exec_5", testCaseKey: "TC-1031", status: "PASS", platformStatuses: { WEB: "PASS", AOS: "PASS", IOS: "PASS" }, executedBy: "이도윤", note: "전체 플랫폼 통과" },
      ],
    },
  ],
  nextCaseNumber: 1100,
  nextIssueNumber: 106,
  nextCycleNumber: 23,
};

export type TestManagementDemoAction =
  | { type: "SAVE_ISSUE"; issue: DemoIssue }
  | { type: "SAVE_CYCLE"; cycle: DemoCycle }
  | { type: "CREATE_ISSUE"; issue: DemoIssue }
  | { type: "DELETE_ISSUE"; issueKey: IssueKey }
  | { type: "CREATE_CYCLE"; cycle: DemoCycle }
  | { type: "DELETE_CYCLE"; cycleId: CycleId }
  | {
      type: "HYDRATE_CASE_LIBRARY";
      domains: TestDomain[];
      workItems: DemoWorkItem[];
      testCases: DemoTestCase[];
      append: boolean;
    }
  | { type: "HYDRATE_LIBRARY_PROJECTION"; domains: TestDomain[]; workItems: DemoWorkItem[] }
  | { type: "UPSERT_API_CASE"; testCase: DemoTestCase }
  | { type: "UPSERT_API_WORK_ITEM"; workItem: DemoWorkItem; testCases?: DemoTestCase[] };

export type CasePersistenceStatus = "LOADING" | "READY" | "SAVING" | "ERROR" | "CONFLICT" | "PARTIAL";

export interface CasePersistenceState {
  status: CasePersistenceStatus;
  source: "API";
  message?: string;
  nextCursor: string | null;
  total: number;
}

export function testManagementDemoReducer(
  state: TestManagementDemoState,
  action: TestManagementDemoAction,
): TestManagementDemoState {
  switch (action.type) {
    case "SAVE_ISSUE":
      return {
        ...state,
        issues: state.issues.map((issue) =>
          issue.key === action.issue.key ? action.issue : issue,
        ),
      };
    case "SAVE_CYCLE":
      return {
        ...state,
        cycles: state.cycles.map((cycle) =>
          cycle.id === action.cycle.id ? action.cycle : cycle,
        ),
      };
    case "CREATE_ISSUE":
      return { ...state, nextIssueNumber: state.nextIssueNumber + 1, issues: [action.issue, ...state.issues] };
    case "DELETE_ISSUE":
      return {
        ...state,
        issues: state.issues.filter((issue) => issue.key !== action.issueKey),
        testCases: state.testCases.map((testCase) => ({ ...testCase, linkedIssueKeys: testCase.linkedIssueKeys.filter((key) => key !== action.issueKey) })),
        cycles: state.cycles.map((cycle) => ({
          ...cycle,
          executions: cycle.executions.map((execution) => execution.issueKey === action.issueKey ? { ...execution, issueKey: undefined } : execution),
        })),
      };
    case "CREATE_CYCLE":
      return { ...state, nextCycleNumber: state.nextCycleNumber + 1, cycles: [action.cycle, ...state.cycles] };
    case "DELETE_CYCLE":
      return {
        ...state,
        cycles: state.cycles.filter((cycle) => cycle.id !== action.cycleId),
        issues: state.issues.filter((issue) => issue.cycleId !== action.cycleId),
      };
    case "HYDRATE_CASE_LIBRARY": {
      const incomingCases = action.testCases.map((testCase) => {
        const current = state.testCases.find((candidate) => candidate.key === testCase.key);
        if (
          !current
          || current.version !== testCase.version
          || current.steps.length === 0
          || testCase.steps.length > 0
        ) return testCase;
        return {
          ...testCase,
          precondition: current.precondition,
          steps: current.steps,
          expectedResult: current.expectedResult,
          expectedResultMode: current.expectedResultMode,
          description: current.description,
          history: current.history,
          versions: current.versions,
        };
      });
      const workItems = action.workItems.map((incoming) => {
        const current = state.workItems.find((item) => item.key === incoming.key);
        return current?.detailLoaded && current.resourceVersion === incoming.resourceVersion
          ? { ...incoming, history: current.history, detailLoaded: true }
          : incoming;
      });
      const detailedKeys = new Set(
        workItems.filter((item) => item.detailLoaded).map((item) => item.key),
      );
      const testCases = action.append
        ? [
            ...state.testCases,
            ...incomingCases.filter(
              (testCase) => !state.testCases.some((current) => current.key === testCase.key),
            ),
          ]
        : [
            ...incomingCases,
            ...state.testCases.filter(
              (testCase) => detailedKeys.has(getWorkItemKey(testCase.sourceWorkItem))
                && !incomingCases.some((incoming) => incoming.key === testCase.key),
            ),
          ];
      return {
        ...state,
        domains: action.domains,
        workItems,
        testCases,
      };
    }
    case "HYDRATE_LIBRARY_PROJECTION": {
      const workItems = action.workItems.map((incoming) => {
        const current = state.workItems.find((item) => item.key === incoming.key);
        return current?.detailLoaded && current.resourceVersion === incoming.resourceVersion
          ? { ...incoming, history: current.history, detailLoaded: true }
          : incoming;
      });
      return {
        ...state,
        domains: action.domains,
        workItems,
      };
    }
    case "UPSERT_API_CASE":
      return {
        ...state,
        testCases: state.testCases.some((testCase) => testCase.key === action.testCase.key)
          ? state.testCases.map((testCase) => testCase.key === action.testCase.key ? action.testCase : testCase)
          : [...state.testCases, action.testCase],
      };
    case "UPSERT_API_WORK_ITEM": {
      const current = state.workItems.find((item) => item.key === action.workItem.key);
      const nextWorkItem = {
        ...action.workItem,
        detailLoaded: action.testCases !== undefined || current?.detailLoaded,
      };
      const workItems = current
        ? state.workItems.map((item) => item.key === action.workItem.key ? nextWorkItem : item)
        : [nextWorkItem, ...state.workItems];
      if (!action.testCases) {
        return {
          ...state,
          workItems,
          testCases: state.testCases.map((testCase) => (
            getWorkItemKey(testCase.sourceWorkItem) === action.workItem.key
              ? {
                  ...testCase,
                  domainId: action.workItem.domainId,
                  sourceWorkItem: `${action.workItem.key} · ${action.workItem.summary}`,
                }
              : testCase
          )),
        };
      }
      return {
        ...state,
        workItems,
        testCases: [
          ...state.testCases.filter(
            (testCase) => getWorkItemKey(testCase.sourceWorkItem) !== action.workItem.key,
          ),
          ...action.testCases,
        ],
      };
    }
  }
}

export function reorderTestSteps(steps: TestStep[], sourceId: string, targetId: string) {
  const sourceIndex = steps.findIndex((step) => step.id === sourceId);
  const targetIndex = steps.findIndex((step) => step.id === targetId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return steps;
  const next = [...steps];
  const [source] = next.splice(sourceIndex, 1);
  if (!source) return steps;
  next.splice(targetIndex, 0, source);
  return next;
}

export function getDomainPath(domains: TestDomain[], domainId: DomainId) {
  const byId = new Map(domains.map((domain) => [domain.id, domain]));
  const path: TestDomain[] = [];
  let cursor = byId.get(domainId);
  while (cursor) {
    path.unshift(cursor);
    cursor = cursor.parentId ? byId.get(cursor.parentId) : undefined;
  }
  return path;
}

export function getDomainScope(domains: TestDomain[], domainId: DomainId | null) {
  if (!domainId) return new Set(domains.map((domain) => domain.id));
  const scope = new Set<DomainId>([domainId]);
  let added = true;
  while (added) {
    added = false;
    for (const domain of domains) {
      if (domain.parentId && scope.has(domain.parentId) && !scope.has(domain.id)) {
        scope.add(domain.id);
        added = true;
      }
    }
  }
  return scope;
}

export function getWorkItemKey(sourceWorkItem: string) {
  return sourceWorkItem.split(" · ")[0] as WorkItemKey;
}

export function normalizeWorkItemKey(value: string): WorkItemKey | null {
  const normalized = value.trim().toLocaleUpperCase("ko-KR");
  return /^WORK-\d+$/.test(normalized) ? normalized as WorkItemKey : null;
}

function describeCaseChanges(before: DemoTestCase | undefined, after: DemoTestCase) {
  if (!before) return [];
  const changes: Array<{ field: string; before: string; after: string }> = [];
  const add = (field: string, previous: string, next: string) => {
    if (previous !== next) changes.push({ field, before: previous || "비어 있음", after: next || "비어 있음" });
  };
  add("서머리", before.summary, after.summary);
  add("설명", before.description, after.description);
  add("사전조건", before.precondition, after.precondition);
  add("카테고리", before.domainId, after.domainId);
  add("상태", before.status, after.status);
  add("우선순위", before.priority, after.priority);
  add("담당자", before.owner, after.owner);
  add("라벨", before.labels.join(", "), after.labels.join(", "));
  add("기대 결과 모드", before.expectedResultMode ?? "PER_STEP", after.expectedResultMode ?? "PER_STEP");
  add("케이스 기대 결과", before.expectedResult, after.expectedResult);
  add("테스트 스텝", before.steps.map((step) => `${step.precondition ?? ""} → ${step.action} → ${step.expectedResult}`).join(" | "), after.steps.map((step) => `${step.precondition ?? ""} → ${step.action} → ${step.expectedResult}`).join(" | "));
  return changes;
}

interface TestManagementContextValue {
  state: TestManagementDemoState;
  persistence: CasePersistenceState;
  loadLibrary: (query?: TestCaseQuery, append?: boolean) => Promise<void>;
  loadCase: (caseKey: TestCaseKey) => Promise<void>;
  loadWorkItem: (workItemKey: WorkItemKey) => Promise<WorkItemDetailResult | null>;
  moveCase: (caseKey: TestCaseKey, domainId: DomainId) => Promise<void>;
  moveCaseToWorkItem: (caseKey: TestCaseKey, workItemKey: WorkItemKey) => Promise<void>;
  reorderCase: (caseKey: TestCaseKey, targetCaseKey: TestCaseKey) => Promise<void>;
  saveCase: (testCase: DemoTestCase) => Promise<boolean>;
  saveWorkItem: (workItem: DemoWorkItem) => Promise<boolean>;
  createWorkItem: (input: NewWorkItemInput) => Promise<WorkItemKey | null>;
  createCase: (workItemKey: WorkItemKey, input: NewTestCaseInput) => Promise<TestCaseKey | null>;
  copyCase: (caseKey: TestCaseKey) => Promise<TestCaseKey | null>;
  deleteCase: (caseKey: TestCaseKey) => Promise<boolean>;
  restoreCase: (caseKey: TestCaseKey) => Promise<boolean>;
  restoreCaseVersion: (caseKey: TestCaseKey, version: number) => Promise<boolean>;
  saveIssue: (issue: DemoIssue) => void;
  saveCycle: (cycle: DemoCycle) => void;
  renameDomain: (domainId: DomainId, name: string) => Promise<boolean>;
  createDomain: (name: string) => Promise<DomainId | null>;
  deleteDomain: (domainId: DomainId) => Promise<boolean>;
  createIssue: (cycleId: CycleId) => IssueKey;
  deleteIssue: (issueKey: IssueKey) => void;
  createCycle: (input?: CreateCycleInput) => CycleId;
  deleteCycle: (cycleId: CycleId) => void;
}

export interface CreateCycleInput {
  name?: string;
  description?: string;
  scopeType?: CycleScopeType;
  scopeValue?: string;
}

interface TestManagementStateContextValue {
  state: TestManagementDemoState;
  dispatch: React.Dispatch<TestManagementDemoAction>;
  saveIssue: (issue: DemoIssue) => void;
  saveCycle: (cycle: DemoCycle) => void;
  createIssue: (cycleId: CycleId) => IssueKey;
  deleteIssue: (issueKey: IssueKey) => void;
  createCycle: (input?: CreateCycleInput) => CycleId;
  deleteCycle: (cycleId: CycleId) => void;
}

const TestManagementStateContext = createContext<TestManagementStateContextValue | null>(null);
const TestManagementContext = createContext<TestManagementContextValue | null>(null);

function TestManagementStateProvider({
  children,
  initialState,
}: PropsWithChildren<{ initialState: TestManagementDemoState }>) {
  const [state, dispatch] = useReducer(testManagementDemoReducer, initialState);
  const value = useMemo<TestManagementStateContextValue>(() => ({
    state,
    dispatch,
    saveIssue: (issue) => dispatch({
      type: "SAVE_ISSUE",
      issue: { ...issue, updatedAt: "2026-08-20T06:30:00.000Z" },
    }),
    saveCycle: (cycle) => dispatch({ type: "SAVE_CYCLE", cycle }),
    createIssue: (cycleId) => {
      const key = `ISSUE-${state.nextIssueNumber}` as IssueKey;
      const cycle = state.cycles.find((candidate) => candidate.id === cycleId);
      const linkedTestCaseKeys = cycle?.executions[0] ? [cycle.executions[0].testCaseKey] : [];
      dispatch({
        type: "CREATE_ISSUE",
        issue: {
          key,
          cycleId,
          summary: "제목 없는 이슈",
          status: "TO DO",
          priority: "MEDIUM",
          assignee: "미지정",
          reporter: "QA 팀",
          labels: [],
          precondition: "",
          testSteps: "",
          expectedResult: "",
          actualResult: "",
          attachments: [],
          linkedTestCaseKeys,
          createdAt: "2026-08-20T07:00:00.000Z",
          updatedAt: "2026-08-20T07:00:00.000Z",
        },
      });
      return key;
    },
    deleteIssue: (issueKey) => dispatch({ type: "DELETE_ISSUE", issueKey }),
    createCycle: (input = {}) => {
      const id = `cycle_custom_${state.nextCycleNumber}` as CycleId;
      const scopeType = input.scopeType ?? "WORK_ITEM";
      const scopeValue = input.scopeValue ?? state.workItems[0]?.key ?? "WORK-482";
      const scopedCases = scopeType === "WORK_ITEM"
        ? state.testCases.filter((testCase) => getWorkItemKey(testCase.sourceWorkItem) === scopeValue)
        : state.testCases.filter((testCase) => testCase.labels.includes(scopeValue));
      dispatch({
        type: "CREATE_CYCLE",
        cycle: {
          id,
          name: input.name ?? `새 테스트 사이클 ${state.nextCycleNumber}`,
          status: "PLANNED",
          startDate: "2026-08-20",
          description: input.description ?? `${scopeValue} 범위 QA`,
          scopeType,
          scopeValue,
          executions: scopedCases.map((testCase, index) => ({ id: `${id}_exec_${index + 1}`, testCaseKey: testCase.key, status: "UNEXECUTED", platformStatuses: { WEB: "UNEXECUTED", AOS: "UNEXECUTED", IOS: "UNEXECUTED" }, executedBy: "미지정", note: "" })),
        },
      });
      return id;
    },
    deleteCycle: (cycleId) => dispatch({ type: "DELETE_CYCLE", cycleId }),
  }), [state]);

  return (
    <TestManagementStateContext.Provider value={value}>
      {children}
    </TestManagementStateContext.Provider>
  );
}

interface TestManagementProviderProps extends PropsWithChildren {
  caseService?: TestCaseService;
  workItemService?: WorkItemService;
  categoryService?: CategoryService;
  enableDevelopmentFixtures?: boolean;
}

export function TestManagementProvider({
  children,
  caseService,
  workItemService,
  categoryService,
  enableDevelopmentFixtures = false,
}: TestManagementProviderProps) {
  return (
    <TestManagementStateProvider
      initialState={enableDevelopmentFixtures ? initialTestManagementState : emptyTestManagementState}
    >
      <PersistenceBridge
        caseService={caseService}
        workItemService={workItemService}
        categoryService={categoryService}
      >
        {children}
      </PersistenceBridge>
    </TestManagementStateProvider>
  );
}

function PersistenceBridge({
  children,
  caseService,
  workItemService,
  categoryService,
}: TestManagementProviderProps) {
  const legacy = useContext(TestManagementStateContext);
  if (!legacy) throw new Error("persistence bridge requires the legacy state provider");
  const { dispatch, state } = legacy;
  const service = useMemo(
    () => caseService ?? createDefaultTestCaseService(),
    [caseService],
  );
  const workItems = useMemo(
    () => workItemService ?? createDefaultWorkItemService(),
    [workItemService],
  );
  const categories = useMemo(
    () => categoryService ?? createDefaultCategoryService(),
    [categoryService],
  );
  const [persistence, setPersistence] = useState<CasePersistenceState>({
    status: "LOADING",
    source: "API",
    nextCursor: null,
    total: state.testCases.length,
  });

  const showError = useCallback((error: unknown) => {
    const conflict = (
      error instanceof TestCaseServiceError
      || error instanceof WorkItemServiceError
      || error instanceof CategoryServiceError
    )
      && error.code === "VERSION_CONFLICT";
    setPersistence((current) => ({
      ...current,
      status: conflict ? "CONFLICT" : "ERROR",
      message: error instanceof Error ? error.message : "테스트케이스 요청에 실패했습니다.",
    }));
  }, []);

  const loadLibrary = useCallback(async (query: TestCaseQuery = {}, append = false) => {
    setPersistence((current) => ({ ...current, status: "LOADING", message: undefined }));
    try {
      const [snapshot, projection] = await Promise.all([
        service.list(query),
        categories.loadProjection(),
      ]);
      dispatch({
        type: "HYDRATE_CASE_LIBRARY",
        domains: projection.domains,
        workItems: projection.workItems,
        testCases: snapshot.testCases,
        append,
      });
      setPersistence({
        status: "READY",
        source: snapshot.source,
        nextCursor: snapshot.nextCursor,
        total: snapshot.total,
      });
    } catch (error) {
      showError(error);
    }
  }, [categories, dispatch, service, showError]);

  const reloadProjection = useCallback(async () => {
    const projection = await categories.loadProjection();
    dispatch({
      type: "HYDRATE_LIBRARY_PROJECTION",
      domains: projection.domains,
      workItems: projection.workItems,
    });
    return projection;
  }, [categories, dispatch]);

  const loadCase = useCallback(async (caseKey: TestCaseKey) => {
    setPersistence((current) => ({ ...current, status: "LOADING", message: undefined }));
    try {
      const result = await service.get(caseKey);
      dispatch({ type: "UPSERT_API_CASE", testCase: result.testCase });
      setPersistence((current) => ({
        ...current,
        status: result.partialFailure ? "PARTIAL" : "READY",
        message: result.partialFailure,
      }));
    } catch (error) {
      showError(error);
    }
  }, [dispatch, service, showError]);

  const loadWorkItem = useCallback(async (workItemKey: WorkItemKey) => {
    setPersistence((current) => ({ ...current, status: "LOADING", message: undefined }));
    try {
      const result = await workItems.get(workItemKey);
      dispatch({
        type: "UPSERT_API_WORK_ITEM",
        workItem: result.workItem,
        testCases: result.testCases,
      });
      setPersistence((current) => ({ ...current, status: "READY", message: undefined }));
      return result;
    } catch (error) {
      showError(error);
      return null;
    }
  }, [dispatch, showError, workItems]);

  useEffect(() => {
    void loadLibrary({ limit: 50 });
  }, [loadLibrary]);

  const beginMutation = useCallback(() => {
    setPersistence((current) => ({ ...current, status: "SAVING", message: undefined }));
  }, []);
  const finishMutation = useCallback(() => {
    setPersistence((current) => ({ ...current, status: "READY", message: undefined }));
  }, []);

  const value = useMemo<TestManagementContextValue>(() => ({
    ...legacy,
    persistence,
    loadLibrary,
    loadCase,
    loadWorkItem,
    renameDomain: async (domainId, name) => {
      const category = state.domains.find((candidate) => candidate.id === domainId);
      if (!category) return false;
      beginMutation();
      try {
        await categories.rename(category, name.trim());
        await reloadProjection();
        finishMutation();
        return true;
      } catch (error) {
        showError(error);
        return false;
      }
    },
    createDomain: async (name) => {
      beginMutation();
      try {
        const rootCount = state.domains.filter((domain) => domain.parentId === null).length;
        const id = await categories.create(name.trim(), rootCount);
        await reloadProjection();
        finishMutation();
        return id;
      } catch (error) {
        showError(error);
        return null;
      }
    },
    deleteDomain: async (domainId) => {
      const category = state.domains.find((candidate) => candidate.id === domainId);
      if (!category) return false;
      beginMutation();
      try {
        await categories.delete(category);
        await reloadProjection();
        finishMutation();
        return true;
      } catch (error) {
        showError(error);
        return false;
      }
    },
    moveCase: async (caseKey, domainId) => {
      const target = state.workItems.find((candidate) => candidate.domainId === domainId);
      if (!target) {
        showError(new TestCaseServiceError("VALIDATION_FAILED", "대상 카테고리에 상위 일감이 없습니다."));
        return;
      }
      beginMutation();
      try {
        await service.move(caseKey, target, 0);
        await loadLibrary({ limit: 50 });
      } catch (error) {
        showError(error);
      }
    },
    moveCaseToWorkItem: async (caseKey, workItemKey) => {
      const workItem = state.workItems.find((candidate) => candidate.key === workItemKey);
      if (!workItem) return;
      const targetOrder = state.testCases.filter(
        (testCase) => getWorkItemKey(testCase.sourceWorkItem) === workItemKey
          && testCase.key !== caseKey
          && testCase.deletedAt == null,
      ).length;
      beginMutation();
      try {
        await service.move(caseKey, workItem, targetOrder);
        await loadLibrary({ limit: 50 });
      } catch (error) {
        showError(error);
      }
    },
    reorderCase: async (caseKey, targetCaseKey) => {
      const sourceCase = state.testCases.find((candidate) => candidate.key === caseKey);
      const targetCase = state.testCases.find((candidate) => candidate.key === targetCaseKey);
      const workItem = targetCase
        ? state.workItems.find(
            (candidate) => candidate.key === getWorkItemKey(targetCase.sourceWorkItem),
          )
        : undefined;
      if (!sourceCase || !targetCase || !workItem || caseKey === targetCaseKey) return;
      const targetSiblings = state.testCases.filter(
        (testCase) => getWorkItemKey(testCase.sourceWorkItem) === workItem.key
          && testCase.deletedAt == null,
      );
      const targetOrder = targetSiblings.findIndex((testCase) => testCase.key === targetCaseKey);
      if (targetOrder < 0) return;
      beginMutation();
      try {
        await service.move(caseKey, workItem, targetOrder);
        await loadLibrary({ limit: 50 });
      } catch (error) {
        showError(error);
      }
    },
    saveCase: async (testCase) => {
      const previous = state.testCases.find((candidate) => candidate.key === testCase.key);
      if (describeCaseChanges(previous, testCase).length === 0) return true;
      beginMutation();
      try {
        const updated = await service.update(testCase);
        dispatch({ type: "UPSERT_API_CASE", testCase: updated });
        const detail = await service.get(testCase.key);
        dispatch({ type: "UPSERT_API_CASE", testCase: detail.testCase });
        setPersistence((current) => ({
          ...current,
          status: detail.partialFailure ? "PARTIAL" : "READY",
          message: detail.partialFailure,
        }));
        return true;
      } catch (error) {
        showError(error);
        return false;
      }
    },
    saveWorkItem: async (workItem) => {
      const previous = state.workItems.find((candidate) => candidate.key === workItem.key);
      const changed = previous && (
        previous.summary !== workItem.summary
        || previous.description !== workItem.description
        || previous.domainId !== workItem.domainId
        || previous.owner !== workItem.owner
        || previous.labels.join("\u0000") !== workItem.labels.join("\u0000")
      );
      if (!changed) return true;
      beginMutation();
      try {
        await workItems.update(workItem);
        const detail = await workItems.get(workItem.key);
        dispatch({
          type: "UPSERT_API_WORK_ITEM",
          workItem: detail.workItem,
          testCases: detail.testCases,
        });
        finishMutation();
        return true;
      } catch (error) {
        showError(error);
        return false;
      }
    },
    createWorkItem: async (input) => {
      beginMutation();
      try {
        const created = await workItems.create(input);
        dispatch({ type: "UPSERT_API_WORK_ITEM", workItem: created });
        finishMutation();
        return created.key;
      } catch (error) {
        showError(error);
        return null;
      }
    },
    createCase: async (workItemKey, input) => {
      const workItem = state.workItems.find((candidate) => candidate.key === workItemKey);
      if (!workItem) {
        showError(new TestCaseServiceError(
          "VALIDATION_FAILED",
          "선택한 상위 일감을 찾을 수 없습니다.",
        ));
        return null;
      }
      beginMutation();
      try {
        const created = await service.create(workItem, input);
        dispatch({ type: "UPSERT_API_CASE", testCase: created });
        finishMutation();
        return created.key;
      } catch (error) {
        showError(error);
        return null;
      }
    },
    copyCase: async (caseKey) => {
      const source = state.testCases.find((candidate) => candidate.key === caseKey);
      const workItem = source
        ? state.workItems.find(
            (candidate) => candidate.key === getWorkItemKey(source.sourceWorkItem),
          )
        : undefined;
      if (!workItem) return null;
      beginMutation();
      try {
        const copied = await service.copy(caseKey, workItem);
        dispatch({ type: "UPSERT_API_CASE", testCase: copied });
        finishMutation();
        return copied.key;
      } catch (error) {
        showError(error);
        return null;
      }
    },
    deleteCase: async (caseKey) => {
      beginMutation();
      try {
        await service.softDelete(caseKey);
        await loadLibrary({ limit: 50 });
        return true;
      } catch (error) {
        showError(error);
        return false;
      }
    },
    restoreCase: async (caseKey) => {
      beginMutation();
      try {
        const restored = await service.restore(caseKey);
        dispatch({ type: "UPSERT_API_CASE", testCase: restored });
        finishMutation();
        return true;
      } catch (error) {
        showError(error);
        return false;
      }
    },
    restoreCaseVersion: async (caseKey, version) => {
      beginMutation();
      try {
        const result = await service.restoreVersion(caseKey, version);
        dispatch({ type: "UPSERT_API_CASE", testCase: result.testCase });
        setPersistence((current) => ({
          ...current,
          status: result.partialFailure ? "PARTIAL" : "READY",
          message: result.partialFailure,
        }));
        return true;
      } catch (error) {
        showError(error);
        return false;
      }
    },
  }), [
    beginMutation,
    categories,
    dispatch,
    finishMutation,
    legacy,
    loadCase,
    loadLibrary,
    loadWorkItem,
    persistence,
    reloadProjection,
    service,
    showError,
    state,
    workItems,
  ]);

  return (
    <TestManagementContext.Provider value={value}>
      {children}
    </TestManagementContext.Provider>
  );
}

export function useTestManagement() {
  const context = useContext(TestManagementContext);
  if (!context) throw new Error("useTestManagement must be used inside TestManagementProvider");
  return context;
}
