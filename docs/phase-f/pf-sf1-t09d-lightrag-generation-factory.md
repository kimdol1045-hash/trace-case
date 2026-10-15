# PF-SF1-T09D credential-owning generation runtime factory

- Audit/implementation date: 2026-09-04 (Asia/Seoul)
- Scope: local core factory, subprocess protocol, Settings composition, API/worker startup, and focused tests
- Verdict: **LOCAL API/WORKER RUNTIME COMPOSITION IMPLEMENTED; STAGING/LIVE ACCEPTANCE NOT RUN**

No staging credential, database, provider, generation pointer, or live data was read or changed. A local
disposable PostgreSQL 16 + pgvector database and protocol-only LightRAG child were used for the integrated
controller/API/worker routing test, then removed. API and worker defaults remain disabled. Their real
startup paths build the isolated factory from complete `TC_LIGHT_RAG_*` settings while retaining the
explicit test injection seam.

## Why the boundary is a subprocess

The installed official `lightrag-hku==1.5.6` implementation reads `POSTGRES_HOST`, port, user,
password, database, workspace, pool size, SSL, and vector settings from `os.environ` in
`ClientManager.get_config()`. `ClientManager` owns one process-wide PostgreSQL client/pool and
`LightRAG.__post_init__()` initializes additional process-global shared storage. Temporarily replacing
environment variables under a thread lock cannot isolate constructors or later async pool use and would
allow concurrent generations to cross-route.

`OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID` is the single code identity for this adapter.
`IsolatedLightRAGGenerationRuntimeFactory` therefore selects an exact typed config and returns a proxy
whose first operation starts one long-lived child interpreter. The parent may receive the
`TC_LIGHT_RAG_POSTGRES_PASSWORD` deployment setting from its secret-injection environment and retains it
only as `SecretStr`; the password is then sent through the child's already-open stdin, never argv. Only
the child installs immutable official `POSTGRES_*`, imports the provider bootstrap with exact
`authorityId`, `providerId`, `workspace`, and `runtimeConfigDigest`, verifies version 1.5.6
again, constructs the four PostgreSQL adapters, and owns their process-global pool/shared state. Provider
stdout/stderr is not forwarded into application logs; the parent accepts only bounded JSON-lines responses.

## Binding and secret contract

Factory selection and child startup attest all of the following:

- the complete durable `ActiveGenerationBinding` digest;
- exact provider ID, provider contract, distribution, and version;
- exact generation workspace supplied by the active durable binding;
- exact auxiliary authority ID;
- the stable auxiliary-target digest, separate from the generation workspace;
- a canonical runtime-config digest covering authority, stable target, provider/bootstrap, version, host,
  port, role, database, pool/SSL values, storage classes, AGE capability, and deployment environment;
- all four official storage classes and the Apache AGE requirement.

The PostgreSQL password is a `SecretStr`, is omitted from the canonical digest so password rotation does
not redefine corpus identity, and is not included in repr, attestation/evidence, errors, or logs. Workspace
is also excluded from the runtime-config digest so consecutive immutable generations on one authority can
select distinct workspaces. The durable row seals authority ID, stable target digest, exact provider
contract/distribution/version, runtime-config digest, and its own workspace as separate fields.
Settings composition, factory validation, and the full-reindex controller reject any provider ID other
than the canonical constant even when config, binding, runtime digest, and storage claim are mutually
consistent.
Missing/disabled providers, missing or wrong versions, malformed bindings, authority/provider/workspace/
digest mismatch, storage/AGE mismatch, startup failure, timeout, process exit, and protocol errors fail
closed as typed LightRAG unavailability.

## Lifecycle and cache behavior

