# Sprint 6 독립 검수 리포트

**기준일:** 2026-08-21  
**범위:** `P3-S6-T01~T13`, Sprint 6 종료 체크, PostgreSQL 16 + pgvector, FastAPI, generated client, Web  
**판정:** 재현 가능한 code/deterministic integration gate PASS, live LightRAG storage와 실제 Slack corpus 품질 gate OPEN

## 1. 결론

`P3-S6-T01~T07`, `T10~T13`과 네 개 Sprint 6 종료 체크는 독립 재실행 근거가 있어 완료로 판정한다. Ontology v1 relation/evidence 강제, 원장+outbox atomicity, at-least-once replay, Slack 후보 validator와 상태 판별, case/execution/issue projection, BGE-M3 1024차원 embedding/pgvector, 동일 API의 LightRAG 장애 fallback, 실제 Web 목록·검색·탐색·편집이 모두 재현됐다.

`P3-S6-T08`과 `T09`는 완료하지 않는다. 코드에는 current LightRAG storage class(`PGKVStorage`, `PGVectorStorage`, `PGDocStatusStorage`, `PGGraphStorage`)와 Apache AGE requirement, 보조 adapter와 hybrid API 경계가 있지만 이 검수 환경에는 `lightrag` package와 AGE extension이 없어 실제 storage 초기화·source indexing·LightRAG rank merge를 실행하지 못했다. 따라서 Sprint 6 code gate 통과를 Phase 3 또는 live AI/search 성공으로 확대하지 않는다.

## 2. 검수 환경

| 항목 | 실제 값 |
|---|---|
| Host | macOS, Python 3.13.7, Node 24.13.0, pnpm 10.28.2 |
| Disposable DB | 전용 `pgvector/pgvector:0.8.1-pg16` container, 별도 `tc_sprint6_test` database |
| PostgreSQL | 16.12 |
| pgvector | 0.8.1, `knowledge_embeddings.embedding vector(1024)` |
| Alembic head | `20260821_0008` |
| Web/API browser fixture | synthetic ledger 6 nodes(5 ACTIVE, 1 SUPERSEDED), 4 edges, ACTIVE explorer evidence 9건, deterministic BGE-M3-shaped 1024 vectors |
| Live Ollama probe | `bge-m3:latest` `/api/embed` 1건 성공, dimension 1024, 약 2.06초 total duration |
| LightRAG/AGE | Python package 없음, disposable PostgreSQL AGE extension 없음 |
| External credentials | 현재 process에 Slack bot/signing secret과 Google client ID/secret 없음 |

브라우저 fixture의 embedding provider는 실제 Ollama가 아니라 deterministic 1024차원 provider다. 별도 live Ollama probe는 transport/model/dimension만 확인했으며, qwen 기반 knowledge extraction이나 실제 corpus 품질을 증명하지 않는다.

## 3. 자동 검증 증거

| 명령/흐름 | 결과 | 직접 증명하는 범위 |
|---|---:|---|
| `pnpm check` | PASS | ESLint/ruff, TS/Python typecheck, TS 112 + Python non-PostgreSQL 129 tests, 5 strict Schema·12 fixtures, ADR, OpenAPI/client drift |
| `pnpm --filter @tc/web build` | PASS | production TypeScript + Vite build, 91 modules |
| `TC_TEST_DATABASE_URL=... pnpm test:migration` | 43 PASS | disposable PostgreSQL base/head migration과 기존 repository 회귀 |
| `pytest backend/tests/postgres/test_knowledge_repository.py` | 5 PASS | DB relation/evidence trigger, append-only source, atomic outbox/replay, ledger projection, pgvector/dedup |
| `pytest apps/api/tests -m postgres` | 10 PASS | 실제 FastAPI/PostgreSQL API E2E 전체; Sprint 6 extraction consumer와 Knowledge list/detail/patch/search/explorer/jobs 포함 |
| `pytest test_knowledge_extraction.py test_lightrag_adapter.py` | 15 PASS | 후보 grounding/status와 LightRAG unavailable/error fallback |
| Sprint 6 contract test | 6 PASS | knowledge API/extraction Schema, synthetic deletion/revision semantics와 stale citation rejection |
| live Ollama `/api/embed` | 1 PASS | 현재 host `bge-m3:latest`가 1024차원 vector를 반환 |

