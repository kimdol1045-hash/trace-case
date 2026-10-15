# Phase E Generation API input/output contract

## Contract status

`PE-SE1-T02` defines the provider-neutral Generation API payload pair below. Both schemas
use JSON Schema Draft 2020-12, reject unknown properties, and negotiate one exact version
pair. The fixtures are synthetic and contain no production Slack, QA, issue, or provider
data.

| Direction | Current version | JSON Schema `$id` | Repository source |
|---|---|---|---|
| Trusted adapter → provider | `1.0.0` | `https://qa-tool.local/schemas/generation/input/1.0.0/schema.json` | `packages/contracts/generation/input/1.0.0/schema.json` |
| Provider → trusted adapter | `1.0.0` | `https://qa-tool.local/schemas/generation/output/1.0.0/schema.json` | `packages/contracts/ai-test-case-generation-output.schema.json` |

The input fixture is
`packages/contracts/generation/fixtures/1.0.0/input.full-context.json`; its matching
response is `output.grounded.json` in the same directory.

## Input boundary

The adapter sends domain context rather than provider-native prompts, messages, models, or
sampling controls. Every context section is required so an empty search result cannot be
confused with an omitted builder step.

| Section | Contract rule |
|---|---|
| `slackWorkItem` | Carries the trusted work-item/node IDs, source version and immutable revision IDs plus the four structured Slack fields. A missing field is `null` and is also named in `missingFields`. |
| `activeKnowledge` | Accepts only current `ACTIVE` requirements, decisions, and constraints with `validTo: null`, evidence revision IDs, rank, and selection reason. `AMBIGUOUS`, `SUPERSEDED`, and `RETRACTED` knowledge cannot validate here. |
| `existingCases` | Carries current `ACTIVE` cases and immutable version IDs for duplicate/reuse context, supporting both `PER_STEP` and `CASE` expected-result modes. |
| `failedOrBlockedExecutions` | Accepts only actual `FAIL` or `BLOCKED` attempts; `PASS` and `UNEXECUTED` are outside generation risk context. |
| `openIssues` | Accepts only `OPEN`, `IN_PROGRESS`, or `REOPENED` issues. `RESOLVED` and `CLOSED` issues are not current open-issue context. |
| `extraSources` | Accepts only material explicitly attached to this request by a user (`providedByUser: true`) with trusted source/evidence IDs and a content hash. |
| `categoryCandidates` | Lists existing QA-board categories the provider may select. The adapter must include the system `미분류` candidate as its fallback; the provider cannot create a category. |
| `generationPolicy` | Fixes the batch policy at 5–10 cases, `CASE` expected-result mode, happy-path/applicable-CRUD priority, evidence-required behavior, no unsupported edge cases, open questions for unknown facts, redaction, and output language. |

Provider-specific `provider`, `model`, `messages`, `temperature`, credential, endpoint,
timeout, and rate-limit fields are deliberately rejected by this payload. They belong to
the Integration/adapter configuration and may change without changing the domain contract.

## Output and trust boundary

The provider returns 5–10 cases with summary, ordered actions and optional per-step
preconditions, one case-level expected result, priority, labels, generation reason,
inference marker, open questions, and context reference IDs. It selects a category from the
request candidates.

JSON Schema rejects any provider-supplied `caseId`, `caseKey`, `workItemId`,
`generationJobId`, `contextSnapshotId`, `sourceRevisionIds`, `sourceMetadata`, or creation
timestamp. After schema validation, the adapter performs the cross-document checks JSON
Schema cannot express by itself:

1. `outputSchemaVersion` exactly equals the request's `requestedOutputSchemaVersion`.
2. Every `evidenceNodeId` exists in the input snapshot's Slack work item, ACTIVE knowledge,
   existing cases, failed/blocked executions, open issues, or explicit extra sources.
3. The primary category and every alternative exist in `categoryCandidates`, are unique,
   and do not repeat each other.
