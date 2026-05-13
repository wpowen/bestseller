from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.story_shape_router import derive_story_shape

pytestmark = pytest.mark.unit


def test_very_long_web_serial_uses_volume_chapter_scene_depth() -> None:
    shape = derive_story_shape(
        genre="仙侠升级",
        target_chapters=520,
        metadata={"publication_mode": "web_serial"},
    )

    assert shape.length_class == "series"
    assert shape.publication_mode == "web_serial"
    assert shape.outline_depth == "volume_chapter_scene"
    assert "forward_pull" in shape.primary_duties
    assert "measurable_progression" in shape.primary_duties


def test_short_literary_project_uses_scene_depth_and_theme_duty() -> None:
    shape = derive_story_shape(
        genre="现实文学",
        target_chapters=12,
        target_word_count=42000,
        metadata={"publication_mode": "literary"},
    )

    assert shape.length_class == "short"
    assert shape.publication_mode == "literary"
    assert shape.outline_depth == "scene"
    assert "theme_or_perception_turn" in shape.primary_duties
    assert "forward_pull" not in shape.primary_duties


def test_mystery_project_adds_fair_play_duty() -> None:
    shape = derive_story_shape(
        genre="悬疑推理",
        target_chapters=80,
        metadata={"publication_mode": "commercial_book"},
    )

    assert shape.length_class == "long"
    assert shape.publication_mode == "commercial_book"
    assert "fair_play_clue_movement" in shape.primary_duties


def test_relationship_project_from_project_metadata_adds_state_shift() -> None:
    project = SimpleNamespace(
        genre="女频",
        sub_genre="言情成长",
        target_chapters=90,
        metadata_json={"story_facets": {"relationship_mode": "slow-burn"}},
    )

    shape = derive_story_shape(project)

    assert shape.length_class == "long"
    assert "relationship_state_shift" in shape.primary_duties
    assert shape.source_signals["genre"] == "女频"
