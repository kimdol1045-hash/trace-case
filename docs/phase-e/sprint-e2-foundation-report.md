# Phase E Sprint E2 기반 구현 보고서

| 항목 | 내용 |
|---|---|
| 실행일 | 2026-08-28 |
| Orca 근거 | T01 `run_d5675601d041`; T02 `task_bb058a4168e7` 계약, `task_38db3c9dc153` 독립 audit, `task_128a09c7d9fd` 구현, `task_98d7ca6501ef` 통합 gate; T03 `task_d9e045eab463` 독립 production audit·마감; T05 `task_949eeb0d9858` 독립 adversarial 검증·마감; T06 `task_6367d9b41b32` 독립 adversarial 검증·수정·마감; T07 `task_6d514ce0e90f` production composition·DB/UI 독립 adversarial 검증·수정·마감 |
| 완료 범위 | `PE-SE2-T01`~`PE-SE2-T07`: orchestration, deduplication, impact assessment, irreversible human release, immutable suggestion, original-thread terminal reply, durable provider retry/exhaustion/materialization/history와 production worker composition |
| 미완료 범위 | `PE-SE2-T08`~`PE-SE2-T11`, golden quality/version regression/실제 root→Slack E2E/human-modified suggestion 전체 E2E 및 실제 signed Slack/provider credential live gate |

- [x] `PE-SE2-T05` sealed generation suggestion diff·apply·ignore API/UI 및 독립 production gate
- [x] `PE-SE2-T06` 생성 terminal fact의 원본 Slack thread idempotent reply 및 독립 production gate
- [x] `PE-SE2-T07` durable provider single-call, retry/exhaustion/materialization, failure API/UI 및 독립 production composition gate
- [ ] 다음 항목: `PE-SE2-T08` golden quality release gate

T02 구현 전 계약과 독립 audit을 분리했고, 구현 뒤 `task_98d7ca6501ef`에서 migration·model·DB
predicate와 runtime 경합/rollback/retry 경로를 release-gate 관점으로 다시 검증했다. T03은
`task_d9e045eab463`에서 계약, migration `0022`, ORM·trigger, semantic/config digest,
assessment/materialization chain과 runtime receipt를 독립적으로 재검토했다. 이 audit에서
`changed_fields`의 중복·고정 순서·reserved-value 불변식과 cross-table deferred guard를 DB에서
완전하게 강제하지 않던 결함을 보강한 뒤 전체 지정 gate를 다시 통과시켰다. T04는
`20260827_0023`에 CREATED-only lineage와 irreversible release ledger를 추가했고, T05는
`20260827_0024`에 sealed target, immutable suggestion/diff와 append-only decision receipt를 추가했다.
T05 독립 검증에서 DB provenance guard, harmless SKIP, apply-vs-human-edit lock order를 보강했다.
T06 독립 검증은 구현 보고를 신뢰하지 않고 migration/ORM/capture/transport/worker/main/self-filter를
다시 감사해 final-attempt receipt 누락, retryable failure의 caller-supplied exhaustion 우회,
reconciliation destination drift, forged DEAD receipt, unsafe product-link delimiter와 implicit write
enablement를 보강했다.
T07 독립 검증은 실제 worker `main()`이 provider adapter를 조립하지 않던 P0를 찾아 명시적 opt-in
Settings와 `max_attempts=1` production runner, domain lease heartbeat를 연결했다. success observation crash는
blind resend 없이 outcome-unknown으로 닫고, notification helper의 synthetic pre-SUCCEEDED materialization
우회를 제거했다. migration은 schedule DELETE와 background type/payload 위조를 막으며, failure list의
operator-retry 이후 actionability와 Web filter/session idempotency/CSS·semantic 결함도 함께 보강했다.

## 완료 경계

quiet-window가 만료된 현재 Slack source version은 다음의 결정적 경로를 지난다.

1. Slack worker가 현재 `quiet_job_id`, claim attempt, source version과 DB의
   `quiet_due_at`을 잠그고 검증한 뒤 `WORK_ITEM_PARSE`를 한 번 enqueue한다.
2. parse transaction이 현재 non-deleted root/thread revision을 4필드 결과로 저장하고
   `SLACK_PARSE_RESULT_STORED` outbox와 idempotent
   `SLACK_AUTOMATIC_GENERATION` handoff를 함께 commit한다.
