# Phase F Sprint F1 wave 7 independent verification

- Verification date: 2026-09-04 (Asia/Seoul)
- Scope: fresh verification after the focused-mypy RED remediation
- Verdict: **GREEN for the local MinIO disposable sub-gate only**
- Release boundary: **`PF-SF1-T08` and the Phase F release gate remain unchecked/open**

No Cloudflare R2, staging/live endpoint, Slack, model provider, or deployment was contacted. Actual
R2 recovery/versioning/backup, a matched separately hosted staging database, operator handoff, and
production RTO/RPO remain **`NOT_RUN`**.

## Independent implementation review

The verification reviewed the wave-7 recovery helper, provider-neutral verifier, S3-compatible
adapter, focused tests, runbooks/readiness records, roadmap entry, all five JSON artifacts, and the
focused-mypy RED/remediation record without relying on the earlier verdict.

The recovery helper is fail-closed around literal generated wave-7 database, bucket, prefix, dump,
temporary-directory, manifest, and evidence identities. Preview does not read credentials or mutate
state. Execution accepts only a credential-free loopback HTTP MinIO origin and a loopback `tc`
administrator database; requires exact database comments, absent source/recovery resources, distinct
source/recovery buckets, an empty recovery database and bucket, private files, and exact
confirmation; restores without `--clean`; checks every destination key before copy; and cleans only
the pinned, inventory-equal resources. The API verification is read-only and validates all pages,
exact provider/bucket/privacy/scan policy, metadata consistency, and full-byte samples.

The adapter signs root `GET` for authenticated bucket inventory and signs bucket `HEAD`, `PUT`, and
`DELETE` through the same SigV4 path. Focused unit coverage exercises the root inventory and bucket
lifecycle, while the fresh live MinIO run independently proved that the root-signed request and exact
bucket lifecycle are accepted by the loopback service.

The original focused mypy claim was correctly invalidated by four `import-untyped` errors caused by
editable internal packages without `py.typed` markers. The remediation is narrow: only those
out-of-scope internal imports use `# type: ignore[import-untyped]`, and
`verify-wave7-mypy.mjs` removes ambient `MYPYPATH` and passes `--no-incremental`. It passed twice:

```text
node scripts/phase-f/verify-wave7-mypy.mjs
MYPYPATH=/definitely/hostile/nonexistent:/tmp/wave7-shadow node scripts/phase-f/verify-wave7-mypy.mjs

Success: no issues found in 2 source files
Success: no issues found in 2 source files
```

## Adversarial and regression results

| Check | Independent result |
|---|---|
| Focused Ruff | PASS |
| Recovery/rehearsal/object-storage pytest | 70 passed, 1 opt-in catalog test skipped |
| Additional in-process adversarial probes | 18 PASS: missing/unexpected/orphan key, corrupt bytes, wrong MIME/size/SHA/provider/owner, non-overwrite, incomplete page, missing HEAD SHA, empty and coverage-incomplete sample |
| Existing fail-closed coverage | PASS: unsafe/external target, duplicate key, cursor cycle, manifest tamper, empty destination preflight, restore without `--clean`, provider/bucket mismatch, R2-only wrapper, disposable-loopback-only MinIO |
| Fresh loopback MinIO/ClamAV integration | 1 passed |
| Fresh isolated PostgreSQL/API/repository/migration matrix | 13 passed |
| `pnpm check` | PASS: Ruff/ESLint/type checks, mypy 145 sources, Python 651 passed/1 skipped/109 deselected, Web 191 passed/1 skipped, contracts 128, API client 23, UI 6, schema/ADR/operations/OpenAPI |
| Standalone `pnpm test:operations` | 44 passed; 14 operations sections validated |
| `pnpm phase-f:uat:validate` | PASS: 33 automated checks, 15 manual checks, 15 workflows; manifest SHA-256 `534fb9f66ffadedbbcf06a53e46384ec95fbc380ec06fd9012b13cb25cb665a7` |

## Independent evidence recomputation

All files parsed as UTF-8 JSON with duplicate-key rejection. Each is a regular, single-link,
mode-0600 file. Independent scans found no URL, presigned URL, authorization value, bearer token,
credential, or secret material.

