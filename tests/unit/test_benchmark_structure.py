from __future__ import annotations

from bestseller.services.benchmark_structure import (
    aggregate_profiles,
    compare_to_baseline,
    load_structure_baseline,
    profile_chapter,
)

_DIALOGUE_HEAVY = "\n".join(
    ['"你到底想要什么？"他把刀放在桌上。', '"三百两。"', '"疯了。"她笑了。'] * 20
    + ["他没有回答，只是吹灭了灯。最后一块地板响了吗？"]
)

_NARRATION_HEAVY = "\n".join(
    ["黄昏的废墟上覆盖着经年的尘埃，远处的山脊像一条沉睡的脊背，在暮色里起伏延绵不绝。"] * 30
)


def test_profile_chapter_basic_metrics() -> None:
    profile = profile_chapter(_DIALOGUE_HEAVY)
    assert profile is not None
    assert profile.chars > 500
    assert profile.dialogue_ratio > 0.8
    assert profile.ending_hook == 1  # ends with a question

    narration = profile_chapter(_NARRATION_HEAVY)
    assert narration is not None
    assert narration.dialogue_ratio == 0.0

    assert profile_chapter("") is None
    assert profile_chapter("太短") is None


def test_aggregate_profiles_quartiles() -> None:
    profiles = [profile_chapter(_DIALOGUE_HEAVY), profile_chapter(_NARRATION_HEAVY)] * 3
    profiles = [p for p in profiles if p]
    aggregated = aggregate_profiles(profiles)
    assert aggregated["n_chapters"] == 6
    assert "median" in aggregated["dialogue_ratio"]
    assert aggregated["dialogue_ratio"]["p25"] is not None


def test_compare_to_baseline_flags_deviation() -> None:
    baseline = {
        "t2": {
            "dialogue_ratio": {"p25": 0.25, "p75": 0.55},
            "short_sentence_ratio": {"p25": 0.05, "p75": 0.3},
            "avg_paragraph": {"p25": 40, "p75": 120},
            "ending_hook": {"mean": 0.7},
        }
    }
    narration_profiles = [p for p in [profile_chapter(_NARRATION_HEAVY)] * 4 if p]
    findings = compare_to_baseline(narration_profiles, baseline, tier="t2")
    assert any("对话占比" in f for f in findings)

    # No baseline → advisory no-op
    assert compare_to_baseline(narration_profiles, {}, tier="t2") == []
    assert compare_to_baseline([], baseline, tier="t2") == []


def test_load_structure_baseline_missing_is_empty(tmp_path) -> None:
    assert load_structure_baseline(tmp_path / "none.json") == {}
