"""Tests for ``bestseller.services.character_lifecycle`` — the
fake-death predicate and the flashback / memorial / vision scene
recognisers.

These helpers are the single source of truth for "is this character
actually dead in chapter N?" and "is this scene / passage allowed to
mention a deceased character?" — so every consumer (prompt rendering,
contradiction checks, post-write scanner) reaches the same conclusion.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.character_lifecycle import (
    FLASHBACK_SCENE_MODES,
    fake_death_revealed_at_chapter,
    filter_alive_at_chapter,
    is_character_dead_at_chapter,
    prose_window_is_flashback,
    scene_is_flashback_like,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# fake_death_revealed_at_chapter
# ---------------------------------------------------------------------------


class TestFakeDeathRevealed:
    def test_no_metadata_is_not_revealed(self) -> None:
        assert fake_death_revealed_at_chapter(None, 100) is False
        assert fake_death_revealed_at_chapter({}, 100) is False

    def test_no_fake_death_record_is_not_revealed(self) -> None:
        assert fake_death_revealed_at_chapter({"unrelated": True}, 100) is False

    def test_reveal_chapter_in_future_is_not_revealed(self) -> None:
        meta = {"fake_death": {"revealed_chapter": 50}}
        assert fake_death_revealed_at_chapter(meta, 49) is False

    def test_reveal_chapter_at_current_is_revealed(self) -> None:
        meta = {"fake_death": {"revealed_chapter": 50}}
        assert fake_death_revealed_at_chapter(meta, 50) is True

    def test_reveal_chapter_in_past_is_revealed(self) -> None:
        meta = {"fake_death": {"revealed_chapter": 50}}
        assert fake_death_revealed_at_chapter(meta, 200) is True

    def test_unparsable_reveal_chapter_is_not_revealed(self) -> None:
        meta = {"fake_death": {"revealed_chapter": "not-a-number"}}
        assert fake_death_revealed_at_chapter(meta, 100) is False


# ---------------------------------------------------------------------------
# is_character_dead_at_chapter
# ---------------------------------------------------------------------------


class TestIsCharacterDeadAtChapter:
    def test_no_death_chapter_is_alive(self) -> None:
        assert is_character_dead_at_chapter(
            death_chapter_number=None, chapter_number=10
        ) is False

    def test_planned_death_in_future_is_alive(self) -> None:
        # Planner schedules a death at ch435 — the character is alive
        # in every chapter before that. This is the case that produced
        # the ch6 苏瑶 / 陆沉 incident.
        assert is_character_dead_at_chapter(
            death_chapter_number=435, chapter_number=6
        ) is False

    def test_death_at_current_chapter_is_dead(self) -> None:
        # The death event happens in this chapter; the lifecycle test
        # treats them as dead from the chapter onward.
        assert is_character_dead_at_chapter(
            death_chapter_number=10, chapter_number=10
        ) is True

    def test_past_death_with_no_fake_death_is_dead(self) -> None:
        assert is_character_dead_at_chapter(
            death_chapter_number=5, chapter_number=20
        ) is True

    def test_fake_death_before_reveal_is_still_dead(self) -> None:
        # Apparent death at ch5; fake-death reveal scheduled for ch12.
        # In ch10 the character is still presumed dead.
        meta = {"fake_death": {"revealed_chapter": 12}}
        assert is_character_dead_at_chapter(
            death_chapter_number=5,
            chapter_number=10,
            character_metadata=meta,
        ) is True

    def test_fake_death_after_reveal_is_alive(self) -> None:
        meta = {"fake_death": {"revealed_chapter": 12}}
        assert is_character_dead_at_chapter(
            death_chapter_number=5,
            chapter_number=20,
            character_metadata=meta,
        ) is False


# ---------------------------------------------------------------------------
# scene_is_flashback_like
# ---------------------------------------------------------------------------


class TestSceneIsFlashbackLike:
    def test_none_scene_is_not_flashback(self) -> None:
        assert scene_is_flashback_like(None) is False

    def test_scene_type_flashback_is_recognised(self) -> None:
        scene = SimpleNamespace(scene_type="flashback", metadata_json={})
        assert scene_is_flashback_like(scene) is True

    def test_scene_type_memorial_is_recognised(self) -> None:
        scene = SimpleNamespace(scene_type="memorial", metadata_json={})
        assert scene_is_flashback_like(scene) is True

    def test_scene_metadata_scene_mode_recognised(self) -> None:
        scene = SimpleNamespace(
            scene_type="setup",
            metadata_json={"scene_mode": "vision"},
        )
        assert scene_is_flashback_like(scene) is True

    def test_scene_metadata_is_flashback_flag(self) -> None:
        scene = SimpleNamespace(
            scene_type="setup",
            metadata_json={"is_flashback": True},
        )
        assert scene_is_flashback_like(scene) is True

    def test_dict_scene_works(self) -> None:
        scene = {"scene_type": "回忆", "metadata_json": {}}
        assert scene_is_flashback_like(scene) is True

    def test_normal_scene_is_not_flashback(self) -> None:
        scene = SimpleNamespace(scene_type="setup", metadata_json={})
        assert scene_is_flashback_like(scene) is False

    def test_all_canonical_modes_present(self) -> None:
        # Sanity that the canonical mode set still covers what the
        # planner is allowed to emit.
        for mode in ("flashback", "memorial", "vision", "dream",
                     "quoted_reference", "回忆", "闪回", "梦境"):
            assert mode in FLASHBACK_SCENE_MODES


# ---------------------------------------------------------------------------
# prose_window_is_flashback
# ---------------------------------------------------------------------------


class TestProseWindowIsFlashback:
    def test_zh_remembered_marker(self) -> None:
        assert prose_window_is_flashback("他回想起那年的雨夜") is True

    def test_zh_funeral_marker(self) -> None:
        assert prose_window_is_flashback("葬礼那天，所有人都到了") is True

    def test_zh_will_marker(self) -> None:
        assert prose_window_is_flashback("他从抽屉里翻出那封遗书") is True

    def test_zh_present_tense_no_match(self) -> None:
        assert prose_window_is_flashback("苏瑶冷冷地看了他一眼") is False

    def test_en_remembered(self) -> None:
        assert prose_window_is_flashback(
            "He remembered the night Su Yao went silent forever",
            is_english=True,
        ) is True

    def test_en_present_tense_no_match(self) -> None:
        assert prose_window_is_flashback(
            "Su Yao stared at the rain-slick floor.", is_english=True,
        ) is False

    def test_empty_text_is_not_flashback(self) -> None:
        assert prose_window_is_flashback("") is False


# ---------------------------------------------------------------------------
# filter_alive_at_chapter
# ---------------------------------------------------------------------------


class TestFilterAliveAtChapter:
    def test_filters_planned_future_deaths_out_of_dead_set(self) -> None:
        rows = [
            SimpleNamespace(
                name="苏瑶",
                death_chapter_number=435,
                metadata_json=None,
            ),
        ]
        # In chapter 6 苏瑶's planned death is in the future, so they
        # are *not* dead. ``filter_alive_at_chapter`` returns alive-only.
        assert len(filter_alive_at_chapter(rows, 6)) == 1

    def test_keeps_truly_dead_characters_out(self) -> None:
        rows = [
            SimpleNamespace(
                name="王守真",
                death_chapter_number=10,
                metadata_json=None,
            ),
        ]
        # In chapter 100 王守真 is genuinely dead — filter excludes them.
        assert filter_alive_at_chapter(rows, 100) == []

    def test_fake_death_revealed_returns_to_alive(self) -> None:
        rows = [
            SimpleNamespace(
                name="林霄",
                death_chapter_number=12,
                metadata_json={"fake_death": {"revealed_chapter": 30}},
            ),
        ]
        # Before reveal: dead. After reveal: alive.
        assert filter_alive_at_chapter(rows, 20) == []
        kept = filter_alive_at_chapter(rows, 50)
        assert len(kept) == 1
        assert kept[0].name == "林霄"
