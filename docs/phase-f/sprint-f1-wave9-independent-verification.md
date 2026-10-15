# Sprint F1 Wave 9 independent verification — PF-SF1-T10

- Date: 2026-09-07 (Asia/Seoul)
- Independent run ID: `iv9a7c2d`
- Verdict: **GREEN for the LOCAL DISPOSABLE verification only**
- Scope: independent read-only review plus one fresh disposable PostgreSQL 16 rehearsal and exact evidence-bound cleanup
- Task status: **PF-SF1-T10 remains unchecked and `NOT_RUN` beyond the local disposable sub-gate**

## Independent verdict

The implementation, tests, operations documentation, prior sealed evidence, and a fresh unique run
were independently inspected without trusting the implementer report. The real Alembic graph has
exactly one current head and exactly one immediate predecessor, and the fresh rehearsal executed the
exact edge `20260902_0030 → 20260901_0029 → 20260902_0030` with one downgrade and one upgrade.
The source `tc` database was read through one exported `REPEATABLE READ, READ ONLY` snapshot and a
snapshot-pinned custom dump; its complete semantic fingerprint was identical before and after.

The disposable target was PostgreSQL `16.12` (`server_version_num=160012`) on a generated loopback
port. It was empty, carried the exact generated database comment, and used generated names containing
the `pf-sf1-t10-wave9` namespace. The harness and focused adversarial suite reject production-like
names, foreign database/project/resource identity, nonempty or foreign-extension targets, wrong
comments, unsupported PostgreSQL majors, non-loopback source identity, missing/multiple/unknown graph
heads, missing/ambiguous/unknown predecessors, partial/multiple/unknown database revisions, and every
non-pristine LightRAG control-plane state.

Source drift, schema/category/object-count drift, normalized-data drift, role drift, unexpected raw
data drift, partial migrations, unexpected resources, evidence tamper/link/overwrite, prior-evidence
drift, and cleanup target/comment/dump/preservation failures all raise without producing PASS
evidence. Cleanup verifies the sealed rehearsal, exact resource ownership, exact database comment,
and pinned dump identity before removing anything; a failed postcondition cannot produce cleanup PASS
evidence.

## Graph, snapshot, and round trip

| Check | Independent result |
|---|---|
| Alembic heads | exactly 1: `20260902_0030` |
| Immediate predecessors | exactly 1: `20260901_0029` |
| Graph digest | `d457d498cf02baed12fd06a96c0fedaef43adab69a72c67650da98ef332f5e0b` |
| Head migration file SHA-256 | `c5e1feddc53f28464504828499121fedfefe5954cac0908117f2e8fb106938c0` |
| Source access | exported repeatable-read/read-only snapshot plus snapshot-pinned `pg_dump` |
| Source fingerprint before/after | equal: `553ebef6582db2c9cb90a20e24ece69bf20728e38b475b1f60db130731695887` |
| Dump | 689,165 bytes; SHA-256 `9978a64b0aa8a64da77d9046b8e7dcbedf9971f8cf6dca9f0728c62e390e223f` |
| Control plane before downgrade | 0 generations, 0 transitions, exactly 1 pristine pointer |
| Migration operations | downgrade 1, upgrade 1, broad downgrade false |
| Rehearsal time | 10.275655 seconds, locally measured |

The restored baseline and final state matched across all 16 catalog categories, catalog object
counts, normalized data, and the PostgreSQL role/membership catalog. The baseline contained 78 public
tables, 945 live logical columns, and 707 normalized rows. Key fingerprints were:

| Fingerprint | SHA-256 |
|---|---|
| Catalog | `8846c25f73faecdc56220b9597a399b5e44a41202b0dc4ac1eec5dc18414ed62` |
| Normalized data | `d442ad6912990492b426f2b29326122e66f26772e0518a84e5843c145749661c` |
| Logical columns | `824392080bc6b2523621c2b96ad5d86ea39e82070c61b58cfb183bc7ffae5d30` |
| Roles and memberships | `cd8185d908e8a2dff95fb4cb79e3037caad4040e7df30c4e784172522c47b446` |

Normalization is narrowly limited to replacing `updated_at` only for the one provably pristine
LightRAG pointer row (`scope=GLOBAL`, pointer version 0, no active generation). Every other value is
exact. The only source-to-restored physical catalog difference was PostgreSQL dump/restore compaction
of dropped-column attribute slots; equality of all 945 live logical columns and every other catalog
category was separately proven. The final round-trip catalog itself matched the restored baseline
exactly. API reads returned 200, the representative write was rolled back, the nine-handler worker
claimed zero jobs, external calls were zero, and the post-smoke fingerprint remained unchanged.

## Fresh evidence and seals

Both fresh files are regular, non-symlink, single-link, mode-0600 files. Duplicate-key parsing,
canonical seals, file hashes, and safety metadata were revalidated independently after cleanup.

