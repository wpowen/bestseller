"""Single source of truth for the 反AI腔 (anti-AI-voice) prose discipline.

Why this module exists
----------------------
This discipline used to be a bare string literal inside ``drafts.py``'s zh writer
system prompt. That made "inject it into the writer" and "inject it into the
rewriter" two unrelated code paths, so every anti-AI-flavor fix (2026-07-04,
07-08, 07-13) landed on the writer only. Meanwhile the rewrite prompts
(``scene_rewrite`` / ``chapter_rewrite``) — which produced **76% of shipped
prose** on a measured 24-chapter book — carried *zero* anti-AI rules, and their
output showed 1.75x the embodied-verb tic density and 2.07x the conclusion-first
density of writer output.

Any new rule added here reaches every prose-producing path at once. Do not
re-inline these rules into a prompt; import and render instead.

Scope
-----
The embodied-verb budget is scope-sensitive. The writer emits one *scene* at a
time and a chapter assembles 2-3 scenes, so a per-scene cap of 2 silently
becomes 6+ per chapter — above the detector's own ``verb_tic_spam`` threshold.
Callers rewriting a whole chapter must pass ``scope="chapter"`` so the budget is
stated against the text the model actually holds.
"""

from __future__ import annotations

from typing import Literal

from bestseller.services.writing_profile import is_english_language

Scope = Literal["scene", "chapter"]

def _embodied_verbs() -> str:
    """The verb list, derived from the detector that judges it.

    Hardcoding it here let the two drift: the prompt banned 10 verbs while
    ``_detect_verb_tic_spam`` flagged 12, so prose was rewritten for over-using
    攥 / 掐 — the two most frequent tics in a measured book (攥×30) — having never
    been told not to. Deriving removes that failure mode by construction.
    """

    from bestseller.services.ai_flavor.detector import _VERB_TIC_LEXICON_ZH

    return "、".join(_VERB_TIC_LEXICON_ZH)

_SCOPE_LABEL: dict[str, str] = {"scene": "全场", "chapter": "全章"}
# A chapter holds 2-3 scenes; keep the per-scene budget intact rather than
# letting it multiply by the scene count.
_VERB_CAP: dict[str, int] = {"scene": 2, "chapter": 4}
_SIMILE_CAP: dict[str, int] = {"scene": 1, "chapter": 3}
_MEASURE_CAP: dict[str, int] = {"scene": 2, "chapter": 5}


def render_anti_ai_voice_discipline(
    *,
    language: str | None = None,
    scope: Scope = "scene",
) -> str:
    """Render the 反AI腔 discipline block for a prose system prompt.

    Returns ``""`` for English (the rules are zh-specific lexical/syntactic
    prescriptions and have no English equivalent), so callers can concatenate
    unconditionally.
    """

    if is_english_language(language):
        return ""

    where = _SCOPE_LABEL.get(scope, _SCOPE_LABEL["scene"])
    verb_cap = _VERB_CAP.get(scope, _VERB_CAP["scene"])
    simile_cap = _SIMILE_CAP.get(scope, _SIMILE_CAP["scene"])
    measure_cap = _MEASURE_CAP.get(scope, _MEASURE_CAP["scene"])

    return (
        "# CONTEXT · 语体与用词纪律（反AI腔，违反会被判重写）\n"
        "- 不要结论先行/总分总：禁止先抛出判断、情绪标签或场面总结、再用描写去补证。"
        "先写正在发生的具体动作与感知，结论让读者自己得出，能不说就不说。"
        "删掉一切替读者算账、下定论的句子（如“他算了一笔账”“这一刻他明白了”）。\n"
        "- 语体=现代白话网文：先把话说清楚，再谈修辞。禁止文白夹杂的压缩腔"
        "（连续出现省略主语、省略量词的短句会让读者出戏）。\n"
        f"- 具身动词禁止复读：{_embodied_verbs()} 这类高冲击动词，"
        f"同一个词{where}最多 {verb_cap} 次；写感官时用平实动词（闻到/听见/看见/摸到）不丢人，"
        "复读高冲击动词才是最重的 AI 腔。\n"
        f"- 通感与陌生化比喻是味精：{where}≤{simile_cap}处，且必须贴合当下事件；"
        "严禁感官动词错配的怪喻（如“香味撞上来”“蒸汽舀进脑仁”——读者只会出戏）。\n"
        f"- 度量腔限用：“半寸/一寸/三分/半息”这类精确度量{where}≤{measure_cap}次，"
        "不要每个动作都带尺子。\n"
        "- 句子向前流动地叙事，不要为了节奏感把每个动作/心理拍点切成一句各占一段"
        "（整章像分镜脚本就是最刺眼的AI腔）；长短句交错，短句是偶尔的重锤，不是默认节奏。\n"
    )


__all__ = ["render_anti_ai_voice_discipline"]
