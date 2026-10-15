# Sprint 1 통합·회귀·성능 보고서

| 항목 | 결과 |
|---|---|
| 실행일 | 2026-08-20 (Asia/Seoul) |
| 범위 | `P1-S1-T01`~`P1-S1-T09`, PostgreSQL·API·generated client·Web 통합 |
| 데이터베이스 | `pgvector/pgvector:0.8.1-pg16`, PostgreSQL 16.12, pgvector 0.8.1, disposable `tc_sprint1_final_test` |
| 기능 판정 | T01~T09 완료 조건 통과 |
| Sprint 종료 판정 | PASS — 실제 API-backed UI에서 새로고침을 포함한 3-depth create·rename·subtree move·sort·delete preview·soft delete·restore를 재현했다. |
| Phase 판정 | Phase 0 외부 선행 조건이 남아 있으므로 Phase 0·Phase 1은 종료하지 않는다. |

## 1. 통합에서 수정한 결함

- Web 기본 service가 generated `@tc/api-client`를 사용하는 경계를 확인하고, API가 반환하는 `FOLDER_NAME_CONFLICT`, `FOLDER_CONTENT_WARNING`, `RESOURCE_NOT_FOUND`, `SYSTEM_FOLDER_PROTECTED` 등의 안정된 code와 `details`를 fallback에 숨기지 않고 보존하도록 정렬했다.
- 실제 API가 사용하는 workspace audit identity인 `entityType=WORKSPACE_SETTINGS`, `action=UPDATE`와 in-memory adapter·History label을 일치시켰다.
- canonical error JSON Schema에 실제 API가 반환하던 `FOLDER_CONTENT_WARNING`과 `SYSTEM_FOLDER_PROTECTED` shape가 빠진 drift를 보완하고 strict positive fixture를 추가했다.
- PostgreSQL CI job이 repository migration test뿐 아니라 `apps/api`의 Sprint 1 PostgreSQL contract test도 실행하도록 연결했다. 원격 CI 실행과 branch protection 여부는 이번 로컬 gate의 evidence가 아니므로 Phase 0 완료로 과장하지 않는다.
- 5,551개 폴더와 100,000개 synthetic case를 재현 가능하게 만드는 `benchmark:sprint1-folders` command를 추가했다. DB 이름에 `test`가 없는 URL은 거부한다.

## 2. P1-S1 task evidence

| Task | 판정 | 완료 조건 evidence |
|---|---|---|
| `P1-S1-T01` | PASS | migration이 singleton `workspace_settings`, workspace별 `TEST_CASE`·`ISSUE` counter, append-only `audit_logs`를 생성한다. repository test에서 설정 행 1개, 8 worker의 24개 동시 `TC-000001`~`TC-000024` 발급, caller transaction rollback과 before/after audit를 검증했다. |
| `P1-S1-T02` | PASS | active root·child partial unique index, self-reference와 alias constraint가 migration/model에 일치한다. normalized sibling 중복은 repository와 DB 양쪽에서 거부되고 root를 grandchild 아래로 이동하면 `FOLDER_CYCLE`이다. |
| `P1-S1-T03` | PASS | PostgreSQL API contract test가 3-depth tree의 create·rename·move·sort·children query와 409/422를 통과했다. 실제 HTTP server에 generated client를 연결한 smoke에서도 3-level subtree가 이동·정렬 후 다시 조회됐다. |
| `P1-S1-T04` | PASS | delete preview가 3개 폴더와 confirmation 필요를 반환했고, subtree soft delete 후 동일 batch로 3개 폴더를 복원했다. repository/API test는 child가 있는 폴더의 경고, active case가 있는 폴더의 `FOLDER_NOT_EMPTY`, 잘못된 batch와 정상 복원을 자동화한다. |
| `P1-S1-T05` | PASS | Web은 실제 client adapter를 기본 service로 사용하며 lazy children, breadcrumb, next-read rename/move/restore, loading/error/empty와 responsive 3-pane component test가 통과한다. 최종 browser gate는 live API indicator를 확인한 뒤 실제 UI click으로 3-depth `Gate Archive / Checkout / Card checkout`을 만들고 rename·move·sort·delete·restore했으며, 새로고침 뒤 같은 breadcrumb와 system-folder protected surface를 재확인했다. |
| `P1-S1-T06` | PASS | 실제 UI/HTTP에서 workspace version 1→2, 팀명·case/issue prefix·기본 환경을 `Sprint 1 Final QA`·`GATE`·`BUG`·`production`으로 수정했다. 조회한 UTC audit의 `UPDATE` row에서 `before.teamName=QA Team`, `after.teamName=Sprint 1 Final QA`, 시각과 `Test Library UI` actor를 확인했다. |
| `P1-S1-T07` | PASS | 3-level 5,551 folder와 100,000 case(95,000 active) fixture의 모든 leaf direct count와 section/root subtree count가 정확했다. 최종 gate 재실행의 warmup 3회 후 30회 tree+count query는 p50 65.17 ms, p95 84.53 ms, max 98.72 ms로 일반 조회 p95 1,000 ms 목표를 통과했다. |
| `P1-S1-T08` | PASS | 자동 PostgreSQL test가 cycle, 동시에 상호 의존하는 move 중 1 success/1 cycle, stale concurrent rename 1 success/1 version conflict, subtree sort, warning·delete·restore를 검증했다. |
| `P1-S1-T09` | PASS | empty DB rebuild 후 고정 ID `AI 생성함`이 1개 seed된다. `ensure_default_workspace`를 두 번 실행해도 workspace 1개·counter 2개·system folder 1개이며 rename/delete는 repository와 API에서 `SYSTEM_FOLDER_PROTECTED`로 거부됐다. |

