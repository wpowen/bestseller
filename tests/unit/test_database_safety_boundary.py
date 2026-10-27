"""Offline behavior tests: real SQLAlchemy pools, SQLite IO, zero PostgreSQL IO."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import asyncpg
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import QueuePool

from bestseller.infra.db.session import create_engine as create_application_engine
from bestseller.settings import load_settings
from support.database_safety import (
    DATABASE_ENV,
    DatabaseSafetyError,
    load_test_database_settings,
    selected_database_url,
)

pytestmark = pytest.mark.unit
TEST_URL = "postgresql+asyncpg://fixture:secret@invalid.invalid/boundary_test"


@pytest.fixture(autouse=True)
def no_postgres_io(monkeypatch):
    attempts = []

    async def forbidden_connect(*args, **kwargs):
        attempts.append(True)
        pytest.fail("Offline safety tests must never reach the PostgreSQL driver.")

    monkeypatch.setattr(asyncpg, "connect", forbidden_connect)
    yield
    assert not attempts


@pytest.mark.parametrize(
    "name", ["bestseller", "postgres", "contest", "city", "attempt", "testimony"]
)
async def test_direct_async_factory_refuses_non_test_names_before_driver(monkeypatch, name):
    monkeypatch.setenv(DATABASE_ENV, TEST_URL)
    # A test marker in the password/host must not authorize the database.
    url = f"postgresql+asyncpg://fixture:memory_test@ci.invalid/{name}"
    engine = create_async_engine(url)
    try:
        with pytest.raises(DatabaseSafetyError):
            async with engine.begin() as connection:
                await connection.execute(text("DELETE FROM projects"))
    finally:
        await engine.dispose()


@pytest.mark.parametrize("override", ["1", "true", "yes"])
async def test_legacy_override_cannot_authorize_production(monkeypatch, override):
    monkeypatch.setenv(DATABASE_ENV, TEST_URL)
    monkeypatch.setenv("BESTSELLER_ALLOW_PROD_DB_IN_TESTS", override)
    engine = create_async_engine(TEST_URL.replace("boundary_test", "bestseller"))
    try:
        with pytest.raises(DatabaseSafetyError):
            await engine.connect()
    finally:
        await engine.dispose()


async def test_empty_env_settings_fallback_is_blocked_at_actual_engine(monkeypatch):
    monkeypatch.setenv(DATABASE_ENV, TEST_URL)
    settings = load_settings(env={})
    assert make_url(settings.database.url).database == "bestseller"
    engine = create_application_engine(settings)
    try:
        with pytest.raises(DatabaseSafetyError):
            await engine.connect()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "changed", [None, "production", "contest", "another_test", "sqlite", "host", "port"]
)
async def test_environment_change_after_engine_creation_cannot_reuse_authorization(
    monkeypatch, changed
):
    monkeypatch.setenv(DATABASE_ENV, TEST_URL)
    engine = create_async_engine(TEST_URL)
    if changed is None:
        monkeypatch.delenv(DATABASE_ENV)
    elif changed == "sqlite":
        monkeypatch.setenv(DATABASE_ENV, "sqlite+aiosqlite:///:memory:")
    elif changed in {"host", "port"}:
        replacement = {changed: "different.invalid" if changed == "host" else 6543}
        monkeypatch.setenv(
            DATABASE_ENV,
            make_url(TEST_URL).set(**replacement).render_as_string(hide_password=False),
        )
    else:
        monkeypatch.setenv(DATABASE_ENV, TEST_URL.replace("boundary_test", changed))
    try:
        with pytest.raises(DatabaseSafetyError):
            await engine.connect()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "args",
    [
        {"database": "bestseller"},
        {"database": "another_test"},
        {"dbname": "bestseller"},
        {"dsn": "postgresql://fixture:secret@invalid.invalid/bestseller"},
        {"host": "production.invalid"},
        {"port": 6543},
        {"hostaddr": "192.0.2.1"},
        {"target_session_attrs": "read-write"},
    ],
)
async def test_driver_connect_args_cannot_redirect_a_safe_engine_url(monkeypatch, args):
    monkeypatch.setenv(DATABASE_ENV, TEST_URL)
    engine = create_async_engine(TEST_URL, connect_args=args)
    try:
        with pytest.raises(DatabaseSafetyError):
            await engine.connect()
    finally:
        await engine.dispose()


def test_custom_creator_cannot_return_a_writable_unverified_connection(monkeypatch):
    monkeypatch.setenv(DATABASE_ENV, TEST_URL)
    sql = []

    def creator():
        connection = sqlite3.connect(":memory:")
        connection.set_trace_callback(sql.append)
        return connection

    # Custom creators skip SQLAlchemy's do_connect event. Even a safe engine
    # URL must not grant their returned connection authority to execute SQL.
    engine = create_engine("sqlite:///:memory:", creator=creator)
    engine.url = make_url(TEST_URL)
    try:
        with pytest.raises(DatabaseSafetyError, match="validated test database routing"):
            with engine.begin() as connection:
                connection.exec_driver_sql("DELETE FROM projects")
        assert not sql
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "query",
    [
        "database=bestseller",
        "dbname=bestseller",
        "service=test",
        "host=production.invalid",
        "port=6543",
        "hostaddr=192.0.2.1",
        "target_session_attrs=read-write",
    ],
)
async def test_url_query_cannot_redirect_database(monkeypatch, query):
    monkeypatch.setenv(DATABASE_ENV, TEST_URL)
    engine = create_async_engine(f"{TEST_URL}?{query}")
    try:
        with pytest.raises(DatabaseSafetyError):
            await engine.connect()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("routing", [{"host": "production.invalid"}, {"port": 6543}])
async def test_engine_url_must_match_selected_server_even_with_same_database(monkeypatch, routing):
    monkeypatch.setenv(DATABASE_ENV, TEST_URL)
    engine = create_async_engine(make_url(TEST_URL).set(**routing))
    try:
        with pytest.raises(DatabaseSafetyError):
            await engine.connect()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("name", ["bestseller_test", "ci_books", "books-tmp-42", "test"])
def test_explicit_test_names_resolve_without_connections(monkeypatch, name):
    monkeypatch.setenv(DATABASE_ENV, TEST_URL.replace("boundary_test", name))
    settings = load_test_database_settings()
    assert make_url(settings.database.url).database == name


def test_settings_keep_in_memory_sqlite_url_a_string(monkeypatch):
    monkeypatch.setenv(DATABASE_ENV, "sqlite+aiosqlite:///:memory:")
    assert load_test_database_settings().database.url == "sqlite+aiosqlite:///:memory:"


@pytest.mark.parametrize("value", [None, "garbage:secret", "postgresql://u:secret@host/contest"])
def test_configuration_is_explicit_and_fail_closed_without_secret_leaks(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(DATABASE_ENV)
    else:
        monkeypatch.setenv(DATABASE_ENV, value)
    with pytest.raises(DatabaseSafetyError) as caught:
        selected_database_url()
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("url", ["sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite://", "file"])
async def test_explicit_sqlite_allows_real_offline_transactions(tmp_path, url):
    if url == "file":
        url = f"sqlite+aiosqlite:///{tmp_path / 'boundary_test.sqlite3'}"
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE proof (value INTEGER)"))
            await connection.execute(text("INSERT INTO proof VALUES (7)"))
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT value FROM proof")) == 7
    finally:
        await engine.dispose()


@pytest.fixture
def outside_pytest_root(tmp_path_factory):
    root = tmp_path_factory.getbasetemp().resolve()
    # Deliberately resemble pytest's name and share its path prefix, while
    # remaining outside this session's actual managed directory.
    with TemporaryDirectory(prefix=f"{root.name}-pytest-decoy-", dir=root.parent) as directory:
        path = Path(directory).resolve()
        assert not path.is_relative_to(root)
        yield path


@pytest.mark.parametrize("name", ["closure.sqlite", "knowledge.db", "contest.sqlite3"])
async def test_unmarked_sqlite_files_in_pytest_basetemp_are_writable(
    tmp_path, tmp_path_factory, name
):
    path = tmp_path / name
    assert path.resolve().is_relative_to(tmp_path_factory.getbasetemp().resolve())
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("CREATE TABLE proof (value INTEGER)")
            await connection.exec_driver_sql("INSERT INTO proof VALUES (7)")
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT value FROM proof")) == 7
        assert path.is_file()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("name", ["production.sqlite3", "contest.sqlite3", "memory.sqlite3"])
async def test_ordinary_sqlite_files_outside_pytest_root_are_not_safe(outside_pytest_root, name):
    path = outside_pytest_root / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        with pytest.raises(DatabaseSafetyError):
            await engine.connect()
        assert not path.exists()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("escape", ["parent_traversal", "file_symlink", "directory_symlink"])
async def test_sqlite_managed_path_cannot_escape_by_resolution(
    tmp_path, tmp_path_factory, outside_pytest_root, escape
):
    target = outside_pytest_root / "knowledge.db"
    if escape == "parent_traversal":
        path = tmp_path_factory.getbasetemp() / ".." / outside_pytest_root.name / target.name
    elif escape == "file_symlink":
        path = tmp_path / "knowledge.db"
        path.symlink_to(target)
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(outside_pytest_root, target_is_directory=True)
        path = alias / target.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        with pytest.raises(DatabaseSafetyError):
            await engine.connect()
        assert not target.exists()
    finally:
        await engine.dispose()


async def test_test_named_symlink_does_not_authorize_ordinary_external_file(
    tmp_path, outside_pytest_root
):
    target = outside_pytest_root / "production.db"
    alias = tmp_path / "boundary_test.db"
    alias.symlink_to(target)
    engine = create_async_engine(f"sqlite+aiosqlite:///{alias}")
    try:
        with pytest.raises(DatabaseSafetyError):
            await engine.connect()
        assert not target.exists()
    finally:
        await engine.dispose()


async def test_relative_and_internal_symlink_sqlite_paths_still_work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "alias.db").symlink_to(tmp_path / "knowledge.db")
    engine = create_async_engine("sqlite+aiosqlite:///alias.db")
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("CREATE TABLE proof (value INTEGER)")
        assert (tmp_path / "knowledge.db").is_file()
    finally:
        await engine.dispose()


def test_driver_path_is_checked_even_when_engine_url_is_memory(outside_pytest_root, monkeypatch):
    path = outside_pytest_root / "production.db"
    # A custom dialect argument translator can disagree with its Engine URL.
    # Exercise do_connect, bypassing only the earlier URL check, without IO.
    monkeypatch.setattr(
        SQLiteDialect_pysqlite,
        "create_connect_args",
        lambda _self, _url: ([str(path)], {}),
    )
    engine = create_engine("sqlite:///:memory:")
    try:
        with pytest.raises(DatabaseSafetyError):
            engine.connect()
        assert not path.exists()
    finally:
        engine.dispose()


def test_explicit_custom_basetemp_is_managed_without_name_markers(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env[DATABASE_ENV] = "sqlite+aiosqlite://"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p", "pytest_cov.plugin",
            "-p", "pytest_asyncio.plugin",
            "--no-cov",
            "-p",
            "no:cacheprovider",
            "-q",
            "--basetemp",
            str(tmp_path / "scratch"),
            f"{__file__}::test_unmarked_sqlite_files_in_pytest_basetemp_are_writable",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout


@pytest.fixture
def postgres_simulator(monkeypatch):
    """Exercise real pool events/query/transactions using only SQLite memory.

    The PostgreSQL routing seam supplies named driver arguments; SQLite's
    current_database UDF acts as the remote identity response. Safety listeners
    themselves are never mocked, called directly or removed.
    """
    monkeypatch.setenv(DATABASE_ENV, TEST_URL)
    state = SimpleNamespace(identity="boundary_test", sql=[], connects=0, fail_probe=False)

    def connect_offline(**kwargs):
        assert kwargs["database"] == "boundary_test"
        state.connects += 1
        connection = sqlite3.connect(":memory:")
        connection.set_trace_callback(state.sql.append)

        def current_database():
            if state.fail_probe:
                raise RuntimeError("driver-secret-do-not-disclose")
            return state.identity

        connection.create_function("current_database", 0, current_database)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteDialect_pysqlite,
            "create_connect_args",
            lambda _self, _url: (
                [],
                {"database": "boundary_test", "host": "invalid.invalid", "port": 5432},
            ),
        )
        engine = create_engine("sqlite:///:memory:", poolclass=QueuePool)
    engine.url = make_url(TEST_URL)
    engine.dialect.name = "postgresql"
    monkeypatch.setattr(engine.dialect, "connect", connect_offline)
    yield engine, state
    engine.dispose()


@pytest.mark.parametrize("identity", ["bestseller", "contest", "other_test", None])
def test_current_database_mismatch_blocks_before_connect_handlers_or_writes(
    postgres_simulator, identity
):
    engine, state = postgres_simulator
    state.identity = identity
    callbacks = []
    event.listen(engine, "connect", lambda *_args: callbacks.append(True))
    with pytest.raises(DatabaseSafetyError, match=r"current_database\(\)"):
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE must_not_exist (id INTEGER)")
    assert state.connects == 1
    assert state.sql == ["SELECT current_database()"]
    assert not callbacks


def test_verified_connection_leaves_explicit_transaction_and_rollback_intact(postgres_simulator):
    engine, state = postgres_simulator
    with engine.connect() as connection:
        assert not connection.in_transaction()
        with connection.begin():
            connection.exec_driver_sql("CREATE TABLE proof (value INTEGER)")
        transaction = connection.begin()
        connection.exec_driver_sql("INSERT INTO proof VALUES (9)")
        transaction.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM proof")) == 0
    assert state.connects == 1  # reuse actually exercised
    assert state.sql.count("SELECT current_database()") >= 2


def test_identity_is_rechecked_on_pool_reuse_before_delete(postgres_simulator):
    engine, state = postgres_simulator
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE proof (value INTEGER)")
        connection.exec_driver_sql("INSERT INTO proof VALUES (9)")
    state.identity = "bestseller"
    state.sql.clear()
    with pytest.raises(DatabaseSafetyError):
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM proof")
    assert state.connects == 1
    assert state.sql == ["SELECT current_database()"]


def test_reconnect_does_not_inherit_previous_connection_identity(postgres_simulator):
    engine, state = postgres_simulator
    with engine.connect():
        pass
    engine.dispose()
    state.identity = "other_test"
    state.sql.clear()
    with pytest.raises(DatabaseSafetyError):
        engine.connect()
    assert state.connects == 2
    assert state.sql == ["SELECT current_database()"]


@pytest.mark.parametrize("change", ["database", "host", "port"])
def test_raw_pool_checkout_cannot_reuse_stale_environment_authority(
    postgres_simulator, monkeypatch, change
):
    engine, state = postgres_simulator
    with engine.connect():
        pass
    replacement = {
        change: {"database": "bestseller", "host": "other.invalid", "port": 6543}[change]
    }
    monkeypatch.setenv(
        DATABASE_ENV, make_url(TEST_URL).set(**replacement).render_as_string(hide_password=False)
    )
    state.sql.clear()
    with pytest.raises(DatabaseSafetyError):
        engine.pool.connect()
    assert not state.sql


def test_probe_failure_is_fail_closed_and_redacted(postgres_simulator):
    engine, state = postgres_simulator
    state.fail_probe = True
    with pytest.raises(DatabaseSafetyError) as caught:
        engine.raw_connection()
    assert "driver-secret" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert state.sql == ["SELECT current_database()"]


@pytest.mark.parametrize(
    "selected", [None, "postgresql://user:startup-secret@invalid.invalid/bestseller"]
)
def test_pytest_startup_rejects_unsafe_selection_even_with_override(selected):
    root = Path(__file__).resolve().parents[2]
    env = {key: value for key, value in os.environ.items() if not key.startswith("BESTSELLER__")}
    env["BESTSELLER_ALLOW_PROD_DB_IN_TESTS"] = "1"
    if selected is not None:
        env[DATABASE_ENV] = selected
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--no-cov", "--collect-only", "-q", str(__file__)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 4
    assert "ERROR:" in result.stderr
    assert "startup-secret" not in result.stdout + result.stderr
