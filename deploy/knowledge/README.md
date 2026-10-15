# Knowledge auxiliary services

PostgreSQL의 Slack revision, `knowledge_nodes`, typed relation, lifecycle 상태가
원장이다. BGE-M3 embedding, pgvector, LightRAG은 검색 보조 인덱스이며 LightRAG
검색 결과만으로 `ACTIVE` 여부나 테스트 생성 근거를 확정하지 않는다.

## 기본 개발 Compose 상태

- `deploy/compose.dev.yml`의 PostgreSQL 이미지는
  `pgvector/pgvector:0.8.1-pg16`이고 `vector` extension만 설치한다.
- 기본 개발 이미지에는 Apache AGE가 없다. 따라서 AGE가 필요한 LightRAG
  `PGGraphStorage` 초기화 성공을 주장할 수 없다.
- Ollama 서비스는 profile 뒤에 있고 현재 중단 상태일 수 있다. unit test는
  fake transport/core만 사용하며 BGE-M3 또는 LightRAG live 성공을 검증하지 않는다.
- LightRAG가 `UNAVAILABLE` 또는 `ERROR`이면 호출자는 PostgreSQL typed graph와
  pgvector 검색으로 전환한다.

## 검증 기준 인터페이스

LightRAG embedded core의 비동기 호출은 공식 문서의 현재 이름을 따른다.

- lifecycle: `initialize_storages()` / `finalize_storages()`
- indexing: `ainsert(input, ids=..., file_paths=...)`
- retrieval: `aquery(query, param=QueryParam(...))`
- PostgreSQL storage: `PGKVStorage`, `PGVectorStorage`,
  `PGDocStatusStorage`, `PGGraphStorage`
- `PGGraphStorage`에는 Apache AGE가 필요하다.

