# PE-SE2-T10 production implementation report

## Result

The production worker now composes the previously absent automatic knowledge projection consumer
behind an explicit, default-off switch. A bounded structured Ollama extraction adapter, the
existing BGE-M3 adapter and the production `KnowledgeOutboxConsumer` own the quiet/parse to
ontology handoff. Slack's canonical root permalink is resolved through `chat.getPermalink` before
the ledger transaction, propagated through thread snapshots and sealed into generation input.

The deterministic disposable-PostgreSQL E2E sends a signed root and reply through the production
HTTP ingress and production worker dispatcher. It proves one complete five-case `ACTIVE`/
`SLACK_AI` materialization and one original-thread reply, including an accepted write whose
acknowledgement is lost and recovered by reconciliation. Exact signed-event, projection and
notification replays do not add any work item, source revision, knowledge, generation, case or
notification row; a signed bot self-reply is ignored. Thread sync and notification delivery also
recheck revoked private-channel access before network activity.

## Evidence

- Focused adapter/composition units: `38 passed`.
- T10 plus explicit ack-loss/self-reply/stale-source regressions: `4 passed`.
- Private-access pre-network regressions: `2 passed`.
- T01–T10 plus Slack repository PostgreSQL suite: `97 passed in 110.11s`.
- Python non-PostgreSQL suite: `370 passed`, `106 deselected`.
- Full Python Ruff and strict mypy: clean (`126 source files` type-checked).

The exact executable matrix and cardinalities are in
[the T10 contract](./automatic-generation-root-to-slack-e2e-contract.md).

## Remaining live gate

No real Slack, Ollama or credentialed generation endpoint was called. Production rollout still
requires approved external credentials, an allowlisted real channel, model availability, the
protected T08/T09 operational attestation/baseline, and observation of one signed real root/thread
ending in one reconciled or acknowledged reply. T11's full human-modified suggestion path is now
closed by its [separate production E2E](./automatic-generation-human-release-to-suggestion-e2e-contract.md);
this T10 implementation remains the ordinary-batch prerequisite and does not weaken the
suggestion-only protection.

The environment allowlist is not a database seed; rollout must reconcile the authoritative DB
allowlist through its API. Historical raw Slack payload minimization/retention migration remains a
Phase F privacy boundary because changing the existing append-only source ledger is larger than
this production E2E.
