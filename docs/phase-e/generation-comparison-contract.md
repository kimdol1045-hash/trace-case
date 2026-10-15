# PE-SE1-T11 generation comparison read-model contract

## Status and endpoint

This document fixes the provider-neutral `1.0.0` comparison/read-model boundary. The backend
read service, API operation, generated client, and responsive Web route implement this boundary. The canonical JSON Schema is
`packages/contracts/generation/comparison/1.0.0/schema.json`, with schema ID
`https://qa-tool.local/schemas/generation/comparison/1.0.0/schema.json`.

The single authoritative operation is:

```text
GET /api/generation-jobs/{jobId}/comparison
```

It returns `GetGenerationComparisonResponse`. There is no query mode, request body, provider
selection, or mobile-specific response. Desktop and mobile clients consume the same ordered
read model. A missing job uses the existing `RESOURCE_NOT_FOUND` envelope; incomplete historical
source data is a successful comparison response with explicit `MISSING_SOURCE` state, not a
lookup of current data.

The operation is historical and read-only. Its `policy` object is not advisory metadata: every
response fixes `sourceBasis: SEALED_GENERATION_JOB`, `readOnly: true`, and all of
`liveSourceSubstitution`, `generationInputReconstructed`, `providerCallAttempted`, and
`mutationPerformed` to `false`.

## Response topology and order

The response has one job identity/provenance header and six independently renderable sections:

| Section | Purpose | Authoritative order |
|---|---|---|
| `slackSource` | Slack work-item identity, the four structured fields, and exact root/reply revision evidence | fields in the fixed contract order; revisions in sealed root-first/message/revision chronology |
| `activeKnowledge` | requirements, decisions, and constraints that were `ACTIVE` with `validTo: null` at the job seal | first sealed context rank for each node |
| `existingCases` | historical existing cases with immutable case/version identity and any body actually sealed | first sealed context rank for each case node |
| `supplementalEvidence` | referenced risk executions, open issues, explicit sources, and otherwise classified sealed evidence | first sealed context rank |
| `generation` | stored category selection and every generated candidate, including exact `generationReason`, `openQuestions`, `inferenceOnly`, and `evidenceNodeIds` | provider result array order, with contiguous zero-based `index` |
| `referenceEdges` | explicit candidate-to-evidence edges | candidate order, then each candidate's original `evidenceNodeIds` order |
| `apply` plus each candidate's `apply` | committed receipt header and created/reused candidate linkage, when it exists | receipt/outcome generated-case order |

Servers do not sort these arrays for presentation. A narrow mobile card, desktop side-by-side view,
or accessibility tree can regroup visually, but its keys and order come from this response.

`SourceReference` is the common historical citation. It copies one `generation_contexts` row's
context item ID/rank, knowledge evidence ID, node/edge identity, source kind/type/entity/revision,
content hash, and permalink state. It never describes the source entity's current revision.

## Exact historical mapping

The read model is a projection, not a new snapshot and not the T12 generation input. The backend
implementation must use the following mapping and may only normalize identifier prefixes and the
named availability envelopes.

| Comparison field | Persisted source | Mapping rule |
|---|---|---|
| `job.id`, `slackWorkItemId`, `status`, `lineage`, timestamps | `generation_jobs` | Same values as the T09 job resource; omit provider/model columns. |
| `job.context` | `generation_jobs` seal/provenance columns | Same exact T09 context resource. `digest` is the sealed digest, not recomputed from live rows. |
| every `SourceReference` | one ordered `generation_contexts` row | Copy the stored row and encode IDs. `permalink: EMPTY` means the stored row has `NULL`; do not fetch one. |
| Slack identity | the sealed Slack `WORK_ITEM` node snapshot and its context sources | Read `workspaceId`, `channelId`, `messageTs`, `threadTs`, and permalink only when those exact values were sealed. |
| Slack structured fields | sealed root snapshot properties or a future sealed structured-source projection | Emit all four field names exactly once. A sealed `null` is `EMPTY`; absent historical material is `MISSING_SOURCE`. |
| Slack revision entries | exact structured revision data sealed for this job plus matching context source rows | Retain immutable revision ID, logical message ID, Slack timestamps, root/reply, create/edit/delete, author/body/permalink, recorded time, and citations. A delete has `body: EMPTY`. |
| `activeKnowledge.items[]` | sealed node snapshots whose `nodeType` is `REQUIREMENT`, `DECISION`, or `CONSTRAINT` | Use snapshot status/content/validity and group every matching context source row. Never re-check or replace content from current `knowledge_nodes`. |
| existing case identity | sealed `TEST_CASE` node snapshot | `testCaseId` is the sealed node entity ID; `testCaseVersionId` is the sealed pinned/current-version ID. This pair is immutable even if the aggregate later changes. |
| existing case content | full case body only when it was sealed into the job snapshot | Project summary/precondition/steps/expected-result mode/result/priority/labels verbatim. If the seal contains only identity or summary, return `content: MISSING_SOURCE`; never read the case's current version. |
| supplemental evidence | remaining sealed node snapshots and context sources | Classify as risk execution, open issue, explicit source, or other sealed evidence and expose the sealed object or an unavailable state. |
| `generation.categoryCandidate` and candidates | `generation_jobs.result` | Copy the validated stored result without editing content. Candidate `index` is its array offset. Compute `resultDigest` with the T10 canonical SHA-256 rule. |
| `referenceEdges` | stored candidate `evidenceNodeIds` joined to sealed context node IDs | Emit one edge per evidence entry. A resolved edge includes every matching context row in rank order. No fuzzy match or current graph traversal is allowed. |
| top-level `apply.receipt` | immutable `generation_applies.receipt` | Split the receipt into its header/parent/counts while preserving job/context/result/placement digests, audit ID, and applied time. |
| candidate `apply.outcome` | receipt `cases[generatedCaseIndex]` (validated against stored outcomes) | `CREATED` points to the created case/version; `REUSED` points to the preserved existing case/version. The outcome index and candidate index must be equal. |

