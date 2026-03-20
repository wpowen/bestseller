from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from bestseller.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_settings_cache()
    for key in list(os.environ):
        if key.startswith("BESTSELLER__"):
            monkeypatch.delenv(key, raising=False)
    yield
    reset_settings_cache()
