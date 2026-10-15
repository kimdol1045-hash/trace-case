# PE-SE2-T08 자동 생성 golden quality release gate 계약

| 항목 | 값 |
|---|---|
| 계약 버전 | `automatic-generation-quality-gate/1.0.0` |
| 작성 기준일 | 2026-08-28 (Asia/Seoul) |
| 상태 | **authoritative contract + production evaluator·gate·required CI GREEN / operational reviewed golden live gate BLOCKED** |
| normative schema | `packages/contracts/generation/quality-gate/1.0.0/schema.json` |
| 범위 | 자동 생성 품질 평가 단위, 100/0/0 threshold, offline snapshot, report/certification, release integration 경계 |
| 비범위 | provider 선택·호출 구현, Slack credential 사용, T09 version regression, T10~T11 E2E |

## 1. 목적과 안전 불변식

T08은 자동 생성 결과를 출시 가능한 것으로 인증하기 위한 세 가지 무관용 기준을 고정한다.

```text
evidence coverage = 100%
retired knowledge misuse = 0
duplicate = 0
```

세 값은 반올림 전 exact 값으로 동시에 충족해야 한다. 평균, category별 상쇄, sampling,
warning 전환, 승인자 override, retry 후 best-of 선택으로 실패를 통과시킬 수 없다. 이 세 값 외의
provenance, unresolved authority, result validity, ordering, dataset eligibility check도 모두 통과해야
release certification을 발급한다.

이 계약은 evaluator가 외부 provider, Slack, database 또는 embedding service를 호출하지 않는
deterministic offline snapshot 평가를 전제로 한다. 실제 credential을 사용해 생성한 운영 golden
snapshot의 획득과 승인은 별도 live gate다. 합성 fixture는 contract candidate gate를 통과할 수 있지만
어떤 경우에도 production release certification을 발급하거나 사칭할 수 없다.

## 2. 선행 권위와 평가 단위

T08은 T01~T07 원장을 재구성하거나 대체하지 않는다. 다음 immutable authority를 snapshot으로
캡처한다.

| 평가 단위 | identity와 필수 권위 |
|---|---|
| generation root | `generationRootId`, canonical Slack work item, exact `sourceVersion`, `sealedSourceTime`, T03 assessment 계보 |
| generation job | terminal selected root/retry attempt, root/retry relation, `AUTOMATIC`, exact input/output schema, context/input/result digest |
| generation result | stored result snapshot ID, validation status, output schema, RFC 8785 result digest, ordered case array |
| generated case | result-local zero-based `caseIndex`, normalized field seals, exact key, semantic cluster, placement, materialization provenance |
| generated step | case-local one-based `sequence`, precondition/action/expected-result seals |
| evidence reference | case 또는 exact step coverage target, context entry/node, citation/source revision과 content/excerpt hash |

하나의 release 평가 root는 그 root에서 materialization authority가 된 유일한 `SUCCEEDED` job을
선택한다. retry child가 성공했다면 `selectedJob.generationJobId`는 그 child이고
`rootJobId`는 root여야 한다. 실패·QUEUED·RUNNING·결과 불명·서로 다른 root의 결과를 평가 가능한
성공으로 바꾸지 않는다.

### 2.1 result/apply/suggestion provenance

각 case의 `materialization`은 다음 중 하나다.

- `APPLY_CREATED`: immutable `generation_apply`와 ordered `generation_apply_outcome` ID가 모두 있고,
  outcome의 job/root/context/result seal이 평가 job과 정확히 같다.
- `APPLY_REUSED`: 같은 apply/outcome proof를 가지며 기존 case에 write가 없다는 T10 apply receipt와
  일치한다. 안전한 reuse는 duplicate 생성으로 세지 않는다.
- `SUGGESTION`: suggestion, target, T03 `REGENERATE` assessment, T04 lineage, baseline case version,
  release receipt가 모두 있고 T05 source job/root/context/result와 일치한다. case overwrite는 0이어야 한다.
