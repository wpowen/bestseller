"""Unit tests for the pacing engine (Stage D) + related block builders."""

from __future__ import annotations

import pytest

from bestseller.services.deduplication import (
    build_cliffhanger_diversity_block,
    build_location_ledger_block,
    build_tension_target_block,
)
from bestseller.services.pacing_engine import (
    BEAT_SHEET,
    CLIFFHANGER_DISTRIBUTION,
    CLIFFHANGER_TYPES,
    evaluate_hook_diversity,
    evaluate_tension_variance,
    score_tension,
    target_beat_for_chapter,
    target_tension_for_chapter,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Cliffhanger taxonomy
# ---------------------------------------------------------------------------

def test_seven_cliffhanger_types() -> None:
    assert len(CLIFFHANGER_TYPES) == 7
    assert set(CLIFFHANGER_DISTRIBUTION.keys()) == set(CLIFFHANGER_TYPES.keys())


def test_cliffhanger_distribution_sums_to_one() -> None:
    assert abs(sum(CLIFFHANGER_DISTRIBUTION.values()) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# evaluate_hook_diversity
# ---------------------------------------------------------------------------

def test_evaluate_hook_diversity_empty_history() -> None:
    result = evaluate_hook_diversity([])
    assert result["forbid"] == []


def test_evaluate_hook_diversity_forbids_immediate_repeat() -> None:
    result = evaluate_hook_diversity(["suspense", "crisis", "twist"])
    assert "suspense" in result["forbid"]


def test_evaluate_hook_diversity_forbids_triple_run() -> None:
    result = evaluate_hook_diversity(["suspense", "suspense", "suspense", "crisis"])
    assert "suspense" in result["forbid"]


def test_evaluate_hook_diversity_does_not_forbid_two_in_a_row() -> None:
    # Two same in a row triggers immediate-repeat forbid, but three-run rule
    # only triggers at 3; second-to-last isn't automatically forbidden here.
    result = evaluate_hook_diversity(["crisis", "crisis", "twist"])
    # Immediate-prior "crisis" IS forbidden, but "twist" isn't in forbid.
    assert "crisis" in result["forbid"]
    assert "twist" not in result["forbid"]


def test_evaluate_hook_diversity_suggests_underused_types() -> None:
    # Heavy suspense usage → other types should rank higher.
    history = ["suspense"] * 10
    result = evaluate_hook_diversity(history)
    assert "suspense" not in result["suggested"]
    # An underrepresented type should show up near the top.
    assert len(result["suggested"]) >= 3


# ---------------------------------------------------------------------------
# Beat sheet / tension target
# ---------------------------------------------------------------------------

def test_beat_sheet_percentiles_are_monotonic() -> None:
    lows = [entry.percentile_low for entry in BEAT_SHEET]
    assert lows == sorted(lows)


def test_target_beat_at_climax() -> None:
    entry = target_beat_for_chapter(95, 100)
    assert entry.beat_name == "Climax"
    assert entry.tension_target >= 9.0


def test_target_tension_for_chapter_opening_is_hook() -> None:
    # 1 / 100 = 0.01 → falls in Hook (0.00-0.05)
    assert target_tension_for_chapter(1, 100) == 6.0


def test_target_beat_overflow_chapter_past_total_clamps_to_last() -> None:
    entry = target_beat_for_chapter(120, 100)
    assert entry.beat_name == "New Equilibrium"


# ---------------------------------------------------------------------------
# score_tension
# ---------------------------------------------------------------------------

def test_score_tension_all_defaults_is_five() -> None:
    assert score_tension({}) == 5.0


def test_score_tension_all_max_is_ten() -> None:
    components = {k: 10 for k in (
        "stakes", "conflict", "pace", "novelty", "emotion", "info_density"
    )}
    assert score_tension(components) == 10.0


def test_score_tension_clamps_out_of_range() -> None:
    assert score_tension({"stakes": 50.0}) <= 10.0
    assert score_tension({"stakes": -5.0}) >= 0.0


# ---------------------------------------------------------------------------
# Variance
# ---------------------------------------------------------------------------

def test_evaluate_tension_variance_too_few_samples() -> None:
    result = evaluate_tension_variance([7.0, 7.0])
    assert result["sufficient_sample"] is False


def test_evaluate_tension_variance_flat_rhythm_flagged() -> None:
    flat = [7.0, 7.1, 6.9, 7.0, 7.2, 6.8]
    result = evaluate_tension_variance(flat)
    assert result["sufficient_sample"] is True
    assert result["flag_flat"] is True


def test_evaluate_tension_variance_healthy_variance() -> None:
    varied = [2.0, 8.5, 4.0, 9.0, 3.5, 7.0]
    result = evaluate_tension_variance(varied)
    assert result["flag_flat"] is False


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------

def test_build_cliffhanger_diversity_block_zh_renders_forbid_and_suggest() -> None:
    block = build_cliffhanger_diversity_block(
        ["suspense", "suspense", "suspense"],
        chapter_number=30, total_chapters=100, language="zh-CN",
    )
    assert "章末钩子" in block
    assert "suspense" in block


def test_build_cliffhanger_diversity_block_empty_history() -> None:
    block = build_cliffhanger_diversity_block(
        [], chapter_number=1, total_chapters=100, language="zh-CN",
    )
    assert "近章尚无钩子记录" in block


def test_build_tension_target_block_without_scores() -> None:
    block = build_tension_target_block(
        50, 100, recent_tension_scores=None, language="zh-CN",
    )
    assert "节拍位" in block
    assert "张力" in block


def test_build_tension_target_block_flags_flat_rhythm() -> None:
    flat = [7.0] * 6
    block = build_tension_target_block(
        40, 100, recent_tension_scores=flat, language="zh-CN",
    )
    assert "扁平" in block or "flat" in block.lower()


def test_build_location_ledger_block_cap_reached() -> None:
    block = build_location_ledger_block(
        "下水道",
        ["下水道", "下水道", "下水道", "下水道", "塔楼"],
        language="zh-CN",
    )
    assert "已达上限" in block


def test_build_location_ledger_block_revisit_triggers_rules() -> None:
    block = build_location_ledger_block(
        "下水道", ["下水道", "塔楼"], language="zh-CN",
    )
    assert "复访规则" in block
    assert "价值轴" in block


def test_build_location_ledger_block_first_visit() -> None:
    block = build_location_ledger_block(
        "街巷", ["下水道", "塔楼"], language="zh-CN",
    )
    assert "新地点" in block


def test_build_location_ledger_block_english() -> None:
    block = build_location_ledger_block(
        "sewer", ["sewer"] * 5, language="en",
    )
    assert "Visit cap reached" in block
