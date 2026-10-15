from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import tc_api.main as api_main
import tc_worker.main as worker_main
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

import tc_backend.knowledge.lightrag_generation_factory as generation_factory_module
from tc_backend.config import Settings
from tc_backend.db.models import BackgroundJob
from tc_backend.knowledge.lightrag import LightRAGCallStatus
from tc_backend.knowledge.lightrag_composition import (
    build_lightrag_generation_runtime_factory,
)
from tc_backend.knowledge.lightrag_generation_factory import (
    IsolatedLightRAGRuntimeConfigAttestation,
    LightRAGGenerationRuntimeUnavailable,
    LightRAGGenerationRuntimeUnavailableReason,
)
from tc_backend.knowledge.lightrag_runtime import LightRAGRuntimeResolver
from tc_backend.repositories.lightrag_generation_repository import (
    DurableLightRAGGenerationControlPlane,
)

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "phase-f" / "lightrag_full_reindex.py"
TEST_WORKER = (
    ROOT
    / "backend"
    / "tests"
    / "unit"
    / "fixtures"
    / "lightrag_generation_test_worker.py"
)
SECRET = "cross-boundary-lightrag-secret-must-not-leak"


def _load_reindex() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase_f_lightrag_application_routing", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


reindex = _load_reindex()


class _Source:
    def __init__(self, snapshot: object) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> object:
        return self._snapshot


class _Storage:
    def __init__(
        self,
        target: object,
        plan: object,
        attestation: IsolatedLightRAGRuntimeConfigAttestation,
    ) -> None:
        self._target = target
        self._plan = plan
        self._attestation = attestation
        self._begun = False
        self._documents: list[object] = []

    def capabilities(self) -> object:
        return reindex.IsolatedGenerationStorageCapabilities(
            authority_id=self._attestation.authority_id,
            provider_id=self._attestation.provider_id,
            provider_contract_version=self._attestation.provider_contract_version,
            provider_distribution=self._attestation.provider_distribution,
            provider_version=self._attestation.provider_version,
            auxiliary_target_digest=self._target.auxiliary_target_digest,
            runtime_config_digest=self._attestation.runtime_config_digest,
            control_plane="embedded-postgresql",
            redirect_hops=0,
            fresh_generation_isolation=True,
        )

    def generation_exists(self, generation_id: str) -> bool:
        assert generation_id == self._plan.generation_id
        return self._begun

    def begin_generation(
        self,
        generation_id: str,
        *,
        workspace: str,
        manifest_digest: str,
    ) -> None:
        assert generation_id == self._plan.generation_id
        assert workspace == generation_id
        assert manifest_digest == self._plan.manifest_digest
        self._begun = True

    def ingest_batch(self, generation_id: str, documents: object) -> None:
        assert generation_id == self._plan.generation_id
        self._documents.extend(documents)  # type: ignore[arg-type]

    def inspect_generation(self, generation_id: str) -> object:
        assert generation_id == self._plan.generation_id
        identifiers = tuple(
            document.postgres_record_id for document in self._documents
        )
        return reindex.IsolatedGenerationInspection(
            generation_id=generation_id,
            workspace=generation_id,
            auxiliary_target_digest=self._target.auxiliary_target_digest,
            manifest_digest=self._plan.manifest_digest,
            document_count=len(self._documents),
            processed_count=len(self._documents),
            failed_count=0,
            corpus_digest=self._plan.snapshot.corpus_digest,
            processed_document_ids=identifiers,
        )

    def discard_generation(self, generation_id: str) -> None:
        assert generation_id == self._plan.generation_id
        self._begun = False
        self._documents.clear()


def _settings(engine: Engine, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=engine.url.render_as_string(hide_password=False),
        encryption_master_key="cross-boundary-encryption-key-000001",
        light_rag_enabled=True,
        light_rag_authority_id="tc-phase-f-knowledge-staging-copy",
        light_rag_auxiliary_target_digest="sha256:" + "0" * 64,
        light_rag_provider_id="official-lightrag-postgresql",
        light_rag_postgres_host="auxiliary.invalid",
        light_rag_postgres_user="lightrag_runtime",
        light_rag_postgres_password=SECRET,
        light_rag_postgres_database="tc_phase_f_lightrag_staging_copy",
        light_rag_postgres_ssl_mode="verify-full",
        light_rag_apache_age_available=True,
        light_rag_runtime_bootstrap="approved.runtime:build",
        light_rag_working_directory_root=(tmp_path / "runtimes").resolve(),
        light_rag_startup_timeout_seconds=4,
        light_rag_operation_timeout_seconds=4,
        light_rag_shutdown_timeout_seconds=1,
    )


