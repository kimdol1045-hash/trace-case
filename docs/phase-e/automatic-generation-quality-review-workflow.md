# Phase E automatic-generation quality review workflow

- 기준일: 2026-09-01 (Asia/Seoul)
- 구현 범위: T08 candidate import, append-only reviewer decision, two-person approval, reviewed candidate export
- 인증 경계: **`REVIEWED_NOT_RELEASE_CERTIFIED`**
- live/provider/actual-sample 상태: **OPEN**

## 목적과 비목적

이 workflow는 PostgreSQL quality candidate exporter의 private T08 snapshot을 서버 안에서 검증한 뒤
E4 `review-candidate-envelope/1.0.0`의 `ACTUAL_MINIMIZED` revision-one envelope를 만들어 검토 원장에
들인다. 두 명의 서로 다른 설치된 principal이 같은 immutable envelope에 `APPROVE`를 남기면
reviewed export 2.0.0을 만들 수 있다. 이 결과도 attestation은 `NOT_RUN`, certification은
`REVIEWED_NOT_RELEASE_CERTIFIED`이므로 release 인증이 아니다.

다음은 이 workflow가 주장하지 않는 범위다.

- 실제 32-root/16-classification corpus의 품질 충족
- credentialed generation provider 호출 또는 staging/production live attestation
- `profile=RELEASE`, `releaseCertified=true`, T09 production activation
- 일반 사용자 로그인·세션·역할 시스템

## 권위와 secret 설정

일반 로그인 대신 이 좁은 workflow에만 reviewer token을 둔다.

```text
TC_QUALITY_REVIEWER_PRINCIPALS=["quality-principal-alpha","quality-principal-beta"]
TC_QUALITY_REVIEWER_TOKEN_HASHES=["sha256:<alpha>","sha256:<beta>"]
TC_QUALITY_REVIEW_SOURCE_SEAL_KEY=<separate secret, at least 32 characters>
TC_QUALITY_REVIEW_SOURCE_SEAL_KEY_ID=quality-review-source-v1
```

raw token은 DB·response에 저장하지 않는다. runtime secret 설정에는 token 자체가 아니라 hash만 둔다. mutation request가
`X-Quality-Reviewer-Token`으로 보낸 token을 서버가 SHA-256으로 계산한 뒤 모든 configured hash와
constant-time 비교하고 opaque principal로 매핑한다. `X-Actor-Label`과
`X-External-Actor-Id`는 reviewer authority가 아니다. token 누락·오류는 모두 동일한 `401
QUALITY_REVIEWER_UNAUTHORIZED`이며 token은 error details에 들어가지 않는다.

Production startup은 서로 다른 principal/hash 두 쌍과 source-seal key가 없으면 fail closed한다.
Local process는 미설정 상태로 기동할 수 있지만 review route는 `503`으로 닫힌다.

Migration은 token을 포함하지 않으며 `generation_quality_reviewer_authorities`에는 principal, credential
hash, recomputable authority digest만 admin-only `tc_install_generation_quality_reviewer` 함수로 설치한다.
이 table은 insert guard 뒤 UPDATE/DELETE가 금지된다. 결정 쓰기는 `NOLOGIN`, `NOINHERIT`
`tc_quality_review_writer` role에 table DML을 주지 않고 `tc_record_generation_quality_decision` 실행권만
준다. 함수는 credential hash와 설치 principal, request/decision digest, candidate fence, terminal 상태를
다시 검증하고 정확히 두 distinct approval일 때만 approval을 만든다. Application의 constant-time token
mapping은 이 DB 검증을 대체하지 않는다.

## Import 경계

Operator/internal import는 다음 값을 명시한다.

- raw root job UUID 목록: 이 request에서만 허용하며 response에는 넣지 않는다.
- capture environment와 세 개의 relative report output 경로
- request body와 같은 `Idempotency-Key`

