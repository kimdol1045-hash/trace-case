# Phase A Sprint A4 검수 보고서

- 검수일: 2026-08-24, 최종 독립 재검수 2026-08-25 (Asia/Seoul)
- 범위: `PA-SA4-T01`~`PA-SA4-T12`, Test Library·상태 배지·연결 근거·실행 버튼 마감 결함, 배포 전 localhost actual R2 경계
- 환경: physical Chrome desktop 1920×850, Vite `localhost:5173`/`127.0.0.1:5173`, FastAPI `localhost:8000`, live PostgreSQL, private Cloudflare R2, fail-closed ClamAV
- 결론: **배포 전 로컬 UI·actual private R2 data path는 GREEN이다. 다만 current `pnpm check`는 Web Fast Refresh warning 3건으로 RED이고 physical 다중 파일 browser 전체 흐름은 `NOT RUN — owner manual follow-up`이므로 Phase A는 미종료다.**

## 1. 판정 요약

| Task | 판정 | 검수 근거 |
|---|---|---|
| `PA-SA4-T01` | PASS | Orca 내장 Browser의 실제 `drag` 동작으로 같은 상위일감 reorder, populated 타 상위일감 이동, empty 상위일감 drop을 순서대로 실행하고 API projection의 source/target 순서와 건수를 재확인했다. reducer-only 테스트로 대체하지 않았다. |
| `PA-SA4-T02` | PASS | 테스트·사이클·이슈의 create/save/cancel/back을 실제 route에서 실행했다. 취소된 draft는 목록 건수를 바꾸지 않았고 저장된 엔티티만 detail/history 및 reload 후 나타났다. |
| `PA-SA4-T03` | PASS | route component를 lazy split하고 고정 최소 높이의 `Suspense` fallback을 추가했다. list→create→detail→back 및 hard reload에서 이전 엔티티가 노출되지 않았고 `/test-cases`, `/cycles`, `/issues`의 buffered non-input layout-shift 합은 각각 0이었다. |
| `PA-SA4-T04` | PASS | 이슈 OPEN 카드와 사이클 PLANNED/IN_PROGRESS/COMPLETED 카드를 실제 클릭해 목록 필터와 전체 집계가 분리됨을 확인했다. 사이클 목록의 `platformResultCount`를 PASS처럼 표시하던 결함도 중립적인 `저장된 플랫폼 결과 2/3` progress로 수정했다. |
| `PA-SA4-T05` | PASS | 동일 cycle execution에서 WEB step 1=PASS, AOS step 1=FAIL을 저장해 통합 FAIL과 header 집계 `성공 1 / 실패 1 / 차단 0 / 미실행 1`을 확인했다. 동일 attempt에서 `ISSUE-1`, `ISSUE-2` 두 건을 생성해 각각 연결됨을 확인했다. |
| `PA-SA4-T06` | PASS (MinIO) | 깨끗한 text와 EICAR 두 파일을 한 번에 실제 file input으로 선택했다. 이슈는 한 번만 생성되고 clean 1건은 MinIO에 저장, EICAR 1건은 ClamAV에 차단됐으며 retry에는 실패 파일만 남고 성공 첨부는 중복되지 않았다. |
| `PA-SA4-T07` | PASS | `TC-000005`의 2단계 삭제 확인→deleted 포함 조회→복원, 이슈 content/attachment history, 두 browser page의 stale resourceVersion conflict를 실제 조작했다. 충돌 입력은 저장되지 않고 명시적 경고와 `최신 원장 다시 불러오기` 동작이 제공된다. |
| `PA-SA4-T08` | PASS | `/test-cases` 1280×900, `/cycles` 1440×900, `/issues` 1920×1080 baseline과 SHA-256 manifest를 만들고 `pnpm test:screenshots`로 이미지 형식·pixel 크기·checksum을 고정했다. |
| `PA-SA4-T09` | PASS | 세 baseline을 직접 검토했다. table/rail/action 높이, 긴 텍스트 wrapping, focus outline, status/filter/dropdown 정렬에서 clipping 또는 겹침이 없었다. |
| `PA-SA4-T10` | PASS | dialog 공통 hook에 initial focus, Tab/Shift+Tab wrap, Escape close, opener focus restore를 구현했다. unit test와 실제 issue dialog의 Enter→wrap→Escape→opener restore를 모두 확인했다. |
| `PA-SA4-T11` | PASS | route-level named dynamic import를 적용했다. initial JS는 552.23 kB (gzip 158.69 kB)에서 330.82 kB (gzip 103.14 kB)로 40.1%/35.0% 감소했고 cycle/issue/library 등은 별도 chunk가 됐다. |
| `PA-SA4-T12` | PASS | Web unit/type/build, screenshot verifier, monorepo check, PostgreSQL/API/browser flow에 이어 actual R2 lifecycle과 matched restore를 추가 검증했다. 2026-08-25 UI 마감 결함 재검수도 통과했고 physical 다중 파일 전체 흐름만 owner manual follow-up으로 남긴다. |

