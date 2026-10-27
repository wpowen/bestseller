"""Pytest-only connection guards; never imported by production engine code.

Names must contain a *delimited* test/tmp/ci token, or SQLite files must resolve
inside the current pytest session's managed basetemp. A URL check alone cannot
prove where a proxy or connect_args actually sends us, so DBAPI connections are
also checked before use and on every pool checkout. No production override is
supported. Errors deliberately omit URLs, driver errors and credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
import json
import os
from pathlib import Path
import re
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import URL, Engine, make_url

DATABASE_ENV = "BESTSELLER__DATABASE__URL"
_PYTEST_BASETEMP: Path | None = None
_PROVENANCE = "bestseller_test_database_target"
_TOKEN = re.compile(r"(?:^|[_-])(?:test|tmp|ci)(?:$|[_-])", re.IGNORECASE)
_REDIRECT_KEYS = frozenset(
    {
        "database",
        "dbname",
        "dsn",
        "service",
        "async_creator_fn",
        "host",
        "hostaddr",
        "port",
        "target_session_attrs",
    }
)


class DatabaseSafetyError(RuntimeError):
    """A test attempted to acquire an unverified database connection."""


def _test_name(name: object) -> bool:
    return isinstance(name, str) and bool(_TOKEN.search(name))


def _test_sqlite_file(name: object) -> bool:
    if not isinstance(name, str) or not name:
        return False
    try:
        # Resolve both parent and file symlinks, including not-yet-created DBs.
        # A sibling/prefix match, arbitrary /tmp path or "pytest" in a path
        # grants no authority. Only pytest's actual session root does.
        path = Path(name).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if _PYTEST_BASETEMP is not None and path != _PYTEST_BASETEMP:
        if path.is_relative_to(_PYTEST_BASETEMP):
            return True
    return _test_name(path.stem)


def _parse_test_url(value: str | URL) -> URL:
    try:
        url = make_url(value)
        backend = url.get_backend_name()
        if backend == "postgresql":
            valid = (
                bool(url.host)
                and (url.port is None or 0 < url.port < 65536)
                and _test_name(url.database)
                and not (_REDIRECT_KEYS & url.query.keys())
            )
        elif backend == "sqlite":
            # In-memory, explicitly test-named or pytest-managed files only.
            # "memory" in a hostname/password is not permission.
            valid = not url.query and (
                url.database in {None, "", ":memory:"} or _test_sqlite_file(url.database)
            )
        else:
            valid = False
    except Exception:
        raise DatabaseSafetyError("Invalid test database URL (details redacted).") from None
    if not valid:
        raise DatabaseSafetyError("Refusing a database URL without an explicit test target.")
    return url


def selected_database_url() -> URL:
    """Read each time: neither defaults, dotenv nor a startup cache authorize IO."""
    value = os.environ.get(DATABASE_ENV)
    if not value:
        raise DatabaseSafetyError(f"Set {DATABASE_ENV} explicitly to an isolated test database.")
    return _parse_test_url(value)


def _postgres_target(url: URL) -> tuple[str | None, int, str | None]:
    return url.host, url.port or 5432, url.database


def require_test_engine_url(value: str | URL) -> URL:
    url = _parse_test_url(value)
    if url.get_backend_name() == "postgresql":
        selected = selected_database_url()
        if selected.get_backend_name() != "postgresql" or _postgres_target(url) != _postgres_target(
            selected
        ):
            raise DatabaseSafetyError("Engine target differs from the explicit test selection.")
    return url


def load_test_database_settings():
    """Keep the test DB explicit even when local configuration contains a live URL."""
    from bestseller.settings import load_settings

    selected_database_url()
    # Settings interprets env values as YAML. Quote the URL as a scalar so
    # :memory: cannot be mistaken for a mapping; the engine still gets a str.
    settings = load_settings(env={DATABASE_ENV: json.dumps(os.environ[DATABASE_ENV])})
    require_test_engine_url(settings.database.url)
    return settings


def _before_driver_connect(dialect, connection_record, cargs, cparams) -> None:
    if dialect.name == "postgresql":
        selected = selected_database_url()
        # A positional DSN, service or custom async creator can silently replace
        # database=. Refuse such opaque routing instead of trying to infer it.
        if cargs or any(
            key in cparams
            for key in ("dsn", "service", "async_creator_fn", "hostaddr", "target_session_attrs")
        ):
            raise DatabaseSafetyError("Opaque database connection routing is not allowed in tests.")
        names = [cparams[key] for key in ("database", "dbname") if key in cparams]
        if (
            selected.get_backend_name() != "postgresql"
            or not names
            or any(not _test_name(name) or name != selected.database for name in names)
            or cparams.get("host") != selected.host
            or cparams.get("port", 5432) != (selected.port or 5432)
        ):
            raise DatabaseSafetyError("Driver target differs from the explicit test selection.")
        target = ("postgresql", _postgres_target(selected))
    elif dialect.name == "sqlite":
        name = cargs[0] if len(cargs) == 1 else cparams.get("database")
        if name != ":memory:" and not _test_sqlite_file(name):
            raise DatabaseSafetyError("SQLite driver requires a verified test file or memory DB.")
        target = ("sqlite", name)
    else:
        raise DatabaseSafetyError("Unverified database dialect in tests.")
    connection_record.info[_PROVENANCE] = target


def _verify_connection(dbapi_connection, connection_record, *_args) -> None:
    target = connection_record.info.get(_PROVENANCE)
    if target is None:
        raise DatabaseSafetyError("Connection lacks validated test database routing.")
    backend, expected = target
    if backend == "sqlite":
        return
    selected = selected_database_url()
    if selected.get_backend_name() != "postgresql" or _postgres_target(selected) != expected:
        raise DatabaseSafetyError("Test database selection changed before connection checkout.")
    try:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SELECT current_database()")
            row = cursor.fetchone()
        finally:
            cursor.close()
        # Do not leak the probe's transaction into the test's explicit begin().
        dbapi_connection.rollback()
    except Exception:
        raise DatabaseSafetyError("Cannot verify current_database() (details redacted).") from None
    if not row or not _test_name(row[0]) or row[0] != expected[2]:
        raise DatabaseSafetyError("Connected current_database() differs from the test target.")


def install_database_safety(*, config=None) -> Callable[[], None]:
    """Install for one pytest session, including previously imported factories.

    Engine.raw_connection is the pool entry used by sync and async engines;
    wrapping it checks the actual engine URL *before* a driver can connect.
    Class-level events cover direct factories, raw pool checkouts and newly
    reconnected DBAPI handles. File authorization comes only from the actual
    config._tmp_path_factory, never from an environment variable or directory
    naming convention. All hooks and root authorization are removed at cleanup.
    """
    global _PYTEST_BASETEMP
    previous_basetemp = _PYTEST_BASETEMP
    factory = getattr(config, "_tmp_path_factory", None)
    _PYTEST_BASETEMP = factory.getbasetemp().resolve(strict=True) if factory is not None else None
    original = Engine.raw_connection

    @wraps(original)
    def guarded_raw_connection(engine: Engine) -> Any:
        require_test_engine_url(engine.url)
        return original(engine)

    Engine.raw_connection = guarded_raw_connection
    event.listen(Engine, "do_connect", _before_driver_connect)
    event.listen(Engine, "connect", _verify_connection, insert=True)
    event.listen(Engine, "checkout", _verify_connection, insert=True)

    def uninstall() -> None:
        global _PYTEST_BASETEMP
        event.remove(Engine, "checkout", _verify_connection)
        event.remove(Engine, "connect", _verify_connection)
        event.remove(Engine, "do_connect", _before_driver_connect)
        Engine.raw_connection = original
        _PYTEST_BASETEMP = previous_basetemp

    return uninstall
