# ADR-0016: 평면 카테고리·상위 일감 라이브러리와 시작 게이트 사이클

| 항목 | 값 |
|---|---|
| ADR 번호 | 0016 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Product / Architecture / FE / BE / QA |

## Context

초기 구현은 Zephyr의 folder tree를 그대로 따라 좌측 카테고리 아래에 도메인 그룹을 두고, release 중심 cycle tree를 제공했다. 실제 팀의 관리 단위는 folder depth나 release가 아니라 Slack에서 유입되는 상위 일감과 그 일감에 속한 테스트케이스다. 그룹이 category와 work item 사이에 끼면 `계정 프로필` 같은 업무 제목이 탐색 폴더로 잘못 보이고, 동일 일감의 케이스 순서 변경·다른 일감 이동·사이클 범위 선택이 모호해진다.

실행 결과는 사이클이 시작되기 전에는 의미가 없으며, WEB·AOS·iOS별 결과와 BLOCKED 사유는 실행 중에만 수정되어야 한다. 테스트 라이브러리의 케이스와 이슈를 직접 연결하면 실행 당시 결함이라는 맥락이 사라지므로 이슈 연결 대상은 사이클에 고정된 테스트케이스(`CYCLE_ITEM`) 또는 그 실행 시도로 한정해야 한다. 연결을 만드는 진입점은 실행 화면의 자동 연결과 이슈 생성·수정 화면의 수동 연결을 모두 허용한다.

ADR-0013은 이미 PostgreSQL adjacency-list folder topology와 lifecycle을 구현·검증했지만, 그 저장 기능 전체를 제품의 탐색 구조로 노출해야 한다는 결정까지 정당화하지는 않는다. 이 ADR은 MVP의 제품-facing aggregate와 cycle scope를 새 canonical 경계로 정한다.

## Decision Drivers

- 팀이 실제로 사용하는 상위 일감 중심의 QA 범위
- category·work item·case의 단일한 소속과 드래그 의미
- 사이클 시작 전 결과 오입력 방지
- WEB·AOS·iOS 결과와 BLOCKED 사유의 명확한 집계
- 이슈를 특정 실행 맥락과 연결하는 추적성
- Jira·Zephyr에 익숙한 사용자가 별도 교육 없이 이해할 수 있는 화면
- 불변 케이스 버전과 cycle snapshot 정책의 유지

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| 평면 category → work item → case | category에는 work item이, work item에는 ordered case가 직접 속함 | 선택 |
| category → domain group → work item → case | 기존 mock과 folder depth를 제품 UI에 그대로 노출 | 업무 제목과 탐색 그룹이 혼동되어 제외 |
| 무제한 folder tree만 사용 | work item도 folder로 표현 | Slack source·설명·담당자·라벨·이력을 가진 aggregate가 필요해 제외 |
| release 중심 cycle | release/build/environment 아래 cycle 구성 | 팀의 QA 범위가 상위 일감 또는 라벨이므로 제외 |
| 결과를 언제나 수정 | PLANNED에서도 PASS·FAIL 입력 가능 | 실행 시작 시점과 집계 기준이 무너져 제외 |
| case와 issue 직접 연결 | library case에 issue 링크 보존 | 어느 cycle·platform 실행에서 발생했는지 모호해져 제외 |

## Decision

### Library aggregate

MVP Test Library의 탐색 구조는 `Category → WorkItem → TestCase` 세 단계로 고정한다.

