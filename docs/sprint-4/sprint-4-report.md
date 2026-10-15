# Sprint 4 이슈·Drive·리포트 품질 게이트

**검증일:** 2026-08-20  
**범위:** `P2-S4-T01~T11`, PostgreSQL 16 + pgvector, FastAPI, generated TypeScript client, Web, deterministic fake Drive  
**판정:** 코드·결정론적 통합 게이트 통과 / Sprint 4·Phase 2 운영 종료 보류

> **Historical only (2026-08-24):** ADR-0017이 이 저장 방식과 설치 절차를 대체했다.
> 아래 결과는 당시 결정론적 증거로만 보존하며 Google credential을 제품에 다시
> 설정하면 안 된다. 현재 절차는 [private object-storage runbook](../../deploy/object-storage/README.md)을 따른다.

## 1. 결론

이슈 원장, 실행 snapshot, Google Drive adapter 경계, 첨부 metadata, dashboard·CSV와 Web no-fallback 연결은 로컬 PostgreSQL과 deterministic fake Drive에서 재현 가능하게 통과했다. `FAIL`과 `BLOCKED` attempt 모두 pinned case version의 사전조건·ordered steps·expected result와 attempt의 actual result·note를 immutable 이슈 snapshot으로 복사하며, 생성·link·audit·outbox-ready record가 같은 transaction에 남는다. API 단일 흐름은 이슈 수정·optimistic conflict, append-only comment, link, private proxy attachment upload/download/soft delete, quota warning, cycle 완료와 CSV까지 검증한다.

그러나 이 저장소와 실행 환경에는 실제 Google OAuth client와 전용 Google 계정 credential이 없다. 따라서 전용 My Drive에서의 실제 private ACL, live token refresh/revoke, 실제 quota·rate limit과 운영자 복구 절차는 검증하지 않았고, Sprint 4의 My Drive 종료 조건과 Phase 2 운영 리허설은 완료 처리하지 않는다. ADR-0007도 이 외부 가정이 실증될 때까지 `Proposed`로 유지한다.

## 2. 자동화된 단일 원장 흐름

`apps/api/tests/test_sprint4_api_postgresql.py::test_sprint4_issue_drive_quota_and_report_flow`는 실제 FastAPI application과 disposable PostgreSQL transaction, deterministic fake OAuth/Drive를 사용한다.

1. PKCE·일회성 state가 포함된 authorize URL을 만들고 callback에서 refresh token을 암호화해 `integration_settings`에 저장한다.
2. application의 Drive runtime을 재생성한 뒤, 저장된 nonce·ciphertext·key version을 복호화하고 refresh token으로 access token을 다시 받아 adapter를 복구한다.
3. category→work item→case→cycle을 생성하고 cycle을 시작한 뒤 WEB attempt를 `FAIL`로 종료한다.
4. attempt에서 `ISSUE-1`을 생성해 pinned version, script, expected result, actual result를 복사하고 동일 idempotency key replay가 같은 resource를 반환하는지 확인한다.
5. 이슈 status·severity·assignee를 수정하고 stale `resourceVersion`이 `VERSION_CONFLICT`인지 확인한다. comment를 append하고 3개 link 중 하나를 제거해 field/link history와 resource version 증가를 확인한다.
6. issue attachment를 streaming 검사 후 private fake Drive에 올리고 metadata를 commit한다. 같은 idempotency key replay와 payload conflict, backend proxy download, Drive URL·file ID 비노출을 확인한다.
7. execution attempt evidence도 multipart로 업로드하고 owner가 attempt로 남는지 확인한다.
8. fake quota를 70%로 바꿔 `WARNING_70`과 export manifest를 확인하고 cycle을 incomplete acknowledgement와 함께 `COMPLETED`로 전이한다.
9. dashboard를 ledger source에서 집계하고 같은 filter source의 UTF-8 CSV에 `Sprint 4 regression,COMPLETED,WEB`이 포함되는지 확인한다.
10. attachment metadata를 soft delete한 뒤 provider delete state `DELETED`, proxy `409`, attachment deletion history를 확인한다.
11. 사용한 OAuth state 재사용을 거부하고 reconnect idempotency·conflict와 revoke 기반 disconnect audit를 확인한다.

Repository parameterized test는 같은 snapshot 규칙을 `FAIL`과 `BLOCKED`에 각각 적용한다. `BLOCKED`에서는 필수 note가 immutable actual result와 source note로 복사되며 outbox payload의 `sourceStatus`도 `BLOCKED`다.

## 3. 보안·장애 주입 근거

