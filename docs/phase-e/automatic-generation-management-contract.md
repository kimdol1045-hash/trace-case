# PE-SE2-T04 generated case management contract

## Status and scope

This document is the pre-implementation, RED contract for `PE-SE2-T04`. It composes the
released T01-T03 automatic-generation chain with the released generation apply and immutable
TestCase version ledgers. T04 owns only generated-case management eligibility, human release,
and the guard that prevents automatic overwrite or reacquisition after release.

The protected invariant is:

```text
generation_managed = true
    only while the current case is still owned by one proven CREATED generation lineage

human semantic commit
    => append the intended TestCaseVersion when applicable
    + set generation_managed = false
    + seal the release receipt
    in one transaction

released once
    => never managed again and never automatically overwritten
```

T04 does not add generation suggestion/diff/apply/ignore API or UI, execute a generation
provider, persist a provider result, or post a Slack reply. Those remain T05, the provider
execution task, and T06 respectively.

## Existing authorities and current gaps

T04 introduces no alternate TestCase, job, assessment, or apply identity. The existing
authorities are:

| Concern | Existing authority |
|---|---|
| TestCase identity and optimistic clock | `test_cases.id`, `current_version_id`, and `version` |
| Immutable semantic history | `test_case_versions`, `test_steps`, and version tags |
| Generated source snapshot | `test_case_versions.source_type = SLACK_AI` plus sealed `source_metadata` |
| Materialization proof | immutable `generation_applies` and `generation_apply_outcomes` |
| CREATED versus REUSED | `generation_apply_outcomes.outcome` and `preserved` |
| Generation source lineage | the outcome's apply/job, the job's Slack work item, and its sealed context/result |
| Automatic source evolution | T02 automatic roots and the T03 `generation_change_assessments` chain |
| Human actor and request replay | server-built `AuditContext` and the API idempotency ledger |

At the T03 head, `TestCase` has no `generation_managed` state or durable release receipt.
`CaseRepository.update_case` always appends a version, including a semantic no-op;
`restore_version` appends a version; and move, reorder, soft-delete, and deleted-case restore
change only the aggregate resource version. Generation apply creates `SLACK_AI` cases for
`CREATED` outcomes and writes no case/version mutation for `REUSED`. No current production path
automatically updates an existing case. T04 must preserve that closed-world safety while adding
the management state and guards below.

## Management eligibility and immutable lineage

`source_type = SLACK_AI`, caller-supplied source metadata, an automatic generation job, a T03
assessment, an exact-content match, or an AI-inbox parent is not independently sufficient to
make a case managed.

A case may enter `generation_managed = true` exactly once, in the same transaction in which
`GenerationApplicationService` commits a `generation_apply_outcomes` row satisfying all of:

1. `outcome = CREATED` and `preserved = false`;
2. the outcome's `test_case_id` is the newly inserted case;
3. the outcome's `test_case_version_id` is that case's initial/current version;
4. that version is `SLACK_AI` and its trusted `generationJobId`, context digest, result digest,
   source revisions, and evidence resolve to the outcome's immutable apply/job seals; and
5. no release receipt exists for the case or lineage.

The job may have been initiated manually or automatically; the authority is the trusted
generation application transaction and its `CREATED` outcome, not a client-controlled trigger
label. A direct repository/API creation with `SLACK_AI` metadata remains unmanaged.

Every ever-managed case receives a server-generated opaque `generation_lineage_id` and retains:

- the unique CREATED outcome that established the lineage;
- the initial `baseline_generated_version_id`;
- the most recent generated version in that same lineage; and
- the Slack work item/job chain resolved through the immutable apply/job/assessment ledgers.

The baseline never changes. A later proven system update may advance the latest generated
version, but each such version points to its predecessor in the same lineage. A copied case starts
an independent unmanaged case; copied source provenance cannot transfer management.