- `NO_CHANGE`: T05 materialization batch가 동등 proposal을 no-op으로 확정한 경우다. suggestion/case
  mutation을 주장하면 provenance failure다.
- `NONE`: 미적용 후보 설명용이다. release profile에서는 materialization 완료를 요구하는 분류가
  `NONE`이면 실패한다.

`APPLY_*`가 suggestion authority를 주장하거나 `SUGGESTION`이 apply authority를 주장하는 partial shape,
cross-workspace/cross-root ID, live row에서 재구성한 digest, missing/unknown ID는 모두 fail-closed다.

## 3. 두 dataset 계층

### 3.1 `SYNTHETIC_FIXTURE`

- `synthetic=true`, raw source text와 production data가 없다.
- deterministic fake result와 hash-only evidence를 사용한다.
- provider/Slack credential, network, live database 없이 schema와 evaluator 알고리즘을 회귀한다.
- `CANDIDATE` gate가 PASS여도 report는 반드시
  `releaseCertified=false`, `status=NOT_CERTIFIED_SYNTHETIC`이다.
- `RELEASE` profile로 요청하면 유효한 입력을 평가한 quality/eligibility 실패(exit `1`)다. 합성이라는
  이유로 malformed input(exit `2`)으로 바꾸지 않는다.

Repository fixture는 다음 두 파일이다.

- `evals/fixtures/automatic-generation-quality/synthetic-input-v1.json`
- `evals/fixtures/automatic-generation-quality/synthetic-report-v1.json`

Fixture는 1 root, 1 job/result, 5 cases, 5 steps, 10 evidence references의 작은 contract sample이다.
운영 최소 규모를 충족하지 않으며 그 사실이 synthetic certification 거부 근거다.

### 3.2 `OPERATIONAL_GOLDEN`

운영 golden은 실제 승인된 generation runtime에서 캡처한 non-synthetic hash-only snapshot이다.
release eligibility 최소값은 다음과 같다.

| 항목 | 최소값 |
|---|---:|
| generation roots | 32 |
| selected generation jobs | 32 |
| generation results | 32 |
| generated cases | 160 |
| generated steps | 160 |
| evidence references | 320 |

모든 result는 현재 output `1.0.0`의 5~10 case 정책을 그대로 지킨다. 한 case에 step이 하나 이상이므로
최소 coverage denominator는 320이다. 선언 count는 실제 hierarchy count와 정확히 일치해야 하며 더 큰
숫자를 선언해 최소값을 우회하면 `DATASET_COUNT_MISMATCH`다.

각 root는 정확히 하나의 primary classification을 가진다. 운영 golden은 아래 16분류를 각각 최소 2
root 포함하고, 선언 distribution은 실제 root label histogram과 같아야 한다.

1. `GROUNDED_ACTIVE`
2. `SUPERSEDED_TRAP`
3. `RETRACTED_TRAP`
4. `DELETED_TRAP`
5. `REJECTED_TRAP`
6. `UNAVAILABLE_TRAP`
7. `UNRESOLVED_CONFLICT`
8. `DECISION_CANDIDATE`
9. `EXACT_DUPLICATE_TRAP`
10. `SEMANTIC_DUPLICATE_TRAP`
11. `CATEGORY_COLLISION_TRAP`
12. `PARENT_COLLISION_TRAP`
13. `CASE_ORDERING`
14. `BLANK_OR_INVALID_RESULT`
15. `APPLY_PROVENANCE`
16. `SUGGESTION_PROVENANCE`

Trap label은 결함이 결과에 있어야 한다는 뜻이 아니다. 승인 label이 위험 후보를 제공하고, 생성 결과가
그 후보를 사용하지 않거나 안전하게 reuse/suggestion 처리했음을 평가한다. Label을 결과를 본 뒤
변경하는 것은 금지한다.

