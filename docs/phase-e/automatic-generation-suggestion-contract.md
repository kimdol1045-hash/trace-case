# PE-SE2-T05 automatic generation suggestion contract

## Status and scope

This document fixes the pre-implementation, intentionally RED contract for `PE-SE2-T05`.
It composes the released T01-T03 automatic-generation chain, the released generation apply
ledger, and T04's CREATED-only generation lineage and irreversible human release.

T05 owns five effects only:

1. issue and seal an opaque target reference in a separate versioned provider input/output
   contract for an eligible ever-managed, now-released TestCase;
2. persist a later generated candidate as a suggestion for that exact target without changing
   the target case;
3. preserve an immutable before/after diff and complete source provenance;
4. let any team member deliberately apply the open suggestion as one new immutable
   TestCaseVersion; and
5. let any team member ignore it without mutating the TestCase ledger.

The protected invariant is:

```text
released generated case + later proven same-source candidate
    => immutable OPEN suggestion
    + zero TestCase/TestCaseVersion mutation

OPEN suggestion + deliberate, current-version apply
    => one new immutable version + append-only APPLIED resolution receipt
    while generation_managed remains false and the T04 release receipt is unchanged

OPEN suggestion + ignore
    => append-only IGNORED resolution receipt
    + zero TestCase mutation
```

T05 specifies the provider-facing suggestion exchange but does not execute a provider, select
credentials/model/cost, post a Slack reply, implement
T06 or later work, introduce approval roles, or automatically apply a suggestion. Existing
generation apply behavior and its `CREATED|REUSED` vocabulary remain unchanged. An ordinary
exact/semantic REUSED candidate is never reinterpreted as a suggestion.

## Existing authorities and composition seam

T05 introduces no alternate case, source, or generation identity:

| Concern | Authority |
|---|---|
| TestCase clock and deletion state | locked `test_cases.id`, `version`, `current_version_id`, `deleted_at` |
| Human semantic baseline | immutable current `test_case_versions`, ordered `test_steps`, and version tags |
| Ever-managed identity | T04 `generation_lineage_id`, CREATED outcome, baseline/latest generated version, and write-once release receipt |
| Generated candidate | one schema-valid candidate at `generation_jobs.result.cases[index]` |
| Source seal | generation job/root, context/input/result digests, Slack work item, and T03 assessment when automatic |
| Candidate-to-case authority | server-issued `suggestionTargetRef` backed by an immutable target row |
| Request replay | `api_idempotency_receipts` scoped by workspace, operation, key, and request hash |
| Actor provenance | server-built `AuditContext`; request JSON is never an authorization capability |

The creation seam is a separate internal `GenerationSuggestionService`, invoked only after a
suggestion-mode automatic job has a schema-valid `SUCCEEDED` result. T03 target capture, result
validation, and suggestion materialization use the fixed lock order workspace → Slack work item →
head assessment → target lineage/case → target/suggestion rows. They do not call or reuse
`GenerationApplicationService._materialize` and do not create a `generation_apply_outcomes` row.

Exact/semantic matching may help retrieve candidate cases before the server issues a target, but
it is never target authority. Candidate index, summary, exact key, similarity, evidence citation,
or a current/future `REUSED` receipt cannot select a released case for T05.

## Eligibility and same-lineage/source provenance

The trusted transaction may persist a suggestion only when all of the following hold:

1. the target case is active, not soft-deleted, `generation_managed = false`, has a non-null
   immutable T04 lineage and complete release receipt, and the release receipt still matches the
   append-only management ledger;
2. a T03 `REGENERATE` assessment is the current head for the same Slack work item and selects the
   automatic root that will receive the target. `INITIAL_GENERATE`, `SKIP`, and `REVIEW_REQUIRED`
   cannot issue a new target;
3. the target's establishing CREATED job, target case, Slack work item, assessment, selected root,
   and workspace all agree. A later provider attempt is either that selected root or its unique
   successful retry;
4. the target's current version/resource version exactly match the immutable baseline captured in
   the selected root's suggestion Input; the case is present in that Input's sealed existing-case
   snapshot;
5. the server persists one opaque, unguessable `suggestionTargetRef` mapped to assessment, selected
   root, case, lineage, CREATED outcome, and baseline before the provider boundary sees it;
6. the `SUCCEEDED` result uses `generation-suggestion/1.0.0`, echoes exactly that target ref once,
   and its context/Input/result/target seals validate; and
7. no suggestion already exists for the exact `(target, successful job, result candidate)`
   identity.

