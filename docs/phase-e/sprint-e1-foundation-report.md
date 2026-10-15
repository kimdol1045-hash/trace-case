# Phase E Sprint E1 기반 구현 보고서

| 항목 | 내용 |
|---|---|
| 실행일 | 2026-08-27 |
| Orca Run | `run_f151b2c75d05` (`T02`~`T06`), `run_29729e1bd491` (`T07`~`T08`), `run_8b1565a1bff5` (`T09`), `run_1da5a111c42b` (`T10`·통합 gate), `task_bc22ad3ed019` (`T11` 통합 gate), `run_9c09c45402b9` (`T12` 구현·통합 gate) |
| 완료 범위 | `PE-SE1-T02`~`PE-SE1-T12` (`T01` 제외) |
| 미완료 gate | `PE-SE1-T01` 실제 endpoint·인증·요금·quota·context 한도, 실제 signed Slack과 production provider의 외부 live gate |

## 구현 결과

### Versioned generation 계약

- 입력과 출력은 JSON Schema Draft 2020-12의 exact pair `1.0.0 → 1.0.0`을 사용한다.
- 입력은 Slack 일감, ACTIVE 지식, 기존 케이스, FAIL/BLOCKED 실행, 미종결 이슈,
  사용자가 명시적으로 준 자료, category 후보와 생성 정책을 분리한다.
- provider는 case key, work item, generation job, source metadata와 timestamp를 만들 수
  없으며 알 수 없는 evidence/category 참조는 trusted adapter에서 거부된다.
- 세부 계약과 외부 미확정 항목은 [Generation 계약](./generation-contract.md)에 기록했다.

### Grounded context와 불변 snapshot

- `generation_jobs`가 Slack source/generation version과 provider·model·prompt·schema·ontology
  provenance를 소유한다.
- `generation_contexts`는 사용한 node 또는 edge, knowledge evidence, source record와
  revision/hash/permalink, rank/reason, 당시 resource snapshot을 저장한다.
- builder는 한 Slack work item의 현재 ACTIVE graph만 순회한다. 현재 ACTIVE test case,
  FAIL/BLOCKED attempt, OPEN/IN_PROGRESS/REOPENED issue만 원장과 projection이 일치할 때
  포함하고 stale 또는 무근거 resource는 전체 context 생성을 실패시킨다.
- digest seal과 PostgreSQL trigger가 snapshot update/delete, seal 이후 append와 job
  provenance 변경을 차단한다.

### Provider-neutral adapter와 validator

- provider 설정과 auth materialization을 호출 경계에 격리하고 secret·endpoint·payload가
  repr/error에 노출되지 않게 했다.
- timeout, 최대 attempt, retry status allowlist, stable error code와 HTTP response streaming
  byte limit을 적용했다.
- content type, UTF-8, JSON object, 중복 key, 비표준 `NaN`/`Infinity`, trailing content와
  과대 response를 fail-closed한다.
- validator는 exact output version, 5~10 cases, 연속 step, 정규화된 summary/label/category
  중복, ACTIVE evidence와 category membership을 검증한다.
- evidence에 없는 수치·정책 표현은 deterministic rule로 review flag를 남기거나 운영
  policy에 따라 저장 전 거부할 수 있다.

### 중복 탐지·배치 placement planner

- exact content key는 Unicode·대소문자·공백·문장부호를 정규화한 summary, 순서가 있는
  action/precondition step과 case 전체 기대결과를 함께 비교한다. ACTIVE이며 삭제되지 않은
  기존 case만 후보로 삼고 exact 후보는 cosine 점수가 같은 semantic 후보보다도 먼저 둔다.
- category와 상위일감은 exact·alias를 semantic보다 먼저 적용한다. category miss는 caller가
  준 유일한 기존 system `미분류`를 선택하며 category를 생성하지 않는다. 상위일감 miss는
  source identity·key·title·선택 category를 가진 명시적 create intent를 반환한다.
