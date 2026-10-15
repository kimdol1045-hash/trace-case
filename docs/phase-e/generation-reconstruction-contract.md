# PE-SE1-T12 generation input reconstruction contract

## Status and authoritative boundary

This document fixes the provider-neutral `1.0.0` reconstruction contract and the storage
precondition required to implement it safely. The canonical Draft 2020-12 schema is
`packages/contracts/generation/reconstruction/1.0.0/schema.json`, with schema ID
`https://qa-tool.local/schemas/generation/reconstruction/1.0.0/schema.json`.

The single authoritative operation is:

```text
GET /api/generation-jobs/{jobId}/reconstruction
```

It has no request body, query mode, provider selector, execution option, or mutation mode. A found
job returns `GetGenerationReconstructionResponse` with either `AVAILABLE` or one stable
`UNAVAILABLE` reason. An unknown job uses the existing `RESOURCE_NOT_FOUND` envelope. Historical
unavailability is a successful `200` read because it describes what was or was not sealed; it is
not permission to look up present-day data.

The backend implementation includes the immutable capture migration, trusted assembler,
reconstruction service/repository loader, and FastAPI operation. The Web UI remains intentionally
out of scope.

## Exactness guarantee

For `availability: AVAILABLE`, `input` is the exact trusted provider-neutral JSON value that was
validated and sealed for this job before any execution attempt. Exactness means:

- every Generation Input 1.0.0 field and value is retained;
- every array remains in its original order, including source revision IDs, ranked context,
  steps, labels, issue links, category candidates, and empty arrays;
- JSON `null`, `[]`, `{}`, and non-empty values remain distinguishable;
- no field is projected from `generation_jobs.result`, `generation_applies`, or apply outcomes;
- no provider-native endpoint, model, message, sampling, credential, or response field is added.

JSON object member order and transport whitespace are not domain data and are not promised.
PostgreSQL `jsonb` is suitable because it preserves the JSON data model, array order, and
null/empty distinctions. The canonical digest removes object-member/whitespace ambiguity.

The response `policy` is executable contract state, not advisory metadata. Every variant fixes:

- `sourceBasis: SEALED_GENERATION_INPUT`;
- `readOnly: true` and `sealedSnapshotsOnly: true`;
- all live Slack, knowledge, case, issue, and category consultations to `false`;
- `providerCallAttempted: false` and `mutationPerformed: false`;
- array-order and null/empty preservation to `true`.

The operation must not acquire a write lock, flush a session, append audit/outbox rows, update a
last-accessed timestamp, call an embedding/generation adapter, or cause lazy loads of live source
relationships.

## Canonical digest provenance

`provenance.inputDigest` is distinct from the existing sealed context digest:

1. Validate that the stored input is a JSON object.
2. Canonicalize the complete input with the JSON Canonicalization Scheme in RFC 8785.
3. Encode the canonical representation as UTF-8.
4. Compute SHA-256 and expose the lower-case 64-character hex value.
5. Expose the canonical UTF-8 byte length as `canonicalByteLength`.

The response therefore fixes `algorithm: SHA-256` and `canonicalization: RFC8785`. Canonicalization
sorts object member names but does not sort arrays, discard nulls, collapse empty collections,
normalize strings, or trim text. A serializer that changes array order or converts null to an empty
value necessarily changes the digest.

`sealedContextDigest` remains the existing T09 digest over job/context provenance and ranked
`generation_contexts`. It proves the context-row seal; it is not a digest of Generation Input
1.0.0 and must never be reported as `inputDigest`.

Capture rejects a canonical Generation Input larger than the response contract's 16 MiB maximum
before the job can be sealed, so every `AVAILABLE` capture is representable by this operation.

## Availability and compatibility behavior

The service classifies a found job into exactly one state:

| Availability | Reason | Required behavior |
|---|---|---|
| `AVAILABLE` | `null` | The input capture is complete, the stored and payload versions agree, Input 1.0.0 validation passes, and the RFC 8785 digest matches. Return the stored JSON value unchanged and derive the canonical byte length. |
| `UNAVAILABLE` | `LEGACY_INPUT_NOT_SEALED` | The migration explicitly marks a pre-capture job and all input snapshot fields are absent. Return `input: null`, no input digest, and never backfill. |
| `UNAVAILABLE` | `SEALED_INPUT_INCOMPLETE` | Capture fields are partial, the payload is not an object, the sealed context fails verification, version fields disagree, schema validation fails, or canonical digest verification fails. Return `input: null` and no input digest. |
| `UNAVAILABLE` | `UNSUPPORTED_INPUT_SCHEMA_VERSION` | A complete payload and verified canonical digest exist, but reconstruction contract 1.0.0 does not allowlist its recorded input version. Return no input and do not downgrade or guess compatibility. |

