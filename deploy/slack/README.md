# Slack App 설치·검증 runbook

이 문서는 [app-manifest.yaml](./app-manifest.yaml)을 테스트 workspace에 설치하고
Phase C에서 확정한 HTTP Events webhook으로 수집 원장을 live 검증하는 절차다.
Socket Mode 구현은 public ingress를 사용할 수 없게 될 때만 적용하는 fallback으로
유지한다. 저장소의 deterministic fake Slack 검사는 완료됐지만 실제 Slack
credential·테스트 workspace·channel은 제공되지 않았으므로, 이 문서 작성 시점에는
live App 설치나 자동 수집 성공을 주장하지 않는다.

현재 scope는 public channel 전용 `channels:history`, 초대된 channel의 thread reply용
`chat:write`, event는 `message.channels`로 고정한다. `chat:write.public`, Jira,
Incoming Webhook, shortcut, private channel·DM 수집은 사용하지 않는다.

## 1. 보안 경계와 준비값

- 로그인 없는 UI와 `/api/*`는 사내망/VPN에서만 제공한다.
- HTTP mode만 public TLS endpoint의 정확한
  `POST /integrations/slack/events` 경로를 노출한다. 다른 path와 method는 공개하지
  않는다.
- 수집 대상은 팀 전체가 볼 수 있는 public channel allowlist로 제한한다. Private
  channel은 기본 거부하며 현재 manifest에는 `groups:history`와 `message.groups`
  권한이 없다.
- Slack secret/token을 manifest, Git, shell history, 문서, 브라우저 또는 로그에
  기록하지 않는다. 배포 secret store 또는 권한 제한 환경 변수로만 주입한다.

준비할 값은 다음과 같다.

| 값 | HTTP | Socket | 확인 위치 |
|---|---:|---:|---|
| workspace ID (`T...`) | 필수 | 필수 | Slack workspace 정보 |
| public channel ID (`C...`) | 필수 | 필수 | channel 정보의 channel ID |
| bot token (`xoxb-...`) | 필수 | 필수 | OAuth & Permissions |
| signing secret | 필수 | 사용 안 함 | Basic Information |
| app-level token (`xapp-...`, `connections:write`) | 사용 안 함 | 필수 | Basic Information → App-Level Tokens |
| public Slack ingress HTTPS base URL | 필수 | 사용 안 함 | 운영 ingress |
| trusted product Web HTTPS base URL | 선택 | 선택 | 고정 운영 UI authority; 없으면 링크 생략 |

## 2. App 생성과 channel 참여

1. Slack API의 **Create New App → From an app manifest**에서 테스트 workspace를
   선택한다.
2. HTTP mode면 manifest의 `request_url`을 실제 public HTTPS exact path로 바꾼 뒤
   import한다. Placeholder인 `replace-me.invalid`로는 설치 검증을 진행하지 않는다.
3. manifest가 요청하는 bot scope가 `channels:history`, `chat:write`이고 event가
   `message.channels` 하나인지 확인한다. `chat:write.public` 또는 private channel
   scope/event가 보이면
   설치를 중단한다.
4. **Install to Workspace**를 실행하고 bot token을 secret store에 저장한다.
5. 각 allowlist public channel에서 `/invite @TC Ingestion`으로 App을 참여시킨다.
   App이 참여하지 않은 channel의 history/event 수집을 성공으로 간주하지 않는다.

실제 workspace 설치와 channel 참여가 끝나야 `PC-SC1-T02`를 완료로 바꿀 수 있다.

## 3. 수신 mode

Phase C의 선택은 HTTP Events다. 한 배포에서는 아래 bundle 중 정확히 하나만
사용하며 설정을 섞으면 startup validation 실패로 처리한다. Socket Mode는 HTTP
ingress를 제공할 수 없는 장애·환경 제약 때만 전환한다.

### HTTP Events

```text
TC_SLACK_MODE=http
TC_SLACK_SIGNING_SECRET=<secret-store reference>
TC_SLACK_BOT_TOKEN=<secret-store reference>
TC_SLACK_WORKSPACE_ID=<T...>
TC_PUBLIC_SLACK_BASE_URL=https://<public-host>
TC_PUBLIC_WEB_BASE_URL=https://<stable-product-ui-host>
TC_SLACK_ALLOWLIST_CHANNEL_IDS=<C...>
```

