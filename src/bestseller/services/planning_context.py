"""Compact context summaries for planning prompts and volume feedback collection.

Instead of passing raw ``_json_dumps()`` of full planning artifacts into LLM
prompts (4500-10000 tokens each), this module produces **prose summaries**
(300-500 tokens each) that preserve the information the LLM actually needs
while drastically reducing prompt size.

All ``summarize_*`` functions are **deterministic string formatting** — no LLM
calls, no async.  The ``collect_*`` and ``load_*`` functions are async because
they query the database.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    CanonFactModel,
    CharacterModel,
    CharacterStateSnapshotModel,
    ProjectModel,
    RelationshipEventModel,
    VolumeModel,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s(val: Any, default: str = "") -> str:
    """Coerce to non-empty string."""
    if val is None:
        return default
    text = str(val).strip()
    return text if text else default


def _list_s(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(v) for v in val if v]
    return []


def _mapping(val: Any) -> dict[str, Any]:
    return val if isinstance(val, dict) else {}


# ---------------------------------------------------------------------------
# Phase 1: Deterministic prose summaries of planning artifacts
# ---------------------------------------------------------------------------

def summarize_book_spec(book_spec: dict[str, Any], *, language: str = "zh-CN") -> str:
    """~400 tokens: title, logline, protagonist core, key characters, series engine essentials."""
    bs = _mapping(book_spec)
    is_en = language.startswith("en")
    protag = _mapping(bs.get("protagonist"))
    engine = _mapping(bs.get("series_engine"))
    stakes = _mapping(bs.get("stakes"))

    if is_en:
        lines = [
            f"Title: {_s(bs.get('title'))}",
            f"Logline: {_s(bs.get('logline'))}",
            f"Genre: {_s(bs.get('genre'))} | Audience: {_s(bs.get('target_audience'))}",
            f"Tone: {', '.join(_list_s(bs.get('tone')))}",
            f"Themes: {', '.join(_list_s(bs.get('themes')))}",
            f"Protagonist: {_s(protag.get('name'))} — archetype: {_s(protag.get('archetype'))}, "
            f"edge: {_s(protag.get('golden_finger'))}, core wound: {_s(protag.get('core_wound'))}",
            f"External goal: {_s(protag.get('external_goal'))}",
            f"Stakes: personal={_s(stakes.get('personal'))}, social={_s(stakes.get('social'))}",
            f"Series engine: {_s(engine.get('core_loop'))}",
            f"Hook style: {_s(engine.get('hook_style'))} | Payoff rhythm: {_s(engine.get('payoff_rhythm'))}",
            f"Reader promise: {_s(engine.get('reader_promise'))}",
        ]
    else:
        lines = [
            f"书名：{_s(bs.get('title'))}",
            f"一句话：{_s(bs.get('logline'))}",
            f"类型：{_s(bs.get('genre'))} | 读者：{_s(bs.get('target_audience'))}",
            f"调性：{', '.join(_list_s(bs.get('tone')))}",
            f"主题：{', '.join(_list_s(bs.get('themes')))}",
            f"主角：{_s(protag.get('name'))}——原型：{_s(protag.get('archetype'))}，"
            f"金手指：{_s(protag.get('golden_finger'))}，核心创伤：{_s(protag.get('core_wound'))}",
            f"外在目标：{_s(protag.get('external_goal'))}",
            f"赌注：个人={_s(stakes.get('personal'))}，社会={_s(stakes.get('social'))}",
            f"连载引擎：{_s(engine.get('core_loop'))}",
            f"钩子策略：{_s(engine.get('hook_style'))} | 爽点节奏：{_s(engine.get('payoff_rhythm'))}",
            f"读者承诺：{_s(engine.get('reader_promise'))}",
        ]

    # ── key_characters: pass book_spec characters to downstream planning ──
    raw_chars = bs.get("key_characters") or []
    if isinstance(raw_chars, list) and raw_chars:
        if is_en:
            lines.append("Key characters defined in BookSpec:")
        else:
            lines.append("BookSpec 已定义的关键角色：")
        for ch in raw_chars:
            cm = _mapping(ch)
            name = _s(cm.get("name"))
            role = _s(cm.get("role"))
            personality = ", ".join(_list_s(cm.get("personality_keywords")))
            relationship = _s(cm.get("relationship_to_protagonist"))
            if is_en:
                line = f"  - {name} ({role})"
                if personality:
                    line += f" — personality: {personality}"
                if relationship:
                    line += f" — relationship: {relationship}"
            else:
                line = f"  - {name}（{role}）"
                if personality:
                    line += f"——性格特征：{personality}"
                if relationship:
                    line += f"——与主角关系：{relationship}"
            lines.append(line)
        if is_en:
            lines.append("IMPORTANT: CastSpec MUST use these characters. Do NOT invent replacements.")
        else:
            lines.append("【重要】CastSpec 必须使用以上角色，不得另行编造替代角色。")

    # ── antagonist_forces ──
    raw_forces = bs.get("antagonist_forces") or []
    if isinstance(raw_forces, list) and raw_forces:
        if is_en:
            lines.append("Antagonist forces defined in BookSpec:")
        else:
            lines.append("BookSpec 已定义的冲突力量：")
        for af in raw_forces:
            fm = _mapping(af)
            fname = _s(fm.get("name"))
            ftype = _s(fm.get("force_type"))
            threat = _s(fm.get("threat_description"))
            if is_en:
                lines.append(f"  - {fname} ({ftype}): {threat}")
            else:
                lines.append(f"  - {fname}（{ftype}）：{threat}")

    return "\n".join(lines)


def summarize_world_spec(world_spec: dict[str, Any], *, language: str = "zh-CN") -> str:
    """~400 tokens: world name, key rules, power system, key locations, factions."""
    ws = _mapping(world_spec)
    is_en = language.startswith("en")
    power = _mapping(ws.get("power_system"))
    rules = ws.get("rules") or []
    locations = ws.get("locations") or []
    factions = ws.get("factions") or []

    if is_en:
        lines = [
            f"World: {_s(ws.get('world_name'))} — {_s(ws.get('world_premise'))}",
            f"Power system: {_s(power.get('name'))}, tiers: {', '.join(_list_s(power.get('tiers')))}",
            f"  Acquisition: {_s(power.get('acquisition_method'))} | Limits: {_s(power.get('hard_limits'))}",
        ]
        for i, rule in enumerate(rules[:3]):
            r = _mapping(rule)
            lines.append(f"  Rule {i+1}: {_s(r.get('name'))} — {_s(r.get('description'))} (consequence: {_s(r.get('story_consequence'))})")
        for loc in locations[:3]:
            loc_d = _mapping(loc)
            lines.append(f"  Location: {_s(loc_d.get('name'))} [{_s(loc_d.get('type'))}] — {_s(loc_d.get('story_role'))}")
        for fac in factions[:2]:
            fac_d = _mapping(fac)
            lines.append(f"  Faction: {_s(fac_d.get('name'))} — goal: {_s(fac_d.get('goal'))}, stance: {_s(fac_d.get('relationship_to_protagonist'))}")
    else:
        lines = [
            f"世界：{_s(ws.get('world_name'))}——{_s(ws.get('world_premise'))}",
            f"力量体系：{_s(power.get('name'))}，层级：{', '.join(_list_s(power.get('tiers')))}",
            f"  获取方式：{_s(power.get('acquisition_method'))} | 硬限制：{_s(power.get('hard_limits'))}",
        ]
        for i, rule in enumerate(rules[:3]):
            r = _mapping(rule)
            lines.append(f"  规则{i+1}：{_s(r.get('name'))}——{_s(r.get('description'))}（后果：{_s(r.get('story_consequence'))}）")
        for loc in locations[:3]:
            loc_d = _mapping(loc)
            lines.append(f"  地点：{_s(loc_d.get('name'))}[{_s(loc_d.get('type'))}]——{_s(loc_d.get('story_role'))}")
        for fac in factions[:2]:
            fac_d = _mapping(fac)
            lines.append(f"  势力：{_s(fac_d.get('name'))}——目标：{_s(fac_d.get('goal'))}，与主角关系：{_s(fac_d.get('relationship_to_protagonist'))}")
    return "\n".join(lines)


def summarize_cast_spec(
    cast_spec: dict[str, Any],
    *,
    language: str = "zh-CN",
    volume_number: int | None = None,
) -> str:
    """~400 tokens: protagonist core, antagonist core, top supporting cast.

    If *volume_number* is given, filters ``antagonist_forces`` to those active
    in that volume.
    """
    cs = _mapping(cast_spec)
    is_en = language.startswith("en")
    protag = _mapping(cs.get("protagonist"))
    antag = _mapping(cs.get("antagonist"))

    def _char_line(c: dict[str, Any], role_label: str) -> str:
        vp = _mapping(c.get("voice_profile"))
        if is_en:
            return (
                f"{role_label}: {_s(c.get('name'))} — {_s(c.get('role'))}, "
                f"goal: {_s(c.get('goal'))}, flaw: {_s(c.get('flaw'))}, "
                f"arc: {_s(c.get('arc_trajectory'))}, power: {_s(c.get('power_tier'))}, "
                f"voice: {_s(vp.get('speech_register'))}"
            )
        return (
            f"{role_label}：{_s(c.get('name'))}——{_s(c.get('role'))}，"
            f"目标：{_s(c.get('goal'))}，缺陷：{_s(c.get('flaw'))}，"
            f"弧线：{_s(c.get('arc_trajectory'))}，实力：{_s(c.get('power_tier'))}，"
            f"语感：{_s(vp.get('speech_register'))}"
        )

    def _personhood_lines(c: dict[str, Any], role_label: str) -> list[str]:
        """Render the psych/life/family/belief layer into 4-6 compact lines.

        Tightly budgeted (~120 tokens per character) so the chapter prompt
        stays under its envelope. Skips empty fields entirely rather than
        printing placeholders — an empty psych profile is silence, not
        noise.
        """
        out: list[str] = []
        psych = _mapping(c.get("psych_profile"))
        if psych:
            psych_bits: list[str] = []
            if psych.get("mbti"):
                psych_bits.append(f"MBTI={_s(psych.get('mbti'))}")
            if psych.get("enneagram"):
                psych_bits.append(f"九型={_s(psych.get('enneagram'))}")
            if psych.get("attachment_style"):
                psych_bits.append(f"依恋={_s(psych.get('attachment_style'))}")
            big_five = psych.get("big_five") or {}
            if isinstance(big_five, dict) and big_five:
                ocean = ", ".join(f"{k}:{v}" for k, v in big_five.items())
                psych_bits.append(f"OCEAN={ocean}")
            if psych_bits:
                tag = "Psych" if is_en else "人格"
                out.append(f"  {tag}[{role_label}]: {' | '.join(psych_bits)}")

        history = _mapping(c.get("life_history"))
        if history:
            evt = history.get("formative_events") or []
            evt_titles: list[str] = []
            if isinstance(evt, list):
                for e in evt[:3]:
                    em = _mapping(e)
                    if em.get("title"):
                        evt_titles.append(_s(em.get("title")))
            if evt_titles or history.get("defining_moments"):
                tag = "History" if is_en else "生平"
                pieces = evt_titles[:]
                if history.get("defining_moments"):
                    pieces.extend(_list_s(history.get("defining_moments"))[:2])
                out.append(f"  {tag}[{role_label}]: { '；'.join(pieces[:3]) }")

        family = _mapping(c.get("family_imprint"))
        if family:
            fam_bits: list[str] = []
            if family.get("parenting_style"):
                fam_bits.append(_s(family.get("parenting_style")))
            if family.get("sibling_dynamics"):
                fam_bits.append(_s(family.get("sibling_dynamics")))
            if fam_bits:
                tag = "Family" if is_en else "原生家庭"
                out.append(f"  {tag}[{role_label}]: { '；'.join(fam_bits) }")

        beliefs = _mapping(c.get("beliefs"))
        if beliefs:
            blf_bits: list[str] = []
            if beliefs.get("ideology"):
                blf_bits.append(f"信念={_s(beliefs.get('ideology'))}")
            if beliefs.get("religion"):
                devot = _s(beliefs.get("devotion_level")) or ""
                blf_bits.append(f"宗教={_s(beliefs.get('religion'))}{('/' + devot) if devot else ''}")
            if beliefs.get("philosophical_stance"):
                blf_bits.append(f"哲学={_s(beliefs.get('philosophical_stance'))}")
            if blf_bits:
                tag = "Beliefs" if is_en else "信仰"
                out.append(f"  {tag}[{role_label}]: { ' | '.join(blf_bits) }")

        social = _mapping(c.get("social_network"))
        if social:
            family_ties = social.get("family") or []
            mentor_ties = social.get("mentors") or []
            ties: list[str] = []
            for t in (family_ties[:2] + mentor_ties[:1]) if isinstance(family_ties, list) and isinstance(mentor_ties, list) else []:
                tm = _mapping(t)
                if tm.get("name"):
                    ties.append(f"{_s(tm.get('name'))}（{_s(tm.get('bond'))}）")
            if ties:
                tag = "Ties" if is_en else "关键关系"
                out.append(f"  {tag}[{role_label}]: { '；'.join(ties) }")

        return out

    def _villain_lines(c: dict[str, Any]) -> list[str]:
        """Render villain_charisma so the chapter prompt knows the antagonist
        is a tragic rival, not a difficulty slider."""
        v = _mapping(c.get("villain_charisma"))
        if not v:
            return []
        bits: list[str] = []
        if v.get("noble_motivation"):
            bits.append(f"高尚动机：{_s(v.get('noble_motivation'))}")
        if v.get("pain_origin"):
            bits.append(f"伤痛起源：{_s(v.get('pain_origin'))}")
        if v.get("personal_code"):
            code = _list_s(v.get("personal_code"))
            if code:
                bits.append(f"底线：{ '/'.join(code[:2]) }")
        if v.get("protagonist_mirror"):
            bits.append(f"与主角对照：{_s(v.get('protagonist_mirror'))}")
        if not bits:
            return []
        tag = "Villain charisma" if is_en else "反派魅力"
        return [f"  {tag}: { ' | '.join(bits) }"]

    protag_label = "Protagonist" if is_en else "主角"
    antag_label = "Antagonist" if is_en else "反派"
    lines = [
        _char_line(protag, protag_label),
        _char_line(antag, antag_label),
    ]
    lines.extend(_personhood_lines(protag, protag_label))
    lines.extend(_personhood_lines(antag, antag_label))
    lines.extend(_villain_lines(antag))

    # Antagonist forces (filter by volume if given)
    forces = _mapping(cast_spec).get("antagonist_forces") or []
    if isinstance(forces, list):
        for f in forces:
            fd = _mapping(f) if isinstance(f, dict) else {}
            if hasattr(f, "model_dump"):
                fd = f.model_dump()
            active_vols = fd.get("active_volumes") or []
            if volume_number is not None and active_vols and volume_number not in active_vols:
                continue
            label = "Force" if is_en else "冲突力量"
            lines.append(
                f"  {label}: {_s(fd.get('name'))} [{_s(fd.get('force_type'))}] — "
                f"{_s(fd.get('threat_description'))}"
            )

    # Top supporting cast (max 4)
    supporting = cs.get("supporting_cast") or []
    if isinstance(supporting, list):
        for sc in supporting[:4]:
            sc_d = _mapping(sc)
            role = _s(sc_d.get("role"), "supporting")
            if is_en:
                lines.append(f"  Supporting: {_s(sc_d.get('name'))} ({role}) — {_s(sc_d.get('goal'))}")
            else:
                lines.append(f"  配角：{_s(sc_d.get('name'))}（{role}）——{_s(sc_d.get('goal'))}")

    return "\n".join(lines)


def summarize_volume_plan_entry(entry: dict[str, Any], *, language: str = "zh-CN") -> str:
    """~200 tokens: single volume's plan in prose."""
    e = _mapping(entry)
    is_en = language.startswith("en")
    opening = _mapping(e.get("opening_state"))

    if is_en:
        lines = [
            f"Volume {_s(e.get('volume_number'))}: {_s(e.get('volume_title'))}",
            f"Theme: {_s(e.get('volume_theme'))} | Phase: {_s(e.get('conflict_phase'))} | Force: {_s(e.get('primary_force_name'))}",
            f"Chapters: {_s(e.get('chapter_count_target'))} | Words: {_s(e.get('word_count_target'))}",
            f"Goal: {_s(e.get('volume_goal'))}",
            f"Obstacle: {_s(e.get('volume_obstacle'))}",
            f"Climax: {_s(e.get('volume_climax'))}",
            f"End hook: {_s(e.get('volume_end_hook'))}",
            f"Opening state: {_s(opening.get('protagonist_status'))}, power: {_s(opening.get('protagonist_power_tier'))}",
        ]
    else:
        lines = [
            f"第{_s(e.get('volume_number'))}卷：{_s(e.get('volume_title'))}",
            f"主题：{_s(e.get('volume_theme'))} | 阶段：{_s(e.get('conflict_phase'))} | 力量：{_s(e.get('primary_force_name'))}",
            f"章数：{_s(e.get('chapter_count_target'))} | 字数：{_s(e.get('word_count_target'))}",
            f"目标：{_s(e.get('volume_goal'))}",
            f"障碍：{_s(e.get('volume_obstacle'))}",
            f"高潮：{_s(e.get('volume_climax'))}",
            f"卷尾钩子：{_s(e.get('volume_end_hook'))}",
            f"开局状态：{_s(opening.get('protagonist_status'))}，实力：{_s(opening.get('protagonist_power_tier'))}",
        ]
    return "\n".join(lines)


