# Private R2 / local MinIO operations runbook

This runbook is the operational source for TC attachment storage. New attachments use a
private S3-compatible bucket: Cloudflare R2 in production and MinIO in local development.
Google OAuth is not part of setup, runtime operation, recovery, or credential rotation.

## Production gate

Do not mark production storage ready until an operator records all of these facts without
copying secret values into a ticket, log, or report:

1. The R2 account ID, private bucket name, and exact Web origin are known.
2. Public bucket access and any public/custom-domain route are disabled.
3. A bucket-scoped S3 token can list, put, head, get, and delete objects in only that bucket.
4. The CORS policy below is installed and a real browser origin passes direct PUT.
5. A reachable ClamAV daemon returns a clean verdict; scanner failure keeps uploads quarantined.
6. The API lifecycle smoke passes: session, PUT, complete, proxy download, integrity, restore
   sample, and delete.

Required secret-store entries are `TC_OBJECT_STORAGE_ACCESS_KEY_ID` and
`TC_OBJECT_STORAGE_SECRET_ACCESS_KEY`. The non-secret deployment values are:

```text
TC_OBJECT_STORAGE_ENABLED=true
TC_OBJECT_STORAGE_PROVIDER=R2
TC_OBJECT_STORAGE_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
TC_OBJECT_STORAGE_BROWSER_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
TC_OBJECT_STORAGE_BUCKET=<PRIVATE_BUCKET>
TC_OBJECT_STORAGE_REGION=auto
TC_OBJECT_STORAGE_CLAMAV_HOST=<REACHABLE_PRIVATE_HOST>
TC_OBJECT_STORAGE_CLAMAV_PORT=3310
```

Production configuration validation rejects disabled/non-R2 storage, an incomplete S3
credential bundle, or a missing ClamAV host. Never print the environment or a presigned URL.

## Bucket privacy, token scope, and CORS

Create one private bucket with no lifecycle deletion rule. Scope the production token to that
bucket with object read/write and list access; do not grant account administration, worker,
DNS, or unrelated bucket permissions. The API needs ListObjectsV2, PutObject, HeadObject,
GetObject, and DeleteObject. The browser receives only a five-minute, object-specific PUT URL.

Install CORS with the deployed HTTPS Web origin substituted exactly:

```json
[
  {
    "AllowedOrigins": ["https://<EXACT_WEB_ORIGIN>"],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": ["content-type", "x-amz-meta-sha256"],
    "ExposeHeaders": ["etag", "content-length", "content-type", "x-amz-meta-sha256"],
    "MaxAgeSeconds": 300
  }
]
```

Do not use `*` for an origin or header. TC currently sends browser object traffic only for the
signed PUT and proxies reads through the API; GET and HEAD are retained in the approved policy
for an explicit browser verification, not as public access. An unsigned object GET must never
return 2xx: MinIO returns 403, while R2 may return 400 `InvalidArgument` with `Authorization`.

For local actual-R2 verification, apply
[`r2-cors.localhost.json`](./r2-cors.localhost.json), then replace both localhost origins with the
single deployed HTTPS Web origin before production approval.

## Validation and quarantine policy

Upload session creation normalizes the filename with Unicode NFKC and rejects blank/dot names,
UTF-8 names over 255 bytes, path separators, drive prefixes, NUL, and control/format characters.
It also enforces the configured MIME allowlist, positive configured-bounded size up to the hard
50 MiB maximum, and a lowercase
64-character SHA-256.

Objects are written below `quarantine/attachments/`. Completion first compares HEAD size,
Content-Type, and `x-amz-meta-sha256`, then downloads the bounded object to a mode-0600 temporary
file, recomputes size and SHA-256 from bytes, sniffs content, parses declared JSON, and asks ClamAV
for an explicit CLEAN verdict. Only then is readable attachment metadata committed. Detected or
invalid content is deleted; scanner outage produces a typed failure and leaves an unreferenced
quarantine object for the orphan job. The temporary file is always removed.