- `GenerationPlacementPlanner`는 validated case 순서를 유지한 case별 reuse 후보와 category/
  parent 결정을 하나의 불변 plan으로 묶는다. 기존 case/category/parent projection과 선택적
  precomputed vector만 받아 provider·embedding·repository를 호출하지 않고 QA 원장도 쓰지
  않는다.
- 후보 순서를 역전한 deterministic replay, 한국어 exact/alias, semantic reuse,
  `미분류`/create fallback과 vector 개수 fail-fast를 합성 integration test로 검증했다.

### 수동 generation job API와 UI

- `create/list/detail/execute/retry`를 canonical `/api/generation-jobs` route family로 연결하고
  본문과 `Idempotency-Key` header가 일치하는 append-only audit replay를 적용했다.
- `20260826_0018`은 root/retry 계보, attempt, 시작·완료 시각과 allowlisted redacted failure
  metadata를 추가한다. DB constraint/trigger가 상태 shape, `QUEUED → RUNNING → terminal`
  전이, 봉인 provenance와 terminal metadata 불변성을 강제하고 one-step downgrade는 0017
  guard function을 정확히 복원한다.
- root create는 현재 grounded context를 봉인한 `QUEUED` job만 만들고 외부 호출하지 않는다.
  retry는 `FAILED` predecessor의 context를 exact copy한 새 `QUEUED` child만 append하며 즉시
  실행하지 않는다. 세대 version과 attempt/root/retry 링크는 결정적이고 이전 실패는 보존된다.
- T01 미확정 production execute는 `GENERATION_SETUP_REQUIRED`와
  `externalCallAttempted=false`를 반환하고 job을 `QUEUED`로 유지한다. success/failure 전이는
  outbound network가 없는 injected fake runner로만 검증했다.
- Slack 요청 상세에서 수동 job 화면으로 이동해 이력·상세·봉인 근거·오류·후보 결과를
  확인하고 create/execute/retry를 명시적으로 누른다. setup-required 안내와 수동 만들기
  링크는 한국어로 유지되며 900/700/480 px 반응형 stacking과 44 px mobile action을 둔다.
- API PostgreSQL 회귀가 lifecycle 전후 `TestCase`와 `TestCaseVersion` count 0을 검증한다.
  성공 결과는 명시적 T10 apply 전까지 후보 JSON으로만 남는다.

### Transactional generation apply

- `GET /api/generation-jobs/{jobId}/apply-plan`이 SUCCEEDED stored result와 sealed context를 다시
  검증하고 현재 ACTIVE category/parent/case projection에 대한 authoritative digest·resource
  version·ordered placement를 반환한다. 기본 Web route가 generated OpenAPI type으로 이 plan을
  읽으므로 브라우저는 version, digest, exact key 또는 ledger ID를 만들지 않는다.
- `POST /api/generation-jobs/{jobId}/apply`는 job과 현재 원장을 다시 잠그고 schema, grounding,
  context/result digest, exact content key와 전체 placement를 재계산한다. plan 조회와 POST 사이
  stale state는 write 전 `GENERATION_PLACEMENT_CONFLICT`로 거부되고 Web은 detail/plan을 새로
  받아 재시도한다.
- 새 후보는 ACTIVE case와 version 1, `SLACK_AI`, CASE expected-result, 원래 순서의 step과
  nullable precondition, label, generation reason, evidence/source revision과 job/context/result
  provenance로 저장된다. exact duplicate는 receipt에 `REUSED`/`ledgerMutation: NONE`을 남기고
  기존 MANUAL version·step·tag·source를 전혀 바꾸지 않는다.
- 한 apply transaction이 필요한 trusted parent, case/version/step/tag, ordered outcome, batch
  audit와 outbox를 함께 commit한다. 동일 key/job/request replay는 original receipt를 반환하고
  다른 key의 같은 job, non-SUCCEEDED, invalid result와 stale/forged placement는 stable code로
  실패한다.
