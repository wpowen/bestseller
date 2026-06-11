from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.drafts import select_scenes_for_auto_repair

pytestmark = pytest.mark.unit


def _scenes(n: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(scene_number=i + 1) for i in range(n)]


def test_ending_hook_only_resets_last_scene() -> None:
    scenes = _scenes(3)
    selected = select_scenes_for_auto_repair(scenes, ("ENDING_HOOK_MISSING",))
    assert selected == [scenes[2]]


def test_hook_echo_only_resets_first_scene() -> None:
    scenes = _scenes(3)
    selected = select_scenes_for_auto_repair(
        scenes, ("HOOK_ECHO_MISSING", "OPENING_PRESSURE_THIN")
    )
    assert selected == [scenes[0]]


def test_both_positional_codes_reset_first_and_last() -> None:
    scenes = _scenes(3)
    selected = select_scenes_for_auto_repair(
        scenes, ("HOOK_ECHO_MISSING", "ENDING_HOOK_MISSING")
    )
    assert selected == [scenes[0], scenes[2]]


def test_non_positional_code_keeps_whole_chapter_reset() -> None:
    scenes = _scenes(3)
    selected = select_scenes_for_auto_repair(
        scenes, ("ENDING_HOOK_MISSING", "PERSONA_WEIGHTED_SCORE_LOW")
    )
    assert selected == scenes


def test_single_scene_chapter_not_duplicated() -> None:
    scenes = _scenes(1)
    selected = select_scenes_for_auto_repair(
        scenes, ("HOOK_ECHO_MISSING", "ENDING_HOOK_MISSING")
    )
    assert selected == [scenes[0]]


def test_empty_codes_keep_all_scenes() -> None:
    scenes = _scenes(2)
    assert select_scenes_for_auto_repair(scenes, ()) == scenes
