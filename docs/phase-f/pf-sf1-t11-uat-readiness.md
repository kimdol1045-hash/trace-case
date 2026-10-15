# PF-SF1-T11 UAT readiness foundation

- Audit date: 2026-09-07 (Asia/Seoul)
- Scope: current Web routes, actual local API/worker, isolated PostgreSQL 16/MinIO/ClamAV, Phase A–E acceptance boundaries, and the Phase F roadmap
- Toolkits: [`uat_evidence.py`](../../scripts/phase-f/uat_evidence.py) and [`uat_browser_local.mjs`](../../scripts/phase-f/uat_browser_local.mjs)
- Manifest: [`phase-f-uat-manifest.json`](../../deploy/operations/phase-f-uat-manifest.json)
- Verdict: **LOCAL ISOLATED PHYSICAL-BROWSER AUTOMATED UAT PASS; OWNER UAT NOT RUN; PF-SF1-T11 OPEN**

No deployed origin was supplied, no external/live-service request was sent, and no current `tc`
product data or external service was mutated. Local mutations were confined to exact disposable
resources and removed afterward. The manifest starts all 15 owner checks at `NOT_RUN`.
The validator reports `complete=true` only when all 33 automated receipts and all 15 separately
recorded owner checks pass with matching local artifact hashes.

## Wave 10 local isolated browser sub-gate

Wave 10 adds a separate local-only sub-gate without weakening the deployed-origin collector. The
authoritative final-code `92af97f8` run drove the actual Vite UI through system-installed Google
Chrome against the actual FastAPI API and worker, with product network mocking disabled. It used
generated exact resource names, an empty disposable PostgreSQL 16 database, private MinIO, ClamAV,
generated non-default loopback ports, and a fresh private browser profile. All 15 manifest workflow
PASS results were derived from 37 explicit executed assertion receipts; a missing, duplicate or
unexpected workflow/assertion fails closed. All 15 accountable-owner checks remain separately
`NOT_RUN`.

The browser covered 1440×1000 and 390×844 viewports. It created and persisted one domain, two parent
work items and two test cases; exercised cancel/no-storage, invalid drag, reorder/move, reload,
deep-link, planned-cycle gating, pinned snapshot, all WEB/AOS/iOS status and note boundaries,
complete/read-only/reopen, and case/cycle/issue exact-confirmation delete/cancel/restore. It created an
execution-linked issue, selected two synthetic attachments in one chooser event, byte-matched both
private-proxy downloads, deleted both, reconciled Dashboard/Reports APIs and UI across hard reload,
and exercised a deterministic terminal generation failure plus a sealed local
`CANDIDATE`/`UNREVIEWED` quality-review record without an external provider. Mobile checks covered
all 19 planned routes, the failure and quality-review in-route details, and the execution issue
modal; primary controls were checked before any visibility filtering.

Every request failure, including `net::ERR_ABORTED`, is retained and adjudicated by exact phase,
origin class, method, path class and resource type. Independent RED `task_0c95d0badb67` showed that the
former producer/validator pair could accept an aborted MinIO PUT after harness teardown; its passing
fixture exercised that unsafe shape. Remediation `task_d1aff5905dae` now permits only exact
browser-port `NET_ERR_ABORTED` GET/fetch receipts on the explicit normalized route-abort allowlist,
with distinct workflow and harness-teardown dispositions. Object-storage/foreign origins, unsafe
method or resource type, non-abort, phase/path mismatch, unexpected fields and unresolved failures all
fail closed. The authoritative run recorded only allowlisted local GET/fetch AbortController cleanup
during deliberate route replacement, with zero unresolved failures, HTTP failures, console/page
errors, redirects or external requests.

Preview is credential-free and non-mutating:

```bash
pnpm phase-f:uat:browser:preview
```

Execution prints an exact generated confirmation; rerun the printed command with that confirmation.
The authoritative invocation and evidence hashes are recorded in the
[Wave 10 report](./sprint-f1-wave10-verification.md). This sub-gate does not satisfy the exact HTTPS
deployment, accountable-owner judgment, live Slack/R2/provider, production, or release gates.

## Audit findings

The production router in [`app.tsx`](../../apps/web/src/app.tsx) and its eager test mirror expose the
same board surfaces: Dashboard, Test Library and case creation/detail, work-item detail, Cycles and
execution, Issues, Reports, Slack Work Items and generation jobs/comparison, automatic-generation
failures, quality review, Knowledge, and Integrations. Static SPA routes can be checked without
inventing record identifiers. The local disposable harness now creates bounded synthetic records and
exercises dynamic work-item/case/cycle/issue/Slack detail routes and the meaningful mutation/lifecycle
boundaries; accountable-owner judgment against approved deployed UAT records remains separate.

