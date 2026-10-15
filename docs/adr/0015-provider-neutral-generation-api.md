# ADR-0015: 수동 생성을 기본으로 하고 provider-neutral Generation API를 후속 연결

| 항목 | 값 |
|---|---|
| ADR 번호 | 0015 |
| 날짜 | 2026-08-20 |
| 상태 | Proposed |
| 결정자 범위 | Architecture / BE / FE / AI / OPS / QA |

## Context

테스트 관리 도구의 주된 과업은 사람이 테스트케이스를 작성·수정하고 사이클을 실행하며 내부 이슈를 추적하는 것이다. 자동 생성은 Slack 일감·thread와 온톨로지 히스토리를 활용해야 하지만, 관리 도구 안에 별도 AI 생성 화면과 특정 모델 runtime을 기본 탑재할 필요는 없다. 기존 ADR-0010은 Ollama·Qwen3-8B를 MVP 생성 runtime으로 제안했으나, 수동 생성을 기본으로 하고 생성 기능은 별도 API로 연결한다는 제품 결정과 충돌한다.

## Decision Drivers

- 수동 테스트케이스 작성이 항상 사용 가능한 기본 흐름
- 생성 모델·provider를 관리 도구 배포와 독립적으로 교체
- Slack·ontology context와 생성 결과 schema의 추적 가능성
- 유료 여부·quota·보안 경계를 endpoint 연결 전에 명시적으로 확인
- API 장애가 Library·Cycle·Issue 수동 운영을 막지 않음
- 사람이 수정한 생성 케이스를 자동 응답이 덮어쓰지 않음

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| Provider-neutral Generation API | HTTP 계약으로 context를 전달하고 schema-valid 결과만 등록 | 제안 선택 |
| Ollama를 관리 도구에 내장 | Web/API/Worker 배포가 특정 로컬 모델 runtime을 직접 운영 | ADR-0010을 대체하며 제외 |
| 자동 생성 기능 완전 제외 | 수동 관리만 제공하고 후속 연결점도 두지 않음 | 원래 자동 생성 목표 때문에 제외 |
| 특정 hosted AI SDK 직접 연결 | provider SDK와 credential을 관리 도구에 결합 | 비용·교체·보안 결합 때문에 제외 |

## Decision

관리 도구의 Library 생성 버튼은 수동 테스트케이스를 기본으로 만든다. Slack 요청 상세에는 별도 standalone AI 작성기가 아니라 봉인된 근거와 append-only 상태를 확인하는 수동 generation job 화면만 둔다. 자동 생성은 `Generation API` adapter를 통해서만 호출하며 endpoint·인증 방식·provider ID·schema version·timeout·rate limit·비용 상태를 Integration 설정으로 관리한다.

Worker는 Slack root·thread revision, typed ontology의 현재 `ACTIVE` 결정, 기존 테스트케이스와 실행·이슈 히스토리를 고정 context snapshot으로 만든 뒤 provider-neutral HTTP payload로 전달한다. 응답은 canonical JSON Schema를 통과한 경우에만 Library에 등록한다. 수동 작성은 step별 기대결과를 사용하고 Generation API payload는 케이스 전체 기대결과 하나를 사용한다. 도메인 매핑에 실패하면 새 도메인을 자동 생성하지 않고 `미분류`에 둔다.

기존 n8n 자동화에서는 정상 흐름·기본 CRUD·명시된 특이사항 중심, 추측성 일반 예외
제외, 기능 묶음·generation batch별 5~10개라는 작성 정책만 재사용한다. 넓은 일감은
여러 batch로 나눈다. Jira 입력과 Google Sheets 출력,
`Depth1/2/3` 형식은 제거하고 Slack+RAG context에서 현재 QA 보드의 category·상위일감·
테스트케이스 계약으로 직접 저장한다. Provider는 board ID나 source metadata를 발급하지
않으며 trusted adapter가 원장 기준으로 이를 부여한다.

API가 연결되지 않았거나 실패해도 수동 Library·Cycle·Issue 기능은 계속 동작한다. 생성 케이스를 사람이 수정한 이후의 API 응답은 새 version을 자동 적용하지 않고 suggestion으로 보존한다. MVP 비용 목표는 신규 유료 AI 호출 0원이므로 팀 소유·self-hosted 또는 이미 비용이 승인된 endpoint만 활성화하며, endpoint 비용·quota 확인 전에는 Integration 상태를 `Setup required`로 유지한다.

versioned schema contract, grounded context snapshot 원장, provider-neutral adapter와
timeout·재시도·응답 validator, exact·semantic 기존 case reuse 후보와 category/상위일감
placement planner는 구현됐다. planner는 validated response와 caller-supplied projection 및
precomputed vector만 받아 case 순서를 보존하고, exact를 semantic보다 우선하며, category
miss는 기존 system `미분류`, parent miss는 명시적 create intent로 반환한다. 이 단계는
provider 또는 repository를 호출하지 않으며 QA 원장을 쓰지 않는다. 수동 generation job
`create/list/detail/execute/retry` API와 Slack 요청 기반 UI도 구현됐다. create와 retry는 외부
호출 없이 `QUEUED` job을 append하고, explicit execute는 T01 미확정 상태에서
`GENERATION_SETUP_REQUIRED`로 fail-closed한다. job/context/audit만 저장하며 `TestCase` 원장은
쓰지 않는다. 실제 endpoint·credential·비용·quota·context 한도 결정과
Slack→provider→Library 저장 E2E가 아직 없으므로
`Proposed`다.

