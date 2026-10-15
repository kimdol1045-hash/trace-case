# ADR-0000: 제목

| 항목 | 값 |
|---|---|
| ADR 번호 | 0000 |
| 날짜 | YYYY-MM-DD |
| 상태 | Proposed |
| 결정자 범위 | 예: Architecture / BE / FE / OPS / AI / QA |

## Context

문제, 현재 제약, 이 결정을 지금 기록해야 하는 이유를 적는다.

## Decision Drivers

- 선택을 좌우하는 품질 속성·제품 제약을 적는다.
- 비용, 보안, 운영 복잡도, 데이터 무결성, 성능을 필요에 따라 포함한다.

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| A | 대안 A | 선택/제외 |
| B | 대안 B | 선택/제외 |

## Decision

선택한 방안과 적용 경계를 검증 가능한 문장으로 적는다. `Proposed` 상태라면 확정 전 필요한 evidence도 함께 적는다.

## Consequences

### Positive

- 기대되는 이점을 적는다.

### Negative

- 비용, 제약, 포기한 능력을 적는다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| 위험 | 완화책과 검증 방법 |

## Validation Evidence

- code, migration, test, benchmark, 운영 확인의 링크와 결과를 적는다.
- 아직 구현·실측·credential이 없으면 명시한다.

## Revisit Triggers

- 결정을 재검토하게 만드는 구체적인 규모·장애·비용·보안 조건을 적는다.

## Related Documents

- [ADR 원장](./README.md)
- 관련 개발 명세, 구현, 검증 문서를 추가한다.
