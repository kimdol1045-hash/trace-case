# Phase F Sprint F1 observability foundation

## Outcome and scope

`PF-SF1-T05` now has a dependency-free production code foundation for API, worker, PostgreSQL,
Slack, private object storage, outbox, knowledge, and generation telemetry. The implementation adds
an internal Prometheus surface, readiness/degradation semantics, an atomic worker textfile surface,
bounded alert rules, threshold tests, and this operator runbook.

This is configuration and code, not evidence that an Alertmanager receiver, paging integration, or
real on-call acknowledgement exists. Receiver wiring and a controlled delivery exercise remain a
deployment gate.

## Existing-path inventory

| Component | Existing operational path | Added observation |
| --- | --- | --- |
| API | `GET /api/health` returned process liveness; request tracing used a validated opaque trace ID | `GET /api/ready`, `GET /api/metrics`, bounded request count and duration |
| Database | startup validated PostgreSQL 16, pgvector, required tables, and embedding column shape | scrape-time `SELECT 1`, readiness fail-closed, `tc_database_up` |
| Worker | queue claims used `SKIP LOCKED`; stale leases used `locked_at`/`heartbeat_at`; per-job heartbeat preserved ownership | poll/job/heartbeat metrics and atomic textfile publication |
| Slack | runtime exposed disabled/setup-required/configured; ingress and sync state lived in `slack_events`, `slack_threads`, and background jobs | bounded Slack ledger counts, request component metrics, failed-ingress alert |
| R2/MinIO | runtime exposed adapter/scanner readiness, request-class counters, metadata bytes, and configured budget warnings | readiness gauge, class A/B counters, byte and budget-ratio gauges |
| Outbox | durable `READY/PROCESSING/PUBLISHED/FAILED/DEAD` ledger with retry availability and knowledge checkpoints | state counts, oldest runnable age, dead/lag alerts |
| Knowledge | hybrid search already returned typed `LIGHTRAG_UNAVAILABLE` and `VECTOR_UNAVAILABLE` degradation | degraded-response counter and projection checkpoint counter |
| Generation | durable execution schedules, provider permits/observations, retry exhaustions, materialization schedules, and safe error codes | current schedules/lag/unknown outcome, durable provider and exhaustion counters, materialization age |

No new network probe sends Slack, R2, Ollama, LightRAG, or generation-provider traffic. Local
configuration readiness is not described as external provider health.

## Surfaces and semantics

### Liveness

`GET /api/health` remains backward-compatible and returns `200` while the API process can answer.
It does not query dependencies and must be used only as a liveness/restart signal.

### Readiness

`GET /api/ready` is excluded from public OpenAPI generation and returns only stable component
states and fixed reason codes.

| Condition | HTTP | status | Meaning |
| --- | ---: | --- | --- |
| PostgreSQL query succeeds; all enabled optional dependencies are locally configured | 200 | `ready` | database-backed traffic may be sent |
| PostgreSQL succeeds; an enabled Slack/storage/knowledge/generation dependency is unavailable | 200 | `degraded` | core ledger remains usable; affected capability must be isolated/triaged |
| PostgreSQL is unavailable or no session factory exists | 503 | `not_ready` | remove instance from database-backed traffic |
| Optional dependency is intentionally disabled | 200 | `ready` with dependency `disabled` | no false degradation/alert |

The readiness response never contains exception messages, credentials, provider URLs, bucket
names, object keys, source text, actor/reviewer data, or identifiers.

### Metrics

`GET /api/metrics` is excluded from public OpenAPI generation and returns Prometheus text format
with `Cache-Control: no-store`. Scrape it only through the internal network. The database collector
uses aggregate counts and ages; if any observation query fails, it emits `tc_database_up 0` without
the database exception.

The worker has no HTTP dependency. When `TC_WORKER_METRICS_PATH` is set to an absolute `.prom`
path, it atomically publishes process metrics for node-exporter's textfile collector after startup,
every poll, and graceful shutdown. A unique collector target/directory is required per worker;
worker/host/pod IDs are deliberately not labels.

Primary stable families are:

- API/database: `tc_api_up`, `tc_api_ready`, `tc_api_degraded`, `tc_database_up`,
  `tc_dependency_ready`, `tc_api_http_requests_total`,
  `tc_api_http_request_duration_seconds`.
- Worker/queue: `tc_worker_up`, `tc_worker_last_poll_timestamp_seconds`,
  `tc_worker_last_success_timestamp_seconds`, `tc_worker_poll_total`, `tc_worker_jobs_total`,
  `tc_worker_heartbeat_failures_total`, `tc_background_jobs`,
  `tc_background_job_oldest_ready_age_seconds`.
- Slack/outbox/knowledge: `tc_slack_events`, `tc_outbox_events`,
  `tc_outbox_oldest_ready_age_seconds`, `tc_knowledge_projection_events_total`,
  `tc_knowledge_degraded_responses_total`.
- Object storage: `tc_object_storage_ready`, `tc_object_storage_bytes`,
  `tc_object_storage_requests_total`, `tc_object_storage_budget_ratio`.
