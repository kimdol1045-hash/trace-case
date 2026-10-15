# 테스트 관리·자동 생성 플랫폼 실행 로드맵

| 항목 | 내용 |
|---|---|
| 문서 버전 | v2.9 |
| 갱신일 | 2026-09-07 |
| 기준 문서 | [제품 기획서](./test-management-product-plan.md), [개발 명세서](./test-management-development-spec.md), [사용 가이드](./user-guide.md) |
| 기술 결정 | [ADR 목록](./adr/README.md), [ADR-0017 R2 비공개 첨부](./adr/0017-r2-direct-private-attachments.md) |
| 계획 단위 | Phase → Sprint → Task |
| 현재 실행 위치 | Phase F / Sprint F1 Wave 9 run `w9rt7c3b`에서 current `tc`의 read-only snapshot/dump를 exact `pf-sf1-t10-wave9` loopback PostgreSQL 16 copy에 복원하고 실제 Alembic graph의 `20260902_0030 → 20260901_0029 → 20260902_0030` 한 revision 왕복을 실행해 PF-SF1-T10 **LOCAL DISPOSABLE** sub-gate를 PASS했다. Complete logical catalog/normalized data/roles, no-external API/write/worker smoke, current-`tc` fingerprint, Docker/DB inventory와 prior evidence 27개 hash가 일치했고 exact disposable resources/dump/temp/process를 정리했다. PF-SF1-T10/Phase F/release gate는 unchecked이며 separately hosted staging copy, operator handoff, broader migration boundaries, production RTO/RPO는 모두 `NOT_RUN`이다. |

이 문서는 현재 코드와 실제 운영 준비 상태를 기준으로 앞으로 수행할 작업만 관리한다. 이전 Sprint 0~6의 완료 근거는 `docs/sprint-*` 보고서와 ADR에 보존한다.

## 1. 로드맵 운영 규칙

- 하나의 체크박스는 독립적으로 검증 가능한 Task다.
- Task ID는 `P{Phase}-S{Sprint}-T{번호}` 형식을 사용한다.
- 코드 작성만으로 완료 처리하지 않는다. 각 Task의 완료 조건과 자동 또는 실제 환경 검증을 함께 충족해야 한다.
- Phase A·B 실사용 검증과 Phase C·D 개발은 병행할 수 있다. 자동 생성 결과를 운영 QA 원장에 쓰는 Phase E 연결은 Phase B 기능 동결 뒤 수행한다.
- 관리툴 기본 흐름은 계속 수동 생성이다.
- Jira와 Google Drive는 연동하지 않는다.
- 첨부파일은 운영에서 private Cloudflare R2, 로컬에서 private MinIO를 사용한다.
- Reports 화면은 CSV 다운로드를 제공하지 않는다.
- 기존 완료 근거와 새 정책이 충돌하면 이 문서와 최신 Accepted ADR을 우선한다.

## 2. 전체 Phase 현황

- [ ] **Phase A — 관리툴 완성:** 원장 영속화, 실행 모델, R2, 브라우저 회귀를 닫는다. **남은 gate:** owner가 localhost physical browser에서 다중 파일 전체 흐름을 수동 확인한다. 현재 전체 `pnpm check`는 GREEN이다.
- [ ] **Phase B — 실제 QA 운영 리허설:** 실제 테스트 요청 하나를 처음부터 끝까지 수행한다.
- [ ] **Phase C — 실제 Slack 수집:** 실제 워크스페이스의 일감과 thread revision을 자동 수집한다.
- [ ] **Phase D — 온톨로지 검색 완성:** LightRAG와 typed graph·pgvector hybrid retrieval을 실데이터로 검증한다.
- [ ] **Phase E — 테스트케이스 생성 API:** 외부 Generation API 수동 호출과 안전한 자동 생성을 연결한다.
- [ ] **Phase F — 운영 배포:** 보안, 복구, 성능, 관측성과 UAT를 통과해 배포한다.

### 현재 완료된 기준선

- [x] 테스트 라이브러리, 테스트케이스 상세, 사이클, 실행, 내부 이슈, Overview, Reports의 주요 화면이 존재한다.
- [x] 테스트케이스 버전, 사이클 snapshot, 스텝별 플랫폼 실행, 이슈·실행 연결의 PostgreSQL/FastAPI 계약이 존재한다.
- [x] 이슈 상태 카드가 현재 목록 상태 필터와 분리되어 집계된다.
- [x] Reports 화면의 CSV 다운로드 버튼과 호출 기능을 제거했다.
- [x] 첨부파일 direct upload, remote metadata 검증, private download와 삭제를 로컬 MinIO actual binary로 검증했다.
- [x] Slack source ledger와 typed ontology 기본 코드가 존재한다.
- [x] Generation API `1.0.0` 계약, grounded context snapshot 원장, provider-neutral adapter와 deterministic validator가 존재한다.
- [x] Web unit, typecheck, production build와 전체 품질 검사를 실행할 수 있다.

## 3. Phase A — 관리툴 완성

**목표:** 실제 QA 운영 전에 모든 화면을 PostgreSQL 원장에 연결하고 데이터·상태·브라우저 동작을 안정화한다.

**종료 조건:** 새로고침과 페이지 이동 후에도 원장 상태가 일치하고 mock 없이 실제 QA 흐름을 시작할 수 있다.

### Sprint A1 — 원장 완전 영속화와 레거시 제거

**Sprint 목표:** 카테고리·상위일감·케이스·사이클 화면의 남은 demo/mock 경계를 제거한다.

- [x] `PA-SA1-T01` `useTestManagementDemo()` 사용 지점을 기능별로 분류한다. **완료 근거:** [Sprint A1 영속화 감사](./phase-a/sprint-a1-persistence-audit.md)에 실제 API·reducer-only mutation·자동 fallback·제거 dependency를 route별로 기록했다.
- [x] `PA-SA1-T02` 상위일감 생성·조회·수정 API와 repository를 완성했다. **완료 근거:** workspace·root category 소유권, key 중복, resourceVersion optimistic locking, append-only audit와 하위 case projection을 PostgreSQL/API 테스트로 검증했다.
- [x] `PA-SA1-T03` 상위일감 생성 모달과 상세 화면을 실제 API에 연결했다. **완료 근거:** API 성공 후에만 화면 원장을 갱신하고, 빈 상위일감·상세 필드·하위 케이스·이력을 재조회에서도 보존하는 회귀 테스트를 추가했다.
- [x] `PA-SA1-T04` 카테고리 생성·이름 변경·삭제와 상위일감 목록 projection을 단일 원장 기준으로 정리한다. **완료 근거:** category adapter가 paginated folder/work-item generated client만 사용하고, PostgreSQL 계약 테스트와 Web projection 테스트가 필터 전후 canonical category count, 빈 상위일감과 200개 초과 page를 보존한다.
- [x] `PA-SA1-T05` 테스트케이스 생성·상세·이동·순서 변경의 in-memory mutation 경로를 제거한다. **완료 근거:** default/legacy case mutation fallback을 삭제했고, 성공한 move/reorder 뒤 API projection을 재조회한다. PostgreSQL 테스트가 same-parent `REORDER`, cross-parent `MOVE`, stale-version rollback과 append-only audit를 검증한다.
- [x] `PA-SA1-T06` 사이클 목록·생성·상세·실행의 sticky mock fallback을 개발 전용 feature flag 뒤로 격리한다. **완료 근거:** `VITE_ENABLE_CYCLE_MOCK_FALLBACK=true`는 development mode에서만 유효하고, production availability failure는 fallback을 latch하지 않은 채 명시적 오류를 반환한다.
- [x] `PA-SA1-T07` `Latest diff` UI, state와 cycle-item diff 자동 호출을 완전히 제거했다. **완료 근거:** 전용 Web state·handler·CSS와 client·FastAPI·OpenAPI 계약을 제거하고 pinned/latest 버전 번호, 일반 버전 이력·복원 및 snapshot refresh 내부 diff를 보존했다.
- [x] `PA-SA1-T08` Reports CSV의 client wrapper와 공개 API 계약을 제거했다. **완료 조건:** OpenAPI·client·계약 테스트가 JSON Reports API만 제공하는 정책으로 일치한다.
- [x] `PA-SA1-T09` Google Drive legacy 코드·문구·환경 변수·demo 데이터를 조사하고 R2 전환에 필요한 호환 범위만 남긴다. **완료 근거:** [Sprint A3 storage 검수](./phase-a/sprint-a3-storage-verification.md)에 OAuth/runtime/env/demo/contract 제거와 schema + metadata-only migration inventory만 남긴 경계를 기록했다.
- [x] `PA-SA1-T10` 모든 주요 생성 화면의 취소·저장·뒤로가기 navigation 정책을 재검증한다. **완료 근거:** [Sprint A1 검수](./phase-a/sprint-a1-verification-report.md)에 route별 push/replace, explicit save와 keyed loading/no-ghost 근거를 기록했다.
- [x] `PA-SA1-T11` Sprint A1 PostgreSQL API·Web E2E와 회귀 테스트를 추가한다. **완료 근거:** category→상위일감→case→cycle을 새 FastAPI application과 fresh Web adapter로 reload해 PostgreSQL 원장을 재조회했다.
- [x] `PA-SA1-T12` Sprint A1 검수 보고서와 남은 fallback 목록을 작성한다. **완료 근거:** [Sprint A1 검수](./phase-a/sprint-a1-verification-report.md)가 production 0개와 development/test-only 잔존 경계를 파일·route 단위로 분리한다.

#### Sprint A1 종료 체크

