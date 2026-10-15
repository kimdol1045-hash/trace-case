from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import tc_api.main as api_main
from tc_api.main import API_VERSION, create_app
from tc_backend.config import Settings
from tc_backend.knowledge.lightrag import (
    LightRAGAuxiliaryIndex,
    LightRAGCallResult,
    LightRAGCallStatus,
    LightRAGSearchCandidate,
)
from tc_backend.knowledge.lightrag_composition import (
    build_lightrag_generation_runtime_factory,
)
from tc_backend.knowledge.lightrag_runtime import (
    LightRAGGenerationRuntime,
    LightRAGRuntimeResolver,
)
from tc_backend.observability import MetricsRegistry
from tc_backend.repositories.lightrag_generation_repository import (
    ActiveGenerationBinding,
)

ROOT = Path(__file__).resolve().parents[3]


def _light_rag_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://tc:tc@localhost:5432/tc_test",
        "encryption_master_key": "api-test-encryption-key-00000000001",
        "light_rag_enabled": True,
        "light_rag_authority_id": "auxiliary-test",
        "light_rag_auxiliary_target_digest": "sha256:" + "f" * 64,
        "light_rag_provider_id": "official-lightrag-postgresql",
        "light_rag_workspace": "tc",
        "light_rag_postgres_host": "auxiliary.invalid",
        "light_rag_postgres_user": "lightrag_api",
        "light_rag_postgres_password": "api-lightrag-secret",
        "light_rag_postgres_database": "lightrag_api",
        "light_rag_postgres_ssl_mode": "verify-full",
        "light_rag_apache_age_available": True,
        "light_rag_runtime_bootstrap": "approved.runtime:build",
        "light_rag_working_directory_root": (tmp_path / "lightrag").resolve(),
    }
    return Settings(_env_file=None, **(values | overrides))


def _binding(
    *, auxiliary_target_digest: str = "sha256:" + "a" * 64
) -> ActiveGenerationBinding:
    digest = "sha256:" + "b" * 64
    return ActiveGenerationBinding(
        generation_id="generation-api",
        pointer_version=1,
        pointer_updated_at=datetime(2026, 9, 2, tzinfo=UTC),
        authority_id="auxiliary-test",
        provider_id="official-lightrag-postgresql",
        provider_contract_version="tc-lightrag-generation-provider/v1",
        provider_distribution="lightrag-hku",
        provider_version="1.5.6",
        workspace="tc",
        auxiliary_target_digest=auxiliary_target_digest,
        runtime_config_digest="sha256:" + "c" * 64,
        manifest_digest=digest,
        corpus_digest=digest,
        source_ledger_digest=digest,
        projection_digest=digest,
        pgvector_digest=digest,
        document_count=1,
        registration_digest=digest,
    )


def test_health_endpoint() -> None:
    with TestClient(create_app(validate_database=False)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "api",
        "status": "ok",
        "version": API_VERSION,
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["permissions-policy"].startswith("camera=()")
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_readiness_distinguishes_database_failure_from_optional_disablement() -> None:
    with TestClient(create_app(validate_database=False)) as client:
        response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "service": "api",
        "status": "not_ready",
        "version": API_VERSION,
        "dependencies": {
            "database": "unavailable",
            "generation": "disabled",
            "knowledge": "disabled",
            "lightrag": "disabled",
            "object_storage": "disabled",
            "slack": "disabled",
        },
        "degradationReasons": ["DATABASE_UNAVAILABLE"],
    }


def test_readiness_is_ready_with_a_live_database_and_disabled_optional_dependencies() -> (
    None
):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with TestClient(
            create_app(validate_database=False, database_engine=engine)
        ) as client:
            response = client.get("/api/ready")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["degradationReasons"] == []


