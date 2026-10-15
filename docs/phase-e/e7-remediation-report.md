# E7 implementation remediation report

- Date: 2026-09-01 (Asia/Seoul)
- Outcome: code and contract remediation complete
- Release status: not certified; operational sample and provider-live gates remain open

## Remediated boundaries

The T08 candidate exporter, import service, persistence, list/detail API, web projection, and
reviewed export now build and verify the E4 `ACTUAL_MINIMIZED` revision-one envelope. Public
responses are limited to the strict aggregate projection, E4 candidate/payload/artifact
fingerprints, and domain-separated HMAC source/reviewer seals. Legacy source-derived public SHA
values, aliases, root/source identifiers, report paths, URLs/endpoints, raw text/excerpts,
principal IDs, and internal decision/approval IDs are rejected or kept private.

Migration `20260901_0029` adds an immutable installed-reviewer authority ledger, recomputable
authority/decision/approval integrity, a no-login writer role without direct ledger DML, and a
narrow security-definer decision function. Plaintext reviewer tokens are not installed in the
database; application token-to-principal comparison remains constant-time and constant-work across
configured credentials. The migration rejects
legacy candidate rows until an explicit E4 re-import and its downgrade refuses to reinterpret E4
rows as the legacy contract.

The Python sensitive-key scanner now follows the schema's strict embedded `raw` substring rule, so
`drawCount` is rejected by both producer and schema. The quality-review page header reports
`목록 이용 불가` when the settled list request failed and no page exists.

## Verification evidence

- Focused Python privacy/export/API/reviewer tests: **56 passed**.
- Quality-review contract package: **127 passed**.
- Generated API client: **23 passed**.
- Web unit suite: **189 passed, 1 skipped**.
- Disposable PostgreSQL 16 + pgvector repository/migration suite: **72 passed**, with one existing
  SQLAlchemy pytest collection warning; this includes migration upgrade/downgrade, race,
  immutability, source drift, direct-DML denial, installed-authority, recomputed-integrity,
  repository, and API tests.
- Final focused PostgreSQL raw-SQL/tamper adversarial rerun: **2 passed**.
- Final `pnpm check`: ESLint/Ruff passed; TypeScript typecheck passed; strict mypy passed for **134
  source files**; TypeScript unit suites totaled **345 passed, 1 skipped**; non-PostgreSQL Python
  totaled **428 passed, 108 deselected**; **13** strict schemas, **17** ADRs, and generated OpenAPI
  drift checks passed.
- Web production build: **99 modules transformed**, build succeeded.

## Gates intentionally left open

No actual reviewer approval or release certificate was created. The agreed operational
**32-root/16-class** sample has not been supplied or attested, and a credentialed provider live
attestation has not run; both remain required before any release-certification claim.
