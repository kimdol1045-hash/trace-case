# Sprint 2 품질 게이트 보고서

기준일은 2026-08-20이며 범위는 로드맵 `P1-S2-T01`~`T11`과 Sprint 2 종료 체크다. 결과 원장은 PostgreSQL 16.12 + pgvector 0.8.1, 실제 FastAPI HTTP, generated TypeScript client 기반 Web UI에서 재현했다. Phase 1 전체 종료나 production SLO까지 확대 해석하지 않는다.

## 1. 결론

Sprint 2의 case ledger 종료 조건은 PASS다. immutable version, optimistic conflict, diff/restore, ownership/sort, copy, lifecycle, 검색·복합 필터·pagination과 실제 UI persistence가 자동 test 및 live smoke에서 연결됐고, 100,000-case warm-cache API p95는 측정한 7개 일반 조회 모두 1,000 ms 목표 안이었다.

동시에 다음 항목은 열린 상태다.

- rare substring 검색의 `EXPLAIN`에는 `test_cases`와 `test_case_versions` sequential scan이 남는다. 현재 100,000건 p95는 통과하지만 데이터 증가 전에 `pg_trgm` GIN과 cross-table `OR`를 제거하는 candidate-ID `UNION` 또는 current-search projection을 비교해야 한다.
- 공개 cursor는 fingerprint를 포함하지만 내부 위치는 offset이다. 95,000-row deep offset 실측은 28.74 ms였으나 비용이 page depth와 함께 증가하므로 selected sort tuple 기반 keyset cursor가 후속 보완안이다.
- benchmark는 로컬 Docker, warm cache, single-process FastAPI `TestClient`, 단일 client다. production hardware의 cold-cache·동시 사용자 p95 증거는 아니다.
- public work-item create/list API는 Sprint 2 contract에 없다. live UI 빈 원장 검증에서는 work item 2개를 SQL fixture로 준비했으며, 실제 Slack/수동 work-item 유입 연결은 후속 API/ingestion 범위다.
- Generation API는 아직 연결하지 않았다. `CASE` mode snapshot은 UI에서 읽기 전용으로 표시하고 새 version 생성은 `WORKER` source 규칙에 맡긴다.
- cycle snapshot과 실행 attempt는 Sprint 3 범위이므로 ADR-0012와 ADR-0016은 계속 `Proposed`다.

## 2. 로드맵 evidence

| Task | 결과 | 재현 evidence |
|---|---|---|
| `P1-S2-T01` | PASS | migration `20260820_0004`, case repository PostgreSQL test가 root category projection, work-item ownership FK, immutable versions, version당 최소 1 step, tags, `PER_STEP`/`CASE` DB trigger를 검증했다. |
| `P1-S2-T02` | PASS | 실제 HTTP create가 `TC-000001`, 즉시 `ACTIVE`, `201`을 반환했다. workspace counter concurrency/rollback test와 API idempotency replay가 allocation boundary를 검증한다. |
| `P1-S2-T03` | PASS | update 뒤 같은 `baseVersion=1` 재요청이 `409 VERSION_CONFLICT`, `actualVersion=2`를 반환했고 concurrent repository update는 1 success + 1 conflict였다. |
| `P1-S2-T04` | PASS | v1→v2 diff에서 summary/priority/labels를 확인했고 v1 restore가 기존 행을 바꾸지 않고 v3을 append했다. repository immutable trigger test도 원행 UPDATE를 거부했다. |
| `P1-S2-T05` | PASS | API-backed detail에서 precondition/action/per-step expected result와 최소 1 step delete guard를 확인했다. `CASE` mode는 source contract에 맞춰 읽기 전용이며 Generation API 소유임을 표시한다. |
| `P1-S2-T06` | PASS | live UI update 후 v1~v4 history 4행과 7개 field/step before-after diff를 표시했다. source actor와 restore action도 version history에 유지됐다. |
| `P1-S2-T07` | PASS | API PostgreSQL contract test가 ID/summary, category/work item/status/priority/label ANY·ALL, cursor invalidation을 검증했고 100,000-row compound/filter/search benchmark가 목표를 통과했다. |
| `P1-S2-T08` | PASS | generated client service와 UI가 inline summary, same-work-item reorder, cross-work-item move, contextual drawer soft delete를 transaction에 연결하고 loading/error/conflict/partial 상태를 구분한다. 상세/list hydration race는 gate 중 발견 후 reducer regression으로 고정했다. |
| `P1-S2-T09` | PASS | repository/API test가 copy source pointer와 독립 version history, move ownership/sort, soft delete/restore를 검증했다. live HTTP lifecycle은 resource version 4→5→6으로 진행됐다. |
| `P1-S2-T10` | PASS | 20 category, 1,000 work item, 100,000 case/version/step, 200,000 current/version tag mapping fixture에서 7개 warm API scenario p95가 모두 1초 이하였다. |
| `P1-S2-T11` | PASS | PostgreSQL API 자동 test와 live HTTP에서 create→update→409→diff→version restore→move→soft delete→restore를 한 aggregate로 통과했고 Browser smoke가 동일 PostgreSQL row의 API-backed detail update/history를 확인했다. |