서버는 `REPEATABLE READ` transaction에서 import를 직렬화하고 root가 singleton workspace 소유의
실제 automatic root인지 확인한 뒤 exporter를 실행한다. Client가 snapshot, legacy fingerprint, alias를
제출하는 계약은 없고 unknown field로 거부한다. 서버는 private quality snapshot에서 aggregate count,
고정 순서 16-class distribution, capture bucket/revision/time, `NOT_RUN`/false 경계만 projection해 E4
envelope를 만든다. Root set과 전체 private snapshot은 서로 다른 field domain의 HMAC seal로만 공개된다.
Revision-one envelope의 fingerprint와 source seal을 import·read·decision·reviewed-export마다 재검증한다.

같은 idempotency key와 같은 request는 동일 candidate를 반환하면서 source fence를 다시 확인한다.
같은 key의 다른 request와 다른 key의 동일 candidate는 각각 conflict다. candidate와 private root mapping은
모두 UPDATE/DELETE trigger로 immutable하다.

## 원장과 상태

다섯 table은 다음 책임만 가진다.

| table | 공개 여부 | 책임 |
|---|---|---|
| `generation_quality_candidates` | E4 safe projection만 API에 공개 | private snapshot과 verified envelope를 immutable하게 결합 |
| `generation_quality_candidate_roots` | private | candidate와 raw root UUID의 workspace-guarded mapping |
| `generation_quality_reviewer_authorities` | private | 설치된 principal·credential hash·recomputable authority digest |
| `generation_quality_review_decisions` | keyed seal/fingerprint만 공개 | principal당 최대 한 개의 append-only `APPROVE|REJECT` |
| `generation_quality_approvals` | sanitized fingerprint만 공개 | 서로 다른 두 authorize된 principal로부터 자동 생성되는 단일 approval |

저장된 mutable status column은 없다. 조회 상태는 원장에서 다음과 같이 계산한다.

```text
no decisions                -> UNREVIEWED
one APPROVE                 -> PENDING_APPROVAL (1/2)
two distinct APPROVE        -> APPROVED (2/2, one approval row)
any REJECT                  -> REJECTED (terminal)
```

같은 principal은 두 slot을 채울 수 없다. `REJECT`와 생성된 approval은 terminal이다. Security-definer
함수가 candidate row를 잠그므로 두 reviewer가 동시에 승인해도 두 decision과 한 approval만 commit된다.
DB trigger는 설치되지 않은 principal, recomputation과 다른 authority/request/decision/approval digest,
rootless review, candidate/workspace mismatch, cross-candidate decision, principal mismatch, third approval,
terminal 후 insert, 모든 ledger UPDATE/DELETE를 별도로 거부한다. Writer role의 direct DML은 privilege
boundary에서 거부된다.

Decision 전에는 stored artifact/fingerprint/root HMAC을 재계산하고 같은 private roots로 exporter를 다시
실행한다. source drift, stored tamper 또는 caller의 stale snapshot fingerprint가 있으면 decision은 commit되지
않는다.

## API

기본 경로는 `/api/generation-quality/review-candidates`다.

| method | path | 용도 |
|---|---|---|
| `POST` | `/` | operator candidate import; roots는 echo하지 않음 |
| `GET` | `/` | E4 candidate key 기반 list/cursor/filter |
| `GET` | `/{candidateId}` | safe evidence, count, classification, digest, progress, ledger |
| `POST` | `/{candidateId}/decisions` | token-authenticated approve/reject |
| `GET` | `/{candidateId}/reviewed-snapshot` | approved candidate의 deterministic reviewed export |

List/detail은 E4 candidate key, aggregate count/classification, capture bucket/revision, truthful E4 privacy
metadata, sanitized payload/artifact fingerprint, domain-separated source/reviewer seal, progress만 반환한다.
Raw root/source/workspace/Slack/channel/user UUID, unkeyed alias, legacy dataset/source/snapshot hash, report
path, provider endpoint, permalink/URL, source text/excerpt, opaque internal ID, principal ID, token은 반환하지
않는다. Reject reason은 자유 텍스트가 아니라 고정 reason code다.

