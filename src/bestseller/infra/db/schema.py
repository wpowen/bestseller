from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateIndex, CreateTable

from bestseller.infra.db import models as _models  # noqa: F401
from bestseller.infra.db.base import Base


POSTGRES_EXTENSION_SQL = (
    "CREATE EXTENSION IF NOT EXISTS pgcrypto;",
    "CREATE EXTENSION IF NOT EXISTS vector;",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
)


async def initialize_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for statement in POSTGRES_EXTENSION_SQL:
            await connection.execute(text(statement))
        await connection.run_sync(Base.metadata.create_all)


def render_schema_statements(*, include_extensions: bool = True) -> list[str]:
    dialect = postgresql.dialect()
    statements: list[str] = []
    if include_extensions:
        statements.extend(POSTGRES_EXTENSION_SQL)
    for table in Base.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)))
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            statements.append(str(CreateIndex(index).compile(dialect=dialect)))
    return statements


def render_schema_sql() -> str:
    return ";\n\n".join(render_schema_statements()).strip() + ";"
