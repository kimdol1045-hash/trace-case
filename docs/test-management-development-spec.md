# 테스트케이스 관리·자동 생성 플랫폼 개발 명세서

| 항목 | 내용 |
|---|---|
| 문서 버전 | v1.2 |
| 작성일 | 2026-08-20 |
| 기준 문서 | [제품 기획서 v0.4](./test-management-product-plan.md) |
| 대상 릴리스 | MVP |
| 문서 목적 | 개발·테스트·배포에 필요한 구현 기준을 하나의 명세로 고정 |

실행 순서와 진행 체크는 [개발 로드맵](./test-management-roadmap.md)을 따른다.
아키텍처 선택의 이유, 검증 근거와 재검토 조건은 [ADR 원장과 canonical coverage](./adr/README.md)에 기록하며, 명세 section별 기계 검증 mapping은 [decision coverage manifest](./adr/decision-coverage.json)를 따른다.

## 1. 개발 목표와 범위

단일 QA 팀이 사내망 또는 VPN에서 사용하는 Zephyr형 테스트 관리 도구를 구축한다. 테스트 자산 관리, QA 실행, 내부 이슈, Slack 일감 수집과 온톨로지 검색을 하나의 제품으로 제공한다. 테스트케이스 작성의 기본값은 수동이며 자동 생성은 내장 AI 화면이 아니라 후속 Generation API 연동으로 등록한다.

MVP의 완료 상태는 다음과 같다.

1. 좌측 평면 카테고리와 우측 상위 일감·하위 테스트케이스 구조에서 버전이 있는 테스트케이스를 관리한다.
2. 상위 일감 또는 Jira형 라벨 범위로 고정된 케이스 버전을 선택하고, 사이클 시작 후에만 WEB·AOS·iOS별 QA 결과와 비고·재실행 이력을 기록한다.
3. 실패 실행에서 내부 이슈를 만들고 private R2 첨부파일을 연결한다.
4. 허용된 Slack 채널의 일감과 전체 thread를 자동 수집한다.
5. Slack 대화, 케이스, 실행, 이슈를 출처가 있는 QA 온톨로지로 투영한다.
6. 후속 Generation API가 현재 유효한 결정과 과거 히스토리를 입력으로 받아 생성 결과를 이 관리 도구에 등록할 수 있다.

### 1.1 MVP 제외 범위

- 로그인, 개인 계정, 역할·승인 체계
- 복수 조직·프로젝트·tenant
- 외부 Jira 연동
- CI/CD 또는 자동화 테스트 직접 실행
- 모바일 네이티브 앱
- 유료 LLM·embedding API
- 내장 AI 생성 화면과 생성 모델 운영; MVP Web은 수동 생성을 기본으로 하고 Generation API 연결점만 제공
- R2 이외의 추가 관리형 클라우드 파일 저장소

## 2. 확정 구현 원칙

| 구분 | 구현 원칙 |
|---|---|
| 업무 공간 | 단일 워크스페이스 1개 |
| 접근 | UI·일반 API는 사내망/VPN 전용, 로그인 없음 |
| Slack 진입점 | HTTP Events API를 우선 사용하고 public ingress가 없으면 Socket Mode 사용 |
| 테스트케이스 | 기본은 수동 생성. 수동 작성은 각 step별 기대결과, 자동 생성 API payload만 케이스 전체 기대결과 1개 |
| 변경 이력 | 수정할 때마다 불변 버전 생성, 삭제는 soft delete |
| 실행 | 상위 일감·라벨 범위로 사이클에 케이스 버전을 고정하고 시작 후 WEB·AOS·iOS 결과·비고·재실행을 append-only 시도로 저장 |
| 이슈 | 제품 내부 이슈만 사용 |
| 첨부 | private Cloudflare R2, browser direct PUT, local MinIO, lifecycle 삭제 없이 보존 |
| 지식 원장 | PostgreSQL의 typed ontology가 상태·시간·출처의 원장 |
| LightRAG | 지식 추출과 검색 보조 수단이며 업무 원장이 아님 |
| 자동 생성 | 내장 생성 UI 없이 provider-neutral Generation API로 후속 연결; 관리 도구의 기본 생성 동작은 수동 |
| 비동기 처리 | PostgreSQL 기반 작업 큐와 worker 사용, MVP에 Redis를 추가하지 않음 |
| 비용 | 승인된 R2 저장·요청 budget과 rate를 설정하고 70%·85%·95%에서 운영 경고 |

## 3. 시스템 구성

```text
팀 브라우저
    │ 사내망/VPN
    ▼
React Web ───────► FastAPI API ──────────────► PostgreSQL + pgvector
                       │                              │
                       │ outbox/job                   │ 원장·감사·검색 인덱스
                       ▼                              ▼
                 Worker / Scheduler ───────────► LightRAG storage
                    │       │
                    │       ├───────────────► Local embedding(BGE-M3, 선택)
                    │       ├───────────────► Generation API(후속 연결)
                    └───────────────────────► Private R2 / local MinIO
                    │
Slack Events API ───┴─► /integrations/slack/events
또는 Socket Mode receiver
```

### 3.1 실행 컴포넌트

| 컴포넌트 | 책임 | 확장 방식 |
|---|---|---|
| `web` | 화면, 입력 검증, 상태 표시 | 정적 파일 또는 컨테이너 1개 |
| `api` | REST API, 트랜잭션, Slack HTTP 수신, R2 presign·검증·private proxy | 무상태 프로세스 |
| `worker` | Slack 동기화, 지식 추출, embedding, Generation API 조정, object integrity 작업 | 동일 큐를 소비하는 다중 프로세스 가능 |
| `scheduler` | quiet window, 재동기화, 백업·용량 점검 작업 등록 | 단일 leader |
| `postgres` | 업무 원장, 온톨로지, pgvector, 큐·outbox | MVP 단일 인스턴스 |
| `ollama` | 로컬 LLM·embedding 추론 | 장비 자원에 따라 별도 호스트 가능 |
| `clamav` | quarantine 첨부파일 악성 코드 검사 | fail-closed daemon, 운영에서 필수 |

### 3.2 권장 저장소 구조

```text
tc/
├── apps/
│   ├── web/                  # React + TypeScript + Vite
│   ├── api/                  # FastAPI HTTP application
│   └── worker/               # 비동기 작업·scheduler entrypoint
├── packages/
│   ├── api-client/           # OpenAPI 기반 TypeScript client
│   ├── ui/                   # 공통 UI component
│   └── contracts/            # JSON Schema와 enum 원본
├── backend/
│   ├── domain/               # 프레임워크 독립 도메인 규칙
│   ├── application/          # use case와 transaction 경계
│   ├── infrastructure/       # DB, Slack, object storage, Generation API, LightRAG adapter
│   └── migrations/           # PostgreSQL·ontology migration
├── prompts/                  # 버전이 있는 추출·생성 prompt
├── evals/                    # 파서·검색·생성 평가 데이터와 기준
├── deploy/                   # compose, reverse proxy, backup script
├── tests/                    # 통합·E2E·contract test
└── docs/
```

프론트엔드와 백엔드를 한 저장소에서 관리한다. API 계약과 enum은 한 원본에서 생성해 화면과 서버의 상태값 불일치를 방지한다.

## 4. 기술 스택

