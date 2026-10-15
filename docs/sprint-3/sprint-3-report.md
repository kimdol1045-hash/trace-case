# Sprint 3 사이클·실행 품질 게이트 보고서

| 항목 | 결과 |
|---|---|
| 검증일 | 2026-08-20 |
| 대상 | `P2-S3-T01~T10`, 제품 인수 조건 4~7, Sprint 3 종료 체크 4개 |
| 판정 | **PASS — 재현 가능한 로컬 PostgreSQL/API/Web 근거 확보** |
| DB 기준선 | PostgreSQL 16.12, pgvector 0.8.1, Alembic `20260820_0005` |
| 계약 기준선 | `PLANNED | IN_PROGRESS | COMPLETED`, `WORK_ITEM | LABEL`, `WEB | AOS | IOS`, `UNEXECUTED | WIP | PASS | FAIL | BLOCKED` |

이 판정은 현재 저장소의 로컬 품질 게이트를 통과했다는 뜻이다. 운영 배포, 다중 사용자 부하, 인증·권한, Sprint 4 issue/Drive persistence까지 완료됐다는 뜻은 아니다.

## 1. 자동화한 원장 흐름

[Sprint 3 PostgreSQL API E2E](../../apps/api/tests/test_sprint3_cycles_postgresql.py)는 disposable DB에서 다음 단일 흐름을 실제 FastAPI serializer와 repository transaction으로 실행한다.

1. WORK_ITEM과 LABEL option을 조회하고 cursor로 LABEL 두 페이지를 순회한다. 두 scope preview가 같은 ACTIVE case를 중복 없이 해석하는지 확인한다.
2. idempotency key로 cycle을 만들고 동일 요청 재전송이 같은 resource를 반환하는지 확인한다.
3. PLANNED progress와 attempt 시작을 `CYCLE_EXECUTION_NOT_STARTED`로 거부한다.
4. library case v2를 만든 뒤 item diff를 조회하고 사용자가 확인한 item snapshot만 v2로 refresh한다. 다시 v3을 만든 뒤 전체 snapshot을 명시적으로 refresh하고 `resourceVersion`을 3으로 올린다.
5. stale `resourceVersion` update를 `VERSION_CONFLICT`로 거부하고 cycle을 시작해 v3을 고정한다.
6. 시작 후 library v4를 만들어도 pinned summary와 v3 ID가 유지되고 latest v4 diff만 노출되는지 확인한다. item refresh와 scope update는 `CYCLE_SCOPE_LOCKED`로 거부한다.
7. WEB attempt #1을 시작하고 중복 platform 시작을 `EXECUTION_PLATFORM_DUPLICATE`로 거부한다. 빈 FAIL을 `ACTUAL_RESULT_REQUIRED`로 거부한 뒤 실제결과 `HTTP 500`을 저장한다.
8. retry가 기존 FAIL row를 수정하지 않고 WEB attempt #2를 append하는지, history가 `#2 PASS`, `#1 FAIL`과 이전 actualResult 및 latest ID를 함께 반환하는지 확인한다.
9. AOS BLOCKED의 빈 note를 `BLOCKED_NOTE_REQUIRED`로 거부한 뒤 차단 사유를 저장한다.
10. overall/platform/owner/work-item/label progress가 WEB의 과거 FAIL이 아닌 최신 PASS만 사용하고 `PASS 1 / BLOCKED 1 / UNEXECUTED 1`을 반환하는지 확인한다. filter된 WEB+payments 분모는 1이다.
11. 미실행 IOS가 남은 완료를 먼저 `CYCLE_INCOMPLETE_WARNING`으로 막고 명시 승인 후 완료한다. 완료 후 result와 새 attempt를 잠그고 reopen 후 같은 최신 결과를 유지한다.

[Cycle repository PostgreSQL suite](../../backend/tests/repository/test_cycles_postgresql.py)는 API 흐름 아래의 DB 불변성을 별도로 검증한다.

