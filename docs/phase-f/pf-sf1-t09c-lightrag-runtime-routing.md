# PF-SF1-T09C LightRAG runtime routing evidence

- Audit date: 2026-09-04 (Asia/Seoul)
- Scope: API, worker, and knowledge LightRAG construction/query/index paths
- Verdict: versioned resolver and API/worker opt-in runtime composition implemented; staging execution remains open

No staging/live PostgreSQL, provider, R2, or staging operation was performed. Local verification used a
disposable PostgreSQL 16 + pgvector control plane and protocol-only LightRAG children; the guarded
failover rehearsal separately used disposable AGE+pgvector Docker resources and removed them. The
authoritative staging/live QA/knowledge ledger was not changed.

## Composition inventory

| Process/path | Previous route | Versioned route after T09C |
|---|---|---|
| API startup and `/api/v1/knowledge/search` | `create_app()` retained one injected `LightRAGAuxiliaryIndex` in application state; the dependency and `KnowledgeService` reused it without consulting the active-generation pointer. | The real `app = create_app()` lifespan loads settings; a complete `TC_LIGHT_RAG_*` opt-in builds the isolated factory and composes one resolver against the API's TC PostgreSQL engine. The request dependency exposes it through `LightRAGIndex`. Static/factory injection remains a test seam and conflicts with settings-enabled composition. |
| Incremental worker projection | `build_automatic_knowledge_consumer()` did not accept or compose LightRAG; direct consumers could retain one injected static adapter. | The real `main() -> build_worker()` path builds the same settings factory and resolver, passes it into `KnowledgeOutboxConsumer`, and finalizes cached generation runtimes at worker shutdown. An injected consumer cannot bypass enabled routing. |
| Queued knowledge projection/reindex | `KnowledgeOutboxConsumer` and `KnowledgeJobConsumer` accepted a concrete static adapter for `index_document()`. | Production `build_worker()` registers claim-aware `KNOWLEDGE_PROJECTION` and `KNOWLEDGE_REINDEX` handlers. They lazily construct `KnowledgeJobConsumer` when automatic projection is disabled, share the process resolver, leave claim/heartbeat/success/failure ownership to the generic worker, and cannot silently starve behind its registered-type filter. |
| Official LightRAG constructor | `build_official_lightrag_postgres_runtime()` was used by tests and the isolated failover rehearsal and validated process-global PostgreSQL environment values. | It is selected only inside the T09D per-generation child boundary. The credential-owning factory returns an exact-binding-attested proxy and never rotates `POSTGRES_WORKSPACE` or another environment value in the parent process. |

`rg` found no other application LightRAG composition path. Rehearsal and PostgreSQL tests build
the official adapter directly by design; they are disposable test boundaries, not API/worker
runtime routing.

## Durable routing and cache invariant

`LightRAGGenerationRepository.active_generation()` reads the singleton pointer and its immutable
`lightrag_generations` row in one outer-joined SQL statement. It fails closed for a missing
singleton, no active generation, an invalid pointer shape, a missing target row, or any target not
marked `VERIFIED`. The returned binding contains no credential and is cache-fenced by:

```text
(pointer_version, generation_id, registration_digest)
```

Every `search()` and `index_document()` operation executes this sequence:

1. atomically read and validate the durable binding;
2. acquire or build a runtime whose factory attestation exactly equals that binding;
3. reread the binding before invoking the auxiliary index;
4. buffer the complete auxiliary result (streaming output is not accepted by the adapter);
5. reread the binding after the operation and expose the result only if the entire binding still matches.

A pointer change, rollback to the same generation at a newer version, missing generation, invalid
metadata, factory target mismatch, storage exception, or post-call disappearance returns the
existing typed `LIGHTRAG_UNAVAILABLE` result with
`typed_graph+pgvector` fallback. Stale context, record IDs, and indexing receipts are discarded.
Retired cache entries remain alive only for their in-flight leases and are finalized when the last
lease releases; they are never selected by an unversioned workspace/environment cache.

