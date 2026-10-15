# Phase E PostgreSQL quality candidate snapshot export

- 기준일: 2026-09-01 (Asia/Seoul)
- 상태: **E4-wrapped APPLY candidate export 구현 / actual review·live release gate 미실행**
- 공개 출력 계약: `review-candidate-envelope/1.0.0` revision one
- data class: 항상 `ACTUAL_MINIMIZED`; review `UNREVIEWED`; attestation `NOT_RUN`

## 목적과 경계

`scripts/export_automatic_generation_quality_snapshot.py`는 PostgreSQL authoritative ledger의
Slack → knowledge → automatic generation → apply → QA version 계보를 한 `REPEATABLE READ, READ ONLY`
transaction에서 읽어 private T08 evaluator input을 만든 뒤 strict aggregate projection만 E4 envelope에
넣는다. Private evaluator input은 caller memory에서 source seal을 계산할 때만 사용하고 파일에는 쓰지
않는다. Provider, Slack, HTTP client를 import하거나
호출하지 않으며 root `.env`를 읽지 않는다. DB URL은 명시된 process environment variable
(`TC_DATABASE_URL` 기본값)만 사용하고 stderr에는 URL이나 예외 본문을 출력하지 않는다.

Private T08 input schema의 dataset kind는 `SYNTHETIC_FIXTURE|OPERATIONAL_GOLDEN` 두 값뿐이다. 따라서
실제 운영 row를 사용한 private candidate는 vocabulary상 `OPERATIONAL_GOLDEN, synthetic=false`지만,
이는 승인된 golden이라는 주장이 아니다. Public E4 envelope는 다음 값을 강제해 review·release
authority와 분리한다.

```text
dataClass=ACTUAL_MINIMIZED
review.status=UNREVIEWED
attestation.status=NOT_RUN
certification.status=NOT_CERTIFIED
certification.eligible=false
```

T09 `generation_version_sets.reviewed`, baseline approval 또는 activation을 T08 dataset review로 승격하지
않는다. T08 전용 label review/approval artifact와 live attestation은 별도 protected workflow 입력이어야
한다.

## 현재 지원하는 최소 slice

명시적으로 선택한 root job ID만 포함하며 다음 조건을 전부 만족하는 `APPLY_CREATED|APPLY_REUSED`
automatic root만 지원한다.

- retry attempt가 1부터 연속이고 마지막 하나만 `SUCCEEDED`다.
- 모든 attempt의 sealed input/context/source version과 T09 version-set pin이 같다.
- sealed Generation Input 1.0.0 digest, context count/digest/time, stored result digest가 재계산과 같다.
- generation context의 knowledge evidence/source record/node snapshot join이 exact 일치한다.
- apply receipt/outcome, apply audit의 semantic inventory seal, QA case version/step/source metadata가 exact
  일치한다.
- sealed input의 전체 `existingCases`와 `categoryCandidates`로 exact/semantic/reference inventory를
  재구성할 수 있다.
- parent는 apply가 `REUSE_EXISTING`으로 확정한 경우만 포함한다. 현재 원장은 CREATE 시점의 전체 parent
  candidate inventory를 별도 봉인하지 않으므로 `CREATE_NEW`는
  `PARENT_CREATE_INVENTORY_UNSEALED`로 fail-closed한다.

Suggestion/NO_CHANGE와 16개 reviewer trap classification은 이 slice에서 추론하지 않는다. 모든 exported
root의 label은 `GROUNDED_ACTIVE`이며, suggestion authority와 reviewer-approved classification corpus는
후속 slice다.

## Private reconstruction and public envelope

- Private reconstruction uses legacy hash-only aliases and source-derived SHA values only to run the T08
  validator and rebuild exact source state. They are never copied into the public envelope.
- Slack body, knowledge label/properties content, generation result text, QA summary/action/expected result는
  출력하지 않는다. 평가에 필요한 text는 NFKC/exact normalization 후 `normalizedLength`와 SHA-256만
  기록한다.
- Public projection contains only aggregate counts, the fixed-order 16-class count array, capture
  bucket/revision/time, materialization, and explicit non-certification state. The E4 scanner runs in
  `REJECT` mode, so `CLEAN` and `foundationPolicyVersion` describe the envelope actually built.
- Raw root UUIDs and the complete private T08 snapshot are represented only by separate
  `operational-root-set` and `private-t08-snapshot` HMAC-SHA-256 field seals. Public SHA-256 values are
  limited to E4 candidate/payload/artifact fingerprints computed after sanitization.
- semantic cluster는 apply와 동일한 sealed local vector policy로 generated+sealed existing inventory를
  다시 계산하고, 선택한 모든 root 전체에서 threshold-connected component를 deterministic하게 봉인한다.
- captured time은 wall clock이 아니라 선택 row의 최대 `completed_at|applied_at`이고, ID/order/count/
  distribution/fingerprint와 JSON key order가 고정된다. 같은 DB snapshot과 같은 argument는 byte-identical
  output을 만든다.
- report output은 relative non-traversing path만 허용하지만 public envelope에는 넣지 않는다. URL,
  email, token/key, raw identifier, source hash 같은 sensitive projection은 E4 scanner가 쓰기 전에 거부한다.

## 실행과 기존 gate 연결

```bash
TC_DATABASE_URL='<injected PostgreSQL URL>' \
TC_QUALITY_REVIEW_SOURCE_SEAL_KEY='<injected 32-byte-minimum secret>' \
pnpm generation:quality:export-candidate \
  --root-job-id generation_job_<uuid> \
  --capture-environment staging \
  --snapshot-output artifacts/automatic-generation-quality.candidate.json

# The resulting file is an E4 review envelope, not an EvaluationInput and not a certificate.
```

Exporter는 생성 직후 E4 candidate/payload/artifact fingerprint와 두 source seal을 재검증한다. 기존 T08
evaluator/verifier가 필요하면 권한 있는 server-side workflow가 private snapshot을 직접 넘겨야 하며 public
E4 JSON을 legacy EvaluationInput처럼 사용하면 안 된다.

## 검증과 남은 live gate

Focused tests는 PostgreSQL `transaction_read_only=on`, 같은 transaction 재구성, E4 fingerprint/source-seal
검증, malformed source state fail-close와 public JSON에서 root/source IDs, legacy hashes/aliases, raw
Slack/test content, email/token/key/URL/report path 비노출을 검증한다. CLI tests는 `REPEATABLE READ`/`SET
TRANSACTION READ ONLY`/rollback, exclusive output, DB URL/source-key 누락 redaction과 unsafe report path
거부를 검증한다. 이 검증은 synthetic repository fixture에 한정된다.

Release 완료 조건은 여전히 다음과 같다.

1. 최소 32 root와 16분류별 최소 2 root의 reviewer-approved T08 corpus;
2. immutable label set, two-person/group review와 T08 approval artifact;
3. CREATE parent와 suggestion slice에 필요한 complete sealed inventory authority;
4. credentialed provider call을 관찰한 live attestation bundle;
5. 같은 snapshot fingerprint, freshness, protected runner와 400일 retention 증거.

이 항목 전에는 candidate report가 PASS여도 release certification이 아니다.
