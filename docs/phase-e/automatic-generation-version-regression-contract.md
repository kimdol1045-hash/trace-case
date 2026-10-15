# PE-SE2-T09 자동 생성 version regression과 baseline 전환 계약

| 항목 | 값 |
|---|---|
| 계약 버전 | `automatic-generation-version-regression/1.0.0` |
| 작성 기준일 | 2026-08-31 (Asia/Seoul) |
| 상태 | **authoritative contract + independent fixture/PostgreSQL verification GREEN, operational live gate BLOCKED** |
| normative schema | `packages/contracts/generation/version-regression/1.0.0/schema.json` |
| 범위 | prompt/model/embedding/schema/evaluator/policy/placement pin 변경의 old/new 회귀, baseline 원장, 승인·활성화·rollback 계약 |
| 비범위 | provider credential 발급, operational golden 승인, T10~T11 E2E |

## 1. 목적과 선행 권위

T09는 T01~T08을 대체하지 않는다. T01/T02의 canonical source/root, T03의 semantic/config
assessment, T04의 단방향 human release, T05의 immutable suggestion, T06의 terminal notification,
T07의 provider permit/observation/retry/materialization 원장, T08의 operational golden 100/0/0
report가 그대로 선행 권위다.

T09가 새로 답하는 질문은 하나다.

```text
행동에 영향을 주는 version pin을 바꿀 때,
동일한 승인 cohort와 봉인된 population에서 old/new를 비교하고,
누가 어떤 baseline을 승인·활성화·대체·rollback했는지 재현할 수 있는가?
```

T09는 threshold를 튜닝하는 task가 아니다. old와 new 모두 다음 T08 기준을 각각 만족해야 한다.

```text
evidence coverage = 100%
retired knowledge misuse = 0
duplicate = 0
unresolved/provenance/invalid/ordering failure = 0
```

평균, 반올림, category별 상쇄, 허용 오차, warning, 승인자 override, best-of retry, 표본 제외로
`100/0/0`을 완화할 수 없다.

## 2. baseline identity 확정

Baseline identity는 특정 한 필드나 단일 model name이 아니다. 다음 **모든 behavior-affecting exact pin의
canonical tuple**이며, RFC 8785 canonical JSON UTF-8 bytes의 lower-case SHA-256을
`baselineIdentityDigest`로 봉인한다.

| 영역 | baseline identity 필드 |
|---|---|
| generation policy | `generationPolicyVersion`, `generationPolicyDigest` |
| prompt | `promptVersion`, `promptDigest` |
| generation provider | `providerId`, `modelId`, provider-reported exact `modelRevision` |
| embedding/semantic duplicate | `embeddingProviderId`, `embeddingModelId`, `embeddingRevision`, `embeddingDimensions`, `semanticDuplicateAlgorithmVersion`, exact inclusive `semanticDuplicateThreshold`, `semanticDuplicatePolicyDigest` |
| exact duplicate | `exactDuplicateAlgorithmVersion` |
| generation schemas | exact input/output/suggestion schema versions |
| quality interpretation | `qualityGateSchemaVersion` |
| ontology | `ontologySchemaVersion`, canonical `ontologySchemaDigest` |
| regression | `regressionEvaluatorVersion`, `regressionPolicyVersion`, `regressionPolicyDigest` |
| placement | `placementPolicyVersion`, `placementPolicyDigest` |

따라서 promptVersion만 같아도 prompt bytes가 다르면 다른 identity이고, provider/model이 같아도
model revision이 다르면 다른 identity다. Embedding dimension, threshold 또는 policy digest만 바뀌어도
다른 identity다. Schema/evaluator/placement 변경도 결과 해석이나 materialization 결정을 바꿀 수 있으므로
identity에 포함한다.

Approval ID, approver, activation time, cohort ID, live run ID와 환경은 baseline identity가 아니다.
그 값들은 identity를 승인하고 사용한 **governance authority**이며 별도 ledger/receipt에 봉인한다.
Credential, token, endpoint query, raw prompt/source/result/provider body도 identity artifact에 넣지 않는다.

`latest`, range, alias, implicit default, missing, unknown, unconfigured, unpinned은 exact pin이 아니다.
Schema-valid 문자열로 위장한 reserved value도 evaluator semantic validation에서 exit `2`로 거부한다.

## 3. 비교 population과 operational golden

Production approval 비교는 old/new를 같은 `APPROVED` cohort에서 실행한다. 다음은 두 report가
byte-for-byte 공유해야 한다.

- `cohortId`, `cohortDigest`, cohort approval ID/digest;
- `sourcePopulationDigest`;
- `populationMembershipDigest`와 동일 root/slot 수;
- 각 slot의 `generationRootId`와 `sourceSnapshotFingerprint`.