3. 명시적으로 주입된 `KnowledgeOutboxConsumer`가 sealed Slack bundle을 추출·검증하고
   PostgreSQL typed graph/evidence/pgvector projection을 만든다. 기존 같은 version의
   non-stale checkpoint가 있으면 재추출하지 않고 그대로 resume한다.
4. 같은 transaction에서 `GenerationRepository`가 현재 ACTIVE graph만 한 번 grounded retrieval해
   Generation Input `1.0.0`을 준비하고, T03 comparator가 transport identity를 제외한 semantic
   projection과 별도 contract-config digest를 만든다.
5. assessment가 `INITIAL_GENERATE|REGENERATE`이면 바로 그 built context로 root를 봉인하고,
   `SKIP`이면 마지막 automatic materialization을 재사용하며, `REVIEW_REQUIRED`이면 job을 만들거나
   재사용하지 않는다. assessment, 생성 audit, checkpoint의 assessment/outcome/job receipt는 한
   transaction으로 commit되고 replay는 ledger를 통해 같은 결정을 반환한다.

새로 선택된 생성 job은 `provider = model = UNCONFIGURED`, `result = null`, 시작·완료 시각 없음으로
남는다. `GenerationJobLifecycle.execute`와 provider runner는 이 경로에 없고 TestCase, apply,
Slack reply 원장도 변경하지 않는다. `SKIP`과 `REVIEW_REQUIRED`는 새 generation job 자체가 없다.

T02는 이 T01 transaction 경계에 다음 중복 방지 규칙을 추가했다.

1. `slack_work_items.id`가 이미 보장하는 canonical
   `(workspace_id, slack_workspace_id, channel_id, canonical_root_message_ts)` identity를 그대로
   사용하며 delivery/outbox/background-job ID를 제품 key로 사용하지 않는다.
2. `generation_trigger`를 `MANUAL|AUTOMATIC`으로 영속화하고 retry child가 root trigger를
   상속한다.
3. DB partial unique index가 같은 `(slack_work_item_id, source_version)`의 `AUTOMATIC` root를
   하나로 제한하되 manual root와 retry child는 제외한다.
4. repository는 canonical work-item row를 잠근 뒤 기존 automatic root를 조회하거나 같은 잠금
   아래 다음 generation ordinal을 할당한다. 서로 다른 manual idempotency key는 같은 source
   version에서도 반복 생성할 수 있다.
5. exact-delivery receipt가 없더라도 canonical root를 원자적으로 재사용해 checkpoint를
   복구하며, manual/wrong/corrupt receipt는 cache miss로 취급하지 않고 typed conflict로 거부한다.

T03은 이 canonical source/root 경계 위에 다음 영향 판정 규칙을 추가했다.

1. full Generation Input digest를 비교 키로 사용하지 않고 네 Slack 필드뿐 아니라 ACTIVE
   requirement/decision/constraint와 content-based evidence, existing cases, FAIL/BLOCKED execution,
   open issue, extra source, category candidate와 generation policy를 canonical projection으로 묶는다.
2. comparator/input/output/ontology/prompt/retrieval/extractor/validator/classifier version은 별도
   `contract_config_digest`로 봉인하며, 둘 다 지원되고 semantic/config digest가 같을 때만 `SKIP`한다.
3. semantic predecessor는 직전 assessment, materialization predecessor는 직전 automatic root로
   분리한다. 따라서 v1 generate→v2 SKIP→v3 change는 v2 chain을 보존하면서 v1 root에서 v3 root를
   새로 materialize한다. manual root와 automatic retry child는 baseline이 아니다.
4. ambiguous, selected evidence 삭제·retract, legacy/incompatible seal, unsupported comparator와 unsafe
   state는 `REVIEW_REQUIRED`로 남기고 job을 만들지 않는다. explicit validated supersede만 deterministic
   `REGENERATE`가 될 수 있다.
5. `(slack_work_item_id, current_source_version)` assessment는 append-only이며 job link는 같은 work item의
   automatic root인지 deferred constraint trigger로 검증한다. `changed_fields`는 DB에서도 allowlist,
   중복 없음, 고정 순서, outcome별 reserved-value shape를 강제한다.

## T04~T05 사람 수정 보호

