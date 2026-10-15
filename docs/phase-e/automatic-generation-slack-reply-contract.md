# PE-SE2-T06 automatic generation Slack reply contract

## Status and scope

This document is the released production contract for `PE-SE2-T06`. It composes the released T01 quiet-window orchestration, T02 canonical
source deduplication, T03 impact decision, T04 one-way human management release, T05
suggestion ledger, the generation job/apply ledgers, and the existing Slack source and
background-job infrastructure. Production code, migration `20260828_0025`, configured-only
worker wiring and the minimal Slack write scope are implemented; live credentials remain a
separate external gate.

T06 owns one user-visible terminal reply for each eligible automatic generation attempt,
including an automatic root or its retry child. It does not execute a provider, create a
retry lineage, change T03 decisions, apply a generation result, resolve a suggestion, edit
or delete a Slack message, or post any top-level/channel fallback.

The normative internal envelope is
`packages/contracts/generation/slack-reply/1.0.0/schema.json`. Structural validation is
necessary but not sufficient: the PostgreSQL lineage, source, materialization, ordering,
and transport proofs below are authoritative.

## Existing authorities and trust boundary

| Fact | Sole authority |
|---|---|
| Local tenant | `WorkspaceSettings.id` reached through the locked `SlackWorkItem.workspace_id` |
| Slack destination | the canonical `SlackWorkItem` plus its one `SlackThread` and root `SlackMessage`; never a job payload |
| Generation attempt | immutable `GenerationJob` root/retry lineage and terminal lifecycle fields |
| Automatic provenance | `generation_trigger = AUTOMATIC` on both the terminal attempt and root |
| Ordinary success counts | one committed `GenerationApply` and its ordered `GenerationApplyOutcome` rows |
| Suggestion success | the sealed `generation-suggestion/1.0.0` job and T05 materialization result |
| Failure disclosure | `GenerationJob.error_code`, `error_retryable`, and attempt number; never `error` |
| Current-source fence | locked `SlackWorkItem.source_version`, root revision/tombstone, current allowlist, and exact Slack workspace configuration |
| Delivery truth | append-only notification receipt backed by Slack acknowledgement or exact thread reconciliation |

The background-job payload contains only `notificationIntentId`. A caller-supplied job ID,
workspace, Slack workspace, channel, `thread_ts`, terminal status, counts, error, link,
message text, metadata, or idempotency key is untrusted and cannot select a destination or
create a receipt. A forged or cross-workspace payload is a permanent worker failure with no
Slack call.

## Canonical Slack work item and thread identity

The T02 canonical root key remains:

```text
(workspace_id, slack_workspace_id, channel_id, canonical_root_message_ts)
```

For T06, all of these relations must be proven in one locked read before an intent is
captured and again immediately before the first external call:

1. `GenerationJob.slack_work_item_id` identifies that canonical `SlackWorkItem`.
2. The job and its root agree on `slack_work_item_id`, `root_job_id`, `AUTOMATIC` trigger,
   source version, and monotonic attempt lineage.
3. Exactly one `SlackThread` belongs to the work item and
   `SlackThread.thread_ts = SlackWorkItem.message_ts` after six-fraction normalization.
4. Exactly one root `SlackMessage` belongs to that thread, has `is_root = true`, and has
   the same `message_ts`. Its current revision is live.
5. The current allowlist row has the same local workspace, Slack workspace, and channel.
6. The configured token bundle names the same Slack workspace. A token for another team
   cannot be used even if it can see a similarly named channel.

