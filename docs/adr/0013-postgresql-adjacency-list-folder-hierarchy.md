# ADR-0013: PostgreSQL adjacency list로 폴더 계층과 lifecycle 관리

| 항목 | 값 |
|---|---|
| ADR 번호 | 0013 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Architecture / BE / FE / AI / QA |

## Context

제품은 제한 없는 깊이의 디렉토리, 하위 tree 이동, breadcrumb, 직접·하위 전체 케이스 수, 메뉴 alias, soft delete/restore와 `AI 생성함` fallback을 하나의 라이브러리로 제공해야 한다. 이 규칙들은 모두 “폴더가 어떤 parent chain과 lifecycle을 가지는가”라는 같은 topology invariant를 바꾸며, 하나만 따로 rollback하면 tree 해석과 mutation이 불일치한다. [ADR-0002](./0002-postgresql-system-of-record.md)는 PostgreSQL 원장 선택만 소유하므로 폴더 표현·조회·변경 규칙은 이 ADR에서 함께 결정한다.

2026-08-20 후속 제품 결정인 [ADR-0016](./0016-flat-category-work-items-and-start-gated-cycles.md)은 이 storage topology의 구현·성능 evidence를 폐기하지 않지만, child folder를 MVP Test Library의 사용자 탐색·case ownership·cycle scope 계약에서 제외한다. 따라서 이 ADR은 기존 folder 저장·lifecycle의 canonical owner이고, 현재 제품-facing `Category → WorkItem → TestCase` 경계는 ADR-0016이 소유한다.

## Decision Drivers

- 무제한 깊이와 일반 생성·rename의 단순성
- recursive subtree move에서 cycle과 동시성 race 방지
- active root·sibling 이름 uniqueness의 database enforcement
- tree, breadcrumb, 직접·subtree case count의 일관된 query 의미
- alias vocabulary가 실제 parent topology를 오염시키지 않음
- soft delete 후 subtree 전체를 복구할 수 있는 원자성
- AI fallback이 의존하는 system folder의 안정성
- 10만 케이스에서 tree와 paginated case list를 분리하는 성능 경계

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| PostgreSQL adjacency list와 통합 folder invariant | self-reference, recursive CTE, topology transaction, lifecycle batch | 선택 |
| Materialized path | 각 folder에 ancestor path 저장 | 이동·복원 시 subtree path 재작성 때문에 제외 |
| PostgreSQL `ltree` | extension path와 ancestor operator 사용 | Sprint 1에 extension·migration 복잡도를 추가할 근거가 부족해 제외 |
| Closure table | 모든 ancestor-descendant pair 저장 | write amplification과 복구 복잡도 때문에 제외 |
| alias를 alternate parent로 표현 | 하나의 folder에 여러 tree path 허용 | breadcrumb·cycle·uniqueness가 모호해 제외 |

## Decision

`folders`는 PostgreSQL adjacency list로 표현한다. 실제 folder row는 nullable `parent_id` self-reference, `name`, deterministic normalization 결과인 `normalized_name`, `sort_order`, optimistic-lock `version`, nullable `deleted_at`, nullable `deletion_batch_id`와 nullable `system_kind`를 가진다. 합성 root 행은 두지 않고 `parent_id IS NULL`인 active row를 root로 취급한다.

active child 이름은 `(parent_id, normalized_name) WHERE deleted_at IS NULL AND parent_id IS NOT NULL`, active root 이름은 `(normalized_name) WHERE deleted_at IS NULL AND parent_id IS NULL` partial unique index로 보호한다. PostgreSQL의 nullable unique 의미에 기대지 않는다. normalization은 Unicode NFKC, trim, whitespace fold, casefold 순서로 하나의 domain 함수에서 수행한다.

tree topology와 ancestor breadcrumb는 `WITH RECURSIVE`로 계산한다. count-ready query는 active folder 집합의 direct case count를 별도로 집계하고 application에서 descendant topology를 fold해 subtree count를 계산한다. `directTestCaseCount`는 해당 active folder의 non-deleted case 수, `subtreeTestCaseCount`는 자신과 모든 active descendant folder의 non-deleted case 수다. case의 `ACTIVE`·`DEPRECATED` 업무 상태는 모두 포함하고 deleted folder·soft-deleted case만 제외한다. tree endpoint는 hierarchy와 count만 반환하고 10만 case 목록은 별도 paginated query로 조회한다. count를 저장된 counter로 중복 유지하지 않으며 query plan 실측 전에는 cache나 closure projection을 추가하지 않는다.