1. [slack-http.conf](../nginx/slack-http.conf)처럼 exact POST path만 API로 proxy하고
   나머지 public path는 `404`인지 외부에서 확인한다.
   CIDR, trusted proxy, rate/body/timeout, 보안 header와 query-free log의 설치 순서는
   [production edge runbook](../nginx/README.md)을 따른다. 이 Nginx 파일만으로 실제
   firewall, VPN, TLS 또는 public DNS가 검증됐다고 간주하지 않는다.
2. Event Subscriptions의 Request URL을 저장해 URL verification이 성공하는지
   확인한다.
3. 위조 signature와 허용 범위를 지난 timestamp가 `401`이고 정상 event가 빠르게
   `200`인지 검증한다.

피드백 배포에 사용하는 임시 `trycloudflare.com` Quick Tunnel URL은 재시작하면
바뀔 수 있으므로 Slack App의 상시 Request URL로 간주하지 않는다. Live smoke에
사용했다면 정확한 URL과 실행 기간만 비밀이 아닌 검수 기록으로 남기고, 종료 후
Event Subscription을 비활성화한다. 지속 수집에는 고정 HTTPS hostname을 사용한다.

### Socket Mode fallback

```text
TC_SLACK_MODE=socket
TC_SLACK_APP_TOKEN=<secret-store reference>
TC_SLACK_BOT_TOKEN=<secret-store reference>
TC_SLACK_WORKSPACE_ID=<T...>
TC_PUBLIC_WEB_BASE_URL=https://<stable-product-ui-host>
TC_SLACK_ALLOWLIST_CHANNEL_IDS=<C...>
```

1. Slack App Settings에서 Socket Mode를 켠다.
2. `connections:write`가 있는 app-level token을 생성해 secret store에 저장한다.
3. public Slack listener를 배포하지 않는다. Worker가 outbound WSS로 연결하고
   envelope를 ack하는지 worker log의 event ID·상태만으로 확인한다.
4. `TC_SLACK_MODE=http` 설정과 HTTP public listener가 동시에 활성화되지 않았는지
   확인한다.

Mode를 바꿀 때는 기존 receiver를 먼저 중지하고 설정 bundle을 교체한 다음
API·worker를 재시작한다. 두 transport 모두 같은 normalization, allowlist,
idempotency, PostgreSQL job pipeline을 사용하지만 mode 전환 자체는 운영 변경이다.
최근 thread 재동기화 기본값은 최근 7일을 3,600초 간격으로 검사한다. 필요할 때만
`TC_SLACK_RECENT_RESYNC_LOOKBACK_DAYS`와
`TC_SLACK_RECENT_RESYNC_INTERVAL_SECONDS`를 허용 범위 안에서 조정한다.

## 4. 자동 생성 reply 안전 운용

- credential-free example과 application 기본값은
  `TC_SLACK_MODE=disabled`, `TC_SLACK_GENERATION_NOTIFICATIONS_ENABLED=false`다.
  테스트 workspace App 설치, `chat:write` scope, workspace ID, allowlist와 bot의 channel
  참여를 확인한 뒤에만 선택한 ingress mode와 notification flag를 함께 활성화한다.
- `TC_PUBLIC_WEB_BASE_URL`은 query·fragment·credential이 없는 고정 HTTPS 제품 UI
  authority만 허용한다. localhost, 사설/loopback IP, `trycloudflare.com`, Slack ingress
  URL은 결과 link authority로 사용하지 않는다. 값을 생략하면 reply의 link 줄도
  결정적으로 생략된다.
- 기본 retry는 5초 exponential backoff, 최대 3,600초이며 HTTP 429의 durable
  `Retry-After`가 더 길면 그 값을 줄이지 않는다. ambiguous write는 최대 86,400초 동안
  `conversations.replies` marker reconciliation만 수행하고 blind repost하지 않는다.
- `TC_SLACK_NOTIFICATION_BREAKER_OPEN=true`는 신규 외부 write를 차단한다. credential이
  없거나 Slack runtime이 configured가 아니면 delivery job은 claim하지 않으며 live 성공으로
  해석하지 않는다.
- `timeout`, HTTP 5xx, `fatal_error`, `internal_error`, response loss는 remote commit 여부가
  불명확하다. deterministic marker와 provider thread receipt를 찾지 못한 채 strict
  observable exactly-once를 주장하지 않으며, reconciliation horizon 뒤에는
  `AMBIGUOUS_UNRESOLVED`로 fail-safe 종료한다.
