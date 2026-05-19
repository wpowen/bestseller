"""Unit tests for the Step C / Phase 4 quality-lever loaders."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.chapter_signature_audit import (
    load_chapter_signature_audit,
    render_chapter_signature_block,
)
from bestseller.services.quality_levers.emotion_choreography import (
    audit_emotion_labels,
    load_emotion_choreography,
    render_emotion_choreography_block,
)
from bestseller.services.quality_levers.information_choreography import (
    InformationFlowState,
    ReaderBelief,
    evaluate_information_state,
    load_information_choreography,
    render_information_choreography_block,
)
from bestseller.services.quality_levers.quality_trend_dashboard import (
    ChapterScoreSnapshot,
    evaluate_dashboard_window,
    load_quality_trend_dashboard,
    render_dashboard_summary,
)
from bestseller.services.quality_levers.rhythm_engineering import (
    audit_rhythm,
    load_rhythm_engineering,
    render_rhythm_block,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# chapter_signature_audit
# ---------------------------------------------------------------------------


def test_load_chapter_signature_audit_returns_six_types() -> None:
    config = load_chapter_signature_audit()
    expected = {
        "golden_line",
        "surgical_description",
        "scene_climax_moment",
        "twist_with_foreshadow_landing",
        "micro_detail_punch",
        "reaction_amplification_burst",
    }
    assert expected <= set(config.signature_types.keys())
    assert config.minimum_per_chapter >= 1
    assert config.diversity_window_size >= 1
    assert config.diversity_min_types >= 1


def test_render_chapter_signature_block_includes_types_and_minimum() -> None:
    block = render_chapter_signature_block(chapter_role="ordinary_chapter")
    assert "signature" in block
    assert "本章至少" in block
    assert "golden_line" in block


# ---------------------------------------------------------------------------
# information_choreography
# ---------------------------------------------------------------------------


def test_load_information_choreography_loads_belief_audit() -> None:
    config = load_information_choreography()
    assert config.belief_max_distance >= 1
    assert config.open_question_ceiling >= 1
    assert "ch1_b1" in config.sample_belief_audit
    belief = config.sample_belief_audit["ch1_b1"]
    assert belief.planted_in_chapter == 1
    assert belief.payoff_chapter >= 1


def test_evaluate_information_state_passes_healthy_chapter() -> None:
    state = InformationFlowState(
        chapter_number=5,
        new_questions=("Why did the candle die?",),
        open_questions_count=4,
    )
    verdict = evaluate_information_state(state)
    assert verdict.must_rewrite is False
    assert verdict.new_curiosity_ok is True
    assert verdict.open_question_ok is True


def test_evaluate_information_state_flags_no_new_curiosity() -> None:
    state = InformationFlowState(
        chapter_number=5,
        new_questions=(),
        new_beliefs_planted=(),
        open_questions_count=3,
    )
    verdict = evaluate_information_state(state)
    assert verdict.must_rewrite is True
    assert any("no_new_curiosity" in reason for reason in verdict.reasons)


def test_evaluate_information_state_flags_open_question_ceiling() -> None:
    state = InformationFlowState(
        chapter_number=5,
        new_questions=("q1",),
        open_questions_count=20,
    )
    verdict = evaluate_information_state(state)
    assert verdict.must_rewrite is True
    assert any("open_question_ceiling" in reason for reason in verdict.reasons)


def test_evaluate_information_state_flags_overdue_belief() -> None:
    state = InformationFlowState(
        chapter_number=20,
        new_questions=("q1",),
        beliefs_paid_off=(),
        open_questions_count=3,
    )
    active = {
        "ch1_b1": ReaderBelief(
            belief_id="ch1_b1",
            planted_in_chapter=1,
            reader_belief="x",
            truth="y",
            payoff_chapter=6,
            payoff_method="dialogue",
            misdirection_type="角色错位",
            curiosity_level=5,
        ),
    }
    verdict = evaluate_information_state(state, active_beliefs=active)
    assert verdict.must_rewrite is True
    assert "ch1_b1" in verdict.overdue_beliefs


def test_render_information_choreography_block_includes_hard_indicators() -> None:
    block = render_information_choreography_block(chapter_number=3)
    assert "information_choreography" in block
    assert "open_questions" in block or "curiosity" in block


# ---------------------------------------------------------------------------
# rhythm_engineering
# ---------------------------------------------------------------------------


def test_load_rhythm_engineering_loads_four_anchor_types() -> None:
    config = load_rhythm_engineering()
    assert {
        "hard_stop",
        "acceleration",
        "delay",
        "external_interrupt",
    } <= set(config.rhythm_anchors.keys())
    assert config.per_1500_min_count >= 1
    assert config.per_1500_min_count == 4
    assert config.per_1500_min_types == 3


def test_audit_rhythm_detects_anchors() -> None:
    text = """
