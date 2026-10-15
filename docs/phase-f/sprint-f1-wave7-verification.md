# Phase F Sprint F1 wave 7 T08 local object-storage recovery verification

- Verification date: 2026-09-04 (Asia/Seoul)
- Scope: `PF-SF1-T08` matched PostgreSQL attachment metadata and S3-compatible object recovery on
  loopback Compose PostgreSQL, MinIO, and ClamAV
- Verdict: **LOCAL MINIO DISPOSABLE SUB-GATE PASS; `PF-SF1-T08` AND PHASE F REMAIN UNCHECKED**

This execution did not contact Cloudflare R2, staging/live endpoints, Slack, model providers, or a
deployment. It is evidence for local MinIO mechanics only. An actual recovered Cloudflare R2
bucket, R2 versioning/backup proof, a matched separately hosted staging database, operator handoff,
and production recovery objectives are all **`NOT_RUN`**.

## Implementation and fail-closed contract

[`object_storage_recovery.py`](../../scripts/phase-f/object_storage_recovery.py) adds preview-first
`recover` and `cleanup` actions for one exact `pf-sf1-t08-wave7` run. The helper accepts only a
loopback `tc` PostgreSQL administrator connection, a credential-free loopback HTTP object-store
origin, literal generated `*_test_disposable` database names, exact database comments, distinct
generated source/recovery bucket names, and a matching generated prefix. Execution requires the
confirmation printed by preview. It refuses existing/non-empty destinations and never uses
`pg_restore --clean` or overwrites an object, dump, manifest, or evidence file.

The deterministic fixture contains 12 objects: two objects for every cross-product of the two owner
types supported by the attachment model (`EXECUTION_ATTEMPT`, `ISSUE`) and three MIME classes present
in the fixture (`application/json`, `image/png`, `text/plain`). The object list limit is three, so
the S3-compatible inventory spans five list pages; both API attachment and metadata verification
span four pages. Every manifest item retains the attachment and owner identities, object key,
content type, byte size, SHA-256, recovery-point identity, and deterministic fixture identity.

The source and recovery buckets are distinct and the recovery bucket begins empty. The rehearsal
creates the source database at current migration head, uploads and locally ClamAV-scans every source
object, takes a descriptor-pinned PostgreSQL custom dump, writes a non-overwriting manifest, restores
the database into an empty destination, and copies the objects without overwrite. It then requires:

- exact source/restored full PostgreSQL catalog and data fingerprints;
- exact source/restored attachment metadata and source/recovered bucket inventory digests;
- HEAD size, MIME, and SHA metadata for all 12 objects;
- full-byte SHA verification for the deterministic 12-object sample, covering every owner type and
  MIME class with no empty sampling;
- status `READY`, provider `MINIO`, exact recovery bucket, privacy `PRIVATE`, ClamAV fail-closed scan
  policy, proxy download, and presigned-PUT upload;
- all-page dry-run metadata verification and all-page attachment enumeration with no mutation,
  incomplete page chain, or cursor cycle; and
- private anonymous denial without a redirect for both source and recovery buckets.

The product's persisted attachment enum remains the existing `R2` storage contract; the runtime
provider used and reported by this drill is `MINIO`. Nothing in this report or its JSON artifacts is
Cloudflare R2 execution evidence.

Focused adversarial tests reject an unsafe name, external/R2 endpoint, duplicate key, missing or
unexpected object, corrupt bytes, wrong MIME/size/SHA, empty or coverage-incomplete sampling,
incomplete pagination, cursor cycles, manifest tamper, and a non-empty recovery destination. The
existing `r2-verify` command remains a strict R2-only wrapper with exact provider/bucket matching and
redirect rejection; the new provider-neutral `object-storage-verify` path permits MinIO only for a
disposable loopback target and rejects staging MinIO.

## Exact final run

The final recovery point was `pf-sf1-t08-wave7-a47d91c6`. Only these newly generated resources were
mutated:

| Role | Exact resource | Exact attestation/scope |
|---|---|---|
| source database | `tc_phase_f_pf_sf1_t08_wave7_a47d91c6_source_test_disposable` | `tc-phase-f-object-storage-recovery:v1:pf-sf1-t08-wave7-a47d91c6:source:test-disposable` |
| recovery database | `tc_phase_f_pf_sf1_t08_wave7_a47d91c6_recovery_test_disposable` | `tc-phase-f-object-storage-recovery:v1:pf-sf1-t08-wave7-a47d91c6:recovery:test-disposable` |
| source bucket | `tc-phase-f-pf-sf1-t08-wave7-a47d91c6-source-disposable` | prefix `pf-sf1-t08-wave7/a47d91c6/objects/` |
| recovery bucket | `tc-phase-f-pf-sf1-t08-wave7-a47d91c6-recovery-disposable` | same recovery-point prefix, initially empty |
| dump/temp | `/private/tmp/pf-sf1-t08-wave7-a47d91c6.dump` and `/private/tmp/pf-sf1-t08-wave7-a47d91c6-temp` | exact attested file/directory identities |

The administrator URL and MinIO credentials were supplied only through the named
`TC_PHASE_F_ADMIN_DATABASE_URL`, `TC_PHASE_F_MINIO_ACCESS_KEY`, and
`TC_PHASE_F_MINIO_SECRET_KEY` process variables. The actual values were neither printed nor written
to evidence or documentation. The preview and execution used the same argument set below; execution
added only `--execute` and its exact confirmation:

```text
node scripts/python.mjs scripts/phase-f/object_storage_recovery.py recover \
  --target-name tc-phase-f-pf-sf1-t08-wave7-a47d91c6-disposable \
  --target-class disposable \
  --recovery-point pf-sf1-t08-wave7-a47d91c6 \
  --source-database-name tc_phase_f_pf_sf1_t08_wave7_a47d91c6_source_test_disposable \
  --recovery-database-name tc_phase_f_pf_sf1_t08_wave7_a47d91c6_recovery_test_disposable \
  --source-bucket tc-phase-f-pf-sf1-t08-wave7-a47d91c6-source-disposable \
  --recovery-bucket tc-phase-f-pf-sf1-t08-wave7-a47d91c6-recovery-disposable \
  --object-prefix pf-sf1-t08-wave7/a47d91c6/objects/ \
  --dump-file /private/tmp/pf-sf1-t08-wave7-a47d91c6.dump \
  --temp-directory /private/tmp/pf-sf1-t08-wave7-a47d91c6-temp \
  --manifest-output docs/phase-f/evidence/pf-sf1-t08-wave7-20260904-a47d91c6-final-manifest.json \
  --output docs/phase-f/evidence/pf-sf1-t08-wave7-20260904-a47d91c6-final-recovery.json \
  --admin-database-url-env TC_PHASE_F_ADMIN_DATABASE_URL \
  --endpoint-url <credential-free-loopback-minio-origin> \
  --minio-access-key-env TC_PHASE_F_MINIO_ACCESS_KEY \
  --minio-secret-key-env TC_PHASE_F_MINIO_SECRET_KEY

RUN PF-SF1-T08 LOCAL-OBJECT-STORAGE-RECOVERY ON tc-phase-f-pf-sf1-t08-wave7-a47d91c6-disposable
```

The exact cleanup was also previewed first, consumed the pinned recovery evidence and manifest, and
then used this confirmation:

```text
node scripts/python.mjs scripts/phase-f/object_storage_recovery.py cleanup \
  --target-name tc-phase-f-pf-sf1-t08-wave7-a47d91c6-disposable \
  --target-class disposable \
  --recovery-evidence docs/phase-f/evidence/pf-sf1-t08-wave7-20260904-a47d91c6-final-recovery.json \
  --output docs/phase-f/evidence/pf-sf1-t08-wave7-20260904-a47d91c6-final-cleanup.json \
  --admin-database-url-env TC_PHASE_F_ADMIN_DATABASE_URL \
  --endpoint-url <credential-free-loopback-minio-origin> \
  --minio-access-key-env TC_PHASE_F_MINIO_ACCESS_KEY \
  --minio-secret-key-env TC_PHASE_F_MINIO_SECRET_KEY

RUN PF-SF1-T08 LOCAL-OBJECT-STORAGE-CLEANUP ON tc-phase-f-pf-sf1-t08-wave7-a47d91c6-disposable
```

