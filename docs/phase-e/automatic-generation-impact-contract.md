# PE-SE2-T03 automatic generation impact contract

## Status and scope

This document fixes the released implementation boundary for `PE-SE2-T03`. It extends the
released [T01 orchestration contract](./automatic-generation-orchestration-contract.md) and
[T02 canonical-source deduplication contract](./automatic-generation-deduplication-contract.md)
only after a newer `SlackWorkItem.source_version` has passed the existing quiet-window, parse,
validated knowledge projection, grounded retrieval, and complete Generation Input gates.

T03 answers one durable question: does the current source snapshot change an input that can
affect generated TestCase meaning? It must not create a new automatic root merely because a
Slack delivery, outbox event, projection receipt, background-job attempt, timestamp, or revision
identifier changed.

The only generated effect remains one provider-free, sealed `AUTOMATIC` root in `QUEUED` state.
T03 does not execute a provider, persist a generation result, create or overwrite a TestCase,
produce a suggestion/apply receipt, post a Slack reply, or define the T04 protection policy for a
human-modified case.

## Existing authorities and composition point

T03 introduces no second Slack, knowledge, category, or generation-input model. The decision is
made inside `KnowledgeOutboxConsumer.process_automatic_generation`, after the current projection
is authoritative and while the canonical `SlackWorkItem` lock is held, using these existing
authorities:

| Concern | Existing authority |
|---|---|
| Canonical source and monotonic clock | `slack_work_items.id` and `source_version` |
| Immutable root/thread history | `slack_messages` and `slack_message_revisions` |
| Four structured fields | current-version `slack_parse_results` / `SlackWorkItem.parse_result` |
| Effective semantic state | current `ACTIVE` knowledge nodes and edges with immutable evidence |
| Retrieval selection | `GenerationRepository.build_context` |
| Complete provider input | Generation Input `1.0.0` assembly and validation |
| Historical comparison baseline | the preceding sealed `AUTOMATIC` root and its sealed context/Input |
| Exact transport replay optimization | `KnowledgeProjectionEvent.result`, never the product identity |

The current `BuiltGenerationContext` and one `as_of` boundary must be used both to produce the
impact projection and, when required, to seal the new job. Re-running retrieval between impact
classification and job capture is forbidden because it could classify one set and seal another.

## Semantic predecessor and automatic materialization baseline

T03 keeps two histories separate:

1. The semantic predecessor for current source version `V` is the immediately prior completed
   impact assessment for the same work item, ordered by `current_source_version`. A `SKIP` or
   `REVIEW_REQUIRED` assessment remains a real predecessor because it records what was evaluated,
   with which comparator/config contract, and why no job was created.
2. The canonical materialization predecessor is the last committed `AUTOMATIC` root before `V`:

   ```text
   generation_trigger = AUTOMATIC
   AND retry_of_job_id IS NULL
   AND slack_work_item_id = current work item
   AND source_version < V
   ORDER BY source_version DESC, generation_version DESC
   LIMIT 1
   ```

The work-item lock and existing automatic-source uniqueness make the materialization baseline
singular. A manual root, explicit retry child, background-job attempt, delivery receipt, or outbox
row is never that baseline. An automatic retry remains in its root lineage but is not a new
semantic or materialization predecessor.

After `v1 -> generate, v2 -> SKIP`, the semantic predecessor for `v3` is the completed v2
assessment while the materialization baseline remains the v1 automatic root. The v2 assessment
must carry both facts. Comparing v3 directly to a job and forgetting v2 is forbidden. If no prior
assessment and no preceding automatic root exist, the current version is the initial automatic
source even when its numeric `source_version` is greater than one. A historical automatic root
without a compatible assessment/seal is not silently comparable; it leads to `REVIEW_REQUIRED`.

## Generation-impact projection `1.0.0`

### Canonicalization

