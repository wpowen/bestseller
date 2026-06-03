from __future__ import annotations

import pytest

from bestseller.services.chapter_signal_builder import build_signal_pack
from bestseller.services.market_constraint_compiler import (
    compile_chapter_constraints,
)
from bestseller.services.voice_signature import extract_voice_dna_from_text

pytestmark = pytest.mark.unit


_STRONG_CHAPTER = (
    "夜色如墨，山风扑过，火光在崖边一闪而灭。\n"
    "他握紧剑柄，心中暗想：今夜若不退，便是死路一条。\n"
    "“你当真敢杀我？”那人冷冷一笑。\n"
    "他不答，只是出剑。剑光如电，劈开夜色，劈开风声。\n"
    "他想起十年前的那个雨夜，眼眶微红。\n"
    "终于，他明白了那个名字的意义。\n"
    "下一刻，门外脚步声响起，名单还在他怀中。\n"
    "未及反应，墙壁砉然破开！\n"
) * 50


def test_build_signal_pack_minimal_inputs() -> None:
    pack = build_signal_pack(_STRONG_CHAPTER, chapter_position=1)

    assert pack.chapter_position == 1
    assert pack.chapter_text_chars > 0
    assert pack.hook_count > 0
    assert pack.payoff_count >= 1
    assert 0 <= pack.cliffhanger_strength <= 1
    assert pack.voice_dna_drift == 0  # no target DNA -> 0
    assert pack.market_hooks_required == 0


def test_build_signal_pack_counts_rule_survival_payoff_markers() -> None:
    text = (
        "沈照把反证贴上提示牌，豁免印落进他掌心。\n"
        "门缝里的红字当场改判，规则修正即时生效。\n"
    )

    pack = build_signal_pack(text, chapter_position=1)

    assert pack.payoff_count >= 3


def test_build_signal_pack_rejects_invalid_position() -> None:
    with pytest.raises(ValueError):
        build_signal_pack(_STRONG_CHAPTER, chapter_position=0)


def test_build_signal_pack_with_target_dna_computes_drift() -> None:
    target = extract_voice_dna_from_text(
        _STRONG_CHAPTER, source_id="target"
    )
    different = "他说好。她说不好。" * 200  # totally different register

    pack = build_signal_pack(
        different, chapter_position=2, target_voice_dna=target
    )

    assert pack.voice_dna_drift > 0
    # pacing ratios are populated from observed text
    assert pack.dialogue_ratio >= 0


def test_build_signal_pack_with_constraints_counts_hooks() -> None:
    # Build a constraints object where 'must_hit_hooks' includes a substring
    # that we know appears in _STRONG_CHAPTER.
    from datetime import UTC, date, datetime

    from bestseller.domain.fanqie_market import (
        FanqieCategoryProfile,
        FanqieCompetitorProfile,
        FanqieCraftProfile,
        FanqieMarketAnalysisBundle,
        FanqieRankingSnapshot,
    )

    bundle = FanqieMarketAnalysisBundle(
        snapshot=FanqieRankingSnapshot(
            category="武侠",
            data_date=date(2026, 5, 20),
            fetched_at=datetime.now(UTC),
        ),
        competitor_profiles=[
            FanqieCompetitorProfile(
                source_book_id="b1",
                title="t",
                rank=1,
                hook_patterns=["下一刻", "名单"],
                structure_patterns=["case_to_action"],
            )
        ],
        category_profile=FanqieCategoryProfile(
            category="武侠",
            data_date=date(2026, 5, 20),
            sample_size=1,
            hook_patterns=["下一刻", "名单"],
            payoff_patterns=["终于明白"],
            confidence=0.6,
        ),
        craft_profile=FanqieCraftProfile(
            category="武侠",
            safety_boundary="",
            confidence=0.6,
        ),
    )
    constraints = compile_chapter_constraints(bundle, chapter_position=2)

    pack = build_signal_pack(
        _STRONG_CHAPTER, chapter_position=2, constraints=constraints
    )

    assert pack.market_hooks_required > 0
    assert pack.market_hooks_hit >= 1
    assert pack.target_length_min > 0
    assert pack.target_length_max > pack.target_length_min


def test_build_signal_pack_empty_text() -> None:
    pack = build_signal_pack("", chapter_position=1)

    assert pack.chapter_text_chars == 0
    assert pack.hook_count == 0
    assert pack.payoff_count == 0
    assert pack.cliffhanger_strength == 0
    assert pack.emotional_beat_count == 0


def test_build_signal_pack_overrides_critic_scores() -> None:
    pack = build_signal_pack(
        _STRONG_CHAPTER,
        chapter_position=1,
        novelty_score=0.3,
        consistency_score=0.5,
        prose_quality_score=0.9,
    )

    assert pack.novelty_score == 0.3
    assert pack.consistency_score == 0.5
    assert pack.prose_quality_score == 0.9


def test_build_signal_pack_critic_scores_clamped() -> None:
    pack = build_signal_pack(
        _STRONG_CHAPTER,
        chapter_position=1,
        novelty_score=1.5,
        prose_quality_score=-0.3,
    )

    assert pack.novelty_score == 1.0
    assert pack.prose_quality_score == 0.0


def test_build_signal_pack_invalid_score_falls_back_to_default() -> None:
    pack = build_signal_pack(
        _STRONG_CHAPTER,
        chapter_position=1,
        novelty_score="not a number",  # type: ignore[arg-type]
    )

    # Falls back to the documented default 0.55
    assert pack.novelty_score == 0.55


def test_build_signal_pack_extra_markers() -> None:
    pack = build_signal_pack(
        "他笑了。XYZ。笑了。",
        chapter_position=1,
        extra_hook_markers=["XYZ"],
        extra_payoff_markers=["笑了"],
    )

    assert pack.hook_count >= 1
    assert pack.payoff_count >= 1
