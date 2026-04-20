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

# Order matters: more specific / stronger-signal keywords are checked first so
# "真相揭露，生死危机" maps to revelation (the decisive reveal) rather than
# being shadowed by weaker substring matches. Keys must match CLIFFHANGER_TYPES
# (pacing_engine): suspense / twist / crisis / revelation / sudden / emotional /
# philosophical.
_HOOK_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "revelation",
        (
            "揭露", "揭示", "揭开", "真相", "原来是", "竟是",
            "曝光", "暴露", "身份暴露", "露馅", "真面目", "发现",
            "reveal", "exposed", "revelation", "unmasked",
        ),
    ),
    (
        "twist",
        (
            "翻转", "反转", "逆转", "出乎意料", "没想到", "却不想",
            "峰回路转", "意料之外", "意外", "翻盘",
            "twist", "reversal", "plot twist",
        ),
    ),
    # crisis covers both the 危机 / 绝境 family AND the "imminent specific
    # threat" subcategory (威胁 / 逼近 / 杀机), per CLIFFHANGER_TYPES semantics
    # ("迫在眉睫的具体威胁").
    (
        "crisis",
        (
            "危机", "绝境", "险境", "濒死", "重伤", "生死", "命悬",
            "致命", "九死一生", "危在旦夕", "毁灭", "性命",
            "威胁", "逼近", "盯上", "锁定", "追杀", "杀机", "兵临",
            "锁死", "步步紧逼", "临门一脚",
            "crisis", "critical", "dire", "peril", "threat", "menace",
            "looming", "closing in",
        ),
    ),
    (
        "sudden",
        (
            "突发", "骤然", "忽然", "陡然", "猝然", "突然", "蓦地",
            "霎时", "轰的一声", "砰然", "炸裂",
            "sudden", "suddenly", "abruptly", "out of nowhere",
        ),
    ),
    # emotional covers internal-emotional hits AND the "must-choose" decision
    # pivot (抉择 / 咬牙 / 决意) — both are "内心重击" in taxonomy.
    (
        "emotional",
        (
            "泪下", "泪流", "崩溃", "心碎", "绝望", "决绝", "哽咽",
            "颤抖", "泣不成声", "黯然", "苦笑", "孤独",
            "选择", "抉择", "决定", "决意", "下定决心", "横心",
            "咬牙", "必须选", "取舍", "两条路",
            "emotional", "heartbreak", "tears", "decision", "decide",
            "choice", "choose",
        ),
    ),
    (
        "philosophical",
        (
            "悟道", "领悟", "深思", "沉思", "人心", "意义", "道理",
            "天理", "何为", "究竟", "苍生", "大道",
            "philosophical", "meaning", "what is",
        ),
    ),
    # suspense — unresolved big question; also absorbs 诡异/谜团 "mystery" signals.
    (
        "suspense",
        (
            "悬念", "悬而未决", "未解", "留白", "不得而知", "等待揭晓",
            "余音", "欲说还休", "缓缓合上", "戛然",
            "谜", "诡异", "不对劲", "蹊跷", "疑云", "疑窦", "怪异",
            "无从知晓", "古怪",
            "suspense", "cliffhanger", "to be continued",
            "mystery", "inexplicable", "bizarre",
        ),
    ),
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
# Environment 7-D classifier
# ---------------------------------------------------------------------------
# Keyword maps for each of the 7 ENV_DIMENSIONS (see services/scene_taxonomy.py).
# Values must match ENV_VALUES exactly so downstream EnvVector.from_dict accepts
# them. Classification is conservative: if no keyword hits, the dimension is
# omitted rather than guessed (prevents polluting env_history with low-quality
# signals that would cause spurious "≥3 dim must differ" alerts).

