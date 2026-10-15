# ADR-0017: R2 직접 업로드와 S3 호환 비공개 첨부 저장

| 항목 | 값 |
|---|---|
| ADR 번호 | 0017 |
| 날짜 | 2026-08-24 |
| 상태 | Proposed |
| 결정자 범위 | Product / Architecture / BE / FE / OPS / Security |

## Context

이슈와 실행 증거 파일은 PostgreSQL 밖에 보관해야 하지만, Google Drive 방식은 팀 사용자가 OAuth 연결 상태와 전용 계정 quota를 관리해야 하고 API 서버가 업로드 binary 전체를 중계한다. 이 제품은 로그인 없는 단일 팀 내부 도구이므로 사용자별 저장소 연결이 제품 흐름에 노출되지 않아야 한다. 첨부파일은 로컬 파일 선택만으로 저장되고 무기한 보존돼야 하며, 개발 환경에서도 production과 같은 object storage 계약을 재현해야 한다.

Cloudflare R2는 S3-compatible private bucket과 presigned PUT을 제공한다. 저장·요청 비용은 배포 시점의 승인된 rate를 설정해 계산하며 무료 또는 고정 가격을 코드에서 가정하지 않는다. 실제 R2 계정·bucket·CORS·credential 검증은 아직 없다.

## Decision Drivers

- Google OAuth와 사용자별 연결 화면 제거
- API 서버의 upload bandwidth·임시 파일 부담 제거
- private object와 짧은 단일-object 권한
- checksum·size·MIME 완료 검증 뒤에만 metadata 원장 생성
- production R2와 로컬 MinIO의 동일 S3-compatible 계약
- 기존 Google Drive attachment row의 명시적 migration-required 호환
- provider credential과 원본 object URL의 browser 비노출

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| Cloudflare R2 + presigned PUT | browser가 5분 URL로 private bucket에 직접 업로드 | 선택 |
| Google My Drive + backend proxy | OAuth account와 15GB quota, API binary 중계 | legacy 호환만 유지 |
| PostgreSQL BLOB | 업무 원장에 binary 저장 | backup·I/O 증가로 제외 |
| server local disk | 단일 host filesystem | 복구·확장성 때문에 제외 |
| public bucket/domain | 인증 없는 object 공개 | private 증거 원칙 때문에 제외 |

## Decision

- 신규 첨부파일의 production provider는 private Cloudflare R2 bucket이다. 로컬 개발은 localhost에만 노출한 MinIO를 같은 path-style S3 계약으로 사용한다.
- API는 owner, 파일명, MIME, size, browser SHA-256과 idempotency key를 검증하고 단일 object·`PUT`·`Content-Type`·`x-amz-meta-sha256`에 묶인 5분 presigned URL을 발급한다.
- browser는 파일을 API 서버에 보내지 않고 presigned URL로 직접 PUT한 뒤 서명된 upload claim을 완료 endpoint에 제출한다.
- 완료 endpoint는 S3 `HEAD`로 size, MIME, SHA-256 metadata를 비교한 뒤 object bytes를 다시 읽어 size·SHA-256·sniffed MIME과 fail-closed ClamAV 결과를 검증한다. 모두 일치하고 CLEAN일 때만 PostgreSQL `attachments` metadata와 audit를 원자적으로 생성한다.
- upload claim은 배포 master key로 HMAC 서명하며 owner, object key, metadata, idempotency key, 만료 시각을 담는다. 별도 session table은 두지 않는다.
- R2 API credential은 API process에만 둔다. browser에는 짧은 PUT URL만 전달하며 bucket은 public access를 켜지 않는다.
- download는 기존 private API proxy를 유지한다. delete는 metadata soft delete를 먼저 남긴 뒤 provider object를 제거하고 실패 시 retry 상태를 기록한다.
- `attachments.object_key`가 provider-neutral identity다. legacy `drive_file_id`와 `GOOGLE_DRIVE` enum은 기존 migration/row를 읽기 위한 schema compatibility로만 보존한다. legacy content/delete는 typed migration-required conflict이며 신규 row는 `storage_provider=R2`만 허용한다.
- R2 bucket CORS는 실제 Web origin만 허용하고 `PUT`·`GET`·`HEAD`, `Content-Type`, `x-amz-meta-sha256`만 열어야 한다. presigned URL은 bearer token으로 취급해 log·audit·history에 저장하지 않는다.
- 무기한 보존은 lifecycle 자동 삭제를 설정하지 않는다는 뜻이며 무료를 보장한다는 뜻이 아니다. 승인된 storage/request budget과 rate를 설정하고 70%·85%·95% 경고를 관측한다.
- orphan list/delete, metadata-object HEAD 비교, full-byte restore sample을 operator job endpoint로 제공한다.

