# ADR-0001: Monorepo와 runtime 경계

| 항목 | 값 |
|---|---|
| ADR 번호 | 0001 |
| 날짜 | 2026-08-20 |
| 상태 | Accepted |
| 결정자 범위 | Architecture / BE / FE / OPS |

## Context

Web, API, 비동기 처리, ontology, AI adapter가 같은 업무 계약을 공유한다. 팀과 MVP 규모에서 repository를 서비스별로 분리하면 enum·Schema·migration·prompt version 변경을 원자적으로 검토하기 어렵지만, 모든 책임을 한 process에 넣으면 Slack 수신과 장시간 AI 작업의 장애·확장 경계가 섞인다.

## Decision Drivers

- JSON Schema·OpenAPI·enum의 단일 source와 원자적 변경
- 작은 팀의 낮은 repository·release 관리 비용
- API 요청과 장시간 worker 작업의 장애·resource 격리
- PostgreSQL transaction 경계와 domain 책임의 가시성
- 향후 component별 독립 scaling 가능성

## Considered Options

| Option | 설명 | 선택 여부 |
|---|---|---|
| Monorepo + 분리 runtime | source는 하나, web/api/worker/scheduler는 별도 process | 선택 |
| 서비스별 polyrepo | 각 runtime을 repository와 release 단위로 분리 | MVP에서 제외 |
| 단일 monolith process | HTTP, scheduler, AI job을 한 process에서 실행 | 제외 |

## Decision

하나의 Git repository에서 `apps`, `backend`, `packages`, `prompts`, `evals`, `deploy`, `docs`를 관리한다. 배포 runtime은 `web`, 무상태 `api`, queue consumer `worker`, 단일-leader `scheduler`, `postgres`, `ollama`로 나누고 ClamAV는 optional daemon으로 둔다. Domain/application/infrastructure 책임을 코드 안에서도 분리하며, runtime 분리는 별도 업무 원장이나 microservice별 database를 뜻하지 않는다.

## Consequences

### Positive

- 계약·server·client·fixture 변경을 한 change set으로 검토할 수 있다.
- worker와 API의 CPU·memory·restart 정책을 독립적으로 설정할 수 있다.
- 공통 domain과 adapter contract를 중복 없이 공유한다.

### Negative

- repository 전체 CI가 느려질 수 있고 ownership 규칙이 필요하다.
- 잘못된 import가 runtime 경계를 다시 결합시킬 수 있다.
- 초기에는 배포 단위가 여러 개라 단일 process보다 Compose가 복잡하다.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| package 간 순환 의존 | dependency 방향을 domain → application → infrastructure로 제한하고 architecture test를 추가한다. |
| 모든 변경에 전체 CI 실행 | 경로 기반 빠른 검사와 계약 변경 시 전체 contract test를 병행한다. |
| runtime별 설정 drift | 공통 typed settings와 startup validation을 사용한다. |

## Validation Evidence

- 현재 repository에 [Web/API/Worker apps](../../apps), [공통 packages](../../packages), [backend](../../backend), `evals`, `deploy`, `docs`가 한 root workspace로 연결돼 있다.
- [개발 명세의 실행 component와 권장 구조](../test-management-development-spec.md)는 이 경계와 일치한다.
- [Sprint 0 통합 보고서](../sprint-0/sprint-0-report.md)의 clean setup smoke에서 Web, API, Worker가 한 명령으로 별도 process 기동됐다.
- scheduler entrypoint와 production별 독립 배포·장애 주입은 아직 구현·검증되지 않았다. 이는 선택한 runtime 경계를 뒤집는 충돌이 아니라 후속 구현 범위다.

## Revisit Triggers

- 팀별 release cadence가 분리되고 monorepo 변경 충돌이 지속적으로 delivery를 막는다.
- 한 runtime의 보안·규제 경계가 별도 repository와 독립 release를 요구한다.
- CI 시간이 합의된 목표를 넘고 경로 기반 최적화로 해결되지 않는다.

## Related Documents

- [ADR 원장](./README.md)
- [개발 명세 3장 시스템 구성](../test-management-development-spec.md)
- [Backend foundation](../../backend/README.md)