하나의 `ComparisonPopulation.resultSlots[]`가 root별 old/new result snapshot ID와 digest를 함께 가진다.
즉 source와 평가 membership은 동일하고, old/new result bytes는 각각 immutable하게 봉인되며 서로 달라도
된다. 새 결과가 마음에 들지 않는다는 이유로 root를 교체하거나 결과를 다시 뽑아 slot을 바꾸는 것은
금지한다. Old/new result population digest는 각각 독립 seal이고, 비교 시작 뒤 write/reorder/filter할 수 없다.

Old와 new는 각각 T08 `OPERATIONAL_GOLDEN`, `releaseCertified=true`, gate PASS report여야 한다.
한 쪽의 실패를 다른 쪽의 개선으로 상쇄하지 않는다. Cohort, source population, slot membership 또는
quality report artifact digest가 다르면 비교 자체가 fail-closed다.

## 4. change request와 idempotency

Version 변경은 immutable change request로 시작한다.

- server-issued `changeRequestId`;
- actor-scoped `idempotencyKey`와 canonical `requestDigest`;
- authoritative requester principal과 request time;
- raw reason 대신 bounded normalized reason digest;
- `expectedActiveBaselineId`, `expectedLedgerVersion`;
- old/proposed baseline identity digest;
- fixed-order changed dimension set;
- compatibility matrix ID.

같은 actor/key/digest replay는 원래 receipt를 반환하고 원장 version을 추가하지 않는다. 같은 actor/key에
다른 digest는 `IDEMPOTENCY_CONFLICT`다. Expected active baseline 또는 ledger version이 달라지면
`STALE_BASELINE`이며 최신 후보로 자동 rebase하지 않는다.

## 5. compatibility matrix

Matrix는 14개 dimension을 정확히 한 번 포함한다.

1. generation policy
2. prompt
3. generation provider/model/revision
4. embedding provider/model/revision
5. embedding dimension
6. semantic threshold/policy
7. exact duplicate algorithm
8. generation input schema
9. generation output schema
10. suggestion schema
11. quality-gate schema
12. ontology schema
13. regression evaluator/policy
14. placement policy

각 entry는 `UNCHANGED|COMPATIBLE|MIGRATION_REQUIRED|INCOMPATIBLE`와 두 방향을 기록한다.

| 방향 | 의미 |
|---|---|
| `oldReaderNewArtifact` | downgrade/현재 production reader가 새 artifact를 안전하게 읽는가 |
| `newReaderOldArtifact` | forward/new reader가 기존 baseline·rollback artifact를 읽는가 |

두 방향 중 하나라도 `REJECTED`, classification이 `INCOMPATIBLE`, dimension 누락/중복, unknown 상태,
필요 migration receipt 부재이면 activation은 금지한다. “새 버전만 정상”은 rollback 가능성을 증명하지
않으므로 승인 근거가 아니다.

## 6. schema migration

Generation/ontology/quality/evaluator artifact schema가 바뀌면 version string 비교만으로 충분하지 않다.
각 `MIGRATION_REQUIRED` entry는 다음을 가진다.

- from/to exact version과 affected dimension;
- `OFFLINE_DETERMINISTIC` migration artifact digest;
- `lossless=true`, `reversible=true`;
- migrated fixture/operational population verification receipt;
- rollback artifact digest.

Live row를 읽어 빠진 필드를 추측하거나 unknown field를 버리는 migration, one-way/lossy transform,
silent default backfill은 v1에서 허용하지 않는다. Migration 전후 source/result population membership은
같아야 하며 T08 100/0/0을 다시 평가한다.

## 7. semantic vector dimension drift

Embedding provider/model/revision/dimension/threshold/digest는 한 묶음으로 검증한다. Dimension이 바뀌면
기존 vector와 새 vector를 같은 index/cluster에서 혼합할 수 없다.

Activation 전 `EmbeddingMigration`은 old/new dimension, exact source population digest, 전체 재-embedding
population digest, rollback vector population digest, receipt digest와 `mixedDimensionsObserved=false`를
증명해야 한다. 일부 row만 새 dimension인 상태, pgvector column과 pin 불일치, missing revision,
threshold만 바꾸고 policy digest를 유지한 상태는 모두 fail-closed다.

Dimension이 같아도 provider/model/revision/threshold/policy digest가 바뀌면 baseline identity는 달라지고
old/new golden 비교를 생략할 수 없다.

## 8. regression evaluator report

Evaluator는 network, provider, Slack, live DB 없이 이미 봉인된 old/new report와 population만 읽는다.
다음을 다시 계산하고 strict JSON/JUnit/Markdown을 fresh-write한다.

- baseline identity digest와 change dimension;
- same cohort/source/membership/result-slot seals;
- old/new 각각의 exact 100/0/0;
- compatibility 14-dimension completeness와 양방향 판정;
- schema/embedding migration receipt;
- missing/unknown pin count 0;
- partial rollout 없음;
- production profile에서는 별도 live attestation link.

