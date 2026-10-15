# PE-SE2-T04 production implementation report

## 결과

`PE-SE2-T04`의 production 구현과 독립 adversarial 검증을 완료했다. `20260827_0023`은
generation-created TestCase의 관리 상태, 불변 lineage, baseline/latest generated version,
write-once release receipt와 append-only management event ledger를 설치한다. 독립 검증에서
발견한 P1 결함을 수정한 뒤 전체 집중·회귀·정적 gate와 실제 PostgreSQL migration
round-trip을 다시 통과했으며 `docs/test-management-roadmap.md`의 T04 항목을 완료로
변경했다.

T04는 suggestion/diff/apply/ignore API 또는 UI, generation provider 실행, Slack reply를
추가하지 않았다. 기존 apply의 `CREATED` materialization만 관리 초기화 권한을 가지며,
`REUSED`, public `SLACK_AI`/`WORKER` 주장, 근거 없는 legacy row는 unmanaged 상태로 남는다.

## 구현 경계

### PostgreSQL ownership와 원장

- `test_cases`에 `generation_managed`, unique lineage/CREATED outcome, same-case
  baseline/latest/release version FK, release timestamp/reason/actor/mutation을 추가했다.
- shape, CREATED link, one-way 전이를 각각 독립 trigger로 강제한다. ever-managed row의
  lineage/baseline/outcome/release receipt는 변경할 수 없고 released row의 재승격은 raw
  SQL에서도 거부된다.
- `test_case_generation_management_events`는 `INITIALIZED|RELEASED`를 append-only로
  보존한다. 관리 시작과 해제는 TestCase/apply outcome/version/audit/outbox와 같은
  transaction에서만 commit된다.
- semantic no-op은 domain version/resource/audit/outbox를 전혀 쓰지 않는다. keyed no-op의
  exact replay만 별도 `api_idempotency_receipts` append-only ledger에 보존하므로 no-op과
  기존 API idempotency 계약을 동시에 지킨다.
- 0022 legacy backfill은 유일한 CREATED outcome과 initial `SLACK_AI` version,
  generation job ID, context/result seal이 모두 일치하는 경우만 provenance를 인정한다.
  이후 version/move/delete/restore 이력이 있으면
  `LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE`로 즉시 released 처리하며, 충돌하거나 모순된
  CREATED history는 migration을 중단한다.

### Repository와 apply 권한

- `GenerationApplication._materialize`가 CREATED outcome을 먼저 기록한 뒤 같은 transaction의
  repository-internal capability로만 management를 초기화한다. REUSED branch에는 초기화
  호출이 없다.
- public content PATCH는 caller의 `source.type` 또는 actor header를 자동 권한으로 사용하지
  않는다. canonical semantic diff가 있으면 새 immutable version과 release event/release
  audit을 함께 기록하고, 정확한 semantic no-op이면 version/resource timestamp/audit/outbox를
  모두 그대로 둔다.
- older-version restore와 cross-parent move, soft-delete가 managed case를 단방향 release한다.
  same-parent reorder는 content lineage를 유지하고, deleted restore는 기존 release receipt를
  변경하지 않는다.
- generation apply outcome이 남은 case의 permanent delete는 FK 예외로 새지 않고 기존
  `PERMANENT_DELETE_CONFLICT` 응답으로 종료된다.

### API 계약

TestCase detail과 TestCase를 포함하는 mutation 응답에 `generationManagement`를 추가했다.
never-managed, active-managed, released를 literal `managed` 값으로 구분하는 strict union으로
노출한다. never-managed 응답은 `{"managed": false}`만 포함하고 active/released 응답은
각 상태의 필수 lineage/release 필드를 빠짐없이 요구한다. Sprint 2 JSON Schema, OpenAPI
JSON, generated TypeScript schema도 같은 wire shape로 갱신했다. exact move no-op은
`auditId`를 만들거나 반환하지 않는다.

## 독립 adversarial 검증에서 수정한 결함

- **기존 공개 case에 대한 CREATED provenance 위조:** 같은 transaction에서 가짜 CREATED
  outcome과 GUC를 조합하면 기존 `SLACK_AI`-shaped version을 managed로 승격할 수 있던
  경로를 차단했다. migration trigger가 baseline version 1, parent, job/apply seal과 source
  metadata 전체를 대조하고 CREATED outcome과 baseline version의 현재 transaction 생성을
  각각 `xmin`으로 증명한다. event insert도 현재 case의 exact receipt와 일치해야 한다.
- **exact move no-op 및 keyed replay:** same-parent/same-position move가 resource version,
  timestamp, audit/outbox를 쓰던 동작을 total no-op으로 수정했다. audit가 없는 PATCH/move
  no-op도 append-only API receipt를 통해 동일 응답을 재생하고, key가 다른 요청에 재사용되면
  기존 conflict를 유지한다.
- **느슨한 public management shape:** 모든 lineage/release 필드가 optional이던 OpenAPI
  모델을 세 상태 union으로 교체해 partial/contradictory response가 생성 contract를
  통과하지 못하게 했다.
- **legacy fail-closed와 event ledger:** contradictory CREATED seal은 0023 upgrade를
  중단하고, backfill release time은 audit/later-version/deletion 중 가장 이른 방어 가능한
  시각을 사용한다. management event update/delete와 case state에 대응하지 않는 fabricated
  release insert를 DB에서 거부한다.

## 검증

검증에는 disposable PostgreSQL 16 + pgvector database를 사용했다.

| 검증 | 결과 |
|---|---|
| T04 focused PostgreSQL | 12 passed |
| T01–T03 + generation apply + case PostgreSQL 회귀 | 44 passed |
| migration/repository PostgreSQL | 57 passed, 기존 collection warning 1 |
| Python non-PostgreSQL unit | 286 passed, 73 deselected |
| TypeScript unit | 279 passed, live E2E 1 skipped |
| Ruff (`backend`, `apps`, `scripts`) | PASS |
| strict mypy | 114 source files, issue 0 |
| strict JSON Schema | 7 schemas, generation pair, Slack fixtures PASS |
| OpenAPI freshness | JSON + generated TypeScript PASS |
| TypeScript typecheck + ESLint | PASS |
| Alembic `0023 -> 0022 -> 0023` | PASS |
| legacy backfill rehearsal | unchanged CREATED 2 managed, divergent/deleted CREATED 2 released, REUSED/direct claim 2 unmanaged |
| contradictory legacy provenance | 0023 upgrade fail-closed PASS |

핵심 실행 명령:

```text
TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/pytest -q \
  apps/api/tests/test_phase_e_generation_management_postgresql.py

TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/pytest -q \
  apps/api/tests/test_phase_e_automatic_generation_postgresql.py \
  apps/api/tests/test_phase_e_automatic_generation_impact_postgresql.py \
  apps/api/tests/test_generation_apply_api_postgresql.py \
  apps/api/tests/test_sprint2_cases_postgresql.py -m postgres

TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> pnpm test:migration
pnpm test:unit:py
pnpm test:unit:ts
pnpm lint:py
pnpm typecheck:py
pnpm test:schema
pnpm openapi:check
pnpm typecheck:ts
pnpm lint:ts

TC_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/alembic -c backend/alembic.ini downgrade 20260827_0022
TC_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/alembic -c backend/alembic.ini upgrade head
```

## 열린 후속 범위

- T05 suggestion/diff/apply/ignore API/UI와 released case에 대한 대체 suggestion 저장은
  여전히 열려 있다.
- provider 실행, provider credential/cost gate, signed Slack live delivery와 Slack reply는
  이 변경의 범위 밖이다.