4. Step sequences are one-based and contiguous; case summaries and labels are unique after
   trim, Unicode normalization, and case folding.

Only the trusted boundary may allocate case keys, resolve the internal parent work
item/category, attach source revisions, or set authoritative case timestamps after those
checks. The current T09 lifecycle allocates its generation job ID/version/timestamps when it
seals context and may retain a validated response as candidate-only job result; it does not
allocate a case key or write the Library. A schema-valid response with an unknown evidence or
category ID is still rejected and is never eligible for the future T10 transaction.

## Post-validation placement plan

`PE-SE1-T07` and `PE-SE1-T08` add a pure `GenerationPlacementPlanner` after response
validation and before any future transaction boundary. The caller supplies the
`ValidatedGeneration`, current existing-case/category/parent projections, trusted Slack parent
identity, and any precomputed semantic vectors. The planner has no repository, transport, or
provider dependency and cannot allocate keys or write a generation response to the QA ledger.

The plan contains one entry per generated case in the original response order. Each entry
retains the generated case, its deterministic exact-content key, and ordered reuse candidates.
Exact comparison covers normalized summary, ordered action/precondition steps, and the
case-level expected result; exact candidates always precede semantic candidates, including an
equal cosine score. Semantic comparison is threshold-inclusive and only uses vectors supplied
by the caller, so planning never invokes an embedding or Generation API provider.

The same plan contains one explicit category/parent decision:

- A trusted category name/alias takes precedence when supplied. Otherwise the validated
  provider-selected category ID is resolved to the supplied active candidate's canonical name.
- Category exact/alias matching precedes semantic matching. A miss always selects the one
  supplied active system `미분류` category; the planner never creates a category.
- Parent Slack source identity/key and normalized title/alias precede semantic matching. A
  miss returns `CreateNewParentDecision` with the trusted source key, title, and selected
  category rather than performing a write.
- Candidate inputs are materialized once and the duplicate/mapping engines apply stable ID/key
  tie-breaks, so replaying the same validated response and vectors produces an equal plan even
  when candidate iteration order changes.

## Manual generation job lifecycle

`PE-SE1-T09` exposes the provider-neutral lifecycle and `PE-SE1-T10` adds an explicit,
provider-independent persistence boundary:

| Operation | Current behavior |
|---|---|
| `POST /api/generation-jobs` | Requires a body/header idempotency key and Slack work-item ID, deterministically allocates the next generation version, seals the current grounded context, and returns a root `QUEUED` job without an outbound call. |
| `GET /api/generation-jobs` | Lists immutable job resources newest first with Slack work-item/status filters and a filter-bound cursor. |
| `GET /api/generation-jobs/{jobId}` | Returns lifecycle state, lineage, sealed provenance, stable redacted failure metadata, candidate-only result, and ordered context items. |
| `POST /api/generation-jobs/{jobId}/execute` | Accepts only `QUEUED`. While `PE-SE1-T01` remains unresolved, production returns `GENERATION_SETUP_REQUIRED`, proves `externalCallAttempted: false`, and leaves the job `QUEUED`; injected test runners alone prove `RUNNING → SUCCEEDED/FAILED`. |
| `POST /api/generation-jobs/{jobId}/retry` | Accepts only `FAILED` and appends a distinct `QUEUED` child with the same root, `retryOfJobId` pointing to the failed predecessor, the next attempt/generation version, and an exact copy of the predecessor's sealed context. It never invokes a runner automatically. |
| `GET /api/generation-jobs/{jobId}/apply-plan` | Accepts only an unapplied `SUCCEEDED` job, revalidates its stored result and sealed grounding, plans against locked current ACTIVE ledger projections, and returns server-owned context/result digests plus complete optimistic category/parent/case placement. It makes no ledger or provider write. |
| `POST /api/generation-jobs/{jobId}/apply` | Requires the body/header idempotency key and the complete server plan. It locks and recomputes the trust boundary, then atomically creates any required parent and new ACTIVE `SLACK_AI` cases while recording no-write reuse outcomes for existing cases. The job remains `SUCCEEDED`. |
| `GET /api/generation-jobs/{jobId}/comparison` | T11's single provider-neutral historical read model. It compares the exact sealed Slack/knowledge/case evidence with candidates and optional apply linkage, explicitly marks empty/redacted/missing history, and performs no live-source substitution, T12 input reconstruction, provider call, or mutation. The contract, read-only API, generated client, and responsive UI are implemented. |
| `GET /api/generation-jobs/{jobId}/reconstruction` | T12's implemented read boundary returns the exact sealed Generation Input JSON value plus RFC 8785 SHA-256 provenance, or a stable legacy/incomplete/unsupported-version state. Migration `20260827_0020`, the snapshot-only repository/service, FastAPI operation, and generated OpenAPI client are implemented; no Web screen is required. It never rebuilds from live Slack/knowledge/cases/issues/categories, calls a provider, or mutates rows. |

