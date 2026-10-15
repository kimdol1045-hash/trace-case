# ADR-0014: workspace key allocation과 append-only audit를 같은 transaction에 결합

| 항목 | 값 |
|---|---|
| ADR 번호 | 0014 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Architecture / BE / OPS / QA |

## Context

테스트케이스와 이슈는 내부 UUID 외에 `TC-000123`, `ISSUE-000123` 같은 사람이 읽는 key를 생성 즉시 받아야 하며, 설정·폴더·케이스·실행·이슈·integration의 중요한 변경은 전후 값과 함께 감사돼야 한다. key counter만 commit되고 aggregate나 audit가 실패하거나, domain mutation만 commit되고 audit가 빠지면 원장 추적성이 깨진다. 따라서 public key 발급과 append-only audit는 별도 기능 ADR이 아니라 “업무 mutation의 필수 side record가 같은 PostgreSQL transaction에서 함께 성공한다”는 하나의 transaction boundary 결정으로 다룬다.

[ADR-0002](./0002-postgresql-system-of-record.md)는 storage engine만, [ADR-0012](./0012-immutable-case-versions-and-cycle-snapshots.md)는 case version·execution snapshot 불변성만 소유한다. outbox는 비동기 projection 전달 수단이므로 사람이 조회하는 audit ledger를 대신하지 않는다.

## Decision Drivers

- workspace·entity type 안에서 중복 없는 immutable public key
- counter, aggregate insert/update와 audit 사이의 atomicity
- concurrent create와 client retry의 deterministic 결과
- prefix 변경 뒤에도 기존 key를 renumber·reuse하지 않음
- actor identity가 없는 UI mutation도 source·trace로 조사 가능
- 과거 audit row의 application-level 수정·삭제 방지
- secret과 대용량 payload를 audit snapshot에 복제하지 않음

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| workspace counter + domain write + audit INSERT의 단일 transaction | counter와 업무 원장 side record를 함께 commit | 선택 |
| PostgreSQL sequence + application log | sequence key와 file/stdout log 사용 | rollback gap, workspace scope와 durable audit가 약해 제외 |
| `MAX(key)+1` + audit best effort | 최대 key 조회 후 audit를 별도 기록 | concurrency race와 감사 누락 때문에 제외 |
| outbox를 audit로 재사용 | projection event를 감사 화면에 사용 | delivery payload와 human audit 목적이 달라 제외 |
| full event sourcing | 모든 current state를 event fold로 생성 | MVP migration·query 복잡도 때문에 제외 |

## Decision

`workspace_key_counters`는 `(workspace_id, entity_type)` primary key와 `next_value`를 가진다. `TEST_CASE`와 `ISSUE` 생성 transaction은 해당 row를 atomic `UPDATE ... RETURNING` 또는 동등한 row-lock upsert로 증가시키고 ordinal을 얻는다. 현재 workspace prefix와 ordinal을 최소 6자리 zero-padding으로 결합하되, 내부 UUID를 FK와 API canonical resource ID로 유지한다.

counter update, aggregate INSERT, public key unique constraint와 `audit_logs` INSERT가 모두 성공해야 하나의 transaction을 commit한다. 어느 단계든 실패하면 전부 rollback하고 `KEY_ALLOCATION_FAILED`를 기록 가능한 오류로 반환한다. 공개 key는 생성 뒤 immutable하며 soft delete, restore 또는 prefix 설정 변경 뒤에도 renumber하거나 재사용하지 않는다. prefix를 과거 값으로 되돌려도 counter는 reset하지 않는다.

중복 위험이 있는 create는 `Idempotency-Key`와 normalized request hash를 같은 transaction에 저장한다. 같은 key·payload retry는 기존 resource와 기존 public key를 반환하고, 같은 idempotency key의 다른 payload는 `409 IDEMPOTENCY_CONFLICT`를 반환한다. committed 뒤 삭제된 resource나 정상적인 운영 변경으로 생긴 번호 gap은 허용하되 재사용하지 않는다.

모든 중요한 domain mutation은 같은 PostgreSQL transaction에서 `audit_logs`에 새 row를 `INSERT`한다. audit row는 immutable ID, `workspace_id`, `entity_type`, `entity_id`, stable `action`, redacted `before`/`after` JSONB, source type, optional actor label/external actor ID, reason, DB-generated UTC `occurred_at`, trace/correlation과 idempotency key를 가진다. audit INSERT가 실패하면 domain mutation도 rollback한다.

application role과 repository에는 audit `UPDATE` 또는 `DELETE` path를 제공하지 않는다. read API는 cursor pagination의 `AuditEntry`와 `AuditPageResponse`만 공개하고 audit create/update/delete request contract는 두지 않는다. 잘못된 audit를 고쳐야 할 때도 원행을 변경하지 않고 correction action을 새 row로 append한다. credential, token, raw integration secret, binary와 불필요한 개인 데이터는 entity별 allowlist serializer로 redact하며, MVP audit는 database backup과 함께 무기한 보존한다.

## Consequences

### Positive

- resource와 감사 기록 없는 public key, 또는 감사 없는 domain mutation이 commit되지 않는다.
- concurrent create에서 workspace·entity별 key uniqueness를 transaction으로 보장한다.
- prefix 변경이 기존 URL·링크·감사 이력을 바꾸지 않는다.
- 여러 domain의 변경을 같은 audit query와 wire contract로 조사할 수 있다.
- version history, outbox와 audit의 책임 경계가 명확하다.

### Negative

