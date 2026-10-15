# Phase F Sprint F1 wave 8 independent verification

- Verification date: 2026-09-07 (Asia/Seoul)
- Role: final independent verifier after semantic AGE digest remediation
- Verdict: **GREEN for the local disposable network-free sub-gate only**
- Release boundary: **`PF-SF1-T09`, Phase F, staging, live, and production gates remain unchecked/open**

No product code, contract, roadmap, or sealed evidence was edited. No Cloudflare, Slack, external
model provider, staging, production, or deployment surface was contacted; the only mutable runtime
surface was the exact verifier-owned local disposable Docker rehearsal described below. The two
temporary verifier evidence files were hashed, consumed by evidence-bound cleanup, and then removed,
so this report is the only retained verifier-created file.

## Independent implementation verdict

The remediated semantic AGE digest is fit for the stated local sub-gate:

- `_semantic_graph_payload()` discards workspace-local AGE node and edge IDs from the digest payload,
  uses each node's non-empty stable `entity_id`, keeps all node and edge properties except the
  provider insertion-time `created_at`, and canonicalizes edges by their semantic endpoint identities.
  Canonical list sorting makes row and insertion order irrelevant.
- Node properties, edge properties, endpoint topology, node/edge multiplicity, and node/edge
  cardinality remain in the canonical payload. Removing or adding a node/edge therefore changes the
  digest. A repeated semantic edge is retained as repeated payload content, so multiplicity is not
  collapsed.
- Missing stable node/endpoint identities, unknown endpoints, duplicate AGE node/edge storage IDs,
  and duplicate semantic node identities fail closed. The inspection also requires exact equality
  between graph node identities and entity-vector identities, and between graph endpoint pairs and
  relation-vector endpoint pairs.
- `_assert_semantic_graph_digest_agreement()` accepts exactly the `prior`, `cutover`, and
  `failedActivation` inspections, validates the digest version and shape, and rejects more than one
  SHA-256. The execution path calls it at lines 2239-2245, then verifies source, process, database,
  and prior-evidence preservation before `_seal()` starts at line 2266. Thus evidence cannot be sealed
  before the three semantic graph digests agree.

The permanent adversarial tests at
`scripts/phase-f/tests/test_lightrag_full_reindex_disposable.py` cover invariance under different AGE
internal IDs and different provider `created_at`, plus failure on semantic node-property,
edge-property, and edge-topology divergence. A separate read-only in-process verifier probe passed
12/12 cases: internal-ID/time invariance; node property, edge property, topology, node-cardinality,
and edge-cardinality divergence; missing node identity; duplicate internal node, internal edge, and
semantic node identities; unknown endpoint identity; and three-generation agreement rejection on
cardinality divergence.

## Authoritative evidence pair

Both files parsed as UTF-8 JSON with duplicate-key rejection. Independent canonicalization used
sorted compact JSON and the declared version prefix separated by a zero byte, excluding only the
top-level `contractDigest` field. File hashes, canonical hashes, and documentation statements all
match.

| Artifact | Bytes | File SHA-256 | Recomputed canonical SHA-256 |
|---|---:|---|---|
| `pf-sf1-t09-wave8-local-disposable-sg9c42fd-20260907.json` | 26,380 | `13489b5388989d5ee6a34016e03191def750b44c7025f4b7afc7b37da482eb76` | `2f646db8648f4e2b5e81bac73621d2dccc6427d20261fecbd038b691b45f0300` |
| `pf-sf1-t09-wave8-cleanup-sg9c42fd-20260907.json` | 9,615 | `76d1dbdd2d77ff17b1edff866dafe70dd41f5d0b2d3f270b31030ed56f7f0f84` | `16aee3d9eaca455ee3143fba3f2a5ca50cd8e4795189025988e01421f62492c3` |

Each is a regular, non-symlink, single-hard-link file with mode `0600`. Recursive key/value scans
found zero connection or web URL values, credential/authorization values, raw fixture corpus values,
or permalink fields. Both assert `rawCorpusOmitted=true` and `credentialsOmitted=true`.

The rehearsal evidence records three `tc.lightrag.semantic-age-graph.v2` digests equal to
`2550902ed0cabaa2f8a73b4086fa5f450b265e6d6c0601a219f71301ebd717c8`, two nodes and one edge per
workspace, exact graph/vector identity-set agreement, successful prior/cutover flows, failed
activation with successful automatic rollback, failed rollback with an unchanged pointer, active
and restored generation query/citation smoke, child-process and auxiliary-storage typed fallback,
and provider restart recovery. The cleanup evidence consumes the exact rehearsal file and contract
hashes, identifies the same target and `NOT_RUN` list, records exact removal, and records absence of
all disposable resources.