_ENV_PHYSICAL_SPACE: list[tuple[str, tuple[str, ...]]] = [
    ("underground", ("地下", "地穴", "地窖", "矿道", "地宫", "地底", "墓穴", "dungeon", "underground", "tunnel")),
    ("water", ("海中", "江面", "河上", "湖中", "溪", "水下", "海底", "船舱", "舟上", "河道", "ocean", "sea", "river", "underwater")),
    ("rooftop_exposed", ("屋顶", "楼顶", "塔顶", "观星台", "鹤立", "檐上", "飞檐", "rooftop", "balcony")),
    ("vehicle_interior", ("马车内", "轿中", "车厢", "飞舟内", "船舱内", "机甲内", "carriage", "vehicle")),
    ("wilderness", ("荒野", "森林", "山林", "丛林", "原野", "草原", "山中", "林间", "旷野", "山野", "wilderness", "forest", "wild")),
    ("street_alley", ("街道", "街巷", "巷中", "胡同", "坊市", "市集", "小道", "alley", "street")),
    ("indoor_open", ("大殿", "正殿", "大厅", "礼堂", "正厅", "殿内", "厅堂", "广场内", "hall", "atrium")),
    ("indoor_enclosed", ("房内", "室内", "密室", "牢房", "禅房", "书房", "厢房", "寝", "屋内", "chamber", "room", "cell")),
    ("liminal_space", ("门槛", "入口", "通道", "门廊", "悬崖边", "界碑", "阶梯", "回廊", "threshold", "gateway", "corridor")),
]

_ENV_TIME_OF_DAY: list[tuple[str, tuple[str, ...]]] = [
    ("pre_dawn_dark", ("黎明前", "破晓前", "天未亮", "五更天", "pre-dawn", "predawn")),
    ("small_hours", ("凌晨", "四更", "五更", "夤夜", "寅时", "卯时", "small hours")),
    ("deep_night", ("深夜", "三更", "子时", "午夜", "夜深人静", "midnight", "dead of night")),
    ("night", ("夜晚", "入夜", "夜里", "当夜", "黑夜", "夜色", "戌时", "亥时", "night", "evening")),
    ("dusk", ("黄昏", "傍晚", "日落", "暮色", "夕阳", "酉时", "dusk", "sunset", "twilight")),
    ("afternoon", ("午后", "下午", "申时", "未时", "afternoon")),
    ("noon", ("正午", "午时", "日中", "日正", "当午", "noon", "midday")),
    ("morning", ("清晨", "早上", "破晓", "天亮", "日出", "辰时", "巳时", "morning", "sunrise", "dawn")),
]

_ENV_WEATHER_LIGHT: list[tuple[str, tuple[str, ...]]] = [
    ("storm", ("风暴", "暴风雨", "雷暴", "惊雷", "狂风", "飓风", "暴风", "storm", "tempest", "thunderstorm")),
    ("snow", ("飞雪", "大雪", "雪花", "落雪", "银装", "雪地", "snow", "snowing", "blizzard")),
    ("rain", ("下雨", "暴雨", "细雨", "大雨", "骤雨", "连绵", "雨声", "rain", "drizzle", "downpour")),
    ("fog", ("浓雾", "迷雾", "雾霭", "云雾", "雾气", "薄雾", "fog", "mist", "foggy")),
    ("blazing_sun", ("烈日", "艳阳", "灼日", "日头", "烈阳", "毒日", "blazing sun", "scorching")),
    ("overcast", ("阴霾", "阴沉", "乌云", "阴云", "阴天", "遮天", "overcast", "cloudy", "gloom")),
    ("artificial_light", ("灯火", "烛火", "火把", "油灯", "灯笼", "昏黄灯", "火光", "torch", "lantern", "lamp", "candle")),
    ("total_dark", ("漆黑", "一片黑暗", "伸手不见五指", "纯黑", "黢黑", "pitch black", "total darkness")),
]

_ENV_DOMINANT_SENSE: list[tuple[str, tuple[str, ...]]] = [
    ("proprioception", ("失衡", "眩晕", "跌落", "悬空", "飘浮", "天旋地转", "头晕目眩", "vertigo", "falling", "weightless")),
    ("taste", ("尝到", "舌尖", "嘴里", "血腥味", "苦涩", "回甘", "入喉", "taste", "bitter", "sweet", "flavor")),
    ("smell", ("气味", "香气", "血腥", "腥味", "臭味", "焦味", "清香", "幽香", "嗅到", "scent", "smell", "aroma", "stench")),
    ("touch_temperature", ("刺骨", "冰凉", "灼烫", "炙热", "寒气", "暖意", "冷汗", "肌肤", "触感", "temperature", "cold", "hot", "icy")),
    ("sound", ("声音", "响起", "轰鸣", "鼓声", "回荡", "嘶吼", "呼啸", "寂静", "脚步声", "sound", "echo", "roar")),
    ("sight", ("目光", "凝视", "望去", "视线", "眼前", "映入眼帘", "看见", "眼中", "sight", "gaze", "glimpse")),
]

