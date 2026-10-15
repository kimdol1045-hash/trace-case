# Sprint F1 recovery and performance readiness

- Audit date: 2026-09-01; T06/T07/T08 local updates: 2026-09-04; T09/T10 local updates: 2026-09-07
- Scope: `PF-SF1-T06` through `PF-SF1-T10`, plus `PF-SF1-T15` preparation
- Toolkit: [`scripts/phase-f/rehearse.py`](../../scripts/phase-f/rehearse.py) plus dedicated Wave 8/9 disposable harnesses
- Verdict: **T06/T07/T10 LOCAL DISPOSABLE, T08 LOCAL MINIO, AND T09 LOCAL NETWORK-FREE SUB-GATES PASS; ALL SIX PHASE F TASKS REMAIN OPEN**

No benchmark, database backup/restore, R2 request, knowledge rebuild, migration,
Docker failover, deployed CORS request, or browser upload was executed while initially preparing this
toolkit. Wave 5 later executed T06. Wave 6 executed a preview-first T07 backup/isolated-restore drill,
then completed two independent RED remediation cycles. After the archive-inventory stage was finalized,
the second cycle was rerun with new exact-comment local disposable databases and new retry6 evidence;
retry5 remains immutable historical superseded evidence. Wave 7 then exercised a matched PostgreSQL
metadata and object recovery using only loopback Compose PostgreSQL, MinIO, and ClamAV. Every
staging/live/edge/operator gate remains unrun. Wave 8 exercised the full local network-free LightRAG
reindex/fallback flow, and Wave 9 exercised the current migration one-step rollback/roll-forward only
on a disposable copy restored from a read-only snapshot of `tc`.

## Safety contract

The Phase F entrypoint is preview-only unless `--execute` is supplied. Preview does not resolve
database/API secret values and does not invoke subprocesses or network requests. An execution is
accepted only when all applicable controls pass:

1. Target class is exactly `disposable` or `staging-copy`.
2. Target, database, bucket, and remote host names reject `prod`, `production`, `live`, `primary`,
   and `main` as name tokens.
3. Target names use `tc-phase-f-<purpose>-<class>` and database names use
   `tc_phase_f_<purpose>_<class>`. Disposable databases must be on loopback; remote staging-copy
   hosts must identify themselves with `stage`, `staging`, or `stg` and use
   `sslmode=verify-full`. Ambient `PG*` variables are discarded before PostgreSQL client calls.
4. Every database must have an exact, independently installed database comment:
   `tc-phase-f-rehearsal:<class>:<target-name>`. A suggestive database name alone is insufficient.
5. Destructive benchmark and restore destinations must have zero public tables. Restore never
   uses `--clean`; backup files cannot be overwritten and must remain outside the repository.
6. Backup evidence and dump consumers open one `O_RDONLY|O_NOFOLLOW` descriptor, require a regular
   single-link file with permissions no broader than 0600, and compare device/inode/size/mtime-ns/
   ctime-ns around every JSON, SHA, archive-inventory, and restore stage. PostgreSQL commands receive
   the pinned descriptor through stdin and never reopen its pathname.
7. Credentials and presigned URLs are read only from `TC_PHASE_F_*` environment variables. They
   are not passed in subprocess argv or returned in JSON evidence.
8. Every execution requires the exact task/action/target confirmation printed by its preview.
9. Provider-neutral object-storage verification binds the API-reported provider and bucket to the
   declared values, forces
   `verify-metadata` to `dryRun=true` on every page, and fails on an empty population, any
   mismatch/failure, redirect, incomplete pagination/cursor cycle, or any unverified restore-sample
   item. The `r2-verify` wrapper remains strict R2-only; MinIO is accepted only for a disposable
   loopback target.
10. Wave-7 recovery accepts only generated literal `pf-sf1-t08-wave7` database/bucket/prefix names,
    exact database comments, distinct empty recovery destinations, and one shared recovery point.
    It pins regular single-link mode-0600 manifest/evidence/dump inputs to one no-follow descriptor,
    forbids overwrite, and binds versioned canonical digests across the manifest, source/restored DB
    metadata, and source/recovered bucket inventories.
11. Knowledge preview/apply output retains only digests, counts, status totals, and write/preserved
   scopes; source events and projection rows are deliberately omitted from evidence JSON.
12. Knowledge apply requires explicit opt-in, `qwen3:8b`/`bge-m3` pins, and a loopback or
    explicitly staging-named Ollama origin; a production-like model host is rejected.
13. Wave-9 migration rehearsal accepts only generated literal `pf-sf1-t10-wave9` names, one
    loopback PostgreSQL 16 target on a generated non-default port, the exact database comment, and
    an initially empty database. It reads current `tc` only through an exported repeatable-read,
    read-only snapshot and its pinned `pg_dump`, and mutates only the restored disposable copy.