- [x] 상위일감 생성과 수정이 새로고침 후 유지된다.
- [x] 테스트 라이브러리 핵심 mutation이 PostgreSQL 원장에 저장된다. **근거:** category/work item/case CRUD와 case move/reorder PostgreSQL 계약 테스트.
- [x] 운영 모드에서 demo/mock 데이터가 표시되지 않는다. **근거:** empty-by-default scoped provider, production SSR no-fixture tests와 development-only cycle gate.
- [x] Reports에 CSV 다운로드 UI와 실행 경로가 없다.
- [x] 사이클 상세가 `Latest diff` endpoint를 자동 호출하지 않는다.

### Sprint A2 — 사이클·실행 상태 모델 정리

**Sprint 목표:** 실행 상태와 스텝·플랫폼별 저장 규칙을 최종 계약으로 고정한다.

- [x] `PA-SA2-T01` 사용자 입력 상태를 `UNEXECUTED`, `PASS`, `FAIL`, `BLOCKED`로 고정한다. **근거:** Web/OpenAPI/generated client/JSON Schema/runtime enum 일치와 contract·Web tests.
- [x] `PA-SA2-T02` `WIP` legacy enum과 DB 데이터를 조사하고 안전한 migration 정책을 구현한다. **근거:** `20260824_0013` WIP→UNEXECUTED migration과 attempt snapshot/audit lossless PostgreSQL round-trip.
- [x] `PA-SA2-T03` 스텝별·플랫폼별 결과 저장과 append-only attempt 규칙을 재검증한다. **근거:** 2 step × WEB/AOS/IOS 독립 저장과 WEB rerun attempt 1/2 PostgreSQL matrix test.
- [x] `PA-SA2-T04` 통합결과 규칙을 server와 client 공통 계약으로 고정한다. **근거:** UNEXECUTED·BLOCKED 제외, FAIL 우선 server/Web/contract 판정표 tests.
- [x] `PA-SA2-T05` 미진행 입력 차단, 진행 중 입력 허용, 완료 읽기 전용, 다시 열기를 E2E로 검증한다. **근거:** disposable PostgreSQL FastAPI lifecycle flow와 Web execution gate test.
- [x] `PA-SA2-T06` 재실행 시 이전 attempt와 이슈 연결이 유지되는지 검증한다. **근거:** issue-linked terminal attempt 이후 append rerun/PASS에도 original link 유지 PostgreSQL test.
- [x] `PA-SA2-T07` 이슈·사이클 상태 카드 집계를 단일 aggregate API 또는 효율적인 query로 개선한다. **근거:** cycle `statusCounts` 단일 request, status-filter count 불변 test, fixed 4-query report source와 batched issue KPI.
- [x] `PA-SA2-T08` 대량 실행 queue에 pagination 또는 virtual scrolling을 적용한다. **근거:** DB cursor page + Web 100-row load-more, 1,000-case API `<2s` gate와 10-page merge test.
- [x] `PA-SA2-T09` 사이클 수정·삭제·완료 충돌과 부분 실패 E2E를 추가한다. **근거:** stale update/active delete/incomplete complete/completed mutation conflicts와 progress-only partial failure preservation tests.
- [x] `PA-SA2-T10` Sprint A2 검수 보고서를 작성한다. **근거:** [`docs/phase-a/sprint-a2-execution-model-verification.md`](phase-a/sprint-a2-execution-model-verification.md).

#### Sprint A2 종료 체크

- [x] 화면에 WIP 선택·표시가 없다.
- [x] 여러 스텝과 플랫폼 결과가 독립 저장된다.
- [x] 통합결과와 대시보드 집계가 동일하다.
- [x] 완료된 사이클은 읽기 전용이다.

### Sprint A3 — R2 첨부파일 운영 완성

**Sprint 목표:** 로컬 MinIO에서 통과한 첨부 흐름을 실제 private R2로 검증하고 복구 가능하게 한다.

- [x] `PA-SA3-T01` 운영 R2 bucket, S3 credential과 최소 권한을 구성한다. **완료 근거:** `tc-attachments-prod`의 APAC·Standard, `r2.dev` disabled, custom domain 0건을 Wrangler로 확인했고 bucket-scoped key는 object lifecycle/ListObjects에 성공하면서 account ListBuckets는 403으로 거부됐다.
- [x] `PA-SA3-T02` 현재 배포 전 운영 Web origin 기준 PUT·GET·HEAD header CORS를 구성한다. **완료 근거:** actual R2가 `http://localhost:5173`·`http://127.0.0.1:5173` exact origin의 PUT/GET/HEAD preflight를 204로 허용했고, physical Chrome의 `login.png`·`login (1).png` 개별 direct PUT이 성공했다. 아직 존재하지 않는 배포 HTTPS origin의 CORS 설치·smoke는 `PF-SF1-T15`로 분리하며 production 검증으로 주장하지 않는다.
- [x] `PA-SA3-T03` 실제 R2 upload session→direct PUT→complete→private download→delete E2E를 수행한다. **완료 근거:** 2026-08-25 actual R2 smoke에서 presigned direct PUT, HEAD/byte SHA·MIME, ClamAV, proxy download, metadata/orphan/restore check와 provider delete가 모두 통과했고 정리 후 object 0건을 확인했다.
- [x] `PA-SA3-T04` 파일 MIME·크기·SHA-256과 unsafe filename 검증 회귀를 확대한다. **완료 근거:** declaration·HEAD·실제 bytes의 size/SHA/MIME를 unit/API/MinIO에서 검증했다.
- [x] `PA-SA3-T05` 악성 파일 검사 또는 quarantine 정책을 구현한다. **완료 근거:** quarantine prefix, mode-0600 temp, fail-closed ClamAV와 live EICAR 거부를 검증했다.
- [x] `PA-SA3-T06` complete되지 않은 orphan 객체 탐지·정리 job을 구현한다. **완료 근거:** paginated ListObjectsV2, cutoff, dry-run/delete/retry 결과 endpoint와 API test.
- [x] `PA-SA3-T07` DB attachment metadata와 R2 객체 무결성 검사 job을 구현한다. **완료 근거:** HEAD size/MIME/SHA 비교와 missing/mismatch unreadable status를 migration/API test로 검증했다.
- [x] `PA-SA3-T08` R2 저장량·요청량·비용 관측과 70%·85%·95% 경고를 구현한다. **완료 근거:** configured budget/rate 기반 70/85/95/100 경계 test와 observations API; durable R2 analytics 대조는 T01~T03 external gate에 남는다.
- [x] `PA-SA3-T09` PostgreSQL 복구 후 object key·checksum으로 표본 첨부를 복구 검증한다. **완료 근거:** actual R2 첨부가 active인 시점의 PostgreSQL custom dump를 격리 DB `tc_r2_restore_test_20260825`에 복원하고 restored API에서 표본 1건의 size·SHA를 full-byte로 검증했다(`verified=1`, `failed=0`). 임시 객체·DB·dump는 검증 후 삭제했다.
- [x] `PA-SA3-T10` credential rotation, 장애, backup과 복구 runbook을 작성한다. **완료 근거:** [private object-storage runbook](../deploy/object-storage/README.md)에 최소 권한/CORS, rotation/rollback, failure와 matched backup recovery를 고정했다.
- [x] `PA-SA3-T11` legacy Google Drive row가 존재할 경우 읽기 호환 또는 migration 정책을 수행한다. **완료 근거:** 신규 write/OAuth/content operation 없이 metadata inventory + typed migration-required와 offline export/import 정책을 제공한다.
- [x] `PA-SA3-T12` Sprint A3 운영 검수 보고서를 작성한다. **완료 근거:** [Sprint A3 storage 검수](./phase-a/sprint-a3-storage-verification.md)가 local evidence와 real R2 blocker를 분리한다.

#### Sprint A3 종료 체크

- [x] 운영 R2 bucket은 public access가 차단됐다. **근거:** `r2.dev` disabled, custom domain 0건, unsigned S3 GET이 `InvalidArgument: Authorization` 4xx로 거부됐다.
- [ ] 실제 브라우저에서 다중 파일을 업로드·다운로드·삭제할 수 있다. **NOT RUN — owner manual follow-up:** 2026-08-25 자동 검수에서는 사용자 지시에 따라 file chooser 재시도를 중단했다. 두 PNG의 개별 actual R2 저장과 provider 삭제는 확인했지만 하나의 chooser event를 통한 batch upload와 각 browser download는 완료로 판정하지 않는다.
- [x] orphan과 metadata 불일치를 탐지할 수 있다. **근거:** live MinIO dry-run과 PostgreSQL API test.
- [x] 저장량과 비용 경고가 동작한다. **근거:** local API와 70/85/95/100 boundary unit test; production rate/analytics evidence는 미완료.

### Sprint A4 — 브라우저 E2E와 디자인 회귀

**Sprint 목표:** 지금까지 수정한 상호작용·화면 전환·정렬을 자동 회귀로 고정한다.

