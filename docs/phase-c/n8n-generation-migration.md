# n8n Slack 자동화의 QA 보드 전환 경계

## 결론

저장소 밖의 n8n workflow JSON은 기존 작성 정책을 확인하는 로컬 참고 원본이다.
새 플랫폼은 n8n 실행 그래프를 그대로 이식하지 않고 Slack 원장,
typed ontology·RAG, Generation API와 QA 보드 원장을 연결한다. 원본 파일이나 credential
참조는 repository에 복사하지 않는다.

## 재사용하는 규칙

- 정상 흐름과 실제 기능에 해당하는 기본 CRUD를 우선한다.
- Slack 또는 RAG 근거에 명시된 특이사항·주의사항·과거 결함만 회귀 범위에 포함한다.
- 일반적인 예외, 근거 없는 edge case와 수치·정책을 추측하지 않는다.
- 하나의 테스트케이스는 하나의 독립된 검증 목적을 가진다.
- 한 기능 묶음·generation batch에서 5~10개 테스트케이스를 생성한다. 넓은 일감은 여러
  기능 묶음으로 나눠 batch를 반복하며 전체 결과를 10개로 잘라내지 않는다.
- 구조화된 JSON만 Generation API 결과로 허용한다.

## 제거하는 경로

| 기존 n8n 경로 | 새 플랫폼 처리 |
|---|---|
| Jira 티켓 조회·Slack과 티켓 번호 merge | 제거한다. Slack 일감과 thread revision이 source 원장이다. |
| `Depth1/Depth2/Depth3` | 제거한다. 요청 메뉴를 기존 QA 보드 category에 매핑한다. |
| `ID=TC001...` | 제거한다. QA 보드의 workspace key allocator가 case key를 발급한다. |
| 한 문자열의 `Test Step` | 순서가 있는 스텝별 사전조건·테스트 동작으로 변환한다. |
| `Expected Result` 열 | 자동 생성 정책에 따라 케이스 전체 기대결과 하나로 저장한다. |
| Google Sheet 생성·행 추가 | 제거한다. QA 보드 API transaction으로 직접 저장한다. |

## 목표 파이프라인

```text
Slack public allowlist root/thread revision
  → 일감 4개 필드 구조화
  → ACTIVE 지식 + 기존 케이스 + 실행/이슈 RAG context snapshot
  → provider-neutral Generation API
  → strict QA board output validation
  → category 매핑 + 내부 상위일감 생성/재사용
  → 하위 ACTIVE 테스트케이스 5~10개 transaction 저장
  → Slack thread에 QA 보드 링크 게시
```

Generation provider는 category·상위일감·case key, Slack revision과 evidence 원장을
직접 만들 수 없다. 관리 도구가 입력 context에 존재하는 ID만 허용하고 source metadata,
generation job, context snapshot을 함께 저장한다. `AMBIGUOUS`, `SUPERSEDED`,
`RETRACTED` 지식은 확정 근거에서 제외한다.

## Phase 경계

- Phase C는 Slack root/thread 원문과 revision을 빠짐없이 수집한다.
- Phase D는 thread 결정과 QA 히스토리를 typed graph·pgvector·LightRAG로 검색한다.
- Phase E는 이 문서의 작성 정책과 현재 QA 보드 JSON Schema를 Generation API에 적용해
  상위일감과 테스트케이스를 실제 원장에 저장한다.

과거 팀 작성 스타일과 회귀 예시는 [2026 과거 QA CSV corpus 프로파일](./historical-tc-corpus-profile.md)을
사용한다. 이 corpus는 현재 사실의 원장이 아니며 Slack·`ACTIVE` RAG 근거보다 우선할 수 없다.