def test_api_default_settings_leave_lightrag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
        encryption_master_key="api-test-encryption-key-00000000001",
    )
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(api_main, "load_settings", lambda: settings)
    monkeypatch.setattr(api_main, "validate_startup", lambda _settings: None)
    try:
        application = create_app(validate_database=True, database_engine=engine)
        with TestClient(application) as client:
            response = client.get("/api/ready")
            assert application.state.knowledge_light_rag is None
    finally:
        engine.dispose()

    assert response.json()["dependencies"]["lightrag"] == "disabled"


def test_api_builds_enabled_lightrag_factory_from_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _light_rag_settings(tmp_path)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(api_main, "load_settings", lambda: settings)
    monkeypatch.setattr(api_main, "validate_startup", lambda _settings: None)
    try:
        application = create_app(validate_database=True, database_engine=engine)
        with TestClient(application) as client:
            assert isinstance(
                application.state.knowledge_light_rag, LightRAGRuntimeResolver
            )
            response = client.get("/api/ready")
    finally:
        engine.dispose()

    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"]["lightrag"] == "unavailable"
    assert response.json()["degradationReasons"] == ["LIGHTRAG_UNAVAILABLE"]


def test_api_lightrag_startup_failure_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _light_rag_settings(
        tmp_path, light_rag_provider_id="invalid/provider"
    )
    validated: list[Settings] = []
    monkeypatch.setattr(api_main, "load_settings", lambda: settings)
    monkeypatch.setattr(
        api_main, "validate_startup", lambda received: validated.append(received)
    )

    with pytest.raises(RuntimeError, match="provider_unavailable"):
        with TestClient(create_app(validate_database=True)):
            pass

    assert validated == []


def test_api_lightrag_active_generation_mismatch_returns_unavailable(
    tmp_path: Path,
) -> None:
    factory = build_lightrag_generation_runtime_factory(
        _light_rag_settings(tmp_path)
    )
    assert factory is not None
    binding = _binding(auxiliary_target_digest="sha256:" + "f" * 64)

    class Source:
        @staticmethod
        def active_generation() -> ActiveGenerationBinding:
            return binding

    resolver = LightRAGRuntimeResolver(Source(), factory)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        application = create_app(
            validate_database=False,
            database_engine=engine,
            knowledge_light_rag=resolver,
            observability_requirements={"lightrag": True},
        )
        with TestClient(application) as client:
            result = asyncio.run(
                application.state.knowledge_light_rag.search("mismatched generation")
            )
            readiness = client.get("/api/ready")
    finally:
        engine.dispose()

    assert result.status is LightRAGCallStatus.UNAVAILABLE
    assert readiness.json()["dependencies"]["lightrag"] == "unavailable"


def test_api_shutdown_finalizes_the_active_lightrag_generation() -> None:
    binding = _binding()

    class Source:
        @staticmethod
        def active_generation() -> ActiveGenerationBinding:
            return binding

    class Index:
        finalized = False

        @staticmethod
        async def search(
            _query: str, *, mode: str = "mix"
        ) -> LightRAGCallResult[LightRAGSearchCandidate]:
            del mode
            return LightRAGCallResult(
                status=LightRAGCallStatus.OK,
                value=LightRAGSearchCandidate(context="ready"),
            )

        async def finalize(self) -> LightRAGCallResult[None]:
            self.finalized = True
            return LightRAGCallResult(status=LightRAGCallStatus.OK)

    index = Index()
    resolver = LightRAGRuntimeResolver(
        Source(),
        lambda active: LightRAGGenerationRuntime(binding=active, index=index),
    )
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        application = create_app(
            validate_database=False,
            database_engine=engine,
            knowledge_light_rag=resolver,
        )
        with TestClient(application):
            result = asyncio.run(resolver.search("initialize generation"))
            assert result.status is LightRAGCallStatus.OK
            assert index.finalized is False
        assert index.finalized is True
    finally:
        engine.dispose()


