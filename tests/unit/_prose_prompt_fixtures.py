"""Minimal fixtures that render the real prose system prompts.

Shared by the 反AI腔 discipline guards so each prose path is exercised through
its *actual* prompt builder rather than a stand-in.
"""

from __future__ import annotations

from types import SimpleNamespace


def _project(language: str = "zh-CN") -> SimpleNamespace:
    return SimpleNamespace(
        title="吞神证我",
        genre="仙侠",
        sub_genre=None,
        language=language,
        metadata_json={},
    )


def _chapter() -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=8,
        title="血枭",
        chapter_goal="裴铸夺刀反制陈七。",
        target_word_count=2400,
    )


def _rewrite_task() -> SimpleNamespace:
    return SimpleNamespace(
        instructions="补足尾钩与冲突代价。",
        rewrite_strategy="scene_quality_rewrite",
        metadata_json={},
    )


def build_scene_rewrite_system_prompt(language: str = "zh-CN") -> str:
    from bestseller.services.reviews import build_scene_rewrite_prompts

    scene = SimpleNamespace(
        scene_number=1,
        title="夺刀",
        purpose={"story": "夺刀", "emotion": "压迫感"},
        target_word_count=1200,
    )
    system_prompt, _ = build_scene_rewrite_prompts(
        _project(language),
        _chapter(),
        scene,
        SimpleNamespace(content_md="旧稿。", word_count=900),
        _rewrite_task(),
        SimpleNamespace(pov_type="third-limited", tone_keywords=["紧张"]),
    )
    return system_prompt


def build_chapter_rewrite_system_prompt(language: str = "zh-CN") -> str:
    from bestseller.services.reviews import build_chapter_rewrite_prompts

    system_prompt, _ = build_chapter_rewrite_prompts(
        _project(language),
        _chapter(),
        SimpleNamespace(content_md="旧稿。", word_count=2000),
        _rewrite_task(),
        None,
    )
    return system_prompt
