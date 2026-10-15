# PE-SE2-T08 독립 검증 보고서

- 검증일: 2026-08-28 (Asia/Seoul)
- 역할: 구현 보고와 release certification을 신뢰하지 않는 독립 검증자
- 결론: **수정 후 local code/contract/CI gate는 PASS, release certification은 BLOCKED**
- release artifact: **발급하지 않음**. 저장소에 운영 reviewed golden과 보호 runner 실행 증거가 없다.

## 1. 권위와 검증 범위

다음 원문을 직접 읽고 구현 보고의 주장과 대조했다.

- `docs/phase-e/automatic-generation-quality-gate-contract.md`
- `packages/contracts/generation/quality-gate/1.0.0/schema.json`
- `backend/src/tc_backend/generation/quality_gate.py`
- `scripts/evaluate_automatic_generation_quality.py`
- `scripts/verify_automatic_generation_quality_artifacts.py`
- `backend/src/tc_backend/generation/semantic_duplicates.py`
- `backend/src/tc_backend/generation/application.py`
- `.github/workflows/automatic-generation-quality-release.yml`, `.github/workflows/ci.yml`
- 독립 mutation/contract/unit fixture와 실제 `/generation/failures` preview

T09~T11의 roadmap/check 상태는 변경하지 않았다.

## 2. 독립 공격에서 발견하고 수정한 결함

### P0: release snapshot 권위 위조

초기 workflow는 `workflow_dispatch` 호출자가 snapshot path와 SHA를 함께 정할 수 있어 forged snapshot도
자기 SHA와 함께 통과시킬 수 있었다. 호출자 input을 전부 제거하고 protected environment 관리자가 관리하는
`vars.APPROVED_SNAPSHOT_PATH`와 `secrets.APPROVED_SNAPSHOT_SHA256`만 release job에 전달하도록 수정했다.
path/SHA 부재, SHA 형식 오류, 파일 부재, symlink, hash mismatch는 모두 shell exit `2`다.

직접 실행한 trusted SHA 없는 release:

```text
automatic-generation quality gate input error: RELEASE_REQUIRES_TRUSTED_SNAPSHOT_SHA256
ELIFECYCLE Command failed with exit code 2
EXIT_CODE=2
```

`pnpm generation:quality:eval -- ...`와 verifier의 중간 `--`도 모든 workflow/document command에서 제거했다.
pnpm 10 형식으로 candidate evaluator → report revalidation → artifact verification을 직접 실행해 exit `0`을
확인했다. quality mutation은 exit `1`, malformed/unsafe/trusted-SHA 부재는 exit `2`로 분리된다.

### P0: cached report와 artifact replay/tamper

이전 verifier는 JSON/JUnit/Markdown이 실제 승인 snapshot에서 생성됐는지 증명하지 않았다. 수정 후 verifier는
snapshot SHA를 확인하고 evaluator를 fresh 재실행해 JSON 전체 동일성을 검사하며, Markdown을 exact 재생성하고,
JUnit의 suite/class/count/gate를 독립 검사한다. snapshot도 manifest hash/byte inventory와 secret/PII scan 대상에
포함했다. 다른 valid snapshot에 이전 report를 붙인 replay, Markdown 변경, PII 추가는 모두 exit `2`다.

### P1: provenance와 숨긴 attempt

다음 값이 서로 교차 검증되지 않거나 fingerprint 밖에 있어 독립 mutation이 통과했다.

- duplicate `applyOutcomeId`
- root/job/result/materialization의 workspace, source revision, context/input/result digest drift
- filtered prior failure와 이유 없는 `REJECTED`/`UNAVAILABLE` attempt
- review/approval, capture freshness/degraded/call count, live gate approval mutation

schema/evaluator를 수정해 root의 ordered `attemptHistory`, workspace/source/input/result/context seal, 모든 outcome
ID uniqueness를 검증하고 fingerprint에 dataset/review/privacy/inventory/capture/profile/threshold/liveGate를
포함했다. attempt 수는 selected attempt number와 정확히 같아야 하고 `1..n` 순서와 retry chain을 만족해야 하며
`REJECTED|UNAVAILABLE`에는 rejection code가 필요하다.

