# Sprint F1 wave 8 — PF-SF1-T09 local disposable verification

- Date: 2026-09-07 (Asia/Seoul)
- Verdict: **LOCAL DISPOSABLE NETWORK-FREE sub-gate PASS; PF-SF1-T09 and Phase F remain open**
- Authoritative remediated run: `sg9c42fd`
- Harness: [`lightrag_full_reindex_disposable.py`](../../scripts/phase-f/lightrag_full_reindex_disposable.py)
- Compose: [`compose.wave8.yml`](../../deploy/knowledge/compose.wave8.yml)

## What passed

The preview-first harness created only generated `pf-sf1-t09-wave8` resources on loopback: one
PostgreSQL 16 + pgvector source copy and one distinct empty PostgreSQL 16 + AGE + pgvector auxiliary
target. It read the current `tc` database only through read-only snapshots and `pg_dump`, restored
the logical schema and all data into the disposable source, then added a two-node, one-edge,
three-source/evidence, two-vector deterministic fixture only to that copy. The source and restored
data digests were exact. PostgreSQL logically compacted 26 dropped-column attribute slots during
dump/restore, so evidence truthfully records `rawPhysicalFingerprintEqual=false` while independently
proving all 945 live column definitions and every other schema category identical.

The final run exercised production paths with `lightrag-hku==1.5.6` and its official
`PGKVStorage`, `PGVectorStorage`, `PGDocStatusStorage`, and `PGGraphStorage`. It exported the complete
locally supported node corpus (2 documents), verified 2 full documents, 2 processed statuses, 2
chunks, 2 1024-dimensional chunk vectors, 2 entity vectors, 1 relation vector, 2 AGE nodes, 1 AGE
edge, UUID citation identities, exact cardinalities and versioned digests with page size 1, and
retained three immutable workspaces. The AGE digest now canonicalizes nodes by `entity_id` and
edges by semantic endpoint identities, retains all content/topology properties, and omits only AGE
internal node/edge IDs plus provider insertion-time `created_at`; all three generations are required
to have one identical semantic digest before evidence can be sealed. Raw corpus and query context
are omitted.

The audit exposed and fixed one production-child defect: executing the worker by filesystem path put
`tc_backend/knowledge` first on `sys.path`, so the repository's `lightrag.py` shadowed the installed
official `lightrag` package. The factory now uses `python -m
tc_backend.knowledge.lightrag_generation_process`, with a regression test for the exact command.

The durable controller registered generations before CAS activation, a separate API resolver and
worker resolver observed the cutover, and an in-flight stale result was discarded. The drill then
injected and verified a failed rollback with an unchanged pointer, rolled the cutover back to the
prior retained generation, injected a post-CAS activation failure with successful automatic
rollback, killed an exact captured production child and separately stopped the auxiliary provider,
and obtained typed graph + pgvector fallback for both. Provider restart recovered query/citation
smoke; a counterfeit provider was rejected.

## Evidence and cleanup

The authoritative final artifacts are private regular, single-link, mode-0600 JSON:

| Artifact | File SHA-256 | Canonical contract SHA-256 |
|---|---|---|
| [`pf-sf1-t09-wave8-local-disposable-sg9c42fd-20260907.json`](./evidence/pf-sf1-t09-wave8-local-disposable-sg9c42fd-20260907.json) | `13489b5388989d5ee6a34016e03191def750b44c7025f4b7afc7b37da482eb76` | `2f646db8648f4e2b5e81bac73621d2dccc6427d20261fecbd038b691b45f0300` |
| [`pf-sf1-t09-wave8-cleanup-sg9c42fd-20260907.json`](./evidence/pf-sf1-t09-wave8-cleanup-sg9c42fd-20260907.json) | `76d1dbdd2d77ff17b1edff866dafe70dd41f5d0b2d3f270b31030ed56f7f0f84` | `16aee3d9eaca455ee3143fba3f2a5ca50cd8e4795189025988e01421f62492c3` |