## 3. 100,000-case benchmark

재현 command:

```bash
TC_TEST_DATABASE_URL='postgresql+psycopg://tc:<password>@127.0.0.1:55432/tc_sprint2_test' \
  pnpm benchmark:sprint2-cases --output docs/sprint-2/sprint-2-benchmark.json
```

fixture는 20 active root categories, 1,000 work items, 100,000 cases, 100,000 immutable v1 rows, 100,000 steps, 20 tags와 case/version별 2개 tag mapping을 실제 Sprint 2 schema에 set-based insert한다. 매 20번째 case는 soft deleted라 active count는 95,000이며 script가 cases/active/versions/steps count를 검증한 뒤 측정한다.

warmup 3회 뒤 각 scenario를 30회 실행한 실제 결과다.

| Scenario | Result count | p50 | p95 | max | 1초 목표 |
|---|---:|---:|---:|---:|---|
| folder tree + case count | 21 roots | 15.48 ms | 17.13 ms | 17.21 ms | PASS |
| 기본 work-item order list | 95,000 | 134.03 ms | 143.21 ms | 144.56 ms | PASS |
| updated-desc list | 95,000 | 133.79 ms | 157.25 ms | 159.55 ms | PASS |
| rare key search | 1 | 77.66 ms | 121.94 ms | 176.56 ms | PASS |
| rare summary search | 1 | 78.06 ms | 89.60 ms | 91.15 ms | PASS |
| common summary search | 95,000 | 225.19 ms | 251.35 ms | 253.47 ms | PASS |
| category+work item+status+priority+2 labels ALL | 100 | 76.97 ms | 91.23 ms | 123.02 ms | PASS |

raw JSON은 [sprint-2-benchmark.json](./sprint-2-benchmark.json)에 보존한다. API list는 total count와 최대 50개 item의 version/step/tag serializer까지 포함하므로 SQL-only latency가 아니다.

### EXPLAIN과 index 판단

rare summary query는 58.93 ms였고 `Seq Scan:test_cases`, `Seq Scan:test_case_versions`, hash join, gather merge가 관찰됐다. 지금 즉시 GIN index를 migration에 추가하지 않은 이유는 측정 p95가 목표 대비 충분히 낮고 immutable version write/storage 비용을 늘리기 전에 cross-table `OR` query shape까지 함께 비교해야 하기 때문이다.

보완 후보는 다음 순서로 검증한다.

1. test database에서 `pg_trgm`을 enable하고 `lower(test_cases.case_key)` 및 `lower(test_case_versions.summary)` GIN index의 latency와 index 크기/write cost를 비교한다.
2. 두 table column을 `OR`하는 대신 key candidate와 summary candidate를 `UNION`한 뒤 case ID로 join하는 query를 비교한다.
3. 증가량이 계속되면 current key/summary/search vector를 transactionally 갱신하는 별도 projection을 검토하되 immutable version 원장을 검색 projection으로 대체하지 않는다.
4. offset 95,000의 28.74 ms는 현재 통과지만 cursor payload를 `(sort fields, id)` keyset으로 바꾸고 first/deep page를 함께 회귀 측정한다.

