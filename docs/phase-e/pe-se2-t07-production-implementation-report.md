# PE-SE2-T07 Production 구현 보고서

- 작성일: 2026-08-28 (Asia/Seoul)
- 구현 기준: `automatic-generation-retry-history-contract.md`, retry-history schema 1.0.0, focused RED 5+2+3, 독립 감사 acceptance
- 상태: production composition과 독립 adversarial 수정, disposable PostgreSQL 및 실제 Web production preview 검증 완료. 외부 provider·Slack credential live gate는 미실행이다.
- Roadmap: required local gate가 모두 green이므로 `PE-SE2-T07`을 완료 처리했다. T08~T11은 열려 있다.

## 구현 결과

Alembic `20260828_0026`은 0025 다음 revision으로 다음 7개 권위 원장을 설치한다.

1. `generation_execution_schedules`
2. `generation_attempt_events`
3. `generation_provider_call_permits`
4. `generation_provider_call_observations`
5. `generation_retry_requests`
6. `generation_retry_exhaustions`
7. `generation_materialization_schedules`

다섯 immutable 원장에는 UPDATE/DELETE 거부 trigger를 설치했고, mutable execution/materialization schedule도 DELETE할 수 없게 했다. root active schedule, job/provider attempt, permit observation, actor+idempotency, root exhaustion, job/root materialization, root terminal Slack intent의 named unique guard를 설치했다. 모든 FK는 RESTRICT이며 deferred authority trigger가 workspace, work item, source version, root, 직전 `retry_of`, contiguous attempt/event sequence, provider permit/observation, policy snapshot, exact background job type/payload, materialization receipt, terminal Slack receipt 연결을 commit 시 검증한다. background job을 뒤늦게 위조해도 해당 table의 deferred trigger가 거부한다. T03 canonical baseline 함수와 service query는 실제 committed apply 또는 suggestion materialization이 있는 automatic root만 선택하며 failed/queued/unexecuted/manual root와 retry child 자체를 baseline ID로 선택하지 않는다. downgrade에서는 0025 의미의 baseline 함수와 기존 generation error constraint를 복원한다.

## Production worker composition

독립 감사에서 `apps/worker`의 실제 `main()`이 `generation_execution_runner` 없이 `build_worker`를 호출하여, injection test와 달리 모든 AUTOMATIC job이 `GENERATION_CONFIG_INVALID`로 끝나는 P0를 확인했다. `Settings`에 명시적 opt-in, provider/model ID, HTTP(S) endpoint, secret auth token/header/scheme, timeout, response byte limit을 추가하고 다음 경계를 production main에 조립했다.

- disabled 기본은 adapter/HTTP transport를 만들지 않으므로 credential 없이 외부 network call이 0회다.
- enabled이지만 provider/model/endpoint/token 중 하나라도 없으면 startup validation이 fail-fast 한다.
- endpoint userinfo를 거부하고 token은 `SecretStr` 및 redacted adapter repr로만 다룬다. raw provider body, exception, credential, endpoint는 ledger/API/log에 저장하지 않는다.
- `GenerationAPIAdapter`는 `RetryPolicy(max_attempts=1)`인 durable T07 single-call runner로만 bridge한다. adapter가 hidden retry를 주장하거나 permit 이후 결과가 불명확하면 재전송하지 않고 `PROVIDER_OUTCOME_UNKNOWN/TERMINAL_REVIEW_REQUIRED`로 닫는다.
- generic background heartbeat와 별도로 provider call 동안 execution lease를 주기적으로 갱신하며 late completion은 lease token과 background owner/attempt CAS를 모두 통과해야 한다.

운영 값, canary, kill switch, credential rotation과 ambiguous outcome 대응은 `deploy/generation/README.md`에 고정했고 `backend/.env.example`은 disabled 기본과 placeholder만 제공한다.

## 실행 및 retry 상태 의미

실행 schedule 상태는 `NOT_BEFORE/READY -> LEASED -> TERMINAL`이며, permit 이후 observation이 없는 lease 상실은 `PROVIDER_OUTCOME_UNKNOWN`과 `TERMINAL_REVIEW_REQUIRED` receipt로 fail-closed 된다. claim 전 또는 claim 후 permit 전 crash는 lease expiry 뒤 reclaim 가능하고, permit commit 이후 외부 호출 구간에는 DB transaction이나 row lock을 유지하지 않는다. worker 단계는 다음처럼 분리했다.

1. background delivery ownership 및 schedule lease claim commit
2. provider call permit commit
3. DB transaction 밖에서 명시적 single-call runner 실행
4. lease-token CAS observation commit
5. validated result finalization 또는 failure+next schedule/exhaustion commit
6. 별도 materialization delivery에서 apply/suggestion+Slack intent commit

