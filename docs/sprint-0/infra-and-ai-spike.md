# Sprint 0 인프라·로컬 AI spike

| 항목 | 값 |
|---|---|
| 대상 작업 | `P0-S0-T10`, `P0-S0-T12` |
| 점검일 | 2026-08-20 (Asia/Seoul) |
| 후보 장비 | Apple M4 10-core, 16 GiB unified memory, macOS 15.6.1, arm64 |
| 결론 | 일반 생성·embedding 후보는 통과, 32k 입력은 기능상 통과하나 지연 기준은 미달 |

## 1. 결정 요약

- UI와 일반 API는 무로그인이므로 사내망/VPN listener에서만 제공한다. PostgreSQL, Ollama, worker, scheduler는 외부 listener를 갖지 않는다.
- Slack은 운영 환경마다 `HTTP Events API`와 `Socket Mode` 중 하나만 활성화한다. public HTTPS endpoint를 안전하게 운영할 수 있으면 HTTP를 기본으로 하고, public ingress가 없으면 outbound-only Socket Mode를 사용한다.
- 개발 Compose의 기본 실행은 PostgreSQL 16 + pgvector다. 컨테이너 Ollama는 profile로 분리했고, Apple silicon에서는 Metal을 쓰는 host-native Ollama를 권장한다.
- 이 장비의 `qwen3:8b` Q4_K_M은 32,768 context로 로드하면 약 7.05 GiB, BGE-M3와 동시 로드하면 약 7.67 GiB를 Ollama가 보고했다. 따라서 AI worker concurrency와 loaded model 수는 우선 1로 제한한다.
- 짧은 생성은 cold first output 11.26초, warm p50 0.44초였지만 30,000-token 실제 입력은 first output까지 346.65초가 걸렸다. 32k는 batch 작업에서만 허용하고 일반 생성은 retrieval 결과를 8k~16k로 압축한다.

## 2. 배포 토폴로지

### 2.1 HTTP Events API 선택 시

```text
                         Internet
Slack Events API ── HTTPS :443 ──► public Slack ingress
                                    exact POST only:
                                    /integrations/slack/events
                                    모든 다른 path: 404/차단
                                               │ private upstream
                                               ▼
사내 사용자 ── 사내망/VPN ──► internal ingress ──► web
                                  │              └► api
                                  │                  │ outbox/job
                                  │                  ▼
                                  └────────────► worker / scheduler
                                                     │
                                      ┌──────────────┴──────────────┐
                                      ▼                             ▼
                              PostgreSQL 16                    Ollama
                              + pgvector                   qwen3:8b, bge-m3
                              (backend only)                (backend only)
```

`deploy/nginx/internal.conf`는 UI, `/api/`, health를 내부 listener에만 연결한다. `deploy/nginx/slack-http.conf`는 public listener에서 정확히 `POST /integrations/slack/events`만 API로 전달하고 나머지는 404로 닫는다. 운영에서는 이 listener 앞 또는 해당 server block에서 TLS를 종료하고, 방화벽에서 내부 listener의 public 접근을 거부해야 한다.

Slack HTTP handler는 이후 구현 시 서명과 timestamp를 검증한 뒤 빠르게 2xx를 반환하고 원문 처리는 PostgreSQL job으로 넘겨야 한다. public ingress가 API 전체 upstream에 직접 연결되면 안 된다.

### 2.2 Socket Mode 선택 시

```text
Slack ── outbound WSS 연결 ◄── Socket Mode receiver(worker process)
                                      │
                                      ▼
                            HTTP mode와 동일한 event
                            normalization/job pipeline

Public DNS / public port / Slack HTTP ingress: 없음
Internal ingress: UI와 일반 API만 사내망/VPN에 제공
```

| 판단 항목 | HTTP Events API | Socket Mode |
|---|---|---|
| public FQDN·TLS·방화벽 운영 가능 | 선택 | 불필요 |
| 외부 공개 endpoint 금지 환경 | 부적합 | 선택 |
| ingress 관찰·rate limit 중앙화 | 용이 | receiver에서 구성 |
| 연결 특성 | Slack의 inbound HTTPS | 앱의 outbound WebSocket |
| 권장 | 운영 기본 | public ingress가 없을 때 fallback |

두 모드를 동시에 켜지 않는다. 설정 하나(`SLACK_MODE=http|socket`)로 receiver만 바꾸고, 검증 이후의 normalization과 idempotency pipeline은 공유한다. Socket Mode 선택 시 `slack-http.conf` listener 자체를 배포하지 않는다.

### 2.3 노출 허용표

| 대상 | 사내망/VPN | Internet | 비고 |
|---|---:|---:|---|
| UI `/` | 허용 | 차단 | 로그인 없음 |
| 일반 API `/api/*`, `/health` | 허용 | 차단 | 운영 설정 API 포함 |
| Slack events exact path | 필요 없음 | HTTP mode에서 POST만 허용 | Socket Mode에서는 listener 없음 |
| PostgreSQL | backend/관리망만 | 차단 | public port 금지 |
| Ollama | backend/host loopback만 | 차단 | 외부 API 호출 없음 |
| worker/scheduler | listener 없음 | 차단 | outbound Slack/Drive만 필요 시 허용 |

