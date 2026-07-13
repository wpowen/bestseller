"""Book-level story-enhancer selection → hard outline contracts.

These are the opt-in "make the story good" capabilities a user checks when
creating a book (frontend checkboxes / CLI flags), stored under
``ProjectCreate.metadata[STORY_ENHANCERS_METADATA_KEY]``. Unlike the per-chapter
story-effect router (which picks one primary skill per chapter), a book-level
selection applies to EVERY chapter: each checked capability becomes a hard
per-chapter outline contract, closing the gap that made zhaoshen-hr-v13 come out
logically rigorous but bland (脑洞/反差/喜剧 never reached the chapters).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bestseller.services.story_effect_skills import (
    ALL_STORY_EFFECT_SKILL_KEYS,
    _render_selected_story_effect_contract,
)

STORY_ENHANCERS_METADATA_KEY = "story_enhancers"

# 代价强度三档（纯正爽文）：standard=现状；external=代价外置(主角不自损,
# 由对手/世界/资源承担);minimal=极简代价(服务爽感,点到为止)。默认 standard
# → 全链字节级不变。
COST_STYLES: tuple[str, ...] = ("standard", "external", "minimal")
COST_STYLE_DEFAULT = "standard"

# The four genre_creativity directions (反常识/反套路 axes).
CREATIVITY_DIRECTIONS: tuple[str, ...] = (
    "genre-synthesis",
    "cross-genre-friction",
    "distilled-mechanism-remix",
    "anti-cliche-opening",
)
_CREATIVITY_LABEL_ZH = {
    "genre-synthesis": "题材合成（奖励轴主兑现）",
    "cross-genre-friction": "跨题材摩擦（第二母题碰撞）",
    "distilled-mechanism-remix": "蒸馏机制重混（抽象结构+原创规则）",
    "anti-cliche-opening": "反套路开篇（禁模板开场+事件碰撞）",
}


class StoryEnhancerSelection(BaseModel):
    """What the user checked at book creation. All fields default off/empty."""

    model_config = ConfigDict(frozen=True)

    brainhole: bool = False
    concept_lab: bool = False
    creativity_direction: str | None = None
    effect_skills: tuple[str, ...] = ()
    # 脑洞全开：松开概念淘汰赛三道收敛闸门（俗套/审计改罚分、降 winner_min、
    # 判官偏新颖）。无每章合同 → 故意不计入 is_empty()（不渲染空的合同头），
    # 只在构思淘汰赛调用点消费。
    wild_concept: bool = False
    # 纯正爽文·代价强度三档（见 COST_STYLES）。非合同类，不计入 is_empty()；
    # 由意识形态内核派生/渲染消费，控制"金手指是否强制自损代价"。
    cost_style: str = COST_STYLE_DEFAULT

    @field_validator("cost_style", mode="before")
    @classmethod
    def _valid_cost_style(cls, v: Any) -> str:
        s = str(v or "").strip().lower()
        return s if s in COST_STYLES else COST_STYLE_DEFAULT

    @field_validator("creativity_direction")
    @classmethod
    def _valid_direction(cls, v: str | None) -> str | None:
        if not v:
            return None
        return v if v in CREATIVITY_DIRECTIONS else None

    @field_validator("effect_skills", mode="before")
    @classmethod
    def _coerce_skills(cls, v: Any) -> tuple[str, ...]:
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, (list, tuple)):
            return ()
        seen: list[str] = []
        for item in v:
            key = str(item).strip()
            if key in ALL_STORY_EFFECT_SKILL_KEYS and key not in seen:
                seen.append(key)
        return tuple(seen)

    def is_empty(self) -> bool:
        """无每章合同类选择（决定是否渲染故事增强合同块）。

        故意不含 wild_concept / cost_style——它们无每章合同，不该触发空合同头。
        """
        return not (
            self.brainhole
            or self.concept_lab
            or self.creativity_direction
            or self.effect_skills
        )

    def is_default(self) -> bool:
        """全部字段皆默认 → 无需持久化、全链字节级不变。持久化门用此判定。"""
        return (
            self.is_empty()
            and not self.wild_concept
            and self.cost_style == COST_STYLE_DEFAULT
        )


def resolve_story_enhancers(
    metadata: Mapping[str, Any] | None,
) -> StoryEnhancerSelection:
    """Read the book-level enhancer selection from project metadata (tolerant)."""

    raw = (metadata or {}).get(STORY_ENHANCERS_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return StoryEnhancerSelection()
    try:
        return StoryEnhancerSelection.model_validate(dict(raw))
    except ValueError:
        return StoryEnhancerSelection()


def wants_wild_concept(metadata: Mapping[str, Any] | None) -> bool:
    """脑洞全开开关（单一真源读取口）。构思淘汰赛据此决定是否合并 wild_mode。"""

    return resolve_story_enhancers(metadata).wild_concept


def resolve_cost_style(metadata: Mapping[str, Any] | None) -> str:
    """代价强度三档（单一真源读取口）。意识形态内核派生/渲染据此选变体。"""

    return resolve_story_enhancers(metadata).cost_style


def render_story_enhancer_contract_block(
    selection: StoryEnhancerSelection,
    *,
    language: str = "zh-CN",
) -> str:
    """Render the book-level hard contract: every checked capability must be
    cashed in every chapter (or, for brainhole/concept-lab, on the cadence the
    bespoke blocks define). Empty selection → empty block."""

    if selection.is_empty():
        return ""
    is_en = language.lower().startswith("en")
    parts: list[str] = []
    if is_en:
        parts.append(
            "[BOOK STORY-ENHANCER CONTRACT — chosen at creation, HARD: every "
            "chapter must visibly cash the selected effects; a chapter that only "
            "advances logistics without cashing them is incomplete and will be "
            "sent back for repair]"
        )
    else:
        parts.append(
            "【本书故事增强合同 — 建书时勾选，硬约束：每一章都必须可见地兑现下列被勾选的"
            "故事效果；只推进事务流程、不兑现这些效果的章视为未完成，会被打回重修】"
        )
    if "comedy_engine" in selection.effect_skills:
        parts.append(
            "・【基调锚点·硬底线】本书是爽文喜剧，喜剧/脑洞是贯穿全书的基础基调，"
            "不是某些章节才有的可选效果：每一章——哪怕该章主线极沉重、主推爽点/两难/"
            "悬念——都必须保留至少一个符合本题材世界规则的喜剧或脑洞落点，让读者"
            "全程保持愉悦。严禁整章只有沉重剧情而无喜剧落点；越往后主线越重，越要靠这条"
            "基调锚点把'虐'压成'爽中带笑'。"
            if not is_en
            else "・[TONE ANCHOR · HARD FLOOR] This is a comedic feel-good web "
            "novel. Comedy/brainhole is the book's baseline tone running through "
            "EVERY chapter — not an optional per-chapter effect. Even chapters whose "
            "primary engine is hype/dilemma/suspense MUST still carry at least one "
            "visible genre-native comic or brainhole beat so "
            "the reader stays delighted. Never let a whole chapter be heavy drama "
            "with no comedic landing."
        )
    if selection.brainhole:
        parts.append(
            "・脑洞引擎：每章（或每 2 章至少一次）必须落地一个高概念反差名场面，"
            "把'设定脑洞'变成读者眼见的具体奇观，而不是停留在设定层。"
            if not is_en
            else "・Brainhole: land one high-concept contrast set-piece every 1-2 "
            "chapters — turn the premise's hook into a visible on-page spectacle."
        )
    if selection.concept_lab:
        parts.append(
            "・脑洞组合 story-loop：每章必须推进'开篇问题→复现压力→升级轴'这条主回路，"
            "不许把它晾在一边只写支线事务。"
            if not is_en
            else "・Concept-lab story-loop: every chapter must advance the "
            "opening-question → recurring-pressure → escalation loop."
        )
    if selection.creativity_direction:
        label = _CREATIVITY_LABEL_ZH.get(
            selection.creativity_direction, selection.creativity_direction
        )
        parts.append(
            f"・反常识方向【{label}】：全书冲突设计走这条反套路轴，"
            "禁止退回到同质化的程序/合规微冲突。"
            if not is_en
            else f"・Anti-cliché direction [{selection.creativity_direction}]: design "
            "conflict along this axis; do not regress to homogeneous procedural "
            "micro-conflicts."
        )
    for skill_key in selection.effect_skills:
        contract = _render_selected_story_effect_contract(skill_key, language=language)
        if contract:
            parts.append(contract)
    return "\n\n".join(parts)


# ── coverage audit (the 校验门) ───────────────────────────────────────────────
# Heuristic on-page signals per effect. The audit only catches the *systemic
# absence* case (the zhaoshen-hr-v13 failure: a selected effect appears in ~0
# chapters), so a coarse keyword signal is enough and avoids false per-chapter
# blocking. Cashed effects live in prose, not structured fields, so this reads
# the outline's conflict/scene text.
_EFFECT_SIGNALS: dict[str, tuple[str, ...]] = {
    "brainhole_engine": ("脑洞", "反差", "名场面", "奇观", "改写", "错位", "反常识"),
    "comedy_engine": ("笑", "荒诞", "吐槽", "喜剧", "反差", "梗", "尴尬", "囧", "搞笑"),
    "emotional_payoff_engine": ("泪", "感动", "释怀", "和解", "愧", "暖", "心疼", "动容"),
    "relationship_chemistry_engine": ("信任", "羁绊", "暧昧", "并肩", "默契", "化学", "关系"),
    "suspense_reveal_engine": ("悬念", "揭示", "真相", "谜", "线索", "隐瞒", "反转"),
    "hype_satisfaction_engine": ("爽", "打脸", "逆袭", "碾压", "翻盘", "解气", "扬眉"),
    "moral_dilemma_engine": ("两难", "道德", "抉择", "代价", "良心", "牺牲", "困境"),
    "system_payoff_engine": ("系统", "奖励", "结算", "面板", "兑现", "升级", "数值"),
    "tension_pressure_engine": ("压力", "紧张", "倒计时", "危机", "逼", "险", "死线"),
    "rhythm_pacing_engine": ("节奏", "张弛", "留白", "急转", "缓"),
    "twist_reversal_engine": ("反转", "逆转", "没想到", "反水", "出乎", "翻盘", "真相"),
    "callback_motif_engine": ("回收", "呼应", "伏笔", "母题", "照应", "前文"),
    "world_texture_engine": ("质感", "气味", "触感", "风物", "市井", "细节"),
    "wonder_awe_engine": ("惊奇", "震撼", "壮观", "敬畏", "浩瀚", "奇景"),
    "danger_action_engine": ("追杀", "搏", "逃", "险", "动作", "厮杀", "亡命"),
    "dialogue_spark_engine": ("交锋", "唇枪", "怼", "机锋", "台词", "对峙"),
    "healing_grief_engine": ("治愈", "悲伤", "抚慰", "失去", "怀念", "哀"),
    "romance_tenderness_engine": ("温柔", "心动", "浪漫", "脸红", "心跳", "靠近"),
}

# A selected effect should surface in at least this share of chapters.
_COVERAGE_FLOOR = 0.34


def _flatten_text(value: Any) -> str:
    """Recursively collect all leaf text from nested dict/list/scalars."""
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_text(v) for v in value)
    return str(value)


# Fields whose (possibly nested) text the audit scans for effect signals. The
# selected effects are contractually delivered into the *structured* contract
# fields (brainhole_contract, the per-skill *_effect_contract under
# selected_effect_skills.expected_contracts, etc.), NOT only into main_conflict
# /hook_description. Reading only the narrative fields under-counted real
# coverage to ~0% and produced misleading repair directives.
_BLOB_FIELDS = (
    "main_conflict",
    "title",
    "hook_description",
    "goal",
    "opening_situation",
    "opening_pressure",
    "target_emotion",
    "tail_hook",
    "key_reveals",
    "required_payoff",
    "brainhole_contract",
    "selected_effect_skills",
    "methodology_contract",
    "causal_contract",
)


def _chapter_blob(chapter: Mapping[str, Any]) -> str:
    parts = [_flatten_text(chapter.get(k)) for k in _BLOB_FIELDS]
    for scene in chapter.get("scenes") or []:
        if isinstance(scene, Mapping):
            parts.append(_flatten_text(scene.get("purpose")))
            parts.append(_flatten_text(scene.get("summary")))
            parts.append(_flatten_text(scene.get("beats")))
    return " ".join(p for p in parts if p)


def audit_story_enhancer_coverage(
    chapters: list[Mapping[str, Any]],
    selection: StoryEnhancerSelection,
    *,
    floor: float = _COVERAGE_FLOOR,
) -> list[dict[str, Any]]:
    """Return one gap dict per selected effect that is under-delivered across the
    chapter batch. Empty list = every selected effect is sufficiently cashed."""

    chapters = [c for c in chapters if isinstance(c, Mapping)]
    if not chapters or not selection.effect_skills:
        return []
    blobs = [(_chapter_blob(c), c.get("chapter_number")) for c in chapters]
    total = len(blobs)
    gaps: list[dict[str, Any]] = []
    for skill_key in selection.effect_skills:
        signals = _EFFECT_SIGNALS.get(skill_key)
        if not signals:
            continue
        hit_numbers = [num for blob, num in blobs if any(s in blob for s in signals)]
        coverage = len(hit_numbers) / total if total else 0.0
        if coverage < floor:
            missing = [num for blob, num in blobs if not any(s in blob for s in signals)]
            gaps.append(
                {
                    "effect": skill_key,
                    "coverage": round(coverage, 2),
                    "missing_chapters": missing,
                }
            )
    return gaps


def story_enhancer_repair_directives(
    chapters: list[Mapping[str, Any]],
    selection: StoryEnhancerSelection,
) -> list[str]:
    """Repair directives for under-delivered selected effects (feeds the
    chapter-outline repair loop, mirroring the commercial-judge findings path)."""

    directives: list[str] = []
    for gap in audit_story_enhancer_coverage(chapters, selection):
        effect = gap["effect"]
        miss = gap["missing_chapters"][:12]
        directives.append(
            f"勾选的故事效果 `{effect}` 在本批章纲覆盖率仅 {int(gap['coverage'] * 100)}%，"
            f"严重不足（这些章完全没兑现：{miss}）。请重写这些章，让每章都落地一个 `{effect}` "
            "的具体 beat（行动/选择/揭示兑现，不是贴标签），不要再退回到同质化的程序/合规微冲突。"
        )
    return directives


# ── prose-writer injection ────────────────────────────────────────────────────
# The outline layer renders the book-level contract into every outline prompt and
# the chapter LLM cashes each selected effect into *structured* chapter fields
# (brainhole_contract / selected_effect_skills). But the prose writer never saw
# any of it — it only knew the genre label. That gap is why selected enhancers
# (脑洞/喜剧/爽点) never showed up in the prose even when the outline planned them.
# These renderers carry both the book-level mandate AND this chapter's planned
# cashing into the writer prompt. Soft/advisory: empty selection + empty chapter
# contract → empty string, so non-opted-in books get a byte-identical prompt.

# Human-readable labels for the default brainhole_contract field keys
# (see planner._brainhole_required_contract_fields). Unknown keys fall back to
# the raw key so a customised profile still renders.
_BRAINHOLE_FIELD_LABEL_ZH: dict[str, str] = {
    "one_sentence_sell": "一句话卖点",
    "character_core_used": "调用的人设内核",
    "modern_system": "题材原生规则/系统",
    "contrast_mechanism": "反差机制",
    "visible_comedy": "可见喜剧落点",
    "serious_underbelly": "严肃内核",
    "plot_consequence": "剧情后果",
    "protagonist_decision": "主角抉择",
    "growth_stage_fit": "成长阶段契合",
    "risk_check": "风险校验",
}

# Per-field text cap so a verbose contract can't blow up the prose prompt.
_WRITER_FIELD_CHAR_CAP = 240


def _render_chapter_cashed_effects_block(
    chapter_metadata: Mapping[str, Any] | None,
    *,
    language: str,
) -> str:
    """Render the SPECIFIC effect beats the outline already planned for THIS
    chapter (persisted into ``chapter.metadata_json`` by
    ``workflows._sync_chapter_causality_metadata``). Empty when absent."""

    if not isinstance(chapter_metadata, Mapping):
        return ""
    is_en = language.lower().startswith("en")
    sections: list[str] = []

    brainhole = chapter_metadata.get("brainhole_contract")
    if isinstance(brainhole, Mapping) and brainhole:
        rows: list[str] = []
        for key, value in brainhole.items():
            text = _flatten_text(value).strip()
            if not text:
                continue
            label = key if is_en else _BRAINHOLE_FIELD_LABEL_ZH.get(key, key)
            rows.append(f"  - {label}：{text[:_WRITER_FIELD_CHAR_CAP]}")
        if rows:
            head = (
                "・This chapter's planned brainhole beat — render it as an on-page "
                "spectacle the reader can see, not just a premise note:"
                if is_en
                else "・本章已规划的脑洞兑现点（把它写成读者眼见的名场面，不要停在设定层）："
            )
            sections.append(head + "\n" + "\n".join(rows[:10]))

    effects = chapter_metadata.get("selected_effect_skills")
    if isinstance(effects, Mapping) and effects:
        rows = []
        primary = _flatten_text(effects.get("primary")).strip()
        secondary = _flatten_text(effects.get("secondary")).strip()
        if primary:
            rows.append((f"  - primary: {primary}" if is_en else f"  - 主效果：{primary}"))
        if secondary:
            rows.append((f"  - secondary: {secondary}" if is_en else f"  - 次效果：{secondary}"))
        expected = effects.get("expected_contracts")
        if isinstance(expected, Mapping):
            for name, value in expected.items():
                text = _flatten_text(value).strip()
                if text:
                    rows.append(f"  - {name}：{text[:_WRITER_FIELD_CHAR_CAP]}")
        elif isinstance(expected, (list, tuple)):
            names = "、".join(str(x).strip() for x in expected if str(x).strip())
            if names:
                rows.append(
                    (f"  - contracts to cash: {names}" if is_en else f"  - 需兑现合同：{names}")
                )
        if rows:
            head = (
                "・This chapter's primary story-effect beats to cash in the prose:"
                if is_en
                else "・本章主推的故事效果兑现点（在正文里真正落地，不是贴标签）："
            )
            sections.append(head + "\n" + "\n".join(rows[:12]))

    return "\n\n".join(sections)


def render_story_enhancer_writer_block(
    project_metadata: Mapping[str, Any] | None,
    chapter_metadata: Mapping[str, Any] | None = None,
    *,
    language: str = "zh-CN",
) -> str:
    """Writer-facing story-enhancer block for the PROSE prompt.

    Two layers, both soft/advisory (never a gate):
      1. book-level — the same hard contract injected into outlines (tone anchor +
         per-skill effect contracts). Tells the writer WHAT kind of story this is
         and which effects every chapter owes. Single-sourced from
         ``render_story_enhancer_contract_block`` so it can never drift from the
         outline contract.
      2. per-chapter — the SPECIFIC beats the outline already planned for this
         chapter (brainhole_contract / selected_effect_skills), so the writer
         lands the exact spectacle/comedy/hype beat instead of re-inventing it.

    Returns "" when the book opted into nothing AND the chapter carries no cashed
    contract → the prose prompt stays byte-identical for non-opted-in books.
    """

    book_block = render_story_enhancer_contract_block(
        resolve_story_enhancers(project_metadata), language=language
    )
    chapter_block = _render_chapter_cashed_effects_block(chapter_metadata, language=language)
    parts = [p for p in (book_block, chapter_block) if p]
    return "\n\n".join(parts)
