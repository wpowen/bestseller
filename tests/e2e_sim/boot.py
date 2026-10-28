"""Container-only native process launcher; simulation hooks never enter src/."""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

try:
    from .diagnostics import safe_exception
except ImportError:  # Direct script execution without importing transport dependencies.
    from diagnostics import safe_exception


class SafeFailureHandler(logging.Handler):
    """Persist actual native exception frames without formatting a LogRecord."""

    def __init__(self, evidence: Path, role: str):
        super().__init__()
        self.evidence = evidence
        self.role = role

    def save(self, error, **metadata):
        failure = {**safe_exception(error), "process_role": self.role, **metadata}
        self.evidence.mkdir(parents=True, exist_ok=True)
        with (self.evidence / f"failures-{self.role}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(failure, sort_keys=True) + "\n")
        return failure

    def emit(self, record):
        if not record.exc_info or record.exc_info[1] is None:
            return
        error = record.exc_info[1]
        previous = error.__traceback__
        try:
            error.__traceback__ = record.exc_info[2]
            self.save(error, logger=record.name, level=record.levelname, phase="native_logger")
        finally:
            error.__traceback__ = previous


@contextmanager
def safe_logging(handler):
    # Worker basicConfig observes this handler; uvicorn must use log_config=None.
    # Previously configured child handlers could otherwise format exception text.
    root = logging.getLogger()
    saved = [(root, root.handlers[:], root.level, root.propagate)]
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):
            saved.append((logger, logger.handlers[:], logger.level, logger.propagate))
            logger.handlers = []
            logger.setLevel(logging.NOTSET)
            logger.propagate = True
    root.handlers = [handler]
    root.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        for logger, handlers, level, propagate in saved:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
        handler.close()


def verify_database():
    import psycopg

    expected = os.environ["E2E_SIM_DATABASE"]
    if not re.fullmatch(r"par_P3_[a-f0-9]{8,32}_test_db", expected):
        raise RuntimeError("explicit isolated test database name required")
    url = os.environ["BESTSELLER__DATABASE__URL"].replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    parsed = urlsplit(url)
    if (
        parsed.path != "/" + expected
        or parsed.hostname != os.environ["E2E_SIM_PG_IP"]
        or parsed.port != 5432
    ):
        raise RuntimeError("database target does not match isolated endpoint")
    # This check is read-only even before running Alembic's native migrations.
    with psycopg.connect(
        url, options="-c default_transaction_read_only=on", connect_timeout=10
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_setting('server_version_num'), "
                "host(inet_server_addr())"
            )
            database, version, address = cursor.fetchone()
            probe = {
                "read_only_probe": True,
                "database_matches": database == expected,
                "address_matches": address == parsed.hostname,
                "postgres_version_num": int(version),
                "version_supported": 150000 <= int(version) < 170000,
                "actual_server_address": address,
                "expected_server_address": parsed.hostname,
            }
            print(json.dumps(probe, sort_keys=True), flush=True)
            if (
                not probe["database_matches"]
                or not probe["address_matches"]
                or not probe["version_supported"]
            ):
                raise RuntimeError("test database identity or native PG version check failed")
            cursor.execute(
                "SELECT default_version FROM pg_available_extensions WHERE name='vector'"
            )
            if cursor.fetchone() is None:
                raise RuntimeError("cached PostgreSQL image must provide vector")
    print(
        json.dumps(
            {
                "verified_test_database": expected,
                "postgres_version_num": int(version),
                "read_only_probe": True,
            }
        )
    )


def launch(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in ("api", "worker", "migrate", "prefix-check"):
        raise SystemExit("usage: python -m e2e_sim.boot api|worker|migrate|prefix-check")
    role = "prefix" if args[0] == "prefix-check" else args[0]
    from e2e_sim.network_fence import install_fence

    install_fence(
        {(os.environ["E2E_SIM_PG_IP"], 5432), (os.environ["E2E_SIM_REDIS_IP"], 6379)},
        Path("/evidence") / f"network-{role}.jsonl",
    )
    verify_database()
    if role == "prefix":
        from e2e_sim import scenarios

        previous = sys.argv
        try:
            sys.argv = [
                "e2e_sim.scenarios",
                "--prefix-only",
                "--output",
                "/evidence/prefix-output",
                "--evidence-root",
                "/evidence",
            ]
            return scenarios.main()
        finally:
            sys.argv = previous
    if role == "migrate":
        from alembic import command
        from alembic.config import Config

        config = Config("/app/alembic.ini")
        # Load the native settings, then keep env.py from installing a raw
        # traceback formatter through fileConfig. Schema execution stays native.
        config.get_section(config.config_ini_section)
        config.config_file_name = None
        command.upgrade(config, "head")
        verify_database()
        return

    from e2e_sim.transport import NativeTemplateObserver, Tape, install_scripted_http

    tape = Tape.from_file(
        os.environ["E2E_SIM_TAPE"],
        audit_path=Path("/evidence") / f"wire-{role}.jsonl",
        template_observer=NativeTemplateObserver(),
    )
    with install_scripted_http(tape):
        try:
            if role == "api":
                import uvicorn

                # A single process preserves the process-local hook installation.
                uvicorn.run(
                    "bestseller.api.app:create_app",
                    factory=True,
                    host="0.0.0.0",
                    port=8000,
                    workers=1,
                    loop="asyncio",
                    access_log=False,
                    log_config=None,
                )
            else:
                from bestseller.worker.main import main as worker_main

                worker_main()
        finally:
            (Path("/evidence") / f"transport-{role}-summary.json").write_text(
                json.dumps(tape.summary(), sort_keys=True), encoding="utf-8"
            )


def main(argv=None, *, evidence=None):
    args = sys.argv[1:] if argv is None else argv
    role = args[0] if len(args) == 1 and args[0] in {"api", "worker", "migrate"} else "boot"
    if args == ["prefix-check"]:
        role = "prefix"
    handler = SafeFailureHandler(Path("/evidence") if evidence is None else Path(evidence), role)
    with safe_logging(handler):
        try:
            return launch(args) or 0
        except (Exception, KeyboardInterrupt, SystemExit) as error:
            if isinstance(error, SystemExit) and error.code in (None, 0):
                return 0
            failure = handler.save(error, phase="boot_top_level", process_status="crashed")
            print(json.dumps(failure, sort_keys=True), flush=True)
            return 130 if isinstance(error, KeyboardInterrupt) else 2


if __name__ == "__main__":
    raise SystemExit(main())
