"""Derive missing structural outline fields from existing planner content.

Scene participation, target emotion and hype are authored decisions. Missing
values stay missing for source-bound model repair; roster membership, text
mentions and chapter position are never evidence for those decisions.
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
_MAX_FIELD_LEN = 400


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


def _tag_enriched(chapter: dict[str, Any], field_name: str) -> None:
    """Leave a trace of which field this pass backfilled (T5, 2026-07-09) —
    lets quality audits tell "字段非空" apart from "字段是模型真写的"."""

    tags = chapter.setdefault("enriched_fields", [])
    if field_name not in tags:
        tags.append(field_name)


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


def enrich_outline_batch_fields(
    content: dict[str, Any],
    identity_names: list[str],
    *,
    protagonist: str = "",
) -> tuple[dict[str, Any], dict[str, int]]:
    """Fill systematically-missing batch fields in place; returns (content, stats).

    Roster arguments remain accepted for caller compatibility; they do not
    establish scene presence. Explicit participants, emotion and hype are preserved.
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
    for chapter in content.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        _fill_opening_situation(chapter, stats)
        _fill_opening_pressure(chapter, stats)
        _fill_causal_contract(chapter, stats)
        _normalize_hook_type(chapter, stats)
    stats["total"] = sum(stats.values())
    return content, stats
