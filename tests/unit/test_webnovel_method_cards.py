"""网文方法卡（webnovel_method_cards）：加载、渲染、归一化与软降级回归。

架构约束回归点：方法论只烘焙进 planner 上游 prompt；config 缺失/损坏时
所有 render 函数必须返回空串（soft，不阻断管线）。
"""

import pytest

from bestseller.services.quality_levers import webnovel_method_cards as mc


@pytest.fixture(autouse=True)
def _fresh_cache():
    mc.load_webnovel_method_cards.cache_clear()
    yield
    mc.load_webnovel_method_cards.cache_clear()


def test_load_full_taxonomy():
    config = mc.load_webnovel_method_cards()
    assert len(config.chapter_end_hooks) == 13
    assert len(config.chapter_open_hooks) == 7
    assert set(config.stage_hook_strength) == {
        "opening", "early", "middle", "pre_climax", "finale",
    }
    assert config.golden_chapter_rules.new_proper_noun_caps == {
        "ch1": 6, "ch2": 5, "ch3": 5,
    }
    assert "爽" in config.target_emotion_vocabulary
    # 每张章尾钩子卡都有公式与归一化别名
    for card in config.chapter_end_hooks.values():
        assert card.formula and card.aliases


def test_render_taxonomy_block_contains_all_13_keys():
    keys = mc.chapter_end_hook_keys()
    assert len(keys) == 13
    block = mc.render_outline_hook_taxonomy_block("opening")
    assert block
    for key in keys:
        assert key in block
    assert "hook_type" in block
    assert "第1章" in block  # opening 阶段档位被渲染
    assert "target_emotion" in block


def test_render_taxonomy_unknown_stage_falls_back_to_full_table():
    block = mc.render_outline_hook_taxonomy_block("nonexistent_stage")
    assert "sudden_reveal" in block
    assert "高潮前" in block  # 整张阶段表兜底渲染


def test_render_golden_opening_rules_block():
    block = mc.render_golden_opening_rules_block()
    assert block
    assert "300" in block and "1000" in block
    assert "序章" in block
    assert "危机感 > 人设 > 金手指暗示 > 世界观" in block
    assert "ch1≤6" in block and "ch2≤5" in block and "ch3≤5" in block


def test_match_hook_type_key_variants():
    assert mc.match_hook_type_key("identity_reversal") == "identity_reversal"
    assert mc.match_hook_type_key("Sudden Reveal") == "sudden_reveal"
    assert mc.match_hook_type_key("身份反转") == "identity_reversal"
    assert mc.match_hook_type_key("章末倒计时压顶") == "countdown"
    assert mc.match_hook_type_key("天降外星人") is None
    assert mc.match_hook_type_key("") is None


def test_missing_config_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(mc, "load_yaml", lambda _filename: {})
    mc.load_webnovel_method_cards.cache_clear()
    assert mc.render_outline_hook_taxonomy_block("opening") == ""
    assert mc.render_golden_opening_rules_block() == ""
    assert mc.chapter_end_hook_keys() == ()
    assert mc.match_hook_type_key("身份反转") is None
    # 受控情绪词表有硬编码兜底，prompt 契约不随 config 丢失
    assert "爽" in mc.target_emotion_vocabulary()


def test_corrupt_config_degrades_without_raising(monkeypatch):
    def _boom(_filename):
        raise ValueError("corrupt yaml")

    monkeypatch.setattr(mc, "load_yaml", _boom)
    mc.load_webnovel_method_cards.cache_clear()
    assert mc.render_outline_hook_taxonomy_block(None) == ""
    assert mc.render_golden_opening_rules_block() == ""
    assert mc.target_emotion_vocabulary()
