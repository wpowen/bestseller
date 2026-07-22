"""Single source of truth for the 视角体验 (POV experience) prose discipline.

Why this module exists
----------------------
Two capabilities kept losing to prose paths that never received them:

1. **内心导航**. Benchmarking against 诡秘之主 (E1/E2/E3, 2026-07) found the
   readability gap — not the craft gap — was what lost blind reads: our POV
   character had *zero* on-page thoughts across three chapters (benchmark: 6
   thoughts / 15 question marks) because ``cinematic_pov`` + embodiment render
   conspired to convert every intention into a physiological symptom. Authorising
   inner voice recovered clarity/breath/retention 4-0 in the E3 rerun.
2. **机制落地律**. Increment validation (2026-06-29, claude pairwise, VOTES=5)
   measured a 100% single-add win rate for "any proper noun / ability / setting
   gets one plain-sentence explanation of what it is, what it does, what it
   costs" — one of only four blocks that survived greedy forward selection.

Both lived only inside the ``PROSE_SCENE`` methodology compile, which the
chapter-first writer never calls. Import and render instead of re-inlining, so a
fix here reaches every path at once.

Scope
-----
Inner-voice density is stated per unit of text the model actually holds. A
chapter is 2-3 scenes, so a per-scene floor of 3 must be restated as a chapter
floor rather than silently read as "3 per chapter".

Deliberately excluded
---------------------
Simile and new-concept budgets are **not** restated here even though the source
rubric carries them: ``anti_ai_voice_discipline`` already owns the simile cap,
and two blocks naming two different numbers (≤3 vs ≤4 per chapter) is how a
prompt teaches the model that the caps are negotiable.
"""

from __future__ import annotations

from typing import Literal

from bestseller.services.writing_profile import is_english_language

Scope = Literal["scene", "chapter"]

_SCOPE_LABEL: dict[str, str] = {"scene": "每场", "chapter": "全章"}
# 3 per scene x 2-3 scenes. Stated as 8 rather than 9 so a tight 2-scene chapter
# is not pushed into padding thoughts it does not have room for.
_INNER_VOICE_FLOOR: dict[str, int] = {"scene": 3, "chapter": 8}


def render_pov_experience_block(
    *,
    language: str | None = None,
    scope: Scope = "scene",
) -> str:
    """Render the 视角体验 discipline block for a prose system prompt.

    Returns ``""`` for English (these are zh-specific prescriptions), so callers
    can concatenate unconditionally.
    """

    if is_english_language(language):
        return ""

    where = _SCOPE_LABEL.get(scope, _SCOPE_LABEL["scene"])
    floor = _INNER_VOICE_FLOOR.get(scope, _INNER_VOICE_FLOOR["scene"])

    return (
        "# CONTEXT · 视角与体验纪律（读者靠这个跟住故事）\n"
        f"- 内心导航：视角人物脑子里流过的念头是读者的导航，{where}至少 {floor} 处。"
        "下决定前把赌注说给自己听；对反常自问；对眼前事下他自己的判断。"
        "要短、要口语、要是他自己的嗓音，一两句就落回动作。"
        "念头可以点破他对处境的理解（哪怕是错的）——这不是旁白，这是体验。\n"
        "- 单一视角：只写视角人物能亲身感知到的。别人的心理和动机只能由他从"
        "外在（动作/神态/语气/物件）去推断，不能由叙述者直接说出口。\n"
        "- 机制落地：任何专名、能力或设定第一次出现，先让视角人物用一句大白话说出"
        "他自己的理解——是什么、能干什么、要付什么代价（理解错了也行，后面再纠）。"
        "其余设定只留存在感，本章不展开。\n"
    )


__all__ = ["render_pov_experience_block"]