The only legal persisted statuses are `QUEUED`, `RUNNING`, `SUCCEEDED`, and `FAILED`.
PostgreSQL constraints and triggers enforce lifecycle shape, legal transitions, immutable sealed
provenance, one retry child per predecessor, and complete allowlisted failure metadata. Retry
context rows may be copied only when every evidence/source/snapshot field equals the sealed
predecessor; a one-step migration downgrade restores the exact preceding trigger definitions.

Mutation replay uses the existing append-only audit-backed idempotency convention. Reusing one
key with the same normalized operation returns the original response; reusing it with another
payload returns `IDEMPOTENCY_CONFLICT`. T09 lifecycle operations store generation jobs, context
rows, and audit evidence only. T10 apply is the sole generation path that may create ledger rows;
it stores one immutable receipt/outcome set and does not add an `APPLIED` job status.

The implemented `PE-SE1-T10` apply boundary is specified separately in
[Generation apply contract](./generation-apply-contract.md). It defines
`POST /api/generation-jobs/{jobId}/apply`, complete optimistic placement, trusted stored-result
revalidation, one-transaction created/reused receipts, no-write reuse protection, stable conflicts,
and PostgreSQL fault-injection evidence. The generated OpenAPI client owns the request/receipt/plan
types; the browser never fabricates resource versions, digests, candidate content, or ledger IDs.

The implemented `PE-SE1-T12` reconstruction boundary is specified in
[Generation input reconstruction contract](./generation-reconstruction-contract.md). New root jobs
atomically validate and seal the complete provider-neutral Input `1.0.0` before execution; captured
retries copy every domain array, category candidate, and policy from the predecessor seal while
allocating only their own request ID and creation time. Execution supplies that exact stored value
to the injected provider boundary, and reconstruction returns the same JSON value with its verified
RFC 8785 SHA-256. Pre-capture jobs remain explicitly unavailable, partial or corrupt captures fail
closed, and a verified but unallowlisted input version is reported without exposing its payload.

The Slack work-item detail links to a responsive manual job screen. The current screen exposes explicit
create, execute, refresh, detail, append-only retry, and explicit all-candidate apply controls;
renders setup-required guidance and created/reused receipt outcomes in Korean; and keeps
`/test-cases/new` available. A successful result remains candidate data until the user confirms the
server plan. Tablet/mobile CSS stacks the two-column history/detail
layout at 900 px, turns actions into 44 px full-width controls at 700 px, and collapses dense
metadata/context rows at 480 px. Each job links to the T11 comparison route, whose four ordered
sections render the sealed Slack fields/thread revisions, snapshot-time ACTIVE knowledge, immutable
case/version snapshot, and every ordered generated candidate with reason/questions/inference and
keyboard-operable evidence links. Created/reused outcomes link to case details, and unavailable
historical material has distinct empty/redacted/missing labels. The precise boundary and verification
evidence are specified in [Generation comparison read-model contract](./generation-comparison-contract.md).

## Version policy

