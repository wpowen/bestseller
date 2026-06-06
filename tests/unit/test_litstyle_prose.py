"""Unit tests for the LitStyle config loader + deterministic AI腔 detector.

Fully deterministic — no LLM, no DB. Locks:
* config integrity (9 dims summing to 100, calibration anchors self-consistent),
* the deterministic detector fires on the high-AI腔 anchor and does NOT false-fire
  on the clean (mature) anchor,
* level + writer-tier-target resolution.
"""

# ruff: noqa: RUF001, RUF002, RUF003

from __future__ import annotations

import pytest

from bestseller.services.litstyle_prose import (
    detect_ai_tone,
    is_premium_writer_model,
    litstyle_level_for_score,
    litstyle_target_for_writer_model,
    load_litstyle_config,
)


@pytest.fixture(scope="module")
def config():
    return load_litstyle_config()


def test_nine_dimensions_sum_to_100(config):
    assert len(config.dimensions) == 9
    assert config.base_score_max == 100
    assert set(config.dimension_keys) == {
        "concrete", "visuality", "sensory", "rhythm", "imagery_system",
        "blank_space", "originality", "theme_unity", "narrative_fit",
    }


def test_ai_tone_markers_and_thresholds(config):
    assert config.ai_tone_penalty_max == 20
    assert config.ai_tone_high_risk_threshold == 15
    assert config.ai_tone_mature_ceiling == 4
    # Three deterministic markers, summing to 11 (4 + 4 + 3).
    deterministic = [m for m in config.ai_tone_markers if m.deterministic]
    assert {m.marker_id for m in deterministic} == {
        "abstract_value_density", "symmetric_syntax", "emotion_label_substitution",
    }
    assert config.deterministic_penalty_max == 11


def test_calibration_anchors_are_self_consistent(config):
    """Each anchor's FinalScore must equal max(0, Σ dims − penalty)."""

    assert len(config.calibration_anchors) == 3
    dim_keys = set(config.dimension_keys)
    for anchor in config.calibration_anchors:
        base = sum(v for k, v in anchor.scores.items() if k in dim_keys)
        penalty = anchor.scores.get("ai_tone_penalty", 0)
        assert max(0, base - penalty) == anchor.final, anchor.anchor_id


def test_detect_ai_tone_fires_on_template_text(config):
    """The high-risk template anchor must trip all three deterministic markers."""

    template = (
        "他意识到，真正可怕的不是失败，而是在失败中失去希望。"
        "不是世界抛弃了他，而是他抛弃了自己。"
        "他感到震惊、痛苦、无助，但也明白成长总要付出代价。"
    )
    result = detect_ai_tone(template, config)
    assert result.symmetric_hits >= 2  # 不是…而是 ×2 + 他意识到
    assert result.emotion_label_hits >= 3  # 震惊 / 痛苦 / 无助
    assert result.abstract_value_hits >= 2  # 希望 / 成长
    assert set(result.flagged) == {
        "abstract_value_density", "symmetric_syntax", "emotion_label_substitution",
    }
    assert result.deterministic_penalty > 0


def test_detect_ai_tone_does_not_false_fire_on_clean_text(config):
    """The mature anchor (clean, concrete, no AI腔) must score 0 penalty."""

    clean = (
        "雨停后，院里那棵枣树还滴着水。她把父亲的旧雨衣搭回门后，"
        "摸到口袋里那盒潮了的火柴，才想起今年清明，他没有回来。"
        "厨房里水开了两次，她都没去下面。"
    )
    result = detect_ai_tone(clean, config)
    assert result.flagged == ()
    assert result.deterministic_penalty == 0.0


def test_detect_ai_tone_empty_text_is_safe(config):
    result = detect_ai_tone("", config)
    assert result.char_count == 0
    assert result.deterministic_penalty == 0.0
    assert result.flagged == ()


@pytest.mark.parametrize(
    ("score", "level"),
    [(95, "卓越"), (82, "成熟"), (75, "可用"), (65, "待修"), (21, "较弱"), (0, "较弱")],
)
def test_level_for_score(config, score, level):
    assert litstyle_level_for_score(score, config) == level


def test_target_binds_to_writer_tier(config):
    assert litstyle_target_for_writer_model("claude-sonnet-4", config) == 80.0
    assert litstyle_target_for_writer_model("minimax-m2", config) == 72.0
    assert litstyle_target_for_writer_model(None, config) == 72.0


def test_premium_tier_parity_with_commercial_judge():
    """litstyle's tier check must match the commercial judge's (kept in sync)."""

    from bestseller.services.chapter_llm_quality_judge import (
        is_premium_writer_model as commercial_premium,
    )

    for model in ("claude-3-5-sonnet", "gpt-4o", "minimax-m2", "deepseek-chat", None):
        assert is_premium_writer_model(model) == commercial_premium(model)
