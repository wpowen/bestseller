"""Guards for the 2026-07-22 creation-option audit fixes.

Four问题 the audit confirmed and this locks down:
- E-1: 玄幻/仙侠 emotion exemplars led with 灭门血仇 / 灭宗血仇, injected into the
  same conception prompt that forbids 亲属死亡 as a motive — a real death-motif
  source. Must not lead with a wiped clan / dead relative.
- F.5 / channel: conception dropped the user's channel and re-inferred 男频 from
  the genre; an explicit 通用 pick must yield the neutral reader.
- A: narrative_scale was collected but never changed the outline; 宏大长篇 must
  now reach planner.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.genre_persona import resolve_persona
from bestseller.services.planner import (
    _planner_narrative_scale_block,
    _story_enhancer_contract_line,
)
from bestseller.services.story_appeal import _DEFAULT_EMOTION_EXEMPLARS

pytestmark = pytest.mark.unit

_DEATH_MOTIFS = ("灭门", "灭宗", "痛失至亲", "满门", "血仇")


class TestEmotionExemplarsDropDeathMotif:
    """The exemplar is injected as 'put this emotion FIRST'; it must not fight
    the prompt's own 【默认动机禁用】 by leading with a dead relative."""

    @pytest.mark.parametrize("key", ["generic", "xuanhuan", "xianxia"])
    def test_no_death_motif_in_the_pool(self, key: str) -> None:
        pool = _DEFAULT_EMOTION_EXEMPLARS[key]
        for term in pool:
            assert not any(m in term for m in _DEATH_MOTIFS), (
                f"{key} still carries a death motif: {term}"
            )

    def test_still_high_arousal(self) -> None:
        """Removing death must not flatten the pool into mild words."""

        assert "逆袭碾压" in _DEFAULT_EMOTION_EXEMPLARS["xuanhuan"]


class TestChannelIsHonoured:
    def test_male_and_female_route_to_their_persona(self) -> None:
        assert resolve_persona("玄幻", "玄幻", channel="男频").channel == "男频"
        assert resolve_persona("言情", "甜宠", channel="女频").channel == "女频"

    def test_explicit_general_gives_the_neutral_reader(self) -> None:
        """通用 on a 玄幻 book used to fall through to 男频 inference — the very
        framework the user picked 通用 to escape."""

        assert resolve_persona("玄幻", "玄幻", channel="通用").channel == "通用"
        assert resolve_persona("玄幻", "玄幻", channel="general").channel == "通用"

    def test_no_channel_still_infers_from_genre(self) -> None:
        """No-op guard: absence of a pick keeps the old genre inference."""

        assert resolve_persona("玄幻", "玄幻", channel=None).channel == "男频"


class TestNarrativeScaleReachesPlanner:
    def _project(self, scale: str):
        return SimpleNamespace(
            genre="玄幻",
            sub_genre="玄幻",
            audience="男频",
            metadata_json={
                "genre_intent_contract": {"channel_key": "male", "narrative_scale": scale}
            },
        )

    def test_epic_injects_a_scale_instruction(self) -> None:
        assert "宏大长篇" in _planner_narrative_scale_block(self._project("epic"), "zh-CN")

    def test_serial_is_the_silent_baseline(self) -> None:
        assert _planner_narrative_scale_block(self._project("serial"), "zh-CN") == ""

    def test_epic_and_serial_produce_different_outline_lines(self) -> None:
        line_epic = _story_enhancer_contract_line(self._project("epic"), "zh-CN")
        line_serial = _story_enhancer_contract_line(self._project("serial"), "zh-CN")
        assert line_epic != line_serial
        assert "宏大长篇" in line_epic

    def test_english_epic_is_english(self) -> None:
        block = _planner_narrative_scale_block(self._project("epic"), "en")
        assert "EPIC" in block
        assert "宏大长篇" not in block
