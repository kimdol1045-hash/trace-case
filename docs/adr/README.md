# Architecture Decision Record 원장

이 디렉토리는 테스트케이스 관리·자동 생성 플랫폼의 아키텍처 결정을 한 결정당 한 파일로 기록한다. 개발 명세는 목표 상태를 설명하고, ADR은 선택 이유·대안·부작용·검증 근거·재검토 조건을 보존한다.

## 상태 기준

| 상태 | 의미 |
|---|---|
| `Proposed` | 선택안은 있으나 운영 환경, 외부 credential, 핵심 구현 또는 실측 근거가 아직 부족하다. 구현 착수 전에 담당 범위의 확인이 필요하다. |
| `Accepted` | 결정자 범위에서 선택이 고정됐고, 현재 repository의 구현·검사 또는 재현 가능한 로컬 evidence가 핵심 가정을 뒷받침한다. 구현 완료와 같은 뜻은 아니다. |
| `Superseded` | 새 ADR이 같은 결정의 canonical owner가 됐으며 기존 결정은 역사로만 보존된다. 대체 ADR 링크가 필수다. |

외부 계정·실제 ingress·운영 장비가 필요한 결정은 문서에 단정적으로 쓰여 있어도 검증 전에는 `Proposed`로 둔다. 상태 변경 시 기존 ADR을 조용히 덮지 말고 근거를 추가하거나 새 ADR로 대체하고 `Superseded` 관계를 명시한다.

## 인덱스

| 번호 | 제목 | 상태 | 구현·검증 정합성 요약 |
|---:|---|---|---|
| [0000](./0000-adr-template.md) | ADR template | — | 작성 양식이며 결정·상태 집계 대상이 아님 |
| [0001](./0001-monorepo-and-runtime-boundaries.md) | Monorepo와 runtime 경계 | Accepted | root workspace와 Web/API/Worker 별도 process clean smoke가 존재 |
| [0002](./0002-postgresql-system-of-record.md) | PostgreSQL 16 업무 원장 | Accepted | migration·Compose·startup capability check가 선택과 일치 |
| [0003](./0003-pgvector-in-postgresql.md) | pgvector를 PostgreSQL에 공동 배치 | Accepted | `vector(1024)` migration과 실제 거리 쿼리가 존재 |
| [0004](./0004-postgresql-job-queue-no-redis.md) | PostgreSQL 작업 큐와 Redis 제외 | Accepted | `SKIP LOCKED`, retry, heartbeat, idempotency 구현·테스트가 존재 |
| [0005](./0005-transactional-outbox.md) | Transactional outbox와 최종 일관성 | Accepted | case·execution·issue·Slack producer와 `SKIP LOCKED` consumer checkpoint/replay가 실제 PostgreSQL에서 원자성·멱등성을 검증 |
| [0006](./0006-private-network-and-slack-ingress.md) | 무로그인 사내망 경계와 Slack ingress mode | Proposed | HTTP/Socket 공통 pipeline·signature test와 proxy/runbook은 있으나 실제 ingress·Slack credential 없음 |
| [0007](./0007-google-drive-attachment-storage.md) | 전용 Google My Drive 첨부 저장 | Superseded | ADR-0017의 R2 직접 업로드로 대체; legacy row 호환 근거로 보존 |
| [0008](./0008-typed-ontology-ledger.md) | Typed ontology를 지식 원장으로 사용 | Accepted | DB-enforced relation/evidence, source revision, projection·embedding repository와 API/Web 검증 존재 |
| [0009](./0009-lightrag-as-derived-index.md) | LightRAG를 보조 index로 사용하고 fallback 제공 | Proposed | current storage config·adapter·reindex·장애 전환은 구현; live PGGraphStorage+AGE와 정상 rank merge 미실증 |
| [0010](./0010-local-ai-with-ollama.md) | Ollama 로컬 AI와 유료 API 제외 | Superseded | 수동 기본·Generation API 연동 결정인 ADR-0015로 대체 |
| [0011](./0011-contract-first-json-schema.md) | 계약 우선 JSON Schema | Accepted | Sprint 0/1 Schema 10개·fixture test와 OpenAPI/TypeScript client drift gate가 존재 |
| [0012](./0012-immutable-case-versions-and-cycle-snapshots.md) | 불변 케이스 버전과 사이클 snapshot | Accepted | immutable case/cycle pin migration, explicit diff/refresh, append-only attempt와 PostgreSQL/API/Web E2E가 존재 |
| [0013](./0013-postgresql-adjacency-list-folder-hierarchy.md) | PostgreSQL adjacency-list 폴더 계층과 lifecycle | Accepted | migration·repository·API concurrency와 100,000-case count benchmark가 핵심 topology 선택을 검증 |
| [0014](./0014-workspace-key-allocation-and-append-only-audit.md) | Workspace key allocation과 append-only audit transaction | Accepted | concurrent counter·rollback·idempotency와 append-only before/after audit test가 transaction boundary를 검증 |
| [0015](./0015-provider-neutral-generation-api.md) | 수동 기본과 provider-neutral Generation API | Proposed | `1.0.0` 계약·context snapshot 원장·adapter/validator는 검증됨; 실제 endpoint·credential과 Slack→Library E2E는 없음 |
| [0016](./0016-flat-category-work-items-and-start-gated-cycles.md) | 평면 카테고리·상위 일감 라이브러리와 시작 게이트 사이클 | Accepted | WORK_ITEM/LABEL scope, start gate, platform attempt/progress가 PostgreSQL/API/generated client/Web에 연결됨; issue persistence는 Sprint 4 |
| [0017](./0017-r2-direct-private-attachments.md) | R2 직접 업로드와 S3 호환 비공개 첨부 저장 | Proposed | actual private R2 lifecycle·scope·matched restore PASS; 배포 HTTPS browser·durable usage analytics 대기 |