## 3. Disposable PostgreSQL 재검증

`pnpm test:migration`은 Alembic `head → base → head` rebuild를 포함해 14개 PostgreSQL test를 모두 통과했다. 여기에는 단일 workspace, 동시 key 발급, normalized sibling uniqueness, cycle, concurrent move, sort, subtree delete/restore, alias suspension, append-only audit와 seed idempotency가 포함된다.

`apps/api/tests/test_sprint1_api_postgresql.py`도 같은 disposable DB에서 통과했다. API test는 3-depth counts, idempotency replay/conflict, concurrent version conflict, delete warning/not-empty, restore, protected system folder, audit cursor pagination과 UTC timestamp를 함께 검증한다.

## 4. 실제 HTTP + generated client + browser smoke

Uvicorn을 disposable DB에 연결하고 Node 24가 `packages/api-client/src/client.ts`의 generated-client wrapper를 직접 실행했다. 수행 순서는 health/settings 조회 → root/child/grandchild 생성 → grandchild rename → child subtree를 다른 root로 move → sort → delete preview → confirmed soft delete → batch restore → settings update → tree/audit 재조회였다.

최종 delivery gate에서는 같은 Uvicorn에 Web dev server를 실제 API origin으로 연결하고 Browser로 Test Library와 Settings를 조작했다. root 2개 → child → grandchild를 생성하고 grandchild rename, 2-level subtree move와 sort, page reload 후 breadcrumb, 3-folder delete warning, soft delete, session trash restore, AI system folder의 잠긴 inspector, workspace update와 History를 순서대로 확인했다. 이는 fallback fixture가 아니라 API indicator `v0.1.0`, PostgreSQL 최종 tree와 10개 audit row가 모두 일치하는 live flow다.

| 관측 | 결과 |
|---|---|
| API | `service=api`, `status=ok`, version `0.1.0` |
| 계층 | 3 levels, move affected folder 2개, restore 후 `Gate Archive / Checkout / Card checkout` |
| version | 최종 root 3, Checkout 5, Card checkout 4; sort order 5 |
| delete preview | affected folder 3, active case 0, confirmation required |
| settings | version 1→2, `Sprint 1 Final QA`, `GATE`, `BUG`, `production` |
| audit | 10개 gate event, `CREATE`, `RENAME`, `MOVE_SUBTREE`, `SORT`, `SOFT_DELETE_SUBTREE`, `RESTORE_SUBTREE`, `UPDATE`; settings before/after와 UTC 시각 확인 |
| browser 재조회 | `Gate Archive / Checkout / Card checkout`, depth 2, Checkout sort order 5, restore 뒤 version 3과 protected `AI 생성함` inspector 확인 |

## 5. 성능 측정

실행 command는 `TC_TEST_DATABASE_URL=... pnpm benchmark:sprint1-folders`다. fixture는 50 root × root당 10 section × section당 10 leaf에 system folder 1개를 더한 5,551개 폴더와 leaf에 균등 배치한 case 100,000개로 구성했다. 20번째 case마다 soft-deleted로 두어 active 95,000개를 기대값으로 검증했고 `(folder_id, deleted_at)` count-ready index를 사용했다.

이 결과는 로컬 Docker의 warm-cache, single-client repository query 측정이다. production hardware의 concurrent load·cold-cache SLO를 대신하지 않지만 목표 대비 약 11.8배 여유가 있어 Sprint 1에서 closure table이나 cache를 추가할 근거는 발견되지 않았다. 영속 `test_cases` schema와 paginated case list/search의 100,000-row 성능은 `P1-S2-T10` 소유이며 이번 PASS에 포함하지 않는다.

## 6. 전체 gate 결과

