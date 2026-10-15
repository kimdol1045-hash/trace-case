# PE-SE2-T08 production 구현 보고서

- 기준일: 2026-08-28 (Asia/Seoul)
- 권위: `automatic-generation-quality-gate/1.0.0`, normative schema 1.0.0, 완료된 adversarial audit 요구
- 코드 상태: **완료 / 필수 로컬 gate GREEN**
- 운영 인증 상태: **BLOCKED — 실제 `REVIEWED` operational golden과 provider live attestation 없음**
- release certification: **발급하지 않음**

## 1. 구현 결과

[`scripts/evaluate_automatic_generation_quality.py`](../../scripts/evaluate_automatic_generation_quality.py)는
credential, service URL, DB 또는 wall clock을 읽지 않는 deterministic offline CLI다. 입력을 strict JSON으로
읽으면서 empty, duplicate key, `NaN`/Infinity, malformed JSON을 먼저 거부하고 normative schema의
`EvaluationInput`을 검증한다. Dataset/source/snapshot fingerprint는 문서가 선언한 canonical envelope를
UTF-8 canonical JSON SHA-256으로 재계산하며 mismatch를 보정하지 않고 exit `2`로 닫는다. CLI profile과
snapshot profile, 세 output 선언/path, input overwrite, output collision, symlink, existing/cached output도
동일하게 safety error(exit `2`)다.

[`tc_backend.generation.quality_gate`](../../backend/src/tc_backend/generation/quality_gate.py)는 다음을 pure
release decision seam으로 제공한다.

- hierarchy 실제 count와 16분류 histogram을 선언값과 exact 비교한다.
- 모든 case와 step을 integer denominator로 세고 valid evidence가 있는 unit만 numerator에 한 번 센다.
  따라서 PASS는 division 반올림이 아니라 `numerator == denominator`이고 synthetic fixture는 정확히
  `10 == 10`이다.
- context membership, knowledge/source/revision/content/excerpt hash, sealed-time interval과
  `ACTIVE + available + NONE conflict + ACCEPTED|NOT_APPLICABLE`를 함께 검증한다.
- `SUPERSEDED|RETRACTED|DELETED|REJECTED|UNAVAILABLE` misuse와 unresolved conflict/decision candidate를
  reference 단위로 세며 둘 다 threshold `0`이다.
- generated set과 ACTIVE existing inventory의 exact key 및 sealed semantic cluster, category, parent collision을
  각각 세고 합계를 숨기거나 상쇄하지 않는다. `APPLY_REUSED`와 same-lineage materialization shape만 안전
  reuse로 인정한다.
- root/job/retry/result/context/materialization ID와 digest, schema pin, 5~10 case, zero-based case order,
  one-based step order, normalized blank를 fail-closed로 검증한다.
- candidate에서도 count/distribution, offline/network counters, degraded 상태, secret/PII scan을 요구한다.
  Release에서는 추가로 32/32/32/160/160/320 최소값, 16분류별 2 root, `REVIEWED` metadata, 7일 freshness,
  clean privacy, provider credential/call/approval와 exact snapshot fingerprint live attestation을 모두 요구한다.

Schema-valid quality 또는 release eligibility failure는 report를 남기고 exit `1`, 선택 profile gate PASS는
exit `0`이다. 계약 §10/§11에 따라 오래된 operational snapshot 자체는 `SNAPSHOT_STALE` eligibility failure
(exit `1`)이고, stale/cached report 재사용이나 fingerprint/파일 tamper는 evaluator safety failure(exit `2`)다.
Unhandled exception도 redacted class/code만 stderr에 남기고 exit `2`로 닫는다.

## 2. Report와 artifact 무결성

Evaluator는 fresh JSON, JUnit XML, Markdown을 exclusive create로만 쓴다. JSON은 생성 직후 같은 normative
schema의 `EvaluationReport`로 다시 검증하며 coverage·duplicate·retired arithmetic, checks↔gate,
certification 조건도 별도로 교차 검증한다.

- JSON: fingerprints, exact version pins, actual counts, 100/0/0 및 모든 threshold, certification reason을
  hash/opaque ID/count만으로 기록한다.
- JUnit: suite `automatic-generation-quality-gate/1.0.0`, 7 quality checks, global eligibility, root별
  provenance/result/order, 16 distribution category를 만든다. `skipped=0`이며 required 미실행을 PASS로
  바꾸지 않는다.
