# 테스트케이스 관리 및 자동 생성 플랫폼 기획서

| 항목 | 내용 |
|---|---|
| 문서 상태 | 확정안 반영 v0.4 |
| 작성일 | 2026-08-20 |
| 제품 성격 | Zephyr형 테스트케이스 관리·실행·이슈 추적 플랫폼 |
| 1차 목표 | QA 테스트 자산을 구조화하고 사이클 단위로 실행·추적 |
| 2차 목표 | 별도 Generation API가 Slack `일감`을 읽어 테스트케이스를 자동 생성·등록 |
| 3차 목표 | 일감·대화 속 결정·테스트 히스토리를 QA 온톨로지로 축적·재사용 |

### 0.1 2026-08-20 최종 상호작용 정책

이 절은 이후 상세 설명과 충돌할 때 우선한다. 영속 aggregate·cycle scope 결정은 [ADR-0016](./adr/0016-flat-category-work-items-and-start-gated-cycles.md), 구현 기준은 [개발 명세 v1.2](./test-management-development-spec.md)를 따른다.

- Test Library는 `평면 카테고리 → 상위 일감 → 테스트케이스` 구조다. 별도 도메인 그룹과 폴더 depth를 사용자 화면에 노출하지 않는다.
- 상위 일감과 하위 case는 각각 클릭 가능한 Jira형 상세 화면을 가진다. 같은 상위 일감 안 case reorder와 다른 상위 일감으로의 drag 이동을 지원한다.
- case row는 한 번 클릭하면 상세로 이동한다. summary 수정·삭제는 목록에 상시 노출하지 않고 hover/focus 시 우측으로 펼쳐지는 contextual action drawer에서 제공하며 활성 상태 filter와 고정 action 열을 두지 않는다.
- 수동 case step은 `사전조건 / 테스트 동작 / 기대 결과`만 사용하며 테스트 데이터 열과 library case의 issue 연결을 두지 않는다. 자동 생성 API만 case 전체 기대결과를 사용할 수 있다.
- label은 Jira형 중복 없는 문자열 집합이고 변경 이력에는 field·before·after가 보인다.
- 사이클 범위는 상위 일감 또는 label이다. 범위 선택은 생성 화면에만 목록으로 제공하고 생성 뒤에는 확정 범위만 보인다. release·build·environment·end date는 사용하지 않고 미진행·진행 중·완료 상태 카드와 검색 table로 목록을 탐색한다.
- 미진행 사이클에서는 결과·비고·issue link가 잠긴다. 시작 후 WEB·AOS·iOS 결과, BLOCKED 비고와 cycle execution issue link를 기록·집계한다.
- 사이클 실행은 상세와 분리된 `queue / case script / result rail` 화면이며 비고는 가장 오른쪽에 둔다. 이슈도 공통 목록 landing과 Jira형 별도 상세 route로 분리한다.
- 별도 Settings 화면과 내장 AI 생성 화면은 두지 않는다. 연동 상태·연결점은 Integrations에서 관리한다.
- Overview와 Reports는 cycle status, work item/label scope, 시작된 플랫폼 결과, BLOCKED 비고와 열린 내부 issue를 우선 표시한다. Overview에는 사이클 실행 CTA를 두지 않고 Report KPI는 실제 원장/mock 상태에서 계산한다.

## 1. 배경

현재 개발 요청은 Slack의 `일감` 형식으로 전달되고, QA 담당자가 이를 해석해 테스트케이스를 별도로 작성해야 한다. 테스트케이스, 실행 결과, 결함 이슈가 분산되면 다음 문제가 생긴다.

- 테스트케이스의 최신 내용과 수정 이력을 확인하기 어렵다.
- 메뉴와 기능별 테스트 자산을 계층적으로 관리하기 어렵다.
- 어떤 개발 요청에서 어떤 테스트케이스가 만들어졌는지 추적하기 어렵다.
- 테스트 실행 결과와 실패 이슈가 분리되어 재현 정보가 누락된다.
- 반복 QA 시 같은 테스트케이스를 다시 구성해야 한다.
- 일감 본문보다 Slack thread에서 구체화·변경된 결정이 많지만 다음 업무에서 검색·재사용하기 어렵다.

이를 해결하기 위해 테스트 라이브러리, 실행 사이클, 이슈, Slack 기반 자동 생성, QA 지식 온톨로지를 하나의 플랫폼으로 제공한다.

## 2. 제품 목표

### 2.1 핵심 목표

1. 단일 팀 워크스페이스에서 테스트케이스를 평면 카테고리와 상위 일감 기준으로 관리한다.
2. 모든 생성·수정 이력과 시각을 기록한다.
3. 테스트 사이클을 구성하고 케이스별 실행 상태와 결과를 관리한다.
4. 실패한 실행에서 재현 정보가 포함된 이슈를 만들고 연결한다.
5. Slack `일감`을 기반으로 별도 Generation API가 테스트케이스를 만들고 관리 도구에 즉시 등록한다.
6. 일감 → 테스트케이스 → 실행 → 이슈의 추적 관계를 유지한다.
7. Slack thread의 결정사항과 기존 테스트·실행·이슈 히스토리를 출처가 있는 온톨로지로 구축한다.
8. 다음 테스트케이스 작성 시 유효한 결정과 과거 품질 리스크를 검색해 재사용한다.

### 2.2 성공 지표

- 테스트케이스 작성에 걸리는 평균 시간 50% 이상 감소
- Slack 일감과 연결된 테스트케이스 비율 90% 이상
- 실패 실행 중 이슈 또는 사유가 연결된 비율 95% 이상
- 자동 생성 케이스의 실제 사이클 사용률 70% 이상
- 테스트 사이클 종료 시 미실행 케이스 5% 이하
- AI 생성 케이스 중 근거 출처가 하나 이상 표시된 비율 100%
- 현재 유효하다고 판별된 thread 결정사항과 테스트케이스 간 추적 링크 보유율 90% 이상

### 2.3 MVP에서 제외하는 범위

- 테스트 자동화 스크립트의 직접 실행 및 CI/CD 오케스트레이션
- 복수 조직·프로젝트 관리
- 외부 Jira 연동
- 로그인, 개인 계정, 역할별 승인 체계
- 모바일 네이티브 앱

자동화 테스트 실행 연동은 후속 단계에서 확장한다. Jira 연동은 장기 범위에서도 제외한다.

## 3. 이용 범위와 운영 방식

이 도구는 한 팀이 사용하는 단일 워크스페이스로 운영하며 로그인 기능을 두지 않는다.

- 접속한 팀원 누구나 도메인, 테스트케이스, 이슈를 생성·수정할 수 있다.
- 누구나 사이클을 만들고 시작·종료하며 테스트를 실행할 수 있다.
- 테스트케이스와 온톨로지에 승인 단계는 두지 않는다.
- 생성·수정·삭제 시각과 변경 버전은 기록하지만 웹 사용자의 신원은 필수 기록하지 않는다.
- Slack에서 유입된 데이터는 Slack 작성자 ID를 출처 메타데이터로 보존한다.
- 무로그인 UI와 일반 API는 사내망 또는 VPN 안에서만 접근하게 한다. Slack 호출이 필요한 `/integrations/slack/events` 경로만 public ingress로 분리한다.
- 운영 설정 변경도 모든 팀원에게 허용하되 변경 시각과 전후 값을 감사 로그로 남긴다.

## 4. 핵심 용어

| 용어 | 정의 |
|---|---|
| 워크스페이스 | 팀 전체가 공유하는 단일 테스트 업무 공간 |
| 테스트 라이브러리 | 테스트 도메인과 테스트케이스로 구성된 저장소 |
| 테스트 도메인 | 메뉴·업무·기능별 계층 구조. 구현 원장에서는 folder entity를 사용하되 사용자 화면에는 `도메인`으로 표시 |
| 테스트케이스 | 재사용 가능한 테스트 정의 |
| 테스트케이스 버전 | 특정 시점의 테스트케이스 내용 스냅샷 |
| 사이클 | 특정 배포·스프린트·QA 기간에 실행할 케이스 묶음 |
| 실행 항목 | 사이클에 포함된 테스트케이스와 담당자·상태 정보 |
| 실행 시도 | 실행 항목을 실제로 수행한 개별 기록. 재실행 시 새 시도가 추가됨 |
| 이슈 | 실패 또는 확인이 필요한 실행에서 생성한 결함·문의 기록 |
| 일감 | Slack에 등록되는 개발 요청 원문 |
| Slack thread | 일감 원문에 달린 답글 전체와 그 수정 이력 |
| 지식 노드 | 기능, 요구사항, 결정, 제약, 위험, 케이스, 이슈 등 온톨로지의 개체 |
| 지식 관계 | `검증한다`, `영향을 준다`, `대체한다`처럼 노드를 연결하는 의미 관계 |
| 근거 | 지식의 출처가 된 Slack 메시지, 케이스 버전, 실행 또는 이슈 |

## 5. 전체 업무 흐름

```text
Slack 일감 + thread ──→ 원문·수정 이력 보존 ──→ 결정·요구사항·제약 자동 추출
                                                    ↓ 상태·충돌 자동 판별
기존 케이스 + 실행 + 이슈 히스토리 ───────────→ QA 지식 온톨로지
                                                    ↓ 관련 지식 검색
새 Slack 일감 ──────────────────────────────→ AI 테스트케이스 자동 생성
                                                    ↓ 즉시 등록·수정 가능
                                               테스트 라이브러리
                                                    ↓
                                         테스트 사이클 → 실행 → 이슈
```

수동으로 작성한 테스트케이스와 AI가 생성한 테스트케이스는 동일한 라이브러리에 저장한다. 승인 단계는 없으며 생성 출처만 구분한다.

## 6. 기능 요구사항

### 6.1 단일 워크스페이스

