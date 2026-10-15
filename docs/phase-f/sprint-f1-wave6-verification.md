# Phase F Sprint F1 wave 6 T07 local recovery verification

- Verification date: 2026-09-04 (Asia/Seoul)
- Scope: `PF-SF1-T07` local loopback disposable backup/restore sub-gate and second independent RED remediation
- Verdict: **SECOND RED REMEDIATED; LOCAL LOOPBACK DISPOSABLE PASS; `PF-SF1-T07` REMAINS UNCHECKED**

Daily scheduling, encrypted retention, separately hosted staging restore, matched Cloudflare R2 recovery,
operator handoff, and production RTO/RPO are all **`NOT_RUN`**. No staging/live database, scheduler,
R2 bucket, Slack API, provider, deployment, or production surface was called.

## Second independent RED and remediation

The first independent RED had already expanded the database comparator to catalog v3 and enforced a
read-only application transaction. A second independent verifier then found two remaining fail-open
boundaries:

1. Backup evidence and dump checks reopened the pathname between JSON, SHA, archive inventory, and
   restore. The checks only retained device/inode/size partially, so same-inode in-place mutation,
   hard links, broader modes, and replacement races were not all closed.
2. The post-smoke comparator reduced the schema side to a sequence digest. A constraint or other
   non-sequence catalog change after the API smoke could therefore escape the immutability gate, and
   `postSmokeFingerprint` was abbreviated.

The second remediation opens each evidence or dump file once with `O_RDONLY|O_NOFOLLOW`, requires a
regular file, permissions no broader than 0600, and exactly one hard link, and pins
device/inode/size/mtime-ns/ctime-ns. It compares both descriptor and pathname metadata before and after
JSON read/parse, SHA-256, `pg_restore --list`, and actual `pg_restore`. Both PostgreSQL commands consume
the already-open descriptor through stdin; neither reopens the dump pathname. The evidence records the
full identity and stage list for the backup JSON and dump.

The post-smoke comparison now uses the complete fingerprint without projection:
`catalogFingerprintVersion`, `schemaSha256`, all 16 `schemaCategorySha256` values, all 16
`schemaObjectCounts`, `dataSha256`, `tableCount`, `totalRows`, and all 78 per-table row-count/digest
entries. The full post-smoke value is recorded as `postSmokeFingerprint`. Regression tests mutate the
same evidence inode immediately after `json.load` returns, mutate the same dump inode immediately after
SHA returns, reject mode 0640 and multiple links, and fail when only the constraint/schema digests
change after smoke.

## Exact retry6 local targets and commands

Only these new databases on the loopback-bound Compose PostgreSQL service were in scope:

| Role | Exact database | Exact database comment |
|---|---|---|
| source | `tc_phase_f_t07_wave6_retry6_b72f64c9_source_test_disposable` | `tc-phase-f-rehearsal:disposable:tc-phase-f-t07-wave6-retry6-b72f64c9-source-disposable` |
| restore | `tc_phase_f_t07_wave6_retry6_b72f64c9_restore_test_disposable` | `tc-phase-f-rehearsal:disposable:tc-phase-f-t07-wave6-retry6-b72f64c9-restore-disposable` |

Each action was previewed before `--execute`; execution used exactly these generated confirmations:

```text
RUN PF-SF1-T07 POSTGRES-SEED ON tc-phase-f-t07-wave6-retry6-b72f64c9-source-disposable
RUN PF-SF1-T07 POSTGRES-BACKUP ON tc-phase-f-t07-wave6-retry6-b72f64c9-source-disposable
RUN PF-SF1-T07 POSTGRES-RESTORE ON tc-phase-f-t07-wave6-retry6-b72f64c9-restore-disposable
```

The historical retry5 cycle's first seed execute attempt used a plain `postgresql://` SQLAlchemy URL
and failed before the first migration because the unavailable `psycopg2` driver was selected. It wrote
no evidence and left that source at zero public tables; both facts were checked before the successful
retry5 run. Retry6 used the installed `postgresql+psycopg://` driver from the outset, migrated a new
empty exact-attested source to `20260902_0030`, and installed the fixed-ID/fixed-time, non-sensitive
40-row representative ledger.

## Authoritative retry6 results

The new `retry6-final` backup and restore passed all eight source/restore comparisons and all eight
full post-smoke comparisons. Source, restored, and post-smoke fingerprints all report catalog v3,
16 complete categories, 78 public tables, 40 rows, catalog digest
`8846c25f73faecdc56220b9597a399b5e44a41202b0dc4ac1eec5dc18414ed62`, and data digest
`28eb0dee7a6a684d921cf251898e72802e620875884a98a7ff9fc59be9e1fc3d`.

Catalog counts were 78 relations, 945 columns, 738 constraints, 260 indexes, 106 triggers,
230 routines/aggregates, 3 explicit public types, 1 sequence, and 2 extensions. Policies,
views/materialized views, partitions/inheritance, public collations, rules, and extended statistics
were observed as complete zero-count categories, not omitted checks.