`MOVE_SUBTREE`를 포함한 create·rename·move·delete·restore topology mutation은 한 PostgreSQL transaction에서 singleton `workspace_settings` row를 `SELECT ... FOR UPDATE`하여 직렬화한다. move는 `WITH RECURSIVE`로 source subtree를 고정하고 target이 source 자신 또는 descendant이면 `409 FOLDER_CYCLE`을 반환한다. 검증 후 source root의 `parent_id`, `sort_order`, `version`만 update하며 descendant는 parent chain을 따라 함께 이동한다. 요청의 `baseVersion`, active target과 sibling uniqueness를 같은 lock 아래 재검증한다.

`folder_aliases`는 topology node/edge가 아닌 exact lookup metadata다. workspace-global `normalized_alias` 하나는 concrete active `folder_` ID 하나만 참조하며 alias ID는 parent, move target, breadcrumb 또는 case `folderId`가 될 수 없다. 해석 순서는 normalized full path exact match, alias exact match, semantic candidate 순이다. deleted target의 alias reservation은 유지하되 resolution에서 제외하고, 불일치하면 새 folder를 자동 생성하지 않고 protected system folder로 fallback한다.

`AI 생성함`은 `system_kind = AI_GENERATED_INBOX`인 workspace당 한 개의 active root folder로 bootstrap한다. 일반 folder API로 create·rename·move·`SOFT_DELETE_SUBTREE`할 수 없고 위반은 `409 SYSTEM_FOLDER_PROTECTED`로 거부한다. bootstrap과 운영 복구만 idempotent system path에서 이 row를 생성·복구할 수 있다.

일반 folder의 `SOFT_DELETE_SUBTREE`는 non-deleted test case가 subtree에 하나라도 있으면 `409 FOLDER_NOT_EMPTY`로 전부 거부한다. 비어 있으면 recursive subtree 전체에 같은 `deleted_at`과 `deletion_batch_id`를 기록한다. `RESTORE_SUBTREE`는 그 batch 전체만 원자 복원하며 부분 restore를 허용하지 않는다. 원래 parent가 active하지 않으면 명시된 active target parent가 필요하고, parent·sibling 충돌은 `FOLDER_PARENT_DELETED` 또는 `FOLDER_RESTORE_CONFLICT`로 전체 transaction을 rollback한다.

## Consequences

### Positive

- folder representation, traversal, move, alias, lifecycle과 system protection이 하나의 invariant로 검토된다.
- 일반 move는 root row만 바꾸고 descendant path materialization을 재작성하지 않는다.
- active 이름 충돌과 concurrent cycle 검사를 PostgreSQL constraint·lock으로 방어한다.
- tree count와 case pagination의 성능 책임이 분리된다.
- deletion batch로 subtree의 all-or-nothing restore가 가능하다.

### Negative

- 단일 workspace의 topology write는 하나의 lock 지점에서 직렬화된다.
- deep tree와 subtree count는 recursive CTE 비용을 가진다.
- 빈 subtree delete도 descendant folder row를 모두 update한다.
- non-deleted case가 있는 subtree는 한 operation으로 보관할 수 없고 먼저 case를 이동해야 한다.
- system folder와 alias normalization이 application·migration 양쪽의 추가 invariant가 된다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| 비정상 cycle이 recursive query를 확장 | 모든 topology write를 repository transaction으로 제한하고 recursive query에 visited-path 방어를 둔다. |
| topology lock 대기가 latency를 높임 | lock wait와 mutation latency를 계측하고 실제 병목 전에는 별도 lock 체계를 추가하지 않는다. |
| root sibling unique가 nullable parent 때문에 빠짐 | root/child partial unique index를 분리하고 migration test로 확인한다. |
| alias normalization drift | 한 domain normalizer와 positive/negative fixture를 공유한다. |
| deleted target alias로 AI가 저장 | alias query에 active target join을 강제하고 fallback 결과를 audit한다. |
| system folder가 운영 SQL로 손상 | DB shape constraint와 repository guard는 적용했다. production application role의 직접 topology DML 제한과 startup invariant check는 배포 전 확인한다. |
| subtree count가 10만 case에서 느림 | `EXPLAIN ANALYZE`와 p95를 측정한 뒤 index, cache 또는 closure projection을 별도 결정으로 검토한다. |

