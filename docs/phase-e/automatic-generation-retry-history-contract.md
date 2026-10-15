# PE-SE2-T07 자동 생성 실패 관리·조건부 재시도·시도 이력 계약

- 상태: **production 미구현 / focused RED**
- 계약 버전: `automatic-generation-retry-history/1.0.0`
- 작성 기준일: 2026-08-28
- 선행 권위: PE-SE2-T01~T06 계약, `generation_jobs`, generation context/input/result/apply/suggestion/notification 원장
- 구현 범위: migration, service, production worker, API, Web UI는 후속 구현에서 이 계약을 만족해야 한다. 이 문서는 그 구현을 포함하지 않는다.

## 1. 목적과 경계

T07은 T01이 만든 `trigger=AUTOMATIC`, `status=QUEUED`, `attempt_number=1` root 생성 작업을 production worker가 안전하게 실행하고, 실패를 분류해 자동 재시도 또는 최종 실패로 결정하며, 운영자가 추가 조건을 붙여 별도 재시도를 요청하고, 입력부터 Slack 알림까지 전체 시도 이력을 조회하는 권위를 정의한다.

다음은 T07의 범위다.

1. AUTOMATIC root와 그 retry lineage의 production execution.
2. provider adapter 내부 재시도와 generation job-level 재시도의 분리.
3. 한 root에 한 active job, lease/CAS, crash recovery, retry budget/exhaustion.
4. 성공한 자동 생성 결과의 apply 또는 suggestion materialization 예약.
5. 최종 실패와 T06 Slack failure intent의 원자적 연결.
6. 운영자 조건부 재시도의 immutable request, actor, idempotency, 안전성.
7. 실패 queue/list/detail/history/retry API와 한국어 Web workspace.
8. 보존, 관측, live gate.

다음은 범위가 아니다.

- 수동 생성 workspace의 동기 execute/retry 의미 변경.
- 공급자 선택, endpoint, credential, 비용 승인 UI.
- 이미 release된 testcase를 자동으로 다시 관리 상태로 전환.
- 기존 `job-api/1.0.0`의 의미를 뒤에서 바꾸는 일.
- 결과가 불명확한 외부 호출의 자동 재실행.
- terminal job 자체의 resume 또는 in-place mutation.

## 2. 기존 권위와 T07이 채우는 공백

| 선행 계약 | 유지되는 권위 | T07 공백 |
| --- | --- | --- |
| T01 orchestration | 결정적 idempotency로 AUTOMATIC root QUEUED 생성 | 실제 provider execution handler 없음 |
| T02 dedup | apply 시 생성/재사용 원자성 | 자동 성공 뒤 apply 예약 없음 |
| T03 impact | SKIP/REVIEW_REQUIRED/GENERATE 결정, source seal | retry 시 seal 재사용·stale 처리 없음 |
| T04 management | managed/released 경계 | release와 retry/materialization 충돌 처리 없음 |
| T05 suggestions | release target을 덮어쓰지 않는 proposal 원장 | 자동 성공 뒤 suggestion 예약 없음 |
| T06 Slack reply | apply/suggestion 성공 및 최종 실패 notification intent | retryable failure의 exhaustion 권위가 없음 |

현재 수동 API는 request transaction 안에서 주입된 runner로 실행하며 production runner가 의도적으로 없다. 현재 retry API는 새 QUEUED job만 추가하고 자동 실행하지 않는다. 현재 generic `background_jobs.attempt`는 delivery lease의 횟수이며 generation retry budget이 아니다. 현재 provider adapter는 내부 최대 5회 재시도를 수행하지만 개별 provider call의 append-only 이력은 없다. T07 구현은 이 공백만 채우고 선행 권위를 우회하지 않는다.

## 3. 용어와 세 개의 시도 번호

세 숫자는 서로 대체하거나 합산하지 않는다.

| 숫자 | 권위 | 의미 | 예시 상한 |
| --- | --- | --- | --- |
| `provider_attempt_number` | provider permit/observation | 하나의 generation job 안에서 adapter가 실행한 실제 외부 호출 | 정책 기본 3, hard max 5 |
| `generation_job.attempt_number` | `generation_jobs` root lineage | root 또는 새 retry job 한 행 | 자동 정책 root 포함 3 |
| `background_job.attempt` | generic background queue | 같은 실행 schedule의 delivery/lease/reclaim 횟수 | generation budget과 무관 |

예를 들어 generation job 2차가 worker delivery 3차에 claim되고 provider를 2회 호출한 뒤 성공할 수 있다. UI에는 이를 각각 “생성 시도 2차”, “작업 전달 3회”, “공급자 호출 2회”로 표시한다.

`root_job_id`는 lineage 전체의 root다. `retry_of_job_id`는 직전 job만 가리킨다. root는 `retry_of_job_id=NULL`, `attempt_number=1`, `retry_origin=ROOT`다. 이후 행은 직전 행을 가리키며 시도 번호가 정확히 1 증가한다. lineage를 건너뛰거나 분기하지 않는다.

