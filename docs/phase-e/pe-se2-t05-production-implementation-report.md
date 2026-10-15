# PE-SE2-T05 production implementation report

## 결과

`PE-SE2-T05`의 production 구현과 새 worker의 독립 adversarial 검증을 완료했다.
`20260827_0024`는 server-issued `suggestionTargetRef`, immutable suggestion/diff,
append-only `APPLIED|IGNORED` resolution을 설치한다. released generated case는 새 provider
후보가 도착해도 자동으로 변경되지 않으며, 사용자가 현재 baseline pin을 명시해 반영할 때만
새 `TestCaseVersion` 하나가 추가된다.

검증 중 P1 결함 3건과 release metadata drift 1건을 수정한 뒤 실제 PostgreSQL 16 + pgvector에서
`0024 → 0023 → 0024`, T05 focused 9/9, T01~T04·ordinary apply·case·cycle 회귀 58/58,
전체 repository 57/57와 모든 정적·계약·Web/build gate를 다시 통과했다. 남은 P0/P1 결함은 없다.

## 완료 경계

### 봉인된 target과 suggestion 원장

- T03의 현재 `REGENERATE` root가 ever-managed·released·active case를 선택하면 별도
  `generation-suggestion/1.0.0` Input에 opaque ref와 exact case/version/lineage target seal을 넣는다.
  provider output은 ref와 candidate만 반환할 수 있고 case/job/actor/source 권한을 주장할 수 없다.
- materialization은 job/Input/result/context/assessment/root/target seal과 unique successful attempt를
  다시 검증한다. unknown·duplicate·cross-root ref, unsupported shape, stale relevant source,
  deleted/changed case는 case write 없이 fail closed한다.
- baseline/proposal/diff와 provenance digest는 immutable suggestion row에 저장된다. 동일 proposal은
  no-op이고, 더 최신 proven source만 직전 OPEN row를 immutable edge로 supersede한다.
- migration deferred guard는 target의 workspace·Slack item·assessment·root·CREATED outcome·lineage·
  baseline version과 sealed Input 노출을 교차 검증한다. suggestion guard는 successful attempt/result의
  exact ref/index, context와 same-case/source-ordered supersession을 검증한다.

### deliberate apply와 ignore

- apply는 workspace → suggestion → case lock order를 사용하고 OPEN, active/non-deleted, released
  lineage, immutable diff seal, request resource/current-version pin을 검증한다. 성공 시 proposed snapshot으로
  version 하나를 추가하고 원래 T04 release receipt와 `generation_managed = false`를 그대로 보존한다.
- ignore는 case/version/step/tag를 쓰지 않고 resolution, suggestion audit/outbox와 idempotency receipt만
  append한다. apply와 ignore 모두 exact replay를 원래 response로 반환하고 key reuse나 terminal state는
  typed 409로 종료한다.
- resolution deferred guard는 same-workspace suggestion/case/version뿐 아니라 exact suggestion audit,
  actor/reason/receipt, APPLIED version의 trusted suggestion/job/context/result provenance까지 검증한다.
  target, suggestion, resolution과 관련 audit은 update/delete할 수 없다.
- soft delete는 OPEN을 유지하고 apply만 `CASE_DELETED`로 막는다. restore 후에는 aggregate clock이
  증가했으므로 기존 suggestion이 `CASE_VERSION_STALE`이다. permanent delete는 target/suggestion/
  resolution FK가 있는 동안 기존 `PERMANENT_DELETE_CONFLICT`로 거부되고 provenance를 cascade하지 않는다.

### API와 Web wire

- public API는 list/detail/apply/ignore만 제공하고 create/update/delete/rebase/reopen/force route가 없다.
  cross-workspace/unknown ID는 non-enumerating 404, spoofed extra field는 422, stale·terminal·provenance는
  stable 409다.
- 로컬 Web fetch adapter의 네 URL, `{items}` list envelope, camelCase resources, apply/ignore body,
  `Idempotency-Key`와 actor header를 FastAPI response model 및 generated OpenAPI schema와 대조했다.
  focused adapter 3/3, OpenAPI 3/3, freshness check와 TypeScript typecheck가 실제 wire 호환을 증명한다.
- TestCase detail은 released lineage에 `생성 변경 제안` region을 표시하고 OPEN에만 apply/ignore를
  제공한다. native disclosure, focus-trapped Escape/Tab dialog, status/alert, stale reload와 390 px stacked
  layout/44 px target을 focused Web 4/4와 전체 Web 176/176으로 검증했다.

## 독립 adversarial 검증에서 수정한 결함

1. **DB cross-table provenance 미완결:** 초기 0024 guard는 application write가 아닌 raw SQL에서
   잘못된 baseline number, 다른 case/lineage supersession, terminal suggestion resolution,
   audit/receipt 불일치를 충분히 거부하지 못했다. sealed Input/result와 same-case/source ordering,
   exact audit/actor/receipt/APPLIED version provenance를 deferred guard에 추가했다. forged resolution
   probe는 transaction 전체 rollback과 resolution 0행을 확인했다.
2. **harmless SKIP 오거부:** materialization이 원래 `REGENERATE` assessment가 최신 row가 아니면 모두
   stale 처리해, 같은 root를 재사용하고 fingerprint/config가 동일한 최신 `SKIP`까지 버렸다.
   reused job의 root와 두 digest가 일치할 때만 허용하도록 수정했다. 실제
   `INITIAL_GENERATE → REGENERATE → SKIP` PostgreSQL chain 뒤 suggestion이 생성됨을 확인했다.
