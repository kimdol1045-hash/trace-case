# TC 통합 운영 runbook

- 문서 역할: 설치부터 복구까지의 **순서·중단·증거 권위**
- 상세 명령 권위: 이 문서가 연결하는 subsystem runbook과 Phase F rehearsal
- 대상: 승인된 사내/VPN 배포와 별도 staging/disposable rehearsal
- 상태: `FOUNDATION_READY / OPERATOR_HANDOFF_REQUIRED`

이 문서에서 작업 순서와 중단 조건을 선택하고, 링크된 상세 runbook에서 정확한 명령과
입력 제한을 확인한다. 링크된 명령을 이 문서에 복사해 고치지 않는다. 배포 controller,
secret manager, Alertmanager receiver처럼 저장소가 소유하지 않는 명령은 운영자가 승인된
내부 runbook 주소와 owner를 인계 증거에 기록해야 한다. 그 참조가 없으면 추측한
`systemctl`, `kubectl`, provider CLI 또는 SQL로 대체하지 않고 중단한다.

프로덕션 데이터에 Phase F rehearsal을 실행하지 않는다. 해당 wrapper는 이름, host,
database comment, 확인문을 검증한 `disposable` 또는 `staging-copy`만 허용하며 기본값은
preview다. 이 문서와 validator가 통과해도 live Slack, R2, 모델, backup/restore, alert
delivery 또는 operator 인계를 증명하지 않는다. 아래 인계 증거가 검토되기 전에는
`PF-SF1-T12`를 완료로 표시하지 않는다.

<!-- ops-section:bootstrap -->
## OPS-01 — 설치와 bootstrap

### 사전 조건

- Node.js 22+, pnpm 10+, Python 3.12+, Docker와 지원되는 PostgreSQL client 버전을
  확인한다.
- 작업 대상과 실행 환경(`local`, 승인된 staging copy, production)을 먼저 기록한다.
- production bootstrap은 배포 artifact digest, migration revision, 변경 승인자를 고정한다.

### 안전 명령·참조

