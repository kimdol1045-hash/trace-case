# Sprint F1 Wave 10 — second fresh independent adversarial verification

- Date: 2026-09-07 (Asia/Seoul)
- Scope: PF-SF1-T11 Wave 10 after remediation `task_d1aff5905dae`
- Fresh run: `23e3e7c7`
- Verdict: **GREEN for the local isolated physical-browser automated UAT sub-gate only**
- Higher-gate verdict: **PF-SF1-T11 and Phase F remain open / NOT_RUN**

## Independent RED re-audit

I treated prior reports and prior evidence as non-authoritative. Static inspection and direct execution
confirmed that the `requestfailed` producer and the strict rehearsal validator independently accept a
safe failed-request receipt only when all of these are exact: the browser-origin port, `NET_ERR_ABORTED`,
`GET`, `fetch`, a normalized path in the explicit route-abort allowlist, and the phase/disposition pair
`WORKFLOW` / `CLIENT_ABORT_CONTROLLER_ROUTE_TEARDOWN` or `HARNESS_TEARDOWN` /
`HARNESS_CONTEXT_TEARDOWN`.

The focused suite passed 9/9. I also ran a fresh mutation matrix directly against the producer and a
resealed copy of the new run's evidence:

| Adversary | Producer | Strict validator |
|---|---|---|
| Exact Web-port teardown `NET_ERR_ABORTED` `GET`/`fetch` `/api/issues` | `HARNESS_CONTEXT_TEARDOWN` | PASS |
| Exact Web-port workflow equivalent | `CLIENT_ABORT_CONTROLLER_ROUTE_TEARDOWN` | PASS |
| Teardown MinIO object `PUT` | `UNRESOLVED` | REJECTED |
| Foreign loopback port | `UNRESOLVED` | REJECTED |
| Unknown/external origin class | `UNRESOLVED` | REJECTED |
| Wrong method | `UNRESOLVED` | REJECTED |
| `document` resource | `UNRESOLVED` | REJECTED |
| Non-abort error | `UNRESOLVED` | REJECTED |
| Non-normalized/unlisted path | `UNRESOLVED` | REJECTED |
| Unknown phase | `UNRESOLVED` | REJECTED |
| Phase/disposition mismatch | n/a | REJECTED |
| Extra failed-request field | n/a | REJECTED |
| Non-empty unresolved entries | n/a | REJECTED |

The new physical-browser receipt contains 92 failed requests in 16 aggregate classes. Every one is an
exact `IPV4_LOOPBACK:53279` workflow `NET_ERR_ABORTED` `GET`/`fetch` on an allowlisted normalized
path with `CLIENT_ABORT_CONTROLLER_ROUTE_TEARDOWN`; unresolved failures, HTTP failures, console
errors, page errors, redirects, and external requests are all zero. This run recorded no harness
teardown request failure; the exact safe teardown shape was separately proved by the direct validator
test above.

## Fresh preview-first physical-browser result

Preview generated run ID `23e3e7c7` and five distinct free ports: PostgreSQL `53275`, MinIO `53276`,
ClamAV `53277`, FastAPI `53278`, and Vite/Web `53279`. I executed the preview's exact confirmation
`PF-SF1-T11-wave10:23e3e7c7:LOCAL-ISOLATED`.

The 176.057-second run used system-installed Google Chrome with a fresh disposable profile, actual
Vite, FastAPI and worker processes, disposable PostgreSQL 16, private MinIO and ClamAV. Product
network mocking was false, service workers were blocked, and no credential, external service, public
tunnel, staging, production, or current user browser session was used.

The sealed result derives 15/15 workflow PASS from exactly 37 executed named assertions and binds the
exact 15 workflow IDs, 33 automated mappings, and 15 manual mappings. The canonical manifest digest is
`534fb9f66ffadedbbcf06a53e46384ec95fbc380ec06fd9012b13cb25cb665a7`; the bound validator digest is
`859d88f0e783bf879b1746c0b766bd5769baf8bbdbf98a93097f053496626468`.

Re-audited coverage passed:

- Dashboard API/KPI hard-reload reconciliation and Reports API/UI selected-cycle reconciliation.
- Cycle preview/pinned version, PLANNED gating, WEB/AOS/iOS PASS/FAIL/BLOCKED results and notes,
  complete/read-only, reopen, delete/cancel/restore lifecycle.
- Exact permanent-delete confirmation boundaries for case, issue, and cycle, followed by restoration.
- Two attachments selected in one event; both private-proxy downloads byte-matched and both rows were
  soft deleted. The API client drains the successful presigned PUT response via `arrayBuffer()` before
  completion, and the focused 23/23 API-client tests passed.