첫 `pnpm check`에서는 Web Knowledge 파일이 component 외 helper를 export해 `react-refresh/only-export-components` warning 2건으로 실패했다. helper를 파일 내부로 한정하고 같은 의미를 렌더링 테스트로 검증하는 최소 수정 후 전체 check를 처음부터 재실행해 통과했다.

## 4. PostgreSQL repository와 API 결과

### 4.1 Schema·근거·원자성

- 허용되지 않은 v1 source/predicate/target 조합과 evidence 없는 node/edge를 application validator와 PostgreSQL trigger가 모두 거부했다.
- evidence는 append-only `knowledge_source_records` FK를 가지며 같은 revision/content replay는 같은 source를 재사용하고 내용 충돌은 거부했다.
- 강제 rollback한 test case mutation은 업무 row와 outbox가 모두 0건이고, commit된 mutation은 둘 다 존재했다.
- 같은 outbox ID를 두 번 projection하면 checkpoint 1개, node/evidence 1세트만 남고 두 번째 receipt는 `replayed=true`였다.
- TEST_CASE, EXECUTION, ISSUE outbox가 `INSTANCE_OF`, `FOUND_BY` typed edge를 만들고 실제 API E2E의 Slack outbox는 WORK_ITEM/DECISION extraction→projection→embedding을 수행했다.

### 4.2 Embedding·검색

- repository가 BGE-M3 외 model과 1024가 아닌 dimension을 거부한다.
- `(owner, model, model_version, content_hash)` replay는 embedding row를 중복 생성하지 않았다.
- 실제 `vector_cosine_ops` 거리 쿼리는 동일 vector를 0 distance/1.0 score로 첫 번째 반환했다.
- API failure-injection 검색은 LightRAG가 unavailable이어도 `degraded=true`, `degradationReasons=[LIGHTRAG_UNAVAILABLE]`를 반환했고, result signals에는 `TYPED_GRAPH`, `VECTOR`, `EXACT_TEXT`, `FEATURE_PROXIMITY`, `RECENCY`가 포함됐다.
- `ACTIVE` feature 검색 결과에는 `TEST_CASE`, 실패 `EXECUTION`, `ISSUE`가 함께 포함됐고 SUPERSEDED/RETRACTED node는 제외됐다.

## 5. Synthetic extraction/status/citation 평가

이 수치는 실제 Slack/QA corpus나 live qwen 출력이 아니라 deterministic provider와 synthetic fixture에만 적용된다.

| 지표 | 결과 | 표본/해석 |
|---|---:|---|
| 추출 누락 결정 | 0 / 4 | provider protocol test의 REQUIREMENT·DECISION·CONSTRAINT·OPEN_QUESTION 기대 후보 4개 모두 validator 통과 |
| 상태 판별 오류 | 0 / 7 | reversal 2, unresolved conflict 2, deletion retraction 1, explicit retraction 2 candidate의 기대 상태 일치 |
| 잘못된 relation 허용 | 0 / 6 negative cases | 미허용 source/target 5개와 cyclic supersession 1개가 모두 거부됨 |
| 허용 relation 오거부 | 0 / 11 | v1 predicate별 대표 허용 조합 11개 모두 통과 |
| golden semantic case 오류 | 0 / 2 | deletion retraction 1건, exact evidence revision 1건의 Schema+semantic validation 통과 |
| citation revision 오류 | 0 / 4 expected refs | 두 golden case의 node evidence 3개와 edge evidence 1개가 존재하는 정확 revision을 참조 |
| stale/unknown citation 검출 실패 | 0 / 2 mutations | active candidate를 과거 revision으로 바꾼 경우와 unknown tombstone revision 모두 semantic validator가 거부 |
| hallucinated/unknown excerpt 검출 실패 | 0 / 2 mutations | 원문에 없는 normalized span과 bundle에 없는 revision을 provider validator가 거부 |

표본이 작고 synthetic이며 deterministic expected output 자체를 중심으로 한 평가라 recall/precision의 통계적 추정값은 아니다. 실제 Slack sample의 누락 결정 회수율, 실제 LLM relation 오류율, citation 정확도와 LightRAG 유무에 따른 retrieval 개선치는 아직 측정할 수 없다.

## 6. 실제 Orca 브라우저 검수

Web은 `VITE_API_BASE_URL`을 검수 API로 지정했고 mock route/fallback은 사용하지 않았다. 브라우저 network는 PostgreSQL-backed FastAPI의 `/api/knowledge/*`에 직접 연결됐다.