`REUSED` is a relationship receipt only. Applying an exact or semantic `REUSED` outcome writes no
case/version state and never promotes an unmanaged case. A case already managed by an earlier
CREATED lineage remains as it was when a later apply merely reuses it; REUSED is neither a new
lineage nor an update authority.

## Durable state and release receipt

The aggregate must expose and persist this logical shape. Implementations may normalize actor
fields, but may not weaken or omit them:

| Field | Contract |
|---|---|
| `generation_managed` | Required boolean, false by default. It has only `false -> true` at valid CREATED initialization and `true -> false` at release. |
| `generation_lineage_id` | Nullable unique UUID; present for every ever-managed case and immutable. |
| `generation_created_outcome_id` | Nullable unique FK to the establishing CREATED outcome; immutable. |
| `generation_baseline_version_id` | Nullable same-case FK to the initial generated version; immutable. |
| `generation_latest_version_id` | Nullable same-case FK to the latest proven generated version; may advance only while managed and only within the lineage. |
| `generation_released_at` | Nullable UTC transaction time; set once when management is released. |
| `generation_release_reason` | Nullable non-blank user/change reason, at most 500 characters; set with release. |
| `generation_release_actor` | Nullable object containing authoritative audit source plus available actor label/external ID; caller source metadata is not authoritative. |
| `generation_release_mutation` | Nullable allowlisted mutation kind that caused release. |
| `generation_released_from_version_id` | Nullable generated version that was current immediately before release. |
| `generation_released_by_version_id` | Nullable human version committed by content/status/version-restore release; null for aggregate-only lifecycle/parent moves. |

Unmanaged-never-generated cases have no lineage or release fields. Managed cases have complete
lineage and no release fields. Released cases have complete lineage and complete release fields,
except that `generation_released_by_version_id` is null for an aggregate-only release. Partial or
contradictory shapes fail at the database boundary.

Release data is write-once. It cannot be cleared, rewritten, or replaced by application code or
raw SQL. The same transaction also appends a `GENERATION_MANAGEMENT_RELEASED` audit whose before
and after snapshots include the complete lineage/release shape. Existing apply outcome FKs remain
`RESTRICT`; permanent deletion must return the normal referenced-history conflict rather than
erase generated provenance or surface an untyped database error.

## Authoritative mutation provenance

All current public TestCase mutation endpoints are human-authority boundaries, including calls
whose JSON `source.type` says `WORKER`, `SYSTEM`, or `SLACK`. That JSON describes requested version
source metadata; it is not an authorization capability. `X-Actor-*` headers supply attributable
labels to the server-built API audit context but also cannot confer system authority.

An automatic/system update, if introduced at an internal boundary, is allowed to keep a case
managed only when the same locked transaction proves all of:

1. the mutation was invoked through a non-public server capability and its audit source is
   `SYSTEM` or `WORKER`;
2. the target is active, non-deleted, currently managed, and has no release receipt;
3. the request pins the aggregate resource version and current version ID/number;
4. it cites the immutable `generation_lineage_id` and establishing CREATED outcome;
5. its generation job belongs to the same Slack work item and is the selected job (or a retry in
   the selected root lineage) of a valid T03 assessment descended from the lineage's prior
   materialization;
6. the generated candidate is explicitly mapped to this lineage by server-held state; index,
   summary, exact-content key, semantic similarity, or REUSED status alone is not proof; and
7. the new `SLACK_AI` version records job, assessment, predecessor generated version, context, and
   result seals that match the cited rows.

There is currently no automatic existing-case update action in generation apply. Absence remains
safe. T04 must not add a public override switch or treat a public PATCH as the system path. Any
future update seam fails closed with a typed management conflict when a proof above is absent,
stale, cross-work-item, corrupt, deleted, or released.

After release, neither a new automatic job/assessment, generation apply CREATED/REUSED receipt,
retry, exact match, nor a direct internal update may modify the current version or set
`generation_managed = true`. T05 may later store a suggestion, but T04 itself performs no
alternative write.