两个时辰。

门外周神算的脚步停了。

他左手往后一甩。
右手摸进右袖。
银针出袖三枚。
针尖斜对袖口。

钥匙转了第一下。

停。

钥匙转了第二下。

停。

忽然——门外又敲了一下。
"""
    result = audit_rhythm(text)
    assert result.hard_stop_count >= 1
    assert result.acceleration_count >= 1
    assert result.external_interrupt_count >= 1
    assert result.types_covered >= 3


def test_audit_rhythm_handles_empty_text() -> None:
    result = audit_rhythm("")
    assert result.total_anchors == 0
    assert result.passed is False


def test_render_rhythm_block_lists_all_anchor_types() -> None:
    block = render_rhythm_block()
    assert "hard_stop" in block
    assert "acceleration" in block
    assert "delay" in block
    assert "external_interrupt" in block


# ---------------------------------------------------------------------------
# emotion_choreography
# ---------------------------------------------------------------------------


def test_load_emotion_choreography_loads_layers_and_banned_labels() -> None:
    config = load_emotion_choreography()
    assert {
        "physiological",
        "behavioral",
        "object_interaction",
        "silence_pause",
        "dialogue_minimal",
    } <= set(config.expression_layers.keys())
    assert "愤怒" in config.banned_emotion_labels
    assert "悲伤" in config.banned_emotion_labels


def test_audit_emotion_labels_flags_narration_only() -> None:
    text = "他抬头，脸上充满愤怒。门外周神算说话。"
    result = audit_emotion_labels(text)
    assert result.total_hits >= 1
    hit_words = {hit.word for hit in result.hits}
    assert "愤怒" in hit_words


def test_audit_emotion_labels_ignores_dialogue() -> None:
    text = '管家小声说："副捕头莫要愤怒。" 沈青崖没回应。'
    result = audit_emotion_labels(text)
    assert result.total_hits == 0
    assert result.passed is True


def test_render_emotion_choreography_block() -> None:
    block = render_emotion_choreography_block()
    assert "emotion_choreography" in block
    assert "禁用情绪标签词" in block


# ---------------------------------------------------------------------------
# quality_trend_dashboard
# ---------------------------------------------------------------------------


def test_load_quality_trend_dashboard_loads_metrics() -> None:
    config = load_quality_trend_dashboard()
    assert config.window_size >= 1
    # At least a handful of named metrics should be present
    assert "per_persona_score_trend" in config.metrics
    assert "anti_pattern_frequency" in config.metrics
    assert "clue_payoff_ratio" in config.metrics


def test_evaluate_dashboard_window_handles_empty_input() -> None:
    assert evaluate_dashboard_window([]) is None


def test_evaluate_dashboard_window_aggregates_persona_avg() -> None:
    snapshots = [
        ChapterScoreSnapshot(
            chapter_number=1,
            persona_scores={"platform_editor": 0.80, "peer_author": 0.75},
            anti_pattern_hits=2,
            signature_types_present=("golden_line",),
        ),
        ChapterScoreSnapshot(
            chapter_number=2,
            persona_scores={"platform_editor": 0.85, "peer_author": 0.80},
            anti_pattern_hits=1,
            signature_types_present=("micro_detail_punch",),
        ),
    ]
    window = evaluate_dashboard_window(snapshots)
    assert window is not None
    assert window.start_chapter == 1
    assert window.end_chapter == 2
    assert window.persona_avg_scores["platform_editor"] == pytest.approx(0.825)
    assert window.anti_pattern_total == 3
    assert window.signature_type_diversity == 2


def test_evaluate_dashboard_window_emits_red_alerts() -> None:
    snapshots = [
        ChapterScoreSnapshot(
            chapter_number=1,
            persona_scores={"peer_author": 0.60},  # below 0.70 red threshold
            anti_pattern_hits=0,
            clue_payoff_ratio=0.30,
            voice_drift_vs_ch1=0.70,
        ),
    ]
    window = evaluate_dashboard_window(snapshots)
    assert window is not None
    severities = {alert.severity for alert in window.alerts}
    assert "red" in severities
    metric_ids = {alert.metric_id for alert in window.alerts}
    assert any("peer_author" in mid for mid in metric_ids)
    assert "clue_payoff_ratio" in metric_ids
    assert "voice_drift_vs_ch1" in metric_ids


def test_render_dashboard_summary_smoke() -> None:
    snapshots = [
        ChapterScoreSnapshot(
            chapter_number=1,
            persona_scores={"platform_editor": 0.80},
            anti_pattern_hits=0,
            signature_types_present=("golden_line",),
        ),
    ]
    window = evaluate_dashboard_window(snapshots)
    assert window is not None
    summary = render_dashboard_summary(window)
    assert "Quality Trend Window" in summary
    assert "platform_editor" in summary