## 4. 상태와 최종성

### 4.1 GenerationJob

기존 `QUEUED -> RUNNING -> SUCCEEDED|FAILED` 상태를 유지한다. terminal 행을 QUEUED/RUNNING으로 되돌리지 않는다. retry와 운영자 재시도는 항상 새 `GenerationJob`이다.

### 4.2 Execution schedule

T07은 job 상태와 별도로 mutable CAS projection인 `generation_execution_schedules`를 둔다.

```text
NOT_BEFORE --(clock)--> READY --(claim)--> LEASED
    ^                                  |
    |                     pre-permit crash/reclaim
    +----------------------------------+

LEASED --(observed terminal outcome)--> TERMINAL
LEASED --(permit exists, observation absent, lease lost)--> PROVIDER_OUTCOME_UNKNOWN
PROVIDER_OUTCOME_UNKNOWN --(operator review)--> TERMINAL/REVIEW_REQUIRED
```

`PROVIDER_OUTCOME_UNKNOWN`은 외부 호출 permit이 commit됐으나 observation이 commit되지 않은 상태다. 네트워크 timeout, process kill, DB 장애 때문에 공급자가 요청을 처리했는지 알 수 없으므로 자동 재호출하지 않는다. 이를 retryable transport failure로 추측하면 안 된다.

### 4.3 Failure finality

- `AUTOMATIC_RETRY_SCHEDULED`: retryable이며 같은 transaction에서 다음 job/schedule이 생성됐다.
- `TERMINAL_NON_RETRYABLE`: 비재시도 오류다.
- `TERMINAL_EXHAUSTED`: retryable이지만 자동 budget이 끝났고 exhaustion receipt가 있다.
- `TERMINAL_REVIEW_REQUIRED`: 호출 결과 불명확 또는 정책상 사람 판단이 필요하다.

최종성은 문자열이나 `attempt_number >= N`을 조회 시 추론한 값이 아니다. `generation_retry_exhaustions` receipt 또는 같은 transaction의 next schedule이 권위다.

## 5. 영속 원장과 database guards

후속 migration은 아래 production 테이블을 추가한다. 이름은 focused RED의 구현 seam이다.

### 5.1 `generation_execution_schedules`

job당 한 행인 mutable execution projection이다.

- PK `id`, FK `generation_job_id`, `root_job_id`, `background_job_id`.
- `state`, `not_before`, `lease_owner`, `lease_token`, `lease_expires_at`, `resource_version`.
- `provider_permit_count`, `worker_delivery_attempt_count`, `created_at`, `updated_at`.
- `generation_job_id` UNIQUE.
- AUTOMATIC lineage에 대해 READY/NOT_BEFORE/LEASED/PROVIDER_OUTCOME_UNKNOWN 중 **root당 하나만** 허용하는 partial unique `uq_generation_execution_active_root`.
- lease 필드는 state와 일치해야 한다는 CHECK. LEASED만 owner/token/expiry를 갖는다.
- `not_before`는 UTC timestamptz이며 claim 시 DB clock과 비교한다.

### 5.2 `generation_attempt_events`

입력, 실행, 결과, 실패, apply/suggestion, notification을 root 단위 sequence로 잇는 append-only timeline이다.

- PK `id`, FK `root_job_id`, `generation_job_id`.
- `(root_job_id, sequence)` UNIQUE, sequence는 1부터 빈틈없이 증가.
- `event_type`, optional `provider_attempt_number`, safe `error_code`, 관련 원장 ID, `occurred_at`.
- UPDATE/DELETE를 거부하는 `trg_generation_attempt_events_append_only`.
- source text, prompt, raw provider body, exception string, credential을 저장하지 않는다.

### 5.3 `generation_provider_call_permits`

실제 외부 호출 전에 commit되는 append-only permit이다.

- FK job/schedule, `provider_attempt_number`, `request_digest`, provider/model의 safe identifier, `permitted_at`.
- `(generation_job_id, provider_attempt_number)` UNIQUE `uq_generation_provider_permit_attempt`.
- provider attempt 번호는 1..5 CHECK.
- UPDATE/DELETE 거부 trigger.

permit은 API key, endpoint query, prompt, source body를 담지 않는다. `request_digest`는 secret을 제외한 canonical sealed input digest다.

### 5.4 `generation_provider_call_observations`

permit에 대한 성공/실패/ambiguous 관찰을 append-only로 기록한다.

- FK permit UNIQUE `uq_generation_provider_observation_permit`.
- outcome, deterministic error code, safe provider code, optional HTTP status, result digest, 시작/종료시각.
- raw response/request body 및 exception message 금지.
- UPDATE/DELETE 거부 trigger.

### 5.5 `generation_retry_requests`

