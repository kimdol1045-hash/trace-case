# PE-SE2-T01 automatic generation orchestration contract

## Status and scope

This is the released implementation boundary for `PE-SE2-T01`. The focused PostgreSQL
contract and the separate integration/release-gate task are green; the wider evidence and
residual live gates are recorded in the
[Sprint E2 foundation report](./sprint-e2-foundation-report.md).

The smallest accepted outcome is one sealed `generation_jobs` row in `QUEUED` state. This
task does not execute the Generation API, apply a result to the Library, post to Slack, or
choose what to do after a human edits a generated case.

## Existing seams used by the boundary

The implementation must compose the existing seams rather than introduce a second source
or context model:

| Stage | Existing authority |
|---|---|
| Quiet-window gate | `SlackWorkerRuntime.finish_quiet_window` plus the locked `SlackWorkItem.source_version` and `quiet_due_at` |
| Four-field parse | `SlackWorkerRuntime.parse_work_item` / `SlackRepository.store_parse_result` |
| Immutable extraction input | `KnowledgeRepository.load_slack_source_bundle` |
| Validated extraction | `SlackKnowledgeExtractor` and its exact-revision evidence validator/classifier |
| Authoritative projection | `KnowledgeOutboxConsumer` / `KnowledgeRepository`, `KnowledgeProjectionEvent`, PostgreSQL graph, pgvector; LightRAG remains auxiliary |
| Grounded retrieval and capture | `GenerationRepository.build_context` and `create_next_job_with_context` |
| Automatic result | A root `GenerationJob` with a sealed context/input and status `QUEUED` |

`GenerationRepository.build_context` is the retrieval boundary for this task. It traverses
the current `ACTIVE` typed graph and admits only grounded current ledger resources. The
orchestrator must not call the public search API and then reconstruct trusted context from a
page of response data, and it must not treat a LightRAG result as authoritative.

## Implemented composition

The worker now composes the repository seams at explicit queue and transaction boundaries:

- `finish_quiet_window` fences the current quiet job ID and claimed attempt before it
  enqueues `WORK_ITEM_PARSE`;
- `parse_work_item` stores the parse result/outbox and idempotently enqueues the explicit
  `SLACK_AUTOMATIC_GENERATION` handoff in the same transaction;
- `KnowledgeOutboxConsumer.process_automatic_generation` locks and revalidates the exact
  work item/version, reuses or creates its non-stale projection checkpoint, and atomically
  seals the grounded job/context/input plus audit and checkpoint receipt;
- `build_worker` registers the handoff only when an explicitly injected knowledge consumer
  is present. Without those dependencies the filtered worker leaves the handoff queued for a
  configured worker instead of consuming its retry budget, and no generation provider is
  selected or called;
- generic worker claims are filtered to registered handler types, claims are heartbeated,
  completion is fenced by attempt, malformed payloads dead-letter immediately, and
  transient/not-ready failures keep the bounded retry policy.
- `GenerationRepository.create_next_job_with_context` already provides the required
  provider-free sealed root `QUEUED` job operation.

T01 remains a worker composition/transaction change, not a new wire contract, generation
provider implementation, retrieval algorithm, or schema redesign.

## One-delivery state transition

For a quiet-window delivery carrying `{workItemId, sourceVersion}`, the worker performs the
following transition:

1. Lock the referenced `SlackWorkItem`.
2. Return successfully with no new effect when `sourceVersion` is not current.
3. Retry without parsing, projection, or generation when `quiet_due_at` is still in the
   future.
4. The existing `WORK_ITEM_PARSE` handler parses the latest non-deleted root/thread revisions
   for the same source version and stores `PARSED` or `NEEDS_REVIEW`. In that transaction it
   appends the existing `SLACK_PARSE_RESULT_STORED` outbox event and idempotently enqueues one
   `SLACK_AUTOMATIC_GENERATION` handoff carrying exactly `outboxEventId`, `workItemId`, and
   `sourceVersion`. Its queue idempotency key is
   `slack-automatic-generation:{outboxEventId}`. Missing structured fields remain explicit
   `null` values in the captured Generation Input; they do not permit invented facts.
5. The handoff handler calls a transaction-scoped
   `KnowledgeOutboxConsumer.process_automatic_generation` entrypoint with those three values.
   It locks and revalidates the current work item, processes the explicit `SLACK_WORK_ITEM`
   outbox event with `SlackKnowledgeExtractor`, persists the validated projection/evidence
   and embeddings, and ignores stale aggregate versions. The existing projection-only
   `process_event` behavior remains available and does not implicitly create generation jobs.