- [x] `PA-SA4-T01` 같은 상위일감 reorder, 다른 상위일감 이동과 빈 상위일감 drop의 physical pointer E2E를 구현한다. **근거:** 실제 Browser `drag` 3회와 mutation 후 PostgreSQL/API projection 재조회로 source/target 순서·건수를 검증했다.
- [x] `PA-SA4-T02` 테스트·사이클·이슈 생성의 저장·취소·뒤로가기 E2E를 구현한다. **근거:** 세 entity의 unsaved cancel/no-create, save/detail/reload, explicit back을 실제 route에서 검증했다.
- [x] `PA-SA4-T03` route 전환 시 잔상, 이전 데이터 노출과 layout shift를 측정·수정한다. **근거:** route-level lazy import와 stable-height Suspense fallback을 적용하고 list/create/detail/back/hard reload에서 stale entity가 없으며 `/test-cases`, `/cycles`, `/issues`의 buffered non-input CLS가 모두 0임을 확인했다.
- [x] `PA-SA4-T04` 이슈·사이클 상태 카드 필터와 건수 회귀 E2E를 구현한다. **근거:** OPEN 및 PLANNED/IN_PROGRESS/COMPLETED 실제 card filter/count를 검증하고 cycle result-count를 PASS처럼 보이던 표현을 중립 progress로 수정했다.
- [x] `PA-SA4-T05` 사이클 스텝별 결과, 통합결과와 이슈 다중 생성 E2E를 구현한다. **근거:** 동일 cycle에서 WEB PASS/AOS FAIL 독립 저장, FAIL 우선 aggregate와 동일 attempt의 이슈 2건 연결을 Browser에서 검증했다.
- [x] `PA-SA4-T06` 첨부 다중 업로드, 부분 실패와 재시도 E2E를 구현한다. **근거:** 실제 local MinIO/ClamAV에서 clean+EICAR batch가 1 success/1 failure가 되고 실패 파일만 retry되며 성공 첨부와 이슈가 중복되지 않음을 검증했다.
- [x] `PA-SA4-T07` 삭제·복원·변경이력과 optimistic conflict E2E를 구현한다. **근거:** case delete/restore, issue attachment/content history, 두 browser page의 stale issue save 409과 explicit canonical reload를 검증했다.
- [x] `PA-SA4-T08` 주요 route의 desktop width별 screenshot regression을 구축한다. **근거:** `/test-cases` 1280, `/cycles` 1440, `/issues` 1920 baseline과 dimension/SHA-256 verifier를 추가했다.
- [x] `PA-SA4-T09` 컴포넌트 높이, 텍스트 줄바꿈, focus outline과 드롭다운 정렬을 최종 점검한다. **근거:** 세 desktop baseline과 live dialog/filter/dropdown에서 clipping·overlap·focus loss가 없음을 검수했다.
- [x] `PA-SA4-T10` 키보드·focus·dialog 접근성 검사를 추가한다. **근거:** 공통 dialog initial/wrap/Escape/restore hook, unit test와 실제 Enter→Tab/Shift+Tab→Escape→opener restore를 통과했다.
- [x] `PA-SA4-T11` Web bundle 분할과 초기 로딩 성능을 개선한다. **근거:** initial JS 552.23→330.82 kB, gzip 158.69→103.14 kB로 감소하고 feature route chunks 및 warning-free production build를 확인했다.
- [x] `PA-SA4-T12` Phase A 통합 회귀와 검수 보고서를 작성한다. **근거:** [Phase A 검수](./phase-a/phase-a-verification-report.md)에 Browser, MinIO, PostgreSQL, screenshot, bundle, 전체 자동 검증과 real R2 blocker를 분리했다.

#### Phase A 종료 조건

- [x] 주요 화면이 실제 PostgreSQL/API 원장만 사용한다. **근거:** Sprint A1 no-production-fallback 감사와 A4 browser reload/projected mutation 검증.
- [x] 실제 R2 첨부 흐름이 검증됐다. **근거:** actual private R2의 session→presigned PUT→complete→scan→proxy GET→integrity/restore→delete와 localhost OPTIONS 204 CORS를 검증했다.
- [x] 핵심 사용자 흐름의 브라우저 E2E가 통과한다. **근거:** physical drag, create/cancel/save/back, execution/issue, MinIO attachment, delete/restore/conflict 검수.
- [ ] 출시 차단 수준의 관리툴 결함이 없다. **미충족 근거:** Test Library 진입, 상태 배지, 연결 근거 선택·키보드 focus, 실행 버튼 대비와 actual R2 data path에서는 blocker를 찾지 못했고 2026-08-25 전체 `pnpm check`도 GREEN이다. 다만 physical 다중 파일 전체 흐름이 아직 owner `NOT RUN`이므로 release gate를 닫지 않는다.

## 4. Phase B — 실제 QA 운영 리허설

### Sprint B1 — 실제 QA 표본 UAT

- [x] `PB-SB1-T01` 실제 상위일감과 테스트케이스 표본을 등록한다. **완료 근거:** [Sprint B1 CSV 이관 보고서](./phase-b/sprint-b1-csv-import-report.md)의 원본 CSV를 2개 도메인·2개 상위일감·19개 테스트케이스·73개 스텝으로 변환했고, 기존 스모크 원장을 초기화한 뒤 라이브 API와 PostgreSQL 개수를 대조했다.
- [x] `PB-SB1-T02` 상위일감 또는 라벨 기준 사이클을 생성하고 시작한다. **완료 근거:** [Sprint B1 UAT 보고서](./phase-b/sprint-b1-uat-report.md)에서 `WORK-5371`, `WORK-6651` 기준 사이클 2개와 PLANNED→IN_PROGRESS 전이를 라이브 API로 검증했다.
- [x] `PB-SB1-T03` 필요한 WEB·AOS·iOS 스텝 결과와 비고를 기록한다. **완료 근거:** Web 전용 CSV 표본의 WEB 원장에 72 PASS·1 BLOCKED와 사유를 기록하고, 범위 밖 AOS·iOS 38개는 의도적으로 UNEXECUTED 유지했다.
- [x] `PB-SB1-T04` 실행에서 이슈 여러 개와 실제 첨부파일을 생성한다. **완료 근거:** 같은 BLOCKED attempt에서 이슈 2개를 만들고 private/CLEAN 텍스트 증거 2개를 각 이슈에 업로드했으며, UAT 재실행에서도 중복이 없었다.
- [x] `PB-SB1-T05` 사이클 완료·다시 열기와 이슈 상태 전이를 수행한다. **완료 근거:** 사이클 2개를 완료하고 한 사이클의 완료→다시 열기→재완료를 검증했다. 이슈는 OPEN→IN_PROGRESS 및 OPEN→IN_PROGRESS→RESOLVED→CLOSED 이력이 남았다.
- [x] `PB-SB1-T06` Overview와 Reports 집계를 원장 표본과 대조한다. **완료 근거:** 사이클 2·케이스 19·플랫폼 슬롯 57·WEB PASS 18/BLOCKED 1·AOS/iOS UNEXECUTED 38·이슈 심각도/상태가 원장 표본과 일치했다.
- [ ] `PB-SB1-T07` 실제 QA 담당자 피드백을 blocker·major·minor로 분류한다.
- [ ] `PB-SB1-T08` blocker와 major를 수정하고 전체 UAT를 재수행한다.
- [ ] `PB-SB1-T09` 관리툴 기능 동결 기준과 Phase C 진입 결정을 기록한다.

#### Phase B 종료 조건

- [x] 실제 QA 요청 하나가 케이스→사이클→실행→이슈→완료까지 처리됐다. **근거:** CSV 실제 표본 2건의 19개 케이스를 사이클로 실행하고 BLOCKED attempt의 복수 이슈·첨부와 완료를 원장에 기록했다.
- [ ] QA 담당자가 별도 설명 없이 [사용 가이드](./user-guide.md)로 핵심 흐름을 수행했다.
- [x] 자동 생성 없이 관리툴을 운영할 수 있다. **근거:** 테스트케이스 자동 생성 기능 없이 이관된 수동 관리 케이스를 사용해 사이클·실행·이슈·완료 전 과정을 처리했다.

## 5. Phase C — 실제 Slack 수집

### Sprint C1 — Slack live 연결과 golden sample

- [x] `PC-SC1-T01` Slack App manifest와 scope를 현재 정책으로 확정한다. **완료 근거:** public channel 전용 `channels:history` + `message.channels`, private/DM/Jira/shortcut 제외 정책을 manifest·unit test·[runbook](../deploy/slack/README.md)에 고정했다.
- [ ] `PC-SC1-T02` 실제 workspace에 App을 설치하고 allowlist 채널에 참여시킨다.
- [x] `PC-SC1-T03` HTTP webhook 또는 Socket Mode를 운영 네트워크에 맞게 확정한다. **완료 근거:** 사용자 요구의 자동 webhook 방식에 따라 HTTP Events exact POST ingress를 Phase C 경로로 확정하고 Socket Mode는 public ingress 불가 시 fallback으로만 유지했다. 실제 hostname URL verification은 T02·T04 live gate다.
- [ ] `PC-SC1-T04` root 일감의 사용자 동작 없는 자동 수집을 검증한다.
- [ ] `PC-SC1-T05` 전체 thread, 수정, 삭제 revision의 증분 동기화를 검증한다.
- [ ] `PC-SC1-T06` Slack signature·timestamp와 event/message idempotency를 live replay로 검증한다.
- [ ] `PC-SC1-T07` retry·DEAD job과 재처리 운영 화면을 검증한다.
- [ ] `PC-SC1-T08` 정상·누락·번복·충돌을 포함한 익명화 golden sample 10개 이상을 만든다. **현재 근거:** 네 분류별 3개씩 합성 fixture 12개는 있으나 실제 팀 표현을 익명화한 표본은 아직 없다.
- [x] `PC-SC1-T09` 자유 형식 일감 parser의 필드 추출 정확도를 평가한다. **완료 근거:** `pnpm slack:eval`이 합성 12건에서 4개 필드 41/48(85.42%), 누락 필드 12/12, source revision 5/12를 재현한다. thread 최종결정에 따른 요청내용 갱신은 Phase D 지식 상태와 결합할 개선점으로 분리했다.
- [x] `PC-SC1-T10` Slack live 수집 runbook과 credential rotation 절차를 작성한다. **완료 근거:** [Slack 설치·검증 runbook](../deploy/slack/README.md)에 App 설치, secret 주입, exact ingress, live smoke, retry/DEAD, rotation·rollback과 Quick Tunnel 비영속 경계를 기록했다.

#### Phase C 종료 조건

- [ ] allowlist root와 thread가 자동 수집된다.
- [ ] 수정·삭제 이력이 append-only revision으로 남는다.
- [ ] 반복 event로 중복 work item이 생기지 않는다.