## 3. Docker Compose 개발 구성

파일은 `deploy/compose.dev.yml`이며 image tag를 `pgvector/pgvector:0.8.1-pg16`, `ollama/ollama:0.30.11`로 고정했다. 두 image의 arm64 manifest가 존재하는 것도 확인했다.

### 3.1 기본 인프라

```bash
docker compose -f deploy/compose.dev.yml up -d postgres
docker compose -f deploy/compose.dev.yml ps
```

- PostgreSQL은 host loopback `127.0.0.1:55432`에만 publish한다.
- 최초 volume 초기화 때 `deploy/postgres/init/001-extensions.sql`이 `vector` extension을 생성하고 없으면 실패한다.
- DB명·사용자는 기본 `tc`, password는 로컬 전용 기본값이다. 공유·운영 환경에서는 반드시 외부 secret 주입으로 덮어쓴다.
- 초기화 SQL은 새 volume에서만 자동 실행된다. 기존 volume에는 migration에서 `CREATE EXTENSION IF NOT EXISTS vector`를 적용한다.

### 3.2 Ollama

Apple silicon에서는 host-native Ollama를 실행하고 앱 컨테이너에서 `http://host.docker.internal:11434`를 사용한다. Linux GPU 장비나 메모리가 충분한 Docker VM에서는 다음 profile을 사용할 수 있다.

```bash
docker compose -f deploy/compose.dev.yml --profile container-ai up -d ollama
docker compose -f deploy/compose.dev.yml --profile models run --rm ollama-models
```

컨테이너 Ollama는 loopback `127.0.0.1:11435`에만 publish한다. `models` profile은 `qwen3:8b`, `bge-m3`를 명시적으로 pull하며 credential을 요구하지 않는다. 현재 Docker Desktop VM 메모리는 약 7.65 GiB로 qwen3 32k와 BGE-M3의 동시 로드 보고량 7.67 GiB보다 작으므로, 이 장비에서는 컨테이너 Ollama를 실행하지 않고 host-native 경로를 검증했다.

```bash
# 데이터 volume은 보존하고 테스트 컨테이너와 network만 종료
docker compose -f deploy/compose.dev.yml down
```

## 4. 재현 가능한 benchmark

`scripts/benchmark_ollama.py`는 Python 표준 라이브러리만 사용한다. `/api/version`, `/api/tags`, `/api/show`, `/api/ps`를 확인하고 모델을 pull하지 않은 채 cold/warm generation, batch embedding, 선택적인 near-32k smoke를 JSON으로 출력한다. 외부 inference endpoint, API key, Slack/Drive credential은 사용하지 않는다.

```bash
ollama pull qwen3:8b
ollama pull bge-m3

python3 scripts/benchmark_ollama.py \
  --base-url http://127.0.0.1:11434 \
  --generation-model qwen3:8b \
  --embedding-model bge-m3 \
  --num-ctx 32768 \
  --num-predict 96 \
  --warm-runs 2 \
  --context-smoke \
  --output /tmp/tc-ollama-benchmark.json
```

`--context-smoke`는 ` test`를 30,000번 넣고 Ollama가 보고한 실제 `prompt_eval_count`가 context 설정의 90% 이상인지 검사하므로 약 6분이 걸릴 수 있다. 빠른 smoke에서는 이 flag를 빼고, 릴리스 장비 확정 때 다시 포함한다.

## 5. 현재 장비 실측

### 5.1 버전·모델

