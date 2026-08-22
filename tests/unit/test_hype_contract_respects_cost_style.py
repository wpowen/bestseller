"""勾了「无代价」的书，爽点合同不许再要求「爽必须留下代价」。

2026-08-22 用户定罪（《书院笔仙》custom-xuanhuan-1787328262）：
建书时勾的是

    effect_skills = ["comedy_engine", "hype_satisfaction_engine"]
    cost_style    = "minimal"          # 纯爽，不要自损代价

产出的简介却是「他不敢撕、不敢改、不敢停」「悬赏令贴满回廊：查出笔奴
下落者赏灵石千枚」——8 句里主角受动 5 句，**主动使用金手指 0 句**。
用户原话：「这是爽文设定，怎么看简介那么憋屈呢。」

机制在这里：`hype_satisfaction_engine` 的合同硬性写着

    禁忌：satisfaction must leave a new pressure or cost behind

而 `cost_style` 字段自己的注释写着它「控制金手指是否强制自损代价」。
**同一件事住两地，合同赢了。**

修法不是删掉压力——爽文也需要「赢完引来更强的对手」这种上行压力。
区别在压力**落在谁身上**：

* 外部对抗升级（赢了 → 惊动更大的势力）→ 主角始终在赢，是爽文。
* 主角自身代价（赢了 → 自己付出损失）→ 主角在受苦，是憋屈。

minimal 档只禁后者。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
from bestseller.services.story_enhancers import (
    render_story_enhancer_contract_block,
    resolve_story_enhancers,
)


def _block(cost_style: str) -> str:
    selection = resolve_story_enhancers(
        {
            "story_enhancers": {
                "effect_skills": ["hype_satisfaction_engine"],
                "cost_style": cost_style,
            }
        }
    )
    return render_story_enhancer_contract_block(selection, language="zh-CN")


def test_minimal_cost_book_is_never_told_that_satisfaction_must_cost_something() -> None:
    block = _block("minimal")
    assert "hype_satisfaction" in block, "前置条件：爽点合同本来就该出现"
    # ⚠️ 这里必须同时查中英两种写法。第一版只断言中文「代价」，**假绿**——
    # 因为 guardrail 原文是英文 "cost"。中文 prompt 里嵌英文判据，会让
    # 判据本身也失效，不只是腔调问题。
    assert "代价" not in block, "勾了『无代价』的书，合同里不许再要求代价"
    assert "cost" not in block.lower(), (
        "英文 guardrail 同样算数——用户勾的两个选项不能在框架内部互相拆台"
    )


def test_minimal_cost_book_still_gets_escalating_external_pressure() -> None:
    """不是把压力删掉，是把压力挪到主角身外——爽文靠这个续航。"""

    block = _block("minimal")
    assert "外部" in block or "对手" in block or "升级" in block


def test_default_cost_style_keeps_the_original_cost_guardrail() -> None:
    """没勾『无代价』的书行为不变——修复不该改变它不需要改变的东西。"""

    block = _block("standard")
    assert "hype_satisfaction" in block