## 6. Phase D — 온톨로지 검색 완성

### Sprint D1 — LightRAG와 hybrid retrieval

- [x] `PD-SD1-T00` 과거 QA CSV를 개인정보·Jira·중복 오염 없이 retrieval corpus 후보로 정규화한다. **완료 근거:** preview-only parser가 실제 세 파일 20,805행에서 결과 포함 후보 19,380건을 누락 0건으로 회수하고 source hash·exact duplicate 통계를 남겼다. 원본과 preview는 repository·DB에 저장하지 않았으며 [기반 구현 보고서](./phase-d/sprint-d1-foundation-report.md)에 실제 dry-run 수치를 기록했다.
- [x] `PD-SD1-T01` version을 고정한 LightRAG storage adapter를 실제 PostgreSQL 환경에 연결한다. **완료 근거:** `lightrag-hku==1.5.6`, `asyncpg==0.31.0`을 고정하고 PostgreSQL 연결·workspace·SSL 설정 및 네 storage class를 검증하는 runtime 경계를 추가했다. 일회성 AGE+pgvector PostgreSQL 18에서 네 storage 초기화·11개 LightRAG table·workspace graph 생성·종료를 확인했다. 제품 기준 PostgreSQL 16 role/권한과 실제 Ollama indexing은 후속 live gate다.
- [x] `PD-SD1-T02` Slack source bundle을 index하고 citation을 원문 revision으로 연결한다. **완료 근거:** root와 thread의 create/edit/delete revision 전체를 결정적 순서의 bundle로 구성하고 permalink·tombstone evidence를 원장 node에 보존한다. duplicate·out-of-order·stale outbox 재처리와 active/retracted lifecycle을 실제 PostgreSQL projection test로 검증했다. 실제 Slack workspace round trip은 Phase C 외부 credential gate에 남아 있다.
- [x] `PD-SD1-T03` typed graph + pgvector + LightRAG 결과 merge와 ranker를 구현한다. **완료 근거:** PostgreSQL node/evidence로 해석되는 UUID만 LightRAG 후보로 수용하고 typed graph·exact·pgvector·기능 거리·LightRAG·최신성의 고정 가중치를 합산한다. lifecycle/source/node/status filter, node dedupe, score+UUID tie-break와 cursor pagination을 단위·PostgreSQL 통합 테스트로 검증했다.
- [x] `PD-SD1-T04` 상태, 출처, 기능 거리, 최신성과 결과 이유를 API에서 반환한다. **완료 근거:** 절대 rank, lifecycle/source, nullable feature distance, 최신성 기준 시각·age·score, 실제 사용 signal 기반 reason, typed degradation을 OpenAPI·Sprint 6 계약·생성 client·한국어 Knowledge UI에 반영했다. 응답/항목 degradation 일치와 rank·score 불변식도 계약에서 검증한다.
- [ ] `PD-SD1-T05` `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED` 제외 규칙을 실데이터로 검증한다. **현재 근거:** 합성 retrieval gate에서 inactive misuse 0을 재현했다. 승인된 read-only export를 7개 lifecycle/source 계층으로 결정적 표본 추출하고 민감정보를 제거하는 preview-first candidate builder도 완료했지만, 현재 로컬 DB에 Slack 원장이 0건이므로 실제 Slack label 검증은 남아 있다.
- [ ] `PD-SD1-T06` 기존 케이스·실패 실행·이슈 retrieval 품질을 golden sample로 평가한다. **현재 근거:** 합성·익명화 fixture의 recall@1 0.625, recall@3 1.0, evidence·citation 100%, 중복 0 기준은 통과했다. 기존 케이스·실패 실행·이슈가 모두 사람 검토됐을 때만 인증하는 offline result-snapshot release gate와 `UNLABELED/NOT_CERTIFIED` 후보 builder를 구현했지만 승인된 운영 label/result snapshot 평가는 남아 있다.
- [x] `PD-SD1-T07` 전체 projection rebuild와 증분 outbox 재처리를 검증한다. **완료 근거:** preview/apply 확인문, event/source digest, PostgreSQL advisory lock, `REPEATABLE READ` 원자 replay를 구현했다. Slack create/edit/delete와 테스트케이스·실행·이슈 원장을 증분 처리한 상태와 두 번의 full rebuild가 UUID 독립 canonical snapshot·checkpoint·embedding까지 같고 원장/outbox를 변경하지 않음을 PostgreSQL 16+pgvector에서 검증했다.
- [x] `PD-SD1-T08` LightRAG 장애 시 typed graph + pgvector fallback을 운영 환경에서 시험한다. **완료 근거:** digest 고정 PostgreSQL 16 AGE+pgvector auxiliary와 별도 PostgreSQL 16+pgvector 원장을 구성하고 실제 auxiliary container를 `KILL`했다. 원장은 계속 응답했고 동시 4요청이 1.050초 안에 동일한 typed graph+pgvector 순서와 `LIGHTRAG_UNAVAILABLE`을 반환했으며, 재시작 후 0.474초 안에 LightRAG hint가 복구됐다. 시험 리소스 전부 삭제도 확인했다. DNS/TLS·다중 API·부하 p95/p99·alert 전달은 Phase F 운영 비기능 gate로 분리한다.
- [x] `PD-SD1-T09` Knowledge 화면에서 모든 결과의 원문 또는 QA 원장 이동을 검증한다. **완료 근거:** Slack permalink와 tombstone 안내에 더해 `TEST_CASE_VERSION`, `EXECUTION_ATTEMPT`, `ISSUE_SNAPSHOT`을 원장 조회로 case key·cycle 실행 context·issue key 기반 app URI로 변환한다. API PostgreSQL round trip, 계약 fixture, 생성 client와 Knowledge UI 테스트를 통과했고 해석 불가능한 source만 명시적으로 이동 불가 처리한다.
- [ ] `PD-SD1-T10` retrieval 평가 리포트와 허용 품질 기준을 확정한다. **현재 근거:** [평가 계약](./phase-d/knowledge-retrieval-evaluation.md)에 candidate/release profile, dataset/results fingerprint, runtime/model/search provenance, JSON·Markdown report를 구현했다. release는 synthetic·미검토 label·불완전 provenance·세 relevance category 누락을 fail-closed하지만, 승인된 Slack·운영 embedding snapshot으로 수치를 확정하는 절차는 남아 있다.

#### Phase D 종료 조건

- [ ] 실제 Slack 결정과 QA 히스토리가 근거 링크를 가진 검색 결과로 반환된다.
- [x] 전체·증분 projection이 같은 최종 상태를 만든다.
- [x] LightRAG가 중단돼도 기본 검색이 유지된다.

## 7. Phase E — 테스트케이스 생성 API

### Sprint E1 — 근거 기반 수동 Generation API