def test_api_composes_only_versioned_runtime_factory_for_enabled_lightrag() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    def factory(_binding: ActiveGenerationBinding) -> LightRAGGenerationRuntime:
        raise AssertionError("startup must not resolve an unrequested generation")

    try:
        application = create_app(
            validate_database=False,
            database_engine=engine,
            knowledge_light_rag_runtime_factory=factory,
            observability_requirements={"lightrag": True},
        )
        with TestClient(application) as client:
            assert isinstance(
                application.state.knowledge_light_rag, LightRAGRuntimeResolver
            )
            response = client.get("/api/ready")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json()["dependencies"]["lightrag"] == "ready"


def test_api_rejects_static_and_versioned_lightrag_composition_together() -> None:
    def factory(_binding: ActiveGenerationBinding) -> LightRAGGenerationRuntime:
        raise AssertionError("conflicting factory must not run")

    with pytest.raises(ValueError, match="either a test LightRAG index"):
        create_app(
            validate_database=False,
            knowledge_light_rag=LightRAGAuxiliaryIndex(None),
            knowledge_light_rag_runtime_factory=factory,
        )


def test_readiness_keeps_core_ready_while_enabled_storage_is_degraded() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        with TestClient(
            create_app(
                validate_database=False,
                database_engine=engine,
                observability_requirements={"object_storage": True},
            )
        ) as client:
            response = client.get("/api/ready")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"]["object_storage"] == "unavailable"
    assert response.json()["degradationReasons"] == ["OBJECT_STORAGE_UNAVAILABLE"]


def test_metrics_surface_has_prometheus_content_and_bounded_request_labels() -> None:
    registry = MetricsRegistry()
    with TestClient(
        create_app(validate_database=False, metrics_registry=registry)
    ) as client:
        client.get("/api/health", headers={"X-Trace-Id": "trace_private_identifier"})
        response = client.get("/api/metrics")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/plain")
    assert "tc_api_up 1" in response.text
    assert "tc_database_up 0" in response.text
    assert (
        'tc_api_http_requests_total{component="system",method="GET",status_class="2xx"} 1'
        in response.text
    )
    assert "trace_private_identifier" not in response.text
    assert "/api/health" not in response.text


def test_operational_endpoints_are_excluded_from_public_openapi() -> None:
    paths = create_app(validate_database=False).openapi()["paths"]

    assert "/api/ready" not in paths
    assert "/api/metrics" not in paths