3. **apply-vs-human-edit deadlock:** suggestion apply가 suggestion/case를 잡은 뒤 `CaseRepository`에서
   workspace를 잡아, 일반 사람 편집의 workspace→case 순서와 역전됐다. T03 target capture와
   materialization은 workspace→Slack item→job/assessment→case/suggestion, apply/ignore는
   workspace→suggestion→case 순서로 통일했다. 무지연 5회 경합에서 매번 한 요청만 200이고 다른 요청은
   `CASE_VERSION_STALE` 또는 기존 `VERSION_CONFLICT` 409였으며 500/deadlock은 재발하지 않았다.
4. **migration head test drift:** unit gate가 head를 0023으로 고정해 0024에서 1건 실패했다.
   expected head를 `20260827_0024`로 갱신하고 Python unit 289/289를 다시 통과했다.

추가 공격성 probe에서 확인했던 apply-vs-ignore는 이제
`test_pe_se2_t05_concurrent_apply_and_ignore_have_one_terminal_winner` 영속 PostgreSQL 회귀다.
두 독립 production API client를 `Barrier`/`ThreadPoolExecutor`로 동시에 시작해 200/409 단일 승자,
loser `NOT_OPEN`, resolution 정확히 1행을 검증한다. APPLIED 승자는 aggregate/case version과
case audit/outbox를 정확히 1씩 늘리고, IGNORED 승자는 case delta 0이며, exact replay와 반대 결정은
그 상태를 바꾸지 못한다. 이 회귀를 같은 별도 DB에서 10회 연속 실행해 모두 통과했다.
그 밖의 공격성 probe에서
unknown `suggestionTargetRef`는 `PROVIDER_OUTPUT_INVALID`, suggestion 0행, case byte-equivalent unchanged였다.
다른 workspace로 같은 suggestion ID를 조회하면 resource-not-found였고 target UPDATE는 append-only trigger가
거부했다. stale apply rollback, superseded terminal state, immutable diff, exact replay, provider spoof와
delete/restore는 focused PostgreSQL suite에서 함께 검증했다.

## 검증 결과

| 검증 | 결과 |
|---|---|
| T05 focused PostgreSQL (latest, persistent race included) | 11 passed in 15.97s |
| T01~T04 + ordinary generation apply + case + cycle PostgreSQL 회귀 | 58 passed |
| 초기 T05 9개와 위 58개 PostgreSQL gate 통합 재실행 | 67 passed |
| T04/T05/T10/T11 protection matrix (latest) | 25 passed in 32.58s |
| migration/repository PostgreSQL | 57 passed, 기존 collection warning 1 |
| T05 provider suggestion contract | 4 passed |
| T05 OpenAPI | 3 passed |
| T05 Web workspace + fetch adapter | 7 passed (UI 4, adapter 3) |
| 전체 Web | 176 passed, live E2E 1 skipped |
| 전체 TypeScript unit | 290 passed, live E2E 1 skipped |
| Python non-PostgreSQL unit | 289 passed, 82 deselected |
| strict JSON Schema | 7 schemas, versioned generation pair, Slack fixtures PASS |
| OpenAPI JSON + generated TypeScript drift | PASS |
| Ruff | PASS |
| strict mypy | 116 source files, issue 0 |
| TypeScript typecheck + ESLint | PASS |
| Web production build | 95 modules, PASS |
| Alembic `0024 → 0023 → 0024` | PASS |

핵심 실행 명령:

```text
TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/pytest -q \
  apps/api/tests/test_phase_e_generation_suggestions_postgresql.py

TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/pytest -q -m postgres \
  apps/api/tests/test_phase_e_automatic_generation_postgresql.py \
  apps/api/tests/test_phase_e_automatic_generation_impact_postgresql.py \
  apps/api/tests/test_phase_e_generation_management_postgresql.py \
  apps/api/tests/test_generation_apply_api_postgresql.py \
  apps/api/tests/test_sprint2_cases_postgresql.py \
  apps/api/tests/test_sprint3_cycles_postgresql.py

TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> pnpm test:migration
pnpm test:unit:py
pnpm test:unit:ts
pnpm test:schema
pnpm openapi:check
pnpm lint:py
pnpm typecheck:py
pnpm typecheck:ts
pnpm lint:ts
pnpm --filter @tc/web build
```

## 외부 상태와 후속 범위

T05 완료는 provider-facing schema와 deterministic stored-result 경계까지다. 실제 provider credential,
model/cost/quota 선택과 외부 HTTP 호출은 실행하지 않았고 T05 완료 조건이 아니다. 실제 signed Slack
delivery와 성공·실패 reply/deduplication은 다음 `PE-SE2-T06` 범위이며 이번 작업은 Slack thread에
답글을 쓰지 않았다.

production-shaped historical data의 배포 전 preflight, 실제 provider/Slack credential live gate,
T06 failure posting/retry, T08 eval과 T10~T11 full E2E는 별도 운영·후속 release gate로 남는다.
이번 검증의 live E2E 1 skipped도 T05 Web unit/build 실패가 아니라 외부 상태가 필요한 기존 gate다.
검증 종료 후 이 작업이 만든 disposable DB `tc_pe_se2_t05_verify_test`만 삭제했으며 공유 개발 DB와
기존 PostgreSQL/MinIO/ClamAV container는 변경하지 않았다.