- entity type별 counter row가 concurrent create의 직렬화 지점이 된다.
- 모든 주요 write에 audit INSERT와 JSONB storage 비용이 추가된다.
- committed 뒤 soft-deleted resource로 인한 key gap은 남는다.
- 무로그인 UI 변경은 필수 개인 actor를 알 수 없고 source·optional label만 남는다.
- idempotency record retention과 redaction allowlist 운영이 필요하다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| hot counter row lock contention | entity type별 row를 분리하고 lock wait를 계측하며 병목이 확인된 뒤만 batch allocation을 검토한다. |
| prefix 변경·수동 reset으로 collision | counter를 prefix와 무관하게 증가시키고 final public key unique constraint와 최대 ordinal 정합성 검사를 둔다. |
| retry가 두 resource를 생성 | `Idempotency-Key`와 request hash를 transaction 안에서 unique하게 저장한다. |
| repository 우회로 audit 누락 | mutation-to-action coverage와 transaction integration test를 둔다. |
| application role이 과거 audit 수정 | DB trigger가 audit UPDATE/DELETE를 거부한다. production role grant에서 직접 mutation 권한도 제거됐는지는 배포 전 확인한다. |
| secret이 before/after에 포함 | 현재 workspace/folder serializer는 allowlist snapshot을 쓴다. entity 확장 때 secret-name canary test를 함께 추가한다. |
| audit table 증가로 조회·backup 저하 | `(workspace_id, occurred_at, id)` index, cursor pagination과 size 지표를 둔다. |

## Validation Evidence

- [제품 기획서 3장, 6.1, 9.2, 12.2](../test-management-product-plan.md)은 workspace 내 public key 자동 발급, 중요 변경 audit와 version history 분리를 요구한다.
- [개발 명세 5장, 6.1, 6.2, 8.1, 15장](../test-management-development-spec.md)은 별도 sequence, `audit_logs` before/after, optimistic locking, `Idempotency-Key`와 필수 audit action을 정의한다.
- [workspace schema](../../packages/contracts/sprint-1/workspace.schema.json), [audit schema](../../packages/contracts/sprint-1/audit.schema.json), [error contract](../../packages/contracts/sprint-1/error-contract.schema.json)와 [strict fixture test](../../packages/contracts/src/sprint-1-contracts.test.ts)는 allocated ordinal, key 형식, read-only audit와 retry/conflict payload를 검증한다.
- [Sprint 1 migration](../../backend/migrations/versions/20260820_0003_workspace_folders_audit.py)은 singleton workspace, entity별 counter, indexed `audit_logs`와 `UPDATE`/`DELETE`를 거부하는 append-only trigger를 만든다. [workspace repository](../../backend/src/tc_backend/db/workspaces.py)와 [audit repository](../../backend/src/tc_backend/db/audit.py)는 counter increment와 audit INSERT를 caller transaction에 결합한다.
- [PostgreSQL repository test](../../backend/tests/repository/test_workspace_folders_postgresql.py)는 8 workers의 24개 concurrent allocation, counter·audit caller rollback, 설정 before/after snapshot과 append-only trigger를 검증한다. [API idempotency boundary](../../apps/api/src/tc_api/idempotency.py)와 [API PostgreSQL contract test](../../apps/api/tests/test_sprint1_api_postgresql.py)는 transaction-scoped advisory lock, same-key replay, changed-payload `IDEMPOTENCY_CONFLICT`, UTC audit pagination과 wire metadata redaction을 재검증한다.
- [Sprint 1 통합 보고서](../sprint-1/sprint-1-report.md)는 migration rebuild, 실제 HTTP 설정 update와 before/after audit 조회, generated client drift gate 결과를 기록한다.
- [Sprint 2 case migration](../../backend/migrations/versions/20260820_0004_case_library_ledger.py), [repository](../../backend/tests/repository/test_cases_postgresql.py), [API E2E](../../apps/api/tests/test_sprint2_cases_postgresql.py)는 case UUID/public key unique path, aggregate/version과 audit의 transaction, copy/move/delete/restore audit action을 검증한다. [Sprint 2 품질 게이트](../sprint-2/sprint-2-report.md)의 live HTTP는 `TC-000001` create와 lifecycle resource version을 재확인한다.
- issue aggregate INSERT, production DB role grant, 모든 향후 entity의 secret canary, 운영 retention·규제 evidence는 아직 없다. 그러나 workspace-scoped counter와 human audit side record가 같은 transaction에 commit·rollback되는 핵심 boundary는 Sprint 1과 case 확장 evidence로 재현되므로 이 ADR은 `Accepted`다.

## Revisit Triggers

- counter row lock이나 audit INSERT가 합의한 create/update latency budget을 지속적으로 넘는다.
- offline 또는 multi-region writer가 중앙 PostgreSQL transaction 없이 key를 발급해야 한다.
- 규제 요구가 hash chaining, 전자서명, WORM storage, 외부 SIEM 또는 기한부 파기를 요구한다.
- 사용자 인증 도입으로 actor identity와 privacy retention 정책을 다시 설계해야 한다.
- 제품이 순차 public key 노출을 금지하거나 opaque public ID만 요구한다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0002 PostgreSQL 업무 원장](./0002-postgresql-system-of-record.md)
- [ADR-0005 transactional outbox](./0005-transactional-outbox.md)
- [ADR-0011 계약 우선 JSON Schema](./0011-contract-first-json-schema.md)
- [ADR-0012 불변 case version과 cycle snapshot](./0012-immutable-case-versions-and-cycle-snapshots.md)
- [Sprint 1 API contract fixture](../../packages/contracts/fixtures/sprint-1-api-contracts.json)
