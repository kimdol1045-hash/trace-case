# ADR-0002: PostgreSQL 16을 업무 원장으로 사용

| 항목 | 값 |
|---|---|
| ADR 번호 | 0002 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Architecture / BE / OPS |

## Context

케이스 버전, 사이클 snapshot, 실행 시도, Slack revision, ontology evidence와 audit는 서로 강한 참조 무결성과 transaction을 요구한다. MVP는 단일 workspace이며 별도 관리형 database나 복수 저장소 운영 비용을 추가하지 않는다.

## Decision Drivers

- 업무 원장 변경의 ACID transaction과 FK·unique·check constraint
- revision, audit, outbox, queue의 일관된 백업·복구
- 단일 workspace 규모에서의 운영 단순성
- JSONB와 관계형 query를 함께 사용해야 하는 요구
- self-host와 신규 구독 비용 0원 목표

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| PostgreSQL 16 단일 원장 | 정규화 table, JSONB, transaction, extension 사용 | 선택 |
| Document database | 유연한 document 위주 저장 | 강한 관계·transaction 때문에 제외 |
| 업무별 database 분리 | case, Slack, knowledge를 별도 저장소로 분리 | MVP 복잡도 때문에 제외 |

## Decision

PostgreSQL 16 이상을 케이스·실행·이슈·Slack 원문·typed ontology·audit의 authoritative system of record로 사용한다. 업무 무결성에 필요한 값은 typed column과 constraint로 두고, 확장 속성만 JSONB를 사용한다. 모든 시각은 UTC로 저장하며 백업·restore는 원장 전체를 기준으로 수행한다.

## Consequences

### Positive

- 업무 변경과 관련 metadata를 한 transaction에서 보존할 수 있다.
- backup, restore, migration, observability 대상이 단순해진다.
- 관계형 query와 flexible metadata를 한 engine에서 지원한다.

### Negative

- PostgreSQL 장애가 MVP 전체의 주요 장애가 된다.
- 대용량 binary와 재생성 가능한 search artifact까지 넣으면 database가 비대해질 수 있다.
- 단일 instance의 connection·disk·vacuum 관리가 중요해진다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| single point of failure | 일일 backup, 분기 restore rehearsal, disk·connection·backup-age 경고를 둔다. |
| JSONB 남용 | 검색·FK·무결성이 필요한 field는 migration으로 typed column을 만든다. |
| binary로 DB 팽창 | 첨부 binary는 Drive에 두고 DB에는 metadata·checksum만 저장한다. |

## Validation Evidence

- [baseline migration](../../backend/migrations/versions/20260820_0001_baseline.py)이 PostgreSQL `server_version_num >= 160000`을 강제한다.
- [개발 Compose](../../deploy/compose.dev.yml)는 PostgreSQL 16 pgvector image와 healthcheck를 제공한다.
- [Sprint 0 인프라 spike](../sprint-0/infra-and-ai-spike.md)에서 PostgreSQL 16.12 container health와 query를 확인했다.
- 전체 업무 domain table은 아직 구현되지 않았으므로 원장 전체 transaction·restore는 후속 검증 대상이다.

## Revisit Triggers

- 단일 instance가 합의된 p95, queue delay, backup window를 지속적으로 넘는다.
- 복수 workspace·region 또는 규제상 물리 분리가 제품 범위에 들어온다.
- database 크기·write 부하가 vertical scale과 partitioning으로 감당되지 않는다.

## Related Documents

- [ADR 원장](./README.md)
- [개발 명세 6장 데이터베이스](../test-management-development-spec.md)
- [ADR-0003 pgvector](./0003-pgvector-in-postgresql.md)
- [ADR-0004 PostgreSQL 작업 큐](./0004-postgresql-job-queue-no-redis.md)