- 좌측에는 평면 category만 노출하며 category row 전체가 선택 대상이다. 이름은 inline 수정할 수 있다.
- 우측에는 선택 category의 parent work item을 Jira형 카드로 표시하고, 제목을 누르면 설명·담당자·라벨·생성·수정 시각·하위 케이스·필드별 변경이력을 가진 상세 화면으로 이동한다.
- 테스트케이스는 정확히 한 work item에 속한다. 같은 work item 안의 drag는 `sort_order`를 변경하고, 다른 work item으로 drag하면 `work_item_id`와 work item의 category에 맞춘 `category_id`를 한 transaction에서 변경한다.
- case row는 한 번 클릭하면 상세 route로 이동한다. 목록에 삭제 버튼을 상시 노출하지 않고 hover/focus 시 나타나는 `•••`에서 우측으로 펼쳐지는 contextual action drawer를 통해 summary 수정과 soft delete를 제공한다. 별도의 활성 상태와 고정 action dropdown 열은 두지 않는다.
- case 상세는 세부 정보를 step table 위에 두고 case 공통 사전조건과 각 step의 선택적 `사전조건 / 테스트 동작 / 기대 결과`를 편집한다. step 사전조건도 불변 case version snapshot에 포함하며 `테스트 데이터` 열과 library-level issue 연결은 제공하지 않는다.
- label은 Jira형 문자열 집합이다. 정규화된 label 값은 case-insensitive unique이며 IME composition 중 Enter를 commit하지 않고 Enter·blur 이중 commit도 한 번으로 합친다.
- 수정 이력은 “수정됨” 한 줄이 아니라 field, before, after를 보존·표시한다.

ADR-0013의 active root folder는 persistent category로 호환 투영할 수 있다. child folder·alias·subtree API와 그 benchmark evidence는 당장 삭제하지 않지만 Test Library의 탐색·소속·cycle scope 계약에는 사용하지 않는다. 후속 migration에서 별도 `test_categories`로 분리할지는 실제 backend aggregate 구현 시 결정한다.

### Cycle aggregate and start gate

사이클 범위는 `WORK_ITEM` 하나 또는 `LABEL` 하나 이상의 union이다.

- 실행 대상 선택은 새 사이클 생성 화면에서만 제공한다. `WORK_ITEM`은 한 번에 비교 가능한 세로 목록, `LABEL`은 복수 선택할 수 있고 선택한 라벨별 case 집합의 union을 구성한다. 여러 라벨에 동시에 속한 case는 한 번만 포함한다. 생성 후 상세 화면에는 확정 범위만 표시하고 scope selector를 반복 노출하지 않는다. 서버는 해당 범위의 중복 없는 현재 case version을 `cycle_items`에 snapshot한다. 기존 단일 `label` 요청·저장값은 one-element union으로 계속 해석한다.
- 사이클에는 release, build, environment, end date를 두지 않는다. 이름, 시작일, 설명, status, scope만 관리한다.
- 사이클명·시작일·설명은 `PLANNED`과 `IN_PROGRESS`에서 resource version과 audit을 남기며 수정할 수 있다. 시작 뒤에도 실행 식별용 문구를 정정할 수 있어야 하기 때문이다. `scope`와 pinned case snapshot은 시작 뒤 계속 잠그고, `COMPLETED`의 기본 정보는 다시 열기 전까지 잠근다.
- 사이클 상세의 명시적 `사이클 시작` 동작만 `PLANNED → IN_PROGRESS`로 전환하고 scope를 잠근다. `PLANNED` 실행 화면은 조회만 허용하며 결과·비고·이슈 생성을 비활성화하고, API도 `CYCLE_EXECUTION_NOT_STARTED`로 실행 mutation을 거부한다.
- `IN_PROGRESS`에서 WEB·AOS·iOS별 모든 pinned step에 `PASS | FAIL | BLOCKED` 결과와 비고를 기록한다. 일부만 기록된 attempt는 WIP이며, 전부 기록되면 `FAIL > BLOCKED > PASS` 우선순위로 platform 전체 결과를 도출한다. BLOCKED step은 API에서도 빈 비고를 거부한다. terminal attempt와 그 step 결과는 immutable이다.
- `IN_PROGRESS → COMPLETED` 후 결과를 잠그며, 다시 열기는 새 attempt 또는 명시적 reopen audit로 기록한다.
- 사이클 첫 route는 미진행·진행 중·완료 카드 필터와 검색 가능한 공통 목록이다. 상세에서는 기본 정보·고정 case snapshot을 보여주고, 실제 실행은 `queue / case script / result rail`의 별도 route에서 수행한다. 비고는 WEB·AOS·iOS 결과·issue link와 함께 가장 오른쪽 result rail에 둔다.
- 내부 issue는 library case 자체가 아니라 cycle item 또는 platform attempt에 연결한다. 실행 화면에서 만든 이슈는 현재 attempt에 자동 연결하고, 일반 이슈 생성·수정 화면에서는 `사이클 → pinned 테스트케이스`를 선택해 연결·해제할 수 있다. 한 이슈의 사이클 근거는 하나의 cycle로 제한하되 같은 cycle의 여러 테스트케이스는 연결할 수 있다. 링크는 cycle item·case version과, attempt가 있는 경우 platform 실행 맥락을 보존하며 issue 화면은 raw ID 대신 cycle명·case key·summary를 표시한다.
- 이슈 첫 route도 사이클과 같은 상태 카드·검색·필터·table landing을 사용하고 Jira형 상세는 별도 route로 연다.
- Overview와 Reports는 PLANNED 결과를 성공률에서 제외하고, cycle status 세 구간, 사람이 읽는 상위일감 `key + summary`, work item/label scope, 실제 platform 결과, BLOCKED 사유, 열린 issue를 기준으로 집계한다. cycle KPI는 work item ID 필터를 받고 필터 echo를 돌려준다. Overview에는 즉시 실행 CTA를 두지 않고 탐색·위험 파악에 집중한다.

