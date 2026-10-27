from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from bestseller.settings import reset_settings_cache
from support.database_safety import (
    DatabaseSafetyError,
    install_database_safety,
    load_test_database_settings,
    selected_database_url,
)

_DB_ENV_KEEP = frozenset({"BESTSELLER__DATABASE__URL"})


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    """Fail closed at startup and guard every actual connection thereafter."""
    # Run after pytest's tmpdir plugin has installed this session's factory.
    config.add_cleanup(install_database_safety(config=config))
    try:
        selected_database_url()
    except DatabaseSafetyError as error:
        raise pytest.UsageError(str(error)) from None


@pytest.fixture
def postgresql_test_settings():
    settings = load_test_database_settings()
    if make_url(settings.database.url).get_backend_name() != "postgresql":
        pytest.skip("Requires an explicitly selected isolated PostgreSQL test database.")
    return settings


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    reset_settings_cache()
    for key in list(os.environ):
        # Keep the database URL: hermetic *settings* must not mean "silently
        # fall back to the operator's production database". See the guardrail
        # in pytest_configure above.
        if key.startswith("BESTSELLER__") and key not in _DB_ENV_KEEP:
            monkeypatch.delenv(key, raising=False)
    # Neutralise the ambient runtime LLM-profile file (artifacts/runtime/
    # llm-runtime-profile.json). It is read from disk by apply_runtime_llm_profile
    # and otherwise leaks the operator's live model strategy (e.g. an all-MiniMax-M3
    # hot-swap) into hermetic tests, overriding each test's explicit env settings and
    # breaking model-specific assertions. Point the path at a per-test temp file that
    # starts empty — so the ambient profile is ignored, while tests that exercise the
    # runtime-profile feature still write+read their own profile at this same path.
    monkeypatch.setenv(
        "BESTSELLER_LLM_RUNTIME_PROFILE_PATH",
        str(tmp_path / "runtime" / "llm-runtime-profile.json"),
    )
    yield
    reset_settings_cache()
