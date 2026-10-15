# Sprint F1 wave 9 — PF-SF1-T10 local disposable verification

- Date: 2026-09-07 (Asia/Seoul)
- Verdict: **LOCAL DISPOSABLE sub-gate PASS; PF-SF1-T10 and Phase F remain open**
- Authoritative run: `w9rt7c3b`
- Migration edge: `20260902_0030` → `20260901_0029` → `20260902_0030`
- Harness: [`migration_roundtrip_disposable.py`](../../scripts/phase-f/migration_roundtrip_disposable.py)
- Compose: [`compose.wave9.yml`](../../deploy/operations/compose.wave9.yml)

## What passed

The preview-first harness resolved the real Alembic graph before execution and required exactly one
head and exactly one immediate predecessor. It rejected unknown, ambiguous, missing, or unsafe
downgrade/upgrade functions and bound the run to graph digest
`d457d498cf02baed12fd06a96c0fedaef43adab69a72c67650da98ef332f5e0b` and head migration
file SHA-256 `c5e1feddc53f28464504828499121fedfefe5954cac0908117f2e8fb106938c0`.
Preview did not read a database URL, invoke Docker/PostgreSQL, or mutate state.

Execution used generated literal `pf-sf1-t10-wave9` names, a generated non-default loopback port,
one PostgreSQL 16 + pgvector container, one volume, and one network. It accepted only the exact
database comment, required an empty target, rejected production-like names, and discarded ambient
PostgreSQL variables. The current `tc` database was held in one exported `REPEATABLE READ, READ ONLY`
snapshot while its fingerprint and a custom-format dump were captured; no current-`tc` write was
performed. The dump was 689,165 bytes with SHA-256
`946e91bb5c7dca4556bc18468f9d8be93e2f4983f38ea1fc47a5104f6db927d9` and was restored only into
the disposable database.

At head, the restored copy contained 78 public tables and 707 normalized rows. Catalog v3 covered
collations, columns, constraints, extensions, indexes, inheritance, partitions, policies, relations,
routines, rules, sequences, statistics, triggers, types, and views; it recorded 945 logical columns,
738 constraints, 260 indexes, 230 routines, 106 triggers, 2 extensions, 3 types, 15 roles and 3 role
memberships. The schema digest was
`8846c25f73faecdc56220b9597a399b5e44a41202b0dc4ac1eec5dc18414ed62`, normalized data digest was
`d442ad6912990492b426f2b29326122e66f26772e0518a84e5843c145749661c`, logical-column digest was
`824392080bc6b2523621c2b96ad5d86ea39e82070c61b58cfb183bc7ffae5d30`, and role digest was
`cd8185d908e8a2dff95fb4cb79e3037caad4040e7df30c4e784172522c47b446`.

The harness downgraded exactly once to `20260901_0029`, verified that the three head-owned tables,
three functions, and four triggers were absent, and proved all surviving application data and the
role catalog unchanged. The control-plane precondition was deliberately strict: zero generations,
zero transitions, and the one pristine pointer row; any used generation/audit state fails closed.
It then upgraded exactly once to `20260902_0030` and required the full logical catalog, normalized
data, and role fingerprints to equal the head baseline. PostgreSQL compacted dropped physical
column slots during dump/restore, while all 945 live logical column definitions and every other
catalog category were equal. The only raw-data difference after the round trip was the freshly
generated `updated_at` on the pristine singleton LightRAG pointer; normalization is limited to that
seed-only field and semantic/catalog/data equality otherwise remained exact.

The final-head smoke constructed the real API and worker with external integrations disabled. Health,
readiness, workspace list, and folder-tree GETs returned 200; a representative workspace write
matched one row inside a transaction and was rolled back; the worker constructed nine handlers,
claimed zero jobs, made zero external calls, and stopped cleanly. A post-smoke fingerprint proved
that the smoke left the restored database unchanged.

## Fail-closed history and adversarial coverage

The first local attempt (`wv9d7e2a`) stopped at the predecessor comparison because the comparator
included Alembic's own bookkeeping row. No evidence was sealed; its exact container, volume, network,
database, dump, and temp tree were removed. The comparator was narrowed to exclude only the
head-owned tables and `alembic_version`, then protected by a focused regression test before the new
authoritative run. No migration was weakened and no repository data was deleted.

