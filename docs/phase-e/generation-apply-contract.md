# PE-SE1-T10 generation apply contract

## Status and scope

This document fixes the provider-neutral `1.0.0` apply boundary before backend or Web
implementation. The canonical schema is
`packages/contracts/generation/apply/1.0.0/schema.json`, with JSON Schema ID
`https://qa-tool.local/schemas/generation/apply/1.0.0/schema.json`.

The operation is:

```text
POST /api/generation-jobs/{jobId}/apply
Idempotency-Key: <same value as body.idempotencyKey>
```

This contract does not connect or select a generation provider, implement the endpoint, add a
Web action, or complete `PE-SE1-T10`. The published T09 job contract remains unchanged: applying
does not add an `APPLIED` generation-job status, and the job remains `SUCCEEDED`.

## Trust boundary and operation order

The request selects placement but never supplies generated case content. Unknown properties are
rejected, including provider/model/endpoint controls and any attempt to supply a replacement
summary, step, expected result, label, evidence, source field, case key, timestamp, or existing-case
update.

The implementation must perform the following checks in one database transaction, with no QA
ledger write before steps 1–6 succeed:

1. Lock the generation job and load its immutable sealed context. Only `SUCCEEDED` with a non-null
   stored result is eligible.
2. Re-run the output `1.0.0` JSON Schema against the stored result. API serialization at T09 is not
   treated as validation evidence.
3. Reconstruct `GenerationContext` from sealed context rows and re-run the trusted
   `GenerationResponseValidator`. Output version, ACTIVE evidence membership, category membership,
   contiguous steps, normalized uniqueness, and unsupported-claim policy are checked again.
4. Compute `expectedResultDigest` as lower-case SHA-256 over the RFC 8785 JSON Canonicalization
   Scheme representation of the stored result. It and the request's `expectedContextDigest` must
   match the locked job.
5. Recompute each `exactContentKey` with the T07 algorithm and require exactly one case placement
   for every stored case in original zero-based order.
6. Lock the referenced category, reused parent, and reused cases/current versions; rerun the T08
   placement against current active projections; and compare every requested version, category,
   mapping method, reuse eligibility, and decision. Stale, deleted, inactive, ambiguous, missing, or
   no-longer-reproducible placement fails before ledger writes.
7. Apply the parent decision and every generated-case decision, record the immutable receipt and
   per-case outcome, append the batch audit/outbox evidence, and commit once. Any error rolls back
   every row from the operation.

`placementDigest` in the response is SHA-256 over the RFC 8785 canonical form of the normalized
`placement` object. It excludes the idempotency key and both optimistic digests.

## Placement invariants

The category is always an existing active category with an expected resource version. The planner
may select it through exact name, exact alias, supplied semantic comparison, or the one active
system `미분류` fallback. Apply never creates a category.

The parent decision is exactly one of:

- `REUSE_EXISTING`: the work item ID, resource version, category, method, and score must still match.
  Reuse does not rename, recategorize, relabel, or otherwise update the work item.
- `CREATE_NEW`: the server-owned Slack source key, summary, and selected category must reproduce
  the planner's `CREATE_NEW_PARENT` decision. A concurrent source/key owner is a conflict; it is not
  silently reused or overwritten.

If a parent is reused, its current category is authoritative for the created cases. The separate
category placement remains explainability evidence and may not be used to move that parent.

Each case placement is exactly one of:

- `CREATE_NEW`: create a new case and initial immutable version from the stored generated case.
- `REUSE_EXISTING`: record the relationship to one current ACTIVE, non-deleted planner candidate.
  The request pins both the aggregate resource version and current version ID/number.

The server may accept a requested exact or semantic reuse only while that case remains in the
recomputed ordered candidate set. Choosing create versus an eligible candidate is an explicit
placement choice; it is not permission to change a candidate. There is deliberately no `UPDATE`,
`RESTORE`, `MOVE`, `APPEND_VERSION`, or overwrite action in the apply contract.

## Atomic ledger materialization

All generated candidates are handled in stored-result order in one transaction. One receipt row
per job and one outcome row per generated case record `CREATED` versus `REUSED`. The implementation
must enforce one committed receipt per generation job.

For a newly created case/version, the authoritative mapping is:

| Ledger field | Value |
|---|---|
| case status | `ACTIVE` |
| aggregate/version number | `1` / `1` |
| version source type | `SLACK_AI` |
| expected-result mode | `CASE` |
| summary, case expected result, priority, labels, generation reason | exact validated stored-result values |
| steps | exact original order and action/precondition values; omitted step precondition persists as `null` |
| aggregate `precondition` projection | distinct non-null step preconditions in original order, joined with LF; if none exist, the server-owned non-claim sentinel `[생성 결과에 명시된 공통 사전조건 없음]` |
| evidence node IDs | exact generated order |
| source revision IDs | for evidence IDs in generated order, sealed source revisions in context rank/source order, de-duplicated by first occurrence |
| source provenance | Slack work-item ID, generation-job ID/version, context/result digests, output schema version, inference marker, open questions |
| IDs, TC key, timestamps | allocated only by the trusted ledger boundary |

