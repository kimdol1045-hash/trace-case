# Sprint B1 실제 QA 표본 UAT 보고서

## 결과

2026-08-25에 CSV에서 이관한 `WORK-5371`, `WORK-6651`을 실제 사이클 원장으로 실행했다. 라이브러리 원본은 변경하지 않았고, 이관 CSV의 과거 결과를 새 사이클의 WEB 스텝 실행으로 재현했다.

- 사이클 2개, 테스트케이스 19개, 테스트 스텝 73개
- 원본 CSV 결과: `Pass` 72개, `확인불가` 1개
- 실행 원장 결과: `PASS` 72개, `BLOCKED` 1개
- WEB 케이스 결과: `PASS` 18개, `BLOCKED` 1개
- AOS·iOS: 이번 표본이 Web 전용이므로 각각 19개 `UNEXECUTED` 유지
- 이슈 2개와 private 첨부 2개 생성
- 같은 UAT를 두 번 실행해 사이클·이슈·첨부 중복이 생기지 않음을 확인

## 사이클

| 상위일감 | 사이클 | 케이스 | 최종 상태 | 검증한 전이 |
|---|---|---:|---|---|
| `WORK-5371` | `cycle_60494a82b9f94630a4394441a61eac40` `새 테스트 사이클` | 16 | `COMPLETED` | 기존 `IN_PROGRESS` → `COMPLETED` → `IN_PROGRESS`(다시 열기) → `COMPLETED` |
| `WORK-6651` | `cycle_a206991674f44661a57465bd99e92c7c` `[Phase B UAT] (Webapp) 결제 로직 테스트` | 3 | `COMPLETED` | `PLANNED` → `IN_PROGRESS` → `COMPLETED` |

완료 시 AOS·iOS의 의도된 미실행 38개를 확인하고 `acknowledgeIncomplete`로 완료했다. 완료된 사이클에서는 실행 결과가 읽기 전용으로 유지된다.

## BLOCKED와 이슈

`WORK-6651`의 `결제 금액 계산 + 배송비` 케이스에서 “배송지 선택 전/후 배송비 재계산” 스텝을 `BLOCKED`로 기록했다. 원본 CSV 사유는 “국가 단위로 변경되나 일본 마켓만 제공하여 확인 불가”다.

하나의 BLOCKED 실행 시도에 서로 다른 후속 조치 두 건을 연결했다.

| 이슈 | 심각도 | 최종 상태 | 첨부 |
|---|---|---|---|
| `ISSUE-1` 국가별 배송비 재계산 검증 환경 필요 | `MEDIUM` | `CLOSED` | `shipping-recalculation-blocked.txt` |
| `ISSUE-2` 다국가 배송비 검증 데이터 보강 | `LOW` | `IN_PROGRESS` | `multi-market-test-data-request.txt` |

`ISSUE-1`은 `OPEN → IN_PROGRESS → RESOLVED → CLOSED`, `ISSUE-2`는 `OPEN → IN_PROGRESS` 전이가 변경이력에 남았다. 두 첨부는 `PRIVATE`, `BACKEND_PROXY`, `scanStatus=CLEAN`이며 소유자가 각각의 이슈로 기록됐다.

## 원장과 집계 대조

| 지표 | 원장 기대값 | Overview | Reports | 결과 |
|---|---:|---:|---:|---|
| 사이클 | 2 | 2 | 2 | 일치 |
| 케이스 | 19 | 19 | 19 | 일치 |
| 플랫폼 슬롯 | 57 | 57 | 57 | 일치 |
| WEB PASS | 18 | 18 | 18 | 일치 |
| WEB BLOCKED | 1 | 1 | 1 | 일치 |
| AOS·iOS UNEXECUTED | 38 | 38 | 38 | 일치 |
| LOW 미종료 이슈 | 1 | 1 | 1 | 일치 |
| MEDIUM 종료 이슈 | 1 | 1 | 1 | 일치 |

스텝 원장은 72 PASS·1 BLOCKED이고 Overview/Reports는 케이스×플랫폼 슬롯을 집계하므로 WEB 18 PASS·1 BLOCKED로 표시된다. 이 차이는 집계 단위의 차이이며 데이터 손실이 아니다. 전체 통합 상태는 사용자 정책대로 `UNEXECUTED`와 `BLOCKED`를 통합 판정에서 제외해 `PASS`다.

## 재현 방법

```bash
# 읽기 전용 사전 점검
node scripts/python.mjs scripts/run_phase_b_uat.py --csv /path/to/source.csv

# 실제 원장에 적용; 재실행해도 같은 사이클·이슈·첨부를 재사용
node scripts/python.mjs scripts/run_phase_b_uat.py --csv /path/to/source.csv --apply
```

[Phase B UAT 스크립트](../../scripts/run_phase_b_uat.py)는 기본 dry-run이며 라이브러리나 기존 데이터를 초기화하지 않는다. 원본 CSV는 저장소 밖에 두고 경로를 명시해야 한다. 적용 모드도 `WORK-5371`, `WORK-6651` 범위만 사용한다.

## 자동 품질 검사

UAT와 문서 반영 후 전체 `pnpm check`를 통과했다.

- TypeScript·Python lint와 typecheck 통과
- Web 101개 통과, 환경 의존 E2E 1개 제외
- Python 126개 통과, PostgreSQL 표식 테스트 15개 제외
- strict schema 5개와 synthetic fixture 12개 검증 통과
- ADR 17개와 연결 계약 검증 통과
- OpenAPI와 생성 TypeScript client 최신 상태 확인

## 남은 수동 게이트

자동 UAT로 `PB-SB1-T02`부터 `PB-SB1-T06`까지 검증했다. 다음 항목은 실제 QA 담당자의 사용 경험이 필요하므로 완료 처리하지 않았다.

- [ ] 사용 가이드만 보고 테스트 라이브러리 → 사이클 → 실행 → 이슈 흐름을 수행한다.
- [ ] 발견한 피드백을 blocker·major·minor로 분류한다.
- [ ] blocker·major를 수정한 뒤 전체 UAT를 다시 수행한다.
- [ ] 기능 동결 기준과 Phase C 진입 결정을 기록한다.