## Reviewed export와 release fence

Reviewed export도 roots를 서버에서 다시 만들고 stored revision-one envelope와 source seals를 exact
비교한다. 2.0.0 export는 verified `baseEnvelope`와 별도 `review` object만 포함한다. Review에는 keyed
reviewer seals와 sanitized decision/authority/approval fingerprints만 있고 principal, decision row ID,
approval ID나 private snapshot은 없다. `attestation.status=NOT_RUN`, certification의 `eligible=false`,
`releaseCertified=false`와 실제 sample/provider gate limitation 세 값이 필수다. Legacy reviewed snapshot
1.0.0 shape는 runtime/OpenAPI 2.0.0 계약에서 허용하지 않는다.

## Web 검토 workspace

`/generation/quality-review`는 production에서 lazy route로 로드하며 `자동 생성 실패` 메뉴 바로 옆에 둔다.
화면은 generated API client의 safe candidate DTO만 사용하고 raw snapshot JSON을 렌더링하지 않는다.

- 상단에는 `CANDIDATE / UNREVIEWED` 또는 `CANDIDATE / REVIEWED_NOT_RELEASE_CERTIFIED` 경계를 항상 표시한다.
- 검토 상태·진행, 16개 classification, `COMPLETE` materialization, E4 candidate key 검색은 서버 query로 전달하며 cursor 기반 이전·다음 페이지와 수동 새로고침을 제공한다.
- 첫 항목을 자동 선택하지 않는다. 데스크톱은 master/detail, 768px 이하에서는 명시적인 목록/상세 전환을 사용한다.
- 상세는 E4 fingerprint와 capture revision, privacy scan, source seals, root/case/step/evidence aggregate count, classification·duplicate·materialization proof와 append-only decision/approval fingerprint만 표시한다.
- reviewer token은 password input으로 받아 현재 React tree의 메모리에만 둔다. `localStorage`와 `sessionStorage`를 사용하지 않으며 token이 없거나 후보가 terminal이면 mutation을 비활성화한다.
- 승인·거절 확인 dialog는 snapshot fingerprint를 다시 표시한다. 거절은 고정 reason code가 필수이고, `401`은 token 재입력 상태로 전환하며 `409`는 mutation을 중단하고 최신 후보를 새로고침한다.
- 같은 reviewer의 두 번째 결정은 서버가 거부한다. 서로 다른 reviewer의 첫 승인과 둘째 승인은 각각 `1/2`, `2/2`로 보이고 거절은 terminal이다.

Dialog는 initial focus, Tab focus trap, Escape, focus restore와 live mutation status를 제공한다. 품질 검토 workspace의 모든 select는 desktop과 mobile 모두 실제 높이 44px 이상이며, 모바일 dialog는 full-screen이고 입력 font는 16px, sticky action에는 safe-area inset을 적용한다.

## Code-complete evidence와 남은 live blocker

Schema/API/generated-client/unit/disposable-PostgreSQL 검증은 legacy/unknown-field rejection, token spoof/missing,
idempotent replay/conflict, 1/2 progress, same-principal rejection, concurrent two-person one-row approval,
terminal reject, E4 tamper/source-drift fences, writer-role direct DML denial, authority immutability,
recomputed decision/approval integrity, deterministic reviewed export와 no-release claim을 다룬다.
Repository에는 실제 승인된 operational fixture를 추가하지 않았다.

남은 live blocker는 실제 provider/sample data다. 실제 32 root와 16분류별 최소 표본을 합의된 reviewer가
검토하고 credentialed provider live gate가 같은 reviewed snapshot에 결합되기 전에는 Phase E의 “실제 일감
sample 품질” 체크를 닫지 않는다.
