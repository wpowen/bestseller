"""Local native-row IO only; no provider, Redis, PostgreSQL or approval fixture.

Main runs these tests. The authoring worker only writes and statically checks
them. JSON/UUID/vector and advisory-lock functions are SQLite dialect adapters,
not evidence for PostgreSQL transaction/lease safety.
"""

import asyncio
import socket
import zlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from pgvector.sqlalchemy import Vector
import pytest
from sqlalchemy import JSON, BigInteger, Integer, MetaData, Uuid, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import models as m
from bestseller.settings import load_settings
from support.database_safety import require_test_engine_url


class SQLiteUUIDIO(Uuid):
    """Preserve native string/UUID identity at this fixture's DBAPI boundary."""

    def bind_processor(self, dialect):
        processor = super().bind_processor(dialect)

        def process(value):
            if self.as_uuid and isinstance(value, str):
                value = UUID(value)
            return processor(value) if processor is not None else value

        return process


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    from bestseller.services import conception, llm

    def forbidden(*args, **kwargs):
        raise AssertionError("Q4 creation boundary tests forbid model/network IO")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setattr(conception, "complete_text", forbidden)


@pytest.fixture
def creation_db(tmp_path, monkeypatch):
    from bestseller.services import web_pipeline_owner
    from bestseller.web import server

    path = tmp_path / "q4_creation_test.sqlite3"
    url = f"sqlite+aiosqlite:///{path}"
    require_test_engine_url(url)
    engine = create_async_engine(url, poolclass=NullPool)
    # Native material DTOs carry string UUIDs; SQLite's emulated UUID binder
    # expects UUID objects. Adapt only this engine, not global ORM metadata or
    # business functions, and retain strict UUID parsing/result conversion.
    engine.sync_engine.dialect.colspecs = {
        **engine.sync_engine.dialect.colspecs,
        Uuid: SQLiteUUIDIO,
    }

    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_functions(connection, record):
        connection.create_function("hashtextextended", 2, lambda value, seed: zlib.crc32(value.encode(), seed))
        connection.create_function("pg_advisory_xact_lock", 1, lambda key: None)
        connection.create_function("now", 0, lambda: datetime.now(UTC).isoformat(sep=" "))

    def create_tables(connection):
        metadata = MetaData()
        for original in m.Base.metadata.tables.values():
            table = original.to_metadata(metadata)
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                elif isinstance(column.type, PGUUID):
                    column.type = Uuid(as_uuid=True)
                elif isinstance(column.type, Vector):
                    column.type = JSON()
                elif (isinstance(column.type, BigInteger) and column.primary_key
                      and column.autoincrement is True):
                    # SQLite generates row IDs only for INTEGER PRIMARY KEY.
                    # Keep native material inserts and uniqueness constraints.
                    column.type = Integer()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
        for table in metadata.sorted_tables:
            connection.execute(CreateTable(table))

    async def initialize():
        async with engine.begin() as connection:
            await connection.run_sync(create_tables)

    asyncio.run(initialize())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = load_settings(env={})
    settings.database.url = url
    settings.output.base_dir = str(tmp_path / "output")

    @asynccontextmanager
    async def session_scope(config):
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def offline_lease_transport(config, slug):
        # Explicit IO collaborator, not an acquired-owner/production claim.
        assert slug
        yield

    monkeypatch.setattr(server, "load_settings", lambda: settings)
    monkeypatch.setattr(server, "session_scope", session_scope)
    monkeypatch.setattr(web_pipeline_owner, "web_pipeline_owner", offline_lease_transport)
    yield SimpleNamespace(engine=engine, sessions=factory, settings=settings, root=tmp_path)
    asyncio.run(engine.dispose())
