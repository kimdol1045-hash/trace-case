# Sprint F1 edge/security code foundation

검증일: 2026-09-01 (Asia/Seoul)

## 판정

`PF-SF1-T01`~`T04`의 저장소 내 code/config/test foundation은 구현했다. 내부 listener는
명시적 CIDR include와 최종 deny를 요구하고, public listener는 Slack exact POST path만
제한적으로 proxy하며, query/header/body를 남기지 않는 access log와 trusted-proxy 계약을
고정했다. 그러나 실제 firewall/VPN, TLS/DNS/LB, secret store와 rotation, production R2
origin, real Slack signature는 설치·검증하지 않았으므로 네 task와 ADR-0006은 운영 완료로
판정하지 않는다. `docs/test-management-roadmap.md`는 변경하지 않았다.

## 구현과 감사 결과

| Task | 저장소 근거 | 현재 판정 |
|---|---|---|
| `PF-SF1-T01` | `internal.conf` 전체 server ACL, required allowlist include, forwarding-header overwrite, internal-only Slack exclusion | code/config foundation PASS; firewall/VPN external gate OPEN |
| `PF-SF1-T02` | public exact location, default 404, POST/JSON/no-query, 256 KiB, body/rate/connection/timeout bounds, no upstream replay | code/config foundation PASS; public TLS/real Slack external gate OPEN |
| `PF-SF1-T03` | `SecretStr` 기존 경계 재사용, normalized-path log 도입, Uvicorn access-log 금지/peer trust 문서, Slack rotation 절차 | redaction gap fixed; secret-store encryption/real rotation OPEN |
| `PF-SF1-T04` | React stored-XSS escaping, bound SQL attack test, Unicode/path traversal, RFC 5987 file header, proxy/config tests | focused deterministic and disposable PostgreSQL regressions PASS |

감사 중 확인한 실제 gap은 두 가지였다. 기존 internal Nginx는 host binding 주석만 있고
source allow/deny가 없어 listener publication 실수에 fail-closed하지 않았으며, 기본 Nginx/
Uvicorn access logging은 request target의 query를 보존할 수 있었다. 반대로 application
settings의 credential은 이미 `SecretStr`, Slack receiver는 raw body를 JSON parse 전에
constant-time signature/timestamp 검증, SQL search는 SQLAlchemy parameter expression,
React text rendering은 `dangerouslySetInnerHTML` 없이 escape, file declaration은 generated
object key와 분리된 filename/MIME/size/SHA/malware fail-closed 검증을 사용했다. 따라서
credential 저장 형식이나 Slack body pipeline은 불필요하게 재작성하지 않았다.

## Edge contract

- `00-security-common.conf`: `$uri`만 기록하는 JSON access log, exact real-IP trust include,
  Slack content-type map, per-client rate/connection zones.
- `internal.conf`: `/etc/nginx/tc/internal-allowlist.conf`와 security-header include가 없으면
  startup 실패. Example은 loopback health check 외 원격 source를 final `deny all`로 거부.
- `slack-http.conf`: exact `/integrations/slack/events`만 upstream으로 전달. 다른 public API/UI,
  query, non-JSON, oversized/slow request, retry-on-next-upstream을 허용하지 않음.
- Nginx는 inbound `Forwarded`를 제거하고 verified `$remote_addr`로 forwarding headers를
  교체한다. Uvicorn은 exact Nginx peer만 `--forwarded-allow-ips`로 신뢰하고 production
  `--no-access-log`로 실행해야 한다.
- API는 data/Slack response에 CSP `default-src 'none'`, no-store, nosniff, frame/referrer/
  permissions headers를 defense in depth로 추가한다. UI CSP는 exact R2 browser origin을
  deployment include에 명시할 때까지 cross-origin connect를 fail-closed한다.
- Slack signature headers와 request body는 proxy에서 변환하지 않는다. Body buffering은
  256 KiB/5초 bound 적용 후 동일 entity bytes를 API에 전달하며 implicit upstream retry를
  끈다.

## File/header hardening

Attachment download의 기존 ASCII-only `filename="..."` 조립은 한글 filename을 HTTP header로
안전하게 표현하지 못하는 gap이 있었다. `attachment_content_disposition`은 기존 filename
normalization을 다시 적용하고 ASCII fallback과 RFC 5987 `filename*=UTF-8''...`을 생성한다.
CR/LF, slash/backslash, NUL, control/format character, drive prefix, dot-relative와 NFKC 후
full-width slash가 거부된다. Download는 attachment disposition, exact content length/type,
`private, no-store`, `nosniff`를 유지한다.

