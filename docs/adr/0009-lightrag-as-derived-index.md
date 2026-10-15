# ADR-0009: LightRAG를 보조 index로 사용하고 fallback 제공

| 항목 | 값 |
|---|---|
| ADR 번호 | 0009 |
| 날짜 | 2026-08-20 |
| 최종 검토 | 2026-09-04 (Phase F wave 4 independent RED remediation) |
| 상태 | Proposed |
| 결정자 범위 | Architecture / AI / BE / OPS |

## Context

Typed graph와 pgvector만으로도 필터·semantic search는 가능하지만 source bundle에서 entity/relation을 추출하고 graph-aware retrieval을 실험하기 위해 LightRAG를 후보로 둔다. LightRAG의 내부 storage나 추출 결과를 업무 원장으로 사용하면 library update, index 손상, 비결정적 추출이 제품 상태를 지배할 수 있다.

## Decision Drivers

- graph-aware retrieval 품질 실험
- PostgreSQL typed ledger의 authoritative 상태 유지
- index 유실·LightRAG 장애 시 기본 검색 지속
- source revision citation으로의 역추적
- 전체 reindex와 version 회귀 가능성

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| LightRAG 보조 index | 추출·검색 보조, 원장은 PostgreSQL | 제안 선택 |
| LightRAG를 업무 원장으로 사용 | graph/storage를 authoritative하게 사용 | 제외 |
| LightRAG 없이 typed graph + pgvector | 단순한 기본 경로만 운영 | mandatory fallback으로 유지 |
| 별도 managed Graph RAG | 외부 service 사용 | 비용·credential 때문에 제외 |

## Decision

LightRAG는 source bundle의 knowledge extraction과 retrieval을 보조하는 재생성 가능한 search layer로만 사용한다. 최종 상태·validity·evidence는 PostgreSQL typed ontology가 결정하며, LightRAG 결과는 validator를 거쳐야 한다. LightRAG 장애나 index rebuild 중에도 동일 search API가 typed graph + pgvector 결과와 `degraded` 표시를 반환해야 한다.

Generation은 TC PostgreSQL의 immutable metadata, versioned singleton pointer와 append-only transition
evidence로 선택한다. Cutover와 rollback은 expected generation/pointer version의 compare-and-swap만
허용하고, API/worker resolver는 operation 전후 binding을 재확인해 stale output을 폐기한다. Official
LightRAG 1.5.6의 process-global PostgreSQL 환경/pool은 generation별 child interpreter에 격리하며,
parent process의 `POSTGRES_WORKSPACE` 변경으로 generation을 선택하지 않는다.

Binding은 stable auxiliary authority ID/digest, provider ID/contract/distribution/version,
password/workspace를 제외한 runtime-config digest, generation workspace를 별도로 봉인한다. Production
worker는 API가 enqueue하는 `KNOWLEDGE_PROJECTION`과 `KNOWLEDGE_REINDEX`를 모두 claim 가능한 handler로
등록하고 같은 resolver를 사용한다.

Official PostgreSQL adapter identity는 단일 코드 상수
`OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID=official-lightrag-postgresql`로 고정한다. Settings factory와
ledger-backed controller는 config/binding/storage가 동일한 다른 ID에 합의해도 이를 거부한다. Child
bootstrap handoff는 canonical attestation key `runtimeConfigDigest`를 사용한다.

API와 worker의 실제 startup은 complete `TC_LIGHT_RAG_*` opt-in에서 이 factory/resolver를 합성하고
기본값은 disabled다. Disposable PostgreSQL에서 controller binding을 settings-built API/worker가
generation switch와 rollback까지 공유하고 두 knowledge job을 처리하는 local 증거와, 별도
AGE+pgvector process-failure rehearsal은 구현 근거다. 그러나 approved bootstrap/secret-manager
staging config, full corpus checksum/citation smoke,
multi-process cutover/rollback과 live rank 품질은 `NOT_RUN`이다. 따라서 상태는 계속 `Proposed`다.

## Consequences

### Positive

- LightRAG의 graph retrieval을 실험하면서 업무 무결성을 분리한다.
- dependency 장애 중에도 기본 검색과 수동 업무를 유지한다.
- index version을 폐기하고 원장에서 재생성할 수 있다.

### Negative

