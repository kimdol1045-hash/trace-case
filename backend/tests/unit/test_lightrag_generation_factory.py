from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from tc_backend.knowledge import lightrag_generation_factory as generation_factory_module
from tc_backend.knowledge.lightrag import (
    OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
    LightRAGCallStatus,
    LightRAGIndexDocument,
)
from tc_backend.knowledge.lightrag_generation_factory import (
    IsolatedLightRAGGenerationConfig,
    IsolatedLightRAGGenerationRuntimeFactory,
    LightRAGGenerationRuntimeUnavailable,
    LightRAGGenerationRuntimeUnavailableReason,
    LightRAGSubprocessPostgresCredentials,
    LightRAGSubprocessStorageConfig,
    SubprocessLightRAGIndex,
)
from tc_backend.knowledge.lightrag_runtime import LightRAGRuntimeResolver
from tc_backend.repositories.lightrag_generation_repository import (
    ActiveGenerationBinding,
)

TEST_SECRET = "factory-test-password-must-never-appear"
TEST_WORKER = Path(__file__).parent / "fixtures" / "lightrag_generation_test_worker.py"
PRODUCTION_CHILD_HARNESS = (
    Path(__file__).parent
    / "fixtures"
    / "lightrag_generation_production_child_harness.py"
)


def test_default_worker_uses_module_execution_to_avoid_provider_shadowing() -> None:
    assert generation_factory_module._default_worker_command() == (
        sys.executable,
        "-u",
        "-m",
        "tc_backend.knowledge.lightrag_generation_process",
    )


def _config(
    root: Path,
    *,
    bootstrap: str = "tests.fake:probe",
    required_version: str = "1.5.6",
    storage: LightRAGSubprocessStorageConfig | None = None,
    enabled: bool = True,
) -> IsolatedLightRAGGenerationConfig:
    return IsolatedLightRAGGenerationConfig(
        enabled=enabled,
        authority_id="authority-test",
        auxiliary_target_digest="sha256:" + "f" * 64,
        provider_id=OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
        postgres=LightRAGSubprocessPostgresCredentials(
            host="auxiliary.internal",
            port=5432,
            user="lightrag_runtime",
            password=SecretStr(TEST_SECRET),
            database="lightrag_runtime",
            max_connections=4,
            ssl_mode="verify-full",
        ),
        storage=storage
        or LightRAGSubprocessStorageConfig(apache_age_available=True),
        required_version=required_version,
        runtime_bootstrap=bootstrap,
        working_directory_root=(root / "runtimes").resolve(),
        startup_timeout_seconds=4,
        operation_timeout_seconds=4,
        shutdown_timeout_seconds=1,
    )


def _binding(
    config: IsolatedLightRAGGenerationConfig,
    *,
    generation_id: str = "generation-a",
    pointer_version: int = 1,
    workspace: str = "workspace-a",
) -> ActiveGenerationBinding:
    digest = "sha256:" + "a" * 64
    return ActiveGenerationBinding(
        generation_id=generation_id,
        pointer_version=pointer_version,
        pointer_updated_at=datetime(2026, 9, 2, tzinfo=UTC),
        authority_id=config.authority_id,
        provider_id=config.provider_id,
        provider_contract_version=config.provider_contract_version,
        provider_distribution=config.distribution,
        provider_version=config.required_version,
        workspace=workspace,
        auxiliary_target_digest=config.auxiliary_target_digest,
        runtime_config_digest=config.runtime_config_digest,
        manifest_digest=digest,
        corpus_digest=digest,
        source_ledger_digest=digest,
        projection_digest=digest,
        pgvector_digest=digest,
        document_count=3,
        registration_digest=digest,
    )


def _factory(
    *configs: IsolatedLightRAGGenerationConfig,
    installed_version: str | None = "1.5.6",
) -> IsolatedLightRAGGenerationRuntimeFactory:
    return IsolatedLightRAGGenerationRuntimeFactory(
        configs,
        version_resolver=lambda _distribution: installed_version,
        worker_command=(sys.executable, "-u", str(TEST_WORKER)),
    )


def _reason(
    captured: pytest.ExceptionInfo[LightRAGGenerationRuntimeUnavailable],
) -> LightRAGGenerationRuntimeUnavailableReason:
    return captured.value.reason


