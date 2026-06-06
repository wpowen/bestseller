"""Unit tests for the文采 polish prompt builder (pure, no LLM)."""


from __future__ import annotations

from bestseller.domain.litstyle_judge import litstyle_result_from_mapping
from bestseller.services.litstyle_polish import build_litstyle_polish_prompt
from bestseller.services.litstyle_prose import load_litstyle_config

CONFIG = load_litstyle_config()


def _result(**overrides):
    base = dict.fromkeys(CONFIG.dimension_keys, 10)
    base["ai_tone_penalty"] = 0
    base.update(overrides)
    return litstyle_result_from_mapping(base, config=CONFIG)


def test_polish_targets_low_dimensions_only():
    # sensory + blank_space are weak; others fine.
    result = _result(sensory=2, blank_space=2, revision_priority=["补一处嗅觉"])
    system, user = build_litstyle_polish_prompt(draft="原文内容。", result=result, config=CONFIG)
    assert "保剧情" in system or "不改剧情" in system
    assert "感官密度" in user  # sensory fix selected
    assert "留白" in user       # blank_space fix selected
    assert "补一处嗅觉" in user  # judge's revision_priority echoed
    # A healthy dimension's fix should not be force-injected.
    assert "主题统一度" not in user


def test_polish_adds_ai_tone_fix_when_penalty_high():
    result = _result(ai_tone_penalty=10)
    _system, user = build_litstyle_polish_prompt(draft="原文。", result=result, config=CONFIG)
    assert "去AI腔" in user


def test_polish_preserves_plot_and_length_constraints():
    result = _result(concrete=3)
    system, _user = build_litstyle_polish_prompt(draft="原文。", result=result, config=CONFIG)
    assert "不增删情节" in system
    assert "±12%" in system  # length guard


def test_polish_clean_text_falls_back_not_fabricates():
    """An already-clean draft (every dim at its max) gets a light pass, not fabricated fixes."""

    clean = {dim.key: dim.max for dim in CONFIG.dimensions}
    clean["ai_tone_penalty"] = 0
    result = litstyle_result_from_mapping(clean, config=CONFIG)
    _system, user = build_litstyle_polish_prompt(draft="原文。", result=result, config=CONFIG)
    assert "轻微" in user  # the generic light-pass fallback