## 2. Physical pointer와 navigation 증적

### 2.1 물리 drag

동일 live PostgreSQL projection의 시작 순서는 `TC-000001, TC-000002, TC-000003`이었다.

1. 실제 Browser `drag`로 `TC-000001` handle을 같은 상위일감의 마지막 target에 놓아 `TC-000002, TC-000003, TC-000001` 순서를 확인했다.
2. `TC-000002`를 이미 case가 있는 `WORK-4102`로 옮겨 source `3→2`, target `1→2`를 확인했다.
3. `TC-000003`을 case가 없던 `WORK-4103`에 놓아 source `2→1`, target `0→1`을 확인했다.

각 단계는 DOM reducer 결과만 읽지 않고 mutation 후 서버 projection을 다시 읽은 화면에서 검증했다. 구현 경계는 [`folder-library-page.tsx`](../../apps/web/src/folder-library-page.tsx)의 pointer listener와 API-backed move/reorder mutation이다.

### 2.2 생성 route

| Entity | cancel/no-create | save/reload | back |
|---|---|---|---|
| Test case | `Unsaved browser draft` 취소 후 총 5건 유지 | `TC-000006 Browser saved test case` 생성, detail history 1건과 library 재진입 후 6건 | detail의 명시적 뒤로가기로 library 복귀 |
| Cycle | `Unsaved cycle browser draft` 취소 후 총 1건 유지 | `Phase A4 Browser Cycle` 생성·시작, hard reload 뒤 IN_PROGRESS 1건 | create 취소와 detail back 모두 `/cycles` 복귀 |
| Issue | `Unsaved issue browser draft` 취소 후 총 3건 유지 | `ISSUE-1`~`ISSUE-3` 생성, detail/history reload 유지 | create 취소와 detail back 모두 `/issues` 복귀 |

정책 구현/회귀 근거는 [`navigation-policy.ts`](../../apps/web/src/navigation-policy.ts), [`navigation-policy.test.ts`](../../apps/web/src/navigation-policy.test.ts)와 실제 browser history 흐름이다. create route는 저장 성공 전 entity mutation을 발생시키지 않는다.

## 3. 실행·집계·첨부·충돌 증적

- Cycle status는 `Planned 0 / In Progress 1 / Completed 0 / All 1`; status card를 선택해도 전체 count는 변하지 않고 row만 필터링됐다.
- Issue status는 첫 두 이슈에서 `Open 2`, 첨부 검수 이슈 생성 뒤 `Open 3`; OPEN card 선택 시 해당 row 수와 `total`이 일치했다.
- Cycle execution에서 WEB와 AOS 결과가 독립 저장됐고 FAIL 우선 aggregate가 적용됐다. 같은 attempt에 연결한 두 이슈는 execution 화면에서 함께 보였다.
- MinIO/ClamAV actual binary 결과는 `phase-a-clean.txt` 51 bytes, `text/plain` 1건 저장과 EICAR signature 1건 거부였다. 첫 batch 결과는 `1개 저장, 1개 실패`, retry는 `0개 저장, 1개 실패`였고 상세에는 clean 객체 하나만 있었다.
- multi-upload 동작은 [`attachment-batch.ts`](../../apps/web/src/attachment-batch.ts)와 [`attachment-batch.test.ts`](../../apps/web/src/attachment-batch.test.ts)에 실패 격리 및 retry-safe list로 고정했다.
- stale issue save는 더 이상 자동 reload로 메시지를 지우지 않는다. [`issue-workspace-page.tsx`](../../apps/web/src/issue-workspace-page.tsx)가 409을 명시하고 사용자가 canonical row reload를 선택하게 한다.

## 4. Screenshot·시각·접근성

Baseline manifest는 [`screenshots/baseline.json`](screenshots/baseline.json), 검증기는 [`verify-phase-a-screenshots.mjs`](../../scripts/verify-phase-a-screenshots.mjs)다.