Exit code는 T08과 같다.

| exit | 의미 |
|---:|---|
| `0` | 선택 profile gate PASS. Synthetic candidate는 contract-only다. |
| `1` | schema-valid 비교지만 quality/compatibility/eligibility가 실패했다. |
| `2` | schema, pin, fingerprint, path, cached output, I/O 또는 safety 오류다. |

## 9. baseline candidate와 two-person approval

Production `BASELINE_CANDIDATE`가 되려면 다음이 모두 필요하다.

1. non-synthetic production profile;
2. same approved operational cohort old/new T08 PASS;
3. T09 regression PASS;
4. compatibility와 모든 migration PASS;
5. credentialed live attestation PASS;
6. current active baseline/ledger CAS가 그대로 유효함.

Approval은 `automatic-generation-baseline-approval/1.0.0`이며 정확히 두 명의 서로 다른 principal이
필요하다. 한 명은 `QUALITY_OWNER`, 다른 한 명은 `RELEASE_OWNER`다. Requester는 승인자가 될 수 없고,
한 principal이 두 role로 self-approve할 수 없다. 두 decision digest와 approval artifact digest는
immutable하다. 승인 뒤 identity/report/matrix/live attestation이 바뀌면 새 change request와 새 승인이
필요하다.

Synthetic fixture/report/ledger record는 항상 `CONTRACT_ONLY`, `approvalEligible=false`다. Schema는
synthetic ledger의 active baseline, approval, activation, rollback receipt를 거부한다. 운영자 수기 승인도
이 경계를 우회하지 못한다.

## 10. activation, supersession과 partial rollout 금지

V1 activation은 `ATOMIC_ALL_WORKSPACES`, `trafficPercent=100`, `partialRolloutAllowed=false`만 허용한다.
Canary percentage, workspace subset, old/new random routing, dual-write, silent fallback은 별도 versioned
계약 없이는 금지한다.

한 transaction에서 다음을 수행한다.

1. active baseline row와 ledger head를 잠근다.
2. change request의 expected active ID/version과 CAS한다.
3. candidate identity/report/matrix/approval/live attestation digest를 재검증한다.
4. 기존 active baseline을 `SUPERSEDED`로 append-only 기록한다.
5. 새 baseline을 유일한 `ACTIVE`로 만든다.
6. activation과 supersession event/receipt, audit를 같은 commit에 쓴다.

중간 fault는 모두 rollback된다. Active baseline이 둘이거나 0인 partial commit은 허용하지 않는다.
동시 activation은 하나만 이기며 loser는 stale이다. Unique conflict를 새 baseline version 할당으로 숨기지
않는다.

## 11. rollback target과 재현성

Rollback target은 임의 pin object가 아니라 이전에 실제 승인·활성화된 immutable baseline record다.
Receipt는 target baseline ID/identity digest, 최초 activation receipt ID, expected/committed ledger version,
failed baseline ID를 가리킨다.

Rollback도 100% atomic이며 부분 traffic을 허용하지 않는다. 복구 완료 전 다음을 재현한다.

- target identity digest와 모든 exact pin;
- target schema/ontology/embedding vector population;
- target operational cohort와 frozen source/result slot population;
- target T08 100/0/0;
- target reader가 보존 artifact를 읽는 forward/downgrade compatibility;
- reproduction report/artifact digest.

`reproductionStatus=VERIFIED`와 restored 100/0/0 없이는 rollback을 성공으로 기록하지 않는다. Target
artifact/credential/revision/vector가 없어 재현할 수 없으면 자동으로 “가장 가까운” version을 선택하지 않고
release를 차단한다. Rollback 뒤 재시작/replay는 같은 target identity와 receipt를 반환해야 한다.

## 12. live attestation은 별도 gate

Offline regression evaluator에는 provider credential을 전달하지 않는다. 별도 protected live gate가
old/new exact identity와 같은 population membership을 대상으로 실제 credentialed provider call을 각각
관찰하고 attestation artifact를 만든다.

필수 값은 gate/run ID, artifact digest, staging/production environment, credentialed=true, old/new call
observed=true, 두 baseline identity digest, population membership digest, 승인 principal/time이다.
`NOT_RUN|FAILED`, credential 없음, 한 쪽 call 미관찰, 다른 identity/population digest는 activation을 막는다.

Actual credential 존재는 quality를 대신하지 않고, offline 100/0/0도 actual provider call 증거를 대신하지
않는다. 두 gate는 독립적으로 모두 PASS해야 한다.

## 13. audit, retention과 privacy

Change request, candidate, compatibility/migration receipt, regression report, approval, activation,
supersession, rollback과 reproduction receipt는 append-only다. FK는 `RESTRICT`; online UPDATE/DELETE로
history를 바꾸지 않는다.

