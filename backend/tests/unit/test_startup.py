from __future__ import annotations

import pytest

from tc_backend.startup import (
    DatabaseCapabilities,
    StartupValidationError,
    validate_database_capabilities,
)


def capabilities(**overrides: object) -> DatabaseCapabilities:
    values = {
        "server_version_num": 160_004,
        "vector_version": "0.8.0",
        "background_jobs_exists": True,
        "ontology_schemas_exists": True,
        "embedding_column_type": "vector(1024)",
    }
    values.update(overrides)
    return DatabaseCapabilities(**values)  # type: ignore[arg-type]


def test_valid_database_capabilities_pass() -> None:
    validate_database_capabilities(capabilities(), expected_embedding_dimension=1024)


@pytest.mark.parametrize("version", [150_999, 120_000])
def test_postgresql_older_than_16_fails(version: int) -> None:
    with pytest.raises(StartupValidationError, match="PostgreSQL 16"):
        validate_database_capabilities(
            capabilities(server_version_num=version),
            expected_embedding_dimension=1024,
        )


def test_missing_pgvector_fails() -> None:
    with pytest.raises(StartupValidationError, match="pgvector"):
        validate_database_capabilities(
            capabilities(vector_version=None),
            expected_embedding_dimension=1024,
        )


def test_incomplete_migrations_fail() -> None:
    with pytest.raises(StartupValidationError, match="ontology_schemas"):
        validate_database_capabilities(
            capabilities(ontology_schemas_exists=False),
            expected_embedding_dimension=1024,
        )


def test_embedding_dimension_mismatch_fails() -> None:
    with pytest.raises(StartupValidationError, match="dimension mismatch"):
        validate_database_capabilities(
            capabilities(embedding_column_type="vector(768)"),
            expected_embedding_dimension=1024,
        )