Classification is fail-closed. `SEALED_INPUT_INCOMPLETE` takes precedence over version support when
the stored value or digest cannot first be trusted. `UNSUPPORTED_INPUT_SCHEMA_VERSION` may expose
the already verified digest provenance, but not an unvalidated payload. There is no partially
reconstructed response and no per-section live fallback.

Authorization, if added to the generation job family, must be checked before disclosing the exact
input. Redaction is deliberately not an availability variant: a redacted document would no longer
be the exact input. An unauthorized caller receives the platform's normal authorization response,
not a modified reconstruction.

## Storage audit and resolved authoritative gap

The current T09–T11 implementation was inspected at the job model/migrations, context builder and
seal, lifecycle runner, adapter boundary, stored result/apply ledger, comparison projection, and
wire serializers.

What is already immutable and usable without a live lookup:

- `generation_jobs` identity, lifecycle, lineage, output `schema_version`, prompt/provider/model
  provenance, context item count/digest/sealed time, and optional stored result;
- ordered `generation_contexts` evidence/source metadata and generic node/edge JSON snapshots;
- immutable apply receipt, result/context/placement digests, and ordered apply outcomes;
- `GenerationRequest.payload`, once supplied, is sent unchanged by the provider adapter.

Before this implementation, the following values were not stored or derivable exactly:

- the Generation Input `inputSchemaVersion` (the API serializer currently hardcodes `1.0.0` while
  `generation_jobs.schema_version` is the output version);
- `requestId`, input `createdAt`, all authoritative empty arrays, and their original order;
- the exact four Slack structured fields, `missingFields`, `openQuestions`, and ordered source
  revision IDs for every historical job;
- complete existing-case, failed/blocked execution, open-issue, and explicit-source input objects;
- the ordered category candidate set and complete generation policy/batch;
- a canonical digest of the complete input document.

The current `generation_context_digest` covers selected job provenance and ranked context entries.
It does not cover a Generation Input 1.0.0 document. Empty input sections have no context row, and
the generic ontology snapshots do not reliably retain the complete input objects. T10 result/apply
storage starts after generation and cannot recover outbound input. T11 intentionally represents
missing display material as `MISSING_SOURCE`; its comparison response is not an outbound input.

Consequently, exact reconstruction from pre-migration rows remains impossible in the general case.
The implementation adds the immutable persisted input snapshot for new jobs without backfilling
legacy rows. Any implementation that joins current `slack_work_items`, Slack revisions/parse
results, `knowledge_nodes`, cases,
executions, issues, folders/categories, or settings/policy rows would create a plausible new input,
not reconstruct the original one, and violates this contract.

## Implemented persistence change

Migration `20260827_0020` adds these immutable `generation_jobs` fields:

| Column | Shape | Purpose |
|---|---|---|
| `generation_input_capture_state` | `LEGACY_NOT_CAPTURED` or `SEALED`, non-null | Makes legacy classification explicit rather than inferring it from dates or null accidents. |
| `generation_input_schema_version` | nullable nonblank version | Records the actual input version; it is not the existing output `schema_version`. |
| `generation_input` | nullable JSONB object | Stores the complete validated provider-neutral input JSON value. |
| `generation_input_digest` | nullable lower-case SHA-256 hex | Stores the RFC 8785 canonical input digest. |

Migration behavior is part of the contract:

- every pre-migration job is set to `LEGACY_NOT_CAPTURED`, with the other three fields `NULL`;
- there is no data backfill from Slack, knowledge, case, issue, category, result, or apply rows;
- after the cutover, new root jobs and retries of captured predecessors must be `SEALED` with all
  three fields present;
- a database check enforces the two complete shapes, JSON object type, and digest format;
- the existing sealed-job immutability mechanisms reject replacement or update of any capture
  field after sealing, while sealed context ownership prevents job deletion;
