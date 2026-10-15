# PE-SE2-T02 automatic generation deduplication contract

## Status and scope

This document fixes the implemented contract for `PE-SE2-T02`. Migration `20260827_0021`,
the SQLAlchemy model/repository, and the Slack knowledge consumer enforce this boundary. The
tests named `test_pe_se2_t02_*` remain the focused PostgreSQL regression target.

T02 prevents two automatic root `generation_jobs` rows from representing the same Slack
source snapshot merely because different Slack deliveries, outbox rows, projection paths, or
worker attempts reached the generation boundary. It preserves repeatable manual generation
and the exact-delivery receipt implemented by `PE-SE2-T01`.

This contract does not decide whether a newer thread snapshot affects generated cases, select
cases to regenerate, execute a provider, apply generated cases, or post a Slack reply. Those
belong to `PE-SE2-T03` and later tasks.

## Canonical Slack message key

The canonical message is the root of a Slack thread, not the delivery that happened to expose
it. Within one local workspace its key is the normalized tuple:

```text
(workspace_id, slack_workspace_id, channel_id, canonical_root_message_ts)
```

- `workspace_id` is the local tenant/workspace UUID.
- `slack_workspace_id` is Slack's team/workspace ID.
- `channel_id` is Slack's channel ID.
- `canonical_root_message_ts` is `thread_ts` for a reply and `message_ts` for a root, edit, or
  delete after Slack timestamp normalization to six fractional digits.

`event_id`, `event_ts`, `slack_events.id`, `outbox_events.id`, background-job ID, projection
receipt ID, revision ID, permalink, body hash, author, and delivery transport are not part of
the canonical key. They can all vary while the logical Slack message remains the same.

The database already normalizes this tuple through
`uq_slack_work_items_workspace_channel_message`. `slack_work_items.id` is the local surrogate
for that unique tuple. Generation code must use that row and foreign key; it must not persist a
second concatenated or hashed copy of the external key that can drift from the source ledger.

## Version meanings

`SlackWorkItem.source_version` and `GenerationJob.generation_version` are separate clocks:

| Field | Exact meaning |
|---|---|
| `source_version` | Positive, append-only version of the Slack root/thread source snapshot sealed into this job. It changes when the Slack source ledger accepts a new revision. |
| `generation_version` | Positive, immutable append ordinal of a generation-job row for one canonical Slack root. Manual roots, automatic roots, and explicit generation retry children all occupy distinct ordinals. It is allocated while the canonical `SlackWorkItem` is locked. |
| `attempt_number` | Position inside one explicit generation retry lineage. It is not a Slack source version or a worker-delivery attempt. |
| background-job `attempts` | Delivery execution count. Retrying one automatic delivery must not allocate a generation version. |

Consequently `generation_version` is neither an outbox aggregate version nor an alias for
`source_version`. A rollback leaves no committed version reservation; the clean retry may use
the same next ordinal. No contract promises gapless history after administrative repair, but
committed rows never renumber.

## Persisted trigger provenance and database constraints

`generation_jobs` must add a non-null `generation_trigger` with the values `MANUAL` and
`AUTOMATIC`:

- a root created by `POST /api/generation-jobs` is `MANUAL`;
- a root created by the Slack automatic consumer is `AUTOMATIC`;
- an explicit retry child inherits its root's trigger. Retry lineage remains distinguished by
  `retry_of_job_id` and `attempt_number`.

The migration must preserve and enforce this shape:

1. Keep `uq_slack_work_items_workspace_channel_message` as the canonical-message constraint.
2. Keep `uq_generation_jobs_work_item_generation_version` so one canonical message cannot have
   two job rows with the same generation ordinal, regardless of trigger.
3. Add `ck_generation_jobs_generation_trigger_allowed` for `MANUAL | AUTOMATIC`.
4. Add the partial unique index
   `uq_generation_jobs_automatic_source_version` on
   `(slack_work_item_id, source_version)` where
   `generation_trigger = 'AUTOMATIC' AND retry_of_job_id IS NULL`.