| 영역 | 선택 | 비고 |
|---|---|---|
| Web | React, TypeScript, Vite | 상태·폼·테이블 라이브러리는 구현 착수 시 고정 |
| API | Python, FastAPI, Pydantic | OpenAPI 자동 생성 |
| ORM·migration | SQLAlchemy, Alembic | migration은 배포 전에 자동 적용 |
| DB | PostgreSQL 16 이상 | `ltree` 사용은 검증 후 선택, 기본은 adjacency list |
| Vector | pgvector | embedding model·dimension을 metadata로 고정 |
| Graph RAG | LightRAG | PostgreSQL storage adapter 우선 검증 |
| Local embedding | BGE-M3 또는 연결 API의 embedding | typed ontology·pgvector 검색용; 생성 모델은 관리 도구에 내장하지 않음 |
| Generation | provider-neutral HTTP API | 후속 연결, 미연결 시 수동 생성만 제공, MVP는 팀 소유·무료 endpoint 우선 |
| 파일 | private R2 / local MinIO | binary는 object storage, metadata는 DB |
| 메시지 | Slack Events API 또는 Socket Mode | allowlist 채널만 처리 |
| 작업 큐 | PostgreSQL `background_jobs` | `FOR UPDATE SKIP LOCKED` 기반 |
| 테스트 | Pytest, frontend unit runner, Playwright | Slack·S3-compatible adapter contract test 포함 |
| 배포 | Docker Compose 또는 동등한 self-host 구성 | 외부 공개는 Slack 단일 경로만 |

모든 dependency와 모델 tag는 lock file과 배포 manifest에 고정한다. 버전 변경은 migration·회귀 테스트를 통과한 뒤 반영한다.

## 5. 도메인 모듈

| 모듈 | 주요 책임 | 다른 모듈과의 경계 |
|---|---|---|
| Workspace | 단일 설정, prefix, 기본 환경, quiet window | tenant 기능을 포함하지 않음 |
| Library | 디렉토리, 케이스, 버전, 스텝, 태그, 검색 | 실행 결과를 직접 변경하지 않음 |
| Cycle | 사이클, 고정 케이스 버전, 실행 시도, 리포트 | 케이스 현재 버전과 분리 |
| Issue | 내부 이슈, 재현 스텝, 연결, 코멘트 | 외부 이슈 adapter 없음 |
| Attachment | R2 direct upload, private proxy, quarantine, integrity·restore | binary를 DB에 저장하지 않음 |
| Slack | 이벤트 검증, 원문·thread revision, 일감 파싱 | AI 생성은 job으로 위임 |
| Knowledge | typed node·edge·evidence, 상태·시간, projection | LightRAG 결과를 검증 후 반영 |
| Generation | retrieval, prompt, JSON 검증, dedup, 제안 | 사람 수정 버전을 자동 덮어쓰지 않음 |
| Audit | 변경 전후 값과 시스템 source 기록 | 온톨로지 evidence와 별도 보존 |

## 6. 데이터베이스 상세 설계

### 6.1 공통 규칙

- PK는 정렬 가능한 UUID를 사용한다. 외부에 노출되는 `TC-000123`, `ISSUE-000123`은 별도 sequence로 발급한다.
- 모든 시각은 DB에서 UTC로 저장하고 UI에서 Asia/Seoul로 표시한다.
- 주요 테이블은 `created_at`, `updated_at`을 가진다.
- soft delete 대상은 `deleted_at`을 가지고 기본 조회에서 제외한다.
- JSON 속성은 `jsonb`를 사용하되 검색·무결성에 필요한 값은 정규 컬럼으로 둔다.
- FK 삭제 정책은 원장 이력을 보존하도록 `RESTRICT` 또는 soft delete를 기본으로 한다.
- 웹 사용자 ID는 저장하지 않는다. Slack 작성자와 선택 입력한 담당자·실행자 표시명은 source metadata로 저장한다.

### 6.2 핵심 테이블과 제약

#### 라이브러리

| 테이블 | 핵심 제약·인덱스 |
|---|---|
| `workspace_settings` | 단일 행 제약, prefix와 quiet window 범위 검증 |
| `folders` | 기존 adjacency-list 저장은 유지하되 MVP Test Library에는 active root를 평면 category로만 투영 |
| `folder_aliases` | `normalized_alias` unique, 하나의 대상 folder 연결 |
| `work_items` | `work_item_key` unique, category FK, summary·description·owner·labels·audit metadata |
| `test_cases` | `case_key` unique, `current_version_id` FK, `status` check |
| `test_case_versions` | `(test_case_id, version)` unique, 내용 수정 불가 |
| `test_steps` | `(test_case_version_id, sequence)` unique, sequence 1 이상 |
| `tags` | `normalized_name` unique |
| `test_case_tags` | `(test_case_id, tag_id)` unique |

케이스 저장 트랜잭션은 `test_case_versions`와 `test_steps`를 생성한 뒤 `test_cases.current_version_id`를 갱신한다. 클라이언트가 전송한 기준 버전이 현재 버전과 다르면 `409 VERSION_CONFLICT`를 반환한다.

#### 사이클·실행·이슈

| 테이블 | 핵심 제약·인덱스 |
|---|---|
| `test_cycles` | `PLANNED|IN_PROGRESS|COMPLETED`, `WORK_ITEM|LABEL` scope, 시작일·설명·`updated_at` index; release/build/environment/end date 없음 |
| `cycle_items` | `(cycle_id, test_case_id)` unique, `test_case_version_id` 필수 |
| `execution_attempts` | `(cycle_item_id, attempt_no, platform)` unique, WEB·AOS·iOS 결과·비고, 완료 후 결과 불변 원칙 |
| `issues` | `issue_key` unique, 상태·심각도 check |
| `issue_steps` | `(issue_id, sequence)` unique |
| `issue_links` | issue와 case 또는 attempt 중 하나 이상 필수, 중복 링크 방지 |
| `comments` | owner type·ID index, soft delete 없이 revision 또는 audit 보존 |

`FAIL` 또는 `BLOCKED` 실행 완료 시 `actual_result`가 비어 있으면 `422 ACTUAL_RESULT_REQUIRED`를 반환한다. 이슈 생성은 케이스 버전과 실행 결과를 복사한 스냅샷이므로 원본이 바뀌어도 자동 변경하지 않는다.

#### 첨부·연동

| 테이블 | 핵심 제약·인덱스 |
|---|---|
| `attachments` | owner type·ID index, `(storage_provider, object_key)` unique, checksum·size 보존; legacy `drive_file_id` nullable |
| `integration_settings` | legacy Drive provider credential ciphertext와 key version 저장 |
| `drive_usage_snapshots` | legacy Drive 이관 전 사용량·limit 기록 |
| `slack_channel_allowlist` | `(workspace_id, channel_id)` unique |
| `slack_events` | `event_id` unique, 처리 상태·재시도 시각 index |
| `slack_work_items` | `(workspace_id, channel_id, message_ts)` unique |
| `slack_threads` | `slack_work_item_id` unique |
| `slack_messages` | `(thread_id, message_ts, revision)` unique |

#### 지식·생성·작업

| 테이블 | 핵심 제약·인덱스 |
|---|---|
| `ontology_schemas` | version unique, 활성 version 1개 |
| `knowledge_nodes` | node type·status·entity reference index |
| `knowledge_edges` | source·predicate·target·validity 복합 index |
| `knowledge_evidence` | 대상 node/edge와 source revision index |
| `knowledge_embeddings` | `(owner_type, owner_id, model_version, content_hash)` unique, vector index |
| `generation_jobs` | work item·생성 version unique, 상태·재시도 index |
| `generation_contexts` | generation job과 고정 근거 revision의 append-only snapshot |
| `generation_suggestions` | 대상 case·기준 version·제안 version·상태 |
| `outbox_events` | aggregate와 event unique key, 미처리 상태 index |
| `background_jobs` | type, payload, status, run_after, attempts, lock owner·time |
| `audit_logs` | entity·occurred_at index, before/after jsonb |

### 6.3 검색 인덱스

- 케이스 ID·서머리: `case_key` btree와 정규화 title trigram 또는 full-text index
- 디렉토리: `parent_id`, `sort_order`, `deleted_at` 복합 index
- 사이클: `status`, `scope_type`, `scope_value`, `starts_at`
- 실행: `cycle_item_id`, `status`, `finished_at`
- 이슈: `status`, `severity`, `updated_at`
- Slack: channel·message timestamp, parse status, last synced time
- 지식: node type·status·validity, edge predicate, evidence source
- embedding: 데이터 규모를 측정한 뒤 HNSW index를 적용하고 작은 데이터에서는 exact search 유지