## Local MinIO verification

```bash
pnpm infra:up
TC_TEST_MINIO_URL=http://127.0.0.1:59000 \
  .venv/bin/pytest -q backend/tests/integration/test_object_storage_minio.py
```

The opt-in test exercises signed PUT, HEAD, GET, list, delete, private anonymous denial, clean
ClamAV scanning, and EICAR-test-signature rejection. For the full API smoke, run the API with the
local MinIO/ClamAV values supplied by `pnpm dev`, create at least one issue, then run:

```bash
TC_SMOKE_API=http://127.0.0.1:8000 .venv/bin/python scripts/smoke_object_storage.py
```

### Local disposable matched-recovery rehearsal

Use [`object_storage_recovery.py`](../../scripts/phase-f/object_storage_recovery.py) when a local
Phase F rehearsal must restore PostgreSQL attachment metadata and S3-compatible objects as one
recovery set. It is deliberately narrower than the general production runbook: it accepts only the
loopback PostgreSQL, MinIO, and ClamAV services from `deploy/compose.dev.yml`, exact generated
`pf-sf1-t08-wave7` database/bucket/prefix identities, distinct empty destinations, and exact database
comments. It cannot be used against Cloudflare R2 or a staging/live target.

Always run `recover` without `--execute` first and review its literal resource list and confirmation.
Supply the local administrator URL and MinIO credential only through the named `TC_PHASE_F_*`
environment variables; never print their values. Execution creates a deterministic multi-page
fixture covering every supported attachment owner type and every fixture MIME class, scans source
bytes through local ClamAV, pins the dump and manifest, restores into an empty database/bucket without
overwrite, and validates the full DB/API/object-store binding.

Cleanup is a separate preview-first action. It consumes the already pinned recovery evidence,
revalidates every database comment, bucket/object inventory, file identity, and pre-existing-state
digest, and removes only that exact generated set after evidence is durable. Never substitute a
shared/default bucket, use a wildcard, or delete an object that is not in the attested manifest.

The 2026-09-04 wave-7 reference passed only the **local MinIO disposable sub-gate**; see the
[verification report](../../docs/phase-f/sprint-f1-wave7-verification.md). It is not Cloudflare R2
evidence. Actual R2 recovery/versioning/backup, a separately hosted staging database, operator
handoff, production RTO/RPO, `PF-SF1-T08`, and the Phase F release gate remain open/`NOT_RUN`.

## Scheduled checks and thresholds

Run these through the internal scheduler or an operator-only job runner. Preserve response JSON
and trace IDs in the operational evidence store, but never preserve signed URLs or credentials.

- `POST /api/integrations/object-storage/reconcile-orphans`: list the workspace quarantine
  prefix, dry-run first, and delete only unreferenced objects older than the chosen cutoff.
- `POST /api/integrations/object-storage/verify-metadata`: compare active DB rows with object HEAD
  size, MIME, and SHA-256 metadata; a non-dry run marks missing/mismatched rows unreadable.
- `POST /api/integrations/object-storage/restore-sample`: download a bounded deterministic sample
  and compare byte size and SHA-256 with the restored DB ledger.
- `GET /api/integrations/object-storage/observations`: report active DB bytes, observed request
  classes, configured budgets, estimated cost, and inclusive 70/85/95/100 warning level.

Request counters are process-local from API start, and storage bytes come from active attachment
metadata. Therefore the response is explicitly labeled `PROCESS_LOCAL_CONFIGURED_ESTIMATE`; it is
not a substitute for durable R2 account analytics. Set approved current rates and budgets through
the `TC_OBJECT_STORAGE_*_BUDGET` and `TC_OBJECT_STORAGE_*_USD_*` settings. Zero rates intentionally
mean “cost estimate disabled,” not zero vendor cost. In production, compare this endpoint with R2
analytics/billing at least monthly and alert on the higher threshold.