The factory adds no second cache. `LightRAGRuntimeResolver` remains the deterministic one-entry cache keyed
by `(pointerVersion, generationId, registrationDigest)`. Each cache miss creates a lazy child proxy;
eviction retires the old proxy, waits for its last in-flight lease, then performs bounded shutdown.
Timeout or failed shutdown escalates to terminate and bounded kill, and the exact generated temporary
working directory is removed. Rollback to the same generation at a newer pointer version creates a new
child rather than reviving mutable process state.

Focused tests use a protocol-only child to prove two generation workspaces can rendezvous concurrently in
different PIDs without changing parent `POSTGRES_*`, reject wrong child attestation, verify redaction,
exercise all binding/provider/version/storage gates, and verify eviction/final cleanup. The disposable
cross-boundary test additionally creates controller rows in PostgreSQL, routes settings-built API and
production worker resolvers through generation switch and rollback, and rejects wrong provider/version/
runtime digest. A separate network-free harness invokes the real production child entrypoint, official
storage-config builder, auxiliary adapter initialization, index call, and shutdown with a core double; it
fails if the bootstrap receives `configDigest` instead of `runtimeConfigDigest`. It deliberately does not
claim a live AGE schema or official provider query.

## Application composition and readiness boundary

The API lifespan and worker `build_worker()` each call the same settings composer. A complete opt-in
constructs one resolver per process against the process's TC PostgreSQL engine; API knowledge search and
worker knowledge projection then use the resolver through `LightRAGIndex`. Disabled or partially supplied
settings construct nothing or fail startup, and an injected consumer cannot bypass enabled worker routing.
API/worker shutdown finalizes their resolver.

The repository does not contain the approved staging bootstrap implementation or secret-manager values.
Bootstrap import, auxiliary connect, AGE/storage initialization and the first official query/index are lazy
child operations. Consequently local startup composition is complete and
`applicationRuntimeComposition=true`, while `runtimeFactoryConfigured=false`,
`approvedBootstrapAvailable=false`, `stagingCredentialsConfigured=false`,
`liveApplySupported=false`, and `executionStatus=NOT_RUN` remain the deployment truth.

## Verification performed

| Exact command | Result |
|---|---:|
| Focused factory and Phase-F controller tests | PASS — 61 tests |
| Focused API and worker composition tests | PASS — 53 tests |
| `pnpm test:phase-f` | PASS — 119 tests |
| `pnpm lint:py` | PASS |
| `pnpm check` | PASS — mypy 144 sources; 348 TypeScript tests with 1 live E2E skipped; 616 Python tests with 109 PostgreSQL tests deselected; schema, ADR, operations, and OpenAPI gates |
| `node scripts/validate-contracts.mjs` | PASS — 13 schemas, generation contracts, and 12 Slack fixtures |
| Focused disposable PostgreSQL controller → settings-built API/worker switch/rollback test | PASS — 1 test |

The focused factory module also passed Ruff and mypy. The earlier guarded AGE+pgvector harness evidence was
not rerun; it did not provide or execute the new approved child bootstrap, staging settings, or application
cutover.

## Remaining architectural and external gates

1. Supply an approved child bootstrap that constructs production LLM and embedding callables without
   returning their secrets to the parent, and load PostgreSQL `SecretStr` values from the approved secret
   manager.
2. Supply the complete disabled-by-default settings to the already-composed API and worker paths. Use the
   same authority ID, stable auxiliary-target digest, provider ID, and connection attestation in both
   processes; let each active generation row select its workspace. Prove exact secret-manager injection,
   least-privilege grants, rotation, and the bootstrap provider boundary.
3. Run the real child against an attested disposable/staging PostgreSQL 16 + pgvector + AGE authority and
   prove TLS/DNS behavior, schema initialization, query/index semantics, load, crash recovery, and pool
   closure.
4. Run multi-process staging CAS activation/rollback and citation smoke. Until those external gates pass,
   `liveApplySupported=false`, `executionStatus=NOT_RUN`, and PF-SF1-T09 remains open; local
   `applicationRuntimeComposition=true` is not staging execution evidence.