### 3.3 PostgreSQL operational candidate export

운영 원장에서 곧바로 `REVIEWED`/release snapshot을 추론하지 않는다. 구현된 첫 slice는 explicit root
selection의 completed automatic APPLY만 `REPEATABLE READ, READ ONLY`로 읽어 hash-only
`CANDIDATE + UNREVIEWED + liveGate.NOT_RUN` input을 만든다. Sealed Generation Input의 existing case/category
inventory와 apply audit semantic seal이 재현되지 않거나 parent decision이 complete inventory가 없는
`CREATE_NEW`이면 fail-closed한다. 세부 authority mapping, redaction, CLI와 후속 완료 조건은
[PostgreSQL quality candidate export](./automatic-generation-quality-candidate-export.md)가 고정한다.

## 4. label review, provenance와 fingerprint

Operational golden은 `REVIEWED`이고 다음 metadata가 모두 있어야 한다.

- reviewer group 또는 두 사람 검토를 나타내는 `reviewedBy`;
- UTC `reviewedAt`;
- immutable `labelSetId`;
- exact `reviewProtocolVersion`;
- 운영 승인 시스템의 opaque `approvalId`.

분류, evidence coverage target, source lifecycle-at-seal, expected duplicate/collision relation과
materialization mode가 review 대상이다. 생성 결과를 만든 사람이나 agent 단독 self-review는 운영 승인으로
사용하지 않는다. `UNREVIEWED`, `REJECTED`, missing reviewer, stale approval은 quality metric과 무관하게
release failure다.

Fingerprint는 UTF-8 RFC 8785 canonical JSON의 lower-case SHA-256이며 `sha256:` prefix를 사용한다.

- `datasetFingerprint`: label, distribution, review metadata, reference inventory의 fingerprint.
- `sourceSnapshotFingerprint`: 캡처한 sealed source/context authority의 fingerprint.
- `snapshotFingerprint`: version pins + generation hierarchy + reference inventory + dataset fingerprint.
- report artifact fingerprint는 구현 evaluator가 JSON report를 쓴 뒤 별도 artifact manifest에 기록한다.

선언 fingerprint와 재계산 값이 다르면 `FINGERPRINT_MISMATCH`; 자동 보정하지 않는다. Live attestation의
`capturedSnapshotFingerprint`도 평가 input의 `snapshotFingerprint`와 byte-for-byte 같아야 한다.

## 5. exact version pins

Release input은 다음 pin을 전부 non-blank exact value와 digest로 가진다.

- generation policy version/digest;
- prompt version/digest;
- provider ID, model ID, provider-reported model revision;
- embedding provider/model/revision/dimension;
- semantic duplicate algorithm version과 threshold;
- exact duplicate algorithm version;
- generation input/output, suggestion, quality-gate JSON Schema version;
- ontology schema version.

`latest`, `unknown`, `unconfigured`, `unpinned`, version range와 implicit default는 금지한다. T08은 이 pin을
현재 snapshot의 identity와 재현성에 사용한다. Pin을 바꾸었을 때 이전/새 baseline을 비교하고 승인하는
절차는 T09다. T08 evaluator가 버전 변경을 자동 허용하거나 threshold를 완화해서는 안 된다.

## 6. evidence coverage 100%의 정확한 분모

### 6.1 denominator

모든 selected result에 캡처된 각 generated case와 그 case의 각 generated step을 한 번씩 센다.

```text
evidenceCoverageDenominator = generatedCaseCount + generatedStepCount
```

Case 단위는 summary, case expected result와 generation reason의 grounded claim 묶음이다. Step 단위는
그 sequence의 precondition/action/expected result 묶음이다. 같은 evidence reference를 여러 번 쓰거나
한 단위에 여러 reference가 있어도 numerator는 단위당 최대 1이다. 누락 result로 denominator가 0이면
coverage rate는 0이며 invalid-result check도 실패한다.