- [ ] `PE-SE1-T01` 외부 Generation API endpoint·인증·rate limit·비용·context 한도를 확정한다.
- [x] `PE-SE1-T02` generation input/output JSON Schema와 version 정책을 구현한다. **완료 근거:** exact SemVer `1.0.0 → 1.0.0` Draft 2020-12 계약과 full-context fixture를 추가했다. provider-native 제어값·신뢰 metadata·알 수 없는 evidence/category를 거부하며 8개 집중 테스트와 전체 48개 계약 테스트가 통과했다. [계약 문서](./phase-e/generation-contract.md)
- [x] `PE-SE1-T03` Slack 일감·현재 결정·기존 케이스·과거 이슈 context builder를 구현한다. **완료 근거:** 단일 현재 Slack 일감의 ACTIVE graph만 결정적 순회하고 현재 ACTIVE 케이스, FAIL/BLOCKED 실행, 미종결 이슈만 원장 상태와 projection revision을 대조해 선택한다. stale·무근거·비활성 resource는 fail-closed하며 단위·실제 PostgreSQL 테스트를 통과했다.
- [x] `PE-SE1-T04` 사용한 node·edge·source revision·rank·reason context snapshot을 저장한다. **완료 근거:** generation job provenance와 node/edge/evidence snapshot, source revision·hash·permalink·rank·reason을 저장하는 `20260826_0017` migration을 추가했다. digest seal과 DB trigger가 context 변경·추가·삭제 및 seal 이후 job provenance 변경을 차단하며 51개 실제 PostgreSQL migration/repository 테스트가 통과했다.
- [x] `PE-SE1-T05` provider-neutral adapter, timeout, retry와 오류 code를 구현한다. **완료 근거:** credential redaction, 외부 timeout, allowlist 기반 bounded retry, stable error code, response byte streaming limit, content-type/UTF-8/중복 key/비표준 수치 거부를 갖춘 HTTP adapter와 scripted transport double을 구현했다.
- [x] `PE-SE1-T06` 필수 필드, 근거, 허구 수치와 malformed response validator를 구현한다. **완료 근거:** canonical `1.0.0` fixture를 기준으로 5~10 cases, 연속 step, 정규화 중복, ACTIVE evidence/category membership, output version을 강제하고 근거 없는 수치·정책을 결정적으로 flag 또는 reject한다. T05와 함께 35개 집중 테스트, Ruff와 strict mypy를 통과했다. [Sprint E1 기반 보고서](./phase-e/sprint-e1-foundation-report.md)
- [x] `PE-SE1-T07` existing case exact·semantic 중복 탐지와 재사용 후보를 구현한다. **완료 근거:** 정규화한 summary·순서 있는 step/precondition·case 기대결과 exact key, ACTIVE/non-deleted 필터, threshold-inclusive cosine과 stable tie-break를 구현했다. 통합 planner가 동점 semantic보다 exact를 우선하고 생성 case 순서를 보존하며 중복·매핑·planning 집중 테스트 39개를 통과했다.
- [x] `PE-SE1-T08` 카테고리·상위일감 exact·alias·semantic 매핑과 `미분류` fallback을 구현한다. **완료 근거:** 한국어 NFKC/공백/casefold exact·alias 우선, caller vector 기반 semantic, 기존 active system `미분류` fallback과 명시적 parent create intent를 하나의 pure placement plan으로 합성했다. 후보 역순 replay가 같은 결과이고 planner에는 provider·repository·원장 write 경로가 없음을 집중 테스트, Ruff와 strict mypy로 검증했다. [Sprint E1 기반 보고서](./phase-e/sprint-e1-foundation-report.md)
- [x] `PE-SE1-T09` generation job API와 수동 실행·재시도 UI를 구현한다. **완료 근거:** provider-neutral `create/list/detail/execute/retry` 계약, PostgreSQL 상태·오류·retry 계보와 본문/헤더 idempotency를 구현했다. retry는 실패 job을 덮어쓰거나 자동 실행하지 않고 봉인 context를 복사한 새 `QUEUED` job만 추가한다. T01 미확정 상태의 explicit execute는 외부 호출 없이 `GENERATION_SETUP_REQUIRED`를 반환하고 한국어 UI가 수동 만들기 경로를 유지한다. Generation 계약 16개, Python non-PostgreSQL 276개, PostgreSQL repository 52개와 API 22개, Web 150개 테스트 및 OpenAPI/schema/ADR/build gate가 통과했고 `TestCase`/`TestCaseVersion` 0건을 검증했다. production preview도 1280/768/390 px physical viewport에서 clipping·가로 overflow 없이 통과했고 mobile action 높이는 44 px였다. [계약·검증 근거](./phase-e/generation-contract.md), [Sprint E1 보고서](./phase-e/sprint-e1-foundation-report.md)
- [x] `PE-SE1-T10` 유효 결과를 `SLACK_AI` source의 `ACTIVE` 케이스로 transaction 저장한다. **완료 근거:** SUCCEEDED job의 sealed result를 POST trust boundary에서 다시 schema·grounding·digest·현재 ACTIVE placement로 검증하고, authoritative `GET .../apply-plan`을 기본 Web 화면에 연결했다. 한 transaction이 필요한 parent와 새 ACTIVE `SLACK_AI` case/version/ordered step/label/evidence provenance를 만들고 exact duplicate는 사람 작성 version을 변경하지 않은 채 재사용한다. 같은 idempotency replay는 원래 receipt를 반환하고 다른 요청·상태·stale placement는 stable conflict로 거부된다. PostgreSQL middle-candidate/late-outbox fault에서 parent·case·version·step·tag·receipt·outcome·key·audit·outbox delta가 모두 0이었고 clean retry는 같은 다음 key를 사용했다. 전체 lint/typecheck/contract/schema/ADR/OpenAPI/build, Python non-PostgreSQL 276개, PostgreSQL repository 53개/API 27개, Web 158개와 local production-preview desktop/mobile apply smoke가 통과했다. [계약·검증 근거](./phase-e/generation-contract.md), [Sprint E1 보고서](./phase-e/sprint-e1-foundation-report.md)
- [x] `PE-SE1-T11` 원문·결정·기존 케이스·생성 이유 비교 화면을 구현한다. **완료 근거:** single read-only `GET .../comparison`이 immutable job/context/result/apply receipt만 사용해 exact sealed Slack/thread, snapshot-time ACTIVE decision/constraint, pinned case/version, ordered candidates/reasons/questions/inference와 candidate→source edge를 반환한다. live Slack/knowledge/case 변경 뒤 response equality, GET row-count/provider tripwire, 404/409와 empty/redacted/missing/delete 상태, created/reused linkage를 PostgreSQL/API/contract/Web test로 검증했다. generated OpenAPI client와 job link, keyboard evidence disclosure/source anchor를 연결했고 production preview 1280/768/390 px에서 overflow 없이 통과했다. [비교 계약·검증 근거](./phase-e/generation-comparison-contract.md), [Sprint E1 보고서](./phase-e/sprint-e1-foundation-report.md)
- [x] `PE-SE1-T12` 동일 context snapshot으로 생성 입력을 재구성하는 E2E를 구현한다. **완료 근거:** `20260827_0020`이 pre-provider Generation Input 전체와 input schema version·RFC 8785 SHA-256을 `generation_jobs`에 원자적으로 봉인하고 기존 job은 명시적 `LEGACY_NOT_CAPTURED`로 남긴다. read-only `GET .../reconstruction`은 job/context snapshot만 읽어 exact Input `1.0.0` 또는 legacy/incomplete/unsupported 상태를 반환하며, generated OpenAPI client도 같은 타입을 노출한다. 실제 provider runner 경계에서 관찰한 payload와 response input/digest의 equality, 반복 read, live Slack·knowledge·case·issue·category 변경/삭제 후 historical equality, source/apply/ledger/audit/outbox 포함 전체 row-count·SQL live-read·provider tripwire, 404와 unavailable execution 409, 16 MiB 초과 capture의 job/context 0-delta를 contract/unit/disposable PostgreSQL/API 테스트로 검증했다. Web 화면은 이 read contract의 완료 조건이 아니므로 추가하지 않았다. [재구성 계약·검증 근거](./phase-e/generation-reconstruction-contract.md), [Sprint E1 보고서](./phase-e/sprint-e1-foundation-report.md)

### Sprint E2 — 자동 생성과 사람 수정 보호

