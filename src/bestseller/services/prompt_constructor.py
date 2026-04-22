"""L3 PromptConstructor — the centralised prompt-assembly layer.

Replaces the inline prompt stitching in ``if_prompts.py`` (plan §3). The
constructor owns three things that previously lived scattered across
``drafts.py`` / ``reviews.py``:

* **Opening archetype assignment** — bug #5 (四本小说开篇雷同). Every
  chapter gets an explicit archetype constraint in the prompt, rotated
  against the ``DiversityBudget`` so the LLM physically cannot choose the
  same opening twice in a row.
* **Hot-vocab ban + formulaic-phrase ban** — bug #7 (`shard`×18/章). The
  last ``hot_vocab_window_chapters`` chapters' top tokens are injected
  as a banned list; the ``invariants.banned_formulaic_phrases`` is fed in
  alongside.
* **Methodology fragment injection** — bug #9 (告知而非演出). Forced
  fragments referenced in ``invariants.forced_methodology_fragments`` are
  stitched verbatim into the prompt so emotion/reversal playbooks stop
  being optional.

**Scope of this stub.** The opinionated "how the bible maps into a prompt
slice" and "how the scene spec renders" pieces still live in the caller
(they depend on deep bible/spec structure this module shouldn't own). We
expose those as caller-supplied strings; the constructor focuses on the
cross-cutting *diversity* pieces it uniquely owns.

The key guarantee: if the caller passes non-empty sections, ``render()``
emits them in a stable, documented order. This means the feedback-driven
regen loop (L4.5) can call ``rebuild_with_feedback`` and know exactly
where the remediation block lands.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field, replace
from typing import Sequence

from bestseller.services.diversity_budget import DiversityBudget
from bestseller.services.hype_engine import (
    HypeDensityBand,
    HypeRecipe,
    HypeScheme,
    HypeType,
    pick_hype_for_chapter,
    target_hype_for_chapter,
)
from bestseller.services.invariants import (
    CliffhangerPolicy,
    CliffhangerType,
    OpeningArchetype,
    ProjectInvariants,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------


DEFAULT_PRIOR_CHAPTER_TAIL_CHARS = 800
DEFAULT_HOT_VOCAB_WINDOW = 5
DEFAULT_HOT_VOCAB_TOP_N = 20
DEFAULT_HOT_VOCAB_MIN_COUNT = 3
DEFAULT_NO_REPEAT_WITHIN_OPENINGS = 3


# Public-facing archetype directive text (ZH / EN), keyed by enum value.
# These are *appended* to the scene_spec so the LLM sees a concrete opening
# requirement. Keep them short and actionable — the anti-slop footer
# reinforces the general "no filler" rules.
_ARCHETYPE_DIRECTIVES_ZH: dict[OpeningArchetype, str] = {
    OpeningArchetype.HUMILIATION: "本章第一个场景必须以【屈辱】开局：主角在公开场合受辱，读者第一页就看到羞耻。",
    OpeningArchetype.CRISIS: "本章第一个场景必须以【危机】开局：外部威胁逼近，主角必须即刻行动或后退。",
    OpeningArchetype.ENCOUNTER: "本章第一个场景必须以【奇遇】开局：主角无意间遭遇一个陌生人 / 异象 / 神秘物件。",
    OpeningArchetype.CONTRAST: "本章第一个场景必须以【反差】开局：将一个宁静日常镜头切入一个剧烈差异（身份 / 外观 / 境遇）。",
    OpeningArchetype.SECRET_REVEAL: "本章第一个场景必须以【秘密外泄】开局：一个藏多年的事实被某个不该知道的人无意撞破。",
    OpeningArchetype.IDENTITY_FALL: "本章第一个场景必须以【身份跌落】开局：主角的社会地位 / 家族地位在本场景开始时就已经失去。",
    OpeningArchetype.BROKEN_ENGAGEMENT: "本章第一个场景必须以【被退婚】开局：在众目睽睽下，主角收到婚约解除 / 羞辱性退亲。",
    OpeningArchetype.BANISHMENT: "本章第一个场景必须以【被驱逐】开局：主角在开场三百字内被逐出家门 / 门派 / 城池。",
    OpeningArchetype.BETRAYAL: "本章第一个场景必须以【被背叛】开局：亲近之人（师傅 / 爱人 / 至交）在开场就表态站在对立面。",
    OpeningArchetype.SUDDEN_POWER: "本章第一个场景必须以【突得外挂】开局：一个系统 / 传承 / 血脉在主角最脆弱的瞬间觉醒。",
    OpeningArchetype.RITUAL_INTERRUPTED: "本章第一个场景必须以【仪式被打断】开局：一场正在进行的仪式（成年礼 / 祭祀 / 婚典）被暴力打断。",
    OpeningArchetype.MUNDANE_DAY: "本章第一个场景必须以【日常被打破】开局：前两段是最普通的一日，第三段发生不可逆转的异常。",
}

_ARCHETYPE_DIRECTIVES_EN: dict[OpeningArchetype, str] = {
    OpeningArchetype.HUMILIATION: "Open this chapter with HUMILIATION: a public shaming scene on page one.",
    OpeningArchetype.CRISIS: "Open this chapter with CRISIS: an imminent external threat forcing an immediate response.",
    OpeningArchetype.ENCOUNTER: "Open this chapter with ENCOUNTER: the protagonist stumbles upon a stranger / anomaly / mysterious object.",
    OpeningArchetype.CONTRAST: "Open this chapter with CONTRAST: a quiet daily moment cuts into a sharp status / appearance / circumstance shift.",
    OpeningArchetype.SECRET_REVEAL: "Open this chapter with SECRET_REVEAL: a long-hidden fact gets witnessed by the wrong person.",
    OpeningArchetype.IDENTITY_FALL: "Open this chapter with IDENTITY_FALL: the protagonist has already lost their social / familial standing as the scene opens.",
    OpeningArchetype.BROKEN_ENGAGEMENT: "Open this chapter with BROKEN_ENGAGEMENT: an engagement is ended in public on page one.",
    OpeningArchetype.BANISHMENT: "Open this chapter with BANISHMENT: within the first 300 words the protagonist is cast out of home / sect / city.",
    OpeningArchetype.BETRAYAL: "Open this chapter with BETRAYAL: a trusted figure (mentor / lover / friend) openly sides against the protagonist on page one.",
    OpeningArchetype.SUDDEN_POWER: "Open this chapter with SUDDEN_POWER: a system / bloodline / inheritance awakens at the protagonist's weakest moment.",
    OpeningArchetype.RITUAL_INTERRUPTED: "Open this chapter with RITUAL_INTERRUPTED: a ceremony (coming-of-age / sacrifice / wedding) is violently interrupted.",
    OpeningArchetype.MUNDANE_DAY: "Open this chapter with MUNDANE_DAY: the first two paragraphs depict a normal day, the third introduces an irreversible anomaly.",
}


def _archetype_directive(archetype: OpeningArchetype, language: str) -> str:
    table = (
        _ARCHETYPE_DIRECTIVES_ZH
        if language.lower().startswith("zh")
        else _ARCHETYPE_DIRECTIVES_EN
    )
    return table.get(archetype, f"Open the chapter using the {archetype.value} archetype.")


# ---------------------------------------------------------------------------
# PromptPlan.
# ---------------------------------------------------------------------------


@dataclass
class PromptPlan:
    """Structured prompt skeleton.

    Each field is a text section. ``render()`` stitches them with double
    newlines in a documented order. Empty strings are tolerated (the
    corresponding slot is simply skipped during rendering) so callers can
    gradually opt in to each section.

    ``feedback_block`` is populated by ``rebuild_with_feedback`` and
    rendered *last* so L4.5 regeneration attempts see the remediation
    instructions adjacent to the LLM's response window.
    """

    system: str = ""
    invariants_section: str = ""
    bible_slice: str = ""
    reader_contract_section: str = ""
    methodology_inject: str = ""
    hype_constraints: str = ""
    diversity_constraints: str = ""
    prior_chapter_tail: str = ""
    scene_spec: str = ""
    anti_slop_footer: str = ""
    feedback_block: str = ""

    # Metadata — not rendered, useful for L5/L6 handoff.
    chapter_no: int | None = None
    assigned_opening: OpeningArchetype | None = None
    assigned_cliffhanger: CliffhangerType | None = None
    assigned_hype_type: HypeType | None = None
    assigned_hype_recipe: HypeRecipe | None = None
    assigned_hype_intensity: float | None = None

    def render(self) -> str:
        sections = [
            self.system,
            self.invariants_section,
            self.bible_slice,
            self.reader_contract_section,
            self.methodology_inject,
            self.hype_constraints,
            self.diversity_constraints,
            self.prior_chapter_tail,
            self.scene_spec,
            self.anti_slop_footer,
            self.feedback_block,
        ]
        return "\n\n".join(s.strip() for s in sections if s and s.strip())


# ---------------------------------------------------------------------------
# Section builders.
# ---------------------------------------------------------------------------


def build_invariants_section(invariants: ProjectInvariants) -> str:
    """Emit a short, authoritative summary of the immutable contract."""

    env = invariants.length_envelope
    bits: list[str] = [
        f"【语言】{invariants.language}",
        f"【视角】{invariants.pov}",
        f"【时态】{invariants.tense}",
        (
            f"【章长度】{env.min_chars}–{env.max_chars} "
            f"字（目标 {env.target_chars}）"
        ),
    ]
    if invariants.naming_scheme and invariants.naming_scheme.seed_pool:
        pool_preview = ", ".join(invariants.naming_scheme.seed_pool[:10])
        bits.append(f"【命名池】{pool_preview}…")
    return "【故事不变量】\n" + "\n".join(bits)


def choose_opening_archetype(
    diversity_budget: DiversityBudget,
    *,
    pool: Sequence[OpeningArchetype] | None = None,
    preassigned: OpeningArchetype | None = None,
    no_repeat_within: int = DEFAULT_NO_REPEAT_WITHIN_OPENINGS,
) -> OpeningArchetype:
    """Decide which opening archetype to mandate for this chapter.

    ``preassigned`` always wins (chapter 1 can be pinned at bible
    materialisation time). Otherwise, pick the first pool member absent
    from the last ``no_repeat_within`` chapters.
    """

    if preassigned is not None:
        return preassigned
    return diversity_budget.next_opening(
        pool=pool, no_repeat_within=no_repeat_within
    )


def choose_cliffhanger_type(
    diversity_budget: DiversityBudget,
    *,
    policy: CliffhangerPolicy | None = None,
    rng: random.Random | None = None,
) -> CliffhangerType:
    """Pick the cliffhanger type for this chapter.

    Defers to ``DiversityBudget.next_cliffhanger`` which already encodes
    the LRU fallback. ``rng`` is reserved for future jitter; current
    implementation is deterministic.
    """

    _ = rng  # placeholder — kept on the signature for callers passing
    # a seeded RNG today so the interface is stable as we move from
    # deterministic to randomised pick in Phase 3.
    return diversity_budget.next_cliffhanger(policy=policy)


def build_diversity_constraints(
    invariants: ProjectInvariants,
    diversity_budget: DiversityBudget,
    *,
    assigned_opening: OpeningArchetype | None = None,
    assigned_cliffhanger: CliffhangerType | None = None,
    hot_vocab_window: int = DEFAULT_HOT_VOCAB_WINDOW,
    hot_vocab_top_n: int = DEFAULT_HOT_VOCAB_TOP_N,
    hot_vocab_min_count: int = DEFAULT_HOT_VOCAB_MIN_COUNT,
) -> str:
    """Assemble the "what not to do, what to hit" section.

    Composition:
      1. Opening archetype directive (bug #5)
      2. Cliffhanger type directive (bug #10)
      3. Hot-vocab ban list (bug #7)
      4. Formulaic-phrase ban list (bug #7)
    """

    language = invariants.language
    lines: list[str] = ["【创作多样性约束】"]

    if assigned_opening is not None:
        lines.append(f"- 开篇: {_archetype_directive(assigned_opening, language)}")

    if assigned_cliffhanger is not None:
        if language.lower().startswith("zh"):
            lines.append(
                f"- 章末悬念: 本章结尾必须采用 {assigned_cliffhanger.value} 类悬念，"
                f"不得与最近章节重复。"
            )
        else:
            lines.append(
                f"- Ending cliffhanger: close this chapter with a "
                f"{assigned_cliffhanger.value} cliffhanger, "
                f"distinct from recent chapters."
            )

    hot = diversity_budget.hot_vocab(
        window=hot_vocab_window,
        top=hot_vocab_top_n,
        min_count=hot_vocab_min_count,
    )
    if hot:
        banned = "、".join(hot) if language.lower().startswith("zh") else ", ".join(hot)
        if language.lower().startswith("zh"):
            lines.append(
                f"- 本章禁用词汇（最近 {hot_vocab_window} 章高频）：{banned}。"
                f"必须用同义替换，禁止出现在叙述、对白、心理活动中。"
            )
        else:
            lines.append(
                f"- Banned vocabulary (top words in last "
                f"{hot_vocab_window} chapters): {banned}. "
                f"Replace with synonyms; do not use these in narration, "
                f"dialogue, or internal monologue."
            )

    if invariants.banned_formulaic_phrases:
        joined = "\n    ".join(
            f"- {p}" for p in invariants.banned_formulaic_phrases
        )
        if language.lower().startswith("zh"):
            lines.append("- 套话黑名单（绝对禁止）：\n    " + joined)
        else:
            lines.append("- Phrase blacklist (never allowed):\n    " + joined)

    return "\n".join(lines) if len(lines) > 1 else ""


def build_reader_contract_section(
    invariants: ProjectInvariants,
    *,
    chapter_no: int | None = None,
    reader_contract_cadence_head: int = 10,
    reader_contract_cadence_tail: int = 5,
) -> str:
    """Emit the per-book "reader contract" (selling points + promise + hook strategy).

    Renders only when (a) ``HypeScheme`` has any populated fields AND
    (b) the chapter is within the first ``reader_contract_cadence_head``
    chapters OR it lines up every ``reader_contract_cadence_tail``
    chapters. Returning ``""`` keeps the prompt lean on long books.
    """

    scheme: HypeScheme = invariants.hype_scheme
    if scheme.is_empty:
        return ""

    # Cadence: always emit in the first 10 chapters; after that, every 5th.
    if chapter_no is not None and chapter_no > reader_contract_cadence_head:
        stride = max(reader_contract_cadence_tail, 1)
        if (chapter_no - reader_contract_cadence_head) % stride != 1:
            return ""

    is_zh = (invariants.language or "").lower().startswith("zh")
    lines: list[str] = []

    if is_zh:
        header = "【读者契约】"
        if scheme.selling_points:
            header += "（卖点：" + " / ".join(scheme.selling_points) + "）"
        lines.append(header)
        if scheme.reader_promise:
            lines.append(f"本书承诺：{scheme.reader_promise}")
        if scheme.chapter_hook_strategy:
            lines.append(f"章级钩子策略：{scheme.chapter_hook_strategy}")
        if scheme.hook_keywords:
            lines.append("核心钩子意象：" + "、".join(scheme.hook_keywords))
    else:
        header = "[READER CONTRACT]"
        if scheme.selling_points:
            header += " (selling points: " + " / ".join(scheme.selling_points) + ")"
        lines.append(header)
        if scheme.reader_promise:
            lines.append(f"Promise: {scheme.reader_promise}")
        if scheme.chapter_hook_strategy:
            lines.append(f"Chapter-level hook strategy: {scheme.chapter_hook_strategy}")
        if scheme.hook_keywords:
            lines.append("Hook imagery: " + ", ".join(scheme.hook_keywords))

    return "\n".join(lines) if len(lines) > 1 else ""


def build_hype_constraints(
    invariants: ProjectInvariants,
    *,
    band: HypeDensityBand,
    hype_type: HypeType | None,
    recipe: HypeRecipe | None,
    intensity_target: float,
    is_golden_three: bool = False,
) -> str:
    """Emit the per-chapter hype constraint block.

    Always renders when at least one of ``hype_type`` / ``recipe`` is
    present. Empty ``HypeScheme`` short-circuits at the caller
    (``build_chapter_prompt``) — we don't gate here so callers can test
    the section independently.
    """

    if hype_type is None and recipe is None:
        return ""

    is_zh = (invariants.language or "").lower().startswith("zh")
    lines: list[str] = []

    if is_zh:
        lines.append("【本章爽点约束】")
        if hype_type is not None:
            lines.append(
                f"- 爽点类型：{hype_type.value}（强度目标 "
                f"{intensity_target:.1f}/10）"
            )
        if recipe is not None:
            lines.append(f"- 推荐配方：【{recipe.key}】")
            if recipe.narrative_beats:
                lines.append(
                    "  叙事节拍：" + " → ".join(recipe.narrative_beats)
                )
            if recipe.trigger_keywords:
                lines.append(
                    "  关键意象：" + "、".join(recipe.trigger_keywords)
                )
            if recipe.cadence_hint:
                lines.append(f"  节奏提示：{recipe.cadence_hint}")
        if band.min_count_per_chapter >= 2 or is_golden_three:
            lines.append(
                "- 黄金三章特别约束：本章必须至少 2 个爽点峰值，"
                "第 1 个在前 1000 字内。"
            )
        lines.append(
            "- 爽点 ≠ 章末悬念。爽点负责【本章情绪释放峰值】，"
            "在中段或末段；章末悬念另起一段负责勾下一章，不得合写。"
        )
    else:
        lines.append("[CHAPTER HYPE CONSTRAINTS]")
        if hype_type is not None:
            lines.append(
                f"- Hype type: {hype_type.value} "
                f"(intensity target {intensity_target:.1f}/10)"
            )
        if recipe is not None:
            lines.append(f"- Recommended recipe: [{recipe.key}]")
            if recipe.narrative_beats:
                lines.append(
                    "  Beats: " + " -> ".join(recipe.narrative_beats)
                )
            if recipe.trigger_keywords:
                lines.append(
                    "  Imagery: " + ", ".join(recipe.trigger_keywords)
                )
            if recipe.cadence_hint:
                lines.append(f"  Cadence: {recipe.cadence_hint}")
        if band.min_count_per_chapter >= 2 or is_golden_three:
            lines.append(
                "- Golden-three-chapters rule: at least 2 hype peaks in this "
                "chapter, the first within the first 1000 characters."
            )
        lines.append(
            "- Hype != cliffhanger. The hype peak carries this chapter's "
            "emotional release in the mid or late section; the chapter-end "
            "cliffhanger is a separate paragraph that hooks the next chapter."
        )

    return "\n".join(lines) if len(lines) > 1 else ""


def build_methodology_inject(invariants: ProjectInvariants) -> str:
    """Assemble forced methodology fragments.

    These are treated as *unmodified* prose — the caller supplies them via
    ``invariants.forced_methodology_fragments``. Empty tuple → empty
    section. Phase 1 treats this as a pass-through; Phase 3 will pick
    per-chapter-beat fragments.
    """

    frags = invariants.forced_methodology_fragments
    if not frags:
        return ""
    header = "【强制创作方法论】" if invariants.language.lower().startswith("zh") else "## MANDATORY METHODOLOGY"
    body = "\n\n".join(f.strip() for f in frags if f and f.strip())
    if not body:
        return ""
    return f"{header}\n{body}"


def build_prior_chapter_tail(
    prior_text: str | None,
    *,
    max_chars: int = DEFAULT_PRIOR_CHAPTER_TAIL_CHARS,
) -> str:
    """Emit the last ``max_chars`` of the previous chapter verbatim.

    Critical for continuity (plan §3 L3). Summary doesn't work — the LLM
    needs the actual ending prose to continue voice, sensory texture, and
    open threads.
    """

    if not prior_text or max_chars <= 0:
        return ""
    tail = prior_text[-max_chars:].lstrip()
    if not tail:
        return ""
    header = "【前一章结尾原文（供连贯性参考）】"
    return f"{header}\n{tail}"


def build_anti_slop_footer(language: str) -> str:
    """Short, reusable anti-filler reminder at the bottom of the prompt."""

    if language.lower().startswith("zh"):
        return (
            "【禁止项】\n"
            "- 禁止在结尾写\"还有更多精彩\"\"欲知后事如何\"等套话；\n"
            "- 禁止用排比、反问、感叹号灌水；\n"
            "- 每段必须推进情节、冲突、揭示或感官，无推进即删。"
        )
    return (
        "## DO NOT\n"
        "- No cliffhanger clichés (\"and that was just the beginning\", etc.);\n"
        "- No padding with rhetorical questions, parallelism, or exclamation spam;\n"
        "- Every paragraph must advance plot, conflict, revelation, or sensory texture — otherwise cut it."
    )


# ---------------------------------------------------------------------------
# Top-level builder + regen hook.
# ---------------------------------------------------------------------------


def build_chapter_prompt(
    invariants: ProjectInvariants,
    diversity_budget: DiversityBudget,
    *,
    chapter_no: int | None = None,
    total_chapters: int | None = None,
    pacing_profile: str = "medium",
    system: str = "",
    bible_slice: str = "",
    scene_spec: str = "",
    prior_chapter_text: str | None = None,
    preassigned_opening: OpeningArchetype | None = None,
    opening_pool: Sequence[OpeningArchetype] | None = None,
    cliffhanger_policy: CliffhangerPolicy | None = None,
    prior_chapter_tail_chars: int = DEFAULT_PRIOR_CHAPTER_TAIL_CHARS,
    hot_vocab_window: int = DEFAULT_HOT_VOCAB_WINDOW,
    hot_vocab_top_n: int = DEFAULT_HOT_VOCAB_TOP_N,
    hot_vocab_min_count: int = DEFAULT_HOT_VOCAB_MIN_COUNT,
    no_repeat_within_openings: int = DEFAULT_NO_REPEAT_WITHIN_OPENINGS,
    reader_contract_cadence_head: int = 10,
    reader_contract_cadence_tail: int = 5,
) -> PromptPlan:
    """Assemble a full ``PromptPlan`` for a chapter.

    The caller supplies the sections this constructor can't own
    (``system``, ``bible_slice``, ``scene_spec``). The constructor fills
    in everything diversity-related (archetype pick, hot-vocab ban,
    cliffhanger assignment, prior-chapter tail, anti-slop footer) AND
    Phase 1 hype engine sections (reader contract + per-chapter hype
    constraints).
    """

    policy = cliffhanger_policy or invariants.cliffhanger_policy
    opening = choose_opening_archetype(
        diversity_budget,
        pool=opening_pool or invariants.opening_archetype_pool,
        preassigned=preassigned_opening,
        no_repeat_within=no_repeat_within_openings,
    )
    cliffhanger = choose_cliffhanger_type(diversity_budget, policy=policy)

    # Hype engine — no-op when scheme is empty.
    hype_type: HypeType | None = None
    recipe: HypeRecipe | None = None
    intensity_target = 0.0
    hype_section = ""
    if not invariants.hype_scheme.is_empty and chapter_no is not None:
        total = total_chapters or max(chapter_no, 1)
        band = target_hype_for_chapter(
            chapter_no, total, pacing_profile=pacing_profile
        )
        hype_type, recipe, intensity_target = pick_hype_for_chapter(
            band,
            invariants.hype_scheme.recipe_deck,
            recent_hype_types=list(
                reversed(diversity_budget.recent_hype_types(5))
            ),
            recent_recipe_keys=list(
                reversed(diversity_budget.recent_recipe_keys(5))
            ),
        )
        hype_section = build_hype_constraints(
            invariants,
            band=band,
            hype_type=hype_type,
            recipe=recipe,
            intensity_target=intensity_target,
            is_golden_three=chapter_no <= 3,
        )

    reader_contract = build_reader_contract_section(
        invariants,
        chapter_no=chapter_no,
        reader_contract_cadence_head=reader_contract_cadence_head,
        reader_contract_cadence_tail=reader_contract_cadence_tail,
    )

    plan = PromptPlan(
        system=system,
        invariants_section=build_invariants_section(invariants),
        bible_slice=bible_slice,
        reader_contract_section=reader_contract,
        methodology_inject=build_methodology_inject(invariants),
        hype_constraints=hype_section,
        diversity_constraints=build_diversity_constraints(
            invariants,
            diversity_budget,
            assigned_opening=opening,
            assigned_cliffhanger=cliffhanger,
            hot_vocab_window=hot_vocab_window,
            hot_vocab_top_n=hot_vocab_top_n,
            hot_vocab_min_count=hot_vocab_min_count,
        ),
        prior_chapter_tail=build_prior_chapter_tail(
            prior_chapter_text, max_chars=prior_chapter_tail_chars
        ),
        scene_spec=scene_spec,
        anti_slop_footer=build_anti_slop_footer(invariants.language),
        chapter_no=chapter_no,
        assigned_opening=opening,
        assigned_cliffhanger=cliffhanger,
        assigned_hype_type=hype_type,
        assigned_hype_recipe=recipe,
        assigned_hype_intensity=(
            intensity_target if hype_type is not None else None
        ),
    )
    return plan


def rebuild_with_feedback(
    prior_plan: PromptPlan, feedback: str
) -> PromptPlan:
    """Attach the L4.5 remediation block to a plan for the next regen attempt.

    Returns a **new** ``PromptPlan`` — we honour immutability-by-default
    even though the underlying dataclass is mutable. The feedback block
    replaces any prior one (we don't chain them; the loop sends the
    *latest* ``QualityReport`` feedback each attempt).
    """

    if not feedback or not feedback.strip():
        return replace(prior_plan, feedback_block="")
    return replace(prior_plan, feedback_block=feedback.strip())


__all__ = [
    "DEFAULT_HOT_VOCAB_MIN_COUNT",
    "DEFAULT_HOT_VOCAB_TOP_N",
    "DEFAULT_HOT_VOCAB_WINDOW",
    "DEFAULT_NO_REPEAT_WITHIN_OPENINGS",
    "DEFAULT_PRIOR_CHAPTER_TAIL_CHARS",
    "PromptPlan",
    "build_anti_slop_footer",
    "build_chapter_prompt",
    "build_diversity_constraints",
    "build_hype_constraints",
    "build_invariants_section",
    "build_methodology_inject",
    "build_prior_chapter_tail",
    "build_reader_contract_section",
    "choose_cliffhanger_type",
    "choose_opening_archetype",
    "rebuild_with_feedback",
]