기존 `(workspace_id, category_id, deleted_at)`, `(work_item_id, sort_order, deleted_at, id)`, version, tag mapping indexes는 현재 plan과 compound-filter 결과에서 제거 근거가 없으므로 유지한다.

## 4. 핵심 E2E 원장

### PostgreSQL + live FastAPI HTTP

main local PostgreSQL에 migration head를 적용한 뒤 quality-gate category와 work items를 fixture로 준비하고 `http://127.0.0.1:8000`의 실제 Uvicorn API에 요청했다.

| 단계 | 관찰 결과 |
|---|---|
| create | `201`, `TC-000001`, content/resource version `1/1` |
| update | `200`, content/resource version `2/2` |
| stale update | `409 VERSION_CONFLICT`, `actualVersion=2` |
| diff v1→v2 | `200`, summary/priority/labels field changes |
| restore v1 | `200`, immutable v3 append, resource version 3 |
| move | `200`, target `WORK-9202`, resource version 4 |
| soft delete | `200`, non-null `deletedAt`, resource version 5 |
| restore deleted | `200`, null `deletedAt`, resource version 6 |

### Live Web UI

동일 API와 PostgreSQL을 바라보는 production-equivalent Vite app에서 다음을 Browser automation으로 확인했다.

- Test Library가 `TC-000001`과 `Sprint 2 E2E Quality Gate`를 표시하고 mock fallback banner를 표시하지 않았다.
- 검색 뒤 case row 단일 클릭으로 `/test-cases/TC-000001` 상세에 이동했다.
- generated client update로 summary를 저장해 content v4가 됐고 success status와 no conflict/error를 확인했다.
- history tab은 version 4행과 field/step diff 7행, 과거 version restore action을 표시했다.
- `WORKER` source의 실제 `CASE` mode `TC-000002`는 common expected result와 Generation API ownership 안내를 표시하고 mode selector/save를 비활성화했다.
- browser console의 app-origin error는 없었다. 외부 wallet extension의 listener warning은 application failure로 집계하지 않았다.

첫 save 시도는 provider 초기 list와 detail hydrate가 겹쳐 draft가 이전 값으로 돌아가는 race를 드러냈다. gate에서 list-only row가 같은 version의 이미 로드된 steps/history를 덮지 않도록 reducer를 수정하고 LOADING 중 save를 비활성화했으며 regression test를 추가한 뒤 같은 live update를 재실행해 통과했다.

## 5. 검증 command

| Command | 결과 |
|---|---|
| `pnpm check` | PASS — ESLint/Ruff, TS/Python typecheck, TS/Python unit, schema, ADR, OpenAPI drift |
| `TC_TEST_DATABASE_URL=… pnpm test:migration` | PASS — PostgreSQL repository/migration 21 tests |
| `… pytest apps/api/tests/test_sprint2_cases_postgresql.py -m postgres` | PASS — 1 full API PostgreSQL E2E |
| `pnpm benchmark:sprint2-cases …` | PASS — 100,000 cases, 7/7 p95 targets |
| `pnpm --filter @tc/web build` | PASS — production Vite bundle |
| live FastAPI HTTP chain | PASS — 201→200→409→200→200→200→200→200 |
| live Browser API-backed smoke | PASS after hydrate-race fix |

## 6. Sprint 2 종료 체크와 남은 범위

- [x] 필수 필드를 가진 case가 `ACTIVE`로 저장·조회되고 public key가 발급된다.
- [x] 수정 전후 전체 content와 시각이 immutable version으로 재현되며 diff/restore가 새 version을 append한다.
- [x] 검색·복합 필터·대량 first-page 목록이 100,000-case warm p95 1초 목표 안에서 동작한다.

아래는 이 PASS에 포함하지 않는다.

- production concurrent/cold-cache SLO와 실제 index capacity planning
- work-item public ingestion/lifecycle API
- Slack/Generation API의 실제 `WORKER` source case creation
- cycle snapshot, execution attempt, issue linkage와 Phase 1 전체 acceptance
- production role grant, backup/restore, retention·규제 evidence
