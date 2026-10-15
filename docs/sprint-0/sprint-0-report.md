# Sprint 0 통합 검증 보고서

| 항목 | 값 |
|---|---|
| 점검일 | 2026-08-20 (Asia/Seoul) |
| 범위 | `P0-S0-T01`~`P0-S0-T12`, ADR-0001~0012, Sprint 0 종료 gate |
| 판정 | **ADR gate 통과, Sprint 0 미종료** — 10개 Task 완료, `T02`·`T08` 미완료 |
| 검증 환경 | Apple M4 10-core, 16 GiB, macOS 15.6.1 arm64, Node 24.13.0, pnpm 10.28.2, Python 3.13.7, Docker 28.5.2 |

## 1. 결론

코드·계약·DB·UI·로컬 AI 후보의 실행 가능한 검사는 통과했다. 깨끗한 임시 복제본에서 문서의 `python3 -m venv .venv`, `pnpm run setup`, `pnpm dev` 순서만으로 PostgreSQL, Web, API, Worker를 기동했고 API health 200과 Web HTML을 확인했다. 전체 lint, typecheck, TypeScript/Python unit, JSON Schema, OpenAPI/client drift, Web build, PostgreSQL migration/repository 검사가 통과했다.

아키텍처 gate는 통과했다. 개발 명세의 주요 기술 결정 12개가 ADR-0001~0012의 정확히 하나에 연결되고, 6개 Accepted와 6개 Proposed 상태가 현재 evidence와 일치한다. Accepted 구현 충돌은 0건이며, Proposed ADR은 모두 누락 evidence와 2개 이상의 재검토 trigger를 명시한다. 오래된 ADR-0001·0011 evidence와 ADR-0008의 미구현 validator 경계를 바로잡았다.

다만 Sprint 0은 종료하지 않는다. `T02`의 12개 sample은 모두 synthetic이며 실제 Slack root/thread를 익명화한 자료가 하나도 없다. `T08`은 CI workflow와 실패 명령은 구현됐지만 저장소에 remote와 branch protection 증거가 없어 실패한 status가 실제 merge를 차단한다는 완료 조건을 확인할 수 없다.

## 2. Task별 판정

| Task | 판정 | 증거와 제한 |
|---|---|---|
| `T01` | 완료 | `packages/contracts`의 Draft 2020-12 Schema 5개가 enum·필수 payload·Slack parse·AI output의 원본이다. `pnpm test:schema` strict compile과 12개 fixture 검증 통과. |
| `T02` | **미완료** | 정상·누락·번복·충돌 synthetic fixture는 각 3건, 합계 12건이다. 실제 익명화 Slack sample이 아니므로 완료 조건을 충족하지 않는다. |
| `T03` | 완료 | root workspace와 Web/API/Worker entrypoint가 있으며 깨끗한 임시 복제본에서 `pnpm dev` 한 명령 기동, API 200, Web 응답, worker ready 확인. |
| `T04` | 완료 | `pgvector/pgvector:0.8.1-pg16` container가 healthy. 실제 PostgreSQL 16.12, vector 0.8.1, vector 거리 query 결과 `1` 확인. |
| `T05` | 완료 | Alembic baseline과 ontology v1 migration이 있고, test DB를 `base`로 내린 뒤 `head`로 재생성하는 repository test 통과. |
| `T06` | 완료 | 중복 worker의 단일 선점·단일 성공, retry→dead, idempotency key, stale lock 회수 PostgreSQL test 4건 통과. |
| `T07` | 완료 | FastAPI OpenAPI와 생성 TypeScript client가 연결되어 있고 `pnpm openapi:check` drift 없음. |
| `T08` | **미완료** | `.github/workflows/ci.yml`의 quality/migration job과 로컬 동등 검사는 통과했다. 그러나 Git remote·실제 workflow run·required status check 설정이 없어 merge 차단 조건은 미검증이다. |
| `T09` | 완료 | `backend/.env.example`, typed Settings와 조건부 integration validation이 있다. 필수 DB URL·암호화 key 없이 직접 실행 시 exit 1, 관련 unit 8건 통과, `.env`는 ignore 대상이다. |
| `T10` | 완료 | 내부 UI/API와 exact Slack POST ingress를 분리한 HTTP/Socket Mode 배치도·선택 기준·Nginx 후보가 있다. Nginx 1.27 `nginx -t` 통과. 실제 DNS/TLS/firewall은 후속 운영 검증 대상이다. |
| `T11` | 완료 | 공통 shell/routing/navigation/API health 및 loading/error/empty 상태와 placeholder가 구현됐다. 계약 enum·생성 client를 사용하며 UI/Web smoke 9건과 build 통과. |
| `T12` | 완료 | 현재 후보 장비에서 Ollama 0.30.11, qwen3:8b, BGE-M3를 실제 실행했다. 최종 재검증은 candidate-pass: cold/warm first output 6.90초/0.17초, 전체 8.45초/1.91초, BGE-M3 cold/warm 1.23초/0.20초·1024차원, peak model allocation 47.94%; 기존 30k-token 실측은 346.65초로 120초 기준 미달이며 qwen3:4b가 fallback benchmark 후보로 기록돼 있다. |

