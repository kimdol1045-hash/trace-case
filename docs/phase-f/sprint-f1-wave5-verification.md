# Phase F Sprint F1 wave 5 T06 local load verification

- Verification date: 2026-09-04 (Asia/Seoul)
- Scope: `PF-SF1-T06` preview, empty-database safety, fixed 100,000-case concurrent mixed load,
  defect remediation, PostgreSQL regressions, evidence verification, and exact-target cleanup
- Verdict: **LOCAL LOOPBACK DISPOSABLE PASS; OPERATOR/REVIEWER, PRODUCTION-LIKE, EDGE, AND STAGING GATES NOT RUN**

No staging/live database, deployed endpoint, Slack workspace/API, R2 endpoint, external provider, or paid
model was called. The only database mutations were in two uniquely named databases on the existing
loopback-only Docker PostgreSQL service; the first retained a fail-closed diagnostic run, the second
retained the successful rerun, and both databases were removed after evidence verification. The existing
`tc` development database and every other database were left untouched.

## Target and preview proof

The final target was
`tc-phase-f-t06-wave5-20260904-baa12856-r2-disposable`, database
`tc_phase_f_t06_wave5_20260904_baa12856_r2_test_disposable`, and required comment
`tc-phase-f-rehearsal:disposable:tc-phase-f-t06-wave5-20260904-baa12856-r2-disposable`.
The Compose port was `127.0.0.1:55432 -> 5432/tcp`. Before migration, both the independent SQL check and
the harness preflight proved PostgreSQL `160012`, exact database/comment identity, zero public tables,
and no Alembic revision.

Preview ran without the URL environment variable and returned `mode=preview`, `executes=false`,
`workUnitsExecuted=0`, `loadGate=NOT_RUN`, and this exact confirmation:

```text
RUN PF-SF1-T06 LOAD-PROFILE ON tc-phase-f-t06-wave5-20260904-baa12856-r2-disposable
```

The reviewed profile command was:

```bash
node scripts/python.mjs scripts/phase-f/load_profile.py \
  --target-name tc-phase-f-t06-wave5-20260904-baa12856-r2-disposable \
  --target-class disposable \
  --database-url-env TC_PHASE_F_T06_WAVE5_R2_DATABASE_URL \
  --database-name tc_phase_f_t06_wave5_20260904_baa12856_r2_test_disposable \
  --concurrency 16 --duration-seconds 120 --queue-sample-interval 0.5 \
  --output docs/phase-f/evidence/pf-sf1-t06-wave5-20260904-baa12856-r2.json \
  --execute \
  --confirm 'RUN PF-SF1-T06 LOAD-PROFILE ON tc-phase-f-t06-wave5-20260904-baa12856-r2-disposable'
```

The URL was injected only into the named environment variable from the already-running local container
configuration. It was not printed, placed in argv, written to evidence, or added to the repository. The
host did not have `psql`; an ephemeral, credential-free adapter used the same Compose container's
PostgreSQL 16 client for the harness attestation queries and was deleted after the run.

## Fail-closed finding and remediation

The first target, without `-r2`, exposed three concrete local-gate defects. The initial preflight stopped
before migration because a `CASE` expression still caused PostgreSQL to resolve the absent
`alembic_version` table. After that fix, the first full run correctly wrote a `FAIL`: Docker's default
64 MiB `/dev/shm` could not allocate parallel-query dynamic shared-memory segments, producing eight
`OperationalError` samples; the default SQLAlchemy capacity of 15 connections then produced five
30-second `TimeoutError` samples at concurrency 16 plus the sampler.

The remediation:

- probes `to_regclass` first and reads `alembic_version` only when the table exists;
- gives the harness one connection per configured worker plus two connections of sampler/lifespan
  headroom, with no overflow;
- gives the local PostgreSQL service 256 MiB shared memory for concurrent 100k-case parallel scans;
- adds focused regression tests for absent/present Alembic tables, pool sizing at concurrency 16/64,
  and the Compose shared-memory contract.

The failed evidence was never overwritten:

- [`pf-sf1-t06-wave5-20260904-baa12856.json`](./evidence/pf-sf1-t06-wave5-20260904-baa12856.json)
- SHA-256: `b3b3a6b738c145e2cce97500e8657a652d65b585cd7b562cbd2c33e4c9573e16`
- Result: `FAIL`, 745 operations, 13 errors, warm throughput `4.897/s`, warm API p95
  `30,656.743 ms`; provider outbound calls `0`

## Final local disposable result

The rerun used a fresh, independently attested empty database and a new output path. It loaded exactly
100,000 cases, versions, and steps, with 95,000 active cases. All 15 structural/threshold checks passed.