## 실행 evidence

실행한 deterministic checks:

```text
node scripts/python.mjs -m pytest \
  scripts/tests/test_phase_f_edge_security.py \
  backend/tests/unit/slack/test_receivers.py \
  backend/tests/unit/test_object_storage.py apps/api/tests/test_api.py -q
# 38 passed

TC_TEST_DATABASE_URL=<disposable-tc_phase_f_test> python -m pytest \
  apps/api/tests/test_sprint2_cases_postgresql.py::test_case_search_binds_sql_injection_text_without_schema_side_effects \
  apps/api/tests/test_object_storage_api_postgresql.py::test_private_object_storage_lifecycle_operations_and_legacy_policy -q
# 2 passed; disposable database dropped after the run

pnpm --filter @tc/web test:unit -- slack-workspace-page.test.tsx
# 37 files passed, 191 tests passed, 1 live E2E skipped

node scripts/python.mjs -m ruff check <changed Python files>
# All checks passed

pnpm exec eslint apps/web/src/slack-workspace-page.test.tsx --max-warnings 0
# PASS

nginx:1.27-alpine nginx -t with all tracked configs/examples mounted
# syntax is ok; test is successful

pnpm check
# PASS: lint, TS/Python typecheck, 446 non-PostgreSQL Python tests,
# TS workspace tests, schemas, ADR coverage and OpenAPI/client drift

pnpm --filter @tc/web build
# PASS: production bundle built
```

Nginx 1.27 container routing smoke (credential-free, no upstream success claim):

| Probe | Result |
|---|---:|
| internal `/health` from unallowlisted Docker gateway | `403` |
| public `/api/health` | `404` |
| public GET Slack exact path | `403` (`limit_except POST`) |
| public POST Slack path with query | `400` |
| public POST Slack path with `text/plain` | `415` |
| public POST Slack path with 300 KiB body | `413` |

The captured public access-log records showed only method and normalized
`/integrations/slack/events` path; the probe query value was absent. Existing raw-body receiver
regressions still prove exact-byte success plus forged/stale rejection without a live secret.

## Exact changed files

- `deploy/nginx/00-security-common.conf`
- `deploy/nginx/internal.conf`
- `deploy/nginx/slack-http.conf`
- `deploy/nginx/internal-allowlist.conf.example`
- `deploy/nginx/trusted-proxies.conf.example`
- `deploy/nginx/internal-security-headers.conf.example`
- `deploy/nginx/README.md`
- `deploy/slack/README.md`
- `apps/api/src/tc_api/main.py`
- `apps/api/src/tc_api/routers/object_storage.py`
- `backend/src/tc_backend/infrastructure/object_storage/validation.py`
- `backend/src/tc_backend/infrastructure/object_storage/__init__.py`
- `apps/web/src/slack-workspace-page.test.tsx`
- `backend/tests/unit/test_object_storage.py`
- `apps/api/tests/test_api.py`
- `apps/api/tests/test_object_storage_api_postgresql.py`
- `apps/api/tests/test_sprint2_cases_postgresql.py`
- `scripts/tests/test_phase_f_edge_security.py`
- `docs/phase-f/sprint-f1-edge-security.md`

## Remaining external release gates

1. Install exact office/VPN and LB CIDRs, bind listeners and firewall rules, then run black-box
   allowed-VPN and Internet deny probes. Review that API/Web/container ports are not public.
2. Install DNS/TLS, preserve HSTS/security headers, validate exact CSP and production R2 CORS origin,
   and inspect normalized production logs without recording credentials or Slack bodies.
3. Configure the real secret store and least-privilege runtime identity; rehearse signing-secret,
   bot/app token, provider token and encryption-key rotation/rollback with version-only evidence.
4. In a test Slack workspace, verify URL challenge, good signature, forged/stale signature, rate/body
   bounds, quick ack and Socket-mode no-listener fallback. No such live success is claimed here.
5. Run the complete PostgreSQL migration/API suite against the staging copy before release; the two
   focused disposable-DB attacks above and green `pnpm check` are not production approval.
