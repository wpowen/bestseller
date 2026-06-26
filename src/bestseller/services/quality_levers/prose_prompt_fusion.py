"""Arena-proven prose prompt fusion rules.

These rules distill the 2026-06 prose prompt arena result into a compact
PROSE_SCENE block. They intentionally avoid methodology labels and tell the
writer what must appear on the page.
"""

from __future__ import annotations

# ruff: noqa: RUF001


def render_prose_prompt_fusion_block(*, language: str = "zh-CN") -> str:
    """Render scene-level hard prose actions proven useful by prompt arena."""

    if str(language or "").lower().startswith("en"):
        return ""
    return """【横测胜出融合写法 · 正文硬约束】
本场正文先执行这些页面动作，不要写出“黄金三章/爽点/去AI味”等方法论名词：
- 开场即放出不可逆代价或倒计时：读者必须在第一屏知道主角为什么不能等。
- 每 300-500 字制造一个来自行动结果的具体问题；解掉一个小问题后，立刻抛出更强问题。
- 主角判断先落到手、眼、呼吸、步伐、停顿、触感，再给一句以内判断；少写“他意识到/他明白了”。
- 每个关键动作必须碰到具体地点、道具、规则或人物反应；禁止用泛词替代已给定物料。
- 爽点必须写完整四拍：压迫 → 选择 → 执行 → 反馈；
  反馈要来自环境、对手、旁观者或规则系统，不要只写“震惊”。
- 围绕本场最强画面写开中结：开头埋视觉部件，中段推进，结尾兑现、反转，或让该画面变成下一场钩子。"""


__all__ = ["render_prose_prompt_fusion_block"]
