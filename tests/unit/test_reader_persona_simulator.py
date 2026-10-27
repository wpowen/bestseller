from __future__ import annotations

import pytest

from bestseller.services.reader_persona_simulator import (
    ChapterSignalPack,
    default_personas,
    render_persona_feedback_block,
    simulate_readers,
)

pytestmark = pytest.mark.unit


def _strong_signals(position: int = 5) -> ChapterSignalPack:
    return ChapterSignalPack(
        chapter_position=position,
        chapter_text_chars=2500,
        hook_count=5,
        payoff_count=3,
        cliffhanger_strength=0.85,
        voice_dna_drift=0.1,
        market_hooks_hit=4,
        market_hooks_required=3,
        novelty_score=0.7,
        consistency_score=0.9,
        emotional_beat_count=3,
        saturated_trope_hits=0,
        target_length_min=2200,
        target_length_max=3000,
        dialogue_ratio=0.35,
        action_ratio=0.25,
        interior_ratio=0.2,
        prose_quality_score=0.8,
    )


def _weak_signals(position: int = 5) -> ChapterSignalPack:
    return ChapterSignalPack(
        chapter_position=position,
        chapter_text_chars=4500,  # too long
        hook_count=0,
        payoff_count=0,
        cliffhanger_strength=0.1,
        voice_dna_drift=0.7,
        market_hooks_hit=0,
        market_hooks_required=3,
        novelty_score=0.15,
        consistency_score=0.4,
        emotional_beat_count=0,
        saturated_trope_hits=3,
        target_length_min=2200,
        target_length_max=3000,
        dialogue_ratio=0.05,
        action_ratio=0.05,
        interior_ratio=0.05,
        prose_quality_score=0.35,
    )


def test_default_personas_have_seven_distinct_keys() -> None:
    personas = default_personas()

    assert len(personas) == 7
    keys = {p.key for p in personas}
    assert len(keys) == 7
    assert {"laobai", "commute", "kaoju", "emotion", "vip", "newbie", "literati"} <= keys


def test_simulate_readers_returns_all_personas() -> None:
    signals = _strong_signals()
    result = simulate_readers(signals)

    assert result.chapter_position == 5
    assert len(result.per_persona) == 7
    assert all(0 <= s.overall_score <= 1 for s in result.per_persona)
    assert all(0 <= s.abandon_probability <= 1 for s in result.per_persona)


def test_simulate_readers_strong_signals_high_score_low_abandon() -> None:
    result = simulate_readers(_strong_signals())

    assert result.weighted_score > 0.55
    assert result.abandon_rate < 0.5
    assert len(result.high_risk_personas) <= 2


def test_simulate_readers_narrow_length_band_uses_tolerance() -> None:
    signals = ChapterSignalPack(
        chapter_position=1,
        chapter_text_chars=2411,
        hook_count=5,
        payoff_count=3,
        cliffhanger_strength=0.7,
        voice_dna_drift=0.1,
        market_hooks_hit=2,
        market_hooks_required=2,
        novelty_score=0.7,
        consistency_score=0.9,
        emotional_beat_count=3,
        saturated_trope_hits=0,
        target_length_min=2200,
        target_length_max=2200,
        dialogue_ratio=0.3,
        action_ratio=0.3,
        interior_ratio=0.2,
        prose_quality_score=0.8,
    )

    result = simulate_readers(signals)
    pacing = result.per_persona[0].channel_scores["pacing"]

    assert pacing > 0.70
    assert not any(
        "节奏偏离" in concern
        for persona in result.per_persona
        for concern in persona.concerns
    )


def test_simulate_readers_weak_signals_low_score_high_abandon() -> None:
    result = simulate_readers(_weak_signals())

    assert result.weighted_score < 0.5
    assert result.abandon_rate > 0.45
    assert result.high_risk_personas, "weak chapter must trigger at least one high-risk persona"
    assert result.aggregated_concerns


def test_simulate_readers_directives_address_missing_hooks() -> None:
    signals = _weak_signals()
    result = simulate_readers(signals)

    text = " ".join(result.next_chapter_directives)
    assert "钩子" in text or "声纹" in text or "套路" in text


def test_simulate_readers_deterministic() -> None:
    signals = _strong_signals()
    a = simulate_readers(signals)
    b = simulate_readers(signals)

    assert a.weighted_score == b.weighted_score
    assert a.abandon_rate == b.abandon_rate