| Artifact | File SHA-256 | Canonical contract SHA-256 |
|---|---|---|
| `pf-sf1-t10-wave9-local-disposable-iv9a7c2d-independent-20260907.json` | `9840f76d42c26c501917950674701b89c6beff0fa28905a4d6d63ca21509111e` | `bc46318da8e556609ab64f96d02fd65543e29fdbca62ab9a2a143f70b29ca4bd` |
| `pf-sf1-t10-wave9-cleanup-iv9a7c2d-independent-20260907.json` | `b9a319a09607fcd7ef660ae2b4d3bff1c77750371eff73c77e515fae319fe940` | `eac8994070ff4b1a9e327f73afa0ad556c72d9b43b9c656d4b98d85acbb7455c` |

The previously sealed authoritative Wave 9 pair also revalidated with the documented hashes:

| Artifact | File SHA-256 | Canonical contract SHA-256 |
|---|---|---|
| `pf-sf1-t10-wave9-local-disposable-w9rt7c3b-20260907.json` | `8ce31fb7f0e6e5b043ef7419c84ebbce88786b783dffeb6c73dead268b82a486` | `3417f99f0859c8611af7235e798647967775d0ab3ae5c2f66547c09094a8de62` |
| `pf-sf1-t10-wave9-cleanup-w9rt7c3b-20260907.json` | `9c5941a7bbbaadfff3731ea693d525c9562a982a6f8b93a1918368c5a377a22a` | `f3fc82bdda5dc567c1340181b14a4c4236545e096b345769721577963fd3a38f` |

All 31 Phase F JSON evidence files passed the regular/private/single-link read check. The fresh
cleanup rehashed and preserved all 29 files that predated this independent run; their inventory
digest remained `c9fed6bbd9cdae0e871c1beee31614f18b2a407c0646cb2a1c6abe9ae9a23831`.
The two fresh files explain the expected increase from 29 to 31. A recursive scan of the fresh and
previously sealed Wave 9 pairs found zero URL values, credential fields/values, or raw-record
payloads; normalized table evidence contains only table name, row count, and digest.

## Preview, validation, and test results

Preview resolved the graph and exact plan without reading database credentials or invoking Docker or
PostgreSQL. Before and after preview, the evidence file, cleanup file, temp directory, dump, and exact
Docker resources were all absent. Compose configuration validation also completed with no artifact.

| Check | Result |
|---|---|
| Focused Ruff | PASS |
| Deterministic focused mypy | PASS, 5 source files |
| Focused adversarial/migration tests | PASS, 87 passed and 1 optional catalog test skipped |
| `pnpm check` | PASS: mypy 148 files; TypeScript 348 passed/1 skipped; Python 722 passed/1 skipped/109 PostgreSQL deselected; schema, ADR, operations, and OpenAPI checks passed |
| `pnpm test:operations` | PASS, 44 tests and 14-section validator |
| `pnpm test:phase-f` | PASS, 223 passed and 1 optional catalog test skipped |
| `pnpm phase-f:uat:validate` | PASS, 15 workflows, 33 automated checks, and 15 manual checks declared; manual execution remains `NOT_RUN` |

## Exact cleanup and preservation proof

Evidence-bound cleanup removed exactly one container, one volume, one network, its container-owned
database, the pinned dump, and its private temp directory. The independent post-cleanup audit found:

- 0 exact-run containers, volumes, or networks;
- 0 containers, volumes, or networks carrying both `PF-SF1-T10` and `wave9` labels;
- 0 exact disposable databases;
- 0 matching dump or temp paths under `/tmp` or `/private/tmp`;
- 0 matching child processes.

The pre-existing Docker inventory remained 8 containers, 37 images, 5 networks, and 5 volumes with
digest `2f8b63f57ab62e8bb4b07f3fbd5045e6932973d66d225b57455b86eadce97a33`.
The database inventory remained 20 databases with digest
`822bd3a9da55784787a1e5c28a10e2294bda0f28b545899379058166288fa3b2`, and the current-`tc`
fingerprint remained the exact value recorded above. No pre-existing evidence file changed.

## Gates intentionally unchanged

This run did not access staging, production, Cloudflare, Slack, any external model/provider,
credentials, deployment systems, or operator channels. The following remain explicitly unchecked
and `NOT_RUN`:

- `PF-SF1-T10` overall task gate;
- Phase F gate;
- staging rehearsal and staging service/writer/worker quiescence;
- live-service and production rehearsal;
- operator/reviewer handoff;
- production RTO/RPO;
- release approvals and release gates.

Only the local disposable Wave 9 verification is GREEN. Nothing in this report promotes that result
to staging, live, operator, production, Phase F, PF-SF1-T10, or release completion.