`event_id`, event timestamp, outbox ID, revision ID, permalink, body, author, job delivery,
and transport mode are not destination identity. A reply always sends the root timestamp as
Slack `thread_ts`; a reply timestamp is never accepted as the parent. Slack likewise documents
that `thread_ts` must identify the parent message in
[`chat.postMessage`](https://docs.slack.dev/reference/methods/chat.postmessage).

## Eligibility and one notification per attempt

The durable notification identity is:

```text
automatic-generation-slack-reply/1.0.0 : terminal_generation_job_id : terminal_status
```

It is unique by `generation_job_id`; terminal status is immutable, so a second status is a
corruption rather than another notification. `client_msg_id` is the UUIDv5 of this key under
one repository-fixed namespace. The UUID and full key are stored, not recalculated from mutable
rows during delivery.

| Generation/impact state | T06 decision |
|---|---|
| Automatic root `FAILED` | capture one failure intent in the same commit that makes the job terminal |
| Automatic retry `FAILED` | capture its own failure intent; attempt number is shown |
| Automatic root/retry `SUCCEEDED`, ordinary `1.0.0` output, committed apply | capture one `SUCCESS_APPLIED` intent in the apply commit |
| Automatic root/retry `SUCCEEDED`, suggestion output, completed T05 materialization | capture one `SUCCESS_SUGGESTIONS` intent in the materialization commit, including zero equal/no-op proposals |
| `SUCCEEDED` provider result without apply/materialization | no success intent yet; provider success alone must not claim that cases or suggestions are ready |
| Manual root or any retry whose root is manual | excluded; no intent, enqueue, attempt, receipt, or Slack call |
| T03 `SKIP` | no new job and no T06 reply; reusing an earlier materialization is not a new success |
| T03 `REVIEW_REQUIRED` | no job and no T06 reply |
| stale quiet delivery or other no-job path | no T06 reply |

An automatic root failure followed later by a retry success therefore produces two truthful,
separately deduplicated replies. T06 does not collapse attempt history or create the retry; T07
owns retry policy and failure management.

### Successful materialization vocabulary

`CREATED` means that the committed apply receipt created a new active case. `REUSED` means that
the receipt reused an existing case with `ledgerMutation = NONE`; it must be rendered as
“existing case reused (no change)”, never “updated”. Counts come only from the immutable apply
receipt and must equal its ordered outcomes.

A suggestion-mode success is not a `CREATED` or `REUSED` case outcome. It reports the number of
new immutable T05 suggestions and explicitly reports zero automatic overwrites. A proposal that
is semantically equal and therefore materializes no suggestion still has a truthful suggestion
analysis success with count zero. Applying or ignoring a suggestion later does not edit the
already-posted T06 reply.

## Late, edited, deleted, and missing source policy

The product prefers silence plus durable operator evidence over posting an obsolete or
misdirected result.

- If `SlackWorkItem.source_version` no longer equals the job's sealed source version when the
  intent is captured, append a `SUPPRESSED_STALE_SOURCE` intent/terminal receipt and make no
  external call.
- If the source changes after intent capture but before delivery, the worker appends that same
  terminal suppression receipt. It never rewrites the frozen message or retargets a newer root.
- An edit/reply that does not alter canonical identity still advances source version and is
  therefore stale for the older job. A rename or permalink change alone is not identity.
- A deleted root, a root whose current revision is a tombstone, `root_seen = false`, a missing
  `SlackThread`, a missing/duplicate root `SlackMessage`, or mismatched `thread_ts` produces
  `SUPPRESSED_SOURCE_UNAVAILABLE` with no Slack call.
- If the root is deleted, locked, archived, or becomes inaccessible after the preflight but
  Slack definitively rejects the write, record a permanent sanitized delivery failure. Do not
  post to the channel, another thread, a DM, an admin channel, or the source permalink.
- A source edit after a confirmed Slack acknowledgement cannot retract or mutate the historical
  notification. The receipt remains evidence of what was posted for that sealed source version.

The suppression receipt is a local terminal outcome, not a claim that a Slack message exists.
It is visible to operations and mutually exclusive with a delivered receipt.

## Durable append-only ledgers

The implementation adds four immutable ledgers. All foreign keys use `RESTRICT`; `UPDATE` and
`DELETE` are rejected by database triggers. JSON objects use strict shape guards and never store
credentials, headers, raw provider bodies, or raw exception strings.

### `generation_slack_notification_intents`

One row is the frozen application decision and message snapshot. It contains at least:

| Column group | Required content |
|---|---|
| Identity | ID, local workspace, Slack work item, root job, terminal job, source version, attempt number |
| Optional success authority | committed generation apply ID or selecting/materialized suggestion assessment |
| Destination snapshot | Slack workspace ID, channel ID, canonical root message timestamp, equal thread timestamp |
| Outcome | `SUCCESS_APPLIED`, `SUCCESS_SUGGESTIONS`, or `FAILURE`; terminal job status; `POST` or a fixed suppression disposition |
| Message seal | contract/template version, `ko-KR`, exact text snapshot, RFC 8785 SHA-256 digest, link mode and optional exact HTTPS URL |
| Provider correlation | notification key, deterministic UUID `client_msg_id`, exact safe metadata payload |
| Time | application decision timestamp, excluded from message/idempotency digests |

Unique constraints cover the terminal generation job, notification key, and client message UUID.
Deferred guards prove all workspace → source → job/root → apply/suggestion relations and the
terminal/source/message snapshot. An exact replay validates every frozen field and returns the
same intent. A mismatch is corruption and fails closed.

### `generation_slack_notification_delivery_attempts`

Before any external write or reconciliation, the worker appends and commits one immutable permit
containing intent ID, operation (`POST` or `RECONCILE`), background-job/claim attempt, worker
identity, lease token/expiry, request digest, and prepared time. A database guard permits only one
unresolved operation for an intent. Once a `POST` permit exists without definitive no-delivery
evidence, later operations may only reconcile; they may not post again.

### `generation_slack_notification_delivery_observations`

After a transport/reconciliation result, append a sanitized observation referencing the prepared
attempt. Allowed classifications are `ACKNOWLEDGED`, `DEFINITIVE_NO_DELIVERY`, `RATE_LIMITED`,
`AMBIGUOUS`, `RECONCILED_FOUND`, and `RECONCILED_NOT_FOUND`. It stores a bounded provider code,
HTTP status, retry-after seconds, and safe diagnostic enum when applicable. Provider response
text, token, authorization header, endpoint query, request body, Slack message bodies other than
the exact frozen notification, and stack trace are forbidden.

### `generation_slack_notification_receipts`

There is exactly one terminal receipt per intent:

- `DELIVERED` with proof `POST_ACK` or `THREAD_RECONCILIATION`, Slack channel/message/root
  timestamps, client message UUID, message digest, and acknowledged/recorded times;
- `SUPPRESSED` with a fixed stale/source/configuration reason and no Slack message timestamp; or
- `DEAD` with `AMBIGUOUS_UNRESOLVED` or a fixed permanent transport reason and no claim of
  delivery.

Unique constraints cover `intent_id` and any delivered `(slack_workspace_id, channel_id,
message_ts)`. Deferred guards require receipt identity/digest to equal its intent and attempt.
`DELIVERED`, `SUPPRESSED`, and `DEAD` are mutually exclusive and terminal.

## Transaction and crash-window protocol

### Application commit before external Slack

1. Lock and terminalize/materialize the authoritative job outcome.
2. Revalidate automatic lineage, canonical source, current version, and success/failure authority.
3. Render and seal the message, append or replay the intent, and enqueue
   `SLACK_GENERATION_NOTIFICATION_DELIVERY` with idempotency key
   `slack-generation-notification:{intent_id}`.
4. Commit all application rows, the intent, and enqueue together.
5. Only a later worker transaction may authorize an external Slack call.

A crash before step 4 exposes neither a terminal application effect nor a notification. A crash
after step 4 leaves a durable intent/job for replay. Calling Slack inside the terminalization,
apply, or suggestion transaction is forbidden.

### Worker write protocol

1. Claim the background job with the existing worker ID/attempt CAS.
2. Lock the intent. If a terminal receipt exists, return a successful no-op. Revalidate current
   source, workspace, allowlist, and destination; append a suppression receipt when required.
3. If an unresolved earlier `POST` permit exists, prepare `RECONCILE`; otherwise append/commit one
   `POST` permit and its exact request digest.
4. Release the database transaction, then perform exactly the permitted external operation.
5. Append the sanitized observation and terminal receipt or schedule in one new transaction.
6. Mark the background job successful only after that transaction commits. Attempt-CAS loss makes
   the worker stop; it never grants another external write.

Holding a PostgreSQL transaction open during the HTTP call is forbidden. Duplicate enqueues and
workers race on the same locked intent; only one can append the first `POST` permit. A loser sees
the permit/receipt and reconciles or exits.

### Slack acknowledgement followed by local commit failure

Slack `ok: true` returns the destination channel and message `ts`, but a process or database
failure can lose the local receipt after Slack accepted the message. On the next delivery the
persisted unresolved `POST` permit forbids retransmission. The worker reads the canonical thread
and searches for the exact client message UUID and metadata notification ID, then verifies the
root timestamp and frozen message digest. An exact match creates a
`THREAD_RECONCILIATION` receipt; conflicting/duplicate matches are `DEAD` security/integrity
failures and never select one arbitrarily.

Slack documents that successful `chat.postMessage` responses contain `channel`, `ts`, and the
server-parsed message object, and warns that some `fatal_error`/`internal_error` responses may
follow a successful side effect. Those responses are therefore ambiguous, not definitive
failures. See the official
[`chat.postMessage` response and error contract](https://docs.slack.dev/reference/methods/chat.postmessage).

## Exact-once observable outcome over at-least-once workers

The guarantee is:

```text
for one intent: at most one Slack reply + exactly one local terminal receipt
```

When Slack acknowledgement or exact reconciliation proves the message, the local outcome is
`DELIVERED`. When safety cannot prove delivery or non-delivery, the system chooses
`DEAD/AMBIGUOUS_UNRESOLVED` and does not blindly retransmit. This fail-safe choice is required:
without a provider-committed idempotency guarantee, it is impossible to promise both eventual
delivery and zero duplicates across an external side effect/local commit crash.

The deterministic `client_msg_id` and message metadata are defense-in-depth correlation keys.
They are not treated as the sole proof of Slack idempotency because Slack's public method contract
does not promise an application-controlled exactly-once transaction. Exact thread reconciliation
or the original `ok: true` acknowledgement is required for `DELIVERED`.

An ambiguous write is reconciled with the existing exponential schedule
`min(3600 seconds, 5 * 2^(attempt - 1))` for at most 24 hours. A not-found read inside that horizon
does not prove absence and never authorizes another POST. At the horizon or the final durable
worker attempt it becomes `AMBIGUOUS_UNRESOLVED`; final rate-limit exhaustion becomes the fixed
`RATE_LIMIT_EXHAUSTED` terminal reason. Operations may surface an explicit later repair workflow, but T06 has no
force-post or receipt-edit escape hatch.

## Slack transport request and metadata

The write transport adds one narrow operation; the ingestion methods remain unchanged. It sends
JSON to `chat.postMessage` with exactly:

```json
{
  "channel": "<canonical channel ID>",
  "thread_ts": "<canonical root message ts>",
  "text": "<frozen Korean text>",
  "client_msg_id": "<deterministic UUIDv5>",
  "reply_broadcast": false,
  "unfurl_links": false,
  "unfurl_media": false,
  "metadata": {
    "event_type": "tc_generation_terminal_notification_v1",
    "event_payload": {
      "notification_id": "<opaque intent ID>",
      "generation_job_id": "<opaque terminal job ID>",
      "outcome": "SUCCESS_APPLIED|SUCCESS_SUGGESTIONS|FAILURE",
      "contract_version": "1.0.0"
    }
  }
}
```

Metadata is visible to workspace members, so it contains no local tenant UUID, error detail,
source content, case content, digest, link, token, or credential. Slack supports metadata with
`event_type` and `event_payload`, and recommends app namespacing; see
[Using message metadata](https://docs.slack.dev/messaging/message-metadata/) and
[Designing metadata event schema](https://docs.slack.dev/messaging/message-metadata/designing-metadata-event-schema/).
The app manifest must register the matching metadata shape when Slack requires validation.

The bot token needs `chat:write`, and the app must be a member of every allowlisted public
channel. `chat:write.public`, customized authorship, Incoming Webhooks, broadcast replies, private
channel scopes, DMs, link/media unfurls, blocks, and attachments are excluded.

## Retry, rate limit, and ambiguity classification

The HTTP write method performs no hidden retry. Retry ownership belongs to the durable worker:

| Observation | Classification and next action |
|---|---|
| `ok: true` with exact channel and valid `ts` | append acknowledgement and `DELIVERED` receipt |
| HTTP 429 / `ratelimited` with `Retry-After` | definitive no-delivery; wait at least `max(Retry-After, exponential backoff)` and prepare a new POST attempt |
| documented auth/scope/membership/read-only/locked/archived/invalid-thread rejection | definitive permanent failure; sanitized `DEAD`, no fallback |
| locally proven request not sent | definitive no-delivery; exponential retry |
| connect/read timeout after the request may have left the process | ambiguous; reconcile only |
| HTTP 5xx, `fatal_error`, `internal_error`, `service_unavailable`, `request_timeout` | ambiguous; reconcile only |
| HTTP 200 invalid JSON, `ok` missing, malformed ack, mismatched channel/ts | ambiguous or integrity failure; never retransmit blindly |

Posting is serialized per `(slack_workspace_id, channel_id)` and never earlier than the durable
channel next-allowed time. Slack's published guidance generally allows one message per second per
channel and requires callers to honor rate limits; see
[Slack Web API rate limits](https://docs.slack.dev/apis/web-api/rate-limits/).

The delivery job uses 12 worker attempts. `Retry-After` is never shortened by the one-hour local
backoff cap. Provider/network exception text is reduced to a fixed classification and bounded
provider code before persistence or logs.

## Deterministic Korean message templates

Rendering happens once at intent capture. LF line endings and the exact bullet/middle-dot text
below are part of template version `automatic-generation-slack-reply/1.0.0`. Counts are base-10
non-negative integers. No Slack/user/provider/source text is interpolated.

### Ordinary applied success

```text
✅ 자동 테스트케이스 생성이 완료되었습니다.
• 새 테스트케이스: {createdCount}건
• 기존 테스트케이스 재사용(변경 없음): {reusedCount}건
• 상위 일감: {새로 등록|기존 일감 재사용(변경 없음)}
{optionalLinkLine}
```

`optionalLinkLine`, when present, is exactly:

```text
• 결과 보기: <{absoluteHttpsUrl}|QA 도구에서 보기>
```

The rendered message has no trailing LF. `createdCount + reusedCount` must equal the immutable
apply receipt's generated count.

### Suggestion success

For a positive suggestion count:

```text
✅ 자동 테스트케이스 변경 제안이 준비되었습니다.
• 변경 제안: {suggestionCount}건
• 자동 덮어쓰기: 0건
{optionalLinkLine}
```

For zero materialized proposals, only the second line differs:

```text
• 변경 제안: 0건(변경 없음)
```

The message never says a case was created, reused, updated, or applied.

### Failure

```text
⚠️ 자동 테스트케이스 생성에 실패했습니다.
• 생성 시도: {attemptNumber}차
• 오류 코드: {fixedKoreanLabel} ({GenerationErrorCode})
• 재시도 가능: {가능|불가}
{optionalLinkLine}
```

`fixedKoreanLabel` is a total versioned mapping from the released Generation error enum. Unknown,
blank, legacy, or structurally incomplete errors map to `안전하게 표시할 수 없는 오류
(GENERATION_PROVIDER_ERROR)` and `불가`; raw `GenerationJob.error` is never read by the renderer.
The stored/generated text must not contain provider response body, prompt/context content,
credential/token/authorization material, endpoint/host/query, stack trace, exception class,
Slack source body, or user-supplied retry reason.

## Permalink and product UI link policy

Slack source permalinks are evidence only. They do not route the reply, are not echoed back into
the same thread, and cannot replace channel/root identity. T06 does not call `chat.getPermalink`
or synthesize a Slack URL from workspace/channel/timestamp.

A product link is included only when a new locally validated `public_web_base_url` existed at
intent capture, uses HTTPS, has no credentials/query/fragment, and the server builds the fixed
opaque-ID generation history path. Localhost, loopback, `trycloudflare.com`, the public Slack
ingress URL, API URLs, and caller-supplied URLs are forbidden. If the setting is absent, the link
line is omitted and the frozen intent records `linkMode = OMITTED`; later configuration does not
rewrite old intents.

## Disabled/setup-required and live gate

Terminal application commits still record/enqueue a valid intent when Slack credentials are not
configured. The generic worker must not claim `SLACK_GENERATION_NOTIFICATION_DELIVERY` unless the
Slack runtime is locally `CONFIGURED`, the token workspace matches the source, the channel is
currently allowlisted, `chat:write` is present in the installed manifest, and the delivery
transport is injected. The credential-free and notification-write defaults are both disabled;
notification delivery requires an explicit opt-in after those checks. A queued unclaimed delivery has zero attempts and derived
`SETUP_REQUIRED`; it is not `DELIVERED`, `DEAD`, or evidence of a live credential check.

The repository manifest grants only `channels:history` and invited-channel `chat:write`; it omits
`chat:write.public`. No real Slack App, token or channel membership was supplied for this task.
Therefore all focused delivery tests use a deterministic fake, make zero live Slack calls, and
do not claim an installed or credential-verified reply integration.

## Focused production verification

The gates added by T06 are:

- `packages/contracts/src/phase-e-automatic-generation-slack-reply-contracts.test.ts`:
  strict envelope/template/post/receipt shapes, safe metadata, Korean vocabulary, and failure
  sanitization controls. This contract-only suite is green.
- `apps/worker/tests/test_phase_e_automatic_generation_slack_reply.py`: expected notification
  service, write/reconciliation transport, configured-only worker handler, public Web URL setting,
  and Slack manifest write/metadata capability. This suite is green.
- `apps/api/tests/test_phase_e_automatic_generation_slack_reply_postgresql.py`: expected
  append-only intent/attempt/observation/receipt tables, uniqueness, immutable/link triggers,
  failure ordering and atomic rollback. This suite is green at migration head `20260828_0025`.

Additional PostgreSQL apply/suggestion tests cover ack loss, pre-provider crash, final-attempt
reconciliation, durable rate-limit exhaustion, stale/self suppression and raw SQL proof forgery.
Import/collection errors, syntax errors, missing dependencies, unavailable PostgreSQL, live Slack
calls, or failures in T01–T05 controls are not acceptable evidence.

## Production implementation order

Implement in this order:

1. add the next migration and ORM mappings for the four immutable notification ledgers and their
   deferred canonical-source/job/materialization guards;
2. add a transaction-scoped notification capture service and call it from automatic failure,
   committed ordinary apply, and completed suggestion materialization seams;
3. add pure template/seal/error-redaction code matching the versioned contract;
4. extend the Slack protocol, HTTP adapter, and deterministic fake with one no-hidden-retry thread
   post plus exact metadata/client-ID reconciliation;
5. register the delivery worker only for a configured runtime, add `public_web_base_url`, and add
   the manifest `chat:write`/metadata shape; then run deterministic crash/race tests; and
6. perform a separate credentialed test-workspace smoke, recording only opaque IDs/status/times.

## Explicit exclusions

- Generation provider selection/execution, credentials, model, price, quota, or prompt changes.
- T03 impact rules, T05 suggestion decisions, automatic apply, or retry creation/management.
- Manual generation notifications.
- Slack message update/delete, channel broadcast, top-level fallback, DM/admin escalation, or
  private-channel expansion.
- Public notification-status API or new Web UI; existing integration/job views may expose the
  derived ledger later.
- A claim that live Slack credentials, scopes, channel membership, metadata schema, or delivery
  have been verified.