Request classification follows the [Cloudflare R2 pricing operation
classes](https://developers.cloudflare.com/r2/pricing/): direct PUT and ListObjects are Class A,
HEAD/GET are Class B, and DeleteObject is free and therefore excluded from both counters.

Suggested cadence is orphan dry-run hourly, orphan delete daily with a 24-hour cutoff, metadata
integrity daily, and restore sampling after every DB restore plus monthly. Stop automated deletion
if list pagination, clock, or DB availability is uncertain. Every orphan and metadata-integrity run
must follow `nextCursor` until it is null; persist the cursor between attempts so one failed page can
be retried without restarting a large inventory.

## Legacy row migration policy

`GOOGLE_DRIVE` remains only as a historical database enum/column value so existing rows and old
migrations load safely. New Google Drive rows, OAuth routes, credential configuration, download,
and delete are disabled. `GET /api/integrations/object-storage/legacy-attachments` exposes a
metadata-only inventory without provider file IDs; ordinary content/delete requests return a
typed `LEGACY_ATTACHMENT_MIGRATION_REQUIRED` conflict.

Inventory consumers must follow `nextCursor` until it is null; `total` describes the full legacy-row
population, while `items` contains only the current page.

For each inventory row, an authorized operator must export bytes outside TC using separately
approved legacy-account access, validate the recorded size and SHA-256, upload through the normal
R2 validation/scan path, and update the row in a reviewed migration transaction. Keep the old row
until the R2 object passes restore sampling. Do not restore Google OAuth code or credentials to
the product to perform this migration.

## Credential rotation

1. Create a second bucket-scoped token with the same minimum permissions and record its creation
   and intended expiry in the secret manager.
2. Replace both S3 credential values atomically in one deployment; never rotate only one field.
3. Verify status, signed PUT/complete, proxy download, list/integrity, restore sample, and delete.
4. Roll back to the still-valid old secret if verification fails and investigate endpoint, clock,
   permission, or CORS errors.
5. Revoke the old token only after all API replicas use the new version and verification passes.
6. Record token version, deployment, verifier, and revocation time, never the credential value.

Emergency rotation follows the same sequence but starts by disabling new upload sessions if a
credential or signed URL is suspected compromised. Bucket privacy is never relaxed to recover
availability.

## Failure response

| Signal | Likely cause | Safe response |
|---|---|---|
| `SETUP_REQUIRED` | incomplete storage or scanner bundle | keep upload disabled; correct secret/non-secret values and restart |
| browser PUT CORS failure | wrong exact origin, method, or signed header | compare the installed policy and request; do not use wildcard CORS |
| S3 401/403 | bad/expired token, clock skew, or insufficient bucket scope | verify clock and token version, then rotate or correct least privilege |
| scanner unavailable | ClamAV down/unreachable | keep objects quarantined, restore scanner, retry complete before orphan cutoff |
| metadata mismatch/missing | object change/deletion or wrong DB restore | mark unreadable, stop destructive cleanup, recover from the matched backup pair |
| request/storage threshold | budget approaching/exceeded | inspect R2 analytics, orphan volume, retention, and approved spend before raising budget |

## Backup and recovery

Treat PostgreSQL metadata and the private bucket as one recovery set. Back up PostgreSQL with a
transactionally consistent dump and preserve an authenticated object inventory containing key,
size, last-modified time, and checksum metadata at the same recovery-point label. Keep bucket
versioning/backup retention and encryption under the organization backup policy; never make the
bucket public or download production evidence to an unmanaged workstation.

Recovery order is: restore PostgreSQL to an isolated target, restore/attach the corresponding
private object set, deploy credentials that point only to that recovery bucket, run metadata
verification in dry-run mode, run bounded full-byte restore sampling, and enable reads only after
mismatch review. Run orphan cleanup last. A backup is not accepted solely because `pg_restore`
succeeds: at least one attachment from each owner type and MIME class present must match both size
and SHA-256, or the entire available population when it is smaller than the sample target.