def test_openapi_uses_api_base_path_and_explicit_operation_id() -> None:
    schema = create_app(validate_database=False).openapi()

    assert schema["info"]["version"] == API_VERSION
    assert schema["paths"]["/api/health"]["get"]["operationId"] == "getHealth"
    assert schema["paths"]["/api/workspace/settings"]["patch"]["operationId"] == (
        "updateWorkspaceSettings"
    )
    assert schema["paths"]["/api/folders/tree"]["get"]["operationId"] == "getFolderTree"
    assert schema["paths"]["/api/folders/{folderId}/move"]["post"]["operationId"] == (
        "moveFolder"
    )
    assert schema["paths"]["/api/audit-logs"]["get"]["operationId"] == "listAuditLogs"
    assert schema["paths"]["/api/test-cases"]["get"]["operationId"] == "listTestCases"
    assert schema["paths"]["/api/test-cases"]["post"]["operationId"] == "createTestCase"
    assert schema["paths"]["/api/work-items"]["get"]["operationId"] == "listWorkItems"
    assert {
        parameter["name"]
        for parameter in schema["paths"]["/api/work-items"]["get"]["parameters"]
    } == {"limit", "offset"}
    assert schema["paths"]["/api/work-items"]["post"]["operationId"] == "createWorkItem"
    assert (
        schema["paths"]["/api/work-items/{workItemKey}"]["get"]["operationId"]
        == "getWorkItem"
    )
    assert (
        schema["paths"]["/api/work-items/{workItemKey}"]["patch"]["operationId"]
        == "updateWorkItem"
    )
    create_key = schema["components"]["schemas"]["CreateWorkItemRequest"]["properties"][
        "key"
    ]
    resource_key = schema["components"]["schemas"]["WorkItemResourceResponse"][
        "properties"
    ]["key"]
    detail_key = schema["paths"]["/api/work-items/{workItemKey}"]["get"]["parameters"][
        0
    ]["schema"]
    assert create_key["pattern"] == "^WORK-[0-9]+$"
    assert resource_key == {
        "maxLength": 100,
        "minLength": 1,
        "pattern": r"\S",
        "title": "Key",
        "type": "string",
    }
    assert detail_key["maxLength"] == 100
    assert detail_key["pattern"] == r"\S"
    assert schema["paths"]["/api/test-cases/{caseId}"]["patch"]["operationId"] == (
        "updateTestCase"
    )
    assert (
        schema["paths"]["/api/test-cases/{caseId}/versions/diff"]["get"]["operationId"]
        == "diffTestCaseVersions"
    )
    assert "/api/cycle-items/{cycleItemId}/versions/diff" not in schema["paths"]
    assert "CycleItemLatestVersionDiffResponse" not in schema["components"]["schemas"]
    assert schema["paths"]["/api/reports/cycles"]["get"]["operationId"] == (
        "listCycleReports"
    )
    assert "/api/reports/cycles.csv" not in schema["paths"]
    assert "/api/reports/cycles/export" not in schema["paths"]
    assert "CsvManifest" not in schema["components"]["schemas"]
    assert "CsvReportResponse" not in schema["components"]["schemas"]
    assert (
        schema["paths"]["/api/test-cases/{caseId}/restore/{version}"]["post"][
            "operationId"
        ]
        == "restoreTestCaseVersion"
    )
    assert (
        schema["paths"]["/api/test-cases/{caseId}/permanent"]["delete"]["operationId"]
        == "permanentlyDeleteTestCase"
    )
    assert (
        schema["paths"]["/api/cycles/{cycleId}/permanent"]["delete"]["operationId"]
        == "permanentlyDeleteCycle"
    )
    assert (
        schema["paths"]["/api/issues/{issueId}/restore"]["post"]["operationId"]
        == "restoreIssue"
    )
    assert (
        schema["paths"]["/api/issues/{issueId}/permanent"]["delete"]["operationId"]
        == "permanentlyDeleteIssue"
    )
    assert {
        parameter["name"]
        for parameter in schema["paths"]["/api/issues"]["get"]["parameters"]
    } >= {
        "includeDeleted",
        "deletedOnly",
    }
    for child_path in (
        "/api/issues/{issueId}/links",
        "/api/issues/{issueId}/comments",
        "/api/issues/{issueId}/history",
        "/api/cycles/{cycleId}/items",
    ):
        assert "includeDeleted" in {
            parameter["name"]
            for parameter in schema["paths"][child_path]["get"]["parameters"]
        }

    create_fields = schema["components"]["schemas"]["CreateTestCaseRequest"]
    assert set(create_fields["required"]) == {
        "workItemId",
        "status",
        "summary",
        "precondition",
        "expectedResultMode",
        "priority",
        "labels",
        "steps",
        "source",
    }
    resource_fields = schema["components"]["schemas"]["TestCaseResourceResponse"]
    assert set(resource_fields["required"]) >= {
        "id",
        "key",
        "workItemId",
        "categoryId",
        "resourceVersion",
        "version",
        "currentVersion",
        "deletedAt",
    }


def test_removed_reports_csv_endpoints_are_not_routable() -> None:
    with TestClient(create_app(validate_database=False)) as client:
        assert client.get("/api/reports/cycles.csv").status_code == 404
        assert client.get("/api/reports/cycles/export").status_code == 404


