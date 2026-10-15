# Sprint 0 계약과 Slack 평가 샘플

## 1. 범위와 현재 상태

이 문서는 `P0-S0-T01`과 `P0-S0-T02`의 기준선을 기록한다. 제품 기획서 v0.3과 개발 명세서의 명시된 규칙을 기준으로 상태 enum, 핵심 쓰기 API payload, Slack 일감 파싱 결과, AI 생성 출력의 JSON Schema 원본을 만들었다.

현재 실제 Slack 일감이나 thread는 제공되지 않았다. `evals/fixtures`의 12개 사례는 모두 계약 및 평가 harness 개발을 위한 **synthetic 데이터**이며, 실제 팀의 표현 분포·예외·개인정보·업무 관행을 대표한다고 간주하면 안 된다. 실제 샘플을 확보하면 원본을 익명화한 별도 dataset version을 만들고 이 synthetic 세트는 회귀용으로 유지한다.

## 2. 계약 원본

| 파일 | 계약 내용 |
|---|---|
| `packages/contracts/state-enums.schema.json` | 테스트케이스, 사이클, 실행, 이슈, 지식 상태와 우선순위, 심각도, 생성 출처, Slack 파싱 필드, ontology node·predicate enum |
| `packages/contracts/core-api-payloads.schema.json` | 폴더·테스트케이스·사이클·실행·이슈·Slack AI 생성의 핵심 request와 공통 오류 response `$defs` |
| `packages/contracts/slack-work-item-parse-result.schema.json` | 자유 형식 Slack root/thread에서 구조화한 4개 일감 필드, 누락 필드, confidence, 근거 revision |
| `packages/contracts/ai-test-case-generation-output.schema.json` | 현재 QA 보드 형식의 5~10개 케이스, 스텝별 사전조건·동작, 케이스 전체 기대결과, evidence, 생성 이유, 라벨과 카테고리 후보 |
| `packages/contracts/slack-golden-fixture.schema.json` | synthetic Slack source bundle과 파싱·지식 상태·생성 컨텍스트 기대값 |

`state-enums.schema.json`의 `$defs`가 상태값의 단일 원본이다. 다른 Schema는 상대 `$ref`로 이를 참조한다. 문서에 값이 확정되지 않은 parse job, generation job, background job 내부 상태 enum은 임의로 추가하지 않았다.

### 2.1 고정한 상태와 값

| 구분 | 값 |
|---|---|
| 테스트케이스 | `ACTIVE`, `DEPRECATED` |
| 사이클 | `DRAFT`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED` |
| 실행 | `NOT_RUN`, `IN_PROGRESS`, `PASS`, `FAIL`, `BLOCKED`, `SKIPPED` |
| 이슈 | `OPEN`, `IN_PROGRESS`, `RESOLVED`, `CLOSED`, `REOPENED` |
| 지식 | `ACTIVE`, `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED` |
| 우선순위·심각도 | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| 테스트케이스 생성 출처 | `MANUAL`, `SLACK_AI`, `FILE_IMPORT`, `API` |

### 2.2 핵심 필수 규칙

- 테스트케이스 생성은 `folderId`, `ACTIVE`, 1~200자 서머리, 사전조건, 1개 이상 step, 케이스당 예상결과 1개, 생성 출처를 필수로 한다.
- `SLACK_AI` 출처는 일감 ID, 생성 job ID, 1개 이상의 evidence node ID를 추가로 요구한다.
- 테스트케이스 수정은 `baseVersion`과 `changeReason`을 필수로 하여 optimistic locking을 드러낸다.
- 실행 수정에서 `FAIL` 또는 `BLOCKED`이면 비어 있지 않은 `actualResult`가 필수다.
- 이슈는 서머리, 사전조건, 1개 이상 재현 step, 기대결과, 실제결과, 상태, 심각도가 필수다.
- Slack 파싱 결과는 없는 값을 만들지 않는다. `missingFields`에 든 필드는 결과에 존재할 수 없고, `missingFields`에 없는 4개 필드는 반드시 존재한다.
- AI 출력은 현재 QA 보드 모델의 5~10개 케이스를 반환하고 각 케이스는 1개 이상의 evidence를 가져야 한다. `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED` 지식은 확정 생성 컨텍스트에서 제외한다.
- Jira·Google Sheets·`Depth1/2/3`는 생성 계약에 포함하지 않는다. Slack 일감은 내부 상위일감으로 생성·재사용하고 출력 케이스는 그 하위에 저장한다.

JSON Schema 밖의 deterministic domain validator가 추가로 검사할 규칙은 step `sequence`의 1부터 시작하는 연속성과 중복 없음, evidence ID가 해당 generation context snapshot에 존재하는지, `startsAt <= endsAt`, 상태 전이 허용 여부다.

## 3. Synthetic golden dataset

manifest는 `evals/fixtures/manifest.json`, 개별 사례는 `evals/fixtures/slack-work-items`에 있다. 각 fixture는 synthetic 표시, Slack root와 thread, 기대 파싱 결과, 기대 지식 상태와 근거 revision, 생성에 포함·제외할 지식, 확인 필요 사항을 함께 가진다.

| 분류 | 수 | 주요 평가 포인트 |
|---|---:|---|
| 정상 | 3 | 완전한 4개 필드 파싱, thread의 명시적 결정, `ACTIVE` 근거 사용 |
| 누락 | 3 | 요청 메뉴·기능 설명·요청내용 누락, 값 비생성, `missingFields`와 open question |
| 결정 번복 | 3 | 시간 순서, 명시적 취소·변경, 이전 결정 `SUPERSEDED`, 새 결정 `ACTIVE` 및 supersedes 링크 |
| 충돌 | 3 | 상충 발화에 최종 결론 없음, 양쪽 `AMBIGUOUS`, 확정 생성 근거에서 제외 |
| 합계 | 12 | Sprint 0 최소 10개 조건 충족 |

fixture의 confidence 값은 synthetic 정답 label일 뿐 실제 모델 calibration 기준이 아니다. 실제 데이터 확보 전에는 현행 결정 회수율 90% 같은 제품 품질 목표의 달성 여부를 이 세트만으로 판단하지 않는다.

## 4. 실제 Slack 샘플 intake template

실제 샘플은 아래 단위로 받아 원본 접근이 통제된 위치에서 익명화한 뒤 repository용 fixture로 변환한다. token, signing secret, 이메일, 전화번호, 고객 식별자, 주문번호, 실제 사용자 데이터는 제출하지 않는다. 작성자 ID는 대화 내 동일 인물 여부만 유지하는 가명으로 치환하고, URL은 원본 추적이 필요한 내부 evidence 저장소에서만 보존한다.

```yaml
sample_id: real-slack-YYYY-NNN
collection_context:
  workspace_id_alias: T_REAL_001
  channel_id_alias: C_REAL_001
  channel_visibility: public | private
  captured_at: 2026-08-20T00:00:00Z
  purpose_and_consent_confirmed: true