- 복수 프로젝트 생성·전환 기능은 제공하지 않는다.
- 테스트케이스와 이슈 식별자는 각각 `TC-000123`, `ISSUE-000123` 형식으로 자동 발급한다.
- 설정 화면에서 팀명, 기본 실행 환경, Slack 수집 채널, 케이스 prefix를 관리한다.
- 로그인과 사용자·역할 관리 화면은 제공하지 않는다.
- 모든 팀원이 동일한 기능을 사용하며 누구나 사이클을 생성·실행·완료할 수 있다.

### 6.2 도메인 기반 테스트 라이브러리

- 테스트 도메인은 루트 아래에 제한 없이 하위 도메인을 가질 수 있다.
- 생성, 이름 변경, 이동, 정렬 순서 변경이 가능해야 한다.
- 테스트케이스는 drag and drop으로 다른 도메인에 이동할 수 있다.
- 도메인 이동 시 하위 도메인과 테스트케이스도 함께 이동한다.
- 현재 위치를 breadcrumb로 표시한다.
- 도메인별 테스트케이스 수와 하위 전체 케이스 수를 표시한다.
- 라이브러리 화면은 좌측 도메인 tree와 우측 case list의 2-pane으로 구성하며 별도 도메인 상세 pane은 두지 않는다.
- 내용이 있는 도메인은 즉시 삭제하지 않고 이동 또는 보관을 안내한다.
- 도메인 삭제는 soft delete로 처리하며 누구나 복원할 수 있다.

권장 초기 구조 예시는 다음과 같다.

```text
워크스페이스
├── 회원
│   ├── 가입
│   ├── 로그인
│   └── 권한
├── 주문
│   ├── 장바구니
│   ├── 결제
│   └── 취소·환불
└── 공통
    ├── 접근성
    └── 오류 처리
```

### 6.3 테스트케이스

#### 필수 필드

| 필드 | 설명 | 규칙 |
|---|---|---|
| 식별자 | 시스템 자동 발급 ID | 워크스페이스 내 유일, 변경 불가 |
| 서머리 | 테스트 목적을 표현하는 제목 | 필수, 1~200자 |
| 사전조건 | 실행 전 필요한 계정·데이터·환경 | 필수, rich text 허용 |
| 테스트스텝 | 사용자가 수행할 순서형 단계 | 1개 이상, drag and drop 순서 변경 |
| 기대결과 | 수동 작성은 스텝별 결과, 자동 생성 API는 케이스 전체 결과 | `PER_STEP` 또는 `CASE` mode 중 하나, 필수 |
| 도메인 | 라이브러리 내 위치 | 필수 |
| 상태 | `ACTIVE`, `DEPRECATED` | 생성 즉시 `ACTIVE` |

수동 작성의 기본 mode는 `PER_STEP`이며 각 행이 `테스트스텝 / 테스트 데이터 / 기대결과`를 가진다. 향후 자동 생성 API가 등록하는 케이스만 `CASE` mode로 전체 기대결과 하나를 사용한다. 사용자가 수동으로 만드는 화면에는 AI mode 선택을 노출하지 않는다.

| 순서 | 테스트스텝 | 테스트 데이터 | 기대결과 |
|---:|---|---|---|
| 1 | 로그인 화면에서 정상 계정 정보를 입력한다. | 정상 테스트 계정 | 입력값이 유효 상태로 표시된다. |
| 2 | 로그인 버튼을 누른다. |  | 홈 화면으로 이동하고 로그인한 사용자명이 표시된다. |

#### 보조 필드