공식 문서는 [Programming With LightRAG Core](https://github.com/HKUDS/LightRAG/blob/main/docs/ProgramingWithCore.md)와
[LightRAG Docker Deployment](https://github.com/HKUDS/LightRAG/blob/main/docs/DockerDeployment.md)를
기준으로 한다. Ollama embedding transport는 공식 `POST /api/embed` batch
형식을 사용하며 BGE-M3는 `bge-m3:latest`, 1024 dimension으로 고정 검증한다.

## Phase D LightRAG failover 계약

`LightRAGAuxiliaryIndex`의 query deadline 기본값은 5초다. Connection, OS, pool
transport 오류 또는 deadline 초과는 `LIGHTRAG_UNAVAILABLE` degradation으로
변환하며, 해당 요청은 LightRAG hint와 `LIGHTRAG` signal을 전부 버리고 같은
PostgreSQL session의 typed graph + pgvector 결과만 rank한다. 다음 정상 query는
자동 복구를 허용하고, 명시적 reinitialize가 실패하면 이전 pool을 ready로
간주하지 않으며 initialize 성공 전까지 fallback만 사용한다.

기존 PostgreSQL 통합 테스트는 disposable PostgreSQL 16 + pgvector 원장을 계속
실행한 상태에서 별도 LightRAG core/storage transport 경계에 connection failure와
실제 coroutine deadline을 주입한다. 두 동시 검색도 서로 다른 SQLAlchemy
session으로 실행해 동일한 grounded fallback order를 확인한다.

```bash
docker run --rm -d --name tc-hybrid-failover-test \
  -e POSTGRES_USER=tc -e POSTGRES_PASSWORD=tc-test-only \
  -e POSTGRES_DB=tc_test -p 127.0.0.1:55435:5432 \
  pgvector/pgvector:0.8.1-pg16
TC_TEST_DATABASE_URL='postgresql+psycopg://tc:tc-test-only@127.0.0.1:55435/tc_test' \
  node scripts/python.mjs -m pytest \
  backend/tests/postgres/test_knowledge_repository.py::test_hybrid_search_survives_auxiliary_transport_failure_timeout_and_recovery -q
docker stop tc-hybrid-failover-test
```

이 검증이 증명하는 경계는 실제 TC PostgreSQL schema/pgvector query, PostgreSQL
node/evidence 재해석, lifecycle filter, deterministic remaining-signal rank,
degradation metadata, timeout cancellation, 자동 복구와 reinitialize state다. 이
실패 주입 테스트만으로 실제 process 단절을 증명하지는 않는다.

## Disposable 실제 process 단절 rehearsal

`compose.failover.yml`은 기본 개발 stack과 분리된 다음 두 데이터베이스만 만든다.

- `tc-lightrag-failover-authoritative-postgres`: PostgreSQL 16 + pgvector TC 원장
- `tc-lightrag-failover-aux-postgres`: PostgreSQL 16 + Apache AGE 1.6.0 + pgvector
  0.8.1 LightRAG 보조 저장소

두 upstream image는 tag와 multi-architecture digest를 함께 고정한다. 보조 image는
Apache AGE 공식 PostgreSQL 16 image에 공식 pgvector image의 PostgreSQL extension
artifact만 복사한다. project, container, volume, network 이름은 모두
`tc-lightrag-failover-*` 범위로 고정하며 compose password는 저장소에 두지 않고
harness가 실행마다 임의 생성한다. 데이터베이스 이름은 모두 `test`를 포함하고
loopback port만 publish한다.

기본 명령은 read-only preview다.

```bash
node scripts/python.mjs scripts/rehearse_lightrag_failover.py
```

실제 실행은 preview에 출력되는 확인문을 정확히 요구한다.

```bash
node scripts/python.mjs scripts/rehearse_lightrag_failover.py \
  --run --confirm 'RUN TC LIGHTRAG FAILOVER REHEARSAL'
```

실행 전에는 고정 이름 resource collision과 `127.0.0.1:55438`, `:55439` port 점유를
거부한다. 실행 중에는 Alembic을 적용한 원장에 typed node/edge와 실제 1024차원
pgvector row를 넣고, pinned `lightrag-hku==1.5.6`의 `PGKVStorage`,
`PGVectorStorage`, `PGDocStatusStorage`, `PGGraphStorage`를 실제 asyncpg pool로
초기화한다. deterministic embedding과 custom KG fixture는 외부 LLM/Ollama 호출을
제거하지만 storage/query 경로는 fake가 아니다.

live test는 정상 query에서 PostgreSQL로 재해석되는 LightRAG UUID hint를 확인한
다음, label과 project ownership을 다시 검사하고 보조 container 하나만
`docker kill --signal KILL`로 실제 단절한다. 그 동안 원장 `SELECT 1`, typed graph + pgvector 결과 순서/신호,
`degraded=true`, `LIGHTRAG_UNAVAILABLE`, LightRAG signal 제거, 단일 및 동시 4개
요청의 2.5초 bound를 확인한다. 같은 보조 container를 restart하고 15초 안에
LightRAG hint가 복구되는지도 확인한다. 성공·실패와 관계없이 harness는 label을
검사한 뒤 이 compose project의 container, volume, network만 제거한다.

중단된 실행을 수동 정리할 때도 같은 확인문이 필요하다. 이름이 같더라도 project와
rehearsal label이 모두 일치하지 않으면 삭제하지 않는다.

```bash
node scripts/python.mjs scripts/rehearse_lightrag_failover.py \
  --cleanup --confirm 'RUN TC LIGHTRAG FAILOVER REHEARSAL'
```

### 증명 범위 구분

- 구현된 harness: digest-pinned AGE+pgvector image, 두 DB compose, preview/run/cleanup
  guard, test-only DB 검증, 실제 auxiliary stop/start, deterministic live assertion과
  자동 cleanup.
- 성공적으로 실행된 근거(2026-08-26, Docker Desktop 28.5.2): 실제 LightRAG table
  11개와 `tc_lightrag_failover_test_chunk_entity_relation` AGE graph를 생성했다.
  auxiliary KILL 후 단일 fallback 1.022초, 동시 4개 fallback 1.050초에 동일 결과와
  `LIGHTRAG_UNAVAILABLE`을 확인했고 원장은 계속 응답했다. restart 후 LightRAG hint는
  0.474초에 복구됐다. pytest `1 passed`, 마지막 harness JSON은
  `liveTestPassed=true`, `cleanupPerformed=true`, `remainingScopedResources=[]`였고
  별도 Docker inspect에서도 고정 container/volume/network가 모두 없었다.
- 남은 production gate: 실제 DNS 장애와 resolver cache, TLS 인증서/verify-full,
  load balancer·connection draining, 여러 API process의 독립 pool, 운영 부하 p95/p99,
  alert 발생·전달·on-call 확인, 운영 role/grant와 backup/restore. 이 local rehearsal은
  이 항목이나 실제 corpus 품질을 인증하지 않는다.

## Phase F 전체 corpus reindex 계약

[`lightrag_full_reindex.py`](../../scripts/phase-f/lightrag_full_reindex.py)는 기본적으로
비실행 preview만 출력한다. `--inspect`도 독립적으로 attestation된 authoritative/auxiliary
PostgreSQL을 `REPEATABLE READ, READ ONLY, DEFERRABLE`로 읽고, 원장·projection·pgvector·corpus
digest와 cardinality만 출력한다. corpus 본문, permalink, 접속 URL, 비밀번호는 evidence에
포함하지 않으며 문서 citation은 오직 `postgresql://knowledge/nodes/<UUID>`만 허용한다.

적용 provider가 충족해야 하는 machine-readable 계약은
[`lightrag-full-reindex-contract.json`](./lightrag-full-reindex-contract.json)에 있다. fresh
generation, bounded batches, full processed cardinality, exact corpus checksum, activation 직전
source digest 재검증, exact-target atomic compare-and-swap activation과 rollback이 모두 필수다.

TC PostgreSQL에는 immutable generation metadata, singleton/versioned active pointer,
append-only transition evidence를 보존하는 durable control plane이 있고, provider controller는
등록 후 expected generation/pointer version을 모두 확인하는 CAS activation과 CAS rollback만
허용한다. API와 worker의 `LightRAGRuntimeResolver`는 같은 pointer와 generation row를 원자적으로
읽고 `(pointerVersion, generationId, registrationDigest)`로 cache를 fence하며, 모든 query/index
전후에 binding을 다시 확인한다. no-active, missing/invalid generation, operation 중 pointer 변경은
stale LightRAG 결과를 버리고 typed graph+pgvector fallback으로 fail-closed한다.

`IsolatedLightRAGGenerationRuntimeFactory`는 `SecretStr` PostgreSQL 자격 증명과 password/workspace를
제외한 canonical runtime-config digest를 소유한다. Stable auxiliary-target digest, provider
contract/distribution/version, runtime-config digest와 generation workspace는 durable binding에서 서로
별도 필드로 검증한다. Provider ID는 단일 코드 상수
`OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID=official-lightrag-postgresql`이며 Settings composition,
factory, `LedgerBackedGenerationController`가 모두 이 exact 값을 강제한다. Config/binding/storage가
동일한 위조 ID에 합의해도 child 생성과 generation/ledger mutation 전에 fail-closed한다. 공식
LightRAG 1.5.6이 `POSTGRES_*`와 PostgreSQL
client pool을 process-global로 사용하므로 generation마다 전용 child interpreter를 시작하고,
child만 immutable `POSTGRES_*` 환경과 pool을 소유한다. Parent API/worker의 `os.environ`은 변경하지
않으며 resolver가 cache entry를 retire하면 마지막 in-flight lease 뒤 child를 bounded
finalize/terminate/kill하고 임시 working directory를 제거한다.

API의 실제 `app = create_app()` lifespan과 worker의 실제 `main() -> build_worker()` startup은 모두
`Settings`에서 `IsolatedLightRAGGenerationRuntimeFactory`를 만들고 TC PostgreSQL engine에
`LightRAGRuntimeResolver`를 합성한다. 이 경로는 `TC_LIGHT_RAG_ENABLED=false`가 기본이며, 아래 값이
모두 유효할 때만 켜지는 code-complete local composition이다. 따라서 계약은
`applicationRuntimeComposition=true`로 기록한다. 반면 repository/deploy evidence에는 approved
bootstrap module, secret-manager 값 또는 staging runtime instance가 없으므로
`runtimeFactoryConfigured=false`, `approvedBootstrapAvailable=false`,
`stagingCredentialsConfigured=false`다. `liveApplySupported=false`와 `executionStatus=NOT_RUN`도
그대로이며, 이 조건부 wiring을 staging 실행이나 cutover 증거로 해석하지 않는다.

### Wave 8 local disposable rehearsal

[`compose.wave8.yml`](./compose.wave8.yml)과
[`lightrag_full_reindex_disposable.py`](../../scripts/phase-f/lightrag_full_reindex_disposable.py)는
오직 새로 생성한 literal `pf-sf1-t09-wave8` 이름, comment, workspace와 loopback port만 허용한다.
기본 동작은 preview이며 execute는 exact confirmation, resource collision/label, source/auxiliary
database comment, empty auxiliary, PostgreSQL 16, AGE/pgvector, pinned local image identity를 먼저
검증한다. 현재 `tc`는 read-only dump/fingerprint로만 읽고, fixture와 generation ledger mutation은
새 disposable source copy에서만 수행한다.

2026-09-07 authoritative remediated run `sg9c42fd`는 official LightRAG 1.5.6 storage 4종의 initialization/index/query,
page-size-1 exact document/status/chunk/citation/vector/AGE inspection, immutable registration,
API/worker resolver CAS cutover, stale result 폐기, rollback/retention, child `SIGKILL`과 auxiliary
PostgreSQL stop 각각의 typed graph+pgvector fallback, provider recovery를 통과했다. AGE node/edge는
workspace-local internal ID와 provider insertion `created_at`을 제외한 stable entity identity,
semantic properties, endpoint topology/multiplicity로 canonicalize하며 prior/cutover/failed-activation
3개 generation digest가 정확히 같지 않으면 evidence seal 전에 실패한다. Cleanup도 durable
evidence에 봉인된 exact resource/comment/workspace/dump identity가 일치할 때만 별도 confirmation으로
실행한다. 명령, authoritative hash와 남은 gate는
[wave 8 report](../../docs/phase-f/sprint-f1-wave8-verification.md)가 권위다. 이 결과는 local
network-free sub-gate뿐이며 staging bootstrap/credential, live provider, staging apply, production
network/load/alert/RTO/RPO를 증명하지 않는다.

### API·worker 공통 opt-in 설정

API와 worker에 같은 non-secret binding 값을 배포하고 secret 값은 각 process identity가 approved
secret manager에서 주입받아야 한다. `backend/.env.example`은 이름/shape 예시일 뿐 credential
배포 수단이 아니다.

| 설정 | 요구사항 |
|---|---|
| `TC_LIGHT_RAG_ENABLED` | 명시적으로 `true`; 기본 `false` |
| `TC_LIGHT_RAG_AUTHORITY_ID` | attested auxiliary authority ID |
| `TC_LIGHT_RAG_AUXILIARY_TARGET_DIGEST` | workspace와 분리된 stable auxiliary authority SHA-256; controller row와 정확히 일치 |
| `TC_LIGHT_RAG_PROVIDER_ID` | 반드시 canonical `official-lightrag-postgresql`; 다른 유효한 identifier도 거부 |
| `TC_LIGHT_RAG_WORKSPACE` | 기존 static adapter 기본값. Versioned factory는 이 값으로 generation을 선택하지 않고 active durable row의 workspace를 child에 전달 |
| `TC_LIGHT_RAG_POSTGRES_HOST`, `PORT`, `USER`, `DATABASE` | 별도 AGE+pgvector auxiliary authority; authoritative TC DB credential 재사용 금지 |
| `TC_LIGHT_RAG_POSTGRES_PASSWORD` | secret manager에서 주입하는 `SecretStr`; 파일, argv, evidence, log 금지 |
| `TC_LIGHT_RAG_POSTGRES_MAX_CONNECTIONS`, `SSL_MODE` | 승인된 per-child pool bound와 staging remote의 `verify-full` |
| `TC_LIGHT_RAG_APACHE_AGE_AVAILABLE` | operator가 실제 auxiliary capability를 확인한 뒤에만 `true`; 자동 probe가 아님 |
| `TC_LIGHT_RAG_RUNTIME_BOOTSTRAP` | child에서 import 가능한 reviewed `module:function`; LLM/embedding callable만 반환하고 secret을 parent로 반환하지 않음 |
| `TC_LIGHT_RAG_WORKING_DIRECTORY_ROOT` | API/worker identity만 쓸 수 있는 absolute local path |
| `TC_LIGHT_RAG_STARTUP_TIMEOUT_SECONDS`, `OPERATION_TIMEOUT_SECONDS`, `SHUTDOWN_TIMEOUT_SECONDS` | bounded child lifecycle 값; 기본 `20/30/5`초 |

Versioned opt-in의 authority/target/provider/connection 값 하나라도 빠지거나 disabled 상태에 activation
값이 섞이면 `Settings`가 startup을 거부한다. Provider
ID/bootstrap shape, installed `lightrag-hku==1.5.6`, storage/AGE 조합도 resolver를 만들기 전에
검증한다. Child는 bootstrap에 `authorityId`, `providerId`, `workspace`, `runtimeConfigDigest`만
전달하며 존재하지 않는 `configDigest` alias를 사용하지 않는다. Bootstrap import, auxiliary 연결과 공식 storage 초기화는 첫 query/index 때 child에서
lazy 실행되므로 startup/readiness만으로 live provider 성공을 주장할 수 없다.

### Secret·isolation·lifecycle 경계

Parent는 배포 방식에 따라 `TC_LIGHT_RAG_POSTGRES_PASSWORD`를 process environment에서 읽을 수 있고
factory memory에는 이를 `SecretStr`로 보유한다. 보장하는 경계는 password가 repr/config digest/
attestation/error/log/argv에 들어가지 않고, 공식 provider가 읽는 `POSTGRES_*`와 process-global
pool은 parent에 설치되지 않는다는 것이다. Password는 이미 열린 stdin으로 선택된 child에만
전달되고, child는 unrelated environment를 상속하지 않으며 provider stdout/stderr도 application
log로 전달하지 않는다.

각 API/worker process는 한 resolver cache를 독립 소유하고, cache miss마다 generation 전용 child를
lazy 생성한다. Pointer 변경/rollback은 이전 entry를 retire하고 마지막 in-flight lease가 끝난 뒤
bounded finalize -> terminate -> kill 순서로 child를 정리하며 temporary working directory를
삭제한다. API lifespan 종료와 worker `finally`도 resolver를 finalize한다. Parent의 강제 종료처럼
normal cleanup을 건너뛴 경우 남은 child/directory 확인과 안전한 orphan cleanup은 staging runbook
소유자의 작업이며 자동 성공 증거가 아니다.

Local disposable PostgreSQL 검증은 controller가 생성한 binding을 API/worker resolver가 generation
A → B → CAS rollback A 순서로 관찰하고 provider/version/runtime digest mismatch와 secret 노출을
fail-closed한다. Wave 8은 namespace-locked network-free bootstrap으로 production child와 실제 local
AGE+pgvector official adapter initialize/index/query/finalize를 추가 실행했다. 이는 local contract와
disposable storage 증거이며 approved staging bootstrap, staging corpus/live provider 또는 production
operator cutover 증거가 아니다.

### Failure·rollback 경계

잘못된 config는 application startup을 실패시킨다. No-active/missing/stale binding, child startup/
protocol/operation failure와 operation 중 pointer 변경은 해당 LightRAG 결과를 버리고 typed graph +
pgvector fallback을 반환한다. Runtime은 active pointer를 자동 변경하거나 원장을 롤백하지 않는다.
Generation activation/rollback은 오직 provider controller의 exact expected identity/version CAS이며,
ambiguous activation에서도 새 generation이 정확히 current일 때만 이전 generation으로 CAS rollback한다.
현재 application에는 full-corpus apply entrypoint가 없으므로 운영자가 `POSTGRES_WORKSPACE`나 다른
parent environment를 수동 변경해 activation 성공으로 기록해서는 안 된다.

기본 preview는 secret environment를 읽거나 네트워크/DB에 연결하지 않는다.

```bash
node scripts/python.mjs scripts/phase-f/lightrag_full_reindex.py \
  --target-name tc-phase-f-knowledge-staging-copy \
  --target-class staging-copy \
  --authoritative-database-name tc_phase_f_knowledge_staging_copy \
  --authoritative-database-url-env TC_PHASE_F_KNOWLEDGE_DATABASE_URL \
  --auxiliary-database-name tc_phase_f_lightrag_staging_copy \
  --auxiliary-database-url-env TC_PHASE_F_LIGHTRAG_DATABASE_URL \
  --active-workspace tc
```

`--inspect`는 preview가 출력한 exact confirmation과 두 secret-backed database URL을 요구한다.
authoritative database comment는 기존 Phase F attestation을, auxiliary comment는 아래 exact
attestation을 사용한다.

```text
tc-phase-f-lightrag-auxiliary:staging-copy:tc-phase-f-knowledge-staging-copy
```
