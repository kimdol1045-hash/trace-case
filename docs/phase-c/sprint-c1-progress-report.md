# Phase C Sprint C1 진행 보고서

**검증일:** 2026-08-26  
**현재 판정:** `PC-SC1-T01`, `T03`, `T09`, `T10` 완료. 실제 Slack workspace가 필요한
`T02`, `T04`~`T08`은 열려 있다.

## 1. 확정한 수집 정책

- Slack Events API의 HTTP webhook을 사용하고 shortcut이나 Incoming Webhook은 쓰지 않는다.
- public channel allowlist만 수집한다. `channels:history`와 `message.channels` 외 private
  channel·DM scope/event는 manifest에 넣지 않는다.
- Internet에는 `POST /integrations/slack/events`만 노출한다. UI와 일반 API는 무로그인
  제품이므로 public Slack listener와 분리한다.
- raw body 서명, 5분 timestamp window, workspace·channel allowlist와 event/message
  idempotency를 통과한 event만 PostgreSQL job으로 등록한다.
- root, reply, edit, delete를 append-only revision으로 저장하고 실제 parser·thread sync는
  빠른 2xx 응답 뒤 worker가 수행한다.
- Socket Mode 코드는 유지하지만 Phase C 기본 경로가 아니라 HTTP ingress 불가 시 fallback이다.

Manifest와 scope는 Slack 공식 `message.channels`/`channels:history` 계약과 raw body
signature 검증 절차를 다시 대조했다. Live URL verification은 실제 App 설치 task에서
검증한다.

## 2. 기존 구현 감사

이미 repository에는 HTTP/Socket 공통 normalization, public allowlist/private deny,
PostgreSQL source ledger, 수정·삭제 tombstone, event/message 중복 방지, thread pagination,
최근 thread resync, quiet window, Retry-After/DEAD, FastAPI/generated client/Web 운영 화면이
있다. Deterministic fake provider와 disposable PostgreSQL 검증은
[Sprint 5 보고서](../sprint-5/sprint-5-report.md)에 보존돼 있다.

이 코드 근거만으로 실제 자동 수집을 완료 처리하지 않는다. 실제 App 설치, channel 참여,
root→reply→edit→delete와 provider retry를 live workspace에서 재현해야 `T02`, `T04`~`T07`을
닫을 수 있다.

## 3. parser 평가

`pnpm slack:eval`로 `synthetic-slack-golden-v1` 12건을 평가한다.

| 지표 | 결과 |
|---|---:|
| 개발건·요청 메뉴·기능 설명·요청내용 필드 | 41/48, 85.42% |
| 누락 필드 판정 | 12/12 |
| source revision exact | 5/12 |
| 전체 parse object exact | 1/12 |

7개 불일치는 모두 thread에서 확정·보강·번복된 내용을 `요청내용`에 반영하는 기대값과
보수적인 label parser의 차이다. 단순 문자열 규칙으로 최종 결정을 덮어쓰지 않는다.
Phase D에서 Slack revision을 evidence로 갖는 `ACTIVE`, `SUPERSEDED`, `AMBIGUOUS` 지식과
결합하고, 생성 context는 현재 `ACTIVE` 결과만 사용한다.

현재 12건은 합성 회귀 세트다. 실제 팀 표현 10건 이상을 익명화한 dataset version이
없으므로 `PC-SC1-T08`은 미완료다.

## 4. 기존 n8n 자동화 반영

[n8n 전환 경계](./n8n-generation-migration.md)에 따라 Jira와 Google Sheets,
`Depth1/2/3`, 임의 `TC001` 번호는 제거한다. 정상 흐름·기본 CRUD·명시된 특이사항 중심,
근거 없는 예외 제외, 기능 묶음·batch별 5~10개라는 작성 정책만 재사용한다. 사용자가
제공한 [과거 QA CSV corpus 프로파일](./historical-tc-corpus-profile.md)은 약 1.9만 개의
결과 포함 후보 행을 제공하며, 넓은 일감은 여러 batch로 나눠야 한다는 근거가 됐다.

Generation 출력 계약도 현재 QA 보드 기준으로 바꿨다.

- category 후보와 내부 상위일감
- 서머리
- 순서형 스텝과 스텝별 사전조건·테스트 동작
- 자동 생성 케이스 전체 기대결과
- 우선순위와 라벨
- evidence node, 생성 이유, 추론 여부, 확인 필요 사항

관리 도구가 source revision, generation job, context snapshot과 case key를 부여하며
provider가 보드 식별자를 임의 생성하지 못하게 한다.

## 5. 남은 live 준비값

다음 값은 실제 Slack App 관리자 화면과 고정 HTTPS ingress가 준비될 때 secret store에
주입한다. 채팅, Git, 문서에는 실제 값을 기록하지 않는다.

- workspace ID와 public test channel ID
- bot token과 signing secret
- 고정 public HTTPS base URL
- Slack workspace의 App 설치·channel 초대 권한

[HTTP 환경 변수 예시](../../deploy/slack/http.env.example)와
[설치·검증 runbook](../../deploy/slack/README.md)을 사용한다. 임시 Quick Tunnel은 제한된
live smoke에는 쓸 수 있지만 재시작 시 URL이 바뀌므로 상시 Request URL로 사용하지 않는다.
