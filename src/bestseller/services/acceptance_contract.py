"""Chapter acceptance contract — scene-duty decomposition.

Chapter-level gates validate properties that only specific scenes can
deliver: the ending hook lives in the LAST scene's final 120 chars, the
hook echo lives in the FIRST scene's opening 300 chars. Historically these
acceptance criteria existed only inside the gates — no scene ever learned
"you are the last scene, your final paragraph must land a hook" — so
``ENDING_HOOK_MISSING`` / ``HOOK_ECHO_MISSING`` were a per-chapter lottery
(0/10 strict acceptance in the 2026-06-11 500-chapter run).

This module turns each chapter-level acceptance criterion into a *scene
duty* rendered into exactly the scene that can satisfy it, phrased in the
same terms the gate validates. Production promise == acceptance check.

Single source of truth: ``ENDING_HOOK_ANCHOR_TERMS`` is THE term list the
deterministic post-write audit scans for; ``deterministic_post_write_audit``
imports it from here.
"""

from __future__ import annotations

from dataclasses import dataclass


# The exact terms the ending-hook audit accepts (substring match over the
# final 120 whitespace-stripped chars). The last scene's duty block quotes
# this list verbatim so the writer is judged by the contract it was given.
ENDING_HOOK_ANCHOR_TERMS: tuple[str, ...] = (
    "？",
    "?",
    "什么",
    "谁",
    "为什么",
    "不对",
    "忽然",
    "突然",
    "响起",
    "裂开",
    "倒计时",
)

# How many of the previous chapter's hook tokens the first scene must reuse
# verbatim. The hook-echo gate passes at coverage ≥ 0.5; instructing ≥3 of
# the top 5 tokens lands at 0.6 with natural-sounding margin.
OPENING_ECHO_TOKEN_TARGET = 3
OPENING_ECHO_TOKEN_POOL = 5


@dataclass(frozen=True)
class SceneDuty:
    """Resolved chapter-duty assignment for one scene."""

    is_first_scene: bool
    is_last_scene: bool

    @property
    def has_duty(self) -> bool:
        return self.is_first_scene or self.is_last_scene


def resolve_scene_duty(scene_number: int, total_scenes: int | None) -> SceneDuty:
    """Map a scene position to its chapter-level duties.

    When ``total_scenes`` is unknown, only the first-scene duty can be
    resolved safely; the last-scene duty is skipped rather than guessed.
    """

    is_first = scene_number <= 1
    is_last = bool(total_scenes) and scene_number >= int(total_scenes or 0)
    return SceneDuty(is_first_scene=is_first, is_last_scene=is_last)


def _render_opening_echo_clause(
    prev_hook_tokens: tuple[str, ...] | list[str],
    *,
    language: str,
) -> str:
    tokens = [t for t in prev_hook_tokens if t and str(t).strip()]
    if not tokens:
        return ""
    shortlist = [str(t).strip() for t in tokens[:OPENING_ECHO_TOKEN_POOL]]
    target = min(OPENING_ECHO_TOKEN_TARGET, len(shortlist))
    if language.lower().startswith("zh"):
        return (
            "·【开篇呼应义务】本场景是本章第一个场景。开篇前 300 字内必须"
            f"正面承接上一章结尾的未解钩子：以下钩子词中至少 {target} 个"
            f"必须逐字出现 —— {'、'.join(shortlist)}。"
            "不是复述上一章，而是让钩子的后续当场发生。"
        )
    return (
        "- [OPENING ECHO DUTY] This is the chapter's FIRST scene. Within the "
        "first 300 characters, directly continue the previous chapter's "
        f"unresolved hook: use at least {target} of these hook tokens "
        f"verbatim — {', '.join(shortlist)}. Do not recap; make the hook's "
        "consequence happen on-page."
    )


def _render_ending_hook_clause(*, language: str) -> str:
    sample_terms = "、".join(ENDING_HOOK_ANCHOR_TERMS[2:8])
    if language.lower().startswith("zh"):
        return (
            "·【章末钩子义务】本场景是本章最后一个场景。最后一段必须以未解"
            "问题或突发事件收尾，且最后 120 字内必须显式出现下列锚点词"
            f"至少一个：问句（带「？」），或 {sample_terms} 之类的"
            "悬念/突变词。抽象感叹（如「一切才刚刚开始」）不算钩子。"
        )
    return (
        "- [ENDING HOOK DUTY] This is the chapter's LAST scene. The final "
        "paragraph must end on an unresolved question or sudden event, and "
        "the last 120 characters must explicitly contain a question mark or "
        "an abruptness/suspense anchor word. Abstract sighs ('this was only "
        "the beginning') do not count."
    )


def _render_payoff_clause(*, language: str) -> str:
    """One per-scene payoff obligation.

    Payoff density is the weakest persona channel in calibration (0.17 on
    the known-bad chapter vs 0.35 commercial pass), and the recurring
    ``PERSONA_PAYOFF_DENSITY_LOW`` blocker. Phrased as action/result — not
    vocabulary — so it cannot be satisfied by keyword stuffing.
    """

    if language.lower().startswith("zh"):
        return (
            "·【兑现义务】本场至少把一处已立的悬念/承诺落为可见结果"
            "（证据到手、对抗分出胜负、关系或代价坐实），"
            "写成现场动作与结果，不要旁白预告。"
        )
    return (
        "- [PAYOFF DUTY] Land at least one established hook/promise as a "
        "visible on-page result in this scene (evidence obtained, a clash "
        "decided, a relationship or cost made concrete). Show the result in "
        "action — no narrated previews."
    )


def render_scene_acceptance_block(
    *,
    scene_number: int,
    total_scenes: int | None,
    chapter_number: int,
    prev_hook_tokens: tuple[str, ...] | list[str] | None = None,
    language: str = "zh-CN",
    include_payoff_clause: bool = True,
) -> str:
    """Render the per-scene chapter-duty block for the scene writer prompt.

    First/last scenes carry their positional duties (opening echo / ending
    hook); every scene carries the payoff obligation. Position-specific
    instructions never land on scenes that cannot satisfy them.
    """

    duty = resolve_scene_duty(scene_number, total_scenes)

    clauses: list[str] = []
    if duty.is_first_scene and chapter_number >= 2 and prev_hook_tokens:
        clause = _render_opening_echo_clause(prev_hook_tokens, language=language)
        if clause:
            clauses.append(clause)
    if duty.is_last_scene:
        clauses.append(_render_ending_hook_clause(language=language))
    if include_payoff_clause:
        clauses.append(_render_payoff_clause(language=language))

    if not clauses:
        return ""
    header = (
        "【本场景章级验收硬指标 — 通不过即整章返修】"
        if language.lower().startswith("zh")
        else "[CHAPTER ACCEPTANCE DUTIES FOR THIS SCENE — chapter fails its gate without them]"
    )
    return header + "\n" + "\n".join(clauses)


__all__ = [
    "ENDING_HOOK_ANCHOR_TERMS",
    "OPENING_ECHO_TOKEN_POOL",
    "OPENING_ECHO_TOKEN_TARGET",
    "SceneDuty",
    "render_scene_acceptance_block",
    "resolve_scene_duty",
]
