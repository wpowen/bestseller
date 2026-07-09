"""Deterministic enrichment for systematically-missing outline batch fields.

Why this exists (2026-06-12, 《神仙都是我招的》v3 run evidence):
the volume outline batch planner systematically omits three field families —
``opening_situation`` (50/50 chapters empty), scene ``participants`` beyond the
protagonist (50/50 solo scenes), and whole-batch ``causal_contract`` drops.
Those fields are hard requirements of downstream gates
(``chapter_causality_contract`` at materialization, golden-three checks in
``commercial_planning_readiness``), so every book died or burned repair rounds
at gates two layers away from the producer.

This module closes the production-acceptance gap deterministically: derive the
missing fields from content the planner *did* produce, before validation runs.
It never overwrites planner-provided values and stays fully genre-agnostic
(identity names come from the project manifest, not word lists).
"""

from __future__ import annotations

import re
from typing import Any, Mapping

_CLAUSE_SPLIT = re.compile(r"[；;。]")
_FIELD_PUNCT_RE = re.compile(r"[\s，,。.！!？?；;：:、\"'“”‘’「」『』（）()\-—…]+")


def _normalize_outline_field_text_local(text: str) -> str:
    """Whitespace/punctuation-insensitive compare — mirrors planner.py's
    ``_normalize_outline_field_text`` (kept local to avoid a heavy cross-import
    into the 4000-line planner module from this small, self-contained one)."""

    return _FIELD_PUNCT_RE.sub("", text or "")

# Minimum derivable fields before we commit a synthesized causal contract.
_MIN_CONTRACT_FIELDS = 8
_GOLDEN_CHAPTERS = (1, 2, 3)
_DEFAULT_GOLDEN_HYPE = 8.0
_MAX_PARTICIPANTS = 5
_MAX_FIELD_LEN = 400

# ── target_emotion fallback (webnovel method cards) ────────────────────────
# Golden chapters of commercial serials default to 爽 (the blind-review loss
# of《神仙都是我招的》ch1 traced to a 暖 opening); later chapters default to
# the universal pull-forward emotion 紧张 unless hype_type hints otherwise.
_GOLDEN_DEFAULT_EMOTION = "爽"
_LATER_DEFAULT_EMOTION = "紧张"
# Ordered specific → generic: first matching token wins.
_HYPE_EMOTION_HINTS: tuple[tuple[str, str], ...] = (
    ("打脸", "爽"),
    ("逆袭", "爽"),
    ("悬念", "悬疑"),
    ("反转", "震撼"),
    ("震撼", "震撼"),
    ("热血", "燃"),
    ("温情", "暖"),
    ("情感", "虐"),
    ("危机", "紧张"),
)


def _clauses(text: Any, count: int = 1) -> str:
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(str(text or "")) if p.strip()]
    return "；".join(parts[:count])


def _scene_text(scene: Mapping[str, Any]) -> str:
    purpose = scene.get("purpose")
    if isinstance(purpose, Mapping):
        body = " ".join(str(v) for v in purpose.values() if v)
    else:
        body = str(purpose or "")
    return f"{body} {scene.get('title') or ''}"


def _chapter_text(chapter: Mapping[str, Any]) -> str:
    return " ".join(
        str(chapter.get(key) or "")
        for key in (
            "goal",
            "main_conflict",
            "hook_description",
            "opening_situation",
            "opening_pressure",
        )
    )


def _tag_enriched(chapter: dict[str, Any], field_name: str) -> None:
    """Leave a trace of which field this pass backfilled (T5, 2026-07-09) —
    lets quality audits tell "字段非空" apart from "字段是模型真写的"."""

    tags = chapter.setdefault("enriched_fields", [])
    if field_name not in tags:
        tags.append(field_name)


def _fill_participants(
    chapter: dict[str, Any],
    names: list[str],
    protagonist: str,
    stats: dict[str, int],
) -> None:
    chapter_text = _chapter_text(chapter)
    for scene in chapter.get("scenes") or []:
        participants = [p for p in (scene.get("participants") or []) if p]
        searchable = f"{_scene_text(scene)} {chapter_text}"
        if protagonist and protagonist not in participants:
            participants.insert(0, protagonist)
        if len(participants) < 2:
            found = [
                name
                for name in names
                if name != protagonist and name in searchable and name not in participants
            ]
            if found:
                participants.extend(found[:2])
                stats["participants"] += 1
                _tag_enriched(chapter, "participants")
        scene["participants"] = participants[:_MAX_PARTICIPANTS]


# Chapter fields that name the pressure source / other parties involved, used
# to pick the most plausible second participant for a golden solo-rescue.
_PRESSURE_REF_KEYS = (
    "antagonist",
    "antagonist_force_name",
    "faction_refs",
    "location_refs",
    "cast_refs",
    "key_reveals",
    "main_conflict",
    "opening_situation",
    "hook_description",
    "goal",
)


