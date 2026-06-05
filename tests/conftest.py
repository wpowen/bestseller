from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from bestseller.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def isolate_settings_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    reset_settings_cache()
    for key in list(os.environ):
        if key.startswith("BESTSELLER__"):
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
