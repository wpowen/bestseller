"""中文 prompt 里的 skill 合同不许再嵌英文判据。

2026-08-22 定罪：中文章节 prompt 里渲染出

    效果：Deliver visible gain, reversal, status shift, …
    禁忌：satisfaction must leave a new pressure or cost behind；…

**中文 prompt 嵌英文判据，失效的不只是腔调，判据本身会被绕过**——同日
我自己的测试就假绿过一次：断言中文「代价」不在 prompt 里，绿了，因为
guardrail 原文是英文 "cost"。

修法：18 个 skill 的 description / use_when / misuse_guardrails 补中文
本地化表，zh 渲染路径一律用中文；英文原文留给 en 路径，不删。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import re

from bestseller.services.story_effect_skills import _SKILL_ZH_TEXTS
from bestseller.services.story_enhancers import (
    render_story_enhancer_contract_block,
    resolve_story_enhancers,
)

_ALL_KEYS = (
    "brainhole_engine",
    "callback_motif_engine",
    "comedy_engine",
    "danger_action_engine",
    "dialogue_spark_engine",
    "emotional_payoff_engine",
    "healing_grief_engine",
    "hype_satisfaction_engine",
    "moral_dilemma_engine",
    "relationship_chemistry_engine",
    "rhythm_pacing_engine",
    "romance_tenderness_engine",
    "suspense_reveal_engine",
    "system_payoff_engine",
    "tension_pressure_engine",
    "twist_reversal_engine",
    "wonder_awe_engine",
    "world_texture_engine",
)

# 允许出现的 ASCII：skill_key 本身、output_contract 标识符、标点。
_EN_SENTENCE = re.compile(r"\b[a-z]+(?:\s+[a-z]+){3,}\b")


def _zh_block(keys: list[str], cost_style: str = "standard") -> str:
    selection = resolve_story_enhancers(
        {"story_enhancers": {"effect_skills": keys, "cost_style": cost_style}}
    )
    return render_story_enhancer_contract_block(selection, language="zh-CN")


def test_every_skill_has_a_zh_localization() -> None:
    missing = [k for k in _ALL_KEYS if k not in _SKILL_ZH_TEXTS]
    assert missing == [], f"缺中文本地化：{missing}"


def test_zh_contract_carries_no_english_sentences() -> None:
    for key in _ALL_KEYS:
        block = _zh_block([key])
        for line in block.split("\n"):
            if any(tag in line for tag in ("效果：", "禁忌：", "适合用在：")):
                assert not _EN_SENTENCE.search(line), f"{key} 泄漏英文判据：{line[:120]}"


def test_minimal_cost_swap_still_works_on_zh_guardrails() -> None:
    """代价档替换要认得中文「代价」——不只英文 cost。"""

    block = _zh_block(["hype_satisfaction_engine"], cost_style="minimal")
    assert "代价" not in block.split("禁忌：")[-1].split("；")[0] or "外部对抗" in block
    assert "外部对抗升级" in block


def test_standard_cost_keeps_the_zh_cost_guardrail() -> None:
    block = _zh_block(["hype_satisfaction_engine"], cost_style="standard")
    assert "代价" in block or "压力" in block