def summarize_volume_plan_context(
    volume_plan: list[dict[str, Any]],
    current_volume: int,
    *,
    language: str = "zh-CN",
) -> str:
    """Full detail for current volume, one-line summaries for adjacent volumes."""
    is_en = language.startswith("en")
    parts: list[str] = []
    normalized = volume_plan if isinstance(volume_plan, list) else []

    for entry in normalized:
        e = _mapping(entry)
        vol_num = int(e.get("volume_number") or 0)
        if vol_num == current_volume:
            parts.append(summarize_volume_plan_entry(e, language=language))
        elif abs(vol_num - current_volume) <= 1:
            # One-line summary for adjacent volumes
            if is_en:
                parts.append(
                    f"[Volume {vol_num}: {_s(e.get('volume_title'))} — "
                    f"phase: {_s(e.get('conflict_phase'))}, force: {_s(e.get('primary_force_name'))}]"
                )
            else:
                parts.append(
                    f"[第{vol_num}卷：{_s(e.get('volume_title'))}——"
                    f"阶段：{_s(e.get('conflict_phase'))}，力量：{_s(e.get('primary_force_name'))}]"
                )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Category context summary
# ---------------------------------------------------------------------------


def summarize_category_context(
    category_key: str | None,
    *,
    language: str = "zh-CN",
    max_tokens: int = 200,
) -> str:
    """Generate a compact category-context summary for prompt injection (~200 tokens).

    Returns empty string if category_key is None or not found.
    """
    from bestseller.services.novel_categories import get_novel_category

    if not category_key:
        return ""
    cat = get_novel_category(category_key)
    if cat is None:
        return ""

    is_en = language.startswith("en")
    lines: list[str] = []

    # Header
    cat_name = cat.name_en if is_en else cat.name
    lines.append(f"[Category: {cat_name}]" if is_en else f"【品类：{cat_name}】")

    # Reader promise (truncated)
    promise = (cat.reader_promise_en if is_en else cat.reader_promise_zh) or ""
    if promise:
        lines.append(promise[:120].rstrip("。.") + ("." if is_en else "。"))

    # Evolution summary (phase names only)
    if cat.challenge_evolution_pathway:
        phase_names = [
            (p.phase_name_en if is_en else p.phase_name_zh)
            for p in cat.challenge_evolution_pathway
        ]
        arrow = " → ".join(phase_names)
        lines.append(f"Evolution: {arrow}" if is_en else f"进化路径：{arrow}")

    # Top quality traps
    critical_traps = [t for t in cat.quality_traps if t.severity == "critical"][:2]
    if critical_traps:
        header = "AVOID:" if is_en else "避免："
        trap_descs = "; ".join(
            (t.description_en or t.description_zh)[:60] if is_en else t.description_zh[:60]
            for t in critical_traps
        )
        lines.append(f"{header} {trap_descs}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 3: Volume writing feedback collection (反哺)
# ---------------------------------------------------------------------------

async def collect_volume_writing_feedback(
    session: AsyncSession,
    project_id: UUID,
    volume_number: int,
) -> dict[str, Any]:
    """Gather structured feedback from volume N's actual writing results.

    All data sources are already populated by the existing pipeline:
    - CharacterStateSnapshotModel (feedback.py _apply_character_state_updates)
    - CanonFactModel arc_summary (linear_arc_summary.py)
    - CanonFactModel world_snapshot (linear_arc_summary.py)
    - RelationshipEventModel (feedback.py)
    - ProjectModel.metadata_json (consistency warnings, voice drift)
    """
    # Determine chapter range for this volume
    volume_row = (
        await session.execute(
            select(VolumeModel).where(
                VolumeModel.project_id == project_id,
                VolumeModel.volume_number == volume_number,
            )
        )
    ).scalar_one_or_none()

    if volume_row is None:
        return {}

    meta = _mapping(volume_row.metadata_json)
    arc_ranges = meta.get("arc_ranges") or []
    # Derive chapter range from arc_ranges or metadata
    chapter_start = None
    chapter_end = None
    if arc_ranges:
        flat = [ch for rng in arc_ranges if isinstance(rng, list) for ch in rng]
        if flat:
            chapter_start = min(flat)
            chapter_end = max(flat)
    if chapter_start is None:
        chapter_start = int(meta.get("chapter_start") or 1)
        chapter_end = int(meta.get("chapter_end") or chapter_start + int(volume_row.target_chapter_count or 10) - 1)

    # 1. Character state snapshots (latest per character in this volume)
    char_snapshots = (
        await session.execute(
            select(CharacterStateSnapshotModel, CharacterModel.name, CharacterModel.role)
            .join(CharacterModel, CharacterStateSnapshotModel.character_id == CharacterModel.id)
            .where(
                CharacterStateSnapshotModel.project_id == project_id,
                CharacterStateSnapshotModel.chapter_number >= chapter_start,
                CharacterStateSnapshotModel.chapter_number <= chapter_end,
            )
            .order_by(CharacterStateSnapshotModel.chapter_number.desc())
        )
    ).all()

    # Deduplicate: keep latest snapshot per character
    seen_chars: set[UUID] = set()
    cast_evolution: list[dict[str, Any]] = []
    for snap, char_name, char_role in char_snapshots:
        if snap.character_id in seen_chars:
            continue
        seen_chars.add(snap.character_id)
        cast_evolution.append({
            "name": char_name,
            "role": char_role,
            "arc_state": snap.arc_state,
            "emotional_state": snap.emotional_state,
            "physical_state": snap.physical_state,
            "power_tier": snap.power_tier,
            "chapter_number": snap.chapter_number,
        })

    # 2. Arc summaries (from CanonFactModel)
    arc_facts = (
        await session.execute(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project_id,
                CanonFactModel.fact_type == "arc_summary",
                CanonFactModel.is_current.is_(True),
                CanonFactModel.valid_from_chapter_no >= chapter_start,
            ).order_by(CanonFactModel.valid_from_chapter_no.desc())
            .limit(3)
        )
    ).scalars().all()

    unresolved_threads: list[str] = []
    next_arc_setup: str = ""
    protagonist_growth: str = ""
    open_clues: list[str] = []
    for af in arc_facts:
        val = _mapping(af.value_json)
        for t in (val.get("unresolved_threads") or []):
            if t and t not in unresolved_threads:
                unresolved_threads.append(str(t))
        if not next_arc_setup:
            next_arc_setup = _s(val.get("next_arc_setup"))
        if not protagonist_growth:
            protagonist_growth = _s(val.get("protagonist_growth"))
        for c in (val.get("open_clues") or []):
            if c and c not in open_clues:
                open_clues.append(str(c))

    # 3. World snapshot (latest)
    world_snapshot_fact = (
        await session.execute(
            select(CanonFactModel).where(
                CanonFactModel.project_id == project_id,
                CanonFactModel.fact_type == "world_snapshot",
                CanonFactModel.is_current.is_(True),
            ).order_by(CanonFactModel.valid_from_chapter_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    world_state: dict[str, Any] = {}
    if world_snapshot_fact is not None:
        world_state = _mapping(world_snapshot_fact.value_json)

    # 4. Relationship events (milestones in this volume)
    rel_events = (
        await session.execute(
            select(RelationshipEventModel).where(
                RelationshipEventModel.project_id == project_id,
                RelationshipEventModel.chapter_number >= chapter_start,
                RelationshipEventModel.chapter_number <= chapter_end,
                RelationshipEventModel.is_milestone.is_(True),
            ).order_by(RelationshipEventModel.chapter_number)
            .limit(10)
        )
    ).scalars().all()

    relationship_shifts = [
        {
            "characters": f"{evt.character_a_label} <-> {evt.character_b_label}",
            "change": evt.relationship_change,
            "chapter": evt.chapter_number,
        }
        for evt in rel_events
    ]

    # 5. Project-level signals
    project_row = (
        await session.execute(
            select(ProjectModel).where(ProjectModel.id == project_id)
        )
    ).scalar_one_or_none()

    writing_quality: dict[str, Any] = {}
    if project_row is not None:
        pmeta = _mapping(project_row.metadata_json)
        writing_quality = {
            "consistency_warnings": pmeta.get("_pending_consistency_warnings") or [],
            "voice_drift_characters": [
                c.get("character_name", "")
                for c in (pmeta.get("voice_corrections") or [])
                if isinstance(c, dict)
            ],
        }

    return {
        "volume_number": volume_number,
        "chapter_range": [chapter_start, chapter_end],
        "cast_evolution": cast_evolution,
        "protagonist_growth": protagonist_growth,
        "unresolved_threads": unresolved_threads[:10],
        "next_arc_setup": next_arc_setup,
        "open_clues": open_clues[:10],
        "world_state": world_state,
        "relationship_shifts": relationship_shifts,
        "writing_quality": writing_quality,
    }


def summarize_volume_feedback(
    feedback: dict[str, Any],
    *,
    language: str = "zh-CN",
) -> str:
    """~500 tokens of prose describing what happened in the previous volume."""
    fb = _mapping(feedback)
    is_en = language.startswith("en")
    parts: list[str] = []

    vol = fb.get("volume_number", "?")
    ch_range = fb.get("chapter_range") or []
    range_str = f"{ch_range[0]}-{ch_range[1]}" if len(ch_range) >= 2 else "?"

    if is_en:
        parts.append(f"=== Feedback from Volume {vol} (chapters {range_str}) ===")
    else:
        parts.append(f"=== 第{vol}卷反馈（第{range_str}章）===")

    # Protagonist growth
    pg = _s(fb.get("protagonist_growth"))
    if pg:
        parts.append(f"{'Protagonist growth' if is_en else '主角成长'}：{pg}")

    # Cast evolution
    for ce in (fb.get("cast_evolution") or [])[:6]:
        c = _mapping(ce)
        if is_en:
            parts.append(
                f"  {_s(c.get('name'))} ({_s(c.get('role'))}): "
                f"arc={_s(c.get('arc_state'))}, emotion={_s(c.get('emotional_state'))}, "
                f"power={_s(c.get('power_tier'))}"
            )
        else:
            parts.append(
                f"  {_s(c.get('name'))}（{_s(c.get('role'))}）："
                f"弧线={_s(c.get('arc_state'))}，情绪={_s(c.get('emotional_state'))}，"
                f"实力={_s(c.get('power_tier'))}"
            )

    # Unresolved threads
    threads = fb.get("unresolved_threads") or []
    if threads:
        label = "Unresolved threads" if is_en else "未解悬念"
        parts.append(f"{label}：")
        for t in threads[:8]:
            parts.append(f"  - {t}")

    # Open clues
    clues = fb.get("open_clues") or []
    if clues:
        label = "Open clues" if is_en else "未关闭线索"
        parts.append(f"{label}：")
        for c in clues[:5]:
            parts.append(f"  - {c}")

    # Next arc setup
    nas = _s(fb.get("next_arc_setup"))
    if nas:
        parts.append(f"{'Next arc setup' if is_en else '下一弧预设'}：{nas}")

    # Relationship shifts
    shifts = fb.get("relationship_shifts") or []
    if shifts:
        label = "Key relationship shifts" if is_en else "关键关系变化"
        parts.append(f"{label}：")
        for s in shifts[:5]:
            sd = _mapping(s)
            parts.append(f"  - ch{sd.get('chapter', '?')}: {_s(sd.get('characters'))} — {_s(sd.get('change'))}")

    # World state summary
    ws = _mapping(fb.get("world_state"))
    ws_summary = _s(ws.get("world_summary"))
    if ws_summary:
        parts.append(f"{'World state' if is_en else '世界状态'}：{ws_summary}")

    return "\n".join(parts)