1. `/knowledge` 진입 시 기본 `ACTIVE` 목록 5건과 current decision 1건을 표시하고 SUPERSEDED decision은 제외했다.
2. FEATURE를 선택하자 `FEATURE → DECISION → TEST_CASE → EXECUTION → ISSUE` 5개 lane, 5 nodes, 4 typed edges가 나타났다.
3. `Password reset` 검색은 5 results와 선택 이유/신호/citation을 표시했고 case, iOS FAIL execution, issue를 함께 반환했다.
4. LightRAG unavailable 상태에서도 typed graph + pgvector 결과가 유지되고 비차단 `DEGRADED · LIGHTRAG_UNAVAILABLE` 안내가 표시됐다.
5. decision 상세에서 source citation과 evidence를 확인하고 inline editor로 feature scope를 저장했다. 실제 API/DB resource version이 v1→v2, audit 0→1, reindex job 0→1로 증가했고 화면에 변경자·사유·scope가 다시 표시됐다.
6. SUPERSEDED 필터를 켜자 `Reset links never expire` 과거 결정이 current decision과 분리돼 나타났다.

API에서 같은 fixture를 재검증한 결과 search node types는 `DECISION/EXECUTION/FEATURE/ISSUE/TEST_CASE`, explorer는 정확히 `FEATURE/DECISION/TEST_CASE/EXECUTION/ISSUE`, edge 4건, evidence 9건, `truncated=false`였다.

## 7. T01~T13 판정

| Task | 판정 | 근거/남은 경계 |
|---|---|---|
| T01 | PASS | migration head와 DB trigger가 v1 관계·evidence를 강제 |
| T02 | PASS | node/edge/evidence/source/embedding repository PostgreSQL 5 tests |
| T03 | PASS | 원장+outbox rollback/commit과 consumer checkpoint replay |
| T04 | PASS(synthetic) | provider-neutral Slack bundle pipeline, schema/evidence validator; live LLM provider는 미실증 |
| T05 | PASS(synthetic) | reversal/conflict/delete/retraction 상태 7/7 |
| T06 | PASS | TEST_CASE/EXECUTION/ISSUE와 Slack outbox projection/replay |
| T07 | PASS | live BGE-M3 1024 probe + DB version/hash dedup + cosine search |
| T08 | OPEN | adapter/config는 있으나 live `PGGraphStorage`+AGE storage/index/citation round trip 없음 |
| T09 | OPEN | typed graph+pgvector ranker/API는 PASS; 실제 LightRAG candidate merge 품질 미검증 |
| T10 | PASS(injected) | no-core, initialize/insert/query failure와 PostgreSQL API fallback; 실제 running LightRAG process stop은 아님 |
| T11 | PASS | PostgreSQL API patch/audit + actual browser scope/version/audit flow |
| T12 | PASS | actual browser 5-lane explorer + API edge/evidence payload |
| T13 | PASS(synthetic baseline) | 이 문서에 누락·관계·citation 수치 기록; live corpus baseline은 OPEN |

## 8. 종료 체크와 미실증 경계

Sprint 6의 네 code-gate 종료 조건은 PASS다: current/past 구분, non-ACTIVE 확정 검색 제외, feature별 case/failure/issue 동시 검색, LightRAG unavailable fallback을 모두 실제 PostgreSQL/API/Web으로 재현했다.

다음은 주장하지 않는다.

- 실제 Slack App 설치·test channel 자동 수집 또는 실제 Slack sample extraction 품질
- qwen3:8b/9b가 만든 knowledge 후보의 golden 정확도나 운영 latency
- live LightRAG package, `PGGraphStorage`, Apache AGE의 초기화·index/reindex·citation 성공
- LightRAG가 typed graph+pgvector보다 retrieval 품질을 개선한다는 결론
- Google OAuth/My Drive credential·ACL·quota 성공
- 운영 장비의 동시성, 장시간 안정성, backup/restore, production SLO

따라서 Phase 3 종료 조건은 열린 상태다. 다음 gate는 AGE가 포함된 disposable PostgreSQL과 version-pinned LightRAG를 준비해 storage migration/index/citation round trip을 실행하고, 익명화한 실제 Slack/QA corpus로 fallback 대비 retrieval·citation 지표를 비교하는 것이다.