개발 checkout의 정확한 설치·기동 명령은 [처음 한 번 준비](../../README.md#처음-한-번-준비)와
[Web + API + Worker 개발 모드](../../README.md#web--api--worker-개발-모드)를 따른다.
DB capability와 migration 사전 검증은 [backend startup 절차](../../backend/README.md#run-postgresql-migration-and-repository-tests)가
권위다. Production에서는 승인된 artifact를 설치하는 배포 controller의 내부 참조가 없으면
여기서 중단한다.

### 기대 증거

증거: runtime/tool 버전, artifact digest, Alembic head, 설치 명령 exit status와
`tc-backend-check`의 sanitized 성공 요약을 남긴다. 환경 파일, DB URL, token 또는 전체
process environment는 첨부하지 않는다.

### 롤백·중단 조건

중단: 지원 버전, artifact digest, 전용 target 또는 migration head가 하나라도 불명확하면
프로세스를 시작하지 않는다. 롤백은 새 artifact를 활성화하기 전의 controller release로만
수행하며, DB downgrade를 application rollback의 기본값으로 사용하지 않는다.

<!-- ops-section:configuration -->
## OPS-02 — 구성과 secret 취급

### 사전 조건

- production secret은 versioned secret manager reference와 runtime의 최소 read 권한으로
  준비하고, 비밀이 아닌 설정은 review 가능한 deployment config로 분리한다.
- Slack HTTP/Socket, R2/ClamAV, knowledge projection, generation, Slack notification은 서로
  독립된 bundle/switch임을 확인한다.
- 운영 증거에 secret value, presigned URL, provider body, 전체 환경 또는 credential-bearing
  URL을 남기지 않는 기록 위치를 정한다.

### 안전 명령·참조

변수 이름과 credential-free 기본값은 [backend environment example](../../backend/.env.example)을
참조하고, edge 주입·rotation 경계는 [Secret and rotation boundary](../nginx/README.md#secret-and-rotation-boundary)를
따른다. Slack은 [보안 경계와 준비값](../slack/README.md#1-보안-경계와-준비값), R2는
[Production gate](../object-storage/README.md#production-gate), generation은
[Enablement](../generation/README.md#enablement)를 각각 상세 권위로 사용한다.

### 기대 증거

증거: 설정 bundle revision, secret **version ID**, runtime service account, least-privilege
review 결과와 startup validation 결과만 기록한다. 실제 값과 `.env` 사본은 증거가 아니다.

### 롤백·중단 조건

중단: 불완전한 credential pair, URL userinfo/query credential, wildcard origin, production
secret의 shell-history 주입, 구·신 secret을 섞은 bundle이 보이면 배포하지 않는다. 롤백은
아직 유효한 직전 **전체 bundle version**으로만 하고 검증이 모호하면 관련 writer/ingress를
닫는다.

<!-- ops-section:lifecycle -->
## OPS-03 — 프로세스 시작·종료·재시작 순서

### 사전 조건

- deployment controller에서 API, worker, internal edge, Slack public edge를 독립적으로
  drain/scale/restart할 수 있어야 한다.
- PostgreSQL/pgvector, private R2/ClamAV, model/knowledge 보조 서비스의 owner와 상태를
  확인한다.
- 현재 queue/outbox age, active worker lease, provider outcome-unknown 수를 기준선으로
  기록한다.

### 안전 명령·참조

정상 시작 순서는 `(1) PostgreSQL과 필수 storage/scanner → (2) migration/capability check →
(3) API canary → (4) worker canary → (5) internal edge → (6) 선택된 Slack ingress →
(7) generation/notification opt-in`이다. Edge listener와 API startup 제한은
[Listener contract](../nginx/README.md#listener-contract)와
[Forwarded-address trust and application startup](../nginx/README.md#forwarded-address-trust-and-application-startup)을
따른다. 종료는 외부 ingress와 신규 writer를 먼저 닫고 worker를 drain한 뒤 위 순서의
역순으로 진행한다. 개발 모드의 공동 종료만 [Web + API + Worker 개발 모드](../../README.md#web--api--worker-개발-모드)의
`Ctrl-C` 계약을 사용한다.

### 기대 증거

증거: 각 단계의 controller rollout ID, 시작/종료 시각, `/api/health`, `/api/ready`, worker
poll timestamp, queue/outbox age의 전후 값을 남긴다. 재시작 뒤 old/new replica가 동시에
외부 write한 흔적이 없어야 한다.

### 롤백·중단 조건

중단: site-specific drain/restart 명령 참조가 없거나, lease가 줄지 않거나, DB가
not-ready이거나, 신규 replica의 readiness가 실패하면 다음 단계를 열지 않는다. 반복
restart로 DB/외부 의존성 장애를 가리지 말고 마지막 정상 release로 controller rollback한
뒤 ingress는 닫힌 상태를 유지한다.

<!-- ops-section:health -->
## OPS-04 — health, readiness와 배포 판정

### 사전 조건

- health/ready/metrics 경로는 internal listener에서만 접근하고 public Slack listener에는
  노출하지 않는다.
- 배포 전 liveness와 dependency readiness의 의미가 다른지 확인한다.
- worker마다 고유한 textfile collector 경로와 scrape target을 준비한다.

### 안전 명령·참조

판정 의미와 HTTP status는 [Liveness](../../docs/phase-f/sprint-f1-observability.md#liveness)와
[Readiness](../../docs/phase-f/sprint-f1-observability.md#readiness)를 그대로 사용한다.
Prometheus 설치와 검증은 [Deployment and validation](../../docs/phase-f/sprint-f1-observability.md#deployment-and-validation)을
따른다. `GET /api/health`의 `200`만으로 DB-backed traffic을 열지 않고 반드시
`GET /api/ready`와 worker poll freshness를 함께 본다.

### 기대 증거

증거: liveness HTTP status, readiness의 `ready|degraded|not_ready`와 고정 reason code,
`tc_database_up`, `tc_api_ready`, worker last-poll age를 time window와 함께 남긴다. 응답에
identifier, provider URL 또는 exception text가 없는지도 확인한다.

### 롤백·중단 조건

중단: `not_ready`, DB down, stale worker textfile, 예상 밖 dependency degradation이면 traffic
확대를 멈춘다. Optional dependency의 `degraded`에서는 core ledger만 유지하고 해당 기능을
격리하며, readiness가 안정될 때까지 rollout을 롤백하거나 pause한다.

<!-- ops-section:jobs -->
## OPS-05 — worker와 job 재처리

### 사전 조건

- 원본 job/outbox/schedule 상태, safe error code, attempt, lease expiry, root lineage와 현재
  idempotency receipt를 권한 있는 UI/API에서 확인한다.
- 외부 side effect의 commit 여부가 명확하고 provider outcome-unknown이 아님을 확인한다.
- 재처리할 한 개 resource와 기대 version을 고정하고 새 idempotency key를 한 operator
  action 동안 재사용한다.

### 안전 명령·참조

일반 DEAD/outbox 대응은 [TCBackgroundJobDead or TCOutboxDead](../../docs/phase-f/sprint-f1-observability.md#tcbackgroundjobdead-or-tcoutboxdead)를
따른다. Slack thread는 제품의 `POST /api/slack-work-items/{work_item_id}/sync-thread`, 생성 실패는
[API와 HTTP 계약](../../docs/phase-e/automatic-generation-retry-history-contract.md#14-api와-http-계약)의
조건부 retry만 사용한다. 저장소에 generic DEAD job을 재개하는 operator endpoint는 없다.
그 경우 DB status/attempt/lease를 직접 바꾸지 말고 owner가 검토한 bounded reprocessor가
추가될 때까지 중단한다.

### 기대 증거

증거: 기존 row가 보존된 상태에서 새 job/receipt ID, request digest, idempotent replay 여부,
attempt lineage, 처리 전후 queue age를 기록한다. 같은 key의 response-loss 재시도는 새 side
effect를 만들지 않아야 한다.

### 롤백·중단 조건

중단: permit만 있고 observation이 없거나, active lease가 남았거나, 기대 root/failed
job/attempt/version이 달라졌거나, 동일 key가 다른 payload에 쓰였으면 재처리하지 않는다.
이미 commit된 side effect를 삭제로 되돌리지 말고 append-only compensating/operator review
flow를 사용한다.

<!-- ops-section:slack -->
## OPS-06 — Slack ingress, notification과 rotation

### 사전 조건

- HTTP 또는 Socket 중 정확히 한 mode, test workspace, public channel allowlist, bot 참여,
  최소 scope를 확인한다.
- HTTP면 exact public POST path, TLS/DNS, signing clock과 default-deny edge를 준비한다.
- notification은 ingestion과 별도 switch이며 generation notification breaker 상태를 확인한다.

### 안전 명령·참조

설치와 mode별 bundle은 [App 생성과 channel 참여](../slack/README.md#2-app-생성과-channel-참여)와
[수신 mode](../slack/README.md#3-수신-mode), live 검증은
[allowlist와 live smoke](../slack/README.md#5-allowlist와-live-smoke), 한 credential씩 수행하는
rotation은 [Credential rotation](../slack/README.md#credential-rotation)을 따른다. Ambiguous
notification은 [자동 생성 reply 안전 운용](../slack/README.md#4-자동-생성-reply-안전-운용)의
marker reconciliation만 사용하고 blind repost하지 않는다.

### 기대 증거

증거: mode, manifest/scope revision, workspace·channel의 opaque ID, URL verification,
forged/stale request의 거부 status, 정상 event ID, 중복 delivery 후 원장 cardinality,
secret version과 revoke 시각을 기록한다. 메시지 본문과 raw request는 남기지 않는다.

### 롤백·중단 조건

중단: 예상 밖 channel, signature 실패, 두 mode 동시 활성, ACK deadline 위반 또는 write
결과 모호성이 생기면 receiver/publisher를 drain하고 public listener를 닫는다. 이전 secret이
유효할 때만 전체 bundle을 되돌리고, 그 외에는 allowlist를 비운 채 새 credential로 rotation한다.

<!-- ops-section:object-storage -->
## OPS-07 — R2 upload, download, quarantine와 복구

### 사전 조건

- private bucket, exact HTTPS Web origin, bucket-scoped token, reachable ClamAV, 50 MiB 이하의
  정책과 PostgreSQL/R2 recovery-point label을 확인한다.
- public/custom-domain access가 꺼져 있고 wildcard CORS가 없는지 검토한다.
- recovery는 isolated DB와 그 시점에 맞는 별도 recovery bucket에서만 수행한다.

### 안전 명령·참조

upload→quarantine→HEAD/byte 검증→scan→metadata commit 순서는
[Validation and quarantine policy](../object-storage/README.md#validation-and-quarantine-policy),
download/delete/orphan/integrity endpoint는
[Scheduled checks and thresholds](../object-storage/README.md#scheduled-checks-and-thresholds),
matched 복구 순서는 [Backup and recovery](../object-storage/README.md#backup-and-recovery)를
따른다. Phase F에서는 [Safety contract](../../docs/phase-f/sprint-f1-recovery-performance-readiness.md#safety-contract)의
preview/attestation을 통과한 검증 절차만 사용한다. 실제 R2 gate에는 strict `r2-verify`를 사용하고,
local disposable matched PostgreSQL+object recovery에는
[`object_storage_recovery.py`](../../scripts/phase-f/object_storage_recovery.py)의 `recover`와 `cleanup`을
각각 preview한 뒤 exact confirmation으로 실행한다. Local helper는 Compose loopback PostgreSQL,
MinIO, ClamAV와 생성된 `pf-sf1-t08-wave7` resource만 허용한다.

### 기대 증거

증거: bucket/config revision, private anonymous denial, upload session과 completion의 opaque ID,
size/MIME/SHA-256 일치, CLEAN verdict, proxy download digest, dry-run 전체 pagination,
bounded restore sample과 recovery-point label을 남긴다. Local disposable recovery evidence may
retain deterministic non-sensitive fixture object keys needed for the manifest binding; operator or
production evidence must not retain object keys. Signed URL, credential, token, database URL, or
object bytes are never retained.

Wave 7은 source/recovery DB와 bucket을 하나의 manifest/recovery point로 묶고 12개 local MinIO
object를 복구해 owner type/MIME class 전체, HEAD, full-byte SHA, 4-page API dry-run, exact provider /
bucket / privacy / scan policy를 통과했다. 이는 **LOCAL MINIO DISPOSABLE PASS**만 의미한다. Actual
Cloudflare R2 bucket/versioning/backup, separately hosted staging DB, operator handoff와 production
RTO/RPO는 `NOT_RUN`이며 `PF-SF1-T08`/Phase F checkbox는 열어 둔다. 상세 근거는
[wave 7 report](../../docs/phase-f/sprint-f1-wave7-verification.md)를 따른다.

### 롤백·중단 조건

중단: scanner outage면 객체를 quarantine에 두고, metadata mismatch면 unreadable로 격리하며,
pagination/clock/DB가 불명확하면 orphan 삭제를 멈춘다. Manifest tamper, duplicate/unexpected key,
non-empty recovery bucket, missing/corrupt/wrong MIME·size·SHA, incomplete page, cursor cycle, source /
recovery mismatch가 하나라도 있으면 cleanup과 read traffic을 중단한다. Bucket을 public으로
바꾸거나 wildcard CORS로 availability를 복구하지 않는다.

<!-- ops-section:generation -->
## OPS-08 — generation provider와 model enable/disable

### 사전 조건

- knowledge projection, credentialed generation, Slack notification 세 switch를 별도 승인한다.
- 승인된 provider/model ID, endpoint allowlist, single-call transport, 호출 budget, timeout,
  alert와 canary worker를 준비한다.
- 기존 `PROVIDER_OUTCOME_UNKNOWN`과 active execution/materialization schedule이 없는지 확인한다.

### 안전 명령·참조

기본 disable과 enable 순서는 [Pre-enable gate](../generation/README.md#pre-enable-gate), kill
switch·token rotation·ambiguous result는
[Disable, rotate, and recover](../generation/README.md#disable-rotate-and-recover)를 따른다.
모델 version rollback은 [T09 version-regression release](../generation/README.md#t09-version-regression-release)의
immutable version-set/digest만 선택한다. Outcome-unknown은
[TCGenerationProviderOutcomeUnknown](../../docs/phase-f/sprint-f1-observability.md#tcgenerationprovideroutcomeunknown)에
따라 신규 traffic을 먼저 끄고 operator review로 보낸다.

### 기대 증거

증거: provider/model의 safe ID, configuration revision, canary의 permit:request:observation
`1:1:1`, sanitized terminal code, budget, alert 발생/해소와 immutable model artifact digest를
기록한다. Endpoint, prompt, raw result와 auth header는 기록하지 않는다.

### 롤백·중단 조건

중단: permit 이후 observation이 없거나, budget/timeout이 승인과 다르거나, result validation이
실패하면 `TC_AUTOMATIC_GENERATION_ENABLED=false` 상태로 worker를 재시작하고 blind replay하지
않는다. Migration downgrade 전에는 generation worker와 schedule을 반드시 중지한다.

<!-- ops-section:knowledge -->
## OPS-09 — knowledge rebuild와 fallback

### 사전 조건

- PostgreSQL의 Slack revision/knowledge ledger가 원장이고 LightRAG/pgvector는 파생 검색
  계층임을 확인한다.
- staging-copy target attestation, Alembic head `20260902_0030`, fresh event/source ledger digest,
  고정 모델과 승인된 Ollama endpoint를 준비한다.
- 현재 active generation ID와 pointer version, exact immutable generation binding, 별도 AGE+pgvector
  auxiliary authority와 reviewed child bootstrap을 준비한다.
- API와 worker 두 process identity에 같은 authority ID, stable auxiliary-target digest, provider ID와
  connection attestation을 배포한다. Generation workspace와 provider contract/distribution/version/
  runtime-config digest는 controller가 등록한 active durable row만 선택하며 static
  `TC_LIGHT_RAG_WORKSPACE` 변경으로 cutover하지 않는다.
  `TC_LIGHT_RAG_POSTGRES_PASSWORD`는 approved secret manager에서 각각 주입한다. `.env`, shell
  history, argv, evidence bundle에 secret을 기록하지 않는다.
- rebuild 중 source write 정책과 advisory-lock owner를 합의한다.

### 안전 명령·참조

Projection preview/apply의 정확한 guarded 절차는
[Exact rehearsal commands](../../docs/phase-f/sprint-f1-recovery-performance-readiness.md#exact-rehearsal-commands),
full auxiliary corpus의 preview/read-only inspection과 provider 요구사항은
[PF-SF1-T09 full-reindex contract](../../docs/phase-f/pf-sf1-t09-lightrag-full-reindex.md)를 따르고,
process별 versioned pointer routing과 stale-result 폐기 규칙은
[PF-SF1-T09C runtime routing evidence](../../docs/phase-f/pf-sf1-t09c-lightrag-runtime-routing.md)를 따르며,
required opt-in settings와 child isolation/lifecycle은
[API·worker 공통 opt-in 설정](../knowledge/README.md#apiworker-공통-opt-in-설정)을 따르며,
장애 시 typed graph+pgvector fallback은
[Phase D LightRAG failover 계약](../knowledge/README.md#phase-d-lightrag-failover-계약)을 따른다.
Disposable process-kill rehearsal만 [Disposable 실제 process 단절 rehearsal](../knowledge/README.md#disposable-실제-process-단절-rehearsal)을
사용한다. 새 local full-flow 검증에는
[`lightrag_full_reindex_disposable.py`](../../scripts/phase-f/lightrag_full_reindex_disposable.py)를
preview → exact-confirm execute → evidence-bound cleanup preview → exact-confirm cleanup 순서로만
사용하고, 생성된 `pf-sf1-t09-wave8` resource 밖을 대상으로 바꾸지 않는다. Final 명령과 결과는
[wave 8 report](../../docs/phase-f/sprint-f1-wave8-verification.md)를 따른다. 이 helper를 staging/live
명령으로 전용하거나 production credential/provider를 주입하지 않는다.

### 기대 증거

증거: preview/apply의 event-order/source-ledger digest, authoritative before/after digest 동일성,
derived-table write scope, verified generation registration digest, activation/rollback transition
event ID와 committed pointer version, runtime before/after binding, row/status count, fallback reason,
bound와 recovery timing을 남긴다. Source event, row body, prompt, database URL 또는 endpoint
credential은 남기지 않는다.

Wave 8 local disposable evidence는 official storage initialization과 complete supported fixture-corpus
index, exact document/status/chunk/citation/vector/AGE digest, controller-produced generation
A → B → rollback A, 별도 API/worker resolver observation, stale-result fencing, failed activation/
rollback, production child kill과 auxiliary provider stop fallback, provider recovery, exact cleanup까지
증명한다. Staging evidence에는 별도로 secret-manager/bootstrap attestation, staging full corpus
checksum/pointer event, live-provider query/index/citation smoke, production-like load/alert/recovery와
operator handoff가 있어야 한다. 하나라도 없으면 해당 항목은 `NOT_RUN`으로 기록한다.

### 롤백·중단 조건

중단: config가 incomplete이거나 pinned provider/bootstrap/AGE attestation이 맞지 않으면 API와
worker startup을 중단한다. Digest가 stale이거나 Alembic head/target attestation이 다르거나 source
ledger가 변하면 apply하지 않는다. Unknown generation, stale expected pointer, target attestation
mismatch, activation/rollback failure는 append-only rejected evidence와 함께 중단한다. Runtime child
장애나 stale result는 active pointer/원장을 자동 롤백하지 않고 typed graph+pgvector fallback을
유지한다. 정상 shutdown은 child와 temporary directory를 bounded cleanup하지만 강제 종료 뒤 orphan은
PID/active binding을 확인한 operator가 정리한다. Wave 8 local sub-gate가 통과했어도 approved
bootstrap/secret-manager staging config와 live-provider staging reindex/cutover 증거가 없으므로
full-corpus 복구를 완료로 주장하지 않는다.

<!-- ops-section:postgresql -->
## OPS-10 — PostgreSQL backup, restore와 migration

### 사전 조건

- server와 호환되는 `psql`, `pg_dump`, `pg_restore`, 외부 encrypted dump 경로와 별도
  attested staging/disposable DB를 준비한다. Host client가 없을 때
  `deploy/compose.dev.yml`의 `postgres` service client를 쓰는 fallback은 loopback DB URL에만
  허용된다. Remote staging-copy는 host client가 없으면 fail-closed한다. Fallback은 `PG*` 값을
  `docker compose exec --env <name>`으로만 상속하며 credential·URL 값을 argv·report에 넣지 않는다.
- backup에는 같은 recovery-point의 private object inventory를 결합한다.
- migration rollback 전에 현재 head, 대상 revision의 데이터 blocker와 active writer/worker를
  확인한다.
- Wave 9 local rehearsal은 current `tc`를 source로 직접 변경하지 않는다. 한 exported
  `REPEATABLE READ, READ ONLY` snapshot에 fingerprint와 `pg_dump`를 고정하고, exact-comment empty
  loopback PostgreSQL 16 disposable copy만 downgrade/upgrade 대상으로 사용한다.

### 안전 명령·참조

Non-overwrite dump, archive validation, empty restore destination, no-clean restore의 정확한
preview/execute 명령은 [T07 preparation](../../docs/phase-f/sprint-f1-recovery-performance-readiness.md#t07--postgresql-backup-and-isolated-restore),
최종 코드로 재생성한 local authoritative 수치와 evidence hash는
[wave 6 retry6 verification](../../docs/phase-f/sprint-f1-wave6-verification.md#authoritative-retry6-results),
현재 one-step migration 왕복은 [T10 preparation](../../docs/phase-f/sprint-f1-recovery-performance-readiness.md#t10--current-migration-one-step-rollbackroll-forward)을
따른다. 일반 migration test가 disposable DB를 내렸다 올리는 위험은
[Migration recovery](../../backend/README.md#migration-recovery)에서 확인한다.

Wave 9의 strongest local sub-gate는
[`migration_roundtrip_disposable.py`](../../scripts/phase-f/migration_roundtrip_disposable.py)를
preview → exact-confirm rehearsal → cleanup preview → evidence-bound exact-confirm cleanup 순서로만
사용한다. 이 helper는 생성된 `pf-sf1-t10-wave9` 이름, loopback PostgreSQL 16, generated
non-default port, exact database comment와 initially empty target만 받으며 staging/live target이나
외부 credential을 지원하지 않는다. 재실행할 때는 완료된 run/evidence를 재사용하거나 덮어쓰지
말고 새 8자리 lowercase hexadecimal run ID, 새 port, 새 private `/private/tmp` dump/temp와 evidence
경로를 만든다. 검증된 final 명령, migration edge, file/canonical hashes와 cleanup proof는
[wave 9 report](../../docs/phase-f/sprint-f1-wave9-verification.md)를 따른다.

Local synthetic drill은 `postgres-seed` preview/execute로 empty attested source를 current head로
올리고 fixed-ID/fixed-time ledger를 적재한다. 실제 staging copy에는 seed를 실행하지 않는다.
Backup은 `REPEATABLE READ READ ONLY` exported snapshot을 dump에 고정하고, restore는 반드시 그
backup JSON evidence를 입력으로 받아 dump SHA와 source fingerprint를 결합한다. 두 번째 독립 RED
remediation 이후 evidence와 dump 소비는 각각 하나의 `O_RDONLY|O_NOFOLLOW` descriptor에 고정한다.
Regular file, mode 0600 이하, `nlink=1`을 요구하고 device/inode/size/mtime-ns/ctime-ns를 JSON
read/parse, SHA, `pg_restore --list`, 실제 restore 각 단계 전후에 descriptor와 pathname 양쪽에서
비교한다. `pg_restore`는 열린 descriptor를 stdin으로 받아 pathname을 다시 열지 않는다. Strict
schema는 executed action/task/target, comment/database/head, recovery point, complete catalog/data
fingerprint, archive count/digest, dump bytes/SHA와 file identity/stage binding을 검증한다. Restore
destination은 별도 exact-comment/empty DB여야 하며 `--clean`은 금지다.

### 기대 증거

증거: source/target attestation, exported snapshot ID/UTC timestamp/WAL LSN/transaction snapshot,
dump bytes/SHA-256와 `pg_restore --list` entry count/digest, backup/restore command timing,
restore-to-read-only-application-smoke 측정값, restored Alembic head, versioned complete catalog와
category fingerprint, 모든 public table의 row count/data SHA-256, sequence definition/current-state,
application smoke와 matched R2 label을 기록한다. API smoke DB connection은 read-only transaction을
강제하고 smoke 후 `catalogFingerprintVersion`, 전체 schema/category digest와 object count,
data/table/row/per-table fingerprint가 모두 동일해야 한다. 축약하지 않은 `postSmokeFingerprint`를
evidence에 기록한다. RPO는 recovery-point
timestamp와 smoke 시점의 측정 age로만 표현하고 production 보장으로 쓰지 않는다.
RTO도 local/staging에서 실제 측정된 범위만 기록한다. DB URL, credential, dump contents와 response
body는 첨부하지 않는다.

Wave 9 local migration evidence는 실제 graph에서 찾은 unique head/predecessor와 migration-file
hash, source snapshot/dump identity, current-`tc` before/after fingerprint, head/predecessor/final
revision, predecessor에서 사라져야 하는 head-owned table/function/trigger, catalog v3의 16개
category와 모든 public-table normalized digest, role/membership digest, raw physical drift 설명,
no-external-call API/write/worker smoke, exact cleanup과 pre-existing Docker/database/evidence hash
보존을 기록한다. Pristine singleton pointer의 새 `updated_at` 외 data normalization은 허용하지
않으며, dropped physical column slot compaction은 945개 live logical column equality와 별도로
표현한다.

현재 local reference는 final archive-inventory 구현으로 생성한 `retry6-final`이다: head
`20260902_0030`, catalog v3 16 categories, 78 tables/40 rows, dump 605,484 bytes/SHA-256
`0df58e095a88533ab419f1d7c433d4a0fd65f011e0a13c109dd913e133105bcf`, inventory 791 entries/listing
SHA-256 `2b256f661cf02c962238aa28b2e341385b9e055bc156b20bd018912314441665`, full post-smoke equality다.
Retry5와 그 이전 evidence는 immutable historical superseded다. 이는 scheduler, retention,
separately hosted staging, Cloudflare R2, operator handoff나 production RTO/RPO 실행 근거가 아니며
해당 gate는 모두 `NOT_RUN`이다.

### 롤백·중단 조건

중단: dump가 이미 존재하거나 repo 안에 있거나, restore DB가 비어 있지 않거나, target
attestation/head가 다르거나, evidence/dump가 symlink·non-regular·mode 초과·multi-link이거나, 어느
단계에서든 pinned device/inode/size/mtime-ns/ctime-ns가 달라지거나, backup evidence strict
binding/dump SHA·bytes/archive inventory/전체 catalog/data/table fingerprint가 다르거나,
migration blocker row가 있으면 실행하지 않는다. `--clean`, production 직접 target, broad downgrade
또는 데이터 삭제로 통과시키지 않는다. 실패 시 writer를 닫고 원본은 보존한 채 isolated target을
새로 준비한다. Evidence hash를 검증하기 전 dump/DB를 지우지 말고, 검증 뒤에는 exact database
name/comment/session을 다시 확인한 다음 그 source/restore DB와 exact temporary dump만 삭제해 catalog와
filesystem absence를 남긴다.

Wave 9는 multiple/unknown head, missing/ambiguous/wrong predecessor, unsafe or missing downgrade,
partial revision, source drift, used LightRAG control-plane state, catalog/data/role mismatch,
unexpected resource, evidence symlink/hardlink/tamper/overwrite 또는 cleanup preservation mismatch를
모두 fail-closed한다. 이 local PASS를 staging service rehearsal, operator handoff, production RTO/RPO
또는 release 승인으로 승격하지 않는다.

<!-- ops-section:alerts -->
## OPS-11 — alert 접수와 대응

### 사전 조건

- environment label, alert start time, safe alert name, current value, dashboard/ledger 접근권한과
  component owner를 확보한다.
- Alertmanager receiver/routing과 acknowledgement owner가 deployment 문서에 있어야 한다.
- incident channel이 secret/raw payload를 받지 않도록 제한한다.

### 안전 명령·참조

초기 threshold와 각 alert별 조사 순서는 [Alert thresholds](../../docs/phase-f/sprint-f1-observability.md#alert-thresholds)와
[Runbook](../../docs/phase-f/sprint-f1-observability.md#runbook)을 따른다. Rule 설치·검증과
controlled delivery는 [observability configuration](../observability/README.md)을 사용한다.
Alert에서 바로 SQL 수정, provider resend 또는 직접 Slack post를 하지 않는다.

### 기대 증거

증거: alert firing/resolved 시각, safe metric/label, owner acknowledgement, 수행한 rollout 또는
idempotent receipt, recovery 뒤 age/error trend를 남긴다. Raw Slack/provider/object data와
trace payload는 제한된 원본 시스템 밖으로 복사하지 않는다.

### 롤백·중단 조건

중단: receiver가 없거나 acknowledgement를 받지 못했으면 배포 gate를 통과시키지 않는다.
원인 확인 없이 threshold를 높이거나 silence하지 않는다. Mitigation이 지표를 악화시키면
관련 capability를 disable하고 마지막 정상 release로 롤백한다.

<!-- ops-section:rollback -->
## OPS-12 — application rollback과 변경 중단

### 사전 조건

- 직전 정상 artifact/config/secret/model version과 DB schema compatibility matrix를 확보한다.
- 외부 write/ingress, worker lease, generation schedule, migration의 commit 지점을 확인한다.
- 데이터 rollback과 application rollback이 별도 승인임을 확인한다.

### 안전 명령·참조

Edge/Slack 변경 실패는 [deterministic preflight와 rollback](../slack/README.md#6-deterministic-preflight와-rollback),
R2 credential은 [Credential rotation](../object-storage/README.md#credential-rotation),
generation은 [Disable, rotate, and recover](../generation/README.md#disable-rotate-and-recover)를
따른다. Migration은 Phase F의 [Safety contract](../../docs/phase-f/sprint-f1-recovery-performance-readiness.md#safety-contract)를
통과한 one-step staging-copy rehearsal 외에 자동 downgrade하지 않는다.

### 기대 증거

증거: rollback decision/owner, disabled ingress·writer 시각, 선택한 immutable version/digest,
controller rollout ID, schema compatibility, health/readiness와 queue recovery를 남긴다. 어떤
append-only 원장도 rollback 과정에서 수정·삭제되지 않아야 한다.

### 롤백·중단 조건

중단: 직전 version이 현재 schema와 호환되지 않거나, old secret이 revoked됐거나, 외부 write
결과가 ambiguous이면 application rollback만으로 해결됐다고 판정하지 않는다. 데이터를
삭제하거나 attempt/status를 되감지 말고 capability를 닫은 채 recovery/operator review로
전환한다.

<!-- ops-section:credential-loss -->
## OPS-13 — credential 유출·분실 복구

### 사전 조건

- 영향 credential의 종류, version, 사용 replica, scope와 마지막 정상 사용 시각을 secret
  manager audit에서 확인한다.
- 유출 의심과 단순 만료/분실을 구분하되, 불명확하면 유출로 취급한다.
- provider revoke 권한자와 affected capability owner가 함께 대응한다.

### 안전 명령·참조

먼저 영향 writer/ingress를 닫고, provider에서 의심 version을 revoke한 뒤, 새 최소권한
credential을 발급해 전체 bundle을 원자 교체한다. Slack 순서는
[Credential rotation](../slack/README.md#credential-rotation), R2 emergency 절차는
[Credential rotation](../object-storage/README.md#credential-rotation), generation token은
[Disable, rotate, and recover](../generation/README.md#disable-rotate-and-recover)를 따른다.
`TC_ENCRYPTION_MASTER_KEY` 분실/교체는 outstanding upload claim TTL을 먼저 drain하는
[Secret and rotation boundary](../nginx/README.md#secret-and-rotation-boundary)를 따른다.

### 기대 증거

증거: 노출하지 않은 credential 종류/version, revoke 및 replacement 시각, affected replica
inventory, secret-manager audit, forged/old credential 거부, 새 version canary와 정상
readiness를 남긴다. Secret 값이나 signed claim은 증거에 포함하지 않는다.

### 롤백·중단 조건

중단: 유출 의심 old credential로 롤백하지 않는다. Master key가 불명확하면 신규 upload
session을 닫고 outstanding claim을 실패 폐쇄하며, bucket privacy를 완화하지 않는다.
Replacement 검증이 실패하면 capability를 disabled 상태로 유지하고 별도 recovery owner에게
escalate한다.

<!-- ops-section:handoff -->
## OPS-14 — 운영자 인계와 완료 판정

### 사전 조건

- 실행 운영자, 검토자, on-call owner, 대상 environment와 증거 저장 위치를 지정한다.
- 모든 live 작업은 change ticket, maintenance window, rollback authority를 가져야 한다.
- 이 저장소의 deterministic 검증과 외부 operator evidence를 분리한다.

### 안전 명령·참조

저장소 구조 검증은 root에서 `pnpm test:operations`로 실행한다. 외부 rehearsal의 안전 경계와
남은 gap은 [Evidence-backed gap matrix](../../docs/phase-f/sprint-f1-recovery-performance-readiness.md#evidence-backed-gap-matrix)와
[Wave 2 limitations and next gates](../../docs/phase-f/sprint-f1-wave2-verification.md#limitations-and-next-gates)를
인계 checklist의 원본으로 사용한다.

### 기대 증거

증거: 아래 항목마다 `evidence ID / 실행 시각 / operator / reviewer / 대상 / 결과 / rollback
결과`를 기록한다.

| 필수 인계 항목 | 완료 전 필요한 외부 증거 |
|---|---|
| 배포 lifecycle | site-specific install/drain/start/stop/restart 명령 참조와 canary 기록 |
| Secret | secret-store 암호화·최소권한과 실제 rotation/revoke rehearsal |
| Slack | 고정 HTTPS ingress 또는 Socket, signed event, 중복 수신, notification rotation |
| R2 | private bucket, exact-origin browser flow, quarantine/scan, matched restore sample |
| Generation/model | credentialed canary, kill switch, outcome-unknown alert/review, model rollback |
| Knowledge | staging projection rebuild/fallback와 full LightRAG corpus reindex/checksum |
| PostgreSQL | scheduled backup, isolated matched restore, RTO/RPO, one-step migration rehearsal |
| Observability | production-version rule 검증, warning/resolved delivery와 on-call acknowledgement |
| Recovery | application rollback과 credential-loss tabletop/실행 rehearsal |
| Performance | attested disposable 100,000-case concurrent profile, host/DB resource 관찰과 reviewer 승인 |
| UAT | exact deployed HTTPS origin의 자동 receipt와 15개 owner 수동 artifact |

### 롤백·중단 조건

중단: 항목 하나라도 `NOT RUN`, 미구현, evidence link 없음, reviewer 미서명이면
`PF-SF1-T12`는 **미완료**다. 이 문서와 validator만으로 roadmap checkbox를 바꾸지 않는다.
인계 중 실패한 live capability는 해당 섹션의 stop condition에 따라 닫고, 증거를 보존해
다음 승인 window로 넘긴다.

## PF-SF1-T11 Wave 10 local isolated physical-browser UAT

이 절차는 OPS-14의 deployed HTTPS/owner UAT를 대체하지 않는 local automated sub-gate다.
기본 preview는 credential을 요구하거나 state를 변경하지 않으며, exact process, loopback
port, disposable resource, route/workflow, output과 confirmation을 출력한다.

```bash
pnpm phase-f:uat:browser:preview
```

실행자는 preview가 출력한 `--run-id`, 다섯 포트와 exact `--confirm`을 그대로 사용한다.
Harness는 system-installed Chrome, Vite, FastAPI, worker, PostgreSQL 16, MinIO와 ClamAV만
사용하며 staging/production/Slack/R2/provider/public tunnel을 호출하지 않는다. Target이
foreign/nonempty이거나 origin·redirect·request·source·Docker/database/evidence inventory가
drift하면 evidence를 PASS로 봉인하지 않는다.

Mutation 전 canonical manifest를 Python validator로 먼저 검사하고, exact 15 workflow ID와
33 automated/15 manual check mapping 및 canonical manifest/validator digest를 evidence에
binding한다. Workflow PASS는 37개 named assertion receipt의 exact 집합에서만 도출하며 누락,
중복, 추가 assertion/workflow가 있으면 중단한다. Generation failure와 quality review는
disposable DB의 credential-free deterministic safe row만 사용하고 retry/review/provider/Slack
외부 동작은 실행하지 않는다.

Desktop 검증은 Dashboard hard reload/KPI 및 Reports API-UI reconciliation과 전체 lifecycle
경계를 포함한다. 390×844 검증은 19개 planned route, generation failure/quality-review in-route
detail과 execution issue modal을 모두 방문하고, visibility filter 전에 모든 primary button/link의
hidden/zero-size/horizontal clipping과 document overflow를 검사한다. `requestfailed`는 aborted
request도 버리지 않고 workflow/harness phase, loopback port, method, safe path class, resource
type과 disposition으로 기록한다. Safe disposition은 exact Web port의 `NET_ERR_ABORTED`
`GET`/`fetch`와 명시된 normalized route-abort path에만 허용하고, workflow와 harness teardown
phase를 각각 별도 disposition에 고정한다. MinIO/object-storage PUT, foreign/unknown port 또는
origin, unsafe method/resource type, non-abort, phase/path mismatch, unexpected field 또는
unresolved request는 PASS를 금지한다.

```bash
pnpm phase-f:uat:browser -- --run-id <8-lowercase-alnum> \
  --postgres-port <generated> --minio-port <generated> --clamav-port <generated> \
  --api-port <generated> --web-port <generated> \
  --confirm PF-SF1-T11-wave10:<same-run-id>:LOCAL-ISOLATED
```

성공 후 rehearsal/cleanup JSON과 desktop/mobile screenshot은 새 mode-0600 regular
single-link 파일이어야 한다. Cleanup receipt의 exact container/volume/network/database/
bucket/temp/profile/process/port absence, pre-existing inventory equality, current `tc` write-counter
digest equality와 external request 0을 확인한다. JSON은 seal 검증 뒤 exact schema-v1
key/type/fixed-value, lifecycle/mutation count, mobile set, manifest binding, artifact name/hash와
remaining `NOT_RUN` gate를 모두 재검증해야 하며 누락/추가/tamper를 허용하지 않는다. 전체
절차와 independent RED `task_0c95d0badb67`, remediation `task_d1aff5905dae`, authoritative
final-code run `92af97f8`의 hashes는
[Wave 10 verification](../../docs/phase-f/sprint-f1-wave10-verification.md)에 있다.