- Generation: `tc_generation_jobs`, `tc_generation_execution_schedules`,
  `generation_execution_schedule_lag_seconds`, `generation_execution_outcome_unknown`,
  `generation_execution_outcome_unknown_total`, `generation_execution_claim_total`,
  `generation_execution_lease_reclaim_total`, `generation_provider_attempt_total`,
  `generation_job_retry_total`, `generation_retry_exhaustion_total`,
  `generation_operator_retry_total`, `generation_failure_queue_actionable`,
  `tc_generation_materialization_schedules`, `generation_materialization_total`,
  `tc_generation_materialization_oldest_nonterminal_age_seconds`,
  `generation_terminal_notification_capture_total`, and
  `generation_terminal_notification_capture_gap`.

`generation_execution_schedule_lag_seconds` is the maximum current READY age, not a historical p95.
Phase F load work can add a durable lag histogram after validating scrape/query cost.

## Cardinality and privacy contract

The registry rejects undeclared metric names, missing/extra labels, and non-allowlisted label values.
Allowed dimensions are fixed component, HTTP method/status class, bounded lifecycle state, billing
class, materialization mode/finality, typed degradation reason, and enumerated safe generation error
code.

Never add a label for a request path/route parameter, URL, hostname, tenant/workspace/channel/thread,
job/root/event/attachment ID, object key/bucket, trace/idempotency key, provider/model, actor/reviewer,
condition/error text, raw Slack text, file name, or reviewer token/hash. Do not put these values in
Prometheus annotations either. Investigation should pivot from a time window and safe status/code to
the authorized ledger UI or restricted database query; raw data must not be copied into alerts.

## Alert thresholds

The repository rules in `deploy/observability/prometheus-rules.yml` implement these initial bounds:

| Signal | Threshold | Hold | Severity |
| --- | --- | --- | --- |
| API scrape, database, or readiness down | zero/down | 2 minutes | critical |
| enabled dependency degraded | `tc_api_degraded == 1` | 10 minutes | warning |
| API 5xx ratio | over 5% | 10 minutes | warning |
| API p95 duration | over 2 seconds | 10 minutes | warning |
| worker poll age | over 120 seconds or series absent/stopped | 2 minutes | critical |
| dead background job / dead outbox | greater than zero | 5 minutes | warning / critical |
| oldest runnable outbox event | over 300 seconds | 15 minutes | warning |
| failed Slack ingress event | greater than zero | 10 minutes | warning |
| knowledge degraded response ratio | over 10% | 15 minutes | warning |
| configured storage budget | over 85% / 95% | 15 / 5 minutes | warning / critical |
| generation provider outcome unknown | greater than zero | immediate evaluation | critical |
| READY generation schedule | over 300 seconds | 15 minutes | warning |
| generation exhaustion/success ratio | over 20% with at least one new exhaustion | 5 minutes | warning |
| non-terminal materialization age | over 600 seconds | 5 minutes | critical |
| terminal failure without notification intent | greater than zero | 5 minutes | critical |

The pure `breached_thresholds` boundary tests use strict `>` comparison at the same numeric values.
Prometheus `for` durations deliberately prevent a boundary equality from firing.

## Runbook

Start every response by recording alert start time, environment, safe alert name, and current metric
values. Do not paste raw payloads or secrets into incident chat/tickets. Confirm `/api/health`, then
`/api/ready`, then the relevant metrics target. Silence or route an alert only through the deployed
monitoring ownership process; this repository does not define that receiver.

### TCApiScrapeDown or TCPostgreSQLUnavailable

1. Distinguish API process/network loss from `tc_database_up 0` using liveness and scrape `up`.
2. For database loss, check connection saturation, PostgreSQL readiness, migration version, and
   pgvector/schema capability without printing the database URL.
3. Remove the instance from ready traffic; do not loop restarts if PostgreSQL remains unavailable.
4. After recovery, confirm readiness and that queue/outbox ages decline.

### TC API degraded

Read the fixed `degradationReasons` and matching `tc_dependency_ready{dependency=...}` series.
Keep database-backed core traffic if readiness is `200 degraded`, isolate the affected capability,
and use the component section below. A configured state is not proof of live external connectivity.

### TCWorkerPollStale

1. Verify node-exporter and the worker textfile independently; a stale file can outlive a crash.
2. Check worker process state and database reachability without exposing worker IDs in alert data.
3. Inspect aggregate `tc_background_jobs` and oldest-ready age. Preserve lease ownership rules; do
   not manually rewrite `locked_by`, attempts, or heartbeats.
4. Restart through the deployment controller and confirm poll time advances before replay decisions.

### TCBackgroundJobDead or TCOutboxDead

Use bounded component/status counts to choose the authorized ledger. Inspect only safe error codes
first. Replay through the existing idempotent job/outbox workflow; never mutate attempts/status or
delete authority rows by hand. For generation jobs, check the domain schedule and permits before any
generic replay.

### TCOutboxLagHigh

Compare outbox READY/FAILED/PROCESSING counts with worker poll age and knowledge checkpoint growth.
Check consumer capacity and database locks. Keep the source-of-record transaction committed; a
LightRAG or embedding failure must not roll back source data. Confirm age trends down after recovery.

