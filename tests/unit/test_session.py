from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.infra.db import session as session_module
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def close(self) -> None:
        self.closed = True


def build_settings():
    return load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )


@pytest.mark.asyncio
async def test_create_engine_uses_database_url() -> None:
    engine = session_module.create_engine(build_settings())

    try:
        assert (
            engine.sync_engine.url.render_as_string(hide_password=False)
            == "postgresql+asyncpg://bestseller:bestseller@localhost:5432/bestseller"
        )
    finally:
        engine.sync_engine.dispose()


def _patch_session_scope(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: "FakeSession",
    fake_engine: "FakeEngine | None" = None,
) -> "FakeEngine":
    """Wire ``session_scope`` to use a fake engine + session.

    ``session_scope`` is now a one-shot context manager (fresh engine per
    call). To intercept it without needing a real database we replace
    ``create_engine`` so it returns a fake engine, and
    ``create_session_factory`` so it returns a factory that yields the fake
    session. Returns the fake engine so callers can assert ``disposed``.
    """
    engine = fake_engine or FakeEngine()

    def fake_create_engine(settings=None):
        return engine

    def fake_create_session_factory(settings=None, engine=None):
        def factory() -> FakeSession:
            return fake_session

        return factory

    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)
    monkeypatch.setattr(session_module, "create_session_factory", fake_create_session_factory)
    return engine


@pytest.mark.asyncio
async def test_session_scope_commits_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = FakeSession()
    fake_engine = _patch_session_scope(monkeypatch, fake_session)

    async with session_module.session_scope():
        pass

    assert fake_session.committed is True
    assert fake_session.rolled_back is False
    assert fake_session.closed is True
    # Engine must be disposed on exit so the next ``session_scope`` call
    # (potentially under a different event loop) starts fresh.
    assert fake_engine.disposed is True


@pytest.mark.asyncio
async def test_session_scope_rolls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = FakeSession()
    fake_engine = _patch_session_scope(monkeypatch, fake_session)

    with pytest.raises(RuntimeError, match="boom"):
        async with session_module.session_scope():
            raise RuntimeError("boom")

    assert fake_session.committed is False
    assert fake_session.rolled_back is True
    assert fake_session.closed is True
    # Engine must STILL be disposed when the body raises — otherwise the
    # cross-loop bug returns the moment a request errors out.
    assert fake_engine.disposed is True


@pytest.mark.asyncio
async def test_session_scope_creates_fresh_engine_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the cross-event-loop ``InterfaceError`` that
    crashed every web-server request after the first.

    The synchronous web server calls ``asyncio.run(coro)`` per HTTP request,
    creating a brand-new event loop each time. If ``session_scope`` were to
    reuse a cached ``AsyncEngine`` across loops, asyncpg connections from
    the previous (now-dead) loop would blow up the next request with
    ``cannot perform operation: another operation is in progress``. This
    test pins the contract: each ``session_scope`` invocation must build
    AND dispose its own engine.
    """
    engines: list[FakeEngine] = []

    def fake_create_engine(settings=None):
        eng = FakeEngine()
        engines.append(eng)
        return eng

    def fake_create_session_factory(settings=None, engine=None):
        def factory() -> FakeSession:
            return FakeSession()

        return factory

    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)
    monkeypatch.setattr(session_module, "create_session_factory", fake_create_session_factory)

    async with session_module.session_scope():
        pass
    async with session_module.session_scope():
        pass
    async with session_module.session_scope():
        pass

    assert len(engines) == 3, "session_scope must create a new engine per call"
    assert all(e.disposed for e in engines), "every engine must be disposed on exit"


@pytest.mark.asyncio
async def test_dispose_shared_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_engine = FakeEngine()
    monkeypatch.setattr(session_module, "_shared_engine", fake_engine)
    monkeypatch.setattr(session_module, "_shared_session_factory", object())

    await session_module.dispose_shared_engine()

    assert fake_engine.disposed is True
    assert session_module._shared_engine is None
    assert session_module._shared_session_factory is None
