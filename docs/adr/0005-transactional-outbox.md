# ADR-0005: Transactional outbox와 최종 일관성

| 항목 | 값 |
|---|---|
| ADR 번호 | 0005 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Architecture / BE / AI / OPS |

## Context

케이스 버전, 실행, 이슈, Slack revision이 바뀌면 typed ontology, embedding, LightRAG 같은 파생 projection도 갱신돼야 한다. 업무 row commit 뒤 별도 enqueue를 수행하면 process crash에서 event가 유실되고, 모든 projection을 업무 transaction 안에서 동기 처리하면 외부 AI·index 장애가 원장 쓰기를 막는다.

## Decision Drivers

- 업무 원장 commit과 변경 event 기록의 atomicity
- 외부/파생 projection 장애로부터 원장 write 격리
- at-least-once delivery에서 재처리·감사 가능성
- projection lag와 실패의 관측성
- search layer를 원장에서 재생성할 수 있는 구조

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| Transactional outbox | 업무 row와 event를 한 DB transaction에 기록 | 제안 선택 |
| Commit 후 queue enqueue | application이 두 번째 write 수행 | 유실 window 때문에 제외 |
| 모든 projection 동기 처리 | 원장 transaction에서 search/AI까지 갱신 | 장애 결합 때문에 제외 |
| Change Data Capture | WAL/connector로 변경 수집 | MVP 운영 복잡도로 보류 |

## Decision

케이스 버전 생성, 실행 완료, 이슈 변경, Slack revision 저장은 동일 PostgreSQL transaction에서 dedup 가능한 `outbox_events` row를 만든다. Outbox publisher와 projection consumer를 연결할 때는 event를 at least once로 읽고 typed ontology, pgvector, LightRAG projection을 idempotent하게 갱신하며 성공 시 처리 시각을 기록한다. Projection 실패는 retry/dead 상태로 노출하지만 이미 commit된 업무 원장은 rollback하지 않아 최종 일관성을 허용한다.

`outbox_events` migration/model과 case·execution·issue·Slack mutation producer가 업무 변경과 event를 한 transaction에 기록한다. Knowledge consumer는 READY/FAILED event를 `SKIP LOCKED`로 claim하고 typed ontology·BGE-M3 embedding·보조 LightRAG index를 처리하며, `knowledge_projection_events` checkpoint로 at-least-once replay를 멱등하게 만든다. 성공은 `PUBLISHED`, 실패는 `FAILED`/`DEAD`로 기록되고 이미 commit된 업무 원장은 LightRAG 장애로 rollback하지 않는다. 이 핵심 경계가 disposable PostgreSQL에서 재현됐으므로 `Accepted`다.

## Consequences

### Positive

- 업무 변경과 event 기록 사이의 유실 window가 사라진다.
- search/AI 장애가 핵심 업무 저장을 차단하지 않는다.
- lag, retry, replay를 명시적으로 운영할 수 있다.

### Negative

- 사용자는 짧은 기간 stale projection을 볼 수 있다.
- publisher·consumer idempotency·정리 정책을 직접 구현해야 한다.
- outbox table이 쌓이면 partition/retention 관리가 필요하다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| 중복 delivery가 projection을 중복 생성 | aggregate ID + event type/version unique와 consumer checkpoint를 사용한다. |
| poison event가 queue를 막음 | 독립 attempt/dead 상태, 관리 화면, 수정 후 replay를 제공한다. |
| lag 동안 잘못된 검색 결과 | API에 projection watermark/degraded 상태를 노출하고 확정 판단은 원장을 조회한다. |

## Validation Evidence

- [개발 명세 9.1](../test-management-development-spec.md)은 업무 row와 outbox의 동일 transaction, projection retry를 요구한다.
- [`20260820_0006_sprint4_issue_drive_reports.py`](../../backend/migrations/versions/20260820_0006_sprint4_issue_drive_reports.py)와 [`OutboxEvent`](../../backend/src/tc_backend/db/models.py)는 aggregate/event/version unique와 READY/PROCESSING/PROCESSED/DEAD 상태를 저장한다.
- [`SlackRepository.ingest_event`](../../backend/src/tc_backend/db/slack.py)는 event 수신, append-only revision, work item, audit, outbox, quiet job을 caller transaction에 함께 기록한다. 동일 event 세 번은 outbox 하나이며 강제 rollback 뒤에는 event·revision·audit·outbox·job이 모두 0개인 PostgreSQL test가 통과한다.
- [`test_sprint4_postgresql.py`](../../backend/tests/repository/test_sprint4_postgresql.py)는 issue·link·audit·outbox producer의 atomic commit/rollback을 검증하고, [`test_slack_postgresql.py`](../../backend/tests/repository/test_slack_postgresql.py)는 Slack revision producer의 idempotency·concurrency·rollback을 검증한다.
- [`KnowledgeOutboxConsumer`](../../backend/src/tc_backend/worker/knowledge_consumer.py)는 supported aggregate READY/FAILED row를 `SKIP LOCKED`로 claim하고 projection·embedding·index를 실행한다. 실패 receipt를 원장 transaction과 분리하고 checkpoint가 있으면 `replayed=true` result를 돌려준다.
- [`test_knowledge_repository.py`](../../backend/tests/postgres/test_knowledge_repository.py)는 case row+outbox 강제 rollback과 commit, 동일 outbox 두 번 projection 시 checkpoint/node/evidence가 하나뿐임을 실제 PostgreSQL에서 검증한다.
- [`test_knowledge_api_postgresql.py`](../../apps/api/tests/test_knowledge_api_postgresql.py)는 Slack extraction과 case projection event를 실제 consumer로 처리하고 같은 event replay, `PUBLISHED`, projection/reindex job `SUCCEEDED`를 검증한다.
- `background_jobs`의 `SKIP LOCKED`, retry, DEAD는 ADR-0004의 작업 큐 근거다. 운영 process의 장시간 lag·retention·poison-event 복구 훈련은 아직 후속 운영 evidence다.

## Revisit Triggers

- 구현 전 transaction boundary가 확정되지 않거나 outbox와 job row를 하나로 합칠 필요가 생긴다.
- event 처리량이 polling outbox의 목표 lag를 넘는다.
- 운영상 CDC infrastructure가 이미 제공돼 더 단순한 신뢰 경계를 만들 수 있다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0002 PostgreSQL 원장](./0002-postgresql-system-of-record.md)
- [ADR-0004 PostgreSQL 작업 큐](./0004-postgresql-job-queue-no-redis.md)
- [ADR-0009 LightRAG 보조 index](./0009-lightrag-as-derived-index.md)