6. Call `GenerationRepository.create_next_job_with_context` against the just-projected
   authoritative graph. The repository must fail closed if the current Slack root,
   source-version match, active ontology, evidence, category candidates, or complete input
   seal cannot be proven.
7. Persist the generated job ID in the successful `KnowledgeProjectionEvent.result` as
   `generationJobId`. The row is the exact-delivery receipt used if the same projection
   event is replayed.
8. Commit projection/checkpoint/embedding/context/job changes together. The parse result,
   parse outbox event, and explicit handoff are the preceding idempotent transaction. Only
   after the projection/context transaction commits may the enclosing handoff job be marked
   successful.

The generated row must be a root attempt with:

- `status = QUEUED`;
- `provider = model = UNCONFIGURED`;
- current Slack `source_version` and the next serialized `generation_version`;
- a non-empty sealed `generation_contexts` set and matching context digest;
- a `SEALED` Generation Input `1.0.0` capture and matching RFC 8785 SHA-256 digest;
- no provider call, result payload, execution timestamp, apply receipt, TestCase write, or
  Slack write.

## Transaction and replay rule

The knowledge projection checkpoint and the queued generation job are one PostgreSQL commit.
If extraction, validation, embedding, projection, grounded retrieval, or input capture fails,
no partial checkpoint or generation job may commit. Existing worker retry/dead-letter behavior
then owns another attempt.

Replaying the exact same outbox aggregate/version must read `generationJobId` from the
existing projection checkpoint and return the same job without appending another one. This
is transport/checkpoint replay safety for T01, not the product deduplication promised by
`PE-SE2-T02`: T01 does not deduplicate distinct deliveries by canonical Slack source, decide
whether two different source versions are semantically equivalent, or prevent a separately
requested manual generation job. The subsequent source/version, DB uniqueness, receipt-fallback,
and manual-repeat decisions are fixed in the
[T02 deduplication contract](./automatic-generation-deduplication-contract.md).

A current, successful projection checkpoint can already exist without `generationJobId`
because projection-only maintenance is an independent supported operation. The automatic
entrypoint must lock that checkpoint, reuse its authoritative projection, create the sealed
job, and add the receipt once; it must not re-extract solely because the receipt is absent.
A stale checkpoint never authorizes job creation.

The current migrations already provide the required storage primitives: the Slack source and
quiet-window ledger (`20260821_0007`), JSONB projection checkpoint (`20260821_0008`), sealed
generation context/lifecycle (`20260826_0017` and `20260826_0018`), and sealed Generation Input
(`20260827_0020`). T01 therefore needs no schema migration if its replay receipt is stored in
`knowledge_projection_events.result`. Any new message-key/version uniqueness constraint belongs
to T02 and is out of scope here.

## Fail-closed cases

The worker must not create a generation job when any of these is true:

- the quiet delivery is stale or not yet due;
- the current Slack source has no live text or no root;
- extraction cites an unknown revision or a non-source excerpt;
- the projection checkpoint is stale for the current source version;
- the `WORK_ITEM` knowledge root is absent, non-`ACTIVE`, duplicated, or has a different
  `sourceVersion`;
- any selected active node/edge lacks immutable evidence;
- the complete Generation Input cannot be sealed.

A LightRAG outage alone is not a reason to weaken or skip PostgreSQL projection/context
grounding. Its existing auxiliary degraded behavior remains unchanged.

## Implemented T01 seams

1. The parse transaction resolves `SLACK_PARSE_RESULT_STORED` and enqueues the idempotent
   `SLACK_AUTOMATIC_GENERATION` job.
2. `process_automatic_generation` owns the atomic projection/checkpoint/context/input/job/audit
   transaction and validates a replay receipt before returning it.
3. `SlackWorkerRuntime` registers the handoff with an optional, explicitly injected
   `KnowledgeOutboxConsumer`; no Generation API runner is injected or invoked.
4. Focused tests cover the green path, exact replay, existing-checkpoint resume, stale quiet
   and source fencing, embedding rollback, supported-type claims, attempt fencing, permanent
   malformed failure, and setup-required behavior.

## Explicit exclusions

- Generation API/provider execution, credentials, endpoint selection, cost approval, and
  `RUNNING`/terminal execution transitions.
- A new knowledge-extraction or embedding provider transport; the T01 seam receives the
  existing interfaces and fails closed when they are unavailable.