After capture, a newer same-root `SKIP` assessment is harmless only when the source fingerprint
and generation-contract digest remain identical. A newer `REGENERATE`, `REVIEW_REQUIRED`,
evidence retraction/deletion, selected-root replacement, or workspace/work-item divergence makes
the source seal stale and materialization fails closed.

Caller-supplied case ID, lineage ID, job/root/assessment ID, candidate index, digest, source type,
actor type, exact key, or similarity score proves none of these facts. The public API has no
create/recompute suggestion endpoint. A direct `SLACK_AI` case, a never-managed exact duplicate,
a copied lineage in JSON, a REUSED-only case, a different Slack work item, an unselected automatic
job, a manual job, an older generation, an unsupported result schema, or corrupt/legacy seal produces no
suggestion and no fallback case mutation.

## Server-issued target and provider exchange `generation-suggestion/1.0.0`

The normative JSON Schema is
`packages/contracts/generation/suggestion/1.0.0/schema.json`. It is a separate envelope around
the sealed base Generation Input `1.0.0`; it does not add target claims to the ordinary output or
mix whole-batch apply with suggestion materialization.

The input contains the selected generation job and T03 assessment, the full sealed base Input,
and one or more server-issued targets. Each target exposes an opaque `suggestionTargetRef`, target
case/version and lineage IDs for provider correlation, and a target digest. The durable mapping,
not any visible ID, is authoritative. The output contains only the schema version and
`{suggestionTargetRef, candidate}` pairs. Additional case, lineage, actor, source, match reason,
similarity, assessment, or job claims are forbidden.

The server rejects an unknown, duplicated, cross-job, consumed, stale, or unsealed ref. One result
cannot nominate the same ref twice or two refs for the same lineage. An ordinary generation
output `1.0.0`, even if exact/semantic planning later returns REUSED, produces no suggestion.
Suggestion-target jobs are not accepted by whole-batch apply-plan or
`POST /generation-jobs/{id}/apply`; those boundaries return typed 409
`GENERATION_SUGGESTION_CONFLICT` with
`SUGGESTION_JOB_NOT_WHOLE_BATCH_APPLICABLE` and write nothing.

`generation_suggestion_targets` is append-only and stores the opaque ref plus workspace and
Slack work item, assessment, selected root, target case/lineage/CREATED outcome, baseline case version/resource
clock, sealed target snapshot/digest, and creation time. It is unique by ref,
`(assessment_id, generation_lineage_id)`, and `(selected_root_job_id, test_case_id)`. Deferred
guards validate the complete workspace → Slack item → assessment → selected root → original
CREATED job → lineage → current baseline path. Retries reuse their root's targets rather than
issuing new mappings.

## Immutable suggestion and diff snapshot

`generation_suggestions` is an append-only proposal ledger. It has no mutable status column.
State is derived as `OPEN` when no resolution or newer superseding edge exists, `APPLIED` or
`IGNORED` from the unique append-only resolution, and `SUPERSEDED` from the unique newer edge.
Every row contains at least:

| Column | Contract |
|---|---|
| `id` | UUID primary key; receipt identity only. |
| `workspace_id` | Required workspace FK used for API/idempotency isolation. |
| `test_case_id` | Required FK to the preserved target, `RESTRICT`. |
| `generation_lineage_id` | The immutable T04 lineage, never client supplied. |
| `suggestion_target_id` | Unique immutable FK to the server-issued target mapping. |
| `source_generation_job_id` / `source_root_job_id` | Successful attempt and sealed root. |
| `source_change_assessment_id` | Required selecting T03 `REGENERATE` assessment. |
| `generated_case_index` / `exact_content_key` | Recomputed candidate identity. |
| `baseline_case_version_id` / `baseline_case_version` | Same-case immutable human baseline. |
| `baseline_case_resource_version` | Locked optimistic aggregate version at capture. |
| `context_digest` / `result_digest` | Lowercase SHA-256 seals revalidated at capture. |
| `diff_schema_version` | Exactly `generation-suggestion-diff/1.0.0`. |
| `baseline_snapshot` / `proposed_snapshot` | Canonical immutable semantic snapshots. |
| `baseline_digest` / `proposed_digest` / `diff_digest` | Lowercase RFC 8785 SHA-256 values. |
| `diff_snapshot` | Ordered immutable diff described below. |
| `supersedes_suggestion_id` | Nullable immutable self-FK to the immediately preceding OPEN suggestion. |
| `created_at` | Receipt time, excluded from all identity/digest calculations. |

The exact replay identity is unique on
`(suggestion_target_id, source_generation_job_id, generated_case_index)` and on
`(source_change_assessment_id, generation_lineage_id)`. Exact replay validates and returns the
existing row; it never recalculates a diff from live case data.