운영자 조건부 재시도 요청의 immutable authority다.

- authenticated principal ID와 당시 display label snapshot.
- root, expected failed job, expected attempt, source version/context digest/input digest.
- normalized reason, normalized additional conditions, code-point count, UTF-8 byte count, digest.
- actor-scoped idempotency key와 request digest.
- `(actor_principal_id, idempotency_key)` UNIQUE `uq_generation_retry_request_actor_idempotency`.
- 하나의 request는 최대 하나의 retry job을 가리킨다.
- UPDATE/DELETE 거부 trigger.

### 5.6 `generation_retry_exhaustions`

최종 failure notification을 허용하는 append-only receipt다.

- root당 최대 하나 `uq_generation_retry_exhaustion_root`.
- terminal failed job, finality, retry budget snapshot, deterministic error code, exhausted_at.
- `TERMINAL_EXHAUSTED`이면 `retryable=true`; `TERMINAL_NON_RETRYABLE`이면 `retryable=false` CHECK.
- UPDATE/DELETE 거부 trigger.

### 5.7 `generation_materialization_schedules`

성공 결과의 apply 또는 suggestion scheduling projection이다.

- successful generation job당 하나 `uq_generation_materialization_job`.
- sealed `mode`는 `AUTOMATIC_APPLY|AUTOMATIC_SUGGESTIONS`; 실행 후 모드를 바꾸지 않는다.
- background job, not-before, lease/CAS, terminal materialization receipt FK.
- apply와 suggestion receipt를 동시에 가질 수 없다는 CHECK.

### 5.8 기존 원장 강화

기존 unique `(root_job_id, attempt_number)`, unique `retry_of_job_id`, root당 successful job 하나를 유지한다. 다음 deferred constraint trigger를 추가한다.

- root는 자기 자신을 가리키고 attempt 1이어야 한다.
- retry는 같은 root의 직전 attempt를 가리켜야 한다.
- job의 source/context seal은 lineage 정책과 일치해야 한다.
- AUTOMATIC root에 active execution schedule은 하나뿐이다.
- success job은 failure/exhaustion을 동시에 가질 수 없다.
- automatic retry가 예약된 failure는 next job과 schedule을 같은 commit에서 가져야 한다.

PostgreSQL constraint/trigger가 service 검사보다 마지막 권위다. SQLite 또는 메모리-only 검사로 production invariant를 대체하지 않는다.

## 6. Lock order와 transaction 경계

deadlock을 피하기 위해 모든 retry/execution/materialization 경로는 다음 순서를 지킨다.

1. `slack_work_items` 행.
2. root `generation_jobs` 행.
3. 현재 latest `generation_jobs` 행.
4. `generation_execution_schedules` 또는 `generation_materialization_schedules` 행.
5. `generation_retry_requests` / `generation_retry_exhaustions`.
6. T02 apply 또는 T05 suggestion target/receipt.
7. T06 notification intent.
8. generic `background_jobs` enqueue/update.

목록 조회는 lock을 잡지 않는다. claim은 `not_before <= clock_timestamp()`인 schedule의 generic background job을 `FOR UPDATE SKIP LOCKED`로 하나 선택한다. 실행 전 `lease_token + resource_version` CAS로 ownership을 확인한다. heartbeat와 terminal write도 동일 token을 요구한다. lease를 잃은 worker의 뒤늦은 결과는 commit할 수 없다.

provider HTTP 호출을 DB transaction 안에서 하지 않는다. permit commit, 외부 호출, observation/result commit의 세 단계다. crash window에 대한 처리:

| crash 지점 | 복구 |
| --- | --- |
| claim 전 | 다른 worker가 claim |
| claim 후, permit 전 | lease 만료 뒤 같은 schedule reclaim; provider call 없음 |
| permit commit 후, HTTP 전/중/후, observation 전 | `PROVIDER_OUTCOME_UNKNOWN`; 자동 재호출 금지 |
| observation commit 후, job terminal commit 전 | observation을 idempotently fold해 terminal 처리; 새 provider call 금지 |
| job failure commit 후, next retry enqueue 전 | 허용하지 않음. failure + next schedule/exhaustion + event + background enqueue를 한 transaction에 commit |
| job success commit 후, materialization enqueue 전 | 허용하지 않음. success + materialization schedule + event + background enqueue를 한 transaction에 commit |

generic worker handler는 예상된 generation 실패를 domain transaction으로 기록한 뒤 generic background delivery에는 성공을 반환한다. 예상 failure를 generic `JobRepository.fail()`에 던져 delivery retry를 generation retry로 쓰면 안 된다. infrastructure DB outage처럼 domain transaction 자체가 commit되지 않은 경우만 generic delivery가 retry된다.

## 7. 재시도 budget, backoff, 오류 분류

### 7.1 Budget