- Markdown: PASS/FAIL, synthetic/operational, certification, fingerprints, counts, pins와 stable reason code만
  표시한다.

[`verify_automatic_generation_quality_artifacts.py`](../../scripts/verify_automatic_generation_quality_artifacts.py)는
승인 snapshot SHA-256을 먼저 확인하고 snapshot을 다시 평가해 JSON 전체가 동일한지 검증한다. 이어 JSON
schema/arithmetic, exact Markdown 재생성, JUnit count/gate를 독립 재검증하고 snapshot/JSON/JUnit/Markdown에
secret·PII·raw text pattern이 없는지 검사한다. 그 뒤 snapshot을 포함한 각 artifact의 byte length와 SHA-256을
fresh manifest에 기록한다.

## 3. Synthetic와 operational certification 분리

Synthetic fixture fingerprint를 evaluator가 실제로 재계산한 값으로 갱신했다. 이 fixture는 implementation
regression에서 candidate PASS를 만들지만 report는 항상 다음 상태다.

```text
releaseCertified=false
status=NOT_CERTIFIED_SYNTHETIC
reasonCodes=[SYNTHETIC_ONLY]
```

같은 fixture에 `RELEASE` profile을 요청하면 schema-invalid로 위장하지 않고 operational golden 부재를 포함한
eligibility FAIL(exit `1`)을 기록하며 certification은 계속 false다. Repository에는 synthetic input/report 외에
approved operational golden을 추가하지 않았다.

### 3.1 Read-only operational candidate bridge (2026-09-01)

`tc_backend.generation.quality_snapshot`과
`scripts/export_automatic_generation_quality_snapshot.py`를 추가해 PostgreSQL sealed authority에서 private
T08 `EvaluationInput`을 재구성하고 public E4 `ACTUAL_MINIMIZED` review envelope만 쓰는 bridge를 구현했다.
Explicit automatic APPLY root만 선택하고
Slack/knowledge/source record, retry/input/context/result, T09 version set, apply audit/outcome와 QA version을
교차검증한다. 같은 DB snapshot은 row time 기반 capture와 stable sort로 byte-identical하며 root `.env`,
provider/Slack/HTTP client, DB write를 사용하지 않는다.

Private T08 snapshot의 source-derived SHA, aliases, IDs, report paths, endpoint/text/excerpt는 공개하지
않고 aggregate projection, sanitized E4 fingerprints와 domain-separated source HMAC seals만 반환한다.
현재 원장이 CREATE 시점 parent 전체 inventory를 봉인하지 않으므로 exporter는 `CREATE_NEW` parent와
suggestion을 지원한다고 주장하지 않고 거부한다. Envelope도 `UNREVIEWED`, attestation `NOT_RUN`,
certification `NOT_CERTIFIED`이고, T09 reviewed/approval을 T08 review로 대체하지 않는다. 계약과 실행 방법은
[candidate export 문서](./automatic-generation-quality-candidate-export.md)에 있다.

## 4. Production apply semantic duplicate 입력

기존 production apply service는 `GenerationPlacementPlanner`에 case semantic vector를 전달하지 않아 exact
match 외 semantic candidate가 항상 비활성인 경로였다. 이를
[`semantic_duplicates.py`](../../backend/src/tc_backend/generation/semantic_duplicates.py)의 sealed offline input으로
연결했다.

- NFKC/casefold/whitespace 정규화된 summary, ordered precondition/action, case expected result를 고정
  char bigram/trigram feature hashing과 L2 normalization으로 256차원 vector로 만든다.
- 정책 `offline-char-ngram-cosine-v2`, provider `local-deterministic`, model
  `sha256-char-ngram-feature-hash`, revision `2`, dimension `256`, inclusive cosine threshold `0.85`와
  이 전체 tuple의 정책 digest를 고정한다. Summary/case expected/각 ordered step field의 scope를 feature에
  포함하므로 step 순서를 바꾸면 vector digest가 달라진다.
- generated vector와 ACTIVE ledger case vector 각각의 digest, sorted existing case inventory,
  workspace ID/version, workspace-version digest, context digest와 result digest를 하나의 receipt digest로
  봉인한다.
- plan과 apply에서 receipt를 재검증하고 source workspace/version/context/result 또는 vector/policy/receipt
  tamper가 있으면 `SEMANTIC_DUPLICATE_SEAL_MISMATCH`/`PLACEMENT_NOT_REPRODUCIBLE`로 닫는다.
