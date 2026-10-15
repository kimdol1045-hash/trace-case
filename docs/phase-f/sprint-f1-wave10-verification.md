# Sprint F1 wave 10 — PF-SF1-T11 local isolated physical-browser UAT

- Date: 2026-09-07 (Asia/Seoul)
- Verdict: **LOCAL ISOLATED PHYSICAL-BROWSER AUTOMATED UAT sub-gate PASS; PF-SF1-T11 and Phase F remain open**
- Authoritative final-code run: `92af97f8`
- Automated workflows: 15 PASS, 0 NOT_RUN, derived from 37 executed named assertions
- Human workflows: 0 PASS, 15 NOT_RUN
- Browser: system-installed Google Chrome, headless, 1440×1000 and 390×844
- Network: product mocking disabled, service workers blocked, external requests 0

## Independent RED and remediation provenance

Independent review `task_0c95d0badb67` found that the request-failure producer assigned
`HARNESS_CONTEXT_TEARDOWN` to every local `ERR_ABORTED` after teardown began, while the strict
validator accepted that disposition without constraining the method, resource type, exact browser
origin/port or normalized path. The then-passing test fixture demonstrated the defect by accepting an
aborted MinIO object `PUT` as harness teardown. This invalidated `r10k8a3d` as final-code evidence even
though its recorded failures happened to be browser-origin GET/fetch route aborts.

Remediation dispatch `task_d1aff5905dae` made both sides fail closed. The producer can now emit a safe
disposition only for `NET_ERR_ABORTED`, exact browser-port `GET`/`fetch` requests whose normalized path
is in the harness's explicit route-abort allowlist; `WORKFLOW` maps only to
`CLIENT_ABORT_CONTROLLER_ROUTE_TEARDOWN` and `HARNESS_TEARDOWN` maps only to
`HARNESS_CONTEXT_TEARDOWN`. The validator independently enforces the same exact visible receipt shape
and rejects object-storage or foreign/unknown origins, PUT or other methods, non-fetch resources,
non-aborts, disposition/phase mismatches, unlisted paths, unexpected fields and any unresolved
failure. The permissive fixture was replaced by one exact valid teardown receipt plus adversarial
producer and sealed-validator rejections.

## Remediation result

The Wave 10 runner no longer emits a fixed PASS list. Each workflow result is derived from its exact
named assertion receipts, and execution fails if a workflow or assertion is missing, duplicated, or
unexpected. The evidence validator separately requires all 15 canonical workflow IDs, the exact 33
automated and 15 manual manifest check mappings, canonical manifest digest
`534fb9f66ffadedbbcf06a53e46384ec95fbc380ec06fd9012b13cb25cb665a7`, and authoritative
manifest-validator digest `859d88f0e783bf879b1746c0b766bd5769baf8bbdbf98a93097f053496626468`.

The browser exercised actual Vite, FastAPI and worker endpoints against an exact empty disposable
PostgreSQL 16 database, private MinIO bucket and ClamAV. It created and persisted one domain, two
work items and two cases; proved cancel leaves no draft/history/storage state; checked invalid drag,
same-parent reorder and cross-parent move; and reloaded/deep-linked the resulting detail state.
Cycle coverage included scoped preview and pinned version, PLANNED execution gating, start,
WEB/AOS/iOS PASS/FAIL/BLOCKED plus failure notes, reload, complete/read-only execution, reopen with
pinned version intact, and exact-confirmation delete/cancel/restore. Case, issue and cycle permanent
delete buttons rejected empty and whitespace-altered confirmation, and the issue gate also rejected
lowercase; each accepted only the exact identifier/name. The proof canceled instead of permanently
deleting, then restored each row.

The execution flow created one linked issue and selected two deterministic text files in one chooser
event. Both private-proxy downloads matched the original bytes and both attachments were soft
deleted. The direct-upload client now drains the successful presigned PUT response before completing
the upload; this removed the previously observed Chrome `net::ERR_ABORTED` PUTs instead of exempting
them. Dashboard values were reconciled with product API aggregates before and after a hard reload,
and the Reports summary and selected-cycle row were reconciled with the same API data.

Only deterministic disposable local rows were seeded for the otherwise unreachable safety states.
The generation-failure detail proved terminal non-retryable finality, one attempt, append-only failure
timeline, no raw provider/model/error rendering, and a visible but uninvoked operator retry boundary.
Quality review used the production local generation/apply/import path to create one sealed,
credential-free `CANDIDATE / UNREVIEWED` record, then proved its reviewable safe detail, no reviewer
token in URL/local or session storage, and no release-certified claim.
`REVIEWED_NOT_RELEASE_CERTIFIED` remains an owner boundary rather than an invented PASS.

At 390×844, the run checked all 19 planned routes—lists, create forms, work-item/case/cycle/issue and
Slack detail, cycle execution, reporting and every safe-state surface—plus the generation-failure
detail, quality-review candidate detail and execution issue-create modal. Every surface included
document overflow checks and every primary button/link was inspected without filtering hidden
elements first, so hidden, zero-size, clipped or unreachable primary actions fail the run.

All request failures, including aborts, were tracked. The final evidence records 92 local GET/fetch
`NET_ERR_ABORTED` observations in 16 exact aggregate classes, all caused by the application's explicit
`AbortController` cleanup during deliberate route replacement; each subsequent route stabilized with
no HTTP/UI error. There were zero unresolved request failures, HTTP failures, console errors, page
errors, redirects or external requests. The final run recorded no teardown failure; should an exact
allowlisted browser-origin GET/fetch abort occur after teardown begins, it is the only shape eligible
for `HARNESS_CONTEXT_TEARDOWN`. An active object-storage PUT, unexpected method/resource type,
foreign/unknown port or origin, non-abort, phase/path mismatch or unresolved failure cannot pass.