The partial index is the product-deduplication backstop. A second automatic root for the same
canonical root and exact Slack snapshot is forbidden even if application code incorrectly
chooses another `generation_version`. Manual roots are deliberately outside this predicate, so
different manual idempotency commands may generate repeatedly from the same snapshot. Automatic
retry children are also outside the predicate; the root remains the source-snapshot receipt and
existing retry-lineage constraints own the child behavior.

Historical rows must be backfilled before the column becomes non-null. A job referenced by a
T01 projection `result.generationJobId` or an `AUTOMATIC_GENERATION_JOB_CREATED` audit is
`AUTOMATIC`; other existing jobs are `MANUAL`. The migration must fail rather than silently pick
a winner if historical evidence identifies more than one automatic root for one
`(slack_work_item_id, source_version)`.

## Repository and transaction contract

The automatic repository operation is a get-or-create, not “calculate max and always insert”:

1. Lock the canonical `SlackWorkItem` row with `FOR UPDATE` in the same transaction that will
   seal or reuse the job.
2. Revalidate that the requested `source_version` is current and that quiet/parse/projection
   prerequisites still hold. A stale delivery is a terminal no-op and creates nothing.
3. Query the automatic root for `(slack_work_item_id, source_version)` under the lock.
4. If it exists, verify its canonical source, `AUTOMATIC` trigger, root lineage, sealed context,
   sealed Input, and immutable provenance, then return that job without adding context, audit, or
   generation-version rows.
5. If it does not exist, allocate `max(generation_version) + 1` while the same work-item lock is
   held, insert an `AUTOMATIC` root, seal context and Input, append its one creation audit, and
   attach the projection receipt in the existing T01 transaction.

The existing uniqueness constraints remain the last line of defense. Two sessions racing on the
same canonical message serialize on the work-item row; the loser rechecks and returns the winner.
If a uniqueness violation still occurs because of legacy corruption or a programming error, the
operation fails closed. It must not respond by incrementing again and creating a second automatic
root.

The projection/checkpoint, generation job/context/Input, creation audit, and receipt remain one
commit. A fault at any point rolls them all back. A background retry runs the same get-or-create
path and either creates the one root after the rollback or returns the already committed root.

## T01 exact-delivery receipt compatibility

`KnowledgeProjectionEvent.result.generationJobId` remains the fastest and most specific replay
receipt:

1. When the exact checkpoint has a receipt, validate and return it as T01 already does.
2. When the exact checkpoint exists without a receipt, or another delivery reaches the same
   canonical source snapshot, perform the canonical automatic-root lookup before any allocation.
3. If that lookup finds the root, attach the same job ID to the current checkpoint and commit no
   new generation job or creation audit.

A receipt pointing to a `MANUAL` job, a retry child, another canonical Slack root, another source
snapshot, or an incomplete/corrupt seal is an `AutomaticGenerationReceiptError`. The handler must
not reinterpret it as a cache miss and must not allocate a replacement version.

## Manual-generation compatibility

The manual API keeps request idempotency and repeat generation as distinct guarantees:

- replaying the same normalized `Idempotency-Key` returns the original manual job;
- a different valid key is an intentional new manual request and appends the next
  `generation_version`, even when `source_version` is unchanged;
- concurrent different manual keys serialize on the same canonical work item and both commit with
  distinct versions;
- a manual job is never accepted as an automatic projection receipt.

The explicit `POST .../{jobId}/retry` contract remains append-only. It creates a child with its
existing retry lineage and next generation ordinal; it does not turn a worker redelivery into a
generation retry.

## Conflict and retry outcomes

| Situation | Required outcome |
|---|---|
| Exact T01 receipt replay | Return the referenced automatic root; zero new rows. |
| Different delivery/checkpoint, same canonical root and source version | Return and attach the existing automatic root; zero new job/context/audit rows. |
| Two concurrent automatic attempts for the same canonical root/source version | One automatic root commits; both callers observe its ID. |
| Same canonical root, newer source version | T02 makes no impact decision. If a later stage explicitly requests generation, it may create one new automatic root for that source version. |
| Stale source version | Terminal no-op; no projection or generation write. |
| Missing receipt after an earlier job commit | Canonical lookup repairs the receipt by reference; it does not regenerate. |
| Receipt points to manual/wrong/corrupt job | Typed conflict, full rollback, no replacement job. |
| Fault before commit | Full rollback; a clean retry may create the one root. |
| Distinct manual idempotency keys | Distinct manual roots and generation versions are allowed. |

