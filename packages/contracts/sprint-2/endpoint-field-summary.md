# Sprint 2 test case contract handoff

Canonical schema IDs:

- `https://qa-tool.local/schemas/sprint-2/test-case-api.schema.json`
- `https://qa-tool.local/schemas/sprint-2/error-contract.schema.json`

The package also exports these as `@tc/contracts/schemas/sprint-2/test-case-api` and `@tc/contracts/schemas/sprint-2/error-contract`.
Register `state-enums` before the test-case schema and register the Sprint 1 error contract before the Sprint 2 error schema; both dependencies have matching package subpath exports.

## Endpoint fields

| Endpoint | Request/query definition and fields | Response definition and fields |
|---|---|---|
| `GET /api/test-cases` | `TestCaseListQuery`: `search`, `categoryIds`, `workItemIds`, `statuses`, `priorities`, `labels`, `labelMatch=ANY|ALL`, `includeDeleted=false`, `sort`, `cursor`, `limit=50` (max 200) | `TestCasePageResponse`: `items`, `nextCursor`, `total`; each list item includes ID/key, category/work-item ownership and order, status, summary/priority/owner/labels, content `version`, aggregate `resourceVersion`, source, timestamps/deletion state |
| `POST /api/test-cases` | `CreateTestCaseRequest`: `workItemId`, `status=ACTIVE`, summary/precondition, expected-result mode and script, priority/owner/labels, source; generated `CASE` mode additionally needs `caseExpectedResult`, `generationReason`, and `WORKER` generation metadata | `CreateTestCaseResponse`: full `TestCaseResource`; the default workspace begins with `TC-000001`, `ACTIVE`, content version 1 and resource version 1 |
| `GET /api/test-cases/{caseId}` | No JSON body | `GetTestCaseResponse`: ownership/status plus current immutable version and lifecycle timestamps |
| `PATCH /api/test-cases/{caseId}` | `UpdateTestCaseRequest`: `baseVersion` plus a complete new content snapshot, optional status, `changeReason`, source | `UpdateTestCaseResponse`: full resource pointing at the newly appended immutable content version; stale content version returns `409 VERSION_CONFLICT` |
| `DELETE /api/test-cases/{caseId}` | `SoftDeleteTestCaseRequest`: `baseResourceVersion`, `reason`, source | `SoftDeleteTestCaseResponse`: `operation=SOFT_DELETE`, resource with non-null `deletedAt`, `auditId`; content version is preserved |
| `POST /api/test-cases/{caseId}/restore` | `RestoreDeletedTestCaseRequest`: `baseResourceVersion`, `reason`, source; optional replacement `targetWorkItemId` + `targetSortOrder` | `RestoreDeletedTestCaseResponse`: `operation=RESTORE_DELETED`, resource with `deletedAt=null`, `auditId` |
| `POST /api/test-cases/{caseId}/move` | `MoveTestCaseRequest`: `baseResourceVersion`, `targetWorkItemId`, `targetSortOrder`, `changeReason`, source | `MoveTestCaseResponse`: updated resource, previous work-item/category/order, `auditId`; server derives category from the target work item and updates ownership/order atomically |
| `POST /api/test-cases/{caseId}/copy` | `CopyTestCaseRequest`: explicit immutable `sourceVersion`, target work item/order, optional summary override, `changeReason`, source | `CopyTestCaseResponse`: independently keyed `ACTIVE` resource at version/resourceVersion 1, explicit `copiedFrom`, `auditId` |
| `GET /api/test-cases/{caseId}/versions` | `TestCaseVersionListQuery`: `cursor`, `limit=50` (max 200) | `TestCaseVersionPageResponse`: full immutable version snapshots, `nextCursor`, `total` |
| `GET /api/test-cases/{caseId}/versions/{version}` | No JSON body | `GetTestCaseVersionResponse`: one immutable version including content, mode/script, source, reason and creation time |
| `GET /api/test-cases/{caseId}/versions/diff` | `TestCaseVersionDiffQuery`: `fromVersion`, `toVersion` | `TestCaseVersionDiffResponse`: field-level `before`/`after` plus ordered step `ADDED|REMOVED|CHANGED|MOVED` entries |
| `POST /api/test-cases/{caseId}/restore/{version}` | `RestoreTestCaseVersionRequest`: current `baseVersion`, `changeReason`, source; path version is the immutable restore source | `RestoreTestCaseVersionResponse`: full resource, `restoredFromVersion`, `auditId`; restore appends a new version and never edits history |

## Cross-endpoint invariants

- `version` guards immutable content updates and version restores. `resourceVersion` guards ownership/order and soft-delete lifecycle mutations.
- `PER_STEP` requires every step's `expectedResult` and forbids `caseExpectedResult`. Generation-worker `CASE` payloads require one `caseExpectedResult` and forbid per-step results.
- Steps are one-based and contiguous. Labels are unique and matched after trim, Unicode NFKC normalization and case folding; arrays use OR within a filter, supplied filter groups use AND, and label matching is selected by `ANY|ALL`.
- Source metadata preserves `UI|API|SYSTEM|SLACK|WORKER`, optional display actor/external actor/correlation, Slack revisions and generation evidence. No web user ID is implied.
- Copy records the source version only as `copiedFrom`; the new case owns a distinct version ID and starts its history at version 1.
- The Sprint 2 error union reuses the Sprint 1 `VERSION_CONFLICT`, validation, not-found, idempotency and key-allocation shapes, and adds strict label, result-mode, mutation and cursor errors.

## Verification

- `pnpm --filter @tc/contracts typecheck`
- `pnpm --filter @tc/contracts test:unit` — 3 files, 12 tests
- `pnpm exec eslint packages/contracts --max-warnings 0`
- `pnpm test:schema` — 5 pre-existing top-level schemas and 12 Sprint 0 synthetic fixtures; Sprint 2 schemas are recursively compiled per definition by the package unit suite
