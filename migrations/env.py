from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from bestseller.infra.db import models as _models  # noqa: F401
from bestseller.infra.db.base import Base
from bestseller.infra.db.migration_bootstrap import baseline_to_head, database_is_greenfield
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

        # Greenfield databases cannot replay the chain: migration 0001 renders
        # the entire current metadata, which collides with later create_table
        # migrations. Build the schema directly and stamp head instead.
        if database_is_greenfield(connection):
            from alembic.script import ScriptDirectory

            head_revision = ScriptDirectory.from_config(config).get_current_head()
            baseline_to_head(connection, head_revision=head_revision)
            connection.commit()
            return

        # Commit the preliminary work (alembic_version capacity DDL + the
        # greenfield probe). Those statements autobegin a transaction on the
        # connection; if it is left open, alembic's own begin_transaction()
        # nests inside it and the migration + version bump are never committed
        # (the upgrade "runs" in the log but nothing persists).
        connection.commit()

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