- provider 내부: 기본 3회, hard max 5회. adapter config가 범위를 벗어나면 startup `GENERATION_CONFIG_INVALID`.
- automatic generation job: root 포함 총 3개, 즉 policy retry 최대 2개.
- worker delivery: generation budget에 포함하지 않는다. generic queue의 별도 운영 정책을 따른다.
- 운영자 조건부 retry: 한 번의 승인 요청이 한 개 job을 추가한다. 자동 budget을 초기화하거나 다시 열지 않는다. 그 job이 terminal failure가 된 뒤에만 새 운영자 요청이 가능하다.

운영자 retry job도 해당 job 안에서는 provider 내부 retry 정책을 사용한다. 한 root에 active job은 여전히 하나다.

### 7.2 Backoff

automatic job retry의 `not_before`는 다음 결정식이다.

```text
policy_delay_seconds = min(3600, 30 * 2 ** (failed_job_attempt_number - 1))
effective_delay_seconds = max(policy_delay_seconds, sanitized_retry_after_seconds or 0)
not_before = database_clock + effective_delay_seconds
```

jitter는 사용하지 않아 replay와 운영 설명을 결정적으로 유지한다. `Retry-After`는 0..86400초로 sanitize하며 더 큰 값은 86400으로 cap한다. provider adapter 내부 backoff는 기존 bounded adapter policy를 유지하고 job-level `not_before`와 혼용하지 않는다.

### 7.3 Deterministic error codes

공개 history/API/Slack에는 아래 stable code만 노출한다.

| 코드 | 기본 재시도 | 최종성 예 |
| --- | --- | --- |
| `GENERATION_CONFIG_INVALID` | 아니오 | NON_RETRYABLE |
| `GENERATION_UNSUPPORTED_SCHEMA_VERSION` | 아니오 | NON_RETRYABLE |
| `GENERATION_AUTH_REQUIRED` | 아니오 | NON_RETRYABLE |
| `GENERATION_AUTH_REJECTED` | 아니오 | NON_RETRYABLE |
| `GENERATION_TIMEOUT` | 예 | SCHEDULED 또는 EXHAUSTED |
| `GENERATION_TRANSPORT_UNAVAILABLE` | 예 | SCHEDULED 또는 EXHAUSTED |
| `GENERATION_RATE_LIMITED` | 예 | SCHEDULED 또는 EXHAUSTED |
| `GENERATION_PROVIDER_REJECTED` | 아니오 | NON_RETRYABLE |
| `GENERATION_PROVIDER_ERROR` | 예(5xx만) | SCHEDULED 또는 EXHAUSTED |
| `GENERATION_INVALID_CONTENT_TYPE` | 아니오 | NON_RETRYABLE |
| `GENERATION_RESPONSE_TOO_LARGE` | 아니오 | NON_RETRYABLE |
| `GENERATION_MALFORMED_JSON` | 아니오 | NON_RETRYABLE |
| `GENERATION_INVALID_RESPONSE` | 아니오 | NON_RETRYABLE |
| `GENERATION_SOURCE_STALE` | 아니오 | REVIEW_REQUIRED |
| `GENERATION_SOURCE_UNAVAILABLE` | 아니오 | REVIEW_REQUIRED |
| `GENERATION_WORK_ITEM_DELETED` | 아니오 | REVIEW_REQUIRED |
| `GENERATION_EXECUTION_OUTCOME_UNKNOWN` | 아니오 | REVIEW_REQUIRED |
| `GENERATION_WORKER_DELIVERY_EXHAUSTED` | 아니오 | REVIEW_REQUIRED |

provider raw status text/body를 error message로 저장하지 않는다. 공개 `messageLabel`은 서버의 code-to-Korean-label 고정 table에서 렌더한다. unknown exception은 내부 trace ID로만 조사하고 공개 code는 허용된 deterministic mapping으로 축약한다.

## 8. 자동 실행 절차

1. production worker는 전용 job type `automatic_generation_execute`를 claim한다.
2. source work item, root, latest job, schedule을 lock order로 잠그고 trigger가 AUTOMATIC인지 확인한다.
3. `not_before`, lease token, latest lineage, one-active, source/release 정책을 검증한다.
4. 실행하지 못할 source 상태면 provider permit 없이 deterministic terminal/review event를 쓴다.
5. 기존 sealed context/input을 로드하고 digest를 재계산해 일치하지 않으면 호출하지 않는다.
6. provider call permit을 append하고 commit한다.
7. adapter가 각 내부 호출 전에 permit을 확보하고 각 결과에 observation을 append한다.
8. validate된 성공 result는 기존 result 원장에 저장하고 SUCCEEDED로 전환한다.
9. retryable failure는 budget이 남으면 다음 job, execution schedule, background job, timeline을 같은 transaction에 만든다.
10. non-retryable 또는 exhausted failure는 exhaustion/finality receipt와 T06 failure intent를 같은 transaction에 만든다.

