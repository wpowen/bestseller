"""黄金三章 opening hard-contract must reach the DEFAULT (scene-first) writer for ch1-3.

Regression guard: the golden-three rules previously only lived in the chapter-first
generation path (``enable_chapter_first_generation``, OFF by default), so the default
scene-first writer never received the strongest opening contract — a major cause of
weak openings. They are now wired into the writer's SYSTEM prompt for ch1-3 (zh) via
``build_opening_hook_directive`` (which self-guards ch>3 / non-zh).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.drafts import build_scene_draft_prompts

_GOLDEN_MARKER = "【黄金三章·开篇硬契约"


def _fixtures(chapter_number: int):
    project = SimpleNamespace(title="测试书", slug="test-golden", metadata_json={})
    chapter = SimpleNamespace(
        chapter_number=chapter_number, chapter_goal="开场冲突", title="开篇"
    )
    scene = SimpleNamespace(
        scene_number=1,
        title="开场",
        participants=["主角"],
        purpose={"story": "亮出冲突", "emotion": "危机"},
        time_label="此刻",
        entry_state={},
        exit_state={},
        scene_type="opening",
        target_word_count=1000,
    )
    style_guide = SimpleNamespace(pov_type="third-limited", tone_keywords=["紧张"])
    return project, chapter, scene, style_guide


@pytest.mark.parametrize("chapter_number", [1, 2, 3])
def test_golden_three_contract_reaches_front_chapter_writer(chapter_number: int) -> None:
    project, chapter, scene, style_guide = _fixtures(chapter_number)
    system_prompt, _ = build_scene_draft_prompts(project, chapter, scene, style_guide)
    assert _GOLDEN_MARKER in system_prompt
    # a concrete, enforceable rule actually reaches the model
    assert "第一句长度 ≤ 25 个汉字" in system_prompt


@pytest.mark.parametrize("chapter_number", [4, 10, 50])
def test_golden_three_contract_absent_after_front_chapters(chapter_number: int) -> None:
    project, chapter, scene, style_guide = _fixtures(chapter_number)
    system_prompt, _ = build_scene_draft_prompts(project, chapter, scene, style_guide)
    assert _GOLDEN_MARKER not in system_prompt