## 7. 상태와 핵심 도메인 규칙

### 7.1 테스트케이스

```text
ACTIVE ⇄ DEPRECATED
```

- 신규 수동·AI 케이스는 즉시 `ACTIVE`다.
- 내용 변경은 상태 전이가 아니라 새 버전 생성이다.
- 과거 버전 복원도 과거 행을 수정하지 않고 새 버전을 만든다.
- 사람이 한 번이라도 수정한 AI 케이스는 `manual_edit_at`을 기록한다.

### 7.2 사이클과 실행

```text
PLANNED → IN_PROGRESS → COMPLETED
              ↑          │
              └──REOPEN──┘
```

- `PLANNED`에서는 상위 일감 또는 Jira형 라벨 범위를 시각적으로 선택하며 결과·비고·이슈 연결을 비활성화한다.
- 시작 transaction이 범위에 포함된 현재 케이스 버전을 고정하고 상태를 `IN_PROGRESS`로 바꾸며 이후 scope 변경을 막는다.
- `COMPLETED`의 `UNEXECUTED`·`WIP` 항목은 경고만 하고 전환을 막지 않는다.
- 최신 버전 반영은 명시적 동작이며 변경 전후 diff를 보여준다.
- 실행 시도 상태는 `UNEXECUTED`, `WIP`, `PASS`, `FAIL`, `BLOCKED`를 사용하고 WEB·AOS·iOS별로 기록한다.
- `BLOCKED`는 비고에 차단 사유를 요구한다. 이슈는 library case가 아니라 cycle execution에 연결한다.
- 재실행은 기존 시도를 덮지 않고 `attempt_no + 1`로 생성한다.

### 7.3 이슈

```text
OPEN → IN_PROGRESS → RESOLVED → CLOSED
  ↑                     │          │
  └────── REOPENED ─────┴──────────┘
```

이슈 상태 변경과 모든 필드 수정은 audit log에 남긴다. 이슈가 종료되어도 케이스·실행 링크는 삭제하지 않는다.

### 7.4 지식 상태

| 상태 | 현재 생성 근거 | 처리 |
|---|---:|---|
| `ACTIVE` | 사용 | 유효 기간과 출처 필수 |
| `AMBIGUOUS` | 제외 | 확인 필요 사항으로만 표시 |
| `SUPERSEDED` | 제외 | 대체한 최신 결정과 연결 |
| `RETRACTED` | 제외 | 삭제·철회 근거 보존 |

별도 승인·escalation은 없다. 자동 판별 결과를 사람이 수정하면 audit log와 새 상태 이력을 남기고 영향받은 검색 projection을 다시 계산한다.

## 8. REST API 구현 규약

### 8.1 공통 계약

- base path는 `/api`, API 문서 버전은 OpenAPI에 기록한다.
- 성공 응답은 resource 또는 list를 직접 반환하고 목록에는 `items`, `nextCursor`, `total`을 둔다.
- 오류 응답은 `code`, `message`, `details`, `traceId`를 가진다.
- 입력 validation 오류는 `422`, 존재하지 않는 자원은 `404`, 버전 충돌은 `409`를 사용한다.
- 목록 API는 기본 50개, 최대 200개로 제한한다.
- 변경 API는 현재 version 또는 ETag를 받아 optimistic locking을 수행한다.
- 중복 위험이 있는 생성 API는 `Idempotency-Key`를 지원한다.
- 내부 날짜·시간은 ISO 8601 UTC로 전송한다.
- 무로그인이므로 CORS는 사내 UI origin으로만 제한하고 외부 API 접근을 네트워크에서 차단한다.

### 8.2 API 묶음

| 묶음 | 주요 endpoint | 구현 완료 조건 |
|---|---|---|
| Folder | `/folders/tree`, `/folders` | CRUD·move·sort·restore와 cycle 검증 |
| Test case | `/test-cases`, `/test-cases/{id}/versions` | 생성·수정·검색·diff·restore |
| Cycle | `/cycles`, `/cycles/{id}/items`, `/cycle-items/{id}` | 버전 고정·중복 방지·상태 전이 |
| Execution | `/cycle-items/{id}/attempts`, `/execution-attempts/{id}` | append-only 재실행·결과 검증 |
| Issue | `/issues`, `/execution-attempts/{id}/issues` | 자동 채움·링크·상태 이력 |
| Attachment | `/issues/{id}/attachment-upload-sessions`, `/attachment-upload-sessions/complete`, `/attachments/{id}/content` | R2 직접 PUT·HEAD 완료 검증·soft delete |
| Slack | `/integrations/slack/events`, `/slack-work-items/{id}/sync-thread` | 검증·idempotency·revision |
| Generation | `/slack-work-items/{id}/generate`, `/generation-jobs/{id}` | 비동기 상태·재시도·근거 snapshot |
| Knowledge | `/knowledge/nodes`, `/knowledge/search`, `/features/{id}/test-coverage` | typed filter·evidence·hybrid search |
| Object storage | `/integrations/object-storage/status` | 배포 R2/로컬 MinIO 준비 상태 |

세부 endpoint 목록과 테스트케이스 생성 요청 예시는 제품 기획서 10장을 기준으로 한다. 개발 중 계약이 바뀌면 기획서와 OpenAPI를 같은 변경에서 갱신한다.

## 9. 트랜잭션과 비동기 작업

### 9.1 원장 변경과 outbox

케이스 버전 생성, 실행 완료, 이슈 변경, Slack revision 저장은 업무 데이터와 `outbox_events`를 한 DB 트랜잭션에서 기록한다. Outbox publisher와 projection consumer가 구현되면 READY event를 at least once로 읽어 온톨로지·embedding·LightRAG projection을 idempotent하게 갱신한다. Projection 실패는 원장 트랜잭션을 되돌리지 않고 재시도한다.

Sprint 5 시점에는 issue·Slack mutation의 outbox producer와 atomic rollback test까지 존재한다. READY event publisher, consumer checkpoint, projection 성공/retry/DEAD와 replay는 아직 없으므로 outbox 최종 일관성 전체를 구현 완료로 보지 않는다.

### 9.2 PostgreSQL 작업 큐

1. API 또는 scheduler가 `background_jobs`에 작업을 등록한다.
2. worker가 `run_after <= now()`인 작업을 `FOR UPDATE SKIP LOCKED`로 선점한다.
3. 성공 시 `SUCCEEDED`, 재시도 가능 실패는 지수 backoff를 적용한다.
4. 최대 시도 횟수를 넘으면 `DEAD`로 전환하고 관리 화면에 노출한다.
5. 오래된 `RUNNING` lock은 heartbeat 기준으로 회수한다.

주요 작업 유형은 다음과 같다.

- `SLACK_EVENT_PROCESS`
- `SLACK_THREAD_SYNC`
- `WORK_ITEM_PARSE`
- `KNOWLEDGE_EXTRACT`
- `KNOWLEDGE_PROJECT`
- `EMBED_CONTENT`
- `GENERATE_TEST_CASES`
- `RECOMPUTE_SUGGESTION`
- `DRIVE_USAGE_SYNC`
- `REINDEX_KNOWLEDGE`

작업 payload에는 원문 전체보다 원장 ID를 넣어 재시도 시 최신 상태를 안전하게 조회한다.

## 10. Slack 연동 상세

### 10.1 앱 권한과 구독

