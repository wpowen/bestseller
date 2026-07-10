"""Single source of truth for AI slop phrases.

Previously the same blacklist was maintained independently in multiple
places inside ``drafts.py`` (``_NOVEL_OUTPUT_PROHIBITION`` internal list,
``_NOVEL_OUTPUT_PROHIBITION_EN`` internal list, scene-level system
``# EXAMPLES`` block, chapter-first system ``# EXAMPLES`` block).

This module provides one canonical tuple and a renderer so that:

1. Adding a new banned phrase only requires updating one place.
2. The prompt only injects the blacklist once per path.
3. Post-generation detectors can import the same tuples for matching.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Chinese slop phrases
# ---------------------------------------------------------------------------

ZH_SLOP_PHRASES: tuple[str, ...] = (
    "血液仿佛凝固了",
    "血液冰封",
    "浑身的血液都冷了",
    "空气仿佛凝固了",
    "时间仿佛静止了",
    "周围的一切仿佛都消失了",
    "心中五味杂陈",
    "心中百感交集",
    "眼眶不由得湿润了",
    "一股莫名的情绪",
    "一种说不清的感觉",
    "一阵莫名的恐惧",
    "电流般的感觉",
    "触电般的感觉",
    "沉甸甸的",
    "仿佛有一只无形的手",
    "像是被什么东西攫住了",
)

ZH_SLOP_OPENERS: tuple[str, ...] = (
    "显而易见",
    "毫无疑问",
    "不言而喻",
)

ZH_SLOP_ENDINGS: tuple[str, ...] = (
    "这一切才刚刚开始",
    "真正的答案还在等待揭开",
    "欲知后事如何",
)

# ---------------------------------------------------------------------------
# English slop phrases
# ---------------------------------------------------------------------------

EN_SLOP_PHRASES: tuple[str, ...] = (
    "blood crystallized",
    "blood ran cold",
    "blood turned to ice",
    "words landed like a stone in still water",
    "words hung in the air",
    "cold as vacuum",
    "frozen fire",
    "liquid fire",
    "something almost like",
    "something that might have been",
    "the world narrowed to",
    "time seemed to slow",
    "the air itself seemed to",
    "a laugh that held no humor",
    "a smile that didn't reach their eyes",
    "electricity crackled between them",
    "tension thick enough to cut",
    "every fiber of their being",
    "a weight settled in their chest",
    "the silence was deafening",
    "pregnant pause",
    "comfortable silence",
)

EN_SLOP_OPENERS: tuple[str, ...] = (
    "It goes without saying",
    "Without a doubt",
    "Needless to say",
)

EN_SLOP_ENDINGS: tuple[str, ...] = (
    "and that was just the beginning",
    "the real answer was still waiting to be uncovered",
)


# ---------------------------------------------------------------------------
# Renderer — called once per prompt assembly path
# ---------------------------------------------------------------------------


def render_slop_blacklist_block(language: str) -> str:
    """Render the AI slop blacklist as a prompt block.

    Inject this **once** per prompt — placing it in multiple sections
    wastes tokens and dilutes attention to other instructions.
    """
    if language.lower().startswith("zh"):
        phrases = "\n".join(f"- \u300c{p}\u300d" for p in ZH_SLOP_PHRASES)
        openers = "\u3001".join(ZH_SLOP_OPENERS)
        endings = "\n".join(f"- \u7ae0\u672b\u300c{e}\u300d" for e in ZH_SLOP_ENDINGS)
        return (
            "\u3010AI\u5957\u8bdd\u9ed1\u540d\u5355\u2014\u2014\u4ee5\u4e0b\u8868\u8fbe\u7edd\u5bf9\u7981\u6b62\u3011\n"
            f"{phrases}\n"
            f"- \u4efb\u4f55\u4ee5\u300c{openers}\u300d\u5f00\u5934\u7684\u53e5\u5b50\n"
            f"{endings}\n"
            "\u7528\u5177\u4f53\u3001\u539f\u521b\u3001\u4ece\u6545\u4e8b\u4e16\u754c\u4e2d\u751f\u957f\u51fa\u6765\u7684\u610f\u8c61\u66ff\u4ee3\u8fd9\u4e9b\u5957\u8bdd\u3002"
        )

    phrases = "\n".join(f'- "{p}"' for p in EN_SLOP_PHRASES)
    openers = "; ".join(EN_SLOP_OPENERS)
    endings = "\n".join(f'- Ending: "{e}"' for e in EN_SLOP_ENDINGS)
    return (
        "BANNED AI CLICH\u00c9S \u2014 these phrases instantly mark text as "
        "machine-generated. NEVER use them:\n"
        f"{phrases}\n"
        f'- Any sentence starting with: {openers}\n'
        f"{endings}\n"
        "Replace these with concrete, specific, original imagery drawn from "
        "the story's world."
    )


# Every banned expression flattened for post-generation detection. Keep the
# detector aligned with all categories rendered into the prompt, not only the
# central phrase lists.
ALL_SLOP_PHRASES: tuple[str, ...] = (
    ZH_SLOP_PHRASES
    + ZH_SLOP_OPENERS
    + ZH_SLOP_ENDINGS
    + EN_SLOP_PHRASES
    + EN_SLOP_OPENERS
    + EN_SLOP_ENDINGS
)