job-level retry에서 provider adapter의 이전 내부 횟수를 이어서 세지 않는다. 새 job의 provider attempt는 다시 1에서 시작한다. 반대로 generic delivery reclaim 때문에 이미 observation이 있는 provider attempt를 다시 호출하지 않는다.

## 9. 자동 성공 뒤 materialization과 T06

T03이 root를 만들 때 다음 materialization mode를 seal한다.

- `AUTOMATIC_APPLY`: T02 whole-batch apply를 예약한다.
- `AUTOMATIC_SUGGESTIONS`: T05 suggestion materialization을 예약한다.

provider 성공 뒤 현재 release 상태를 보고 mode를 임의 변환하지 않는다. ordinary automatic apply batch가 실행 중 release되면 apply는 source/release CAS로 실패 또는 skip되고, 그 결과를 suggestion으로 자동 전환하지 않는다. `AUTOMATIC_SUGGESTIONS`는 T05가 요구하는 target/baseline/source seal이 provider 호출 전에 확보된 root에만 가능하다.

success job commit과 materialization schedule/background enqueue는 한 transaction이다. T02/T05 materialization은 각각 자신의 기존 unique/idempotency/CAS/whole-batch 권위를 유지한다. Slack success intent는 provider success 시점이 아니라 apply receipt 또는 suggestion materialization receipt가 commit된 뒤 T06가 만든다.

terminal failure 처리:

- non-retryable automatic failure는 finality receipt와 함께 T06 failure intent를 capture한다.
- retryable failure는 next schedule이 있으면 Slack failure intent를 만들지 않는다.
- retryable 최종 failure는 `generation_retry_exhaustions` receipt가 같은 transaction에 있을 때만 T06 intent를 만든다.
- Slack notification 전달 실패는 generation retry를 만들지 않는다. T06 notification delivery 원장/재조정 정책에만 속한다.

## 10. 수동 execute/retry와 자동 retry 분리

기존 `/api/generation-jobs/{job_id}/execute`와 `/retry`는 MANUAL trigger workspace의 명시적 사용자 행위다. AUTOMATIC root 또는 그 lineage에 이 endpoint를 호출하면 `409 GENERATION_AUTOMATIC_OPERATION_FORBIDDEN`이다. 자동 job을 request thread에서 동기 실행하지 않는다.

자동 정책 retry는 worker가 실패 transaction 안에서 생성한다. 운영자 조건부 retry는 T07 failure endpoint만 사용한다. 둘 다 기존 terminal job을 수정하지 않고 새 job을 append하지만 origin이 각각 `AUTOMATIC_POLICY`, `OPERATOR_CONDITIONS`다.

T07 v1은 cancel/resume을 제공하지 않는다. 운영 중 일시 중지는 새 claim을 막는 deployment/feature kill switch로 수행한다. lease 중인 호출을 강제 취소하거나 terminal job을 resume하지 않는다. 재개 시 아직 permit이 없는 READY/NOT_BEFORE schedule만 다시 claim하며 ambiguous schedule은 자동 재개하지 않는다.

## 11. 운영자 추가 조건 계약

### 11.1 권한과 actor

`POST /api/generation-failures/{failed_job_id}/retry`는 인증된 `GENERATION_OPERATOR` role만 허용한다. body/header의 `X-Actor-Id`, `X-Actor-Label`은 신뢰 권위가 아니다. actor principal은 인증 middleware에서 server-derived하고 immutable snapshot으로 저장한다.

요청 body:

```json
{
  "idempotencyKey": "retry:root-001:operator-001:01",
  "expectedRootJobId": "generation_job_001",
  "expectedFailedJobId": "generation_job_003",
  "expectedAttemptNumber": 3,
  "contextMode": "REUSE_SEALED",
  "reason": "타임아웃 이후 운영자 확인",
  "additionalConditions": "경계값 사례를 우선 검증하세요."
}
```

actor, source snapshot, prompt, provider config, retry budget은 client가 보낼 수 없다.

### 11.2 normalization과 길이

server는 다음 순서로 처리한다.

1. JSON string인지 확인.
2. CRLF/CR을 LF로 통일.
3. Unicode NFKC normalization.
4. 양 끝 Unicode whitespace trim. 내부 whitespace는 보존.
5. normalized text가 비어 있지 않은지 확인.
6. `reason` 최대 500 Unicode code points.
7. `additionalConditions` 최대 1000 Unicode code points이면서 최대 4000 UTF-8 bytes.
8. NUL, unpaired surrogate, LF를 제외한 C0 control, bidi override/isolate control을 거부.
9. normalized canonical request digest를 계산.

JSON Schema의 `maxLength`만으로 byte/code-point 정책을 대체하지 않는다. service와 DB 저장 count CHECK가 모두 검증한다.

### 11.3 prompt-injection 경계