- [x] `PE-SE2-T01` quiet window 만료 후 지식 추출→검색→생성 orchestration을 연결한다. **완료 근거:** 현재 quiet job/attempt fence→4필드 parse/outbox/handoff→검증된 sealed Slack bundle 지식 projection→PostgreSQL grounded context/Input `1.0.0` seal→외부 실행 없는 단일 root `QUEUED` job을 연결했다. projection·embedding·checkpoint·context·job·audit/receipt는 한 commit이고 exact replay와 기존 non-stale checkpoint resume를 지원한다. provider lifecycle tripwire 0회, embedding/post-seal fault 전체 rollback, supported-type claim·heartbeat·attempt CAS를 집중 PostgreSQL 8개와 전체 Python 283개, repository 56개, API 37개, knowledge 11개 회귀로 검증했다. canonical message-key/source-version 제품 중복 방지는 T02에서 완성했다. [orchestration 계약](./phase-e/automatic-generation-orchestration-contract.md), [Sprint E2 기반 보고서](./phase-e/sprint-e2-foundation-report.md)
- [x] `PE-SE2-T02` Slack message key와 generation version 기반 중복 방지를 구현한다. **완료 근거:** `20260827_0021`이 `MANUAL|AUTOMATIC` trigger provenance와 automatic-root `(slack_work_item_id, source_version)` 부분 unique index를 설치하고, canonical `SlackWorkItem` 잠금 아래 get-or-create/ordinal 할당을 수행한다. exact T01 receipt 우선, receipt 없는 canonical root 재사용·checkpoint 복구, 수동 반복과 retry trigger 상속, manual receipt 거부, 경합·rollback 재시도를 PostgreSQL T02 8개(원래 RED 계약 7개 + 실행 경로 retry 상속 1개), T01 포함 16개, migration/repository 57개와 API 45개 회귀로 검증했다. 배포 전 `0020` historical collision preflight는 0행이어야 하며 실제 signed Slack/provider gate는 열려 있다. [중복 방지 계약](./phase-e/automatic-generation-deduplication-contract.md), [Sprint E2 기반 보고서](./phase-e/sprint-e2-foundation-report.md)
- [x] `PE-SE2-T03` thread 변경 영향 분석과 선택적 재생성을 구현한다. **완료 근거:** `20260827_0022` append-only assessment chain이 canonical 4필드·ACTIVE knowledge/evidence·retrieval/category/policy projection과 별도 contract-config digest를 비교한다. `INITIAL_GENERATE|REGENERATE`만 같은 built context로 sealed `AUTOMATIC` root를 만들고, `SKIP`은 마지막 automatic materialization을 재사용하며 `REVIEW_REQUIRED`는 ambiguous·deleted/retracted·legacy/unsafe 입력에서 job을 만들지 않는다. v1→v2 SKIP→v3, full-input digest 배제, 4필드 외 의미 변화, same-text/late-old, manual/retry baseline 배제, receipt/replay, stale·경합·rollback을 PostgreSQL 16 T03 16개로 검증했고 T01/T02 16개, migration 57개, non-PostgreSQL 286개, Ruff와 strict mypy 회귀도 통과했다. [영향 분석 계약](./phase-e/automatic-generation-impact-contract.md), [production 보고서](./phase-e/pe-se2-t03-production-implementation-report.md), [Sprint E2 보고서](./phase-e/sprint-e2-foundation-report.md)
- [x] `PE-SE2-T04` 사람 수정 시 `generation_managed` 해제와 자동 overwrite 차단을 구현한다. **완료 근거:** `20260827_0023`이 CREATED-only immutable lineage, write-once release receipt, append-only management event와 no-op idempotency receipt를 설치하고 public source/actor spoof, REUSED 승격, raw SQL 재획득을 fail-closed로 막는다. semantic PATCH·parent move·version restore·soft delete가 version/audit/outbox와 원자적으로 release되고 exact PATCH/move는 domain total no-op이며, deleted restore와 이후 REUSED는 release를 되돌리지 않는다. 독립 adversarial PostgreSQL 12개, T01~T03/apply/case 44개, migration/repository 57개, Python unit 286개, TypeScript unit 279개, Ruff, strict mypy, schema/OpenAPI/generated client/typecheck와 실제 `0023 -> 0022 -> 0023` round-trip을 통과했다. [management 계약](./phase-e/automatic-generation-management-contract.md), [production·독립 검증 보고서](./phase-e/pe-se2-t04-production-implementation-report.md)
- [x] `PE-SE2-T05` generation suggestion diff·적용·무시 API와 UI를 구현한다. **완료 근거:** `20260827_0024`가 sealed opaque target, immutable baseline/proposal/diff, same-case source-ordered supersession과 append-only APPLIED/IGNORED receipt를 설치했다. apply는 workspace-first lock과 exact case pins로 새 version 하나만 추가하면서 T04 released/no-reacquisition 상태를 보존하고 ignore는 case ledger를 0건 변경한다. forged/cross-workspace ref, raw SQL provenance·audit/receipt spoof, apply-vs-ignore·human edit 경합, stale/rollback/delete/restore/permanent-delete를 실제 PostgreSQL에서 검증했고 T05 9개, T01~T04/apply/case/cycle 58개, migration 57개, Python unit 289개, TypeScript unit 290개, Web 176개와 schema/OpenAPI/generated client/Ruff/strict mypy/typecheck/ESLint/build 및 `0024 → 0023 → 0024`가 통과했다. [suggestion 계약](./phase-e/automatic-generation-suggestion-contract.md), [production·독립 검증 보고서](./phase-e/pe-se2-t05-production-implementation-report.md)
- [x] `PE-SE2-T06` 생성 성공·실패를 원본 Slack thread에 중복 없이 게시한다. **완료 근거:** `20260828_0025`가 apply/suggestion/final non-retryable failure와 frozen intent·delivery job을 같은 transaction에 기록하고 네 append-only notification ledger와 canonical source/job/materialization deferred guard를 설치했다. Worker는 committed POST permit 뒤 DB 밖에서 invited-thread `chat.postMessage`만 한 번 호출하며 ack loss·crash는 exact marker/client ID/parent/text reconciliation으로만 복구하고 final ambiguity·rate-limit exhaustion에도 terminal receipt를 남긴다. stale/unavailable/config mismatch는 provider-free suppression, manual/SKIP/REVIEW_REQUIRED/retryable failure는 무게시이며 self reply는 Events/thread sync source를 바꾸지 않는다. 독립 감사에서 retryable caller 우회, final-attempt receipt 누락, reconciliation destination drift, forged DEAD receipt, unsafe UI mrkdwn delimiter와 credential-enabled implicit write를 보강했다. T01~T06 PostgreSQL 76개, migration/repository 57개, Python non-PG 308개, TypeScript 296개(실제 E2E 1 skip), Slack unit 31개, worker 13개, Ruff/mypy/schema/ADR/OpenAPI/typecheck/ESLint/Web build와 `0025 → 0024 → 0025`가 통과했다. 실제 Slack credential smoke와 automatic provider execution/materialization scheduling은 각각 live gate와 T07 경계로 남는다. [Slack reply 계약](./phase-e/automatic-generation-slack-reply-contract.md), [production·독립 검증 보고서](./phase-e/pe-se2-t06-production-implementation-report.md)
- [x] `PE-SE2-T07` 실패 관리, 조건 추가 재시도와 attempt history를 구현한다. **완료 근거:** `20260828_0026`의 execution/event/permit/observation/retry/exhaustion/materialization 원장과 deferred provenance guard, worker lease·provider single-call·검증 후 finalization·별도 materialization을 production main에 조립했다. provider 설정은 명시적 opt-in이며 disabled 기본은 credential·endpoint 노출이나 network call 없이 setup-required로 닫히고 enabled incomplete 설정은 startup에서 fail-fast 한다. 독립 adversarial 감사에서 success-observation crash의 blind resend, notification 경로의 synthetic pre-SUCCEEDED materialization, background authority 변조/삭제, operator-retry 뒤 stale actionability, Web root CSS/semantic/filter/idempotency 결함을 보강했다. focused RED 5+2+3, normal T07 Python 5·PostgreSQL 4, T01~T06 PostgreSQL 79, migration/repository 57, Python non-PG 326, TypeScript 307(실제 E2E 1 skip), Ruff/strict mypy/schema/ADR/OpenAPI/ESLint/typecheck/Web build와 `0026 → 0025 → 0026` 및 clean-head migration이 통과했다. 실제 provider/Slack credential 호출과 staging process-kill/DB failover는 live gate로 남긴다. [production·독립 검증 보고서](./phase-e/pe-se2-t07-production-implementation-report.md), [운영 runbook](../deploy/generation/README.md)
- [x] `PE-SE2-T08` evidence 100%, 폐기 지식 오사용 0, 중복 0 기준 golden eval을 release gate에 연결한다. **코드 완료 근거:** strict schema와 재계산 fingerprint를 먼저 검증하는 deterministic offline evaluator가 case+step integer coverage, retired/unresolved authority, exact+reviewed semantic cluster/category/parent duplicate, result/order/materialization provenance와 operational review/freshness/live attestation을 all-or-nothing으로 판정하고 JSON/JUnit/Markdown을 fresh-write한 뒤 자체 report schema를 재검증한다. malformed·empty·NaN·missing·fingerprint tamper·cached output은 exit 2, schema-valid metric/eligibility failure는 exit 1, gate PASS는 exit 0이며 synthetic candidate는 항상 `NOT_CERTIFIED_SYNTHETIC`이다. Production apply planner는 workspace/version/context/result와 policy version/dimension/threshold/digest에 묶인 deterministic offline semantic-vector receipt를 사용하고 audit placement seal에 포함한다. focused production 3개, evaluator/semantic 회귀 16개, quality contract 7개, generation apply disposable PostgreSQL 16개, Python unit 339개, TypeScript unit 314개와 `pnpm check`가 통과했다. **별도 차단 상태:** 실제 운영 `REVIEWED` 32-root golden과 credentialed provider live attestation은 repository에 없으므로 protected release workflow가 명시적으로 실패하며 release certification은 발급하지 않았다. [quality gate 계약](./phase-e/automatic-generation-quality-gate-contract.md), [production 구현 보고서](./phase-e/pe-se2-t08-production-implementation-report.md), [독립 검증 보고서](./phase-e/pe-se2-t08-independent-verification.md)
  - **2026-09-01 operational candidate bridge E7 보강:** PostgreSQL `REPEATABLE READ, READ ONLY` APPLY slice exporter가 private T08 input을 재구성하되 파일/API에는 E4 `ACTUAL_MINIMIZED` revision-one envelope만 낸다. 공개 projection은 aggregate count/class distribution과 explicit non-certification state뿐이고 root set/private snapshot은 domain-separated HMAC seals, public digest는 sanitized envelope fingerprint로 제한한다. Legacy source-derived SHA/alias/ID/report path/text/URL/endpoint는 공개하지 않으며 import가 legacy payload를 거부한다. Parent CREATE complete inventory, suggestion slice, 실제 32-root/16분류 review corpus와 live attestation은 계속 별도 gate다. [candidate export](./phase-e/automatic-generation-quality-candidate-export.md)
  - **2026-09-01 review ledger/API E7 보강:** verified E4 envelope를 immutable candidate에 결합하고 installed reviewer authority ledger, recomputable authority/decision/approval integrity trigger, direct DML 없는 `tc_quality_review_writer` role과 narrow security-definer decision function을 추가했다. Application token mapping은 constant-time을 유지하고 DB에는 plaintext token을 넣지 않는다. Safe list/detail/decision과 reviewed-export 2.0.0은 keyed reviewer/source seals와 sanitized fingerprints만 공개하며 attestation `NOT_RUN`, `eligible=false`, `releaseCertified=false`를 고정한다. 실제 approved fixture를 seed하거나 approval/certification을 만들지 않았고 실제 32-root/16분류 sample과 credentialed provider live attestation이 없으므로 아래 Phase E 실제 sample gate는 그대로 미완료다. [review workflow](./phase-e/automatic-generation-quality-review-workflow.md)
- [x] `PE-SE2-T09` prompt·model·embedding·schema version 변경 회귀 절차를 구현한다. **코드 완료 근거:** immutable version-set identity, strict comparator CLI, append-only PostgreSQL ledger/migration, two-person approval·activation·rollback service와 protected release workflow를 추가했다. reviewed operational golden/provider live attestation 부재로 production activation은 BLOCKED이며 synthetic candidate는 인증되지 않는다. [production 구현 보고서](./phase-e/pe-se2-t09-production-implementation-report.md)
- [x] `PE-SE2-T10` root→thread→ontology→케이스→Slack 답글 전체 E2E를 구현한다. **완료 근거:** production worker main이 explicit/default-off `KnowledgeOutboxConsumer`를 structured Ollama extraction+BGE-M3 composition으로 실제 조립하고 Slack `chat.getPermalink`를 봉인 입력까지 전달한다. signed root+reply가 disposable PostgreSQL에서 quiet/parse→grounded ontology/embedding→sealed automatic execution→5개 `ACTIVE`/`SLACK_AI` case→동일 root thread 답글까지 production handlers를 통과했으며 event/projection/job replay cardinality 불변, ack-loss reconciliation 1-post, self-reply 제외, stale-source 무게시와 private ACL 재검사를 검증했다. T01~T10+Slack repository PostgreSQL 97개, 집중 회귀 4+2개, Python non-PostgreSQL 370개와 전체 Python Ruff/strict mypy가 통과했다. 실제 Slack/Ollama/generation-provider credential smoke와 T08/T09 operational attestation은 별도 live gate이고 human-modified 경로는 별도 T11 E2E가 닫았다. [E2E 계약](./phase-e/automatic-generation-root-to-slack-e2e-contract.md), [production 구현 보고서](./phase-e/pe-se2-t10-production-implementation-report.md)
- [x] `PE-SE2-T11` 사람 수정 케이스에 suggestion만 생성되는 회귀 E2E를 구현한다. **완료 근거:** T10 signed root/reply로 만든 5개 `ACTIVE`/`SLACK_AI` generation-managed 케이스 중 하나를 public semantic PATCH로 one-way release한 뒤 signed root `message_changed`를 production worker가 source v3 `REGENERATE`→suggestion schema execution→released lineage 1개만 target→`OPEN` suggestion과 원 thread `SUCCESS_SUGGESTIONS` 답글로 처리했다. 원 case/current version/version history/step/label/parent/sort/status/release receipt는 byte-for-byte 불변이고 apply 1·outcome 5를 유지하며 materialization의 automatic overwrite는 0이다. source/projection/enqueue/materialization/reply cardinality 불변과 T04/T05 apply·ignore·supersession·stale·concurrency·delete·rollback 보호를 PostgreSQL 25개 집중 회귀와 T01~T11/apply/Slack 86개 통합 회귀로 검증했고 non-PostgreSQL 370개, Ruff, strict mypy, schema/ADR/OpenAPI gate도 통과했다. [T11 E2E 계약](./phase-e/automatic-generation-human-release-to-suggestion-e2e-contract.md), [production 구현 보고서](./phase-e/pe-se2-t11-production-implementation-report.md)