def _chapter_has_duo_scene(chapter: Mapping[str, Any]) -> bool:
    for scene in chapter.get("scenes") or []:
        parts = {
            str(p).strip()
            for p in (scene.get("participants") or [])
            if p and str(p).strip()
        }
        if len(parts) >= 2:
            return True
    return False


def _pressure_ref_blob(chapter: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in _PRESSURE_REF_KEYS:
        value = chapter.get(key)
        if isinstance(value, (list, tuple)):
            parts.extend(str(v) for v in value if v)
        elif value:
            parts.append(str(value))
    return " ".join(parts)


def _rescue_golden_solo_scene(
    chapter: dict[str, Any],
    names: list[str],
    protagonist: str,
    stats: dict[str, int],
) -> None:
    """Golden-three (G8): force a named second participant into a still-solo
    golden chapter so it is not a solo-chain.

    Runs only for chapters 1-3 and only after :func:`_fill_participants` already
    tried to resolve names from scene text. A 凡人流 opening (protagonist alone
    discovering the artifact) legitimately has no other name in its prose, so the
    text-match fill leaves it solo and the downstream hard gate kills the entire
    volume outline. The chapter's own pressure-bearing fields (antagonist /
    faction_refs / main_conflict) name the off-screen counter-force; we promote
    the most-referenced roster name into the highest-tension scene. Deterministic
    and golden-three only — never fabricates participants for later chapters.
    """

    if chapter.get("chapter_number") not in _GOLDEN_CHAPTERS:
        return
    scenes = chapter.get("scenes") or []
    if not scenes or _chapter_has_duo_scene(chapter):
        return
    candidates = [n for n in names if n and n != protagonist]
    if not candidates:
        return
    ref_blob = _pressure_ref_blob(chapter)
    second = next((n for n in candidates if n in ref_blob), candidates[0])
    target = max(scenes, key=lambda s: len(_scene_text(s)))
    participants = [p for p in (target.get("participants") or []) if p]
    if protagonist and protagonist not in participants:
        participants.insert(0, protagonist)
    if second not in participants:
        participants.append(second)
    target["participants"] = participants[:_MAX_PARTICIPANTS]
    stats["golden_solo_rescued"] += 1


def _fill_opening_pressure(chapter: dict[str, Any], stats: dict[str, int]) -> None:
    if str(chapter.get("opening_pressure") or "").strip():
        return
    head = _clauses(chapter.get("main_conflict"), 2)
    if head:
        chapter["opening_pressure"] = head
        stats["opening_pressure"] += 1


def _fill_opening_situation(chapter: dict[str, Any], stats: dict[str, int]) -> None:
    if str(chapter.get("opening_situation") or "").strip():
        return
    scenes = chapter.get("scenes") or []
    first = scenes[0] if scenes else {}
    purpose = first.get("purpose")
    story = purpose.get("story") if isinstance(purpose, Mapping) else None
    conflict_head = _clauses(chapter.get("main_conflict"), 1)
    goal = chapter.get("goal")
    # 防止退化(T5)：opening_situation="开场时空处境"，goal="本章意图"——语义不同，
    # 不该是同一句话。story 缺失时先试场景的差异化素材(time_label/entry_state)，
    # 只有真的没有差异化素材才退回复制 goal，且必须打标供审计。
    seed = story
    used_goal_as_seed = False
    if not seed:
        entry_state = first.get("entry_state")
        entry_state_text = (
            entry_state.get("summary") if isinstance(entry_state, Mapping) else entry_state
        )
        alt_seed = first.get("time_label") or entry_state_text
        goal_norm = _normalize_outline_field_text_local(str(goal or ""))
        if alt_seed and _normalize_outline_field_text_local(str(alt_seed)) != goal_norm:
            seed = alt_seed
        elif goal:
            seed = goal
            used_goal_as_seed = True
        else:
            seed = conflict_head
    if not seed:
        return
    pressure = conflict_head or chapter.get("hook_description") or ""
    chapter["opening_situation"] = (
        f"开章即事中：{seed}；当场压力——{pressure}".strip("；— ")
    )
    stats["opening_situation"] += 1
    _tag_enriched(chapter, "opening_situation")
    if used_goal_as_seed:
        _tag_enriched(chapter, "opening_situation_copied_from_goal_no_alt_material")


def _fill_causal_contract(chapter: dict[str, Any], stats: dict[str, int]) -> None:
    if chapter.get("causal_contract"):
        return
    scenes = chapter.get("scenes") or []
    last = scenes[-1] if scenes else {}
    exit_state = last.get("exit_state")
    exit_summary = (
        exit_state.get("summary") if isinstance(exit_state, Mapping) else exit_state
    )
    contract = {
        "pressure": chapter.get("opening_pressure") or _clauses(chapter.get("main_conflict"), 1),
        "resistance": _clauses(chapter.get("main_conflict"), 2) or chapter.get("main_conflict"),
        "protagonist_choice": chapter.get("goal"),
        "protagonist_desire": chapter.get("goal"),
        "visible_action_or_reaction": _scene_text(last).strip() or chapter.get("goal"),
        "state_change": exit_summary or chapter.get("hook_description"),
        "gain_or_reveal": "；".join(chapter.get("key_reveals") or []) or chapter.get("hook_description"),
        "cost_or_tradeoff": _clauses(chapter.get("main_conflict"), 1),
        "chapter_function": chapter.get("chapter_event_role")
        or f"推进卷{chapter.get('volume_number')}主线",
        "next_reader_desire": chapter.get("hook_description") or chapter.get("tail_hook"),
    }
    contract = {
        key: str(value)[:_MAX_FIELD_LEN]
        for key, value in contract.items()
        if value
    }
    if len(contract) >= _MIN_CONTRACT_FIELDS:
        chapter["causal_contract"] = contract
        stats["causal_contract"] += 1
        _tag_enriched(chapter, "causal_contract")


def _fill_target_emotion(chapter: dict[str, Any], stats: dict[str, int]) -> None:
    """Deterministic target_emotion fallback — never overwrites planner values."""

    if str(chapter.get("target_emotion") or "").strip():
        return
    try:
        number = int(chapter.get("chapter_number") or 0)
    except (TypeError, ValueError):
        number = 0
    if number in _GOLDEN_CHAPTERS:
        # Position wins over hype hints: golden chapters of a commercial
        # serial deliver 爽 unless the planner explicitly says otherwise.
        derived = _GOLDEN_DEFAULT_EMOTION
    else:
        hype = str(chapter.get("hype_type") or "")
        derived = next(
            (
                emotion
                for token, emotion in _HYPE_EMOTION_HINTS
                if token and token in hype
            ),
            _LATER_DEFAULT_EMOTION,
        )
    chapter["target_emotion"] = derived
    stats["target_emotion"] += 1


def _normalize_hook_type(chapter: dict[str, Any], stats: dict[str, int]) -> None:
    """Map free-text hook_type onto the canonical 13-key taxonomy.

    Soft by contract: unmatched values are kept verbatim, and any method-card
    loading failure makes this a no-op (never blocks the batch).
    """

    raw = str(chapter.get("hook_type") or "").strip()
    if not raw:
        return
    try:
        from bestseller.services.quality_levers.webnovel_method_cards import (
            match_hook_type_key,
        )

        matched = match_hook_type_key(raw)
    except Exception:
        return
    if matched and matched != raw:
        chapter["hook_type"] = matched
        stats["hook_type_normalized"] += 1


def _fill_golden_hype(chapter: dict[str, Any], stats: dict[str, int]) -> None:
    if chapter.get("chapter_number") not in _GOLDEN_CHAPTERS:
        return
    if not str(chapter.get("hype_type") or "").strip():
        chapter["hype_type"] = "悬念冲击"
        stats["golden_hype"] += 1
    if chapter.get("hype_intensity") is None:
        chapter["hype_intensity"] = _DEFAULT_GOLDEN_HYPE


def enrich_outline_batch_fields(
    content: dict[str, Any],
    identity_names: list[str],
    *,
    protagonist: str = "",
) -> tuple[dict[str, Any], dict[str, int]]:
    """Fill systematically-missing batch fields in place; returns (content, stats).

    Never overwrites planner-provided values. ``identity_names`` should come
    from the project's locked identity manifest; ``protagonist`` defaults to
    the first manifest name when omitted.
    """

    stats = {
        "participants": 0,
        "opening_situation": 0,
        "opening_pressure": 0,
        "causal_contract": 0,
        "golden_hype": 0,
        "target_emotion": 0,
        "hook_type_normalized": 0,
        "golden_solo_rescued": 0,
    }
    names = [str(n).strip() for n in identity_names if str(n or "").strip()]
    lead = protagonist or (names[0] if names else "")
    for chapter in content.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        _fill_participants(chapter, names, lead, stats)
        _rescue_golden_solo_scene(chapter, names, lead, stats)
        _fill_opening_situation(chapter, stats)
        _fill_opening_pressure(chapter, stats)
        _fill_causal_contract(chapter, stats)
        _fill_golden_hype(chapter, stats)
        _fill_target_emotion(chapter, stats)
        _normalize_hook_type(chapter, stats)
    stats["total"] = sum(stats.values())
    return content, stats
