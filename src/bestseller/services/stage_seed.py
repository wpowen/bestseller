"""Stage 0 — seed metadata for Chapter / SceneCard / Character rows.

When a chapter or scene is first created (in `if_generation._bootstrap_db_structure`),
the pipeline only has a lightweight IF-card describing the chapter. Stage A-D block
builders read structured fields from `metadata_json` (conflict_tuple, scene_purpose_id,
env_7d, location_id, hook_type, tension_score, inner_structure). Those readers return
empty until the planner writes the fields.

This module closes that gap with **heuristic seeders** that extract the best-effort
values from what the card already contains, so Stage A-D blocks start accumulating
history from chapter 1 of the next generation run.

Design principles:
  * **Additive only**: callers merge the seed dict into their existing metadata_json,
    never overwriting non-empty upstream fields.
  * **Graceful None**: if a field can't be derived, it's left absent rather than
    faked — readers already fall back cleanly.
  * **Zero LLM calls**: keyword heuristics only. A later post-generation refinement
    pass (Stage 0-B) can replace these with classified values derived from chapter text.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from bestseller.services.pacing_engine import (
    CLIFFHANGER_TYPES,
    target_beat_for_chapter,
    target_tension_for_chapter,
)


# ---------------------------------------------------------------------------
# Scene-purpose classifier (24 types → family)
# ---------------------------------------------------------------------------

# Keyword-to-purpose map. Keywords are matched case-insensitively over the card's
# chapter_goal + main_conflict + title text. First match wins.
_PURPOSE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # B action — most specific, match first
    ("pursuit",           ("追击", "追捕", "追杀", "逃", "pursuit", "chase")),
    ("infiltration",      ("潜入", "混入", "刺探", "infiltrate", "undercover")),
    ("heist",             ("劫夺", "盗取", "抢", "heist")),
    ("battle",            ("战斗", "厮杀", "交手", "battle", "fight")),
    ("rescue",            ("营救", "救", "rescue", "解救")),
    ("chase_with_talk",   ("边走边说", "押送", "同行")),
    # C relation
    ("revelation",        ("揭示", "真相", "曝光", "reveal", "披露")),
    ("confrontation",     ("对峙", "硬碰硬", "摊牌", "confront")),
    ("negotiation",       ("谈判", "交换", "议价", "negotiate", "交易")),
    ("bonding",           ("结伴", "亲近", "bond", "和解接近")),
    ("betrayal",          ("背叛", "倒戈", "反水", "betray")),
    ("reconciliation",    ("和解", "修复", "重归", "reconcile")),
    ("alliance",          ("结盟", "盟誓", "契约", "alliance")),
    # D interior
    ("reflection",        ("反思", "独白", "复盘", "reflect")),
    ("dilemma",           ("两难", "抉择", "dilemma", "艰难选择")),
    ("worldbuilding",     ("世界观", "规则", "历史", "典章", "lore")),
    ("relief",            ("喘息", "轻松", "插科", "relief", "comic")),
    ("foreshadow",        ("伏笔", "预兆", "foreshadow", "种子")),
    # A structural — meta labels usually assigned by beat engine, match last
    ("inciting",          ("触发", "事件起点", "inciting", "kickoff", "启动")),
    ("first_threshold",   ("跨越", "门槛", "立誓", "threshold", "入场")),
    ("midpoint_reversal", ("中点", "false victory", "镜像")),
    ("crisis",            ("危机", "绝境", "crisis", "最坏")),
    ("climax",            ("高潮", "决战", "终局", "climax", "对决")),
    ("resolution",        ("收束", "收尾", "尘埃落定", "resolution")),
]


def _classify_scene_purpose(text: str) -> str | None:
    """Return the first matching scene-purpose id, or None."""
    if not text:
        return None
    lowered = text.lower()
    for purpose_id, keywords in _PURPOSE_KEYWORDS:
        for kw in keywords:
            if kw.lower() in lowered:
                return purpose_id
    return None


# ---------------------------------------------------------------------------
# Conflict-tuple classifier (4-axis)
# ---------------------------------------------------------------------------

_OBJECT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("self",               ("自己", "内心", "自我", "心魔", "self")),
    ("supernatural_fate",  ("命运", "天道", "预言", "宿命", "神明", "fate")),
    ("technology",         ("系统", "算法", "机关", "器物", "tech")),
    ("nature",             ("风暴", "雪山", "灾", "自然", "生存", "nature")),
    ("society",            ("体制", "规矩", "阶级", "律令", "朝廷", "society", "system")),
    ("group",              ("派系", "门派", "家族", "团伙", "group", "faction")),
    ("person",             ("他", "她", "对手", "敌人", "person", "个人")),
]

_LAYER_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("inner_identity",    ("身份", "我是谁", "identity")),
    ("inner_desire",      ("欲望", "想要但", "desire", "矛盾")),
    ("personal_relation", ("亲密", "信任", "背叛", "友情", "母女", "父子")),
    ("communal",          ("派系", "同袍", "荣誉", "归属", "community")),
    ("institutional",     ("律", "礼", "规", "institutional", "制度")),
    ("cosmic",            ("天道", "宇宙", "熵", "cosmic")),
]

_NATURE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("temporal_irreversible",  ("倒计时", "来不及", "时限", "deadline", "countdown")),
    ("information_asymmetry",  ("秘密", "不知道", "情报", "信息差", "隐瞒")),
    ("moral_dilemma",          ("两难", "必须选", "dilemma")),
    ("value_clash",            ("价值", "信念", "道", "value", "理念")),
    ("resource_scarcity",      ("资源", "稀缺", "不足", "scarcity")),
    ("cooperative_game",       ("合作", "同盟", "共赢", "coop")),
    ("antagonistic",           ("零和", "你死我活", "对抗", "antagonistic")),
]


def _classify_first(text: str, table: list[tuple[str, tuple[str, ...]]]) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    for key, keywords in table:
        for kw in keywords:
            if kw.lower() in lowered:
                return key
    return None


def _classify_conflict_tuple(text: str) -> dict[str, str] | None:
    """Derive a {object, layer, nature, resolvability} signature from text.

    Returns None if both object and layer fail to classify — there's no
    useful partial conflict tuple.
    """
    obj = _classify_first(text, _OBJECT_KEYWORDS)
    layer = _classify_first(text, _LAYER_KEYWORDS)
    if obj is None and layer is None:
        return None
    nature = _classify_first(text, _NATURE_KEYWORDS) or "antagonistic"
    return {
        "object": obj or "person",
        "layer": layer or "personal_relation",
        "nature": nature,
        "resolvability": "dynamic_equilibrium",
    }


# ---------------------------------------------------------------------------
# Hook-type classifier (7 canonical types)
# ---------------------------------------------------------------------------

_HOOK_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("crisis",     ("危机", "绝境", "crisis", "critical")),
    ("twist",      ("翻转", "反转", "twist", "逆转")),
    ("revelation", ("揭露", "真相", "发现", "reveal", "exposed")),
    ("decision",   ("选择", "抉择", "decision", "决定")),
    ("threat",     ("威胁", "逼近", "threat", "menace")),
    ("mystery",    ("谜", "悬念", "mystery", "疑问")),
    ("suspense",   ("悬", "悬念", "suspense", "未解")),
]


def _classify_hook_type(text: str) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    for hook_type, keywords in _HOOK_KEYWORDS:
        for kw in keywords:
            if kw.lower() in lowered:
                return hook_type
    return None


# ---------------------------------------------------------------------------
# Location-id stable slug
# ---------------------------------------------------------------------------

_LOCATION_HINTS: tuple[str, ...] = (
    "下水道", "管道", "塔", "废墟", "街巷", "宫", "殿", "房", "山", "洞",
    "桥", "院", "书房", "码头", "港", "林", "崖",
    "palace", "tower", "ruin", "alley", "cave", "bridge", "court",
)


def _extract_location_id(text: str) -> str | None:
    """Pick the first recognised place-noun from the text, or None."""
    if not text:
        return None
    for hint in _LOCATION_HINTS:
        if hint in text:
            return _slugify(hint)
    return None


def _slugify(value: str) -> str:
    """Stable, filesystem-safe id for Chinese/English strings."""
    cleaned = re.sub(r"\s+", "_", value.strip())
    if cleaned.isascii():
        return cleaned.lower()
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:8]
    return f"loc_{digest}"


# ---------------------------------------------------------------------------
# Public seeders
# ---------------------------------------------------------------------------

def seed_scene_metadata(card: dict[str, Any]) -> dict[str, Any]:
    """Best-effort Stage 0 metadata for a new SceneCardModel.

    Reads card-level free-text fields (chapter_goal / main_conflict / title)
    and emits structured keys that Stage A/B/D readers consume. Keys are
    **omitted** when classification fails so graceful-degradation paths still
    trigger.
    """
    title = str(card.get("title") or "")
    goal = str(card.get("chapter_goal") or "")
    conflict_text = str(card.get("main_conflict") or "")
    merged = " ".join(filter(None, (title, goal, conflict_text)))

    seed: dict[str, Any] = {}

    purpose_id = _classify_scene_purpose(merged)
    if purpose_id:
        seed["scene_purpose_id"] = purpose_id

    conflict_tuple = _classify_conflict_tuple(merged)
    if conflict_tuple:
        seed["conflict_tuple"] = conflict_tuple

    location_id = _extract_location_id(merged)
    if location_id:
        seed["location_id"] = location_id

    return seed


def seed_chapter_metadata(
    card: dict[str, Any],
    chapter_number: int,
    total_chapters: int,
) -> dict[str, Any]:
    """Best-effort Stage 0 metadata for a new ChapterModel.

    Hook type is classified from `next_chapter_hook` text. Tension score is
    seeded from the target-curve value (the writer can overwrite with
    observed tension later via a post-generation refinement pass).
    """
    seed: dict[str, Any] = {}

    hook_text = str(card.get("next_chapter_hook") or card.get("hook") or "")
    hook_type = _classify_hook_type(hook_text)
    if hook_type and hook_type in CLIFFHANGER_TYPES:
        seed["hook_type"] = hook_type

    if total_chapters > 0:
        try:
            seed["tension_score"] = float(
                target_tension_for_chapter(chapter_number, total_chapters)
            )
            beat = target_beat_for_chapter(chapter_number, total_chapters)
            seed["beat_id"] = beat.beat_name
        except Exception:
            pass

    return seed


def seed_character_inner_structure(
    character_input: Any,
    lie_truth_arc: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Derive a 10-field `inner_structure` dict from a character bible entry.

    Returns None if no meaningful fields can be extracted. Caller should merge
    the result under the `inner_structure` key of CharacterModel.metadata_json.
    """
    lta = lie_truth_arc or {}
    getter = (lambda k: getattr(character_input, k, None)) if not isinstance(character_input, dict) else (lambda k: character_input.get(k))

    lie = lta.get("core_lie") or getter("lie") or getter("falsely_believes")
    truth = lta.get("core_truth") or getter("truth")
    want = getter("goal") or getter("want")
    need = getter("need") or getter("arc_state")
    ghost = getter("secret") or getter("ghost") or getter("backstory_trauma")
    fear = getter("fear")
    flaw = getter("flaw")
    arc_trajectory = (getter("arc_trajectory") or "").lower() if getter("arc_trajectory") else ""

    # Map arc_trajectory label to one of the 6 arc types.
    if any(kw in arc_trajectory for kw in ("negative", "tragic", "fall", "堕落", "负面")):
        arc_type = "FALL"
    elif any(kw in arc_trajectory for kw in ("corrupt", "腐化")):
        arc_type = "CORRUPTION"
    elif any(kw in arc_trajectory for kw in ("disillusion", "幻灭")):
        arc_type = "DISILLUSIONMENT"
    elif any(kw in arc_trajectory for kw in ("flat_negative", "守旧")):
        arc_type = "FLAT_NEGATIVE"
    elif any(kw in arc_trajectory for kw in ("flat", "考验", "守护")):
        arc_type = "FLAT"
    else:
        arc_type = "POSITIVE_CHANGE"

    structure = {
        "lie_believed": _as_str(lie),
        "truth_to_learn": _as_str(truth),
        "want_external": _as_str(want),
        "need_internal": _as_str(need),
        "ghost": _as_str(ghost),
        "fear_core": _as_str(fear),
        "fatal_flaw": _as_str(flaw),
        "arc_type": arc_type,
        "defense_mechanisms": [],
        "desire_shadow": None,
    }
    # If all three load-bearing fields are empty, skip emission entirely.
    if not any((structure["lie_believed"], structure["want_external"], structure["need_internal"])):
        return None
    return structure


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (list, tuple)) and value:
        head = value[0]
        return str(head).strip() or None if head else None
    return str(value).strip() or None