def test_factory_requires_exact_authority_provider_version_and_config_digest(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    binding = _binding(config)
    factory = _factory(config)

    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as wrong_provider:
        factory(replace(binding, provider_id="other-provider"))
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as malformed_binding:
        factory(replace(binding, generation_id="../wrong"))
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as wrong_authority:
        factory(replace(binding, authority_id="other-authority"))
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as wrong_version:
        factory(replace(binding, provider_version="1.5.5"))
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as wrong_authority_digest:
        factory(replace(binding, auxiliary_target_digest="sha256:" + "b" * 64))
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as wrong_config_digest:
        factory(replace(binding, runtime_config_digest="sha256:" + "c" * 64))

    assert _reason(wrong_provider) is LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
    assert _reason(malformed_binding) is LightRAGGenerationRuntimeUnavailableReason.BINDING_MISMATCH
    assert _reason(wrong_authority) is LightRAGGenerationRuntimeUnavailableReason.AUTHORITY_MISMATCH
    assert (
        _reason(wrong_version)
        is LightRAGGenerationRuntimeUnavailableReason.VERSION_MISMATCH
    )
    assert (
        _reason(wrong_authority_digest)
        is LightRAGGenerationRuntimeUnavailableReason.AUTHORITY_MISMATCH
    )
    assert (
        _reason(wrong_config_digest)
        is LightRAGGenerationRuntimeUnavailableReason.CONFIG_DIGEST_MISMATCH
    )


def test_factory_rejects_self_consistent_counterfeit_provider_id(
    tmp_path: Path,
) -> None:
    config = replace(_config(tmp_path), provider_id="counterfeit-lightrag-postgresql")

    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as counterfeit:
        _factory(config).validate_binding(_binding(config))

    assert (
        _reason(counterfeit)
        is LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
    )


def test_factory_fails_closed_for_disabled_missing_provider_and_version(tmp_path: Path) -> None:
    config = _config(tmp_path)
    binding = _binding(config)

    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as missing_provider:
        _factory()(binding)
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as disabled:
        disabled_config = replace(config, enabled=False)
        _factory(disabled_config)(_binding(disabled_config))
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as missing_version:
        _factory(config, installed_version=None)(binding)
    undeclared_version = replace(config, required_version="")
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as no_declared_version:
        _factory(undeclared_version).validate_configuration()
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as wrong_version:
        _factory(config, installed_version="1.5.5")(binding)

    assert _reason(missing_provider) is LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
    assert _reason(disabled) is LightRAGGenerationRuntimeUnavailableReason.DISABLED
    assert _reason(missing_version) is LightRAGGenerationRuntimeUnavailableReason.VERSION_UNAVAILABLE
    assert _reason(no_declared_version) is LightRAGGenerationRuntimeUnavailableReason.VERSION_UNAVAILABLE
    assert _reason(wrong_version) is LightRAGGenerationRuntimeUnavailableReason.VERSION_MISMATCH


def test_factory_rejects_apache_age_and_official_storage_mismatch(tmp_path: Path) -> None:
    no_age = _config(
        tmp_path,
        storage=LightRAGSubprocessStorageConfig(apache_age_available=False),
    )
    wrong_storage = _config(
        tmp_path,
        storage=LightRAGSubprocessStorageConfig(
            vector_storage="NanoVectorDBStorage",
            apache_age_available=True,
        ),
    )

    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as age_failure:
        _factory(no_age)(_binding(no_age))
    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as storage_failure:
        _factory(wrong_storage)(_binding(wrong_storage))

    assert _reason(age_failure) is LightRAGGenerationRuntimeUnavailableReason.APACHE_AGE_UNAVAILABLE
    assert _reason(storage_failure) is LightRAGGenerationRuntimeUnavailableReason.STORAGE_MISMATCH


def test_remote_staging_requires_verify_full_but_local_disposable_allows_no_tls(
    tmp_path: Path,
) -> None:
    baseline = _config(tmp_path)
    assert baseline.postgres is not None
    remote_staging = replace(
        baseline,
        deployment_environment="staging",
        postgres=replace(baseline.postgres, ssl_mode="require"),
    )
    local_disposable = replace(
        baseline,
        deployment_environment="local",
        postgres=replace(
            baseline.postgres,
            host="127.0.0.1",
            ssl_mode="disable",
        ),
    )

    with pytest.raises(LightRAGGenerationRuntimeUnavailable) as insecure_remote:
        _factory(remote_staging).validate_configuration()
    _factory(local_disposable).validate_binding(_binding(local_disposable))

    assert (
        _reason(insecure_remote)
        is LightRAGGenerationRuntimeUnavailableReason.TLS_REQUIRED
    )


def test_child_attestation_must_match_the_complete_binding_and_cleans_up(tmp_path: Path) -> None:
    config = _config(tmp_path, bootstrap="tests.fake:wrong_attestation")
    runtime = _factory(config)(_binding(config))
    assert isinstance(runtime.index, SubprocessLightRAGIndex)

    result = asyncio.run(runtime.index.search("must fail closed"))
    working_directory = runtime.index.working_directory

    assert result.status is LightRAGCallStatus.UNAVAILABLE
    assert isinstance(result.failure, LightRAGGenerationRuntimeUnavailable)
    assert result.failure.reason is LightRAGGenerationRuntimeUnavailableReason.BINDING_MISMATCH
    assert runtime.index.worker_running is False
    assert working_directory is not None
    assert not working_directory.exists()


def test_production_child_bootstrap_receives_runtime_config_digest(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, bootstrap="approved.runtime:build")
    factory = IsolatedLightRAGGenerationRuntimeFactory(
        (config,),
        version_resolver=lambda _distribution: "1.5.6",
        worker_command=(sys.executable, "-u", str(PRODUCTION_CHILD_HARNESS)),
    )
    runtime = factory(_binding(config))
    assert isinstance(runtime.index, SubprocessLightRAGIndex)

    result = asyncio.run(
        runtime.index.index_document(
            LightRAGIndexDocument(
                postgres_record_id="11111111-1111-4111-8111-111111111111",
                content="network-free production child contract",
                source_uri=(
                    "postgresql://knowledge/nodes/"
                    "11111111-1111-4111-8111-111111111111"
                ),
            )
        )
    )
    working_directory = runtime.index.working_directory

    assert result.status is LightRAGCallStatus.OK
    assert result.require_value().track_id.endswith("-production-child-track")
    assert runtime.index.worker_running is True
    assert working_directory is not None and working_directory.exists()
    asyncio.run(runtime.index.finalize())
    assert not working_directory.exists()


def test_secret_is_redacted_and_parent_environment_is_never_mutated(tmp_path: Path) -> None:
    config = _config(tmp_path)
    binding = _binding(config)
    factory = _factory(config)
    before = {
        key: os.environ.get(key)
        for key in config.postgres._official_environment(binding.workspace)  # type: ignore[union-attr]
    }
    runtime = factory(binding)
    assert isinstance(runtime.index, SubprocessLightRAGIndex)

    result = asyncio.run(runtime.index.search("credential boundary"))
    after = {key: os.environ.get(key) for key in before}
    rendered = " ".join(
        (
            repr(config),
            repr(config.postgres),
            repr(factory),
            repr(runtime),
            repr(runtime.index),
            repr(result),
        )
    )

    assert result.require_value().context.startswith("workspace-a:")
    assert before == after
    assert TEST_SECRET not in rendered
    asyncio.run(runtime.index.finalize())


def test_two_generations_run_concurrently_in_distinct_process_environments(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    factory = _factory(config)
    first = factory(
        _binding(config, generation_id="generation-a", workspace="workspace-a")
    )
    second = factory(
        _binding(
            config,
            generation_id="generation-b",
            pointer_version=2,
            workspace="workspace-b",
        )
    )
    assert isinstance(first.index, SubprocessLightRAGIndex)
    assert isinstance(second.index, SubprocessLightRAGIndex)
    barrier = (tmp_path / "barrier").resolve()

    async def scenario() -> tuple[str, str]:
        first_result, second_result = await asyncio.gather(
            first.index.search(f"barrier:{barrier}"),
            second.index.search(f"barrier:{barrier}"),
        )
        return (
            first_result.require_value().context,
            second_result.require_value().context,
        )

    first_context, second_context = asyncio.run(scenario())
    first_directory = first.index.working_directory
    second_directory = second.index.working_directory

    assert first_context.startswith("workspace-a:")
    assert second_context.startswith("workspace-b:")
    assert first.index.worker_pid != second.index.worker_pid
    assert first_directory is not None and first_directory.exists()
    assert second_directory is not None and second_directory.exists()
    asyncio.run(first.index.finalize())
    asyncio.run(second.index.finalize())
    assert not first_directory.exists()
    assert not second_directory.exists()


def test_resolver_eviction_and_finalization_close_each_owned_process(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first_binding = _binding(
        config, generation_id="generation-a", workspace="workspace-a"
    )
    second_binding = _binding(
        config,
        generation_id="generation-b",
        pointer_version=2,
        workspace="workspace-b",
    )

    class BindingSource:
        binding = first_binding

        def active_generation(self) -> ActiveGenerationBinding:
            return self.binding

    source = BindingSource()
    core_factory = _factory(config)
    indexes: list[SubprocessLightRAGIndex] = []

    def recording_factory(binding: ActiveGenerationBinding):  # type: ignore[no-untyped-def]
        runtime = core_factory(binding)
        assert isinstance(runtime.index, SubprocessLightRAGIndex)
        indexes.append(runtime.index)
        return runtime

    resolver = LightRAGRuntimeResolver(source, recording_factory)

    assert asyncio.run(resolver.search("first")).status is LightRAGCallStatus.OK
    first_directory = indexes[0].working_directory
    assert first_directory is not None and first_directory.exists()
    source.binding = second_binding
    assert asyncio.run(resolver.search("second")).status is LightRAGCallStatus.OK
    assert not first_directory.exists()
    second_directory = indexes[1].working_directory
    assert second_directory is not None and second_directory.exists()

    asyncio.run(resolver.finalize())
    assert not second_directory.exists()
    assert all(not index.worker_running for index in indexes)


def test_hung_child_shutdown_is_bounded_and_forcibly_cleaned(tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path, bootstrap="tests.fake:hang_shutdown"),
        shutdown_timeout_seconds=0.1,
    )
    runtime = _factory(config)(_binding(config))
    assert isinstance(runtime.index, SubprocessLightRAGIndex)
    assert asyncio.run(runtime.index.search("started")).status is LightRAGCallStatus.OK
    working_directory = runtime.index.working_directory
    assert working_directory is not None and working_directory.exists()

    started = time.monotonic()
    asyncio.run(runtime.index.finalize())
    elapsed = time.monotonic() - started

    assert elapsed < 1
    assert runtime.index.worker_running is False
    assert not working_directory.exists()