## Strict evidence and cleanup contract

Both schema-version-1 evidence objects are verified against exact top-level and nested keys, types,
fixed values, lifecycle counters, route/mobile sets, artifact names and hashes, preservation records,
remaining gates, and their canonical seals before exclusive mode-0600 creation. Adversarial tests
cover omitted/extra/tampered workflow assertions, manifest IDs/mappings/digests, failed-request
dispositions, mobile coverage, lifecycle counts, mutation counts, cleanup counts/absence and evidence
receipt bytes. Symlinks, hardlinks, broad file modes and overwrite attempts remain rejected.

Final disposable database invariants were 78 public tables, one workspace, two folders, three work
items, seven active cases/nine case versions, one active cycle/one item, three execution attempts/nine
step results, one active issue, two soft-deleted attachments, 64 audit events, one generation failure
and failure event, one quality-review candidate, and one idempotency proof. The source inventory was
287 files with digest `1d6d1667d6655cc1a4ec39a94d057c6223e6a64c088576a7add33f8936bfb198`.

Cleanup removed the exact four containers, two volumes, one network, database, bucket, temp tree,
browser profile and three application processes, and proved all five loopback ports free. It
preserved 8 pre-existing containers, 37 images, 5 networks, 5 volumes, 20 databases and 60 prior
evidence files. The current `tc` write-counter digest remained
`960943cedf5fdd1fe70c99e69e1ac7a15af5905949463172a3dbf908363ccbfb`.

## Authoritative evidence

All four artifacts are regular non-symlink single-link mode-0600 files created without overwrite:

| Artifact | File SHA-256 | Canonical contract SHA-256 |
|---|---|---|
| [`pf-sf1-t11-wave10-92af97f8-local-physical-browser-20260907.json`](./evidence/pf-sf1-t11-wave10-92af97f8-local-physical-browser-20260907.json) | `b7d8ad10a2f88619479b7983302033f048fbe40fd70b19359d99b565f90d8946` | `360d379dc8b6d7e71ef1199f10c75ad9450cdd8cb66b0713a959ea2f0a3497f5` |
| [`pf-sf1-t11-wave10-92af97f8-cleanup-20260907.json`](./evidence/pf-sf1-t11-wave10-92af97f8-cleanup-20260907.json) | `fe648c99521e5588884d34620dd794e10b7cdcfc8adcf7419cd4ad896e4fe39b` | `4665de24977388921cf10106669f0db54fc68544971a7bc7ab0128842dc34bf2` |
| [`pf-sf1-t11-wave10-92af97f8-desktop-20260907.png`](./evidence/pf-sf1-t11-wave10-92af97f8-desktop-20260907.png) | `f605f211fc2dd1fff71a8bd08afe5b6902693a606d8d9783e6f3583ef8f2b5c5` | — |
| [`pf-sf1-t11-wave10-92af97f8-mobile-390-20260907.png`](./evidence/pf-sf1-t11-wave10-92af97f8-mobile-390-20260907.png) | `9aaa44413a596febc16ee7d0f383ad42f9819538aec6f1c7cb5c66f2b2cfc37d` | — |

Earlier Wave 10 PASS artifacts, including `r10d8f1c`, `r10g4c7f` and `r10k8a3d`, are immutable
historical evidence and are not authoritative for final code. `r10k8a3d` remains retained specifically
as the pre-`task_0c95d0badb67` RED source binding. Failed post-test attempts created no evidence and
their exact disposable resources were removed.

## Verification commands

| Check | Result |
|---|---|
| `pnpm check` | PASS — lint; mypy 149 files; TypeScript; 722 Python passed/1 environment-gated skip/109 deselected; schemas, ADRs, operations and OpenAPI |
| `pnpm --filter @tc/web test:unit` | PASS — 202 passed, 1 intentional environment-gated skip |
| `pnpm --filter @tc/web typecheck` | PASS |
| Focused API-client tests/typecheck | PASS — 23/23 and typecheck |
| `pnpm test:operations` | PASS — 44/44 and 14-section validator |
| Focused `node --test scripts/tests/phase-f-uat-browser-local.test.mjs` | PASS — 9/9 |
| `pnpm test:phase-f` | PASS — 223 passed, 1 environment-gated skip; Wave 10 adversarial Node suite 9/9 |
| `pnpm phase-f:uat:validate` | PASS — 15 workflows, 33 automated checks, 15 manual checks |
| Preview then physical-browser execute | PASS — `92af97f8`, ports 49713–49717, 15/15 workflows, 37/37 assertions, 136.554 s |
| Final evidence/cleanup and screenshot audit | PASS — seals, hashes, mode/link policy, exact absence/preservation; both screenshots inspected |

## Still NOT_RUN

No staging, production, Cloudflare R2, Slack, public tunnel, external model/provider, deployed
credential or user browser session was contacted. The deployed exact HTTPS-origin collector,
accountable QA-owner judgment for all 15 workflows, live external integrations, the
`REVIEWED_NOT_RELEASE_CERTIFIED` two-authority owner scenario, production observation and release
approval remain `NOT_RUN`. Therefore `PF-SF1-T11`, `PHASE_F`, `DEPLOYED_EXACT_HTTPS_ORIGIN`,
`HUMAN_QA_OWNER_CHECKS`, `EXTERNAL_INTEGRATIONS`, `PRODUCTION` and `RELEASE` all remain open; only
the local isolated physical-browser automated sub-gate is PASS.
