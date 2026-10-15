# PF-SF1-T06 동시 부하 프로파일 보고서

- 최초 준비일: 2026-09-01
- 실제 로컬 실행 갱신일: 2026-09-04 (wave 5)
- 도구: [`scripts/phase-f/load_profile.py`](../../scripts/phase-f/load_profile.py)
- 프로파일: `PF-SF1-T06-CONCURRENT-100K-V1`
- 판정: **LOCAL LOOPBACK DISPOSABLE PASS / OPERATOR·PRODUCTION-LIKE·EDGE·STAGING GATES NOT RUN**

Wave 5에서 고유한 빈 PostgreSQL 16 + pgvector loopback disposable DB를 exact comment로
attest한 뒤 preview를 먼저 확인하고, 100,000건 fixture와 concurrency 16, 120초 mixed
profile을 실제 실행했다. 첫 실행은 Docker PostgreSQL의 64 MiB shared memory와 harness pool
용량 결함을 드러내며 fail-closed했고 그 evidence를 보존했다. 두 결함과 빈 DB preflight
결함을 회귀 테스트와 함께 수정한 뒤 새 DB/새 evidence로 재실행해 로컬 gate가 PASS했다.

이는 [wave 5 검증](./sprint-f1-wave5-verification.md)에 한정된 로컬 disposable 결과다. 실제
Slack/provider 호출은 없었고 operator/reviewer 승인, production-like multi-process/host,
edge/TLS/WAN, staging, cold-cache 및 soak gate는 수행하지 않았다. 따라서 `PF-SF1-T06`과
Phase F 출시 gate는 계속 열린 상태다.

## 범위와 실행 모델

별도 도구는 기존 [`benchmark_cases.py`](../../scripts/benchmark_cases.py)의 실제 ledger
migration과 고정 100,000건 synthetic fixture를 재사용한다. Fixture는 20 category, 1,000
work item, 20 tag, 100,000 case/version/step과 결정론적 label mapping만 만들며 실제 사람,
Slack 대화, 고객 데이터 또는 credential을 포함하지 않는다. Profile work도 실행당 최대
100,000 unit으로 제한하며 그 위에서 제한된 시간 동안
다음 네 surface를 하나의 동시 profile로 반복한다.

| Surface | 실제로 통과하는 경계 | 의도적으로 제외한 경계 |
|---|---|---|
| `api` | in-process ASGI `GET /api/test-cases?limit=50`, 실제 PostgreSQL query | Nginx, TLS, WAN |
| `slackIngress` | synthetic HMAC body 검증, normalize, allowlist, event 및 `SLACK_EVENT_PROCESS` atomic enqueue | Slack network/API와 실제 workspace |
| `workerPoll` | 실제 `SKIP LOCKED` claim, generic lease heartbeat, Slack projection 또는 stub 처리, success transition | 별도 worker process/container |
| `generationScheduling` | `AUTOMATIC_GENERATION_EXECUTE` durable job과 idempotency key enqueue, worker claim/lease | domain generation materialization 및 유료/live model 호출 |

Generation execution은 transport가 없는 `NoOutboundGenerationStub`만 handler에 등록한다.
`TC_AUTOMATIC_GENERATION_ENABLED`, `TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED`,
`TC_SLACK_GENERATION_NOTIFICATIONS_ENABLED`, `TC_OBJECT_STORAGE_ENABLED` 중 하나라도 true면
database URL을 읽기 전에 실행을 거부한다. 실행용 `Settings`도 네 기능을 명시적으로
비활성화하며 evidence의 `providerOutboundCallCount`는 구조상 `0`이어야 gate를 통과한다.

## 안전 경계와 사전 조건

1. 기본 동작은 preview다. Preview는 database URL 환경변수를 읽지 않고 subprocess,
   migration, HTTP request, 파일 쓰기를 하지 않으며 `workUnitsExecuted: 0`을 출력한다.