The baseline and proposed snapshots contain the complete suggestion-owned semantic shape:
`summary`, aggregate `precondition`, ordered steps, `expectedResultMode`, case expected result,
priority, and normalized sorted labels. The proposed snapshot also preserves the generated
reason, evidence IDs, open questions, inference flag, and trusted source provenance. Status,
assignee, work item, category, case key, deletion state, sort order, T04 management/release data,
timestamps, IDs, and optimistic versions are not proposed changes. Apply preserves those live
aggregate fields.

The proposed aggregate precondition is derived by the released generation-apply rule from the
ordered candidate step preconditions. Expected result mode remains `CASE`. No live Slack,
knowledge, case, category, or provider read is allowed after capture to reconstruct either side.

`diff_snapshot` has this canonical shape:

```json
{
  "schemaVersion": "generation-suggestion-diff/1.0.0",
  "changedFields": ["summary", "steps", "caseExpectedResult", "priority", "labels"],
  "changes": [
    {"path": "summary", "before": "human text", "after": "generated text"}
  ]
}
```

Changed fields and changes use this fixed order:

```text
summary
precondition
steps
expectedResultMode
caseExpectedResult
priority
labels
generationReason
evidenceNodeIds
openQuestions
inferenceOnly
```

Steps are compared and displayed as whole ordered arrays, including sequence, precondition,
action, and per-step expected result. Arrays that are semantic sets are NFKC/trim/casefold
normalized, deduplicated, and sorted by canonical JSON. `changedFields` is non-empty. An equal
proposal creates no suggestion and no case mutation. All three snapshots, schema version, source
columns, and digests reject `UPDATE` and `DELETE`; foreign keys use `RESTRICT`.

## Multiple suggestions and supersession

There is at most one OPEN suggestion per `(test_case_id, generation_lineage_id)`. A newer valid
generation may create a new immutable suggestion whose `supersedes_suggestion_id` points to the
previous OPEN row. The old row is not updated; its `SUPERSEDED` state and
`supersededBySuggestionId` are derived from the unique newer edge. The old diff and source remain
readable forever.

Ordering is by proven source generation version, then attempt number within the selected root;
wall-clock time and UUID order never select a winner. Exact replay of the same source returns the
same row. An out-of-order older source after a newer suggestion is a typed stale no-op and cannot
supersede the newer row. A newer source may supersede even when its proposed content digest is
equal to the older proposal because its immutable evidence/source seal differs. Already APPLIED,
IGNORED, or SUPERSEDED suggestions are never reopened or rewritten.

`generation_suggestion_resolutions` is append-only and contains at most one human decision per
suggestion. Supersession is not a resolution:

| Column | Contract |
|---|---|
| `id`, `workspace_id`, `suggestion_id` | Receipt ID and unique suggestion FK. |
| `decision` | `APPLIED` or `IGNORED`. |
| `actor` / `reason` | Server-built actor object and non-blank human reason. |
| `observed_case_resource_version` / `observed_case_version_id` | Locked case state seen by the decision. |
| `applied_case_version_id` | Required same-case version only for `APPLIED`. |
| `audit_id`, `receipt`, `decided_at` | Immutable audit link, exact API response, and transaction time. |

Shape and deferred cross-table guards prove workspace, case, lineage, source ordering, same-case
versions, and audit identity. `UPDATE` and `DELETE` are rejected. The generic API idempotency
ledger is also append-only.

## Apply API, stale checks, and atomicity

```http
GET  /api/test-cases/{caseId}/generation-suggestions
GET  /api/generation-suggestions/{suggestionId}
POST /api/generation-suggestions/{suggestionId}/apply
```

Apply requires matching header/body idempotency keys and:

```json
{
  "idempotencyKey": "suggestion-apply-...",
  "expectedCaseResourceVersion": 4,
  "expectedCaseVersionId": "case_version_...",
  "changeReason": "Apply the updated checkout behavior."
}
```

Ordering is strict:

1. resolve workspace-scoped idempotency; exact replay returns the original response without
   reading live source rows, and key reuse with another request is `409 IDEMPOTENCY_CONFLICT`;
2. lock the suggestion and target case;
3. require OPEN state, an unchanged valid diff/source seal, active non-deleted target, unchanged
   T04 lineage/release receipt, and exact suggestion baseline plus request resource/current-version
   pins;
4. append exactly one new TestCaseVersion from the immutable proposed snapshot while preserving
   status, owner, parent/category, case key, and `generation_managed = false`;