- One terminal non-retryable generation failure with one attempt and append-only failure event, no raw
  provider/model/error rendering, and an uninvoked retry boundary.
- One non-synthetic production-path generated/imported, sealed `CANDIDATE / UNREVIEWED` quality
  candidate: reviewable, materialization complete, privacy clean, reviewer token absent from URL and
  browser storage, and no release certification claim.
- All 19 planned mobile routes plus generation-failure detail, quality-candidate detail, and the
  execution issue-create modal at 390x844. Document overflow and every primary control were checked
  before visibility filtering, including hidden, zero-size, and horizontal clipping failures.

The resulting database invariants include 78 public tables, one workspace, two folders, three work
items, seven active cases/nine case versions, one restored cycle/item, three execution attempts/nine
step results, one active issue, two deleted attachments, 64 audit events, one generation failure/event,
one quality candidate, and one idempotency proof.

## Verification commands

| Check | Result |
|---|---|
| `node --test scripts/tests/phase-f-uat-browser-local.test.mjs` | PASS, 9/9 |
| Direct producer plus resealed-validator adversarial matrix | PASS, exact safe receipts accepted and every requested unsafe shape rejected |
| `pnpm --filter @tc/api-client test:unit` and typecheck | PASS, 23/23 and typecheck |
| `pnpm --filter @tc/web test:unit` and typecheck | PASS, 202 passed / 1 intentional environment-gated skip and typecheck |
| `pnpm check` | PASS, including lint, mypy 149 files, 722 Python passed / 1 gated skip / 109 deselected, schemas, ADRs, operations, and OpenAPI |
| `pnpm test:phase-f` | PASS, 223 passed / 1 gated skip plus Wave 10 Node 9/9 |
| `pnpm phase-f:uat:validate` | PASS, 15 workflows / 33 automated / 15 manual |
| Preview then exact physical-browser execution | PASS, run `23e3e7c7`, 15 workflows / 37 assertions |
| Independent schema, seal, hash, file, source, cleanup, and preservation audit | PASS |

## Fresh immutable evidence quartet

All four files are regular non-symlink, single-link, mode-0600 files. The JSON files passed exact-key
schema-v1 validation, canonical-seal verification, manifest/validator binding, and cross-file rehearsal
receipt binding. Both screenshots were visually inspected: the 1440x1000 Reports view is complete and
internally consistent, and the 390x844 execution view has reachable, unclipped platform controls and
issue creation action.

| Artifact | Bytes | File SHA-256 | Canonical SHA-256 |
|---|---:|---|---|
| `pf-sf1-t11-wave10-23e3e7c7-local-physical-browser-20260907.json` | 16,730 | `d942ae8b9907dfdc1d8b5f1a3eae745b590e3b65dc21873fbfeda6095bd22959` | `4143b712be0a60d69b8e7dd7f508ddb7a15c4996e8b6ba7d1841c0ff2b188f8f` |
| `pf-sf1-t11-wave10-23e3e7c7-cleanup-20260907.json` | 2,137 | `36844286dd3b7d7526927ef6007fe7e9613e7e2e40a29302e3146cae65392f91` | `16bbb79d63445c108bb4e3613e52f203d3b9bc6dc66f412e486beb4d1e9a69ca` |
| `pf-sf1-t11-wave10-23e3e7c7-desktop-20260907.png` | 132,276 | `4b744468aa2921a8ff050ae768abadc77680331126db952b9015d62c5897ab93` | — |
| `pf-sf1-t11-wave10-23e3e7c7-mobile-390-20260907.png` | 34,886 | `39280048ae11b99be5dcdb2cc0e74ae823b4bded6983a3b82d629bda16dab892` | — |

Source binding independently recomputed as 287 files with digest
`1d6d1667d6655cc1a4ec39a94d057c6223e6a64c088576a7add33f8936bfb198`.

## Cleanup and preservation

Independent post-run checks proved the exact four containers, two volumes, one network, database,
bucket, temp tree/browser profile, three application processes, and all five ports absent/free. There
were zero matching namespace processes.

Pre-existing inventory still matches the sealed baseline exactly: 8 containers, 37 images, 5
networks, 5 volumes, 20 databases, and 64 historical evidence files. The current `tc` write-counter
digest remains `960943cedf5fdd1fe70c99e69e1ac7a15af5905949463172a3dbf908363ccbfb`.
No product, test, manifest, validator, runbook, roadmap, or historical evidence file was modified.

## Gates intentionally left NOT_RUN

The deployed exact HTTPS-origin check, all 15 accountable human QA-owner checks, live Slack/R2/model
integration, staging, production, and release remain `NOT_RUN`. Therefore PF-SF1-T11 and Phase F stay
open despite the GREEN local isolated automated sub-gate.