- server-owned draft는 hash-only semantic seal을 보유하고 committed apply의 `placementDigest`와 audit after에
  같은 seal이 들어간다. External embedding/provider/network fallback은 없다.

이 변경은 T08 duplicate 방어를 production materialization 경로에 연결한 것이며 embedding/model baseline
비교나 pin migration을 구현하지 않는다. 그 소유권은 T09에 그대로 남겼다.

## 5. Package와 required CI

Root package에 다음 command를 추가했다.

```text
pnpm generation:quality:export-candidate ...
pnpm generation:quality:eval ...
pnpm generation:quality:verify-artifacts ...
```

[`automatic-generation-quality-release.yml`](../../.github/workflows/automatic-generation-quality-release.yml)은
`continue-on-error` 없이 두 job을 둔다.

- PR/main synthetic candidate는 fresh 세 report 생성, report schema 재검증, artifact hash/PII scan, manifest,
  `releaseCertified=false` assertion을 수행하고 30일 artifact를 보존한다.
- protected operational release는 `automatic-generation-quality-release` environment와 전용 self-hosted runner의
  관리자 설정 `vars.APPROVED_SNAPSHOT_PATH`, `secrets.APPROVED_SNAPSHOT_SHA256`만 요구한다.
  `workflow_dispatch` 호출자가 path/SHA를 주입할 input은 없다. 값이 비거나 SHA 형식·파일·symlink·hash 조건이
  맞지 않으면 exit `2`로 닫는다. Evaluator에는 credential/service endpoint를 전달하지 않는다. 모든 action은
  immutable commit SHA로 pin하며 모든 release assertion과 400일 retention artifact가 통과하기 전에는 인증
  성공 상태가 없다.

현재 repository와 runner context에 실제 approved path를 제공하지 않았으므로 protected operational job은
의도대로 차단 상태다. Synthetic fixture나 hand-written report로 이 부재를 대체하지 않았다.

## 6. 검증 근거

| Gate | 결과 |
|---|---:|
| focused T08 production seam | `3 passed` |
| evaluator + semantic receipt focused regression | `6 passed` |
| quality-gate TypeScript contract | `7 passed` |
| generation apply actual PostgreSQL | `16 passed` |
| Python default non-PostgreSQL | `332 passed`, `104 deselected` |
| TypeScript unit in `pnpm check` | `314 passed`, live Web `1 skipped` |
| strict schema | `9` schemas + versioned generation contracts + Slack fixture validation PASS |
| Ruff | PASS |
| strict mypy | `124 source files` PASS |
| ADR validation | `17 ADRs`, `15 canonical decisions` PASS |
| OpenAPI/generated client drift | PASS |
| full `pnpm check` | PASS |

실행한 focused command는 다음과 같다.

```text
node scripts/python.mjs -m pytest -q \
  scripts/tests/red_phase_e_automatic_generation_quality_gate.py \
  backend/tests/unit/test_generation_semantic_duplicates.py \
  scripts/tests/test_evaluate_automatic_generation_quality.py

pnpm --filter @tc/contracts exec vitest run \
  src/phase-e-automatic-generation-quality-gate-contracts.test.ts

TC_TEST_DATABASE_URL=<disposable-t08-postgresql-url> \
  node scripts/python.mjs -m pytest -q \
  apps/api/tests/test_generation_apply_api_postgresql.py

pnpm check
```

## 7. 남은 live gate와 범위 경계

T08의 계약 코드와 required local gate는 완료됐으므로 roadmap T08 code item을 체크했다. 단, 다음은 명시적
별도 차단 상태이며 release certification을 발급하지 않았다.

1. 실제 generation runtime에서 캡처한 hash-only 32-root 이상 operational golden;
2. 두 사람/승인 group의 `REVIEWED` label metadata와 immutable approval;
3. 실제 credentialed provider call이 관찰된 live attestation;
4. 같은 snapshot fingerprint, 7일 freshness와 clean source artifact scans;
5. protected runner의 실제 workflow execution과 400일 immutable retention 증거.

T09 version regression/baseline 승인·rollback, T10 signed Slack root→reply 전체 E2E, T11 human-modified
suggestion 전체 E2E의 코드·roadmap 상태는 변경하지 않았다.