14. Wave 9 resolves the real Alembic graph and requires one head, one immediate predecessor, callable
    safe upgrade/downgrade functions, pristine head-owned control-plane state, exact one-revision
    downgrade/upgrade, complete logical catalog/data/role equality, evidence-bound cleanup, and
    unchanged current-`tc`, Docker/database inventory, and all prior evidence hashes.

The shared controls are implemented in [`rehearse.py`](../../scripts/phase-f/rehearse.py); Wave 9's
stronger local-only controls are in
[`migration_roundtrip_disposable.py`](../../scripts/phase-f/migration_roundtrip_disposable.py).
Focused tests are in [`test_rehearse.py`](../../scripts/phase-f/tests/test_rehearse.py) and
[`test_migration_roundtrip_disposable.py`](../../scripts/phase-f/tests/test_migration_roundtrip_disposable.py),
and the reviewed staging CORS shape is at
[`phase-f-r2-cors.staging.example.json`](../../deploy/operations/phase-f-r2-cors.staging.example.json).

## Evidence-backed gap matrix

| Task | Existing evidence found | Preparation added | Status and evidence still required |
|---|---|---|---|
| `PF-SF1-T06` | `benchmark_cases.py` defaults to 100,000 synthetic cases and covers tree/list/search/filter/deep-offset queries, but its direct guard only requires a database name containing `test`. Its own report says measurements are warm-cache, single-process TestClient and exclude deep pagination from the p95 gate. | The separate preview-first [`load_profile.py`](../../scripts/phase-f/load_profile.py) reuses the fixed fixture behind an attested empty loopback boundary, adds bounded concurrent API, signed synthetic Slack ingress/queueing, real worker claim/lease, no-outbound generation job scheduling, p50/p95/p99/error/throughput and queue/lease lag gates. Wave 5 fixed empty-Alembic preflight, pool headroom, and local PostgreSQL shared memory after retaining a fail-closed run. | **LOCAL DISPOSABLE PASS / TASK OPEN.** A fresh exact-attested DB rerun loaded 100,000 cases and passed all 15 gates with 4,832/4,832 successful operations and zero provider calls; both exact disposable DBs were removed after evidence verification. True cold cache, Nginx/TLS/network, multi-process, DB/container resource saturation, staging and operator/reviewer handoff remain `NOT_RUN`. See the [T06 report](./pf-sf1-t06-concurrent-load-profile.md) and [wave 5 verification](./sprint-f1-wave5-verification.md). |
| `PF-SF1-T07` | The object-storage runbook defines a transactionally consistent PostgreSQL dump, matched object inventory, isolated restore, dry-run integrity, and byte sampling. A first independent RED rejected the incomplete catalog comparator and ambiguous `-r2` names; a second RED rejected pathname reopen TOCTOU and the abbreviated post-smoke schema comparison. | Catalog v3 covers 16 complete schema categories and every public-table digest. Evidence and dump consumption is pinned to one `O_RDONLY|O_NOFOLLOW` descriptor with regular/mode-at-most-0600/nlink-1 enforcement and device/inode/size/mtime-ns/ctime-ns checks around JSON, SHA, archive inventory, and restore. Restore enforces read-only FastAPI transactions and compares the complete catalog/data fingerprint after smoke. | **SECOND RED REMEDIATED / LOCAL LOOPBACK DISPOSABLE PASS / TASK OPEN.** New authoritative final-code `retry6-final` evidence restored a 605,484-byte archive (SHA-256 `0df58e095a88533ab419f1d7c433d4a0fd65f011e0a13c109dd913e133105bcf`, 791 inventory entries, listing SHA-256 `2b256f661cf02c962238aa28b2e341385b9e055bc156b20bd018912314441665`) at head `20260902_0030`; catalog v3's 16 categories, 78 tables, 40 rows, catalog digest `8846c25f73faecdc56220b9597a399b5e44a41202b0dc4ac1eec5dc18414ed62`, and data digest `28eb0dee7a6a684d921cf251898e72802e620875884a98a7ff9fc59be9e1fc3d` matched before and after smoke. Backup/restore/restore-to-smoke were 0.643859/4.011901/6.526439 s; recovery-point age was 30.773653 s. Retry4 and retry5 remain historical superseded. Daily scheduler, encrypted retention, separately hosted staging, matched Cloudflare R2, operator handoff, and production RTO/RPO remain `NOT_RUN`; task/release gate remain unchecked. See [wave 6](./sprint-f1-wave6-verification.md). |
| `PF-SF1-T08` | The API status exposes provider/bucket/readiness. `verify-metadata` compares HEAD size/MIME/SHA and mutates rows only when `dryRun=false`; `restore-sample` downloads bounded bytes and compares size/SHA. The runbook requires matched DB/object recovery and class-aware sampling. | The provider-neutral verifier preserves strict R2 behavior while accepting MinIO only on disposable loopback. The wave-7 helper builds a 12-object owner/MIME cross-product, restores the full DB plus objects into distinct empty destinations, binds canonical metadata/inventory/manifest digests, paginates all active rows in dry-run, and verifies HEAD plus a non-empty full-byte sample covering every owner/MIME class. | **LOCAL MINIO DISPOSABLE SUB-GATE PASS / TASK OPEN.** Source/restored catalog v3 (78 tables/53 rows), 12 metadata rows, 12 objects, exact privacy/provider/policy, and all canonical bindings matched; cleanup preserved the pre-existing DB/bucket inventories and `tc` fingerprint. This is MinIO evidence, not Cloudflare R2 evidence. Actual R2 recovery/versioning/backup, separately hosted staging DB, operator handoff, and production RTO/RPO remain `NOT_RUN`; task/release gate remain unchecked. See [wave 7](./sprint-f1-wave7-verification.md). |
| `PF-SF1-T09` | PostgreSQL projection rebuild uses a fresh event/source digest, advisory lock, repeatable-read transaction, derived-table-only reset, and authoritative-ledger digest check. The fixed Docker LightRAG rehearsal retains prior local process-kill/fallback evidence. | Head `20260902_0030` adds the isolated immutable generation ledger, singleton/versioned active pointer and append-only transition evidence. Wave 8 adds a preview-first exact `pf-sf1-t09-wave8` loopback harness using a disposable restored source copy, distinct empty AGE+pgvector auxiliary, official LightRAG 1.5.6 child, deterministic network-free callables, page-size-1 deep inspection, CAS cutover/rollback and evidence-bound cleanup. | **LOCAL DISPOSABLE NETWORK-FREE SUB-GATE PASS / TASK OPEN.** Two documents/statuses/chunks, 2 chunk/entity vectors, 1 relation vector and 2-node/1-edge AGE graphs matched in each of 3 retained workspaces. Separate API/worker resolvers, stale-result fencing, failed activation/rollback, child kill and auxiliary stop fallback, recovery, exact cleanup, unchanged `tc` and prior hashes passed. Approved staging credentials/bootstrap, live provider, staging apply, production-like networking/load/alerts, operator handoff and production RTO/RPO remain `NOT_RUN`; `liveApplySupported=false`, task/release gate unchecked. See [wave 8](./sprint-f1-wave8-verification.md). |
| `PF-SF1-T10` | Repository migration tests exercise base/head and selected one-step round trips on a generic disposable database. Current head is `20260902_0030`; its downgrade refuses any generation/audit evidence or non-pristine active pointer. After a safe pristine downgrade to `0029`, the older candidate-row rollback blocker still applies to any further downgrade. | Wave 9's preview-first [`migration_roundtrip_disposable.py`](../../scripts/phase-f/migration_roundtrip_disposable.py) resolves the real graph, snapshots and dumps current `tc` read-only, restores only into exact-comment empty loopback PostgreSQL 16, checks complete catalog/data/roles, executes exact `0030 → 0029 → 0030`, runs no-external API/write/worker smoke, and performs evidence-bound cleanup. | **LOCAL DISPOSABLE SUB-GATE PASS / TASK OPEN.** The authoritative run proved all surviving predecessor invariants and final logical catalog/normalized data/roles equal; only the pristine pointer's generated `updated_at` was normalized, while pg_dump physical dropped-column slot compaction was recorded separately. Current `tc`, Docker/database inventories and 27 prior evidence hashes were unchanged. A separately hosted staging copy, staging service/writer quiescence, operator handoff, broader boundaries and production RTO/RPO remain `NOT_RUN`; task/release gate stay unchecked. See [wave 9](./sprint-f1-wave9-verification.md). |
| `PF-SF1-T15` | Prior evidence covers localhost exact-origin preflight and individual physical-browser PUTs. It explicitly records multi-file browser flow as `NOT RUN` and says the deployed HTTPS origin remains a Phase F gate. | `cors-policy` rejects wildcards and validates the one-origin/PUT+GET+HEAD/signed-header policy. `cors-preflight` performs secret-safe read-only OPTIONS checks for all three methods and always reports physical-browser evidence as `NOT_RUN`. | **OPEN / NOT RUN.** The exact deployed HTTPS origin does not exist in supplied evidence; no policy was installed on a deployed bucket, no request was sent, and no physical browser multi-file upload→download→delete was performed. Provider analytics comparison also remains open. |

