from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts/repair_qingnang_ch51_75_guardrails.py"
    spec = importlib.util.spec_from_file_location("repair_qingnang_ch51_75_guardrails", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_merge_guardrails_adds_game_drift_terms_once():
    module = _load_module()
    raw = {
        "forbidden_terms": [{"term": "玩家", "reason": "old"}, {"term": "灯在仁"}],
        "state_rules": [{"subject": "林远山", "forbidden_patterns": ["父亲林远山"]}],
    }

    merged = module.merge_guardrails(raw)

    terms = [entry["term"] if isinstance(entry, dict) else entry for entry in merged["forbidden_terms"]]
    assert terms.count("玩家") == 1
    assert "源代码" in terms
    assert "试炼通关" in terms
    assert "镜主候选" in terms
    player = next(entry for entry in merged["forbidden_terms"] if isinstance(entry, dict) and entry["term"] == "玩家")
    assert "死亡游戏" in player["reason"]


def test_recovery_contract_records_option_c_and_mirror_forgery():
    module = _load_module()

    contract = module.build_recovery_contract_text()

    assert "Option C" in contract
    assert "镜影伪造的身份战支线" in contract
    assert "ch50 -> ch51" in contract
    assert "老宅井口" in contract


def test_rewrite_targets_cover_drift_chapters():
    module = _load_module()

    chapters = {target["chapter_number"] for target in module.REWRITE_TARGETS}
    reasons = {reason for target in module.REWRITE_TARGETS for reason in target["reasons"]}

    assert {51, 62, 63, 64, 69, 71}.issubset(chapters)
    assert "forbidden_game_vocabulary" in reasons
    assert "source_code_vocabulary" in reasons
    assert "canon_state_regression" in reasons
