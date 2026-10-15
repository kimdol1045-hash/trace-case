# PE-SE2-T06 production implementation report

## 결과

`automatic-generation-slack-reply/1.0.0`의 production 경계를 구현했다. 자동 생성의
terminal application transaction은 immutable apply 또는 suggestion materialization
batch receipt, 혹은 최종 non-retryable failure를 근거로 frozen intent와 delivery job을
함께 기록한다. Manual, SKIP/no-job, retry exhaustion 전의 retryable failure, stale 또는
unavailable source는 게시 대상으로 승격하지 않는다.

외부 write는 별도 worker attempt에서만 허용된다. Worker는 짧은 transaction으로
`POST`/`RECONCILE` permit을 먼저 commit하고, DB transaction 밖에서 Slack을 호출한 뒤,
별도 transaction으로 sanitized observation과 terminal receipt를 기록한다. Provider ack
유실이나 local receipt commit 유실 뒤에는 unresolved POST permit이 blind repost를 막고,
exact metadata marker, deterministic `client_msg_id`, canonical parent와 frozen text가 모두
일치하는 `conversations.replies` 결과만 `THREAD_RECONCILIATION` delivery proof가 된다.

## 영속성과 truth authority

- Migration `20260828_0025`는 `generation_suggestion_materializations`와 네 notification
  ledger를 설치한다. FK는 `RESTRICT`, ledger는 UPDATE/DELETE trigger로 immutable이며,
  intent/receipt logical identity, attempt transition, observation/permit 조합, retry lineage,
  canonical source, materialization/apply authority를 DB constraint trigger로 검증한다.
- Ordinary success 수치는 committed `GenerationApply`와 ordered outcomes의 `CREATED` /
  `REUSED`에서만 계산한다. Suggestion success는 immutable batch receipt의 candidate,
  suggestion, no-change 수치만 사용하며 automatic overwrite는 항상 0이다.
- Failure renderer는 allowlisted stable generation error code만 사용한다. Provider result,
  prompt, token, raw error, endpoint 또는 Slack source permalink는 frozen message에 넣지
  않는다.
- Destination은 locked local workspace, configured token team, channel allowlist,
  `SlackWorkItem`/`SlackThread`/root message identity와 live latest revision으로 재검증한다.
  Payload permalink나 caller-provided destination은 사용하지 않는다.

## Slack/worker 안전 경계

- `chat.postMessage` write seam은 JSON request를 단 한 번만 보낸다. Transport-level hidden
  retry는 없고, 429는 durable `Retry-After`, auth/scope/destination rejection은 permanent,
  timeout/5xx/internal/fatal/invalid ack/response loss는 ambiguous로 분류한다.
- Ambiguous 상태는 reconciliation horizon 동안 read-only reconciliation만 수행한다.
  exact match가 없으면 재게시하지 않고, horizon 종료 시 `AMBIGUOUS_UNRESOLVED`로 fail-safe
  terminalize한다. 따라서 Slack의 provider-committed idempotency 보장 없이 strict
  observable exactly-once를 주장하지 않는다.
- 마지막 worker attempt도 receipt 없이 generic job만 `DEAD`가 되지 않는다. 마지막 POST의 ack
  loss는 같은 claim에서 즉시 reconciliation하고, not-found/transport ambiguity와 durable 429
  exhaustion은 각각 `AMBIGUOUS_UNRESOLVED`/`RATE_LIMIT_EXHAUSTED` receipt로 닫는다. POST permit 뒤
  provider 호출 전 crash도 다음 claim에서 reconciliation만 수행한다.
- Production worker는 configured transport가 있을 때만
  `SLACK_GENERATION_NOTIFICATION_DELIVERY`를 claim한다. 정확한 token workspace ID와 explicit
  notification opt-in이 없으면 handler 자체를 등록하지 않는다. Generic worker의 claim/heartbeat,
  provider network call, observation/receipt commit과 final background-job CAS가 분리된다.
- Events API와 Socket Mode token-team mismatch를 거부한다. Provider-attested bot/app
  identity와 T06 metadata marker가 함께 있는 self reply는 event ingest와 thread sync에서
  제거되어 source version, quiet job, outbox 또는 generation feedback loop를 만들지 않는다.
- Manifest에는 `chat:write`와 metadata shape만 추가했다. `chat:write.public`, broadcast,
  blocks/attachments, DM/private scopes와 fallback destination은 추가하지 않았다.