- event sequence가 원장 순서 권위이며 timestamp는 설명용이다;
- 모든 mutation은 principal, idempotency key, request/receipt digest, expected/committed ledger version,
  before/after active baseline을 남긴다;
- operational artifact는 최소 400일 immutable 보존한다;
- legal/audit hold가 TTL보다 우선한다;
- raw prompt/source/result/provider body, credential, endpoint, header, token, PII는 artifact에 넣지 않는다.

Retention 만료는 active/rollback target의 재현성을 먼저 깨뜨릴 수 없다. Target이 참조되는 동안 identity,
schema, vectors, reports, approval와 activation receipt를 삭제하지 않는다.

## 14. concurrency와 fail-closed summary

다음은 모두 승인/활성화 실패다.

- missing/unknown/range/implicit baseline pin;
- baseline identity digest 또는 artifact fingerprint mismatch;
- same approved cohort/source/membership이 아님;
- old/new 한 쪽이라도 100/0/0 실패;
- matrix dimension 누락/중복/unknown/incompatible;
- forward/downgrade rejection;
- schema migration 또는 embedding re-index receipt 부재;
- mixed vector dimension;
- duplicate/self/requester approval;
- stale expected active baseline/ledger version;
- partial rollout;
- missing/failed/mismatched live attestation;
- rollback target 또는 reproduction proof 부재.

Unknown을 unchanged로, missing migration을 compatible로, stale activation을 replay로, idempotency conflict를
새 request로 변환하지 않는다.

## 15. versioned schema와 deterministic fixture

Normative schema는 다음 세 artifact를 strict Draft 2020-12 `oneOf`로 제공한다.

- `RegressionEvaluationInput`;
- `RegressionEvaluationReport`;
- `BaselineLedgerSnapshot`(approval/activation/rollback receipt `$defs` 포함).

모든 object는 `additionalProperties=false`다. Repository fixture는 합성 contract regression 전용이다.

- `evals/fixtures/automatic-generation-version-regression/synthetic-comparison-input-v1.json`
- `evals/fixtures/automatic-generation-version-regression/synthetic-comparison-report-v1.json`
- `evals/fixtures/automatic-generation-version-regression/synthetic-baseline-ledger-v1.json`

Fixture는 동일 synthetic cohort에서 prompt v1→v2 identity 변경을 비교하지만 production approval이나
live attestation을 주장하지 않는다.

## 16. contract, evaluator와 PostgreSQL verification

Contract/schema gate:

```text
pnpm test:schema
pnpm --filter @tc/contracts exec vitest run \
  src/phase-e-automatic-generation-version-regression-contracts.test.ts
pnpm --filter @tc/contracts typecheck
```

Contract test는 schema compile/export, deterministic fixture, full baseline identity, immutable cohort/population,
100/0/0, compatibility 양방향, schema migration, semantic dimension drift, synthetic non-approval, partial rollout,
two-person approval와 rollback reproduction shape를 검증한다. Production evaluator와 CLI 독립 fixture는 다음으로
실행한다.

```text
node scripts/python.mjs -m pytest -q \
  scripts/tests/test_phase_e_automatic_generation_version_regression.py \
  scripts/tests/red_phase_e_automatic_generation_version_regression.py
```

Disposable PostgreSQL 16 + pgvector에서는 다음 focused suite가 migration columns/constraints/immutable guards,
two-person approval, concurrent CAS activation, transaction fault rollback, in-flight/retry pin inheritance와 sealed
rollback replay를 검증한다.

```text
TC_TEST_DATABASE_URL=<disposable-test-db> node scripts/python.mjs -m pytest -q \
  backend/tests/repository/test_generation_version_regression_postgresql.py -m postgres
```

## 17. production 구현과 금지선

Production 구현은 다음 seam을 제공한다.

1. strict parser, canonical identity/population/matrix fingerprint validator;
2. deterministic offline old/new regression evaluator와 report renderer;
3. append-only baseline/change/approval/activation/rollback ledger와 DB guards;
4. two-person approval service;
5. CAS 기반 atomic activation/supersession;
6. sealed rollback/reproduction service;
7. protected synthetic candidate + operational approval/release workflow.

Always-PASS evaluator, mutable “current config” row만 있는 구현, synthetic approval, warning-only 100/0/0,
`continue-on-error`, caller-controlled approved path/SHA, provider credential을 offline evaluator에 전달하는 흐름,
부분 rollout 또는 재현되지 않은 rollback은 production 구현으로 인정하지 않는다.

Operational golden/provider credential/live attestation이 없으면 protected workflow도 activation 또는 release
certification을 만들지 않는다. `docs/test-management-roadmap.md`의 T10/T11 상태는 이 계약의 범위가 아니다.
