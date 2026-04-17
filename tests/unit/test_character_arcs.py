"""Unit tests for the character-arc taxonomy + 5-layer thinking contract."""

from __future__ import annotations

import pytest

from bestseller.services.character_arcs import (
    ARC_TYPES,
    BEAT_ORDER,
    BEAT_TABLE,
    CharacterInnerStructure,
    FORBIDDEN_EMOTION_WORDS_EN,
    FORBIDDEN_EMOTION_WORDS_ZH,
    beats_elapsed_before,
    compute_arc_stage_for_chapter,
    render_five_layer_block,
)
from bestseller.services.deduplication import (
    build_arc_beat_block,
    build_five_layer_thinking_block,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Arc types + taxonomy
# ---------------------------------------------------------------------------

def test_six_arc_types_defined() -> None:
    assert len(ARC_TYPES) == 6
    assert "POSITIVE_CHANGE" in ARC_TYPES
    assert "FLAT_NEGATIVE" in ARC_TYPES


def test_beat_table_is_ordered_by_low_percentile() -> None:
    lows = [entry[0] for entry in BEAT_TABLE]
    assert lows == sorted(lows)


def test_beat_order_matches_beat_table() -> None:
    assert len(BEAT_ORDER) == len(BEAT_TABLE)


# ---------------------------------------------------------------------------
# CharacterInnerStructure
# ---------------------------------------------------------------------------

def test_inner_structure_is_complete_requires_load_bearing_fields() -> None:
    partial = CharacterInnerStructure(lie_believed="x", want_external="y")
    assert not partial.is_complete()

    full = CharacterInnerStructure(
        lie_believed="独自扛下一切才是强",
        truth_to_learn="被保护不等于软弱",
        want_external="抓出杀母凶手",
        need_internal="允许自己被爱",
    )
    assert full.is_complete()


def test_inner_structure_round_trip_through_dict() -> None:
    s = CharacterInnerStructure(
        ghost="母亲死在她面前",
        lie_believed="相信会被背叛",
        truth_to_learn="有人值得信任",
        want_external="查出真相",
        need_internal="放下戒备",
        defense_mechanisms=("讽刺", "过度工作"),
        arc_type="POSITIVE_CHANGE",
    )
    d = s.as_dict()
    assert d["defense_mechanisms"] == ["讽刺", "过度工作"]
    assert d["arc_type"] == "POSITIVE_CHANGE"
    reparsed = CharacterInnerStructure.from_dict(d)
    assert reparsed == s


def test_inner_structure_from_dict_handles_missing_fields() -> None:
    s = CharacterInnerStructure.from_dict({})
    assert s.lie_believed is None
    assert s.arc_type == "POSITIVE_CHANGE"
    assert s.defense_mechanisms == ()


# ---------------------------------------------------------------------------
# compute_arc_stage_for_chapter
# ---------------------------------------------------------------------------

def test_compute_arc_stage_early_chapter_is_normal_world() -> None:
    stage = compute_arc_stage_for_chapter(1, 100)
    assert stage["primary_beat"] == "normal_world"
    assert "normal_world" in stage["active_beats"]


def test_compute_arc_stage_at_midpoint_hits_midpoint_beat() -> None:
    stage = compute_arc_stage_for_chapter(50, 100)
    assert stage["primary_beat"] == "midpoint_false_victory"


def test_compute_arc_stage_at_90_percent_is_climax() -> None:
    stage = compute_arc_stage_for_chapter(90, 100)
    assert stage["primary_beat"] == "climax"


def test_compute_arc_stage_handles_zero_total() -> None:
    stage = compute_arc_stage_for_chapter(1, 0)
    assert stage["total_chapters"] == 1


def test_beats_elapsed_before_returns_past_beats() -> None:
    elapsed = beats_elapsed_before(50, 100)
    assert "normal_world" in elapsed
    assert "climax" not in elapsed


# ---------------------------------------------------------------------------
# 5-layer thinking contract
# ---------------------------------------------------------------------------

def test_render_five_layer_block_zh_includes_all_five_layers() -> None:
    block = render_five_layer_block(language="zh-CN")
    for layer in ("SENSATION", "PERCEPTION", "JUDGMENT", "DECISION", "RATIONALIZATION"):
        assert layer in block


def test_render_five_layer_block_en_fallback() -> None:
    block = render_five_layer_block(language="en")
    assert "SENSATION" in block
    assert "nails biting" in block


def test_build_five_layer_thinking_block_wrapper() -> None:
    block = build_five_layer_thinking_block(language="zh-CN")
    assert "RATIONALIZATION" in block


# ---------------------------------------------------------------------------
# build_arc_beat_block
# ---------------------------------------------------------------------------

def test_build_arc_beat_block_with_inner_structure() -> None:
    inner = {
        "lie_believed": "独自扛下一切才是强",
        "truth_to_learn": "有人值得信任",
        "want_external": "查出真相",
        "need_internal": "允许被爱",
        "ghost": "母亲死",
        "arc_type": "POSITIVE_CHANGE",
    }
    block = build_arc_beat_block(
        inner, chapter_number=50, total_chapters=100,
        pov_name="林鸢", language="zh-CN",
    )
    assert "林鸢" in block
    assert "独自扛下一切才是强" in block
    assert "有人值得信任" in block
    assert "midpoint_false_victory" in block


def test_build_arc_beat_block_fracture_beat_flags_crack() -> None:
    inner = {
        "lie_believed": "x", "truth_to_learn": "y",
        "want_external": "z", "need_internal": "w",
    }
    block = build_arc_beat_block(
        inner, chapter_number=76, total_chapters=100,
        pov_name="林鸢", language="zh-CN",
    )
    assert "裂缝期" in block or "dark_night" in block


def test_build_arc_beat_block_without_inner_structure() -> None:
    block = build_arc_beat_block(
        None, chapter_number=10, total_chapters=100, language="zh-CN",
    )
    assert "尚无 POV 内在结构" in block


def test_build_arc_beat_block_english() -> None:
    block = build_arc_beat_block(
        None, chapter_number=10, total_chapters=100, language="en",
    )
    assert "POV CHARACTER ARC" in block


# ---------------------------------------------------------------------------
# Forbidden emotion words
# ---------------------------------------------------------------------------

def test_forbidden_emotion_words_not_empty() -> None:
    assert "害怕" in FORBIDDEN_EMOTION_WORDS_ZH
    assert "afraid" in FORBIDDEN_EMOTION_WORDS_EN
