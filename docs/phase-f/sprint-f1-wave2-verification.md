# Phase F Sprint F1 wave 2 integration verification

> 이 문서는 2026-09-01 wave 2 시점의 historical evidence다. 이후 durable generation
> control plane/controller/runtime routing 상태는
> [wave 3 통합 검증](./sprint-f1-wave3-verification.md)이 갱신한다.

- Verification date: 2026-09-01 (Asia/Seoul)
- Scope: `PF-SF1-T06`, `PF-SF1-T09`, `PF-SF1-T11`, and `PF-SF1-T12` local tooling integration
- Verdict: **LOCAL FOUNDATION VERIFIED; EXTERNAL ACCEPTANCE AND RELEASE GATES OPEN**

This integration reconciles the UAT collector, unified operations runbook, LightRAG full-reindex
contract, and concurrent-load profile in the shared repository. It validates only bounded local
code, configuration, preview, and test behavior. No deployed origin, live external service,
staging provider, production data, or 100,000-case workload was mutated or executed.

## Integrated contracts

| Task | Integrated local evidence | Status after wave 2 |
|---|---|---|
| `PF-SF1-T06` | The fixed 100,000-case concurrent profile is a separate preview-first tool covering in-process API, signed synthetic Slack ingress, worker queue/lease behavior, and no-outbound generation scheduling. Preview reports `workUnitsExecuted=0` and `loadGate=NOT_RUN`. | **OPEN** — no attested disposable execution or operator/reviewer handoff |
| `PF-SF1-T09` | The projection/fallback runbook now links the provider-neutral full-reindex/checksum contract and read-only exact-target inspector. The current official adapter truthfully reports no durable active-generation pointer or atomic apply capability. | **OPEN** — staging inspection, provider implementation, corpus cutover, smoke, and rollback not run |
| `PF-SF1-T11` | The strict manifest has 15 workflows, 33 same-origin GET checks, and 15 separately hashed owner checks. The validator cannot return complete while owner evidence is `NOT_RUN`. | **OPEN** — deployed-origin collection and all owner UAT not run |
| `PF-SF1-T12` | The Korean-first operations entrypoint retains 14 canonical sections, exact local references, canonical shell/SQL secret and destructive-target guards, stop conditions, and explicit handoff requirements. | **OPEN** — site commands and live operator/reviewer rehearsal evidence not supplied |

The root `pnpm check` path now includes `scripts/phase-f/tests` and typechecks all three wave-2
Python tools. `pnpm test:phase-f` is the bounded focused regression entrypoint, and
`pnpm phase-f:uat:validate` is the safe manifest-only operator entrypoint. Argument-bearing load and
LightRAG previews remain documented as exact commands rather than being hidden behind a shortcut
that could obscure target identity.

## Red-finding remediation verification

The three integration findings were rechecked together after their fixes landed:

| Finding | Adversarial reproduction | Fail-closed evidence |
|---|---|---|
| Operations secret/destructive guard | Nine sensitive-literal variants and 23 broad `rm`, `find -delete`, `DROP`, and `TRUNCATE` variants were appended to otherwise-valid runbook input. The second remediation adds LF/CRLF shell continuations, SQL newlines, line/block comments, mixed casing/spacing, and named/numeric entities. | Every literal and destructive variant produced its expected guard error. Empty values, placeholders, variable references, bounded filesystem/SQL controls, SQL-commented examples, and ordinary multiline Korean prose remained allowed. |
| LightRAG processed-document identity | The auxiliary status rows reported two processed documents and matching corpus, citation, chunk, and graph evidence, but substituted `...0003` for expected source document `...0002`. | `statusDocumentIdentitySetMatches=false` and `matchesSourceSnapshot=false`. Missing, extra, duplicate, and partially processed nearby cases also fail closed, while the exact identity set passes. |
| Concurrent-load warm generation sample | Warm `generationScheduling` reported `count=0` and zero-valued percentiles while the combined phase and lag samples were otherwise valid. | `passed=false`; both `GENERATION_SCHEDULE_P95_EXCEEDED` and `SURFACE_MISSING` are present, so a synthetic zero p95 cannot satisfy the gate. |