The fingerprint is lowercase SHA-256 over RFC 8785 canonical JSON bytes of a
`generation-impact/1.0.0` projection. Text uses the same deterministic NFKC and whitespace
normalization as validated knowledge extraction. Arrays that are semantic sets are sorted by
their normalized canonical JSON value and retain multiplicity. Object keys use the contract
names below. No LLM, embedding similarity, locale collation, current wall clock, or fuzzy
paraphrase decision participates.

“Meaning-equivalent” in this contract therefore means equality of these deterministic semantic
projections. A paraphrase that changes normalized content is conservatively relevant; a new
delivery or uncited thread revision that leaves the projection equal is not.

Slack history is reduced per logical message to its latest effective revision by normalized
revision chronology before semantic projection. A newly received late-old revision can increment
`source_version` while leaving that effective state unchanged, and the same normalized text under
a new revision ID remains equal. Tombstones remain in the immutable source ledger but affect the
projection through typed validity/review rules, not through timestamp or revision-ID churn.

The semantic fingerprint is compared together with a separate lowercase SHA-256
`contract_config_digest`. Its versioned canonical object includes impact comparator/canonicalizer
version, input/output schema versions, ontology version, prompt version, generation policy,
retrieval policy, and extractor provider/model/schema plus deterministic validator/classifier
versions. Runtime instance IDs, credentials, request IDs, and timestamps are excluded. `SKIP`
requires both digests to be supported and equal. A supported config change is `REGENERATE` with
`contractConfig` in `changed_fields`; an incompatible or unrecognized config is
`REVIEW_REQUIRED`, never an assumed match.

### Included fields

The projection includes only values that are passed to generation or determine its grounded
retrieval/category policy:

| Projection section | Included generation meaning |
|---|---|
| `slackWorkItem` | Exact nullable values of `developmentItem`, `requestedMenu`, `featureDescription`, and `requestDetails`; normalized `openQuestions`. `missingFields` is derived from the four nullable values and is not hashed separately. |
| `activeKnowledge.requirements` | Every currently effective `ACTIVE` requirement's normalized content. |
| `activeKnowledge.decisions` | Every currently effective `ACTIVE` decision's normalized content. |
| `activeKnowledge.constraints` | Every currently effective `ACTIVE` constraint's normalized content. |
| `activeKnowledge.evidence` | For each included requirement/decision/constraint, the sorted set of `(sourceKind, sourceEntityType, sourceContentHash)` citations from the selected sealed context. Evidence identity is content-based, not revision-ID-based. |
| `retrievalInputs.existingCases` | `caseKey`, summary, precondition, ordered step behavior, expected-result mode/content, priority, and normalized labels for every selected current ACTIVE case. Surrogate case/version/node IDs are omitted. |
| `retrievalInputs.failedOrBlockedExecutions` | FAIL/BLOCKED status, platform, normalized actual result, and the semantic linked-issue set. Execution/node/version IDs and occurrence timestamps are omitted. |
| `retrievalInputs.openIssues` | `issueKey`, current open status, severity, summary, expected result, and actual result. Surrogate IDs, linked-attempt IDs, and `updatedAt` are omitted. |
| `retrievalInputs.extraSources` | source type, title, normalized content, URI, content hash, and `providedByUser`. Source/evidence node IDs are omitted. |
| `categoryCandidates` | Ordered candidate semantics after canonical set sorting: stable category ID, label, score, and selection reason. A label, score, membership, or category identity change is relevant. |
| `generationPolicy` | Feature group, batch number/finality, case-count bounds, expected-result mode, happy-path/CRUD/edge-case/evidence flags, unknown/sensitive-data handling, and language. Job-derived batch/request IDs are omitted. Version/config identity is carried by `contract_config_digest`, not duplicated into semantic content. |

The active-knowledge lists contain no `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED`, expired, or
otherwise ungrounded item. Graph edges influence the fingerprint only through the resulting
grounded selected set; an edge rewrite that selects exactly the same semantic inputs is a `SKIP`.

### Excluded volatile and non-semantic values

The projection must not include:

- Slack `event_id`, delivery/event timestamps, message/revision UUIDs, `revision_ts`, author,
  permalink, raw payload, `source_version`, parse-result ID, parser confidence, or parser version;
- projection/outbox/checkpoint IDs and timestamps, `as_of`, node/edge/evidence/source-record IDs,
  `recordedAt`, `validFrom`, and immutable context row IDs/ranks;
- generation job/root/retry/request/batch IDs, `generation_version`, `attempt_number`, context or
  full-input digest, creation/seal/execution timestamps, provider execution metadata, audit IDs,
  and background-job ID/attempt/claim data;
- apply/suggestion/receipt IDs or current TestCase ownership-management fields reserved for T04.

A revision identifier may still appear in the complete sealed Generation Input for historical
provenance. Its presence there does not authorize including it in the impact fingerprint.
The full `generation_input_digest` is never an impact fingerprint: it intentionally covers
`requestId`, `createdAt`, `sourceVersion`, revision IDs, ranks, and other values that differ across
otherwise equivalent captures.

## Changed-field vocabulary

`changed_fields` is a JSON array with no duplicates, ordered by the following fixed contract
order. It reports semantic sections, not volatile leaf identities:

```text
slackWorkItem.developmentItem
slackWorkItem.requestedMenu
slackWorkItem.featureDescription
slackWorkItem.requestDetails
slackWorkItem.openQuestions
activeKnowledge.requirements
activeKnowledge.decisions
activeKnowledge.constraints
activeKnowledge.evidence
retrievalInputs.existingCases
retrievalInputs.failedOrBlockedExecutions
retrievalInputs.openIssues
retrievalInputs.extraSources
categoryCandidates
generationPolicy
contractConfig
```

The four Slack fields are reported individually. Content or membership changes use the matching
knowledge/retrieval section; citation-content membership changes additionally use
`activeKnowledge.evidence`. Initial and review-only assessments use the reserved singletons
`$INITIAL_SOURCE` and `$REVIEW_REQUIRED`. `SKIP` requires an empty list.

## Durable outcomes

The decision is one of four typed outcomes:

| Outcome | Reason and required effect |
|---|---|
| `INITIAL_GENERATE` | `FIRST_AUTOMATIC_SOURCE`; no predecessor exists. Create and select one new sealed `AUTOMATIC` root for the current source. |
| `SKIP` | `NO_GENERATION_RELEVANT_CHANGE`; supported predecessor/current semantic fingerprints and config digests are equal. Create no job/context/input/job-audit rows and reuse the materialization baseline. |
| `REGENERATE` | `GENERATION_RELEVANT_FIELDS_CHANGED` or `GENERATION_CONTRACT_CHANGED`; a supported deterministic comparison proves a relevant delta. Create and select one new sealed `AUTOMATIC` root for the current source. |
| `REVIEW_REQUIRED` | Equality or safe automatic regeneration cannot be proven. Reasons include `UNKNOWN_EFFECTIVE_STATE`, `AMBIGUOUS_KNOWLEDGE`, `DELETED_OR_RETRACTED_EVIDENCE`, `LEGACY_INPUT_INCOMPATIBLE`, `UNSUPPORTED_COMPARISON_SCHEMA`, `INCOMPATIBLE_CONTRACT_CONFIG`, `CORRUPT_SEAL`, and `IMPACT_COMPARISON_UNSAFE`. Persist the assessment and materialization baseline, but create/reuse no job. |

Legacy, unsupported, ambiguous, deleted/retracted, corrupt, or otherwise unknown data must never
be silently treated as equal or converted into an automatic job. `REVIEW_REQUIRED` is the
conservative typed outcome requested by this task. It records every available digest and uses
`$REVIEW_REQUIRED`; selected and reused job fields stay null. A transient failure before a stable
typed assessment is possible still follows T01 rollback and worker retry/dead-letter behavior.

## PostgreSQL impact ledger

Migration following T02 must add append-only `generation_change_assessments` with:

| Column | Contract |
|---|---|
| `id` | UUID primary key. Receipt only; never a meaning key. |
| `slack_work_item_id` | Required FK to the canonical Slack work item. |
| `predecessor_assessment_id` | Nullable self-FK to the immediately prior completed semantic assessment. |
| `baseline_source_version` | Nullable positive semantic predecessor version; for legacy review without an assessment, the materialization baseline's source version. |
| `current_source_version` | Required positive evaluated source version. |
| `materialization_baseline_generation_job_id` | Nullable FK to the last preceding automatic root; carried through SKIP/REVIEW. |
| `selected_generation_job_id` | FK to the newly created current automatic root for initial/regenerate; otherwise null. |
| `reused_generation_job_id` | FK equal to the materialization baseline for `SKIP`; otherwise null. |
| `outcome` | `INITIAL_GENERATE`, `SKIP`, `REGENERATE`, or `REVIEW_REQUIRED`. |
| `reason_code` | One allowlisted reason above, constrained to its outcome. |
| `changed_fields` | Required JSON array satisfying the fixed vocabulary/order. |
| `fingerprint_schema_version` | `generation-impact/1.0.0` when a semantic projection is available. |
| `baseline_fingerprint` / `current_fingerprint` | Nullable lowercase semantic SHA-256 values; complete for SKIP/REGENERATE. |
| `contract_config_schema_version` | Version of the canonical config projection. |
| `baseline_contract_config_digest` / `current_contract_config_digest` | Nullable lowercase SHA-256 config values; complete for SKIP/REGENERATE. |
| `created_at` | Receipt time only; excluded from meaning and fingerprint rules. |

The table must enforce:

1. `uq_generation_change_assessments_source_version` on
   `(slack_work_item_id, current_source_version)`; this is the assessment identity.
2. Unique non-null `selected_generation_job_id`; one generated root is selected by at most one
   assessment. `reused_generation_job_id` is intentionally repeatable across skipped versions.
3. A predecessor self-FK belongs to the same work item, has
   `current_source_version = baseline_source_version`, and is the greatest completed assessment
   version less than the current version. Initial shape has no predecessor/baseline; legacy review
   may have a baseline version/job but no predecessor assessment.
4. Skip shape has complete equal semantic/config digests, selected null, reused equal to the
   materialization baseline, and `changed_fields = []`.
5. Regenerate shape has a supported unequal semantic or config digest, selected non-null, reused
   null, and non-empty non-reserved changed fields.
6. Review shape has selected/reused null, preserves the last materialization baseline when one
   exists, and uses `changed_fields = ["$REVIEW_REQUIRED"]`; unavailable digests remain null.
7. Cross-table deferred guards validate that materialization/reused/selected jobs belong to the
   same work item and are `AUTOMATIC` roots. A selected job matches `current_source_version` and
   remains sealed `QUEUED`; a baseline is strictly older. Manual roots and retry children fail.
8. `UPDATE` and `DELETE` are rejected. Foreign keys use `RESTRICT`; no job, assessment, context,
   or source cleanup can erase the ledger.

The ledger deliberately has no delivery, Slack event, outbox event, projection checkpoint,
background-job, job-attempt, or audit ID column. Those IDs may point to an assessment from their
own transport receipts but cannot select, compare, or duplicate one.

## Transaction, receipt, concurrency, and replay

For a current projected source, one PostgreSQL transaction performs:

1. Lock the canonical `SlackWorkItem` and revalidate quiet/parse/projection currency.
2. If an assessment already exists for `(work item, source version)`, validate its shape and jobs
   and return it without reading live sources or appending rows. This exact replay remains valid after
   the work item later advances.
3. Select the immediately prior assessment and independently validate its carried canonical
   automatic materialization baseline.
4. Build the current context once, derive/compare semantic and config projections, and classify
   the outcome. Unknown/ambiguous/retracted inputs produce `REVIEW_REQUIRED` before job creation.
