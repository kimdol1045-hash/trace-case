# Phase F Sprint F1 wave 3 LightRAG integration verification

- Verification date: 2026-09-02 (Asia/Seoul)
- Scope: `PF-SF1-T09A` durable ledger, `T09B` provider/controller, and `T09C` runtime routing
- Verdict: **LOCAL CONTRACT INTEGRATED; STAGING/LIVE ACCEPTANCE REMAINS OPEN**

No live or staging database, provider, network, Docker, R2, Slack, or secret operation was run.
This wave did not mutate the authoritative QA/knowledge ledger and does not close PF-SF1-T09.

## One generation contract

| Layer | Integrated invariant | Fail-closed behavior |
|---|---|---|
| Durable authority | Alembic head `20260902_0030` owns only immutable `lightrag_generations`, singleton/versioned `lightrag_active_generation_pointer`, and append-only `lightrag_generation_transition_events`. | Unknown/invalid targets, stale expected identity/version, replay conflicts, invalid rollback ancestry, direct pointer writes, and used-state downgrade are rejected with durable evidence where a transition was attempted. |
| Provider/controller | `LedgerBackedGenerationController` accepts an injected isolated storage handle without credentials, binds it to the exact auxiliary authority/workspace/manifest, proves processed identity set/cardinality/checksum, rechecks source digests, registers immutable evidence, and uses repository CAS activation/rollback. | Partial or mismatched generations are discarded before activation. Ambiguous activation rolls back only from the exactly observed new pointer; rollback failure remains explicit and secret-safe. |
| Runtime routing | API, worker, and knowledge consumers use `LightRAGRuntimeResolver` through the shared `LightRAGIndex` protocol. Every process reads the same atomic pointer+metadata binding and caches by `(pointerVersion, generationId, registrationDigest)`. | no-active, missing/invalid/disappearing generation, factory target mismatch, storage failure, or pre/post-operation pointer change discards LightRAG output and returns typed graph+pgvector fallback. No unversioned environment switch selects a generation. |

The controller and resolver share the repository contract in
[`lightrag_generation_repository.py`](../../backend/src/tc_backend/repositories/lightrag_generation_repository.py).
The controller records the same generation identity, digests, document cardinality, workspace, and
registration digest that the resolver later requires in its immutable binding. Pointer versions advance
only through compare-and-swap transition methods; rolling back to an earlier generation still produces a
new cache version.

## Authoritative-ledger boundary

Migration `0030` creates and drops exactly the three control-plane tables. Its foreign key can target only
`lightrag_generations`; its trigger/function SQL operates only on those tables. A new non-PostgreSQL
adversarial test executes both migration functions through a recording Alembic operation boundary and
rejects any authoritative-ledger or knowledge-projection table name in raw migration SQL. The PostgreSQL
suite separately asserts the foreign-target set, append-only guards, downgrade refusal, and unchanged
counts for representative `test_cases`, knowledge projection, and audit tables.

The provider workflow receives a read-only source snapshot and reports
`authoritativeLedgerMutation=false`. The runtime resolver reads only the generation control plane; it does
not write the QA ledger or knowledge projection.

## Adversarial evidence

Focused contracts cover concurrent activation with one winner, idempotent replay, stale expected pointer,
unknown generation, invalid rollback predecessor, activation fault atomicity, rollback failure visibility,
and downgrade safety. Declaration and endpoint regressions reject whole production tokens plus numbered
variants such as `prod01`, `production2`, `live01`, `primary2`, and `main01` across target/database/workspace
names and remote host labels without rejecting unrelated words that merely share a prefix. Controller tests
add exact target/workspace/manifest attestation, bounded batches,
deterministic document identity/cardinality/checksum, immediate source digest recheck, secret-safe reports,
and CAS rollback after ambiguous activation. Runtime tests add independent resolver instances, versioned
cache invalidation, rollback to the same identity at a newer version, no-active/missing/invalid fallback,
and suppression of stale query/index results when a generation changes or disappears mid-operation.

## Verification performed

| Exact command | Result |
|---|---|
| `node scripts/python.mjs -m pytest scripts/phase-f/tests/test_lightrag_full_reindex.py -q` | PASS — 47 tests |
| Focused migration/repository/runtime/adapter/API/worker/controller pytest selection | PASS — 125 tests |
| `pnpm test:phase-f` | PASS — 116 tests |
| `node scripts/python.mjs -m alembic -c backend/alembic.ini heads` | PASS — single head `20260902_0030` |
| `env -u TC_TEST_DATABASE_URL pnpm test:migration` | NOT RUN by design — 77 PostgreSQL tests skipped without a disposable database URL; no database connection occurred |
| `pnpm check` | PASS — ESLint/Ruff; TypeScript typecheck; mypy 141 sources; 348 TypeScript tests with one live Web E2E skipped; 584 Python tests with 109 PostgreSQL tests deselected; schema, ADR, 44 operations-validator, and OpenAPI/client drift gates |

## Remaining gates

1. Implement and review the credential-owning isolated LightRAG storage factory that consumes only an
   exact immutable generation binding; wire it into the existing API and worker resolver seams without
   changing `POSTGRES_WORKSPACE` or other process-global environment to select a generation.
2. Run the PostgreSQL control-plane races, failure injection, replay, and downgrade suite on a fresh
   disposable PostgreSQL 16 + pgvector database, then retain the bounded cleanup evidence.
3. Run read-only inspection and the complete bounded corpus build on exact attested staging copies; prove
   source/cardinality/checksum identity, CAS cutover, multi-process query/index routing, citation smoke,
   injected activation failure, CAS rollback, fallback/recovery, and retired-resource closure.
4. Capture staging DNS/TLS, load p95/p99, alert delivery/acknowledgement, backup/restore, retention, operator,
   reviewer, and release-owner evidence. At the wave 3 cutoff both composition flags were `false`; wave 4
   later completed disabled-by-default API/worker composition. `liveApplySupported=false`, execution
   `NOT_RUN`, and the open roadmap checkbox remain unchanged.
