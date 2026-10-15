# Phase D knowledge retrieval 평가 계약

## 목적과 안전 경계

이 평가기는 두 입력 모드를 지원한다.

- 기본 `synthetic` 모드는 기존 Sprint 6 fixture와 `DeterministicRetriever`를 실행한다. 기존 `pnpm knowledge:retrieval:eval` 동작은 candidate gate로 유지된다.
- 명시적 `result-snapshot` 모드는 다른 절차에서 캡처한 검색 결과 JSON을 오프라인으로 재생한다. 평가기는 서비스 URL, 토큰, 환경 변수 또는 secret을 받지 않으며 네트워크, 검색 서비스, embedding 모델을 호출하지 않는다.

운영 모드라는 이름은 입력 데이터가 non-synthetic이라는 뜻이지, 이 명령이 운영 시스템을 호출했다는 뜻이 아니다. Snapshot 생성과 비식별화, 접근 통제, label review는 평가기 바깥의 승인된 절차에서 수행해야 한다.

## 평가 항목

normal 결과와 `LIGHTRAG_UNAVAILABLE` degraded 결과를 함께 검사한다.

- lifecycle exclusion: query status scope 밖의 `AMBIGUOUS`, `SUPERSEDED`, `RETRACTED` 결과를 센다.
- relevance: reviewed label의 node ID뿐 아니라 node type도 일치해야 한다. `FAILED_EXECUTION` label은 `nodeType=EXECUTION`과 `executionOutcome=FAILED`를 함께 요구한다.
- evidence/citation: ACTIVE 결과의 evidence 존재 여부와 result source revision/citation source revision의 동일성을 검사한다.
- result shape: query 응답 안의 duplicate node, 반환 순서와 rank 불일치, 빈 reason summary/signals를 검사한다.
- degradation: baseline은 non-degraded여야 하고 degraded 결과에는 `LIGHTRAG_UNAVAILABLE`가 있어야 한다. Item degraded flag, LightRAG signal 제거, relevant result retention도 검사한다.

`recall@k`는 query별 reviewed relevant label recall의 macro average다. Category recall은 `fallbackK` 안에서 `EXISTING_TEST_CASE`, `FAILED_EXECUTION`, `ISSUE`별로 계산된다.

## Threshold profile과 certification

[manifest.json](../../evals/fixtures/knowledge-retrieval/manifest.json)은 두 profile을 고정한다.

| Profile | 용도 | Release certification |
| --- | --- | --- |
| `candidate` | fixture 회귀와 캡처 품질의 조기 확인 | 발급하지 않음 |
| `release` | reviewed operational snapshot의 release gate | 모든 quality/eligibility check 통과 시에만 발급 |

Release profile은 fail-closed다. 다음 중 하나라도 해당하면 `gate.passed=false`, `certification.releaseCertified=false`, 종료 코드 `1`이다.

- `synthetic=true`
- dataset review status가 `REVIEWED`가 아니거나 `reviewedBy`, `reviewedAt`, `labelSetId`가 없음
- runtime의 `serviceVersion`/`revision`이 없음
- model의 `embeddingModel`/`rerankerModel`이 없음 (`rerankerModel`을 사용하지 않았다면 `not-used`처럼 명시)
- search의 `implementation`/`indexRevision`/`configurationRevision`이 없음
- existing test case, failed execution, issue label category가 없거나 category recall threshold 미달

따라서 synthetic pass나 unreviewed snapshot을 T05/T06/T10의 live 완료 근거로 사용하면 안 된다. 실제 완료 주장은 승인된 non-synthetic label set, 캡처 provenance와 별도 운영 증거가 모두 있을 때만 가능하다.

## Synthetic fixture

Fixture는 [retrieval-eval.schema.json](../../evals/fixtures/knowledge-retrieval/retrieval-eval.schema.json), [manifest.json](../../evals/fixtures/knowledge-retrieval/manifest.json), [golden-v1.json](../../evals/fixtures/knowledge-retrieval/golden-v1.json)에 있다. 모든 데이터는 합성·익명화 데이터이며 실제 사용자 CSV 원문, 사람 이름, Jira URL을 포함하지 않는다. Citation URI도 `null`이다.

기존 명령은 candidate profile로 같은 synthetic metric과 종료 코드 계약을 유지한다.

```bash
pnpm knowledge:retrieval:eval
pnpm --silent knowledge:retrieval:eval --json
pnpm --silent knowledge:retrieval:eval --manifest path/to/manifest.json --json
```