첨부의 binary 원장, checksum과 soft-delete 정책은 이 변경의 범위 밖이며 신규 R2 첨부는 ADR-0017, legacy Drive 호환은 ADR-0007을 따른다. Step 결과는 첨부 owner를 새로 만들지 않고 기존 `EXECUTION_ATTEMPT` owner를 계속 사용한다.

사이클 snapshot과 append-only attempt의 불변성은 ADR-0012가 계속 소유한다. 이 ADR은 snapshot 대상을 고르는 scope와 입력 가능 시점, execution metadata의 제품 경계를 소유한다.

Persistent category/work item/case schema와 API-backed library transaction은 Sprint 2에서 구현됐다. Sprint 3에서 WORK_ITEM/LABEL scope option·preview, PLANNED snapshot, start gate, WEB/AOS/IOS attempt와 latest-attempt progress가 repository/API/generated client/Web까지 연결됐으므로 핵심 aggregate 선택을 `Accepted`로 고정한다. Issue link persistence는 이 선택을 되돌리는 조건이 아니라 Sprint 4의 별도 aggregate 범위로 남는다.

## Consequences

### Positive

- 업무 제목과 탐색 folder가 분리되어 상위 일감의 책임·범위가 명확하다.
- drag가 동일 일감 reorder와 타 일감 move라는 두 가지 의미로만 해석된다.
- 사이클 범위를 정한 후에만 실행하므로 시작 전 결과가 집계되는 오류를 막는다.
- 사용자가 명시적으로 시작한 시점이 실행 경계와 scope 잠금 시점으로 기록된다.
- step별 사전조건·결과·비고와 도출된 platform 결과를 같은 pinned version 맥락에서 추적할 수 있다.
- issue가 발생 cycle·case version·platform 결과와 함께 추적된다.
- Overview·Reports의 분모가 “시작된 실행”으로 일관된다.

### Negative