def _target(active_workspace: str) -> object:
    return reindex.TargetDeclaration(
        name="tc-phase-f-knowledge-staging-copy",
        target_class="staging-copy",
        authoritative_database_name="tc_phase_f_knowledge_staging_copy",
        auxiliary_database_name="tc_phase_f_lightrag_staging_copy",
        active_workspace=active_workspace,
    )


def _snapshot(target: object) -> object:
    return reindex.CorpusSnapshot(
        source_target_digest=target.source_target_digest,
        source_ledger_digest="1" * 64,
        projection_digest="2" * 64,
        pgvector_digest="3" * 64,
        pgvector_model="bge-m3",
        pgvector_dimension=1024,
        documents=(
            reindex.CorpusDocument(
                postgres_record_id="00000000-0000-4000-8000-000000000001",
                content="credential-free cross-boundary fixture",
                source_uri=(
                    "postgresql://knowledge/nodes/"
                    "00000000-0000-4000-8000-000000000001"
                ),
            ),
        ),
    )


def _apply_generation(
    *,
    target: object,
    snapshot: object,
    attestation: IsolatedLightRAGRuntimeConfigAttestation,
    control: DurableLightRAGGenerationControlPlane,
) -> tuple[object, object]:
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    source = _Source(snapshot)
    controller = reindex.LedgerBackedGenerationController(
        plan=plan,
        source=source,
        storage=_Storage(target, plan, attestation),
        control_plane=control,
        actor_id="release-owner",
    )
    report = reindex.FullReindexWorkflow(source).apply(
        plan,
        controller,
        confirmation=plan.confirmation_required,
        expected_manifest_digest=plan.manifest_digest,
    )
    assert report["executionStatus"] == "SUCCEEDED"
    return plan, controller


def _search_workspace(resolver: LightRAGRuntimeResolver, query: str) -> str:
    result = asyncio.run(resolver.search(query))
    assert result.status is LightRAGCallStatus.OK
    return result.require_value().context