- middle `CREATE_VERSION`과 late `OUTBOX` fault 뒤 새 transaction에서 parent, case, version,
  step, current/version tag, receipt, outcome, key counter, audit, outbox delta가 모두 0임을
  확인했다. clean retry는 rollback 전과 같은 다음 TC key를 할당했다.
- apply API에 호출 시 실패하는 provider-runner tripwire를 주입한 plan/apply/replay/live smoke
  전체에서 호출 count가 0이었다. semantic reuse도 recomputed candidate set 밖이면 fail-closed한다.

### Historical generation comparison

- `GET /api/generation-jobs/{jobId}/comparison`은 `generation_jobs`, 순서가 고정된
  `generation_contexts`, stored result, optional immutable apply receipt/outcome만 투영한다. GET 전후
  generation/ledger/apply/audit/outbox/Slack/knowledge row count가 같고 provider tripwire 호출은 0이다.
- 성공 job은 봉인된 Slack workspace/channel/message/thread identity와 네 structured field, root
  revision body/author/link, snapshot 시점 ACTIVE decision·constraint, immutable case/version ID와
  sealed case body를 그대로 반환한다. live Slack 수정, decision/constraint projection 변경,
  current case version 추가 뒤에도 applied 비교 JSON이 byte-equivalent structure로 유지된다.
- candidate/result/reference 배열은 원래 index/rank 순서를 유지하고 generation reason, open
  questions, inference marker와 candidate→source edge를 노출한다. apply 전에는 `NOT_APPLIED`, apply
  뒤에는 한 reused + 네 created case 링크와 동일 receipt ID를 candidate 순서대로 표시한다.
- Web route는 Slack, 결정·제약, 기존 case snapshot, 생성 후보의 네 section을 제공한다. evidence
  chip은 keyboard로 열리고 controlled region과 같은 페이지의 봉인 source anchor로 이동한다.
  empty/redacted/missing 및 Slack delete body를 서로 다른 한국어 label로 표시하며 현재 source를
  채우지 않는다. QUEUED/RUNNING/FAILED 결과 없음과 404/409 recovery도 별도 안내한다.

### Exact Generation Input reconstruction

- `20260827_0020`은 `generation_jobs`에 capture state, input schema version, complete JSONB Input,
  RFC 8785 SHA-256을 추가한다. 기존 row는 `LEGACY_NOT_CAPTURED`와 null capture를 유지하며 Slack,
  knowledge, case, execution, issue, category의 현재 상태로 backfill하지 않는다.
- 새 root job은 Input `1.0.0`의 Slack structured fields와 source revision order, ACTIVE knowledge,
  pinned existing case/step/label, FAIL/BLOCKED execution, open issue, explicit empty source array,
  ordered category candidate, batch와 generation policy를 context seal과 같은 transaction에서
  검증·봉인한다. 16 MiB 초과 input은 job/context row를 남기기 전에 거부된다.
- captured retry는 predecessor input에서 request ID와 creation time만 새로 만들고 모든 domain
  array와 null/empty distinction을 그대로 복사한다. legacy retry는 legacy 상태를 유지하며 partial,
  corrupt 또는 unsupported capture를 live state로 복구하거나 자동 실행하지 않는다.
- `GET /api/generation-jobs/{jobId}/reconstruction`은 job과 ordered context snapshot만 읽고 exact
  input 또는 stable unavailable reason을 반환한다. response input은 같은 job의 injected provider
  runner 경계에서 관찰한 payload와 같고 canonical byte length/SHA-256도 재계산 결과와 일치한다.
- 동일 response 반복 read와 live Slack·knowledge·case·issue·category 변경/삭제 뒤 equality를
  확인했다. SQL tripwire와 generation/context/apply/ledger/audit/outbox/source 전체 row-count가 live
  table read, provider call, lock, flush 또는 mutation이 없음을 증명한다. 404와 unavailable execution
  409 code도 고정했고 OpenAPI schema와 generated TypeScript client를 재생성했다. Web 화면은 추가하지
  않았다.