### 6.2 valid reference

한 reference가 coverage를 제공하려면 다음을 모두 충족해야 한다.

1. `coverageTarget`이 정확한 case 또는 존재하는 step sequence를 가리킨다.
2. `contextEntryId`가 그 generation root의 immutable context seal member다.
3. entry와 reference의 knowledge node ID가 같다.
4. entry source와 reference source의 type/entity/revision/content hash가 모두 같다.
5. `citationSourceRevisionId`가 그 exact sealed source revision과 같다.
6. citation excerpt hash가 캡처된 evidence excerpt hash와 일치한다.
7. context digest, job context digest, materialization context digest가 같다.
8. 아래 lifecycle-at-seal rule에서 ACTIVE이고 권위가 확정됐다.

유효 reference가 하나 이상인 unit만 numerator에 1을 더한다.

```text
evidenceCoverage = numerator / denominator
release threshold = exactly 1.0
```

## 7. sealed source time의 lifecycle와 폐기 지식 오사용 0

Lifecycle 판정 시각은 evaluation time이나 현재 DB 시간이 아니라 root의 `sealedSourceTime`이다. Context
entry는 `validFrom <= sealedSourceTime < validTo`를 만족해야 하며 `validTo=null`은 open interval이다.
`contextSeal.sealedAt`, job seal과 source version 관계가 모순되면 provenance failure다.

Coverage 가능한 권위는 다음 전부를 만족한다.

```text
lifecycleStatusAtSeal = ACTIVE
availableAtSeal = true
conflictState = NONE
decisionState = ACCEPTED or NOT_APPLICABLE
```

다음 status를 참조한 evidence reference 하나당 해당 misuse counter를 1 증가시킨다.

- `SUPERSEDED`
- `RETRACTED`
- `DELETED`
- `REJECTED`
- `UNAVAILABLE`

이 합이 `retiredKnowledgeMisuse.total`이고 threshold는 정확히 0이다. 폐기 지식을 ACTIVE successor로
추측하거나 현재 source를 읽어 바꾸지 않는다. `UNAVAILABLE`은 네트워크 장애뿐 아니라 required historical
revision, permission 또는 source record를 snapshot에서 증명할 수 없는 경우도 포함한다.

`conflictState=UNRESOLVED` 또는 `decisionState=CANDIDATE` reference는 별도
`unresolvedAuthorityCount`에 reference당 1을 더하고 coverage를 제공하지 않는다. Open question으로
표현됐다는 이유만으로 authoritative evidence가 되지 않는다. Threshold는 0이다.

## 8. duplicate 0의 정확한 정의

### 8.1 exact key

T07의 canonical exact-content algorithm과 pin을 사용한다. 의미 필드는 normalized summary, ordered
precondition/action steps, case-level expected result다. 한 result 또는 전체 evaluated generated set에서
같은 exact key가 `n`개면 deterministic first를 제외한 `n-1`을 센다.

`APPLY_CREATED` case가 sealed-time ACTIVE existing case와 같은 exact key이면 generated case당 1을 추가한다.
`APPLY_REUSED`가 그 exact existing case를 명시적으로 reuse하고 no-write receipt가 맞으면 collision이
아니다.

### 8.2 semantic cluster

Pinned embedding model/revision/dimension과 pinned threshold를 사용해 reviewer-approved deterministic
cluster ID를 snapshot에 봉인한다. 같은 cluster에 generated case `n`개면 `n-1`을 센다.
`APPLY_CREATED`가 ACTIVE existing case cluster와 충돌하면 generated case당 1을 추가한다.
`APPLY_REUSED` 또는 same-lineage T05 `SUGGESTION`은 provenance가 맞을 때만 안전 처리다. Cluster가 missing,
unknown model revision 또는 dimension mismatch면 0으로 간주하지 않고 fail-closed provenance/eligibility
failure다.