The deployed-origin collector remains sequential and read-only across health/readiness, folder tree,
work items, active/deleted cases/cycles/issues, dashboard/report aggregates, Slack status, active
knowledge, object-storage status, generation jobs/failures/quality candidates and the audit ledger.
The separate local physical-browser harness uses only its exact disposable database/bucket/profile
for create, patch, move, sort, start, execute, upload, delete and restore; it does not invoke retry,
projection, reindex, Slack sync, provider, R2 or any external endpoint.

One important product boundary is explicit: the application is intentionally no-login inside an
internal/VPN network today. Soft-delete state, optimistic versioning, and exact confirmation gate
permanent deletion, but there is no application-level member/admin RBAC to test or claim. The owner
permission check therefore records both the network access boundary and the state/confirmation
rules. Role-based authorization remains a future organizational-scale item in the roadmap.

## Phase A–E evidence boundary

| Phase | Existing evidence reused as context | External/manual boundary retained by this UAT |
|---|---|---|
| A | PostgreSQL-backed library/execution, browser accessibility, private MinIO/R2 lifecycle, matched restore, and localhost direct PUT evidence | The local browser now proves one chooser event with two files plus both downloads/deletes; accountable-owner and deployed exact-origin CORS evidence remains `NOT_RUN` and the latter belongs to `PF-SF1-T15` |
| B | API-driven QA sample covers two cycles, per-step execution, issues, private attachments, reports, and idempotent replay | A QA owner has not yet completed the guide unaided, classified feedback, fixed blocker/major findings, and rerun the full UAT |
| C | Signed-event, append-only revision, allowlist/default-deny, retry/DEAD, and synthetic parser coverage exists | Actual workspace install, allowlisted automatic root/thread/edit/delete capture, live replay, and real anonymized sample remain open |
| D | Typed graph + pgvector, auxiliary LightRAG failure fallback, and fail-closed synthetic retrieval evaluation exist | Approved real Slack/QA labels, inactive-state evaluation, and a certified real-data retrieval result snapshot remain open |
| E | Manual/automatic generation contracts, deduplication, human-edit suggestion protection, retry history, and quality-review safe-state regressions exist | Provider endpoint/credential/rate/cost decision, live Slack/provider/model smoke, and agreed real-work-item quality threshold remain open |

These earlier automated and synthetic results inform the scenarios; they do not substitute for the
owner evidence required here. The roadmap remains unchecked for Phases A–F and PF-SF1-T11.

## Deterministic safety contract

1. The manifest is strict JSON: duplicate or unknown keys, duplicate IDs, unknown workflow links,
   unsafe query parameters, non-GET methods, and uncovered workflows fail validation.
2. The origin must be one canonical credential-free HTTPS origin such as
   `https://tc-stage.example.test`, with no path, query, fragment, user information, default port,
   uppercase hostname, or trailing slash.
3. Requests are sequential same-origin `GET`s with a 10-second timeout, `Accept-Encoding: identity`,
   a 2 MiB response cap, standard TLS verification, no cookies, and an opener that denies every
   redirect. Thus optional bearer authority cannot be forwarded to another origin.
4. An optional bearer value is accepted only through a `TC_PHASE_F_*` environment variable. The
   CLI, error receipts, and evidence never include the variable name's value, request headers,
   response bodies, `Location`, or network-library exception text.
5. A passing automated receipt contains only check ID, method, manifest path, status, normalized
   content type, byte count, and body SHA-256. Expected status, security headers, JSON keys/types and
   fixed safe values, or the SPA root marker must all match.
6. Collection output uses exclusive creation with mode `0600` under
   `docs/phase-f/evidence`; overwriting an existing file is refused.
7. Owner checks are a different array and begin empty as `NOT_RUN`. A manual PASS needs an owner,
   UTC timestamp, every required artifact kind, an existing non-symlink file under the evidence
   root, and a matching lowercase SHA-256 digest.
8. Any missing manual check keeps `manual.status=NOT_RUN`, `pfSf1T11Status=NOT_RUN`, and
   `complete=false`, even when every automated request passes. Automated or manual failures fail
   the relevant section; only all-pass evidence completes the task.

## Board workflow coverage

The 15 manifest workflows cover the requested acceptance surface:

- dashboard KPIs and hard reload;
- Test Library domain → work item → case navigation and persistence;
- cancel/no-drop, same-parent reorder, cross-work-item drag/move, reload, and audit evidence;
- case create cancel/history behavior, save, edit, versioning, and duplicate prevention;
- cycle scope preview, pinned snapshot, create/start/complete/reopen and state gating;
- step × WEB/AOS/iOS results, FAIL/BLOCKED notes, reload, and aggregation;
- issue-from-attempt linkage plus one-event, two-file physical-browser upload/download/delete;
- active/deleted list separation, restore, permanent-delete eligibility/confirmation, and the actual
  VPN/no-login permission boundary;