- allowlist 공개 채널 수집은 `message.channels` event와 필요한 history scope를 사용한다.
- private 채널은 기본 제외하며 팀 전체 공유가 확인된 채널만 명시적으로 허용한다.
- bot을 대상 채널에 참여시키고 workspace·channel ID를 설치 설정으로 입력한다.
- HTTP 방식에서는 Slack 서명, timestamp 허용 범위, URL verification을 구현한다.
- Socket Mode 방식에서도 동일한 event normalization과 idempotency 처리를 사용한다.

### 10.2 이벤트 처리

1. 수신 즉시 signature와 timestamp를 검증한다.
2. `event_id` unique insert로 재전송을 중복 제거한다.
3. allowlist와 message subtype을 검사한다.
4. 원문 payload를 저장하고 빠르게 성공 응답한다.
5. root는 `slack_work_items`, reply는 해당 `slack_threads`에 연결한다.
6. 수정·삭제는 기존 원문을 덮어쓰지 않고 새 `slack_message_revisions` row와 delete tombstone으로 보존한다.
7. 마지막 thread 변경 시각에서 quiet window 10분 뒤 파싱·지식화·생성을 예약한다.

Slack 재시도 헤더와 event ID를 함께 사용하고, 순서가 뒤바뀐 이벤트는 Slack message timestamp와 revision 시각으로 정렬한다. 이벤트 누락을 대비해 최근 N일 thread를 scheduler가 증분 재동기화한다.

### 10.3 일감 파싱 결과

```json
{
  "developmentItem": "비밀번호 재설정 기능 개선",
  "requestedMenu": "회원 > 계정관리 > 비밀번호",
  "featureDescription": "이메일 인증 후 비밀번호 변경",
  "requestDetails": "인증번호 유효시간 5분, 5회 오류 시 잠금",
  "missingFields": [],
  "confidence": 0.96,
  "sourceMessageRevisions": ["message-revision-id"]
}
```

형식이 자유로워도 의미 기반 파싱을 수행한다. 없는 값은 생성하지 않고 `missingFields`와 확인 필요 사항으로 남긴다.

### 10.4 Sprint 5 구현·검증 경계

- FastAPI HTTP ingress는 exact raw body를 서명·timestamp 검증한 뒤 URL verification
  또는 event/job 등록까지만 동기 처리한다.
- Socket receiver는 같은 normalized event와 repository 등록 pipeline을 사용하며
  `TC_SLACK_MODE=disabled|http|socket` 한 값으로 receiver를 선택한다.
- PostgreSQL transaction은 event delivery, work item/thread/message, append-only
  revision, audit, outbox producer, quiet job을 함께 commit/rollback한다.
- Thread worker는 `conversations.replies` pagination과 sync watermark를 사용하고,
  `Retry-After`를 queue retry에 전달하며 max attempt 뒤 `DEAD`를 API/Web에 노출한다.
- FastAPI, generated TypeScript client, Integrations와 Slack Work Items Web route는
  actual API만 사용하고 네트워크 실패에서 mock 원장으로 fallback하지 않는다.
- 상세 결정론적 gate는 [Sprint 5 보고서](./sprint-5/sprint-5-report.md), App 설치와
  mode 전환은 [Slack runbook](../deploy/slack/README.md)을 따른다.
- 실제 Slack App/workspace/channel credential이 없어 live 자동 수집은 미검증이다.
  Typed ontology extraction/search와 Generation API는 Sprint 6~8 범위다.

## 11. R2 비공개 첨부 구현

### 11.1 배포 설정

1. production에 private Cloudflare R2 bucket을 만든다.
2. 해당 bucket object read/write로 제한한 S3 API credential을 배포 secret에 넣는다.
3. Web origin만 허용하는 CORS를 설정하고 `PUT`·`GET`·`HEAD`, `Content-Type`, `x-amz-meta-sha256`를 허용한다.
4. public bucket과 custom public domain은 사용하지 않는다.
5. 로컬에서는 Compose의 localhost MinIO와 private `tc-attachments` bucket을 사용한다.

사용자별 계정 연결이나 OAuth는 없다. Access key와 secret key는 API process에만 주입하며 Web bundle, API response, audit, log에 기록하지 않는다.

### 11.2 업로드·다운로드

- browser는 로컬 파일 SHA-256을 계산하고 API에 파일명, MIME, size, checksum과 idempotency key를 보낸다. 기본 제한은 파일당 50MB다.
- API는 owner와 metadata를 검증한 뒤 object 하나의 `PUT`에만 유효한 5분 presigned URL과 HMAC upload claim을 발급한다.
- browser가 private bucket으로 직접 PUT하므로 binary는 API server와 임시 disk를 통과하지 않는다.
- 완료 API는 S3 `HEAD` 대조 후 object bytes의 size·SHA-256·sniffed MIME과 ClamAV CLEAN 결과를 검증한 뒤에만 `attachments` metadata와 audit를 commit한다.
- 다운로드는 backend가 owner·soft delete를 확인한 뒤 authenticated S3 GET을 streaming proxy한다.
- 삭제는 DB soft delete를 먼저 기록하고 provider object 삭제 결과와 실패 retry 상태를 audit한다.
- 기존 `GOOGLE_DRIVE` row는 metadata-only inventory와 typed migration-required 응답만 제공한다. 신규 row는 `storage_provider=R2`만 사용하고 legacy bytes는 승인된 offline migration으로 옮긴다.

### 11.3 용량과 장애

- R2 저장·요청 비용은 배포 시점의 승인된 rate를 명시해 계산한다. 무기한 보존은 무료를 뜻하지 않는다.
- upload URL은 bearer token이므로 5분 뒤 만료하고 URL 자체를 log하지 않는다.
- PUT 성공 뒤 완료 호출이 유실된 object는 quarantine prefix·생성 시각 기준 reconciliation job으로 정리한다.
- R2 장애나 CORS 실패는 명확한 오류를 반환하되 기존 metadata와 다운로드 상태를 보존한다.
- DB와 object HEAD metadata 무결성 검사, full-byte restore sample, provider-neutral object key/checksum을 유지한다.
- 저장량과 process-local request class를 budget과 비교해 70%·85%·95%·100% 경고를 노출하고 durable R2 analytics와 대조한다.

### 11.4 2026-08-24 구현·검증 경계

- `TC_OBJECT_STORAGE_ENABLED`, endpoint, bucket, region, access key, secret key와 scanner가 완전할 때만 `READY`를 반환한다.
- stdlib+httpx S3 adapter가 SigV4 presigned PUT과 authenticated HEAD/GET/DELETE/ListObjectsV2를 구현한다.
- unit/API/PostgreSQL test가 signed method/header/expiry, HMAC claim, filename/MIME/size/SHA, quarantine/scan, job, 관측과 legacy 정책을 검증한다.
- local Compose는 pinned MinIO, private bucket initializer와 multi-architecture ClamAV를 제공하며 live local lifecycle/scan smoke를 통과했다.
- actual R2 private bucket·bucket-scoped credential·localhost CORS·live lifecycle과 matched PostgreSQL restore는 2026-08-25 검증했다. 배포 HTTPS origin의 물리 browser 다중 첨부와 durable provider analytics는 아직 미검증이며 [ADR-0017](./adr/0017-r2-direct-private-attachments.md)과 [운영 runbook](../deploy/object-storage/README.md)에 기록한다.

## 12. 온톨로지와 LightRAG

### 12.1 지식 계층

```text
Evidence layer   : Slack message revision, case version, execution, issue
Derived layer    : 자동 추출 후보와 confidence
Controlled layer : 상태·유효 기간·typed relation이 검증된 현재 projection
Search layer     : pgvector와 LightRAG의 재생성 가능한 인덱스
```

원장과 controlled layer만 제품 판단에 사용한다. search layer가 유실되어도 원장에서 재생성할 수 있어야 한다.

### 12.2 schema v1