1. generation apply의 `CREATED`만 immutable lineage와 management event를 시작한다. 사람 semantic edit,
   parent move, older-version restore와 soft delete는 write-once release receipt와 함께 management를
   영구 해제하고, exact no-op·REUSED·public source/actor 주장은 관리 권한을 만들지 않는다.
2. released case의 새 automatic `REGENERATE` root는 별도 `generation-suggestion/1.0.0` Input에
   server-issued opaque target을 봉인한다. provider는 ref와 candidate만 반환하며 case/job/lineage/actor를
   권한으로 주장할 수 없다.
3. materialization은 case를 변경하지 않고 baseline/proposal/diff와 source provenance를 immutable row로
   저장한다. 더 최신 proven source만 same-case OPEN suggestion을 edge로 supersede하며 과거 diff는 남는다.
4. apply는 exact case resource/current-version pin이 맞을 때만 새 immutable version 하나와 APPLIED receipt를
   같은 transaction에 append한다. ignore는 case ledger를 전혀 쓰지 않고 IGNORED receipt만 append한다.
   두 결정 모두 workspace-first lock order, exact replay와 typed stale/terminal conflict를 사용한다.
5. target/suggestion/resolution deferred guard가 workspace→Slack item→assessment/root→CREATED lineage→
   baseline/result/audit/receipt를 교차 검증한다. soft delete/restore는 suggestion을 rebase하지 않고,
   permanent delete는 provenance FK가 존재하는 동안 conflict로 거부된다.

## T06 원본 thread terminal reply

1. ordinary apply, suggestion materialization, 최종 non-retryable failure transaction이 terminal
   authority, frozen intent와 `SLACK_GENERATION_NOTIFICATION_DELIVERY` job을 함께 commit한다. Manual,
   SKIP, REVIEW_REQUIRED와 T07의 durable exhaustion authority가 없는 retryable failure는 intent를
   만들지 않으며 stale/unavailable/config mismatch는 provider call 없는 terminal suppression이다.
2. destination은 immutable Slack work item/team/channel/root와 live latest revision에서만 결정한다.
   Worker payload는 intent ID만 받고 production main은 configured token team과 explicit notification
   opt-in이 모두 있을 때만 handler를 등록한다. credential-free 기본은 Slack disabled/write false다.
3. committed POST permit 뒤 DB transaction 밖에서 `chat.postMessage`를 한 번만 호출한다. unresolved
   permit은 POST를 다시 허용하지 않고 exact provider bot/app marker, client ID, channel, parent와 frozen
   text가 모두 맞는 thread reconciliation만 수행한다. final attempt의 ack loss도 같은 attempt에서
   reconcile하고, not-found/transport ambiguity/rate-limit exhaustion은 receipt 없이 job만 DEAD가 되지
   않도록 fixed terminal receipt로 닫는다.
4. raw SQL은 materialization seal, current source, notification authority, attempt/observation/receipt
   조합과 immutable update/delete를 deferred guard로 검증한다. Delivered timestamp는 root와 달라야 하고
   forged ACK→AUTH DEAD 같은 불법 proof 조합은 commit되지 않는다.
5. T06 metadata를 가진 provider-attested bot/app reply는 HTTP Events mapping과 thread-sync merge 전에
   제거되어 source version, outbox와 generation job 수를 바꾸지 않는다. 링크는 trusted product HTTPS
   authority만 허용하고 Slack mrkdwn delimiter, credential/query/fragment, raw provider body, unfurl,
   broadcast/top-level fallback과 `chat:write.public`은 허용하지 않는다.

## Queue, 실패와 원자성

- generic worker는 등록된 handler job type만 claim한다. 따라서 Slack worker가 knowledge 또는
  다른 worker의 job을 가져가 retry budget을 소진하지 않는다.
- 실행 중인 claim은 heartbeat하고 `heartbeat`, `succeed`, `fail` 모두 worker ID와 attempt를
  함께 비교한다. 재claim된 동일 worker ID의 이전 attempt는 completion CAS를 통과하지 못한다.
- 잘못된 UUID/필드/영역 payload는 typed permanent error로 즉시 `DEAD` 처리하고, 아직 due가
  아니거나 projection 준비가 덜 된 상태는 typed retry delay를 사용한다. stale source delivery는
  성공적인 terminal no-op이고 새 projection/job을 만들지 않는다.