## Consequences

### Positive

- 사용자는 Google 계정을 연결하지 않고 로컬 파일만 선택하면 된다.
- 큰 upload body가 API 서버와 임시 디스크를 통과하지 않는다.
- object별 짧은 권한과 private bucket을 함께 유지한다.
- R2 장애 없이 local MinIO에서 upload flow를 재현할 수 있다.
- provider-neutral key로 후속 S3-compatible storage 이관이 단순해진다.

### Negative

- R2 bucket CORS가 잘못되면 browser PUT이 실패한다.
- upload PUT과 metadata commit은 하나의 transaction이 아니므로 완료되지 않은 object 정리 작업이 필요하다.
- SHA-256 계산이 browser memory·CPU를 사용한다.
- 비용은 실제 R2 rate와 durable provider analytics를 별도로 대조해야 한다.
- legacy row는 제품에서 읽거나 삭제할 수 없으므로 승인된 offline migration이 끝날 때까지 content 접근이 제한된다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| presigned URL 유출 | object·method·header에 묶인 5분 TTL, URL log 금지 |
| MIME 또는 content 바꿔치기 | signed Content-Type, 실제 byte size·SHA-256, sniffed MIME과 malware scan 대조 |
| CORS 오설정 | 허용 origin·PUT·두 header를 runbook과 smoke test로 고정 |
| PUT 성공 후 완료 호출 유실 | deterministic object key와 idempotent complete, 미완료 prefix 정리 job 추가 |
| credential 노출 | deployment secret만 사용하고 API response·audit에서 제외 |
| legacy Drive 단절 | metadata-only inventory와 typed migration-required 응답, 승인된 offline export/import 정책 |
| 비용 상한 초과 | 70/85/95 관측 endpoint와 R2 analytics/billing 대조를 운영 gate로 추가 |

## Validation Evidence

- [provider-neutral migration](../../backend/migrations/versions/20260824_0011_object_storage_attachments.py)은 `object_key`, provider+key unique와 legacy Drive backfill을 정의한다.
- [S3-compatible adapter](../../backend/src/tc_backend/infrastructure/object_storage/adapter.py)는 dependency 추가 없이 SigV4 presigned PUT, authenticated HEAD/GET/DELETE/ListObjectsV2를 구현한다.
- [upload runtime](../../apps/api/src/tc_api/object_storage_runtime.py)은 HMAC upload claim, quarantine key, 5분 expiry, ClamAV와 request/cost observation을 구현한다.
- [object storage API](../../apps/api/src/tc_api/routers/object_storage.py)는 owner 확인, upload session, 실제 bytes 검증·scan, idempotent metadata commit, orphan·metadata·restore·legacy inventory endpoint를 제공한다.
- [generated Web client](../../packages/api-client/src/client.ts)는 browser SHA-256 → session → direct PUT → complete 흐름을 사용한다.
- [local Compose](../../deploy/compose.dev.yml)는 private MinIO, bucket initialization과 ClamAV를 제공한다.
- [운영 runbook](../../deploy/object-storage/README.md)은 R2 최소 권한/CORS, 검증, rotation, 장애, backup/recovery와 legacy migration을 고정한다.
- unit/API/PostgreSQL test와 opt-in MinIO integration은 validation, SigV4, token, scan fail-closed, private lifecycle, job/관측/legacy 정책을 검증한다.
- **2026-08-25 검증:** actual private R2의 public-disabled/scope, localhost CORS 204, session→direct PUT→HEAD/byte 검증→ClamAV→proxy download→integrity/restore→delete와 matched PostgreSQL restore sample을 통과했다.
- **아직 미검증:** 배포 HTTPS origin CORS의 물리 browser 다중 첨부와 durable provider analytics/usage alert. 이 외부 경계와 임시 credential rotation 때문에 상태는 `Proposed`다.

## Revisit Triggers

- 실제 R2 Standard storage 또는 요청 비용이 승인된 월 상한에 접근한다.
- 미완료 object 정리량이 운영 목표를 넘는다.
- R2 장애 또는 latency가 QA 증거 업로드 SLO를 충족하지 못한다.
- 조직 표준 S3-compatible storage나 data residency 정책이 바뀐다.
- legacy Drive attachment가 모두 이관되어 schema compatibility column/table 제거가 가능해진다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0007 Google Drive 첨부](./0007-google-drive-attachment-storage.md)
- [개발 명세 11장](../test-management-development-spec.md)
- [Cloudflare R2 presigned URL 문서](https://developers.cloudflare.com/r2/api/s3/presigned-urls/)
- [Cloudflare R2 CORS 문서](https://developers.cloudflare.com/r2/buckets/cors/)
- [Cloudflare R2 가격](https://developers.cloudflare.com/r2/pricing/)