Key source boundaries:

- Benchmark guard and limitations: [`scripts/benchmark_cases.py`](../../scripts/benchmark_cases.py)
- R2 operational contract: [`deploy/object-storage/README.md`](../../deploy/object-storage/README.md)
- Metadata and restore endpoints: [`apps/api/src/tc_api/routers/object_storage.py`](../../apps/api/src/tc_api/routers/object_storage.py)
- Projection guard: [`backend/src/tc_backend/knowledge/rebuild.py`](../../backend/src/tc_backend/knowledge/rebuild.py)
- Existing failover boundary: [`deploy/knowledge/README.md`](../../deploy/knowledge/README.md)
- Current one-step rollback blocker: [`backend/migrations/versions/20260902_0030_lightrag_generation_control_plane.py`](../../backend/migrations/versions/20260902_0030_lightrag_generation_control_plane.py)
- Wave-9 local disposable harness: [`scripts/phase-f/migration_roundtrip_disposable.py`](../../scripts/phase-f/migration_roundtrip_disposable.py)
- Earlier candidate-evidence blocker: [`backend/migrations/versions/20260901_0029_quality_review_e7_remediation.py`](../../backend/migrations/versions/20260901_0029_quality_review_e7_remediation.py)
- Prior browser evidence boundary: [`docs/phase-a/phase-a-verification-report.md`](../phase-a/phase-a-verification-report.md)