- application sealing validates that payload `inputSchemaVersion` equals the recorded input
  version and `requestedOutputSchemaVersion` equals the job's output `schema_version`.

The canonical byte length is recomputed from the immutable JSON value and verified with the digest;
it need not be another persisted source of truth.

For a new root job, the trusted assembler must finish the full Input 1.0.0 document, validate it,
canonicalize it, and persist it atomically with the ranked context seal before returning `QUEUED`.
Execution later passes that stored payload unchanged to `GenerationRequest`.

For a retry of a captured job, all domain context arrays, category candidates, and generation policy
come from the predecessor's sealed input without live reads. The child receives its own trusted
`requestId` and `createdAt`, is validated and sealed as its own complete input, and receives its own
digest. A retry of a legacy job remains `LEGACY_NOT_CAPTURED`; it must not be silently upgraded by
reading current state.

## Read-service algorithm and implementation mapping

The read implementation is intentionally narrow:

1. Load the job and ordered sealed context rows only; use the existing context digest verifier.
2. Read the four immutable input-capture columns from the job snapshot.
3. Apply the availability precedence above.
4. For a candidate complete capture, compute RFC 8785 canonical bytes/digest, verify the recorded
   versions and digest, derive the byte length, then validate the exact allowlisted input schema.
5. Return the persisted input JSON value unchanged or one unavailable envelope.

| Implementation area | Mapping |
|---|---|
| Alembic / `GenerationJob` model | Four capture fields, shape constraints, explicit legacy marking, and sealed-row immutability coverage are installed by `20260827_0020`. |
| trusted input assembler | Every Input 1.0.0 section is materialized once in authoritative order and validated before sealing. |
| `GenerationRepository.create_*` / `persist_context` | Context rows and the exact input/digest persist atomically; no new complete seal exists without both. |
| `retry_job_with_context` | Captured domain input is copied without live reads; legacy predecessors preserve legacy unavailability. |
| reconstruction repository/service | The read-only loader touches only job/input/context snapshots and returns the classified contract model. |
| generation lifecycle runner | The stored input payload is passed unchanged to the injected provider boundary; execution-time live reconstruction is forbidden. |
| FastAPI / generated client | `GET /api/generation-jobs/{jobId}/reconstruction` and its complete generated TypeScript operation/schema surface are published with normal not-found behavior. |

No Web implementation is part of this backend task.

## Relationship to T11 comparison

T11 and T12 are separate reads over related immutable history:

- T11 projects display-friendly evidence, stored result, reference edges, and optional apply
  linkage; it may expose granular `EMPTY`, `REDACTED`, or `MISSING_SOURCE` states.
- T12 returns one exact provider-neutral input document or no document. It never uses T11 as an
  intermediate representation and never reads result/apply data.

A T11 response can remain useful for a legacy job while T12 correctly returns
`LEGACY_INPUT_NOT_SEALED`. Richer future T11 display snapshots do not authorize a T12 backfill.

## Contract evidence

Focused checks:

```text
pnpm --filter @tc/contracts exec vitest run \
  src/phase-e-generation-reconstruction-contracts.test.ts
pnpm test:schema
pnpm --filter @tc/contracts typecheck
pnpm exec eslint packages/contracts --max-warnings 0
node scripts/python.mjs -m pytest \
  backend/tests/unit/test_generation_reconstruction.py \
  apps/api/tests/test_generation_openapi.py
```

The fixtures cover one complete exact reconstruction and the legacy, incomplete, and unsupported
version states. Contract tests compile every definition in strict Draft 2020-12 mode, verify the
package/index exports, recompute the fixture's RFC 8785 SHA-256 and canonical byte length, prove that
array reordering changes the digest, distinguish JSON null from empty arrays, and reject provider,
live-source, result/apply, and mutation fields.

Disposable PostgreSQL tests additionally capture the exact value observed at the runner/provider
boundary, mutate or delete mutable live Slack, knowledge, case, issue, and category records, and
install live-table read tripwires that include immutable executions. They prove that reconstruction
stays byte-for-byte equal without provider calls, row writes, audit events, or outbox events, and
exercise not-found, legacy, incomplete, unsupported-version, and execution-unavailable behavior.
An oversized-input regression also proves the 16 MiB schema/API/capture limit stays identical and
that a rejected capture leaves both `generation_jobs` and `generation_contexts` row counts unchanged.
