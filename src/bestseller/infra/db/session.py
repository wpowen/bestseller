from __future__ import annotations

from typing import AsyncIterator

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bestseller.settings import AppSettings, get_settings


# ---------------------------------------------------------------------------
# Shared engine (legacy; retained for backward compat but no longer used by
# ``session_scope``).
#
# These globals used to cache an ``AsyncEngine`` + session factory across calls
# for CLI/script efficiency, but the cache is not safe to reuse across
# different asyncio event loops. The web server (``bestseller-web-1``) is a
# synchronous ``BaseHTTPRequestHandler`` that calls ``asyncio.run(coro)`` per
# request — each call creates a fresh event loop, and asyncpg connections
# cached from the first loop blow up in subsequent loops with
# ``InterfaceError: cannot perform operation: another operation is in
# progress``. ``session_scope`` now creates and disposes a fresh engine on
# every invocation so it is loop-agnostic. Long-lived async processes (API,
# worker, scheduler) should use ``init_db`` + ``get_server_session`` instead.
# ---------------------------------------------------------------------------

_shared_engine: AsyncEngine | None = None
_shared_session_factory: async_sessionmaker[AsyncSession] | None = None


# ---------------------------------------------------------------------------
# Long-lived server engine (API server, ARQ worker)
# ---------------------------------------------------------------------------

_global_engine: AsyncEngine | None = None
_global_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db(settings: AppSettings) -> None:
    """Initialize a shared engine + session factory for long-lived server processes."""
    global _global_engine, _global_session_factory
    if _global_engine is not None:
        return  # Already initialized — prevent pool leaks from double-init
    _global_engine = create_engine(settings)
    _global_session_factory = create_session_factory(engine=_global_engine)


async def shutdown_db() -> None:
    """Dispose the shared engine on server shutdown."""
    global _global_engine, _global_session_factory
    if _global_engine is not None:
        await _global_engine.dispose()
    _global_engine = None
    _global_session_factory = None


@asynccontextmanager
async def get_server_session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession from the shared server pool (commit on success, rollback on error)."""
    if _global_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    session = _global_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def create_engine(settings: AppSettings | None = None) -> AsyncEngine:
    effective_settings = settings or get_settings()
    return create_async_engine(
        effective_settings.database.url,
        echo=effective_settings.database.echo_sql,
        pool_size=effective_settings.database.pool_size,
        max_overflow=effective_settings.database.max_overflow,
        pool_timeout=effective_settings.database.pool_timeout_seconds,
        pool_recycle=effective_settings.database.pool_recycle_seconds,
        connect_args={
            "server_settings": {
                "application_name": effective_settings.database.application_name,
                "statement_timeout": str(effective_settings.database.statement_timeout_ms),
                "lock_timeout": str(effective_settings.database.lock_timeout_ms),
            }
        },
    )


def create_session_factory(
    settings: AppSettings | None = None,
    engine: AsyncEngine | None = None,
) -> async_sessionmaker[AsyncSession]:
    effective_engine = engine or create_engine(settings)
    return async_sessionmaker(effective_engine, expire_on_commit=False)


def get_shared_engine(settings: AppSettings | None = None) -> AsyncEngine:
    global _shared_engine
    if _shared_engine is None:
        _shared_engine = create_engine(settings)
    return _shared_engine


def get_shared_session_factory(settings: AppSettings | None = None) -> async_sessionmaker[AsyncSession]:
    global _shared_session_factory
    if _shared_session_factory is None:
        _shared_session_factory = create_session_factory(engine=get_shared_engine(settings))
    return _shared_session_factory


async def dispose_shared_engine() -> None:
    global _shared_engine, _shared_session_factory
    if _shared_engine is not None:
        await _shared_engine.dispose()
        _shared_engine = None
        _shared_session_factory = None


@asynccontextmanager
async def session_scope(settings: AppSettings | None = None) -> AsyncIterator[AsyncSession]:
    """Yield a one-shot ``AsyncSession`` bound to a freshly-created engine.

    A new ``AsyncEngine`` is created on entry and disposed on exit. This
    trades a small amount of connection-setup latency (~50ms per call) for
    full isolation across event loops: the engine, its pool, and every
    asyncpg connection it creates all live and die inside the current
    ``asyncio`` event loop, so the next caller — even one running under a
    different ``asyncio.run()`` loop in the same process — starts from a
    clean slate.

    This is deliberately safe for the synchronous web server (which spawns
    a fresh event loop per HTTP request) and for CLI commands (one
    ``asyncio.run()`` per invocation). Long-lived async services (API,
    worker, scheduler) should not use this — they should keep using
    ``init_db`` + ``get_server_session`` which retains a pooled engine for
    the lifetime of the process.
    """
    engine = create_engine(settings)
    try:
        factory = create_session_factory(engine=engine)
        session = factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
    finally:
        await engine.dispose()