_ENV_SOCIAL_DENSITY: list[tuple[str, tuple[str, ...]]] = [
    ("anonymous_crowd", ("人群", "众人", "万人", "千人", "人山人海", "摩肩接踵", "熙攘", "crowd", "mob", "throng")),
    ("virtual_presence", ("神识", "传音入密", "心灵感应", "灵波", "梦境", "投影", "神念", "astral", "telepathy")),
    ("small_group", ("小队", "一行", "几人", "一群", "三五人", "数人", "同伴", "group", "party", "squad")),
    ("triad", ("三人", "三位", "三方", "三者", "triad", "trio")),
    ("dyad", ("两人", "二人", "两个人", "对坐", "对立", "面对面", "dyad", "pair", "couple")),
    ("alone", ("独自", "一人", "孤身", "独处", "独坐", "孤独", "一个人", "alone", "solo", "solitary")),
]

_ENV_TEMPO_SCALE: list[tuple[str, tuple[str, ...]]] = [
    ("nested_flashback", ("回忆起", "忆起", "往事", "想起多年前", "幼时", "当年", "flashback", "remembered")),
    ("montage", ("几日后", "数日", "多日", "转眼", "光阴荏苒", "岁月", "数月", "时光流转", "montage")),
    ("slow_motion", ("时间凝滞", "仿佛变慢", "慢动作", "时间仿佛", "一切都慢", "slow motion", "time slowed")),
    ("accelerated", ("一瞬", "刹那", "转瞬", "电光火石", "说时迟", "闪电般", "instant", "split second", "blink")),
    ("realtime", ("与此同时", "正当此时", "此刻", "real time", "at this moment")),
]

_ENV_VERTICAL_ENCLOSURE: list[tuple[str, tuple[str, ...]]] = [
    ("airborne", ("御剑", "飞行", "腾空", "悬浮", "空中", "凌空", "翱翔", "airborne", "flying", "in the air")),
    ("elevated_open", ("高处", "塔顶", "山巅", "悬崖", "峰顶", "屋顶", "楼台", "瞭望", "elevated", "peak", "summit")),
    ("deep_underground_sealed", ("地下深处", "密封", "封印之地", "密闭地宫", "最底层", "sealed", "deep underground")),
    ("ground_half_sealed", ("大殿", "厅内", "室内", "房中", "密室", "帐内", "殿中", "half-sealed", "indoor")),
    ("ground_open", ("地面", "广场", "空地", "开阔", "露天", "平地", "旷野", "open ground", "outdoor")),
]


def _classify_env_dim(text: str, table: list[tuple[str, tuple[str, ...]]]) -> str | None:
    """Scan text in table order; first matching keyword wins."""
    if not text:
        return None
    lowered = text.lower()
    for value, keywords in table:
        for kw in keywords:
            if kw.lower() in lowered:
                return value
    return None


def _classify_env_7d(text: str) -> dict[str, str] | None:
    """Derive an env_7d signature from scene text.

    Returns a dict with **only** dimensions that classified. Returns None if
    fewer than 2 dimensions hit — one-dimensional signatures aren't useful for
    diversity rule evaluation (min_diff_vs_prev=3 would almost always pass).
    """
    if not text:
        return None
    env: dict[str, str] = {}
    mapping = (
        ("physical_space", _ENV_PHYSICAL_SPACE),
        ("time_of_day", _ENV_TIME_OF_DAY),
        ("weather_light", _ENV_WEATHER_LIGHT),
        ("dominant_sense", _ENV_DOMINANT_SENSE),
        ("social_density", _ENV_SOCIAL_DENSITY),
        ("tempo_scale", _ENV_TEMPO_SCALE),
        ("vertical_enclosure", _ENV_VERTICAL_ENCLOSURE),
    )
    for dim, table in mapping:
        value = _classify_env_dim(text, table)
        if value is not None:
            env[dim] = value
    if len(env) < 2:
        return None
    return env


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

    env_7d = _classify_env_7d(merged)
    if env_7d:
        seed["env_7d"] = env_7d

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
