# Sprint 5 Slack 수집·원문 원장 품질 게이트

**검증일:** 2026-08-21  
**범위:** P3-S5-T01~T11 중 repository에서 재현 가능한 코드·결정론적 integration  
**결론:** T02~T11 코드 gate 통과, T01 live 설치와 Sprint 5 live 종료 gate 미완료

## 1. 요약

Slack HTTP Events와 Socket Mode를 동일 normalization/repository pipeline에 연결하고,
public allowlist와 private default deny, PostgreSQL append-only source revision,
event/message idempotency, thread pagination·증분/최근 재동기화, quiet window,
4개 필드 parser, Retry-After와 DEAD, FastAPI/generated TypeScript client/Web 조회까지
결정론적으로 검증했다.

실제 Slack credential, 테스트 workspace·channel, public DNS/TLS가 없으므로 App 설치,
channel 참여, HTTP URL verification 또는 live Socket 연결, 사용자 shortcut 없는 자동
수집 성공은 실행하지 않았다. Manifest와 [설치 runbook](../../deploy/slack/README.md)은
준비됐지만 이것만으로 T01 또는 Sprint 5 live 종료를 완료 처리하지 않는다.

## 2. 구현 경계

```text
Slack HTTP raw body ─ signature/timestamp ─┐
                                           ├─ normalized message event
Slack Socket envelope ─ ack ──────────────┘
                         │
                         ▼
              allowlist / private deny
                         │
                         ▼
       slack_events + SLACK_EVENT_PROCESS job
                         │
                         ▼
 work item + thread + message + append-only revision
       + audit + outbox producer + quiet job
                  (one PostgreSQL transaction)
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
    thread sync / retry         quiet → parse
             │                       │
             └──── FastAPI → generated client → Web
```

수신 ack는 event와 processing job 등록까지만 기다린다. Worker가 원문 projection,
thread 보완과 parser를 처리하며 provider `Retry-After`를 PostgreSQL job 재시각에
전달한다. Work item 상세는 root/reply와 CREATED/EDITED/DELETED revision을 provider
시각 순서로 직렬화하고 delete를 tombstone으로 표시한다.

## 3. 품질 게이트 결과

| Gate | 실행 명령 | 결과 |
|---|---|---|
| 전체 정적·unit·schema·ADR·drift | `pnpm check` | PASS — ESLint/Ruff, TS/Python typecheck, TS unit 100개, Python non-PostgreSQL 95개, strict schema fixture, ADR link/status, OpenAPI/client drift |
| Web production | `pnpm --filter @tc/web build` | PASS — TypeScript + Vite production bundle |
| PostgreSQL migration/repository | `TC_TEST_DATABASE_URL=... pnpm test:migration` | PASS — 43개, Alembic base→head rebuild, FK/unique/check/append-only, rollback, queue·Slack concurrency |
| FastAPI PostgreSQL E2E | `TC_TEST_DATABASE_URL=... node scripts/python.mjs -m pytest apps/api/tests -m postgres` | PASS — 8개, Sprint 1~5 actual PostgreSQL API |
| Sprint 5 primary E2E | `... pytest apps/api/tests/test_sprint5_slack_api_postgresql.py` | PASS — 2개, HTTP/Socket·worker·fake provider·DEAD 포함 |
| OpenAPI/client drift | `pnpm openapi:check` | PASS — generated OpenAPI와 TypeScript schema byte-equivalent |
| Web no-fallback smoke | Orca browser `/integrations`, `/slack-work-items`, 존재하지 않는 typed detail ID | PASS — actual API status/empty/404 표시, mock source row 없음 |

PostgreSQL 검사는 이름에 `test`가 포함된 disposable `tc_test_sprint5` DB에서만
수행했다. Migration test가 DB를 `base`까지 내리므로 개발 DB에는 이 명령을 실행하지
않았다.

## 4. 단일 결정론적 Slack E2E

Primary test는
[`test_sprint5_slack_deterministic_postgresql_end_to_end`](../../apps/api/tests/test_sprint5_slack_api_postgresql.py)다.
한 FastAPI TestClient, 실제 PostgreSQL schema, 실제 generic Worker queue, generated
resource shape와 fake Slack Web API를 묶어 다음을 재현한다.

1. 위조 signature와 301초 지난 timestamp는 `401`, 정상 URL verification은 challenge
   body로 응답한다.
2. 같은 message payload를 HTTP와 Socket receiver에 넣어 normalized event가 같음을
   확인한다.
3. Public `C...` channel만 allowlist로 저장하고 private `G...` event는
   `PRIVATE_DEFAULT_DENY`로 무시한다. Status 응답은 로그인 없는 ACL warning을
   포함한다.
