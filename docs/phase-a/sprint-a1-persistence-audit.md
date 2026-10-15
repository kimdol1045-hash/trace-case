# Phase A / Sprint A1 persistence audit

- 감사 대상: `apps/web/src`
- 기준일: 2026-08-24
- 연결 작업: [`PA-SA1-T01`, Sprint A1](../test-management-roadmap.md#sprint-a1--원장-완전-영속화와-레거시-제거)
- 방법: route/import/call-site 정적 감사, FastAPI transaction 경계 대조, disposable PostgreSQL API 계약 테스트와 Web unit/build 검증. full browser hard-reload E2E는 T11 범위로 남긴다.

> 구현 갱신 (2026-08-24): PA-SA1-T02~T06으로 Work Item과 category projection, case mutation, cycle production failure 경계를 PostgreSQL/API/generated client 기준으로 전환했다. 아래의 과거 위험은 현재 route 표와 완료 판정에서 해결 여부를 명시하며, dormant demo type/provider 제거와 full browser hard-reload E2E는 별도 후속 작업이다.

> 최종 갱신 (2026-08-24): PA-SA1-T10~T12에서 provider를 관련 route로 한정하고 empty-by-default로 전환했으며 `useTestManagementDemo`와 workspace sticky fallback을 제거했다. PostgreSQL application reload와 live Web adapter reload 결과, 최종 route/navigation/fallback 판정은 [Sprint A1 검수 보고서](./sprint-a1-verification-report.md)가 이 감사의 과거 잔존-risk 문구를 대체한다.

## 1. 결론

현재 Test Library의 category, work item, test case 경계는 generated client를 통해 FastAPI/PostgreSQL 원장만 읽고 쓴다. Category count와 전체 Work Item projection은 case list query와 별도로 paginated 조회하므로 검색·상태·라벨 필터나 200개 page 경계가 원본 count와 빈 상위일감을 바꾸지 않는다.

Test-case create/detail/move/reorder에는 mutable fixture service나 legacy reducer mutation callback이 없다. Move/reorder 성공 뒤에는 canonical list를 재조회하며, DB 계약 테스트가 same-parent `REORDER`, cross-parent `MOVE`, 양쪽 sort-order compaction, stale-version rollback과 audit before/after를 검증한다.

Cycle mock snapshot은 `MODE=development`이면서 `VITE_ENABLE_CYCLE_MOCK_FALLBACK=true`인 경우에만 활성화된다. Production에서는 availability failure가 sticky fallback을 만들지 않고 목록·생성·상세·실행 화면이 명시적 error/retry 상태를 유지한다. 남은 구조적 부채는 모든 route를 감싸는 전역 provider, API hydrate 전의 demo-shaped initial state, dormant issue/cycle fixture type이며 T09/T10/T11 범위에서 분리·E2E 검증해야 한다.

## 2. 전역 경계

[`AppRoutes`](../../apps/web/src/app.tsx)는 아직 모든 route를 [`TestManagementDemoProvider`](../../apps/web/src/test-management-demo.tsx)로 감싼다. Provider의 Test Library 공개 mutation은 `ApiCategoryService`, `ApiWorkItemService`, `ApiTestCaseService`로만 수행하고, 성공 응답 또는 canonical API 재조회 결과만 reducer projection에 반영한다.

- Category는 folder tree와 work-item list를 동시에 조회하고 folder create/rename/delete를 generated client로 실행한다.
- Test case는 mutable in-memory service와 automatic fallback factory를 삭제했다. Create/detail/update/move/reorder/delete/restore는 API failure를 오류로 유지한다.
- Legacy reducer에는 issue/cycle demo 동작과 API 응답 hydrate/upsert만 남고, category/work-item/case create/move/reorder mutation action은 없다.
- Cycle singleton은 development-only flag가 참일 때만 availability error를 sticky dev fixture mode로 전환한다. Production singleton은 같은 error를 매번 명시적으로 반환한다.

### 전역 위험

| 상태 | 관찰 | 남은 영향 / 후속 |
| --- | --- | --- |
| 해결 | Case/category/work-item availability failure는 local write로 전환되지 않는다. | 오류 배너를 유지하며 retry 또는 사용자 재시도가 필요하다. |
| 해결 | Production cycle mode는 static cycle/case fixture를 선택하거나 수정하지 않는다. | Dev fixture는 명시적 flag에서만 허용되고 `MOCK FALLBACK`으로 표시된다. |
| P1 잔존 | 모든 route 진입이 전역 provider와 초기 demo-shaped state에 결합된다. | API-only route의 불필요한 case projection load와 type dependency는 T10 provider 분리 대상이다. |
| P1 잔존 | full browser hard reload/new context 체인은 아직 자동화하지 않았다. | PostgreSQL API와 Web service 단위 검증은 통과했지만 category→work item→case→cycle E2E는 T11에 남는다. |

## 3. route·기능별 영속화 판정

판정은 “API 호출 존재”가 아니라 사용자 mutation이 새로고침 뒤 PostgreSQL에서 다시 읽히는지에 맞췄다. `API 우선 + mock`은 정상 시 API에 저장되지만 장애 시 같은 UI action이 memory-only 성공으로 끝날 수 있음을 뜻한다.

| Route / entry | 읽기 경계 | mutation과 실제 영속화 | mock/fallback 경계 | 제거 우선순위 |
| --- | --- | --- | --- | --- |
| 전 route (`AppRoutes`) | Provider mount가 case list, folder tree, work-item projection을 API에서 읽는다. | 해당 없음. route 자체와 무관한 side effect다. | API 실패는 명시적 error이며 case fixture를 hydrate하지 않는다. | P1: 마지막 demo consumer 전환 후 provider를 route root에서 제거한다. |
| `/test-cases` · [`FolderLibraryPage`](../../apps/web/src/folder-library-page.tsx) | case list와 독립적으로 canonical folder count와 전체 work-item projection을 읽는다. 빈 상위 일감도 유지된다. | category create/rename/delete, work-item create, case move/reorder는 API 성공 뒤 canonical projection을 재조회한다. | Test Library mutation의 local fallback이 없다. | **T02~T05 완료**. Browser hard-reload 체인은 T11에 남는다. |
| `/test-cases/new` · [`TestCaseCreatePage`](../../apps/web/src/test-case-create-page.tsx) | category/work-item 선택지는 canonical API projection이다. | 저장 버튼은 `createTestCase` 성공 뒤에만 새 key로 이동한다. 저장 전 form draft만 local이다. | 장애 시 error를 표시하고 fixture case를 만들지 않는다. | **T05 완료**. |
| `/test-cases/:caseKey` · [`TestCaseDetailPage`](../../apps/web/src/test-case-detail-page.tsx) | case/detail/version/diff는 API에서 읽는다. | save/delete/restore/version restore는 API 성공 응답만 반영한다. 화면의 `description`과 category select는 현재 case wire payload에 없는 별도 UX/contract 정리 대상이다. | 장애 시 current fixture를 읽거나 수정하지 않는다. | **T05 fallback 제거 완료**; unsupported editable field는 후속 contract 정리 대상. |
| `/work-items/:workItemKey` · [`WorkItemDetailPage`](../../apps/web/src/work-item-detail-page.tsx) | Work Item detail API에서 원장 필드, append-only audit history, active child case projection을 읽는다. | summary/description/category/owner/labels를 baseResourceVersion과 함께 PATCH하고 성공 뒤 재조회한다. category 변경은 하위 case category projection과 resourceVersion도 같은 transaction에서 갱신한다. | Work Item read/write에는 mock fallback이 없다. 실패·409는 명시적 오류로 남고 local 성공 상태를 만들지 않는다. | **T02/T03 완료**. legacy reducer 자체 삭제는 T04/T05 이후. |
| `/cycles` · [`CycleWorkspacePage`](../../apps/web/src/cycle-workspace-page.tsx) | `listCycles`와 status별 count를 API에서 읽는다. | 목록 mutation 없음. | Production availability error는 빈 error/retry 상태다. Static list/count는 explicit development flag에서만 표시한다. | **T06 완료**. |
| `/cycles/new` · [`CycleCreatePage`](../../apps/web/src/cycle-workspace-page.tsx) | scope option/preview, folder tree, case list를 API에서 읽는다. | Production은 `createCycle` 성공 뒤에만 이동한다. | Local create/navigate는 explicit development flag에서만 허용한다. | **T06 완료**. |
| `/cycles/:cycleId` · [`CycleDetailPage`](../../apps/web/src/cycle-workspace-page.tsx) | cycle/items/progress를 API에서 읽는다. | update/start/complete/reopen/delete는 API-backed다. | Demo detail/status mutation은 explicit development flag에서만 도달한다. Production failure는 fake detail을 선택하지 않는다. | **T06/T07 완료**. |
| `/cycles/:cycleId/execute` · [`CycleExecutionPage`](../../apps/web/src/cycle-workspace-page.tsx) | cycle items, step execution, attempt history, linked issue를 API에서 읽는다. | step result/retry와 execution-linked issue/attachment는 API-backed다. | Local execution result는 explicit development flag에서만 도달한다. Production failure는 error/retry만 허용한다. | **T06 완료**. |
| `/`, `/reports`, `/integrations` | [`sprint4Api`](../../apps/web/src/sprint4-dashboard-pages.tsx#L220-L270) 직접 호출. | dashboard/report는 조회, integration allowlist 등은 각 API mutation. | data fallback 없음. error helper의 `fallback` 인자는 문구 기본값일 뿐 mock 원장이 아니다. | P1: 전역 demo provider 간접 dependency만 제거. |
| `/issues`, `/issues/new`, `/issues/:issueKey` | [`IssueWorkspacePage`](../../apps/web/src/issue-workspace-page.tsx#L178-L492)가 `sprint4Api` 직접 호출. | create/update/delete/comment/link/R2 attachment가 API-backed다. | data fallback 없음. demo state의 `issues`, `DemoAttachment`, `saveIssue/createIssue/deleteIssue`는 이 route에서 사용하지 않는다. | **T09 완료:** unused Drive storage label과 dead dashboard를 제거했다. |
| `/slack-work-items*`, `/knowledge`, integration Slack panel | [`slackApi`](../../apps/web/src/slack-workspace-page.tsx#L244-L300), [`knowledgeApi`](../../apps/web/src/knowledge-service.ts#L10-L66) 직접 호출. | 노출된 mutation은 API-backed다. | data fallback 없음. | P2: provider 간접 dependency만 제거. |

## 4. 실제 API가 PostgreSQL mutation인가

정적 코드 기준으로 case와 cycle의 `API-backed` 판정은 단순 HTTP mock이 아니다.

- Web case adapter는 create/update/move/copy/delete/restore/version-restore를 generated client로 호출한다 ([case client calls](../../apps/web/src/api-test-case-service.ts#L105-L259)). FastAPI route는 `CaseRepository(session)`을 사용하고 mutation helper가 `with session.begin()`으로 감싼다 ([case routes](../../apps/api/src/tc_api/routers/cases.py#L186-L265), [case transaction helper](../../apps/api/src/tc_api/routers/cases.py#L739-L781)).
- Web cycle 화면은 create/update/start/complete/reopen/delete와 step result를 generated client로 호출한다 ([cycle calls](../../apps/web/src/cycle-workspace-page.tsx#L514-L640), [step result](../../apps/web/src/cycle-workspace-page.tsx#L889-L935)). FastAPI route는 `CycleRepository(session)`과 transaction helper를 사용한다 ([cycle routes](../../apps/api/src/tc_api/routers/cycles.py#L362-L462), [cycle transaction helper](../../apps/api/src/tc_api/routers/cycles.py#L1967-L2008)).
- repository는 SQLAlchemy `Session`과 PostgreSQL migration table을 사용한다 ([case repository](../../backend/src/tc_backend/db/cases.py), [cycle repository](../../backend/src/tc_backend/db/cycles.py), [case migration](../../backend/migrations/versions/20260820_0004_case_library_ledger.py), [cycle migration](../../backend/migrations/versions/20260820_0005_cycle_execution_ledger.py)).

이번 판정은 source-level wiring과 disposable PostgreSQL에서 mutation 후 API 재조회까지 확인했다. 실제 browser hard reload와 새 browser context proof는 T11 E2E가 소유한다.

## 5. route 밖의 legacy/mock 경계

| 파일 | 현재 도달성 | 판정 / 처리 |
| --- | --- | --- |
| `demo-dashboard-pages.tsx` (2026-08-24 제거) | `AppRoutes`가 import하지 않았고 실제 Overview/Reports/Integrations는 [`sprint4-dashboard-pages.tsx`](../../apps/web/src/sprint4-dashboard-pages.tsx)를 사용한다. | T09에서 dead Drive/demo UI를 삭제했다. |
| [`api-workspace-service.ts`](../../apps/web/src/api-workspace-service.ts), [`in-memory-workspace-service.ts`](../../apps/web/src/in-memory-workspace-service.ts) | production route import가 없고 tests만 사용한다. Test Library category는 별도 `ApiCategoryService`를 사용한다. | Dormant workspace demo/adapter 정리는 T10 dead-code 분리 범위다. Production Test Library 경로의 fallback은 아니다. |
| [`workspace-settings-page.tsx`](../../apps/web/src/workspace-settings-page.tsx) | route가 없고 UI test만 사용한다. | Sprint A1 route scope 밖의 dormant 화면. 연결 여부를 별도 결정하고 fallback 제거와 혼동하지 않는다. |
| [`pages.tsx`](../../apps/web/src/pages.tsx) | `NotFoundPage`만 현재 app에서 import한다. 나머지 placeholder export는 unreachable이다. | dead placeholder를 별도 파일로 분리/삭제해 mock 문구 검색 noise를 줄인다. |
| `test-management-demo.tsx`의 dormant issue/attachment fixture | 실제 issue route가 소비하지 않는다. | T09에서 provider-specific storage type/fixture field를 제거했다. 남은 dormant issue reducer 자체는 전역 provider 분리 범위다. |

`slackErrorMessage`, `apiErrorMessage`, `knowledgeErrorMessage`의 `fallback` parameter와 LightRAG fallback은 데이터 mock fallback이 아니다. 이번 제거 대상은 사용자 원장 데이터를 대체하는 in-memory/demo 경로다.

현재 Cycle production 경로는 actual API와 mock을 섞지 않는다. 개발 fixture를 재현하려면 development mode에서 `VITE_ENABLE_CYCLE_MOCK_FALLBACK=true`를 명시해야 하며, 이 경우 화면에 `MOCK FALLBACK`이 표시된다.

## 6. 권장 제거 순서와 dependency

새 runtime package 도입은 필수 dependency가 아니다. 현재 generated client와 route-local service 경계로도 정리할 수 있다. 권장 작업 dependency는 다음과 같다.

1. **타입/fixture 분리**
   - `Demo*` domain type을 `test-management-demo.tsx` 밖의 neutral module 또는 generated contract로 이동한다.
   - 개발 fixture는 production bundle에서 import되지 않는 dev/test module로 분리한다.
   - 이유: `test-case-service.ts`와 `api-test-case-service.ts`가 demo module type에 의존하고, demo provider가 다시 API service를 import하는 현재 순환 경계를 먼저 끊어야 한다.
2. **T02 Work Item API → generated client**
   - **완료**: create/get/update/list projection, optimistic version, audit를 연결했다.
   - `/test-cases/new`, case move/create, `/work-items/:key`가 모두 stable work-item ID와 detail에 의존한다.
3. **T04 Category API adapter 연결**
   - **완료**: 작은 `ApiCategoryService`가 folder tree/work-item list와 create/rename/delete generated client를 연결한다.
   - Production default에 category/work-item in-memory fallback이 없다.
4. **T03 Work Item UI 전환**
   - **완료**: create modal과 detail save/read를 API로 바꾸고 실패 시 local mutation을 금지했다.
   - 이 단계가 끝나기 전에는 legacy work-item reducer를 삭제하면 안 된다.
5. **T05 Case route 전환 완결**
   - **완료**: category/work-item projection을 API 단일 source로 만들고 `FallbackTestCaseService`, `InMemoryTestCaseService`, case create/move/reorder reducer mutation을 제거했다.
   - `description`/category edit UX와 wire contract 불일치는 fallback 문제와 분리된 후속 정리 대상이다.
6. **T06 Cycle fallback 격리**
   - **완료**: production build는 API error만 표시하고 local create/update/execution을 성공 처리하지 않는다.
   - Static fixture sticky mode는 explicit development flag 뒤에 있으며 `MOCK FALLBACK`으로 표시한다.
7. **T07/T09 legacy 정리**
   - T07의 Latest diff UI/call과 전용 공개 계약은 제거됐다.
   - **완료:** T09에서 dead demo dashboard와 provider-specific demo attachment field를 제거하고 placeholder 문구를 object storage로 정리했다.
8. **전역 provider 제거**
   - 모든 `useTestManagementDemo` consumer가 사라진 뒤 `AppRoutes`에서 provider를 제거한다.
   - route별 service 상태만 남겨 unrelated route의 case API side effect를 없앤다.
9. **T11 reload E2E**
   - `category → work item → case create/move/reorder → cycle create/start/result`를 production mode, no-fallback, PostgreSQL로 검증한다.
   - 각 mutation 직후 hard reload와 새 browser context 재조회가 필수다.

의존 관계를 축약하면 `type split → Work Item API/client → Category/Work Item UI → Case fallback 제거 → Cycle fallback flag → dead demo/provider 제거 → reload E2E`다.

## 7. `rg` 근거

아래 명령은 repository root에서 실행했다.

```text
rg -n 'FallbackTestCaseService|InMemoryTestCaseService|source: "MOCK"|MOVE_CASE|REORDER_CASE|CREATE_CASE' apps/web/src
```

결과가 없으므로 case default service, mutable fixture service, mock source와 legacy case mutation action이 제거됐음을 확인한다.

```text
rg -n 'VITE_ENABLE_CYCLE_MOCK_FALLBACK|cycleMockFallbackEnabled' \
  apps/web/src/cycle-service.ts apps/web/src/cycle-workspace-page.tsx
```

결과는 fallback enablement가 `isCycleMockFallbackEnabled`의 development mode + explicit flag 판정과 각 list/create/detail/execution fixture 선택에 함께 적용됨을 확인한다.

```text
rg -n 'createFolder|renameFolder|softDeleteFolder|getFolderTree|listWorkItems' \
  apps/web/src/category-service.ts packages/api-client/src/client.ts
```

결과는 category projection과 CRUD가 generated client 경계만 사용함을 확인한다.

```text
rg -n 'loadLibrary\(\{ limit: 50 \}\)|service.move|service.softDelete' \
  apps/web/src/test-management-demo.tsx
```

결과는 case move/reorder/delete가 API mutation 성공 뒤 local order/delete action 대신 canonical list를 재조회함을 확인한다.

## 8. 완료 판정

- T01의 route/기능별 분류, 실제 API 영속화 여부, fallback/mock 제거 우선순위와 후속 dependency를 기록했다.
- T02/T03은 Work Item PostgreSQL 원장, FastAPI/generated client와 Web create/detail 경로로 구현됐다.
- T04는 category CRUD와 filter-independent folder/work-item projection을 API 단일 원장으로 전환했다.
- T05는 case mutable fallback과 legacy create/move/reorder mutation을 제거했고 PostgreSQL transaction/audit 검증을 추가했다.
- T06은 cycle sticky fixture를 explicit development-only flag 뒤로 격리하고 production failure가 fake data를 표시하지 않음을 테스트했다.
- T07은 Latest diff UI·호출·전용 공개 계약 제거까지 완료됐다.
- T08은 Reports CSV UI와 전용 client·FastAPI·OpenAPI 계약 제거까지 완료됐으며 첨부 `text/csv` 지원은 유지한다.
- 남은 위험은 전역 demo provider/type 분리, case description/category edit wire-contract 정리, full browser hard-reload E2E다.

### 검증 결과

- `TC_TEST_DATABASE_URL=... node scripts/python.mjs -m pytest apps/api/tests/test_sprint2_cases_postgresql.py -q` → `4 passed`
- `TC_TEST_DATABASE_URL=... node scripts/python.mjs -m pytest backend/tests/repository/test_cases_postgresql.py::test_work_item_update_uses_resource_version_and_appends_audit -q` → `1 passed`
- `node scripts/python.mjs -m pytest apps/api/tests/test_api.py -q` → `6 passed`
- `pnpm --filter @tc/web test:unit` → `20 files, 85 tests passed`
- `pnpm --filter @tc/web build` → production build 성공(기존 500 kB chunk warning 유지)
- `pnpm --filter @tc/api-client typecheck` → 성공
- `pnpm openapi:check` → schema와 generated TypeScript client가 최신 상태
