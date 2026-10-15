# Automatic generation provider runbook

The T07 worker provider is disabled by default. A disabled worker still claims a scheduled
automatic-generation execution and records the safe terminal
`GENERATION_CONFIG_INVALID`/setup-required state, but it constructs no HTTP transport and makes
no provider request.

## Enablement

Inject every value from the deployment secret/config store before enabling the worker:

```dotenv
# Independent local knowledge projection; false by default.
TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED=true
TC_OLLAMA_BASE_URL=http://ollama:11434
TC_GENERATION_MODEL=qwen3:8b
TC_EMBEDDING_MODEL=bge-m3
TC_KNOWLEDGE_EXTRACTION_TIMEOUT_SECONDS=60
TC_KNOWLEDGE_EXTRACTION_RESPONSE_MAX_BYTES=1048576

# Independent credentialed generation execution; false by default.
TC_AUTOMATIC_GENERATION_ENABLED=true
TC_AUTOMATIC_GENERATION_PROVIDER_ID=approved-provider
TC_AUTOMATIC_GENERATION_PROVIDER_ENDPOINT=https://generation.example.com/v1/generate
TC_AUTOMATIC_GENERATION_PROVIDER_MODEL_ID=approved-model-version
TC_AUTOMATIC_GENERATION_PROVIDER_AUTH_TOKEN=<secret-store reference>
TC_AUTOMATIC_GENERATION_PROVIDER_AUTH_HEADER=Authorization
TC_AUTOMATIC_GENERATION_PROVIDER_AUTH_SCHEME=Bearer
TC_AUTOMATIC_GENERATION_PROVIDER_TIMEOUT_SECONDS=60
TC_AUTOMATIC_GENERATION_PROVIDER_RESPONSE_MAX_BYTES=1048576
```

Knowledge projection, credentialed generation execution and Slack generation notifications are
three separate switches. Enabling one does not enable another. When
`TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED=false`, the worker does not construct either Ollama
transport; partial manual injection of only extraction or embedding is rejected. The structured
extraction response is bounded to 4 MiB, grounded against immutable Slack revisions, and lifecycle
classification remains deterministic application logic.

`PROVIDER_ID` and `MODEL_ID` are server-owned safe identifiers. The endpoint must be an HTTP(S)
URL without username/password userinfo. The auth header name/scheme must not contain newline
characters, and the response limit must stay between 1 byte and 4 MiB. Startup fails before the
worker loop if enablement is incomplete or the endpoint/header/timeout/byte limit is invalid.
Credentials, endpoint text, provider response bodies, and transport exceptions must not be
written to logs or returned by the failure API.

The production composition forces the HTTP adapter to `max_attempts=1`. T07's committed provider
permit and observation ledger owns the three-call default budget; changing an adapter retry value
would multiply external calls and is prohibited.

## Pre-enable gate

1. Keep `TC_AUTOMATIC_GENERATION_ENABLED=false` while applying migration `20260828_0026` and
   keep `TC_AUTOMATIC_KNOWLEDGE_PROJECTION_ENABLED=false` while verifying the worker starts without
   provider network traffic.
2. Verify the endpoint hostname and path against the approved deployment allowlist and confirm it
   contains no query credential or URL userinfo.
3. Load the token from the secret store, enable one canary worker, and confirm one durable permit
   maps to one outbound provider request and one sanitized observation.
4. Confirm provider timeout is shorter than the surrounding infrastructure timeout. The worker
   maintains both its generic background-job heartbeat and the T07 domain lease during the call.
5. Confirm `PROVIDER_OUTCOME_UNKNOWN` alerts page immediately. A committed permit without a safe
   final observation is never resent blindly; operator review is required.
6. Verify successful output is validated before `SUCCEEDED`, then claimed by the separate
   materialization job. Released/human-owned targets must remain suggestion-only.

The canary/provider credential call, long-duration process-kill/DB-failover rehearsal, and real
Slack delivery remain live gates; local tests use only scripted transports and disposable
PostgreSQL.

The deterministic root-to-reply gate and exact replay cardinalities are specified in
[`PE-SE2-T10`](../../docs/phase-e/automatic-generation-root-to-slack-e2e-contract.md). It is not a
substitute for an approved Slack/Ollama/generation-provider credentialed smoke.

## T09 version-regression release

Run `pnpm generation:version-regression:eval` only with an immutable snapshot and expected SHA-256. Candidate/synthetic reports are never production baselines. Production activation requires the protected workflow, two independent approvers, manifest digest, reviewed cohort and provider live attestation; missing provenance fails closed. Rollback selects the prior immutable version-set and recorded artifact digest.

## Disable, rotate, and recover

- Kill switch: set `TC_AUTOMATIC_GENERATION_ENABLED=false` and restart workers. This prevents new
  provider transports from being constructed; queued jobs close with setup-required semantics.
- Rotation: issue the replacement token, restart canary workers with the new secret, verify a
  single permit/observation, then restart the remaining workers and revoke the old token. Never
  print either token during verification.
- Ambiguous outcome: do not replay the job or edit permit/observation rows. Inspect the safe
  failure history and provider-side idempotency/request digest evidence, then use the operator
  review flow. Raw result bodies or credentials must not be pasted into retry conditions.
- Rollback: disable the provider first. Do not downgrade `0026` while T07 workers are running or
  while execution/materialization schedules are active.