## 개발 명세 canonical coverage

[decision coverage manifest](./decision-coverage.json)는 개발 명세의 주요 기술 결정을 정확히 하나의 canonical ADR에 연결한다. 제품 범위, 개별 endpoint, UI 문구와 세부 상태 전이는 기획·계약 규칙이며 독립적으로 되돌릴 architecture decision이 아니므로 이 표의 decision 수에 포함하지 않는다.

| 개발 명세의 주요 결정 | Canonical ADR | 상태 | 다른 ADR과의 경계 |
|---|---|---|---|
| Monorepo와 Web/API/Worker/Scheduler runtime 경계 | [0001](./0001-monorepo-and-runtime-boundaries.md) | Accepted | 저장 기술과 계약 형식은 다루지 않음 |
| PostgreSQL 16 단일 업무 원장 | [0002](./0002-postgresql-system-of-record.md) | Accepted | vector·queue·outbox의 세부 선택은 별도 결정 |
| pgvector 공동 배치와 embedding version/dimension | [0003](./0003-pgvector-in-postgresql.md) | Accepted | typed ontology의 의미 규칙과 분리 |
| PostgreSQL 작업 큐와 Redis 제외 | [0004](./0004-postgresql-job-queue-no-redis.md) | Accepted | 실행할 job의 내구성; domain event atomicity는 0005 |
| Transactional outbox와 projection 최종 일관성 | [0005](./0005-transactional-outbox.md) | Accepted | 원장 변경 event 기록; worker claim은 0004 |
| 무로그인 내부망 경계와 Slack HTTP/Socket ingress | [0006](./0006-private-network-and-slack-ingress.md) | Proposed | 인증 제품 범위와 transport 이후 domain 처리를 분리 |
| R2 direct private 첨부 storage와 MinIO 로컬 호환 | [0017](./0017-r2-direct-private-attachments.md) | Proposed | 신규 object storage 경계; 0007은 legacy Drive 호환 기록 |
| PostgreSQL typed ontology 지식 원장 | [0008](./0008-typed-ontology-ledger.md) | Accepted | authoritative 상태·근거; LightRAG index는 0009 |
| LightRAG 재생성 보조 index와 fallback | [0009](./0009-lightrag-as-derived-index.md) | Proposed | 원장 권한을 갖지 않는 retrieval layer |
| 수동 생성을 기본으로 하고 provider-neutral Generation API를 후속 연결 | [0015](./0015-provider-neutral-generation-api.md) | Proposed | provider·비용·runtime 경계; output 계약은 0011, ADR-0010을 대체 |
| JSON Schema 계약 원본과 OpenAPI client drift | [0011](./0011-contract-first-json-schema.md) | Accepted | enum·payload 표현; domain 저장 불변성은 0012 |
| 불변 case version·cycle pin·append-only attempt | [0012](./0012-immutable-case-versions-and-cycle-snapshots.md) | Accepted | 업무 transaction 의미; serialization 계약은 0011 |
| PostgreSQL adjacency-list folder hierarchy와 lifecycle | [0013](./0013-postgresql-adjacency-list-folder-hierarchy.md) | Accepted | move·active sibling unique·alias·count·soft delete/restore·system folder가 같은 topology invariant |
| Workspace-scoped key allocation과 append-only audit transaction | [0014](./0014-workspace-key-allocation-and-append-only-audit.md) | Accepted | counter·aggregate·audit가 함께 commit되는 mutation boundary |
| 평면 category→work item→case와 work item/label 시작 게이트 cycle | [0016](./0016-flat-category-work-items-and-start-gated-cycles.md) | Accepted | product-facing ownership·scope·input gate; folder storage topology는 0013, snapshot 불변성은 0012 |