- 우선순위: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`
- 테스트 유형: 기능, 회귀, 권한, UI, API, 성능 등
- 태그
- 담당 QA 표시명(선택, 자유 텍스트)
- 생성 출처: 수동, Slack AI, 파일 가져오기, API
- 원본 일감 링크
- 생성 시 AI가 발견한 확인 필요 사항

#### 작성과 수정 이력

- 모든 테스트케이스에 `created_at`, `updated_at`을 기록한다.
- 내용 수정 시 테스트케이스 버전을 1씩 증가시키고 이전 버전 전체를 보존한다.
- 버전별 변경 시각, 변경 필드, 변경 사유를 확인할 수 있어야 한다.
- 동시에 편집했을 때 마지막 저장이 앞선 변경을 덮지 않도록 optimistic locking을 적용한다.
- 삭제는 soft delete로 처리하고 삭제 시각을 기록한다.

### 6.4 테스트 사이클과 QA 실행

#### 사이클 필드

- 사이클명
- 설명
- 시작일
- 범위: `WORK_ITEM` 또는 Jira형 `LABEL`
- 상태: `PLANNED`, `IN_PROGRESS`, `COMPLETED`
- 포함된 테스트케이스와 고정된 케이스 버전

#### 사이클 구성

- 새 사이클 생성 화면에서 상위 일감 또는 라벨 하나를 목록으로 선택한다.
- 생성 후 상세 화면은 확정된 범위와 포함 케이스 snapshot만 표시하고 대상 selector를 다시 노출하지 않는다.
- 중복된 테스트케이스는 한 번만 추가한다.
- 사이클 추가 시 현재 테스트케이스 버전을 스냅샷으로 고정한다.
- 라이브러리 케이스가 수정되어도 진행 중인 사이클 내용은 자동으로 바뀌지 않는다.
- 누구나 최신 버전 반영을 선택할 수 있으며 변경 내역을 보여준다.
- 누구나 사이클을 생성하고 상태를 변경하며 실행 결과를 기록할 수 있다.

#### 실행 상태

| 상태 | 의미 |
|---|---|
| `UNEXECUTED` | 아직 실행하지 않음 |
| `WIP` | 실행 중 |
| `PASS` | 기대 결과와 일치 |
| `FAIL` | 기대 결과와 불일치 |
| `BLOCKED` | 환경·선행 조건 문제로 실행 불가 |

#### 실행 기록

- 실행 화면은 좌측 테스트 큐, 중앙 case script, 우측 WEB·AOS·iOS 결과·연결 이슈·비고 rail로 분리한다.
- `FAIL` 또는 `BLOCKED` 선택 시 비고에 실제 결과 또는 차단 사유를 필수로 입력한다.
- `FAIL` 선택 직후 이슈 생성 화면으로 연결한다.
- 재실행 시 이전 결과를 덮지 않고 새 실행 시도를 추가한다.
- 각 실행 시도에 선택적으로 입력한 실행자 표시명과 시작·종료 시각, 상태 변경 시각을 기록한다.
- 사이클 목록·상세·실행 화면에서 전체·플랫폼·상위 일감·라벨별 진행률을 확인한다.

### 6.5 이슈와 테스트케이스 연결

이슈는 테스트 실행 중 발견된 결함을 재현할 수 있도록 다음 필드를 가진다.

| 필드 | 필수 여부 | 설명 |
|---|---:|---|
| 이슈 ID | 자동 | 워크스페이스 내 자동 발급 |
| 서머리 | 필수 | 문제를 한 문장으로 설명 |
| 사전조건 | 필수 | 문제가 발생하기 위한 상태·데이터·환경 |
| 테스트스텝 | 필수 | 재현 절차. 순서형 단계 |
| 기대결과 | 필수 | 정상 동작 기준 |
| 실제결과 | 필수 | 실제 발생한 현상 |
| 첨부파일 | 선택 | 이미지, 영상, 로그, 문서 |
| 상태 | 필수 | `OPEN`, `IN_PROGRESS`, `RESOLVED`, `CLOSED`, `REOPENED` |
| 심각도 | 필수 | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| 담당자 | 선택 | 처리 담당자 |

#### 연결 규칙

- 하나의 테스트케이스는 여러 이슈와 연결될 수 있다.
- 하나의 이슈도 여러 테스트케이스 또는 실행 시도와 연결될 수 있다.
- 실행에서 이슈를 만들면 서머리, 사전조건, 테스트스텝, 기대결과를 케이스 버전에서 자동 복사한다.
- 실제결과와 실행 첨부파일도 이슈 생성 화면에 자동 채운다.
- 연결된 이슈 상태를 테스트케이스 및 사이클 화면에서 확인할 수 있다.
- 이슈가 종료되어도 테스트 실행과의 연결 이력은 삭제하지 않는다.

### 6.6 첨부파일

- 테스트 실행과 이슈에 파일을 여러 개 첨부할 수 있다.
- 파일명, 파일 크기, MIME type, 업로드 시각을 기록한다.
- 이미지와 영상은 가능한 경우 미리보기를 제공한다.
- 운영 저장소는 private Cloudflare R2 bucket을 사용하고 로컬 개발은 S3 호환 MinIO를 사용한다.
- 브라우저는 백엔드에서 짧은 수명의 presigned PUT URL을 발급받아 파일을 R2로 직접 전송한다. 업로드 완료 후 백엔드가 size, MIME type, SHA-256 메타데이터를 검증한 경우에만 DB에 첨부 레코드를 확정한다.
- bucket은 public access를 허용하지 않으며 다운로드는 백엔드가 권한과 soft delete 상태를 확인한 뒤 비공개 객체를 중계한다.
- object key는 소유자 유형과 ID를 포함하는 충돌 방지 경로로 생성하고 DB에는 `storage_provider`, `object_key`, checksum, MIME type, size를 기록한다.
- 파일 자동 삭제를 설정하지 않아 기간 제한 없이 보존한다. 애플리케이션에서 삭제하면 DB에 soft delete를 기록하고 객체도 삭제한다.
- R2 Standard 무료 사용량은 월 10GB-month 저장, Class A 100만 회, Class B 1,000만 회이며 인터넷 egress는 무료다. 이 한도를 넘으면 사용량 기반 비용이 발생하므로 완전한 무제한 무료 저장소는 아니다.
- MVP 기본 제한은 파일당 50MB이며 환경 변수로 조정할 수 있다.
- 운영 credential은 서버에만 보관하고 브라우저에는 access key나 secret을 전달하지 않는다. 업로드 URL도 만료 전에는 bearer token처럼 취급한다.
- 허용 origin, PUT/GET/HEAD method, `content-type`, `x-amz-meta-sha256` header를 R2 CORS 정책에 명시한다.
- 삭제 시 파일 메타데이터와 감사 로그를 남긴다.

### 6.7 검색과 필터

- 식별자와 서머리 통합 검색
- 도메인, 상태, 우선순위, 유형, 태그, 작성자, 수정일 필터
- 일감 링크가 있는 케이스 또는 AI 생성 케이스만 필터
- 사이클에서 실행 상태, 담당자, 연결 이슈 상태 필터
- 현재 필터를 저장된 뷰로 보관하는 기능은 2차 범위로 둔다.

### 6.8 대시보드와 리포트

- 진행 중인 사이클과 전체 진행률
- `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN` 비율
- 담당자별 남은 테스트 수
- 열린 이슈 수와 심각도 분포
- 도메인별 실패율

## 7. Slack 일감 기반 테스트케이스 자동 생성

### 7.1 관찰되는 일감 형식

Slack `일감`은 이미 다른 사용자가 작성한 글이므로 이 제품이 작성 템플릿을 강제하거나 작성자에게 입력을 요구하지 않는다. 현재 일감에서 주로 관찰되는 항목은 다음과 같다.

| 입력 항목 | 용도 |
|---|---|
| 개발건 | 테스트 대상 변경 사항 |
| 요청 메뉴 | 테스트케이스를 저장할 메뉴·도메인 후보 |
| 기능 설명 | 기존 기능과 변경 기능의 맥락 |
| 요청내용 | 상세 요구사항과 완료 조건 |

예시:

```text
[일감]
개발건: 비밀번호 재설정 기능 개선
요청 메뉴: 회원 > 계정관리 > 비밀번호
기능 설명: 이메일 인증 후 비밀번호를 변경하는 기능
요청내용: 인증번호 유효시간을 5분으로 변경하고 5회 오류 시 인증을 잠근다.
```

### 7.2 채널 자동 수집 방식

MVP는 사용자의 shortcut 없이 Slack 채널을 자동 감지한다. 구현 방식은 메시지를 Slack으로 보내는 Incoming Webhook이 아니라, Slack Events API가 우리 서비스의 public HTTP endpoint를 호출하는 event webhook이다. 운영 환경에 public HTTPS ingress가 없다면 동일한 event subscription을 Socket Mode로 수신해 신규 비용을 만들지 않는다.

1. 설정 화면에서 일감이 올라오는 전용 Slack 채널을 allowlist로 등록한다.
2. Slack App을 해당 채널에 참여시키고 `message.channels` 이벤트를 구독한다.
3. 새 root 메시지 이벤트가 오면 전용 채널의 모든 root 메시지를 일감 후보로 수집한다.
4. 메시지의 `thread_ts`가 있으면 기존 일감의 답글로 연결하고 thread 원문을 증분 갱신한다.
5. 메시지 수정·삭제 이벤트도 source revision으로 기록한다.
6. Slack 서명과 event timestamp를 검증하고 `event_id`로 중복 처리를 방지한 뒤 빠르게 `HTTP 200`을 반환한다.
7. 실제 파싱·온톨로지 갱신·테스트 생성은 background job에서 처리한다.

항목명과 순서가 달라도 의미 기반으로 파싱한다. 일부 항목이 없더라도 작성자에게 보완을 요청하지 않고, root와 thread에서 확인 가능한 내용만 사용하며 나머지는 `확인 필요 사항`으로 남긴다. 기획 문서, 디자인, 코드 변경 내역 등 추가 자료는 사용자가 별도로 제공한 경우에만 해당 생성 작업의 참고 자료로 사용한다.

**2026-08-21 구현 경계:** Sprint 5는 HTTP raw-body 서명·timestamp와 URL verification, Socket Mode 공통 normalization, public allowlist/private default deny, event 수신과 append-only source revision, thread 증분 sync, quiet window, 4개 필드 parser, retry/DEAD, 실제 FastAPI/generated client/Web 조회까지 구현했다. Deterministic fake Slack과 disposable PostgreSQL에서 이 흐름은 재현됐지만 실제 Slack App 설치·test channel 자동 수집은 credential 부재로 검증하지 않았다. 이 절의 ontology 판별과 테스트케이스 자동 생성·Slack 결과 답글은 Sprint 6~8 후속 범위다.

#### 자동 생성 시점

- 새 root 일감은 마지막 메시지 이후 설정 가능한 quiet window가 지난 뒤 최초 생성한다. MVP 기본값은 10분이다.
- thread에 새 답글이나 수정이 생기면 quiet window 후 온톨로지와 관련 테스트케이스를 다시 계산한다.
- 마지막 자동 생성 이후 사람이 수정하지 않은 AI 케이스는 새 버전으로 자동 갱신할 수 있다.
- 사람이 수정한 케이스는 자동으로 덮어쓰지 않고 변경 제안을 표시한다. 누구나 제안을 반영하거나 무시할 수 있다.
- 이벤트 누락에 대비해 최근 일감 thread를 주기적으로 재동기화한다.

### 7.3 AI 생성 흐름

1. 개발건, 요청 메뉴, 기능 설명, 요청내용을 구조화한다.
2. 일감 thread에서 현재 유효하다고 자동 판별된 결정과 요구사항을 조회한다.
3. 같은 메뉴·기능의 기존 테스트케이스, 과거 실패 이슈, 실제 실행 결과를 온톨로지에서 조회한다.
4. 정상 흐름과 기본 CRUD를 우선하고, Slack 또는 현재 유효한 RAG 근거에 명시된 특이사항·과거 결함만 회귀 케이스로 생성한다.
5. 각 케이스를 현재 QA 보드의 `서머리`, 스텝별 `사전조건·테스트 동작`, 케이스 전체 `예상결과`, `우선순위`, `라벨` 형식으로 만들고 기능 묶음별 5~10개를 반환한다. 넓은 일감은 여러 batch로 분리한다.
6. 각 케이스에 참고한 결정, 기존 케이스, 과거 이슈의 근거 링크를 연결한다.
7. 요청 메뉴를 도메인 매핑 규칙에 대입한다.
8. Slack 일감을 내부 상위일감으로 생성·재사용하고 현재 QA 보드에 하위 `ACTIVE` 테스트케이스로 즉시 저장한다.
9. 생성 결과 링크를 Slack thread에 답한다.

승인 과정은 없으며 자동 생성된 케이스도 곧바로 사이클에 포함할 수 있다. 팀원은 언제든 내용을 수정·비활성화·삭제할 수 있다.

### 7.4 생성 품질 규칙

- 한 케이스는 하나의 명확한 검증 목적을 가진다.
- 스텝은 실행 순서대로 작성하고 모든 스텝 완료 후 관찰 가능한 예상결과를 케이스당 하나 둔다.
- 정상 흐름과 기본 생성·조회·수정·삭제를 우선하되 실제 기능에 적용되는 동작만 작성한다.
- Slack·RAG 근거에 없는 일반적인 예외와 추측성 edge case는 만들지 않는다.
- Slack·RAG에 명시된 특이사항, 제약, 과거 실패 이슈는 해당 근거를 연결한 회귀 케이스로 작성할 수 있다.
- 일감에 없는 계정, 정책, 수치 조건을 사실처럼 만들지 않는다.
- 정보가 부족하면 임의로 확정하지 않고 `확인 필요 사항`으로 표시한다.
- 정상 시나리오뿐 아니라 입력 오류, 경계값, 권한, 중복 요청, 실패 복구를 검토한다.
- 동일 목적과 사전조건을 가진 기존 케이스가 있으면 중복 후보로 표시한다.
- 민감정보나 실제 사용자 데이터는 생성 결과에 포함하지 않는다.
- 폐기되었거나 다른 결정으로 대체된 지식은 현재 요구사항처럼 사용하지 않는다.
- 생성 근거를 찾지 못한 내용은 AI 추론임을 표시하고 확정 사실과 구분한다.

기존 n8n 자동화는 작성 정책을 확인하는 참고 자료로만 사용한다. Jira 조회와 병합,
`Depth1/2/3`·Excel 열 출력, Google Sheets 생성·기록은 새 파이프라인에 포함하지 않는다.
Slack root·thread와 RAG context를 입력으로 사용하고 출력은 이 제품의 QA 보드 API에
직접 저장한다.

과거 QA CSV는 팀의 경로·항목 분할, 간결한 확인 문체와 회귀 관점을 찾는 corpus로
사용한다. 과거 케이스는 현재 요구사항의 사실 근거가 아니며 사람 이름·Jira 링크를
익명화하고 중복 문구를 군집화한 뒤 기존 케이스·실행·이슈 retrieval 경로로만 제공한다.

### 7.5 중복 방지와 추적성

- Slack `workspace_id + channel_id + message_ts`를 수집 원문의 고유 키로 사용한다.
- 같은 일감이 다시 수집되면 새 케이스를 중복 생성하지 않고 기존 생성 작업과 연결한다.
- thread 갱신으로 재생성하면 생성 버전을 올리고 이전 결과와 차이를 기록한다.
- 케이스에 Slack permalink, 생성 모델, 프롬프트 버전, 생성 시각을 저장한다.
- Slack 메시지가 수정되면 source revision과 영향받은 케이스 버전을 연결한다.
- 생성 작업마다 실제로 참조한 지식 노드·관계의 버전 목록을 저장해 결과를 재현할 수 있게 한다.

### 7.6 도메인 자동 매핑

- `요청 메뉴`와 도메인 전체 경로를 정규화하여 정확 일치를 우선한다.
- 설정 화면에서 메뉴 별칭을 등록할 수 있다. 예: `마이페이지` → `회원 > 계정관리`.
- 복수 후보가 나오면 가장 높은 점수의 경로를 사용하고 다른 후보를 생성 메타데이터에 남긴다.
- 일치 경로가 없으면 `미분류` 도메인에 저장하고 새 도메인을 자동 생성하지 않는다.

### 7.7 QA 온톨로지 설계

온톨로지는 단순한 문서 모음이나 embedding 검색 인덱스가 아니다. 기능·요구사항·결정·테스트·결함의 의미 관계와 시간에 따른 변경을 표현하는 팀 지식 그래프다.

#### 핵심 노드 유형

| 노드 유형 | 예시 | 주요 속성 |
|---|---|---|
| `WORK_ITEM` | 비밀번호 재설정 개선 | 개발건, 요청 메뉴, Slack 원문 |
| `MENU` | 회원 > 계정관리 | 정규화된 디렉토리 경로와 별칭 |
| `FEATURE` | 이메일 인증번호 검증 | 기능명, 설명, 상태 |
| `REQUIREMENT` | 인증번호는 5분간 유효하다 | 내용, 적용 범위, 유효 기간 |
| `DECISION` | 5회 오류 시 인증을 잠근다 | 내용, 결정 상태, 결정자, 결정 시각 |
| `CONSTRAINT` | 잠금 해제는 30분 후 가능 | 제한 조건과 근거 |
| `OPEN_QUESTION` | 관리자 해제 기능이 필요한가 | 질문, 담당자, 처리 상태 |
| `RISK` | 인증번호 brute-force | 위험도와 영향 범위 |
| `TEST_CASE` | 5회 오류 잠금 검증 | 실제 케이스와 버전 연결 |
| `EXECUTION` | Cycle 24의 두 번째 실행 | 상태, 실제결과, 빌드, 환경 |
| `ISSUE` | 잠금 후에도 인증 가능 | 심각도, 상태, 실제결과 |
| `RELEASE` | 2026.08 계정 개선 배포 | 빌드·릴리스 범위 |

#### 핵심 관계 유형

| 관계 | 의미 |
|---|---|
| `WORK_ITEM AFFECTS FEATURE` | 일감이 변경하는 기능 |
| `WORK_ITEM TARGETS MENU` | 일감이 요청한 메뉴 위치 |
| `THREAD DISCUSSES WORK_ITEM` | Slack thread와 일감 연결 |
| `DECISION DERIVED_FROM MESSAGE` | 결정의 직접 근거 메시지 |
| `DECISION REFINES REQUIREMENT` | 대화 중 결정이 요구사항을 구체화함 |
| `DECISION SUPERSEDES DECISION` | 뒤의 결정이 앞의 결정을 대체함 |
| `REQUIREMENT APPLIES_TO FEATURE` | 요구사항의 기능 범위 |
| `TEST_CASE VERIFIES REQUIREMENT` | 케이스가 검증하는 요구사항 |
| `TEST_CASE VERIFIES DECISION` | 케이스가 thread 결정사항을 검증함 |
| `TEST_CASE COVERS RISK` | 케이스가 방어하는 품질 위험 |
| `EXECUTION INSTANCE_OF TEST_CASE` | 실행과 고정 케이스 버전 연결 |
| `ISSUE FOUND_BY EXECUTION` | 실행에서 발견된 이슈 |
| `ISSUE AFFECTS FEATURE` | 결함의 기능 영향 범위 |

#### 지식 상태와 시간

Slack 대화에는 제안, 질문, 동의, 번복이 섞이므로 모델이 대화 맥락과 시간 순서를 함께 보고 상태를 자동 판별한다. 별도의 사람 승인 단계는 두지 않는다.

| 상태 | 의미 | 테스트 생성에서의 사용 |
|---|---|---|
| `ACTIVE` | 대화상 명시적이고 현재 유효한 결정으로 판별 | 생성 근거로 사용 |
| `SUPERSEDED` | 이후 결정으로 대체됨 | 과거 이력으로만 표시 |
| `AMBIGUOUS` | 상충하거나 결론이 없음 | 테스트를 만들지 않고 질문으로 표시 |
| `RETRACTED` | 원문에서 명시적으로 철회되거나 삭제됨 | 사용하지 않음 |

각 지식에는 다음을 기록한다.

- `recorded_at`: 시스템에 기록된 시각
- `valid_from`, `valid_to`: 업무적으로 유효했던 기간
- 자동 추출 confidence와 상태 판별 근거
- Slack 대화에서 식별된 결정 발화자
- 근거 Slack 메시지 permalink와 message timestamp
- 생성·수정·대체 이력
- ontology schema 버전

이 구조로 “예전에는 10분이었지만 현재는 5분” 같은 변경을 잃지 않으면서 최신 결정만 다음 테스트 생성에 사용할 수 있다.

### 7.8 Slack thread 지식화 흐름

```text
원문 메시지 + 답글 + 수정 이력
          ↓
