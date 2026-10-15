# PE-SE2-T11 human-release-to-suggestion production E2E contract

## Scope and executable authority

`PE-SE2-T11` closes the local production regression from a T10-created
`ACTIVE`/`SLACK_AI` generation-managed case through a human semantic edit, a signed Slack
thread meaning change, impact `REGENERATE`, sealed automatic suggestion execution,
suggestion-only materialization and one terminal reply in the original Slack root thread.
The executable authority is
[`test_phase_e_human_release_to_suggestion_e2e_postgresql.py`](../../apps/api/tests/test_phase_e_human_release_to_suggestion_e2e_postgresql.py)
using
[`signed-human-release-v1.json`](../../evals/fixtures/automatic-generation-root-to-slack-e2e/signed-human-release-v1.json).
It runs against one disposable PostgreSQL database with the production FastAPI application,
production worker dispatcher and production repositories/services. Knowledge extraction,
embedding, generation execution and Slack Web API are deterministic injected adapters, so the
test makes no external network request.

No new domain write path is introduced for T11. It composes the already-accepted T03 impact,
T04 one-way release, T05 immutable suggestion, T06 Slack notification, T07 execution history and
T10 signed ingress/production-worker seams.

## Required state transition

| Stage | Production stimulus | Required state and invariant |
|---|---|---|
| Initial T10 batch | Send the fixture's signed root and reply, advance only the persisted quiet deadline, and drain the production worker | One successful ordinary automatic root creates one apply, five outcomes and five `ACTIVE` cases whose current versions are `SLACK_AI`; every created case is generation-managed and one `SUCCESS_APPLIED` terminal reply exists in the fixture root thread. |
| Human authority transfer | `PATCH /api/test-cases/{caseId}` with a semantic priority change and a human audit actor | The selected case advances from version/resource 1 to 2, `generation_managed` changes exactly once from true to false, and its immutable lineage, created outcome, generated baseline/latest pointers and one `RELEASED` management event are retained. |
| Source meaning change | Send the fixture's signed root `message_changed` event and advance the new persisted quiet deadline | The canonical Slack work item advances from source version 2 to 3; the second append-only assessment is `REGENERATE`, selects generation version 2 and does not alter any test-case row. |
| Sealed suggestion execution | Drain production projection, execution and materialization handlers | The new root/input/output schema is `generation-suggestion/1.0.0`; exactly one opaque target is sealed for the released lineage and the four still-managed sibling cases are not targets. The provider runner receives only the seal and returns one matching candidate. |
| No automatic overwrite | Materialize the successful suggestion job | `generation_applies` remains 1 and apply outcomes remain 5; the protected case's current version, version history, steps, version labels, work-item/category parent, sort order, status and every release field are byte-for-byte unchanged. One immutable `OPEN` suggestion and one materialization receipt exist with candidate 1, suggestion 1, no-change 0 and automatic-overwrite 0. |
| API read boundary | Read the case suggestion list and suggestion detail, then request the ordinary whole-batch apply plan | List and detail return the same sealed baseline/proposal/diff/provenance and `OPEN` state; the baseline pins human version/resource 2. Ordinary apply-plan access fails with `GENERATION_SUGGESTION_CONFLICT` and `SUGGESTION_JOB_NOT_WHOLE_BATCH_APPLICABLE`. |
| Slack terminal outcome | Deliver the materialization-captured notification | Exactly one `SUCCESS_SUGGESTIONS` intent/receipt adds exactly one second bot reply to the same root thread; its frozen Korean message reports one suggestion and zero automatic overwrites. T10's first accepted-but-unacknowledged post is reconciled rather than duplicated. |

The terminal immutable cardinality is fixed at 3 Slack events, 3 source revisions, 2
projection receipts, 2 change assessments, 2 generation jobs/execution schedules/provider
permits/provider observations/materialization schedules, 1 generation apply, 5 apply outcomes,
5 cases, 6 case versions, 1 suggestion target, 1 suggestion, 1 suggestion materialization,
0 suggestion resolutions, 6 management events and 2 notification intents/receipts.

## Replay contract

The test re-submits the signed semantic-change event and requires
`DUPLICATE_EVENT_ID`. It then redelivers the completed current-source automatic projection job,
re-enqueues the generation execution delivery under its original idempotency key, and redelivers
the completed materialization and notification deliveries. All immutable cardinalities remain
identical, the generation runner remains at two calls, `chat.postMessage` remains at two calls,
and the protected case response remains byte-for-byte identical to the human-release response.

The execution delivery is deliberately verified at the durable queue idempotency seam rather
than by changing a terminal execution schedule back into a claimable state. Terminal schedules
fail closed; the queue's unique idempotency key returns the already-succeeded delivery and does
not call the provider again.

## T04/T05 protection regression matrix

The single T11 E2E owns the production cross-seam happy path and replay proof. The deeper
adversarial state matrix remains executable authority of the existing PostgreSQL suites and must
run with T11:

- T04 semantic patch/replay and fault rollback:
  `test_pe_se2_t04_human_semantic_patch_releases_atomically_and_replays_once` and
  `test_pe_se2_t04_release_fault_rolls_back_then_clean_retry_commits_once`;
- T04 concurrency and no reacquisition:
  `test_pe_se2_t04_concurrent_human_and_reused_apply_linearize_without_overwrite` and
  `test_pe_se2_t04_released_exact_reuse_never_overwrites_or_reacquires`;
- T05 materialization rollback, apply/ignore and stale/delete guards:
  `test_suggestion_materialization_intent_and_delivery_job_rollback_together`,
  `test_pe_se2_t05_apply_is_atomic_released_forever_and_idempotent`,
  `test_pe_se2_t05_ignore_appends_once_and_never_mutates_case`,
  `test_pe_se2_t05_concurrent_apply_and_ignore_have_one_terminal_winner`,
  `test_pe_se2_t05_diff_snapshot_is_immutable_and_stale_apply_fails_closed`, and
  `test_pe_se2_t05_deleted_then_restored_case_never_rebases_open_suggestion`;
- T05 supersession and public-spoof guards:
  `test_pe_se2_t05_multiple_suggestions_use_immutable_supersession_edge` and
  `test_pe_se2_t05_public_source_spoof_and_mutation_routes_fail_closed`.

Passing only the T11 happy path does not replace these guards. Conversely, direct repository
fixtures in T04/T05 do not replace T11's signed ingress and production worker proof.
The four executable T04/T05/T10/T11 PostgreSQL files currently collect 25 tests, including the
real two-writer decision race; the T11 E2E remains one separately runnable test whose terminal
state intentionally has an OPEN suggestion and zero resolutions.

## Default safety and live boundary

All external-capable paths remain separately default-off. The E2E enables knowledge projection,
generation and Slack notifications only in its local `Settings` object and injects all four
provider boundaries explicitly. Production defaults construct no credentialed generation
transport and make zero network calls; T11 adds no implicit enablement or credential lookup.

This contract closes the roadmap condition that a human-modified generated case is not
automatically overwritten. It does not certify a real provider or Slack workspace and does not
close the T08/T09 operational gate: the approved `REVIEWED` 32-root golden, two-person approval
digest, credentialed provider live attestation, protected runner and retained artifacts remain
required before the Phase E real-sample quality condition can close.