Two earlier attempts are retained as immutable fail-closed evidence. Retry 1 stopped when the source
fixture violated the test-cycle transition contract; retry 2 stopped when it tried to create a PASS
attempt without first inserting UNEXECUTED. In both cases, the newly created exact source objects,
source bucket, source database, and temporary artifacts were removed after failure evidence was
pinned and verified. The final implementation seeds the legal transitions before producing the
authoritative `a47d91c6` evidence.

## Authoritative results

| Check | Result |
|---|---|
| fixture | 12 objects; 2 owner types; 3 MIME classes; 2 objects per owner/MIME pair |
| source/recovered object inventory | 12/12; canonical digest `8b2667109399c2cd2916d7abb983f957737e0b282dac46198ff24201be4bda9f` |
| source/restored attachment metadata | 12/12; canonical digest `fe6c103995ce055c0fed582f054fa30c756f3ffef70d2d675098676e1f6737f2` |
| full database | catalog v3, 16 categories, 78 tables, 53 rows; schema digest `8846c25f73faecdc56220b9597a399b5e44a41202b0dc4ac1eec5dc18414ed62`; data digest `6dedcc6302eab8442029612d712d920f5b46b9c3983e7e79e92ba0e03d8dc7e8` |
| dump | 606,634 bytes; SHA-256 `8d8bdd653a20255c38b30ded23ee824a53f9aa0b813af9d65b777464a7944d51`; 791 archive entries |
| API metadata dry-run | 12 scanned, 12 consistent, 0 mismatch, 0 failed, 4 pages |
| API byte sample | 12 sampled, 12 verified, 0 failed; all owner/MIME classes covered |
| privacy/runtime policy | anonymous 403, no redirect; provider `MINIO`; exact bucket; `PRIVATE`; `FAIL_CLOSED_CLAMAV` |
| canonical bindings | manifest/file, DB fingerprint, metadata, and inventory bindings all true |

The manifest contract digest is
`4c45078950149bd5666386b12b8c46137d7f06d962ac9a5a60d64e967a27cfc2`; the recovery evidence
contract digest is `714c327d5c321b4ac28824b8ab5f5834af1e1cd81a59de536602919d5f25a203`.
The local ClamAV scan, PostgreSQL restore, whole rehearsal, and recovery-point-age measurements were
0.489148, 1.401051, 8.790218, and 5.802947 seconds respectively. These are single local measurements,
not RTO/RPO promises.

## Regression and validator results

| Exact command/scope | Result |
|---|---|
| `pytest` on `test_object_storage_recovery.py`, `test_rehearse.py`, and object-storage unit tests | 70 passed, 1 opt-in catalog test skipped |
| Ruff on the changed Python files | all checks passed |
| clean focused mypy on the recovery helper and Phase-F rehearsal controller | deterministic wrapper passed twice; no issues in 2 files on both fresh/no-incremental runs |
| opt-in loopback MinIO/ClamAV integration test | authoritative rerun 1 passed in generated exact `tc-phase-f-pf-sf1-t08-wave7-c8d71a02-source-disposable`; bucket started empty and was deleted after its two test objects were removed |
| object-storage PostgreSQL API, Sprint 4 repository, head-capability, and attachment invariant tests on a generated exact-comment wave-7 test DB | 13 passed; exact test DB previewed, created, attested, then previewed and dropped; final count 0 |
| `pnpm check` | passed; Python 651 passed/1 skipped/109 deselected, Web 191 passed/1 skipped, contracts 128, API client 23, UI 6, mypy 145 sources, operation/OpenAPI checks green |
| `pnpm test:operations` (included in `pnpm check` and rerun standalone) | 44 passed; 14 operations sections validated |
| `pnpm phase-f:uat:validate` | 33 automated checks, 15 manual checks, 15 workflows; manifest SHA-256 `534fb9f66ffadedbbcf06a53e46384ec95fbc380ec06fd9012b13cb25cb665a7` |