## 구성과 운영 문서

`TC_PUBLIC_WEB_BASE_URL`은 credential/query/fragment/Slack mrkdwn delimiter 없는 stable public HTTPS product UI만
허용한다. Localhost, local/private IP와 temporary `trycloudflare.com` host는 거부하며,
값이 없으면 link line을 결정적으로 생략한다. Credential-free env example과 runbook에는
delivery breaker, lease, reconciliation horizon, exponential backoff, durable Retry-After,
self-reply filtering과 ambiguity fail-safe를 기록했다.

Credential-free 기본은 `TC_SLACK_MODE=disabled`와
`TC_SLACK_GENERATION_NOTIFICATIONS_ENABLED=false`다. Ingestion credential을 구성하는 것만으로
Slack write가 암묵적으로 활성화되지 않으며 App scope/team/allowlist/channel membership을 확인한
후에만 운영자가 notification flag를 켠다.

## 독립 adversarial 감사 수정

- retryable failure에 대한 caller-supplied `final_retry_exhausted` 신뢰를 제거했다. T07의 durable
  exhaustion authority가 생기기 전에는 retryable failure가 T06 intent로 승격되지 않는다.
- final attempt의 ack loss, pre-call crash, reconciliation not-found/transport failure와 429 exhaustion이
  terminal receipt 없이 끝나는 경로를 닫았다. Same-channel POST permit은 advisory transaction lock과
  append-only prepared time에서 1초 floor를 계산한다.
- HTTP/fake와 delivery 양쪽에서 reconciliation channel, canonical parent, non-root reply timestamp,
  bot/app identity, exact marker/client ID/text를 검증한다. Destination drift는 임의 match 선택이나
  fallback 없이 permanent integrity failure다.
- Migration guard가 current source, suggestion materialization ID/root/result seal, non-retryable failure,
  delivered-vs-root timestamp와 observation→DEAD reason 조합을 검증한다. Raw UPDATE/DELETE와
  ACK observation을 AUTH failure receipt로 위조하는 commit을 실제 PostgreSQL에서 거부했다.
- Events API mapping과 thread sync 실제 merge seam에서 self notification이 source version, outbox와
  generation job 수를 바꾸지 않음을 검증했다.

## 검증

- `pnpm check`: PASS. Ruff와 ESLint, Python mypy 117 files, TypeScript typecheck, Python unit
  308(99 PostgreSQL deselected), TypeScript unit 296, schema/ADR validation, OpenAPI drift check가 모두 통과했다.
  TypeScript live E2E 1건은 기존 opt-in gate라 skip됐다.
- T01-T06 focused PostgreSQL/API suite: `76 passed`.
- Migration/repository PostgreSQL suite: `57 passed`.
- Original T06 RED gates: worker 5개와 PostgreSQL schema 3개를 포함해 모두 green이다. T06
  PostgreSQL reply 7개, apply 15개, suggestion 10개가 permanent/retryable ordering, application rollback,
  POST ack, pre-call crash, final ack loss/reconciliation, durable 429, raw SQL spoof, stale/self suppression과
  suggestion materialization authority를 exercised했다.
- Slack unit suite: `31 passed`; 전체 worker suite: `13 passed`.
- Disposable PostgreSQL에서 `0025 -> 0024 -> 0025` round trip과 재실행이 통과했다.
- TypeScript typecheck/lint/test와 Web production build가 통과했고 Vite는 `95 modules`를 변환했다.
- 최종 fixture cleanup 뒤 generation job, 네 notification ledger, background job과 Slack work item은
  모두 0행이었고 migration head는 `20260828_0025`였다.

## 남은 외부 gate

실제 Slack App, token, channel membership 또는 credential을 사용하지 않았고 live Slack
call은 0회다. 별도 test workspace에서 app install/metadata registration, invited-channel
membership, real `chat:write`, provider rate-limit/auth behavior와 opaque ID/status/time만 남기는
credentialed smoke가 남아 있다. 또한 현재 T06 범위는 provider를 실행하거나 retry lineage를
생성하지 않으므로, upstream automatic provider execution/apply 또는 suggestion
materialization scheduling이 production에 조립되는 시점의 end-to-end terminal capture는
그 소유 task와 함께 검증해야 한다.