0002/0003/0004/0005는 같은 PostgreSQL을 언급하지만 각각 원장, vector placement, 작업 선점, domain event atomicity라는 독립 변경 축이다. 0008/0009는 authoritative ledger와 derived index를 분리하며, 0011/0012는 wire/generation 계약과 영속 domain versioning을 분리한다. 0015는 생성 provider·배포·비용 경계를 소유하고 0010은 역사로만 보존한다. 0002는 storage engine만 정하므로 folder topology와 mutation transaction을 대신하지 않고, 0012의 append-only execution attempt도 전 도메인 audit ledger를 대신하지 않는다. Sprint 1 preflight의 move·uniqueness·alias·count·delete/restore·system folder는 adjacency-list topology와 함께 rollback해야 하므로 0013 하나가 소유하고, public key counter와 audit는 domain mutation의 같은 atomic commit boundary이므로 0014 하나가 소유한다. 0016은 0013의 저장 evidence를 폐기하지 않지만 child folder를 MVP 탐색 계약에서 제외하고 work item ownership·cycle scope·start gate를 canonical product boundary로 둔다.

## 2026-08-21 정합성 검증

| 검사 | 결과 |
|---|---|
| TypeScript/Python unit test | TypeScript 112개 + Python non-PostgreSQL 129개 통과 |
| JSON Schema contract test | strict Draft 2020-12 Schema 5개·synthetic fixture 12개 통과; Sprint 4 issue/Drive/report enum·payload 포함 |
| PostgreSQL 16 + pgvector disposable DB test | 기존 repository 43개, knowledge repository 5개, API PostgreSQL 10개 통과; base-to-head rebuild와 Sprint 1~6 회귀 포함 |
| Ontology enum 정합성 | migration의 node·predicate·status가 canonical JSON Schema와 정확히 일치 |
| Embedding 설정 정합성 | migration, startup config, BGE-M3 후보가 dimension 1024로 일치 |
| Redis 제외 | backend dependency lock과 개발 Compose에 Redis 없음 |
| Slack network 후보 | internal listener와 exact Slack POST listener가 분리되고 다른 public path는 404 |
| Sprint 1 folder count benchmark | 5,551 folders·100,000 cases에서 정확도 검증, warm-cache p95 84.53 ms로 1초 목표 통과 |
| Sprint 2 case ledger quality gate | 100,000 immutable case/version/step fixture에서 tree·list·search·compound filter 7개 warm API scenario p95 17.13~251.35 ms, actual HTTP lifecycle와 no-fallback UI history 통과; substring seq scan·offset cursor·production concurrent/cold cache는 열린 항목 |
| Sprint 3 cycle execution quality gate | WORK_ITEM/LABEL preview→PLANNED explicit refresh→start pin→library 새 version에도 pin 유지→FAIL/retry/BLOCKED→complete lock→reopen, 5개 latest-attempt progress와 no-fallback Web PASS |
| Sprint 4 issue/Drive/report quality gate | PostgreSQL/FastAPI/generated client/Web issue ledger와 deterministic fake Drive 13 integration·24 OAuth/validation unit, private proxy·quota·orphan reconcile·JSON Reports PASS; live My Drive는 미실증 |
| Sprint 1 구현 정합성 | folder/key/audit migration, repository transaction, API/Web client, concurrency와 HTTP smoke가 ADR-0013/0014 선택과 일치 |
| Sprint 6 knowledge gate | outbox consumer/checkpoint, typed ontology, BGE-M3/pgvector, API/Web와 LightRAG unavailable fallback PASS |
| 미구현·외부 gate | live Slack, LightRAG `PGGraphStorage`+AGE, Google OAuth/My Drive·ClamAV 운영·실제 QA corpus rehearsal이 아직 없음 |
| ADR architecture gate | formal ADR 16개, canonical decision 15개, Accepted 11·Proposed 4·Superseded 1, broken local link 0개 |

