# ADR-0012: 불변 케이스 버전과 사이클 snapshot

| 항목 | 값 |
|---|---|
| ADR 번호 | 0012 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Architecture / BE / FE / QA |

## Context

테스트케이스가 수정된 뒤에도 과거 사이클에서 무엇을 실행했는지 재현해야 한다. 현재 row를 덮어쓰고 사이클이 최신 case를 참조하면 실행 도중 내용이 바뀌며, AI 재생성도 사람이 수정한 내용을 덮을 위험이 있다. Sprint 2의 case version과 `baseVersion` transaction에 이어 Sprint 3에서 cycle pin, 명시적 snapshot refresh/diff와 append-only attempt ledger까지 구현·검증했다.

## Decision Drivers

- 과거 실행·이슈·AI 근거의 재현성
- concurrent edit의 optimistic locking
- 사이클 시작 후 case 내용 안정성
- append-only 실행 시도와 audit
- 사람 수정 case의 자동 overwrite 방지

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| 불변 case version + cycle pin | 수정마다 새 version, cycle item이 version 참조 | 선택 |
| 현재 case row overwrite | 한 row를 직접 수정 | 이력·재현성 때문에 제외 |
| cycle 시 전체 JSON 복사 | cycle item에 case body 중복 저장 | 중복·비교 복잡도로 제외 |
| event sourcing | 모든 change event로 state 재구성 | MVP 복잡도로 보류 |

## Decision

논리 `test_cases` row는 current version pointer와 상태를 가지며 내용 수정은 새 `test_case_versions`와 ordered steps를 append한다. Update request는 `baseVersion`을 보내고 current와 다르면 `409 VERSION_CONFLICT`로 실패한다. `cycle_items`는 추가 시점의 `test_case_version_id`를 고정하며 최신 version 반영은 사용자 명시 동작으로만 새 snapshot을 선택한다. 실행 재시도는 기존 결과를 덮지 않고 `attempt_no + 1`을 append한다.

Case domain과 cycle execution ledger의 migration, service transaction, API/Web E2E가 모두 검증됐다. 시작 뒤 library 최신 version이 바뀌어도 pinned 내용은 유지되고 retry는 기존 actualResult를 수정하지 않으므로 핵심 재현성 선택을 `Accepted`로 고정한다.

## Consequences

### Positive

- 과거 사이클·이슈·generation context를 같은 내용으로 재구성할 수 있다.
- concurrent edit와 AI overwrite를 감지·차단한다.
- version diff, restore, audit가 명확해진다.

### Negative

- version·step row가 계속 증가하고 조회 join이 늘어난다.
- latest와 pinned version을 UI에서 분명히 구분해야 한다.
- 잘못된 version도 수정 대신 새 version으로 정정해야 한다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| current pointer와 version 생성 불일치 | version·steps insert와 pointer update를 한 DB transaction에 묶는다. |
| cycle이 오래된 case를 의도치 않게 실행 | latest 차이를 표시하고 명시적 snapshot update + diff를 제공한다. |
| storage 증가 | immutable 유지 범위 안에서 index·partition·archive를 실측 후 적용한다. |
| AI가 사람 version 덮어쓰기 | `manual_edit_at`/management flag 이후 suggestion만 생성한다. |

## Validation Evidence

- [core API payload Schema](../../packages/contracts/core-api-payloads.schema.json)의 update request가 `baseVersion`과 `changeReason`을 요구한다.
- [개발 명세 6·7장](../test-management-development-spec.md)은 immutable version, current pointer, cycle pin, append-only attempt를 정의한다.
- [Sprint 2 case migration](../../backend/migrations/versions/20260820_0004_case_library_ledger.py)은 current pointer, immutable versions, ordered steps, same-case restore FK와 immutable trigger를 구현한다.
- [case repository PostgreSQL test](../../backend/tests/repository/test_cases_postgresql.py)는 optimistic conflict, original-row UPDATE 거부, full diff, append restore와 concurrent 1-winner update를 검증한다.
- [case API PostgreSQL E2E](../../apps/api/tests/test_sprint2_cases_postgresql.py)는 create→update→`409 VERSION_CONFLICT`→diff→restore wire flow를 재현한다.
- [Sprint 2 품질 게이트](../sprint-2/sprint-2-report.md)는 actual HTTP와 API-backed UI version/history 및 100,000 current-version join 성능을 기록한다.
- [Sprint 3 cycle migration](../../backend/migrations/versions/20260820_0005_cycle_execution_ledger.py)은 pinned version FK, cycle item·attempt unique constraint와 start 후 pin/완료 attempt immutable trigger를 구현한다.
- [cycle repository](../../backend/src/tc_backend/db/cycles.py)는 optimistic cycle mutation, explicit snapshot refresh, start gate, append-only retry와 latest-attempt progress transaction을 소유한다.
- [cycle repository PostgreSQL test](../../backend/tests/repository/test_cycles_postgresql.py)는 명시적 refresh/diff, 시작 후 pin lock, duplicate item/attempt, concurrent attempt 단일 승자와 append-only retry/history를 검증한다.
- [Sprint 3 API PostgreSQL E2E](../../apps/api/tests/test_sprint3_cycles_postgresql.py)는 PLANNED refresh→start pin→library 새 version→diff와 pin 유지→FAIL actualResult→retry history→complete lock→reopen을 실제 wire flow로 검증한다.
- [Sprint 3 공개 계약](../../packages/contracts/sprint-3/cycle-execution-api.schema.json)은 strict Draft 2020-12에서 immutable pin, latest-attempt와 explicit refresh를 normative rule로 고정한다.
- [Sprint 3 품질 게이트](../sprint-3/sprint-3-report.md)는 전체 command, PostgreSQL/FastAPI/generated client와 no-fallback Browser의 pinned execution trace를 기록한다.

## Revisit Triggers

- case version 수와 join 비용이 목표 query p95를 넘는다.
- 규제·감사 요구가 full event sourcing이나 전자서명을 요구한다.
- cycle snapshot 변경 정책이 실제 QA workflow와 반복적으로 충돌한다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0011 계약 우선 JSON Schema](./0011-contract-first-json-schema.md)
- [개발 명세 6장 schema·7장 상태 규칙](../test-management-development-spec.md)
- [제품 기획서](../test-management-product-plan.md)