### Independent verifier RED and deterministic remediation

The independent verifier correctly reported the original documented command as **RED**. In a clean
environment it exited 1 with four `import-untyped` errors: `tc_api.main` at
`scripts/phase-f/rehearse.py:2167`, plus `tc_api.main`, `tc_api.object_storage_runtime`, and
`tc_backend.infrastructure.object_storage` at `scripts/phase-f/object_storage_recovery.py:63-65`.
Ruff still passed and the focused pytest run still reported 70 passed and one opt-in skip; the mypy
failure nevertheless invalidated the earlier focused-mypy PASS claim until it was remediated.

The root cause was environment-dependent internal package resolution. `tc-api` and `tc-backend` are
installed editable in the repository virtual environment but do not publish `py.typed` markers, so
a focused mypy invocation that names only the two scripts sees those imports as untyped installed
packages when `MYPYPATH` is absent. An environment with the internal source roots on `MYPYPATH`
instead resolves them as source, which explains the earlier/independent mismatch; the documented
command also permitted incremental cache state and therefore did not define a clean verification.

The four intentionally out-of-scope internal imports now carry only the specific
`# type: ignore[import-untyped]` code; no global missing-import setting or unrelated typing rule was
weakened. [`verify-wave7-mypy.mjs`](../../scripts/phase-f/verify-wave7-mypy.mjs) makes the focused
gate cross-platform and deterministic by removing ambient `MYPYPATH` and invoking mypy with
`--no-incremental`. This exact remediation command was run twice from the repository root, and both
runs returned `Success: no issues found in 2 source files`:

```text
node scripts/phase-f/verify-wave7-mypy.mjs
```

No schema changed in wave 7. The two selected migration tests nevertheless confirmed current-head
capabilities and the attachment/integration physical invariants on the disposable test database.
The exact non-secret test commands were:

```text
node scripts/python.mjs -m ruff check scripts/phase-f/object_storage_recovery.py \
  scripts/phase-f/rehearse.py scripts/phase-f/tests/test_object_storage_recovery.py \
  scripts/phase-f/tests/test_rehearse.py \
  backend/src/tc_backend/infrastructure/object_storage/adapter.py \
  backend/tests/unit/test_object_storage.py
node scripts/phase-f/verify-wave7-mypy.mjs
node scripts/python.mjs -m pytest -q scripts/phase-f/tests/test_object_storage_recovery.py \
  scripts/phase-f/tests/test_rehearse.py backend/tests/unit/test_object_storage.py
node scripts/python.mjs -m pytest -q backend/tests/integration/test_object_storage_minio.py
node scripts/python.mjs -m pytest -q apps/api/tests/test_object_storage_api_postgresql.py \
  backend/tests/repository/test_sprint4_postgresql.py \
  backend/tests/repository/test_migrations_postgresql.py::test_migrations_seed_schema_and_database_capabilities \
  backend/tests/repository/test_migrations_postgresql.py::test_issue_and_drive_migration_installs_physical_invariant_guards
pnpm check
pnpm test:operations
pnpm phase-f:uat:validate
```

The MinIO/ClamAV and PostgreSQL test connection values were injected into their documented process
environment names and were not printed or persisted.

One preliminary invocation of the existing opt-in integration test used its pre-existing default
test bucket before the generated bucket override was installed. The test's `finally` block deleted
only its two time-namespaced objects; it did not clear or delete the bucket. The authoritative rerun
then used the generated exact wave-7 bucket above, and the final direct inventory digest proved the
pre-existing bucket was unchanged. This preliminary scope deviation is retained here rather than
being represented as wave-7 recovery evidence.

## Evidence and cleanup

All five new JSON artifacts parse successfully and are regular, single-link, mode-0600 files:

| Artifact | Status | Bytes | SHA-256 |
|---|---|---:|---|
| [`...retry1-failure.json`](./evidence/pf-sf1-t08-wave7-20260904-d31c8e7a-retry1-failure.json) | immutable fail-closed history | 4,553 | `1c673b8f9ead029d1656a9ccf08f9e8b4a1b95996751d2586e71f427c8c17593` |
| [`...retry2-failure.json`](./evidence/pf-sf1-t08-wave7-20260904-e29f6c4b-retry2-failure.json) | immutable fail-closed history | 4,535 | `87e6d9a901aa36c5389bb13cb16986efbe63ac77766c5438cbf56ff6ae070872` |
| [`...final-manifest.json`](./evidence/pf-sf1-t08-wave7-20260904-a47d91c6-final-manifest.json) | authoritative manifest | 44,088 | `0ae1658cb4b731008f050bc55e191f22fadf0d68d2a65d941c51e7471a1c97fc` |
| [`...final-recovery.json`](./evidence/pf-sf1-t08-wave7-20260904-a47d91c6-final-recovery.json) | authoritative recovery | 80,422 | `8d2c0a0d9a5a18867f2aa81c3684cc1ee25dba58fe781fdd1852f9424f153976` |
| [`...final-cleanup.json`](./evidence/pf-sf1-t08-wave7-20260904-a47d91c6-final-cleanup.json) | authoritative cleanup/preservation | 8,287 | `34e72df84d23848062b2649bb3e19d76748f32bffcfd1a75c13345a16043a000` |

The cleanup contract removed exactly 12 source objects, 12 recovered objects, both generated
buckets, both exact-comment disposable databases, the 606,634-byte dump, and the exact temporary
directory. It proves their absence afterward. The complete pre-existing database inventory (20
databases), pre-existing MinIO bucket inventory (one bucket), and `tc` database fingerprint were
byte-for-byte digest-equal before and after cleanup; `tc` remained present. All 18 JSON evidence files
that predated the authoritative final run retained their recorded hashes.

The verification suite parses each new JSON file, recomputes its file hash, checks file type/mode/link
count, validates canonical contract digests, and scans for database URLs, credentials, tokens, and
presigned URLs. It passed all five canonical contracts and all 18 pre-existing evidence hash checks.
The final direct inventory check reported zero wave-7 databases, `tc` present, zero wave-7 buckets,
the one pre-existing MinIO bucket with preserved digest
`42beec208f05c3c4a53ec71210f1ca6f9e1a64fd6040f42fc82b3acf2a690762`, and zero wave-7 dump/temp
artifacts.

## Files changed

- Recovery and provider-neutral verification: `scripts/phase-f/object_storage_recovery.py`,
  `scripts/phase-f/rehearse.py`, `scripts/phase-f/verify-wave7-mypy.mjs`,
  `backend/src/tc_backend/infrastructure/object_storage/adapter.py`, and `package.json`.
- Focused/adversarial tests: `scripts/phase-f/tests/test_object_storage_recovery.py`,
  `scripts/phase-f/tests/test_rehearse.py`, and `backend/tests/unit/test_object_storage.py`.
- Runbooks/readiness: `docs/phase-f/sprint-f1-recovery-performance-readiness.md`,
  `docs/phase-f/evidence/README.md`, `deploy/object-storage/README.md`,
  `deploy/operations/README.md`, `docs/test-management-roadmap.md`, and this report.
- Evidence: the five mode-0600 wave-7 JSON files listed above.

## Remaining gates — all `NOT_RUN`

1. Recovery from an actual Cloudflare R2 bucket and a matched real R2 versioning/backup set.
2. Recovery into a separately hosted production-like staging database and API deployment.
3. Independent operator/reviewer execution, handoff, and acknowledgement.
4. Repeated approved production-like measurements supporting production RTO/RPO objectives.

Only the local MinIO disposable sub-gate is PASS. `PF-SF1-T08`, the external gates above, and the
Phase F release gate remain unchecked/open.