## Consequences

### Positive

- 핵심 테스트 관리 흐름이 모델 설치·장애·비용과 분리된다.
- 생성 provider를 바꿔도 관리 도구의 domain·UI 계약을 유지할 수 있다.
- 생성 context와 응답 version을 원장에 기록해 재현·감사가 가능하다.
- 사용자는 자동 생성 설정 없이 즉시 수동 케이스를 작성할 수 있다.

### Negative

- 별도 endpoint 배포·인증·rate limit·비용 관리가 필요하다.
- provider마다 latency·context·품질 차이를 adapter와 eval에서 흡수해야 한다.
- 생성 engine 내부 관측성은 외부 API가 제공하는 범위에 의존한다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| 유료 endpoint가 비용 목표를 깨뜨림 | 연결 전에 가격·quota를 기록하고 승인되지 않은 endpoint는 활성화하지 않는다. |
| Slack·QA 정보가 허용되지 않은 경계로 전송됨 | endpoint allowlist, TLS, field masking, private channel ACL과 전송 audit를 적용한다. |
| malformed·근거 없는 응답 등록 | strict JSON Schema, evidence ID 검증, inference/open-question 분리 후 실패 처리한다. |
| provider 장애가 QA를 막음 | circuit breaker와 실패 상태를 노출하되 수동 생성·실행은 독립 유지한다. |
| 사람 수정 내용 덮어쓰기 | `manual_edit_at` 이후 자동 update를 차단하고 suggestion만 생성한다. |

## Validation Evidence

- [개발 명세 13장](../test-management-development-spec.md)은 수동 기본과 Generation API 입력·출력·수신 검증 경계를 정의한다.
- [개발 로드맵 Phase 4](../test-management-roadmap.md)는 provider-neutral adapter와 비용·credential 확인을 구현 task로 둔다.
- [사용 가이드](../user-guide.md)는 현재 테스트케이스 생성을 수동 작성으로 안내하고 Generation API를 후속 연결 범위로 둔다. dead Integrations mock은 2026-08-24 legacy 감사에서 제거됐다.
- [n8n 전환 경계](../phase-c/n8n-generation-migration.md)는 기존 Slack/Jira/Sheets 자동화에서 재사용할 작성 정책과 폐기할 입출력 형식을 분리한다.
- [Generation `1.0.0` 계약](../phase-e/generation-contract.md)은 exact version pair, trusted reference 경계와 외부 provider 미확정 항목을 기록한다.
- [PE-SE2-T01 자동 생성 orchestration 계약](../phase-e/automatic-generation-orchestration-contract.md)은 quiet-window 이후 검증된 Slack 지식 projection과 PostgreSQL grounded context/input seal을 한 commit의 `QUEUED` job 경계로 묶고, provider 실행과 T02 이후 중복 방지를 분리한다. 명시적으로 주입된 지식 consumer만 사용하는 구현은 후속 integration/release-gate task를 통과했고 generation provider 호출 0회를 유지한다. 독립 검토 근거는 구현 전 adversarial audit에 한정한다. [Sprint E2 기반 보고서](../phase-e/sprint-e2-foundation-report.md)
- [Sprint E1 기반 보고서](../phase-e/sprint-e1-foundation-report.md)는 context snapshot, adapter·validator와 실제 PostgreSQL/전체 회귀 근거를 기록한다.
- `PE-SE1-T09` 수동 job 계약·API·UI 회귀는 idempotent create/execute/retry, append-only
  `QUEUED` retry 계보, 한국어 setup-required/manual fallback, 반응형 layout과 QA 원장 row 0을
  검증한다. 실제 provider나 자동 case persistence를 활성화하지 않는다.
- `DeduplicationDetector`, `GenerationTargetMapper`, `GenerationPlacementPlanner`의 39개 집중
  테스트는 한국어 exact/alias, exact-over-semantic, semantic threshold, `미분류`/parent
  create fallback과 후보 역순 deterministic replay를 검증한다. `run_29729e1bd491`에서
  Ruff·strict mypy·계약·ADR gate와 함께 재검증했다.
- 실제 Generation API endpoint·credential과 Slack→provider→Library 저장 E2E는 아직 없다.

## Revisit Triggers

- 한 provider의 독점 기능이 canonical 계약으로 표현되지 않아 제품 가치가 반복적으로 손실된다.
- 팀이 생성 기능을 영구 제외해 API adapter와 generation 원장이 불필요해진다.
- 보안 정책이 모든 generation inference를 동일 사내 host 안에서만 허용한다.
- 비용·latency·품질 측정 결과 특정 self-hosted runtime을 공통 표준으로 고정할 근거가 생긴다.

## Related Documents

- [ADR 원장](./README.md)
- [ADR-0010 Ollama 로컬 AI](./0010-local-ai-with-ollama.md)
- [ADR-0011 계약 우선 JSON Schema](./0011-contract-first-json-schema.md)
- [ADR-0008 typed ontology 원장](./0008-typed-ontology-ledger.md)
- [개발 명세](../test-management-development-spec.md)
- [개발 로드맵](../test-management-roadmap.md)