## Semantic change and no-op boundary

Human mutation is classified against the locked current state after canonical validation. The
following are semantic and release a managed case:

- summary, status, priority, owner/assignee, or label-set change;
- aggregate precondition or expected-result mode/content change;
- step precondition, action, or expected-result change;
- step addition, deletion, or order change;
- moving the case to a different parent work item (including the implied category change);
- restoring an older TestCaseVersion, even if normalized content happens to equal the current
  content, because the explicit restore lineage is itself meaningful; and
- human soft-delete. This records the user's instruction not to let automation revive the case.

Canonical equality uses persisted normalized content: trimmed/NFKC-normalized text where the
ledger already normalizes it, enum values, normalized label set, and ordered steps. A full PATCH
whose semantic values equal the current version is a no-op: it appends no version, changes no
aggregate version/timestamp, writes no audit/outbox row, and does not release management. A
different `changeReason`, source metadata object, actor header, or idempotency key alone is not a
semantic change.

Reordering a case within the same parent work item is presentation order, not generated test
meaning. It may advance the aggregate resource version and emit the existing REORDER audit/outbox
event, but it does not append a TestCaseVersion or release management. An exact same-parent/same-
position move is a complete no-op. Moving to another parent releases management atomically with
the MOVE resource mutation.

## Delete and restore semantics

- Human soft-delete releases a managed case in the same aggregate transaction. The immutable
  generated version remains current, `deleted_at` is set, and no new content version is created.
- Automatic apply/update/reuse selection must exclude soft-deleted cases. It cannot restore,
  overwrite, re-manage, or reuse one through a stale plan.
- Deleted-case restore is human authority. A normally deleted generated case is already released
  and stays released. A conservative legacy managed+deleted anomaly is released before becoming
  active; it is never revived as managed.
- Restoring an older content version appends the ordinary immutable restore version and releases
  in the same transaction. `restored_from_version_id`, generated baseline, released-from version,
  and human released-by version are all retained.
- Permanent delete remains subject to all immutable cycle, issue, copy, generation apply, and
  management lineage references. A referenced generated or REUSED case is rejected with a stable
  conflict; T04 does not cascade or erase those rows.

## Ordering of checks, replay, concurrency, and rollback

### Expected version and no-op

Optimistic expected versions are checked before no-op classification. A stale request is `409
VERSION_CONFLICT` even if its submitted content now equals the winner. It creates no release,
version, audit, outbox, or resource mutation. The caller must reload and deliberately retry.

### Idempotency

An exact idempotency replay returns the original response and performs no second semantic
classification, version, release, audit, or outbox write. Reusing the key with a different request
remains the existing idempotency conflict. A replay of the original generation apply also returns
the original CREATED/REUSED receipt and cannot initialize or release management twice.

### Concurrent human and automatic writers

Both paths lock the same TestCase aggregate and validate the same resource/current-version clock.
Only one can commit for one expected version:

- if the human semantic transaction commits first, it appends the human version/release and the
  automatic writer then observes released or stale state and performs no case write;
- if the proven automatic same-lineage transaction commits first, the human request is stale and
  performs no release; after reload, a deliberate human retry commits the release; and
- no unique-conflict or retry path may reclassify a released case as managed.

The linearization rule avoids a half-state. It does not claim that an uncommitted human intent
preempts a system transaction that already won the lock.

### Rollback

CREATED case/version/apply outcome, lineage initialization, audit, outbox, and idempotency receipt
commit or roll back together. For human content/restore, the new version, tags/steps, current
version pointer, release fields, release audit, normal case audit, outbox, and idempotency receipt
also commit or roll back together. Aggregate-only move/delete releases have the same atomicity.
A fault at any pre-commit stage leaves the prior management and current-version state unchanged;
a clean retry may commit exactly once.

## Legacy and migration policy

Migration is fail-closed and must never infer management from `SLACK_AI`, source metadata, AI
folder placement, or case-key timing alone.