| 항목 | 실측 |
|---|---|
| Ollama | 0.30.11, `/api/version`·`/api/tags` 정상 |
| qwen3:8b | 8.2B, Q4_K_M, disk 5,225,388,164 bytes, metadata context 40,960 |
| qwen3 digest | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` |
| bge-m3:latest | 566.70M, F16, disk 1,157,672,605 bytes, context 8,192, dimension 1,024 |
| bge-m3 digest | `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab` |

32k 목표는 생성 모델에 적용된다. BGE-M3의 8,192 context는 embedding chunk 크기 제약이며, DB에는 dimension 1,024와 model digest/version을 함께 고정해야 한다.

### 5.2 지연

짧은 generation은 동일한 한국어 prompt, `think=false`, JSON format, `temperature=0`, seed 42, 96 output token으로 측정했다.

| 검사 | cold | warm |
|---|---:|---:|
| qwen3 first output | 11.26초 | p50 0.44초, p95 0.56초 |
| qwen3 전체 96-token 생성 | 16.69초 | p50 5.85초 |
| qwen3 생성 속도 | 17.71 token/s | p50 17.82 token/s |
| BGE-M3 4문장 batch | 3.66초 | p50 0.20초 |

Cold 수치는 직전 `keep_alive=0` unload 이후이며 모델 load 10.78초를 포함한다. 실제 worker에서는 5분 keep-alive와 concurrency 1로 cold load 빈도를 줄인다.

### 5.3 메모리

| 상태 | Ollama `/api/ps` model allocation | process RSS 관측 |
|---|---:|---:|
| qwen3:8b, `num_ctx=32768` | 7,570,582,404 bytes (7.05 GiB) | 약 7.0~7.1 GiB |
| qwen3 + BGE-M3 동시 | 8,235,243,272 bytes (7.67 GiB, host의 47.9%) | 최대 약 7.93 GiB |

측정 중 macOS의 available memory 추정치는 약 1.2~1.7 GiB까지 내려갔다. 이는 다른 프로세스도 포함한 순간값이지만, 16 GiB 장비에서 API·DB·worker와 AI 동시 운영 시 여유가 작다는 신호다. 운영 초깃값은 `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`로 두고 BGE와 qwen을 직렬화한다.

### 5.4 실제 32k 근접 입력

| 항목 | 결과 |
|---|---:|
| 설정 context | 32,768 |
| 실제 prompt token | 30,000 |
| prompt evaluation | 346.38초 |
| first output / 전체 wall | 346.65초 / 346.65초 |
| 기능 판정 | 통과: truncate·오류 없이 30,000 token 처리 |
| 운영 지연 판정 | 미달: 기준 120초 초과 |

따라서 모델의 32k capability는 확인했지만, full-context 요청을 일반 interactive 생성의 기본값으로 쓰지 않는다. retrieval 단계에서 유효한 evidence를 8k~16k로 압축하고, 16k 초과는 비동기 batch로 분리한다. full 32k가 꼭 필요한 경우 timeout을 최소 420초로 두고 사용자에게 장기 작업으로 표시한다.

### 5.5 PostgreSQL + pgvector

Docker Desktop을 기동한 뒤 실제 container에서 다음을 확인했다.

| 검사 | 결과 |
|---|---|
| Compose parse | 통과 |
| PostgreSQL | 16.12 |
| `vector` extension | 0.8.1 |
| `vector(3)` insert + `<->` nearest query | 통과, 기대 id `1` 반환 |
| healthcheck | `healthy` |

Ollama container image는 arm64 manifest와 Compose healthcheck 구성을 확인했지만, 앞서 적은 Docker VM 메모리 부족 때문에 모델을 중복 다운로드·실행하지 않았다. 실제 AI 수치는 같은 버전의 host-native Ollama에서 측정했다.

## 6. fallback·운영 기준

| 조건 | 조치 |
|---|---|
| Ollama health 실패 또는 모델 없음 | job을 retryable failure로 두고 자동 생성 중단; 수동 케이스 작성은 유지 |
| cold first output > 15초, warm p50 > 5초, 또는 일반 생성 > 120초 | `qwen3:4b` Q4 후보를 같은 script와 golden eval로 검증한 뒤 전환 검토 |
| near-32k wall > 120초 | 먼저 context를 8k~16k로 축소; full context는 420초 비동기 job으로 격리 |
| loaded model allocation > host memory 75%, OOM, 또는 available headroom < 2 GiB가 지속 | concurrency 1·한 모델만 load; 그래도 부족하면 qwen3:4b 또는 별도 AI host 사용 |
| BGE-M3 실패 | typed graph + PostgreSQL full-text 검색으로 degraded 운영; embedding 생성 job 일시 중단 |
| BGE-M3를 다른 embedding으로 교체 | dimension/model version이 다르면 별도 vector column/index와 전체 reindex 후 cutover; 기존 vector와 혼합 금지 |

현재 장비에는 `nomic-embed-text:latest`도 있으나 context 2,048, dimension 768이므로 BGE-M3의 투명 fallback이 아니다. 오직 별도 reindex를 전제로 한 비상 후보로만 둔다. `qwen3:4b`도 아직 이 장비에서 pull·품질 평가하지 않았으므로 확정 fallback이 아니라 다음 benchmark 후보이다.

## 7. 남은 확인 사항

- 운영 후보 서버의 실제 RAM/GPU, Docker/host-native 배치, 장시간 발열·throttling을 확정한다.
- 실제 익명화 golden sample로 qwen3:8b와 qwen3:4b의 JSON 유효성, 근거 보존, 누락·환각률을 비교한다.
- 동시 AI job 2개, API·DB·worker 병행 부하, 8k·16k context별 p50/p95를 Sprint 7에서 재측정한다.
- 운영 네트워크에서 HTTP public ingress 제공 가능 여부를 확정하고, 불가하면 Socket Mode만 배포한다.
- 실제 secret은 별도 secret store/배포 변수로 공급한다. 이 spike에서는 credential이나 유료 API를 사용하지 않았다.