- 두 retrieval 경로와 rank merge를 구현·평가해야 한다.
- 정상/장애 결과의 ranking 차이를 사용자에게 설명해야 한다.
- reindex에 CPU, memory, 시간이 추가로 든다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| LightRAG citation이 source revision과 불일치 | index payload에 immutable evidence ID를 넣고 반환 시 원장과 교차 검증한다. |
| dependency update로 storage/schema drift | version pin, shadow workspace, golden regression 후 cutover한다. |
| fallback 품질 급락 | typed graph + pgvector 품질을 독립 release gate로 유지한다. |
| generation 간 process-global pool/환경 교차 오염 | generation별 child process가 immutable `POSTGRES_*`와 pool을 소유하고 resolver retirement 때 bounded cleanup한다. |
| stale/ambiguous cutover 결과 노출 | operation 전후 durable binding fence와 exact-version CAS activation/rollback을 사용한다. Runtime failure는 pointer를 자동 롤백하지 않는다. |
| self-consistent counterfeit provider가 exact-ID 검증을 우회 | Settings composition, factory, controller가 공유하는 canonical provider constant를 각각 강제하고 독립 focused test로 검증한다. |
| credential이 evidence/log/process argv에 노출 | parent는 `SecretStr`로 보유하고 child stdin으로만 전달하며 digest/attestation/log/argv에서 제외한다. Staging secret-manager injection과 rotation은 별도 gate다. |

## Validation Evidence

- [개발 명세 12.4](../test-management-development-spec.md)는 current LightRAG PostgreSQL storage class와 Apache AGE requirement, LightRAG 장애 시 typed graph + pgvector fallback을 요구한다.
- [`LightRAGStorageConfig`와 `LightRAGAuxiliaryIndex`](../../backend/src/tc_backend/knowledge/lightrag.py)는 `PGKVStorage`, `PGVectorStorage`, `PGDocStatusStorage`, `PGGraphStorage`, Apache AGE requirement와 initialize/insert/query/finalize typed failure를 정의한다. LightRAG receipt는 authoritative하지 않고 PostgreSQL ID를 재해석해야 한다.
- [`test_lightrag_adapter.py`](../../backend/tests/unit/test_lightrag_adapter.py)는 missing AGE, storage initialization, insert와 query transport failure를 주입해 `TYPED_GRAPH_PGVECTOR` fallback을 검증한다.
- [`test_knowledge_api_postgresql.py`](../../apps/api/tests/test_knowledge_api_postgresql.py)는 LightRAG no-core 주입에서 동일 search API가 `degraded=true`, `LIGHTRAG_UNAVAILABLE`와 typed graph+pgvector case/execution/issue 결과를 반환함을 검증한다. projection/reindex job도 성공한다.
- [`20260902_0030_lightrag_generation_control_plane.py`](../../backend/migrations/versions/20260902_0030_lightrag_generation_control_plane.py)는 immutable generation, singleton pointer와 append-only transition evidence만 설치하며 used state downgrade를 거부한다.
- [`lightrag_generation_factory.py`](../../backend/src/tc_backend/knowledge/lightrag_generation_factory.py)는 exact config/binding, `SecretStr`, child-only official `POSTGRES_*`와 per-generation lifecycle을 구현한다. 실제 API lifespan과 worker startup은 complete opt-in에서 factory/resolver를 합성한다.
- [`test_lightrag_generation_factory.py`](../../backend/tests/unit/test_lightrag_generation_factory.py)와 [`test_lightrag_full_reindex.py`](../../scripts/phase-f/tests/test_lightrag_full_reindex.py)는 self-consistent counterfeit provider의 factory/controller 거부와 network-free production-child `runtimeConfigDigest` bootstrap 계약을 검증한다.
- [`test_lightrag_application_routing_postgresql.py`](../../backend/tests/repository/test_lightrag_application_routing_postgresql.py)는 controller가 만든 generation A/B row를 실제 disposable PostgreSQL pointer에 activate/rollback하고 settings-built API와 production worker가 같은 전환을 관찰함을 검증한다. API projection/reindex job의 `SUCCEEDED`, exact provider/version/runtime-digest rejection, disabled default와 secret-free public evidence도 같은 flow에서 확인한다.
- 2026-08-26 disposable Docker rehearsal은 실제 AGE+pgvector storage, auxiliary process kill, typed graph+pgvector fallback과 복구를 검증했다. 그러나 staging full corpus reindex/checksum, citation/rank acceptance, multi-process cutover/rollback은 아직 미검증이므로 ADR은 `Proposed`다.

## Revisit Triggers

- golden eval에서 LightRAG가 기본 경로 대비 의미 있는 품질 개선을 보이지 않는다.
- dependency의 storage·license·resource 요구가 self-host 제약과 맞지 않는다.
- typed graph + pgvector만으로 목표 recall과 citation 품질을 만족한다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0005 transactional outbox](./0005-transactional-outbox.md)
- [ADR-0008 typed ontology](./0008-typed-ontology-ledger.md)
- [개발 명세 12장 ontology와 LightRAG](../test-management-development-spec.md)
- [PF-SF1-T09 wave 4 integration](../phase-f/sprint-f1-wave4-verification.md)