def test_controller_binding_routes_settings_built_api_and_worker_across_switch_and_rollback(
    migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    disabled = Settings(
        _env_file=None,
        database_url=migrated_engine.url.render_as_string(hide_password=False),
        encryption_master_key="cross-boundary-encryption-key-000001",
    )
    assert build_lightrag_generation_runtime_factory(disabled) is None

    settings = _settings(migrated_engine, tmp_path)
    target = _target("tc")
    settings = settings.model_copy(
        update={
            "light_rag_auxiliary_target_digest": (
                "sha256:" + target.auxiliary_target_digest
            )
        }
    )
    settings_factory = build_lightrag_generation_runtime_factory(settings)
    assert settings_factory is not None
    (attestation,) = settings_factory.configuration_attestations()
    control = DurableLightRAGGenerationControlPlane(migrated_engine)

    first_snapshot = _snapshot(target)
    first_plan, _first_controller = _apply_generation(
        target=target,
        snapshot=first_snapshot,
        attestation=attestation,
        control=control,
    )
    first_binding = control.active_generation()
    settings_factory.validate_binding(first_binding)

    exact_failures: dict[str, str] = {}
    for label, binding in (
        ("provider", replace(first_binding, provider_id="wrong-provider")),
        ("version", replace(first_binding, provider_version="1.5.5")),
        (
            "digest",
            replace(first_binding, runtime_config_digest="sha256:" + "f" * 64),
        ),
    ):
        try:
            settings_factory.validate_binding(binding)
        except LightRAGGenerationRuntimeUnavailable as error:
            exact_failures[label] = "".join(
                traceback.format_exception_only(type(error), error)
            )
        else:  # pragma: no cover - exact matching must fail closed
            pytest.fail(f"wrong {label} binding was accepted")
    assert "provider_unavailable" in exact_failures["provider"]
    assert "version_mismatch" in exact_failures["version"]
    assert "config_digest_mismatch" in exact_failures["digest"]

    monkeypatch.setattr(
        generation_factory_module,
        "_default_worker_command",
        lambda: (sys.executable, "-u", str(TEST_WORKER)),
    )
    monkeypatch.setattr(api_main, "load_settings", lambda: settings)

    application = api_main.create_app(
        validate_database=True,
        database_engine=migrated_engine,
    )
    worker, worker_engine = worker_main.build_worker(settings)
    try:
        assert worker.light_rag_runtime is not None
        assert {
            worker_main.KNOWLEDGE_PROJECTION_JOB_TYPE,
            worker_main.KNOWLEDGE_REINDEX_JOB_TYPE,
        } <= worker.handlers.keys()
        with TestClient(application) as client:
            api_resolver = application.state.knowledge_light_rag
            assert isinstance(api_resolver, LightRAGRuntimeResolver)
            readiness = client.get("/api/ready")
            assert readiness.status_code == 200
            assert readiness.json()["dependencies"]["lightrag"] == "ready"

            assert _search_workspace(api_resolver, "api-first") == (
                f"{first_plan.generation_id}:api-first"
            )
            assert _search_workspace(worker.light_rag_runtime, "worker-first") == (
                f"{first_plan.generation_id}:worker-first"
            )

            projection = client.post(
                "/api/knowledge/jobs/projection",
                json={"idempotencyKey": "cross-boundary-projection", "limit": 1},
            )
            reindex_job = client.post(
                "/api/knowledge/jobs/reindex",
                json={"idempotencyKey": "cross-boundary-reindex", "nodeIds": []},
            )
            assert projection.status_code == 202, projection.text
            assert reindex_job.status_code == 202, reindex_job.text
            assert worker.run_once() is True
            assert worker.run_once() is True
            assert worker.run_once() is False
            with Session(migrated_engine) as session:
                jobs = session.scalars(
                    select(BackgroundJob).where(
                        BackgroundJob.idempotency_key.in_(
                            ("cross-boundary-projection", "cross-boundary-reindex")
                        )
                    )
                ).all()
            assert {job.job_type for job in jobs} == {
                worker_main.KNOWLEDGE_PROJECTION_JOB_TYPE,
                worker_main.KNOWLEDGE_REINDEX_JOB_TYPE,
            }
            assert {job.status for job in jobs} == {"SUCCEEDED"}

            second_target = _target(first_plan.generation_id)
            second_snapshot = _snapshot(second_target)
            second_plan, second_controller = _apply_generation(
                target=second_target,
                snapshot=second_snapshot,
                attestation=attestation,
                control=control,
            )
            assert second_plan.generation_id != first_plan.generation_id
            assert _search_workspace(api_resolver, "api-second") == (
                f"{second_plan.generation_id}:api-second"
            )
            assert _search_workspace(worker.light_rag_runtime, "worker-second") == (
                f"{second_plan.generation_id}:worker-second"
            )

            second_controller.rollback_generation(
                expected_current=second_plan.generation_id,
                restore_generation=first_plan.generation_id,
            )
            rolled_back = control.active_generation()
            assert rolled_back.generation_id == first_plan.generation_id
            assert rolled_back.pointer_version == 3
            settings_factory.validate_binding(rolled_back)
            assert _search_workspace(api_resolver, "api-rollback") == (
                f"{first_plan.generation_id}:api-rollback"
            )
            assert _search_workspace(worker.light_rag_runtime, "worker-rollback") == (
                f"{first_plan.generation_id}:worker-rollback"
            )

            public_evidence: dict[str, Any] = {
                "readiness": readiness.json(),
                "jobs": [
                    {
                        "type": job.job_type,
                        "status": job.status,
                        "error": job.last_error,
                    }
                    for job in jobs
                ],
                "exactFailures": exact_failures,
                "binding": repr(rolled_back),
                "factory": repr(settings_factory),
                "settings": repr(settings),
            }
            assert SECRET not in json.dumps(public_evidence, sort_keys=True)
    finally:
        worker.mark_stopped()
        worker_engine.dispose()

    runtime_root = settings.light_rag_working_directory_root
    assert runtime_root is not None
    assert not runtime_root.exists() or not any(runtime_root.iterdir())
    assert (
        LightRAGGenerationRuntimeUnavailableReason.CONFIG_DIGEST_MISMATCH.value
        in exact_failures["digest"]
    )
