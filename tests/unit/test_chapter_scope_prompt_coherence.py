"""Guards against per-scene obligations leaking into a whole-chapter prompt.

Root cause (2026-07-20): the chapter-first system prompt told the writer to
"把所有场景揉进一段连续叙事" while its own project profile and serial guardrails
demanded "每场必须包含明确目标、阻碍、升级、信息变化和尾钩". A writer obeying
both produces three mini-arcs each ending on a mini-hook — the stitched shape
the chapter-first unit exists to eliminate. The obligation is not dropped; it is
restated once per chapter.
"""

from __future__ import annotations

import pytest

from bestseller.services.writing_profile import (
    render_serial_fiction_guardrails,
    render_writing_profile_prompt_block,
    resolve_writing_profile,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def profile():
    return resolve_writing_profile(None, genre="仙侠", language="zh-CN")


class TestSerialGuardrailScope:
    def test_scene_scope_keeps_the_per_scene_beat_rule(self, profile) -> None:
        """No-op guard: the scene path must not change behaviour."""

        block = render_serial_fiction_guardrails(profile, language="zh-CN")
        assert "每场必须包含明确目标" in block

    def test_default_scope_is_scene(self, profile) -> None:
        assert render_serial_fiction_guardrails(
            profile, language="zh-CN"
        ) == render_serial_fiction_guardrails(profile, language="zh-CN", scope="scene")

    def test_chapter_scope_restates_the_obligation_at_chapter_level(self, profile) -> None:
        block = render_serial_fiction_guardrails(profile, language="zh-CN", scope="chapter")
        assert "每场必须包含明确目标" not in block
        assert "整章必须有明确目标" in block
        assert "章末钩子" in block

    def test_chapter_scope_does_not_drop_the_beat_obligations(self, profile) -> None:
        """Removing the contradiction must not remove the requirement."""

        block = render_serial_fiction_guardrails(profile, language="zh-CN", scope="chapter")
        for beat in ("目标", "阻碍", "升级", "信息变化"):
            assert beat in block

    def test_english_chapter_scope_also_restates_at_chapter_level(self, profile) -> None:
        block = render_serial_fiction_guardrails(profile, language="en", scope="chapter")
        assert "Every scene needs a goal" not in block
        assert "The chapter needs a goal" in block


class TestWritingProfileChapterMode:
    def test_scene_mode_keeps_the_scene_drive_rule(self, profile) -> None:
        block = render_writing_profile_prompt_block(
            profile, language="zh-CN", mode="scene", chapter_number=8
        )
        assert "每场" in block

    def test_chapter_mode_drops_the_scene_drive_rule(self, profile) -> None:
        block = render_writing_profile_prompt_block(
            profile, language="zh-CN", mode="chapter", chapter_number=8
        )
        assert "每场" not in block

    def test_chapter_mode_keeps_the_rest_of_the_scene_diet(self, profile) -> None:
        """chapter mode is the scene diet minus one line, not a different block."""

        scene = render_writing_profile_prompt_block(
            profile, language="zh-CN", mode="scene", chapter_number=8
        )
        chapter = render_writing_profile_prompt_block(
            profile, language="zh-CN", mode="chapter", chapter_number=8
        )
        assert len(chapter) < len(scene)
        assert profile.serialization.exposition_rule in chapter
        assert profile.serialization.chapter_ending_rule in chapter

    def test_english_chapter_mode_drops_the_scene_drive_rule(self, profile) -> None:
        block = render_writing_profile_prompt_block(
            profile, language="en", mode="chapter", chapter_number=8
        )
        assert profile.serialization.scene_drive_rule not in block


class TestRenderedChapterFirstPromptIsInternallyConsistent:
    def test_no_per_scene_hook_demand_survives_in_the_system_prompt(self) -> None:
        from _prose_prompt_fixtures import build_chapter_first_system_prompt

        prompt = build_chapter_first_system_prompt()
        assert "揉进一段连续叙事" in prompt
        assert "每场必须包含明确目标" not in prompt
        assert "每场都要有目标" not in prompt

    def test_chapter_level_beat_obligation_is_present(self) -> None:
        from _prose_prompt_fixtures import build_chapter_first_system_prompt

        assert "整章必须有明确目标" in build_chapter_first_system_prompt()
