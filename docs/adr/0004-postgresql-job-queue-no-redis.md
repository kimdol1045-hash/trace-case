# ADR-0004: PostgreSQL 작업 큐를 사용하고 MVP에서 Redis 제외

| 항목 | 값 |
|---|---|
| ADR 번호 | 0004 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Architecture / BE / OPS |

## Context

Slack sync, ontology projection, embedding, AI 생성, Drive 후처리는 HTTP request와 분리된 retryable job이어야 한다. MVP의 예상 처리량에서는 별도 broker보다 업무 원장과 같은 PostgreSQL에서 claim·retry·liveness를 관리하는 편이 운영상 단순하다.

## Decision Drivers

- Redis service·backup·monitoring 추가 없이 0원 self-host
- job enqueue와 업무 transaction의 결합 가능성
- 중복 worker에서 한 job만 claim하는 명확한 locking
- idempotency, retry, dead job, stale lock 회수
- 예상 MVP 처리량에서 충분한 성능

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| PostgreSQL `background_jobs` | `FOR UPDATE SKIP LOCKED` 기반 queue | 선택 |
| Redis queue | Redis + worker framework | MVP에서 제외 |
| In-process queue | API memory에 job 보관 | 재시작 유실 때문에 제외 |
| 외부 managed broker | cloud message service | 비용·운영 경계 때문에 제외 |

## Decision

API와 scheduler는 `background_jobs`에 최소 payload와 idempotency key를 기록한다. Worker는 ready job을 priority·run time 순으로 `FOR UPDATE SKIP LOCKED` claim하고 짧은 claim transaction을 commit한 뒤 처리한다. Retry는 지수 backoff, 최대 attempt 이후 `DEAD`, running job은 heartbeat와 stale timeout으로 회수한다. MVP에는 Redis를 배포하지 않는다.

## Consequences

### Positive

- database 하나로 durable queue와 transaction을 관리한다.
- duplicate claim, stale worker, retry 상태를 SQL로 관찰할 수 있다.
- Redis credential·persistence·운영 playbook이 필요 없다.

### Negative

- 높은 queue throughput이 업무 DB connection과 I/O를 경쟁한다.
- broker의 push delivery, stream, routing 기능을 직접 구현해야 한다.
- 장시간 transaction으로 잘못 구현하면 lock contention이 커진다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| 중복 실행 | claim locking과 별개로 handler를 idempotent하게 만들고 unique idempotency key를 사용한다. |
| stale `RUNNING` 고착 | heartbeat, `locked_at`, max attempts로 회수하거나 `DEAD` 처리한다. |
| DB 부하 | queue depth·oldest age·claim latency를 관찰하고 batch/poll interval을 조정한다. |

## Validation Evidence

- [baseline migration](../../backend/migrations/versions/20260820_0001_baseline.py)이 `background_jobs`, status constraint, ready/liveness index를 만든다.
- [JobRepository](../../backend/src/tc_backend/db/jobs.py)에 `SKIP LOCKED`, idempotent enqueue, heartbeat, retry/dead 전이가 구현돼 있다.
- [PostgreSQL repository test](../../backend/tests/repository/test_jobs_postgresql.py)는 두 worker 중 하나만 claim, retry→dead, idempotency, stale lock을 검증한다.
- 2026-08-20 disposable PostgreSQL 16 + pgvector DB에서 repository test 6개 전체가 통과했다.
- 실제 API/worker process의 지속 부하와 crash injection은 아직 후속 검증 대상이다.

## Revisit Triggers

- queue claim이 DB p95를 침해하거나 backlog가 SLA 안에 줄지 않는다.
- partition·priority·scheduled delivery 요구가 현 schema로 감당되지 않는다.
- 독립 scaling한 worker 수가 PostgreSQL connection budget을 지속적으로 넘는다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0002 PostgreSQL 원장](./0002-postgresql-system-of-record.md)
- [개발 명세 9.2 PostgreSQL 작업 큐](../test-management-development-spec.md)
- [Backend foundation](../../backend/README.md)
