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
    LIFECYCLE_KINDS,
    OFFSTAGE_KINDS,
    appearance_rule_for,
    characters_offstage_at_chapter,
    effective_lifecycle_state,
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


class TestAppearanceRuleFor:
    """Each canonical lifecycle kind exposes a single source of truth for
    appearance rules. Other layers (prompt, contradiction check, scene
    filter) all consult these rules so behaviour stays aligned."""

    def test_alive_can_act(self) -> None:
        rule = appearance_rule_for("alive")
        assert rule.can_act_in_present is True
        assert rule.can_be_remembered is True

    def test_deceased_cannot_act(self) -> None:
        rule = appearance_rule_for("deceased")
        assert rule.can_act_in_present is False
        assert rule.can_be_remembered is True
        assert rule.can_appear_as_body is True
        assert rule.can_appear_in_flashback is True

    def test_missing_can_return_without_resurrection(self) -> None:
        rule = appearance_rule_for("missing")
        assert rule.can_act_in_present is False
        assert rule.can_return_without_resurrection_block is True

    def test_sealed_cannot_act_but_body_referencable(self) -> None:
        rule = appearance_rule_for("sealed")
        assert rule.can_act_in_present is False
        assert rule.can_appear_as_body is True

    def test_sleeping_cannot_act(self) -> None:
        assert appearance_rule_for("sleeping").can_act_in_present is False

    def test_unknown_kind_falls_back_to_alive(self) -> None:
        rule = appearance_rule_for("nonsense")
        assert rule.kind == "alive"
        assert rule.can_act_in_present is True


class TestEffectiveLifecycleState:
    """Resolution priority — the helper picks rich lifecycle metadata
    over the legacy alive_status column when both exist, but falls back
    cleanly when the rich record is absent / inactive."""

    def test_legacy_alive_status_used_when_no_metadata(self) -> None:
        kind, payload = effective_lifecycle_state(
            alive_status="injured",
            death_chapter_number=None,
            chapter_number=10,
            character_metadata=None,
        )
        assert kind == "injured"
        assert payload["source"] == "alive_status"

    def test_death_chapter_overrides_legacy_status(self) -> None:
        kind, _ = effective_lifecycle_state(
            alive_status="alive",
            death_chapter_number=5,
            chapter_number=20,
            character_metadata=None,
        )
        assert kind == "deceased"

    def test_active_lifecycle_metadata_wins(self) -> None:
        meta = {
            "lifecycle_status": {
                "kind": "sealed",
                "since_chapter": 5,
                "scheduled_exit_chapter": 200,
            }
        }
        kind, payload = effective_lifecycle_state(
            alive_status="alive",
            death_chapter_number=None,
            chapter_number=50,
            character_metadata=meta,
        )
        assert kind == "sealed"
        assert payload["scheduled_exit_chapter"] == 200

    def test_lifecycle_metadata_expires_after_scheduled_exit(self) -> None:
        meta = {
            "lifecycle_status": {
                "kind": "sealed",
                "since_chapter": 5,
                "scheduled_exit_chapter": 200,
            }
        }
        kind, _ = effective_lifecycle_state(
            alive_status="alive",
            death_chapter_number=None,
            chapter_number=250,  # past the scheduled release
            character_metadata=meta,
        )
        assert kind == "alive"

    def test_lifecycle_metadata_inactive_before_since(self) -> None:
        meta = {
            "lifecycle_status": {
                "kind": "missing",
                "since_chapter": 50,
            }
        }
        kind, _ = effective_lifecycle_state(
            alive_status="alive",
            death_chapter_number=None,
            chapter_number=10,  # before the missing event
            character_metadata=meta,
        )
        assert kind == "alive"

    def test_unknown_kind_in_metadata_falls_back(self) -> None:
        meta = {"lifecycle_status": {"kind": "frozen-by-aliens"}}
        kind, _ = effective_lifecycle_state(
            alive_status="alive",
            death_chapter_number=None,
            chapter_number=10,
            character_metadata=meta,
        )
        # Unknown kind ignored → falls back to alive_status / death.
        assert kind == "alive"


class TestCharactersOffstageAtChapter:
    """Roster-level helper that picks every offstage character in one
    pass — used by the chapter prompt loader."""

    def test_picks_missing_and_sealed_skips_alive(self) -> None:
        rows = [
            SimpleNamespace(
                name="陆沉", alive_status="alive", death_chapter_number=None,
                metadata_json={"lifecycle_status": {"kind": "missing", "since_chapter": 5}},
            ),
            SimpleNamespace(
                name="苏瑶", alive_status="alive", death_chapter_number=None,
                metadata_json={"lifecycle_status": {"kind": "sealed", "since_chapter": 8, "scheduled_exit_chapter": 100}},
            ),
            SimpleNamespace(
                name="宁尘", alive_status="alive", death_chapter_number=None,
                metadata_json=None,
            ),
        ]
        out = characters_offstage_at_chapter(rows, 50)
        kinds = {row.name: kind for row, kind, _ in out}
        assert kinds == {"陆沉": "missing", "苏瑶": "sealed"}

    def test_includes_deceased(self) -> None:
        rows = [
            SimpleNamespace(
                name="王守真", alive_status="deceased", death_chapter_number=10,
                metadata_json=None,
            ),
        ]
        out = characters_offstage_at_chapter(rows, 50)
        assert len(out) == 1
        assert out[0][1] == "deceased"

    def test_kinds_all_in_canonical_set(self) -> None:
        for kind in OFFSTAGE_KINDS:
            assert kind in LIFECYCLE_KINDS


class TestFilterDeadSceneParticipants:
    """Integration of the lifecycle helper with the scene-card filter
    used by the chapter auto-repair path. The filter must:

    1. Remove dead participants from a normal scene's participant list.
    2. Skip the removal entirely when the scene is flashback / memorial /
       vision / dream — those modes legitimately stage the deceased as
       memory, mourning, quoted, or relic.
    """

    def test_filter_strips_dead_in_normal_scene(self) -> None:
        from bestseller.services.drafts import _filter_dead_scene_participants

        scene = SimpleNamespace(
            scene_type="setup",
            metadata_json={},
            participants=["陆沉", "宁尘"],
        )
        removed = _filter_dead_scene_participants(scene, frozenset({"陆沉"}))
        assert "陆沉" in removed
        assert scene.participants == ["宁尘"]

    def test_filter_skips_flashback_scene(self) -> None:
        from bestseller.services.drafts import _filter_dead_scene_participants

        scene = SimpleNamespace(
            scene_type="flashback",
            metadata_json={},
            participants=["陆沉", "宁尘"],
        )
        removed = _filter_dead_scene_participants(scene, frozenset({"陆沉"}))
        assert removed == []
        # Participants list is left untouched — the deceased may speak in
        # flashback as remembered prior dialogue.
        assert scene.participants == ["陆沉", "宁尘"]

    def test_filter_skips_memorial_scene_via_metadata(self) -> None:
        from bestseller.services.drafts import _filter_dead_scene_participants

        scene = SimpleNamespace(
            scene_type="setup",
            metadata_json={"scene_mode": "memorial"},
            participants=["陆沉", "宁尘"],
        )
        removed = _filter_dead_scene_participants(scene, frozenset({"陆沉"}))
        assert removed == []
        assert scene.participants == ["陆沉", "宁尘"]


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
