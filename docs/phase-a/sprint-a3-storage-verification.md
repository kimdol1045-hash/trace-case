# Phase A / Sprint A3 private object-storage verification

- 검증일: 2026-08-24, actual R2 추가 검증 2026-08-25 (Asia/Seoul)
- 범위: `PA-SA1-T09`, `PA-SA3-T01`~`PA-SA3-T12`
- production 결정: private Cloudflare R2
- local parity: private MinIO + fail-closed ClamAV
- 관련 결정/절차: [ADR-0017](../adr/0017-r2-direct-private-attachments.md), [object-storage runbook](../../deploy/object-storage/README.md)

## 1. 결론

신규 첨부의 Google OAuth/Drive runtime, API route, setting, dependency, Web demo 문구와 public contract를 제거했다. 현재 제품의 신규 쓰기는 R2 provider만 허용하고, `GOOGLE_DRIVE`는 과거 migration과 기존 row를 로드하기 위한 enum/column 값 및 metadata-only migration inventory에만 남는다. legacy content/download/delete는 Google credential을 다시 요구하지 않고 typed migration-required conflict를 반환한다.

Local MinIO에서 실제 SigV4 PUT, HEAD, GET, ListObjectsV2, DELETE, unsigned private denial, ClamAV CLEAN/EICAR 판정이 통과했다. disposable PostgreSQL과 live API/MinIO/ClamAV smoke에서는 upload session → quarantine PUT → 실제 bytes size/SHA-256/MIME 검증 → CLEAN metadata commit → private proxy download → metadata check → restore sample → observations → orphan dry-run → delete까지 통과했다.

2026-08-25 actual R2 추가 검증에서 `tc-attachments-prod` APAC·Standard private bucket, bucket-scoped S3 key와 localhost CORS를 구성했다. `r2.dev`는 disabled, custom domain은 0건이고 unsigned GET은 Authorization 4xx로 거부됐다. bucket-scoped key는 지정 bucket의 ListObjects/PUT/HEAD/GET/DELETE에 성공하면서 account ListBuckets는 403으로 거부됐다. 실제 R2 session → presigned direct PUT → complete → bytes/SHA/MIME/ClamAV → private proxy download → integrity/orphan/restore → delete가 통과했다.

같은 actual R2 첨부가 active인 시점의 PostgreSQL custom dump를 격리 DB에 복원한 뒤 restored API의 full-byte restore sample도 `sampled=1`, `verified=1`, `failed=0`으로 통과했다. 임시 객체·복원 DB·dump는 삭제했다. 현재 서비스는 배포 전 localhost 운영 기준이며 exact localhost CORS 구성은 이 범위에서 완료했다. 남은 Phase A gate는 owner의 localhost physical browser 다중 파일 전체 흐름이고, 향후 배포 HTTPS origin CORS와 durable R2 analytics 대조는 Phase F 배포 체크리스트로 분리한다.

## 2. 구현 감사

### 제거된 operational Drive 경계

- `backend/src/tc_backend/infrastructure/google_drive/` 전체 runtime/adapter/OAuth/crypto/transport를 제거했다.
- `apps/api/src/tc_api/drive_runtime.py`, `routers/drive.py`와 main/dependency composition을 제거했다.
- `Settings`와 `.env.example`에서 Google OAuth, Drive credential/root/quota/scanner 변수를 제거했다.
- Drive-only typed DB error와 unused Pydantic request/response 모델, direct `cryptography` dependency를 제거했다.
- generated API client에서 authorize/callback/reconnect/disconnect/quota/manifest method/type을 제거했다.
- dead Drive dashboard/demo storage label과 Drive-specific CSS class를 제거했다.
- Integrations UI는 private/scan/legacy 상태와 process-local 저장량·Class A/B·비용·70/85/95 경고를 표시한다.
- current JSON Schema package를 `object-storage-api.schema.json`으로 교체하고 OAuth/quota/manifest fixture와 export를 제거했다.
- Superseded ADR-0007과 Sprint 4 report는 historical evidence다. 삭제된 runtime을 현재 지원으로 오해하지 않도록 ADR-0007을 historical-only로 갱신했다.

### 의도적으로 보존한 legacy-row compatibility

- 과거 migration graph, `attachments.drive_file_id`, provider enum/check constraint와 historical provider tables는 기존 DB를 무손실로 upgrade/load하기 위해 보존한다.
- current `AttachmentResource`는 legacy row를 `storageProvider=GOOGLE_DRIVE`, `scanStatus=LEGACY_MIGRATION_REQUIRED`로 식별할 수 있다.
- `GET /api/integrations/object-storage/legacy-attachments`는 provider file ID 없이 owner/filename/MIME/size/SHA metadata와 migration-required 상태만 반환한다.
- repository는 신규 Drive row 생성을 거절하며 content/delete API는 offline R2 migration을 요구한다.

