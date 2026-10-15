# Phase D Sprint D1 기반 구현 보고서

## 결론

Phase C의 실제 Slack 설치를 기다리는 동안 병행 가능한 Phase D 기반을 구현했다. 과거 QA
CSV는 원본을 repository나 DB에 복사하지 않고 개인정보를 정제한 deterministic JSONL
preview로 변환할 수 있다. Knowledge 화면은 Slack 원문과 QA 원장으로 이동할 수 있고,
온톨로지 retrieval 품질은 합성·익명화 fixture로 반복 평가할 수 있다. PostgreSQL을 원장으로
삼는 hybrid ranker, 설명 가능한 검색 계약과 UI, 안전한 projection rebuild, LightRAG 장애
fallback까지 구현했다. LightRAG 네 storage의 초기화와 종료뿐 아니라 별도 AGE+pgvector
프로세스의 실제 `KILL`과 복구도 PostgreSQL 16 환경에서 검증했다. 운영 결과 snapshot 평가기와
개인정보를 제거한 golden sample 후보 builder도 준비했다.

이 결과는 실제 Slack workspace ingestion, 제품 role/권한, 운영 BGE-M3·Ollama를 사용한
indexing·retrieval 품질, DNS/TLS·다중 API·운영 부하·alert 전달까지 완료했다는 의미는 아니다.

## Authoritative hybrid ranker

검색 결과의 최종 권한은 PostgreSQL `knowledge_nodes`와 evidence에 둔다. LightRAG 응답은
UUID가 현재 node 또는 evidence로 해석될 때만 후보 signal로 사용하고, 해석되지 않거나 중복된
hint는 점수를 만들지 않는다. ranker는 typed graph 0.04, exact 0.36, pgvector 0.28,
기능 거리 0.18, LightRAG 0.10, 최신성 0.04의 고정 가중치를 사용한다.

- lifecycle, source kind, node type, status와 선택 feature를 검색 전에 적용한다.
- 같은 node는 한 번만 반환하고 score 내림차순과 UUID 오름차순으로 안정 정렬한다.
- cursor pagination은 같은 검색에서 절대 rank와 순서를 유지한다.
- LightRAG가 실패하면 해당 hint와 signal을 모두 버리고 typed graph + pgvector 순서를 다시
  결정적으로 계산한다.

각 signal 단독/조합, 해석 불가능한 hint, evidence UUID 해석, lifecycle 제외, cursor와 degraded
fallback 동등성을 단위 및 PostgreSQL 통합 테스트로 검증했다.

## 설명 가능한 검색 API와 UI

Sprint 6 계약과 OpenAPI에는 각 결과의 절대 rank, lifecycle/status, source kind, nullable
feature distance, 최신성 기준 시각·age seconds·정규화 score, 실제 rank signal과 사람이 읽는
reason, typed degradation reason을 포함한다. API 모델은 rank 연속성, score 비증가,
response/item degradation 일치, unavailable signal 배제를 검증한다. Knowledge 화면은 이 값을
한국어로 표시하고 원문 또는 QA 원장 이동을 유지한다.

## Projection rebuild 동등성

[`rebuild_knowledge_projection.py`](../../scripts/rebuild_knowledge_projection.py)는 기본이
read-only preview다. apply는 정확한 확인문과 직전 preview의 event order/source ledger digest를
요구한다. 실행 중에는 PostgreSQL advisory lock과 `REPEATABLE READ` transaction을 사용하고,
다음 여섯 projection table만 초기화한다.

- `knowledge_projection_events`
- `knowledge_embeddings`
- `knowledge_evidence`
- `knowledge_edges`
- `knowledge_nodes`
- `knowledge_source_records`

Slack create/edit/delete와 테스트케이스·실행·이슈 outbox를 증분 처리한 뒤 두 번 rebuild했다.
새 UUID가 만들어져도 canonical node·edge·evidence·status·source revision·checkpoint·embedding
digest는 같았고, source ledger와 outbox digest는 변경되지 않았다. LightRAG reindex는 이
PostgreSQL transaction 밖의 보조 인덱스 작업이므로 별도 maintenance 단계로 둔다.

## LightRAG 장애 fallback 경계

LightRAG query deadline은 기본 5초다. connection, transport, pool, coroutine timeout은
`LIGHTRAG_UNAVAILABLE`로 변환한다. 실패 요청은 stale LightRAG hint를 버리고 PostgreSQL typed
graph + pgvector만 사용하며, 다음 정상 요청과 명시적 reinitialize에서 복구할 수 있다.
PostgreSQL 16+pgvector 원장은 실제로 유지한 채 별도 auxiliary transport에 connection failure와
timeout을 주입해 동시 요청·순위 안정성·복구를 먼저 검증했다.

이후 digest가 고정된 PostgreSQL 16 AGE+pgvector auxiliary를 실제 official LightRAG 네 storage와
asyncpg pool에 연결하고 auxiliary container를 `docker kill --signal KILL`로 중단했다. 주 원장
`SELECT 1`은 계속 성공했고, 단일 fallback은 1.022초, 동시 4요청은 1.050초 안에 같은 순서와
`LIGHTRAG_UNAVAILABLE`을 반환했다. auxiliary 재시작 후 0.474초 안에 LightRAG hint가 복구됐다.
pytest 1건과 guarded harness가 통과했고 컨테이너·볼륨·네트워크가 모두 삭제됐음을 별도로
확인했다.

