"""R23 regression: scene writer word-budget obedience block.

The scene word target (``scene.target_word_count``) was present mid-prompt but
systematically ignored by the writer (700-word scenes ballooning to 1800+ →
LENGTH_OVER, then over-corrected to LENGTH_UNDER on repair; ch3/5/6/9
oscillation).  Fix = a "scene hard acceptance" block rendered from live scene
data and injected at the VERY FRONT of the user prompt (before every other
constraint block), carrying:

1. the explicit numeric budget band (target ±15%) plus the wrap-up
   instruction ("write to the ceiling → close the scene, no new events");
2. the scene's ``signature_image`` / ``object_signal`` prose obligations;
3. de-dup of the legacy mid-prompt scene-level word band (the chapter-level
   ``chapter_length_block`` is a different bandwidth and stays).

The block must also survive context budgeting (Tier 1, not droppable).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services import drafts
from bestseller.services.drafts import (
    _render_scene_word_budget_block,
    build_scene_draft_prompts,
)

pytestmark = pytest.mark.unit

_ZH_HEADER = "=== 本场硬验收（最高优先级）==="
_EN_HEADER = "=== Scene hard acceptance (highest priority) ==="


def _scene(
    target: int | None = 700,
    metadata: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        scene_number=2,
        title="对峙",
        participants=["主角", "对手"],
        purpose={"story": "推进冲突", "emotion": "压迫"},
        time_label="当夜",
        entry_state={},
        exit_state={},
        scene_type="confrontation",
        target_word_count=target,
        metadata_json=metadata or {},
    )


def _fixtures(scene: SimpleNamespace, *, language: str = "zh-CN"):
    project = SimpleNamespace(
        title="测试书", slug="test-r23", metadata_json={}, language=language
    )
    chapter = SimpleNamespace(
        chapter_number=5, chapter_goal="冲突升级", title="第五章"
    )
    style_guide = SimpleNamespace(pov_type="third-limited", tone_keywords=["紧张"])
    return project, chapter, scene, style_guide


# ---------------------------------------------------------------------------
# Block renderer (unit)
# ---------------------------------------------------------------------------


def test_block_renders_numeric_budget_band_from_scene() -> None:
    block = _render_scene_word_budget_block(_scene(700), is_en=False)
    assert block.startswith(_ZH_HEADER)
    assert "本场字数预算：700字" in block
    assert "595-805" in block  # ±15% explicit numeric range
    assert "写到预算上限必须收束本场，不得开新事件" in block


def test_block_includes_signature_image_and_object_signal() -> None:
    scene = _scene(
        700,
        metadata={
            "methodology_contract": {"signature_image": "断裂的玉佩"},
            "object_signal": "铜镜泛绿光",
        },
    )
    block = _render_scene_word_budget_block(scene, is_en=False)
    assert "本场必须把「断裂的玉佩」写成可见画面" in block
    assert "「铜镜泛绿光」" in block


def test_block_falls_back_to_scene_contract_visible_object() -> None:
    """signature_image reuses the existing contract-controls reading point."""
    scene = _scene(700, metadata={"scene_contract": {"visible_object": "半张当票"}})
    block = _render_scene_word_budget_block(scene, is_en=False)
    assert "「半张当票」" in block


def test_block_empty_without_target_and_signature() -> None:
    assert _render_scene_word_budget_block(_scene(0), is_en=False) == ""
    assert _render_scene_word_budget_block(_scene(None), is_en=False) == ""


def test_block_signature_only_without_target_still_renders() -> None:
    scene = _scene(0, metadata={"methodology_contract": {"signature_image": "血字"}})
    block = _render_scene_word_budget_block(scene, is_en=False)
    assert block.startswith(_ZH_HEADER)
    assert "「血字」" in block
    assert "字数预算" not in block


def test_block_en_branch() -> None:
    block = _render_scene_word_budget_block(_scene(800), is_en=True)
    assert block.startswith(_EN_HEADER)
    assert "800 words" in block
    assert "680-920" in block
    assert "do not open a new event" in block


# ---------------------------------------------------------------------------
# Prompt wiring (placement + de-dup + budget survival)
# ---------------------------------------------------------------------------


def test_zh_user_prompt_leads_with_hard_acceptance_block() -> None:
    project, chapter, scene, style_guide = _fixtures(_scene(700))
    _, user_prompt = build_scene_draft_prompts(project, chapter, scene, style_guide)
    assert user_prompt.startswith(_ZH_HEADER)
    assert "595-805" in user_prompt
    assert "写到预算上限必须收束本场，不得开新事件" in user_prompt


def test_en_user_prompt_leads_with_hard_acceptance_block() -> None:
    scene = _scene(700)
    project, chapter, scene, style_guide = _fixtures(scene, language="en-US")
    _, user_prompt = build_scene_draft_prompts(project, chapter, scene, style_guide)
    assert user_prompt.startswith(_EN_HEADER)
    assert "595-805" in user_prompt


def test_zh_prompt_dedups_legacy_scene_word_line() -> None:
    """No second, numerically conflicting scene-level band mid-prompt."""
    project, chapter, scene, style_guide = _fixtures(_scene(700))
    _, user_prompt = build_scene_draft_prompts(project, chapter, scene, style_guide)
    assert "【硬性要求】正文字数必须在" not in user_prompt
    # the mid-prompt line becomes a pointer to the top block
    assert "目标字数：700（硬性区间以顶部「本场硬验收」块为准）" in user_prompt
    # exactly one hard-acceptance header
    assert user_prompt.count(_ZH_HEADER) == 1


def test_signature_image_flows_into_prompt_front_block() -> None:
    scene = _scene(
        700, metadata={"methodology_contract": {"signature_image": "断裂的玉佩"}}
    )
    project, chapter, scene, style_guide = _fixtures(scene)
    _, user_prompt = build_scene_draft_prompts(project, chapter, scene, style_guide)
    front = user_prompt[:600]
    assert "本场必须把「断裂的玉佩」写成可见画面" in front


def test_block_is_tier1_and_survives_tight_budget() -> None:
    assert "scene_word_budget_line" in drafts._CONTEXT_TIER_1
    assert "scene_word_budget_line" not in drafts._TIER_1_DROPPABLE_GUARDRAILS
    block = _render_scene_word_budget_block(_scene(700), is_en=False)
    sections = {
        "scene_word_budget_line": block,
        "story_bible_section": "设定" * 4000,  # Tier 3 filler far over budget
    }
    result = drafts._budget_context_sections(sections, 200)
    assert result["scene_word_budget_line"] == block