#### Phase E 종료 조건

- [x] 자동 생성 케이스가 원본 링크와 context snapshot을 가진다.
- [x] 같은 source와 job을 반복 처리해도 중복 생성되지 않는다.
- [x] 사람이 수정한 케이스를 자동으로 덮어쓰지 않는다. **근거:** T11 signed production E2E가 human release 뒤 automatic apply·case/version/step/label/parent delta 0, `OPEN` suggestion 1, `automaticOverwriteCount=0`과 원 Slack thread terminal reply를 검증한다.
- [ ] 합의한 실제 일감 sample 품질 기준을 충족한다.

## 8. Phase F — 운영 배포

### Sprint F1 — 보안·복구·성능·출시

- [ ] `PF-SF1-T01` reverse proxy와 방화벽으로 UI·일반 API를 사내망/VPN에 제한한다. **코드 기반 근거:** internal Nginx listener가 exact allowlist include와 final deny, trusted-proxy 이후 forwarding header overwrite, internal Slack path 제외를 강제한다. 실제 firewall/VPN/LB 설치와 양방향 black-box probe가 없어 task는 열어 둔다. [wave 1 통합 검증](./phase-f/sprint-f1-wave1-verification.md)
- [ ] `PF-SF1-T02` Slack ingress만 최소 경로로 외부 노출한다. **코드 기반 근거:** public listener가 exact Slack POST 외 경로를 거부하고 query/content-type/body/rate/connection/timeout/no-retry bound를 적용한다. 실제 TLS endpoint와 signed Slack ingress가 없어 task는 열어 둔다. [wave 1 통합 검증](./phase-f/sprint-f1-wave1-verification.md)
- [ ] `PF-SF1-T03` secret 암호화·rotation·log masking을 검증한다. **코드 기반 근거:** `SecretStr`, query-free normalized edge log, security header, exact proxy trust와 rotation 절차를 검증했고 Phase F subprocess에서 unrelated secret 환경 상속도 차단했다. 실제 secret store encryption·least privilege·rotation rehearsal이 없어 task는 열어 둔다. [wave 1 통합 검증](./phase-f/sprint-f1-wave1-verification.md)
- [x] `PF-SF1-T04` XSS, SQL injection, path traversal과 파일 보안 회귀를 수행한다. **완료 근거:** React text escaping, bound SQL attack, Unicode/path traversal, RFC 5987 filename, MIME/size/SHA/malware fail-closed와 proxy/config 회귀가 통과했고 disposable PostgreSQL 공격 2개를 재검증했다. [wave 1 통합 검증](./phase-f/sprint-f1-wave1-verification.md)
- [ ] `PF-SF1-T05` API·worker·Slack·R2·outbox·generation 운영 지표와 alert를 구성한다. **코드 기반 근거:** bounded-cardinality metrics, readiness/degradation, atomic worker textfile, alert rule과 runbook을 통합했고 missing scrape/textfile guard를 보강했다. production-version `promtool`, real receiver delivery·acknowledgement와 dashboard evidence가 없어 task는 열어 둔다. [wave 1 통합 검증](./phase-f/sprint-f1-wave1-verification.md)
- [ ] `PF-SF1-T06` 10만 케이스와 예상 Slack·generation 부하 성능 테스트를 수행한다. **로컬 disposable 근거:** wave 5에서 preview 후 새 exact-comment/empty loopback PostgreSQL DB에 고정 100,000 case fixture를 적재하고 concurrency 16, 120초 mixed profile을 실행했다. 결함을 드러낸 첫 FAIL evidence를 보존하고 empty-Alembic preflight, connection-pool headroom, Docker PostgreSQL shared-memory를 회귀 테스트와 함께 수정한 뒤 새 DB로 재실행했다. 최종 4,832건 오류 0, warm throughput `40.462/s`, API/Slack/worker/generation warm p95 `998.554/112.645/178.893/55.309 ms`, queue p95 `488.251 ms`, lease p99 `145.389 ms`, provider outbound `0`으로 로컬 gate를 PASS했고 exact DB 두 개만 검증 후 삭제했다. **열린 gate:** operator/reviewer 승인, production-like multi-process 및 DB/container resource, edge/TLS/WAN, staging, true cold-cache/soak/alert evidence가 `NOT_RUN`이므로 task와 출시 gate는 열어 둔다. [부하 프로파일 보고서](./phase-f/pf-sf1-t06-concurrent-load-profile.md) · [wave 5 검증](./phase-f/sprint-f1-wave5-verification.md)
- [ ] `PF-SF1-T07` PostgreSQL 일일 backup과 별도 환경 restore rehearsal을 수행한다. **두 번째 독립 RED remediation 뒤 로컬 disposable 근거:** backup evidence와 dump는 각각 single `O_RDONLY|O_NOFOLLOW` descriptor에 고정하고 regular/mode-at-most-0600/nlink-1 및 device/inode/size/mtime-ns/ctime-ns를 JSON read/parse, SHA, archive inventory, 실제 restore 전후에 검사한다. 최종 archive-inventory 코드로 새 exact-comment retry6 source/restore DB에서 preview-first 40 rows를 head `20260902_0030`에 적재하고 605,484-byte custom archive(SHA-256 `0df58e095a88533ab419f1d7c433d4a0fd65f011e0a13c109dd913e133105bcf`, 791 entries, listing SHA-256 `2b256f661cf02c962238aa28b2e341385b9e055bc156b20bd018912314441665`)를 empty DB에 `--clean` 없이 복원했다. Catalog v3 16 categories, 78 tables/40 rows, catalog digest `8846c25f73faecdc56220b9597a399b5e44a41202b0dc4ac1eec5dc18414ed62`, data digest `28eb0dee7a6a684d921cf251898e72802e620875884a98a7ff9fc59be9e1fc3d`가 source/restored/full post-smoke에서 일치했다. FastAPI는 read-only transaction에서 5개 GET 200을 반환했다. Backup `0.643859s`, restore `4.011901s`, restore→smoke `6.526439s`, recovery-point age `30.773653s`는 local measurement뿐이다. 모든 기존 evidence는 보존했고 retry4-final과 retry5-final은 historical superseded다. **열린 gate:** daily scheduler, encrypted retention, separately hosted staging restore, matched Cloudflare R2, operator handoff, production RTO/RPO는 모두 `NOT_RUN`; task와 release gate는 unchecked/open이다. [T07 readiness](./phase-f/sprint-f1-recovery-performance-readiness.md#t07--postgresql-backup-and-isolated-restore) · [wave 6 검증](./phase-f/sprint-f1-wave6-verification.md)
- [ ] `PF-SF1-T08` R2 attachment metadata·object 복구와 무결성을 시험한다. **LOCAL MINIO DISPOSABLE sub-gate PASS:** wave 7에서 preview-first exact-comment source/recovery PostgreSQL DB와 distinct empty source/recovery MinIO bucket을 만들고 12개 object(지원 owner type 2 × fixture MIME class 3 × 2)를 하나의 recovery-point manifest로 결합했다. Source/restored catalog v3 78 tables/53 rows, attachment metadata 12/12, object inventory 12/12의 versioned canonical digest가 일치했고 HEAD 전체, 12개 full-byte SHA sample, 4-page metadata dry-run, exact `MINIO` provider/bucket/`PRIVATE`/`FAIL_CLOSED_CLAMAV`, anonymous 403/no redirect를 검증했다. Immutable mode-0600 single-link evidence를 검증한 뒤 exact DB/bucket/dump/temp만 삭제했고 pre-existing DB/bucket inventories와 `tc` fingerprint를 보존했다. **열린 gate:** 이는 Cloudflare R2 evidence가 아니며 actual recovered R2 bucket/versioning/backup, matched separately hosted staging DB, operator handoff, production RTO/RPO는 `NOT_RUN`; task와 Phase F release gate는 unchecked/open이다. [wave 7 검증](./phase-f/sprint-f1-wave7-verification.md)
- [ ] `PF-SF1-T09` pgvector·LightRAG 전체 reindex와 fallback 전환을 시험한다. **LOCAL DISPOSABLE NETWORK-FREE sub-gate PASS:** wave 8 remediated run `sg9c42fd`에서 preview-first exact `pf-sf1-t09-wave8` loopback PostgreSQL 16 source copy와 distinct empty AGE+pgvector auxiliary를 만들고 official LightRAG 1.5.6 production child로 complete supported 2-document corpus를 3개 immutable workspace에 index했다. Document/status/chunk/canonical UUID citation, 1024d chunk/entity/relation vectors와 AGE semantic node/property/topology digest를 page size 1로 검증했고 workspace-local AGE IDs와 provider `created_at`은 제외하면서 세 generation digest의 exact equality를 seal 전에 강제했다. Immutable registration, separate API/worker CAS cutover, stale-result 폐기, failed rollback pointer preservation, prior-generation rollback/retention, post-CAS activation rollback, counterfeit provider rejection, exact child `SIGKILL`과 auxiliary PostgreSQL stop 각각의 typed graph+pgvector fallback, restart recovery가 통과했다. Mode-0600 single-link evidence 뒤 exact containers/volumes/network/databases/workspaces/dump/temp/process를 삭제했고 pre-existing Docker/DB/current-`tc`/25 prior evidence hashes를 보존했다. 기존 두 evidence pair는 historical이며 `sg9c42fd` pair만 authoritative다. **열린 gate:** staging credentials/bootstrap, live provider call, staging apply, production-like multiprocess networking/load/alerts, operator handoff와 production RTO/RPO는 모두 `NOT_RUN`; `liveApplySupported=false`이고 task/Phase F release gate는 unchecked/open이다. [wave 8 검증](./phase-f/sprint-f1-wave8-verification.md) · [LightRAG 통합 보고서](./phase-f/pf-sf1-t09-lightrag-full-reindex.md) · [runtime routing evidence](./phase-f/pf-sf1-t09c-lightrag-runtime-routing.md) · [credential-owning factory evidence](./phase-f/pf-sf1-t09d-lightrag-generation-factory.md)
- [ ] `PF-SF1-T10` production migration rollback·roll-forward를 staging copy에서 검증한다. **LOCAL DISPOSABLE sub-gate PASS:** Wave 9 run `w9rt7c3b`는 실제 migration graph의 unique `20260902_0030 → 20260901_0029 → 20260902_0030` edge를 고정하고, current `tc`를 exported repeatable-read/read-only snapshot과 pinned 689,165-byte dump로만 읽어 exact-comment empty loopback PostgreSQL 16 copy에 복원했다. Predecessor에서 head-owned 3 tables/3 functions/4 triggers 부재와 surviving data/roles 불변을 확인하고 roll-forward 뒤 catalog v3 16 categories(78 tables, 945 logical columns), normalized 707 rows, roles/memberships가 baseline과 정확히 일치했다. PostgreSQL dropped-column physical slot compaction과 pristine pointer의 regenerated `updated_at`만 비-semantic drift로 분리했으며 다른 schema/data drift는 허용하지 않았다. API 4 GET 200, transactional write rollback, 9-handler worker startup/zero claim/zero external call 뒤 fingerprint도 불변이었다. Mode-0600 single-link evidence를 봉인하고 exact container/volume/network/database/dump/temp/process를 제거했으며 current-`tc`, pre-existing Docker/20 DB inventory와 prior evidence 27개 hash를 보존했다. **열린 gate:** separately hosted staging copy, staging writer/worker quiescence와 live service smoke, operator/reviewer handoff, broader migration boundaries, production RTO/RPO는 모두 `NOT_RUN`; task, Phase F와 release gate는 unchecked/open이다. [wave 9 검증](./phase-f/sprint-f1-wave9-verification.md)
- [ ] `PF-SF1-T11` 전체 인수 조건과 실제 QA UAT를 수행한다. **Wave 10 독립 RED remediation 뒤 근거:** independent RED `task_0c95d0badb67`은 harness teardown 뒤 모든 local abort를 안전한 것으로 분류하고 aborted MinIO PUT도 허용하던 producer/validator/fixture 결함을 발견했다. Remediation `task_d1aff5905dae`는 exact Web port, `NET_ERR_ABORTED`, `GET`/`fetch`, normalized allowlisted path와 정확한 workflow/teardown disposition만 허용하도록 producer와 strict validator를 fail-closed로 바꿨다. 새 authoritative final-code run `92af97f8`에서 system-installed Chrome이 network mocking 없이 실제 Vite/FastAPI/worker와 disposable PostgreSQL 16·MinIO·ClamAV를 사용했다. 15개 workflow PASS를 고정값이 아니라 37개 실행 assertion에서 도출하고, dashboard hard reload/KPI와 Reports API-UI reconciliation, PLANNED/complete/reopen/delete-restore cycle 경계, terminal generation failure 및 production local 경로로 만든 sealed `CANDIDATE/UNREVIEWED` quality-review safe state, 19개 mobile route와 failure/quality detail 및 execution modal, hidden/overflow primary control 및 모든 aborted request를 검증했다. Exact canonical manifest workflow/check mapping과 validator digest, strict schema-v1 evidence/cleanup validator, mode-0600 single-link evidence, exact cleanup과 current-`tc` 불변을 봉인했고 `r10k8a3d`는 historical로 보존했다. 자동 15 PASS와 owner 수동 15 `NOT_RUN`은 분리되며 deployed exact HTTPS origin, 실제 QA owner 판단, live external integration, production/release는 여전히 `NOT_RUN`이므로 task와 Phase F는 열어 둔다. [UAT readiness](./phase-f/pf-sf1-t11-uat-readiness.md) · [Wave 10 검증](./phase-f/sprint-f1-wave10-verification.md)
- [ ] `PF-SF1-T12` 설치·재시작·job 재처리·Slack·R2·모델·복구 runbook을 완성한다. **준비 근거:** 한국어 우선 통합 진입점, 기존 subsystem runbook의 exact reference, section별 사전 조건·증거·중단 조건과 secret/destructive-target validator를 추가했다. Site-specific controller 명령, 실제 rotation/recovery/alert rehearsal과 operator·reviewer 인계 증거가 `NOT RUN`이므로 task는 열어 둔다. [통합 운영 runbook](../deploy/operations/README.md) · [wave 2 통합 검증](./phase-f/sprint-f1-wave2-verification.md)
- [ ] `PF-SF1-T13` 알려진 제한과 출시 이후 backlog를 확정한다. **현재 근거:** wave 2 검증 문서가 확인된 기술·운영 제한과 다음 gate를 통합했지만 product/release owner의 범위 확정, 우선순위 승인과 backlog 인계가 없어 task는 열어 둔다. [wave 2 제한과 다음 gate](./phase-f/sprint-f1-wave2-verification.md#limitations-and-next-gates)
- [ ] `PF-SF1-T14` production 배포와 초기 관찰 기간을 완료한다.
- [ ] `PF-SF1-T15` 배포된 정확한 HTTPS Web origin을 R2 CORS에 설치하고 그 origin의 physical browser에서 PUT·GET·HEAD preflight와 다중 첨부 upload→download→delete를 검증한다. **준비 근거:** one-origin/no-wildcard exact policy validator와 secret-safe no-redirect preflight wrapper를 구현하고 example policy를 local 검증했다. deployed bucket policy, exact HTTPS origin request와 physical-browser 다중 첨부 flow는 `NOT RUN`이다. [wave 1 통합 검증](./phase-f/sprint-f1-wave1-verification.md)

#### Phase F 종료 조건

- [ ] 실제 팀 업무에 배포됐다.
- [ ] 핵심 smoke와 데이터 무결성 검사가 통과한다.
- [ ] 복구와 장애 대응 절차를 실제 시험했다.
- [ ] 운영 책임자에게 runbook과 credential 복구 절차를 전달했다.

## 9. 오케스트레이션 실행 순서

```text
Phase A
  Sprint A1 원장 영속화
    ├─ demo/mock 경계 조사
    ├─ 상위일감 API·Web 영속화
    ├─ Test Library mutation 영속화
    ├─ cycle fallback·diff·CSV·Drive legacy 정리
    └─ 통합 E2E와 보고서
  Sprint A2 실행 모델
  Sprint A3 R2 운영
  Sprint A4 브라우저·디자인 회귀
    └─ Phase A gate

Phase B 실제 QA UAT
  └─ 관리툴 기능 동결 gate

Phase C Slack live
  └─ Phase D ontology retrieval
       └─ Phase E Generation API
            └─ Phase F production release
```

오케스트레이션은 Sprint마다 Run을 만들고 독립 파일 범위의 Task만 병렬 dispatch한다. 같은 파일을 수정하는 Task는 dependency를 연결해 순차 실행한다. 각 worker는 자동 테스트와 변경 파일을 `worker_done`에 보고하고 coordinator가 통합 검사 후 Sprint gate를 닫는다.

## 10. 주요 위험과 대응

- [ ] **남은 mock이 실제 데이터처럼 보임:** Phase A1에서 운영 fallback을 제거하고 API 오류를 명시한다.
- [ ] **WIP legacy migration 데이터 손실:** 변환 전 분포를 측정하고 rollback 가능한 migration으로 수행한다.
- [ ] **R2 무료 사용량 초과:** 저장량·요청량·비용 alert와 orphan 정리를 운영 gate로 둔다.
- [ ] **무로그인 외부 노출:** production에서 네트워크 차단을 직접 검증한다.
- [ ] **Slack 자유 형식 파싱 오차:** 실제 익명화 sample을 지속 추가한다.
- [ ] **결정 번복·충돌 오판:** evidence와 `AMBIGUOUS` 제외 원칙을 유지한다.
- [ ] **LightRAG 장애·버전 drift:** version을 고정하고 typed graph + pgvector fallback을 유지한다.
- [ ] **AI가 사람 편집을 덮어씀:** `generation_managed`와 suggestion E2E를 release gate로 둔다.

## 11. MVP 이후 후보

- [ ] 테스트케이스 파일 가져오기·내보내기
- [ ] CI/CD와 자동화 테스트 결과 연동
- [ ] 저장된 검색 view와 고급 리포트
- [ ] 요구사항 coverage와 릴리스 품질 지표
- [ ] AI 회귀 범위 추천과 기존 케이스 자동 재사용
- [ ] 조직 확장 시 로그인·권한·복수 프로젝트 재검토