- 기존 child folder UI·API·성능 구현 일부가 MVP 사용자 흐름에서는 사용되지 않는다.
- work item aggregate와 category/work item/case transaction을 새로 구현해야 한다.
- label 범위는 label rename 시 의미가 달라질 수 있어 snapshot에 resolved case version 목록을 반드시 남겨야 한다.
- 복수 label union은 case membership 중복 제거와 순서가 고정된 canonical label 배열을 저장해야 한다.
- cycle reopen과 재실행을 한 화면에서 단순하게 보이면서도 append-only 원장을 유지해야 한다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| child folder 데이터가 새 UI에서 고아처럼 보임 | migration 전 read-only 호환 report를 제공하고 root category/work item으로 명시 mapping한다. |
| cross-work-item drag 중 category만 바뀜 | work item ownership·category update·sort allocation을 한 transaction과 audit row로 묶는다. |
| label IME가 부분 문자열을 중복 등록 | composition guard, normalized Set, last-commit guard와 browser regression을 둔다. |
| PLANNED 결과가 report에 포함 | query가 `cycle.status != PLANNED`와 latest attempt 조건을 공통 view로 사용한다. |
| 시작 후 scope가 바뀜 | scope mutation을 `PLANNED`에만 허용하고 start transaction에서 cycle items를 lock한다. |
| 진행 중 이름 수정이 scope 잠금에 함께 차단됨 | 기본 정보 mutation과 scope mutation을 분리하고 DB trigger·repository·API 회귀 테스트로 동일 정책을 강제한다. |
| BLOCKED 이유가 비어 있음 | UI placeholder와 API validation을 함께 적용한다. |
| 시작 전 결과가 저장됨 | Web control을 비활성화하고 repository와 API가 `IN_PROGRESS` 상태를 함께 검증한다. |
| step 결과와 전체 결과 불일치 | 모든 pinned step이 채워질 때 서버가 우선순위로 전체 결과를 도출하고 직접 완료 요청도 step 원장과 대조한다. |

## Validation Evidence