The final rehearsal recorded corpus digest
`a58dcb38896c0be604683dab2534af405431fd94739e82a996daf781cfee11cf`,
source-ledger/projection/source-pgvector digests
`fc1d8dcd234d9560c885d9411ff1f809e6511c62c17a7888009d2033f22b19fb`,
`7d17865642fc06a8bf753e5b0995e8c84eb936cd961ce70af9e9fb58561816ee`, and
`9ee03d0d6651d1038313ca222c33a68fc6f6d04b3597b6de9cbdfa2bde08f69c`,
document-identity/content/citation/chunk/vector digests
`33419d38802aeeebd399aafc107c2104f061bf73deb78f6bf4a7385a5ba77378`,
`54ede8f5c2962d0336f1c272039ef3bd1f2d74175b75de31084cbf4042ad3e30`,
`d016ff9103212be7d5ffd44ff0732a340c46708cac7251c0203ae501ee51f5bc`,
`5ada8a1148d6530dbce10f1d0b19305057ea67a594c433b96f24b5c7757fe7a5`, and
`68d8434edebe6c2df9dffe2390c5af79ee0a0edb3a128e11d0f38a1fdfdba11c`,
three-generation semantic AGE graph digest
`2550902ed0cabaa2f8a73b4086fa5f450b265e6d6c0601a219f71301ebd717c8`,
workspace-inventory digest
`37e314f2ae76b87c4c642fe5491aabb5220615b714a5bd5c581e03cf56ee2817`,
current-`tc` fingerprint digest
`5cb5704c43263e57dc1230e545b2a1a8ae755367d55304e2aca4bbb5acd0c54e`,
and prior-evidence inventory digest
`bc09fa8ac9cb4daeb0d206237f0e5d39ab7d7609b5fb3fb426b00772fbb2b5ac`
for 25 pre-existing JSON artifacts. Cleanup removed exactly 2 containers, 2 volumes, 1 network, 2
container-owned databases, 3 workspaces, the dump, and the temp tree. All generated resources and
provider processes were absent afterward; the pre-existing Docker inventory, database inventory,
current-`tc` fingerprint, and all 25 prior evidence hashes were unchanged.
The unchanged Docker and database inventory digests are respectively
`2f8b63f57ab62e8bb4b07f3fbd5045e6932973d66d225b57455b86eadce97a33` and
`822bd3a9da55784787a1e5c28a10e2294bda0f28b545899379058166288fa3b2`.

Both earlier wave-8 rehearsal/cleanup pairs remain immutable historical evidence. The former
`-final-` pair hashes workspace-specific AGE internal IDs and therefore does not prove graph equality
across generations; only the `sg9c42fd` pair above is authoritative for this sub-gate verdict.

## Verification

Final command results are recorded here after completion:

| Check | Result |
|---|---|
| Focused Ruff | PASS |
| Focused strict mypy | PASS — 5 source files |
| Focused controller/factory/adversarial pytest | PASS — 86 |
| Disposable PostgreSQL 16 + AGE + pgvector integration | PASS — rehearsal and cleanup |
| `pnpm check` | PASS — mypy 147 files; TS 348 passed/1 skipped; Python 676 passed/1 skipped/109 PostgreSQL deselected; schema/ADR/operations/OpenAPI PASS |
| Operations validator | PASS — 44 tests and 14-section validator |
| Phase F UAT validator | PASS — 15 workflows, 33 automated and 15 manual checks in the manifest; manual execution remains `NOT_RUN` |
| `pnpm test:phase-f` | PASS — 177 passed, 1 skipped |

Adversarial coverage includes AGE internal-ID and insertion-time normalization plus rejection of
semantic node-property, edge-property, and edge-topology changes; exact target/comment/workspace
mismatches, source drift, partial,
extra, duplicate and wrong-status identities, checksum/cardinality mismatch, counterfeit provider,
stale pointer/result, failed activation and rollback, non-empty auxiliary target, unordered,
duplicate, repeated or oversized pagination, evidence digest tamper, and evidence-bound cleanup
preservation.

## Still NOT_RUN

This did not contact staging/live, Cloudflare, Slack, any model provider, or a deployment system.
Actual staging credentials/bootstrap, live provider calls, staging apply, production-like
multi-process networking/load/alerts, operator handoff, and production RTO/RPO remain `NOT_RUN`.
The general full-reindex CLI still has no staging apply entrypoint (`liveApplySupported=false`).
Consequently `PF-SF1-T09`, every dependent Phase F release gate, and production readiness stay
unchecked.