## 검증 결과

| 검증 | 결과 |
|---|---|
| Generation 계약 집중 테스트 | 43 PASS (input/output 8 + job API 8 + apply 10 + comparison 10 + reconstruction 7) |
| Generation adapter·validator 집중 테스트 | 35 PASS |
| 중복·매핑·placement 집중 테스트 | 39 PASS |
| 전체 Generation Python unit | 78 PASS |
| 실제 PostgreSQL migration/repository 묶음 | 53 PASS |
| 전체 TypeScript 계약 테스트 | 83 PASS |
| API client / UI package 테스트 | 21 / 6 PASS |
| 전체 Python non-PostgreSQL 테스트 | 280 PASS, PostgreSQL 29 deselected |
| 실제 PostgreSQL repository / API 테스트 | 53 / 29 PASS |
| Generation comparison·reconstruction PostgreSQL / 관련 Web 집중 테스트 | 10 / 28 PASS |
| Web 테스트 | 169 PASS, physical live E2E 1 SKIP |
| Local production-preview comparison smoke | 1280×900 / 768×1024 / 390×844 PASS; exact sealed Slack/thread + ACTIVE decision/constraint + immutable case/version + 5 ordered candidates, 4 created + 1 reused links, horizontal overflow 0, mobile evidence/apply target 44 px |
| Python mypy | 112 source files PASS |
| Ruff·ESLint·전체 TypeScript typecheck | PASS |
| Schema / ADR / OpenAPI drift 검사 | PASS / PASS / PASS |
| Web production build | PASS, 94 modules transformed |

실제 PostgreSQL 검증은 이름에 `test`가 포함된 local disposable PostgreSQL 16 + pgvector DB를
매 test 전후 truncate하는 fixture에서 수행했다. fixture와 browser smoke seed는 합성 데이터이며
실제 Slack 또는 provider credential을 repository에 저장하지 않았다.
API 회귀는 root create와 idempotent replay, setup-required execute의 상태 보존, fake failure,
queued retry의 exact context copy, fake success, cursor list/detail뿐 아니라 authoritative plan,
atomic apply/replay/conflict/rollback, historical comparison 안정성과 영속 원장 row를 검증했다.

## 아직 연결하지 않은 범위

- 실제 provider endpoint·credential·비용·quota·context limit 확정(`T01`)
- 실제 signed Slack 수집 sample에서 production sealer가 optional structured field/thread display
  snapshot을 얼마나 채우는지 확인한다. 누락 값은 T11의 `MISSING_SOURCE`로 유지하고 live 조회로
  보완하지 않는다.

local production preview와 disposable API/PostgreSQL을 함께 띄운 physical browser에서 applied
synthetic SUCCEEDED job의 exact sealed Slack/thread, 두 ACTIVE knowledge item, pinned case/version와
다섯 candidate/reference/apply link를 확인했다. 1280 px에서는 source/knowledge가 두 column,
768 px와 390 px에서는 한 column으로 전환됐고 세 viewport 모두 document/client width가 같아
horizontal overflow가 없었다. 390 px evidence button과 applied link는 44 px였으며 Enter로 evidence
disclosure를 열어 `aria-controls` region과 `#comparison-slack` source anchor 이동을 확인했다.

남은 live gate는 실제 서명된 Slack session의 production deployment 재현과 T01 provider
endpoint·credential·요금·quota·context limit 승인이다. provider call은 의도적으로 실행하지 않았고
tripwire로 comparison/plan/apply/reconstruction이 provider boundary를 전혀 호출하지 않음을
증명했다. T12의 sealed input equality와 canonical digest E2E는 완료했으며, 수동 테스트케이스 생성
경로는 그대로 유지된다.