### 8.3 category와 parent collision

- `categoryDecision=CREATE_NEW`인데 sealed reference inventory에 같은 canonical category key의 ACTIVE
  category가 있으면 case당 category collision 1이다.
- `parentDecision=CREATE_NEW`인데 같은 canonical Slack source key/title mapping의 ACTIVE parent가 있으면
  case당 parent collision 1이다.
- `REUSE_EXISTING`은 exact pinned reference를 가리킬 때 collision이 아니다.

```text
duplicateCount = exactKey + semanticCluster + categoryCollision + parentCollision
release threshold = 0
```

한 case가 exact와 semantic 모두 충돌하면 둘 다 센다. All-or-nothing threshold가 0이므로 defect를
상쇄하거나 하나만 대표로 숨기지 않는다.

## 9. result validity, case/step ordering과 blank

Release 가능한 result는 다음을 모두 만족한다.

- selected job이 `AUTOMATIC/SUCCEEDED`이고 root/retry lineage가 연속이다.
- stored result가 존재하고 `validationStatus=VALID`다.
- input/output pair가 exact `1.0.0 -> 1.0.0` pin과 같다.
- job/result/materialization result digest가 같다.
- result case 수가 5~10이다.
- case array의 `caseIndex`가 정확히 `0..n-1`이다.
- 각 steps array의 `sequence`가 정확히 `1..n`이다.
- summary, case expected result, generation reason, step action과 expected result의 NFKC/trim 후 길이가
  1 이상이다. Optional precondition은 length 0을 허용하지만 digest는 반드시 있다.
- result/case/step/evidence reference ID가 평가 scope에서 유일하다.

Missing/unknown/invalid result, 빈 case 배열, blank 필드, digest mismatch는
`invalidOrBlankResultCount`; case/step 순서 결함은 `orderingFailureCount`; cross-record seal/ID mismatch는
`provenanceFailureCount`로 보고한다. 세 threshold 모두 0이다. JSON Schema shape가 invalid하면 평가 가능한
quality defect가 아니라 input error(exit `2`)다. Schema-valid snapshot 안의 captured product invalidity는
quality failure(exit `1`)다.

## 10. all-or-nothing gate와 fail-closed eligibility

Release PASS는 다음이 모두 true일 때만 가능하다.

| Check | threshold |
|---|---:|
| evidence coverage | `== 1.0` |
| retired knowledge misuse | `== 0` |
| duplicate total | `== 0` |
| unresolved authority | `== 0` |
| provenance failure | `== 0` |
| invalid/blank result | `== 0` |
| ordering failure | `== 0` |
| dataset count/distribution mismatch | `== 0` |
| missing/unknown/stale/unreviewed/degraded eligibility | `== 0` |

다음 상태는 모두 fail-closed release failure다.

- field, pin, fingerprint, seal, result, source revision 또는 live attestation이 missing/unknown;
- snapshot age가 `releaseCandidateAt - capturedAt > 604800`초(7일), 음수이거나 timestamp가 invalid;
- label set이 unreviewed/rejected, approval가 missing/stale;
- `capture.degraded=true`, offline replay/network/provider/Slack call counter가 계약과 다름;
- secrets/PII scan이 `CLEAN`이 아님;
- operational minimum/distribution 또는 actual count가 부족함;
- live gate가 `NOT_RUN|FAILED`, credentialed provider call이 관찰되지 않음, snapshot fingerprint가 다름.

`releaseCandidateAt`은 protected release run이 입력에 고정한 UTC 값이다. Evaluator wall clock을 쓰지 않으므로
같은 input replay는 같은 stale 판정을 낸다.

## 11. deterministic offline execution과 exit code

Production evaluator의 명령 계약은 다음이다.

```text
pnpm generation:quality:eval \
  --snapshot <approved-hash-only-input.json> \
  --expected-snapshot-sha256 <administrator-approved-sha256> \
  --profile candidate|release \
  --json-output <report.json> \
  --junit-output <report.junit.xml> \
  --markdown-output <report.md>
```