def test_simulate_readers_requires_personas() -> None:
    with pytest.raises(ValueError):
        simulate_readers(_strong_signals(), personas=[])


def test_persona_concerns_align_with_persona_priorities() -> None:
    # Build signals that are weak only on novelty.
    s = _strong_signals()
    weak_novelty = ChapterSignalPack(
        chapter_position=s.chapter_position,
        chapter_text_chars=s.chapter_text_chars,
        hook_count=s.hook_count,
        payoff_count=s.payoff_count,
        cliffhanger_strength=s.cliffhanger_strength,
        voice_dna_drift=s.voice_dna_drift,
        market_hooks_hit=s.market_hooks_hit,
        market_hooks_required=s.market_hooks_required,
        novelty_score=0.15,
        consistency_score=s.consistency_score,
        emotional_beat_count=s.emotional_beat_count,
        saturated_trope_hits=s.saturated_trope_hits,
        target_length_min=s.target_length_min,
        target_length_max=s.target_length_max,
        dialogue_ratio=s.dialogue_ratio,
        action_ratio=s.action_ratio,
        interior_ratio=s.interior_ratio,
        prose_quality_score=s.prose_quality_score,
    )

    result = simulate_readers(weak_novelty)
    laobai = next(s for s in result.per_persona if s.persona_key == "laobai")

    assert any("创意" in c or "套路" in c for c in laobai.concerns)


def test_render_persona_feedback_block_zh_is_labeled_simulation_advisory() -> None:
    """W4-1：输出必须自称"模拟诊断/供参考"，不得伪装成读者判决或质检命令。"""

    result = simulate_readers(_weak_signals())
    block = render_persona_feedback_block(result)

    assert "模拟诊断" in block
    assert "供参考" in block
    assert "不是真实读者反馈" in block
    # 确定性来源与不确定性必须写明
    assert "结构信号" in block
    assert "不单独构成不合格判定" in block

    # 旧包装必须撤除：不得再冒充读者判决、也不得虚构质检后果
    assert "本章必须响应" not in block
    assert "本章必须执行" not in block
    assert "判定不合格" not in block
    assert "必须" not in block


def test_render_persona_feedback_block_handles_none() -> None:
    assert render_persona_feedback_block(None) == ""


def test_render_persona_feedback_block_supports_english() -> None:
    result = simulate_readers(_weak_signals())
    block = render_persona_feedback_block(result, language="en")

    assert "Simulation" in block
    assert "advisory" in block
    assert "not measured readers" in block
    assert "must address" not in block
    assert "Required next-chapter corrections" not in block


def test_signal_pack_position_propagates() -> None:
    signals = _strong_signals(position=42)
    result = simulate_readers(signals)
    assert result.chapter_position == 42


# ── W4 先决条件：固定现有公式的输入/输出行为 ──────────────────────────────
# 计划 W4 要求「先固定现有公式、输入与分数的行为回归，再修改权威边界」，并
# 「文案降级、门禁降级和评分公式变更分别提交与验证」。以下数值是 2026-09-10
# 的确定性基线：任何对 _normalize_channels / _score_for_persona 的改动都必须
# 单独提交并显式更新这里，不允许在改文案或改门禁时顺带漂移。


def test_persona_formula_strong_signals_baseline_is_pinned() -> None:
    result = simulate_readers(_strong_signals())

    assert result.weighted_score == pytest.approx(0.8634631208120976, abs=1e-12)
    assert result.abandon_rate == pytest.approx(0.038011571977255916, abs=1e-12)
    assert result.high_risk_personas == []


def test_persona_formula_weak_signals_baseline_is_pinned() -> None:
    result = simulate_readers(_weak_signals())

    assert result.weighted_score == pytest.approx(0.02726468601639946, abs=1e-12)
    assert result.abandon_rate == pytest.approx(0.9578282470529792, abs=1e-12)
    assert len(result.high_risk_personas) == 7


def test_persona_channel_formula_baseline_is_pinned() -> None:
    """通道分是「结构计数过 sigmoid」，不是文学质量判断——数值本身即证据。"""

    channels = simulate_readers(_strong_signals()).per_persona[0].channel_scores

    assert channels["hook_density"] == pytest.approx(0.8189195275630781, abs=1e-12)
    assert channels["payoff_density"] == pytest.approx(0.6899744811276125, abs=1e-12)
    assert channels["pacing"] == pytest.approx(0.85, abs=1e-12)
    assert channels["emotional_impact"] == pytest.approx(0.7109495026250039, abs=1e-12)

