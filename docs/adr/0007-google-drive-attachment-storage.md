# ADR-0007: 전용 Google My Drive에 첨부 binary 저장

| 항목 | 값 |
|---|---|
| ADR 번호 | 0007 |
| 날짜 | 2026-08-20 |
| 상태 | Superseded |
| 결정자 범위 | Architecture / BE / OPS / Security / QA |

## Context

> 2026-08-24: 사용자별 OAuth와 backend binary 중계를 제거하기 위해
> [ADR-0017](./0017-r2-direct-private-attachments.md)로 대체됐다. 아래 내용은 기존
> Google Drive 구현의 역사와 legacy attachment 호환 근거로만 보존한다.

이슈와 실행에는 screenshot·log 같은 첨부가 필요하지만 binary를 PostgreSQL에 넣으면 backup 크기와 DB I/O가 증가한다. 조직은 신규 storage 구독 없이 전용 무료 Google 계정의 15GB My Drive를 사용할 계획이다. 현재 전용 계정, OAuth client, refresh token, quota 실측은 없다.

## Decision Drivers

- 신규 구독 비용 0원과 15GB 한도 내 운영
- 최소 OAuth scope와 private file 원칙
- DB backup과 binary storage 분리
- file ID·path·checksum 기반 복구·이관 가능성
- browser가 Drive credential을 직접 받지 않는 구조

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| 전용 My Drive + `drive.file` | 앱이 만든 private file만 접근 | 제안 선택 |
| PostgreSQL BLOB | 원장 DB에 binary 포함 | backup·I/O 때문에 제외 |
| Shared Drive | 조직 공유 storage | MVP 제외 범위 |
| public object storage | S3 계열 신규 service | 비용·credential 때문에 제외 |
| server local disk | 단일 host filesystem | 복구·확장성 때문에 제외 |

## Decision

전용 Google 계정의 My Drive에 `drive.file` scope로 앱이 만든 첨부만 private 저장한다. PostgreSQL에는 owner, Drive file ID, logical path, MIME, size, checksum, 상태를 저장하고 upload/download는 backend가 중계한다. Upload 전 크기·MIME·malware 검사와 checksum을 수행하며 DB commit 실패의 고아 file은 보상 job으로 정리한다. Shared Drive와 public sharing은 MVP에서 사용하지 않는다.

Adapter·table·contract는 deterministic fake provider에서 구현·검증됐다. 다만 이 결정의 외부 경계인 전용 Google 계정, Production OAuth credential, 실제 My Drive private ACL·upload/download·quota가 검증되기 전까지 `Proposed`다.

## Consequences

### Positive

- PostgreSQL backup에서 큰 binary를 분리한다.
- 최소 scope와 private file로 계정 전체 노출을 줄인다.
- file manifest를 사용해 후속 storage로 이관할 수 있다.

### Negative

- 15GB quota, Google API rate limit, OAuth token lifecycle에 의존한다.
- DB transaction과 Drive write가 원자적이지 않아 보상 처리가 필요하다.
- backend proxy가 download bandwidth와 latency를 부담한다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| quota 초과 | 70/85/95% 경고와 크기 제한, export manifest를 제공한다. |
| OAuth 취소·만료 | encrypted refresh token, 재연결 flow, 기존 metadata 보존을 제공한다. |
| 고아 Drive file 또는 dangling metadata | checksum·status와 idempotent 보상/reconcile job을 둔다. |
| 악성 파일 | MIME/size 검증, 임시 격리, ClamAV scan 후 upload한다. |

## Validation Evidence

아래 항목은 historical evidence이며 현재 runtime 지원을 뜻하지 않는다.

- [Sprint 4 migration](../../backend/migrations/versions/20260820_0006_sprint4_issue_drive_reports.py)은 당시 attachment와 provider-specific integration 원장을 보존한다. 이 table/column은 legacy row와 migration graph compatibility를 위해 남는다.
- [Sprint 4 품질 게이트](../sprint-4/sprint-4-report.md)는 2026-08-20 당시 deterministic adapter test와 외부 credential 미검증 사실을 보존한다.
- 2026-08-24 ADR-0017 적용에서 Drive runtime, OAuth routes/settings, adapter, crypto dependency와 실행 test/fixture는 제거됐다. 이 ADR의 삭제된 source 링크를 현재 구현 근거로 오해하지 않도록 historical report만 연결한다.
- 실제 전용 계정과 credential 경계는 끝내 검증되지 않았으며 superseded 결정에서는 다시 검증하지 않는다.

## Revisit Triggers

- 실제 사용량이 15GB의 70%를 넘거나 월 증가량상 95% 도달이 임박한다.
- Shared Drive 또는 조직 표준 object storage를 사용할 권한·예산이 생긴다.
- Drive API SLA/rate limit이 합의된 첨부 흐름을 충족하지 못한다.

## Related Documents

- [ADR 원장](./README.md)
- [개발 명세 11장 R2 비공개 첨부](../test-management-development-spec.md)
- [개발 명세 20장 백업과 복구](../test-management-development-spec.md)
- [Sprint 4 이슈·Drive·리포트 품질 게이트](../sprint-4/sprint-4-report.md)