- 노드: `WORK_ITEM`, `MENU`, `FEATURE`, `REQUIREMENT`, `DECISION`, `CONSTRAINT`, `OPEN_QUESTION`, `RISK`, `TEST_CASE`, `EXECUTION`, `ISSUE`, `RELEASE`
- 관계: `AFFECTS`, `TARGETS`, `DISCUSSES`, `DERIVED_FROM`, `REFINES`, `SUPERSEDES`, `APPLIES_TO`, `VERIFIES`, `COVERS`, `INSTANCE_OF`, `FOUND_BY`
- 상태: `ACTIVE`, `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED`
- 시간: `recorded_at`, `valid_from`, `valid_to`
- 근거: source type·ID·revision·permalink·excerpt hash

predicate별 허용 source/target type을 schema에 선언하고 위반 관계는 controlled layer에 넣지 않는다.

### 12.3 projection 파이프라인

1. source bundle을 정규화하고 민감정보를 마스킹한다.
2. LLM이 schema에 맞는 node·edge 후보와 evidence를 추출한다.
3. deterministic validator가 필수 필드, type 조합, source revision을 검증한다.
4. 기존 지식과 동일·충돌·대체 후보를 찾는다.
5. 시간 순서와 대화 문맥으로 상태를 자동 판별한다.
6. typed ontology와 evidence를 트랜잭션으로 반영한다.
7. outbox로 pgvector와 LightRAG index를 갱신한다.

LLM 출력 자체를 SQL이나 상태 변경 명령으로 직접 실행하지 않는다.

### 12.4 hybrid retrieval

LightRAG PostgreSQL prototype은 current storage class인 `PGKVStorage`, `PGVectorStorage`, `PGDocStatusStorage`, `PGGraphStorage`를 사용한다. `PGGraphStorage`의 Apache AGE extension·graph 초기화가 필수 배포 capability다. AGE가 없거나 storage 초기화에 실패하면 LightRAG를 unavailable로 표시하고 authoritative PostgreSQL typed graph와 제품 `vector(1024)` index만 사용한다.

검색 후보는 다음 두 경로에서 병렬로 가져온다.

- Graph: 메뉴·기능에 연결된 현재 `ACTIVE` 결정, 요구사항, 케이스, 최근 실패 이슈
- Semantic: 일감·기능 설명과 유사한 지식, 케이스, 이슈

최종 순위는 상태, 출처 존재 여부, 기능 거리, 최신성, semantic score, 이슈 심각도를 조합한다. `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED`는 확정 컨텍스트에서 강제 제외한다. 검색 결과에는 score뿐 아니라 선택 이유와 evidence를 반환한다.

LightRAG 장애 시 typed graph + pgvector만으로 동일 API를 제공하는 fallback 경로를 둔다.

## 13. 자동 생성 API 연동

자동 생성 엔진과 모델 운영은 이 관리 도구의 기본 UI 범위가 아니다. 연동 API가 아래 입력·출력 계약을 만족하면 생성된 케이스를 Library에 등록한다. 사용자가 별도 API를 연결하지 않은 상태에서는 수동 생성만 제공하며, Integrations 화면은 연결 상태와 schema만 표시한다.

### 13.1 Generation API 입력

- 구조화된 Slack 일감
- root·thread의 현재 `ACTIVE` 결정·요구사항·제약
- 같은 메뉴·기능의 현재 테스트케이스
- 과거 `FAIL`, `BLOCKED`, 미종결 이슈
- 사용자가 해당 요청에 명시적으로 제공한 추가 자료
- 팀 생성 정책과 JSON Schema version

### 13.2 Generation API 출력 계약

```json
{
  "cases": [
    {
      "summary": "인증번호 5회 오류 시 인증 요청이 잠긴다",
      "steps": [
        {
          "sequence": 1,
          "precondition": "비밀번호 재설정이 가능한 활성 계정이 있다.",
          "action": "이메일 인증을 요청한다."
        },
        {"sequence": 2, "action": "잘못된 인증번호를 5회 입력한다."}
      ],
      "caseExpectedResult": "인증이 잠기고 추가 입력이 차단된다.",
      "priority": "HIGH",
      "labels": ["회원", "보안", "회귀"],
      "evidenceNodeIds": ["decision-id", "issue-id"],
      "generationReason": "현재 잠금 결정과 과거 우회 결함을 검증한다.",
      "inferenceOnly": false,
      "openQuestions": []
    }
  ],
  "categoryCandidate": {
    "categoryId": "folder-id",
    "score": 0.94,
    "alternatives": []
  }
}
```

### 13.3 수신 검증과 저장

1. JSON Schema validation을 통과하지 못한 API 응답은 등록하지 않고 실패 처리한다.
2. 5~10개 case, summary, 1개 이상 step과 케이스 전체 기대결과 1개를 필수 검증한다.
3. evidence ID가 generation context에 존재하는지 확인한다.
4. 근거 없는 수치·정책을 탐지해 `inferenceOnly` 또는 확인 필요로 이동한다.
5. exact key와 semantic similarity로 기존 케이스 중복 후보를 계산한다.
6. 요청 메뉴를 정규화·alias·semantic 순으로 현재 QA 보드의 category에 매핑한다.
7. 매핑 실패 시 미분류 도메인에 저장하고 새 도메인을 자동 생성하지 않는다.
8. Slack 일감을 QA 보드의 상위일감으로 생성·재사용하고 유효한 케이스를 하위 `ACTIVE` 케이스로 즉시 저장한다.
9. generation job, context snapshot, provider·model·schema version을 연결한다.
10. Slack thread에 생성 결과 링크와 케이스 수를 알린다.

기존 n8n 워크플로의 Jira 입력, `Depth1/2/3`, Google Sheets 출력은 사용하지 않는다.
해당 워크플로에서는 정상 흐름 중심, 명시된 특이사항만 포함, 추측성 일반 예외 제외,
기능별 독립 케이스 5~10개라는 작성 정책만 가져온다. Generation provider가 반환한
식별자를 그대로 신뢰하지 않으며 category·상위일감·source metadata는 관리 도구의
원장과 context snapshot을 기준으로 trusted adapter가 확정한다.

API 생성 케이스를 사람이 수정한 뒤에는 자동 응답으로 덮어쓰지 않는다. 이후 응답은 `generation_suggestions`로 저장하며 적용 여부는 사람이 정한다.

### 13.4 품질 평가

| 지표 | MVP 목표 |
|---|---:|
| 필수 필드 JSON validation 성공률 | 99% 이상 |
| 케이스별 evidence 보유율 | 100% |
| 현행 결정 회수율 | 기준 평가 세트에서 90% 이상 목표 |
| 폐기·모호 지식의 확정 근거 오사용 | 0건 |
| 동일 일감 재처리 중복 생성 | 0건 |
| 사람 수정 케이스 자동 덮어쓰기 | 0건 |
| 기존 케이스 중복 추천 precision | 평가 후 기준 확정 |

실제 Slack 일감·thread 예시를 익명화해 golden dataset으로 만들고 prompt·model 변경 전후를 같은 데이터로 비교한다.

## 14. 프론트엔드 구현 범위