5. retain the original T04 lineage and release fields byte-for-byte and record trusted suggestion
   source metadata on the new version;
6. append the APPLIED resolution receipt, suggestion audit, normal case-version audit/outbox, and idempotency
   receipt; then commit together.

The apply response contains the immutable suggestion, resolution receipt, and updated TestCase.
Any failure rolls back version, steps, tags, current pointer/resource version, resolution, audits,
outbox, and idempotency receipt. A clean retry may commit once.

Apply and ignore are also one terminal decision under real concurrency. Two independent production
API clients released by one barrier may race with different idempotency keys, but PostgreSQL must
commit exactly one `200` winner and one `409 NOT_OPEN` loser, with exactly one resolution row. An
`APPLIED` winner advances the aggregate resource version and immutable case version by exactly one
and adds exactly one case audit/outbox pair; an `IGNORED` winner has zero case-ledger delta. Exact
winner replay returns the frozen response, and a later opposite decision cannot change either the
resolution or case cardinalities.

Stale checks precede semantic equality. A changed aggregate resource version, current version ID,
or current version number is `409 GENERATION_SUGGESTION_CONFLICT` with `CASE_VERSION_STALE`, even
if current content happens to equal the proposal. There is no automatic rebase. Other stable
reasons include `NOT_OPEN`, `SUPERSEDED`, `CASE_DELETED`, `LINEAGE_MISMATCH`,
`SOURCE_PROVENANCE_UNPROVEN`, and `DIFF_SEAL_INVALID`.

## Ignore API and no-case-mutation rule

```http
POST /api/generation-suggestions/{suggestionId}/ignore
```

Ignore requires matching header/body idempotency keys and a non-blank reason. It locks the
suggestion and reads the case only to capture observed IDs. It does not require the case to remain
at the suggestion baseline because it cannot change case state. In one transaction it appends an
IGNORED resolution receipt, suggestion audit/outbox, and idempotency receipt. It writes no TestCase,
TestCaseVersion, step, tag, generation management, case audit, or case outbox row.

Exact replay returns the original receipt. Reusing the key with another reason/suggestion is the
existing idempotency conflict. Applying or ignoring a terminal suggestion returns a stable
`NOT_OPEN`/`SUPERSEDED` conflict and never replaces its resolution.

## Delete and restore behavior

- Soft delete after suggestion capture leaves the immutable suggestion OPEN and does not decide
  it. Apply fails `CASE_DELETED` and cannot restore or clone the case. Ignore remains permitted
  and does not mutate the deleted case.
- Human deleted-case restore retains T04 released state. It does not rebase or reopen a
  suggestion. Because restore advances the aggregate clock, a pre-delete suggestion remains stale
  for apply even when its content version ID is unchanged.
- A later valid source captured after restore may create a new suggestion and supersede the stale
  OPEN one. The old diff remains queryable.
- Permanent deletion is rejected by the normal historical-reference conflict while target,
  suggestion, or resolution rows reference the case. T05 never cascades provenance.

## Public boundary and fail-closed errors

The public API permits list/detail/apply/ignore only. It has no suggestion create, update,
delete, force-apply, rebase, reopen, supersede, reclaim-management, or automatic-update route.
Request models forbid extra source/job/lineage/digest/diff/status/actor fields. `source.type =
WORKER|SYSTEM`, actor headers, guessed internal IDs, or a copied response cannot enter the trusted
materialization path.

Unknown and cross-workspace resources use the existing non-enumerating 404 shape. Invalid JSON
uses 422. Stale/terminal/provenance decisions use typed 409 responses and never fall through to
REUSED, CREATED, restore, or a case write. GET and conflict paths do not call a provider or mutate
any ledger.

## Web contract: keyboard and mobile

The TestCase detail page shows a Korean `생성 변경 제안` region for ever-managed released cases.
It lists newest first, shows `열림`, `적용됨`, `무시됨`, or `대체됨`, and labels the immutable
baseline and generated source versions. The default card summarizes changed fields; a native
button with `aria-expanded` and `aria-controls` reveals the complete before/after diff. Changed
steps preserve sequence and wrap long text rather than using horizontal scrolling.

Only OPEN suggestions show native `제안 반영` and `무시` buttons. Apply uses an accessible
confirmation dialog, names the case and version to be created, and never presents automation as
already committed. Ignore requires an explicit confirmation/reason. Dialog focus enters the
dialog, Escape closes without mutation, Tab remains trapped, and completion/status errors use
`role=status` or `role=alert`. Terminal suggestions have no active decision controls.