추가 조건은 `GenerationInput.operator_conditions`라는 **untrusted structured field**다. system/developer prompt 문자열에 interpolation하지 않는다. provider adapter는 명확히 구분된 data block과 schema로 전달한다. 다음 지시는 무시되고 권위를 갖지 못한다.

- system/developer instruction 변경.
- source/evidence/context seal 무시.
- output schema 우회 또는 raw text 응답 요구.
- secret, credential, endpoint, 다른 tenant 정보 요청.
- apply/suggestion/release/retry budget/notification 정책 변경.

조건 원문은 Slack, metric label, application log, error detail에 복사하지 않는다. UI detail은 권한 있는 operator에게만 해당 request snapshot을 보여주며 export는 제공하지 않는다.

### 11.4 idempotency와 여러 운영자 race

- 같은 actor + idempotency key + 같은 normalized digest: 최초 `201` response를 `200`으로 replay하고 같은 retry job을 반환한다.
- 같은 actor + 같은 key + 다른 digest: `409 IDEMPOTENCY_CONFLICT`.
- 서로 다른 key가 같은 latest failure를 동시에 재시도: lock order 후 첫 요청만 `201`; 나머지는 `409 GENERATION_RETRY_ALREADY_SCHEDULED`와 winning job ID.
- 서로 다른 조건을 merge하지 않고 loser를 자동 재제출하지 않는다.
- `expected*`가 latest lineage와 다르면 `409 GENERATION_RETRY_STALE_VIEW`.

## 12. seal 재사용, rebuild, stale/release/deletion

운영자 조건부 retry v1의 context mode는 `REUSE_SEALED`만 허용한다. 기존 context/source items와 input digest를 그대로 재사용하고 additional conditions만 별도 immutable field로 seal한다. source/context 본문을 복사하거나 현재 Slack을 다시 읽지 않는다.

`REBUILD_CURRENT`는 retry endpoint가 아니다. source가 진전됐으면 T03 impact assessment가 새 source version을 평가해야 한다. 같은 source version에는 T01/T03의 deterministic automatic root unique를 우회해 두 번째 root를 만들지 않는다.

| 상태 | provider 호출 | retry queue | materialization |
| --- | --- | --- | --- |
| 동일 source, seal 정상 | 허용 | 계약대로 | sealed mode대로 |
| source version 증가 | 기존 sealed retry 호출 안 함 | `REASSESS_CURRENT_SOURCE` 안내 | 기존 result 적용 금지 |
| source tombstone/unavailable | 안 함 | REVIEW_ONLY | `SKIPPED_SOURCE_UNAVAILABLE` |
| work item deleted | 안 함 | REVIEW_ONLY | 없음 |
| T03 `SKIP` | job 자체가 없으므로 failure queue 제외 | 없음 | 없음 |
| T03 `REVIEW_REQUIRED` | job 자체가 없으므로 failure queue 제외 | 없음 | 없음 |
| human release 이전, mode APPLY | 조건 충족 시 허용 | 가능 | apply CAS |
| provider 중/후 human release | 결과 저장 가능 | generation 성공 유지 | apply는 skip/conflict; 자동 overwrite 없음 |
| mode SUGGESTIONS로 미리 seal | 허용 | 가능 | T05 suggestion만 생성 |

deleted/unavailable은 물리 row 부재만 뜻하지 않는다. tombstone, 접근 권한 상실, 필요한 historical source record 불완전도 포함한다. stale/deleted를 발견한 시점이 provider permit 전이면 외부 호출은 0회다.

## 13. attempt timeline

public detail은 각 generation job별 snapshot과 root-level ordered timeline을 함께 반환한다.

job snapshot에는 다음만 포함한다.

- root/retry lineage, attempt number, retry origin.
- status/execution state/not-before/worker delivery count.
- context/input/result digest와 schema version.
- provider attempt number, safe outcome/code/status/timestamps.
- deterministic failure/finality/next not-before/exhaustion ID.
- apply ID 또는 suggestion materialization ID와 상태.
- T06 notification intent/receipt ID와 delivery 상태.

timeline event는 `INPUT_SEALED`, `EXECUTION_QUEUED`, `WORKER_LEASED`, provider started/failed/succeeded, job failed/retry scheduled/succeeded, apply/suggestion scheduled/committed/skipped, Slack captured/delivered/suppressed/dead를 append한다. `(root, sequence)`가 표시 순서 권위이며 timestamp는 설명용이다. 동일 시각의 event를 timestamp만으로 정렬하지 않는다.

API는 provider request/response body, source text, prompt, raw exception, stack trace, URL, headers, API key, token을 반환하지 않는다. `safeProviderCode`는 allowlist 값만 허용한다.

## 14. API와 HTTP 계약

새 resource는 기존 `generation/job-api/1.0.0`을 변경하지 않고 `generation/retry-history/1.0.0`으로 독립 versioning한다. 기존 enum/required field/의미를 바꿔야 한다면 job API `2.0.0`을 발행한다. additive endpoint도 response의 `contractVersion`을 고정하고 generated client/schema snapshot을 함께 갱신한다.

