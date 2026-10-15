# ADR-0008: Typed ontology를 지식 원장으로 사용

| 항목 | 값 |
|---|---|
| ADR 번호 | 0008 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Architecture / AI / BE / QA |

## Context

Slack thread에는 요구사항·결정·제약·번복이 섞여 있고 단순 vector 검색만으로 현재 유효한 지식과 폐기된 지식을 구분할 수 없다. AI가 추출한 text를 그대로 사실로 사용하면 source와 시간, 충돌 상태, 허용 관계를 검증할 수 없다.

## Decision Drivers

- node·edge의 type 제약과 허용 source/target 관계
- `ACTIVE`, `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED` 상태
- recorded/valid time과 source revision evidence
- 업무 entity와 지식 projection의 traceability
- search index 유실 시 원장에서 재생성 가능

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| PostgreSQL typed ontology | versioned schema, node, edge, evidence 원장 | 선택 |
| vector-only knowledge base | chunk + embedding만 저장 | 상태·관계·근거 부족으로 제외 |
| LightRAG graph를 원장으로 사용 | 추출/index graph를 authoritative로 사용 | 재현·업무 무결성 때문에 제외 |
| 자유 JSON graph | type rule 없이 node/edge 저장 | 잘못된 관계 차단 불가로 제외 |

## Decision

PostgreSQL의 versioned ontology schema, typed node, typed edge, evidence를 controlled knowledge ledger로 사용한다. 모든 node·edge는 상태, recorded time, validity와 source evidence를 가져야 하며 schema가 허용한 관계만 controlled layer에 반영한다. LLM 출력은 후보일 뿐 deterministic validator를 통과하기 전에는 원장 사실이나 SQL 명령으로 사용하지 않는다.

## Consequences

### Positive

- 현재 결정과 과거·충돌·철회 지식을 구분할 수 있다.
- 검색·생성 결과를 Slack revision이나 업무 entity로 추적할 수 있다.
- index를 폐기하고 원장에서 다시 만들 수 있다.

### Negative

- ontology schema versioning과 relation validator 유지 비용이 생긴다.
- 자동 상태 판별 오류를 검토·수정할 운영 UI가 필요하다.
- graph traversal과 projection 재생성이 단순 document 검색보다 복잡하다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| LLM이 잘못된 relation 생성 | allowlist schema, evidence 존재, source/target type을 deterministic하게 검사한다. |
| 최신 결정 오판 | 모호하면 `AMBIGUOUS`로 두고 확정 generation context에서 제외한다. |
| schema 변경으로 과거 지식 손상 | schema version과 migration을 보존하고 shadow projection으로 전환한다. |

## Validation Evidence

- [ontology v1 migration](../../backend/migrations/versions/20260820_0002_ontology_v1.py)과 [Sprint 6 knowledge migration](../../backend/migrations/versions/20260821_0008_knowledge_projection.py)이 schema, node, edge, evidence, source record, embedding, projection checkpoint와 상태/관계 constraint를 생성한다.
- [canonical enum Schema](../../packages/contracts/state-enums.schema.json)와 migration의 node·predicate·status 목록이 일치한다.
- [synthetic fixture](../sprint-0/contracts-and-samples.md)는 정상·누락·번복·충돌 12건과 expected evidence/status를 제공한다.
- [`KnowledgeRepository`](../../backend/src/tc_backend/repositories/knowledge_repository.py)는 v1 allowed source/predicate/target validator, source revision/content conflict, node·edge evidence와 BGE-M3 embedding version/hash dedup을 구현한다.
- [`test_knowledge_repository.py`](../../backend/tests/postgres/test_knowledge_repository.py)는 application validator를 우회한 raw ORM write에도 DB trigger가 미허용 relation과 evidence 없는 node/edge를 거부함을 disposable PostgreSQL에서 검증한다. source FK, projection replay, case/execution/issue traceability와 vector 검색도 같은 suite가 검증한다.
- [`test_knowledge_extraction.py`](../../backend/tests/unit/test_knowledge_extraction.py)는 hallucinated/unknown evidence, cyclic relation을 거부하고 reversal/conflict/delete/retraction 상태를 deterministic하게 판별한다.
- [`test_knowledge_api_postgresql.py`](../../apps/api/tests/test_knowledge_api_postgresql.py)와 [Sprint 6 report](../sprint-6/sprint-6-report.md)는 실제 PostgreSQL API와 Web browser에서 current filter, evidence citation, manual patch/audit, explorer를 재현했다.
- 실제 Slack sample의 extraction 정확도와 운영자 검토 품질은 아직 검증하지 않았다. 이 외부 품질 경계는 `Accepted`인 authoritative ledger 선택과 분리한다.

## Revisit Triggers

- 실제 sample에서 현 node/predicate가 반복적으로 의미를 잃거나 과도한 `properties` 사용을 강제한다.
- graph query 규모가 PostgreSQL에서 목표 latency를 충족하지 못한다.
- 외부 표준 ontology와 상호운용 요구가 제품 범위에 들어온다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0003 pgvector](./0003-pgvector-in-postgresql.md)
- [ADR-0009 LightRAG 보조 index](./0009-lightrag-as-derived-index.md)
- [개발 명세 12장 ontology](../test-management-development-spec.md)
