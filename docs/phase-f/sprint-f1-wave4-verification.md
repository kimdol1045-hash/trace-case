# Phase F Sprint F1 wave 4 LightRAG remediation verification

- Verification date: 2026-09-04 (Asia/Seoul)
- Scope: controller/runtime binding remediation, production API/worker composition, queue routing,
  independent RED finding PF-SF1-T09 remediation, deploy/operations contracts, ADR, and roadmap
- Verdict: **LOCAL COMPOSITION AND DISPOSABLE CROSS-BOUNDARY EVIDENCE COMPLETE; STAGING EXECUTION NOT RUN**

No staging/live credential, secret-manager value, database, provider, generation pointer, corpus, or
operator workflow was read or changed. Local verification used one isolated PostgreSQL 16 + pgvector
database, protocol-only generation children, and the separately guarded AGE+pgvector failover harness.
Only test-scoped control-plane/background-job rows were mutated; the disposable resources were removed.

The repository truthfully reports `applicationRuntimeComposition=true` and
`productionKnowledgeJobsClaimable=true`. It keeps `runtimeFactoryConfigured=false`,
`approvedBootstrapAvailable=false`, `stagingCredentialsConfigured=false`,
`liveApplySupported=false`, and `executionStatus=NOT_RUN`.

## Remediated contract

| Boundary | Integrated behavior | Remaining acceptance boundary |
|---|---|---|
| Controller/ledger | Registration seals stable auxiliary authority ID/digest separately from generation workspace, plus the canonical official PostgreSQL provider ID, exact provider contract, distribution, version, and runtime-config digest. `LedgerBackedGenerationController` rejects a storage/controller pair whose counterfeit provider ID is internally consistent. Activation and rollback remain expected-generation + expected-pointer-version CAS. | No staging inspection, full-corpus registration, pointer mutation, or rollback ran. |
| Factory/child | `OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID` is the single code identity. Settings composition and the factory both reject any other provider ID before child creation, even if its binding and digest agree. The production child passes the attested `runtimeConfigDigest` to the reviewed bootstrap, then owns child-only `POSTGRES_*`; remote staging/production authorities require `verify-full`. | The repository contains no approved staging bootstrap or staging secret-manager values. The network-free child contract double is not official AGE/provider live success. |
| API | The real lifespan builds the settings factory/resolver against the TC PostgreSQL engine, exposes it through `LightRAGIndex`, checks the active binding for readiness, and finalizes it on shutdown. | Startup/readiness is not a staging auxiliary query or citation smoke. |
| Worker | Production `build_worker()` builds the same resolver and always registers `KNOWLEDGE_PROJECTION` and `KNOWLEDGE_REINDEX`. It lazily constructs `KnowledgeJobConsumer` when automatic projection is disabled, shares the resolver, preserves generic claim/heartbeat/outcome ownership, and finalizes on shutdown. | No staging queue drain, corpus reindex, or multi-worker load proof ran. |
| Defaults/secrets | `TC_LIGHT_RAG_ENABLED=false` creates no factory. Partial/mixed opt-in fails startup. Passwords remain `SecretStr`, are excluded from digests/repr/public errors/evidence/argv, and are sent only over the selected child's open stdin. | Secret-manager encryption, least privilege, rotation, revocation, and operator recovery remain unproved. |

The runtime-config digest deliberately excludes password and workspace. Password rotation therefore does
not redefine immutable evidence, and consecutive generations on one stable auxiliary authority can use
fresh workspaces. The controller stores workspace, stable target digest, provider identity/version, and
runtime digest as distinct attestations; the resolver compares the complete binding and fences its cache
by `(pointer_version, generation_id, registration_digest)`.

## Cross-boundary proof

[`test_lightrag_application_routing_postgresql.py`](../../backend/tests/repository/test_lightrag_application_routing_postgresql.py)
runs this local disposable flow:

1. confirm disabled settings build no runtime factory;
2. build the exact factory from enabled `Settings` and use its credential-free attestation in the
   full-reindex controller;
3. register/activate generation A through the real PostgreSQL control plane;
4. start the real API lifespan and production worker composition from the same settings, then prove both
   resolve A through independent protocol-only children;
5. enqueue projection and reindex through the API and prove the production worker claims both jobs and
   commits both as `SUCCEEDED`;