Sprint A1 core worker 소유의 folder-library/test-case/cycle implementation 파일은 수정하지 않았다. 공용 `backend/tests/repository/test_sprint4_postgresql.py`는 attachment 구간에서 Drive quota/reconcile dependency를 제거하고 R2 row constraint/reconcile을 검증하기 위해서만 수정했으며 case/cycle assertion은 건드리지 않았다.

## 3. 보안·무결성 정책

### 선언 검증

- filename: NFKC normalize, trim, 빈 문자열/`.`/`..`, UTF-8 255 byte 초과, `/`·`\\`·NUL, Unicode control/format, Windows drive prefix 거부
- MIME: configured allowlist만 허용하고 완료 시 signed header, HEAD Content-Type, sniffed bytes를 비교
- size: 양수와 configured maximum을 session 발급 시 확인하고, HEAD와 bounded byte stream에서 다시 확인
- SHA-256: lowercase 64 hex를 요구하고 signed `x-amz-meta-sha256`, HEAD metadata, 실제 byte digest를 모두 비교
- JSON은 UTF-8 decode와 parse까지 성공해야 한다.

### quarantine / malware

- presigned key는 `quarantine/attachments/<workspace>/<owner>/...` 아래에만 생성한다.
- 완료 검증은 mode `0600` 임시 파일에서 수행하고 `finally`로 제거한다.
- ClamAV가 명시적 CLEAN을 반환하기 전에는 DB에 readable attachment가 없다.
- malware/validation failure는 provider object를 삭제하고, scanner unavailable은 fail closed로 metadata를 만들지 않으며 unreferenced object는 orphan cutoff 전까지 quarantine에 남긴다.

### 운영 검사

- orphan reconcile: paginated workspace prefix list와 DB key set 비교, cutoff, dry-run, retryable delete 결과
- metadata integrity: DB size/MIME/SHA와 provider HEAD 비교; non-dry-run은 missing/mismatch row를 unreadable reconcile status로 전환
- restore sample: bounded object 전체 download 후 DB size/SHA와 비교
- observations: active R2 metadata bytes, Class A/B observed requests, configured rates/budgets와 inclusive 70/85/95/100 경고

Observation request counter는 API process-local이며 restart 시 초기화된다. 비용은 operator가 승인된 rate를 넣었을 때만 계산되고 `PROCESS_LOCAL_CONFIGURED_ESTIMATE`로 표시된다. 실제 production 판단은 durable R2 analytics/billing과 대조해야 하며 이 checkout에서는 그 외부 데이터가 없다.

## 4. Sprint A3 판정

| ID | 판정 | 근거 / 남은 경계 |
|---|---|---|
| `PA-SA1-T09` | 완료 | operational OAuth/Drive source, env, demo, client/contract dependency 제거; legacy schema/inventory만 보존 |
| `PA-SA3-T01` | 완료 | APAC·Standard private bucket, `r2.dev` disabled/custom domain 0, bucket object lifecycle 허용과 account ListBuckets 403으로 최소 권한 검증 |
| `PA-SA3-T02` | 완료 (배포 전 범위) | localhost·127.0.0.1 exact-origin PUT/GET/HEAD CORS와 OPTIONS 204를 actual R2에서 확인했고 physical Chrome의 actual R2 direct PUT도 성공했다. 추후 배포 HTTPS origin 설치·smoke는 `PF-SF1-T15`이며 이 행은 production 배포 검증을 의미하지 않는다. |
| `PA-SA3-T03` | 완료 | actual R2 session→presigned PUT→complete→scan→proxy GET→integrity/restore/orphan→delete PASS, cleanup 후 object 0 |
| `PA-SA3-T04` | 완료 | declaration + actual byte validation unit/API/MinIO tests |
| `PA-SA3-T05` | 완료 | quarantine key, fail-closed ClamAV, invalid/infected cleanup, EICAR integration test |
| `PA-SA3-T06` | 완료 | ListObjectsV2 기반 dry-run/delete orphan endpoint와 API test |
| `PA-SA3-T07` | 완료 | HEAD metadata integrity endpoint, `METADATA_MISMATCH` migration/status와 API test |
| `PA-SA3-T08` | local 구현 완료 | 70/85/95/100 unit/API evidence; durable R2 usage/cost comparison은 production external gate |
| `PA-SA3-T09` | 완료 | active actual R2 attachment와 매칭된 PostgreSQL dump를 격리 DB에 복원해 full-byte size/SHA sample 1/1 PASS |
| `PA-SA3-T10` | 완료 | 최소 권한/CORS, rotation, failure, backup/recovery runbook 작성 |
| `PA-SA3-T11` | 완료 | no-new-write, metadata-only inventory, typed migration-required, reviewed offline migration policy |
| `PA-SA3-T12` | 완료 | 이 보고서가 local evidence와 external blockers를 분리 기록 |