### TCSlackIngressFailed

Check configured mode and allowlist, then bounded Slack ledger status and Slack job component counts.
For HTTP mode verify signature-time synchronization and ingress routing; for Socket Mode verify the
outbound connection/reconnect path. Respect rate-limit retry boundaries and do not paste message text,
channel/thread IDs, permalinks, tokens, or raw responses into the incident.

### TCKnowledgeDegradationHigh

Split by typed reason. `LIGHTRAG_UNAVAILABLE` should retain typed graph/vector fallback;
`VECTOR_UNAVAILABLE` should retain other supported signals. Check Ollama/LightRAG locally, confirm
outbox/checkpoint progress, and use the documented reinitialize/rebuild path rather than deleting the
source ledger.

### TCObjectStorageUnavailable or budget alerts

For unavailability, verify locally that both the private adapter and fail-closed malware scanner are
configured; do not log endpoint, bucket, credentials, object keys, or signed URLs. For budget alerts,
compare process-local request counters and database-metadata bytes with durable R2 analytics/billing.
The local estimate alone is not a billing authority. At 95%, stop nonessential bulk operations and
follow the owner-approved quota/cost decision.

### TCGenerationProviderOutcomeUnknown

Disable new automatic provider traffic for the affected rollout and do not blindly replay the generic
job. An unknown result means a permit may have reached the provider without a safely committed
observation. Use only the safe failure history and provider-side idempotency/digest evidence, then
follow operator review. Never paste provider requests/results, endpoint URLs, credentials, source
context, or retry conditions into the alert.

### TCGenerationScheduleLagHigh

Compare worker freshness, generation background-job state, execution schedule state, `not_before`,
and lease expiry through authorized operational tooling. Preserve CAS/lease ownership. If the worker
is healthy, check provider rate limiting and generation-specific budgets before scaling consumers.

### TCGenerationRetryExhaustionHigh

Split only by allowlisted safe error code and finality. Compare new durable provider observations with
new exhaustion receipts. Configuration/auth errors require configuration correction, rate limits
require the committed retry boundary, and review-required finality requires a human decision—not an
automatic replay.

### TCGenerationMaterializationStuck

Verify the generation job succeeded, the materialization schedule is non-terminal, and its generic
delivery/lease is current. Check stale/released/deleted source policy before retrying. Do not apply
direct database state changes or overwrite human-owned cases.

### TCGenerationTerminalNotificationGap

Compare the durable terminal exhaustion receipt with the expected notification intent inside one
authorized transaction history. Do not fabricate a provider receipt or post directly from an alert.
Recover through the idempotent notification-intent/delivery workflow after confirming the source is
still eligible, and keep Slack destination identifiers and message text out of incident data.

### TC API SLO alerts

Split API metrics by the fixed `component` label and compare latency/error timing with database,
worker, and dependency signals. Use application traces only in the restricted trace system; trace IDs
are intentionally not Prometheus labels. Validate capacity before changing alert thresholds.

## Deployment and validation

1. Keep `/api/metrics` and `/api/ready` on the internal listener/network. The public Slack ingress
   must remain restricted to its existing event path.
2. Install the rule file and validate it with the exact production Prometheus `promtool` version.
3. Configure a unique absolute worker textfile path and node-exporter target per worker.
4. Add environment/external labels in Prometheus scrape configuration, not application-controlled
   request data.
5. Wire the real receiver in deployment-owned configuration, run one controlled warning and one
   resolved notification, and record acknowledgement. Until then, alert delivery is `NOT RUN`.
6. Run focused code gates:

   ```text
   .venv/bin/python -m pytest -q backend/tests/unit/test_observability.py apps/api/tests/test_api.py apps/worker/tests/test_worker.py
   .venv/bin/python -m ruff check backend apps
   .venv/bin/python -m mypy backend/src apps/api/src apps/worker/src
   ```

Local evidence recorded on 2026-09-01:

- focused observability/API/worker suite: `40 passed`;
- complete non-PostgreSQL Python unit gate: `447 passed, 109 deselected`;
- Python lint: all checks passed;
- Python typecheck: 136 source files passed;
- OpenAPI/client drift check: up to date, with readiness/metrics excluded from public OpenAPI;
- configured local PostgreSQL collector smoke: `tc_database_up 1` and all aggregate queries passed;
- alert rule YAML load and unique alert-name check: passed.

`promtool` was not installed in this workspace, so exact production-version `promtool check rules`
remains a deployment step. No Alertmanager receiver delivery was attempted.

## Known limits

- External Slack, R2, Ollama, LightRAG, and generation-provider reachability is not synchronously
  probed by readiness or scrape requests.
- Object-storage request counts are process-local and reset on API restart; active bytes come from
  database metadata and must be reconciled with durable provider analytics.
- Generation READY lag is a current maximum, not p95. Real load thresholds remain `PF-SF1-T06`.
- Textfile freshness detects a crashed/stalled worker, but unique per-worker scrape placement is a
  deployment responsibility.
- Dashboard construction, receiver secrets/routes, controlled notification delivery, and on-call
  acknowledgement are not performed or claimed by this implementation.
