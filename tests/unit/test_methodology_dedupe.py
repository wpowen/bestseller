"""Regression tests for methodology-section dedup in the scene writer prompt.

Root cause (2026-06-10 prompt-attribution ladder): the production scene prompt
carried the SAME 8 writing-methodology subsections twice (rendered once by the
scene-rules bridge and once by compile_methodology) plus a duplicated 场景锚定
block (compiled methodology + quality levers). The duplicated ~4k chars cost
−1.5 judge points vs a deduped reassembly of identical content and produced the
only AI-flavor hits across all ladder arms. ``_dedupe_methodology_sections``
drops later exact-body duplicates; these tests pin that contract.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.drafts import (
    _dedupe_methodology_sections,
    build_scene_draft_prompts,
)
from bestseller.services.methodology import render_methodology_scene_rules

pytestmark = pytest.mark.unit


def test_baseline_craft_rules_gate_drops_only_paraphrase_restatements() -> None:
    """画面感规则/对话规则 are paraphrase-duplicates of compile_methodology's
    visual_writing/dialogue_rules; the scene-writer path suppresses them while
    keeping the context-specific bridge rules (开篇/七猫签约)."""
    common = dict(chapter_number=1, is_opening=True, pacing_mode="build")
    with_baseline = render_methodology_scene_rules(**common, include_baseline_craft_rules=True)
    without_baseline = render_methodology_scene_rules(**common, include_baseline_craft_rules=False)

    # default keeps them (review/other callers rely on this)
    assert "【画面感规则】" in with_baseline
    assert "【对话规则】" in with_baseline
    # gated path drops the paraphrase restatements …
    assert "【画面感规则】" not in without_baseline
    assert "【对话规则】" not in without_baseline
    # … but keeps the context-specific opening rule (unique to the bridge)
    assert "黄金三章" in without_baseline


def test_exact_duplicate_sections_are_dropped() -> None:
    text = (
        "## 写法方法论指导\n"
        "【emotion_engineering】\n压缩三章，释放半章。\n"
        "【hook_design】\n每章末一个具体未解物。\n"
        "## writing_methodology · scene\n"
        "【emotion_engineering】\n压缩三章，释放半章。\n"
        "【hook_design】\n每章末一个具体未解物。\n"
    )
    out = _dedupe_methodology_sections(text)
    assert out.count("【emotion_engineering】") == 1
    assert out.count("【hook_design】") == 1
    # The two distinct ## headers have different bodies after dedup, both kept.
    assert "## 写法方法论指导" in out


def test_variant_bodies_are_kept() -> None:
    # Same header, different body = a variant rendering, NOT a duplicate.
    text = (
        "【场景锚定】\n开场必须给出空间锚点。\n"
        "【场景锚定】\n每段至少一个身体锚点。\n"
    )
    out = _dedupe_methodology_sections(text)
    assert out.count("【场景锚定】") == 2


def test_whitespace_only_differences_still_dedupe() -> None:
    text = (
        "【visual_writing】\n动作  优先于形容词。\n\n"
        "【visual_writing】\n动作 优先于形容词。\n"
    )
    out = _dedupe_methodology_sections(text)
    assert out.count("【visual_writing】") == 1


def test_empty_and_headerless_text_pass_through() -> None:
    assert _dedupe_methodology_sections("") == ""
    plain = "没有任何节标题的纯文本。"
    assert _dedupe_methodology_sections(plain) == plain


def test_scene_prompt_carries_no_duplicate_methodology_sections() -> None:
    """Build-path regression: the assembled user prompt must not contain the
    same methodology subsection body twice (the production bug this fixes)."""

    project = SimpleNamespace(
        title="长夜巡航",
        slug="chang-ye-xun-hang",
        genre="仙侠",
        sub_genre="升级流",
        audience=None,
        language="zh-CN",
        target_chapters=20,
        metadata_json={},
    )
    chapter = SimpleNamespace(chapter_number=2, chapter_goal="夺取机缘", title="石门")
    scene = SimpleNamespace(
        scene_number=1,
        title="开门",
        participants=["陆刻舟"],
        purpose={"story": "进入秘境", "emotion": "压迫"},
        time_label="夜",
        entry_state={"location": "山门"},
        exit_state={"risk": "代价显形"},
        scene_type="setup",
        target_word_count=1100,
    )
    style_guide = SimpleNamespace(pov_type="third-limited", tone_keywords=["冷峻"])

    _system_prompt, user_prompt = build_scene_draft_prompts(
        project, chapter, scene, style_guide, {"logline": "废灵根杂役以阴债换道途。"}
    )

    # Generic duplicate scan over 【...】 sections: identical normalized bodies
    # must not appear twice anywhere in the assembled prompt.
    import re

    head_re = re.compile(r"^(?:##\s|【[^】\n]{1,60}】)", re.MULTILINE)
    starts = [m.start() for m in head_re.finditer(user_prompt)]
    seen: dict[str, str] = {}
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(user_prompt)
        section = user_prompt[start:end]
        fp = re.sub(r"\s+", " ", section).strip()
        if len(fp) < 40:  # skip trivial/empty section stubs
            continue
        assert fp not in seen, f"duplicate methodology section: {section[:60]!r}"
        seen[fp] = section[:60]