The aggregate precondition projection exists because output `1.0.0` has per-step optional
preconditions while the current ledger requires one non-blank aggregate precondition. It is a
deterministic presentation projection: step values remain lossless and the no-precondition sentinel
asserts no product behavior.

For a reused case, apply writes no `TestCase`, `TestCaseVersion`, step, tag, case audit mutation, or
case outbox event. The receipt returns `ledgerMutation: NONE` and `preserved: true`. This rule applies
to every existing case, including MANUAL/API/FILE_IMPORT cases and a formerly generated case that a
human later edited; therefore apply cannot silently overwrite human work.

## Success and idempotency

The first successful request returns `201` with one immutable `GenerationApplyReceipt`:

- generation apply/job IDs and the still-`SUCCEEDED` job status;
- context, result, and normalized placement digests;
- `COMMITTED` transaction status;
- one created/reused parent outcome;
- one ordered created/reused case outcome per generated case;
- generated/created/reused counts, one batch audit ID, and the server timestamp.

Created outcomes contain the new ACTIVE case and its complete version-1 snapshot so consumers can
verify ordered content and provenance. Reused outcomes contain only the pinned existing-case
reference, match reason, and the explicit no-mutation guarantee.

Idempotency uses `(idempotencyKey, jobId, expectedContextDigest, expectedResultDigest, normalized
placement)`:

- the same key and identical normalized request for the same job returns the original receipt and
  original HTTP status without allocating a key or adding any row;
- the same key with another job or payload returns the existing `IDEMPOTENCY_CONFLICT`;
- another key for a job that already has a committed receipt returns
  `GENERATION_JOB_ALREADY_APPLIED` with the original apply ID.

## Stable errors

| HTTP | Code | Required meaning |
|---|---|---|
| `404` | `RESOURCE_NOT_FOUND` | Job or referenced placement resource does not exist. |
| `409` | `IDEMPOTENCY_CONFLICT` | The key was used for another normalized operation. |
| `409` | `GENERATION_JOB_STATE_CONFLICT` | Operation is `APPLY`; actual status is not the sole allowed status `SUCCEEDED`; no ledger write started. |
| `409` | `GENERATION_JOB_ALREADY_APPLIED` | A different key/request attempted to apply a job with a committed receipt. |
| `409` | `GENERATION_STORED_RESULT_INVALID` | Redacted schema, grounding, or materialization revalidation failed; no stored provider body is echoed and no ledger write started. |
| `409` | `GENERATION_PLACEMENT_CONFLICT` | Digest, order, exact key, category, parent, or reuse candidate is stale/not reproducible; no ledger write started. |
| `422` | `VALIDATION_FAILED` | The path/body/header or placement shape is malformed, including missing/duplicate/non-contiguous case decisions or header/body key mismatch. |
| `500` | `GENERATION_APPLY_TRANSACTION_FAILED` | A named write stage failed and the transaction status is `ROLLED_BACK`; provider-native error text is never exposed. |

The schema fixes enumerated redacted stages/reasons and rejects extra error details. Structural JSON
Schema cannot express cross-document case count/order or current-ledger checks, so those are
normative semantic validations and map to the stable errors above.

## Rollback evidence required from implementation

The contract test includes the `RollbackEvidence` verification shape, but this is test-only and is
not an API response. The backend acceptance test must use an isolated PostgreSQL database, capture
committed before counts, inject a failure after at least one earlier candidate has been staged, and
then capture committed after counts from a new transaction. Every delta must be exactly zero for:

- work items, test cases, test-case versions, steps, current/version tags;
- generation apply receipts and outcomes;
- key allocations, audit logs, and outbox events.

The test must inject at least a middle-case `CREATE_VERSION` failure and a late `OUTBOX` failure. It
must also assert that a later clean retry can allocate the same next logical TC key; a rolled-back key
allocation is not permitted to leave a gap. Merely checking an API `ROLLED_BACK` field is not database
evidence.

## Contract verification

```text
pnpm --filter @tc/contracts exec vitest run src/phase-e-generation-apply-contracts.test.ts
pnpm --filter @tc/contracts exec vitest run src/phase-e-generation-contracts.test.ts src/phase-e-generation-job-api-contracts.test.ts
pnpm --filter @tc/contracts typecheck
pnpm exec eslint packages/contracts/src/phase-e-generation-apply-contracts.test.ts packages/contracts/src/index.ts --max-warnings 0
pnpm test:schema
```

Passing these checks proves the provider-neutral shape and semantic fixtures only. T10 remains open
until the PostgreSQL transaction, migration constraints, repository/service, FastAPI endpoint, API
integration rollback/idempotency tests, OpenAPI client generation, and relevant UI work are separately
implemented and verified.