2. 실행 대상 class는 정확히 `disposable`, target은
   `tc-phase-f-<purpose>-disposable`, DB는 test/rehearsal token을 가진
   `tc_phase_f_<purpose>_disposable`이어야 한다. `prod`, `production`, `live`, `primary`,
   `main` token을 거부한다.
3. URL은 `TC_PHASE_F_*` 이름의 환경변수로만 받고 PostgreSQL loopback host 및 선언한 exact
   DB name으로 해석되어야 한다. URL이나 credential은 argv/evidence에 기록하지 않는다.
4. PostgreSQL 16 이상, exact database comment
   `tc-phase-f-rehearsal:disposable:<target-name>`, public table 0개를 확인한 뒤에만 migration과
   fixture를 수행한다. 기존 Phase F [operator prerequisite와 attestation 절차](./sprint-f1-recovery-performance-readiness.md#operator-prerequisites)를
   그대로 사용한다.
5. 실행에는 preview가 출력한 exact confirmation이 필요하다. 100,000 case 수는 CLI로
   줄이거나 바꿀 수 없다. 동시성은 1~64, 시간은 1~1,800초, queue sample interval은
   0.1~5초로 제한하며 profile work unit도 최대 100,000건에서 멈춘다.
6. Evidence는 `docs/phase-f/evidence/*.json` 아래 새 파일에만 기록하며 기존 파일을
   덮어쓰지 않는다. 오류에는 exception class 또는 bounded status code만 남긴다.

사전 조건 하나라도 불명확하거나 DB에 public table이 하나라도 있으면 멈춘다. 이 도구를
현재 개발 DB, 공유 staging DB, production clone 또는 remote host에 사용해서는 안 된다.

## 정확한 preview와 실행 명령

먼저 환경변수 없이 preview만 확인한다.

```bash
node scripts/python.mjs scripts/phase-f/load_profile.py \
  --target-name tc-phase-f-load-disposable \
  --target-class disposable \
  --database-url-env TC_PHASE_F_LOAD_DATABASE_URL \
  --database-name tc_phase_f_load_test_disposable \
  --concurrency 16 --duration-seconds 300 --queue-sample-interval 0.5 \
  --output docs/phase-f/evidence/pf-sf1-t06-concurrent-100k.json
```

DBA가 별도로 만든 빈 loopback DB의 exact name/comment를 기존 attestation 절차로 확인하고,
승인된 secret manager에서 URL을 `TC_PHASE_F_LOAD_DATABASE_URL`에 주입한 뒤에만 실행한다.

```bash
node scripts/python.mjs scripts/phase-f/load_profile.py \
  --target-name tc-phase-f-load-disposable \
  --target-class disposable \
  --database-url-env TC_PHASE_F_LOAD_DATABASE_URL \
  --database-name tc_phase_f_load_test_disposable \
  --concurrency 16 --duration-seconds 300 --queue-sample-interval 0.5 \
  --output docs/phase-f/evidence/pf-sf1-t06-concurrent-100k.json \
  --execute \
  --confirm 'RUN PF-SF1-T06 LOAD-PROFILE ON tc-phase-f-load-disposable'
```

실제 실행은 실패하더라도 evidence를 쓴 뒤 exit 1을 반환하고, 안전 경계 위반은 work를
시작하기 전에 exit 2를 반환한다. Wave 5는 같은 구조의 고유 target/DB/output을 사용했고
preview의 exact confirmation을 그대로 실행에 전달했다.

## Wave 5 실제 로컬 결과

최종 실행은 `tc-phase-f-t06-wave5-20260904-baa12856-r2-disposable` target과
`tc_phase_f_t06_wave5_20260904_baa12856_r2_test_disposable` DB를 사용했다. 실행 전 preflight는
PostgreSQL `160012`, exact comment 일치, public table `0`, Alembic revision absent를 확인했다.
고정 fixture는 case/version/step 각각 100,000건과 active case 95,000건을 만들었다.

| 측정 | Wave 5 최종 결과 | 판정 |
|---|---:|---:|
| warm API p95 | `998.554 ms` | PASS (`<= 1,000`) |
| warm Slack ingress p95 | `112.645 ms` | PASS (`<= 3,000`) |
| warm worker poll p95 | `178.893 ms` | PASS (`<= 2,000`) |
| warm generation scheduling p95 | `55.309 ms` | PASS (`<= 1,000`) |
| warm overall throughput | `40.462 success/s` | PASS (`>= 10`) |
| combined operations / errors | `4,832 / 0` | PASS |
| ready queue lag p95 | `488.251 ms` | PASS (`<= 300,000`) |
| running lease lag p99 | `145.389 ms` | PASS (`<= 60,000`) |
| provider outbound calls | `0` | PASS |

최종 immutable evidence는
[`pf-sf1-t06-wave5-20260904-baa12856-r2.json`](./evidence/pf-sf1-t06-wave5-20260904-baa12856-r2.json)이며
SHA-256은 `7a4acb6a8395082ef04ed482e7e7faacc2c1b68e6358caf8576f100a507696f0`이다. 선행 FAIL evidence
[`pf-sf1-t06-wave5-20260904-baa12856.json`](./evidence/pf-sf1-t06-wave5-20260904-baa12856.json)
(`b3b3a6b738c145e2cce97500e8657a652d65b585cd7b562cbd2c33e4c9573e16`)도 덮어쓰지 않고
원인과 수정 이력을 보존한다.

## 수집 지표와 pass 조건

첫 20%(최대 10초)는 `firstTouch`, 나머지는 `warm`, 전 구간은 `combined`로 분리한다. 각
surface와 overall에 nearest-rank p50/p95/p99, error count/rate, 성공 throughput을 기록한다.
동시에 ready queue의 가장 오래된 `run_after` age와 RUNNING job의 가장 오래된
`heartbeat_at`/`locked_at` age를 sampling해 queue/lease lag p50/p95/p99/max를 기록한다.
Sampler 오류, queue 또는 lease 관찰값 누락, sample 0건, 두 lag summary의 sample 수 불일치도
fail-closed다. `p95Ms`/`p99Ms`의 빈 summary 기본값 0은 유효한 측정으로 취급하지 않는다.

| Gate | Pass 조건 | 근거/해석 |
|---|---:|---|
| API warm p95 | `<= 1,000 ms` | 기존 [Sprint 2 100k benchmark](../sprint-2/sprint-2-report.md)의 first-page 목표 |
| Slack ingress warm p95 | `<= 3,000 ms` | [Slack ingress 운영 계약](../../deploy/slack/README.md)의 3초 ack 요구 |
| Worker poll warm p95 | `<= 2,000 ms` | [`--poll-interval` 기본값](../../apps/worker/src/tc_worker/main.py) 2초에 맞춘 harness 상한 |
| Generation scheduling warm p95 | `<= 1,000 ms` | 초기 local durable-scheduling harness 상한; production SLO가 아님 |
| Combined error rate | `0` | synthetic disposable acceptance policy |
| Warm throughput | `>= 10 success/s` | deadlock/정체 탐지용 초기 floor; capacity 인증값이 아님 |
| Ready queue lag p95 | `<= 300,000 ms` | [outbox/generation alert](../../deploy/observability/prometheus-rules.yml)의 5분 경계 |
| Running lease lag p99 | `<= 60,000 ms` | [generation lease](../../apps/worker/src/tc_worker/main.py) 60초 경계 |
| Fixture/provider/surface | exact 100,000 / outbound 0 / warm·combined 각각 모든 surface 1건 이상 | profile 정합성과 no-provider 보장 |

기존 1초/3초/5분/60초 계약을 넘은 값은 자동 실패한다. Generation scheduling 1초와
throughput 10/s는 첫 실제 disposable 측정 전에 합의할 초기 acceptance policy이며, hardware
capacity나 production SLO로 재해석하면 안 된다. 실제 gate 승인에는 실행 환경 사양과 이
임계치에 대한 operator/reviewer 동의도 함께 보관해야 한다.

두 phase 모두 정확한 required surface 집합을 가져야 하고, surface별 `count = successes + errors`
및 overall 합계가 surface 합계와 일치해야 한다. Combined의 count/success/error는 대응하는 warm
값보다 작을 수 없다. 이 구조 검사를 통과하지 않은 surface는 0 ms latency로 gate를 우회할 수 없다.

## Resource, first-touch/warm 한계

Evidence는 harness process의 user/system CPU delta, raw max RSS, fixture 전/후/부하 후 DB
byte size를 남긴다. Wave 5 최종 실행은 process user/system CPU `94.422/43.304초`, max RSS
`139,853,824 bytes`, DB size `13,351,959 → 187,964,439 → 196,271,127 bytes`를 기록했다.
macOS `ru_maxrss`는 byte, Linux는 KiB이므로 raw value와 platform 단위를 함께 해석해야 한다.
DB process/container CPU·memory·I/O, host load, network bandwidth는 수집하지 않는다.

`firstTouch`는 같은 process와 DB를 연 직후의 첫 구간일 뿐이다. PostgreSQL restart,
privileged cache eviction, container restart 또는 OS cache eviction을 하지 않으므로 진짜 cold-cache
측정이 아니다. 이런 destructive/privileged 동작은 이 harness에 추가하지 않았고 별도 승인된
환경 실험으로 남는다. 단일 5분 실행도 soak, multi-host API/worker, edge latency를 증명하지
않는다.

## 중단, evidence와 안전한 정리

- 예상하지 않은 provider flag, 대상/attestation/table-count 불일치, sampler 오류, nonzero
  request 오류 또는 threshold 실패가 있으면 PASS를 선언하지 않는다.
- 실행 도중 중단되면 먼저 harness client와 worker thread가 종료됐는지 확인한다. 재실행은
  같은 DB를 재사용하지 말고 새 exact disposable DB와 새 evidence filename으로 시작한다.
- 자동 cleanup은 없다. 별도 관리자 연결에서 exact DB name과 database comment를 다시
  확인하고 evidence를 보관한 다음, 승인된 DBA 절차로 그 단일 disposable DB만 삭제한다.
  wildcard database 삭제, schema-wide cleanup, 재귀 filesystem 삭제 같은 broad target
  명령은 이 문서와 도구가 제공하지 않는다.

실제 인계 bundle에는 evidence JSON, host/DB/container resource snapshot, 실행자와 검토자,
임계치 승인, 시작/종료 시각, 실패 시 incident/rollback 기록을 포함한다. Wave 5는 local
disposable PASS와 cleanup proof를 추가했지만 operator/reviewer 및 production-like/staging
bundle이 없으므로 현재 `PF-SF1-T06`은 완료가 아니다.

## 로컬 검증과 남은 gap

Focused test는 target/remote rejection, provider-disable 선행 검사, 0-work preview,
threshold failure, nearest-rank metric 결정론, fixed 100,000 profile에 더해 warm generation
0건, warm/combined surface 누락·0건, operation 합계 불일치, queue/lease 관찰 누락·불일치를
결정론적으로 검증한다.

```bash
node scripts/python.mjs -m pytest scripts/tests/test_phase_f_load_profile.py -q
node scripts/python.mjs -m ruff check scripts/phase-f/load_profile.py \
  scripts/tests/test_phase_f_load_profile.py
node scripts/python.mjs -m mypy backend/src apps/api/src apps/worker/src \
  scripts/phase-f/load_profile.py scripts/phase-f/rehearse.py
```

Wave 5에서 focused 50개, 관련 PostgreSQL surface 35개, 전체 repository PostgreSQL 78개와
`pnpm check`를 통과했다. 남은 필수 증거는 별도 DB/container resource 관찰을 포함한
multi-process/edge 조건의 production-like 재검증, staging 실행, cold-cache/soak와
operator/reviewer 인계다. 실제 provider 품질과 domain generation materialization은 이
no-outbound performance profile의 범위가 아니다.