메시지 단위 정규화·민감정보 마스킹
          ↓
결정 / 요구사항 / 제약 / 질문 후보 추출
          ↓
기존 지식과 동일·충돌·대체 관계 자동 판별
          ↓
ACTIVE / AMBIGUOUS / SUPERSEDED / RETRACTED
          ↓
근거 링크가 있는 온톨로지 반영
```

#### 수집 및 갱신 규칙

- 일감의 root 메시지와 thread 답글을 하나의 source bundle로 저장한다.
- 메시지별 작성자, 작성 시각, 수정 시각, 삭제 여부, permalink를 보존한다.
- 수정된 메시지는 기존 원문을 덮지 않고 source revision을 추가한다.
- thread에 새 답글이 생기면 해당 부분만 증분 수집하고 관련 지식 후보를 다시 평가한다.
- 메시지가 삭제되더라도 이미 수집한 원문과 revision은 기간 제한 없이 보존하고 지식 상태를 `RETRACTED`로 재평가한다.
- 명시적으로 뒤집은 최신 결정은 이전 결정을 `SUPERSEDED`로 연결한다.
- 서로 충돌하지만 최종 결론을 판별할 수 없으면 자동 병합하거나 담당자를 호출하지 않고 `AMBIGUOUS`로 표시한다.
- `결정`, `확정`, `그렇게 진행` 같은 표현과 작성 순서, 답글 관계를 함께 사용해 상태를 판별한다.
- 별도 승인·검토·escalation workflow는 제공하지 않는다. 필요한 경우 누구나 지식 상세에서 자동 판별 결과를 수정할 수 있다.

### 7.9 온톨로지 기반 히스토리 검색과 생성

새 테스트케이스 생성 시 구조화된 그래프 탐색과 의미 기반 검색을 함께 사용한다.

1. 요청 메뉴와 기능을 `MENU`, `FEATURE` 노드에 연결한다.
2. 해당 기능에 적용되는 `ACTIVE` 요구사항·결정·제약을 그래프로 조회한다.
3. `SUPERSEDED`, `RETRACTED`, `AMBIGUOUS` 지식과 유효 기간이 끝난 지식은 현재 확정 근거에서 제외한다.
4. 같은 기능을 검증한 `ACTIVE` 테스트케이스와 최근 케이스 버전을 조회한다.
5. 과거 `FAIL`·`BLOCKED` 실행과 연결된 이슈를 조회해 회귀 위험을 찾는다.
6. 자유 서술은 embedding 검색으로 유사 후보를 찾고 그래프 관계로 유효성을 검증한다.
7. 권위, 상태, 기능 근접도, 최신성, 의미 유사도, 과거 결함 심각도로 컨텍스트를 정렬한다.
8. AI가 케이스를 생성하면서 케이스별 `왜 생성했는지`와 근거 노드를 함께 반환한다.
9. 기존 케이스와 의미가 겹치면 신규 생성 대신 재사용·수정 후보로 제안한다.
10. 생성 당시 사용한 지식 snapshot을 저장한다.

#### 정보 우선순위

동일 주제의 정보가 충돌할 때 기본 우선순위는 다음과 같다.

1. 현재 유효한 `ACTIVE` 결정
2. 일감 본문 요구사항과 워크스페이스 테스트 정책
3. 현재 `ACTIVE` 테스트케이스
4. 최근 실제 실행 결과와 미종결 이슈
5. 과거 종료 이슈와 폐기 케이스
6. 미확정 Slack 대화

단, 우선순위가 높더라도 출처가 없으면 생성 컨텍스트에서 제외한다.

### 7.10 온톨로지 운영 원칙

- 관계형 운영 DB를 원장으로 두고 온톨로지는 원장 엔티티를 참조하는 graph projection으로 구성한다.
- raw Slack 원문, 자동 추출된 지식, 현재 활성 지식을 서로 다른 계층으로 분리한다.
- 의미 검색용 embedding은 지식 자체가 아니라 검색 보조 인덱스로 취급한다.
- 사용자는 모든 결정과 AI 생성 케이스에서 원본 근거로 이동할 수 있어야 한다.
- ontology schema와 추출 모델을 버전 관리해 재처리와 결과 비교가 가능해야 한다.
- schema migration은 배포 시 자동 수행하며 별도 승인자나 migration 책임자 workflow를 두지 않는다.
- 잘못되거나 오래된 지식은 물리 삭제보다 `RETRACTED`·`SUPERSEDED` 상태 전이를 우선해 감사 가능성을 유지한다.
- 테스트케이스가 생성되거나 새 버전이 저장되면 관련 `TEST_CASE` 노드와 검증 관계를 갱신한다.
- 실행 시도가 종료되거나 이슈 상태가 바뀌면 `EXECUTION`, `ISSUE`, 위험 관계를 증분 갱신한다.
- projection 갱신 실패는 운영 데이터를 되돌리지 않고 outbox event를 재처리해 최종 일관성을 맞춘다.
- 온톨로지는 감사 로그를 대체하지 않으며, 변경 시각과 전후 값은 별도로 보존한다.

### 7.11 LightRAG 적용 검토안

LightRAG은 Slack 원문과 테스트 히스토리에서 entity·relation을 추출하고 graph+vector hybrid retrieval을 수행하는 보조 엔진 후보로 채택한다. 다만 제품의 테스트케이스, 실행 결과, 이슈, 시간 유효성 상태를 LightRAG 자체에만 맡기지는 않는다.

#### 권장 구성

```text
PostgreSQL
├── 제품 원장: 케이스·사이클·실행·이슈·Slack revision
├── 통제 온톨로지: ACTIVE·SUPERSEDED·AMBIGUOUS 상태와 typed relation
├── pgvector: 제품 자체 의미 검색
└── LightRAG storage: KV·document status·vector·graph table

