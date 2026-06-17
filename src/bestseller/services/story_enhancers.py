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
        return not (
            self.brainhole
            or self.concept_lab
            or self.creativity_direction
            or self.effect_skills
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
            "悬念——都必须保留至少一个'神仙在现代规则中报错'的喜剧或脑洞落点，让读者"
            "全程保持愉悦。严禁整章只有沉重剧情而无喜剧落点；越往后主线越重，越要靠这条"
            "基调锚点把'虐'压成'爽中带笑'。"
            if not is_en
            else "・[TONE ANCHOR · HARD FLOOR] This is a comedic feel-good web "
            "novel. Comedy/brainhole is the book's baseline tone running through "
            "EVERY chapter — not an optional per-chapter effect. Even chapters whose "
            "primary engine is hype/dilemma/suspense MUST still carry at least one "
            "visible 'deity mis-firing in modern rules' comic or brainhole beat so "
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