Each API or worker process owns an independent cache but reads the same TC PostgreSQL pointer on
every operation. No notification bus or environment reload is required for one process to observe
another process's CAS activation or rollback.

## Focused evidence

The focused unit and composition tests cover:

- two resolver instances observing the same pointer and switching independently;
- same-version cache reuse, activation invalidation, and rollback to the same identity at a newer version;
- no-active, missing, and invalid generation fallback without invoking a provider factory;
- pointer changes during queries and writes, with complete candidate/receipt discard;
- generation disappearance after an auxiliary call;
- exact factory target attestation and rejection of a mismatched workspace;
- exact canonical official PostgreSQL provider identity at Settings/factory and controller boundaries,
  including rejection when a counterfeit config, binding, digest, and storage claim all agree;
- production child bootstrap handoff of `runtimeConfigDigest`, covered through the real child adapter
  lifecycle with a network-free core double;
- API runtime-factory composition and static/factory conflict rejection;
- worker resolver composition, both API-enqueued knowledge job handlers, claim-filter inclusion, consumer
  injection, and shutdown finalization;
- atomic repository resolution of pointer plus immutable generation metadata;
- one disposable PostgreSQL flow in which the controller registers and activates generation A, the
  settings-built API and production worker route to A, both observe generation B, both observe CAS rollback
  to A at pointer version 3, wrong provider/version/runtime digest fail closed, both API job types finish
  `SUCCEEDED`, and the configured secret is absent from public evidence.

The Phase-F generation contract now records the resolver name, versioned cache key, per-operation
before/after attestation, credential-free input, stale-result behavior, and prohibition on manual
environment switching. The T09D factory is now composed by the real API and worker startup paths when
the disabled-by-default opt-in is complete, so `applicationRuntimeComposition=true`. This field records
code composition only: no approved bootstrap implementation, secret-manager staging values, or running
staging instance was supplied, so `runtimeFactoryConfigured=false`, `liveApplySupported=false`, and
execution remains `NOT_RUN`. Bootstrap import and provider connection are lazy and are not proven by
startup/readiness alone.

Executed checks:

| Command | Result |
|---|---:|
| Focused factory and Phase-F controller tests | PASS — 61 tests |
| Focused API and worker composition tests | PASS — 53 tests |
| `pnpm test:phase-f` | PASS — 119 tests |
| `pnpm check` | PASS — lint/typecheck; mypy 144 sources; 348 TypeScript tests with 1 live E2E skipped; 616 Python tests with 109 PostgreSQL tests deselected; schema, ADR, operations, and OpenAPI gates |
| `pnpm test:schema` | PASS — 13 schemas, generation contracts, 12 Slack fixtures |
| Focused disposable PostgreSQL controller/application routing test | PASS — 1 test |

The disposable controller/application test uses a protocol-only child and test-only credential, not the
official bootstrap or AGE storage. The earlier guarded AGE+pgvector harness evidence was not rerun and did
not exercise a staging bootstrap or staging application cutover. The
[wave 4 report](./sprint-f1-wave4-verification.md) records the exact scope.

## Remaining integration gates

1. Review and deploy the child bootstrap and supply the exact authority/stable-target/provider connection
   settings to both real process identities; each controller-produced active row supplies the generation
   workspace and sealed provider contract/distribution/version/runtime digest. Inject the auxiliary
   `SecretStr` password from the approved secret manager using a least-privilege PostgreSQL role.
2. Prove the staging auxiliary AGE+pgvector target, TLS/role grants, bootstrap/provider behavior, secret
   rotation, independent process caches, and bounded child/orphan cleanup.
3. Run multi-process staging activation, query/index cutover, failure injection, CAS rollback,
   fallback, recovery, cache-retirement, and resource-closure evidence. Until then, execution is
   `NOT_RUN` and PF-SF1-T09 is not closed.
