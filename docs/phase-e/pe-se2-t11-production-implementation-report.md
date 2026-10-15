# PE-SE2-T11 production implementation report

## Result

T11 reuses the accepted production seams without adding a parallel domain implementation. A new
signed fixture and disposable-PostgreSQL E2E start with T10's ordinary five-case automatic batch,
release one generated case through the public semantic PATCH, submit a signed root
`message_changed` event, and drain the production impact, execution, suggestion materialization
and Slack delivery handlers.

The resulting automatic job targets only the released lineage and stores one immutable `OPEN`
suggestion. The human-owned case, its current/version history, ordered steps, labels, parent,
sort/status and immutable generation-release receipt do not change; no second generation apply or
apply outcome is created. The terminal Slack reply is stored and posted to the original root
thread with suggestion count 1 and automatic overwrite count 0.

## Executable evidence

- T11 signed production E2E: `1 passed in 3.80s` on the dedicated verification database.
- T04/T05/T10/T11 protection matrix: `25 passed in 32.58s` on the same dedicated database.
- T05 focused PostgreSQL, including the persistent apply-versus-ignore race:
  `11 passed in 15.97s`; the race alone also passed 10 consecutive runs.
- The earlier pre-strengthening T01–T11 automatic generation, generation apply and Slack ingress
  PostgreSQL matrix was `86 passed in 71.24s`; the current targeted matrix above is the evidence
  rerun for this verification-asset change.
- Python non-PostgreSQL regression: `370 passed`, `107 deselected`.
- Full Python Ruff, strict mypy (`126 source files`), 10 JSON Schemas, 17 ADRs and generated
  OpenAPI/client drift checks: clean.
- The E2E terminal cardinality is 3 events, 3 revisions, 2 projection/assessment/job/execution/
  provider/materialization-schedule chains, 1 original apply with 5 outcomes, 5 cases with 6
  versions, 1 target/suggestion/materialization, 0 resolutions, 6 management events and 2 Slack
  intents/receipts.
- Duplicate signed source, projection delivery, execution enqueue, materialization delivery and
  notification delivery preserve every immutable cardinality; provider execution stays at 2 and
  Slack posting stays at 2.

The new persistent race starts two independent production API writers from one barrier. It
requires one `200` winner, one `409 NOT_OPEN` loser and exactly one immutable resolution. An apply
winner advances the case/resource version and case version/audit/outbox cardinalities exactly
once; an ignore winner leaves every case cardinality unchanged. Exact replay and a fresh opposite
decision leave the winning response, case state and single resolution unchanged.

The exact transition and inherited protection matrix are defined in
[the T11 contract](./automatic-generation-human-release-to-suggestion-e2e-contract.md).

## Production and network boundary

No production runtime switch or provider fallback changed. The test turns on the three existing
default-off capabilities only in an injected local `Settings` instance and supplies deterministic
extraction, embedding, generation and Slack transports, so external network calls are zero. A
terminal execution replay is checked at the queue idempotency seam; terminal execution schedules
remain fail-closed rather than being made claimable again.

## Completion boundary

The local code/contract gate is sufficient to complete `PE-SE2-T11` and the Phase E condition
that a human-modified generated case is never automatically overwritten. It is not a real-sample
quality or credential certification. The T08/T09 approved operational golden, two-person
approval, credentialed provider live attestation, protected runner and artifact-retention gate
remain open, so Phase E itself remains open.