The response must fail internal validation rather than silently reorder candidates, drop an
`evidenceNodeId`, attach an outcome to another index, or claim `NOT_APPLIED` when a committed
receipt exists. `referenceEdges` and apply linkage are server-derived joins over immutable rows;
they are not model output.

An unresolved evidence ID uses `target.state: MISSING_SOURCE`,
`target.sourceKind: UNKNOWN`, no context references, and reason `UNRESOLVED_REFERENCE`.
`OTHER_SEALED` is reserved for a resolved sealed source whose type has no narrower classification;
it must not disguise a failed join.

## Backend/API implementation evidence

The backend repository now exposes one read-only `load_comparison` projection over the verified
sealed context plus the optional apply row and its outcomes ordered by `generated_case_index`.
The FastAPI serializer maps only those rows and `generation_jobs.result`; it does not join Slack,
knowledge, case, audit, or outbox current-state tables. The route is published as
`getGenerationJobComparison` and returns the same `GenerationComparisonResponse` model for every
client form factor.

Focused disposable PostgreSQL/API coverage proves all of the following in one historical flow:

- exact sealed Slack identity, fixed structured-field order/content, root revision body/author/link,
  and context citations without consulting current Slack rows;
- sealed ACTIVE decision and constraint content, existing case/version identity with its sealed full
  body, and candidate/evidence order across Slack, knowledge, and case references;
- `NOT_APPLIED` before apply and exact `REUSED`/`CREATED` receipt outcomes after apply;
- identical comparison JSON after live Slack, current decision/constraint projection, and current
  case version changes;
- unchanged generation, ledger, apply, audit, outbox, Slack, and knowledge row counts across GETs;
- no generation-provider calls, non-succeeded empty semantics, `404` for an unknown job, and
  stable `409 GENERATION_STORED_RESULT_INVALID` for malformed stored result data.

The focused generation repository/API PostgreSQL matrix passes 9 tests; the comparison contract
passes 10 tests, the focused Web comparison/service suite passes 19 tests, and strict schema,
Ruff, mypy, TypeScript, and OpenAPI drift checks pass. The generated
`packages/api-client/openapi.json` and `packages/api-client/src/schema.gen.ts` artifacts publish the
comparison models and endpoint consumed by the Web service.

## Empty, redacted, and missing-source behavior

Historical text uses a required tagged value:

| `state` | `value` | `reason` | Meaning |
|---|---|---|---|
| `PRESENT` | nonblank exact sealed text | `null` | The displayed text was sealed for this job. |
| `EMPTY` | `null` | `SEALED_EMPTY` | The source was sealed and its value was explicitly empty/null. |
| `REDACTED` | `null` | `SENSITIVE_DATA` or `AUTHORIZATION_POLICY` | The historical value exists but this response must not disclose it. |
| `MISSING_SOURCE` | `null` | `NOT_SEALED`, `SOURCE_INCOMPLETE`, `LEGACY_CONTEXT`, or `UNRESOLVED_REFERENCE` | The job cannot prove the display value from its seal. |

Collections similarly require `availability`, `items`, and `reason`:

- `AVAILABLE` has at least one item and `reason: null`.
- `EMPTY` has `items: []` and proves an authoritative empty set.
- `REDACTED` has `items: []` and a redaction reason.
- `MISSING_SOURCE` has `items: []` and a historical-source reason.

Omission, `undefined`, an untagged `null`, a placeholder string, and a live fallback are invalid.
Per-field states allow a Slack item to show identity while one structured field is unavailable.
Existing case identity remains visible even if its historical body is missing or redacted.

For `QUEUED`, `RUNNING`, or `FAILED` jobs, `generation` is `EMPTY` with
`JOB_NOT_SUCCEEDED`, candidates are `[]`, and `referenceEdges` is `EMPTY` with
`NO_GENERATED_CANDIDATES`. For `SUCCEEDED`, all stored candidates must be returned unless the
whole result is explicitly redacted or the persisted result is historically missing; the latter
states are visibly different from an empty candidate set.

When no receipt exists, top-level and per-candidate apply states are `NOT_APPLIED`. When one
committed receipt exists, top-level state is `APPLIED` and every candidate is `APPLIED` with the
receipt ID and matching created/reused outcome. There is no partially applied state because T10
commits or rolls back the whole batch.