### Second operations-guard remediation

The final RED reverification isolated 12 safety-significant forms: four continued
`TC_API_TOKEN` literal assignments and eight multiline/comment/encoded `DROP` or `TRUNCATE`
forms. All 12 selected probes failed before the scanner change. The scanner now builds separate
canonical shell and SQL views from an allowlisted entity-decoded source: the shell view folds only
immediate backslash-LF/CRLF continuations, while the SQL view masks complete line and block comments
as whitespace without erasing line boundaries. Keeping the views separate prevents SQL `--`
handling from hiding shell option separators such as `rm -- /`.

The post-change focused run passed 14 probes, including the 12 former RED cases and two grouped
safe-control checks. No command was executed, no runbook content was mutated by the validator, and
the test inputs remained in-memory extensions of the authoritative document. This remediation is
local validator evidence only; it does not change any external gate or the Wave 2 verdict.

The package-level Wave 2 regression now also asserts that `test:operations` executes both the
adversarial validator tests and the authoritative validator, and that `test:phase-f` retains the
load-profile suite alongside the Phase F directory. This is a bounded wiring check; the behavioral
proof remains in the focused suites above.

## Verification commands and results

The following focused checks ran before and after integration as applicable:

| Exact command | Result |
|---|---|
| `node scripts/python.mjs -m pytest scripts/phase-f/tests/test_uat_evidence.py -q` | PASS — 21 tests |
| `node scripts/python.mjs -m pytest scripts/phase-f/tests/test_lightrag_full_reindex.py -q` | PASS — 25 tests |
| `node scripts/python.mjs -m pytest scripts/tests/test_phase_f_load_profile.py -q` | PASS — 25 tests |
| `pnpm test:phase-f` | PASS — 93 tests, including the package-wiring regression |
| `pnpm test:operations` | PASS — 44 validator tests and all 14 runbook sections |
| `node --test --test-name-pattern='multiline sensitive environment literal\|placeholders and variable references across valid shell continuations\|drop database across LF\|truncate table across LF\|SQL line comment\|CRLF with spacing\|multiline block comment\|encoded whitespace\|named newline entity\|SQL placeholders, bounded commands' scripts/tests/validate-operations-runbook.test.mjs` | PASS — 14 selected remediation and safe-control probes |
| `pnpm lint` | PASS — ESLint and Ruff |
| `node scripts/python.mjs -m pytest scripts/phase-f/tests/test_lightrag_full_reindex.py -q -k status_identity_probe_rejects_wrong_ids_despite_matching_evidence` | PASS — exact wrong-identity probe; 1 passed, 24 deselected |
| `node scripts/python.mjs -m pytest scripts/tests/test_phase_f_load_profile.py -q -k zero_warm_generation_samples_cannot_pass_via_zero_p95` | PASS — exact zero-warm-generation probe; 1 passed, 24 deselected |
| `pnpm phase-f:uat:validate` | PASS — 15 workflows, 33 automated checks, 15 manual checks; manifest SHA-256 `534fb9f66ffadedbbcf06a53e46384ec95fbc380ec06fd9012b13cb25cb665a7` |
| documented LightRAG default preview for `tc-phase-f-knowledge-staging-copy` | PASS — `executes=false`, `mutates=[]`, `executionStatus=NOT_RUN`, `liveApplySupported=false` |
| documented concurrent-load default preview for `tc-phase-f-load-disposable` | PASS — `workUnitsExecuted=0`, `loadGate=NOT_RUN`; no output file created |
| `node scripts/python.mjs -m ruff check scripts/phase-f/uat_evidence.py scripts/phase-f/lightrag_full_reindex.py scripts/phase-f/load_profile.py scripts/phase-f/tests/test_uat_evidence.py scripts/phase-f/tests/test_lightrag_full_reindex.py scripts/tests/test_phase_f_load_profile.py` | PASS |
| `node scripts/python.mjs -m mypy backend/src scripts/phase-f/uat_evidence.py scripts/phase-f/lightrag_full_reindex.py scripts/phase-f/load_profile.py` | PASS — 84 source files |