DNS/TLS/load balancer, 다중 API process, 운영 부하 p95/p99와 alert 전달은 Phase F 운영
비기능 gate로 남긴다. 세부 실행·안전 guard·한계는
[`deploy/knowledge/README.md`](../../deploy/knowledge/README.md)에 기록한다.

## 과거 QA CSV 정규화

[`historical_corpus.py`](../../backend/src/tc_backend/knowledge/historical_corpus.py)는 다음을
보장한다.

- UTF-8 quoted multiline cell과 파일 내부의 2열형·3열형·이동형 layout을 보존한다.
- 고정 header 대신 실행 결과 열과 workflow 상태를 사용해 행별 의미 layout을 판별한다.
- 경로·공백·Unicode NFKC를 정규화하고 Jira, URL, 담당자와 식별자를 생성 context에서
  제거한다.
- source file fingerprint, source row hash와 exact duplicate 통계를 남긴다.
- DB write 또는 apply mode가 없는 preview-only 경계다.
- 결과 포함 후보와 실제 record 수, 누락 reason을 summary에 남긴다.

사용자가 제공한 실제 세 파일의 최종 dry-run 결과는 다음과 같다.

| corpus | 논리 행 | record | 결과 포함 후보 | 결과 후보 누락 | section | ignored | exact duplicate |
|---|---:|---:|---:|---:|---:|---:|---:|
| POS 어드민 | 2,976 | 2,864 | 2,838 | 0 | 98 | 13 | 377 |
| 모바일 POS | 13,306 | 12,350 | 12,203 | 0 | 776 | 179 | 8,692 |
| StoreX | 4,523 | 4,339 | 4,339 | 0 | 176 | 6 | 1,438 |
| 합계 | 20,805 | 19,553 | 19,380 | 0 | 1,050 | 198 | 10,507 |

실행 명령은 다음과 같다.

```bash
node scripts/python.mjs scripts/preview_historical_qa_csv.py <source.csv>
```

인식 목록 밖의 자유형 실행 결과와 명시적 의미 layout도 없는 결과 없는 행은 section 또는
ignored로 남는다. near duplicate는 embedding을 사용하는 retrieval 단계에서 처리한다.

## Retrieval synthetic gate

[`knowledge-retrieval-evaluation.md`](./knowledge-retrieval-evaluation.md)의 합성 gate 결과는
다음과 같다.

| 지표 | 결과 |
|---|---:|
| recall@1 | 0.625 |
| recall@3 | 1.000 |
| ACTIVE evidence coverage | 100% |
| inactive knowledge misuse | 0 |
| citation revision 연결률 | 100% |
| 중복 결과 | 0 |
| reason/rank 완전성 | 100% |
| LightRAG degraded fallback 유지율 | 100% |

이 gate는 작은 고정 vector와 deterministic LightRAG double을 사용한다. 운영 검색 품질은
실제 Slack golden sample과 운영 embedding 분포로 별도 평가해야 한다.

## 운영 retrieval 평가와 golden 후보

[`evaluate_knowledge_retrieval.py`](../../scripts/evaluate_knowledge_retrieval.py)는 기존 synthetic
candidate gate를 유지하면서 명시적인 offline `RESULT_SNAPSHOT` 모드를 제공한다. 평가기가
network·token·embedding service를 직접 호출하지 않으며 다음을 모두 충족할 때만 release를
인증한다.

- dataset이 non-synthetic이고 사람 검토 metadata가 완전하다.
- 기존 테스트케이스, 실패 실행, 이슈 세 relevance category가 모두 포함된다.
- runtime/model/search provenance와 dataset/results SHA-256 fingerprint가 있다.
- lifecycle 제외, recall, evidence/citation, duplicate, reason/rank와 degraded retention 기준을
  모두 통과한다.

JSON과 Markdown report는 명시적 출력 경로에만 기록한다. synthetic, 미검토 label, provenance
누락은 fail-closed다.

[`build_knowledge_golden_sample.py`](../../scripts/build_knowledge_golden_sample.py)는 승인된
read-only JSON export만 입력받는다. DB connector와 credential 경로가 없고 기본 실행은
preview다. 명시적 repository 외부 출력 경로가 있을 때만 7개 source/lifecycle 계층을 결정적으로
표본 추출하며 Jira·URL·email·사람 식별자·secret을 제거한다. 결과는 항상
`UNLABELED/NOT_CERTIFIED`이고 부족한 Slack·inactive·QA 계층을 명시한다. 현재 로컬 원장은
테스트케이스 20건·실행 20건·이슈 6건이지만 Slack work item과 knowledge projection은 0건이라
운영 품질 인증은 수행하지 않았다.

## Knowledge 원문 이동

Knowledge 검색·탐색·상세 근거에서 다음을 제공한다.