provider attempt는 generation job마다 1부터 다시 시작하며 기본 3/hard max 5이고, generation job attempt는 root 포함 기본 3이다. generic `background_jobs.attempts`는 delivery/reclaim 횟수로 유지되어 generation retry budget을 소비하지 않는다. adapter가 한 호출 seam에서 내부 retry를 숨겨 `attempts != 1`을 반환하거나 permit 뒤 알 수 없는 예외가 발생하면 blind resend하지 않고 outcome unknown/manual review로 종결한다. job retry backoff는 `max(30 * 2^(attempt-1), sanitized Retry-After)`이며 jitter 없이 not-before에 고정된다.

mutation lock 순서는 materialization/operator 경로에서 workspace settings, scoped Slack work item, root/job, schedule/target, notification/outbox 순서를 사용한다. execution의 provider 외부 호출 구간은 lock-free이며, 모든 late/stale worker write는 schedule lease token과 background delivery owner/attempt를 다시 확인한다.

## 성공 materialization과 사람 수정 보호

ordinary automatic result는 기존 sealed context/input과 output schema/grounding을 재검증한 뒤 T02 whole-batch apply를 사용한다. release된 사람 소유 target을 가진 suggestion root는 T05 immutable suggestion materialization을 사용한다. root에서 봉인한 mode를 provider 성공 뒤 임의 변환하지 않으며 automatic overwrite 경로를 추가하지 않았다.

materialization 직전 source version/root availability를 다시 확인한다. stale 또는 unavailable source는 provider 결과를 현재 source에 적용하지 않고 deterministic skipped state와 event를 남긴다. apply/suggestion 중 concurrent human edit, release 또는 stale placement conflict가 발생하면 실패 transaction을 rollback한 뒤 같은 owned delivery를 `REVIEW_REQUIRED`로 fold하여 generic delivery retry가 overwrite 시도로 증폭되지 않게 했다. 성공 apply/suggestion receipt, claimed materialization schedule의 `COMMITTED` transition과 T06 success intent는 같은 authoritative transaction에 연결된다. 이전 notification helper가 background job을 만들고 즉시 `SUCCEEDED`로 바꾸던 synthetic 우회는 제거했으며, 실제 `RUNNING` claim/lease가 없으면 전체 transaction이 rollback된다.

retryable failure는 automatic job budget이 남은 경우 직전 failed job을 가리키는 child 하나와 deterministic schedule/event/background job을 같은 transaction에서 만든다. budget 소진 또는 non-retryable/review-required failure는 immutable exhaustion receipt와 T06 failure intent를 같은 transaction에서 만든다. Slack terminal failure의 `retryable=false`는 “자동 retry가 더 없음”을 의미하며, 운영자 조건부 retry 가능성은 failure UI에서 별도로 표현한다. root terminal notification unique guard와 service replay가 duplicate semantic intent를 막는다. terminal failure 답글 이후 operator retry가 active/success이면 row는 `REVIEW_ONLY`이고 성공은 append-only attempt history에 보이지만 두 번째 상반된 Slack 답글은 만들지 않는다. operator retry가 다시 terminal failure가 되면 row가 latest failed job/attempt로 전진해 새 immutable operator request를 허용하며, list filter와 계산된 actionability가 일치한다.

## 운영자 조건부 retry와 API

`POST /api/generation-failures/{failed_job_id}/retry`는 terminal failed/exhausted latest job에만 새 child를 append한다. actor/workspace/source/policy는 request body에서 받지 않고 single-team 서버 actor와 현재 workspace, failed job의 sealed context/input에서 도출한다. body/header idempotency key가 다르면 422이고, 동일 actor+key+canonical request digest는 동일 retry job을 replay한다. concurrent winner나 stale source/root/attempt는 deterministic 409/404로 닫힌다.

추가 조건과 reason은 NFKC normalize 후 저장하며 조건은 1~1000 code point/4000 UTF-8 byte 경계다. whitespace-only, NUL/C0/C1 control, bidi override/isolate, invalid UTF-8 surrogate, secret/token/password 형태를 거부한다. original digest와 normalized digest를 모두 남기고 provider input에는 `UNTRUSTED_ADVISORY_DATA` structured field로만 추가하여 system prompt, evidence, decision 또는 generation policy로 승격하지 않는다. 기존 manual execute/retry endpoint는 AUTOMATIC lineage에 `409 GENERATION_AUTOMATIC_OPERATION_FORBIDDEN`을 반환한다.

추가된 API는 다음과 같다.

- `GET /api/generation-failures`
- `GET /api/generation-failures/{failed_job_id}`
- `GET /api/generation-failures/{failed_job_id}/attempt-history`
- `POST /api/generation-failures/{failed_job_id}/retry`