Sprint A3의 실제 storage와 복구 task는 완료했다. 다만 physical 다중 파일 종료 체크는 `NOT RUN — owner manual follow-up`이다. 2026-08-25 physical Chrome에서 다운로드 폴더의 `login.png`와 `login (1).png`가 각각 actual R2에 저장되는 것은 확인했지만 하나의 chooser event batch가 아니었고, 사용자 예외 지시에 따라 browser download 및 chooser 재시도를 중단했다. 두 attachment는 provider delete `DELETED`를 확인한 뒤 임시 이슈와 함께 정리했으며 active attachment는 0건이다.

## 5. 검증 결과

| 명령 / 흐름 | 결과 |
|---|---|
| `pnpm check` | 2026-08-24 storage gate PASS. 2026-08-25 current worktree rerun은 `attachment-preview.tsx`의 Fast Refresh warning 3건과 `--max-warnings 0` 때문에 lint 단계 FAIL; storage 동작 실패가 아니며 이후 단계는 미실행. |
| `ruff check backend apps/api` | PASS |
| global Ruff + ESLint | PASS |
| global mypy | PASS, 85 source files |
| `pnpm test:unit:py` | PASS, 126 passed / 13 PostgreSQL deselected |
| focused disposable PostgreSQL Sprint 4 + storage API/repository | PASS, 10 passed |
| `pnpm --filter @tc/contracts test:unit` | PASS, 7 files / 36 tests |
| `pnpm --filter @tc/contracts typecheck` | PASS |
| full TypeScript unit sweep | PASS, UI 6 + client 19 + contracts 36 + Web 87 tests |
| full TypeScript typecheck | PASS |
| `pnpm test:schema` | PASS, 5 canonical schemas / 12 synthetic fixtures |
| `pnpm test:adr` | PASS, 17 ADRs / local links valid |
| `pnpm openapi:check` | PASS after generated artifacts refresh |
| opt-in `test_object_storage_minio.py` against live MinIO/ClamAV | PASS, 1 test |
| live API → MinIO/ClamAV smoke | PASS: SHA/private download/metadata/restore/status/observation/legacy/orphan/delete |
| live API → actual private R2/ClamAV smoke | PASS: READY, SHA/private download/metadata/orphan/restore/delete, cleanup object 0 |
| bucket scope/privacy | PASS: r2.dev disabled, custom domain 0, unsigned Authorization 4xx, account ListBuckets 403 |
| matched PostgreSQL dump → isolated restore DB → actual R2 sample | PASS: sampled 1 / verified 1 / failed 0; temporary object/DB/dump removed |

The PostgreSQL evidence database was a disposable local database named `tc_storage_a3_test`; no production/user database was changed. Local infrastructure used localhost-only Postgres, private MinIO, and `clamav/clamav-debian:1.4.3` because the older image variant had no arm64 manifest.

## 6. 남은 수동 검수와 배포 경계

Repository-root `.env` now supplies temporary actual R2 S3 credentials for local verification and is excluded from Git. No credential value or signed URL is recorded in this report. Because the temporary credential was disclosed during setup, it must be revoked and replaced in the deployment secret store before any production use.

현재 R2 CORS가 허용하는 `http://localhost:5173`과 `http://127.0.0.1:5173`은 이 배포 전 서비스의 현재 운영 Web origin이다. 따라서 `PA-SA3-T02`의 현재 범위는 완료로 판정한다. 아직 존재하지 않는 deployed HTTPS Web origin과 durable R2 analytics/billing evidence는 검증하지 않았으며 production 승인 근거로 주장하지 않는다.

Phase A에 남은 storage UX 증적은 owner가 수행할 localhost physical browser 다중 파일 upload→각 download→각 delete다. 판정은 `NOT RUN`이며 실제 성공 증적 없이 닫지 않는다. deployed HTTPS origin CORS 설치와 그 origin의 PUT/GET/HEAD·다중 첨부 smoke는 Phase F `PF-SF1-T15`에서 수행한다.

## 7. Follow-up evidence required

1. Owner가 localhost physical browser에서 다운로드 폴더의 PNG 두 개를 하나의 chooser event로 선택하고 각 download와 delete까지 수동 확인한다.
2. Revoke the disclosed temporary R2 token, issue a replacement bucket-scoped token and place it only in the deployment secret store.
3. Phase F에서 installed exact HTTPS Web origin CORS와 PUT/GET/HEAD preflight, 다중 파일 direct R2 PUT→completion→download→delete를 검증한다.
4. Compare the observations endpoint with durable R2 storage/request/billing analytics at 70/85/95 thresholds.