LightRAG
├── Slack thread·추가 참고자료 indexing
├── entity/relation 자동 추출
├── local/global/hybrid retrieval
└── 검색 context와 citation 반환
```

- 단일 팀·단일 워크스페이스이므로 초기에는 별도 Neo4j 없이 PostgreSQL 한 대로 시작한다.
- LightRAG의 current PostgreSQL class인 `PGGraphStorage`, `PGVectorStorage`, `PGKVStorage`, `PGDocStatusStorage` 조합을 우선 검증한다. `PGGraphStorage`는 Apache AGE extension과 graph 설정이 필요하므로 AGE가 포함된 disposable PostgreSQL에서 storage 초기화·migration·index round trip을 통과하기 전에는 live PostgreSQL adapter 완료로 간주하지 않는다.
- LLM과 embedding은 유료 외부 API 대신 Ollama의 `qwen3:8b`, `bge-m3:latest`를 사용한다. LightRAG 권고에 맞춰 LLM context는 최소 32k로 설정하고 실제 장비에서 속도·메모리를 benchmark한다.
- LightRAG 결과는 검색 후보로 사용하고 `ACTIVE`, `SUPERSEDED`, `AMBIGUOUS` 판정과 케이스 연결은 제품의 통제 온톨로지에서 적용한다.
- LightRAG가 중단되어도 기본 키워드·pgvector·typed graph 검색으로 테스트 생성이 가능해야 한다.
- embedding model과 차원이 바뀔 때 vector index 재생성이 필요하므로 모델명·차원·index 버전을 고정해 기록한다.
- 실제 Slack thread 샘플로 기존 케이스 재발견률, 누락 결정 회수율, 잘못된 관계 비율, citation 정확도를 평가한 후 기본 엔진 채택 여부를 확정한다.
- LightRAG 버전은 고정하고 ontology extraction prompt·storage schema와 함께 migration test를 운영한다.

## 8. 주요 화면

### 8.1 워크스페이스 대시보드

- 미진행·진행 중·완료 사이클 수와 상위 일감·라벨별 진행률
- 시작된 WEB·AOS·iOS 실행 결과와 PASS 비율
- BLOCKED 비고와 열린 주요 이슈
- 카테고리별 테스트 자산 분포
- 즉시 실행 버튼은 두지 않고 사이클·이슈·리포트 상세 탐색 링크만 제공

### 8.2 테스트 라이브러리

- 좌측: 평면 카테고리 목록과 생성·이름 변경
- 우측: 선택 카테고리의 Jira형 상위 일감 카드와 ordered 하위 case 목록
- case row를 한 번 클릭하면 별도 상세·편집 route로 이동한다.
- 같은 상위 일감 안 reorder와 다른 상위 일감으로 drag 이동을 지원한다.
- summary 수정·삭제는 상시 열이 아니라 hover/focus contextual action drawer에서 제공한다.

### 8.3 테스트케이스 편집

- 상단: 뒤로가기, 식별자, 서머리와 생성·수정 시각
- 본문 상단: 클릭 가능한 상위 내부 일감, 우선순위, 담당자, Jira형 라벨
- 스텝 편집기: `사전조건 / 테스트 동작 / 기대결과` 행 추가·삭제·drag 순서 변경
- 수동 생성은 스텝별 기대결과가 기본이며 자동 생성 API로 들어온 `CASE` mode만 전체 기대결과 1개를 표시한다.
- 하단: field별 before/after 변경 이력. 라이브러리 상세에는 이슈 연결을 두지 않는다.
- 저장 즉시 새 버전이 생성되며 변경 사유 입력은 선택이다.

### 8.4 사이클 실행

- 첫 화면: 미진행·진행 중·완료 상태 카드, 검색, 상위 일감·라벨 범위와 진행 bar를 가진 table
- 생성 화면: 다른 상위 일감 또는 라벨을 한 번에 비교·클릭하는 세로 목록과 포함 case 수 preview
- 상세 화면: 확정된 범위, 포함 케이스 snapshot, 시작·완료·재개·삭제
- 별도 실행 화면 좌측: 검색 가능한 고정 테스트 큐
- 별도 실행 화면 중앙: 사전조건과 테스트 동작·기대결과 script
- 별도 실행 화면 우측: WEB·AOS·iOS 상태, cycle 이슈 연결, 가장 오른쪽 비고, 다음 케이스 이동
- 상태 변경 즉시 상단 집계와 progress bar를 갱신하며 PLANNED에서는 실행 입력을 잠근다.

### 8.5 이슈 상세

- 첫 화면: 상태 카드, 검색, 사이클 filter가 있는 공통 table landing
- table row를 클릭하면 별도 Jira형 상세 route로 이동
- 서머리, 사전조건, 재현 스텝, 기대결과, 실제결과, 첨부파일
- 상태, 심각도, 담당자
- 연결된 케이스, 사이클, 실행 시도
- 변경 이력과 코멘트

### 8.6 AI 생성 이력

- 원본 Slack 일감·thread 결정과 자동 생성된 케이스를 나란히 표시
- 생성·재생성 버전별 차이 보기
- 중복 가능성이 있는 기존 케이스 비교
- 케이스를 만든 근거와 과거 이슈를 케이스별로 표시
- 자동 생성 케이스 수정·삭제·재생성
- 수동 편집 케이스와 재생성 결과가 충돌할 때 변경 제안 보기

### 8.7 지식·결정 상태 화면

- Slack thread에서 추출된 `ACTIVE`, `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED` 지식 목록
- 원문 메시지와 앞뒤 대화 맥락
- 기존 요구사항·결정과의 동일·충돌·대체 관계
- 자동 판별 상태와 판별 이유 표시
- 필요한 경우 누구나 상태, 적용 기능·메뉴·유효 기간 수정
- 해당 결정을 검증하는 테스트케이스와 미검증 영역 표시

### 8.8 온톨로지 탐색기

- 메뉴·기능·요구사항·결정·케이스·이슈 관계를 탐색
- 키워드와 의미 기반 통합 검색
- 특정 기능의 현재 유효한 결정만 보기
- 결정의 원본 Slack 메시지와 버전 이력 보기
- 요구사항별 테스트 커버리지와 과거 실패 이슈 보기

## 9. 데이터 모델 초안

모든 주요 테이블은 공통으로 `id`, `created_at`, `updated_at`을 가진다. 로그인하지 않으므로 웹 변경자의 사용자 ID는 필수 저장하지 않는다.

| 엔티티 | 주요 필드 | 관계·설명 |
|---|---|---|
| `workspace_settings` | team_name, case_prefix, issue_prefix, default_environment, generation_quiet_minutes | 단일 워크스페이스 설정 1행 |
| `folders` | parent_id, name, sort_order, deleted_at | 화면에는 `도메인`으로 표시하는 self-reference hierarchy |
| `test_cases` | folder_id, case_key, current_version_id, status, generation_managed, manual_edit_at | 케이스의 고정 식별 정보 |
| `test_case_versions` | test_case_id, version, summary, precondition, expected_result_mode, case_expected_result, priority, source_type, change_reason | `PER_STEP`·`CASE` mode를 포함한 버전별 불변 스냅샷 |
| `test_steps` | test_case_version_id, sequence, action, test_data, expected_result | 수동 작성의 기대결과를 포함한 버전별 순서형 스텝 |
| `test_case_tags` | test_case_id, tag_id | 태그 다대다 관계 |
| `test_cycles` | name, build, environment, starts_at, ends_at, status | QA 실행 묶음 |
| `cycle_items` | cycle_id, test_case_id, test_case_version_id, assignee_label | 사이클에 고정된 케이스 버전 |
| `execution_attempts` | cycle_item_id, attempt_no, status, actual_result, executor_label, started_at, finished_at | 재실행을 포함한 실행 기록 |
| `issues` | issue_key, summary, precondition, expected_result, actual_result, status, severity, assignee_label | 내부 결함 기록 |
| `issue_steps` | issue_id, sequence, action | 이슈 재현 스텝 |
| `issue_links` | issue_id, test_case_id, execution_attempt_id | 케이스·실행 연결 |
| `attachments` | owner_type, owner_id, storage_provider, object_key, checksum, file_name, mime_type, size, deleted_at | R2·MinIO 실행·이슈 첨부 메타데이터 |
| `integration_settings` | provider, enabled, encrypted_credentials, settings | Slack 등 외부 연동 정보 |
| `slack_channel_allowlist` | slack_workspace_id, channel_id, channel_name, include_private | 자동 수집 허용 채널 |
| `slack_events` | slack_workspace_id, channel_id, event_id, event_type, event_ts, payload, status, received_at, processed_at | workspace/event ID와 workspace/channel/event ts 이중 idempotency, 재처리 |
| `slack_work_items` | slack_workspace_id, channel_id, message_ts, permalink, root_seen, source_version, parse_status, quiet_due_at | Slack root identity와 현재 파생 상태; 원문 revision은 별도 append-only table에 보존 |
| `slack_threads` | slack_work_item_id, thread_ts, sync_cursor, sync_watermark_ts, last_synced_at, sync_status, attempts, retry_at | 일감별 증분·최근 재동기화 상태 |
| `slack_messages` | thread_id, message_ts, author_id, permalink, is_root, first_event_id | root/reply의 안정된 identity와 current revision projection |
| `slack_message_revisions` | message_id, source_event_id, source_kind, revision_ts, body, is_deleted, raw_payload, received_at | edit/delete 순서 역전을 provider 시각으로 정렬하는 append-only 원문·tombstone |
| `slack_parse_results` | slack_work_item_id, source_version, parser_version, result, missing_fields, confidence, error | source version별 append-only 4개 관찰 필드 parse 결과 |
| `generation_jobs` | slack_work_item_id, status, model, prompt_version, result, error | AI 생성 작업과 재현 정보 |
| `test_case_sources` | test_case_id, slack_work_item_id, generation_job_id | 생성 출처 추적 |
| `ontology_schemas` | version, node_types, relation_types, status | 자동 migration되는 온톨로지 schema 버전 |
| `knowledge_nodes` | schema_version, node_type, canonical_name, status, confidence, properties, entity_type, entity_id, valid_from, valid_to | 업무 개체 또는 원장 엔티티의 graph projection |
| `knowledge_edges` | source_node_id, predicate, target_node_id, status, confidence, valid_from, valid_to | 타입이 있는 지식 관계 |
| `knowledge_evidence` | node_id, edge_id, source_type, source_id, source_revision, permalink, excerpt_hash | 노드·관계의 추적 근거 |
| `knowledge_embeddings` | owner_type, owner_id, model, model_version, embedding, content_hash | 의미 검색 보조 인덱스 |
| `generation_contexts` | generation_job_id, node_id, edge_id, source_revision, rank, reason | 생성 당시 참조한 지식 snapshot |
| `outbox_events` | aggregate_type, aggregate_id, event_type, payload, status | 원장 변경을 graph projection에 안전하게 전달 |
| `comments` | owner_type, owner_id, body, author_label | 이슈 등 대상별 대화 기록 |
| `audit_logs` | entity_type, entity_id, action, before, after, source_type, occurred_at | 전체 변경 감사 로그 |

### 9.1 주요 관계

```text
Workspace 1 ── N Folder 1 ── N TestCase 1 ── N TestCaseVersion 1 ── N TestStep
Workspace 1 ── N TestCycle 1 ── N CycleItem 1 ── N ExecutionAttempt
TestCase N ── N Issue
ExecutionAttempt N ── N Issue
SlackWorkItem 1 ── 1 SlackThread 1 ── N SlackMessage
SlackWorkItem 1 ── N GenerationJob ── N TestCase
KnowledgeNode N ── N KnowledgeNode (typed KnowledgeEdge)
KnowledgeNode/Edge 1 ── N KnowledgeEvidence ── 1 SlackMessage/TestCaseVersion/Issue
GenerationJob N ── N KnowledgeNode/Edge (GenerationContext snapshot)
```

### 9.2 중요한 데이터 원칙

- 사이클은 `test_case_id`뿐 아니라 `test_case_version_id`를 반드시 저장한다.
- 실행 결과는 변경 가능한 현재값 하나가 아니라 시도별 append-only 기록으로 남긴다.
- 테스트케이스의 현재 내용은 버전 테이블을 통해서만 변경한다.
- 감사 로그는 사용자 화면용 버전 이력과 별도로 보존한다.
- 단일 워크스페이스이므로 tenant·project 식별자를 도메인 테이블에 반복 저장하지 않는다.
- Slack 원문은 증거 레이어, 자동 추출물은 파생 레이어, `ACTIVE` 노드·관계는 현재 지식 레이어로 분리한다.
- 운영 엔티티와 온톨로지를 독립적으로 중복 편집하지 않고 `entity_type + entity_id`로 원장 레코드를 참조한다.
- 생성 결과에는 당시 참조한 노드·관계·source revision을 고정해 이후 지식 변경과 무관하게 재현할 수 있게 한다.
- R2는 binary 원장이고 DB의 `attachments`가 검색·연결·삭제 상태의 메타데이터 원장이다.

## 10. API 초안

### 10.1 테스트 라이브러리

| Method | Endpoint | 용도 |
|---|---|---|
| `GET` | `/api/folders/tree` | 디렉토리 전체 트리 조회 |
| `POST` | `/api/folders` | 디렉토리 생성 |
| `PATCH` | `/api/folders/{folderId}` | 이름·위치·순서 변경 |
| `GET` | `/api/test-cases` | 검색·필터·목록 조회 |
| `POST` | `/api/test-cases` | 테스트케이스 생성 |
| `GET` | `/api/test-cases/{caseId}` | 현재 버전 상세 조회 |
| `PATCH` | `/api/test-cases/{caseId}` | 새 버전을 생성하며 수정 |
| `GET` | `/api/test-cases/{caseId}/versions` | 버전 이력 조회 |
| `POST` | `/api/test-cases/{caseId}/restore/{version}` | 이전 버전을 새 버전으로 복원 |

### 10.2 사이클과 실행

| Method | Endpoint | 용도 |
|---|---|---|
| `POST` | `/api/cycles` | 사이클 생성 |
| `POST` | `/api/cycles/{cycleId}/items` | 케이스·디렉토리 추가 |
| `PATCH` | `/api/cycle-items/{itemId}` | 담당자 배정 또는 버전 갱신 |
| `POST` | `/api/cycle-items/{itemId}/attempts` | 새 실행 시도 시작 |
| `PATCH` | `/api/execution-attempts/{attemptId}` | 상태·실제결과 기록 |
| `GET` | `/api/cycles/{cycleId}/report` | 진행률·결과 리포트 |

### 10.3 이슈

| Method | Endpoint | 용도 |
|---|---|---|
| `POST` | `/api/issues` | 이슈 생성 |
| `POST` | `/api/execution-attempts/{attemptId}/issues` | 실행 정보로 이슈 생성 |
| `PATCH` | `/api/issues/{issueId}` | 이슈 수정 |
| `POST` | `/api/issues/{issueId}/attachment-upload-sessions` | 이슈 첨부 direct upload session 발급 |
| `POST` | `/api/execution-attempts/{attemptId}/attachment-upload-sessions` | 실행 첨부 direct upload session 발급 |
| `POST` | `/api/attachment-upload-sessions/complete` | 원격 객체 검증 후 첨부 메타데이터 확정 |
| `GET` | `/api/attachments/{attachmentId}/content` | private 객체 중계 다운로드 |
| `DELETE` | `/api/attachments/{attachmentId}` | 첨부파일 soft delete |

### 10.4 Slack 및 AI 생성

| Method | Endpoint | 용도 |
|---|---|---|
| `POST` | `/integrations/slack/events` | Slack URL verification 또는 서명된 message event 수신·빠른 ack |
| `GET` | `/api/integrations/slack/status` | 실제 mode·credential/setup·최근 수신 상태 조회 |
| `GET/PUT` | `/api/integrations/slack/allowlist` | public channel allowlist 조회·교체 |
| `DELETE` | `/api/integrations/slack/allowlist/{channelId}` | allowlist channel 제거 |
| `GET` | `/api/slack-work-items` | channel filter와 cursor-ready work item 목록 |
| `GET` | `/api/slack-work-items/{workItemId}` | root/thread/message/revision/parse 통합 상세 |
| `GET` | `/api/slack-work-items/{workItemId}/messages` | 수집된 원문과 revision 조회 |
| `GET` | `/api/slack-events` | redacted event 처리 원장 조회 |
| `GET` | `/api/slack-jobs` | quiet/thread sync의 pending·retry·DEAD 조회 |
| `POST` | `/api/slack-work-items/{workItemId}/sync-thread` | thread 답글과 수정 이력 증분 수집 |
| `POST` | `/api/slack-work-items/{workItemId}/quiet-window` | source generation 기준 quiet job 갱신 |
| `POST` | `/api/slack-jobs/{jobId}/cancel` | stale quiet job 취소 |
| `POST` | `/api/slack-work-items/{workItemId}/generate` | 테스트케이스 수동 재생성 |
| `GET` | `/api/generation-jobs/{jobId}` | 생성 상태와 결과 조회 |
| `POST` | `/api/generation-jobs/{jobId}/retry` | 조건을 추가해 재생성 |

마지막 세 Generation API endpoint는 Sprint 7~8 목표 계약이며 Sprint 5 구현 완료 범위가 아니다.

### 10.5 온톨로지

| Method | Endpoint | 용도 |
|---|---|---|
| `POST` | `/api/slack-threads/{threadId}/extract-knowledge` | thread 지식 자동 재추출 |
| `GET` | `/api/knowledge/nodes` | 상태별 지식 목록 조회 |
| `PATCH` | `/api/knowledge/nodes/{nodeId}` | 자동 판별 상태·적용 범위 수정 |
| `GET` | `/api/knowledge/search` | graph+semantic 지식 검색 |
| `GET` | `/api/knowledge/nodes/{nodeId}/evidence` | 노드의 Slack·케이스·이슈 근거 조회 |
| `GET` | `/api/features/{featureId}/test-coverage` | 요구사항·결정별 테스트 커버리지 조회 |
| `GET` | `/api/generation-jobs/{jobId}/context` | 생성 당시 사용한 지식 snapshot 조회 |

### 10.6 객체 저장소

| Method | Endpoint | 용도 |
|---|---|---|
| `GET` | `/api/integrations/object-storage/status` | provider·bucket·파일 제한·설정 상태 조회 |

Cloudflare R2 설정은 배포 환경 변수로 주입한다. 사용자별 OAuth 연결 화면은 필요하지 않으며 로컬에서는 동일 API 계약을 MinIO로 검증한다.

### 10.7 테스트케이스 생성 요청 예시

```json
{
  "folderId": "folder_123",
  "status": "ACTIVE",
  "summary": "인증번호 5회 오류 시 인증 요청이 잠긴다",
  "precondition": "비밀번호 재설정이 가능한 활성 계정이 있다.",
  "priority": "HIGH",
  "steps": [
    {
      "sequence": 1,
      "action": "비밀번호 재설정 화면에서 이메일 인증을 요청한다."
    },
    {
      "sequence": 2,
      "action": "잘못된 인증번호를 5회 입력한다."
    }
  ],
  "expectedResult": "인증이 잠기고 추가 입력이 차단된다.",
  "source": {
    "type": "SLACK_AI",
    "slackWorkItemId": "work_456",
    "generationJobId": "gen_789",
    "evidenceNodeIds": ["decision_101", "requirement_203", "issue_304"]
  },
  "generationReason": "현재 ACTIVE 상태인 5회 오류 잠금 결정과 과거 잠금 우회 이슈를 회귀 검증하기 위해 생성"
}
```

## 11. 상태 전이

### 11.1 테스트케이스

```text
ACTIVE ──사용중지──→ DEPRECATED
   ↑                    │
   └──────재활성화──────┘