This pair is consistent with `deploy/knowledge/lightrag-full-reindex-contract.json`, the Wave 8
verification report, the LightRAG integration report, the evidence index, the deployment runbook,
and the roadmap. The contract still says the runtime is unconfigured by default, no approved
bootstrap or staging credentials exist, live apply is unsupported, staging execution is `NOT_RUN`,
and the release gate is `NOT_RUN`.

The authoritative rehearsal's recorded 25-file prior-evidence inventory exactly equals the cleanup
copy and every current file rehash. In particular, the four older Wave 8 artifacts remain historical
and unchanged:

| Historical artifact | Current and recorded SHA-256 |
|---|---|
| `pf-sf1-t09-wave8-local-disposable-20260907.json` | `db303df0de3c1e692f21a65f3bc3b2c596b01830329c6e16f40ced009f7e3efa` |
| `pf-sf1-t09-wave8-cleanup-20260907.json` | `3b25f4e2b255be02ddaefdda17503fb2930b7f002561295d424e3986305d7970` |
| `pf-sf1-t09-wave8-local-disposable-final-20260907.json` | `e8cc291d7875e852b29e84b13407ec59a7673f710e7f9e11d08eb9621d8c57a5` |
| `pf-sf1-t09-wave8-cleanup-final-20260907.json` | `d77654b5bce8b07c8f4b05df36345f0fdd8c62d96a79338cbb1ab4e7f8d2821c` |

## Fresh fully disposable rehearsal

The verifier used run ID `ivd7c31b`, source port `55478`, auxiliary port `55479`, a unique
`/private/tmp` tree, two unique evidence filenames, and generated run-scoped Docker project,
container, volume, network, database, workspace, and provider-runtime identities. Preview ran first
and reported `executes=false`, loopback-only/no-model-provider policy, exact resource identities, and
an unchanged release gate.

The rehearsal invocation, before a read-only stdout field filter, was:

```text
node scripts/python.mjs scripts/phase-f/lightrag_full_reindex_disposable.py rehearse \
  --run-id ivd7c31b \
  --source-port 55478 \
  --auxiliary-port 55479 \
  --temp-directory /private/tmp/pf-sf1-t09-wave8-verifier-ivd7c31b \
  --dump-file /private/tmp/pf-sf1-t09-wave8-verifier-ivd7c31b/pf-sf1-t09-wave8-verifier-ivd7c31b.dump \
  --output docs/phase-f/evidence/pf-sf1-t09-wave8-verifier-ivd7c31b-rehearsal-temp-20260907.json \
  --execute \
  --confirm 'RUN PF-SF1-T09 LOCAL-DISPOSABLE-NETWORK-FREE ON tc-phase-f-pf-sf1-t09-wave8-ivd7c31b-disposable'
```

Result: `LOCAL_DISPOSABLE_NETWORK_FREE_PASS`. The current `tc` ledger was accessed through the
read-only dump/fingerprint path, its knowledge corpus was empty, the restored copy matched before a
representative non-sensitive fixture was added only to the disposable source, and all three
immutable provider workspaces passed document/status/content/citation/chunk, 1024-dimensional
chunk/entity/relation vector, AGE node/edge, and source-snapshot parity. The prior, cutover, and
failed-activation semantic graph SHA-256 was
`9ca3474cff07ee40f80b9ad37d4e8f5c18bd60f411af660f603b525db0f0c121` in all three workspaces;
workspace inventory SHA-256 was
`e7e18e3f4e526a523f4546c639f48709f0ee67e453d22a6982a367e0793dd473`.

The run independently passed CAS prior-to-cutover activation, stale-result fencing, explicit
rollback-failure pointer preservation, rollback to the retained prior generation, injected
post-activation failure with successful automatic rollback, API/worker retrieval and citation
parity, exact child-process failure fallback, exact auxiliary-storage failure fallback, and provider
restart recovery. Its non-authoritative temporary evidence was a regular, single-link, mode-0600
file at the path above with file SHA-256
`2e7d8ab1171c6ea9329f86e5129d63b63030696f2a2478445a75737abc1260ea` and canonical SHA-256
`cfdad3327b39c83fe216b944bba34e44b510e31d9f7765585947bc3d03de2691`.

Cleanup was separately previewed, then executed with:

```text
node scripts/python.mjs scripts/phase-f/lightrag_full_reindex_disposable.py cleanup \
  --run-id ivd7c31b \
  --source-port 55478 \
  --auxiliary-port 55479 \
  --rehearsal-evidence docs/phase-f/evidence/pf-sf1-t09-wave8-verifier-ivd7c31b-rehearsal-temp-20260907.json \
  --output docs/phase-f/evidence/pf-sf1-t09-wave8-verifier-ivd7c31b-cleanup-temp-20260907.json \
  --execute \
  --confirm 'RUN PF-SF1-T09 LOCAL-DISPOSABLE-CLEANUP ON tc-phase-f-pf-sf1-t09-wave8-ivd7c31b-disposable'
```