목록은 `(failed_at DESC, failed_job_id DESC)` stable cursor와 filter fingerprint를 사용한다. 응답은 deterministic safe code/한국어 label, digest, immutable receipt/event reference만 제공하며 raw prompt/source/provider body, endpoint, token, credential, exception string은 반환하지 않는다. OpenAPI와 generated TypeScript client를 함께 갱신했다.

## Web

`/generation/failures`를 manual generation workspace와 분리된 한국어 navigation으로 추가했다. 화면은 실패 목록, 상세, generation/provider/delivery 시도 이력, immutable timeline, generation job/comparison 안전 링크, “조건을 추가해 재시도” dialog를 제공한다. 처리 중/신규 성공/idempotent replay/오류 상태를 `aria-live`로 알리고 dialog label/description, initial focus, focus trap, ESC, keyboard submit/cancel을 지원한다. CSS는 390px media boundary, `overflow-x: hidden`, wrapping grid/list, 44px minimum touch target을 적용하며 provider 원문 진단은 표시하지 않는다.

독립 preview 감사에서 page root와 alert banner가 같은 CSS class를 사용해 workspace typography/padding이 덮이는 결함과 nested `<main>`을 수정했다. state/actionability/finality/error-code filter를 실제 API query에 연결했고, lost response 뒤 dialog를 닫았다 다시 열어도 동일 root+failed job의 session idempotency key를 재사용하도록 했다. Vite production preview를 실제 렌더해 1280×768과 390×768에서 loading/error/empty/12-row/detail/modal을 확인했으며 horizontal overflow 0, 최소 action 높이 44px, modal focus trap/ESC/opener restore와 `aria-modal`을 확인했다.

## 검증 결과

외부 provider 또는 Slack credential call 없이 PostgreSQL 16 disposable DB와 explicit deterministic/fake seam만 사용했다.

- authoritative focused RED: Python 5/5, PostgreSQL 2/2, Web source 3/3
- normal regression 승격: Python 5/5, PostgreSQL 4/4, Web Vitest 3/3
- T01~T06 PostgreSQL 회귀: 79 passed
- T01~T07 PostgreSQL product 묶음: 83 passed, 0 failed
- PostgreSQL budget fault: generation job 3개 × provider permit 3개 = 9 observations, 각 job N+1 permit 차단, exhaustion 1개와 Slack failure intent 1개 확인
- immutable/raw-SQL fault: exhaustion 및 materialization schedule DELETE, background job type/payload 위조를 거부했고 5 append-only trigger, 2 no-delete trigger와 named/deferred guards를 확인
- `pnpm check`: 성공
  - Python non-PostgreSQL: 326 passed, 104 PostgreSQL tests deselected
  - Web: 180 passed, 1 live E2E skipped
  - contracts: 100 passed
  - API client: 21 passed
  - UI package: 6 passed
  - Ruff: 성공
  - strict mypy: 120 source files, 0 issue
  - TypeScript typecheck/ESLint/schema/ADR/OpenAPI drift: 성공
- TypeScript unit 합계: 307 passed, live E2E 1 skipped
- worker unit: 21 passed
- Web production build: Vite 96 modules, 성공
- migration/repository: 57 passed, 기존 collection warning 1; clean base→head와 `0026 -> 0025 -> 0026` 성공, 최종 head `20260828_0026`

PostgreSQL 회귀에는 provider budget 1/N/N+1, retryable automatic chain, committed success observation 뒤 crash recovery, claimed materialization worker→apply→intent→schedule atomic commit, final exhaustion+Slack atomicity, operator retry/lost-response/success-history, raw immutable mutation, T03 actual-materialization baseline만 선택하는 규칙이 포함된다. 선행 T01~T06 suite는 기존 crash/ack, apply atomicity, suggestion human-edit/release, Slack delivery permit/observation, source revision/concurrency 회귀를 계속 실행한다.

## 남은 live gate와 T08~T11 경계

다음은 코드 green과 별개인 배포 환경 gate다.

- 실제 provider credential/model/endpoint로 single-call adapter가 구성되는지 startup/deployment 검증
- provider가 동일 permit/request digest를 처리했는지 확인할 수 있는 운영 대시보드 및 manual outcome-review runbook
- 실제 Slack credential/allowlist에서 terminal intent delivery 및 ack 관찰
- 장시간 lease heartbeat, process kill, DB failover, provider timeout을 포함한 staging chaos rehearsal
- retention/partition/archival 및 운영 지표·경보 임계값 확정

credential이 없는 기본/테스트 worker는 generation transport 자체를 구성하지 않아 네트워크를 호출하지 않고 setup-required 의미로 닫힌다. 위 live gate는 이번 작업에서 credential을 주입하거나 외부 시스템을 호출해 우회하지 않았다. T08 golden quality, T09 version regression, T10 실제 root→Slack 전체 E2E, T11 human-modified suggestion 전체 E2E는 완료 처리하지 않았다.
