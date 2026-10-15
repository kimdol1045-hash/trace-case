# PF-SF1-T09 LightRAG generation-control integration report

- Audit date: 2026-09-07 (Asia/Seoul)
- Scope: durable generation ledger, provider/controller, runtime routing, isolated factory, API/worker composition, and full auxiliary corpus contract
- Tool: [`lightrag_full_reindex.py`](../../scripts/phase-f/lightrag_full_reindex.py)
- Local disposable tool: [`lightrag_full_reindex_disposable.py`](../../scripts/phase-f/lightrag_full_reindex_disposable.py)
- Provider contract: [`lightrag-full-reindex-contract.json`](../../deploy/knowledge/lightrag-full-reindex-contract.json)
- Verdict: **LOCAL DISPOSABLE NETWORK-FREE SUB-GATE PASS; STAGING/LIVE APPLY NOT RUN**

No staging/live PostgreSQL row, pgvector row, LightRAG row/graph, process configuration, Docker
resource, or remote service was mutated during this work. Local tests did mutate and then destroy an
isolated PostgreSQL 16 + pgvector database and guarded AGE+pgvector failover resources. The control plane owns only its three
`lightrag_*` tables and has no foreign key, trigger, or migration SQL against the authoritative
QA/knowledge ledger. No staging database, approved runtime bootstrap, secret-manager configuration,
or live generation was supplied, so this report does not close PF-SF1-T09.

## Wave 8 local disposable result

Wave 8 added an exact, generated, loopback-only PostgreSQL 16 + pgvector source copy and a distinct
empty PostgreSQL 16 + AGE + pgvector auxiliary. The previewed and separately confirmed harness ran
the official LightRAG 1.5.6 PostgreSQL adapters through the production generation child, indexed the
complete two-document supported fixture corpus, verified exact document/status/chunk/citation/vector/
AGE identities and digests, registered immutable generations, exercised CAS cutover with separate
API/worker resolvers, discarded an injected stale result, and rolled back to the retained prior
generation. It additionally proved post-CAS activation rollback, explicit rollback failure,
counterfeit-provider rejection, child-process-kill fallback, auxiliary-provider-stop fallback, and
provider recovery.

The current `tc` corpus was empty and remained unchanged. A read-only custom dump was restored into
the disposable source before a deterministic fixture was added there only. Logical data and all 945
live column definitions matched; the report explicitly records PostgreSQL's expected compaction of
dropped-column attribute slots instead of claiming physical catalog identity. Evidence was written
as private regular single-link mode-0600 JSON and exact cleanup restored the pre-existing Docker,
database, current-`tc`, and prior-evidence inventories. See the
[wave 8 report](./sprint-f1-wave8-verification.md) and the
[authoritative remediated evidence](./evidence/pf-sf1-t09-wave8-local-disposable-sg9c42fd-20260907.json).

## Audit result