4. Reply를 root보다 먼저 보내고 동일 event를 세 번 전달한다. Ack는
   `ACCEPTED, DUPLICATE_EVENT_ID, DUPLICATE_EVENT_ID`이며 processing job은 하나다.
5. Root를 처리한 뒤 quiet job을 `RESCHEDULED`→`UNCHANGED`로 갱신하고 API로
   `CANCELLED` 처리한다.
6. `WORK_ITEM_PARSE` job이 개발건·요청 메뉴·기능 설명·요청 상세 4개 필드,
   `missingFields=[]`, confidence와 source revision을 저장한다. 별도 parser unit은
   일부 필드가 없는 경우 `NEEDS_INPUT`과 질문을 검증한다.
7. Delete가 먼저 수신되고 과거 edit가 늦게 와도 reply revision은
   CREATED→EDITED→DELETED 순이며 원문과 tombstone이 함께 남는다.
8. Fake provider의 누락 reply를 thread sync가 보완한다. 이어 실제
   `SlackWebApiHttpTransport` mock이 두 cursor page를 반환하고 worker가 두 reply를
   추가하며 sync watermark를 갱신한다.
9. Sync API가 등록한 한 PostgreSQL job에 429/`Retry-After: 7`을 다섯 번 주입한다.
   Generic Worker가 RETRY를 거쳐 max attempt에서 DEAD로 전환하고 `/api/slack-jobs`
   응답이 `RATE_LIMIT`, HTTP 429, DEAD를 노출한다.
10. 최종 count는 work item 1개, event processing job 4개, source revision 7개다.
    Duplicate delivery는 work item·revision·outbox·quiet job을 늘리지 않는다.

Companion test는 Socket ingress가 같은 registration/worker pipeline을 사용하고 API
status가 `SOCKET_MODE`/`NOT_APPLICABLE`임을 확인한다. Adapter unit은 history/replies
cursor와 malformed/repeated cursor, provider retry를 더 좁은 경계에서 검증한다.

## 5. PostgreSQL 불변성과 transaction

[`test_slack_postgresql.py`](../../backend/tests/repository/test_slack_postgresql.py)는
다음을 actual PostgreSQL에서 검증한다.

- workspace/event ID unique와 workspace/channel/event ts 보조 unique
- allowlist FK와 private explicit opt-in check
- work item, thread, message identity unique와 root 단일성
- revision·parse append-only trigger와 delete tombstone shape
- 동일 event 세 번에서 event/work item/thread/message/revision/outbox/audit/job 각각 1개
- reply 선도착과 edit/delete 순서 역전의 provider timestamp 정렬
- 누락 reply merge, 중복 revision 제거, sync watermark와 최근 thread job idempotency
- quiet due가 마지막 activity + 기본 10분이며 갱신·취소가 stale generation을 거부
- rate limit retry와 max attempt DEAD
- 고의 예외 시 event·revision·audit·outbox·job 전부 rollback
- 두 concurrent first reply가 work item/thread/quiet job 하나를 공유

Worker에는 configured Slack runtime일 때 최근 activity lookback과 stale sync interval을
사용해 `enqueue_recent_thread_syncs`를 주기 호출하는 maintenance scheduler를 연결했다.
기본값은 최근 7일, 3,600초 interval이며 환경 변수
`TC_SLACK_RECENT_RESYNC_LOOKBACK_DAYS`, `TC_SLACK_RECENT_RESYNC_INTERVAL_SECONDS`로
조정한다. Disabled/SETUP_REQUIRED runtime은 provider sync job을 예약하지 않는다.

## 6. API, generated client와 Web

- FastAPI는 status, allowlist CRUD, work item list/detail/messages, event log, sync,
  quiet upsert/cancel, job list를 typed resource로 제공한다.
- OpenAPI에서 generated TypeScript schema를 만들며 client test가 Slack status,
  allowlist, list/detail, event/job query, sync, quiet upsert/cancel URL·body·idempotency
  header를 검증한다.
- Integrations는 실제 mode·credential/setup 상태, public allowlist, private default deny
  경고, 최근 event와 retry/DEAD 통계를 actual client로 읽는다.
- Slack Work Items는 중복 ID를 한 행으로 합친 검색/filter/cursor-ready 목록과
  chronological root/thread/revision, 4개 parse field·missing/confidence/source,
  quiet due·sync cursor/job 상태를 표시한다.
- Loading, empty, partial, error, conflict 상태는 명시적이며 Slack 화면은 API 실패 시
  demo item을 반환하지 않는다. Knowledge ontology 영속 화면은 Sprint 6로 남겼다.

## 7. Outbox와 ADR 상태

Slack event 처리 transaction은 revision, work item projection, audit,
`outbox_events` READY producer row와 quiet job을 함께 기록한다. 중복과 rollback test는
이 producer atomicity를 증명한다.