- `PE-SE2-T02` Slack message-key/generation-version product deduplication.
- `PE-SE2-T03` change-impact analysis or selective regeneration.
- `PE-SE2-T04` human-overwrite and `generation_managed` semantics.
- Suggestions/apply, Slack replies, result persistence, and case creation.

## Focused verification

The synthetic PostgreSQL contracts are
`apps/api/tests/test_phase_e_automatic_generation_postgresql.py`. It drives the current quiet
gate and parser, requires the parse transaction to enqueue the explicit current-version
handoff, runs deterministic extraction/projection, and requires the projection commit to
contain exactly one sealed `QUEUED` generation job while leaving the TestCase ledger empty. It
then replays the same projection event and requires the stored `generationJobId` receipt to
return the same job.

Focused command:

```text
TC_TEST_DATABASE_URL=postgresql+psycopg://.../tc_test \
  node scripts/python.mjs -m pytest \
  apps/api/tests/test_phase_e_automatic_generation_postgresql.py -m postgres -q
```

On 2026-08-27 the release-gate focused automatic-generation command reports `8 passed`; the job repository
queue/fence command reports `7 passed`; and the worker/job/knowledge/generation unit slice
reports `25 passed`. The subsequent integration/release-gate task passed; its wider counts
and external skips are in the Sprint E2 foundation report.
The existing seam commands remain applicable:

```text
node scripts/python.mjs -m pytest \
  apps/worker/tests/test_worker.py \
  backend/tests/unit/test_jobs.py \
  backend/tests/unit/test_knowledge_extraction.py \
  backend/tests/unit/test_generation_repository.py -q
# 25 passed

TC_TEST_DATABASE_URL=... node scripts/python.mjs -m pytest \
  apps/api/tests/test_knowledge_api_postgresql.py::test_slack_outbox_runs_extraction_projection_embedding_and_idempotent_index -q
# 1 passed

TC_TEST_DATABASE_URL=... node scripts/python.mjs -m pytest \
  backend/tests/repository/test_generation_repository_postgresql.py::test_context_builder_selects_grounded_current_history_and_seals_snapshot -q
# 1 passed

TC_TEST_DATABASE_URL=... node scripts/python.mjs -m pytest \
  apps/api/tests/test_sprint5_slack_api_postgresql.py::test_sprint5_slack_deterministic_postgresql_end_to_end -q
# 1 passed

node scripts/validate-adrs.mjs
# 17 ADRs and 15 canonical decisions validated
```

Final implementation verification used the disposable PostgreSQL URL supplied in
`TC_TEST_DATABASE_URL`:

```text
node scripts/python.mjs -m ruff check backend apps
# All checks passed!

node scripts/python.mjs -m mypy backend/src apps/worker/src
# Success: no issues found in 71 source files

node scripts/validate-adrs.mjs
# Validated 17 ADRs, 15 canonical decisions, 11 Accepted, 4 Proposed,
# 2 Superseded, 7 linked contract schemas.

node scripts/validate-contracts.mjs
# Validated 7 strict Draft 2020-12 schemas, the versioned generation pair
# and 12 Slack fixtures.

node scripts/python.mjs -m pytest \
  apps/worker/tests/test_worker.py backend/tests/unit/test_jobs.py \
  backend/tests/unit/test_knowledge_extraction.py \
  backend/tests/unit/test_generation_repository.py -q
# 25 passed

node scripts/python.mjs -m pytest \
  apps/api/tests/test_phase_e_automatic_generation_postgresql.py \
  backend/tests/repository/test_jobs_postgresql.py -m postgres -q
# 15 passed

node scripts/python.mjs -m pytest \
  backend/tests/postgres/test_knowledge_repository.py \
  backend/tests/repository/test_generation_repository_postgresql.py \
  backend/tests/repository/test_slack_postgresql.py \
  apps/api/tests/test_sprint5_slack_api_postgresql.py::test_sprint5_slack_deterministic_postgresql_end_to_end \
  -m postgres -q
# 23 passed

node scripts/python.mjs -m pytest \
  backend/tests/unit apps/api/tests apps/worker/tests -m 'not postgres' -q
# 246 passed, 37 deselected
```

No migration, OpenAPI, JSON Schema, TypeScript client, or other generated artifact changed.
The implementation change set is limited to the queue/worker/knowledge consumer seams, their
focused tests, and this contract's roadmap/ADR references.