| 화면 | 핵심 component·동작 |
|---|---|
| 대시보드 | Jira형 widget 배치, 미진행·진행 중·완료 cycle, work item/label scope, 시작된 플랫폼 실행, BLOCKED 비고, 열린 이슈, 카테고리 coverage. 즉시 실행 CTA는 두지 않음 |
| 테스트 라이브러리 | 좌측 평면 카테고리 + 우측 상위 일감·ordered 하위 case, 동일 일감 reorder·타 일감 pointer drag 이동, row 단일 클릭 상세 이동, hover/focus contextual drawer의 summary 수정·delete, filter |
| 상위 일감 상세 | Jira형 서머리·설명·category·owner·label·생성/수정 시각, 하위 case link, field-level history |
| 케이스 상세·편집 | 뒤로가기, 클릭 가능한 상위 일감, 세부 정보 우선 배치, `precondition/action/expected result` step table, Jira형 라벨, version·field diff history·delete; library issue link 없음 |
| 사이클 목록·생성·상세 | 공통 상태 카드와 검색 table landing, 생성할 때만 상위 일감·라벨 세로 선택 목록, 생성 후 확정 scope와 case snapshot 상세, stable double-click edit, 생성·삭제 |
| 사이클 실행 | 상세와 분리된 `queue / case script / result rail` route. 시작 전 진입·입력 잠금, 시작 후 상태별 집계·progress, 가장 오른쪽 비고·WEB·AOS·iOS·execution 이슈 연결; Actions 열 없음 |
| 이슈 목록·상세 | 공통 상태 카드·검색·사이클 필터 table landing, 별도 Jira형 상세 route, 재현 정보, 기대·실제 결과, Drive 첨부, 사이클 case 양방향 연결, 생성·삭제 |
| 자동 생성 연동 | Integrations에서 Generation API 연결 상태와 schema만 관리하며 별도 AI 생성 화면은 두지 않음 |
| 지식 상태 | 상태·이유·근거·시간, 수동 수정, 검증 케이스 |
| 온톨로지 탐색기 | node 중심 관계 탐색, 검색, coverage, 원문 이동 |
| 설정 | 별도 Settings 화면 없음. Slack·Drive·Generation API·DB 상태는 Integrations에서 조회·연결 |

### 14.1 UX 공통 규칙

- 서버 상태값은 임의의 화면 문자열이 아니라 공통 enum으로 렌더링한다.
- 위험한 삭제는 대상과 영향 범위를 표시하고 soft delete·복원 경로를 제공한다.
- 저장 충돌 시 상대 변경을 보여주고 새로고침 또는 내용 복사를 선택하게 한다.
- 비동기 작업은 `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`와 재시도 동작을 표시한다.
- Generation API 결과에는 생성 이유, 출처, 추론 여부, 확인 필요 사항을 케이스와 가까이 표시한다.
- 10만 개 케이스를 고려해 tree와 list를 분리 요청하고 목록을 가상화·pagination한다.
- 표시 값을 더블클릭할 때 editor의 너비·높이·타이포를 유지해 레이아웃이 확대되지 않게 한다.
- 변경 이력은 field와 before/after를 표시하고 category·work item·case·cycle의 생성·수정·삭제 시각을 보존한다.
- 사이클 성공률과 플랫폼 결과는 `PLANNED`를 제외한 최신 attempt만 집계한다.
- Dashboard는 위험과 현황 탐색에 집중하고 `사이클 실행` CTA를 노출하지 않는다. Reports KPI·리스크는 고정 예시 배열이 아니라 현재 cycle execution·issue mock 또는 원장 query에서 계산한다.

### 14.2 Zephyr 기능·UX parity 기준선

이 표는 SmartBear 공식 문서에서 확인한 정보 구조와 사용자 과업을 비교 기준으로만 사용한다. 상표, 문구, 이미지, 아이콘과 화면 배치는 복제하지 않으며, 우리 제품의 compact neutral visual language와 domain 계약을 유지한다. 특히 Jira 연동·로그인 없이 내부 워크스페이스와 내부 이슈를 사용한다. 수동 작성은 step별 기대결과가 기본이고 향후 자동 생성 API payload만 케이스 전체 기대결과 하나를 사용한다.