- extraction, embedding 또는 job/context seal 뒤 fault가 발생하면 projection node/evidence/
  embedding/checkpoint, generation context/input/job, audit와 receipt가 모두 rollback된다. outbox에는
  bounded failure metadata만 남아 같은 background job stage가 안전하게 resume할 수 있다.
- exact checkpoint receipt는 같은 delivery replay fast path지만 제품 판정 권한은 T03 assessment
  ledger다. 서로 다른 delivery가 같은 canonical work item/source version을 가리키면 같은 assessment로
  수렴한다. `SKIP` receipt는 더 오래된 materialization job을 합법적으로 가리키고,
  `REVIEW_REQUIRED` receipt에는 job ID가 없다.
- work-item row lock이 assessment predecessor와 materialization baseline 선택, 선택적 job seal과 receipt
  commit을 직렬화한다. stale unseen version은 terminal no-op이고, commit 전 fault는 projection,
  assessment, job/context/Input, audit와 receipt를 함께 rollback한다.

## Release-gate 검증

이름에 `test`가 포함된 local disposable PostgreSQL 16 + pgvector database를 사용했고 각 fixture는
테스트 전후 authoritative table을 truncate했다.

| 검증 | 결과 |
|---|---|
| T05 focused PostgreSQL contracts | 9 PASS |
| T06 focused contract / PostgreSQL reply / apply / suggestion | 6 / 7 / 15 / 10 PASS |
| T07 focused RED / normal PostgreSQL | 5+2+3 / 4 PASS |
| T01~T06 PostgreSQL 회귀 | 79 PASS |
| T01~T07 PostgreSQL product 묶음 | 83 PASS |
| 전체 PostgreSQL migration/repository | 57 PASS, 기존 collection warning 1 |
| 전체 Python non-PostgreSQL unit | 326 PASS, 104 deselected |
| 전체 TypeScript unit | 307 PASS, live E2E 1 skipped |
| 전체 Web | 180 PASS, live E2E 1 skipped |
| worker | 21 PASS |
| T05 provider/OpenAPI/Web focused | 4 / 3 / 7 PASS |
| Ruff | `backend`, `apps`, `scripts` PASS |
| Python mypy | 120 source files PASS |
| JSON Schema + OpenAPI/generated client drift | PASS |
| TypeScript typecheck + ESLint + Web production build | PASS, 96 modules |
| Alembic clean head + `0026 → 0025 → 0026` | PASS, current head `20260828_0026` |

사용한 명령은 다음과 같다.

```text
pnpm lint:py
pnpm typecheck:py
pnpm test:unit:py
pnpm test:unit:ts
pnpm test:schema
pnpm openapi:check
pnpm typecheck:ts
pnpm lint:ts
pnpm --filter @tc/web build

TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> pnpm test:migration
TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/pytest -q \
  apps/api/tests/test_phase_e_generation_suggestions_postgresql.py

TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/pytest -q \
  apps/api/tests/test_phase_e_automatic_generation_postgresql.py \
  apps/api/tests/test_phase_e_automatic_generation_impact_postgresql.py \
  apps/api/tests/test_phase_e_generation_management_postgresql.py \
  apps/api/tests/test_phase_e_generation_suggestions_postgresql.py \
  apps/api/tests/test_phase_e_automatic_generation_slack_reply_postgresql.py \
  apps/api/tests/test_phase_e_automatic_generation_retry_history_postgresql.py \
  apps/api/tests/test_generation_apply_api_postgresql.py

TC_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/alembic -c backend/alembic.ini downgrade 20260827_0023
TC_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/alembic -c backend/alembic.ini upgrade 20260827_0024

TC_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/alembic -c backend/alembic.ini downgrade 20260827_0024
TC_DATABASE_URL=<disposable-postgresql-test-url> \
  .venv/bin/alembic -c backend/alembic.ini upgrade 20260828_0026
```

T05 focused suite는 sealed target/ref, immutable diff, exact replay, stale/rollback, apply/ignore,
delete/restore, supersession과 public spoof를 검증한다. 별도 adversarial probe는 unknown ref,
cross-workspace non-enumeration, forged audit/receipt rollback, apply-vs-ignore 단일 resolution과
apply-vs-human-edit 경합 5회를 검증했다. T01~T04/apply/case/cycle 회귀는 irreversible release,
no-reacquisition, cycle-pinned version 불변과 permanent-delete conflict를 함께 보존한다.

## 정리와 잔여 gate

