from __future__ import annotations

import asyncio
from typing import AsyncIterator

from contextlib import asynccontextmanager
import logging

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


async def _save_rewrite_rejection_after_rollback(factory, error: BaseException) -> None:
    """Persist only detached rejection evidence after all caller writes rolled back."""
    if not hasattr(error, "source_identity") or not hasattr(error, "receipt"):
        return
    from bestseller.services.reviews import RewriteSourceRejected, persist_rewrite_rejection
    if not isinstance(error, RewriteSourceRejected):
        return
    try:
        async with factory() as recovery:
            saved = await persist_rewrite_rejection(recovery, error)
            if saved:
                await recovery.commit()
            else:
                await recovery.rollback()
                logging.getLogger(__name__).error("Rewrite rejection has no recoverable persisted task.")
    except Exception as recovery_error:
        logging.getLogger(__name__).error(
            "Rewrite rejection evidence persistence failed (%s).", type(recovery_error).__name__
        )


async def _save_workflow_failures_after_rollback(factory, error: BaseException) -> None:
    receipts=getattr(error,"workflow_failures",())
    if not receipts:
        return
    from uuid import UUID
    from sqlalchemy import select
    from bestseller.infra.db.models import WorkflowRunModel,ProjectModel
    from bestseller.services.progress_context import TaskCancelledError
    try:
        async with factory() as recovery:
            for receipt in receipts:
                cancellation_code = ("operator_halted" if isinstance(error, TaskCancelledError)
                                     else "workflow_interrupted")
                if isinstance(error, asyncio.CancelledError) and receipt.get("code") == "execution_cancelled":
                    # Mixed-version detached receipts must not turn operator
                    # control flow into a permanent project execution latch.
                    receipt = {**receipt, "original_code": receipt["code"],
                               "code": cancellation_code, "recoverable": True}
                row=await recovery.scalar(select(WorkflowRunModel).where(
                    WorkflowRunModel.id==UUID(receipt["workflow_id"])).with_for_update())
                if row is None or str(row.project_id or "")!=receipt["project_id"] or row.workflow_type!=receipt["workflow_type"]:
                    continue
                history=list((row.metadata_json or {}).get("execution_failure_receipts") or [])
                if receipt not in history:
                    history.append(receipt)
                row.metadata_json={**(row.metadata_json or {}),"execution_failure_receipts":history}
                changed_active_status = row.status in {"pending","queued","running"}
                cancelled = (isinstance(error, asyncio.CancelledError)
                    and receipt.get("recoverable") is True and receipt["code"] == cancellation_code)
                recoverable = cancelled or (receipt.get("recoverable") is True
                    and receipt["code"] == "workflow_failed")
                if changed_active_status:
                    row.status="cancelled" if cancelled else "failed"
                    row.current_step=(receipt.get("step") or row.current_step) if recoverable else receipt["code"]
                    row.error_message=receipt["code"]
                if (row.project_id and changed_active_status and not recoverable
                    and receipt["code"] != "rewrite_source_rejected"):
                    project=await recovery.get(ProjectModel,row.project_id)
                    if project is not None:
                        project.metadata_json={**(project.metadata_json or {}),"execution_terminal":{
                            "terminal":True,"code":receipt["code"],"workflow_id":receipt["workflow_id"],
                        }}
                        project.status="paused"
            await recovery.commit()
    except Exception as recovery_error:
        logging.getLogger(__name__).error("Workflow terminal receipt persistence failed (%s).",type(recovery_error).__name__)


async def _rollback_and_save_failures(factory, session, error: BaseException) -> None:
    """Finish rollback and detached recovery before propagating the original error.

    A second task.cancel() must not interrupt rollback or let a recovery session
    race the unrolled-back transaction. No caller data is committed here.
    """
    async def cleanup():
        await session.rollback()
        await _save_rewrite_rejection_after_rollback(factory, error)
        await _save_workflow_failures_after_rollback(factory, error)

    task = asyncio.create_task(cleanup())
    try:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
    except BaseException as cleanup_error:
        # Recovery failure is diagnostic; it must not replace the task's cause.
        logging.getLogger(__name__).error(
            "Session rollback/recovery failed (%s).", type(cleanup_error).__name__
        )


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
    failure = None
    try:
        yield session
        await session.commit()
    except BaseException as error:
        failure = error
        await _rollback_and_save_failures(_global_session_factory, session, error)
        raise
    finally:
        try:
            await session.close()
        except BaseException:
            if failure is None:
                raise
            logging.getLogger(__name__).error("Session close failed during error propagation.")


def create_engine(settings: AppSettings | None = None) -> AsyncEngine:
    effective_settings = settings or get_settings()
    return create_async_engine(
        effective_settings.database.url,
        echo=effective_settings.database.echo_sql,
        pool_size=effective_settings.database.pool_size,
        max_overflow=effective_settings.database.max_overflow,
        pool_timeout=effective_settings.database.pool_timeout_seconds,
        pool_recycle=effective_settings.database.pool_recycle_seconds,
        pool_pre_ping=True,  # Validate connections before use — critical for
        # long-running LLM calls (10+ min) that leave DB connections idle.
        connect_args={
            "server_settings": {
                "application_name": effective_settings.database.application_name,
                "statement_timeout": str(effective_settings.database.statement_timeout_ms),
                "lock_timeout": str(effective_settings.database.lock_timeout_ms),
                # Allow idle transactions for up to 30 min (planner LLM calls
                # can take 10+ min, during which the session holds a transaction).
                "idle_in_transaction_session_timeout": "1800000",
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
    failure = None
    try:
        factory = create_session_factory(engine=engine)
        session = factory()
        try:
            yield session
            await session.commit()
        except BaseException as error:
            failure = error
            await _rollback_and_save_failures(factory, session, error)
            raise
        finally:
            try:
                await session.close()
            except BaseException:
                if failure is None:
                    raise
                logging.getLogger(__name__).error("Session close failed during error propagation.")
    finally:
        try:
            await engine.dispose()
        except BaseException:
            if failure is None:
                raise
            logging.getLogger(__name__).error("Engine disposal failed during error propagation.")