```

수동·AI 케이스 모두 생성 즉시 `ACTIVE`다. 내용 수정은 승인 없이 새 버전을 만들며 상태를 유지한다.

### 11.2 사이클

```text
DRAFT → IN_PROGRESS → COMPLETED
  └────────┬────────→ CANCELLED
           └────────→ CANCELLED
```

`COMPLETED` 전환 시 `NOT_RUN`과 `IN_PROGRESS` 항목이 있으면 경고하지만 누구나 확인 후 완료할 수 있다.

### 11.3 이슈

```text
OPEN → IN_PROGRESS → RESOLVED → CLOSED
  ↑                     │          │
  └────── REOPENED ─────┴──────────┘
```

### 11.4 지식·결정

```text
                    ┌──충돌·불명확──→ AMBIGUOUS
자동 추출 ──────────┼──현재 결정────→ ACTIVE ──새 결정──→ SUPERSEDED
                    └──명시적 철회──→ RETRACTED
```

모든 전이는 자동 판별한다. 상충 결정을 해결하는 승인·escalation 절차는 두지 않으며 `AMBIGUOUS`는 테스트 생성의 확정 근거에서 제외한다. 누구나 상태를 수정할 수 있고 변경 시각과 전후 값은 남는다.

## 12. 비기능 요구사항

### 12.1 성능

- 일반 목록과 상세 조회의 95 percentile 응답 시간을 1초 이내로 목표로 한다.
- 10만 개 케이스가 있는 워크스페이스에서도 디렉토리 트리와 페이지 목록을 분리 조회한다.
- 목록 API는 cursor 또는 page 기반 pagination을 사용한다.
- AI 생성은 비동기 job으로 처리하고 진행 상태를 제공한다.
- 온톨로지 탐색은 graph query와 vector 검색을 병렬 수행하고 결과 수를 제한한다.
- Slack thread는 전체 재수집보다 `last_synced_at` 이후의 증분 동기화를 기본으로 한다.

### 12.2 보안

- UI와 일반 API는 사내망 또는 VPN에만 배포하고 방화벽에서 외부 접근을 차단한다.
- Slack Events API용 public ingress는 `/integrations/slack/events` 단일 경로만 노출하며 다른 API로 라우팅하지 않는다.
- 로그인 기능이 없으므로 애플리케이션 내부의 사용자별 접근 제어는 제공하지 않는다.
- Slack signing secret으로 모든 Slack 요청의 서명과 timestamp를 검증한다.
- 외부 연동 토큰은 암호화해 저장하고 UI·로그에 노출하지 않는다.
- 첨부파일은 악성 파일 검사 후 다운로드 가능하게 한다.
- AI 입력 전에 토큰, 비밀번호, 개인정보 패턴을 마스킹한다.
- 중요한 생성·수정·삭제·상태 변경은 감사 로그에 남긴다.
- Slack은 설정 화면에 명시한 allowlist 채널만 수집한다.
- private channel은 기본적으로 제외한다. 로그인 없는 도구에서는 사용자별 Slack 권한을 재현할 수 없으므로, 모든 도구 이용자가 해당 채널 구성원인 경우에만 팀 합의로 허용한다.
- 수집된 Slack root, thread, 수정 이력은 기간 제한 없이 보존한다.

### 12.3 안정성

- Slack 이벤트와 AI 생성 요청은 idempotency key를 사용한다.
- AI 또는 Slack 장애 시 재시도하고 최종 실패는 관리 화면에서 확인한다.
- 케이스 수정과 실행 상태 변경은 optimistic locking으로 충돌을 감지한다.
- PostgreSQL DB를 매일 백업하고 R2 object key·bucket lifecycle·credential 복구 절차를 분기마다 시험한다.
- Slack 메시지 수정·삭제·답글 추가는 source revision을 생성하고 영향받은 지식만 재평가한다.
- embedding model 또는 ontology schema가 바뀌면 background re-indexing하며 기존 검색을 중단하지 않는다.

### 12.4 신규 추가 비용 0원 운영 기준과 한계

아래 조합은 현재 사용 중인 유료 Slack을 제외하면 새로운 구독이나 API 비용 없이 self-hosting할 수 있는 기본안이다.

| 구성요소 | 선택 | 비용·라이선스 판단 |
|---|---|---|
| 프론트엔드 | React + Vite | MIT, 소프트웨어 비용 없음 |
| 백엔드 | FastAPI | MIT, 소프트웨어 비용 없음 |
| 데이터베이스 | PostgreSQL | PostgreSQL License, 무료 오픈소스 |
| vector 검색 | pgvector | PostgreSQL License, 무료 오픈소스 |
| graph RAG | LightRAG | MIT, 무료 오픈소스 |
| 로컬 모델 실행 | Ollama | MIT, 로컬 실행 시 API 사용료 없음 |
| 생성 모델 | Qwen3-8B | Apache-2.0, 모델 사용료 없음 |
| embedding | BAAI/bge-m3 | MIT, 모델 사용료 없음 |
| Slack 수집 | 기존 유료 Slack + Events API | 이미 사용 중인 구독 활용, 이 제품으로 인한 추가 API 비용 없음 |
| Slack ingress | 기존 public HTTPS endpoint | 기존 endpoint가 없으면 무료 Socket Mode로 대체 가능 |
| 첨부파일 | Cloudflare R2 Standard + 로컬 MinIO | 월 10GB-month와 정해진 요청 수까지 무료. 초과분은 사용량 기반 과금 |
| 실행 환경 | 팀이 보유한 PC 또는 Linux 서버 | 별도 클라우드 사용료 없음. 기존 장비·전기·백업 디스크 비용은 제외 |

#### 무료 조건에서 반드시 알아야 할 제한

- 유료 Slack은 기본적으로 전체 메시지 기록을 제공하지만 workspace 관리자가 별도 retention 정책을 설정할 수 있다. 생성 근거 재현을 위해 이벤트를 받은 원문과 revision은 우리 DB에도 저장한다.
- Events API HTTP webhook에는 Slack이 접근 가능한 public HTTPS endpoint가 필요하다. 기존 domain·ingress가 없다면 추가 비용을 피하기 위해 Slack Socket Mode로 전환하며 채널 자동 감지 로직은 동일하게 유지한다.
- Cloudflare R2 Standard 무료 사용량은 월 10GB-month 저장, Class A 100만 회, Class B 1,000만 회다. 저장량이나 요청 수가 이를 넘으면 비용이 발생하므로 사용량과 비용 알림을 설정한다.
- 무료 사용량은 계정 단위로 계산되며 무기한 보존 정책 때문에 누적 저장량이 계속 증가할 수 있다. 운영 전 예상 월 증가량을 측정하고 70%, 85%, 95% 경고를 유지한다.
- LightRAG와 로컬 모델은 무료지만 충분한 RAM·CPU/GPU와 전기 비용이 필요하다. Qwen3-8B가 느리거나 메모리가 부족하면 더 작은 Apache-2.0 모델로 낮출 수 있다.
- 신규 추가 비용을 0원으로 유지하려면 R2 무료 사용량을 넘지 않고 OpenAI, Gemini 유료 API, AWS S3, 관리형 PostgreSQL을 사용하지 않는다.
- 외부 서비스의 무료 정책과 quota는 바뀔 수 있으므로 배포 전과 분기별로 다시 확인한다.

## 13. MVP 출시 단계

### 1단계: 테스트 자산 관리

- 단일 워크스페이스와 무로그인 사내망 운영
- 무제한 depth 도메인 트리
- 테스트케이스 생성·수정·복사·이동·보관
- 서머리, 사전조건, 순서형 스텝·테스트 데이터·스텝별 기대결과
- 버전과 생성·수정 감사 이력
- 검색과 기본 필터

완료 기준: QA 담당자가 기존 테스트케이스를 도구 안에서 구조화하고 변경 이력을 확인할 수 있다.

### 2단계: 사이클·실행·이슈

- 테스트 사이클 구성과 버전 스냅샷
- 담당자 배정과 실행 상태
- 실제결과와 첨부파일
- R2 direct upload·private 다운로드·삭제·사용량 경고
- 실패 실행에서 이슈 생성
- 케이스·실행·이슈 연결
- 진행률 대시보드와 상세 리포트

완료 기준: 하나의 QA 요청을 사이클로 구성해 종료하고, 실패 건을 이슈로 추적할 수 있다.

**2026-08-20 검증 상태:** 당시 Google Drive adapter 기준의 deterministic 검증은 [Sprint 4 품질 게이트](./sprint-4/sprint-4-report.md)에 보존한다. 이 결정은 ADR-0017로 대체되었다.

**2026-08-25 검증 상태:** PostgreSQL migration과 local MinIO에 이어 actual private R2에서 FastAPI upload session, presigned direct PUT, 원격 HEAD/byte 검증, ClamAV, private proxy 다운로드, delete와 matched PostgreSQL restore를 검증했다. 배포 HTTPS origin browser 다중 첨부와 durable R2 비용/요청 analytics 검증은 배포 단계에서 수행한다.

### 3단계: Slack 수집과 QA 온톨로지

- allowlist 채널의 Slack Events API webhook 자동 수집
- root 메시지와 전체 thread 답글·수정 이력 수집
- 일감 필드 파싱
- 결정·요구사항·제약·질문 자동 추출
- `ACTIVE`, `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED` 자동 판별
- 근거 링크와 원문 revision
- 테스트케이스·실행·이슈의 graph projection
- PostgreSQL·pgvector·LightRAG 기반 graph+semantic 히스토리 검색

완료 기준: Slack 일감과 thread의 현재 결정을 출처가 있는 지식으로 자동 저장하고, 기능별 기존 케이스와 과거 이슈를 함께 조회할 수 있다.

**Sprint 5 부분 완료:** Slack 수집·source revision·구조화 parse의 코드/결정론적 통합은 완료했다. Live Slack 자동 수집과 Sprint 6 ontology projection·검색이 남아 있으므로 3단계 전체 완료는 선언하지 않는다.

### 4단계: 온톨로지 기반 AI 자동 생성

- 일감과 요청 메뉴의 온톨로지 연결
- 유효한 결정·기존 케이스·과거 실패 이슈 검색
- 디렉토리 자동 매핑
- Ollama·Qwen3·bge-m3를 사용한 로컬 AI 테스트케이스 자동 생성
- 생성 즉시 `ACTIVE` 등록 및 자유 수정
- 원본 일감 추적과 중복 방지
- 케이스별 생성 이유와 근거 표시
- 기존 케이스 중복·재사용 추천
- Slack thread 결과 알림

완료 기준: Slack 일감 하나에서 현재 유효한 결정과 테스트 히스토리를 참고한 근거 기반 케이스를 자동 등록하고 즉시 사이클에서 사용할 수 있다.

### 5단계: 확장

- CI/CD 및 자동화 테스트 결과 연동
- 테스트케이스 파일 가져오기·내보내기
- AI 회귀 범위 추천과 기존 케이스 재사용 추천
- 커버리지 및 릴리스 품질 지표

## 14. MVP 인수 조건

1. 접속한 팀원 누구나 3단계 이상의 도메인 구조를 만들고 하위 트리를 이동할 수 있다.
2. 수동 작성에서 서머리, 사전조건, 테스트 데이터·기대결과를 포함한 1개 이상의 스텝을 저장할 수 있다.
3. 케이스 수정 전후 내용과 생성·수정 시각이 버전 이력에 남는다.
4. 누구나 사이클을 생성·시작·완료할 수 있다.
5. 사이클에 상위 일감 또는 라벨 범위를 추가했을 때 포함된 케이스 버전이 고정된다.
6. 플랫폼 실행을 `UNEXECUTED`, `WIP`, `PASS`, `FAIL`, `BLOCKED`로 처리하고 `FAIL` 실제결과와 `BLOCKED` 사유를 검증할 수 있다.
7. 같은 케이스를 재실행해도 이전 실행 시도와 실제결과가 유지된다.
8. 실패 실행에서 이슈를 만들면 케이스의 사전조건·스텝·예상결과와 실행의 실제결과가 자동 입력된다.
9. 이슈와 실행 첨부파일이 private R2 bucket에 저장되고 앱 안에서 업로드·다운로드·삭제할 수 있다.
10. R2 무료 저장 사용량이 70%, 85%, 95%에 도달하면 경고가 표시된다.
11. allowlist Slack 채널의 root 일감은 사용자 동작 없이 자동 수집된다.
12. quiet window 후 AI 케이스가 `ACTIVE`로 자동 등록되고 원본 메시지 링크가 남는다.
13. 같은 Slack event 또는 메시지를 반복 처리해도 케이스가 중복 생성되지 않는다.
14. 전체 thread 답글과 메시지 수정 이력이 source revision으로 기간 제한 없이 남는다.
15. 현재 결정은 `ACTIVE`, 불명확한 충돌은 `AMBIGUOUS`, 뒤집힌 결정은 `SUPERSEDED`로 자동 판별된다.
16. `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED` 지식은 현재 확정 근거로 사용되지 않는다.
17. AI 생성 케이스마다 참고한 결정·기존 케이스·과거 이슈와 생성 이유를 확인할 수 있다.
18. 생성 후 온톨로지가 변경돼도 생성 당시 사용한 지식 snapshot으로 결과를 재현할 수 있다.
19. 사람이 수정한 AI 케이스는 thread 재수집 시 자동 덮어쓰지 않고 변경 제안만 만든다.
20. OpenAI·Gemini 등 유료 API 없이 Ollama 로컬 모델로 end-to-end 생성이 동작한다.
21. private channel은 기본 수집되지 않으며 명시적으로 allowlist한 채널만 처리된다.
22. 로그인 없이 사내망 또는 VPN 안에서 모든 팀원이 동일 기능을 사용할 수 있다.

### 14.1 2026-08-20 Phase 2 인수 evidence

| 인수 조건 | 코드·결정론적 통합 | 실제 운영 | 판정 |
|---:|---|---|---|
| 4~7 | PostgreSQL cycle snapshot·상태 전이·platform attempt·재실행 E2E | local no-fallback Browser | PASS |
| 8 | FAIL·BLOCKED attempt의 pinned script·expected·actual/note immutable issue snapshot | local PostgreSQL/FastAPI/Web | PASS |
| 9 | R2 호환 presigned PUT·HEAD 검증·private proxy download·soft delete | 로컬 MinIO actual binary PASS, 운영 R2 미실증 | local PASS / 운영 보류 |
| 10 | object storage status와 설정 기반 파일 제한 | 운영 R2 usage metric·비용 경고 미실증 | 부분 PASS / 운영 보류 |

2026-08-20 Google Drive 인수 기준은 ADR-0017 결정에 따라 2026-08-24 R2 기준으로 대체했다. 운영 R2 검증 전에는 Phase 2 전체를 닫지 않는다.

## 15. 확정된 정책과 기술 권고

| 항목 | 확정 내용 |
|---|---|
| 업무 공간 | 단일 팀·단일 워크스페이스 |
| 로그인·권한 | 로그인과 역할 구분 없음. 누구나 전체 기능과 사이클 실행 가능 |
| 배포 범위 | 사내망 또는 VPN 내부 전용 |
| 외부 Jira | 연동하지 않음. 이슈는 도구 내부에서만 관리 |
| Slack 수집 | 기존 유료 Slack의 allowlist 채널을 Events API HTTP webhook으로 자동 감지. public ingress가 없으면 Socket Mode 사용 |
| 일감 형식 | 이미 작성된 글을 그대로 수집하며 작성 템플릿을 강제하지 않음 |
| 기대결과 | 수동 작성은 스텝별, 자동 생성 API는 케이스 전체 1개 |
| 케이스 승인 | 없음. 수동·AI 케이스 모두 생성 즉시 `ACTIVE` |
| 첨부 저장소 | private Cloudflare R2 bucket, local development는 MinIO |
| 첨부 보존 | 기간 제한 없음. 월 10GB-month 무료 한도를 모니터링하고 초과분 과금 가능성을 수용 |
| 내부 이슈 | 자체 이슈만 사용하고 케이스·실행과 연결 |
| 추가 참고자료 | 사용자가 요청 시 제공한 기획·디자인·코드 자료만 해당 생성에 추가 |
| Slack 원문 보존 | 수집한 root·thread·revision을 DB에 기간 제한 없이 보존 |
| 결정 승인 | 별도 승인 없음. 상태를 자동 판별하고 누구나 필요 시 수정 |
| 상충 결정 | `AMBIGUOUS`로 표시하고 escalation 없이 생성 확정 근거에서 제외 |
| private channel | 기본 제외. 전 도구 이용자가 채널 구성원일 때만 명시적 allowlist 권장 |
| 온톨로지 | PostgreSQL 원장 + typed graph projection + pgvector + LightRAG 보조 검색 |
| schema migration | 배포 시 자동 migration, 별도 승인자 없음 |
| AI 비용 | Ollama + Qwen3-8B + bge-m3 로컬 실행으로 외부 API 비용 없음 |
| 추가 운영비 | 기존 Slack·팀 장비를 활용해 신규 구독 및 사용량 기반 API 비용 0원 목표 |

개발 전 필요한 값은 정책 결정이 아니라 설치 정보다: Slack workspace·channel ID와 App credentials, Cloudflare account ID·R2 bucket·S3 API credential·CORS origin, 배포할 팀 PC·서버 사양, 실제 일감/thread 예시가 필요하다.

## 16. 다음 산출물

기획 확정 후 다음 순서로 구체화한다.

작성 완료된 실행 문서는 다음과 같다.

- [개발 명세서 v1.0](./test-management-development-spec.md)
- [Phase·Sprint·Task 체크리스트 로드맵 v1.0](./test-management-roadmap.md)

1. 화면 정보구조와 핵심 화면 wireframe
2. DB ERD와 상태 전이 상세 설계
3. REST API와 무로그인 사내망 배포 명세
4. Slack `일감`과 실제 thread 샘플 수집, Events API 파서·보존 규칙
5. QA 온톨로지 schema v1과 지식 상태·관계 정의
6. 결정 추출·충돌·대체 판별 평가 세트
7. LightRAG+pgvector retrieval와 AI 출력 JSON Schema
8. 프롬프트, 생성 근거 규격, 테스트케이스 품질 평가 세트
9. Cloudflare R2 direct upload·private attachment adapter 상세 설계
10. 무료 로컬 실행 환경의 하드웨어 benchmark
11. MVP 개발 일정
12. 1단계 테스트 관리 기능 구현

## 17. 기술 검증 출처

- [Slack Events API](https://docs.slack.dev/apis/events-api/): HTTP endpoint로 이벤트를 받는 방식과 OAuth scope 모델
- [Slack message.channels event](https://docs.slack.dev/reference/events/message.channels): 공개 채널 메시지 이벤트와 `channels:history` scope
- [Slack 데이터 보존 설정](https://slack.com/help/articles/203457187-Customize-data-retention-in-Slack): 유료 플랜 기본 보존과 관리자 retention 설정
- [Cloudflare R2 가격](https://developers.cloudflare.com/r2/pricing/): Standard 무료 사용량과 초과 과금 기준
- [Cloudflare R2 presigned URL](https://developers.cloudflare.com/r2/api/s3/presigned-urls/): 브라우저 직접 PUT, 만료와 bearer token 주의사항
- [Cloudflare R2 CORS](https://developers.cloudflare.com/r2/buckets/cors/): 브라우저 direct upload에 필요한 origin·method·header 설정
- [LightRAG 공식 저장소](https://github.com/HKUDS/LightRAG): MIT license, citation, graph RAG 기능
- [LightRAG Core 문서](https://github.com/HKUDS/LightRAG/blob/main/docs/ProgramingWithCore.md): PostgreSQL storage와 Ollama 연동
- [PostgreSQL License](https://www.postgresql.org/about/licence/): 무료 오픈소스 PostgreSQL License
- [pgvector License](https://github.com/pgvector/pgvector/blob/master/LICENSE): PostgreSQL License
- [Ollama License](https://github.com/ollama/ollama/blob/main/LICENSE): MIT license
- [Qwen3-8B License](https://huggingface.co/Qwen/Qwen3-8B/blob/main/LICENSE): Apache-2.0
- [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3): MIT license