- [Test Library mock](../../apps/web/src/folder-library-page.tsx)은 flat category 선택, work item 상세 링크, 동일 work item reorder·타 work item 이동, list summary edit·delete를 구현한다.
- [Work Item detail](../../apps/web/src/work-item-detail-page.tsx)은 Jira형 parent detail, child case link, category·label edit와 field-level history를 구현한다.
- [Test Case detail](../../apps/web/src/test-case-detail-page.tsx)은 back button, parent link, detail-before-steps, precondition/action/expected-result 편집을 제공하며 test data·library issue link를 제거했다.
- [Cycle workspace](../../apps/web/src/cycle-workspace-page.tsx)은 검색 가능한 status landing, 생성 전용 scope list, 확정 범위 상세, 별도 Zephyr형 execution player, PLANNED input lock, WEB·AOS·iOS, 우측 note와 cycle issue link를 구현한다.
- [Issue workspace](../../apps/web/src/issue-workspace-page.tsx)은 공통 list landing, 생성 화면과 같은 Jira형 수정 레이아웃, 생성 시 initial cycle-item links, 수정 시 cycle-item 연결·해제와 사람이 읽는 cycle/case 문맥을 구현한다.
- [화면 네비게이션 정책](../ui-navigation-policy.md)은 목록·생성·상세·실행·삭제별 Stack/Replace 규칙을 정의하고 [Web navigation policy](../../apps/web/src/navigation-policy.ts)가 이를 코드로 고정한다.
- [UI unit regression](../../apps/web/src/workspace-ui.test.tsx)과 [route regression](../../apps/web/src/app.test.tsx) 28건이 통과한다. 동일 work item reorder, cross-work-item rejection, ownership move audit와 route 분리가 포함된다.
- 2026-08-20 Browser smoke에서 case row 단일 클릭 상세 이동, 숨김 action drawer, cycle list→create의 WORK-466 선택→확정 scope detail→start→별도 execution player, issue list→detail, Dashboard 실행 CTA 제거와 실제 결과 기반 Report KPI를 확인했다.
- [Sprint 2 case migration](../../backend/migrations/versions/20260820_0004_case_library_ledger.py)과 [repository PostgreSQL test](../../backend/tests/repository/test_cases_postgresql.py)는 root category projection, persistent work-item ownership, same/cross-work-item sort·move, copy와 lifecycle audit transaction을 검증한다.
- [Sprint 2 품질 게이트](../sprint-2/sprint-2-report.md)는 generated client UI를 actual PostgreSQL/FastAPI에 연결해 no-fallback list, single-click detail, update와 field history를 검증했고 list/detail hydration race를 regression으로 고정했다.
- [Sprint 3 cycle migration](../../backend/migrations/versions/20260820_0005_cycle_execution_ledger.py)과 [repository PostgreSQL test](../../backend/tests/repository/test_cycles_postgresql.py)는 WORK_ITEM/LABEL resolved snapshot, start scope lock, platform attempt validation, duplicate/concurrency와 다섯 progress 차원을 검증한다.
- [cycle repository](../../backend/src/tc_backend/db/cycles.py)와 [FastAPI cycle router](../../apps/api/src/tc_api/routers/cycles.py)는 scope resolution, optimistic mutation, start/complete/reopen, attempt와 progress를 실제 transaction 및 typed wire error에 연결한다.
- [Sprint 3 API PostgreSQL E2E](../../apps/api/tests/test_sprint3_cycles_postgresql.py)는 scope option/preview→PLANNED refresh→start→attempt/retry→complete/reopen과 typed errors를 실제 transaction에 연결한다.
- [Step execution migration](../../backend/migrations/versions/20260821_0009_step_execution_and_multi_label_scope.py)은 기존 단일 scope를 배열로 backfill하고 step 사전조건·step 결과 FK/check/immutability guard를 추가한다.
- [Cycle repository PostgreSQL regression](../../backend/tests/repository/test_cycles_postgresql.py)은 복수 label union 중복 제거, 시작 전 결과 거부, 명시적 시작, 부분 WIP, 전 step 결과의 FAIL 우선 도출과 terminal immutability를 검증한다.
- [Sprint 3 API PostgreSQL E2E](../../apps/api/tests/test_sprint3_cycles_postgresql.py)는 복수 label preview/create, step 사전조건 조회, 시작 전 결과 거부와 명시적 start 흐름을 검증한다.
- [Active cycle detail migration](../../backend/migrations/versions/20260824_0010_mutable_active_cycle_details.py)과 Sprint 3 API PostgreSQL E2E는 진행 중 이름·시작일·설명 수정, pinned scope 유지, 완료 후 잠금을 검증한다.
- [Sprint 4 API PostgreSQL E2E](../../apps/api/tests/test_sprint4_api_postgresql.py)는 Overview/Reports의 상위일감 key·summary와 workItemIds 필터 echo를 검증한다.
- [Cycle Web generated-client UI](../../apps/web/src/cycle-workspace-page.tsx)는 API-backed list/create/detail/player, PLANNED input lock, start 후 scope selector 제거, latest diff, attempt history와 five-dimension progress를 제공한다.
- [Sprint 3 품질 게이트](../sprint-3/sprint-3-report.md)는 actual PostgreSQL/FastAPI/generated client/Web no-fallback trace와 제품 인수 조건 4~7을 기록한다.
- cycle execution의 issue selector는 `MOCK · Sprint 4 persistence 예정`으로 명시되어 있다. issue/link/comment 원장과 attachment는 ADR의 cycle start gate와 분리된 Sprint 4 열린 범위다.

## Revisit Triggers

- 실제 QA 팀이 category보다 더 깊은 탐색 계층을 반복적으로 요구한다.
- 하나의 case가 여러 work item에 동시에 속해야 한다.
- label rename·merge가 snapshot 추적성을 훼손한다.
- release/build/environment가 실행 정책 또는 자동화 adapter의 필수 축이 된다.
- physical pointer E2E나 10만 case pagination에서 reorder/move UX가 목표를 충족하지 못한다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0012 불변 케이스 버전과 사이클 snapshot](./0012-immutable-case-versions-and-cycle-snapshots.md)
- [ADR-0013 PostgreSQL adjacency-list 폴더 계층](./0013-postgresql-adjacency-list-folder-hierarchy.md)
- [개발 명세](../test-management-development-spec.md)
- [개발 로드맵](../test-management-roadmap.md)
