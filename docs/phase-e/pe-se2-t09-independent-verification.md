# PE-SE2-T09 독립 검증 보고서

- 검증일: 2026-08-31 (Asia/Seoul)
- 판정: **offline/contract/PostgreSQL authority PASS, operational activation/release BLOCKED**
- 검증 원칙: production 구현 보고서는 증거로 사용하지 않고 contract, schema, fixture, evaluator, CLI,
  migration, PostgreSQL transaction/trigger와 CI workflow를 독립적으로 공격했다.

## 결론

초기 구현은 happy-path symbol 존재 검사는 통과했지만 production authority로 사용할 수 없는 결함이 있었다.
특히 migration에 `generation_jobs` version-set pin column과 model의 check constraint/immutable guard가 없었고,
evaluator는 prompt 이외 dimension drift의 matrix 위장을 허용했으며, synthetic PASS가 exit `0`이면서 JUnit
failure를 만드는 artifact 혼선이 있었다. Retry는 activation 뒤 active baseline을 새로 물려받아 같은 root에서
old/new pin을 섞을 수 있었고 rollback reproduction digest와 target provenance도 충분히 봉인되지 않았다.

이 검증에서 해당 결함을 수정하고 독립 fixture 28개와 PostgreSQL authority/concurrency test 4개를 추가했다.
현재 synthetic fixture는 `CONTRACT_ONLY`로만 PASS하며 approval/activation artifact를 만들지 않는다. 실제
operational golden, provider/Slack credential과 protected human approval은 없으므로 `BASELINE_CANDIDATE`, live
activation 또는 release certification을 생성하거나 주장하지 않았다.

## 수정한 결함

| 영역 | 초기 결함 | 수정과 독립 증거 |
|---|---|---|
| migration/schema | `0027`이 model의 `generation_jobs.version_set_id/version_set_digest`, check constraints와 immutable guards를 설치하지 않음 | column/FK/check, seven authority tables, UPDATE/DELETE guards, runtime monotonic transition guard와 approval/activation insert authority guard를 migration에 추가; base→head와 `0026↔0027` round-trip PASS |
| pin identity | `latest`만 단일 값으로 일부 거부하고 `bge-m3:latest`, `current`, `stable`, wildcard/range를 허용 | schema와 semantic validator가 mutable alias suffix, whitespace, wildcard/range를 exit `2`로 거부 |
| drift matrix | schema version 일부와 embedding dimension만 실제 identity diff와 대조 | 14 dimension 전체 field map, exact `changedDimensions`, UNCHANGED 위장, version/digest pair, semantic threshold/policy pair를 대조 |
| migration receipt | migration plan dimension/from/to와 frozen population을 대조하지 않음 | schema migration에 source/membership digest를 요구하고 plan reference, dimension, version, population, duplicate/stray plan을 검증; embedding re-index receipt도 plan/dimension/population에 결속 |
| artifact semantics | synthetic PASS의 live check가 false여서 exit `0` JUnit에 failure가 존재; cached rejected report validation도 exit `0` | synthetic은 live gate 비적용 check PASS + `CONTRACT_ONLY`; REJECTED report는 failed check를 요구하고 cached validation은 exit `1` |
| CLI safety | output path가 snapshot 선언과 달라도 허용, symlink/cached/collision/partial output 가능, cached report SHA 미검증 | declared path exact match, input size/symlink, output parent/extension/collision/cache preflight, rollback-on-write-fault, optional expected report SHA를 적용; 0/1/2 fixture 검증 |
| in-flight/retry | active baseline 변경 뒤 기존 job을 거부하고 retry는 새 active pin을 캡처해 root 내부 mixed pin 가능 | in-flight job은 sealed historical set으로 계속 검증하고 retry는 predecessor pin을 상속; PostgreSQL trigger가 pin 재작성과 mixed retry insert/update를 거부 |
| activation/audit | audit event의 expected/committed version이 둘 다 commit 이후 값이고 digest가 principal/key/version을 봉인하지 않음 | event에 실제 expected→committed version을 기록하고 receipt digest에 principal, idempotency key와 양 ledger version을 포함 |
| rollback | self target은 DB flush 때만 실패하고 reproduction status/content/digest/cohort/population/migration proof가 약함 | self/never-activated target 선거부, canonical reproduction digest, target identity/manifest/report/cohort/population과 restored 100/0/0 및 compatibility/migration PASS를 모두 확인 |
| atomicity/concurrency | 구현 주장 외 독립 동시성/fault 증거 없음 | 두 PostgreSQL transaction 동시 activation에서 정확히 1 winner/1 `STALE_BASELINE`; activation과 rollback의 event 직전 fault가 runtime/receipt를 함께 rollback함을 검증 |

## 공격 범위와 결과