### P1: production semantic duplicate order 손실

기존 char-ngram projection은 step field를 합친 뒤 vectorize해 step 순서를 바꿔도 같은 vector digest가 됐다.
정책을 `offline-char-ngram-cosine-v2`로 올리고 summary, indexed precondition/action, case expected result를
각각 scope-tagged feature로 만들었다. `providerId=local-deterministic`,
`modelId=sha256-char-ngram-feature-hash`, `modelRevision=2`, `dimensions=256`, `threshold=0.85`와 이 tuple의
`semanticDuplicatePolicyDigest`를 input/report/receipt에 봉인했다.

## 3. 공격 fixture/mutation 결과

| 공격 | 기대/실제 |
|---|---|
| empty file/dataset, missing root/job/result/required field, unknown field/version | schema/input error, exit `2` |
| denominator zero, NaN/Infinity | strict parse/schema/arithmetic error, exit `2` |
| cached output path 또는 input/report path 충돌 | exclusive-create/path safety error, exit `2` |
| metric failure가 report 생성을 생략하거나 success로 변환 | report 생성 후 exit `1`; shell `test "$gate_status" -eq 0`로 CI red |
| unknown lifecycle | schema error, exit `2` |
| `SUPERSEDED`, `RETRACTED`, `DELETED`, `REJECTED`, `UNAVAILABLE` evidence | coverage 제외 + retired misuse, gate FAIL |
| ambiguous/conflicting authority | unresolved authority 증가, gate FAIL |
| stale/wrong source revision, non-member evidence, excerpt hash spoof | provenance/coverage FAIL |
| cross-workspace/context/input/result digest drift | provenance FAIL |
| exact duplicate, semantic cluster duplicate | 각 counter 증가, zero threshold FAIL |
| Unicode canonical equivalent | NFKC/casefold로 같은 semantic vector |
| step order 변경 | v2 vector digest 변경 |
| category/parent create collision | 각 collision counter 증가, zero threshold FAIL |
| duplicate CREATED/apply outcome ID | provenance FAIL |
| filtered failure, 이유 없는 rejected/unavailable attempt | attempt ledger provenance FAIL |
| synthetic/unreviewed release, forged review/provenance | certification false 또는 fingerprint/schema FAIL |
| 다른 snapshot에 cached report replay | `REPORT_SNAPSHOT_REPLAY_MISMATCH`, exit `2` |
| Markdown/JUnit/report tamper | exact replay/structure/schema mismatch, exit `2` |
| PII/raw Slack/secret pattern 주입 | artifact scan FAIL, exit `2` |
| evaluator network/model/embedding call | 호출 코드 없음; capture counters가 0이 아니면 eligibility FAIL |

`EvaluationReport`의 evidence denominator는 generated case별 required evidence 수 합계로 고정하며 synthetic
fixture는 `10/10`; 빈 root/case로 denominator를 0으로 만드는 input은 schema에서 거부한다. report arithmetic,
checks↔gate, certification 조건은 schema validation 뒤에도 다시 계산한다.

## 4. production semantic seam 추적

실제 호출 경로를 코드에서 따라갔다.

1. `application.py:602`가 production apply preparation에서
   `build_sealed_semantic_duplicate_input`을 호출한다.
2. `application.py:615-636`이 sealed threshold와 generated/existing vectors를
   `GenerationPlacementPlanner`에 넘기고 즉시 receipt를 재검증한다.
3. `application.py:499-513`이 apply 직전 source workspace/version/context/result와 receipt를 다시 검증한다.
4. `application.py:1199`가 hash-only semantic seal을 audit/placement metadata에 보존한다.
5. `semantic_duplicates.py:27-32, 207-220`이 version/provider/model/revision/dimension/threshold tuple과 digest를
   고정하고, `:116-140`이 workspace/context/result/vector inventory를 receipt digest에 포함한다.

이 경로는 local SHA-256 feature hashing만 사용한다. evaluator/verifier/semantic seam에서 `requests`, `httpx`,
`urllib`, `socket` 또는 external model/provider/Slack 호출을 찾지 못했고 runtime capture의 provider/Slack call
counter가 0이 아니면 evaluator가 fail-closed한다.

