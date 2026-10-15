from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from tc_backend.db import models  # noqa: F401
from tc_backend.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    url = os.getenv("TC_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("TC_DATABASE_URL is required to run migrations")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("Alembic migrations require a PostgreSQL database URL")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