Result: `LOCAL_DISPOSABLE_NETWORK_FREE_CLEANUP_PASS`. Cleanup consumed the exact rehearsal file and
contract hashes; removed exactly two containers, two volumes, one network, two container-owned
databases, three workspaces, the dump, and the temporary tree; and reported every resource and
provider process absent. The temporary cleanup evidence had file SHA-256
`f21b9aa19f293699f3989996d2710321f37cc99b1a6c58b9c921237b13f5e176` and canonical SHA-256
`d72a76e174a1df3b988f5e6e18230450bc539909e682416db3c2c4ce9ccdca57`.

Independent post-cleanup inspection found both exact containers, both volumes, the network, the
temporary tree/dump, and run-ID-matching processes absent. Pre-existing Docker inventory returned
to 8 containers, 5 volumes, 5 networks, and 37 images with digest
`2f8b63f57ab62e8bb4b07f3fbd5045e6932973d66d225b57455b86eadce97a33`; database inventory returned
to 20 databases with digest `822bd3a9da55784787a1e5c28a10e2294bda0f28b545899379058166288fa3b2`;
and the current `tc` fingerprint remained
`5cb5704c43263e57dc1230e545b2a1a8ae755367d55304e2aca4bbb5acd0c54e`. The 27-file retained JSON
evidence inventory returned exactly to its pre-run path/hash aggregate
`e9253fdeebc04ee27a03ac4809404017175daace146fd1ac757dde145105d584`, with no verifier temporary
evidence residual.

## Automated verification

| Exact check | Result |
|---|---|
| `node scripts/python.mjs -m ruff check scripts/phase-f/lightrag_full_reindex.py scripts/phase-f/lightrag_full_reindex_disposable.py backend/src/tc_backend/knowledge/lightrag_local_rehearsal.py backend/src/tc_backend/knowledge/lightrag_generation_factory.py backend/src/tc_backend/knowledge/lightrag_generation_process.py backend/tests/unit/test_lightrag_generation_factory.py scripts/phase-f/tests/test_lightrag_full_reindex_disposable.py` | PASS — all checks passed |
| `env -u MYPYPATH node scripts/python.mjs -m mypy --no-incremental scripts/phase-f/lightrag_full_reindex.py scripts/phase-f/lightrag_full_reindex_disposable.py backend/src/tc_backend/knowledge/lightrag_local_rehearsal.py backend/src/tc_backend/knowledge/lightrag_generation_factory.py backend/src/tc_backend/knowledge/lightrag_generation_process.py` | PASS — no issues in 5 source files |
| `node scripts/python.mjs -m pytest -q scripts/phase-f/tests/test_lightrag_full_reindex.py scripts/phase-f/tests/test_lightrag_full_reindex_disposable.py backend/tests/unit/test_lightrag_generation_factory.py` | PASS — 86 passed |
| `pnpm check` | PASS — mypy 147 files; TypeScript 348 passed/1 skipped; Python 676 passed/1 skipped/109 PostgreSQL deselected; schema, 17 ADRs, 44 operations tests/14 sections, and OpenAPI passed |
| `pnpm test:operations` | PASS — 44 passed; 14 sections validated |
| `pnpm test:phase-f` | PASS — 177 passed, 1 opt-in PostgreSQL test skipped |
| `pnpm phase-f:uat:validate` | PASS — 15 workflows, 33 automated checks, and 15 manual checks declared; manifest SHA-256 `534fb9f66ffadedbbcf06a53e46384ec95fbc380ec06fd9012b13cb25cb665a7` |

The UAT command validates the manifest only. Manual UAT, staging execution, live-provider calls,
production-like multi-process networking/load/alerts, operator handoff, and production RTO/RPO are
all still **`NOT_RUN`**.

## Release-gate boundary

A fresh read-only current-`tc` fingerprint found `knowledge_nodes`, `knowledge_edges`,
`knowledge_evidence`, and `knowledge_embeddings` at zero rows. The roadmap still contains unchecked
`PF-SF1-T09`, unchecked Phase F, and all four unchecked Phase F exit criteria. Because the actual
current-board knowledge corpus is empty and the staging/live credentials, provider, load,
operator-handoff, and RTO/RPO gates have not run, this verification does not close or advance any
release checkbox.

**Independent conclusion: GREEN for the Wave 8 implementation, authoritative pair integrity, fresh
local loopback rehearsal, regression gates, preservation, and exact cleanup only. `PF-SF1-T09` and
the Phase F release gate remain open.**