## 3. ADR coverage

[canonical coverage manifest](../adr/decision-coverage.json)와 `pnpm test:adr`가 번호·형식·상태·명세 section·local link를 검증한다. ADR-0000은 작성 template이며 결정·상태 집계 대상이 아니다.

| ADR | Canonical 결정 | 상태 | Evidence·구현 정합성 | 재검토 또는 남은 gate |
|---|---|---|---|---|
| [0001](../adr/0001-monorepo-and-runtime-boundaries.md) | Monorepo·runtime 경계 | Accepted | root workspace와 Web/API/Worker 별도 process clean smoke; 충돌 없음 | scheduler·production 격리 구현 또는 release/CI 비용 악화 시 재검토 |
| [0002](../adr/0002-postgresql-system-of-record.md) | PostgreSQL 16 업무 원장 | Accepted | migration이 16+를 강제하고 Compose/startup test 통과; 다른 업무 DB 없음 | 전체 domain table·backup restore는 후속, scale/규제 경계 변화 시 재검토 |
| [0003](../adr/0003-pgvector-in-postgresql.md) | pgvector 공동 배치 | Accepted | extension 0.8.1, `vector(1024)`, startup dimension 검증과 실제 거리 query; 충돌 없음 | QA corpus·HNSW·10만 건 부하는 후속 |
| [0004](../adr/0004-postgresql-job-queue-no-redis.md) | PostgreSQL queue·Redis 제외 | Accepted | `SKIP LOCKED`, retry/dead, heartbeat, idempotency와 동시 worker test; Redis dependency 없음 | queue p95·connection budget 초과 시 재검토 |
| [0005](../adr/0005-transactional-outbox.md) | Transactional outbox | Proposed | 명세만 있고 `outbox_events`, publisher, atomicity test가 없음; 구현이 결정을 선행하지 않음 | transaction boundary, lag, 조직 CDC 도입 시 재검토·승격 |
| [0006](../adr/0006-private-network-and-slack-ingress.md) | 내부망·Slack ingress | Proposed | topology, Nginx 분리, typed settings은 있으나 실제 DNS/TLS/firewall·Slack contract 없음 | 외부 사용자, Slack transport 정책, identity-aware proxy 도입 시 재검토 |
| [0007](../adr/0007-google-drive-attachment-storage.md) | Drive private attachment | Proposed | 설정 bundle validation만 있고 adapter/table/OAuth/quota test 없음 | quota 70/95%, 조직 storage 권한, Drive SLA 미달 시 재검토 |
| [0008](../adr/0008-typed-ontology-ledger.md) | Typed ontology 원장 | Accepted | schema/node/edge/evidence migration과 enum·fixture 정합성; 다른 graph가 원장 역할을 하지 않음 | write-time relation/evidence validator와 실제 Slack flow는 후속 |
| [0009](../adr/0009-lightrag-as-derived-index.md) | LightRAG 보조 index | Proposed | dependency/adapter/citation/fallback API가 아직 없음; PostgreSQL 원장과 충돌 없음 | golden 개선 없음, license/resource 변화, fallback 단독 품질 달성 시 재검토 |
| [0010](../adr/0010-local-ai-with-ollama.md) | Ollama local AI | Proposed | M4 후보 실측은 있으나 운영 서버·실제 golden·concurrency·qwen3:4b가 미검증 | 품질/SLA/memory 실패 또는 데이터 반출·예산 정책 변경 시 재검토 |
| [0011](../adr/0011-contract-first-json-schema.md) | JSON Schema 계약·client drift | Accepted | Schema 5개, fixture 12개, TS enum adapter, OpenAPI/client drift gate; 중복 enum 없음 | domain 상태 전이·backend write binding은 후속 |
| [0012](../adr/0012-immutable-case-versions-and-cycle-snapshots.md) | 불변 version·snapshot | Proposed | `baseVersion` 계약만 있고 domain table/transaction/E2E 없음 | query p95, 감사 요구, 실제 snapshot workflow 충돌 시 재검토 |

