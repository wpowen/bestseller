"""Guard: the reader channel (男频/女频) must drive the outline, not just concept.

Root cause (2026-07-22): a real book was created with a 男频 selection, and the
concept layer differentiated correctly, but ``planner`` never read the channel
at all (``audience_orientation``/``channel_key`` hit count was zero). The outline
came out channel-neutral. The channel now rides the shared outline injection
line so every volume/chapter prompt sees the reader persona.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.planner import (
    _planner_channel_key,
    _planner_reader_persona_block,
    _story_enhancer_contract_line,
)

pytestmark = pytest.mark.unit


def _project(*, channel: str | None, genre: str = "玄幻", audience: str | None = None):
    metadata: dict = {}
    if channel is not None:
        metadata["genre_intent_contract"] = {"channel_key": channel}
    return SimpleNamespace(
        genre=genre, sub_genre=genre, audience=audience, metadata_json=metadata
    )


class TestChannelResolution:
    def test_reads_channel_from_the_frozen_contract(self) -> None:
        assert _planner_channel_key(_project(channel="male")) == "male"

    def test_falls_back_to_project_audience_column(self) -> None:
        assert _planner_channel_key(_project(channel=None, audience="男频")) == "男频"

    def test_none_when_no_signal(self) -> None:
        assert _planner_channel_key(_project(channel=None)) is None


class TestPersonaBlock:
    def test_male_block_carries_male_fantasy(self) -> None:
        block = _planner_reader_persona_block(_project(channel="male"))
        assert "男频" in block
        assert "打脸" in block

    def test_female_block_carries_emotional_fantasy(self) -> None:
        block = _planner_reader_persona_block(
            _project(channel="female", genre="言情")
        )
        assert "女频" in block
        assert "情" in block  # 情绪/情感

    def test_male_and_female_are_actually_different(self) -> None:
        male = _planner_reader_persona_block(_project(channel="male"))
        female = _planner_reader_persona_block(
            _project(channel="female", genre="言情")
        )
        assert male != female

    def test_general_reader_genre_gets_no_channel_block(self) -> None:
        assert _planner_reader_persona_block(_project(channel=None, genre="悬疑")) == ""

    def test_genre_infers_channel_when_none_selected(self) -> None:
        """玄幻/都市 imply 男频, 言情 implies 女频 — same inference the concept
        layer uses, so concept and outline agree on the reader."""

        assert "男频" in _planner_reader_persona_block(_project(channel=None, genre="玄幻"))


class TestReachesOutlinePrompts:
    def test_persona_rides_the_shared_outline_injection_line(self) -> None:
        line = _story_enhancer_contract_line(_project(channel="male"), "zh-CN")
        assert "目标读者画像" in line
        assert "男频" in line

    def test_english_book_gets_no_chinese_persona(self) -> None:
        line = _story_enhancer_contract_line(_project(channel="male"), "en")
        assert "男频" not in line

    def test_genre_without_channel_signal_gets_no_persona(self) -> None:
        """A genre that resolves to the general reader (e.g. 悬疑) injects no
        channel persona; a genre that IS a channel signal (玄幻→男频) does, which
        matches how the concept layer already infers channel from genre."""

        neutral = _story_enhancer_contract_line(
            _project(channel=None, genre="悬疑"), "zh-CN"
        )
        assert "目标读者画像" not in neutral

        inferred = _story_enhancer_contract_line(
            _project(channel=None, genre="玄幻"), "zh-CN"
        )
        assert "男频" in inferred


class TestWebDefaultsToLeanChapter:
    """Front-created books had no lean/chapter entry point, so every one ran the
    legacy full+scene default the A/B judged unreadable."""

    def _server_source(self) -> str:
        import inspect

        from bestseller.web import server

        return inspect.getsource(server)

    def test_generation_unit_defaults_to_chapter(self) -> None:
        from bestseller.services.generation_policy import apply_new_project_generation_policy

        assert apply_new_project_generation_policy({})["generation_unit_mode"] == "chapter"

    def test_prose_profile_defaults_to_lean(self) -> None:
        from bestseller.services.generation_policy import apply_new_project_generation_policy

        assert apply_new_project_generation_policy({})["prose_prompt_profile"] == "lean"

    def test_explicit_scene_still_wins_over_default(self) -> None:
        """The default must only fill the else, never override an explicit pick."""

        from bestseller.services.generation_policy import apply_new_project_generation_policy

        result = apply_new_project_generation_policy(
            {}, generation_unit_mode="scene"
        )
        assert result["generation_unit_mode"] == "scene"