At a 390 CSS-pixel viewport cards and before/after columns stack vertically, no region causes
page-level horizontal overflow, long Korean/ID text wraps, and every decision/disclosure target
has at least 44 CSS pixels of hit height. Keyboard order is disclosure, apply, ignore; no hover,
color, drag, or pointer-only action is required. A stale apply shows a reload action and never
silently rebases or retries.

## Focused intentional RED verification

The focused gates are:

- `apps/api/tests/test_phase_e_generation_suggestions_postgresql.py` for PostgreSQL schema,
  released-case suggestion routing, provenance, immutable diff/resolution receipts, exact replay
  and conflicts, apply atomicity, a barrier-synchronized apply-versus-ignore race, ignore
  zero-case-mutation, delete/restore, supersession, and public spoof/fail-closed behavior;
- `packages/contracts/src/phase-e-generation-suggestion-contracts.test.ts` for the separate
  server-issued target/provider exchange and spoof rejection;
- `apps/api/tests/test_generation_suggestion_openapi.py` for the additive list/detail/apply/ignore
  shapes and absence of a public create endpoint; and
- `apps/web/src/generation-suggestion-workspace.test.tsx` for detail-page diff/actions, terminal
  states, keyboard semantics, and 390 px responsive safety.

Before production implementation these tests must fail because migration `0023` has no
target/suggestion/resolution tables, automatic `REGENERATE` jobs do not yet seal the suggestion
target exchange, the internal suggestion service and FastAPI/OpenAPI routes are absent, and the
TestCase detail page has no suggestion UI. Ordinary generation apply continuing to return
`CREATED|REUSED` is a required passing control, not a RED cause. A RED
failure caused by PostgreSQL unavailability, an import/collection error, syntax error, missing
dependency, or unrelated regression is not acceptable evidence.

### Verified RED baseline (2026-08-27)

- `pnpm --filter @tc/contracts exec vitest run src/phase-e-generation-suggestion-contracts.test.ts`
  passes `4/4`; the standalone target exchange accepts a valid sealed ref and rejects missing,
  duplicated, or provider-spoofed target authority.
- `pnpm test:schema`, focused Ruff/ESLint, Contracts/Web TypeScript checks, and Python compile all
  pass.
- With disposable PostgreSQL `tc_pe_se2_t05_test`, the focused PostgreSQL suite collects and runs:
  `8 failed, 1 passed`. The failures stop only at missing `generation_suggestion_targets` or the
  current ordinary `1.0.0` Input on an otherwise-valid selected T03 `REGENERATE` root. The passing
  test proves ordinary released-case apply remains `REUSED` and performs no case overwrite.
- The focused OpenAPI suite is `2 failed, 1 passed`: only the new routes/resources are absent,
  while the existing `CREATED|REUSED` discriminated union remains intact. The focused Web suite is
  intentionally `4 failed`: the suggestion region, dialogs, terminal states, and responsive CSS
  are absent.
- Existing controls remain green: generation OpenAPI `1/1`, TestCase detail Web `4/4`, and T04
  released exact reuse `1/1`. Thus RED is not caused by PostgreSQL availability, fixture
  grounding, syntax/collection, an unrelated case regression, or a weakened apply contract.

### Production implementation handoff

Implement in this dependency order without folding suggestions into ordinary apply:

1. add the next migration and ORM/repository mappings for immutable target, suggestion, and
   resolution ledgers, including deferred same-workspace/source/lineage guards and permanent-delete
   references;
2. during eligible T03 `REGENERATE` capture, lock and persist targets, seal the separate
   `generation-suggestion/1.0.0` Input, validate its output and reject whole-batch apply-plan/apply;
3. add `tc_backend.generation.suggestions.GenerationSuggestionService.materialize_succeeded_job`
   with pure canonical snapshot/diff construction, no-op suppression, replay validation, and
   immutable supersession edges;
4. add workspace-scoped list/detail plus atomic idempotent apply/ignore services and strict
   FastAPI resources; then generate the client; and
5. render the TestCase-detail workspace with keyboard-safe dialogs and the 390 px layout.

Provider execution remains injected/out of scope: the PostgreSQL RED fixture stores a
deterministic schema-valid result directly after proving the selected automatic root and its
sealed target mapping.

## Explicit exclusions

- Provider HTTP execution, credentials, model selection, pricing, quota, and result production.
- Slack reply delivery, retry, or deduplication (`PE-SE2-T06`).
- T06+ failure-management, eval, and full Slack-to-suggestion E2E work.
- Automatic apply, approval/role workflow, similarity-authorized overwrite, or management
  reacquisition.
- Mutation of cycle-pinned historical versions or reconstruction from live sources.