- test fixture 종료 뒤 `generation_jobs`, `knowledge_projection_events`, `background_jobs`,
  `slack_work_items`는 모두 0행이었다.
- 기존 PostgreSQL 16+pgvector의 exact disposable test DB 두 개와 deterministic transport만 사용했다.
  T07 Web production preview와 local mock API process는 검증 뒤 종료했고 외부 provider/Slack call은 0회다.
  새 worktree나 영구 container는 만들지 않았다.
- `20260827_0021` migration과 SQLAlchemy model은 같은 non-null trigger, allowed-value check,
  root-only partial unique predicate를 가진다. historical fixture는 projection/audit provenance root와
  그 retry child를 `AUTOMATIC`, 나머지를 `MANUAL`로 backfill했고 upgrade/downgrade/upgrade를 통과했다.
- `20260827_0022`는 append-only assessment, source-version uniqueness, selected-job uniqueness,
  outcome/digest/job-link shape와 canonical materialization을 강제한다. 독립 audit 보강 뒤
  `changed_fields` 중복·역순·reserved misuse도 거부하고 cross-table link guard는
  `DEFERRABLE INITIALLY DEFERRED`다. `0022 -> 0021 -> 0022` round trip과 전체 migration suite가
  통과했다.
- `20260827_0023`은 CREATED-only lineage, write-once release receipt와 append-only management
  event를 강제하고 released row의 raw SQL 재획득을 거부한다. `20260827_0024`는 sealed target,
  suggestion/diff/resolution을 append-only로 보존하며 exact source·baseline·supersession·audit/receipt
  link를 deferred guard로 검증한다. `0024 → 0023 → 0024`와 전체 migration suite가 통과했다.
- `20260828_0025`는 suggestion materialization batch receipt와 intent/permit/observation/terminal
  receipt를 append-only로 보존한다. current source, non-retryable failure authority, exact
  materialization seal, attempt-operation-observation과 DEAD/delivery proof 조합을 deferred guard로
  검증하며 `0025 → 0024 → 0025`와 전체 migration suite가 통과했다.
- `20260828_0026`은 execution/event/provider permit·observation/operator retry·exhaustion과
  materialization schedule authority를 설치한다. committed permit/observation과 lease-token CAS,
  background type/payload, root/source/workspace/policy/budget 및 terminal notification을 deferred guard로
  검증하고 mutable schedule DELETE도 거부한다. clean head와 `0026 → 0025 → 0026` round trip이 통과했다.
- 운영 DB가 아직 `0020`일 때 [deduplication contract](./automatic-generation-deduplication-contract.md)의
  historical collision preflight query를 먼저 실행해야 한다. 한 canonical source snapshot에서
  automatic evidence root가 둘 이상인 행이 하나라도 나오면 배포를 중단하고 충돌 원장을 보존한 채
  명시적으로 조정해야 하며 migration이 임의 winner를 고르게 해서는 안 된다.
- 실제 Slack signature/credential과 production knowledge extraction/embedding 설정을 사용한 live
  delivery/re-delivery, production-shaped historical data의 collision 0행 확인, 실제 provider credential
  호출은 외부 운영 gate다. T06 검증은 deterministic fake만 사용했고 live Slack 호출은 0회다.
- `PE-SE2-T08`~`PE-SE2-T11`은 roadmap에서 계속 열려 있다. golden quality, version regression,
  실제 root→Slack production E2E와 human-modified suggestion 전체 E2E는 T07 완료 근거에 포함하지 않는다.
  실제 provider/Slack credential canary와 process-kill/DB-failover rehearsal도 별도 live gate다.

세부 transaction·replay 계약은
[automatic generation orchestration contract](./automatic-generation-orchestration-contract.md)와
[automatic generation deduplication contract](./automatic-generation-deduplication-contract.md),
[automatic generation impact contract](./automatic-generation-impact-contract.md),
[generation management contract](./automatic-generation-management-contract.md),
[generation suggestion contract](./automatic-generation-suggestion-contract.md),
[automatic generation Slack reply contract](./automatic-generation-slack-reply-contract.md)에 있다. 최신
T06 산출물은 [T06 production implementation report](./pe-se2-t06-production-implementation-report.md)에,
최신 T07 production composition과 독립 감사 수치는
[T07 production implementation report](./pe-se2-t07-production-implementation-report.md)에 분리했다.
