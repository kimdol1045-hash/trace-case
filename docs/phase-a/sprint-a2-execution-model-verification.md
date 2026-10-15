# Phase A / Sprint A2 execution-model verification

- 기준일: 2026-08-24
- 작업 범위: `PA-SA2-T01`–`PA-SA2-T10`
- 검증 경계: PostgreSQL 16 + pgvector disposable DB, FastAPI `TestClient`, generated OpenAPI client, JSON Schema, Web unit/type/build

## 1. 결론

Sprint A2의 실행 상태는 `UNEXECUTED`, `PASS`, `FAIL`, `BLOCKED` 네 값으로 고정했다. `WIP`은 Web 입력·표시, FastAPI/Pydantic, OpenAPI, generated client, JSON Schema와 현재 SQLAlchemy model에서 제거했다. Active/partial attempt는 동일한 attempt row를 `UNEXECUTED`로 유지하고 모든 pinned step 결과가 기록됐을 때만 terminal 상태로 전이한다.

Migration `20260824_0013`은 기존 `WIP` row를 `UNEXECUTED`로 변환한다. 변환 중 append-only trigger를 transaction 안에서만 비활성화하고 status/check/guard를 교체하며, attempt id, platform, attempt/run number, executor, started/created timestamps, partial step rows, issue link와 audit row는 변경하지 않는다. PostgreSQL round-trip test가 legacy `WIP` 상태와 status 외 snapshot/audit count를 캡처한 뒤 head upgrade에서 `UNEXECUTED`와 동일 snapshot을 확인한다.

통합 결과는 모든 server/client/report 경계에서 `UNEXECUTED`와 `BLOCKED`를 제외하고 `FAIL`을 우선한다. 포함 결과가 없으면 `UNEXECUTED`, FAIL이 하나라도 있으면 `FAIL`, 그 외 PASS가 있으면 `PASS`다. `passRate` 분모도 같은 규칙에 맞춰 `PASS + FAIL`이며 BLOCKED는 terminal count에는 남지만 실행 verdict와 pass-rate 분모에는 들어가지 않는다.

## 2. 작업별 근거

| 작업 | 완료 근거 |
| --- | --- |
| T01 | runtime·OpenAPI·generated TS·JSON Schema enum이 네 상태로 일치하고 Web step menu는 terminal 입력과 UNEXECUTED clear만 제공한다. runtime/contracts/generated source에서 WIP 검색 결과가 없다. |
| T02 | `20260824_0013_remove_wip_execution_status.py`와 PostgreSQL lossless round-trip test. Downgrade도 active UNEXECUTED를 WIP로 되돌리는 rollback path를 제공한다. |
| T03 | repository matrix test가 2개 step × WEB/AOS/IOS 독립 결과, partial UNEXECUTED, terminal derivation과 WEB attempt 1/2 append-only history의 8개 step-result row를 검증한다. |
| T04 | backend `aggregate_execution_statuses`, Web `aggregateExecutionStatuses`, contract `ExecutionAggregateStatus`, report/progress `aggregateStatus`와 PASS/FAIL denominator test가 동일 판정표를 사용한다. |
| T05 | PostgreSQL/API flow가 PLANNED step/attempt 409, IN_PROGRESS 저장 성공, COMPLETED update/result/start 409, reopen 후 progress/attempt 사용 가능을 검증한다. Web `canEditCycleExecution`도 IN_PROGRESS만 허용한다. |
| T06 | FAIL/BLOCKED attempt에서 만든 issue가 library edit와 새 WEB rerun/PASS 이후에도 원래 attempt id에 연결되고 attempt row 두 개가 유지되는 repository test가 통과한다. |
| T07 | cycle list response에 status filter를 제외한 `statusCounts`를 포함해 Web 4회 요청을 1회로 줄였다. status filter 전후 card count 동일 API assertion이 있고, report source는 cycle 수와 무관한 4 SELECT plan, issue KPI는 전체 cycle batch query를 사용한다. Execution/issue status filter는 row 포함 여부만 결정하고 source breakdown은 다시 쓰지 않는다. |
| T08 | item API의 unfiltered path가 SQL count + offset/limit으로 100개만 serialize하고 detail/execution queue에 다음 100개 cursor UI를 추가했다. PostgreSQL API test가 1,000-case cycle에서 첫 100개, total 1,000, next cursor와 2초 미만 목표를 통과하며 Web test가 10 page/1,000 id merge와 중복 제거를 검증한다. |
| T09 | API flow가 stale update, active delete, incomplete complete confirmation, completed update/result/start conflict를 검증한다. Web detail loader는 cycle/items 성공 뒤 progress 실패를 partial 상태로 보존하며 해당 unit test가 통과한다. |
| T10 | 이 문서가 Sprint A2 구현, 자동화 근거와 잔여 위험을 기록한다. |

