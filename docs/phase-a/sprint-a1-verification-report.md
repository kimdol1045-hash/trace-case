# Phase A / Sprint A1 verification report

- 기준일: 2026-08-24
- 범위: `PA-SA1-T10`, `PA-SA1-T11`, `PA-SA1-T12`와 Sprint A1 종료 체크
- 판정: **PASS**

## 1. 결론

카테고리, 상위일감, 테스트케이스, 사이클의 생성 체인은 Web adapter가 generated client로 FastAPI를 호출하고 PostgreSQL에 저장한 뒤, 이전 provider/service cache가 없는 새 Web adapter와 새 ASGI application에서 같은 원장을 다시 읽는다. `apps/api/tests/test_sprint_a1_e2e_postgresql.py`는 새 application reload를, `apps/web/src/sprint-a1-live-e2e.test.ts`는 실제 HTTP API에 연결한 fresh Web adapter reload를 각각 고정한다.

운영 route의 populated demo 초기 상태를 제거했다. `TestManagementProvider`의 기본 state는 비어 있고 API 응답으로만 hydrate되며, provider는 `/test-cases*`, `/work-items/*`, `/cycles*` route boundary에만 존재한다. Overview, Issues, Reports, Slack, Knowledge, Integrations는 provider mount나 Test Library projection side effect가 없다. `useTestManagementDemo`와 production default의 workspace sticky fallback은 제거됐다.

생성 화면은 명시적 저장 action 전에는 form/modal draft만 가진다. 목록에서 create/detail/execution 진입은 browser history를 보존하고, cancel/create success/delete success/detail return/canonical redirect는 현재 entry를 replace한다. Work Item route key가 바뀔 때 load 성공 state를 먼저 초기화해 이전 상세를 그리지 않으며, production Test Case route는 API hydrate 전에 fixture case를 그리지 않는다. Cycle route는 keyed resource guard, Issue detail은 route-key별 loading reset을 유지한다.

## 2. route navigation 재검증 (`PA-SA1-T10`)

공통 정책은 [`navigation-policy.ts`](../../apps/web/src/navigation-policy.ts)에 있다. `openCreate`, `openDetail`, `openExecution`은 push이고 `cancelCreate`, `createSuccess`, `deleteSuccess`, `returnToList`, `canonicalRedirect`는 replace다. 단순 `navigate(-1)`을 주요 detail return으로 사용하지 않아 direct entry와 hard reload에서도 앱 밖이나 삭제·생성 화면으로 되돌아가지 않는다.

| Route / entry | 진입 | 취소·목록 복귀 | 저장·삭제 이후 | 저장 전 생성 / 잔상 근거 |
| --- | --- | --- | --- | --- |
| `/test-cases` → `/test-cases/new` | `openCreate` push ([`folder-library-page.tsx`](../../apps/web/src/folder-library-page.tsx)) | breadcrumb와 취소 모두 `cancelCreate` replace | create 성공만 `createSuccess` replace로 새 key detail 이동 ([`test-case-create-page.tsx`](../../apps/web/src/test-case-create-page.tsx)) | local draft만 만들고 저장 handler에서만 `createCase`; production SSR가 `TC-1042` fixture를 표시하지 않음을 [`app.test.tsx`](../../apps/web/src/app.test.tsx)가 검증 |
| Work Item create modal / `/work-items/:key` | modal open은 route/API mutation 없음; detail link는 push | modal cancel은 close-only, detail back과 Library link는 `returnToList` replace | modal submit의 API 성공 뒤 projection reload; detail은 explicit save button만 PATCH | [`workspace-ui.test.tsx`](../../apps/web/src/workspace-ui.test.tsx)가 modal open 시 callback 0회를 검증; [`work-item-detail-page.tsx`](../../apps/web/src/work-item-detail-page.tsx)가 route 변경 때 `loadSucceeded=false`로 이전 detail을 차단 |
| `/cycles` → `/cycles/new` | `openCreate` push | breadcrumb와 취소 모두 `cancelCreate` replace | create 성공만 `createSuccess` replace; delete 성공은 `deleteSuccess` replace | [`cycle-workspace-page.tsx`](../../apps/web/src/cycle-workspace-page.tsx)의 `submit`에서만 create; detail/execution은 [`cycle-route-state.ts`](../../apps/web/src/cycle-route-state.ts)의 keyed resource를 사용 |
| `/cycles/:id` → `/cycles/:id/execute` | `openExecution` push | detail→list와 execution→detail은 `returnToList` replace | update/start/complete/reopen은 현재 detail 유지; delete만 list replace | 이전 route data 차단과 same-route revalidate 보존을 [`cycle-workspace-page.test.tsx`](../../apps/web/src/cycle-workspace-page.test.tsx)가 검증 |
| `/issues` → `/issues/new` | `openCreate` push | breadcrumb와 취소는 `cancelCreate` replace | create 성공과 partial attachment recovery detail link는 replace; delete 성공은 list replace | [`issue-workspace-page.tsx`](../../apps/web/src/issue-workspace-page.tsx)는 버튼 action에서만 issue POST하고 detail key 변경 시 `data=null`, `phase=loading`으로 reset |
| `/issues/:key`, `/slack-work-items/:id`, unknown route | list/detail entry는 push | explicit list/overview return은 replace | 해당 없음 | Issue/Slack route effect는 key별 canonical API read; [`pages.tsx`](../../apps/web/src/pages.tsx)는 unreachable mock placeholder를 제거하고 Not Found만 유지 |