| 계약 | 결정론적 근거 | 결과 |
|---|---|---|
| OAuth PKCE/state | `drive.file`, offline access, consent, S256 challenge, state·verifier와 재사용 거부 | PASS |
| 재기동 token 복구 | nonce/keyVersion/ciphertext 저장, authenticated decrypt, refresh, revoke form과 token rotation 경로 | PASS |
| public sharing 금지 | fake transport의 permission create call이 0이고 HTTP transport가 private file API만 호출 | PASS |
| filename/path | NFKC 뒤 `..`, slash, backslash, drive prefix, NUL 및 owner traversal 차단 | PASS |
| MIME/sniff | allowlist와 JSON/PDF/PNG signature mismatch 차단 | PASS |
| 100 MiB | 기본 한도가 정확히 `100 * 1024 * 1024`, declared/observed size 불일치 차단 | PASS |
| malware fail-closed | 감염, scanner unavailable, scanner exception 모두 upload 전 실패하고 temp file 제거 | PASS |
| streaming/temp | mode `0600` temp, checksum·size 계산, resumable lost-response recovery, 성공·실패 cleanup | PASS |
| quota | 69/70/85/95/100/101%에서 `NONE`/70/85/95/`EXCEEDED`, 초과 시 file·temp leak 없이 거부 | PASS |
| DB commit failure | provider upload 후 metadata 실패 시 즉시 delete compensation | PASS |
| orphan compensation failure | delete 실패를 reconciliation 원장에 기록하고 dry-run→retryable failure→recovery 수행 | PASS |
| checksum mismatch | upload와 download mismatch 모두 fail-closed | PASS |
| OAuth/quota/provider failure | callback 502, quota 409, provider/revoke typed error mapping | PASS |
| secret/content log | API response·history에 refresh token, Drive URL/file ID가 없고 attachment content를 log하지 않음 | 정적·계약 PASS; 운영 log 관측은 live 보류 |

## 4. 실행 결과

로컬 disposable DB는 `pgvector/pgvector:0.8.1-pg16`이며 host loopback의 `tc_test`를 사용했다. 아래 URL의 password는 보고서에서 의도적으로 생략한다.

| 명령 | 결과 |
|---|---|
| `pnpm check` | lint PASS, TS typecheck PASS, mypy 57 files PASS, TypeScript unit 86 PASS, Python non-PostgreSQL 76 PASS, strict schema 5개·fixture 12개 PASS, ADR validator PASS, OpenAPI/client drift 없음 |
| `pnpm --filter @tc/web build` | Vite production build PASS, 82 modules transformed |
| `TC_TEST_DATABASE_URL=postgresql+psycopg://tc:<local-test-password>@127.0.0.1:55432/tc_test pnpm test:migration` | PostgreSQL repository 34 PASS; base-to-head rebuild, FK/unique/check/immutable, key rollback, optimistic concurrency 포함 |
| 같은 DB URL로 `pytest apps/api/tests -m postgres -q` | FastAPI PostgreSQL E2E 6 PASS |
| `pytest apps/api/tests/test_sprint4_api_postgresql.py -m postgres -q` | Sprint 4 단일 흐름·setup required·failure injection 3 PASS |
| `pytest backend/tests/integration/test_google_drive_adapter.py -q` | deterministic fake Drive adapter 13 PASS |
| crypto/OAuth/HTTP transport/runtime config/validation scanner unit | 24 PASS |
| `pnpm openapi:check` | generated OpenAPI와 `packages/api-client/src/schema.gen.ts` drift 없음 |

초기 repository 실행은 Compose 기본 password 대신 README 예시 password를 사용해 인증 실패했다. 실행 중인 Compose의 명시된 local-only credential과 disposable `tc_test`를 확인한 뒤 같은 test suite를 다시 실행해 34개가 모두 통과했으며, 인증 실패는 제품 결함으로 분류하지 않았다.

## 5. Web no-fallback Browser smoke

Orca 내장 Browser를 `http://127.0.0.1:5173` Web과 `http://127.0.0.1:8000` FastAPI에 연결했다. API는 local PostgreSQL `tc`를 사용했고 sticky mock fallback을 켜지 않았다.

- `/issues`는 실제 `ISSUE-1`, `ISSUE-2` 두 건과 server resource version을 표시했다.
- `/issues/ISSUE-2`는 AOS `FAIL` attempt, pinned case version v1, immutable actual result, 3개 ledger link, field history를 표시하고 cycle player 양방향 링크를 제공했다.
- 같은 이슈의 attachment 영역은 actual integration status `SETUP_REQUIRED`를 표시해 mock attachment와 섞지 않았다.
- `/reports`는 PostgreSQL의 2 cycles, 2 executed, PASS 1, FAIL 1을 표시했다. AOS filter 뒤에는 executed 1, FAIL 1, open issue 1로 바뀌었다.
- `/integrations`는 `SETUP_REQUIRED`, 미수집 quota, disabled sync/manifest와 credential 설치 안내를 표시했다.