- Schema documents are immutable after publication and identified by an exact SemVer in
  both `$id` and the payload version field.
- The adapter allowlists exact input/output pairs. For the current release the only allowed
  pair is `1.0.0 → 1.0.0`; version ranges, silent downgrade, and “latest” aliases are not
  accepted.
- The request declares `inputSchemaVersion` and `requestedOutputSchemaVersion`. The provider
  must echo the latter as `outputSchemaVersion`; a missing, unsupported, or mismatched value
  fails before any Library write.
- A patch release is validation-equivalent clarification or correction, a minor release is
  a backward-compatible optional addition or relaxation, and a major release removes or
  requires fields or changes their meaning. Even patch/minor versions require an explicit
  allowlisted pair; SemVer never grants automatic runtime compatibility.
- New versions are added alongside retained versions and receive new fixtures and contract
  tests. Exact input replay requires the immutable input snapshot and digest defined by
  [Generation input reconstruction contract](./generation-reconstruction-contract.md); the current
  ranked context seal alone is not a complete Generation Input document. Pre-capture jobs remain
  explicitly unavailable and are never backfilled from live state.

## Validation evidence

`packages/contracts/src/phase-e-generation-contracts.test.ts` compiles every definition in
strict Draft 2020-12 mode, validates the full-context fixtures, exercises lifecycle filters
and policy/version boundaries, rejects provider-specific fields and trusted metadata, and
proves unknown evidence/category IDs fail the adapter membership check.

Focused verification commands:

```text
pnpm --filter @tc/contracts exec vitest run src/phase-e-generation-contracts.test.ts
pnpm --filter @tc/contracts exec vitest run src/phase-e-generation-job-api-contracts.test.ts
pnpm --filter @tc/contracts exec vitest run src/phase-e-generation-comparison-contracts.test.ts
pnpm --filter @tc/contracts exec vitest run src/phase-e-generation-reconstruction-contracts.test.ts
pnpm test:schema
pnpm --filter @tc/contracts typecheck
pnpm openapi:check
node scripts/python.mjs -m pytest backend/tests/unit/test_generation_{deduplication,mapping,planning}.py
TC_TEST_DATABASE_URL=... node scripts/python.mjs -m pytest backend/tests/repository apps/api/tests -m postgres
pnpm --filter @tc/web exec vitest run src/generation-job-*.test.ts src/generation-job-*.test.tsx
pnpm --filter @tc/web build
node scripts/python.mjs -m ruff check backend/src/tc_backend/generation backend/tests/unit/test_generation_*.py
node scripts/python.mjs -m mypy backend/src/tc_backend/generation
TC_TEST_DATABASE_URL=... node scripts/python.mjs -m pytest apps/api/tests/test_generation_apply_api_postgresql.py
```

## External provider decisions still open

These values belong to `PE-SE1-T01` or the later adapter task and are not selected by the
contract:

- Generation API base URL, provider identifier, and production ownership/hosting boundary.
- Authentication mechanism, credential rotation, secret storage, and outbound network
  allowlist/TLS requirements.
- Approved pricing state and quota; the product remains `Setup required` until the endpoint
  is team-owned/self-hosted or its spend is explicitly approved.
- Provider request and token/context limits, including how the adapter splits a broad work
  item into multiple 5–10-case batches when the full snapshot does not fit.
- Rate-limit headers/semantics, concurrency allowance, timeout, retry/backoff, and circuit
  breaker behavior.
- Provider error envelope, health-check endpoint, idempotency header support, and whether
  structured JSON Schema enforcement occurs server-side or only at the trusted adapter.
- Provider/model revision reporting and retention/logging guarantees for transmitted Slack
  and QA context. Provider metadata is recorded locally by the adapter, not accepted from
  generated case content.

Until those decisions are approved, production execution remains setup-required and manual Library
creation remains usable. A previously stored, trusted successful result can now be reviewed and
applied through the provider-neutral transaction, but that capability is not authorization to
configure or call an unapproved provider.