[`navigation-policy.test.ts`](../../apps/web/src/navigation-policy.test.ts)는 push/replace 불변식을 검증한다. Physical pointer와 실제 browser history 버튼을 조작하는 시각 E2E는 Sprint A4 `PA-SA4-T02/T03` 범위지만, Sprint A1의 route semantics, no-create-before-save, reload source-of-truth와 stale entity 차단은 이번 자동 검증으로 닫았다.

## 3. PostgreSQL / API / Web reload E2E (`PA-SA1-T11`)

### PostgreSQL + FastAPI

[`test_sprint_a1_e2e_postgresql.py`](../../apps/api/tests/test_sprint_a1_e2e_postgresql.py)는 disposable PostgreSQL에서 public HTTP contract만 사용해 다음 체인을 실행한다.

1. root category POST
2. Work Item POST
3. Test Case POST
4. Work Item scope Cycle POST와 pinned item 생성
5. 첫 `TestClient`/application 종료
6. 같은 PostgreSQL engine 위에 새 FastAPI application과 client 생성
7. folder tree, Work Item detail/child projection, Test Case detail/step, Cycle detail/pinned version을 전부 재조회

기존 Sprint 1~3 PostgreSQL suites와 함께 실행해 category/work-item/case/cycle transaction과 이번 cross-feature chain 사이에 회귀가 없음을 확인했다: `7 passed`.

### Web generated-client path

[`sprint-a1-live-e2e.test.ts`](../../apps/web/src/sprint-a1-live-e2e.test.ts)는 `VITE_SPRINT_A1_E2E_API_BASE_URL`이 주어진 live gate에서 `ApiCategoryService` → `ApiWorkItemService` → `ApiTestCaseService` → generated Cycle client를 통해 같은 생성 체인을 수행한다. 그 다음 각 adapter와 generated client를 새로 만들어 category projection, Work Item/child cases, Test Case detail/version, Cycle/pinned item을 재조회한다. 이는 provider reducer와 service-local resource map이 없는 Web reload 경계다.

이번 검수에서는 migrated disposable PostgreSQL을 사용하는 `127.0.0.1:8010` FastAPI에 연결해 live gate `1 passed`를 확인했다. 일반 unit suite에서는 외부 mutable DB를 임의로 사용하지 않도록 URL이 없으면 이 단일 live test만 skip하며, PostgreSQL API test는 `TC_TEST_DATABASE_URL` gate로 별도 실행한다.

## 4. production demo / fallback 재감사 (`PA-SA1-T12`)