- A legacy case may be backfilled managed only when exactly one valid immutable CREATED outcome
  proves the case and initial version, all source/apply/job seals agree, the created version is
  still current, the case is not deleted, and no later semantic/lifecycle mutation exists.
- A legacy `SLACK_AI` case without that proof remains unmanaged with no fabricated lineage.
- A REUSED outcome never qualifies, including exact reuse of a human case.
- A proven CREATED case that has a later version, move to another parent, soft-delete, restore, or
  ambiguous history is backfilled released, not managed. Its release uses
  `LEGACY_HUMAN_OR_UNKNOWN_DIVERGENCE`, actor source `SYSTEM` with an explicit migration actor, the
  earliest defensible release time, and the proven baseline lineage. If even the baseline cannot
  be proven, it stays unmanaged without a fabricated lineage.
- A soft-deleted legacy case is never backfilled managed.
- Historical collisions or contradictory CREATED links abort migration for explicit repair; the
  migration never chooses an arbitrary winner.

## API read contract and stable conflicts

TestCase detail and every mutation response containing a TestCase expose
`generationManagement`:

```json
{
  "managed": false,
  "lineageId": "generation_lineage_...",
  "createdOutcomeId": "generation_apply_outcome_...",
  "baselineGeneratedVersionId": "case_version_...",
  "latestGeneratedVersionId": "case_version_...",
  "releasedAt": "2026-08-27T00:00:00Z",
  "releaseReason": "QA corrected the expected result.",
  "releaseActor": {
    "type": "API",
    "actorLabel": "QA Kim",
    "externalActorId": "qa-42"
  },
  "releaseMutation": "CONTENT_VERSION",
  "releasedFromVersionId": "case_version_...",
  "releasedByVersionId": "case_version_..."
}
```

Never-managed resources return only `{"managed": false}`; nullable lineage/release members are
omitted. Managed resources return lineage members with release members omitted. Released resources
return both. List projections may expose at least `generationManaged`, but T04 does not add
management filters or UI.

Existing `VERSION_CONFLICT`, deleted-resource, restore, idempotency, and permanent-delete conflicts
remain authoritative. An internal automatic update rejected by this contract uses a stable
`GENERATION_MANAGEMENT_CONFLICT` with a non-secret reason code such as `NOT_MANAGED`, `RELEASED`,
`LINEAGE_MISMATCH`, `PROVENANCE_UNPROVEN`, `STALE_VERSION`, or `CASE_DELETED`. It never falls back
to create, restore, REUSED promotion, or silent overwrite.

## Focused RED verification

`apps/api/tests/test_phase_e_generation_management_postgresql.py` is the implementation gate. It
must cover:

1. PostgreSQL columns, immutable lineage/release shape, CREATED-only cross-table validation, and
   one-way release enforcement;
2. generation apply CREATED initialization versus exact human REUSED preservation;
3. public semantic PATCH release with durable actor/reason/baseline and spoofed WORKER source;
4. no-op PATCH, same-parent reorder, expected-version ordering, and idempotency replay;
5. parent move, version restore, soft-delete/deleted restore, and permanent-reference behavior;
6. legacy `SLACK_AI`, divergent CREATED, exact REUSED, and soft-deleted backfill policy;
7. concurrent human/system serialization and post-release automatic overwrite/reacquisition
   denial; and
8. injected rollback followed by one clean release.

Before production implementation these focused tests are intentionally RED because the T03 schema
has no management fields/guards and API responses contain no `generationManagement` object.

## Explicit exclusions

- T05 suggestion persistence, suggestion diff, apply/ignore endpoints, and UI.
- Generation provider selection, credentials, execution, retry, result production, and cost.
- Slack success/failure reply and delivery idempotency.
- A public force-manage, reclaim, automatic-update, or release endpoint.
- Content similarity as lineage authority.
- Changes to cycle-pinned historical versions; cycle snapshots remain immutable and independent of
  current management state.