| Route | requested viewport | captured pixels | SHA-256 |
|---|---:|---:|---|
| `/test-cases` | 1280×900 | 1265×966 full page | `a2bd3e306ca071aeb96ded093f783a70957fcb5c05e78dc0e01d0eb4c3774f1d` |
| `/cycles` | 1440×900 | 1425×900 | `c4368ea1c623487906fa22c051b99648e6445730cc9fe6040fd6a465e7507a3e` |
| `/issues` | 1920×1080 | 1905×1080 | `b293259d2a12061291d2ca181a8ccf672eabef5e96bec74d8b042ef5ec3dcd1d` |

15px 차이는 desktop scrollbar가 차지한 CSS viewport 폭이며 manifest는 실제 JPEG pixel을 고정한다. full-page library 높이는 content가 900px을 넘어 966px이다.

각 baseline route가 data load를 마친 뒤 `performance.getEntriesByType("layout-shift")`에서 `hadRecentInput=false`인 entry를 합산했고 `/test-cases`, `/cycles`, `/issues` 모두 CLS 0이었다. 동일 route 전환에서 이전 detail/list entity가 다음 route에 남지 않았으며 fallback은 최소 높이를 유지했다.

[`dialog-accessibility.ts`](../../apps/web/src/dialog-accessibility.ts)를 work-item, cycle execution issue, cycle-case picker dialog에 공통 적용했다. focus wrap unit 판정과 함께 Browser에서 issue dialog opener에 focus→Enter→초기 focus→Shift+Tab/Tab wrap→Escape→동일 opener restore를 확인했다.

## 5. Bundle·자동 검증

### 5.1 Production bundle

[`app.tsx`](../../apps/web/src/app.tsx)는 주요 route를 lazy named import하고 [`app-routes-eager.tsx`](../../apps/web/src/app-routes-eager.tsx)는 deterministic unit test 전용 route tree를 제공한다. `Suspense` fallback은 [`styles.css`](../../apps/web/src/styles.css)의 stable minimum height와 reduced-motion 규칙을 사용한다.

| artifact | before | after |
|---|---:|---:|
| initial JS | 552.23 kB | 330.82 kB |
| initial gzip | 158.69 kB | 103.14 kB |
| cycle chunk | initial 포함 | 60.54 kB |
| issue chunk | initial 포함 | 42.76 kB |
| library chunk | initial 포함 | 22.04 kB |

최종 Vite build는 500 kB warning 없이 통과했다.

### 5.2 실행 명령과 결과

| 명령 | 결과 |
|---|---|
| `pnpm --filter @tc/web typecheck` | PASS |
| `pnpm --filter @tc/web test:unit` | PASS — 22 files / 88 tests, live-only 1 skipped |
| `pnpm --filter @tc/web build` | PASS — route chunks 생성, initial 330.82 kB |
| `pnpm test:screenshots` | PASS — 3 JPEG dimensions/checksums verified |
| `pnpm check` | PASS after canonical upload-token signature fix; lint, TS/Python typecheck, JS/Python unit, schemas, ADR, OpenAPI |
| focused PostgreSQL/API suites | PASS — 9/9; A1 reload, case reorder/move, cycle lifecycle, issue/conflict/history, object-storage lifecycle |

`pnpm check`에서 마지막 base64url 문자의 unused bit를 바꾸면 동일 digest bytes로 decode될 수 있어 upload token tamper 테스트가 간헐적으로 실패하는 보안 경계를 발견했다. [`object_storage_runtime.py`](../../apps/api/src/tc_api/object_storage_runtime.py)는 decoded HMAC뿐 아니라 canonical unpadded base64url signature representation도 constant-time 비교하도록 수정했다.

## 6. 2026-08-25 최종 독립 physical UI 검수

live PostgreSQL/API projection을 사용하는 physical Chrome에서 다음을 재확인했다.

- `/test-cases`는 진입 오류나 load error 없이 9개 케이스와 3개 상위일감을 표시했다.
- `/cycles`의 예정(화면 표기 `미진행`)·진행 중·완료 배지는 모두 64px 폭, 좌우 8.8px padding, `border-box`로 일치했다.
- 연결된 실행 근거 행은 drag와 click 뒤 `window.getSelection()`이 빈 문자열을 유지했다. 동시에 keyboard Tab으로 focus했을 때 `:focus-visible`이 유지되고 3px terracotta outline이 나타났다.
- `실행 화면 ↗` 버튼은 흰 글자와 terracotta 배경으로 5.32:1 contrast를 보였고, 진행 상태 배지도 6.67:1이었다.
- 기존 desktop screenshot 회귀는 `/test-cases` 1280, `/cycles` 1440, `/issues` 1920 기준을 계속 고정한다. 이번 physical 재검수 viewport는 1920×850이었다.

