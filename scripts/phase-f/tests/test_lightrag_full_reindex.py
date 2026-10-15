from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from tc_backend.config import Settings
from tc_backend.knowledge.lightrag import OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID
from tc_backend.knowledge.lightrag_composition import (
    build_lightrag_generation_runtime_factory,
)
from tc_backend.knowledge.lightrag_generation_factory import (
    LightRAGGenerationRuntimeUnavailable,
    LightRAGGenerationRuntimeUnavailableReason,
)
from tc_backend.repositories.lightrag_generation_repository import (
    ActiveGenerationBinding,
)

SCRIPT = Path(__file__).resolve().parents[1] / "lightrag_full_reindex.py"
CONTRACT = (
    SCRIPT.parents[2] / "deploy" / "knowledge" / "lightrag-full-reindex-contract.json"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase_f_lightrag_full_reindex", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


reindex = _load()


def test_deploy_contract_matches_executable_safety_constants() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert contract["schemaVersion"] == reindex.PROVIDER_CONTRACT_VERSION
    assert contract["task"] == reindex.TASK_ID
    assert reindex.HEAD_REVISION == "20260902_0030"
    assert (
        f"Alembic head {reindex.HEAD_REVISION}"
        in contract["sourceSnapshot"]["requiredAttestations"]
    )
    assert contract["authority"]["authoritativeMutation"] == "FORBIDDEN"
    assert contract["export"] == {
        "maxDocuments": reindex.DEFAULT_MAX_DOCUMENTS,
        "maxTotalBytes": reindex.DEFAULT_MAX_TOTAL_BYTES,
        "maxBatchDocuments": reindex.DEFAULT_BATCH_DOCUMENTS,
        "maxBatchBytes": reindex.DEFAULT_BATCH_BYTES,
        "oversizeBehavior": "FAIL_CLOSED",
    }
    assert contract["currentAdapter"]["liveApplySupported"] is False
    assert contract["currentAdapter"]["durableActiveGenerationPointer"] is True
    assert contract["controller"]["credentialInputs"] == []
    assert contract["controller"]["manualEnvironmentSwitch"] == "FORBIDDEN"
    assert contract["runtimeRouting"]["credentialInputs"] == []
    assert contract["runtimeRouting"]["manualEnvironmentSwitch"] == "FORBIDDEN"
    assert contract["runtimeRouting"]["staleResultBehavior"].startswith("DISCARD")
    assert contract["currentAdapter"]["runtimeResolverAvailable"] is True
    assert contract["currentAdapter"]["runtimeFactoryAvailable"] is True
    assert contract["currentAdapter"]["runtimeFactoryConfigured"] is False
    assert contract["currentAdapter"]["runtimeSettingsComposition"] == (
        "API_AND_WORKER_OPT_IN"
    )
    assert contract["currentAdapter"]["runtimeDefaultEnabled"] is False
    assert contract["currentAdapter"]["generationWorkspaceFromSettings"] is False
    assert contract["currentAdapter"]["productionKnowledgeJobsClaimable"] is True
    assert contract["currentAdapter"]["productionKnowledgeJobTypes"] == [
        "KNOWLEDGE_PROJECTION",
        "KNOWLEDGE_REINDEX",
    ]
    assert contract["currentAdapter"]["approvedBootstrapAvailable"] is False
    assert contract["currentAdapter"]["stagingCredentialsConfigured"] is False
    assert contract["currentAdapter"]["applicationRuntimeComposition"] is True
    assert contract["currentAdapter"]["parentProcessEnvironmentMutation"] == "FORBIDDEN"
    assert contract["currentAdapter"]["executionStatus"] == "NOT_RUN"
    assert (
        contract["providerRequirements"]["exactProcessedStatusDocumentIdentitySet"]
        is True
    )
    assert contract["providerRequirements"]["exactProviderContractVersion"] == (
        reindex.PROVIDER_CONTRACT_VERSION
    )
    assert contract["providerRequirements"]["exactProviderDistribution"] == (
        "lightrag-hku"
    )
    assert contract["providerRequirements"]["exactProviderVersion"] == (
        reindex.LIGHTRAG_VERSION
    )
    assert contract["providerRequirements"]["exactProviderId"] is True
    assert contract["providerRequirements"]["exactProviderIdValue"] == (
        OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID
    )
    assert contract["currentAdapter"]["provider"] == (
        OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID
    )
    assert contract["providerRequirements"]["exactRuntimeConfigDigest"] is True
    assert contract["runtimeRouting"]["workspaceSource"] == (
        "ACTIVE_GENERATION_BINDING"
    )
    assert contract["localVerification"] == {
        "controllerProducedBindingToSettingsBuiltApiAndWorker": True,
        "durableGenerationSwitchAndRollback": True,
        "selfConsistentCounterfeitProviderIdRejected": True,
        "exactProviderVersionAndRuntimeDigestRejection": True,
        "productionChildRuntimeConfigDigestHandoff": True,
        "apiKnowledgeJobsClaimedByProductionWorker": True,
        "secretRedactionChecked": True,
        "disabledDefaultChecked": True,
        "tests": [
            "backend/tests/unit/test_lightrag_generation_factory.py",
            "scripts/phase-f/tests/test_lightrag_full_reindex.py",
            (
                "backend/tests/repository/"
                "test_lightrag_application_routing_postgresql.py"
            ),
        ],
        "scope": (
            "DISPOSABLE_POSTGRESQL_PROTOCOL_CHILD_AND_NETWORK_FREE_"
            "PRODUCTION_CHILD_CONTRACT"
        ),
    }
    assert set(contract["stagingEvidence"].values()) == {"NOT_RUN"}


def _target() -> object:
    return reindex.TargetDeclaration(
        name="tc-phase-f-knowledge-staging-copy",
        target_class="staging-copy",
        authoritative_database_name="tc_phase_f_knowledge_staging_copy",
        auxiliary_database_name="tc_phase_f_lightrag_staging_copy",
        active_workspace="tc",
    )


@pytest.mark.parametrize(
    ("override", "expected_label"),
    [
        ({"name": "tc-phase-f-prod01-staging-copy"}, "target name"),
        (
            {
                "authoritative_database_name": (
                    "tc_phase_f_production2_staging_copy"
                )
            },
            "authoritative database name",
        ),
        (
            {"auxiliary_database_name": "tc_phase_f_live01_staging_copy"},
            "auxiliary database name",
        ),
        ({"active_workspace": "primary2"}, "active LightRAG workspace"),
        ({"active_workspace": "main01"}, "active LightRAG workspace"),
    ],
    ids=[
        "target-prod01",
        "authoritative-production2",
        "auxiliary-live01",
        "workspace-primary2",
        "workspace-main01",
    ],
)
def test_target_declaration_rejects_numbered_production_tokens(
    override: dict[str, str],
    expected_label: str,
) -> None:
    declaration = {
        "name": "tc-phase-f-knowledge-staging-copy",
        "target_class": "staging-copy",
        "authoritative_database_name": "tc_phase_f_knowledge_staging_copy",
        "auxiliary_database_name": "tc_phase_f_lightrag_staging_copy",
        "active_workspace": "tc",
    }
    declaration.update(override)

    with pytest.raises(reindex.SafetyError, match=expected_label):
        reindex.TargetDeclaration(**declaration)


def test_target_declaration_allows_unrelated_words_with_guard_prefixes() -> None:
    target = reindex.TargetDeclaration(
        name="tc-phase-f-productivity-staging-copy",
        target_class="staging-copy",
        authoritative_database_name="tc_phase_f_mainland_staging_copy",
        auxiliary_database_name="tc_phase_f_livelihood_staging_copy",
        active_workspace="primarycare",
    )

    assert target.active_workspace == "primarycare"


def _document(number: int, content: str | None = None) -> object:
    record_id = f"00000000-0000-4000-8000-{number:012d}"
    return reindex.CorpusDocument(
        postgres_record_id=record_id,
        content=content or f"document {number}",
        source_uri=f"{reindex.CANONICAL_SOURCE_PREFIX}{record_id}",
    )


def _snapshot(
    target: object,
    *,
    source_digest: str = "a" * 64,
    projection_digest: str = "b" * 64,
    pgvector_digest: str = "c" * 64,
    documents: tuple[object, ...] | None = None,
) -> object:
    return reindex.CorpusSnapshot(
        source_target_digest=target.source_target_digest,
        source_ledger_digest=source_digest,
        projection_digest=projection_digest,
        pgvector_digest=pgvector_digest,
        pgvector_model="bge-m3",
        pgvector_dimension=1024,
        documents=documents or (_document(1), _document(2)),
    )


def test_auxiliary_authority_digest_is_stable_across_active_pointer_versions() -> None:
    original = _target()
    next_pointer = reindex.TargetDeclaration(
        name=original.name,
        target_class=original.target_class,
        authoritative_database_name=original.authoritative_database_name,
        auxiliary_database_name=original.auxiliary_database_name,
        active_workspace="tc_lrag_" + "a" * 24,
    )

    assert original.auxiliary_target_digest == next_pointer.auxiliary_target_digest
    assert (
        original.public_dict()["activeWorkspace"]
        != next_pointer.public_dict()["activeWorkspace"]
    )
    plan = reindex.ReindexPlan.create(
        target=next_pointer, snapshot=_snapshot(next_pointer)
    )
    assert len(plan.generation_id) <= 63
    assert reindex.WORKSPACE_PATTERN.fullmatch(plan.generation_id)


class Source:
    def __init__(self, *snapshots: object) -> None:
        self.snapshots = list(snapshots)
        self.calls = 0

    def snapshot(self) -> object:
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return self.snapshots[index]


class Provider:
    def __init__(
        self,
        target: object,
        *,
        control_plane: str = "embedded-postgresql",
        redirect_hops: int = 0,
    ) -> None:
        self.target = target
        self.control_plane = control_plane
        self.redirect_hops = redirect_hops
        self.documents: dict[str, list[object]] = {}
        self.current: str | None = "tc_previous_generation"
        self.begin_calls = 0
        self.discard_calls = 0
        self.rollback_calls = 0
        self.activate_failure: str | None = None
        self.rollback_fails = False
        self.inspection_count_delta = 0
        self.inspection_processed_delta = 0
        self.inspection_digest: str | None = None
        self.inspection_processed_ids: tuple[str, ...] | None = None

    def capabilities(self) -> object:
        return reindex.ProviderCapabilities(
            contract_version=reindex.PROVIDER_CONTRACT_VERSION,
            provider_id="test-generation-provider",
            auxiliary_target_digest=self.target.auxiliary_target_digest,
            control_plane=self.control_plane,
            redirect_hops=self.redirect_hops,
            fresh_generation_isolation=True,
            atomic_compare_and_swap_activation=True,
            atomic_compare_and_swap_rollback=True,
        )

    def generation_exists(self, generation_id: str) -> bool:
        return generation_id in self.documents

    def begin_generation(self, generation_id: str, manifest_digest: str) -> None:
        assert len(manifest_digest) == 64
        self.begin_calls += 1
        self.documents[generation_id] = []

    def ingest_batch(self, generation_id: str, documents: object) -> None:
        self.documents[generation_id].extend(documents)

    def inspect_generation(self, generation_id: str) -> object:
        documents = self.documents[generation_id]
        count = len(documents)
        return reindex.GenerationInspection(
            generation_id=generation_id,
            document_count=count + self.inspection_count_delta,
            processed_count=count + self.inspection_processed_delta,
            failed_count=0,
            corpus_digest=self.inspection_digest or reindex.corpus_digest(documents),
            processed_document_ids=(
                self.inspection_processed_ids
                if self.inspection_processed_ids is not None
                else tuple(document.postgres_record_id for document in documents)
            ),
        )

    def discard_generation(self, generation_id: str) -> None:
        self.discard_calls += 1
        self.documents.pop(generation_id, None)

    def current_generation(self) -> str | None:
        return self.current

    def activate_generation(
        self, generation_id: str, *, expected_previous: str | None
    ) -> None:
        assert self.current == expected_previous
        if self.activate_failure == "before":
            raise RuntimeError("injected activation failure")
        self.current = generation_id
        if self.activate_failure == "after":
            raise RuntimeError("injected ambiguous activation failure")

    def rollback_generation(
        self,
        *,
        expected_current: str,
        restore_generation: str | None,
    ) -> None:
        self.rollback_calls += 1
        assert self.current == expected_current
        if self.rollback_fails:
            raise RuntimeError("injected rollback failure")
        self.current = restore_generation


class IsolatedStorage:
    def __init__(
        self,
        target: object,
        plan: object,
        *,
        runtime_config_digest: str = "sha256:" + "9" * 64,
        provider_id: str = OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
    ) -> None:
        self.target = target
        self.plan = plan
        self.documents: dict[str, list[object]] = {}
        self.manifests: dict[str, str] = {}
        self.discard_calls = 0
        self.inspection_workspace: str | None = None
        self.inspection_target_digest: str | None = None
        self.inspection_manifest_digest: str | None = None
        self.runtime_config_digest = runtime_config_digest
        self.provider_id = provider_id

    def capabilities(self) -> object:
        return reindex.IsolatedGenerationStorageCapabilities(
            authority_id=self.target.name,
            provider_id=self.provider_id,
            provider_contract_version=reindex.PROVIDER_CONTRACT_VERSION,
            provider_distribution="lightrag-hku",
            provider_version=reindex.LIGHTRAG_VERSION,
            auxiliary_target_digest=self.target.auxiliary_target_digest,
            runtime_config_digest=self.runtime_config_digest,
            control_plane="embedded-postgresql",
            redirect_hops=0,
            fresh_generation_isolation=True,
        )

    def generation_exists(self, generation_id: str) -> bool:
        return generation_id in self.documents

    def begin_generation(
        self,
        generation_id: str,
        *,
        workspace: str,
        manifest_digest: str,
    ) -> None:
        assert workspace == generation_id
        self.documents[generation_id] = []
        self.manifests[generation_id] = manifest_digest

    def ingest_batch(self, generation_id: str, documents: object) -> None:
        self.documents[generation_id].extend(documents)

    def inspect_generation(self, generation_id: str) -> object:
        documents = self.documents[generation_id]
        return reindex.IsolatedGenerationInspection(
            generation_id=generation_id,
            workspace=self.inspection_workspace or generation_id,
            auxiliary_target_digest=(
                self.inspection_target_digest or self.target.auxiliary_target_digest
            ),
            manifest_digest=(
                self.inspection_manifest_digest or self.manifests[generation_id]
            ),
            document_count=len(documents),
            processed_count=len(documents),
            failed_count=0,
            corpus_digest=reindex.corpus_digest(documents),
            processed_document_ids=tuple(
                document.postgres_record_id for document in documents
            ),
        )

    def discard_generation(self, generation_id: str) -> None:
        self.discard_calls += 1
        self.documents.pop(generation_id, None)


class Ledger:
    def __init__(
        self,
        *,
        active_generation_id: str | None = None,
        pointer_version: int = 0,
    ) -> None:
        self.active_generation_id = active_generation_id
        self.pointer_version = pointer_version
        self.registrations: list[dict[str, object]] = []
        self.activations: list[dict[str, object]] = []
        self.rollbacks: list[dict[str, object]] = []
        self.rollback_fails = False

    def active_snapshot(self) -> object:
        return reindex.ActiveGenerationSnapshot(
            generation_id=self.active_generation_id,
            pointer_version=self.pointer_version,
            updated_at=datetime.now(UTC),
        )

    def register_generation(self, **kwargs: object) -> object:
        self.registrations.append(kwargs)
        return SimpleNamespace(**kwargs)

    def activate(
        self,
        generation_id: str,
        *,
        expected_generation_id: str | None,
        expected_pointer_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> object:
        assert (self.active_generation_id, self.pointer_version) == (
            expected_generation_id,
            expected_pointer_version,
        )
        self.activations.append(
            {
                "generationId": generation_id,
                "expectedGenerationId": expected_generation_id,
                "expectedPointerVersion": expected_pointer_version,
                "actorId": actor_id,
                "idempotencyKey": idempotency_key,
            }
        )
        self.active_generation_id = generation_id
        self.pointer_version += 1
        return self._event("ACTIVATE")

    def rollback(
        self,
        generation_id: str,
        *,
        expected_generation_id: str,
        expected_pointer_version: int,
        actor_id: str,
        idempotency_key: str,
    ) -> object:
        assert (self.active_generation_id, self.pointer_version) == (
            expected_generation_id,
            expected_pointer_version,
        )
        if self.rollback_fails:
            raise RuntimeError("credential=must-not-leak")
        self.rollbacks.append(
            {
                "generationId": generation_id,
                "expectedGenerationId": expected_generation_id,
                "expectedPointerVersion": expected_pointer_version,
                "actorId": actor_id,
                "idempotencyKey": idempotency_key,
            }
        )
        self.active_generation_id = generation_id
        self.pointer_version += 1
        return self._event("ROLLBACK")

    def _event(self, operation: str) -> object:
        return SimpleNamespace(
            id=f"event-{self.pointer_version}",
            operation=operation,
            outcome="APPLIED",
            receipt_digest="sha256:" + "d" * 64,
            committed_pointer_version=self.pointer_version,
        )


class InspectionResult:
    def __init__(
        self,
        *,
        rows: tuple[tuple[object, ...], ...] = (),
        scalar: object | None = None,
    ) -> None:
        self.rows = rows
        self.scalar = scalar

    def all(self) -> tuple[tuple[object, ...], ...]:
        return self.rows

    def scalar_one(self) -> object:
        return self.scalar


class InspectionTransaction:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class InspectionConnection:
    def __init__(
        self,
        *,
        full_rows: tuple[tuple[object, ...], ...],
        status_rows: tuple[tuple[object, ...], ...],
        chunk_rows: tuple[tuple[object, ...], ...],
        vector_chunk_rows: tuple[tuple[object, ...], ...],
    ) -> None:
        self.full_rows = full_rows
        self.status_rows = status_rows
        self.chunk_rows = chunk_rows
        self.vector_chunk_rows = vector_chunk_rows
        self.transaction = InspectionTransaction()
        self.read_only = False

    def __enter__(self) -> InspectionConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def begin(self) -> InspectionTransaction:
        return self.transaction

    def exec_driver_sql(self, statement: str) -> None:
        assert statement == (
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE"
        )
        self.read_only = True

    def execute(
        self, statement: object, _parameters: object | None = None
    ) -> InspectionResult:
        query = str(statement)
        if "to_regclass" in query:
            return InspectionResult(scalar=True)
        if "FROM lightrag_doc_full" in query:
            return InspectionResult(rows=self.full_rows)
        if "FROM lightrag_doc_status" in query:
            return InspectionResult(rows=self.status_rows)
        if "FROM lightrag_doc_chunks" in query:
            return InspectionResult(rows=self.chunk_rows)
        if "FROM lightrag_vdb_chunks" in query:
            return InspectionResult(rows=self.vector_chunk_rows)
        if "FROM ag_catalog.ag_graph" in query:
            return InspectionResult(scalar=True)
        raise AssertionError(f"unexpected inspection query: {query}")


class InspectionEngine:
    def __init__(self, connection: InspectionConnection) -> None:
        self.connection = connection

    def connect(self) -> InspectionConnection:
        return self.connection


def _inspect_status_rows(
    monkeypatch: pytest.MonkeyPatch,
    status_rows: tuple[tuple[object, ...], ...],
) -> dict[str, object]:
    target = _target()
    snapshot = _snapshot(target)
    documents = snapshot.documents
    full_rows = tuple(
        (document.postgres_record_id, document.content) for document in documents
    )
    chunk_rows = tuple(
        (
            document.postgres_record_id,
            f"{reindex.CANONICAL_SOURCE_PREFIX}{document.postgres_record_id}",
        )
        for document in documents
    )
    vector_chunk_rows = tuple((document.postgres_record_id,) for document in documents)
    connection = InspectionConnection(
        full_rows=full_rows,
        status_rows=status_rows,
        chunk_rows=chunk_rows,
        vector_chunk_rows=vector_chunk_rows,
    )
    monkeypatch.setattr(
        reindex,
        "_assert_database",
        lambda *_args, **_kwargs: {
            "databaseName": target.auxiliary_database_name,
            "databaseCommentAttested": True,
        },
    )

    report = reindex.inspect_auxiliary(
        InspectionEngine(connection),
        target=target,
        expected_snapshot=snapshot,
    )

    assert connection.read_only is True
    assert connection.transaction.committed is True
    return report


def _apply(plan: object, source: Source, provider: object) -> dict[str, object]:
    return reindex.FullReindexWorkflow(source).apply(
        plan,
        provider,
        confirmation=plan.confirmation_required,
        expected_manifest_digest=plan.manifest_digest,
    )


def test_default_preview_does_not_read_secret_environments_or_apply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TC_PHASE_F_KNOWLEDGE_DATABASE_URL", raising=False)
    monkeypatch.delenv("TC_PHASE_F_LIGHTRAG_DATABASE_URL", raising=False)
    assert (
        reindex.main(
            [
                "--target-name",
                "tc-phase-f-knowledge-staging-copy",
                "--target-class",
                "staging-copy",
                "--authoritative-database-name",
                "tc_phase_f_knowledge_staging_copy",
                "--authoritative-database-url-env",
                "TC_PHASE_F_KNOWLEDGE_DATABASE_URL",
                "--auxiliary-database-name",
                "tc_phase_f_lightrag_staging_copy",
                "--auxiliary-database-url-env",
                "TC_PHASE_F_LIGHTRAG_DATABASE_URL",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["executionStatus"] == "NOT_RUN"
    assert payload["currentAdapter"]["liveApplySupported"] is False
    assert payload["mutates"] == []
    assert payload["rawCorpusOmitted"] is True


def test_unexpected_inspection_failure_does_not_disclose_exception_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "database-password-must-not-leak"

    def fail(_args: object) -> object:
        raise RuntimeError(secret)

    monkeypatch.setattr(reindex, "_run", fail)
    result = reindex.main(
        [
            "--target-name",
            "tc-phase-f-knowledge-staging-copy",
            "--target-class",
            "staging-copy",
            "--authoritative-database-name",
            "tc_phase_f_knowledge_staging_copy",
            "--authoritative-database-url-env",
            "TC_PHASE_F_KNOWLEDGE_DATABASE_URL",
            "--auxiliary-database-name",
            "tc_phase_f_lightrag_staging_copy",
            "--auxiliary-database-url-env",
            "TC_PHASE_F_LIGHTRAG_DATABASE_URL",
        ]
    )

    assert result == 2
    error = capsys.readouterr().err
    assert secret not in error
    assert "RuntimeError" in error


def test_source_documents_reject_remote_or_redirectable_citation_urls() -> None:
    record_id = "00000000-0000-4000-8000-000000000001"
    with pytest.raises(reindex.SafetyError, match="remote URLs, redirects"):
        reindex.CorpusDocument(
            postgres_record_id=record_id,
            content="private source",
            source_uri="https://stage.example.test/redirect?to=elsewhere",
        )


def test_status_identity_probe_rejects_wrong_ids_despite_matching_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_first = "00000000-0000-4000-8000-000000000001"
    wrong_second = "00000000-0000-4000-8000-000000000003"

    report = _inspect_status_rows(
        monkeypatch,
        ((expected_first, "processed"), (wrong_second, "processed")),
    )

    assert report["statusCount"] == 2
    assert report["processedCount"] == 2
    assert report["corpusDigest"] == _snapshot(_target()).corpus_digest
    assert report["citationChecksumMatches"] is True
    assert report["chunkDocumentCardinalityMatches"] is True
    assert report["graphPresent"] is True
    assert report["statusDocumentIdentitySetMatches"] is False
    assert report["matchesSourceSnapshot"] is False


@pytest.mark.parametrize(
    "status_rows",
    [
        (("00000000-0000-4000-8000-000000000001", "processed"),),
        (
            ("00000000-0000-4000-8000-000000000001", "processed"),
            ("00000000-0000-4000-8000-000000000002", "processed"),
            ("00000000-0000-4000-8000-000000000003", "processed"),
        ),
        (
            ("00000000-0000-4000-8000-000000000001", "processed"),
            ("00000000-0000-4000-8000-000000000001", "processed"),
        ),
        (
            ("00000000-0000-4000-8000-000000000001", "processed"),
            ("00000000-0000-4000-8000-000000000002", "processing"),
        ),
    ],
    ids=["missing", "extra-stale", "duplicate", "partial"],
)
def test_status_identity_and_processing_nearby_cases_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    status_rows: tuple[tuple[object, ...], ...],
) -> None:
    report = _inspect_status_rows(monkeypatch, status_rows)

    assert report["matchesSourceSnapshot"] is False


def test_exact_processed_status_identity_set_matches_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _inspect_status_rows(
        monkeypatch,
        (
            ("00000000-0000-4000-8000-000000000001", "processed"),
            ("00000000-0000-4000-8000-000000000002", "processed"),
        ),
    )

    assert report["statusDocumentIdentitySetMatches"] is True
    assert report["matchesSourceSnapshot"] is True


@pytest.mark.parametrize(
    "raw_url",
    [
        (
            "postgresql://user:secret@stage-a.example.test,stage-b.example.test/"
            "tc_phase_f_knowledge_staging_copy?sslmode=verify-full"
        ),
        (
            "postgresql://user:secret@database.example.test/"
            "tc_phase_f_knowledge_staging_copy?sslmode=verify-full"
        ),
        (
            "postgresql://user:secret@tc-stage-db.example.test/"
            "tc_phase_f_knowledge_staging_copy?sslmode=require"
        ),
    ],
)
def test_database_target_rejects_remote_ambiguity(raw_url: str) -> None:
    with pytest.raises(reindex.SafetyError):
        reindex.parse_database_location(
            raw_url,
            expected_name="tc_phase_f_knowledge_staging_copy",
            target_class="staging-copy",
        )


@pytest.mark.parametrize(
    "token",
    ["prod01", "production2", "live01", "primary2", "main01"],
)
def test_database_target_rejects_numbered_production_host_labels(token: str) -> None:
    with pytest.raises(reindex.SafetyError, match="database host"):
        reindex.parse_database_location(
            (
                f"postgresql://user:secret@stage-{token}.example.test/"
                "tc_phase_f_knowledge_staging_copy?sslmode=verify-full"
            ),
            expected_name="tc_phase_f_knowledge_staging_copy",
            target_class="staging-copy",
        )


def test_database_target_allows_unrelated_words_with_guard_prefixes() -> None:
    location = reindex.parse_database_location(
        (
            "postgresql://user:secret@stage-productivity.example.test/"
            "tc_phase_f_knowledge_staging_copy?sslmode=verify-full"
        ),
        expected_name="tc_phase_f_knowledge_staging_copy",
        target_class="staging-copy",
    )

    assert location.host == "stage-productivity.example.test"


def test_stale_digest_stops_before_any_provider_mutation() -> None:
    target = _target()
    preview = _snapshot(target)
    stale = _snapshot(target, source_digest="d" * 64)
    plan = reindex.ReindexPlan.create(target=target, snapshot=preview)
    provider = Provider(target)

    with pytest.raises(reindex.VerificationError, match="source ledger"):
        _apply(plan, Source(stale), provider)

    assert provider.begin_calls == 0


def test_source_change_after_export_discards_shadow_and_never_activates() -> None:
    target = _target()
    preview = _snapshot(target)
    stale = _snapshot(target, pgvector_digest="d" * 64)
    plan = reindex.ReindexPlan.create(target=target, snapshot=preview)
    provider = Provider(target)

    with pytest.raises(reindex.VerificationError, match="pgvector"):
        _apply(plan, Source(preview, stale), provider)

    assert provider.discard_calls == 1
    assert provider.current == "tc_previous_generation"


def test_wrong_exact_auxiliary_target_is_rejected_before_generation_creation() -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    provider = Provider(target)
    provider.target = reindex.TargetDeclaration(
        name="tc-phase-f-other-staging-copy",
        target_class="staging-copy",
        authoritative_database_name="tc_phase_f_other_staging_copy",
        auxiliary_database_name="tc_phase_f_other_lightrag_staging_copy",
        active_workspace="tc",
    )

    with pytest.raises(reindex.VerificationError, match="different auxiliary target"):
        _apply(plan, Source(snapshot), provider)

    assert provider.begin_calls == 0


@pytest.mark.parametrize(
    ("control_plane", "redirect_hops"),
    [("remote-http", 0), ("embedded-postgresql", 1)],
)
def test_redirect_or_remote_provider_control_plane_fails_closed(
    control_plane: str, redirect_hops: int
) -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    provider = Provider(
        target, control_plane=control_plane, redirect_hops=redirect_hops
    )

    with pytest.raises(reindex.LiveApplyUnsupported, match="redirecting"):
        _apply(plan, Source(snapshot), provider)

    assert provider.begin_calls == 0


def test_partial_index_is_discarded_before_activation() -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    provider = Provider(target)
    provider.inspection_processed_delta = -1

    with pytest.raises(reindex.VerificationError, match="incomplete"):
        _apply(plan, Source(snapshot), provider)

    assert provider.discard_calls == 1
    assert provider.current == "tc_previous_generation"


def test_wrong_processed_identity_set_is_discarded_before_activation() -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    provider = Provider(target)
    provider.inspection_processed_ids = (
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000003",
    )

    with pytest.raises(reindex.VerificationError, match="incomplete"):
        _apply(plan, Source(snapshot), provider)

    assert provider.discard_calls == 1
    assert provider.current == "tc_previous_generation"


@pytest.mark.parametrize(
    ("count_delta", "digest", "message"),
    [(1, None, "cardinality"), (0, "f" * 64, "checksum")],
)
def test_checksum_or_cardinality_mismatch_fails_closed(
    count_delta: int, digest: str | None, message: str
) -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    provider = Provider(target)
    provider.inspection_count_delta = count_delta
    provider.inspection_digest = digest

    with pytest.raises(reindex.VerificationError, match=message):
        _apply(plan, Source(snapshot), provider)

    assert provider.discard_calls == 1


def test_ambiguous_activation_failure_rolls_back_to_previous_generation() -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    provider = Provider(target)
    provider.activate_failure = "after"

    with pytest.raises(reindex.ActivationFailure) as raised:
        _apply(plan, Source(snapshot), provider)

    assert raised.value.rollback_attempted is True
    assert raised.value.rollback_succeeded is True
    assert provider.rollback_calls == 1
    assert provider.current == "tc_previous_generation"


def test_rollback_failure_is_explicit_and_never_reported_as_success() -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    provider = Provider(target)
    provider.activate_failure = "after"
    provider.rollback_fails = True

    with pytest.raises(reindex.ActivationFailure) as raised:
        _apply(plan, Source(snapshot), provider)

    assert raised.value.rollback_attempted is True
    assert raised.value.rollback_succeeded is False
    assert provider.current == plan.generation_id


def test_verified_provider_uses_bounded_batches_and_returns_only_attestation() -> None:
    target = _target()
    documents = tuple(_document(number) for number in range(1, 6))
    snapshot = _snapshot(target, documents=documents)
    policy = reindex.ExportPolicy(
        max_documents=5,
        max_total_bytes=4096,
        batch_documents=2,
        batch_bytes=1024,
    )
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot, policy=policy)
    provider = Provider(target)

    report = _apply(plan, Source(snapshot), provider)

    assert report["executionStatus"] == "SUCCEEDED"
    assert report["documentCount"] == 5
    assert report["rawCorpusOmitted"] is True
    assert "document 1" not in json.dumps(report)
    assert provider.current == plan.generation_id


def test_ledger_controller_registers_verified_evidence_and_activates_with_cas() -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    source = Source(snapshot)
    storage = IsolatedStorage(target, plan)
    ledger = Ledger()
    controller = reindex.LedgerBackedGenerationController(
        plan=plan,
        source=source,
        storage=storage,
        control_plane=ledger,
        actor_id="release-owner",
    )

    report = _apply(plan, source, controller)

    assert ledger.active_generation_id == plan.generation_id
    assert ledger.pointer_version == 1
    assert len(ledger.registrations) == 1
    registration = ledger.registrations[0]
    assert registration["workspace"] == plan.generation_id
    assert registration["manifest_digest"] == plan.manifest_digest
    assert registration["corpus_digest"] == snapshot.corpus_digest
    assert registration["source_ledger_digest"] == snapshot.source_ledger_digest
    assert registration["document_count"] == snapshot.document_count
    assert registration["metadata"] == {
        "auxiliaryAuthorityId": target.name,
        "providerContractVersion": reindex.PROVIDER_CONTRACT_VERSION,
        "providerDistribution": "lightrag-hku",
        "providerVersion": reindex.LIGHTRAG_VERSION,
        "runtimeConfigDigest": "sha256:" + "9" * 64,
        "sourceTargetDigest": snapshot.source_target_digest,
        "processedIdentitySetDigest": reindex._digest(
            sorted(document.postgres_record_id for document in snapshot.documents)
        ),
        "citationPolicy": "POSTGRESQL_CANONICAL_NODE_URI",
        "rawCorpusOmitted": True,
    }
    assert ledger.activations[0]["expectedGenerationId"] is None
    assert ledger.activations[0]["expectedPointerVersion"] == 0
    assert report["controlPlane"] == {
        "authority": "TC_POSTGRESQL_GENERATION_LEDGER",
        "operation": "ACTIVATE",
        "outcome": "APPLIED",
        "eventId": "event-1",
        "receiptDigest": "sha256:" + "d" * 64,
        "committedPointerVersion": 1,
        "manualEnvironmentSwitch": False,
        "credentialsOmitted": True,
    }
    serialized = json.dumps(report, sort_keys=True)
    assert "document 1" not in serialized
    assert "password" not in serialized


def test_ledger_controller_rejects_self_consistent_counterfeit_provider_id() -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    source = Source(snapshot)
    storage = IsolatedStorage(
        target,
        plan,
        provider_id="counterfeit-lightrag-postgresql",
    )
    ledger = Ledger()
    controller = reindex.LedgerBackedGenerationController(
        plan=plan,
        source=source,
        storage=storage,
        control_plane=ledger,
        actor_id="release-owner",
    )

    with pytest.raises(reindex.LiveApplyUnsupported, match="provider identity"):
        _apply(plan, source, controller)

    assert storage.documents == {}
    assert ledger.registrations == []
    assert ledger.activations == []


def test_settings_composition_rejects_self_consistent_counterfeit_provider_id(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
        encryption_master_key="phase-f-test-encryption-key-000001",
        light_rag_enabled=True,
        light_rag_authority_id="tc-phase-f-knowledge-staging-copy",
        light_rag_auxiliary_target_digest="sha256:" + "f" * 64,
        light_rag_provider_id="counterfeit-lightrag-postgresql",
        light_rag_postgres_host="auxiliary.invalid",
        light_rag_postgres_user="lightrag_runtime",
        light_rag_postgres_password="phase-f-runtime-secret",
        light_rag_postgres_database="tc_phase_f_lightrag_auxiliary",
        light_rag_postgres_ssl_mode="verify-full",
        light_rag_apache_age_available=True,
        light_rag_runtime_bootstrap="approved.runtime:build",
        light_rag_working_directory_root=(tmp_path / "runtimes").resolve(),
    )

    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as counterfeit:
        build_lightrag_generation_runtime_factory(settings)

    assert (
        counterfeit.value.reason
        is LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
    )


def test_controller_binding_is_accepted_by_settings_factory_and_version_fenced(
    tmp_path: Path,
) -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tc:tc@localhost:5432/tc_test",
        encryption_master_key="phase-f-test-encryption-key-000001",
        light_rag_enabled=True,
        light_rag_authority_id=target.name,
        light_rag_auxiliary_target_digest=target.auxiliary_target_digest,
        light_rag_provider_id=OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
        light_rag_postgres_host="auxiliary.invalid",
        light_rag_postgres_user="lightrag_runtime",
        light_rag_postgres_password="phase-f-runtime-secret",
        light_rag_postgres_database=target.auxiliary_database_name,
        light_rag_postgres_ssl_mode="verify-full",
        light_rag_apache_age_available=True,
        light_rag_runtime_bootstrap="approved.runtime:build",
        light_rag_working_directory_root=(tmp_path / "runtimes").resolve(),
    )
    factory = build_lightrag_generation_runtime_factory(settings)
    assert factory is not None
    (attestation,) = factory.configuration_attestations()
    source = Source(snapshot)
    ledger = Ledger()
    controller = reindex.LedgerBackedGenerationController(
        plan=plan,
        source=source,
        storage=IsolatedStorage(
            target,
            plan,
            runtime_config_digest=attestation.runtime_config_digest,
        ),
        control_plane=ledger,
        actor_id="release-owner",
    )

    _apply(plan, source, controller)
    registration = ledger.registrations[0]
    metadata_value = registration["metadata"]
    assert isinstance(metadata_value, dict)

    def normalized(value: object) -> str:
        rendered = str(value)
        return rendered if rendered.startswith("sha256:") else f"sha256:{rendered}"

    binding = ActiveGenerationBinding(
        generation_id=str(registration["generation_id"]),
        pointer_version=ledger.pointer_version,
        pointer_updated_at=datetime.now(UTC),
        authority_id=str(metadata_value["auxiliaryAuthorityId"]),
        provider_id=str(registration["provider_id"]),
        provider_contract_version=str(metadata_value["providerContractVersion"]),
        provider_distribution=str(metadata_value["providerDistribution"]),
        provider_version=str(metadata_value["providerVersion"]),
        workspace=str(registration["workspace"]),
        auxiliary_target_digest=normalized(
            registration["auxiliary_target_digest"]
        ),
        runtime_config_digest=normalized(metadata_value["runtimeConfigDigest"]),
        manifest_digest=normalized(registration["manifest_digest"]),
        corpus_digest=normalized(registration["corpus_digest"]),
        source_ledger_digest=normalized(registration["source_ledger_digest"]),
        projection_digest=normalized(registration["projection_digest"]),
        pgvector_digest=normalized(registration["pgvector_digest"]),
        document_count=int(registration["document_count"]),
        registration_digest="sha256:" + "d" * 64,
    )

    factory.validate_binding(binding)
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as wrong_provider:
        factory.validate_binding(replace(binding, provider_id="wrong-provider"))
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as wrong_version:
        factory.validate_binding(replace(binding, provider_version="1.5.5"))

    assert (
        wrong_provider.value.reason
        is LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
    )
    assert (
        wrong_version.value.reason
        is LightRAGGenerationRuntimeUnavailableReason.VERSION_MISMATCH
    )


def test_controller_can_activate_consecutive_fresh_workspaces_on_one_authority() -> (
    None
):
    first_target = _target()
    first_snapshot = _snapshot(first_target)
    first_plan = reindex.ReindexPlan.create(
        target=first_target, snapshot=first_snapshot
    )
    ledger = Ledger()
    first_source = Source(first_snapshot)
    first = reindex.LedgerBackedGenerationController(
        plan=first_plan,
        source=first_source,
        storage=IsolatedStorage(first_target, first_plan),
        control_plane=ledger,
        actor_id="release-owner",
    )
    _apply(first_plan, first_source, first)

    second_target = reindex.TargetDeclaration(
        name=first_target.name,
        target_class=first_target.target_class,
        authoritative_database_name=first_target.authoritative_database_name,
        auxiliary_database_name=first_target.auxiliary_database_name,
        active_workspace=first_plan.generation_id,
    )
    second_snapshot = _snapshot(second_target)
    second_plan = reindex.ReindexPlan.create(
        target=second_target, snapshot=second_snapshot
    )
    second_source = Source(second_snapshot)
    second = reindex.LedgerBackedGenerationController(
        plan=second_plan,
        source=second_source,
        storage=IsolatedStorage(second_target, second_plan),
        control_plane=ledger,
        actor_id="release-owner",
    )

    _apply(second_plan, second_source, second)

    assert first_plan.generation_id != second_plan.generation_id
    assert ledger.active_generation_id == second_plan.generation_id
    assert ledger.pointer_version == 2
    assert ledger.activations[1]["expectedGenerationId"] == first_plan.generation_id
    assert ledger.activations[1]["expectedPointerVersion"] == 1
    assert (
        ledger.registrations[0]["auxiliary_target_digest"]
        == ledger.registrations[1]["auxiliary_target_digest"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("inspection_workspace", "wrong_workspace"),
        ("inspection_target_digest", "f" * 64),
        ("inspection_manifest_digest", "e" * 64),
    ],
)
def test_ledger_controller_rejects_inspection_not_bound_to_exact_preview(
    field: str, value: str
) -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    source = Source(snapshot)
    storage = IsolatedStorage(target, plan)
    setattr(storage, field, value)
    ledger = Ledger()
    controller = reindex.LedgerBackedGenerationController(
        plan=plan,
        source=source,
        storage=storage,
        control_plane=ledger,
        actor_id="release-owner",
    )

    with pytest.raises(reindex.VerificationError, match="exact preview target"):
        _apply(plan, source, controller)

    assert storage.discard_calls == 1
    assert ledger.registrations == []
    assert ledger.activations == []


def test_controller_rechecks_source_immediately_before_registration() -> None:
    target = _target()
    snapshot = _snapshot(target)
    stale = _snapshot(target, source_digest="f" * 64)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    # Workflow checks consume the first two snapshots; the controller owns the
    # third, immediately-before-registration recheck.
    source = Source(snapshot, snapshot, stale)
    storage = IsolatedStorage(target, plan)
    ledger = Ledger()
    controller = reindex.LedgerBackedGenerationController(
        plan=plan,
        source=source,
        storage=storage,
        control_plane=ledger,
        actor_id="release-owner",
    )

    with pytest.raises(reindex.ActivationFailure) as raised:
        _apply(plan, source, controller)

    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert raised.value.rollback_attempted is False
    assert raised.value.rollback_succeeded is True
    assert storage.discard_calls == 1
    assert ledger.registrations == []
    assert ledger.activations == []


def test_controller_ambiguous_failure_uses_ledger_cas_rollback() -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    source = Source(snapshot)
    storage = IsolatedStorage(target, plan)
    ledger = Ledger(active_generation_id="tc_previous_generation", pointer_version=1)

    class AmbiguousController(reindex.LedgerBackedGenerationController):
        def _after_activation(self) -> None:
            raise RuntimeError("provider-token-must-not-leak")

    controller = AmbiguousController(
        plan=plan,
        source=source,
        storage=storage,
        control_plane=ledger,
        actor_id="release-owner",
    )

    with pytest.raises(reindex.ActivationFailure) as raised:
        _apply(plan, source, controller)

    rendered_traceback = "".join(
        traceback.format_exception(raised.type, raised.value, raised.tb)
    )
    assert raised.value.rollback_attempted is True
    assert raised.value.rollback_succeeded is True
    assert "provider-token-must-not-leak" not in str(raised.value)
    assert "provider-token-must-not-leak" not in rendered_traceback
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert ledger.active_generation_id == "tc_previous_generation"
    assert ledger.pointer_version == 3
    assert ledger.rollbacks[0]["expectedGenerationId"] == plan.generation_id
    assert ledger.rollbacks[0]["expectedPointerVersion"] == 2


def test_controller_rollback_failure_is_explicit_and_pointer_remains_new() -> None:
    target = _target()
    snapshot = _snapshot(target)
    plan = reindex.ReindexPlan.create(target=target, snapshot=snapshot)
    source = Source(snapshot)
    storage = IsolatedStorage(target, plan)
    ledger = Ledger(active_generation_id="tc_previous_generation", pointer_version=1)
    ledger.rollback_fails = True

    class AmbiguousController(reindex.LedgerBackedGenerationController):
        def _after_activation(self) -> None:
            raise RuntimeError("activation-secret-must-not-leak")

    controller = AmbiguousController(
        plan=plan,
        source=source,
        storage=storage,
        control_plane=ledger,
        actor_id="release-owner",
    )

    with pytest.raises(reindex.ActivationFailure) as raised:
        _apply(plan, source, controller)

    rendered_traceback = "".join(
        traceback.format_exception(raised.type, raised.value, raised.tb)
    )
    assert raised.value.rollback_attempted is True
    assert raised.value.rollback_succeeded is False
    assert "activation-secret" not in str(raised.value)
    assert "credential" not in str(raised.value)
    assert "activation-secret" not in rendered_traceback
    assert "credential=must-not-leak" not in rendered_traceback
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert ledger.active_generation_id == plan.generation_id
    assert ledger.pointer_version == 2


def test_preview_advertises_composed_runtime_but_never_applies_it() -> None:
    target = _target()
    preview = reindex._preview(target)

    assert preview["executionStatus"] == "NOT_RUN"
    assert preview["executes"] is False
    assert preview["mutates"] == []
    assert preview["currentAdapter"]["durableActiveGenerationPointer"] is True
    assert preview["currentAdapter"]["atomicActivation"] == "TC_POSTGRESQL_CAS"
    assert preview["currentAdapter"]["atomicRollback"] == "TC_POSTGRESQL_CAS"
    assert preview["currentAdapter"]["runtimeResolver"] == "LightRAGRuntimeResolver"
    assert preview["currentAdapter"]["runtimePointerAttestation"] == (
        "PER_OPERATION_BEFORE_AND_AFTER"
    )
    assert preview["currentAdapter"]["manualRuntimeEnvironmentSwitch"] == "FORBIDDEN"
    assert preview["currentAdapter"]["runtimeFactory"] == (
        "IsolatedLightRAGGenerationRuntimeFactory"
    )
    assert preview["currentAdapter"]["runtimeFactoryConfigured"] is False
    assert preview["currentAdapter"]["runtimeSettingsComposition"] == (
        "API_AND_WORKER_OPT_IN"
    )
    assert preview["currentAdapter"]["runtimeDefaultEnabled"] is False
    assert preview["currentAdapter"]["approvedBootstrapAvailable"] is False
    assert preview["currentAdapter"]["stagingCredentialsConfigured"] is False
    assert preview["currentAdapter"]["parentProcessEnvironmentMutation"] == "FORBIDDEN"
    assert preview["currentAdapter"]["applicationRuntimeComposition"] is True
    assert preview["apply"]["available"] is False