| Method/path | 의미 | 성공 |
| --- | --- | --- |
| `GET /api/generation-failures` | automatic failure queue, cursor pagination | 200 |
| `GET /api/generation-failures/{failed_job_id}` | failure와 lineage summary | 200 |
| `GET /api/generation-failures/{failed_job_id}/attempt-history` | full safe timeline | 200 |
| `POST /api/generation-failures/{failed_job_id}/retry` | immutable conditional retry | 201, replay 200 |

list는 `actionability`, `finality`, error code, `failedBefore`, cursor, limit(기본 25, 최대 100) filter만 허용한다. 정렬은 `(failed_at DESC, failed_job_id DESC)`, opaque cursor다. MANUAL job과 final failure가 아닌 중간 failure는 기본 queue에서 제외한다.

| 상황 | HTTP/code |
| --- | --- |
| 인증 없음 | 401 `AUTHENTICATION_REQUIRED` |
| operator 권한 없음 | 403 `GENERATION_OPERATOR_REQUIRED` |
| 존재하지 않거나 tenant 밖 resource | 404 `RESOURCE_NOT_FOUND` |
| invalid Unicode/length/body | 422 `VALIDATION_FAILED` |
| latest/expected CAS 불일치 | 409 `GENERATION_RETRY_STALE_VIEW` |
| active retry 존재 | 409 `GENERATION_RETRY_ALREADY_SCHEDULED` |
| idempotency key payload 충돌 | 409 `IDEMPOTENCY_CONFLICT` |
| source 진전 | 409 `GENERATION_SOURCE_STALE` |
| deleted/unavailable/review-only | 409 stable source code |
| 정확한 replay | 200, original response |
| 새 retry 예약 | 201 + `Location` |

모든 mutation은 body idempotency key와 server actor를 사용한다. transport header가 key를 추가로 운반하더라도 둘이 다르면 422로 거부한다. 5xx body에도 trace ID와 safe code만 포함한다.

OpenAPI operation IDs는 focused RED에 고정된 `list_generation_failures`, `get_generation_failure`, `get_generation_attempt_history`, `retry_automatic_generation_failure`다. schema `$id`, package export, generated api-client 변경을 같은 pull request에서 수행한다.

## 15. 한국어 Web UI, mobile, 접근성

자동 실패 workspace는 수동 `/slack-work-items/:id/generation-jobs`와 분리해 `/generation/failures`에 둔다.

### 15.1 목록

- 제목 “자동 생성 실패”, 결과 수, finality/actionability/error filter.
- 각 행에 대상, source version, “생성 시도 N차”, 안전한 한국어 error label, 실패 시각, 다음 재시도 예정.
- 자동 재시도 대기는 버튼 대신 상태로 표시한다.
- operator retry 가능 terminal failure만 “조건을 추가해 재시도”를 제공한다.

### 15.2 상세와 retry dialog

- “시도 이력”에서 세 attempt 종류를 구분해 표시한다.
- input/result/apply/suggestion/notification은 digest/ID/status로만 표시한다.
- provider raw body/source/prompt/secret을 표시할 DOM이나 개발용 expansion을 두지 않는다.
- dialog 제목, `aria-describedby`, focus trap, Escape, opener focus 복귀를 지원한다.
- textarea label “추가 조건”, reason 별도 label, code-point/UTF-8 byte 잔여량을 `aria-live=polite`로 알린다.
- injection 경고: “추가 조건은 참고 데이터이며 시스템 규칙·근거·적용 정책을 변경할 수 없습니다.”
- submit 중 중복 클릭 차단, 결과 live region, 409 race 시 winning attempt로 이동.

### 15.3 responsive/a11y

- 390 CSS px에서 horizontal page scroll 없이 카드 stack.
- interactive target 최소 44×44 CSS px.
- 색만으로 status를 전달하지 않고 text/icon을 함께 사용.
- timeline은 semantic ordered list, table은 header association, dialog는 keyboard-only 완전 조작.
- reduced motion, 200% text zoom, visible focus, screen reader landmark/title을 검증한다.
- mobile에서는 filter와 retry dialog가 viewport 안에서 scroll되며 fixed footer가 textarea/error를 가리지 않는다.

## 16. cleanup과 retention

generation job/context/input/result/apply/suggestion/notification 및 T07 authority ledger는 감사·idempotency·lineage 재현을 위해 online DELETE하지 않는다. FK는 `ON DELETE RESTRICT` 또는 tombstone을 사용한다. T07 v1은 delete/cleanup API를 제공하지 않는다.

generic terminal `background_jobs`는 다음 조건을 모두 만족한 뒤에만 90일 이후 별도 maintenance가 prune할 수 있다.

