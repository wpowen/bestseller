from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from bestseller.settings import load_settings, reset_settings_cache

# ── Production-database guardrail ────────────────────────────────────────────
#
# This exists because the library was wiped twice by test runs (2026-07-14,
# 2026-07-21). Both times the mechanism was the same and it is entirely
# non-obvious:
#
#   * ``isolate_settings_environment`` below strips every ``BESTSELLER__*`` env
#     var so tests are hermetic.
#   * With those stripped, ``load_settings()`` falls back to its DEFAULT
#     database URL — ``postgresql://bestseller:bestseller@localhost:5432/bestseller``.
#   * docker-compose publishes the live database on ``0.0.0.0:5432``.
#
# So on any machine running the stack, "hermetic" tests point straight at
# production, and the handful of tests that delete projects cascade the whole
# library away. ``llm_runs`` survives (its project_id is ON DELETE SET NULL) —
# that lopsided survival is the signature of this exact accident.
#
# The check runs in ``pytest_configure`` so it aborts before a single test is
# collected, not after some of them have already written.
_TEST_DB_MARKERS = ("test", "tmp", "ci")
_OVERRIDE_ENV = "BESTSELLER_ALLOW_PROD_DB_IN_TESTS"
#: The one setting tests must keep control of. Everything else is stripped for
#: hermeticity, but stripping this one is what made "hermetic" mean
#: "production" — there was no way to redirect the suite at a test database.
_DB_ENV_KEEP = frozenset({"BESTSELLER__DATABASE__URL"})


def _database_name(url: str) -> str:
    path = url.split("?", 1)[0].rstrip("/")
    return path.rsplit("/", 1)[-1] if "/" in path else ""


def _looks_like_a_test_database(url: str) -> bool:
    lowered = url.lower()
    # In-memory / file sqlite and explicitly mocked backends are always fine.
    if lowered.startswith("sqlite") or "memory" in lowered:
        return True
    name = _database_name(lowered)
    return any(marker in name for marker in _TEST_DB_MARKERS)


def pytest_configure(config: pytest.Config) -> None:
    """Refuse to run if the suite would talk to a non-test database."""

    if os.environ.get(_OVERRIDE_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return

    # Resolve the URL the way the tests themselves will: with BESTSELLER__*
    # stripped EXCEPT the database URL, matching isolate_settings_environment.
    saved = {
        k: v
        for k, v in os.environ.items()
        if k.startswith("BESTSELLER__") and k not in _DB_ENV_KEEP
    }
    for key in saved:
        os.environ.pop(key, None)
    try:
        reset_settings_cache()
        url = str(getattr(load_settings().database, "url", "") or "")
    except Exception:  # noqa: BLE001 - never let the guard itself break the run
        return
    finally:
        os.environ.update(saved)
        reset_settings_cache()

    if _looks_like_a_test_database(url):
        return

    safe_url = url.split("@", 1)[-1] if "@" in url else url
    raise pytest.UsageError(
        "\n"
        "╔═══════════════════════════════════════════════════════════════════╗\n"
        "║  测试被中止：数据库指向的不是测试库                                 ║\n"
        "╚═══════════════════════════════════════════════════════════════════╝\n"
        f"  解析到的数据库： {safe_url}\n"
        f"  库名 '{_database_name(url)}' 不含 {_TEST_DB_MARKERS} 任一标记。\n"
        "\n"
        "  这个库曾两次被测试清空（2026-07-14、2026-07-21）：conftest 会剥掉\n"
        "  BESTSELLER__* 环境变量使测试保持纯净，而剥掉之后 load_settings()\n"
        "  回落到默认的 localhost:5432/bestseller —— 正是 docker-compose 暴露\n"
        "  的生产库。少数删 project 的测试会级联删掉整个书库。\n"
        "\n"
        "  怎么办：\n"
        "    export BESTSELLER__DATABASE__URL=\\\n"
        "      postgresql+asyncpg://bestseller:bestseller@localhost:5432/bestseller_test\n"
        "    createdb bestseller_test   # 或 docker exec bestseller-db-1 createdb -U bestseller bestseller_test\n"
        "\n"
        f"  确实要用当前这个库：{_OVERRIDE_ENV}=1（会真的写它，请先备份）\n"
    )


@pytest.fixture(autouse=True)
def isolate_settings_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
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