5. For generating outcomes, seal the new T02-compatible `AUTOMATIC` root from that same built
   context. For `SKIP`, create no generation row and reuse the materialization baseline. For
   `REVIEW_REQUIRED`, create or reuse no job.
6. Insert the assessment, existing creation audit when a job was created, and the projection
   receipt, then commit together.

`KnowledgeProjectionEvent.result` may cache `generationChangeAssessmentId`, `generationOutcome`,
and the selected-or-reused `generationJobId`. Receipt validation must resolve through the impact
ledger; a `SKIP` receipt legitimately names an older source-version job, while review has no job
receipt. The checkpoint ID remains
an exact-delivery optimization and is not part of decision uniqueness or fingerprinting.

Concurrent exact or distinct transport attempts serialize on the work-item row and converge on
one assessment. A uniqueness conflict is re-read and validated; it is never answered by allocating a
new generation version. Any fault before commit rolls back projection changes, assessment, job,
context, input, audit, and receipt. A clean retry may then commit the same outcome; a fault after a
winning commit replays the winner.

## Stale versions

- If a committed assessment exists for the requested historical version, exact replay returns that
  immutable assessment even after `SlackWorkItem.source_version` advances and writes nothing.
- If no assessment exists and the requested version is no longer current, processing is a terminal
  stale no-op. It creates no projection, assessment, generation job, audit, or receipt repair.
- A later current version never fills a skipped or stale historical gap retroactively.

## Deleted and superseded evidence

Only currently effective grounded meaning participates:

- deleting/retracting the source for a previously selected claim is not automatically safe. The
  assessment is `REVIEW_REQUIRED / DELETED_OR_RETRACTED_EVIDENCE`, reports
  `$REVIEW_REQUIRED`, and creates no job;
- an explicit `SUPERSEDES` transition removes the predecessor claim and adds the active successor;
  when both citations and the transition validate, the resulting decision/constraint/requirement
  and evidence changes are a deterministic `REGENERATE`;
- provider omission is not proof of deletion or supersession. A previously active slot that
  disappears without an explicit validated transition is `REVIEW_REQUIRED / UNKNOWN_EFFECTIVE_STATE`;
- an `AMBIGUOUS` current slot is `REVIEW_REQUIRED / AMBIGUOUS_KNOWLEDGE`;
- deleting or superseding an already excluded/unselected item can be `SKIP` only when the
  effective-message reducer proves it is transport-only and both projections remain equal;
- a tombstone's revision UUID/timestamp or synthetic deletion text is never hashed as a substitute
  for the effective-state change.

## Explicit exclusions

- T04 `generation_managed`, human-edit detection, overwrite protection, or ownership transfer.
- Existing-case overwrite, candidate suggestion, apply planning/apply, or TestCase ledger writes.
- Generation provider selection/execution, retries caused by provider failure, results, or cost.
- Slack response/outbox posting.
- Semantic paraphrase recognition by an LLM or embedding threshold.

## Focused release verification

The PostgreSQL contracts are in
`apps/api/tests/test_phase_e_automatic_generation_impact_postgresql.py`. They cover schema and
immutability, initial generation, deterministic skip, relevant regeneration, canonical
predecessor selection, exact/distinct-delivery replay, concurrent handling, rollback/retry, stale
versions, deleted/superseded evidence, and legacy conservative review.

Focused command:

```text
TC_TEST_DATABASE_URL=postgresql+psycopg://.../tc_pe_se2_t03_test \
  node scripts/python.mjs -m pytest \
  apps/api/tests/test_phase_e_automatic_generation_impact_postgresql.py \
  -m postgres -q
```

At the T03 production head this suite passes 16 PostgreSQL 16 contracts. The release gate also
reruns the preceding T01/T02 16-test suite, the 57-test migration/repository suite, the complete
non-PostgreSQL Python unit suite, Ruff, and strict mypy. Exact counts and the deliberately open
T04 boundary are recorded in the
[T03 production report](./pe-se2-t03-production-implementation-report.md).