## Verification

The PostgreSQL contract lives in
`apps/api/tests/test_phase_e_automatic_generation_postgresql.py` under the
`test_pe_se2_t02_*` names. Seven tests preserve the original RED boundary: schema/index shape,
canonical fallback when the exact receipt is absent, concurrent fallback, rollback followed by
clean retry, manual repetition, and rejection of a manual receipt. An eighth release regression
executes an automatic root through failure and explicit retry and proves that the child inherits
`AUTOMATIC` without entering the root-only partial-index predicate.

Focused command:

```text
TC_TEST_DATABASE_URL=postgresql+psycopg://.../tc_test \
  node scripts/python.mjs -m pytest \
  apps/api/tests/test_phase_e_automatic_generation_postgresql.py \
  -m postgres -k pe_se2_t02 -q
```

At migration head `20260827_0021` the suite must be GREEN. Downgrading to `20260827_0020`
deliberately removes `generation_trigger`,
`ck_generation_jobs_generation_trigger_allowed`, and
`uq_generation_jobs_automatic_source_version`, leaving the former RED boundary reproducible.

### 2026-08-27 release-gate evidence

| Gate | Result |
|---|---|
| T02 focused PostgreSQL | 8 passed (seven original RED contracts plus retry-trigger regression) |
| T01 + T02 automatic-generation PostgreSQL | 16 passed |
| Migration/repository PostgreSQL | 57 passed, one collection warning |
| API PostgreSQL | 45 passed, 25 deselected |
| Knowledge PostgreSQL | 11 passed, two external-live tests skipped |
| Worker | 6 passed |
| Non-PostgreSQL Python | 283 passed, 45 deselected |
| Ruff / strict mypy | passed / 112 source files passed |
| JSON Schema / ADR / OpenAPI drift | passed / passed / passed |
| `0021 -> 0020 -> 0021` cycle | passed; Alembic head is `20260827_0021` |

The historical backfill fixture passed together with the generation repository suite. The focused
schema test compares the live column, check constraint, and exact partial-index predicate with the
SQLAlchemy model, and an Alembic autogenerate inspection reported no `generation_jobs` operation.
The repository-wide `alembic check` is not GREEN because of pre-existing doubled
naming-convention check-constraint drift and the unrelated
`generation_apply_outcomes.test_case_version_id` nullable mismatch; those are release debt, not a
waiver for a future `generation_jobs` drift.

## Deployment preflight and external gates

Before applying `20260827_0021` to any historical database, run this query while the database is
still at `20260827_0020`. It mirrors the migration's provenance classification and must return zero
rows:

```sql
SELECT job.slack_work_item_id,
       job.source_version,
       count(*) AS automatic_root_count,
       array_agg(job.id ORDER BY job.id) AS generation_job_ids
  FROM generation_jobs AS job
 WHERE job.retry_of_job_id IS NULL
   AND (
        EXISTS (
            SELECT 1
              FROM knowledge_projection_events AS projection
             WHERE projection.result ->> 'generationJobId' = job.id::text
        )
        OR EXISTS (
            SELECT 1
              FROM audit_logs AS audit
             WHERE audit.entity_type = 'GENERATION_JOB'
               AND audit.entity_id = job.id
               AND audit.action = 'AUTOMATIC_GENERATION_JOB_CREATED'
        )
   )
 GROUP BY job.slack_work_item_id, job.source_version
HAVING count(*) > 1;
```

If it returns any row, block deployment, preserve the colliding IDs and their projection/audit
evidence, and resolve the ledger conflict explicitly; do not delete a row or let the migration pick
a winner. Back up the database, run the preflight, apply the migration, then repeat the
upgrade/downgrade/upgrade rehearsal against a production-shaped copy before rollout.

Signed live Slack delivery/re-delivery, production historical collision data, and real provider
credentials remain external gates. No provider was called in T02, and neither PE-SE2-T03 thread
impact analysis nor selective regeneration is covered by this contract.