- `(cycle_id, test_case_id)` 중복 item과 `(cycle_item_id, platform, attempt_no)` 중복 attempt를 unique constraint에서 차단한다.
- 같은 item/platform에 두 transaction이 동시에 attempt를 시작하면 정확히 하나만 성공한다.
- cycle `resourceVersion` stale writer를 거부한다.
- immutable attempt UPDATE trigger, start 후 pinned item UPDATE trigger와 append-only history를 확인한다.
- LABEL 3 case × 3 platform의 9개 slot에서 과거 FAIL→최신 PASS, WIP, BLOCKED, FAIL, UNEXECUTED를 섞어 다섯 progress 차원을 검산한다.

## 2. 실행 결과

| Gate | 실행 명령 | 결과 |
|---|---|---|
| 전체 정적·단위·계약 gate | `pnpm check` | PASS: ESLint/Ruff, TS/Python typecheck, TS 75 tests, Python 41 tests, strict Draft 2020-12 Schema 5개·fixture 12개, ADR gate, OpenAPI/client drift 0 |
| Web production build | `pnpm --filter @tc/web build` | PASS: 76 modules, JS 385.16 kB (gzip 112.29 kB) |
| migration rebuild·repository | `TC_TEST_DATABASE_URL=.../tc_sprint2_test pnpm test:migration` | PASS: 27 tests; migration head rebuild와 cycle concurrency/constraint 포함 |
| API PostgreSQL E2E | `TC_TEST_DATABASE_URL=.../tc_test node scripts/python.mjs -m pytest apps/api/tests -m postgres -q` | PASS: 3 tests; Sprint 1~3 실제 repository/API 흐름 |
| Sprint 3 API 집중 실행 | `node scripts/python.mjs -m pytest apps/api/tests/test_sprint3_cycles_postgresql.py -m postgres -q` | PASS: 1 end-to-end flow |
| Sprint 3 repository 집중 실행 | `node scripts/python.mjs -m pytest backend/tests/repository/test_cycles_postgresql.py -m postgres -q` | PASS: 5 tests |
| OpenAPI/generated client | `pnpm openapi:check` | PASS: checked-in OpenAPI와 `schema.gen.ts` 최신, cycle/execution 관련 path 21개 |

`pnpm check`의 TS 75개는 contracts 19, generated client 8, UI 6, Web 42개다. PostgreSQL 테스트는 `TC_TEST_DATABASE_URL`이 이름에 `test`를 포함하는 disposable DB인지 확인한 뒤 cycle/attempt table까지 매 test에서 명시적으로 비운다.

## 3. no-fallback Browser smoke

실제 개발 stack `http://127.0.0.1:5173` → generated client → FastAPI `0.1.0` → PostgreSQL `tc`를 사용했다. dev DB를 Alembic `20260820_0004`에서 `20260820_0005`로 올린 뒤 다음을 브라우저에서 확인했다.

- `/cycles/new`에서 API가 반환한 `LIVE-301 · Sprint 3 live smoke` WORK_ITEM option과 `TC-000003` preview가 표시됐다.
- Web에서 `Sprint 3 no-fallback browser` cycle을 생성해 `cycle_4ea55f7587ff47618ed67f390bd42202`로 이동하고 start했다.
- 별도 execution player에서 WEB attempt를 시작하고 actual result `Persisted through the generated client.`와 PASS를 저장했다.
- 새로고침한 cycle 목록은 진행 중 1건, 최신 결과 `1 / 3`, PASS 1·UNEXECUTED 2를 표시했다.
- API item 응답과 DB join은 같은 pinned version, WEB attempt #1 PASS, actualResult를 반환했다. cycle scope data에 sticky fallback 또는 mock 표시는 없었다.
- 계획 상태 progress를 호출해 의도된 409를 장애처럼 표시하던 결함을 발견해 수정했다. PLANNED 상세는 이제 `PLANNED · 집계 제외`를 표시하고 progress API를 호출하지 않으며, start 직후 실제 overall/platform/owner/work-item/label progress로 자동 전환한다.
- execution rail의 `연결 이슈 MOCK · Sprint 4 persistence 예정` 표시는 cycle API fallback이 아니라 의도적으로 격리한 Sprint 4 경계다.