6. controller-register/activate generation B and prove both processes route to B;
7. controller CAS-rollback to A at pointer version 3 and prove both processes build a fresh A runtime;
8. reject wrong provider, version, and runtime digest, including self-consistent counterfeit provider IDs
   at both Settings/factory and controller boundaries;
9. run the real production child adapter contract with a network-free core double and prove the bootstrap
   receives `runtimeConfigDigest` rather than the nonexistent legacy `configDigest` key;
10. omit the test secret from all public evidence and verify shutdown removes child working directories.

This is real controller/pointer/API/worker composition evidence on disposable PostgreSQL. The child is a
bounded protocol fixture, not the official staging bootstrap, LLM/embedding provider, or AGE storage, so
it does not make live apply supported.

## Verification performed

| Exact check | Result |
|---|---:|
| `node scripts/python.mjs -m pytest backend/tests/unit/test_lightrag_generation_factory.py scripts/phase-f/tests/test_lightrag_full_reindex.py -q` | PASS — 61 tests |
| `node scripts/python.mjs -m pytest apps/api/tests/test_api.py apps/worker/tests/test_worker.py -q` | PASS — 53 tests |
| `pnpm test:phase-f` | PASS — 119 tests |
| `pnpm check` | PASS — Ruff/ESLint/typecheck; mypy 144 sources; 348 TypeScript tests passed with 1 live E2E skipped; 616 Python tests passed with 109 PostgreSQL tests deselected; schema, 17 ADR, 44 operations, and OpenAPI drift gates |
| `pnpm test:schema && pnpm test:adr && pnpm test:operations` | PASS — 13 schemas, 17 ADRs, and 44 operations tests |
| `TC_TEST_DATABASE_URL=<local-disposable-url> node scripts/python.mjs -m pytest backend/tests/repository/test_lightrag_application_routing_postgresql.py -m postgres -q` | PASS — 1 PostgreSQL test |

The PostgreSQL rerun used the existing loopback `tc_test` disposable database and no staging copy. The
earlier guarded AGE+pgvector failover evidence remains unchanged and was not rerun for this remediation.
No staging database, Ollama, remote provider, supplied deployment credential, or staging pointer was used.

## Remediation file set

- Runtime and composition: `backend/src/tc_backend/knowledge/lightrag.py`,
  `lightrag_generation_factory.py`, `lightrag_composition.py`, and
  `lightrag_generation_process.py` in the same package.
- Controller: `scripts/phase-f/lightrag_full_reindex.py`.
- Focused tests: `backend/tests/unit/test_lightrag_generation_factory.py`, its
  `fixtures/lightrag_generation_production_child_harness.py`, and
  `scripts/phase-f/tests/test_lightrag_full_reindex.py`.
- Contract and deployment truth: `deploy/knowledge/lightrag-full-reindex-contract.json` and
  `deploy/knowledge/README.md`.
- Design and status truth: this report, `pf-sf1-t09-lightrag-full-reindex.md`,
  `pf-sf1-t09c-lightrag-runtime-routing.md`, `pf-sf1-t09d-lightrag-generation-factory.md`,
  `docs/adr/0009-lightrag-as-derived-index.md`, and `docs/test-management-roadmap.md`.

## Remaining gates — all `NOT_RUN`

1. **Credentials/bootstrap:** review and deploy a child-importable bootstrap; provision distinct
   least-privilege API/worker secret-manager bindings; prove `verify-full`, grants, rotation, and revocation.
2. **Live apply/corpus:** inspect the exact staging-copy authoritative and auxiliary targets, build the
   complete staging corpus, and prove processed identity/cardinality, chunks/vectors/AGE graph, checksums,
   citations, and source recheck.
3. **Pointer/cutover/rollback:** mutate the staging pointer only through the guarded controller, prove
   multi-process API/worker query and index cutover, inject failure, and retain exact CAS rollback evidence.
4. **Load/recovery:** prove child retirement/orphan cleanup, DNS/TLS failure, fallback/recovery, p95/p99,
   queue drain without starvation, alerts, backup/restore, and previous-generation retention/disposal.
5. **Operator proof:** retain sanitized evidence and complete release-owner/operator/reviewer handoff.

Until those owner-operated staging gates pass, PF-SF1-T09 and the Phase F release gate remain open.