The 46-test Wave 9 suite covers preview non-mutation and credential independence; confirmation,
target, comment, version, empty-target and foreign-extension mismatches; production-like names;
source drift; missing/multiple/unknown heads; ambiguous/missing/unknown/wrong predecessors; missing
unsafe functions; downgrade/upgrade failures and partial/multiple/unknown revisions; schema, raw and
normalized data mismatches; the one permitted pristine-pointer timestamp normalization; evidence
tamper, symlink, hardlink and overwrite; unexpected resources; prior-evidence drift; and cleanup
target/comment/database/project preservation.

## Evidence and cleanup

The authoritative artifacts are canonical private regular, single-link, mode-0600 JSON and were
created without overwriting earlier evidence:

| Artifact | File SHA-256 | Canonical contract SHA-256 |
|---|---|---|
| [`pf-sf1-t10-wave9-local-disposable-w9rt7c3b-20260907.json`](./evidence/pf-sf1-t10-wave9-local-disposable-w9rt7c3b-20260907.json) | `8ce31fb7f0e6e5b043ef7419c84ebbce88786b783dffeb6c73dead268b82a486` | `3417f99f0859c8611af7235e798647967775d0ab3ae5c2f66547c09094a8de62` |
| [`pf-sf1-t10-wave9-cleanup-w9rt7c3b-20260907.json`](./evidence/pf-sf1-t10-wave9-cleanup-w9rt7c3b-20260907.json) | `9c5941a7bbbaadfff3731ea693d525c9562a982a6f8b93a1918368c5a377a22a` | `f3fc82bdda5dc567c1340181b14a4c4236545e096b345769721577963fd3a38f` |

Cleanup removed exactly one container, one volume, one network, one container-owned database, the
dump, and the private temp tree. The generated resources and child processes were absent afterward.
The pre-existing Docker inventory remained 8 containers, 37 images, 5 networks and 5 volumes with
digest `2f8b63f57ab62e8bb4b07f3fbd5045e6932973d66d225b57455b86eadce97a33`; the 20-database inventory
remained at digest `822bd3a9da55784787a1e5c28a10e2294bda0f28b545899379058166288fa3b2`.
The current-`tc` before/after fingerprint remained
`553ebef6582db2c9cb90a20e24ece69bf20728e38b475b1f60db130731695887`, and all 27 prior Phase F JSON
evidence files remained unchanged with inventory digest
`996dbb7f3a630f21e6d8ca4a8626f6bb2bfd8fa7f28629b57a4d808130386ea8`.

## Verification

Final command results are recorded here after completion:

| Check | Result |
|---|---|
| Focused Ruff | PASS |
| Deterministic focused mypy | PASS — 5 source files |
| Focused migration/adversarial pytest | PASS — 87 passed, 1 skipped because the optional catalog test URL was absent |
| Compose/preview validation | PASS — Compose config valid; preview listed exact actions/resources and created no file, temp path or Docker resource |
| Live disposable PostgreSQL 16 migration round trip | PASS — rehearsal 9.180887 s and exact cleanup |
| `pnpm check` | PASS — mypy 148 files; TypeScript 348 passed/1 skipped; Python 722 passed/1 skipped/109 PostgreSQL deselected; schema/ADR/operations/OpenAPI PASS |
| Operations validator | PASS — 44 tests and 14-section validator |
| `pnpm test:phase-f` | PASS — 223 passed, 1 skipped |
| Phase F UAT validator | PASS — 15 workflows, 33 automated and 15 manual checks in the manifest; manual execution remains `NOT_RUN` |
| Final evidence/resource audit | PASS — both seals and all 27 prior hashes valid; 0 Wave 9 containers/volumes/networks/databases/dump/temp/processes |

## Still NOT_RUN

This rehearsal did not contact staging, production, Cloudflare, Slack, a model/provider, deployment,
or any external credential. A separately hosted staging copy, active writer/worker quiescence under
staging orchestration, live service smoke, operator/reviewer handoff, broader migration boundaries,
production RTO/RPO, and every release approval remain `NOT_RUN`. Therefore `PF-SF1-T10`, Phase F,
staging, production, and release gates stay unchecked; only the local disposable sub-gate is PASS.