| 경험 영역 | 공식 문서에서 확인한 기준 | 우리 제품의 parity·차이 | Phase·owner | 현재 evidence / 완료 기준 |
|---|---|---|---|---|
| Test Library | Tests view가 케이스의 library이며, key·name 검색과 saved filter를 제공한다. [Test Plans](https://support.smartbear.com/zephyr-scale-server/docs/en/test-plans.html), [Feature Comparison](https://support.smartbear.com/zephyr/docs/en/zephyr-squad-to-zephyr-migration-guide/feature-comparison-squad-scale.html) | 좌측은 평면 카테고리, 우측은 상위 일감→테스트케이스 순서다. case row drag는 동일 일감 reorder 또는 타 일감 이동이며 category명과 case summary를 inline 수정한다. | Phase 1 / FE·BE, `P1-S1-T03~T07`, `P1-S2-T02~T10` | 2026-08-20 Browser smoke에서 category 전환·work item/detail·list edit/delete를 확인했다. reorder reducer 회귀는 통과했으며 physical pointer E2E와 실제 API·pagination은 미완료다. |
| Case Detail tabs | case detail에서 Details·Test Script 같은 구획, version/change history와 traceability 맥락을 제공한다. step script는 step별 expected result를 지원한다. [Parameters](https://support.smartbear.com/zephyr-scale-cloud/docs/en/test-cases/parameters.html), [Feature Comparison](https://support.smartbear.com/zephyr/docs/en/zephyr-squad-to-zephyr-migration-guide/feature-comparison-squad-scale.html) | Jira형 key·서머리·클릭 가능한 상위 일감·우측 property rail과 Details·Script·History를 사용한다. 수동 작성은 `precondition + action + expected result`만 사용하고 자동 생성 API만 case-level expected result mode를 사용한다. | Phase 1 / FE·BE, `P1-S2-T01~T06` | mock의 edit→save와 IME label dedup, field before/after history Browser smoke를 완료했다. 불변 version schema/API, diff·restore E2E는 미완료다. |
| Cycles | focused case set의 실행 진척을 추적한다. [Test Cycles](https://support.smartbear.com/zephyr-scale-server/docs/en/test-cycles.html) | 좌측은 미진행·진행 중·완료 cycle 목록이고, 상위 일감 또는 Jira형 라벨을 시각적으로 먼저 선택한다. release/build/environment/end date는 사용하지 않으며 시작 전 result·비고·issue link를 잠근다. | Phase 2 / FE·BE, `P2-S3-T01~T05`, `P2-S3-T09` | PostgreSQL snapshot·상태 전이·latest-attempt 진행률과 generated-client Web no-fallback flow가 통과했다. |
| Execution Player | cycle 내 순서대로 case를 실행하고 결과·환경·실행 이력을 남긴다. 호출한 reusable step도 player에서 펼쳐진다. [Test Cycles](https://support.smartbear.com/zephyr-scale-server/docs/en/test-cycles.html), [Parameters](https://support.smartbear.com/zephyr-scale-cloud/docs/en/test-cases/parameters.html) | 각 execution의 가장 오른쪽에 WEB·AOS·iOS 상태를 분리하고 PASS·FAIL·WIP·BLOCKED·UNEXECUTED를 즉시 집계한다. `Actions` 열은 제거하고 연결 이슈를 표에서 바로 선택한다. | Phase 2 / FE·BE, `P2-S3-T06~T10` | TC-1042 iOS WIP→PASS 시 PASS 3→4·WIP 1→0, ISSUE-104 연결·해제를 실제 브라우저에서 확인했다. rerun history E2E가 기능 완료 evidence다. |
| Issues | upstream은 Jira issue link와 coverage traceability를 중심으로 한다. [FAQ](https://support.smartbear.com/zephyr-scale-server/docs/en/faq.html) | Jira integration은 제외하고 실패 attempt에서 자체 `ISSUE-*` 원장 객체를 만든다. Jira형 편집 화면에 서머리·사전조건·테스트스텝·기대결과·실제결과·Drive 첨부와 하위 테스트케이스 링크를 둔다. | Phase 2 / FE·BE, `P2-S4-T01~T03` | PostgreSQL `ISSUE-*` sequence, FAIL/BLOCKED immutable reproduction snapshot, optimistic update, comment/link/history와 generated-client no-fallback Web E2E가 통과했다. |
| History | test case History와 step/execution 변경 추적을 제공한다. [Feature Comparison](https://support.smartbear.com/zephyr/docs/en/zephyr-squad-to-zephyr-migration-guide/feature-comparison-squad-scale.html) | workspace·folder는 Sprint 1 append-only audit를 우측 context에 표시하고, case는 immutable version, execution은 append-only attempt로 분리한다. source type에 UI·API·SYSTEM·SLACK·WORKER를 보존한다. | Phase 1~4 / BE·FE | Sprint 1 `audit_logs`, `/api/audit-logs`, `AuditHistory`; 이후 version/attempt/source history contract test. |
| Attachments | test case와 test step attachment를 지원한다. [Feature Comparison](https://support.smartbear.com/zephyr/docs/en/zephyr-squad-to-zephyr-migration-guide/feature-comparison-squad-scale.html) | MVP는 issue와 execution evidence attachment를 Google My Drive에 private 저장하고 backend로 중계한다. step attachment와 Jira storage는 지원하지 않는다. | Phase 2 / BE·FE·OPS, `P2-S4-T04~T08`, `P2-S4-T10~T11` | deterministic fake Drive에서 encrypted restart refresh, private folder·streaming upload, checksum·proxy·soft delete, exact quota와 orphan reconcile이 통과했다. 실제 My Drive credential·ACL은 미실증이다. |
| Search / Filter | library title을 key·name으로 즉시 검색하고 query filter를 저장할 수 있다. [Feature Comparison](https://support.smartbear.com/zephyr/docs/en/zephyr-squad-to-zephyr-migration-guide/feature-comparison-squad-scale.html), [REST API search](https://support.smartbear.com/zephyr-scale-server/api-docs/v1/) | Sprint 1은 `/` shortcut, status selector, clear loading/error/empty를 제공한다. ID·summary·folder·status·priority·type·tag의 server pagination과 saved filter는 Sprint 2에서 연결한다. | Phase 1 / FE·BE, `P1-S2-T07~T10` | toolbar component test는 baseline evidence이며 10만 fixture p95·compound filter contract가 완료 evidence다. |
| Reports | execution progress, tester·environment·folder scorecard와 filter 가능한 library/execution reports를 제공한다. [Reports and Analysis](https://support.smartbear.com/zephyr-scale-server/docs/en/reports-and-analysis.html) | 보고서 수의 동일성은 목표가 아니다. cycle·platform·domain·label·internal issue를 PostgreSQL 원장 query에서 집계하며 CSV 다운로드는 제공하지 않는다. | Phase 2 / FE·BE, `P2-S3-T09`, `P2-S4-T09` | cycle·platform·execution·issue severity repository/API와 generated-client Overview·Reports filter가 통과했다. |
| Slack·Generation API·ontology 확장 | upstream parity 대상이 아니다. | Slack thread 원문·revision과 typed ontology evidence를 독립 navigation으로 확장한다. 자동 생성은 내장 AI 화면이 아니라 후속 Generation API 연결이며 기본은 수동 생성이다. | Phase 3~4 / BE·FE·AI·OPS | Knowledge·Integrations mock은 정보 구조 evidence다. `P3-S5-*`, `P3-S6-*`, `P4-S7-*`, `P4-S8-*`의 source revision·evidence·dedup·human-edit protection gate가 기능 evidence다. |

mock prototype은 후속 Phase 3~4 entity의 정보 구조와 개발 장애 경계에만 남긴다. 2026-08-20 기준 case, work item, cycle, execution, issue, attachment metadata와 report는 PostgreSQL/FastAPI/generated client에 연결됐고 Web은 actual API와 mock을 섞지 않는다. Web unit 44개·typecheck·production build와 issue/cycle/report no-fallback Browser smoke가 통과했다. Drive binary는 deterministic fake provider에서만 검증됐으므로 live My Drive를 포함한 Phase 2 운영 완료로 간주하지 않는다. [Sprint 4 품질 게이트](./sprint-4/sprint-4-report.md)가 코드 evidence와 미실증 외부 조건을 분리한다.

## 15. 보안 설계

- reverse proxy와 방화벽에서 UI·`/api/*`를 사내망/VPN으로 제한한다.
- 외부 공개가 필요하면 `/integrations/slack/events`만 별도 route로 노출한다.
- Slack signing secret, bot token, Google refresh token, encryption key는 secret store 또는 권한이 제한된 환경 변수로 주입한다.
- token·cookie·비밀번호·개인정보 패턴은 애플리케이션 log와 AI 입력 전에 마스킹한다.
- Drive 파일은 public sharing을 만들지 않고 backend proxy로만 제공한다.
- 파일명 path traversal, MIME spoofing, 크기 제한, malware 검사를 수행한다.
- SQL은 parameter binding을 사용하고 rich text는 저장·출력 시 XSS 정책을 적용한다.
- private Slack 채널은 기본적으로 수집하지 않는다.
- 무로그인이라는 제품 결정 때문에 네트워크 경계가 사실상 접근 제어다. 내부 API가 인터넷에 노출된 상태는 출시 차단 조건이다.
- 생성·수정·삭제·상태 전환·설정 변경·integration 재연결은 audit log에 기록한다.

## 16. 성능과 안정성

| 항목 | 목표·처리 |
|---|---|
| 일반 조회 | p95 1초 이내 |
| 대규모 케이스 | 10만 건에서 tree와 page list 분리 |
| Slack 수신 | 원문 저장·job 등록까지만 동기 처리 |
| AI 생성 | 전부 비동기, 진행 상태·재시도 제공 |
| DB 동시 수정 | optimistic locking과 409 응답 |
| 이벤트 중복 | Slack event ID, message key, idempotency key로 차단 |
| projection 장애 | outbox 재시도, 원장 rollback 금지 |
| AI 장애 | 실패 상태와 원인 노출, 수동 재시도 |
| LightRAG 장애 | typed graph + pgvector fallback |
| Drive 장애 | metadata 보존, 업로드 재시도·연결 복구 안내 |

## 17. 관측성과 운영 화면

### 17.1 필수 로그

- HTTP trace ID, route, status, latency
- job ID, type, attempt, duration, final error code
- Slack event ID·channel·message key와 처리 단계
- generation job의 model·prompt·schema version과 token/latency 추정치
- Drive 요청의 file ID·size·status. token과 파일 내용은 기록하지 않음
- outbox lag와 projection 처리 결과

### 17.2 지표·경고

- API 5xx 비율과 p95 latency
- background job queue depth, oldest job age, dead job count
- Slack event 처리 실패·quiet window 지연
- AI 생성 성공률·평균 시간·JSON validation 실패율
- ontology extraction 실패와 evidence 누락 수
- PostgreSQL disk·connection·backup age
- Drive 사용량 70%·85%·95%, OAuth 오류
- Generation API health·credential·rate limit·응답 시간과 local embedding health

운영 대시보드에서 실패 job을 조회·재시도할 수 있게 하고 비밀정보가 포함된 raw payload는 기본 화면에 노출하지 않는다.

## 18. 테스트 전략

### 18.1 테스트 계층

| 계층 | 대상 | 필수 예시 |
|---|---|---|
| Unit | domain rule, parser, mapper, ranker | 폴더 cycle, 상태 전이, 케이스 version 충돌 |
| Schema | Pydantic·JSON Schema | 예상결과 1개, 빈 step, evidence ID 오류 |
| Repository | PostgreSQL constraint·transaction | 중복 key, snapshot, outbox atomicity |
| Integration | Slack·Drive·Generation API·LightRAG adapter | 서명 검증, OAuth refresh, schema validation, timeout·retry |
| Contract | OpenAPI와 frontend client | request/response enum 일치 |
| E2E | 사용자 핵심 흐름 | 케이스→사이클→실행 실패→이슈→첨부, Slack event→revision→parse→sync/retry |
| AI eval | 파싱·추출·검색·생성 | 결정 대체, 모호 충돌, 과거 이슈 회귀 |
| Security | network·file·secret | 공개 route 제한, XSS, 악성 첨부 |
| Recovery | backup·restore·reindex | DB restore, Drive manifest, vector 재생성 |

### 18.2 출시 차단 회귀 시나리오

1. 3단계 이상 폴더 생성·이동·복원
2. 케이스 저장·수정·diff·과거 버전 복원
3. 사이클 버전 고정과 최신 버전 수동 반영
4. 동일 케이스 재실행 시 이전 결과 보존
5. 실패 실행에서 값이 자동 채워진 이슈 생성
6. Drive 첨부 업로드·중계 다운로드·soft delete
7. 같은 Slack event 재전송 시 work item·revision·outbox·job 중복 없음; 케이스 생성 dedup은 Sprint 8 별도 gate
8. thread 수정·삭제 시 append-only revision 변경; 지식 상태 변경은 Sprint 6 별도 gate
9. `AMBIGUOUS`·`SUPERSEDED` 지식이 생성 근거에서 제외
10. 사람이 수정한 AI 케이스에 suggestion만 생성
11. LightRAG 중단 상태에서 fallback 검색·생성
12. DB 복구 후 attachment 연결과 generation context 재현

2026-08-20에 시나리오 5~6은 PostgreSQL/FastAPI와 deterministic fake Drive에서 자동화했다. 2026-08-21에 시나리오 7과 8의 source-ledger 부분은 PostgreSQL/FastAPI/worker/generated client/Web과 deterministic fake Slack에서 자동화했다. 2026-08-24에는 R2 SigV4·upload claim 단위 검증과 local MinIO 구성을 추가했다. Live R2/CORS, live Slack App 설치·자동 수집과 시나리오 12의 운영 backup restore는 credential·배포 환경이 준비될 때까지 출시 gate로 열린다.

## 19. 배포와 환경 설정

### 19.1 환경

| 환경 | 용도 | 외부 데이터 사용 |
|---|---|---|
| Local | 개발·unit/integration | mock Slack·MinIO·Generation API |
| Staging | E2E·AI eval·migration rehearsal | 익명화된 샘플과 테스트 채널 |
| Production | 팀 실제 운영 | allowlist 실제 채널·private Cloudflare R2 bucket |

### 19.2 필수 설정

- DB URL과 encryption master key
- `TC_SLACK_MODE=disabled|http|socket`; HTTP은 signing secret·bot token·workspace ID·public base URL, Socket은 app token·bot token·workspace ID, 공통으로 public allowlist channel ID
- `TC_OBJECT_STORAGE_ENABLED`, `TC_OBJECT_STORAGE_PROVIDER=R2`, S3 endpoint·bucket·region
- `TC_OBJECT_STORAGE_ACCESS_KEY_ID`, `TC_OBJECT_STORAGE_SECRET_ACCESS_KEY`; 배포 secret store에만 저장
- 기본 50 MiB `TC_OBJECT_STORAGE_MAX_FILE_BYTES`, MIME allowlist와 300초 presign TTL
- Generation API base URL·credential·provider ID·schema version, embedding model·dimension, context length
- LightRAG workspace·storage 설정
- quiet window, retry 횟수, re-sync 기간
- 사내 UI origin과 public Slack callback base URL

설정값과 secret을 Git에 커밋하지 않는다. 배포 시 필수값 누락과 모델·embedding dimension 불일치를 startup validation으로 차단한다.

### 19.3 배포 절차

1. DB와 Drive metadata backup을 확인한다.
2. 새 image와 dependency·model version을 고정한다.
3. staging에서 Alembic·ontology migration과 rollback 가능성을 검증한다.
4. production migration을 적용한다.
5. API, worker, scheduler, web 순으로 health check한다.
6. [Slack 설치 runbook](../deploy/slack/README.md)에 따라 Slack URL verification 또는 Socket Mode 연결, channel 참여와 private default deny를 확인한다.
7. R2 private bucket·CORS·direct PUT/HEAD와 Generation API·embedding health를 확인한다.
8. 핵심 smoke test를 실행한다.
9. outbox·job lag와 오류율을 관찰한다.

## 20. 백업과 복구

- PostgreSQL은 매일 백업하고 보존 주기와 저장 위치는 서버 운영 정책으로 고정한다.
- 분기마다 별도 환경에 DB restore를 수행해 복구 가능성을 검증한다.
- storage provider, object key, logical path, checksum manifest를 DB backup과 함께 보존한다.
- R2 API credential rotation, bucket 복구·이관 절차와 legacy Drive row 이관 상태를 문서화한다.
- pgvector와 LightRAG index는 원장에서 재생성 가능한 파생 데이터로 취급한다.
- embedding model·dimension·schema version 변경 시 shadow reindex 후 index를 전환한다.
- RPO·RTO는 실제 배포 서버와 백업 위치가 확정될 때 수치로 보완한다.

## 21. 개발 완료 기준

모든 task는 다음 Definition of Done을 만족해야 완료 처리한다.

- 요구사항과 인수 조건을 충족하는 코드가 기본 branch에 반영됨
- unit·integration test가 추가되고 전체 test가 통과함
- DB 또는 ontology 변경에 forward migration과 복구 방안이 있음
- API 변경 시 OpenAPI와 frontend client가 함께 갱신됨
- 비동기 작업은 idempotency·retry·실패 노출을 포함함
- secret·개인정보·Slack raw payload가 log에 노출되지 않음
- 주요 변경이 audit log와 운영 지표에 반영됨
- 사용자 흐름이 staging에서 확인됨
- 관련 문서와 checklist가 갱신됨
- 출시 차단 수준의 미해결 결함이 없음

## 22. 기획 인수 조건 추적표

| 인수 조건 묶음 | 구현 모듈 | 핵심 테스트 |
|---|---|---|
| 폴더·케이스·버전 | Library | 폴더 3-depth, 버전 diff·restore |
| 사이클·실행 | Cycle | 버전 고정, 재실행 append-only |
| 이슈·첨부 | Issue, Attachment | FAIL/BLOCKED snapshot·concurrency·comment/link와 private R2/MinIO upload/proxy/download/delete·quarantine·orphan·integrity·restore·budget; live R2는 별도 운영 gate |
| Slack 자동 수집 | Slack | deterministic allowlist·idempotency·revision·sync·retry/DEAD 통과; live App/channel 자동 수집은 미검증 |
| 지식 상태·근거 | Knowledge | 충돌·대체·철회, evidence link |
| API 자동 등록 | Generation | schema, dedup, domain mapping, 즉시 ACTIVE |
| 사람 수정 보호 | Generation, Library | suggestion 생성, 자동 overwrite 없음 |
| 비용·연동 | Deployment | MVP는 신규 유료 AI 호출 없음, 연결 endpoint 비용·quota 사전 확인 |
| 무로그인 보안 | Deployment, Security | 내부 route 외부 차단, Slack route만 공개 |

## 23. 개발 착수 전 필요한 설치 정보

아래 값은 정책 결정이 아니라 실제 연동·배포를 위한 입력값이다.

- Slack workspace ID, 수집 채널 ID, App credentials, HTTP 또는 Socket Mode 선택
- private R2 account ID·bucket·S3 token, 정확한 Web origin·CORS, credential owner·rotation 담당자와 reachable ClamAV
- 배포할 PC 또는 Linux 서버의 CPU·RAM·GPU·disk와 사내 접속 방식
- 익명화 가능한 실제 일감·thread 예시와 기대 테스트케이스
- 초기 디렉토리 구조·메뉴 별칭 목록
- 백업을 저장할 기존 디스크 또는 NAS 위치
- 사용자 수, 예상 동시 접속자, 월간 Slack 일감 수, 평균 첨부 크기

이 입력값이 확보되면 하드웨어 benchmark, RPO·RTO, 실제 스프린트 인원 계획을 확정한다.
