"""Shared, explicit Settings composition for versioned LightRAG runtimes."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import SecretStr

from tc_backend.config import Settings
from tc_backend.knowledge.lightrag import OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID
from tc_backend.knowledge.lightrag_generation_factory import (
    IsolatedLightRAGGenerationConfig,
    IsolatedLightRAGGenerationRuntimeFactory,
    LightRAGGenerationRuntimeUnavailable,
    LightRAGGenerationRuntimeUnavailableReason,
    LightRAGSubprocessPostgresCredentials,
    LightRAGSubprocessStorageConfig,
)


def build_lightrag_generation_runtime_factory(
    settings: Settings,
) -> IsolatedLightRAGGenerationRuntimeFactory | None:
    """Build the one approved factory contract, or remain fully disabled.

    ``Settings`` normally rejects incomplete activation.  The defensive check
    here also fails closed for settings constructed without Pydantic validation
    and deliberately reports field names only, never credential values.
    """

    if not settings.light_rag_enabled:
        return None

    required = {
        "light_rag_authority_id": settings.light_rag_authority_id,
        "light_rag_auxiliary_target_digest": (
            settings.light_rag_auxiliary_target_digest
        ),
        "light_rag_provider_id": settings.light_rag_provider_id,
        "light_rag_postgres_host": settings.light_rag_postgres_host,
        "light_rag_postgres_user": settings.light_rag_postgres_user,
        "light_rag_postgres_password": settings.light_rag_postgres_password,
        "light_rag_postgres_database": settings.light_rag_postgres_database,
        "light_rag_runtime_bootstrap": settings.light_rag_runtime_bootstrap,
        "light_rag_working_directory_root": settings.light_rag_working_directory_root,
    }
    missing = sorted(
        name for name, value in required.items() if value is None or value == ""
    )
    if missing:
        raise RuntimeError(
            "LightRAG runtime configuration is incomplete: " + ", ".join(missing)
        )
    if settings.light_rag_provider_id != OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID:
        raise LightRAGGenerationRuntimeUnavailable(
            LightRAGGenerationRuntimeUnavailableReason.PROVIDER_UNAVAILABLE
        )
    if not settings.light_rag_apache_age_available:
        raise RuntimeError(
            "LightRAG runtime configuration requires confirmed Apache AGE capability"
        )

    config = IsolatedLightRAGGenerationConfig(
        enabled=True,
        authority_id=cast(str, settings.light_rag_authority_id),
        auxiliary_target_digest=cast(
            str, settings.light_rag_auxiliary_target_digest
        ),
        provider_id=OFFICIAL_LIGHTRAG_POSTGRESQL_PROVIDER_ID,
        postgres=LightRAGSubprocessPostgresCredentials(
            host=cast(str, settings.light_rag_postgres_host),
            port=settings.light_rag_postgres_port,
            user=cast(str, settings.light_rag_postgres_user),
            password=cast(SecretStr, settings.light_rag_postgres_password),
            database=cast(str, settings.light_rag_postgres_database),
            max_connections=settings.light_rag_postgres_max_connections,
            ssl_mode=settings.light_rag_postgres_ssl_mode,
        ),
        storage=LightRAGSubprocessStorageConfig(apache_age_available=True),
        runtime_bootstrap=cast(str, settings.light_rag_runtime_bootstrap),
        deployment_environment=settings.environment,
        working_directory_root=cast(Path, settings.light_rag_working_directory_root),
        startup_timeout_seconds=settings.light_rag_startup_timeout_seconds,
        operation_timeout_seconds=settings.light_rag_operation_timeout_seconds,
        shutdown_timeout_seconds=settings.light_rag_shutdown_timeout_seconds,
    )
    factory = IsolatedLightRAGGenerationRuntimeFactory((config,))
    factory.validate_configuration()
    return factory


__all__ = ["build_lightrag_generation_runtime_factory"]
