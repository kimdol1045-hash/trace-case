# PE-SE2-T10 root-to-Slack production E2E contract

## Scope and boundary

`PE-SE2-T10` closes the local production-composition seam from a signed Slack Events API
root and reply through the existing T01–T09 ledgers to one reply in the original root thread.
The executable authority is
[`test_phase_e_root_to_slack_e2e_postgresql.py`](../../apps/api/tests/test_phase_e_root_to_slack_e2e_postgresql.py)
using
[`signed-thread-v1.json`](../../evals/fixtures/automatic-generation-root-to-slack-e2e/signed-thread-v1.json).
It uses a disposable PostgreSQL database, production API/worker handlers, deterministic injected
provider adapters and the production Slack delivery/reconciliation service. It performs no
external network request.

T11 is not part of this contract. The E2E deliberately materializes a new five-case batch as
`ACTIVE`/`SLACK_AI`; the released/human-owned target rule is closed separately by the
[T11 production E2E contract](./automatic-generation-human-release-to-suggestion-e2e-contract.md)
on top of the T04/T05 contracts and regressions.

## Acceptance matrix

| Seam | Stimulus | Required production path and assertion |
|---|---|---|
| Signed ingress | Canonical root and thread reply, each signed with the fixture secret | `POST /integrations/slack/events` verifies the exact raw body and enqueues `SLACK_EVENT_PROCESS`; the duplicate root returns `DUPLICATE_EVENT_ID`. |
| Root identity | Root has no caller-supplied permalink | Worker resolves the canonical root with `chat.getPermalink` outside the database transaction; the stored work item and sealed generation input retain the fixture permalink. |
| Thread ledger | Root then reply jobs run | One `SlackWorkItem` and exactly two current `SlackMessageRevision` rows exist at source version 2. |
| Quiet/parse | The test advances only the recorded quiet deadline/job | Production `SLACK_QUIET_WINDOW` and `WORK_ITEM_PARSE` handlers store a `COMPLETE` four-field parse and one current projection outbox handoff. |
| Ontology/embedding | Explicit deterministic extraction and 1024-dimensional embedding adapters are injected | Production `KnowledgeOutboxConsumer` validates grounded evidence and persists two nodes, four evidence rows, two embeddings and one projection receipt; extraction and embedding each run once. |
| Sealed generation | Automatic generation is explicitly enabled and a deterministic runner is injected | Production execute handler consumes the sealed Input `1.0.0`, creates one schedule/permit/observation and finishes one automatic root `SUCCEEDED`; reconstruction is `AVAILABLE`, and the runner runs once. |
| Materialization | The validated result contains five new cases | Production materialization creates one apply, five outcomes, five `ACTIVE` cases and five versions whose source is exactly `SLACK_AI`. No human-owned row is updated. |
| Slack delivery | Fake Slack accepts `chat.postMessage` but loses its acknowledgement once | Production notification delivery records one frozen intent, performs exactly one post, reconciles the provider message in the original root thread, and stores one `DELIVERED` receipt with `THREAD_RECONCILIATION` proof. |
| Event replay | Re-submit the same signed root and reply event IDs | Only the original two `SlackEvent` rows exist; work item and source revision cardinality do not change. |
| Projection/job replay | Invoke the same automatic projection payload and notification job claim again | Node/evidence/embedding/projection, generation root/schedule/permit/observation, case/version/apply/outcome and notification intent/receipt cardinalities are byte-for-byte unchanged. |
| Self-reply exclusion | Deliver the accepted bot reply back through signed Events API ingress | Ingress returns `IGNORED`; source version, source revision count and generation root count remain unchanged. |
| Stale source suppression | Change the source after intent capture in the released T06 regression | Delivery closes without a Slack call; T10 must continue to pass `test_source_change_after_capture_suppresses_delivery_without_slack_call`. |
| Revoked private access | Reclassify the allowlist row as private without explicit inclusion before sync or delivery | Thread sync fails before any provider read, while terminal notification records `SUPPRESSED_CONFIGURATION`; both transports observe zero calls. |
| Default safety | Start composition with the new opt-in false | No extraction or embedding transport is constructed, `SLACK_AUTOMATIC_GENERATION` is not claimable, and external network calls remain zero. Partial manual provider injection is rejected. |

The terminal E2E cardinality is fixed at 1 work item, 2 source revisions, 2 knowledge nodes,
4 evidence rows, 2 embeddings, 1 projection receipt, 1 generation root, 1 execution schedule,
1 provider permit, 1 provider observation, 1 apply, 5 outcomes, 5 cases, 5 case versions,
1 notification intent and 1 notification receipt.

## Production composition and configuration

`apps/worker/src/tc_worker/main.py::build_worker` is the owning composition root. It now builds
`KnowledgeOutboxConsumer` when and only when
`TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED=true`, using a structured Ollama extraction adapter and
the existing BGE-M3 embedding adapter. Tests or alternate composition roots may activate the same
path only by explicitly injecting both provider protocols. Slack Events processing obtains the
root permalink through the configured `SlackWebApiTransport`; no URL is invented from untrusted
event text.

The three independent write-capable boundaries stay separately disabled by default:

- knowledge projection: `TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED=false`;
- credentialed generation: `TC_AUTOMATIC_GENERATION_ENABLED=false`;
- Slack terminal reply: `TC_SLACK_GENERATION_NOTIFICATIONS_ENABLED=false`.

Enabling one does not enable either of the others. With all defaults, the worker constructs none
of their provider transports and makes zero external requests.

The database allowlist API is the operational ingestion authority. The deployment environment's
`TC_SLACK_ALLOWLIST_CHANNEL_IDS` startup check does not seed or overwrite database rows; a missing,
disabled, or revoked database row therefore fails closed at ingress, thread sync and notification
delivery. Operators must reconcile the desired list through
`PUT /api/integrations/slack/allowlist` before enabling event traffic.

## Audited follow-up boundary

T10 adds the narrow pre-network private-access recheck to thread sync and notification delivery.
It does not rewrite the existing append-only Slack event/revision ledger to redact historical raw
payload JSON. Designing field-level minimization, retention, erasure and migration for already
stored payloads is a Phase F privacy/data-governance task; until then, the allowlist remains the
fail-closed collection boundary and deployments must not allow channels containing data outside
the approved retention policy.

## Required local and live gates

The deterministic release gate is the fixture-backed disposable-PostgreSQL test plus the focused
ack-loss, self-reply and stale-source regressions. A real credential gate remains separate and may
not be replaced by this fixture: an approved Slack workspace/channel and bot with permalink,
history/replies and `chat:write` access; a verified signed event; reachable Ollama extraction and
BGE-M3 models; an approved credentialed generation provider/version set; and observation of the
single reply in the real root thread. T08/T09 operational golden approval and provider live
attestation remain their own protected release prerequisites rather than being fabricated by T10.
