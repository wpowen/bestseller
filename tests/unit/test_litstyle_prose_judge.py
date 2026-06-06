"""Unit tests for the advisory LitStyle judge — result parsing + prompt assembly.

No live LLM: tests the pure functions (``litstyle_result_from_mapping`` defensive
recompute, ``build_litstyle_system_prompt`` / ``build_litstyle_user_prompt``).
"""

# ruff: noqa: RUF001, RUF002, RUF003

from __future__ import annotations

from bestseller.domain.litstyle_judge import litstyle_result_from_mapping
from bestseller.services.litstyle_prose import detect_ai_tone, load_litstyle_config
from bestseller.services.litstyle_prose_judge import (
    build_litstyle_system_prompt,
    build_litstyle_user_prompt,
)

CONFIG = load_litstyle_config()

# A clean, mature-ish judged payload (mirrors the 82-point anchor).
_MATURE_PAYLOAD = {
    "concrete": 13, "visuality": 9, "sensory": 7, "rhythm": 8, "imagery_system": 8,
    "blank_space": 9, "originality": 9, "theme_unity": 11, "narrative_fit": 10,
    "ai_tone_penalty": 2,
    "evidence": ["旧雨衣搭回门后", "火柴潮了", "水开了两次"],
    "top_issues": ["感官层可再厚"],
    "revision_priority": ["补一处水汽或灶声的触觉"],
}


def test_final_score_recomputed_not_trusted():
    """Even if the model lies about final_score, we recompute from dims − penalty."""

    payload = {**_MATURE_PAYLOAD, "final_score": 5, "level": "较弱"}
    result = litstyle_result_from_mapping(payload, config=CONFIG)
    assert result.base_score == 84
    assert result.final_score == 82  # 84 − 2, NOT the lied 5
    assert result.level == "成熟"
    assert result.is_mature is True
    assert result.is_high_risk_template is False


def test_dimension_scores_clamped_to_max():
    """A model returning over-cap dimensions cannot inflate the base score."""

    payload = {**_MATURE_PAYLOAD, "concrete": 19, "visuality": 50}
    result = litstyle_result_from_mapping(payload, config=CONFIG)
    assert result.dimension_scores["concrete"] == 14  # capped from 19
    assert result.dimension_scores["visuality"] == 10  # capped from 50


def test_ai_tone_prior_is_a_floor():
    """The deterministic prior floors the model's self-reported penalty."""

    payload = {**_MATURE_PAYLOAD, "ai_tone_penalty": 1}
    result = litstyle_result_from_mapping(payload, config=CONFIG, ai_tone_prior=7.4)
    assert result.ai_tone_penalty == 7  # max(1, round(7.4))
    # final drops accordingly: 84 − 7 = 77 → 可用
    assert result.final_score == 77
    assert result.level == "可用"


def test_high_risk_template_flag():
    template_payload = {
        "concrete": 2, "visuality": 2, "sensory": 1, "rhythm": 4, "imagery_system": 1,
        "blank_space": 1, "originality": 2, "theme_unity": 5, "narrative_fit": 3,
        "ai_tone_penalty": 16,
    }
    result = litstyle_result_from_mapping(template_payload, config=CONFIG)
    assert result.is_high_risk_template is True
    assert result.is_mature is False
    assert result.final_score == 5  # 21 − 16


def test_empty_payload_self_labels_unavailable():
    """A transient empty judge response must not masquerade as a genuine all-zero."""

    result = litstyle_result_from_mapping({}, config=CONFIG)
    assert result.final_score == 0
    assert "LITSTYLE_JUDGE_UNAVAILABLE" in result.top_issues
    # A real (non-empty) all-low payload is NOT mislabelled.
    real_low = dict.fromkeys(CONFIG.dimension_keys, 0)
    real_low["ai_tone_penalty"] = 0
    low_result = litstyle_result_from_mapping(real_low, config=CONFIG)
    assert "LITSTYLE_JUDGE_UNAVAILABLE" not in low_result.top_issues


def test_result_has_no_passed_field():
    """Advisory contract: the文采 result must never expose a gating verdict."""

    result = litstyle_result_from_mapping(_MATURE_PAYLOAD, config=CONFIG)
    assert not hasattr(result, "passed")


def test_system_prompt_contains_rubric_and_disclaimer():
    prompt = build_litstyle_system_prompt(config=CONFIG)
    for key in CONFIG.dimension_keys:
        assert key in prompt
    assert "绝不判定作者是否使用 AI" in prompt
    assert "校准锚点" in prompt
    assert "ai_tone_penalty" in prompt
    # We recompute final_score, so the model is told NOT to output it.
    assert "不要输出 final_score" in prompt


def test_user_prompt_includes_deterministic_hint_when_flagged():
    template = (
        "他意识到，真正可怕的不是失败，而是在失败中失去希望。"
        "他感到震惊、痛苦、无助，但也明白成长总要付出代价。"
    )
    ai_tone = detect_ai_tone(template, CONFIG)
    prompt = build_litstyle_user_prompt(
        chapter_number=3, content_md=template, ai_tone=ai_tone
    )
    assert "确定性预扫提示" in prompt
    assert "第3章" in prompt


def test_user_prompt_omits_hint_when_clean():
    clean = "雨停后，院里那棵枣树还滴着水。她把父亲的旧雨衣搭回门后。"
    ai_tone = detect_ai_tone(clean, CONFIG)
    prompt = build_litstyle_user_prompt(
        chapter_number=1, content_md=clean, ai_tone=ai_tone
    )
    assert "确定性预扫提示" not in prompt