## 4. 제품 인수 조건 4~7

| 인수 조건 | 판정 | 근거 |
|---|---|---|
| 4. cycle 생성·시작·완료 | PASS | Browser에서 generated client 생성·시작, API E2E에서 생성·시작·미실행 경고 승인 완료·reopen |
| 5. 선택 범위 case version 고정 | PASS | WORK_ITEM/LABEL preview, PLANNED 명시 refresh, 시작 후 library v4에도 pinned v3 유지와 diff |
| 6. canonical platform 상태 처리 | PASS | WIP 시작과 PASS/FAIL/BLOCKED 완료, UNEXECUTED slot, FAIL actualResult/BLOCKED note validation; 이전 문서의 `SKIPPED`는 canonical enum과 불일치해 제품 기획서 문구를 정정 |
| 7. 재실행 이력·실제결과 보존 | PASS | FAIL #1 actualResult 보존, PASS #2 append, history/latest-attempt·DB immutable trigger |

인수 조건 5의 과거 “도메인 전체” 문구는 ADR-0016의 canonical `WORK_ITEM | LABEL` scope로 치환했다. literal `SKIPPED`를 새로 구현해 enum을 확장하지 않고, 공개 계약에서 고정한 `UNEXECUTED | WIP | PASS | FAIL | BLOCKED`를 인수 조건 6의 기준으로 삼았다.

## 5. Sprint 3 종료 조건

| 종료 조건 | 판정 | Evidence |
|---|---|---|
| 누구나 cycle 생성·시작·완료 | PASS | 무로그인 내부망 제품 경계에서 Browser 생성·시작, API 완료·reopen |
| PLANNED result/note/issue 잠금, 시작 후 scope 고정 | PASS | Web disabled controls, prestart attempt 409, post-start scope/refresh 409 |
| library 수정이 진행 중 script를 자동 변경하지 않음 | PASS | post-start case v4 뒤 item의 pinned v3 summary/steps 유지와 latest diff |
| 모든 attempt와 actualResult 보존 | PASS | append-only trigger, retry #1/#2 history, duplicate/concurrency guard |

## 6. 열린 위험과 Sprint 4 연결점

- issue select는 의도적으로 mock이다. Sprint 4에서 cycle item·pinned version·platform attempt를 참조하는 issue/link/comment 원장으로 교체하기 전에는 이 선택을 영속 evidence로 사용하면 안 된다.
- Browser smoke는 로컬 단일 브라우저와 개발 DB에서 수행했다. production reverse proxy, 다중 사용자 동시 부하, 실제 권한 모델을 검증하지 않는다.
- repository 동시성은 같은 platform attempt 두 writer의 단일 승자와 stale `resourceVersion`을 다루지만, 장시간 실행 중 network retry·process crash의 운영 복구는 별도 장애 주입이 필요하다.
- 완료는 미실행/WIP 경고를 명시 승인하면 허용하는 제품 계약이다. “모든 플랫폼 실행 완료”를 강제하는 정책으로 해석하면 안 된다.
- Sprint 4는 FAIL/BLOCKED attempt snapshot에서 이슈 생성, private Drive 첨부, issue severity/progress/CSV를 구현한 뒤 인수 조건 8~10을 별도 gate로 검증해야 한다.

## 7. 변경 근거

- Sprint 3 ledger migration: [20260820_0005](../../backend/migrations/versions/20260820_0005_cycle_execution_ledger.py)
- Repository implementation: [cycles.py](../../backend/src/tc_backend/db/cycles.py)
- API endpoints and typed errors: [cycles router](../../apps/api/src/tc_api/routers/cycles.py)
- Generated client boundary: [client.ts](../../packages/api-client/src/client.ts)
- Web cycle UI: [cycle-workspace-page.tsx](../../apps/web/src/cycle-workspace-page.tsx)
- Public contract and fixtures: [cycle schema](../../packages/contracts/sprint-3/cycle-execution-api.schema.json), [error schema](../../packages/contracts/sprint-3/error-contract.schema.json), [fixtures](../../packages/contracts/fixtures/sprint-3-cycle-execution-contracts.json)
