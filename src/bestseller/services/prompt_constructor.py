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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import logging
import random
from typing import Any

from bestseller.services.audit_input_sanitizer import sanitize_audit_block, sanitize_audit_input
from bestseller.services.diversity_budget import DiversityBudget
from bestseller.services.fanqie_market_integration import render_fanqie_craft_profile_block
from bestseller.services.hype_engine import (
    GoldenFingerLadder,
    GoldenFingerRung,
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
from bestseller.services.kernel_composer import render_narrative_richness_prompt_block
from bestseller.services.market_constraint_compiler import render_chapter_constraints_block
from bestseller.services.methodology_compiler import (
    ChapterPosition,
    MethodologyStage,
    compile_methodology,
)
from bestseller.services.reader_persona_simulator import render_persona_feedback_block
from bestseller.services.voice_signature import render_voice_dna_block

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


def render_fanqie_market_craft_profile_block(
    craft_profile: Mapping[str, Any] | None,
    *,
    language: str = "zh-CN",
) -> str:
    """Render an anonymous Fanqie craft profile for chapter-planning prompts."""

    return render_fanqie_craft_profile_block(craft_profile, language=language)


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
    seam_contract_section: str = ""
    invariants_section: str = ""
    voice_dna_section: str = ""
    bible_slice: str = ""
    ranking_capability_profile_section: str = ""
    market_profile_section: str = ""
    market_constraints_section: str = ""
    progression_constraints: str = ""
    decision_policy_constraints: str = ""
    rule_system_constraints: str = ""
    faction_ecology_constraints: str = ""
    relationship_agency_constraints: str = ""
    narrative_richness_section: str = ""
    reader_contract_section: str = ""
    ledger_delta_section: str = ""
    audit_report_section: str = ""
    methodology_inject: str = ""
    hype_constraints: str = ""
    diversity_constraints: str = ""
    persona_feedback_section: str = ""
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

    # ------------------------------------------------------------------
    # Rendering.
    #
    # ``render()`` (legacy) returns a single concatenated string used by
    # the original single-message call path. The new ``render_system()``
    # / ``render_user()`` pair partitions the same sections into the two
    # halves that should map to ``LLMCompletionRequest.system_prompt``
    # and ``LLMCompletionRequest.user_prompt`` respectively.
    #
    # Why split?
    #   - **Prompt caching**: Anthropic caches stable prefixes. The
    #     system half (role charter / invariants / methodology / style
    #     anchors / anti-slop footer) is identical across all chapters of
    #     a book; the user half (bible slice for *this* chapter, hype +
    #     diversity for *this* chapter, scene spec, prior tail, feedback)
    #     changes per chapter. Caching the system half drops 30-40% of
    #     repeated input tokens on long runs.
    #   - **Attention bias**: Claude is trained to follow system
    #     instructions more strictly than user content. Role charter +
    #     hard constraints belong in system.
    #
    # The split is **inclusive of every field that ``render()`` emits**:
    # ``render() == render_system() + "\n\n" + render_user()`` when both
    # halves are non-empty. This is asserted in unit tests.
    # ------------------------------------------------------------------

    # Section names that are stable across all chapters of a single book.
    # These belong in the system message.
    _SYSTEM_SECTIONS: tuple[str, ...] = (
        "system",
        "seam_contract_section",
        "invariants_section",
        "voice_dna_section",
        "methodology_inject",
        "anti_slop_footer",
    )

    # Section names that change per chapter. These belong in the user
    # message. Order matches the legacy ``render()`` order to preserve
    # semantic continuity for the LLM.
    _USER_SECTIONS: tuple[str, ...] = (
        "bible_slice",
        "ranking_capability_profile_section",
        "market_profile_section",
        "market_constraints_section",
        "progression_constraints",
        "decision_policy_constraints",
        "rule_system_constraints",
        "faction_ecology_constraints",
        "relationship_agency_constraints",
        "narrative_richness_section",
        "reader_contract_section",
        "ledger_delta_section",
        "audit_report_section",
        "hype_constraints",
        "diversity_constraints",
        "persona_feedback_section",
        "prior_chapter_tail",
        "scene_spec",
        "feedback_block",
    )

    def _section_value(self, name: str) -> str:
        value = getattr(self, name, "")
        if not isinstance(value, str):
            return ""
        return value.strip()

    def render_system(self) -> str:
        """Stable prefix suitable for ``messages[0].role=='system'`` and prompt caching.

        Includes: role charter, invariants, methodology injection, anti-slop
        footer. Empty sections are skipped.
        """
        parts = [self._section_value(n) for n in self._SYSTEM_SECTIONS]
        return "\n\n".join(p for p in parts if p)

    def render_user(self) -> str:
        """Per-chapter volatile body suitable for ``messages[1].role=='user'``.

        Includes: bible slice + all per-chapter constraints + prior chapter
        tail + scene spec + feedback block. Order matches legacy ``render()``.
        """
        parts = [self._section_value(n) for n in self._USER_SECTIONS]
        return "\n\n".join(p for p in parts if p)

    def render(self) -> str:
        """Legacy single-string render — order preserved verbatim.

        Order matters: prior validated prompts and downstream tests anchor on
        the interleaved layout below. Do **not** reshuffle this list — use
        ``render_system()`` / ``render_user()`` / ``to_messages()`` if you
        want the cache-friendly partition.
        """
        sections = [
            self.system,
            self.seam_contract_section,
            self.invariants_section,
            self.voice_dna_section,
            self.bible_slice,
            self.ranking_capability_profile_section,
            self.market_profile_section,
            self.market_constraints_section,
            self.progression_constraints,
            self.decision_policy_constraints,
            self.rule_system_constraints,
            self.faction_ecology_constraints,
            self.relationship_agency_constraints,
            self.narrative_richness_section,
            self.reader_contract_section,
            self.ledger_delta_section,
            self.audit_report_section,
            self.methodology_inject,
            self.hype_constraints,
            self.diversity_constraints,
            self.persona_feedback_section,
            self.prior_chapter_tail,
            self.scene_spec,
            self.anti_slop_footer,
            self.feedback_block,
        ]
        return "\n\n".join(s.strip() for s in sections if s and s.strip())

    def to_messages(
        self,
        *,
        enable_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """Emit Anthropic-style ``[system, user]`` messages.

        When ``enable_cache=True`` and the runtime is Anthropic (litellm
        passthrough), the system block is tagged with
        ``cache_control={"type": "ephemeral"}`` so it joins the prompt
        cache. Non-Anthropic providers silently ignore the marker.

        Returns an empty list when both halves render empty (no plan).
        """
        system_part = self.render_system()
        user_part = self.render_user()
        if not system_part and not user_part:
            return []
        messages: list[dict[str, Any]] = []
        if system_part:
            if enable_cache:
                messages.append(
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": system_part,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                )
            else:
                messages.append({"role": "system", "content": system_part})
        if user_part:
            messages.append({"role": "user", "content": user_part})
        return messages


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
        # Full pool, not a preview: the naming gate validates every detected
        # name against the complete pool, so the writer must see the same
        # contract it will be judged by (truncating to 10 caused recurring
        # NAMING_OUT_OF_POOL regens in long runs).
        pool_names = [
            name.strip()
            for name in invariants.naming_scheme.seed_pool[:60]
            if name and name.strip()
        ]
        bits.append(f"【命名池·硬约束】{', '.join(pool_names)}")
        bits.append(
            "【命名规则】有名字的角色只能用命名池内的名字；"
            "池外一律不得自创人名。无名路人/群众用职务、身份或外貌称谓"
            "（如「值班科员」「那名中年男人」），不要给他们取名。"
        )
    return "【故事不变量】\n" + "\n".join(bits)


def build_opening_hook_directive(
    chapter_no: int | None,
    *,
    language: str = "zh-CN",
) -> str:
    """Render the golden-three opening contract as a system-priority block."""

    if chapter_no is None or chapter_no > 3:
        return ""
    if not language.lower().startswith("zh"):
        return ""
    return (
        "【黄金三章·开篇硬契约 — 最高优先级，违反即重写】\n"
        "1. 第一句长度 ≤ 25 个汉字。\n"
        "2. 第一段长度 ≤ 50 个汉字。\n"
        "3. 前 100 字必须聚焦主角 + 1 个可视化异常物（不能只有人物对话）。\n"
        "4. 前 200 字必须出现至少 1 个不可解释的怪事（视觉/听觉/物件异常）。\n"
        "5. 前 500 字主角必须因这个怪事被迫做出决定（不能只是观察、回忆、对话）。\n"
        "6. 严禁前 500 字内插入超过 2 句的回忆/倒叙（如“X 年前”“那时候他才 N 岁”“他想起”“当年”）。\n"
        "7. 严禁前 300 字介绍 ≥ 3 个新角色（开篇主角 + 委托人即上限）。\n"
        "8. 章末必须留下一个能让读者立刻点开下一章的具体悬念（不能是抽象感叹）。"
    )


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
        # Anti-"instruction-manual" opening (universal retention guard). The
        # single biggest page-one bounce cause is a system panel / customer-
        # service voice / AI / narrator reciting its own rules as a cold
        # infodump. The golden finger must be SHOWN through a concrete present
        # event, never lectured. Emitted alongside the opening directive so the
        # empty-input contract (no opening ⇒ no opening lines) is preserved.
        if language.lower().startswith("zh"):
            lines.append(
                "- 开篇手法（硬性）：严禁用系统面板／客服／AI／旁白整段朗读自身设定或规则来开场。"
                "金手指、系统、世界规则必须通过一个当下正在发生的具体事件被「演」出来——"
                "读者只能从动作、对白和可感后果里推断规则；第一页不得出现成段的规则背诵、"
                "术语解释或「系统说明书」式信息倾倒。"
            )
        else:
            lines.append(
                "- Opening craft (hard rule): never open by having a system panel "
                "/ customer-service voice / AI / narrator recite its own settings "
                "or rules as a cold infodump. The golden finger / system / world "
                "rules must be SHOWN through one concrete event happening now — "
                "the reader infers the rules from action, dialogue, and tangible "
                "consequences. No block of rule-recitation, term-glossing, or "
                "instruction-manual dump on page one."
            )

    if assigned_cliffhanger is not None:
        if language.lower().startswith("zh"):
            lines.append(
                f"- 收尾镜头: 最后一段采用 {assigned_cliffhanger.value} 类动作/画面/揭示，"
                f"不得与近期收束方式重复。"
            )
        else:
            lines.append(
                f"- Ending frame: close on a {assigned_cliffhanger.value} "
                f"action/image/reveal pattern distinct from recent endings."
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
    sanitize_for_prose: bool = True,
) -> str:
    """Emit the per-book reader-facing expectation block.

    Renders only when (a) ``HypeScheme`` has any populated fields AND
    (b) the chapter is within the first ``reader_contract_cadence_head``
    chapters OR it lines up every ``reader_contract_cadence_tail``
    chapters. Returning ``""`` keeps the prompt lean on long books.

    ``sanitize_for_prose=True`` is the anti-slop path: it translates market
    mechanics into visible reader expectations and keeps hook / selling /
    promise labels out of the prose prompt. ``False`` preserves the legacy
    contract wording for diagnostics.
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

    if is_zh and sanitize_for_prose:
        lines.append("【读者期望画面】")
        if scheme.selling_points:
            lines.append(
                "- 读者要看见的吸引力："
                + " / ".join(_sanitize_reader_contract_phrase(p) for p in scheme.selling_points)
            )
        if scheme.reader_promise:
            lines.append(
                "- 本场要兑现为可见后果："
                + _sanitize_reader_contract_phrase(scheme.reader_promise)
            )
        if scheme.chapter_hook_strategy:
            lines.append("- 收尾方式：用未完成动作、画面或揭示自然牵引后续。")
        if scheme.hook_keywords:
            lines.append(
                "- 必须落地的具体物件/感官："
                + "、".join(_sanitize_reader_contract_phrase(k) for k in scheme.hook_keywords)
            )
        lines.append("- 不要复述企划词；把抽象要求转成角色动作、物件、声音、触感和后果。")
    elif (not is_zh) and sanitize_for_prose:
        lines.append("[READER-VISIBLE EXPECTATIONS]")
        if scheme.selling_points:
            lines.append(
                "- Visible appeal: "
                + " / ".join(_sanitize_reader_contract_phrase(p) for p in scheme.selling_points)
            )
        if scheme.reader_promise:
            lines.append(
                "- Convert the expectation into a visible consequence: "
                + _sanitize_reader_contract_phrase(scheme.reader_promise)
            )
        if scheme.chapter_hook_strategy:
            lines.append("- End through an unfinished action, image, or reveal.")
        if scheme.hook_keywords:
            lines.append(
                "- Required concrete objects/sensory anchors: "
                + ", ".join(_sanitize_reader_contract_phrase(k) for k in scheme.hook_keywords)
            )
        lines.append("- Do not restate planning or marketing terms; render them as action, objects, sound, touch, and consequence.")
    elif is_zh:
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


def _sanitize_reader_contract_phrase(value: str) -> str:
    cleaned = str(value or "").strip()
    replacements = {
        "钩子": "未完成动作",
        "hook": "unfinished action",
        "Hook": "Unfinished action",
        "卖点": "可见吸引力",
        "selling point": "visible appeal",
        "selling": "visible",
        "承诺": "可见期待",
        "promise": "expectation",
        "Promise": "Expectation",
        "主线": "当前冲突",
        "副线": "旁支互动",
        "长线": "未完成线索",
        "本章": "本场",
        "本卷": "当前段落",
        "章末": "收尾",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    return cleaned


def build_hype_constraints(
    invariants: ProjectInvariants,
    *,
    band: HypeDensityBand,
    hype_type: HypeType | None,
    recipe: HypeRecipe | None,
    intensity_target: float,
    is_golden_three: bool = False,
    ladder_rung: GoldenFingerRung | None = None,
) -> str:
    """Emit the per-chapter hype constraint block.

    Always renders when at least one of ``hype_type`` / ``recipe`` is
    present. Empty ``HypeScheme`` short-circuits at the caller
    (``build_chapter_prompt``) — we don't gate here so callers can test
    the section independently.

    ``ladder_rung`` — when non-None, appends a "本章金手指阶梯" (chapter
    golden-finger rung) block describing the capability currently
    unlocked and the anchor hype type. Plan §Phase 3 requires this
    injection so LLMs know which power tier the protagonist is on.
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
            "- 爽点负责当前情绪释放峰值；收尾另起一段，只落在动作、画面或揭示，"
            "不得解释其意义。"
        )
        if ladder_rung is not None:
            rung_lines = [
                "【本章金手指阶梯】",
                f"- 第 {ladder_rung.rung_index} 级："
                f"{ladder_rung.capability}"
                f"（锚定爽点：{ladder_rung.hype_type_anchor.value}）",
            ]
            if ladder_rung.signal_keywords:
                rung_lines.append(
                    "  关键信号：" + "、".join(ladder_rung.signal_keywords)
                )
            rung_lines.append(
                "- 能力释放不得超过本级上限；如需越级需在正文中明确代价或限制。"
            )
            lines.extend(rung_lines)
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
            "- The hype peak carries the local emotional release; the ending "
            "is a separate paragraph that lands on action, image, or reveal "
            "without explaining what it means."
        )
        if ladder_rung is not None:
            rung_lines = [
                "[CHAPTER GOLDEN-FINGER RUNG]",
                f"- Rung {ladder_rung.rung_index}: "
                f"{ladder_rung.capability} "
                f"(hype anchor: {ladder_rung.hype_type_anchor.value})",
            ]
            if ladder_rung.signal_keywords:
                rung_lines.append(
                    "  Signals: " + ", ".join(ladder_rung.signal_keywords)
                )
            rung_lines.append(
                "- Do not exceed this rung's ceiling; any higher-tier power "
                "usage must come with an explicit cost or constraint in prose."
            )
            lines.extend(rung_lines)

    return "\n".join(lines) if len(lines) > 1 else ""


def build_methodology_inject(
    invariants: ProjectInvariants,
    *,
    stage: MethodologyStage = MethodologyStage.PROSE_SCENE,
    prompt_pack_key: str | None = None,
    chapter_no: int | None = None,
    chapter_position: ChapterPosition | None = None,
    token_budget: int = 1500,
) -> str:
    """Assemble forced methodology fragments plus stage-aware methodology.

    These are treated as *unmodified* prose — the caller supplies them via
    ``invariants.forced_methodology_fragments``. Empty tuple → empty
    section. Calling with only ``invariants`` preserves the original legacy
    block and appends compiled methodology only for zh paths.
    """

    legacy = _legacy_forced_methodology(invariants)
    compiled_text = ""
    has_new_context = (
        prompt_pack_key is not None
        or chapter_no is not None
        or chapter_position is not None
        or stage is not MethodologyStage.PROSE_SCENE
        or token_budget != 1500
    )
    if has_new_context and invariants.language.lower().startswith("zh"):
        compiled = compile_methodology(
            stage=stage,
            prompt_pack_key=prompt_pack_key,
            language=invariants.language,
            chapter_no=chapter_no,
            chapter_position=chapter_position,
            token_budget=token_budget,
        )
        compiled_text = compiled.text

    return "\n\n".join(section for section in (legacy, compiled_text) if section)


def _legacy_forced_methodology(invariants: ProjectInvariants) -> str:
    """Original build_methodology_inject behavior, preserved verbatim."""

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
            "【禁止项 / 去AI味写作铁律】\n"
            "- 禁止在结尾写\"还有更多精彩\"\"欲知后事如何\"等套话；\n"
            "- 禁止用排比、反问、感叹号灌水；\n"
            "- 禁止论文/助手腔：此外、值得注意的是、深入探讨、作为……的证明、"
            "不仅仅是……而是……、请告诉我、希望这对您有帮助；\n"
            "- 禁止作者替读者下结论：他不知道的是、事情并不简单、一个更大的阴谋即将展开、"
            "命运的齿轮开始转动、蝴蝶效应开始显现；\n"
            "- 少用过滤词和情绪标签：感到、意识到、知道、明白、发现、似乎、仿佛、某种、"
            "震惊、愤怒、恐惧、紧张；要直接写读者能看见/听见/摸到的变化；\n"
            "- 避免工整 AI 句式：不是……而是……、与其……不如……、无论……都……、"
            "随着……越来越……、既……又……；把判断拆成动作、异常、确认、结果；\n"
            "- 信任读者，不要替读者总结意义；把意义压进动作、物证、对话和身体反应；\n"
            "- 句子长短要错开，少用三段式列举，允许短句直接落地；\n"
            "- 每段必须推进情节、冲突、揭示或感官，无推进即删。"
        )
    return (
        "## DO NOT\n"
        "- No ending clichés (\"and that was just the beginning\", etc.);\n"
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
    prompt_pack_key: str | None = None,
    chapter_position: ChapterPosition | None = None,
    methodology_token_budget: int = 1500,
    system: str = "",
    bible_slice: str = "",
    ranking_capability_profile_block: str = "",
    market_profile_section: str = "",
    fanqie_market_craft_profile: Mapping[str, Any] | None = None,
    progression_context_block: str = "",
    decision_policy_block: str = "",
    rule_system_context_block: str = "",
    faction_ecology_context_block: str = "",
    relationship_agency_context_block: str = "",
    narrative_richness_context: object = None,
    narrative_richness_context_block: str = "",
    seam_contract: object = None,
    seam_contract_block: str = "",
    current_region: str | None = None,
    ledger_delta_block: str = "",
    story_bible_dir: str | None = None,
    audit_report_block: str = "",
    scene_spec: str = "",
    prior_chapter_text: str | None = None,
    linear_arc_summary_present: bool = False,
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
    sanitize_for_prose: bool = True,
    golden_finger_ladder: GoldenFingerLadder | None = None,
    voice_dna: Any = None,
    chapter_market_constraints: Any = None,
    prior_persona_feedback: Any = None,
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
        ladder_rung: GoldenFingerRung | None = None
        if (
            golden_finger_ladder is not None
            and not golden_finger_ladder.is_empty
        ):
            ladder_rung = golden_finger_ladder.rung_for_chapter(
                chapter_no, total
            )
        hype_section = build_hype_constraints(
            invariants,
            band=band,
            hype_type=hype_type,
            recipe=recipe,
            intensity_target=intensity_target,
            is_golden_three=chapter_no <= 3,
            ladder_rung=ladder_rung,
        )

    reader_contract = build_reader_contract_section(
        invariants,
        chapter_no=chapter_no,
        reader_contract_cadence_head=reader_contract_cadence_head,
        reader_contract_cadence_tail=reader_contract_cadence_tail,
        sanitize_for_prose=sanitize_for_prose,
    )
    fanqie_market_block = render_fanqie_market_craft_profile_block(
        fanqie_market_craft_profile,
        language=invariants.language,
    )
    market_section = "\n\n".join(
        section
        for section in (market_profile_section, fanqie_market_block)
        if section.strip()
    )
    voice_dna_section = render_voice_dna_block(
        voice_dna, language=invariants.language
    )
    market_constraints_section = render_chapter_constraints_block(
        chapter_market_constraints, language=invariants.language
    )
    persona_feedback_section = render_persona_feedback_block(
        prior_persona_feedback, language=invariants.language
    )
    seam_section = seam_contract_block or _render_seam_contract_block(seam_contract)
    richness_section = narrative_richness_context_block or render_narrative_richness_prompt_block(
        _slice_narrative_richness_context(
            narrative_richness_context,
            chapter_no=chapter_no,
            current_region=current_region,
        ),
        chapter_no=chapter_no,
        current_region=current_region,
    )
    ledger_section = ledger_delta_block
    if not ledger_section and story_bible_dir and chapter_no is not None:
        from bestseller.services.ledger_delta_reader import read_ledger_delta_block

        ledger_section = read_ledger_delta_block(story_bible_dir, chapter_no=chapter_no)
    audit_section = sanitize_audit_block("【审计报告摘录（已净化）】", audit_report_block)

    opening_hook_directive = build_opening_hook_directive(
        chapter_no,
        language=invariants.language,
    )
    effective_system = "\n\n".join(
        section for section in (opening_hook_directive, system) if section.strip()
    )

    plan = PromptPlan(
        system=effective_system,
        seam_contract_section=seam_section,
        invariants_section=build_invariants_section(invariants),
        voice_dna_section=voice_dna_section,
        bible_slice=bible_slice,
        ranking_capability_profile_section=ranking_capability_profile_block,
        market_profile_section=market_section,
        market_constraints_section=market_constraints_section,
        progression_constraints=progression_context_block,
        decision_policy_constraints=decision_policy_block,
        rule_system_constraints=rule_system_context_block,
        faction_ecology_constraints=faction_ecology_context_block,
        relationship_agency_constraints=relationship_agency_context_block,
        narrative_richness_section=richness_section,
        reader_contract_section=reader_contract,
        ledger_delta_section=ledger_section,
        audit_report_section=audit_section,
        methodology_inject=build_methodology_inject(
            invariants,
            stage=MethodologyStage.PROSE_SCENE,
            prompt_pack_key=prompt_pack_key,
            chapter_no=chapter_no,
            chapter_position=chapter_position,
            token_budget=methodology_token_budget,
        ),
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
        persona_feedback_section=persona_feedback_section,
        prior_chapter_tail=build_prior_chapter_tail(
        prior_chapter_text,
        max_chars=300 if linear_arc_summary_present else prior_chapter_tail_chars,
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


@dataclass(frozen=True)
class ChapterHypeBlocks:
    """Pre-rendered hype sections for a chapter.

    Built once per chapter by ``build_chapter_hype_blocks`` and attached to
    every ``SceneWriterContextPacket`` produced for that chapter's scenes,
    so all scenes share the same hype assignment and the chapter row can
    persist the metadata after the draft lands.
    """

    reader_contract_block: str
    hype_constraints_block: str
    assigned_hype_type: HypeType | None
    assigned_hype_recipe: HypeRecipe | None
    assigned_hype_intensity: float | None

    @property
    def is_empty(self) -> bool:
        """True when both blocks are empty — safe no-op for legacy projects."""
        return (
            not self.reader_contract_block
            and not self.hype_constraints_block
            and self.assigned_hype_type is None
        )


EMPTY_HYPE_BLOCKS = ChapterHypeBlocks(
    reader_contract_block="",
    hype_constraints_block="",
    assigned_hype_type=None,
    assigned_hype_recipe=None,
    assigned_hype_intensity=None,
)


def build_chapter_hype_blocks(
    invariants: ProjectInvariants,
    diversity_budget: DiversityBudget,
    *,
    chapter_no: int,
    total_chapters: int,
    pacing_profile: str = "medium",
    reader_contract_cadence_head: int = 10,
    reader_contract_cadence_tail: int = 5,
    sanitize_for_prose: bool = True,
    golden_finger_ladder: GoldenFingerLadder | None = None,
) -> ChapterHypeBlocks:
    """Pick once per chapter; return pre-rendered blocks for scene plumbing.

    Extracted from ``build_chapter_prompt`` so the scene pipeline can share
    the same assignment across every scene of a chapter without rebuilding
    the full chapter prompt. Legacy projects (empty ``HypeScheme``) get
    ``EMPTY_HYPE_BLOCKS`` back and the caller stays a no-op.
    """

    scheme = invariants.hype_scheme
    if scheme.is_empty:
        return EMPTY_HYPE_BLOCKS

    total = max(total_chapters, chapter_no, 1)
    band = target_hype_for_chapter(
        chapter_no, total, pacing_profile=pacing_profile
    )
    hype_type, recipe, intensity_target = pick_hype_for_chapter(
        band,
        scheme.recipe_deck,
        recent_hype_types=list(
            reversed(diversity_budget.recent_hype_types(5))
        ),
        recent_recipe_keys=list(
            reversed(diversity_budget.recent_recipe_keys(5))
        ),
    )
    ladder_rung: GoldenFingerRung | None = None
    if (
        golden_finger_ladder is not None
        and not golden_finger_ladder.is_empty
    ):
        ladder_rung = golden_finger_ladder.rung_for_chapter(chapter_no, total)

    hype_section = build_hype_constraints(
        invariants,
        band=band,
        hype_type=hype_type,
        recipe=recipe,
        intensity_target=intensity_target,
        is_golden_three=chapter_no <= 3,
        ladder_rung=ladder_rung,
    )

    reader_contract = build_reader_contract_section(
        invariants,
        chapter_no=chapter_no,
        reader_contract_cadence_head=reader_contract_cadence_head,
        reader_contract_cadence_tail=reader_contract_cadence_tail,
        sanitize_for_prose=sanitize_for_prose,
    )

    return ChapterHypeBlocks(
        reader_contract_block=reader_contract,
        hype_constraints_block=hype_section,
        assigned_hype_type=hype_type,
        assigned_hype_recipe=recipe,
        assigned_hype_intensity=(
            intensity_target if hype_type is not None else None
        ),
    )


@dataclass(frozen=True)
class ChapterL3Blocks:
    """Per-chapter L3 sections that augment hype_blocks.

    These are the cross-cutting diversity + methodology slices
    ``build_chapter_prompt`` normally emits. Extracting them as a separate
    block lets the scene pipeline inject them without rewriting the scene
    prompt assembly end-to-end. Legacy projects with empty invariants get
    ``EMPTY_L3_BLOCKS`` back and the caller stays a no-op.
    """

    invariants_section: str
    methodology_inject: str
    diversity_constraints: str
    narrative_richness_section: str
    seam_contract_section: str
    ledger_delta_section: str
    anti_slop_footer: str
    assigned_opening: OpeningArchetype | None
    assigned_cliffhanger: CliffhangerType | None

    @property
    def is_empty(self) -> bool:
        return not (
            self.invariants_section
            or self.methodology_inject
            or self.diversity_constraints
            or self.narrative_richness_section
            or self.seam_contract_section
            or self.ledger_delta_section
            or self.anti_slop_footer
        )

    def as_prompt_block(self) -> str:
        """Render as a single text block, stable section order."""
        parts = [
            self.invariants_section,
            self.methodology_inject,
            self.diversity_constraints,
            self.seam_contract_section,
            self.narrative_richness_section,
            self.ledger_delta_section,
            self.anti_slop_footer,
        ]
        return "\n\n".join(s.strip() for s in parts if s and s.strip())


EMPTY_L3_BLOCKS = ChapterL3Blocks(
    invariants_section="",
    methodology_inject="",
    diversity_constraints="",
    narrative_richness_section="",
    seam_contract_section="",
    ledger_delta_section="",
    anti_slop_footer="",
    assigned_opening=None,
    assigned_cliffhanger=None,
)


def build_chapter_l3_blocks(
    invariants: ProjectInvariants,
    diversity_budget: DiversityBudget,
    *,
    chapter_no: int,
    preassigned_opening: OpeningArchetype | None = None,
    opening_pool: Sequence[OpeningArchetype] | None = None,
    cliffhanger_policy: CliffhangerPolicy | None = None,
    hot_vocab_window: int = DEFAULT_HOT_VOCAB_WINDOW,
    hot_vocab_top_n: int = DEFAULT_HOT_VOCAB_TOP_N,
    hot_vocab_min_count: int = DEFAULT_HOT_VOCAB_MIN_COUNT,
    no_repeat_within_openings: int = DEFAULT_NO_REPEAT_WITHIN_OPENINGS,
    narrative_richness_context: object = None,
    narrative_richness_context_block: str = "",
    seam_contract: object = None,
    seam_contract_block: str = "",
    current_region: str | None = None,
    ledger_delta_block: str = "",
    story_bible_dir: str | None = None,
) -> ChapterL3Blocks:
    """Build the per-chapter L3 sections that pair with hype_blocks.

    The scene pipeline injects these alongside ``reader_contract_block`` +
    ``hype_constraints_block``, giving the LLM the full diversity + methodology
    + anti-slop contract on every scene of the chapter without the scene
    layer having to rebuild a PromptPlan per scene.
    """

    policy = cliffhanger_policy or invariants.cliffhanger_policy
    opening = choose_opening_archetype(
        diversity_budget,
        pool=opening_pool or invariants.opening_archetype_pool,
        preassigned=preassigned_opening,
        no_repeat_within=no_repeat_within_openings,
    )
    cliffhanger = choose_cliffhanger_type(diversity_budget, policy=policy)

    ledger_section = ledger_delta_block
    if not ledger_section:
        if story_bible_dir:
            from bestseller.services.ledger_delta_reader import read_ledger_delta_block

            ledger_section = read_ledger_delta_block(story_bible_dir, chapter_no=chapter_no)

    return ChapterL3Blocks(
        invariants_section=build_invariants_section(invariants),
        methodology_inject=build_methodology_inject(invariants),
        diversity_constraints=build_diversity_constraints(
            invariants,
            diversity_budget,
            assigned_opening=opening,
            assigned_cliffhanger=cliffhanger,
            hot_vocab_window=hot_vocab_window,
            hot_vocab_top_n=hot_vocab_top_n,
            hot_vocab_min_count=hot_vocab_min_count,
        ),
        seam_contract_section=seam_contract_block or _render_seam_contract_block(
            seam_contract
        ),
        narrative_richness_section=(
            narrative_richness_context_block
            or render_narrative_richness_prompt_block(
                _slice_narrative_richness_context(
                    narrative_richness_context,
                    chapter_no=chapter_no,
                    current_region=current_region,
                ),
                chapter_no=chapter_no,
                current_region=current_region,
            )
        ),
        ledger_delta_section=ledger_section,
        anti_slop_footer=build_anti_slop_footer(invariants.language),
        assigned_opening=opening,
        assigned_cliffhanger=cliffhanger,
    )


def _slice_narrative_richness_context(
    narrative_richness_context: object,
    *,
    chapter_no: int | None,
    current_region: str | None,
) -> object:
    if narrative_richness_context is None or chapter_no is None:
        return narrative_richness_context
    from bestseller.services.kernel_composer import NarrativeRichnessKernels
    from bestseller.services.kernel_delta_slicer import KernelDeltaSlicer

    try:
        context = NarrativeRichnessKernels.model_validate(narrative_richness_context)
    except (TypeError, ValueError):
        return narrative_richness_context
    return KernelDeltaSlicer().slice_for_chapter(
        context,
        chapter_no=chapter_no,
        current_region=current_region,
    )


def _render_seam_contract_block(seam_contract: object) -> str:
    if seam_contract is None:
        return ""
    from bestseller.services.seam_prompt_composer import render_seam_prompt_block

    return render_seam_prompt_block(seam_contract)


def build_line_rotation_nudge(
    line_gap_report: Any,
    *,
    language: str = "zh-CN",
) -> str:
    """Render the narrative-line rotation nudge for the writing brief.

    Delegates to ``narrative_line_tracker.render_rotation_nudge`` so the
    exact wording (most-overdue layer, gap vs. budget) lives in one
    place.  Returns an empty string when no nudge is needed so callers
    can unconditionally concatenate this into their prompt assembly.

    ``Any`` is used here to avoid a hard import cycle — the tracker
    imports ``genre_profile_thresholds`` which is leaf, but keeping the
    parameter ``Any`` lets the prompt constructor stay import-neutral.
    """

    if line_gap_report is None:
        return ""
    try:
        from bestseller.services.narrative_line_tracker import (
            render_rotation_nudge,
        )
    except ImportError:  # pragma: no cover - defensive
        return ""
    return render_rotation_nudge(line_gap_report, language=language)


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
    return replace(prior_plan, feedback_block=sanitize_audit_input(feedback))


__all__ = [
    "DEFAULT_HOT_VOCAB_MIN_COUNT",
    "DEFAULT_HOT_VOCAB_TOP_N",
    "DEFAULT_HOT_VOCAB_WINDOW",
    "DEFAULT_NO_REPEAT_WITHIN_OPENINGS",
    "DEFAULT_PRIOR_CHAPTER_TAIL_CHARS",
    "EMPTY_HYPE_BLOCKS",
    "EMPTY_L3_BLOCKS",
    "ChapterHypeBlocks",
    "ChapterL3Blocks",
    "PromptPlan",
    "build_anti_slop_footer",
    "build_chapter_hype_blocks",
    "build_chapter_l3_blocks",
    "build_chapter_prompt",
    "build_diversity_constraints",
    "build_hype_constraints",
    "build_invariants_section",
    "build_line_rotation_nudge",
    "build_methodology_inject",
    "build_prior_chapter_tail",
    "build_reader_contract_section",
    "choose_cliffhanger_type",
    "choose_opening_archetype",
    "rebuild_with_feedback",
    "render_fanqie_market_craft_profile_block",
]
