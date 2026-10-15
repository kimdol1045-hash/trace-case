# ADR-0006: 무로그인 사내망 경계와 Slack 단일 ingress mode

| 항목 | 값 |
|---|---|
| ADR 번호 | 0006 |
| 날짜 | 2026-08-20 |
| 상태 | Proposed |
| 결정자 범위 | Architecture / Security / OPS / BE |

## Context

MVP는 사용자 로그인과 역할을 제공하지 않으므로 UI와 일반 API가 Internet에 노출되면 누구나 데이터를 변경할 수 있다. Slack event는 외부에서 들어와야 하지만 운영망이 public endpoint를 제공하지 못할 수 있다. 실제 Slack App credential, public DNS, TLS, firewall 경계는 아직 확보되지 않았다.

## Decision Drivers

- 무로그인 write API의 외부 노출 방지
- public attack surface 최소화
- Slack HTTP signature·timestamp 검증과 빠른 응답
- public ingress 없는 사내 환경 지원
- HTTP/Socket receiver 뒤의 동일 normalization·idempotency

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| 내부 UI/API + exact Slack HTTP ingress | 한 POST path만 public | public ingress 가능 시 제안 선택 |
| Socket Mode | outbound WebSocket receiver, public listener 없음 | public ingress 불가 시 fallback |
| 전체 API public + app auth | 인증을 새로 구현 | MVP 범위 밖 |
| VPN만 사용하고 Slack 연동 없음 | 외부 ingress 완전 제거 | 제품 핵심 흐름 때문에 제외 |

## Decision

UI, `/api/*`, health와 운영 설정은 사내망/VPN listener에서만 제공한다. HTTP mode에서는 Internet에 정확히 `POST /integrations/slack/events` 한 path만 공개하고 다른 path·method는 차단한다. Public ingress가 없으면 Slack Socket Mode receiver가 outbound WSS로 연결하고 public listener는 배포하지 않는다. 한 환경에서 두 mode를 동시에 켜지 않으며 receiver 뒤의 event validation 이후 normalization, allowlist, idempotency, job pipeline을 공유한다.

실제 환경의 `http|socket` 선택, DNS/TLS/firewall 검증과 Slack credential contract test 전까지 `Proposed`다.

## Consequences

### Positive

- 무로그인 화면·API의 attack surface를 내부망으로 제한한다.
- 환경 제약에 따라 public listener 없는 배포가 가능하다.
- 두 receiver의 domain 처리 차이를 줄인다.

### Negative

- network boundary가 곧 접근 제어이므로 VPN·firewall 오구성이 치명적이다.
- 두 Slack transport의 연결·retry 운영 절차를 유지해야 한다.
- HTTP mode는 public TLS endpoint와 secret rotation을 요구한다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| reverse proxy 오구성으로 일반 API 노출 | 외부 black-box test에서 Slack exact path 외 모두 차단되는지 release gate로 검사한다. |
| 위조·replay Slack request | signing secret, timestamp window, constant-time signature 검증 후 body를 처리한다. |
| 두 mode 중복 수신 | startup에서 mode 하나만 허용하고 event ID unique로 dedup한다. |
| Socket 연결 단절 | reconnect/backoff, connection health, thread resync scheduler를 둔다. |

## Validation Evidence

- [internal Nginx 후보](../../deploy/nginx/internal.conf)와 [Slack exact-path 후보](../../deploy/nginx/slack-http.conf)가 listener를 분리한다.
- [Sprint 0 인프라 spike](../sprint-0/infra-and-ai-spike.md)에 HTTP/Socket 배치도와 선택 기준이 있다.
- [backend settings](../../backend/src/tc_backend/config.py)는 `disabled|http|socket`별 필수 설정 bundle을 검증한다.
- [Slack receiver test](../../backend/tests/unit/slack/test_receivers.py)는 exact raw body signature, 위조·오래된 timestamp 거부, URL verification, HTTP/Socket normalization 동등성과 Socket ack를 결정론적으로 검증한다.
- [Sprint 5 PostgreSQL API E2E](../../apps/api/tests/test_sprint5_slack_api_postgresql.py)는 HTTP endpoint에서 정상/위조/오래된 요청, allowlist/private deny, idempotent event job과 worker pipeline을 검증하고 Socket mode가 같은 등록 pipeline을 사용함을 확인한다.
- [Slack App 설치 runbook](../../deploy/slack/README.md)은 HTTP exact ingress와 Socket outbound-only 설치를 분리하고 한 runtime mode만 선택하도록 한다.
- 실제 Slack App 설치, signing secret/app token, public DNS, TLS, firewall external black-box test가 없다. 따라서 로컬 결정론적 code evidence가 늘었어도 상태는 `Proposed`다.

## Revisit Triggers

- 제품이 사내망 밖 사용자 또는 개인별 책임 추적을 지원해야 한다.
- Slack의 transport/security 정책이 바뀌거나 한 mode 지원이 종료된다.
- reverse proxy 앞에 조직 표준 identity-aware proxy가 도입된다.

## Related Documents

- [ADR 원장](./README.md)
- [개발 명세 10장 Slack·15장 보안](../test-management-development-spec.md)
- [Sprint 0 인프라·AI spike](../sprint-0/infra-and-ai-spike.md)