중복처럼 보이는 결정도 canonical owner가 다르다. 0002/0003/0004/0005는 각각 원장·vector·job claim·domain event atomicity, 0008/0009는 authoritative ledger·derived index, 0011/0012는 serialization contract·persistent domain versioning을 소유한다. 현재 누락 ADR, 같은 결정을 소유하는 중복 ADR, Accepted 결정과 구현의 상충은 각각 0건이다.

## 4. 최종 Sprint 0 판정

| Gate | 판정 | 근거 |
|---|---|---|
| ADR canonical coverage | 통과 | 주요 결정 12개 ↔ formal ADR 12개 일대일, broken local link 0개 |
| ADR 상태/evidence | 통과 | Accepted 6·Proposed 6; stale evidence 수정, Proposed 누락 evidence·trigger 확인 |
| Accepted ADR 구현 정합성 | 통과 | 충돌 0건; scheduler, ontology validator 같은 미구현은 ADR에 명시된 후속 범위 |
| Phase 0 기술 spike | 통과 | 현 후보 장비에서 DB/vector/AI 실행, 30k 지연 제한과 HTTP/Socket network 대안 기록; 운영 실증과 동일한 뜻은 아님 |
| Phase 0 API·DB·enum 기준선 | 통과 | ADR-0002·0011 Accepted, migration·OpenAPI/client·canonical enum 검증 통과 |
| Sprint 0 delivery gate | **미통과** | 실제 Slack sample `T02`, 실제 merge 차단 `T08`, credential 없는 3 integration mock 종료 조건 미완료 |

로드맵의 Phase 0 종료 조건 두 항목은 위 evidence로 체크했다. 그러나 Sprint 0 종료 체크와 Task가 모두 끝난 것은 아니므로 상위 “Phase 0 — 개발 기반과 계약 고정” 완료 표시는 남겨 뒀다. 따라서 다음 Sprint의 구현 실험은 이 기준선을 사용할 수 있지만 Sprint 0 자체를 완료·종료로 보고하면 안 된다.

## 5. 실행 결과

| 검사 | 결과 |
|---|---|
| `pnpm install --frozen-lockfile` | 통과 |
| `pnpm lint` | ESLint 0 warning, Ruff 통과 |
| `pnpm typecheck` | TypeScript workspace 통과, mypy 12 source file 통과 |
| `pnpm test:unit` | TypeScript 12건, Python 28건 통과 |
| `pnpm test:schema` | strict Draft 2020-12 Schema 5개, synthetic fixture 12개 및 참조 무결성 통과 |
| `pnpm test:adr` | formal ADR 12개, canonical decision 12개, Accepted 6·Proposed 6, local link 통과 |
| `pnpm openapi:check` | OpenAPI와 TypeScript client 최신 상태 |
| `pnpm --filter @tc/web build` | Vite 57 modules, JS 232.83 kB, CSS 12.52 kB, 성공 |
| `pnpm test:migration` | PostgreSQL repository/migration 6건 통과 |
| PostgreSQL smoke | 16.12, pgvector 0.8.1, healthy, vector 거리 query 성공 |
| clean setup + `pnpm dev` | Web/API/Worker 기동, `/api/health` 200, Web title 확인 |
| Nginx config | `nginx:1.27-alpine nginx -t` 통과 |
| startup negative test | 필수 `TC_DATABASE_URL`, `TC_ENCRYPTION_MASTER_KEY` 제거 시 exit 1 |
| Ollama quick benchmark | candidate-pass; qwen3:8b·BGE-M3 설치 digest와 실제 inference 확인 |

통합 중 `deploy/nginx/internal.conf`의 `/health` upstream이 존재하지 않는 API `/health`로 전달되던 오류를 발견해 `/api/health`로 수정했다. 또한 계약 검증이 일회성 명령에만 머물러 있던 문제를 `scripts/validate-contracts.mjs`, `pnpm test:schema`, root `pnpm check`와 CI quality job으로 연결했다.

