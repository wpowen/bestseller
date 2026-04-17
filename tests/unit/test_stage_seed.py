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