def test_sprint2_openapi_payload_fields_match_public_contract() -> None:
    public = json.loads(
        (ROOT / "packages/contracts/sprint-2/test-case-api.schema.json").read_text()
    )["$defs"]
    actual = create_app(validate_database=False).openapi()["components"]["schemas"]
    pairs = {
        "CreateWorkItemRequest": "CreateWorkItemRequest",
        "UpdateWorkItemRequest": "UpdateWorkItemRequest",
        "WorkItemResource": "WorkItemResourceResponse",
        "WorkItemPageResponse": "WorkItemPageResponse",
        "WorkItemDetailResponse": "WorkItemDetailResponse",
        "CreateTestCaseRequest": "CreateTestCaseRequest",
        "UpdateTestCaseRequest": "UpdateTestCaseRequest",
        "SourceMetadata": "SourceMetadata",
        "TestCaseVersion": "TestCaseVersionResponse",
        "TestCaseResource": "TestCaseResourceResponse",
        "TestCaseListItem": "TestCaseListItemResponse",
        "TestCasePageResponse": "TestCasePageResponse",
        "TestCaseVersionPageResponse": "TestCaseVersionPageResponse",
        "TestCaseVersionDiffResponse": "TestCaseVersionDiffResponse",
        "RestoreTestCaseVersionRequest": "RestoreTestCaseVersionRequest",
        "RestoreTestCaseVersionResponse": "RestoreTestCaseVersionResponse",
        "MoveTestCaseRequest": "MoveTestCaseRequest",
        "MoveTestCaseResponse": "MoveTestCaseResponse",
        "CopyTestCaseRequest": "CopyTestCaseRequest",
        "CopyTestCaseResponse": "CopyTestCaseResponse",
        "SoftDeleteTestCaseRequest": "SoftDeleteTestCaseRequest",
        "SoftDeleteTestCaseResponse": "SoftDeleteTestCaseResponse",
        "RestoreDeletedTestCaseRequest": "RestoreDeletedTestCaseRequest",
        "RestoreDeletedTestCaseResponse": "RestoreDeletedTestCaseResponse",
        "PermanentDeleteTestCaseRequest": "PermanentDeleteTestCaseRequest",
        "PermanentDeleteTestCaseResponse": "PermanentDeleteTestCaseResponse",
    }
    for public_name, actual_name in pairs.items():
        assert set(actual[actual_name].get("properties", {})) == set(
            public[public_name].get("properties", {})
        )
        assert set(actual[actual_name].get("required", [])) == set(
            public[public_name].get("required", [])
        )


def test_sprint3_openapi_exposes_cycle_ledger_operations() -> None:
    schema = create_app(validate_database=False).openapi()
    operations = {
        "/api/cycles": {"get": "listCycles", "post": "createCycle"},
        "/api/cycles/{cycleId}/start": {"post": "startCycle"},
        "/api/cycles/{cycleId}/complete": {"post": "completeCycle"},
        "/api/cycle-items/{cycleItemId}/snapshot/refresh": {
            "post": "refreshCycleItemSnapshot"
        },
        "/api/cycle-items/{cycleItemId}/attempts/retry": {
            "post": "retryExecutionAttempt"
        },
        "/api/execution-attempts/{attemptId}/result": {
            "post": "recordExecutionAttemptResult"
        },
        "/api/cycles/{cycleId}/progress": {"get": "getCycleProgress"},
    }
    for path, methods in operations.items():
        for method, operation_id in methods.items():
            assert schema["paths"][path][method]["operationId"] == operation_id

    components = schema["components"]["schemas"]
    assert set(components["CycleResourceResponse"]["required"]) >= {
        "id",
        "status",
        "scope",
        "scopeLocked",
        "resourceVersion",
        "startedAt",
        "completedAt",
    }
    assert components["StartExecutionAttemptRequest"]["properties"]["platform"][
        "enum"
    ] == [
        "WEB",
        "AOS",
        "IOS",
    ]


