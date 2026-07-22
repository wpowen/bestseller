from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "anti_ai_short_arena.py"
_SPEC = importlib.util.spec_from_file_location("anti_ai_short_arena_script", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


def test_arena_rejects_fallback_results() -> None:
    with pytest.raises(RuntimeError, match="inconclusive"):
        _MODULE._require_real_result({"fallback_used": True}, label="writer")


def test_arena_uses_distinct_judge_families() -> None:
    assert _MODULE.PRIMARY_JUDGE_MODEL_KEY != _MODULE.SECONDARY_JUDGE_MODEL_KEY