| 공격 | 결과 |
|---|---|
| old/new cohort/source/membership/result population mismatch | evaluator exit `1`; identity report cross-link mismatch는 exit `2` |
| pin omission, wildcard, range, latest/current/stable/default/head/main/master alias | strict schema 또는 semantic pin validation exit `2` |
| prompt content/version mismatch | version/digest pair mismatch로 exit `1`; stale identity digest 자체는 exit `2` |
| provider/model/revision, embedding provider/model/revision/dimension, semantic threshold/policy, exact duplicate, generation schema, quality schema, ontology, evaluator/policy, placement drift | 14 dimension parameterized fixture가 stale `UNCHANGED`를 모두 거부 |
| 100/0/0 threshold weakening | threshold artifact 변조는 schema error exit `2`; old/new metric defect는 exit `1` |
| synthetic/unreviewed activation | service와 PostgreSQL approval insert guard가 거부; synthetic CLI PASS도 `approvalEligible=false`, `CONTRACT_ONLY` |
| self-approval, duplicate reviewer, requester approval | service에서 `TWO_PERSON_APPROVAL_INVALID`; DB check가 principal/role separation을 이중 방어 |
| forged manifest/digest | snapshot/report expected SHA, identity/request/matrix canonical digest, protected manifest binding과 rollback reproduction digest mismatch를 거부 |
| baseline replay/tamper/deletion | exact request replay는 기존 receipt를 반환; conflicting replay를 거부; authority row UPDATE/DELETE와 runtime DELETE를 PostgreSQL trigger가 거부 |
| concurrent/stale/partial activation | singleton row lock + ledger CAS로 one-winner; stale loser 거부; injected post-flush fault에서 runtime/activation/audit가 0건으로 rollback |
| rollback-to-self/never-activated/failed reproduction/partial rollback | 모두 선거부; valid prior activation만 100% atomic restore; post-flush fault에서 runtime/rollback/audit가 함께 rollback |
| in-flight job/retry mixed pins, runtime active/cache drift | historical pin은 계속 유효하고 retry가 predecessor와 동일 pin을 상속; DB pin은 설정 뒤 immutable |
| failed report artifact confusion | successful disposition에 failed check 또는 rejected disposition에 no-failure check가 있으면 report validation error; valid rejected cached report는 exit `1` |
| CLI exit `0/1/2` | synthetic valid=`0`, quality/compatibility rejection=`1`, schema/hash/path/cache/safety error=`2` |
| network tripwire | evaluator 실행 중 socket connection을 예외화해도 PASS; provider/Slack/live DB 호출 없음 |
| PII/raw text leak | additional raw prompt/source field를 schema가 거부하고 stderr에는 raw value가 노출되지 않음; persisted authority는 bounded ID/digest metadata만 사용 |

PostgreSQL trigger는 일반 application DML의 UPDATE/DELETE를 막는다. Database owner/superuser의 trigger disable,
TRUNCATE, backup 삭제까지 WORM으로 만드는 것은 application migration이 아니라 운영 권한·backup/retention control
범위이므로 아래 live gate에 남긴다.

## 실행 증거

```text
node scripts/python.mjs -m pytest -q \
  scripts/tests/test_phase_e_automatic_generation_version_regression.py \
  scripts/tests/red_phase_e_automatic_generation_version_regression.py
PASS — 32 tests

pnpm --filter @tc/contracts exec vitest run \
  src/phase-e-automatic-generation-version-regression-contracts.test.ts
PASS — 9 tests

pnpm test:schema
PASS — 10 strict Draft 2020-12 schemas, versioned contracts, 12 Slack fixtures

TC_TEST_DATABASE_URL=<disposable tc_t09_test PostgreSQL 16 + pgvector> \
  node scripts/python.mjs -m pytest -q \
  backend/tests/repository/test_generation_version_regression_postgresql.py -m postgres
PASS — 4 tests

TC_TEST_DATABASE_URL=<same disposable database> pnpm test:migration
PASS — 61 PostgreSQL repository/migration tests

pnpm check
PASS — lint, TypeScript/Python typecheck, unit, schema, ADR, OpenAPI
```

`test_empty_database_can_be_rebuilt_to_head`로 base→head를 검증했고 별도로 `20260831_0027`을
`20260828_0026`까지 downgrade한 뒤 head로 upgrade하여 T09 column/trigger 재설치를 확인했다.

## 남은 live gate

다음 증거가 모두 제공되기 전 activation/release는 계속 BLOCKED다.

1. 같은 승인 cohort와 동일 source/membership slot에서 생성된 old/new **실제 operational golden**과 각 T08
   100/0/0 PASS artifact;
2. exact old/new identity와 동일 population을 사용한 credentialed provider call 양쪽 관찰 및 protected live
   attestation;
3. 실제 QUALITY_OWNER/RELEASE_OWNER 두 principal의 requester 분리 승인과 protected manifest digest;
4. 실제 Slack App credential/channel에서의 terminal notification 연결 확인. Offline evaluator에는 이 credential을
   전달하지 않는다;
5. provider model/embedding revision의 재현 가능 보존, 400일 artifact retention, legal hold, backup/WORM과
   database-owner 권한 통제의 운영 증거;
6. protected self-hosted runner/environment에서 operational workflow dry-run 후 승인된 activation command의 별도
   change-control 검토. 현재 workflow는 evidence를 평가·보존할 뿐 activation을 실행하지 않는다.

이 보고서는 T10/T11 roadmap 상태를 변경하지 않았다.