- report/dashboard/cycle reconciliation;
- Slack root/thread/revisions, allowlist exclusion, and source safety;
- knowledge citations/status and inactive-grounding exclusion;
- Slack/R2 integration state without credential rendering;
- automatic-generation failure finality/actionability/history and secret-safe UI;
- quality-review `CANDIDATE`, `UNREVIEWED`/`REVIEWED_NOT_RELEASE_CERTIFIED`, memory-only reviewer
  token, two-authority, and missing-attestation safe states;
- exact 390 CSS-pixel owner inspection of navigation, lists/details/forms, execution, dialogs,
  actions, result cells, and safe-state banners without hover-only access.

## Operator procedure

Run from the repository root. Validate the tracked definition first:

```bash
node scripts/python.mjs scripts/phase-f/uat_evidence.py validate-manifest
```

Choose the exact approved HTTPS origin. If the edge requires a bearer token, load it from the
approved secret manager into a `TC_PHASE_F_*` environment variable without enabling shell tracing.
Do not put the value in argv, a URL, a ticket, or an evidence file.

```bash
node scripts/python.mjs scripts/phase-f/uat_evidence.py collect \
  --origin https://tc-stage.example.test \
  --output docs/phase-f/evidence/pf-sf1-t11-20260901.json
```

Bearer-protected example (the value is never reported):

```bash
node scripts/python.mjs scripts/phase-f/uat_evidence.py collect \
  --origin https://tc-stage.example.test \
  --bearer-token-env TC_PHASE_F_UAT_BEARER \
  --output docs/phase-f/evidence/pf-sf1-t11-20260901-bearer.json
```

The output name must not already exist. A successful collection can still state
`pfSf1T11Status=NOT_RUN`; that is the expected foundation state before owner work.

Owners perform only approved disposable/staging UAT mutations, save redacted artifacts under the
evidence directory, calculate each SHA-256, and replace the matching manual entry with this exact
shape:

```json
{
  "artifacts": [
    {
      "kind": "OWNER_NOTE",
      "path": "docs/phase-f/evidence/owner-library-note.txt",
      "sha256": "<64 lowercase hex characters>"
    }
  ],
  "checkId": "owner-library",
  "owner": "<accountable owner>",
  "performedAt": "2026-09-01T09:00:00Z",
  "status": "PASS"
}
```

The example is incomplete for `owner-library`, which also requires JSON and SCREENSHOT artifacts;
the validator will reject it until all required kinds exist and match. A failed manual attempt uses
`FAIL` and at least an `OWNER_NOTE` artifact. An unperformed check must retain the original empty
`NOT_RUN` entry.

Validate against the same exact origin:

```bash
node scripts/python.mjs scripts/phase-f/uat_evidence.py validate-evidence \
  --origin https://tc-stage.example.test \
  --evidence docs/phase-f/evidence/pf-sf1-t11-20260901.json
```

Exit `0` means complete and all-pass, exit `3` means structurally valid but incomplete/failed UAT,
and exit `2` means unsafe or invalid input/evidence. The validation report, not an edited roadmap
checkbox, is the release-gate verdict.

## Verification performed for this foundation

| Check | Result |
|---|---:|
| Manifest validation | PASS: 15 workflows, 33 automated checks, 15 manual checks |
| Local browser assertion receipts | PASS: 15 workflows derived from 37 exact named assertions; omitted/extra receipts fail closed |
| Local evidence/cleanup validator tests | PASS: 9/9, including producer and sealed-validator abort adversaries, omitted/extra/tampered schema fields, exact manifest mappings/digests, mobile coverage, lifecycle counts and file safety |
| Fresh final-code physical-browser run | PASS: `92af97f8`, exact cleanup and current-`tc` preservation; `r10k8a3d` retained as historical pre-RED evidence |
| Full repository verification | PASS: `pnpm check`, Web unit/typecheck, operations, Phase F and manifest validation |

## Remaining external UAT and release gates

PF-SF1-T11 remains open until an approved exact HTTPS deployment exists, all automated receipts
pass there, and accountable owners perform and attest every manual scenario. Wave 10 proves the
local automated one-event two-file flow and automated 390px layout assertions only. QA-owner visual
judgment, actual Slack allowlist/revision flow, approved real knowledge sample, provider-backed
automatic-generation failure, two-reviewer quality workflow, and deployed-origin inspection remain
`NOT_RUN` in repository evidence.

This task also does not close PF-SF1-T01–T03, T05–T10, or T12–T15: VPN/firewall/TLS, live Slack,
secret-store rotation, alert delivery, production-like load, separate backup/restore and R2
recovery, full knowledge reindex/fallback, staging migration round trip, runbook/limitations,
production deployment/observation, and exact-origin R2 CORS physical-browser proof retain their
own external gates. No PF-SF1-T11 completion or production release claim is made by this document.
