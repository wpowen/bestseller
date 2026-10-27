from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_SCRIPT = Path(__file__).parents[2] / "scripts/distillation/aggregate_source_package.py"
_SPEC = importlib.util.spec_from_file_location("distillation_aggregate_cli", _SCRIPT)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_fallback_result_contract_is_not_success(tmp_path) -> None:
    result = SimpleNamespace(fallback_stages=("book_design_card",))
    assert _MODULE._result_is_success(result, tmp_path) is False


def test_incomplete_result_contract_is_not_success(tmp_path, monkeypatch) -> None:
    result = SimpleNamespace(fallback_stages=())
    monkeypatch.setattr(
        _MODULE,
        "package_book_phase_complete",
        lambda _path: False,
    )
    assert _MODULE._result_is_success(result, tmp_path) is False