The repository-wide and disposable-database verification completed as follows:

| Exact command | Result |
|---|---|
| `pnpm check` | PASS — ESLint and Ruff; TypeScript typecheck; mypy `139` source files; `348` TypeScript tests; `540` Python tests with `109` PostgreSQL tests deselected; 13 schemas, 12 Slack fixtures, 17 ADRs, 14 operations sections/44 validator tests, and OpenAPI/client drift |
| `pnpm --filter @tc/web exec tsc --project tsconfig.json` then `pnpm --filter @tc/web exec vite build --outDir /tmp/tc-remediation-check.mBQy3n/web --emptyOutDir` | PASS — temporary-output TypeScript plus Vite production build, 99 modules transformed; no repository `dist` output |
| `pnpm test:schema` | PASS — 13 strict Draft 2020-12 schemas, versioned generation contracts, and 12 Slack fixtures |
| `pnpm test:adr` | PASS — 17 ADRs, 15 canonical decisions, and 7 linked contract schemas |
| `pnpm openapi:check` | PASS — OpenAPI schema and generated TypeScript client are current |
| `TC_TEST_DATABASE_URL=postgresql+psycopg://tc:tc-test-only@127.0.0.1:55439/tc_wave2_test node scripts/python.mjs -m pytest backend/tests/repository/test_cases_postgresql.py backend/tests/repository/test_jobs_postgresql.py -m postgres -q` | PASS — 14 tests on a fresh local `pgvector/pgvector:0.8.1-pg16` disposable container |
| `TC_TEST_DATABASE_URL=postgresql+psycopg://tc:tc-test-only@127.0.0.1:55439/tc_wave2_test node scripts/python.mjs -m pytest apps/api/tests/test_knowledge_api_postgresql.py -m postgres -q` | PASS — 3 tests on the same disposable container |
| exact-container cleanup and `docker ps -a --filter name=tc-phase-f-wave2-verify-c5e3a7c5` | PASS — no matching container remains; the pre-existing development PostgreSQL was not used |

The disposable PostgreSQL scope was intentionally limited to case persistence, job queue/lease,
and knowledge API/projection regressions relevant to this wave. It did not create 100,000 cases,
exercise the LightRAG provider, or convert an external acceptance gate into local evidence.

The remediation rerun isolated new mypy, Ruff, and Web output beneath
`/tmp/tc-remediation-check.mBQy3n`; that exact temporary root was trashed after verification. It
did not bulk-delete pre-existing shared project caches. Only the bytecode generated by the new
Wave 2 wiring regression was removed, and no repository `apps/web/dist` directory was created.

## Limitations and next gates

The following evidence remains exactly **NOT RUN / OPEN**:

- deployed exact HTTPS origin collection and all 15 accountable-owner UAT workflows, including
  the physical 390px and one-event two-file browser checks;
- an attested empty loopback disposable database run of the fixed 100,000-case concurrent profile,
  host/DB/container resource observation, threshold approval, and operator/reviewer handoff;
- a staging-copy authoritative/auxiliary LightRAG inspection, a TC-owned durable active-generation
  control plane, bounded full-corpus provider execution, checksum/cardinality proof, atomic cutover,
  query/citation smoke, and injected rollback proof;
- site-specific lifecycle controller commands; real Slack, R2, secret rotation, provider/model,
  backup/restore, alert delivery/acknowledgement, rollback, and credential-loss rehearsals;
- staging migration and recovery gates retained from wave 1, production deployment, initial
  observation, release approval, and roadmap `PF-SF1-T13`/`T14` governance gates.

Accordingly, wave 2 does not close `PF-SF1-T06`, `T09`, `T11`, or `T12`, does not complete Phase F,
and does not claim a production release.