| Metric | First touch | Warm | Combined | Gate |
|---|---:|---:|---:|---:|
| operations / errors | `362 / 0` | `4,470 / 0` | `4,832 / 0` | error rate `0` PASS |
| overall throughput | `35.173/s` | `40.462/s` | `40.012/s` | warm `>= 10/s` PASS |
| API p95 | `1,438.468 ms` | `998.554 ms` | `1,033.459 ms` | warm `<= 1,000 ms` PASS |
| Slack ingress p95 | `204.083 ms` | `112.645 ms` | `118.073 ms` | warm `<= 3,000 ms` PASS |
| worker poll p95 | `219.878 ms` | `178.893 ms` | `181.173 ms` | warm `<= 2,000 ms` PASS |
| generation scheduling p95 | `129.574 ms` | `55.309 ms` | `57.991 ms` | warm `<= 1,000 ms` PASS |

Queue lag p95 was `488.251 ms` (max `715.334 ms`), lease lag p99 was `145.389 ms`
(max `165.582 ms`), and all 228 samples were present with zero sampler errors. The no-transport
generation stub handled 481 jobs and `providerOutboundCallCount` was exactly `0`.

The successful immutable evidence is
[`pf-sf1-t06-wave5-20260904-baa12856-r2.json`](./evidence/pf-sf1-t06-wave5-20260904-baa12856-r2.json),
mode `0600`, 9,122 bytes, SHA-256
`7a4acb6a8395082ef04ed482e7e7faacc2c1b68e6358caf8576f100a507696f0`. A structural verifier re-read
both JSON files, checked the fixed fixture, zero provider calls, empty/attested preflight, gate booleans,
zero successful-run errors, and exact overall/surface totals. A secret-marker scan found no URL,
password, secret, authorization, or token value in either artifact.

## Hardware and runtime context

| Component | Observed local context |
|---|---|
| Host | macOS 15.6.1 (`24G90`), `Mac16,10`, Apple M4, 10 logical CPUs, 16 GiB physical memory |
| Docker Desktop VM | Engine/client 28.5.2, Linux arm64, 10 vCPUs, 8,217,448,448 bytes memory |
| Compose | v2.40.3-desktop.1 |
| PostgreSQL | `pgvector/pgvector:0.8.1-pg16`, PostgreSQL 16.12, container shared memory 256 MiB |
| Toolchain | Python 3.13.7, Node.js 22.18.0, pnpm 10.28.2 |
| Profile | concurrency 16, requested 120 s, measured 120.765 s, sample interval 0.5 s |
| Harness resources | user/system CPU 94.422/43.304 s; max RSS 139,853,824 bytes |
| Database size | 13,351,959 bytes pre-fixture; 187,964,439 post-fixture; 196,271,127 post-load |

This records the execution context but not container/DB CPU, memory, I/O, connection-saturation time
series, or network bandwidth. First touch was not a privileged cold-cache test.

## Verification performed

| Exact check | Result |
|---|---:|
| `node scripts/python.mjs -m pytest scripts/tests/test_phase_f_load_profile.py scripts/phase-f/tests/test_rehearse.py -q` | PASS — 50 tests |
| `node scripts/python.mjs -m ruff check scripts/phase-f/load_profile.py scripts/phase-f/rehearse.py scripts/tests/test_phase_f_load_profile.py scripts/phase-f/tests/test_rehearse.py` | PASS |
| `node scripts/python.mjs -m mypy backend/src apps/api/src apps/worker/src scripts/phase-f/load_profile.py scripts/phase-f/rehearse.py` | PASS — 131 sources |
| Focused cases/jobs/Slack and API PostgreSQL selection | PASS — 35 tests |
| `TC_TEST_DATABASE_URL=<local-disposable-url> pnpm test:migration` | PASS — 78 PostgreSQL tests; one collection warning |
| `pnpm check` | PASS — 348 TypeScript tests with one live E2E skipped; 620 Python tests with 109 PostgreSQL tests deselected; lint/typecheck, 13 schemas, 17 ADRs, 44 operations tests, and OpenAPI drift |

## Cleanup proof

After both evidence hashes were verified and all tests ended, an administrator connection rechecked both
exact database names, their exact distinct attestation comments, and zero active sessions. `dropdb` was
then called separately with each literal database name. A final exact-name query returned count `0`.
No wildcard, schema-wide cleanup, volume deletion, Compose down, or recursive filesystem deletion ran.

The disposable databases cannot be recovered through this task; the two JSON evidence files retain the
bounded results. The PostgreSQL container remains healthy and loopback-only.

## Remaining gates — all `NOT_RUN`

1. An operator and independent reviewer must approve the load mix/thresholds and sign the evidence bundle.
2. A production-like multi-process API/worker deployment must repeat p95/p99, connection/DB/container
   resource, queue-drain, saturation, and failure-recovery measurement.
3. Edge/reverse-proxy/TLS/WAN and deployed Slack ingress behavior need separate black-box evidence.
4. Staging, true cold-cache, sustained soak, capacity headroom, and alert delivery/acknowledgement remain
   untested.
5. Actual generation materialization/provider quality is deliberately outside this zero-outbound profile.

The local disposable sub-gate is PASS. `PF-SF1-T06` and the Phase F release gate remain open until the
external owner-operated gates above are complete.