| Boundary | Evidence found | Consequence for the full-reindex design |
|---|---|---|
| Authoritative ledger | `authoritative_ledger_digest()` length-prefixes and hashes ordered JSON for the 23 domain/outbox tables declared in `AUTHORITATIVE_LEDGER_TABLES`. The projection rebuilder rechecks it after replay and never resets those tables. | The LightRAG workflow receives only a read-only snapshot reader. It has no ledger write handle and requires the source-ledger digest from the exact preview both before export and immediately before activation. |
| PostgreSQL projection and pgvector | `canonical_knowledge_snapshot()` already hashes nodes, edges, sources, evidence, embeddings, and checkpoints. `knowledge_embeddings` is `vector(1024)` with `model='bge-m3'`; the worker hashes the exact `node_content()` passed to BGE-M3. | Snapshot inspection requires exactly one current `NODE` embedding per corpus document with `model_version='bge-m3'`, matching content SHA-256, and a deterministic vector SHA-256. Partial, stale, duplicate-current, wrong-model, or wrong-dimension projection fails closed. |
| Incremental LightRAG input | `KnowledgeOutboxConsumer` and `KnowledgeJobConsumer` index PostgreSQL `knowledge_nodes` by UUID and `node_content()`. The incremental path may pass an evidence permalink as `file_paths`. | Full reindex uses the same node UUID/content, but canonicalizes citations to `postgresql://knowledge/nodes/<UUID>`. It never follows or exports remote permalinks, eliminating redirect/remote citation ambiguity. |
| Official adapter | `lightrag-hku==1.5.6` is pinned. The adapter validates `PGKVStorage`, `PGVectorStorage`, `PGDocStatusStorage`, and `PGGraphStorage`; PostgreSQL rows and the AGE graph are workspace-isolated through `POSTGRES_WORKSPACE`. | The official adapter is created only in the generation child. Workspace isolation alone is not activation; stable auxiliary target, exact generation workspace, provider contract/version, and runtime-config digest are attested separately. |
| Durable control plane | Migration `20260902_0030` owns immutable `lightrag_generations`, singleton/versioned `lightrag_active_generation_pointer`, and append-only `lightrag_generation_transition_events`. Repository guards reject unknown, stale, replay-conflicting, invalid activation and invalid rollback transitions while retaining rejected audit evidence. | Registration is immutable; activation and rollback require both expected generation identity and expected pointer version. A used/non-pristine control plane refuses downgrade instead of erasing evidence. |
| Provider/controller | `LedgerBackedGenerationController` binds the fresh isolated workspace and inspection to the exact preview, requires `OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID`, rechecks source digests immediately before registration/activation, seals authority ID, stable target digest, provider contract/distribution/version and runtime-config digest, then calls the durable CAS API. | A self-consistent counterfeit provider ID now fails before generation creation or ledger mutation. Ambiguous activation inspects the authoritative pointer and performs CAS rollback only if the new generation is exactly current. The controller accepts no credential input. |
| Runtime composition | Real API lifespan and worker startup build `IsolatedLightRAGGenerationRuntimeFactory` from complete, disabled-by-default `TC_LIGHT_RAG_*` settings and the same canonical provider constant, then compose one `LightRAGRuntimeResolver` per process against the TC PostgreSQL pointer. The production child passes the attested `runtimeConfigDigest` to its bootstrap. | Settings/factory self-consistency cannot substitute another provider identity. `applicationRuntimeComposition=true` records a code path, not a running environment; stale/unavailable output still falls back. |
| Remaining deployment wiring | The repository supplies the settings/factory/process composition and a local-only namespace-locked deterministic bootstrap, but no approved staging bootstrap or secret-manager values. Bootstrap import, auxiliary connect, and official storage initialization remain lazy until the first operation. | Wave 8 proves the local path only. `runtimeFactoryConfigured=false`, `approvedBootstrapAvailable=false`, `stagingCredentialsConfigured=false`, `liveApplySupported=false`, and `executionStatus=NOT_RUN` remain truthful for staging/live. Startup/readiness is not live provider evidence; manual parent `POSTGRES_WORKSPACE` replacement is forbidden. |
| Existing fallback rehearsal | The fixed disposable Docker rehearsal collision-checks labeled PostgreSQL resources, initializes real AGE+pgvector LightRAG storage, kills only the auxiliary process, verifies typed graph+pgvector fallback, restarts it, and cleans up. Its corpus/LLM inputs are deterministic fixtures. | It remains valid process-failure evidence, but it is not a full production-like corpus rebuild, checksum comparison, or generation cutover. The new workflow does not overextend that evidence. |

## Implemented workflow

The default CLI path validates declarations and prints a non-executing plan without reading database
URL environments. Read-only `--inspect` requires the exact preview confirmation and independently
attested authoritative and auxiliary database names/comments. Disposable targets require loopback;
remote staging-copy hosts must explicitly identify staging and use `sslmode=verify-full`; multi-host,
ambiguous query, production-like, and same-endpoint declarations are rejected. Production-token matching
also rejects numeric suffixes such as `prod01` or `main01` in target, database, workspace,
and remote-host labels while allowing unrelated whole tokens such as `productivity` and `mainland`.