| Artifact | Bytes | File SHA-256 | Recomputed contract SHA-256 |
|---|---:|---|---|
| `pf-sf1-t08-wave7-20260904-d31c8e7a-retry1-failure.json` | 4,553 | `1c673b8f9ead029d1656a9ccf08f9e8b4a1b95996751d2586e71f427c8c17593` | `b00f20c969bbdaab5e1e1120ab2cdd147e90e768ab0abc6c35cb2cb246a5e2e4` |
| `pf-sf1-t08-wave7-20260904-e29f6c4b-retry2-failure.json` | 4,535 | `87e6d9a901aa36c5389bb13cb16986efbe63ac77766c5438cbf56ff6ae070872` | `1be2eebdefb88b90713f73cd719ac5130517f4d91e575d95964e77277d4c9739` |
| `pf-sf1-t08-wave7-20260904-a47d91c6-final-manifest.json` | 44,088 | `0ae1658cb4b731008f050bc55e191f22fadf0d68d2a65d941c51e7471a1c97fc` | `4c45078950149bd5666386b12b8c46137d7f06d962ac9a5a60d64e967a27cfc2` |
| `pf-sf1-t08-wave7-20260904-a47d91c6-final-recovery.json` | 80,422 | `8d2c0a0d9a5a18867f2aa81c3684cc1ee25dba58fe781fdd1852f9424f153976` | `714c327d5c321b4ac28824b8ab5f5834af1e1cd81a59de536602919d5f25a203` |
| `pf-sf1-t08-wave7-20260904-a47d91c6-final-cleanup.json` | 8,287 | `34e72df84d23848062b2649bb3e19d76748f32bffcfd1a75c13345a16043a000` | `ce372b9ff7a75a75633ccc5e2ce570fa7a5afa4b1f196333878c5359a96cfd77` |

The deterministic fixture bytes were regenerated independently. The 12 unique objects contain
exactly two objects for each of the six `EXECUTION_ATTEMPT|ISSUE` ×
`application/json|image/png|text/plain` pairs, and every regenerated size and SHA-256 matches the
manifest. Source/restored metadata each contain 12 exact rows with canonical digest
`fe6c103995ce055c0fed582f054fa30c756f3ffef70d2d675098676e1f6737f2`; source/recovered object
inventories each contain 12 exact entries over five pages with canonical digest
`8b2667109399c2cd2916d7abb983f957737e0b282dac46198ff24201be4bda9f`.

The source and restored full database fingerprints, all 16 catalog categories, 78 table records,
53 total rows, metadata/object bindings, manifest file/contract bindings, and dump/archive
size/SHA/count bindings are equal. API evidence is internally exact: four metadata pages scan
12/12 with zero mismatch/failure, four attachment pages cover both owners and all MIME classes,
the API sample verifies 12/12, and the independent class-covering sample binds four full-byte
objects. All 18 prior-evidence paths were rehashed successfully, and their canonical inventory
digest also matches.

## Fresh disposable integration resources and cleanup

The live MinIO/ClamAV test used only the newly generated exact bucket
`tc-phase-f-pf-sf1-t08-wave7-ivf4c9b72e-source-disposable`. Preview proved it absent; creation proved
it empty; the test passed signed PUT/HEAD/GET/LIST/DELETE, private anonymous denial, CLEAN scanning,
and EICAR rejection; cleanup proved it empty before deleting that exact bucket. The pre-existing
`tc-attachments` inventory and content listing remained unchanged; final canonical bucket-name
inventory is one bucket with digest
`42beec208f05c3c4a53ec71210f1ca6f9e1a64fd6040f42fc82b3acf2a690762`.

The PostgreSQL/API matrix used only the newly generated database
`tc_phase_f_pf_sf1_t08_wave7_ivf4c9b72e_postgresql_test_disposable`, attested by comment
`tc-phase-f-independent-verification:v1:pf-sf1-t08-wave7-ivf4c9b72e:postgresql:test-disposable`.
Preview proved it absent, creation proved the exact comment and zero public tables, and cleanup
revalidated the comment before dropping only that database. The final database inventory is the
same 20 names with canonical digest
`7e040f77b88dbc6db451cb1b0c410c233c8e8246b3370c38b5866dc7919f4c28`; `tc` remains present and its
full fingerprint digest remains
`1febad64e3669e80dba2938b7535b0a0a41af014186845b72c53e157e4f95fd9`.

Final direct inspection found zero `tc_phase_f_pf_sf1_t08_wave7_*` databases, zero
`tc-phase-f-pf-sf1-t08-wave7-*` buckets, zero wave-7 keys in pre-existing buckets, and zero
`/private/tmp/pf-sf1-t08-wave7-*` dumps or directories. This independently confirms the recorded
authoritative and retry cleanup absence as well as cleanup of the fresh verification resources.

## Gate status

The roadmap still contains `- [ ] PF-SF1-T08`, and all four Phase F exit criteria remain unchecked.
The local disposable result does not satisfy or alter any production gate:

1. Actual Cloudflare R2 recovery/versioning/backup: **`NOT_RUN`**.
2. Matched separately hosted staging database/API recovery: **`NOT_RUN`**.
3. Operator/reviewer handoff and acknowledgement: **`NOT_RUN`**.
4. Repeated production-like RTO/RPO measurements and approval: **`NOT_RUN`**.

**Independent conclusion: GREEN for wave-7 implementation, evidence integrity, local loopback
rehearsal behavior, regression gates, and verified cleanup; `PF-SF1-T08` and Phase F remain open.**
