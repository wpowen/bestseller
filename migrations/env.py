from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from bestseller.infra.db import models as _models  # noqa: F401
from bestseller.infra.db.base import Base
from bestseller.settings import load_settings


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _to_sync_database_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    return url


settings = load_settings()
config.set_main_option("sqlalchemy.url", _to_sync_database_url(settings.database.url))
target_metadata = Base.metadata


def _ensure_alembic_version_capacity(connection) -> None:
    """Alembic's default version_num VARCHAR(32) is too short for this repo."""

    if connection.dialect.name != "postgresql":
        return
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS alembic_version ("
            "version_num VARCHAR(128) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
            ")"
        )
    )
    connection.execute(
        text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)")
    )


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _ensure_alembic_version_capacity(connection)
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
