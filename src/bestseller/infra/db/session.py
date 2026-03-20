from __future__ import annotations

from typing import AsyncIterator

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bestseller.settings import AppSettings, get_settings


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


@asynccontextmanager
async def session_scope(settings: AppSettings | None = None) -> AsyncIterator[AsyncSession]:
    engine = create_engine(settings)
    session_factory = create_session_factory(engine=engine)
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
        await engine.dispose()