## Operator prerequisites

Run from the repository root with the repository virtual environment installed. Database commands
also require PostgreSQL client tools (`psql`, `pg_dump`, and `pg_restore`) compatible with the
server. Load credentials from the approved secret manager into the named `TC_PHASE_F_*`
variables; do not paste secrets into a ticket or evidence file.

For each database, a database owner must attest the exact target before the wrapper will connect.
This example uses a deliberately disposable loopback database:

```bash
export TC_PHASE_F_BENCHMARK_DATABASE_URL='postgresql://<user>:<secret>@127.0.0.1:55432/tc_phase_f_cases_test_disposable'
PGDATABASE="$TC_PHASE_F_BENCHMARK_DATABASE_URL" psql -X -v ON_ERROR_STOP=1 \
  -c "COMMENT ON DATABASE tc_phase_f_cases_test_disposable IS 'tc-phase-f-rehearsal:disposable:tc-phase-f-cases-disposable'"
```

Installing the comment does not make a target safe by itself. The wrapper independently checks
the URL, connected database name, host/class, PostgreSQL version, comment, table count where
required, and Alembic revision.

## Exact rehearsal commands

Every mutating command is shown first in preview form. Review its JSON, confirm that the named
target is a newly created disposable database or an approved staging copy, then use the adjacent
execution command. Replace only credential/host paths and digest placeholders; keep the safe
target identities and confirmation strings synchronized.

### T06 — 100k synthetic case benchmark