def test_sprint3_openapi_payload_fields_match_public_contract() -> None:
    public = json.loads(
        (
            ROOT / "packages/contracts/sprint-3/cycle-execution-api.schema.json"
        ).read_text()
    )["$defs"]
    actual = create_app(validate_database=False).openapi()["components"]["schemas"]
    pairs = {
        "CycleResource": "CycleResourceResponse",
        "CreateCycleRequest": "CreateCycleRequest",
        "UpdateCycleRequest": "UpdateCycleRequest",
        "DeleteCycleRequest": "DeleteCycleRequest",
        "DeleteCycleResponse": "DeleteCycleResponse",
        "RestoreCycleRequest": "RestoreCycleRequest",
        "RestoreCycleResponse": "RestoreCycleResponse",
        "PermanentDeleteCycleRequest": "PermanentDeleteCycleRequest",
        "PermanentDeleteCycleResponse": "PermanentDeleteCycleResponse",
        "CycleListItem": "CycleListItemResponse",
        "CyclePageResponse": "CyclePageResponse",
        "ScopeOptionPageResponse": "ScopeOptionPageResponse",
        "ScopePreviewItem": "ScopePreviewItemResponse",
        "ScopePreviewResponse": "ScopePreviewResponse",
        "UpdateCycleScopeRequest": "UpdateCycleScopeRequest",
        "ExecutionAttemptResource": "ExecutionAttemptResponse",
        "CycleItemResource": "CycleItemResponse",
        "PinnedCycleItemPageResponse": "CycleItemPageResponse",
        "StartCycleRequest": "StartCycleRequest",
        "StartCycleResponse": "StartCycleResponse",
        "CompleteCycleRequest": "CompleteCycleRequest",
        "CompleteCycleResponse": "CompleteCycleResponse",
        "ReopenCycleRequest": "ReopenCycleRequest",
        "ReopenCycleResponse": "ReopenCycleResponse",
        "RefreshCycleItemSnapshotRequest": "RefreshCycleItemSnapshotRequest",
        "RefreshCycleItemSnapshotResponse": "RefreshCycleItemSnapshotResponse",
        "StartExecutionAttemptRequest": "StartExecutionAttemptRequest",
        "CompleteExecutionAttemptRequest": "CompleteExecutionAttemptRequest",
        "RetryExecutionAttemptRequest": "RetryExecutionAttemptRequest",
        "RetryExecutionAttemptResponse": "RetryExecutionAttemptResponse",
        "ExecutionAttemptHistoryPageResponse": "ExecutionAttemptHistoryPageResponse",
        "CycleProgressResponse": "CycleProgressResponse",
    }
    for public_name, actual_name in pairs.items():
        assert set(actual[actual_name].get("properties", {})) == set(
            public[public_name].get("properties", {})
        )
        assert set(actual[actual_name].get("required", [])) == set(
            public[public_name].get("required", [])
        )


def test_issue_lifecycle_openapi_payload_fields_match_public_contract() -> None:
    public = json.loads(
        (ROOT / "packages/contracts/sprint-4/issue-api.schema.json").read_text()
    )["$defs"]
    actual = create_app(validate_database=False).openapi()["components"]["schemas"]
    pairs = {
        "IssueResource": "IssueResource",
        "IssueListItem": "IssueListItem",
        "DeleteIssueRequest": "DeleteIssueRequest",
        "DeleteIssueResponse": "DeleteIssueResponse",
        "RestoreIssueRequest": "RestoreIssueRequest",
        "RestoreIssueResponse": "RestoreIssueResponse",
        "PermanentDeleteIssueRequest": "PermanentDeleteIssueRequest",
        "PermanentDeleteIssueResponse": "PermanentDeleteIssueResponse",
    }
    for public_name, actual_name in pairs.items():
        assert set(actual[actual_name].get("properties", {})) == set(
            public[public_name].get("properties", {})
        )
        assert set(actual[actual_name].get("required", [])) == set(
            public[public_name].get("required", [])
        )
