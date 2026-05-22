from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.context import ChapterWriterContextPacket
from bestseller.services.reviews import build_chapter_rewrite_prompts

pytestmark = pytest.mark.unit


def test_chapter_rewrite_prompt_injects_p1_retention_blocks() -> None:
    project = SimpleNamespace(
        title="青囊不语问阴阳",
        genre="fantasy",
        sub_genre=None,
        language="zh-CN",
        metadata_json={},
    )
    chapter = SimpleNamespace(
        chapter_number=2,
        title="镜口回声",
        chapter_goal="承接上一章尾钩并推进镜案",
        target_word_count=2200,
    )
    draft = SimpleNamespace(content_md="旧稿没有呼应上一章。", word_count=1800)
    rewrite_task = SimpleNamespace(
        instructions="修复留存闸门失败。",
        rewrite_strategy="retention_repair",
    )
    context = ChapterWriterContextPacket(
        project_id=uuid4(),
        project_slug="qingnang",
        chapter_id=uuid4(),
        chapter_number=2,
        query_text="rewrite ch2",
        chapter_goal=chapter.chapter_goal,
        canon_guardrails_block="【正典守护】裴镜渊不得登场。",
        hook_echo_block="【钩子回环 — 必须呼应上一章】倒计时。",
        signature_scene_block="【招牌场景指令】兑现镜中揭示。",
        voice_dna_block="【作者声纹 DNA】短句、冷压。",
        chapter_market_constraints_block="【市场硬约束】前1000字给冲突。",
        exposition_density_block="【铺垫节制 — 本章必须遵守】设定切碎。",
    )

    _, user_prompt = build_chapter_rewrite_prompts(
        project,
        chapter,
        draft,
        rewrite_task,
        context,
    )

    for marker in (
        "正典守护",
        "钩子回环",
        "招牌场景指令",
        "作者声纹",
        "市场硬约束",
        "铺垫节制",
    ):
        assert marker in user_prompt
    assert user_prompt.index("【正典守护】") < user_prompt.index("当前草稿")