따라서 Sprint 1의 두 핵심 rollback 축, Sprint 3 immutable cycle ledger·start-gated boundary와 Sprint 6 transactional outbox/typed ontology는 구현·실측 근거에 따라 `Accepted`다. ADR-0007은 live 전용 My Drive가 없고 ADR-0009는 actual LightRAG `PGGraphStorage`+AGE 초기화가 없어 `Proposed`로 유지한다. `Accepted`는 해당 선택의 핵심 가정이 재현됐다는 뜻이며 production role grant나 다른 외부 Phase 조건까지 완료됐다는 뜻은 아니다.

## 2026-08-21 Sprint 5 정합성 증분

| 검사 | 결과 |
|---|---|
| 전체 quality | `pnpm check` PASS; TypeScript unit 100개, Python non-PostgreSQL 95개, schema·ADR·OpenAPI/client drift 포함 |
| PostgreSQL 16 + pgvector | repository 43개 PASS; base→head rebuild, Slack FK/unique/check/append-only, 동일 event·rollback·concurrency·recent sync 포함 |
| FastAPI/worker/fake Slack | API PostgreSQL 8개 PASS; primary E2E가 서명/오래된 요청, URL verification, allowlist/private deny, 동일 event 3회, reply-first, quiet, parse, edit/delete, pagination/sync, 429→DEAD를 연결 |
| Generated client/Web | Slack status/list/detail/event/job/sync/quiet client contract와 Web unit 49개·production build·actual API no-fallback browser smoke PASS |
| ADR-0005 경계 | Slack revision·issue mutation의 atomic producer/rollback과 knowledge `SKIP LOCKED` consumer/checkpoint/replay를 실제 PostgreSQL에서 재현해 `Accepted`; 운영 장기 lag·retention은 미실증 |
| ADR-0006 경계 | HTTP/Socket 공통 pipeline, exact-path proxy 후보, manifest/runbook은 존재; live App/DNS/TLS/firewall은 없어 `Proposed` 유지 |
| Live gate | 실제 Slack workspace·credential·test channel이 없어 App 설치·자동 수집과 Sprint 5 live 종료는 미완료 |

위 표는 2026-08-20 표의 “Slack adapter 미구현” 부분만 갱신한다. 상세 command와
미완료 조건은 [Sprint 5 보고서](../sprint-5/sprint-5-report.md)에 둔다.

## 작성 규칙

1. 다음 번호를 사용하고 `NNNN-kebab-case-title.md`로 만든다.
2. [0000 template](./0000-adr-template.md)의 모든 항목을 채운다.
3. 한 ADR에는 독립적으로 되돌리거나 재검토할 수 있는 결정 하나만 둔다.
4. `Validation Evidence`는 실행한 검사와 아직 없는 근거를 함께 적는다.
5. code, migration, 배포 manifest, 계약과 ADR이 다르면 ADR 상태를 올리지 않고 차이를 명시한다.
6. secret, token, 실제 사용자 데이터는 ADR에 기록하지 않는다.

## 관련 기준 문서

- [개발 명세서](../test-management-development-spec.md)
- [제품 기획서](../test-management-product-plan.md)
- [개발 로드맵](../test-management-roadmap.md)
- [Sprint 0 계약·sample 결과](../sprint-0/contracts-and-samples.md)
- [Sprint 0 인프라·AI spike](../sprint-0/infra-and-ai-spike.md)
- [Sprint 1 통합·회귀·성능 보고서](../sprint-1/sprint-1-report.md)
- [Sprint 3 사이클·실행 품질 게이트](../sprint-3/sprint-3-report.md)
- [Sprint 4 이슈·Drive·리포트 품질 게이트](../sprint-4/sprint-4-report.md)
- [Sprint 5 Slack 수집·원문 원장 품질 게이트](../sprint-5/sprint-5-report.md)