CSV 내용과 download source는 FastAPI PostgreSQL E2E에서 검증했다. 브라우저에서는 report filter와 download affordance까지 확인했으며 실제 파일 내용 검사는 중복되는 자동화 API assertion을 근거로 사용한다.

## 6. 인수 조건과 종료 판정

| 항목 | 판정 | 근거·제한 |
|---|---|---|
| 기획 인수 조건 8: FAIL 이슈 자동 채움 | PASS | 실제 PostgreSQL/FastAPI 및 FAIL·BLOCKED repository snapshot |
| 기획 인수 조건 9: Drive 저장과 앱 upload/download | deterministic PASS / live 보류 | fake Drive private adapter·proxy·Web 계약은 PASS, 실제 My Drive ACL·binary는 미실증 |
| 기획 인수 조건 10: 70/85/95 경고 | deterministic PASS / live 보류 | exact fake quota와 API/Web warning 계약 PASS, 실제 계정 quota는 미수집 |
| Sprint 4 code·deterministic integration | PASS | `P2-S4-T01~T11` 자동화 근거 존재 |
| Sprint 4 전용 My Drive 종료 조건 | 미완료 | live Google credential 없음 |
| Phase 2 실제 QA 운영 리허설 | 미완료 | local synthetic data이며 운영자·전용 계정 표본 아님 |
| Phase 2 Drive 연결·복구와 15GB 경고 | 미완료 | fake transport만 검증, live restart/revoke/quota 미실증 |
| Phase 2 인수 조건 4~10 모두 통과 | 미완료 | 조건 9의 live provider 성공이 없음 |

## 7. 폐기된 credential 절차

이 보고서의 Google account/OAuth 설치 절차는 2026-08-24 폐기했다. Drive runtime,
route, env setting과 crypto dependency가 제거됐으므로 이전 `TC_GOOGLE_*`, `TC_DRIVE_*`
값을 설정하거나 OAuth app을 복구하지 않는다. Legacy row는
[Sprint A3 storage 검수](../phase-a/sprint-a3-storage-verification.md)의 metadata-only
inventory와 offline migration 정책을 따르고, 신규/운영 첨부는
[R2/MinIO runbook](../../deploy/object-storage/README.md)만 따른다.

## 8. 열린 위험과 Sprint 5 연결점

- live Google OAuth/Drive의 계정 정책, consent 검수, ACL, latency, rate limit, revoke와 실제 quota는 아직 알 수 없다.
- 현재 token cipher composition은 현재 key version을 중심으로 구성된다. 암호화 key 교체 전에는 이전 version decrypt를 유지하는 keyring·재암호화 runbook을 별도로 검증해야 한다.
- ClamAV 가용성은 fail-closed이므로 scanner 장애가 전체 upload를 중지한다. 모니터링, capacity, update 정책이 필요하다.
- 15GB 증가율과 보존 기간은 실제 사용량 표본이 없어 예측값이 없다. manifest와 70/85/95 경고를 Sprint 5 운영 지표에 연결한다.
- Sprint 5 Slack 수집은 issue/comment/audit/outbox 원장을 downstream evidence로 사용할 수 있다. 다만 Phase 2 운영 gate가 열린 상태이므로 Slack event 작업과 병행하더라도 live Drive rehearsal owner·기한을 별도 운영 gate로 추적해야 한다.
- Sprint 5 ontology projection은 attachment binary가 아니라 issue/link/audit와 Drive manifest metadata만 참조하고, provider file ID나 secret을 Slack payload·knowledge text에 넣지 않는다.

## 9. 근거 파일

- `backend/migrations/versions/20260820_0006_sprint4_issue_drive_reports.py`
- `backend/src/tc_backend/db/issues.py`
- `backend/src/tc_backend/db/attachments.py`
- `backend/src/tc_backend/db/reports.py`
- `backend/src/tc_backend/infrastructure/google_drive/`
- `backend/tests/repository/test_sprint4_postgresql.py`
- `backend/tests/integration/test_google_drive_adapter.py`
- `backend/tests/unit/google_drive/`
- `apps/api/tests/test_sprint4_api_postgresql.py`
- `packages/api-client/src/schema.gen.ts`
- `apps/web/src/issue-workspace-page.tsx`
- `apps/web/src/sprint4-dashboard-pages.tsx`
- `apps/web/src/cycle-workspace-page.tsx`