- auth/scope/channel/thread/archive/lock 오류는 sanitized permanent code만 기록하고 다른
  thread, top-level channel, DM 또는 permalink로 fallback하지 않는다. provider body,
  prompt, token, raw error, source permalink는 reply 본문과 observation에 저장하지 않는다.
- App의 marker와 provider-attested bot/app identity가 함께 있는 reply는 Events ingest와
  thread sync에서 제외해 source version, quiet job, outbox, generation feedback loop를 만들지
  않는다.

## 5. allowlist와 live smoke

1. `pnpm dev` 또는 배포 프로세스를 시작하고 `/integrations`에서 실제 status가
   선택한 mode와 일치하는지 확인한다. Credential이 없으면 runtime `DISABLED`가
   API `DISCONNECTED`로 보이거나 `SETUP_REQUIRED`여야 하며 연결 성공으로 해석하지
   않는다.
2. Integrations에서 `C...` public channel ID를 추가하고 저장한다. Private channel
   ID (`G...`)는 이 화면에서 추가하지 않는다.
3. allowlist channel에 root 일감, reply, edit, delete를 순서대로 전송한다.
4. 같은 Slack event가 provider retry로 다시 와도 work item·revision·outbox·job이
   늘지 않는지 확인한다.
5. Slack Work Items 상세에서 root/reply 원문, 작성자, 수신 시각, edit revision,
   delete tombstone, parse 4개 필드와 `missingFields`, quiet due, sync cursor를
   확인한다.
6. 누락 reply를 만든 뒤 thread sync를 실행하고 보완되는지 확인한다. Rate limit은
   Slack의 `Retry-After`를 따르고 최대 attempt 뒤 `DEAD`가 Integrations와 상세에
   노출되는지 확인한다.

live 확인 시 실제 메시지 본문이나 token을 test artifact에 복사하지 않고 event ID,
opaque resource ID, 상태, 시각만 기록한다.

### Credential rotation

HTTP signing secret, bot token, Socket app token은 서로 다른 credential이다. 한 번에
하나의 변경만 수행하고 receiver/publisher를 먼저 drain 또는 disable한 뒤 secret store의
전체 설정 bundle을 원자적으로 교체하고 API·worker를 재시작한다. HTTP signing secret은
구·신 signature의 overlap을 가정하지 않으므로 Event Subscription 중단 창을 계획하고,
정상 request의 실제 검증 전에는 ingress를 복구 완료로 판정하지 않는다. 새 credential을
test workspace에서 확인한 뒤에만 이전 credential을 revoke하며 artifact에는 secret 대신
secret version, 변경 시각, 검증 결과만 남긴다. 이 저장소의 deterministic 검사는 실제
Slack rotation rehearsal을 대신하지 않는다.

## 6. deterministic preflight와 rollback

Live 설치 전에 저장소 root에서 다음을 통과시킨다.

```bash
pnpm check
pnpm --filter @tc/web build
TC_TEST_DATABASE_URL=<disposable-test-db> pnpm test:migration
TC_TEST_DATABASE_URL=<disposable-test-db> \
  node scripts/python.mjs -m pytest apps/api/tests -m postgres
```

실패하거나 예상하지 않은 channel이 수집되면 receiver와 worker를 중지하고 public
listener를 닫은 뒤 bot token·signing secret 또는 app token을 rotate한다. Allowlist를
비우고 pending/retry/dead job과 event ID를 보존해 원인을 조사하며, append-only 원문과
revision을 직접 수정·삭제해 복구하지 않는다.

상세한 결정론적 근거와 아직 열린 live gate는
[Sprint 5 보고서](../../docs/sprint-5/sprint-5-report.md)에 기록한다.

Slack 공식 기준은 `message.channels`에 `channels:history`가 필요하고, Events API는
요청을 3초 안에 2xx로 응답한 뒤 비동기 처리하는 방식을 권장한다. 수신 요청은 raw
body와 `X-Slack-Request-Timestamp`, `X-Slack-Signature`로 검증하며 5분을 넘긴
timestamp는 replay로 거부한다.

- https://docs.slack.dev/reference/events/message.channels/
- https://docs.slack.dev/authentication/verifying-requests-from-slack/
- https://docs.slack.dev/apis/events-api/
