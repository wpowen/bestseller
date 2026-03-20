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


@pytest.mark.asyncio
async def test_session_scope_commits_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_engine = FakeEngine()
    fake_session = FakeSession()

    def fake_create_engine(settings=None) -> FakeEngine:
        return fake_engine

    def fake_create_session_factory(settings=None, engine=None):
        def factory() -> FakeSession:
            return fake_session

        return factory

    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)
    monkeypatch.setattr(session_module, "create_session_factory", fake_create_session_factory)

    async with session_module.session_scope():
        pass

    assert fake_session.committed is True
    assert fake_session.rolled_back is False
    assert fake_session.closed is True
    assert fake_engine.disposed is True


@pytest.mark.asyncio
async def test_session_scope_rolls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_engine = FakeEngine()
    fake_session = FakeSession()

    def fake_create_engine(settings=None) -> FakeEngine:
        return fake_engine

    def fake_create_session_factory(settings=None, engine=None):
        def factory() -> FakeSession:
            return fake_session

        return factory

    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)
    monkeypatch.setattr(session_module, "create_session_factory", fake_create_session_factory)

    with pytest.raises(RuntimeError, match="boom"):
        async with session_module.session_scope():
            raise RuntimeError("boom")

    assert fake_session.committed is False
    assert fake_session.rolled_back is True
    assert fake_session.closed is True
    assert fake_engine.disposed is True