| 경계 | 판정 | 근거 |
| --- | --- | --- |
| `useTestManagementDemo` / `TestManagementDemoProvider` | 제거 | production consumer는 neutral `useTestManagement` / `TestManagementProvider`; `rg` 결과 0건 |
| populated initial provider state | 제거 | [`emptyTestManagementState`](../../apps/web/src/test-management-demo.tsx)가 provider 기본값; populated fixture는 opt-in prop에서만 선택 |
| provider의 전 route mount | 제거 | [`app.tsx`](../../apps/web/src/app.tsx)의 pathless boundary가 Test Library/Work Item/Cycle route만 감싼다 |
| workspace sticky local fallback | 제거 | [`createDefaultWorkspaceService`](../../apps/web/src/api-workspace-service.ts)는 `ApiWorkspaceService`만 반환하며 `FallbackWorkspaceService`가 없다 |
| unreachable page mock/placeholder exports | 제거 | [`pages.tsx`](../../apps/web/src/pages.tsx)는 `NotFoundPage`만 제공 |
| Cycle sticky fixture | **개발 전용 유지** | [`isCycleMockFallbackEnabled`](../../apps/web/src/cycle-service.ts)는 `MODE === "development" && flag === "true"`일 때만 true; production availability error는 fixture를 latch/display하지 않는다 |
| `initialTestManagementState`, `Demo*` type 이름, legacy reducer cycle actions | **구조적 부채로 유지** | explicit development cycle fallback과 focused tests가 소비한다. Production provider는 empty state이고 cycle fixture branch는 production에서 도달 불가하다 |
| `in-memory-workspace-service.ts` | **test/dormant 전용 유지** | production import가 없고 default factory가 참조하지 않는다. 해당 module unit test fixture로만 유지한다 |
| error helper의 `fallback` 인자 | 데이터 fallback 아님 | Slack/Knowledge/API error 문구의 기본 문자열이며 entity 원장을 대체하지 않는다 |

운영 모드에서 사용자 entity를 가짜 성공 또는 fixture 화면으로 대체하는 경계는 0개다. 남은 dev/test fixture는 production source-of-truth가 아니며 활성화 조건과 화면의 `MOCK FALLBACK` 표기가 명시적이다.

## 5. 검증 명령과 결과

| 검증 | 결과 |
| --- | --- |
| `pnpm check` | lint TS/Python, typecheck TS/Python, unit tests, schema, ADR, OpenAPI 모두 성공 |
| TypeScript unit | API client `19`, contracts `36`, UI `6`, Web `86` passed; live DB gate 1건은 default 환경에서 skip |
| Python non-PostgreSQL unit | `126 passed`, `14 deselected` |
| Sprint A1 관련 PostgreSQL | `test_sprint1_api_postgresql.py`, `test_sprint2_cases_postgresql.py`, `test_sprint3_cycles_postgresql.py`, `test_sprint_a1_e2e_postgresql.py` 합계 `7 passed` |
| live Web/API/PostgreSQL | `VITE_SPRINT_A1_E2E_API_BASE_URL=... vitest run src/sprint-a1-live-e2e.test.ts` → `1 passed` |
| `pnpm --filter @tc/web build` | production build 성공; 기존 500 kB chunk-size warning만 유지 |
| `pnpm openapi:check` | OpenAPI schema와 generated TypeScript client 최신 |
| static fallback audit | `rg 'useTestManagementDemo|TestManagementDemoProvider|FallbackWorkspaceService' apps/web/src` → 0건 |

## 6. 완료와 남은 범위

- `PA-SA1-T10`: 주요 create/edit/detail route의 push/replace, explicit save, keyed reload/no-ghost 규칙을 코드와 회귀 테스트로 닫았다.
- `PA-SA1-T11`: category → Work Item → Test Case → Cycle chain을 PostgreSQL/FastAPI application reload와 live Web adapter reload 양쪽에서 통과했다.
- `PA-SA1-T12`: 실제 원장과 남은 development/test-only fixture를 route/file 단위로 기록했다.
- Sprint A1 종료 체크는 모두 green이다. 실제 R2 external gate는 Sprint A3, physical browser pointer/history/visual regression은 Sprint A4가 소유하며 Sprint A1 완료를 가로막는 원장 fallback은 아니다.