## 6. 미완료·외부 입력

1. **실제 Slack sample:** 실제 팀의 root/thread, 수정·삭제·늦은 reply·중복 event, PO/QA 기대 판정과 익명화 승인이 없다. 현재 12건은 회귀 harness용 synthetic일 뿐 `T02` 완료 증거가 아니다.
2. **CI merge gate:** Git remote가 없고 GitHub Actions run 및 `quality`·`migration` required status 설정을 확인할 수 없다. workflow code만으로 merge 차단을 주장하지 않는다.
3. **credential과 운영 연결:** Slack App/signing secret/app token, workspace/channel allowlist, Google OAuth client·전용 Drive root, 실제 DNS/TLS/firewall 정보가 없다. secret을 저장소에 넣지 말고 별도 배포 변수로 공급해야 한다.
4. **mock 종료 조건:** 현재 credential 없이 core stack은 기동되고 Slack synthetic fixture는 있지만 Slack transport, Drive adapter, Ollama application adapter의 명시적 mock 구현은 없다. 따라서 “Slack·Drive·AI는 실제 credential 없이 mock으로 개발 가능” 종료 체크는 남겨 뒀다.
5. **운영 장비 확정:** 현재 M4 16 GiB 후보에서는 qwen3:8b/BGE-M3가 동작하지만 30k-token first output 346.65초로 기준에 미달했다. 운영 서버 RAM/GPU, 장시간 발열, concurrency 2, qwen3:4b 품질·지연은 미확정이다.
6. **운영 네트워크:** HTTP ingress 또는 Socket Mode 최종 선택, public exact-path black-box test, TLS와 firewall penetration test는 실제 환경이 필요하다.

## 7. 다음 단계

1. PO/QA가 실제 Slack sample 최소 10건을 승인된 절차로 익명화·이중 검수하고 synthetic dataset과 별도 version으로 등록한다.
2. Git remote와 기본 branch를 만든 뒤 Actions 두 job을 실제 실행하고 `quality`, `migration`을 required status check로 설정해 실패 PR merge가 차단되는지 확인한다.
3. Slack·Drive·Ollama adapter interface와 deterministic mock을 추가해 credential 없는 integration 개발·CI 종료 조건을 충족한다.
4. 운영 네트워크에서 HTTP/Socket Mode를 결정하고 Nginx exact Slack path 외부 차단을 black-box test한다.
5. 확정 운영 서버에서 qwen3:8b·qwen3:4b와 BGE-M3를 8k/16k/near-32k, concurrency 1/2로 재측정하고 실제 golden 품질과 함께 fallback을 확정한다.

## 8. 재현 명령

```bash
# 신규 환경
python3 -m venv .venv
pnpm run setup
pnpm dev

# 정적·단위·계약·ADR·client drift와 Web build
pnpm check
pnpm test:adr
pnpm --filter @tc/web build

# PostgreSQL 16 + pgvector와 disposable migration test
pnpm infra:up
TC_TEST_DATABASE_URL='postgresql+psycopg://tc:<test-password>@127.0.0.1:55432/tc_test' \
  pnpm test:migration
pnpm infra:down

# Nginx 후보 문법
docker run --rm --add-host api:127.0.0.1 --add-host web:127.0.0.1 \
  -v "$PWD/deploy/nginx/internal.conf:/etc/nginx/conf.d/internal.conf:ro" \
  -v "$PWD/deploy/nginx/slack-http.conf:/etc/nginx/conf.d/slack-http.conf:ro" \
  nginx:1.27-alpine nginx -t

# 빠른 후보 장비 AI smoke; model은 미리 local Ollama에 설치해야 한다
python3 scripts/benchmark_ollama.py \
  --base-url http://127.0.0.1:11434 \
  --generation-model qwen3:8b --embedding-model bge-m3 \
  --num-ctx 32768 --num-predict 32 --warm-runs 1 \
  --output /tmp/tc-sprint0-ollama-smoke.json
```

`pnpm test:migration`은 DB를 `base`까지 내리므로 이름에 `test`가 포함된 폐기 가능한 전용 DB에서만 실행한다. `pnpm dev` 종료 후 PostgreSQL volume은 보존되며 `pnpm infra:down`은 container와 network만 내린다. benchmark의 `--context-smoke`는 실제 30k-token 입력으로 수 분 걸릴 수 있으며 지연 threshold를 넘으면 non-zero로 종료한다.