- citation URI가 있는 Slack 원문 revision 이동
- rank, 최신성, 검색 signal과 degraded 이유의 한국어 표시
- 삭제된 원문의 tombstone 안내
- URI를 제공하지 않는 출처의 비활성 안내
- 모바일 44px action과 겹침 방지 layout

`TEST_CASE_VERSION`, `EXECUTION_ATTEMPT`, `ISSUE_SNAPSHOT` citation은 API serializer가
원장 row를 조회해 각각 case key, cycle 실행 context, issue key 기반 app-relative URI를 만든다.
Knowledge UI는 이 URI를 내부 이동으로 처리하고 Slack permalink는 외부 원문 이동으로
유지한다. 원장 row를 해석할 수 없는 citation은 raw UUID로 경로를 추측하지 않고 이동 불가로
표시한다. 실행 화면 URI에는 case·platform·attempt·run context가 포함되지만 목적지 화면이
특정 과거 attempt를 자동 선택하는 기능은 별도 UX 후속이다.

## Slack source bundle projection

Slack work item projection은 root와 thread reply의 create/edit/delete revision 전체를 원장
순서로 bundle화한다. work-item knowledge node에는 bundle content와 message/revision count를
기록하고, 각 revision의 permalink와 tombstone을 immutable evidence로 연결한다. 동일 event
재처리, 같은 aggregate version의 중복 event, 이전 version의 늦은 도착은 checkpoint로
결정적으로 처리하며 knowledge node나 evidence를 중복 생성하지 않는다.

실제 PostgreSQL/pgvector schema에서 root/thread citation, edit/delete lifecycle,
duplicate/out-of-order 세 시나리오를 검증했다. 실제 Slack workspace에서의 end-to-end 수집은
Slack credential과 allowlist가 필요한 Phase C live gate로 남는다.

## 검증

- `pnpm check`: 통과
- TypeScript unit: Web 141 passed, 1 skipped; contracts 40, API client 21, UI 6 passed
- Python unit/API/script: 201 passed, postgres 21 deselected
- `pnpm knowledge:retrieval:eval`: PASS
- `pnpm --filter @tc/web build`: PASS
- PostgreSQL focused: Knowledge repository + explainability API 14 passed
- LightRAG live storage lifecycle: AGE 1.7.0·vector 0.8.6, 네 storage initialize/finalize 통과
- LightRAG process severing: PostgreSQL 16 AGE+vector auxiliary `KILL`/복구 live pytest 통과
- 장애 리허설 잔여 scoped Docker container·volume·network: 0

## 남은 live gate

1. 실제 Slack App 설치, allowlist 참여와 root/thread revision 수집
2. 제품 PostgreSQL 16 role/권한으로 LightRAG storage lifecycle 재검증
3. 운영 Ollama·BGE-M3를 사용한 LightRAG indexing/citation round trip과 hybrid retrieval 평가
4. 제품 크기 데이터로 rebuild maintenance window와 별도 LightRAG reindex를 예행한다.
5. DNS/TLS·load balancer·다중 API traffic에서 fallback p95/p99와 alert 전달을 검증한다.
6. 승인된 정제 preview를 ontology source로 적재하는 별도 apply pipeline
7. 승인된 read-only export를 사람 검토한 뒤 운영 result snapshot release gate를 실행한다.

## PD-SD1-T01 LightRAG PostgreSQL storage 경계

2026-08-26에 official `lightrag-hku==1.5.6`과 `asyncpg==0.31.0`을 고정하고,
`PGKVStorage`, `PGVectorStorage`, `PGDocStatusStorage`, `PGGraphStorage`만 허용하는
runtime/config 경계를 추가했다. PostgreSQL host·port·user·password·database·pool·workspace와
SSL mode를 검증하며, official runtime이 읽는 `POSTGRES_*` process environment가 검증된
설정과 다르면 import 또는 연결 전에 실패한다. LightRAG receipt와 retrieval candidate는 계속
`authoritative=false`이고 장애 시 `typed_graph+pgvector` fallback을 사용한다.

의존성 lock 전체를 새 환경에 설치한 뒤, official 문서의 AGE+pgvector 이미지
`gzdaniel/postgres-for-rag:pg18-age-pgvector` digest
`sha256:b24e9a7ff85440281970648e6fd5c73c72b7f67f8cf7ea3a791492812d382e80`로
disposable database를 실행했다. 그 환경에서 LightRAG 1.5.6 네 storage의 실제
`initialize_storages()`와 `finalize_storages()`가 성공했고, AGE 1.7.0, vector 0.8.6,
11개 `lightrag_*` table과 workspace graph 생성을 확인했다.

초기 storage 증거는 PostgreSQL 18 lifecycle 범위였지만, 후속 장애 rehearsal은 PostgreSQL 16
AGE 1.6.0+vector 0.8.1에서 실제 asyncpg storage pool과 process 단절·복구까지 확장했다. 다만
제품 role/grant, 실제 Ollama LLM/embedding을 사용한 source indexing·citation round trip과
reindex 동등성은 후속 live gate다. 재현 가능한 integration test는 `POSTGRES_*` 변수가 이름에
`test`가 포함된 disposable database를 가리킬 때만 실행된다.
