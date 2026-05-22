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


def test_render_persona_feedback_block_zh() -> None:
    result = simulate_readers(_weak_signals())

    block = render_persona_feedback_block(result)

    assert "上章读者画像反馈" in block
    assert "弃书率" in block
    assert "本章必须执行" in block or "本章必须" in block


def test_render_persona_feedback_block_handles_none() -> None:
    assert render_persona_feedback_block(None) == ""


def test_render_persona_feedback_block_supports_english() -> None:
    result = simulate_readers(_weak_signals())
    block = render_persona_feedback_block(result, language="en")

    assert "Persona Feedback" in block


def test_signal_pack_position_propagates() -> None:
    signals = _strong_signals(position=42)
    result = simulate_readers(signals)
    assert result.chapter_position == 42