The custom archive was 605,484 bytes with SHA-256
`0df58e095a88533ab419f1d7c433d4a0fd65f011e0a13c109dd913e133105bcf`.
`pg_restore --list` returned 791 entries with listing digest
`2b256f661cf02c962238aa28b2e341385b9e055bc156b20bd018912314441665`.
The dump identity recorded by backup exactly matched the identity opened by restore. Backup consumption
validated SHA and inventory around the same descriptor; restore validated SHA, inventory, and actual
restore around its same descriptor. Backup evidence JSON was independently pinned around JSON
read/parse.

Backup took 0.643859 s. Restore command time was 4.011901 s and restore-start through read-only
application smoke was 6.526439 s. The recovery point was `2026-09-04T07:41:50.262004Z`, WAL
`1/7DA5A4E0`; its age at smoke was 30.773653 s. These are local measurements, not production promises.

Health, readiness, workspace, work-item, and test-case GETs returned 200; readiness was `ready`, with
2 work items and 3 test cases. The engine verified default and active transaction read-only state.
The complete post-smoke fingerprint equals the restored fingerprint, including every catalog version,
schema/category digest, category count, table/data digest, and per-table value.

## Evidence index and history

The new authoritative artifacts are regular, single-link, mode-0600 files:

| Artifact | Status | Bytes | SHA-256 |
|---|---|---:|---|
| [`...retry6-final-seed.json`](./evidence/pf-sf1-t07-wave6-20260904-b72f64c9-retry6-final-seed.json) | authoritative retry6 seed | 17,416 | `d3984780a8b3cc841f5c8124ed391e1ef2bc6feaaae070a408af63de9316eb91` |
| [`...retry6-final-backup.json`](./evidence/pf-sf1-t07-wave6-20260904-b72f64c9-retry6-final-backup.json) | authoritative final-code pinned dump/catalog evidence | 18,426 | `c49a8c52fe6fb10674f8f8a6a591b229c1c6271a81669094fd942459c1705cc7` |
| [`...retry6-final-restore.json`](./evidence/pf-sf1-t07-wave6-20260904-b72f64c9-retry6-final-restore.json) | authoritative final-code pinned restore/full post-smoke evidence | 53,046 | `0e197c1796ad39be92cf2254bfbd92e5bb91fbe0ce2a05edca1ea09cd3b37f4b` |

All eleven earlier wave-6 artifacts remain byte-for-byte unchanged. The four `b6c91d4a` files are the
original historical evidence; the retry3 pair is intermediate; and `retry4-final` remains explicit
**historical superseded first-remediation evidence**. The complete retry5 triplet is now explicit
**historical superseded second-remediation evidence** because archive inventory staging changed after
it was created; this final-code retry6 cycle supersedes it. Nothing was overwritten or deleted. The
ambiguous `-r2` names still mean historical retry 2, never completed Cloudflare R2 work.

## Verification and cleanup

| Check | Result |
|---|---|
| focused/live adversarial `test_rehearse.py` | 38 passed, including live CHECK/index/sequence changes and same-inode evidence/dump mutation regressions |
| focused Ruff / mypy | passed / no issues across 130 source-aware files including `rehearse.py` |
| `pnpm test:migration` | 78 passed; one existing collection warning |
| selected Sprint 1–4 PostgreSQL API files | 12 passed in 17.47 s |
| `pnpm check` | passed; Python 634 passed/1 skipped/109 deselected, web 191 passed/1 skipped, contracts 128, API client 23, UI 6, standard mypy 144 files |
| `pnpm test:operations` | 44 passed; 14 operations sections validated |
| UAT manifest | 33 automated checks, 15 manual checks, 15 workflows; SHA-256 `534fb9f66ffadedbbcf06a53e46384ec95fbc380ec06fd9012b13cb25cb665a7` |
| evidence verification | all 3 new files parsed, were regular single-link mode-0600 files, matched the bytes/hashes above, and passed the secret/URL scan; all 11 historical hashes were unchanged |

Two auxiliary validation invocations were intentionally not hidden: the first cleanup helper compared
JSON objects by serialized key order and reported a false mismatch despite printing identical dump
identity fields and SHA; the corrected field-by-field check passed before cleanup. A standalone mypy
call without repository source roots reported `tc_api` as an untyped installed import; the workspace
source-aware command then passed all 130 sources with no issues. Neither invocation changed evidence,
the dump, or a database.

Cleanup revalidated each exact database name/comment and zero active sessions, revalidated the exact
dump identity against backup evidence, dropped only the two literal retry6 database names, unlinked
only the exact dump, and removed only its empty exact temporary directory. The final catalog result was
zero matching retry6 databases and one preserved `tc` database; dump and temporary directory were both
absent. No wildcard, recursive deletion, volume removal, Compose stop, or broad schema cleanup ran.

## Remaining gates — all `NOT_RUN`

1. Approved daily scheduler execution, failure alerting, and owner acknowledgement.
2. Approved encrypted backup storage, access control, retention, expiry, and recovery policy.
3. Separately hosted production-like staging source/restore and repeated measurements.
4. Matched Cloudflare R2 recovery-point inventory and byte-integrity drill.
5. Independent operator/reviewer handoff and sign-off.
6. Production RTO/RPO objectives supported by repeated approved evidence.

The local disposable sub-gate is PASS. `PF-SF1-T07`, every external gate above, and the Phase F release
gate remain unchecked/open.
