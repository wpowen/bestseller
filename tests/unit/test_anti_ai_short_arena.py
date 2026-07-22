from __future__ import annotations

from bestseller.services.anti_ai_short_arena import (
    aggregate_strategy_metrics,
    build_short_arena_briefs,
    build_short_arena_strategies,
    compute_acceptance,
    repeated_ngram_ratio,
    score_short_sample,
)


def test_experiment_matrix_fits_fifty_call_budget() -> None:
    briefs = build_short_arena_briefs()
    strategies = build_short_arena_strategies()

    assert len(briefs) == 5
    assert len(strategies) == 6
    assert len(briefs) * len(strategies) == 30


def test_short_scorer_flags_reported_ai_flavor_family() -> None:
    brief = build_short_arena_briefs()[0]
    text = (
        "沈砚手腕又烫了一下，喉结滚动。他忽然明白，这不是师父留下的警告，而是命运的召唤。"
        "他没回头，只把钥匙攥得指节发白。"
    )

    score = score_short_sample(text, brief)

    assert score.risk_score >= 30
    assert score.pattern_counts["body_shortcut"] >= 2
    assert score.pattern_counts["epiphany_announcement"] >= 1
    assert score.pattern_counts["negated_definition"] >= 1
    assert score.pattern_counts["negative_action_filler"] >= 1


def test_short_scorer_rewards_event_specific_process() -> None:
    brief = build_short_arena_briefs()[0]
    text = (
        "铜钥刚转半圈，腕骨旧印的热意便顶住他的手。门后敲了两短一长。"
        "沈砚拔出钥匙，改用钥匙齿在门框上回敲同样三声。里面的脚步停了。"
    )

    score = score_short_sample(text, brief)

    assert score.coverage_ratio == 1.0
    assert score.length_passed
    assert score.risk_score < 10


def test_short_scorer_flags_mei_shenme_shi_y_self_explanation() -> None:
    brief = build_short_arena_briefs()[1]
    text = "她摸了摸玉佩。其实没什么舍不得，是已经舍不得了。院门外传来脚步。"

    score = score_short_sample(text, brief)

    assert score.pattern_counts["negated_definition"] == 1


def test_repeated_ngram_ratio_detects_cross_scene_template_reuse() -> None:
    templated = [
        "他喉结滚了一下，把铜钥放下。",
        "她喉结滚了一下，把玉佩放下。",
        "许川喉结滚了一下，把公交卡放下。",
    ]
    varied = [
        "钥匙齿刮过门框。",
        "玉佩落进井里。",
        "公交卡贴在掌纹上。",
    ]

    assert repeated_ngram_ratio(templated) > repeated_ngram_ratio(varied)


def test_acceptance_requires_quality_gain_and_task_coverage() -> None:
    metrics = {
        "production_control": {
            "deterministic_risk": 40.0,
            "length_pass_rate": 1.0,
            "coverage_pass_rate": 1.0,
        },
        "winner": {
            "deterministic_risk": 20.0,
            "length_pass_rate": 1.0,
            "coverage_pass_rate": 0.8,
        },
    }

    result = compute_acceptance(
        winner_id="winner",
        metrics_by_strategy=metrics,
        head_to_head_vs_control=0.8,
        llm_calls=50,
    )

    assert result["passed"] is True


def test_aggregate_strategy_metrics_preserves_per_sample_evidence() -> None:
    briefs = build_short_arena_briefs()
    drafts = {
        brief.brief_id: "铜钥碰到门。玉佩落地。公交卡翻面。工资条递出。蓝盐粘在印泥上。"
        for brief in briefs
    }

    result = aggregate_strategy_metrics(drafts, briefs)

    assert len(result["samples"]) == 5
    assert "cross_sample_reuse_ratio" in result
