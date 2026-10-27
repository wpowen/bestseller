"""Character embodiment (单人入戏) prose lever — pure prompt/render helpers.

A/B evidence (3 experiments, two independent judge families, absolute-blind):
making the model *inhabit* the protagonist and emit **raw first-person interiority**
*before* the writer drafts a scene is the single most reliable prose lever found —
on abstract-material books it lifted FinalScore +9.0 / +4.75 and collapsed
abstract-mechanism leakage (5.25 → 0.5) and AI-tone (3.0 → 0.0). The win comes
from the embodiment pass forcing abstract mechanism terms to be re-thought as
*lived, concrete experience* in plain first-person language before any prose exists.

Critical design constraint proven by the group-simulation arm: the interiority must
reach the writer **raw, never summarized** — an extra "digest" hop re-abstracts the
texture and erases the gain. So this lever renders the interiority verbatim.

This module is pure (no DB / no LLM): the async orchestration that actually calls
the model lives in ``services/character_embodiment.py``. Here we only build the
embodiment prompt and render the precomputed interiority into a soft writer block.

Red lines (consistent with the other prose levers):
* **soft** — never a gate, never blocks publish, never touches word-count contract;
* **zh-only** — embodiment is Chinese-prose specific (English path renders nothing);
* **shape, not content** — we shape *how* to think in-character, we never inject
  fixed phrasing that would homogenize the book.
"""

# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

EMBODIMENT_KEY = "character_embodiment"

_MAX_INTERIORITY_CHARS = 1200


def extract_embodiment(story_bible: Mapping[str, Any] | None) -> str:
    """Pull precomputed first-person interiority from a story-bible mapping.

    Returns ``""`` when absent / malformed so callers can no-op cleanly.
    """

    if not isinstance(story_bible, Mapping):
        return ""
    value = story_bible.get(EMBODIMENT_KEY)
    if not isinstance(value, str):
        return ""
    return value.strip()


def render_embodiment_block(interiority: str) -> str:
    """Render the raw interiority into a soft writer block (verbatim, no summary).

    Empty / blank interiority → ``""`` (the section is simply skipped upstream).
    """

    text = (interiority or "").strip()
    if not text:
        return ""
    if len(text) > _MAX_INTERIORITY_CHARS:
        text = text[:_MAX_INTERIORITY_CHARS].rstrip() + "…"
    return (
        "【主角此刻的真实内心 · 落笔前先读（正文必须长在这上面）】\n"
        "下面是主角本人在本场此刻的第一人称内心推演（未经修饰的原话）。"
        "正文里他注意到的具体东西、他的犹豫与权衡、他说出口和咽回去的话，"
        "都要从这份内心自然长出来。\n"
        "纪律：① 大部分演成连续的动作与对白，但其中【最锋利的一两句念头】"
        "（他对自己说的赌注/恐惧/自问）必须以内心声音的形式直接落进正文——"
        "短、口语、他自己的嗓音；全部转译成身体反应＝读者永远不知道他在想什么，算没写。"
        "② 这段里若把抽象设定想成了具体的人/物/身体反应，正文就沿用那个具体版本，"
        "绝不在正文里写出机制术语；③ 这是写法输入，不计入字数、不替代剧情义务。\n"
        "————\n"
        f"{text}\n"
        "————"
    )


def build_embodiment_prompt(
    *,
    protagonist: str,
    situation: str,
    genre: str = "",
    max_chars: int = 260,
) -> tuple[str, str]:
    """Build the (system, user) prompts for the embodiment pass.

    The system prompt puts the model *inside* the protagonist (first person, no
    authorial voice, plain words, mechanism→concrete). The user prompt feeds the
    protagonist's situation and asks for raw interiority — not prose.
    """

    genre_hint = f"（题材：{genre.strip()}）" if genre and genre.strip() else ""
    system = (
        f"你现在【就是下面这个人物】本人{genre_hint}，不是作者、不是旁观者，用第一人称"
        "“我”思考。不要写小说、不要修辞、不要美化——像真人在那一刻脑子里真实流过的念头。\n"
        # Enumerating the jargon is how you plant it: the old wording named
        # 代价记账/状态账 in every embodiment call (2026-08-09 ledger sweep).
        # Say what to produce, not which words to avoid.
        "重要：设定里的抽象术语一律不进我的念头。凡是规划语言，都先换算成我身上"
        "真实发生的具体东西——一处身体反应、一个数字、一件事的后果、一句话——"
        "再用大白话想。"
    )
    user = (
        f"这是我（{protagonist or '主角'}）的情况：\n{situation.strip()}\n\n"
        f"以“我”的口吻，真实地想一遍（不超过{max_chars}字，可分点）：\n"
        "1. 此刻我身体和眼睛先注意到的具体东西是什么？（越具体越好，不要抽象）\n"
        "2. 我心里最在意 / 最怕的是什么？\n"
        "3. 我会怎么权衡，最后决定怎么做？\n"
        "4. 我会说出口的话 / 我咽回去没说的话各是什么？\n"
        "只输出我的内心，不要写成小说、不要加任何标题或说明。"
    )
    return system, user


__all__ = [
    "EMBODIMENT_KEY",
    "build_embodiment_prompt",
    "extract_embodiment",
    "render_embodiment_block",
]