그러나 READY outbox를 claim/publish하는 publisher, ontology projection consumer,
consumer checkpoint, 처리 성공/retry/DEAD와 replay API는 아직 없다. 따라서
[ADR-0005](../adr/0005-transactional-outbox.md)는 `Proposed`이며 “publisher/consumer가
동작한다”고 주장하지 않는다. HTTP/Socket code evidence와 proxy 후보도 실제
DNS/TLS/firewall/credential을 대신하지 못하므로
[ADR-0006](../adr/0006-private-network-and-slack-ingress.md)도 `Proposed`다.

## 8. Roadmap 판정

| Task | 판정 | 근거/미완료 |
|---|---|---|
| P3-S5-T01 | 미완료 | Manifest·runbook 완료, 실제 test workspace 설치·channel 참여 없음 |
| P3-S5-T02 | 코드 완료 | 정상/위조/오래된 요청, URL verification |
| P3-S5-T03 | 코드 완료 | 단일 mode config, HTTP/Socket normalized pipeline |
| P3-S5-T04 | 코드 완료 | allowlist API/Web, private default deny |
| P3-S5-T05 | 코드 완료 | PostgreSQL source ledger와 append-only revision/tombstone |
| P3-S5-T06 | 코드 완료 | event/message identity dedup, 동일 event 3회 atomic count |
| P3-S5-T07 | 코드 완료 | pagination, watermark, incremental/recent resync scheduler |
| P3-S5-T08 | 코드 완료 | quiet 기본 10분, reschedule/unchanged/cancel/stale generation |
| P3-S5-T09 | 코드 완료 | 4개 field, missingFields, confidence, source revisions |
| P3-S5-T10 | 코드 완료 | actual-client list/detail/revision/parse/sync/job Web |
| P3-S5-T11 | 코드 완료 | reorder, replay, 429 Retry-After, max-attempt DEAD 노출 |

Roadmap의 T02~T11 체크는 위 코드 evidence만 뜻한다. “사용자 shortcut 없이 새 root가
live 자동 수집됨”, “실제 channel의 전체 thread가 장기 보존됨”, “live provider retry가
중복을 만들지 않음”이라는 Sprint 종료 체크는 모두 미완료로 유지한다.

## 9. 열린 위험과 다음 단계

1. **Live Slack 미검증:** Test workspace, App credential, channel, HTTP public TLS 또는
   outbound Socket 연결을 준비해 runbook 전체를 실행해야 한다.
2. **Network ACL 미실증:** Nginx 후보는 exact path를 표현하지만 외부 black-box path
   차단, firewall, DNS/TLS, secret rotation을 staging에서 확인해야 한다.
3. **Outbox consumer 부재:** Sprint 6 ontology 전에 publisher, checkpoint,
   idempotent consumer, replay와 lag/DEAD 관측을 구현해야 한다.
4. **장기 scheduler 운영:** 최근 resync scheduler의 코드/DB idempotency는 통과했지만
   장시간 worker 재시작·Socket 단절·실제 Slack retention/rate limit은 live soak가
   필요하다.
5. **Private channel:** Repository는 명시적 팀 합의 opt-in을 표현할 수 있으나 현재
   manifest와 Web은 public-only다. 로그인 없는 제품에서 개인별 Slack ACL을 재현하지
   못하므로 private 범위를 넓히지 않는다.
6. **Sprint 6 연결점:** Source revision ID를 ontology evidence로 사용하고 outbox
   consumer가 typed node/edge를 만들 때 current revision과 tombstone을 존중해야 한다.
   Knowledge 화면은 이 projection과 citation API가 준비될 때 actual로 전환한다.
7. **Sprint 7~8 연결점:** Generation API는 parse/source snapshot을 입력으로 받고
   source revision + generation version으로 dedup하며 사람이 수정한 case를 덮어쓰지
   않아야 한다.

## 10. Live 종료 절차

1. [Slack runbook](../../deploy/slack/README.md)에 따라 App을 test workspace에 설치한다.
2. Public test channel에 App을 참여시키고 allowlist를 저장한다.
3. HTTP 또는 Socket 중 한 mode만 켜고 status·callback/connection을 확인한다.
4. Root→reply→edit→delete→duplicate→누락 reply→429 시나리오를 실제 channel에서
   실행하되 secret/raw 민감 본문은 artifact에 남기지 않는다.
5. Web에서 work item 1개, 시간순 revision, parse source, sync watermark와 DEAD/retry를
   확인한다.
6. Public ingress의 다른 path/method가 외부에서 차단되는지 black-box test한다.
7. 위 근거를 기록한 뒤에만 T01과 Sprint 5 종료 체크를 갱신한다.