## 5. CI 독립 검사

- protected release에는 caller-controlled `inputs:`가 없다.
- release snapshot은 protected `vars`/`secrets`에서만 오며 copied snapshot도 400일 artifact에 포함된다.
- evaluator invocation의 extra `--`가 없다.
- `actions/checkout`, `pnpm/action-setup`, `actions/setup-node`, `actions/setup-python`, `actions/upload-artifact`와
  일반 CI action은 모두 40-hex immutable commit SHA로 pin됐다.
- `continue-on-error`가 없다.
- fresh output namespace, fresh evaluator, JSON schema 재검증, snapshot replay, artifact hash, PII/secret scan,
  final `gate_status == 0`을 모두 만족해야 job이 성공한다.
- synthetic job은 `releaseCertified=false`를 명시적으로 assert하며 protected job만 release certification을
  검사한다.

## 6. 자동 생성 실패 UI preview

실제 개발 preview에서 기존 공통 `tm-page-header`, `tm-list-surface`, `tm-list-toolbar`, `tm-cycle-modal`을 사용한
페이지를 확인했다. 실제 API error, retry 중 loading, empty, 독립 fetch fixture의 12-row list/detail을 확인했고
접근성 snapshot에는 12개 실패 button, detail summary, attempt table, timeline, 재시도 opener가 노출됐다.

1440×900 DOM 측정은 document `scrollWidth=1425 <= innerWidth=1440`, unexpected overflow 0,
failure workspace visible control 최소 높이 44px였다. 앞선 1280×768 및 390×844 검증 증거와 공통 dialog helper의
focus trap/ESC/opener restore unit gate를 함께 대조했으며, 요청에 따라 추가 screenshot 탐색은 종료했다.
loading/error/empty 전환에서 failure workspace의 header/filter/list/detail 영역 구조가 유지됐고 새로운 확정
layout/overflow 결함은 발견하지 못했다.

## 7. 실행 결과

### Focused

```text
scripts/tests/red_phase_e_automatic_generation_quality_gate.py
backend/tests/unit/test_generation_semantic_duplicates.py
scripts/tests/test_evaluate_automatic_generation_quality.py
=> 16 passed in 0.40s
```

Contract T08 suite는 전체 check 안에서 `7 passed`; Web failure page는 `3 passed`다.

### 전체 required gate

최종 종료 요청 후 추가 브라우저 작업 없이 2026-08-28 17:54 KST에 `pnpm check`를 다시 실행했고 PASS했다.

- ESLint PASS, Ruff PASS
- TypeScript typecheck PASS
- mypy `124 source files` PASS
- TypeScript unit: `314 passed`, live Web `1 skipped`
- Python non-PostgreSQL: `339 passed`, `104 deselected`
- strict schema: `9` schemas + versioned generation contracts + `12` Slack fixtures PASS
- ADR: `17` ADRs, `15` canonical decisions PASS
- OpenAPI/generated client drift PASS

2026-08-31 KST에 운영 DB와 분리된 `tc_test` disposable PostgreSQL을 최신 Alembic head까지 올린 뒤
`apps/api/tests/test_generation_apply_api_postgresql.py`를 독립 재실행했고 `16 passed in 16.05s`를 확인했다.
따라서 generation apply PostgreSQL local gate도 GREEN이다.

## 8. 최종 판정과 남은 live gate

수정된 evaluator, schema, production semantic seam, local CI contract는 독립 공격을 통과했다. 그러나 다음이
없으므로 **release certified를 만들지 않았다**.

1. 최소 32-root의 실제 운영 hash-only golden snapshot;
2. 두 사람/승인 group 검토와 immutable review/approval digest;
3. credentialed provider call을 관찰한 동일 snapshot fingerprint live attestation;
4. protected environment/self-hosted runner의 실제 successful run 및 400일 retention 증거.

따라서 T08 local implementation gate는 GREEN이지만 운영 release gate는 명시적으로 BLOCKED다. T09, T10,
T11은 체크하거나 인증하지 않았다.