1. schedule/materialization/notification의 terminal authority가 독립 원장에 commit됨.
2. active lease가 없음.
3. 해당 generic delivery ID와 최종 delivery count가 timeline snapshot에 있음.
4. legal/audit hold가 없음.

provider permit/observation과 operator request 원문은 authority이므로 무기한 보존한다. 추후 privacy retention이 필요하면 hash-only archival, tenant policy, legal hold, audit event를 포함한 새 versioned 계약이 선행돼야 한다. 임의 TTL로 source/context seal을 지우지 않는다.

## 17. metrics, alerts, live gates

metric label에는 root/job ID, actor, condition text, error message, endpoint를 넣지 않는다. 허용 label은 environment, safe error code, finality, materialization mode, retry origin 정도다.

- `generation_execution_claim_total{outcome}`
- `generation_provider_attempt_total{outcome,error_code}`
- `generation_job_retry_total{origin,error_code}`
- `generation_retry_exhaustion_total{error_code,finality}`
- `generation_execution_schedule_lag_seconds`
- `generation_execution_lease_reclaim_total{phase}`
- `generation_execution_outcome_unknown_total`
- `generation_materialization_total{mode,outcome}`
- `generation_operator_retry_total{outcome}`
- `generation_failure_queue_actionable`
- `generation_terminal_notification_capture_total{outcome}`

alerts:

- READY p95 lag > 5분 for 15분.
- PROVIDER_OUTCOME_UNKNOWN > 0 즉시 page.
- retry exhaustion rate가 15분 동안 성공 대비 20% 초과.
- failure terminal commit 대비 T06 intent capture 차이가 5분 이상 0이 아님.
- materialization schedule가 10분 이상 non-terminal.
- lease reclaim 급증 또는 CAS rejection 급증.

production enable gate:

1. migration unique/check/deferred/append-only trigger 검증.
2. 두 worker race에서 provider permit 하나와 active job 하나.
3. 각 crash window fault injection 통과.
4. provider internal retry와 job retry/delivery count 독립성 검증.
5. exhausted receipt 없이 T06 retryable failure intent가 생성되지 않음.
6. automatic success와 materialization enqueue atomicity 검증.
7. stale/release/delete/SKIP/REVIEW_REQUIRED matrix 통과.
8. operator auth/idempotency/Unicode/injection/race 검증.
9. API schema/client snapshot과 Korean Web a11y/mobile gate 통과.
10. canary에서 provider endpoint allowlist, credential secret store, kill switch, alert dashboard 확인.

## 18. 검증 계약과 의도적 RED

기본 green 계약:

```bash
pnpm --filter @tc/contracts test:unit
pnpm --filter @tc/contracts typecheck
```

`packages/contracts/src/phase-e-automatic-generation-retry-history-contracts.test.ts`는 strict Draft 2020-12 compile, package export, safe public resource, attempt 분리, finality receipt, immutable conditional retry를 검증한다.

focused RED는 파일명이 기본 discovery에 들어가지 않으므로 기존 green gate를 깨지 않는다.

```bash
pytest -q apps/api/tests/red_phase_e_automatic_generation_retry_history.py
TC_TEST_DATABASE_URL=postgresql+psycopg://.../t07_test \
  pytest -q -m postgres \
  apps/api/tests/red_phase_e_automatic_generation_retry_history_postgresql.py
node --test evals/red/phase-e-automatic-generation-retry-history-web.red.test.mjs
```

RED는 각각 다음 production 부재 때문에만 실패해야 한다.

- domain retry/history service와 ORM model 부재.
- failure queue OpenAPI/router 부재.
- worker automatic generation handler wiring 부재.
- migration table/unique/append-only guard 부재.
- Web service/route/Korean accessible UI 부재.

syntax error, missing test fixture, network, secret, 실제 provider 호출 때문에 실패하면 잘못된 RED다.

## 19. production 구현 seam과 순서

1. migration/ORM: 7개 table, constraint, partial unique, deferred/append-only trigger.
2. domain: `tc_backend.generation.retry_history`의 claim/fold/finality/operator retry service.
3. adapter: provider permit/observation callback seam과 sanitized error mapper.
4. worker: `AUTOMATIC_GENERATION_EXECUTE_JOB_TYPE`, `build_automatic_generation_execution_handler`, `build_worker(..., generation_execution_runner=...)`.
5. materialization: T02 apply/T05 suggestion schedule handler와 T06 capture 연계.
6. API: `tc_api.routers.generation_failures`, auth role, four operation IDs, generated api-client.
7. Web: failure service, `/generation/failures`, Korean list/detail/dialog, a11y/mobile.
8. observability/live gate와 canary.

구현이 끝나면 focused RED 파일은 같은 assertion을 유지한 채 green이 되어야 한다. production seam을 테스트 이름 회피용 빈 symbol/route/table로만 추가하는 것은 완료가 아니다. PostgreSQL concurrency/fault-injection/live gate까지 통과해야 T07을 production-ready로 표시한다.
