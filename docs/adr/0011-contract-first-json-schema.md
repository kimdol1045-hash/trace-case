# ADR-0011: 계약 우선 JSON Schema를 canonical source로 사용

| 항목 | 값 |
|---|---|
| ADR 번호 | 0011 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Architecture / BE / FE / AI / QA |

## Context

Backend, Web, AI output, Slack parser, fixture가 같은 enum과 필수 필드를 서로 다르게 구현하면 runtime validation과 UI가 어긋난다. 특히 AI 출력과 외부 event는 명시적 additional-property 정책, evidence ID, versioned Schema가 필요하다.

## Decision Drivers

- enum·필수 field·조건부 요구사항의 단일 source
- Python·TypeScript·fixture 간 기계적 검증·code generation
- AI output을 application code 전에 차단
- backward compatibility와 Schema version 추적
- 실제 data 없이도 synthetic contract test 가능

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| JSON Schema Draft 2020-12 | canonical contract와 code generation source | 선택 |
| Backend Pydantic만 source | server 중심 type | FE·fixture 독립 검증 부족으로 제외 |
| OpenAPI만 source | REST endpoint contract 중심 | AI output·fixture 범위 부족으로 제외 |
| 문서 표만 유지 | 사람이 enum·field를 동기화 | drift 감지 불가로 제외 |

## Decision

`packages/contracts`의 JSON Schema Draft 2020-12를 enum, core write payload, Slack parse result, AI generation output, golden fixture의 canonical source로 사용한다. Backend Pydantic, OpenAPI와 TypeScript client는 이 계약과 같은 변경에서 갱신하거나 생성한다. Schema가 표현하지 못하는 연속 step sequence, evidence snapshot membership, 상태 전이는 deterministic domain validator로 검사하고 그 규칙도 contract 문서에 기록한다.

## Consequences

### Positive

- server·client·AI·fixture drift를 CI에서 기계적으로 찾을 수 있다.
- AI의 임의 field와 누락을 저장 전에 차단한다.
- versioned input/output으로 회귀와 재현성이 좋아진다.

### Negative

- Schema reference와 code generation toolchain을 유지해야 한다.
- JSON Schema와 domain validator가 나뉘어 규칙 위치를 이해해야 한다.
- breaking change에는 versioning·migration·client 동시 갱신이 필요하다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Schema와 generated type drift | CI에서 compile·generate 후 diff를 실패 처리한다. |
| `$ref` 또는 dialect 호환 문제 | Draft 2020-12 validator를 고정하고 모든 Schema를 strict compile한다. |
| Schema 밖 규칙 누락 | deterministic validator 목록과 positive/negative fixture를 유지한다. |

## Validation Evidence

- [canonical contracts](../../packages/contracts)는 Sprint 0 enum·core·Slack·AI Schema 5개와 [Sprint 1 API Schema](../../packages/contracts/sprint-1) 5개를 포함한다.
- [Sprint 0 계약 보고서](../sprint-0/contracts-and-samples.md)는 AJV strict compile과 synthetic Slack fixture 12개 validation을 기록한다.
- [Sprint 1 contract fixture test](../../packages/contracts/src/sprint-1-contracts.test.ts)는 workspace, recursive folder tree, mutation, audit, error의 모든 `$defs`를 Draft 2020-12 strict mode로 compile하고 synthetic API case 14개를 검증한다.
- Core request에 `baseVersion`, AI output에 evidence와 closed properties가 정의돼 있다.
- [contract validator](../../scripts/validate-contracts.mjs)가 strict compile, fixture schema와 reference/evidence membership을 검사하고 root `pnpm check`에 포함된다.
- [OpenAPI drift gate](../../scripts/openapi.mjs)가 FastAPI OpenAPI와 생성된 TypeScript client를 byte 비교하며 CI quality job에서 실행된다.
- Schema 밖의 step 연속성·상태 전이와 실제 backend write payload 연결은 후속 domain validator 범위다.

## Revisit Triggers

- 지원 언어/tooling이 JSON Schema 2020-12를 안정적으로 처리하지 못한다.
- event contract가 REST Schema와 독립된 호환성 registry를 필요로 한다.
- Schema와 domain rule 중복이 오류를 반복적으로 만든다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0012 불변 케이스 버전](./0012-immutable-case-versions-and-cycle-snapshots.md)
- [Sprint 0 계약·sample 결과](../sprint-0/contracts-and-samples.md)
- [개발 명세 13장 AI 출력 계약](../test-management-development-spec.md)