## 3. lifecycle·attempt 판정표

| 상태 | detail edit | step/attempt input | delete | complete/reopen |
| --- | --- | --- | --- | --- |
| PLANNED | 허용 | 409 `CYCLE_EXECUTION_NOT_STARTED` | 허용 | start만 허용 |
| IN_PROGRESS | name/description 허용, scope/pin 잠금 | 허용 | 409 `CYCLE_DELETE_LOCKED` | incomplete 확인 후 complete |
| COMPLETED | 409/read-only | 409 `CYCLE_EXECUTION_LOCKED` | 잠금 | reason을 포함한 reopen만 허용 |

Attempt는 `(cycle_item_id, platform, attempt_no)` unique이고 terminal update 후 DB trigger가 update/delete를 거부한다. Step result는 active UNEXECUTED attempt에만 insert/update할 수 있고 terminal completion 뒤 immutable이다. Rerun은 기존 row를 바꾸지 않고 같은 platform의 `attempt_no + 1` row를 추가한다.

## 4. aggregate와 filter 불변식

`aggregateStatus`와 `passRate`의 유효 입력은 PASS/FAIL뿐이다. BLOCKED는 환경·선행조건 차단을 보존하는 terminal 사실이지만 제품 품질 PASS/FAIL 판정에는 포함하지 않는다.

Cycle status card는 search/scope/date filter는 공유하되 현재 status filter만 count query에서 제외한다. 따라서 PLANNED card를 선택해도 IN_PROGRESS/COMPLETED card 원본 수는 유지된다. Report execution/issue status filter도 cycle 포함 여부를 선택할 뿐 반환 breakdown을 zero-fill하지 않는다.

`ReportRepository.source_rows()`는 cycle, all items, all attempts, all linked issues의 네 SELECT로 끝난다. Router issue KPI도 cycle마다 query하지 않고 grouped cycle id 집합을 한 번에 읽는다.

## 5. 대량 queue

API 기본 화면은 100개 page를 요청하고 `nextCursor`로 다음 page를 append한다. 1,000-case PostgreSQL fixture의 첫 page는 100개만 반환하고 `total=1000`, non-null cursor와 `< 2.0s` assertion을 통과했다. Web merge helper는 stable id로 deduplicate하므로 retry된 page가 기존 row를 늘리지 않는다.

## 6. 검증 결과

- Python unit/API/worker(non-PostgreSQL): `126 passed, 13 deselected`.
- Focused PostgreSQL repository/migration/API: `22 passed, 1 collection warning` (`TestCycle` SQLAlchemy model name warning), `7.05s`.
- Contracts: `7 files, 36 tests passed`.
- Web: `20 files, 87 tests passed`.
- API client: `18 tests passed`; UI package: `6 tests passed`.
- Ruff/ESLint: pass.
- Mypy: pass, 85 source files; recursive TypeScript typecheck: pass.
- Contract validator: `5` strict schemas and `12` synthetic fixtures; contract Vitest: `7 files, 36 tests passed`.
- OpenAPI check: schema와 generated TypeScript client up to date.
- Web production build: pass, 기존 500 kB chunk warning 유지 (`552.98 kB`, gzip `159.16 kB`).

## 7. 잔여 위험

- 실제 브라우저 hard reload/new context E2E runner는 repository에 없다. 이번 E2E-style 근거는 real PostgreSQL FastAPI flow와 Web service/component unit 경계를 결합한 것이다.
- Filtered cycle-item search/label/owner/latest path는 정확한 filter 적용을 위해 현재 page 후보를 response model로 변환한 뒤 slice한다. 1,000-row 핵심 execution queue인 unfiltered path는 DB pagination이지만, 복합-filter 대량 benchmark는 후속 최적화 대상이다.
- Migration은 forward data를 lossless하게 보존한다. Downgrade에서는 active UNEXECUTED와 legacy WIP가 같은 active 의미이므로 unfinished UNEXECUTED attempt를 WIP로 복원하며, 이 표현 변경은 의도된 rollback 정책이다.
- PostgreSQL performance 목표는 local disposable DB의 `< 2.0s` gate다. production latency, connection pool, 실제 row 폭을 포함한 부하 시험은 배포 환경에서 다시 수행해야 한다.
