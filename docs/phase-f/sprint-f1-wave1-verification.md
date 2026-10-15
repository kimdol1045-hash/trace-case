# Phase F Sprint F1 wave 1 integration verification

- Verification date: 2026-09-01 (Asia/Seoul)
- Scope: `PF-SF1-T01`–`T10` and `PF-SF1-T15` wave-1 code/configuration integration
- Verdict: **CODE FOUNDATION VERIFIED; EXTERNAL RELEASE GATES REMAIN OPEN**

This integration reviewed the final repository code and all files identified or referenced by the
edge/security, observability, and recovery/performance-readiness predecessor reports. The wave is
internally consistent and the focused/full repository gates pass. It does not provide production or
staging operational evidence, so the Phase F release gate remains open.

This file preserves the wave-1 point-in-time result. Current T06/T09/T11/T12 tooling and remaining
gates are reconciled in [wave 2 verification](./sprint-f1-wave2-verification.md); do not use the
wave-1 “missing full reindex” wording as the current implementation inventory.

## Integrated code evidence

| Task | Verified repository evidence | Wave-1 status |
|---|---|---|
| `PF-SF1-T01` | Internal Nginx listener requires a deployment allowlist include, retains a final deny in the safe example, overwrites forwarding headers after exact trusted-proxy processing, and excludes Slack ingress from the internal fallback. | Code/config complete; firewall/VPN/LB installation open |
| `PF-SF1-T02` | Public listener exposes only exact Slack POST ingress and applies query, content type, body, connection, rate, timeout, default-deny, and no-upstream-retry bounds. | Code/config complete; public TLS and real Slack ingress open |
| `PF-SF1-T03` | Application credentials remain `SecretStr`; normalized query-free edge logging, application security headers, exact proxy trust guidance, and credential rotation procedures are present. | Code/log-masking foundation complete; secret-store encryption and real rotation open |
| `PF-SF1-T04` | React escaping, bound SQL injection, Unicode/path traversal, RFC 5987 download headers, file declaration/byte/MIME/SHA/malware checks, and proxy configuration regressions pass. | Deterministic and disposable-PostgreSQL regression scope complete |
| `PF-SF1-T05` | Bounded-cardinality API/database/worker/component metrics, readiness/degradation semantics, atomic worker textfile publication, alert rules, and response runbook are integrated. | Code/config complete; production `promtool`, receiver delivery, dashboard, and acknowledgement open |
| `PF-SF1-T06` | A separate preview-first fixed 100,000-case profile requires an attested empty loopback disposable database and exact confirmation, then covers concurrent in-process API, signed synthetic Slack parsing/queueing, worker claim/lease, and no-outbound generation scheduling with deterministic p50/p95/p99/error/throughput and queue/lease lag gates. | Preparation complete; actual disposable load run and operator/reviewer handoff open |
| `PF-SF1-T07` | Backup/restore wrappers require attested targets, non-overwriting external dump paths, empty restore destinations, no `--clean`, archive validation, and restored head revision. | Preparation complete; scheduled backup and isolated restore rehearsal open |
| `PF-SF1-T08` | R2 verification binds status to the declared recovery bucket, forces paginated metadata dry-run, rejects redirects, and requires a non-empty all-verified bounded byte sample. | Preparation complete; recovered bucket/object evidence open |
| `PF-SF1-T09` | Knowledge preview/apply requires head revision, fresh source digests, opt-in pinned local/staging Ollama composition, and derived-table-only guarded rebuild; the fixed disposable LightRAG fallback command is delegated explicitly. | Preparation complete; staging rebuild/fallback and full LightRAG corpus reindex open |
| `PF-SF1-T10` | One-step migration round-trip requires an attested current-head disposable/staging copy and exact normalized schema/data equality. | Preparation complete; staging-copy execution open |
| `PF-SF1-T15` | Local policy validation enforces one exact staging HTTPS origin, exact methods/headers/exposed headers, and no wildcard; secret-backed preflight rejects redirects and never reports the signed URL. | Preparation complete; deployed policy and physical-browser flow open |

## Integration safeguards added

Review found and closed three bounded gaps:

1. Phase F database, benchmark, Alembic, and knowledge subprocess environments now remove all
   ambient `PG*` and unrelated `TC_PHASE_F_*` values before adding only the declared connection
   inputs. This prevents API tokens, presigned URLs, or another target's database credential from
   being inherited by an unrelated child process.
2. R2 API verification and CORS preflight now use a no-redirect opener. A `3xx` is a sanitized
   failure before bearer or signed-URL authority can be forwarded to another destination.
3. The worker-staleness alert now covers a missing node-exporter target and the per-target case in
   which node-exporter is reachable but the worker textfile series is absent. The API scrape alert
   also covers a completely absent configured job series.

No credential, private key, Slack token, or real presigned URL was added. Repository secret-pattern
inspection found only explicit fake test fixtures and documentation placeholders. No cleanup,
restore, benchmark, or migration command defaults to execution; every recovery/performance mutation
still requires `--execute`, exact confirmation, and the target-specific guards documented in the
predecessor report.

## Verification executed

| Check | Result |
|---|---:|
| Focused edge/observability/recovery/API/worker/object-storage suite before integration fixes | `79 passed` |
| Updated rehearsal and alert regressions | `25 passed` |
| Recovery dependencies and non-PostgreSQL object-storage/migration/fallback regressions | `37 passed, 11 deselected` |
| Disposable PostgreSQL SQL-injection and private object-storage lifecycle regressions | `2 passed`; temporary database dropped afterward |
| `pnpm check` | PASS: Ruff, ESLint, TypeScript/Python typecheck, `447` Python tests, `348` TypeScript tests, schemas, ADRs, and OpenAPI/client drift; one live Web E2E skipped |
| `pnpm --filter @tc/web build` | PASS: Vite production bundle built |
| Nginx 1.27 Alpine `nginx -t` with tracked examples mounted | PASS: syntax and configuration successful |
| Guarded T06 mixed-load preview and focused tests | PASS: `workUnitsExecuted=0`; no database environment read or benchmark/provider run; actual gate `NOT_RUN` |
| T15 example CORS policy validation | PASS locally; `physicalBrowserEvidence=NOT_RUN` |

`promtool` is not installed and the repository does not declare the production Prometheus version.
The exact production-version rule validation therefore remains open instead of being replaced with
an arbitrary container version.

## External gates not satisfied by this wave

The following remain explicitly **NOT RUN / OPEN**:

- real office/VPN/firewall/LB/TLS/DNS installation and allowed/denied black-box probes;
- real Slack URL verification, signed event ingress, notification delivery, and credential rotation;
- production secret-store encryption/least-privilege proof and real alert receiver delivery;
- an attested disposable concurrent 100,000-case plus Slack/generation load run against the prepared
  thresholds, operator/reviewer handoff, and a later production-like edge/resource capacity run;
- scheduled PostgreSQL backup, separately hosted restore, matching R2 recovery, RTO/RPO, and restored
  application smoke;
- staging knowledge rebuild, complete LightRAG corpus reindex/checksum, fallback switch, and staging
  migration rollback/roll-forward;
- deployment of the exact R2 CORS policy and a physical-browser multi-file
  upload → download → delete flow from the deployed HTTPS Web origin;
- Phase F UAT, production deployment, initial observation period, and release approval.

Accordingly, only `PF-SF1-T04` is closed in the roadmap. The code/configuration or preparation
evidence for the other wave-1 tasks is recorded without converting their external acceptance gates
to completed status.