현재 golden 기대값은 `recall@1=0.625`, `recall@3=1.0`이다. Synthetic mode의 `fixtureExpectedRecall@k` check가 deterministic 결과의 정확한 회귀도 확인한다.

## Operational result snapshot

Snapshot은 `retrieval-eval.schema.json`의 `RESULT_SNAPSHOT` 계약을 따른다. 핵심 구조는 다음과 같다.

```json
{
  "fixtureKind": "RESULT_SNAPSHOT",
  "schemaVersion": "1.0.0",
  "synthetic": false,
  "dataset": {
    "datasetId": "reviewed_labels_v1",
    "description": "Approved, minimized retrieval labels",
    "kValues": [1, 3],
    "fallbackK": 3,
    "review": {
      "status": "REVIEWED",
      "reviewedBy": "retrieval-quality-reviewers",
      "reviewedAt": "2026-08-26T10:00:00Z",
      "labelSetId": "labels_v1"
    },
    "queries": [
      {
        "queryId": "query_case_example",
        "text": "approved minimized query text",
        "statuses": ["ACTIVE"],
        "relevanceCategory": "EXISTING_TEST_CASE",
        "relevantNodes": [{"nodeId": "case_42", "nodeType": "TEST_CASE"}]
      }
    ]
  },
  "provenance": {
    "capturedAt": "2026-08-26T10:05:00Z",
    "environment": "release-candidate",
    "runtime": {"serviceVersion": "2026.08.26", "revision": "reviewed-revision"},
    "model": {"embeddingModel": "reviewed-model-version", "rerankerModel": "not-used"},
    "search": {
      "implementation": "hybrid-search-v2",
      "indexRevision": "reviewed-index-revision",
      "configurationRevision": "reviewed-search-config"
    }
  },
  "results": [
    {
      "queryId": "query_case_example",
      "baseline": {"degraded": false, "degradationReasons": [], "items": []},
      "degraded": {
        "degraded": true,
        "degradationReasons": ["LIGHTRAG_UNAVAILABLE"],
        "items": []
      }
    }
  ]
}
```

각 result item은 schema에 정의된 `nodeId`, `nodeType`, optional `executionOutcome`, `status`, `evidenceIds`, immutable `source`, `citation`, `rank`, `score`, `reason`, `degraded`를 포함한다. 예시는 구조 설명용이라 release threshold를 통과하는 완성된 label/result set이 아니다.

Operational 평가는 machine JSON과 Markdown의 명시적 출력 경로를 모두 요구한다.

```bash
pnpm --silent knowledge:retrieval:eval \
  --snapshot path/to/reviewed-result-snapshot.json \
  --profile release \
  --json-output artifacts/knowledge-retrieval-eval.json \
  --markdown-output artifacts/knowledge-retrieval-eval.md
```

표준 출력에는 짧은 요약이 남고 두 파일은 같은 평가 결과를 표현한다. 필요하면 `--json`을 추가해 stdout에도 machine JSON을 출력할 수 있다. JSON은 다음 audit 정보를 기록한다.

- canonical dataset JSON의 `sha256` fingerprint
- 캡처된 results array의 별도 `sha256` fingerprint
- dataset review metadata
- 캡처 runtime/model/search provenance와 evaluator version
- threshold profile, 모든 metric/check, query별 baseline/degraded 결과
- release certification status

입력 snapshot을 변경하면 해당 영역의 fingerprint가 바뀐다. Dataset label과 result를 분리해 fingerprint하므로 label drift와 result drift를 구분할 수 있다.

## 종료 코드와 검증

- `0`: 선택한 profile의 gate 통과
- `1`: 유효한 입력을 평가했지만 quality 또는 release eligibility gate 실패
- `2`: fixture/snapshot 형식, profile, 경로 또는 파일 I/O 오류

집중 검증 명령:

```bash
node scripts/python.mjs -m ruff check scripts/evaluate_knowledge_retrieval.py scripts/tests/test_evaluate_knowledge_retrieval.py
node scripts/python.mjs -m mypy scripts/evaluate_knowledge_retrieval.py
node scripts/python.mjs -m pytest scripts/tests/test_evaluate_knowledge_retrieval.py
pnpm knowledge:retrieval:eval
pnpm --silent knowledge:retrieval:eval --json
```

테스트는 legacy golden metric, candidate/release profile, synthetic release rejection, reviewed snapshot certification, missing review/provenance fail-closed, relevance label type/outcome, lifecycle/citation/evidence/duplicate/reason/degraded defects, fingerprint 분리, explicit report outputs와 JSON Schema 적합성을 확인한다.