source:
  root:
    message_ts: "0000000000.000000"
    author_alias: U_REAL_001
    body_anonymized: |-
      [일감]
      개발건: ...
      요청 메뉴: ...
      기능 설명: ...
      요청내용: ...
    revision: 1
    edited_at: null
    deleted_at: null
    permalink_alias: internal-evidence-id
  thread:
    - message_ts: "0000000000.000001"
      author_alias: U_REAL_002
      body_anonymized: "..."
      reply_to_message_ts: "0000000000.000000"
      revision: 1
      edited_at: null
      deleted_at: null
      permalink_alias: internal-evidence-id
expected_annotation:
  parse_result:
    development_item: "..."       # 없으면 키를 생략
    requested_menu: "..."         # 없으면 키를 생략
    feature_description: "..."    # 없으면 키를 생략
    request_details: "..."        # 없으면 키를 생략
    missing_fields: []
  knowledge:
    - annotation_id: K_REAL_001
      type: DECISION | REQUIREMENT | CONSTRAINT | OPEN_QUESTION
      normalized_content: "..."
      status: ACTIVE | AMBIGUOUS | SUPERSEDED | RETRACTED
      evidence_message_ts: ["0000000000.000001"]
      supersedes_annotation_ids: []
      adjudication_reason: "..."
  generation_context:
    include_annotation_ids: []
    exclude_annotation_ids: []
    open_questions: []
  expected_test_cases:
    - summary: "..."
      precondition: "..."
      steps:
        - sequence: 1
          action: "..."
      expected_result: "..."
      evidence_annotation_ids: []
  expected_folder_path: "..."
review:
  annotated_by: PO-QA-alias
  second_review_by: reviewer-alias
  disagreement_and_resolution: ""
```

한 샘플의 수정·삭제 이력이 의미를 바꾸면 revision별 body와 시각을 모두 제공한다. 번복과 충돌 annotation은 단순히 마지막 문장을 택하지 말고, 발화 관계·명시적 확정 표현·업무상 권위에 따른 판단 이유를 함께 기록한다.

## 5. 아직 확보되지 않은 항목

- 익명화 가능한 실제 Slack root/thread와 PO·QA가 합의한 기대 결과
- 항목명 변경, 순서 변경, 자유 서술, 표·목록·링크가 섞인 실제 일감 표현 분포
- message edit, delete, 늦게 도착한 reply, 중복 event를 포함한 실제 revision 사례
- 명시적 결정, 암묵적 동의, 번복, 최종 결론 없는 충돌에 대한 팀의 실제 판정 예시
- 실제 기대 테스트케이스와 기존 케이스 중복 여부, 과거 실패·이슈 근거
- 초기 디렉토리 전체 경로와 메뉴 별칭, `AI 생성함` 실제 folder ID
- Slack workspace/channel ID, allowlist 범위, public/private 수집 정책, HTTP Events API 또는 Socket Mode 선택
- 샘플 반출·보관·익명화 기준과 검토 책임자; App credential과 token은 샘플 intake 및 repository 범위에서 제외

실제 샘플이 들어오면 synthetic와 real-anonymized dataset을 구분하고 dataset version, annotation 변경 이력, train/eval 오염 방지 경계를 기록해야 한다.

## 6. 기계 검증 결과

2026-08-20에 다음을 검증했다.

1. `jq empty`로 `packages/contracts`와 `evals/fixtures` 아래 모든 JSON의 구문을 검증했다.
2. `pnpm test:schema`가 AJV 8의 Draft 2020-12 strict mode로 Schema 5개의 compilation을 검증한다.
3. 같은 명령이 `slack-golden-fixture.schema.json`과 그 참조 Schema로 12개 fixture를 전부 검증한다.
4. fixture ID·경로 중복 없음, manifest와 실제 파일 일치, category별 3개, synthetic flag, source revision 참조, knowledge ID 참조, `ACTIVE` 포함·비활성 제외 규칙도 같은 script에서 확인한다.

검증기는 `scripts/validate-contracts.mjs`에 있으며 root `pnpm check`와 CI quality job에 포함된다. Git commit은 만들지 않았다.
