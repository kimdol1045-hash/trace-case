"""Safe startup validation for database and ontology capabilities."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import Connection, create_engine, text

from tc_backend.config import Settings, load_settings

MIN_POSTGRES_VERSION_NUM = 160_000


class StartupValidationError(RuntimeError):
    """Raised when deployment prerequisites do not match the application."""


@dataclass(frozen=True, slots=True)
class DatabaseCapabilities:
    server_version_num: int
    vector_version: str | None
    background_jobs_exists: bool
    ontology_schemas_exists: bool
    embedding_column_type: str | None


def probe_database_capabilities(connection: Connection) -> DatabaseCapabilities:
    server_version_num = int(
        connection.execute(text("SHOW server_version_num")).scalar_one()
    )
    vector_version = connection.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar_one_or_none()
    tables = connection.execute(
        text(
            "SELECT to_regclass('public.background_jobs') IS NOT NULL, "
            "to_regclass('public.ontology_schemas') IS NOT NULL"
        )
    ).one()
    embedding_column_type = connection.execute(
        text(
            "SELECT format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute AS a "
            "JOIN pg_class AS c ON c.oid = a.attrelid "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' "
            "AND c.relname = 'knowledge_embeddings' "
            "AND a.attname = 'embedding' "
            "AND NOT a.attisdropped"
        )
    ).scalar_one_or_none()
    return DatabaseCapabilities(
        server_version_num=server_version_num,
        vector_version=vector_version,
        background_jobs_exists=bool(tables[0]),
        ontology_schemas_exists=bool(tables[1]),
        embedding_column_type=embedding_column_type,
    )


def validate_database_capabilities(
    capabilities: DatabaseCapabilities,
    *,
    expected_embedding_dimension: int,
) -> None:
    if capabilities.server_version_num < MIN_POSTGRES_VERSION_NUM:
        raise StartupValidationError(
            "PostgreSQL 16 or newer is required; "
            f"server_version_num={capabilities.server_version_num}"
        )
    if capabilities.vector_version is None:
        raise StartupValidationError(
            "pgvector extension is missing; apply the Alembic baseline migration"
        )
    missing_tables = [
        name
        for name, exists in (
            ("background_jobs", capabilities.background_jobs_exists),
            ("ontology_schemas", capabilities.ontology_schemas_exists),
        )
        if not exists
    ]
    if missing_tables:
        raise StartupValidationError(
            "database migrations are incomplete; missing: " + ", ".join(missing_tables)
        )
    match = re.fullmatch(r"vector\((\d+)\)", capabilities.embedding_column_type or "")
    actual_dimension = int(match.group(1)) if match else None
    if actual_dimension != expected_embedding_dimension:
        raise StartupValidationError(
            "embedding column dimension mismatch: "
            f"expected vector({expected_embedding_dimension}), "
            f"found {capabilities.embedding_column_type or 'missing'}"
        )


def validate_startup(settings: Settings) -> DatabaseCapabilities:
    engine = create_engine(settings.sqlalchemy_database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            capabilities = probe_database_capabilities(connection)
        validate_database_capabilities(
            capabilities,
            expected_embedding_dimension=settings.embedding_dimension,
        )
        return capabilities
    finally:
        engine.dispose()


def main() -> None:
    settings = load_settings()
    capabilities = validate_startup(settings)
    print(
        "startup validation passed: "
        f"postgres={capabilities.server_version_num}, "
        f"pgvector={capabilities.vector_version}, "
        f"embedding={capabilities.embedding_column_type}"
    )


if __name__ == "__main__":
    main()

