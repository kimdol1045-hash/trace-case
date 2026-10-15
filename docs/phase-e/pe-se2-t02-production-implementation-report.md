# PE-SE2-T02 production implementation report

## Outcome

Migration `20260827_0021` and the runtime generation boundary now enforce one automatic root
per canonical `SlackWorkItem` and exact `source_version`. Manual roots remain repeatable under
distinct API idempotency keys, retry children inherit their root trigger, and no generation
provider is selected or invoked by this change.

## Implemented boundary

- `generation_jobs.generation_trigger` persists `MANUAL` or `AUTOMATIC`; sealed-job provenance
  cannot be changed by an in-place update.
- `uq_generation_jobs_automatic_source_version` uniquely indexes
  `(slack_work_item_id, source_version)` only for automatic roots. Delivery, outbox, and
  checkpoint IDs remain transport/audit data rather than product identity.
- The migration backfills roots identified by a T01 `generationJobId` receipt or automatic
  creation audit, propagates `AUTOMATIC` to their retry descendants, treats other history as
  `MANUAL`, and fails before installing the index if historical automatic roots conflict.
- Root generation ordinals are allocated while the canonical `SlackWorkItem` row is locked.
  The automatic repository path checks for an existing root under that lock before building a
  new context or calculating `max(generation_version) + 1`.
- The consumer validates an exact T01 receipt first. A missing receipt reuses the canonical
  automatic root, writes its ID into the checkpoint, and does not add another job, context, or
  creation audit; a manual or corrupt receipt fails closed.
- T03 thread-impact analysis, selective regeneration, retry-lineage redesign, timestamp alias
  redesign, and provider invocation remain outside this implementation.

## Changed files

- `backend/migrations/versions/20260827_0021_generation_automatic_deduplication.py`
- `backend/src/tc_backend/db/models.py`
- `backend/src/tc_backend/repositories/generation_repository.py`
- `backend/src/tc_backend/worker/knowledge_consumer.py`
- `apps/api/tests/test_phase_e_automatic_generation_postgresql.py`
- `backend/tests/repository/test_generation_repository_postgresql.py`
- `backend/tests/repository/test_migrations_postgresql.py`
- `backend/tests/unit/test_migration_graph.py`
- `docs/phase-e/automatic-generation-deduplication-contract.md`
- `docs/test-management-roadmap.md`
- `docs/phase-e/pe-se2-t02-production-implementation-report.md`

## Verification

All passing commands used the disposable database
`postgresql+psycopg://tc:***@127.0.0.1:55432/tc_pe_se2_t02_test`.

| Command | Result |
|---|---|
| `pytest apps/api/tests/test_phase_e_automatic_generation_postgresql.py -m postgres -k pe_se2_t02 -q` | 8 passed, 8 deselected |
| `pytest apps/api/tests/test_phase_e_automatic_generation_postgresql.py -m postgres -q` | 16 passed |
| `pnpm test:migration` | 57 passed; one pre-existing pytest collection warning |
| PostgreSQL API suite | 45 passed, 25 deselected |
| PostgreSQL knowledge suite | 11 passed, 2 external live tests skipped |
| worker regression | 6 passed |
| `pnpm test:unit:py` | 283 passed, 45 deselected |
| `pnpm typecheck:py` | strict mypy success across 112 source files |
| `pnpm lint:py` | Ruff success across `backend`, `apps`, and `scripts` |
| `pnpm test:schema` | 7 strict schemas and 12 Slack fixtures validated |
| `pnpm test:adr` | 17 ADRs, 15 canonical decisions, and 7 linked schemas validated |
| Alembic downgrade `0021 -> 0020`, upgrade `0020 -> 0021`, and `current` | succeeded; current is `20260827_0021 (head)` |

The migration regression includes a populated `0020` round trip proving automatic-root audit
backfill, automatic retry inheritance, and manual fallback. The focused PostgreSQL contract
covers serial reuse, concurrent reuse, rollback then retry, automatic-only database uniqueness,
manual repeatability, rejection of a manual job as an automatic receipt, and runtime retry-trigger
inheritance.

## Residual risks

- `alembic check` is not globally clean: it reports repository-wide pre-existing doubled
  check-constraint naming-convention drift and a pre-existing nullable mismatch for
  `generation_apply_outcomes.test_case_version_id`. Its output reported no new
  `generation_jobs`, `generation_trigger`, or automatic-source index operation; the migration
  graph, rebuild/downgrade tests, runtime schema assertions, and all repository tests passed.
- The database migration intentionally fails closed if production history already contains two
  automatic roots for one canonical source snapshot. Deployment therefore needs the normal
  migration preflight/backup discipline; this task does not invent a winner or delete history.
- Cross-host production lock latency and a real pre-existing conflicting history were not load
  tested. PostgreSQL row locking and unique-index behavior were exercised with serial,
  concurrent, rollback, and retry integration tests on the disposable PostgreSQL instance.