## Validation Evidence

- [제품 기획서 6.2, 7.6, 9장과 MVP 인수 조건](../test-management-product-plan.md)은 무제한 depth, subtree 이동, alias, soft delete/restore, `AI 생성함`, self-reference와 3-depth 회귀를 요구한다.
- [개발 명세 4장, 6.2, 6.3, 8.2, 13.3, 18.2](../test-management-development-spec.md)는 adjacency list, sibling unique, move cycle, count query, alias mapping과 restore 검증 범위를 정의한다.
- [folder tree schema](../../packages/contracts/sprint-1/folder-tree.schema.json), [folder mutation schema](../../packages/contracts/sprint-1/folder-mutation.schema.json), [error contract](../../packages/contracts/sprint-1/error-contract.schema.json)와 [strict fixture test](../../packages/contracts/src/sprint-1-contracts.test.ts)는 recursive payload, folder-only target, subtree operation과 주요 conflict payload를 검증한다.
- [Sprint 1 migration](../../backend/migrations/versions/20260820_0003_workspace_folders_audit.py)은 self-reference, root·child active partial unique index, parent/sort/deleted index, alias constraint와 workspace당 system folder shape·uniqueness를 생성한다. [folder repository](../../backend/src/tc_backend/db/folders.py)는 recursive topology, sibling normalization, cycle/version lock, delete batch, restore, alias suspension과 system folder 보호를 이 transaction 규칙으로 구현한다.
- [PostgreSQL repository test](../../backend/tests/repository/test_workspace_folders_postgresql.py)는 3-depth tree와 direct/subtree count, normalized sibling 충돌, 자기 하위 이동, 상호 의존 concurrent move, stale version, delete/restore, deleted alias resolution 중단과 seed idempotency를 검증한다. [API PostgreSQL contract test](../../apps/api/tests/test_sprint1_api_postgresql.py)는 같은 invariant와 `409`/`422` error contract를 실제 router/session 경계에서 재검증한다.
- [Sprint 1 통합 보고서](../sprint-1/sprint-1-report.md)는 5,551 folders와 100,000 synthetic cases에서 count 정확도와 warm-cache p95 84.53 ms를 기록한다. 이는 p95 1초 목표를 통과하지만 production concurrent load·cold cache를 대신하지 않는다.
- 영속 `test_cases` aggregate와 paginated case list는 Sprint 2에서 구현됐고 [Sprint 2 품질 게이트](../sprint-2/sprint-2-report.md)는 20 root category, 1,000 work item, 100,000 case에서 tree p95 17.13 ms와 일반 case query p95 89.60~251.35 ms를 기록했다. full-path·semantic·fallback까지 잇는 AI resolver, production application role의 직접 DML 제한, startup invariant check와 production concurrent/cold-cache evidence는 아직 없지만 adjacency-list topology와 lifecycle의 핵심 rollback 축은 migration·repository·API evidence로 재현되므로 이 ADR은 `Accepted`다.

## Revisit Triggers

- 실측 최대 depth·row count에서 tree/count query가 p95 1초 목표를 지속적으로 넘는다.
- folder topology write lock 대기가 합의한 latency 또는 throughput budget을 넘는다.
- non-deleted case를 포함한 subtree를 한 번에 archive해야 하는 제품 요구가 생긴다.
- 복수 workspace나 locale별 alias scope가 제품 범위에 들어온다.
- system folder가 하나 이상의 managed topology 또는 별도 lifecycle을 요구한다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0002 PostgreSQL 업무 원장](./0002-postgresql-system-of-record.md)
- [ADR-0011 계약 우선 JSON Schema](./0011-contract-first-json-schema.md)
- [ADR-0014 key allocation과 append-only audit transaction](./0014-workspace-key-allocation-and-append-only-audit.md)
- [ADR-0016 평면 카테고리·상위 일감 라이브러리와 시작 게이트 사이클](./0016-flat-category-work-items-and-start-gated-cycles.md)
- [Sprint 1 API contract fixture](../../packages/contracts/fixtures/sprint-1-api-contracts.json)