The destination must be a freshly created empty loopback database with the attestation shown
above. The legacy query-only command below remains useful for comparison but intentionally does
not exercise Slack or generation load. Use the separate [concurrent T06 profile and exact
commands](./pf-sf1-t06-concurrent-load-profile.md#정확한-preview와-실행-명령) for the Phase F mixed
load gate. Wave 5 later ran that profile on local disposable PostgreSQL and passed its local gate;
the production-like/staging/operator gates remain **NOT RUN**.

```bash
node scripts/python.mjs scripts/phase-f/rehearse.py benchmark-cases \
  --target-name tc-phase-f-cases-disposable \
  --target-class disposable \
  --database-url-env TC_PHASE_F_BENCHMARK_DATABASE_URL \
  --database-name tc_phase_f_cases_test_disposable \
  --warmups 3 --runs 30 --target-ms 1000 \
  --output docs/phase-f/evidence/pf-sf1-t06-100k.json

node scripts/python.mjs scripts/phase-f/rehearse.py benchmark-cases \
  --target-name tc-phase-f-cases-disposable \
  --target-class disposable \
  --database-url-env TC_PHASE_F_BENCHMARK_DATABASE_URL \
  --database-name tc_phase_f_cases_test_disposable \
  --warmups 3 --runs 30 --target-ms 1000 \
  --output docs/phase-f/evidence/pf-sf1-t06-100k.json \
  --execute \
  --confirm 'RUN PF-SF1-T06 BENCHMARK-CASES ON tc-phase-f-cases-disposable'
```

### T07 — PostgreSQL backup and isolated restore

Use an approved staging copy as the source, never the production database. For a local proof-only
run, create a uniquely named empty loopback disposable source and run the preview-first
`postgres-seed` action; it migrates to head and installs fixed-ID/fixed-time, non-sensitive workspace,
folder, work-item, case/version/step/tag, cycle, issue/comment, and audit rows. Do not seed an approved
staging copy. The dump parent must already exist in approved encrypted storage, both the dump and JSON
evidence path must be new, and the dump must be outside this repository.

The harness prefers compatible host clients and otherwise uses `psql`, `pg_dump`, and `pg_restore`
inside the already-running `deploy/compose.dev.yml` PostgreSQL service. Compose receives `PG*` values
by environment-variable name, never value-bearing argv. Preview does not read the URL environment
variable or run a subprocess.

```bash
export TC_PHASE_F_BACKUP_DATABASE_URL='postgresql://<user>:<secret>@tc-stage-db.example.test:5432/tc_phase_f_backup_staging_copy?sslmode=verify-full'
PGDATABASE="$TC_PHASE_F_BACKUP_DATABASE_URL" psql -X -v ON_ERROR_STOP=1 \
  -c "COMMENT ON DATABASE tc_phase_f_backup_staging_copy IS 'tc-phase-f-rehearsal:staging-copy:tc-phase-f-backup-staging-copy'"

node scripts/python.mjs scripts/phase-f/rehearse.py postgres-backup \
  --target-name tc-phase-f-backup-staging-copy \
  --target-class staging-copy \
  --database-url-env TC_PHASE_F_BACKUP_DATABASE_URL \
  --database-name tc_phase_f_backup_staging_copy \
  --dump-file /approved/encrypted/tc-phase-f/20260904-staging-copy.dump \
  --output docs/phase-f/evidence/pf-sf1-t07-20260904-backup.json

node scripts/python.mjs scripts/phase-f/rehearse.py postgres-backup \
  --target-name tc-phase-f-backup-staging-copy \
  --target-class staging-copy \
  --database-url-env TC_PHASE_F_BACKUP_DATABASE_URL \
  --database-name tc_phase_f_backup_staging_copy \
  --dump-file /approved/encrypted/tc-phase-f/20260904-staging-copy.dump \
  --output docs/phase-f/evidence/pf-sf1-t07-20260904-backup.json \
  --execute \
  --confirm 'RUN PF-SF1-T07 POSTGRES-BACKUP ON tc-phase-f-backup-staging-copy'
```

Backup holds one exported `REPEATABLE READ READ ONLY` snapshot open while catalog/row fingerprints and
the custom dump are captured. Evidence contains the exact database/target/comment/head binding,
snapshot ID, database UTC timestamp, WAL LSN, transaction snapshot, measured backup time and
recovery-point age, final dump bytes/SHA-256, archive-list count/digest, versioned full catalog and
per-category digests, and all public-table row counts/SHA-256 values. Evidence loading rejects symlinks
before resolution and requires an exact mode-0600 executed-backup schema. The timestamp/age is a
measured recovery point only; it is not a promised RPO.

Create a separate empty restore database and attest it before running restore:

```bash
export TC_PHASE_F_RESTORE_DATABASE_URL='postgresql://<user>:<secret>@127.0.0.1:55432/tc_phase_f_restore_test_disposable'
PGDATABASE="$TC_PHASE_F_RESTORE_DATABASE_URL" psql -X -v ON_ERROR_STOP=1 \
  -c "COMMENT ON DATABASE tc_phase_f_restore_test_disposable IS 'tc-phase-f-rehearsal:disposable:tc-phase-f-restore-disposable'"

node scripts/python.mjs scripts/phase-f/rehearse.py postgres-restore \
  --target-name tc-phase-f-restore-disposable \
  --target-class disposable \
  --database-url-env TC_PHASE_F_RESTORE_DATABASE_URL \
  --database-name tc_phase_f_restore_test_disposable \
  --dump-file /approved/encrypted/tc-phase-f/20260904-staging-copy.dump \
  --backup-evidence docs/phase-f/evidence/pf-sf1-t07-20260904-backup.json \
  --output docs/phase-f/evidence/pf-sf1-t07-20260904-restore.json

node scripts/python.mjs scripts/phase-f/rehearse.py postgres-restore \
  --target-name tc-phase-f-restore-disposable \
  --target-class disposable \
  --database-url-env TC_PHASE_F_RESTORE_DATABASE_URL \
  --database-name tc_phase_f_restore_test_disposable \
  --dump-file /approved/encrypted/tc-phase-f/20260904-staging-copy.dump \
  --backup-evidence docs/phase-f/evidence/pf-sf1-t07-20260904-backup.json \
  --output docs/phase-f/evidence/pf-sf1-t07-20260904-restore.json \
  --execute \
  --confirm 'RUN PF-SF1-T07 POSTGRES-RESTORE ON tc-phase-f-restore-disposable'
```

Restore revalidates the final dump size/SHA and exact archive count/digest before connecting, requires
zero destination public tables, never uses `--clean`, and compares all canonical catalog categories and
every public-table row/count digest. It constructs the real FastAPI application with
`default_transaction_read_only=on`, verifies active read-only state, performs only the bounded GET
smoke, then recaptures all-table data and sequence state and requires equality. Compose fallback is
valid only for loopback URLs; absent host clients fail closed for remote staging-copy targets.
`restoreCommandSeconds` is command time; `rtoMeasuredThroughReadOnlyApplicationSmokeSeconds` is the
measured local restore-to-readable point, not a production RTO promise.

Do not accept this step alone. Start a separately hosted staging API that points to the restored
database and matching recovered R2 bucket, then run T08. Wave 6 proved only same-service local
disposable mechanics; daily scheduling, encrypted retention, separate-host/staging execution, matched
R2, and operator handoff remain open.

### T08 — object-storage metadata integrity and bounded byte restore

Wave 7 adds a separate local-only helper for a complete matched disposable rehearsal:
[`object_storage_recovery.py`](../../scripts/phase-f/object_storage_recovery.py). Its preview and
execute arguments name one generated recovery point, exact-comment source/recovery databases,
distinct source/recovery buckets, an object prefix, a mode-0600 dump/manifest/evidence set, and
credentials by environment-variable name. It accepts only loopback Compose PostgreSQL, MinIO, and
ClamAV and must not be pointed at Cloudflare R2 or staging. See the [wave-7 report](./sprint-f1-wave7-verification.md#exact-final-run)
for the final exact resource set, confirmations, hashes, metrics, and cleanup proof.

Preview `recover` before adding `--execute` and the exact printed confirmation. After successful
recovery, verify the immutable recovery evidence and manifest before previewing and executing
`cleanup`. Cleanup consumes the pinned evidence rather than accepting free-form resource names, then
revalidates exact comments/inventories/file identities before removing only those resources.

The wave-7 local MinIO run passed for 12 objects spanning both supported owner types and three fixture
MIME classes, with multiple items per cross-product and forced pagination. This is a local disposable
sub-gate only: it does not satisfy the still-open actual Cloudflare R2 recovery, versioning/backup,
separately hosted staging database, operator handoff, or production recovery-objective gates.

For the actual R2 gate, the API must be the isolated restored deployment, and its status response
must name both provider `R2` and the exact recovery bucket below. The `r2-verify` wrapper remains
R2-only and rejects redirects. If the internal API needs a bearer token, load it into
`TC_PHASE_F_API_TOKEN` and add `--api-token-env TC_PHASE_F_API_TOKEN` to both commands.

```bash
node scripts/python.mjs scripts/phase-f/rehearse.py r2-verify \
  --target-name tc-phase-f-storage-staging-copy \
  --target-class staging-copy \
  --api-base-url https://tc-stage.example.test \
  --bucket tc-phase-f-attachments-staging-copy \
  --sample-size 100 --max-bytes 52428800

node scripts/python.mjs scripts/phase-f/rehearse.py r2-verify \
  --target-name tc-phase-f-storage-staging-copy \
  --target-class staging-copy \
  --api-base-url https://tc-stage.example.test \
  --bucket tc-phase-f-attachments-staging-copy \
  --sample-size 100 --max-bytes 52428800 \
  --execute \
  --confirm 'RUN PF-SF1-T08 R2-VERIFY ON tc-phase-f-storage-staging-copy'
```

### T09 — PostgreSQL projection rebuild, LightRAG full-reindex inspection and fallback

Attest the staging-copy database as `tc-phase-f-knowledge-staging-copy`, then obtain fresh digests
with the read-only command:

```bash
export TC_PHASE_F_KNOWLEDGE_DATABASE_URL='postgresql://<user>:<secret>@tc-stage-db.example.test:5432/tc_phase_f_knowledge_staging_copy?sslmode=verify-full'
PGDATABASE="$TC_PHASE_F_KNOWLEDGE_DATABASE_URL" psql -X -v ON_ERROR_STOP=1 \
  -c "COMMENT ON DATABASE tc_phase_f_knowledge_staging_copy IS 'tc-phase-f-rehearsal:staging-copy:tc-phase-f-knowledge-staging-copy'"

node scripts/python.mjs scripts/phase-f/rehearse.py knowledge-preview \
  --target-name tc-phase-f-knowledge-staging-copy \
  --target-class staging-copy \
  --database-url-env TC_PHASE_F_KNOWLEDGE_DATABASE_URL \
  --database-name tc_phase_f_knowledge_staging_copy

node scripts/python.mjs scripts/phase-f/rehearse.py knowledge-preview \
  --target-name tc-phase-f-knowledge-staging-copy \
  --target-class staging-copy \
  --database-url-env TC_PHASE_F_KNOWLEDGE_DATABASE_URL \
  --database-name tc_phase_f_knowledge_staging_copy \
  --execute \
  --confirm 'RUN PF-SF1-T09 KNOWLEDGE-PREVIEW ON tc-phase-f-knowledge-staging-copy'
```

Copy `rebuildPlan.eventOrderDigest` and `rebuildPlan.sourceLedgerDigest` from that result. Configure
the approved staging Ollama endpoint and worker projection settings; `TC_ENCRYPTION_MASTER_KEY`
must also come from the staging secret manager. Then preview and apply using the same fresh
digests:

```bash
export TC_ENVIRONMENT=staging
export TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED=true
export TC_OLLAMA_BASE_URL='http://<approved-staging-ollama>:11434'
export TC_GENERATION_MODEL='qwen3:8b'
export TC_EMBEDDING_MODEL='bge-m3'
export TC_PHASE_F_EVENT_ORDER_DIGEST='<64-lowercase-hex-from-preview>'
export TC_PHASE_F_SOURCE_LEDGER_DIGEST='<64-lowercase-hex-from-preview>'

node scripts/python.mjs scripts/phase-f/rehearse.py knowledge-apply \
  --target-name tc-phase-f-knowledge-staging-copy \
  --target-class staging-copy \
  --database-url-env TC_PHASE_F_KNOWLEDGE_DATABASE_URL \
  --database-name tc_phase_f_knowledge_staging_copy \
  --event-order-digest "$TC_PHASE_F_EVENT_ORDER_DIGEST" \
  --source-ledger-digest "$TC_PHASE_F_SOURCE_LEDGER_DIGEST"

node scripts/python.mjs scripts/phase-f/rehearse.py knowledge-apply \
  --target-name tc-phase-f-knowledge-staging-copy \
  --target-class staging-copy \
  --database-url-env TC_PHASE_F_KNOWLEDGE_DATABASE_URL \
  --database-name tc_phase_f_knowledge_staging_copy \
  --event-order-digest "$TC_PHASE_F_EVENT_ORDER_DIGEST" \
  --source-ledger-digest "$TC_PHASE_F_SOURCE_LEDGER_DIGEST" \
  --execute \
  --confirm 'RUN PF-SF1-T09 KNOWLEDGE-APPLY ON tc-phase-f-knowledge-staging-copy'
```

The independent disposable fallback rehearsal remains a separate command:

```bash
node scripts/python.mjs scripts/phase-f/rehearse.py lightrag-fallback \
  --target-name tc-phase-f-lightrag-disposable \
  --target-class disposable

node scripts/python.mjs scripts/phase-f/rehearse.py lightrag-fallback \
  --target-name tc-phase-f-lightrag-disposable \
  --target-class disposable \
  --execute \
  --confirm 'RUN PF-SF1-T09 LIGHTRAG-FALLBACK ON tc-phase-f-lightrag-disposable'
```

위 projection/fallback 명령 자체는 LightRAG auxiliary corpus를 다시 만들지 않는다. 별도의
[preview-first full-reindex/checksum 계약과 read-only inspector](./pf-sf1-t09-lightrag-full-reindex.md)는
exact authoritative/auxiliary target, source/projection/pgvector/corpus digest, bounded export, fresh
generation과 atomic activation/rollback 요구를 고정한다. TC-owned durable pointer/controller/resolver와
credential-owning subprocess factory는 실제 API `create_app()`와 worker `build_worker()` startup에서
complete `TC_LIGHT_RAG_*` opt-in일 때 합성된다. 기본값은 disabled이고 repository evidence에는
reviewed bootstrap/secret-manager staging config가 없으며 apply entrypoint도 제공하지 않는다.
Required setting, secret, per-generation child lifecycle과 failure boundary는
[knowledge deploy contract](../../deploy/knowledge/README.md#apiworker-공통-opt-in-설정)를 따른다.
Wave 8은 별도 local-only
[`lightrag_full_reindex_disposable.py`](../../scripts/phase-f/lightrag_full_reindex_disposable.py)와
Compose로 official storage initialization/index, complete supported fixture corpus checksum,
API/worker cutover/query/citation smoke, stale/provider/child/activation failure, CAS rollback,
retention과 exact cleanup을 통과했다. 상세 명령과 evidence hash는
[wave 8 report](./sprint-f1-wave8-verification.md)에 있다. 그러나 staging full-corpus 실행,
approved staging bootstrap/credential, live provider, production-like networking/load/alert,
operator handoff와 production RTO/RPO는 모두 `NOT_RUN`이므로 T09를 닫지 않는다.

### T10 — current migration one-step rollback/roll-forward

Wave 9 provides a credential-free local sub-gate which may only use the current local `tc` database
as a read-only source. It resolves head/predecessor from the graph, starts its own exact-name
loopback PostgreSQL 16 target, attests that target empty, holds one read-only exported snapshot while
capturing the fingerprint and pinned dump, restores the dump, mutates only that restored copy, then
seals evidence and performs evidence-bound cleanup. Preview is the default and performs no Docker,
database or filesystem mutation.

```bash
node scripts/python.mjs scripts/phase-f/migration_roundtrip_disposable.py rehearse \
  --run-id w9rt7c3b --port 55529 \
  --temp-directory /private/tmp/pf-sf1-t10-wave9-w9rt7c3b \
  --dump-file /private/tmp/pf-sf1-t10-wave9-w9rt7c3b/pf-sf1-t10-wave9-w9rt7c3b.dump \
  --output docs/phase-f/evidence/pf-sf1-t10-wave9-local-disposable-w9rt7c3b-20260907.json

node scripts/python.mjs scripts/phase-f/migration_roundtrip_disposable.py rehearse \
  --run-id w9rt7c3b --port 55529 \
  --temp-directory /private/tmp/pf-sf1-t10-wave9-w9rt7c3b \
  --dump-file /private/tmp/pf-sf1-t10-wave9-w9rt7c3b/pf-sf1-t10-wave9-w9rt7c3b.dump \
  --output docs/phase-f/evidence/pf-sf1-t10-wave9-local-disposable-w9rt7c3b-20260907.json \
  --execute \
  --confirm 'RUN PF-SF1-T10 LOCAL-DISPOSABLE-MIGRATION-ROUNDTRIP ON tc-phase-f-pf-sf1-t10-wave9-w9rt7c3b-disposable'
```

Do not reuse that completed run identity or overwrite its evidence. Generate a fresh eight-character
lowercase hexadecimal run ID, a new unbound non-default port, new `/private/tmp` paths, and new evidence
names for another local rehearsal. After verifying the rehearsal evidence, preview and then execute
cleanup with the same exact run ID, port, and evidence binding:

```bash
node scripts/python.mjs scripts/phase-f/migration_roundtrip_disposable.py cleanup \
  --run-id w9rt7c3b --port 55529 \
  --rehearsal-evidence docs/phase-f/evidence/pf-sf1-t10-wave9-local-disposable-w9rt7c3b-20260907.json \
  --output docs/phase-f/evidence/pf-sf1-t10-wave9-cleanup-w9rt7c3b-20260907.json

node scripts/python.mjs scripts/phase-f/migration_roundtrip_disposable.py cleanup \
  --run-id w9rt7c3b --port 55529 \
  --rehearsal-evidence docs/phase-f/evidence/pf-sf1-t10-wave9-local-disposable-w9rt7c3b-20260907.json \
  --output docs/phase-f/evidence/pf-sf1-t10-wave9-cleanup-w9rt7c3b-20260907.json \
  --execute \
  --confirm 'RUN PF-SF1-T10 LOCAL-DISPOSABLE-CLEANUP ON tc-phase-f-pf-sf1-t10-wave9-w9rt7c3b-disposable'
```

Any generation/transition evidence or non-pristine active pointer makes the `0030` downgrade unsafe
and stops the run; deleting or rewriting those rows is never an acceptable workaround. The earlier
staging-copy wrapper remains only a preparation aid. A separately hosted staging copy still requires
approved credentials, writer/worker quiescence, deployment-level service smoke and operator review;
none of those gates ran in Wave 9.

### T15 — deployed HTTPS R2 CORS preparation

Copy the example policy to the reviewed deployment configuration and replace the example origin
with the one exact deployed staging HTTPS origin. Validate it before installation:

```bash
node scripts/python.mjs scripts/phase-f/rehearse.py cors-policy \
  --target-name tc-phase-f-web-staging-copy \
  --target-class staging-copy \
  --web-origin https://tc-stage.example.test \
  --policy-file deploy/operations/phase-f-r2-cors.staging.example.json
```

Policy installation is intentionally outside this toolkit because no deployed bucket or approved
Cloudflare deployment runner was supplied. After an operator installs the reviewed policy and an
approved staging flow produces a short-lived presigned URL, keep that URL only in a secret
environment variable and run read-only preflights:

```bash
export TC_PHASE_F_R2_PRESIGNED_URL='<short-lived-staging-presigned-url>'

node scripts/python.mjs scripts/phase-f/rehearse.py cors-preflight \
  --target-name tc-phase-f-web-staging-copy \
  --target-class staging-copy \
  --web-origin https://tc-stage.example.test \
  --presigned-url-env TC_PHASE_F_R2_PRESIGNED_URL

node scripts/python.mjs scripts/phase-f/rehearse.py cors-preflight \
  --target-name tc-phase-f-web-staging-copy \
  --target-class staging-copy \
  --web-origin https://tc-stage.example.test \
  --presigned-url-env TC_PHASE_F_R2_PRESIGNED_URL \
  --execute \
  --confirm 'RUN PF-SF1-T15 CORS-PREFLIGHT ON tc-phase-f-web-staging-copy'

unset TC_PHASE_F_R2_PRESIGNED_URL
```

OPTIONS success is not browser evidence. T15 still requires a physical browser at that exact
origin to select multiple files in one chooser event and verify upload, download, and delete, with
PUT/GET/HEAD preflight evidence and no wildcard CORS.

## Non-destructive verification performed for this preparation

```text
node scripts/python.mjs -m pytest scripts/phase-f/tests/test_rehearse.py -q
26 passed

node scripts/python.mjs -m ruff check scripts/phase-f
All checks passed!

node scripts/python.mjs -m pytest -q \
  scripts/tests/test_rehearse_lightrag_failover.py \
  apps/api/tests/test_object_storage_runtime.py \
  backend/tests/unit/test_object_storage.py \
  backend/tests/unit/test_lightrag_adapter.py \
  backend/tests/unit/test_migration_graph.py \
  scripts/phase-f/tests/test_rehearse.py
71 passed
```

Waves 5 through 9 added local disposable execution evidence; see their dedicated reports for full
commands, measurements, hashes, and cleanup proof. Wave 7's provider is MinIO and must not be read as
Cloudflare R2 evidence; Waves 8 and 9 are also local-only. No external environment evidence is attached. In particular, a
staging/separately hosted database restore, encrypted retained backup, daily schedule, actual recovered
R2 bucket/versioning/backup, staging projection/migration run, deployed HTTPS CORS, physical browser
behavior, operator handoff, production RTO/RPO, and provider/alert evidence all remain explicitly open.
