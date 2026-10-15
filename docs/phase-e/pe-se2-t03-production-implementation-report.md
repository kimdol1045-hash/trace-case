# PE-SE2-T03 production implementation report

## 결과

`PE-SE2-T03`을 production 코드 경계에서 독립 검토하고 마감했다. 현재 source snapshot은
full Generation Input digest나 transport revision identity가 아니라
`generation-impact/1.0.0` semantic projection과
`generation-impact-config/1.0.0` config digest로 비교된다. 결과는 append-only
`generation_change_assessments`에 남고, `INITIAL_GENERATE|REGENERATE`만 provider-free sealed
`AUTOMATIC` root를 선택한다. `SKIP`은 마지막 automatic materialization을 재사용하며
`REVIEW_REQUIRED`는 job을 만들거나 재사용하지 않는다.

이 마감은 provider 실행, TestCase 생성·overwrite·suggestion/apply, `generation_managed` 변경,
Slack reply를 수행하지 않았다. 다음 제품 경계는 열린 `PE-SE2-T04` 사람 수정 보호다.

## production 산출물

- `backend/migrations/versions/20260827_0022_generation_change_assessments.py`
  - source-version별 unique, append-only assessment ledger
  - predecessor/materialization/selected/reused automatic-root link guard
  - outcome/reason/digest/job/changed-field shape constraint
  - immutable update/delete trigger와 deferred cross-table constraint trigger
- `backend/src/tc_backend/db/models.py`
  - `GenerationChangeAssessment` ORM과 outcome enum
- `backend/src/tc_backend/generation/impact.py`
  - NFKC·whitespace normalization, RFC 8785 canonical JSON SHA-256
  - semantic projection, config projection, fixed-order changed-field classifier
- `backend/src/tc_backend/repositories/generation_impact_repository.py`
  - semantic predecessor와 automatic materialization predecessor 분리
  - typed outcome 분류와 같은 prepared context의 selective root seal
- `backend/src/tc_backend/worker/knowledge_consumer.py`
  - work-item lock, stale fence, assessment-first replay와 assessment/outcome/job receipt
  - projection·assessment·job/context/Input·audit·receipt 단일 transaction
- `backend/tests/unit/test_generation_impact.py`
  - full-input volatile identity 배제, 4필드/evidence 변화와 config digest 검증
- `apps/api/tests/test_phase_e_automatic_generation_impact_postgresql.py`
  - PostgreSQL 16 production path 16개 계약
- `docs/phase-e/automatic-generation-impact-contract.md`
  - released T03 비교·원장·transaction 계약

## 독립 audit 판정

| 위험 | 판정과 근거 |
|---|---|
| full Generation Input digest 오용 | semantic fingerprint path는 `generation_input_digest`를 읽지 않는다. request/job/source version, revision ID, timestamp와 rank churn을 바꾼 unit fixture가 같은 fingerprint다. |
| 4필드-only false negative | 네 Slack 필드 외 ACTIVE knowledge/evidence, cases, failed/blocked executions, open issues, extra sources, categories와 generation policy도 projection에 포함한다. request detail과 evidence-content 변경이 `REGENERATE`로 검증됐다. |
| same-text·late-old false positive | 새 revision ID의 같은 normalized text와 늦게 도착한 과거 revision은 source version만 늘리고 semantic projection은 같아서 `SKIP`한다. |
| v1→v2 SKIP→v3 chain | v3의 `predecessor_assessment_id`는 v2, materialization baseline은 v1 root다. v3 relevant change만 새 root를 선택한다. |
| manual/retry baseline 오염 | baseline query는 `AUTOMATIC`, `retry_of_job_id IS NULL`, older source만 허용한다. manual root와 automatic retry child를 만든 뒤에도 v1 automatic root가 선택됐다. |
| ambiguous/deleted/retracted/legacy job 생성 | 각각 typed `REVIEW_REQUIRED`로 assessment만 commit한다. selected/reused job은 null이다. explicit validated supersede만 deterministic `REGENERATE`다. |
| stale·경합·rollback 반쪽 commit | work-item row lock과 source-version unique가 concurrent attempt를 하나로 수렴시킨다. unseen stale version은 no-op이고 injected commit fault 뒤 assessment/job이 모두 0-delta이며 clean retry가 하나만 commit한다. |
| runtime receipt/replay | receipt는 assessment ID/outcome과 selected-or-reused job ID를 ledger를 통해 검증한다. review receipt는 job ID가 없고 committed historical assessment는 source advance 뒤에도 exact replay된다. |
| T01/T02 회귀 | 기존 automatic-generation PostgreSQL 16개가 T03 migration/runtime 뒤 그대로 통과했다. manual repeatability, receipt repair/rejection, automatic uniqueness와 retry trigger 상속도 유지됐다. |