| Command | 결과 |
|---|---|
| `pnpm check` | PASS — ESLint, Ruff, TS/Python typecheck, TS 37 tests, Python 31 tests, strict schema 12 fixtures, ADR 14개, OpenAPI/client drift |
| `pnpm --filter @tc/web build` | PASS — Vite production build, 65 modules |
| `pnpm test:migration` | PASS — PostgreSQL 14 tests |
| `python -m pytest apps/api/tests -m postgres` | PASS — Sprint 1 PostgreSQL API contract 1 test |
| `pnpm benchmark:sprint1-folders` | PASS — p95 84.53 ms ≤ 1,000 ms |
| 실제 Uvicorn + generated client smoke | PASS — 3-depth mutation·restore·settings·audit 10 events |
| 실제 Web + Browser smoke | PASS — live API indicator, 3-depth create·rename·move·sort·reload·delete preview·delete·restore·settings·History |

## 7. 남은 조건과 외부 의존성

- `P0-S0-T02`의 익명화된 실제 Slack sample 10개가 없고 Slack credential도 제공되지 않았다. smoke는 `TC_SLACK_MODE=disabled`로 실행했다.
- CI workflow는 존재하지만 이 worktree에서 원격 run과 merge-block branch protection을 확인할 권한·evidence가 없다. 따라서 `P0-S0-T08`과 Sprint 0의 CI 종료 체크는 그대로 미완료다.
- Google Drive OAuth credential·root folder와 실제 배포 서버·Slack 설치 정보가 없다. Drive/Slack external E2E를 통과했다고 주장하지 않는다.
- 실제 API에는 deleted-folder collection endpoint가 아직 없어 Web adapter는 현재 browser session에서 수행한 delete의 batch만 restore 후보로 보관한다. durable restore command 자체는 통과하지만 page reload 후 trash 탐색 UX는 후속 API 확장 대상이다.

## 8. 결론

`P1-S1-T01`~`T09`와 Sprint 1의 세 종료 조건은 disposable PostgreSQL, 실제 HTTP/generated client, 실제 Web browser flow, component/build와 성능 측정으로 충족했다. 따라서 현재 범위의 Sprint 1은 종료 가능하다. 다만 Phase 0의 외부 sample·credential·원격 CI evidence가 없으므로 Phase 0·Phase 1 전체 종료로 확대 해석하지 않는다.

## 9. ADR coverage와 최종 Sprint 1 판정

| Canonical owner | 상태 | 독립 결정 경계 | 구현·검증 evidence | 남은 evidence |
|---|---|---|---|---|
| [ADR-0013](../adr/0013-postgresql-adjacency-list-folder-hierarchy.md) | Accepted | adjacency-list topology, recursive move, active sibling uniqueness, alias, count, soft delete/restore와 system folder 보호를 한 folder invariant로 소유 | migration, folder repository, PostgreSQL/API concurrency·lifecycle test와 5,551-folder/100,000-case benchmark | Sprint 2 영속 case aggregate, AI full-path·semantic resolver, production role/startup invariant와 concurrent cold-cache load |
| [ADR-0014](../adr/0014-workspace-key-allocation-and-append-only-audit.md) | Accepted | workspace key counter, caller aggregate와 human audit side record가 함께 commit·rollback되는 transaction boundary를 소유 | migration, 24개 concurrent allocation, caller rollback, append-only trigger, API idempotency replay/conflict와 settings before/after audit | Sprint 2 case/issue aggregate의 final key unique path, production role grant, 전체 entity secret canary와 운영 retention 정책 |

[Decision coverage manifest](../adr/decision-coverage.json)는 formal ADR 14개와 canonical decision 14개를 1:1로 연결하고 Sprint 1 formal ADR을 위 두 개로만 제한한다. ADR-0002는 PostgreSQL system of record, ADR-0012는 불변 case/cycle history, ADR-0005는 projection outbox를 소유하므로 ADR-0013의 folder topology나 ADR-0014의 human audit transaction boundary와 중복되지 않는다. 두 Sprint 1 ADR은 핵심 구현과 재현 가능한 evidence가 있어 `Accepted`이지만 위 표의 후속 evidence나 Phase 0 외부 조건까지 완료됐다는 뜻은 아니다.

최종 Sprint 1 delivery 판정은 **PASS**다. `P1-S1-T01`~`T09`, 두 ADR의 구현 gate와 로드맵의 세 Sprint 종료 조건을 모두 실제 evidence로 확인했다. Phase 0은 실제 Slack sample·credential과 원격 CI/branch-protection evidence 부재 때문에 여전히 종료하지 않으며, 그 결과 Phase 1 전체도 아직 종료하지 않는다.
