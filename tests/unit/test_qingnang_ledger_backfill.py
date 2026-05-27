from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts/_deprecated/qingnang_repair/backfill_qingnang_ledgers.py"
    spec = importlib.util.spec_from_file_location("backfill_qingnang_ledgers", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_backfill_ledgers_include_recovery_markers():
    module = _load_module()

    event_state = module.render_event_state_ledger("")
    clue_ledger = module.render_clue_ledger("")
    continuity_ledger = module.render_continuity_ledger(
        "青囊不语问阴阳",
        [(51, "城南旧事馆", 2200, 1, "volume_2_identity_war_recovery")],
    )

    assert "第 50 章" in event_state
    assert "审讯室/被释放过场缺口" in event_state or "缺释放/手续过场" in event_state
    assert "第 75 章" in event_state
    assert "C-026" in clue_ledger
    assert "沈家旧卷" in clue_ledger
    assert "镜主候选" in clue_ledger or "镜主信物" in clue_ledger
    assert "reframed_as_mirror_forged_authority" in clue_ledger
    assert "| 51 | 城南旧事馆 |" in continuity_ledger


def test_backfill_sanitizes_forbidden_planning_terms():
    module = _load_module()

    continuity_ledger = module.render_continuity_ledger(
        "青囊不语问阴阳",
        [(71, "试炼通关", 2200, 1, "volume_2_identity_war_recovery")],
    )
    clue_ledger = module.render_clue_ledger("| C-999 | 玩家 | 第 1 章 | 源代码 | 试炼通关 | 镜主候选 |\n")

    combined = continuity_ledger + clue_ledger
    for term in ("玩家", "源代码", "试炼通关", "镜主候选"):
        assert term not in combined
