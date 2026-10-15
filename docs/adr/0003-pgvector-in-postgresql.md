# ADR-0003: pgvector를 PostgreSQL에 공동 배치

| 항목 | 값 |
|---|---|
| ADR 번호 | 0003 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Architecture / BE / AI / OPS |

## Context

Slack 지식, 케이스, 실행 실패, 이슈를 semantic search하려면 embedding 저장과 유사도 query가 필요하다. Embedding은 원문 자체가 아니라 재생성 가능한 search artifact이지만 owner ID, content hash, model version과 원자적으로 추적돼야 한다.

## Decision Drivers

- 업무 entity와 embedding metadata의 참조·version 정합성
- MVP에서 별도 vector database 운영 제외
- exact search에서 HNSW로 단계적 확장 가능
- BGE-M3 dimension 1024 고정 검증
- 원장에서 전체 reindex 가능한 구조

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| PostgreSQL + pgvector | 같은 DB에 embedding과 metadata 저장 | 선택 |
| 별도 vector database | vector 전용 service 사용 | MVP 운영 복잡도로 제외 |
| LightRAG storage만 사용 | semantic index를 LightRAG에만 위임 | 원장·fallback 요구로 제외 |
| semantic search 없음 | full-text와 graph만 사용 | 제품 검색 목표 때문에 제외 |

## Decision

PostgreSQL에 pgvector extension을 설치하고 embedding을 owner type·owner ID·model version·content hash와 함께 저장한다. BGE-M3 후보의 현재 embedding column은 `vector(1024)`로 고정하며 model 또는 dimension 변경은 새 version의 shadow reindex 후 전환한다. 데이터가 작을 때는 exact search를 사용하고 실측 후에만 HNSW를 추가한다.

## Consequences

### Positive

- entity와 embedding version을 같은 backup·transaction 경계에서 관리한다.
- 별도 vector service와 credential이 필요 없다.
- LightRAG 장애 시에도 typed graph + pgvector search가 가능하다.

### Negative

- vector query가 업무 transaction DB의 CPU·memory·I/O를 공유한다.
- dimension 변경은 in-place 교체가 아니라 전체 reindex를 요구한다.
- 대규모 ANN 요구에서는 전용 engine보다 tuning 선택지가 적을 수 있다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| model·dimension 불일치 | startup에서 `vector(1024)`와 설정 dimension을 검증하고 다른 vector를 혼합하지 않는다. |
| query 부하가 OLTP를 방해 | slow query·index size를 관찰하고 read replica나 별도 search service를 재검토한다. |
| stale embedding | content hash + model version unique와 outbox 재처리로 갱신한다. |

## Validation Evidence

- [ontology migration](../../backend/migrations/versions/20260820_0002_ontology_v1.py)이 `knowledge_embeddings.embedding vector(1024)`를 만든다.
- [startup capability check](../../backend/src/tc_backend/startup.py)가 extension과 column dimension을 검증한다.
- [Sprint 0 인프라 spike](../sprint-0/infra-and-ai-spike.md)에서 pgvector 0.8.1의 `vector(3)` insert와 `<->` nearest query가 기대 ID를 반환했다.
- 실제 QA corpus의 recall, HNSW parameter, 10만 case 부하는 아직 측정하지 않았다.

## Revisit Triggers

- vector query가 OLTP p95나 vacuum/backup window를 침해한다.
- embedding 수가 실측한 PostgreSQL/HNSW 수용 범위를 넘는다.
- dimension·model을 동시에 여러 개 online serving해야 한다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0002 PostgreSQL 원장](./0002-postgresql-system-of-record.md)
- [ADR-0008 typed ontology](./0008-typed-ontology-ledger.md)
- [개발 명세 12장 ontology와 retrieval](../test-management-development-spec.md)