Release profile은 protected environment의 관리자 설정 또는 동등한 runner-owned signed manifest에서 온
snapshot path/SHA-256을 사용한다. `workflow_dispatch` caller가 path와 SHA를 함께 정할 수 없으며 trusted SHA가
없으면 exit `2`다. 세 output path는 모두 필수이고 서로 달라야 하며 input을 overwrite할 수 없다.
Evaluator는 environment credential, service URL, provider/Slack token을 argument로 받지 않고 network를
열지 않는다.

| exit | 의미 |
|---:|---|
| `0` | 선택 profile gate PASS. Candidate synthetic PASS는 release certification이 아님. |
| `1` | schema-valid input을 평가했지만 quality 또는 release eligibility 실패. |
| `2` | JSON Schema, unknown field/version/profile, fingerprint recomputation, path/I/O/report write 또는 evaluator safety 오류. |

Unhandled exception도 success로 변환하지 않고 redacted stderr와 exit `2`로 닫는다.

## 12. machine report, JUnit과 Markdown

### JSON

Normative JSON report는 versioned schema의 `EvaluationReport`다. Dataset/snapshot fingerprint, exact version
pins, evaluated counts, 각 metric numerator/denominator/status counter, 모든 check, certification reason code,
artifact paths와 retention을 포함한다. Raw source/result text와 provider response는 포함하지 않는다.

### JUnit XML

- suite name: `automatic-generation-quality-gate/1.0.0`;
- test case: global eligibility 각 1개, 7개 quality threshold 각 1개, root별 provenance/result/order 각 1개,
  16 distribution category 각 1개;
- failure message에는 stable code, opaque root/job/case/reference ID와 count만 포함한다;
- skipped를 PASS 대용으로 쓰지 않는다. Release-required check가 미실행이면 failure다.

### Markdown

사람용 요약은 PASS/FAIL, synthetic/operational, release-certified 여부, fingerprint/pins, count/distribution,
100/0/0 metric과 stable defect code만 표시한다. Source excerpt, prompt, case text, user identity, secret,
credential, provider raw body/exception/endpoint/query는 표시하지 않는다.

## 13. secrets, PII와 retention

Evaluation artifact는 raw Slack/source/case/prompt text 대신 normalized length, content/excerpt hash와 opaque
ID만 가진다. Schema `additionalProperties=false`는 `apiToken`, authorization, endpoint, raw response 같은
임의 field를 거부한다. 운영 capture 절차는 artifact 작성 전 secrets scan과 PII scan을 실행하고 둘 다
`CLEAN`이어야 한다. Reviewer label에 사람 이름 대신 승인된 group/principal snapshot을 사용한다.

| Artifact | retention |
|---|---|
| synthetic input/report/JUnit/Markdown | immutable 30일 이상 |
| operational golden input, live attestation, JSON/JUnit/Markdown, approval | immutable 400일 이상 |
| legal/audit hold | 기간 제한 없이 hold 우선 |

원본 production text는 T08 artifact에 복사하지 않으며 source system retention을 따른다. Retention 만료 시
authority fingerprint/approval가 필요한 release record를 먼저 지우지 않는다.

## 14. CI와 release integration

아래 required target은 T08 production 구현으로 제공한다.

1. `scripts/evaluate_automatic_generation_quality.py` offline evaluator;
2. `tc_backend.generation.quality_gate` release seam;
3. root `package.json`의 `generation:quality:eval` command;
4. `.github/workflows/automatic-generation-quality-release.yml` protected workflow.

CI는 두 층이다.

- pull request/default quality job: synthetic fixture를 `CANDIDATE`로 실행하고 JSON/JUnit/Markdown을 30일
  보존한다. 결과는 release certification을 발급하지 않는다.