따라서 요청된 세 UI 마감 결함은 재현되지 않았고 Test Library 진입 blocker도 없었다.

## 7. actual R2 browser 시도와 정리

2026-08-25 repository-root `.env`의 Git 제외 bucket-scoped 설정으로 actual private R2를 사용했다. credential 값과 presigned URL은 출력하거나 보고서에 기록하지 않았다. 기존 검수의 private/public-disabled, 최소 권한, unsigned access 거부, lifecycle, restore 근거는 그대로 유효하다.

사용자가 지정한 로컬 PNG 파일 2개는 각각 124,848 bytes이며 physical Chrome에서 actual R2 direct PUT과 attachment commit이 성공했다. 이 두 파일은 하나의 chooser event가 아니라 개별 선택으로 저장됐으므로 batch evidence로 계산하지 않는다. 사용자의 예외 지시에 따라 file chooser/Computer Use 재시도와 browser download·GET·HEAD 시도를 즉시 중단했다.

검수용 `ISSUE-6`의 두 attachment를 정상 attachment DELETE API로 정리했고 provider 응답은 각각 `storageDeleteState=DELETED`였다. 이어 임시 이슈를 soft-delete했으며 active attachment 조회는 0건이다. 로컬 보조 서버와 `/tmp` 검수 파일도 종료·삭제했고, 삭제 성공 응답으로 해당 임시 R2 객체가 남지 않았음을 확인했다.

**physical 다중 파일 browser 전체 흐름: `NOT RUN — owner manual follow-up`.** owner가 localhost에서 하나의 file chooser event로 두 PNG를 선택하고 각 browser download와 삭제를 확인해야 한다. 이 미실행 항목은 발견된 제품 결함으로 오인하지 않되 Phase A 완료 증적으로도 사용하지 않는다.

## 8. 범위 판정

- `PA-SA3-T02`: **PASS (현재 배포 전 운영 범위).** actual R2가 `localhost:5173`과 `127.0.0.1:5173` exact origin의 PUT/GET/HEAD preflight를 허용하고 physical Chrome direct PUT도 성공했다. 존재하지 않는 배포 HTTPS origin은 이 task의 실패가 아니라 Phase F 배포 체크리스트 `PF-SF1-T15`의 별도 gate다.
- Sprint A3 physical 다중 파일 종료 조건: **NOT RUN / OPEN.** owner manual follow-up 전에는 PASS로 바꾸지 않는다.
- Phase A: **UI·local/API·actual R2 data path GREEN, current automated gate RED, 전체 Phase는 OPEN.** `attachment-preview.tsx` lint warning을 해소하고 owner manual multi-file 확인을 마쳐야 하며 production 배포 검증이나 승인을 주장하지 않는다.
- 추후 배포: exact HTTPS origin CORS 설치, 그 origin의 browser PUT·GET·HEAD 및 다중 파일 upload→download→delete, durable R2 analytics 대조는 Phase F에서 검증한다.

## 9. Phase A 자동 release gate

**판정: 2026-08-24 자동 행렬과 actual R2 data path는 GREEN이었으나 2026-08-25 current worktree rerun은 RED다.** physical UI와 storage 결함은 발견하지 못했지만 현재 Web lint와 owner manual multi-file 항목이 모두 열린 상태다.

### 2026-08-25 current worktree rerun

| 명령 | 결과 |
|---|---|
| `pnpm check` | **FAIL** — `apps/web/src/attachment-preview.tsx`의 non-component export 3곳이 `react-refresh/only-export-components` warning을 만들었고 `--max-warnings 0`에서 lint 단계가 중단됐다. 이후 typecheck/unit/schema/ADR/OpenAPI 단계는 이 실행에서 시작되지 않았다. |

아래 행렬은 실패한 current rerun을 덮어쓰는 증거가 아니라 2026-08-24 release gate의 마지막 완주 수치다.

### 최종 검증 행렬