The authoritative snapshot runs in `REPEATABLE READ, READ ONLY, DEFERRABLE` and checks PostgreSQL
16+, Alembic head `20260902_0030`, pgvector, exact BGE-M3 model/dimension/content hashes, and a
non-empty sorted UUID corpus. Evidence contains only target, ledger, projection, pgvector, corpus,
and citation digests plus cardinalities and byte counts. It omits corpus text, permalinks,
credentials, and connection URLs.

The auxiliary inspection requires a separately attested AGE+pgvector database. It checks the six
core document/chunk/vector tables, active workspace, exact equality between the authoritative
corpus ID set and the raw processed-status document ID set, full-document content checksum,
canonical citation checksum, document-chunk and vector-chunk document cardinality, and the
workspace AGE graph. Missing, extra, duplicate, wrong, non-processed, partial, or stale status rows
fail closed even when counts, corpus checksum, citations, chunks, vectors, and graph otherwise
match. AGE nodes are canonicalized by stable `entity_id`; edges use sorted semantic endpoint
identities and retain property content and multiplicity. Workspace-local AGE node/edge IDs and the
provider's insertion-time `created_at` metadata are excluded, and prior/cutover/failed-activation
workspaces must produce the same `tc.lightrag.semantic-age-graph.v2` digest before evidence is
sealed. This remains read-only and is reported as inspection, not activation.

`FullReindexWorkflow` plus `LedgerBackedGenerationController` is the guarded apply contract for an
injected isolated storage provider. It requires all of the
following before it can return success:

1. exact confirmation and manifest digest from the preview;
2. fresh source target/ledger/projection/pgvector/corpus digest and cardinality;
3. exact auxiliary target, canonical official PostgreSQL provider ID, and an embedded PostgreSQL control
   plane with zero redirects;
4. a never-reused fresh generation;
5. bounded export: at most 100,000 documents, 512 MiB total, 50 documents and 2 MiB per batch;
6. exact generation document/processed cardinality, exact processed-document identity-set
   equality without duplicates, zero failed documents, and corpus checksum;
7. a second source snapshot match immediately before activation;
8. immutable verified-generation registration in TC PostgreSQL;
9. atomic compare-and-swap activation against the observed prior generation and pointer version;
10. atomic compare-and-swap rollback if activation returns an error after changing the pointer.

Any pre-activation mismatch discards the isolated generation without touching the active one.
Ambiguous activation failure inspects the pointer and rolls back only when the new generation is
provably current. Rollback failure is surfaced as `rollbackSucceeded=false`; it is never converted
to success.