- protected release job: approved `OPERATIONAL_GOLDEN`과 별도 live attestation을 `RELEASE`로 offline
  평가한다. `releaseCertified=true` JSON report와 JUnit 0 failure 없이는 배포 job이 시작되지 않는다.

Live capture는 approved provider credential을 secret store에서 사용해 실제 provider call을 관찰하고
hash-only snapshot을 만든다. Evaluator에는 credential을 넘기지 않는다. T08 live gate는 generation 품질
provider attestation만 요구하며 Slack write를 요구하지 않는다. 실제 signed Slack root/reply와
end-to-end reply는 T10의 별도 live gate다.

## 15. T09~T11 소유권 경계

| Task | T08이 고정하는 것 | 후속 task 소유 |
|---|---|---|
| T09 version regression | 모든 policy/prompt/model/embedding/schema pin과 fingerprint를 report에 봉인 | pin 변경 diff, old/new golden 비교, baseline 승인·rollback, 호환 matrix. T09도 100/0/0을 완화할 수 없음. |
| T10 root→Slack E2E | sealed root/job/result/apply/suggestion provenance와 T08 live provider attestation | 실제 signed root→thread→ontology→generation→case/suggestion→Slack reply, credential/ack/re-delivery 전체 E2E |
| T11 human-modified suggestion E2E | suggestion snapshot의 T03/T04/T05 provenance와 automatic overwrite 0 | 사람 수정 후 apply가 아니라 suggestion만 생성·표시·적용/무시되는 전체 회귀와 UI/DB assertion |

T08 PASS는 T09~T11을 완료시키지 않는다. 반대로 T10/T11 E2E가 PASS여도 T08 100/0/0 report가 없으면
generation quality release gate는 닫힌 상태다.

## 16. 검증과 production GREEN

Green contract controls:

```text
pnpm --filter @tc/contracts exec vitest run \
  src/phase-e-automatic-generation-quality-gate-contracts.test.ts
pnpm test:schema
pnpm --filter @tc/contracts typecheck
```

Contract test는 strict Draft 2020-12 compile/export, deterministic fixture/report, 정확한 case+step coverage
분모, context membership/citation revision/lifecycle-at-seal, 다섯 retired status, unresolved authority,
exact/semantic/category/parent duplicate, case/step ordering, blank/invalid result, synthetic certification spoof,
운영 최소 규모/review/freshness/live attestation과 secret field rejection을 검증한다.

이전 focused production RED는 production seam 존재를 확인하는 focused GREEN으로 전환됐고 default pytest
discovery에는 계속 들어가지 않는다.

```text
node scripts/python.mjs -m pytest -q \
  scripts/tests/red_phase_e_automatic_generation_quality_gate.py
```

이 focused test는 production evaluator, backend release seam, package command와 protected release workflow가
모두 존재할 때 통과한다. Evaluator 동작·fail-closed 회귀는
`scripts/tests/test_evaluate_automatic_generation_quality.py`, deterministic semantic input seal은
`backend/tests/unit/test_generation_semantic_duplicates.py`가 검증한다.

## 17. production 구현 seam

구현 seam의 순서는 다음으로 고정한다.

1. pure snapshot parser + strict schema/fingerprint validator;
2. root/job/result/case/step/evidence hierarchy와 count/distribution validator;
3. lifecycle-at-seal/provenance/apply/suggestion validator;
4. evidence denominator와 duplicate/collision evaluator;
5. release eligibility/live-attestation verifier;
6. deterministic JSON/JUnit/Markdown renderer와 exit `0/1/2` CLI;
7. backend release decision seam과 immutable report reference;
8. synthetic candidate CI, 그 뒤 protected operational release workflow.

빈 symbol, always-PASS report, synthetic report에 `releaseCertified=true`를 쓰는 branch, live DB/provider fallback,
warning-only threshold 또는 CI `continue-on-error`는 구현으로 인정하지 않는다.
