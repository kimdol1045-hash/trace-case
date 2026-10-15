# ADR-0010: Ollama 로컬 AI를 사용하고 유료 API 제외

| 항목 | 값 |
|---|---|
| ADR 번호 | 0010 |
| 날짜 | 2026-08-20 |
| 상태 | Superseded |
| 결정자 범위 | Architecture / AI / OPS / QA |

## Context

Slack knowledge extraction, embedding, 테스트케이스 생성에는 LLM이 필요하지만 MVP 비용 목표는 신규 사용량 기반 API 비용 0원이며 Slack 원문·내부 지식을 외부 유료 AI에 전송하지 않는다. 현재 Apple M4 16GiB에서 후보 모델을 실행했지만 운영 서버, 실제 QA golden 품질, 동시성은 확정되지 않았다.

## Decision Drivers

- 외부 유료 inference API와 credential 제외
- 사내 데이터가 로컬 network boundary를 벗어나지 않음
- generation과 embedding model/version 재현성
- Qwen3-8B의 최소 32k context capability
- BGE-M3 dimension 1024와 multilingual embedding
- timeout, fallback, resource 제한의 관측 가능성

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| Ollama + qwen3:8b + bge-m3 | local generation/embedding | 제안 선택 |
| 유료 hosted LLM/embedding API | 외부 사용량 과금 | MVP에서 제외 |
| qwen3:4b fallback | memory·latency 절감 후보 | 후속 benchmark 후보 |
| AI 기능 없음 | 수동 케이스만 제공 | 제품 목표 때문에 제외 |

## Decision

이 결정은 [ADR-0015](./0015-provider-neutral-generation-api.md)로 대체됐다. Ollama·Qwen3-8B 생성기를 관리 도구의 기본 runtime으로 포함하지 않는다. 아래 내용은 Sprint 0 당시 검토·실측 기록으로만 보존한다.

MVP AI adapter는 Ollama HTTP API만 호출하고 generation 후보를 `qwen3:8b`, embedding 후보를 `bge-m3` dimension 1024로 둔다. 모델 tag/digest, prompt, Schema, embedding dimension과 context 설정을 결과에 기록한다. 유료 hosted AI API와 그 credential은 구현·fallback에 넣지 않는다. 16GiB급 장비의 초기 운영은 loaded model·worker concurrency를 1로 제한하고, full 32k는 interactive 기본값이 아니라 장기 비동기 작업으로 취급한다.

운영 장비와 실제 quality gate가 없으므로 `Proposed`다.

## Consequences

### Positive

- 사용량 기반 API 비용과 외부 AI credential이 없다.
- Slack·QA 원문을 local inference boundary 안에 둔다.
- 모델·prompt·Schema version으로 결과 입력을 재구성할 수 있다.

### Negative

- 모델 download·memory·열·추론 latency를 직접 운영한다.
- 작은 local 모델은 hosted frontier model보다 추출·생성 품질이 낮을 수 있다.
- 모델 upgrade마다 full reindex와 golden regression이 필요하다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| 32k 지연이 너무 큼 | retrieval context를 8k~16k로 압축하고 full context는 timeout 420초의 async job으로 격리한다. |
| memory/OOM | 한 모델·한 worker만 load하고 qwen3:4b 또는 별도 AI host를 benchmark한다. |
| malformed/hallucinated output | JSON Schema, evidence validator, repair 1회, 불확실성 표기를 적용한다. |
| embedding model 변경 | model/dimension별 shadow reindex 후 atomically cutover한다. |

## Validation Evidence

- [benchmark script](../../scripts/benchmark_ollama.py)는 credential 없이 health, model metadata, cold/warm generation, embedding, near-32k를 JSON으로 측정한다.
- [Sprint 0 AI spike](../sprint-0/infra-and-ai-spike.md)에서 Ollama 0.30.11, qwen3:8b, bge-m3를 Apple M4 16GiB에서 실행했다.
- 96-token generation warm first-output p50은 0.44초였고 BGE-M3 4문장 warm p50은 0.20초였다. 30,000-token prompt는 346.65초로 기능상 통과했지만 120초 운영 기준은 미달했다.
- 실제 Slack/QA golden 품질, qwen3:4b, concurrent job, 운영 서버의 장시간 안정성은 아직 미검증이다.

## Revisit Triggers

- golden eval이 합의한 evidence·JSON·정확도 기준을 충족하지 못한다.
- 실제 queue 처리 시간이 업무 SLA를 넘거나 운영 장비에 지속적인 memory pressure가 생긴다.
- 데이터 반출 정책과 예산이 바뀌어 다른 inference 경계를 허용한다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0015 provider-neutral Generation API](./0015-provider-neutral-generation-api.md)
- [ADR-0003 pgvector](./0003-pgvector-in-postgresql.md)
- [ADR-0011 계약 우선 JSON Schema](./0011-contract-first-json-schema.md)
- [Sprint 0 인프라·AI spike](../sprint-0/infra-and-ai-spike.md)