## audit에서 수정한 결함

초기 구현은 application classifier가 fixed-order `changed_fields`를 만들었지만 migration의 check는
`REGENERATE` 배열에 대해 allowlist와 non-empty만 검사했다. 따라서 raw SQL이 중복, 역순 또는
`$INITIAL_SOURCE|$REVIEW_REQUIRED` reserved value를 넣을 여지가 있었다. 또한 job-link guard는
일반 `BEFORE INSERT` trigger라 계약의 commit-boundary deferred guard와 일치하지 않았다.

마감 수정으로 immutable DB validator가 allowlist 순서와 중복 없음을 검사하고, outcome check가
`REGENERATE` reserved value를 거부한다. link guard는 `AFTER INSERT DEFERRABLE INITIALLY DEFERRED`
constraint trigger로 바꿨다. PostgreSQL schema test가 valid ordered subset은 허용하고 역순·중복은
거부하며 trigger의 `tgdeferrable`과 `tginitdeferred`가 모두 true인지 직접 확인한다.

## 검증

모든 PostgreSQL command는 이름에 `test`가 포함된 disposable database와
`pgvector/pgvector:0.8.1-pg16`을 사용했다.

| 검증 | 실제 결과 |
|---|---|
| T03 focused PostgreSQL | 16 passed |
| 기존 T01/T02 PostgreSQL | 16 passed |
| migration/repository PostgreSQL | 57 passed, 기존 collection warning 1 |
| Python non-PostgreSQL unit | 286 passed, 61 deselected |
| Ruff | `backend`, `apps`, `scripts` 전체 PASS |
| strict mypy | 114 source files, issue 0 |
| Alembic `0022 -> 0021 -> 0022` | PASS, head `20260827_0022` |

실행 명령:

```text
TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> \
  node scripts/python.mjs -m pytest \
  apps/api/tests/test_phase_e_automatic_generation_impact_postgresql.py -m postgres -q

TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> \
  node scripts/python.mjs -m pytest \
  apps/api/tests/test_phase_e_automatic_generation_postgresql.py -m postgres -q

TC_TEST_DATABASE_URL=<disposable-postgresql-test-url> pnpm test:migration
pnpm test:unit:py
pnpm lint:py
pnpm typecheck:py

TC_DATABASE_URL=<disposable-postgresql-test-url> \
  node scripts/python.mjs -m alembic -c backend/alembic.ini downgrade 20260827_0021
TC_DATABASE_URL=<disposable-postgresql-test-url> \
  node scripts/python.mjs -m alembic -c backend/alembic.ini upgrade head
```

## 열린 T04·운영 경계

- T03은 `generation_managed`를 설정·해제하지 않고 사람 수정 여부를 판정하지 않는다.
- 기존 TestCase를 overwrite하거나 suggestion/diff/apply/ignore receipt를 만들지 않는다.
- generation provider를 선택·호출·retry하지 않고 결과 또는 비용을 저장하지 않는다.
- Slack thread에 성공·실패 답글을 게시하지 않는다.
- 실제 signed Slack delivery, production extractor/embedding configuration과 provider credential은
  외부 live gate로 남는다.
- T04는 사람 수정 시 관리권 해제와 automatic overwrite 차단을 별도 계약·migration/runtime
  경계로 구현해야 하며, T03 assessment 또는 `SKIP` receipt를 그 권한으로 사용해서는 안 된다.