| 검증 | 결과 |
|---|---|
| 전체 Python unit/repository/integration/API/worker | **191 passed, 0 failed, 0 skipped**, PostgreSQL 16 + pgvector 및 live MinIO/ClamAV 포함; SQLAlchemy model 이름의 pytest collection warning 1건 |
| 전체 TypeScript package tests | **150 passed, 0 failed, 0 skipped** — API client 19, contracts 36, UI 6, Web 89; Web 89에는 opt-in live API/PostgreSQL reload E2E 1건 포함 |
| 최종 합계 | **341 passed, 0 failed, 0 skipped**, warning 1건 |
| `pnpm check` | PASS — ESLint, Ruff, TS/Python typecheck, non-PostgreSQL tests, 5 schema/12 fixtures, 17 ADR, OpenAPI drift |
| PostgreSQL migration suite | PASS — 49/49, downgrade/upgrade와 head physical guards 포함 |
| screenshot regression | PASS — 3/3 desktop JPEG dimension/SHA-256 baseline |
| production build/browser | PASS — production mode에 고의로 `VITE_ENABLE_CYCLE_MOCK_FALLBACK=true`와 unreachable API를 주어도 fixture 대신 명시적 API 오류 표시 |
| private local storage | PASS — MinIO lifecycle/EICAR/private denial 1/1 및 live API upload→SHA/MIME/scan→private download→metadata/restore/orphan→delete smoke |
| actual private R2 | PASS — privacy/scope, localhost CORS 204, direct PUT/complete/scan/proxy GET/integrity/orphan/delete, cleanup object 0 |
| matched restore drill | PASS — PostgreSQL custom dump→isolated restore DB→actual R2 full-byte sample 1/1, failure 0 |

첫 PostgreSQL suite 호출은 disposable database 이름에 필수 `test` 문자열이 없어 49개 setup이 제품 코드를 실행하기 전에 거부됐다. 이름을 `tc_release_gate_test_8823`으로 바로잡은 최종 행렬은 위와 같이 49/49 및 통합 191/191로 통과했으며, 이 harness precondition 거부는 제품 test failure로 집계하지 않았다.

### Claim 재검증

- **A1 / 실제 원장과 production no-fallback:** fresh Web adapter의 category→work item→case→cycle reload가 live API/PostgreSQL에서 통과했다. production build는 flag를 강제로 주고 API를 끊어도 `사이클 상세를 표시할 수 없습니다`를 표시했고 fixture label과 mock fallback UI는 없었다. 개발/test fixture 구현은 명시적 development gate 뒤에만 남는다.
- **A1 / 제거 계약:** live OpenAPI 85 paths에 cycle latest-diff, Reports CSV 또는 Drive operation path가 0개다. 일반 test-case version diff는 version history/restore 계약으로 의도적으로 유지된다. current source/OpenAPI/contracts의 `GOOGLE_DRIVE`는 typed legacy-row inventory/read-block 경계에만 남는다.
- **A2 / WIP와 aggregate:** production Web bundle, current Web/API/model/generated client/current contracts에서 `WIP` token은 0개다. `WIP`은 historical migrations와 lossless upgrade/downgrade test에만 남는다. server, Web와 schema는 BLOCKED/UNEXECUTED를 verdict 분모에서 제외하고 FAIL 우선 규칙을 공유하며 repository report/cycle parity tests가 포함된 전체 PostgreSQL suite가 통과했다.
- **A3 / attachment safety:** unit/API/PostgreSQL/live MinIO 증적에 actual R2를 추가했다. private/public-disabled 및 bucket scope, direct PUT, HEAD/byte SHA·MIME, ClamAV, proxy GET, orphan/integrity/delete와 matched PostgreSQL restore sample이 통과했다.
- **A4 / browser와 visual:** 기존 physical drag/create/cancel/save/back/filter/attempt/issue/attachment/conflict Browser 증적을 code와 report에 대조했고 3개 screenshot checksum을 재검증했다. production no-fallback browser smoke를 별도 재실행했다.

### 남은 수동·배포 follow-up

현재 배포 전 Web origin의 private R2, 최소 권한, CORS 구성, lifecycle과 matched restore는 검증됐다. 남은 Phase A 항목은 current Web lint warning 3건 해소와 owner가 수행할 localhost physical browser 다중 파일 전체 흐름(`NOT RUN`)이다. 배포 HTTPS origin CORS와 그 origin의 smoke 및 durable provider analytics는 `PF-SF1-T15`와 Phase F 운영 gate로 이동한다. 현재 임시 credential은 production 사용 전에 폐기·재발급해야 하며, 이 보고서는 실제 운영 배포를 검증했다고 주장하지 않는다.
