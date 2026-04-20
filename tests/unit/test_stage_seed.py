"""Unit tests for Stage 0 metadata seeders."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.pacing_engine import CLIFFHANGER_TYPES
from bestseller.services.stage_seed import (
    seed_chapter_metadata,
    seed_character_inner_structure,
    seed_scene_metadata,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# seed_scene_metadata
# ---------------------------------------------------------------------------

def test_seed_scene_metadata_empty_card_returns_empty_dict() -> None:
    assert seed_scene_metadata({}) == {}


def test_seed_scene_metadata_classifies_pursuit_scene_from_goal() -> None:
    card = {
        "title": "下水道追击",
        "chapter_goal": "主角在下水道被追击，必须甩开追兵",
        "main_conflict": "敌人逼近",
    }
    seed = seed_scene_metadata(card)
    assert seed["scene_purpose_id"] == "pursuit"
    assert seed["location_id"] == "下水道" or seed["location_id"].startswith("loc_")
    assert seed["conflict_tuple"]["object"] in ("person", "group", "self")
    assert seed["conflict_tuple"]["layer"]


def test_seed_scene_metadata_classifies_revelation() -> None:
    card = {"chapter_goal": "揭示母亲的真相"}
    seed = seed_scene_metadata(card)
    assert seed["scene_purpose_id"] == "revelation"


def test_seed_scene_metadata_skips_conflict_tuple_when_text_is_generic() -> None:
    card = {"chapter_goal": "日常"}
    seed = seed_scene_metadata(card)
    # No classifier hit in OBJECT_KEYWORDS or LAYER_KEYWORDS → no conflict_tuple.
    assert "conflict_tuple" not in seed


# ---------------------------------------------------------------------------
# env_7d classifier
# ---------------------------------------------------------------------------

def test_seed_scene_metadata_classifies_env_7d_from_prose() -> None:
    card = {
        "title": "地下深夜潜行",
        "chapter_goal": (
            "深夜，主角独自潜入地下密室，手持火把，漆黑的甬道里只有脚步声回荡；"
            "他蹑手蹑脚，屏住呼吸。"
        ),
    }
    seed = seed_scene_metadata(card)
    env = seed.get("env_7d")
    assert env is not None, "env_7d should be emitted when keywords match"
    assert env["physical_space"] == "underground"
    assert env["time_of_day"] == "deep_night"
    assert env["weather_light"] == "artificial_light"
    # "脚步声回荡" → sound wins over "独自" on sight
    assert env["dominant_sense"] in ("sound", "sight")
    assert env["social_density"] == "alone"


def test_seed_scene_metadata_env_7d_detects_storm_over_mountain() -> None:
    card = {
        "chapter_goal": (
            "黄昏时分，两人站在山巅，暴风雨骤起，狂风呼啸着掠过峰顶，"
            "远处雷暴翻滚。"
        )
    }
    seed = seed_scene_metadata(card)
    env = seed.get("env_7d")
    assert env is not None
    assert env["time_of_day"] == "dusk"
    assert env["weather_light"] == "storm"
    assert env["vertical_enclosure"] == "elevated_open"
    assert env["social_density"] == "dyad"


def test_seed_scene_metadata_env_7d_requires_at_least_two_dims() -> None:
    # Only one classifier hit ("夜") → result should be dropped.
    card = {"chapter_goal": "日常"}  # no env keywords at all
    seed = seed_scene_metadata(card)
    assert "env_7d" not in seed


def test_seed_scene_metadata_env_7d_montage_time_scale() -> None:
    card = {
        "chapter_goal": "数日后的清晨，一行人离开山林，街巷熙攘，人群川流不息。",
    }
    seed = seed_scene_metadata(card)
    env = seed.get("env_7d")
    assert env is not None
    assert env["tempo_scale"] == "montage"
    assert env["social_density"] == "anonymous_crowd"


# ---------------------------------------------------------------------------
# Expanded hook-type classifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hook_text,expected",
    [
        ("身份彻底暴露，再也无法隐瞒", "revelation"),
        ("原来是他一手策划，真面目终于浮现", "revelation"),
        ("峰回路转，局势出乎意料地逆转", "twist"),
        ("九死一生，他命悬一线", "crisis"),
        # "imminent specific threat" now collapses into crisis per CLIFFHANGER_TYPES
        ("黑影步步紧逼，杀机将至", "crisis"),
        # "abruptly/suddenly happens" maps to `sudden`
        ("骤然炸裂一声巨响，所有人都愣住了", "sudden"),
        # decision/choice pivot now falls under `emotional` (内心重击)
        ("他咬牙下定决心，做出了抉择", "emotional"),
        ("她泪流满面，心碎成无数片", "emotional"),
        # mystery signals now collapse into suspense
        ("诡异的氛围让人不对劲，蹊跷之处愈发明显", "suspense"),
        ("戛然而止，留下悬而未决的余音", "suspense"),
        # philosophical open question
        ("何为正道？他望着苍生，陷入沉思", "philosophical"),
    ],
)
def test_classify_hook_type_expanded_keywords(hook_text: str, expected: str) -> None:
    seed = seed_chapter_metadata({"next_chapter_hook": hook_text}, 50, 100)
    assert seed.get("hook_type") == expected, (
        f"text={hook_text!r} got={seed.get('hook_type')!r} expected={expected!r}"
    )
    assert seed["hook_type"] in CLIFFHANGER_TYPES


def test_classify_hook_type_real_chapter_tail_sample() -> None:
    # Representative chapter ending drawn from a web-novel style: should classify
    # as crisis, not fall through to None.
    tail = (
        "他死死咬牙，鲜血顺着嘴角流下，绝境之中，命悬一线。"
        "远处传来追兵的喊杀声——下一个瞬间，他知道自己再也撑不住了。"
    )
    seed = seed_chapter_metadata({"next_chapter_hook": tail}, 30, 100)
    assert seed.get("hook_type") == "crisis"


# ---------------------------------------------------------------------------
# seed_chapter_metadata
# ---------------------------------------------------------------------------

def test_seed_chapter_metadata_returns_tension_and_beat() -> None:
    seed = seed_chapter_metadata({"next_chapter_hook": "危机"}, 50, 100)
    assert "tension_score" in seed
    assert "beat_id" in seed
    assert seed["hook_type"] == "crisis"
    assert seed["hook_type"] in CLIFFHANGER_TYPES


def test_seed_chapter_metadata_no_hook_text_omits_hook_type() -> None:
    seed = seed_chapter_metadata({}, 1, 100)
    assert "hook_type" not in seed
    assert seed["tension_score"] > 0


def test_seed_chapter_metadata_handles_zero_total() -> None:
    seed = seed_chapter_metadata({}, 1, 0)
    # No tension_score when total_chapters is zero.
    assert "tension_score" not in seed


# ---------------------------------------------------------------------------
# seed_character_inner_structure
# ---------------------------------------------------------------------------

def test_seed_character_inner_structure_with_pydantic_like_input() -> None:
    char = SimpleNamespace(
        goal="查出杀母真相",
        fear="再失去一个亲人",
        flaw="过度自我封闭",
        secret="母亲并非她亲生",
        arc_trajectory="positive change",
        arc_state="",
    )
    lta = {"core_lie": "独自扛下一切才是强", "core_truth": "有人值得信任"}
    structure = seed_character_inner_structure(char, lie_truth_arc=lta)
    assert structure is not None
    assert structure["lie_believed"] == "独自扛下一切才是强"
    assert structure["truth_to_learn"] == "有人值得信任"
    assert structure["want_external"] == "查出杀母真相"
    assert structure["ghost"] == "母亲并非她亲生"
    assert structure["fear_core"] == "再失去一个亲人"
    assert structure["fatal_flaw"] == "过度自我封闭"
    assert structure["arc_type"] == "POSITIVE_CHANGE"


def test_seed_character_inner_structure_maps_negative_arc() -> None:
    char = SimpleNamespace(
        goal="夺回王座",
        fear=None,
        flaw=None,
        secret=None,
        arc_trajectory="tragic fall",
        arc_state=None,
    )
    structure = seed_character_inner_structure(char, lie_truth_arc={"core_lie": "权力即正义"})
    assert structure is not None
    assert structure["arc_type"] == "FALL"


def test_seed_character_inner_structure_returns_none_when_all_empty() -> None:
    char = SimpleNamespace(
        goal=None, fear=None, flaw=None, secret=None,
        arc_trajectory=None, arc_state=None,
    )
    assert seed_character_inner_structure(char) is None


def test_seed_character_inner_structure_accepts_dict_input() -> None:
    char = {
        "goal": "复国",
        "secret": "亡国王子身份",
        "arc_trajectory": "flat",
    }
    structure = seed_character_inner_structure(char)
    assert structure is not None
    assert structure["want_external"] == "复国"
    assert structure["arc_type"] == "FLAT"