Application runtime composition is separate from that apply contract. Both process entrypoints
conditionally build the credential-owning factory when the complete opt-in validates. The parent holds
the auxiliary password as `SecretStr` (and may receive `TC_LIGHT_RAG_POSTGRES_PASSWORD` through its
deployment environment), but never installs the official provider's `POSTGRES_*` or pool. A selected
generation child receives the password over its already-open stdin, owns immutable `POSTGRES_*`, the
official pool and one temporary working directory, and suppresses provider stdout/stderr. Normal cache
retirement and API/worker shutdown finalize the child, then terminate/kill within bounds and remove the
directory. See the [deploy contract](../../deploy/knowledge/README.md#apiworker-공통-opt-in-설정) for
the exact settings and abrupt-termination cleanup boundary.

## Adversarial verification

Focused tests at
[`test_lightrag_full_reindex.py`](../../scripts/phase-f/tests/test_lightrag_full_reindex.py)
cover:

- stale source-ledger digest before export and stale pgvector digest after export;
- wrong authoritative/auxiliary target binding;
- remote/redirectable citation and provider control planes, multi-host database authorities,
  non-staging remote names, and weakened TLS mode;
- partial processed status and shadow generation cleanup;
- adversarial processed-status rows with missing, extra/stale, duplicate, wrong, or
  non-processed document identities despite otherwise matching auxiliary evidence;
- independent checksum and cardinality mismatches;
- ambiguous activation failure with successful rollback;
- explicit rollback failure state;
- bounded multi-batch export and secret/raw-corpus-free success evidence;
- unexpected database/provider exception redaction so secret-bearing error text is omitted;
- numbered production-shaped target, authoritative/auxiliary database, active-workspace, and remote-host
  tokens, with nearby unrelated-word controls that prevent prefix overblocking;
- default preview that reads no database secret and leaves execution `NOT_RUN`;
- migration ownership restricted to the three control-plane tables with no authoritative/projection
  table name in upgrade or downgrade SQL;
- two independent runtime resolver instances observing the same pointer, versioned cache replay,
  and pre/post-operation stale/disappearing binding result suppression.
- disabled-by-default API/worker settings composition, incomplete opt-in rejection without secret echo,
  exact resolver injection into knowledge projection, both claimable API knowledge job types, and shutdown
  finalization;
- controller-produced durable bindings routed through settings-built API and production worker processes
  across generation switch and rollback, with exact provider/version/runtime-digest rejection and public
  evidence secret-redaction checks.
- self-consistent counterfeit provider IDs rejected independently by Settings/factory composition and the
  ledger-backed controller before child creation, generation creation, or ledger writes;
- the real production child entrypoint, official storage-config builder, adapter initialize/index/finalize
  path, and bootstrap handoff exercised with a network-free core double, proving the key is exactly
  `runtimeConfigDigest` and no external credential is required.
- workspace-specific AGE internal IDs and provider insertion timestamps normalize to one semantic
  graph digest while node-property, edge-property, and edge-topology changes fail the explicit
  three-generation agreement check.

Verification executed locally:

| Check | Result |
|---|---:|
| Focused factory/controller/wave-8 adversarial tests | PASS — 86 tests |
| `pnpm test:phase-f` | PASS — 177 passed, 1 skipped |
| `pnpm check` | PASS — mypy 147 sources; 348 TypeScript tests passed with 1 skipped; 676 Python tests passed with 1 skipped and 109 PostgreSQL tests deselected; schema, 17 ADR, 44 operations, and OpenAPI gates |
| Wave-8 disposable PostgreSQL 16 + AGE + pgvector flow | PASS — rehearsal plus exact cleanup |
| Phase F UAT manifest validator | PASS — 15 workflows/33 automated/15 manual entries; manual execution remains `NOT_RUN` |

The wave-8 run used only generated loopback source/auxiliary databases and deterministic network-free
callables, then removed every created resource. No staging database, remote model provider, supplied
staging credential, staging corpus cutover, staging pointer mutation, or staging rollback ran.

## Exact remaining staging/provider gates

The following gates remain open and must not be inferred from the deterministic tests:

1. Provision the exact `20260902_0030` staging-copy authoritative database with its existing Phase F
   comment and a distinct PostgreSQL 16 AGE+pgvector auxiliary database with exact comment
   `tc-phase-f-lightrag-auxiliary:staging-copy:tc-phase-f-knowledge-staging-copy`.
2. Run the read-only inspection and retain its target, source-ledger, projection, pgvector, corpus,
   cardinality, and active-workspace evidence. The source pgvector projection must already be full
   and current; otherwise rerun the separately guarded PostgreSQL projection rebuild first.
3. Review and deploy a child bootstrap that returns the approved LLM/embedding callables, then inject the
   auxiliary password and other complete `TC_LIGHT_RAG_*` values into both API and worker identities from
   the approved staging secret manager. Prove least privilege and rotation without choosing a generation
   by mutating parent process environment.
4. Execute a complete staging corpus build, prove full processed/chunk/vector cardinality and exact
   corpus checksum, recheck all source digests, atomically activate it, query/citation-smoke the new
   generation, inject activation failure, and prove rollback to the previous generation.
5. Run the fallback switch with multiple API/worker processes and staging DNS/TLS behavior, then
   capture p95/p99, alert delivery/acknowledgement, recovery, and previous-generation retention or
   guarded disposal evidence.

Until those gates exist and run, PF-SF1-T09 remains externally open and the live reindex execution
status is exactly **NOT_RUN**.