## Current storage audit and compatibility consequence

The inspected T09/T10 storage already supports these parts without a live lookup:

- job identity, lifecycle, lineage, seal provenance, and stored result;
- ordered `generation_contexts` metadata, node/edge snapshots, selection reasons, hashes, and
  source revision identity;
- ACTIVE knowledge type/status/content/validity from sealed node snapshots;
- existing case aggregate ID and pinned version ID where the TEST_CASE projection includes
  `currentVersionId`;
- immutable apply receipt and created/reused outcomes.

The current seal does **not** reliably retain a fully structured comparison view for every row:

- the Slack root snapshot stores a joined `properties.content` bundle and counts, while logical
  message ID, author, and a structured revision body are not all retained together;
- the current root projection does not consistently seal the four parsed Slack field values or
  their per-field revision mapping;
- current TEST_CASE node snapshots retain entity/current-version identity and summary, not the
  full version body, key, steps, expected result, priority, and labels.

Therefore the comparison API returns `MISSING_SOURCE` for those unavailable historical values on
existing or incompletely sealed jobs. Reading `slack_work_items`, `slack_message_revisions`,
`parse_result`, `knowledge_nodes`, `test_cases`, or `test_case_versions` as a substitute would show
present-day data and violates this contract. The integration fixture also proves the full-fidelity
path when structured Slack revisions/fields and pinned case display content were sealed: those
values survive later live-source changes exactly. Extending every production ingestion projection
to seal that richer optional display material remains a source-completeness improvement; it is not
T12 input reconstruction, must not rewrite old jobs, and does not change T11's stable read semantics.

## Explicit non-goals and residual live checks

This comparison implementation does not:

- change migrations or historical generation rows;
- invoke or configure a generation/embedding provider;
- apply, retry, execute, create, update, or otherwise mutate a job or QA ledger;
- rebuild a generation input payload from the seal (`PE-SE1-T12`).

The implemented API loads the job, ordered context, optional committed apply, and stored
receipt/outcomes without a write transaction, validates the response against this contract, and
publishes the single GET operation in OpenAPI. Focused PostgreSQL coverage uses provider and row-
count tripwires, changes live Slack/knowledge/case sources after sealing, verifies historical
response stability, checks candidate/reference/outcome index order, and exercises existing-job
`MISSING_SOURCE` behavior.

The Web implementation now provides four ordered responsive sections for Slack source, ACTIVE
knowledge, pinned existing case identity/body, and candidate content/reason/questions/inference.
Evidence chips are real buttons with `aria-expanded`/`aria-controls`; their disclosure links navigate
to the corresponding sealed source in the page, and applied candidates link to created/reused case
details. Unit coverage distinguishes empty, redacted, missing, and deleted history as well as
QUEUED/RUNNING/FAILED recovery. A production build connected to disposable PostgreSQL was verified
in a physical browser at 1280×900, 768×1024, and 390×844 with five ordered candidates, all four
sections, no horizontal overflow, and 44 px evidence/apply targets at mobile width.

No new ADR is warranted: this endpoint applies the already accepted immutable snapshot,
PostgreSQL-system-of-record, and provider-boundary decisions without introducing a new storage or
execution architecture. The remaining live checks are an actual signed Slack ingestion sample that
confirms which optional structured display fields the production sealer retains, and T01's approved
provider endpoint/credentials/quota/context-limit exercise. Neither check permits comparison GET to
call the provider or consult live source tables.

`PE-SE1-T12` remains separate and is now defined by the
[generation input reconstruction contract](./generation-reconstruction-contract.md). It requires a
complete immutable input snapshot and input-specific canonical digest for new jobs; the T11
comparison response is not that input, does not expose the generation policy/category candidate set
as an outbound request, cannot backfill legacy jobs, and does not authorize a provider call.

## Contract validation

Focused checks:

```text
pnpm --filter @tc/contracts exec vitest run src/phase-e-generation-comparison-contracts.test.ts
pnpm --filter @tc/contracts typecheck
pnpm test:schema
pnpm exec eslint packages/contracts --max-warnings 0
pnpm --filter @tc/web exec vitest run \
  src/generation-comparison-components.test.tsx src/generation-job-service.test.ts
pnpm --filter @tc/web build
TC_TEST_DATABASE_URL=... node scripts/python.mjs -m pytest \
  apps/api/tests/test_generation_api_postgresql.py \
  apps/api/tests/test_generation_apply_api_postgresql.py \
  backend/tests/repository/test_generation_repository_postgresql.py
node scripts/python.mjs -m mypy backend/src apps/api/src
node scripts/python.mjs -m ruff check backend apps
pnpm openapi:check
```

The comparison tests compile every definition in strict Draft 2020-12 mode, validate a complete
synthetic Slack/knowledge/case/result/reference read model, cover the empty/redacted/missing-source
states, prove original candidate/reference order, enforce created/reused receipt linkage, publish
the package/index exports, and reject live/provider/reconstruction/mutation fields.
