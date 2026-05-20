from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# ruff: noqa: ANN401, RUF001
import re
from typing import Any

from bestseller.services.compliance_boundary_kernel import scan_compliance_texts

PLATFORM_TITLE_WORKFLOW_VERSION = "1.0"
PER_PLATFORM_TITLE_CANDIDATE_COUNT = 5
PLATFORM_TITLE_MATRIX_KEYS = (
    "fanqie",
    "qidian",
    "qimao",
    "jinjiang",
    "feilu",
    "zongheng",
    "tadu",
    "general",
)
DEFAULT_TITLE_CANDIDATE_COUNT = (
    PER_PLATFORM_TITLE_CANDIDATE_COUNT * len(PLATFORM_TITLE_MATRIX_KEYS)
)


@dataclass(frozen=True)
class PlatformTitleStyle:
    key: str
    label: str
    aliases: tuple[str, ...]
    min_chars: int
    max_chars: int
    preferred_min_chars: int
    preferred_max_chars: int
    design_rules: tuple[str, ...]
    avoid_rules: tuple[str, ...]


PLATFORM_TITLE_STYLES: dict[str, PlatformTitleStyle] = {
    "fanqie": PlatformTitleStyle(
        key="fanqie",
        label="番茄小说",
        aliases=("番茄", "fanqie", "番茄小说", "番茄免费小说"),
        min_chars=6,
        max_chars=30,
        preferred_min_chars=9,
        preferred_max_chars=22,
        design_rules=(
            "标题先交代开局事件、身份反差或金手指，降低推荐流理解成本。",
            "允许冒号、逗号、问号，标题可以像一句短广告语。",
            "优先使用“开局/我在/让你/全民/规则”等可秒懂入口。",
        ),
        avoid_rules=(
            "不要只给意象或抽象概念。",
            "不要把世界观名词堆到读者看不懂。",
        ),
    ),
    "qidian": PlatformTitleStyle(
        key="qidian",
        label="起点中文网",
        aliases=("起点", "qidian", "起点中文网", "阅文", "起点读书"),
        min_chars=2,
        max_chars=18,
        preferred_min_chars=3,
        preferred_max_chars=12,
        design_rules=(
            "标题可以更概念化，保留设定质感和长期 IP 感。",
            "优先命名核心职业、制度、家族、法门、世界规则或主角策略。",
            "可以用反常识短句，但不要把简介直接压成标题。",
        ),
        avoid_rules=(
            "不要过度广告化。",
            "不要用过长模板遮蔽原创设定。",
        ),
    ),
    "qimao": PlatformTitleStyle(
        key="qimao",
        label="七猫中文网",
        aliases=("七猫", "qimao", "七猫小说", "七猫中文网", "七猫免费小说"),
        min_chars=4,
        max_chars=24,
        preferred_min_chars=5,
        preferred_max_chars=18,
        design_rules=(
            "标题要让移动端读者立刻知道主角身份、处境和爽点方向。",
            "男频优先身份逆袭、权力/医武/边关/玄幻器物；女频优先关系处境和身份反转。",
            "冒号副标题可用于“从某个低位入口开始”的上升路线。",
        ),
        avoid_rules=(
            "不要只剩文学意象。",
            "不要让标题承诺与前三章实际内容脱节。",
        ),
    ),
    "jinjiang": PlatformTitleStyle(
        key="jinjiang",
        label="晋江文学城",
        aliases=("晋江", "jinjiang", "jjwxc", "晋江文学城"),
        min_chars=2,
        max_chars=24,
        preferred_min_chars=3,
        preferred_max_chars=16,
        design_rules=(
            "标题优先体现关系张力、人物处境、情绪钩子和题材标签。",
            "可用方括号补充穿书、快穿、年代、ABO、GL 等读者筛选信号。",
            "短标题要有记忆点，长标题要像一句人设冲突。",
        ),
        avoid_rules=(
            "不要用男频强爽命令式标题套关系文。",
            "不要为了热词牺牲人物气质。",
        ),
    ),
    "feilu": PlatformTitleStyle(
        key="feilu",
        label="飞卢小说网",
        aliases=("飞卢", "feilu", "faloo", "飞卢小说"),
        min_chars=8,
        max_chars=34,
        preferred_min_chars=12,
        preferred_max_chars=28,
        design_rules=(
            "标题直接抛出频道、同人宇宙、开局身份和爽点结果。",
            "常用“综武/综漫/娱乐/名义/盘点/让你”等高识别前缀。",
            "读者应从标题就知道看点冲突和预期爽点。",
        ),
        avoid_rules=(
            "不要隐藏题材来源。",
            "不要使用慢热文学化命名。",
        ),
    ),
    "zongheng": PlatformTitleStyle(
        key="zongheng",
        label="纵横中文网",
        aliases=("纵横", "zongheng", "纵横中文网"),
        min_chars=2,
        max_chars=20,
        preferred_min_chars=3,
        preferred_max_chars=14,
        design_rules=(
            "标题偏传统类型和热血长线，重气势、格局、功法、王朝与江湖。",
            "适合比番茄更稳重、比起点更直观的类型化标题。",
        ),
        avoid_rules=("不要用过多问号和口播式标题。",),
    ),
    "tadu": PlatformTitleStyle(
        key="tadu",
        label="塔读小说",
        aliases=("塔读", "tadu", "塔读小说"),
        min_chars=2,
        max_chars=22,
        preferred_min_chars=3,
        preferred_max_chars=16,
        design_rules=(
            "标题可在传统类型名和免费阅读快感之间平衡。",
            "历史、都市、玄幻可用身份/时代/系统/长生等明确类型词。",
        ),
        avoid_rules=("不要过度抽象到看不出分类。",),
    ),
    "general": PlatformTitleStyle(
        key="general",
        label="全平台",
        aliases=("全平台", "general", "custom", "all platforms"),
        min_chars=2,
        max_chars=24,
        preferred_min_chars=4,
        preferred_max_chars=16,
        design_rules=(
            "标题同时保留类型入口、核心卖点和可记忆符号。",
            "输出需可被各平台继续二次改写。",
        ),
        avoid_rules=("不要只做同义词替换。",),
    ),
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _compact_text(value: Any) -> str:
    return " ".join(_clean_text(value).split())


def _is_english(language: str) -> bool:
    return (language or "").lower().startswith("en")


def _dedupe_strings(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _dedupe_strings([part.strip() for part in value.replace("，", ",").split(",")])
    if isinstance(value, (list, tuple, set)):
        return _dedupe_strings(list(value))
    return []


def normalize_title_platform(platform: str | None) -> str:
    raw = _clean_text(platform).lower()
    if not raw:
        return "general"
    for key, style in PLATFORM_TITLE_STYLES.items():
        if key == "general":
            continue
        if any(alias.lower() in raw for alias in style.aliases):
            return key
    return "general"


def resolve_title_style(platform: str | None) -> PlatformTitleStyle:
    return PLATFORM_TITLE_STYLES[normalize_title_platform(platform)]


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _short_token(value: str, *, max_chars: int = 8, fallback: str = "") -> str:
    text = re.sub(r"[《》“”\"'（）()\[\]【】#]+", "", _compact_text(value))
    text = re.split(r"[，,。.!！？?；;：:\s/]+", text)[0].strip()
    if not text:
        return fallback
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _signal_tokens(profile: Mapping[str, Any]) -> dict[str, str]:
    title = _clean_text(profile.get("primary_title")) or "未命名作品"
    primary = _first_nonempty(
        profile.get("primary_category"),
        profile.get("genre"),
        profile.get("category"),
        "类型",
    )
    secondary = _first_nonempty(
        profile.get("secondary_category"),
        profile.get("sub_genre"),
        profile.get("subcategory"),
        primary,
    )
    tags = _string_list(profile.get("tags"))
    logline = _compact_text(profile.get("logline") or profile.get("short_intro"))
    promise_values = _string_list(profile.get("reader_promise"))
    promise = _compact_text(
        promise_values[0] if promise_values else profile.get("reader_promise")
    )
    characters = profile.get("main_characters")
    first_character = characters[0] if isinstance(characters, list) and characters else {}
    if not isinstance(first_character, Mapping):
        first_character = {}
    protagonist = _first_nonempty(first_character.get("name"), "主角")
    raw_identity = _first_nonempty(
        first_character.get("identity"),
        first_character.get("role"),
        secondary,
        primary,
    )
    if protagonist in {"主角设定", "Protagonist Profile / 主角设定"}:
        protagonist = "主角"
    hook_candidates = [
        item
        for item in tags
        if item not in {primary, secondary, title}
        and len(item) <= 10
        and not _is_genre_like_token(item)
    ] or [
        item
        for item in tags
        if item not in {primary, secondary, title} and len(item) <= 10
    ]
    hook = _first_nonempty(*(hook_candidates[:2] or []), secondary, primary)
    hook2 = _first_nonempty(*(hook_candidates[1:3] or []), hook)
    setting = _resolve_setting(tags, primary, secondary)
    object_token = _resolve_object_token(title, hook, hook2, logline, primary, secondary, tags)
    threat_token = _resolve_threat_token(tags, logline, hook)
    action = _resolve_action(primary, secondary, tags, logline)
    identity = _resolve_identity(raw_identity, tags, logline, primary, secondary)
    origin = _resolve_origin(primary, secondary, tags, identity)
    entry = _resolve_entry_token(tags, logline, identity, hook)
    twist = _resolve_twist(tags, logline, hook2)
    promise_token = _short_token(promise or logline, max_chars=10, fallback=hook)
    return {
        "title": title,
        "primary": primary,
        "secondary": secondary,
        "hook": _short_token(hook, max_chars=8, fallback=secondary),
        "hook2": _short_token(hook2, max_chars=8, fallback=hook),
        "setting": _short_token(setting, max_chars=8, fallback=secondary),
        "object": _short_token(object_token, max_chars=8, fallback=hook),
        "threat": _short_token(threat_token, max_chars=8, fallback=hook),
        "action": action,
        "origin": _short_token(origin, max_chars=8, fallback=identity),
        "entry": _short_token(entry, max_chars=8, fallback=origin),
        "twist": _short_token(twist, max_chars=10, fallback=hook),
        "protagonist": _short_token(protagonist, max_chars=4, fallback="主角"),
        "identity": _short_token(identity, max_chars=8, fallback=secondary),
        "promise": promise_token,
        "tag": _short_token(
            _first_nonempty(hook_candidates[0] if hook_candidates else "", secondary),
            max_chars=6,
        ),
    }


def _is_genre_like_token(value: str) -> bool:
    token = _clean_text(value)
    generic_markers = (
        "悬疑",
        "灵异",
        "言情",
        "都市",
        "玄幻",
        "科幻",
        "历史",
        "脑洞",
        "男频",
        "女频",
        "探案",
        "破案",
        "小说",
        "综合",
    )
    return any(marker in token for marker in generic_markers)


def _resolve_identity(
    raw_identity: str,
    tags: list[str],
    logline: str,
    primary: str,
    secondary: str,
) -> str:
    for item in [raw_identity, logline, *tags]:
        phrase = _extract_identity_phrase(item)
        if phrase:
            return phrase
    for item in [raw_identity, *tags, secondary, primary]:
        token = _short_token(item, max_chars=8)
        if token and not _is_genre_like_token(token):
            return token
    return secondary or primary


def _extract_identity_phrase(text: str) -> str:
    value = _clean_text(text)
    if not value:
        return ""
    exact_markers = (
        "巡捕房副捕头",
        "巡捕房捕头",
        "茅山外门弟子",
        "城西分局警察",
    )
    for marker in exact_markers:
        if marker in value:
            return marker
    suffix_markers = (
        "副捕头",
        "捕头",
        "巡捕",
        "风水师",
        "道士",
        "法医",
        "仵作",
        "剑修",
        "弟子",
        "出马仙",
        "秘书",
        "神医",
        "特工",
        "警察",
    )
    for marker in suffix_markers:
        if marker not in value:
            continue
        if marker == "副捕头" and "巡捕房副捕头" in value:
            return "巡捕房副捕头"
        if marker == "捕头" and "巡捕房捕头" in value:
            return "巡捕房捕头"
        if marker == "弟子" and "茅山外门弟子" in value:
            return "茅山外门弟子"
        match = re.search(rf"[\u4e00-\u9fff]{{0,3}}{re.escape(marker)}", value)
        if match:
            return match.group(0)
        return marker
    return ""


def _resolve_setting(tags: list[str], primary: str, secondary: str) -> str:
    setting_keywords = (
        "末世",
        "星际",
        "修仙",
        "仙界",
        "江湖",
        "宫廷",
        "官场",
        "都市",
        "边关",
        "古代",
        "民国",
        "校园",
        "娱乐圈",
        "副本",
    )
    for item in [*tags, secondary, primary]:
        if any(keyword in item for keyword in setting_keywords):
            return item
    return secondary or primary


def _resolve_object_token(
    title: str,
    hook: str,
    hook2: str,
    logline: str,
    primary: str,
    secondary: str,
    tags: list[str],
) -> str:
    priority_markers = (
        "重瞳",
        "阴阳眼",
        "青囊",
        "困魂镜",
        "归墟会",
        "双穿门",
        "系统",
        "命盘",
        "神图",
        "秘卷",
        "账本",
        "名单",
        "回执",
        "契约",
        "旧案",
        "血字",
    )
    candidates = [*tags, hook2, hook, title, secondary, primary]
    logline_tokens = re.findall(r"[\u4e00-\u9fff]{2,8}", logline)
    for marker in priority_markers:
        for item in candidates:
            if item and marker in item:
                return item
        phrase = _extract_marker_phrase(logline, marker)
        if phrase:
            return phrase
    candidates.extend(logline_tokens)
    vague_tokens = {"真相", "主角", "小说", "一个", "读者", "平台", "故事", "核心"}
    for item in candidates:
        if item and len(item) >= 2 and item not in vague_tokens and not _is_genre_like_token(item):
            return item
    for item in candidates:
        if item and len(item) >= 2 and item not in vague_tokens:
            return item
    return "命盘"


def _extract_marker_phrase(text: str, marker: str) -> str:
    if not text or marker not in text:
        return ""
    if marker in {"重瞳", "阴阳眼"}:
        match = re.search(rf"[\u4e00-\u9fff]{{0,2}}{re.escape(marker)}", text)
        return match.group(0) if match else marker
    if marker in {"归墟会", "双穿门", "困魂镜"}:
        return marker
    if marker == "会":
        match = re.search(r"[\u4e00-\u9fff]{2,6}会", text)
        return match.group(0) if match else marker
    match = re.search(rf"[\u4e00-\u9fff]{{0,4}}{re.escape(marker)}", text)
    return match.group(0) if match else marker


def _resolve_threat_token(tags: list[str], logline: str, fallback: str) -> str:
    priority_phrases = (
        "归墟会",
        "三族契约",
        "上古邪神",
        "幕后黑手",
        "灭门旧案",
        "凶宅委托",
        "困魂镜",
    )
    for phrase in priority_phrases:
        if phrase in tags or phrase in logline:
            if phrase == "幕后黑手":
                continue
            return phrase
    if "灭门" in logline:
        return "灭门旧案"
    threat_markers = ("会", "旧案", "凶手", "邪神", "名单", "契约", "宗", "门", "局", "案")
    tokens = [
        token
        for token in [*tags, *re.findall(r"[\u4e00-\u9fff]{2,8}", logline)]
        if token and not _is_genre_like_token(token)
    ]
    for marker in threat_markers:
        for token in tokens:
            if marker in token:
                return token
    return fallback


def _resolve_action(primary: str, secondary: str, tags: list[str], logline: str) -> str:
    label = " ".join([primary, secondary, *tags, logline])
    if any(token in label for token in ("悬疑", "刑侦", "探案", "灵异", "规则")):
        return "破局"
    if any(token in label for token in ("官场", "权力", "职场")):
        return "登阶"
    if any(token in label for token in ("历史", "边关", "王朝", "争霸")):
        return "封侯"
    if any(token in label for token in ("仙", "玄幻", "武侠", "修真", "升级")):
        return "证道"
    if any(token in label for token in ("末世", "科幻", "星际", "废土", "系统")):
        return "重启文明"
    if any(token in label for token in ("言情", "婚恋", "纯爱", "甜宠", "破镜")):
        return "改写关系"
    return "逆袭"


def _resolve_origin(primary: str, secondary: str, tags: list[str], identity: str) -> str:
    for token in [identity, *tags, secondary, primary]:
        if token and len(token) <= 8 and not _is_genre_like_token(token):
            return token
    for token in [identity, secondary, primary, *tags]:
        if token and len(token) <= 8:
            return token
    return identity or secondary or primary


def _resolve_entry_token(
    tags: list[str],
    logline: str,
    identity: str,
    fallback: str,
) -> str:
    entry_phrases = (
        "凶宅委托",
        "灭门惨案",
        "灭门旧案",
        "连环厉鬼索命",
        "古宅冤魂索债",
        "沉江棺材",
        "沉江棺案",
        "省府秘书",
        "下山",
        "入赘",
        "退婚",
        "重生",
        "穿书",
        "觉醒",
        "开局",
    )
    for phrase in entry_phrases:
        if phrase in logline:
            if phrase == "灭门惨案":
                return "灭门旧案"
            if phrase == "沉江棺材":
                return "沉江棺案"
            return phrase
    if "凶宅" in logline:
        return "凶宅案"
    if "灭门" in logline:
        return "灭门旧案"
    for token in tags:
        if token and len(token) <= 8 and not _is_genre_like_token(token):
            return token
    return identity or fallback


def _resolve_twist(tags: list[str], logline: str, fallback: str) -> str:
    for token in [*tags, *re.findall(r"[\u4e00-\u9fff]{2,10}", logline)]:
        if any(key in token for key in ("失忆", "穿书", "重生", "反派", "系统", "协议", "倒计时")):
            return token
    return fallback


def build_platform_title_workflow(
    profile: Mapping[str, Any],
    *,
    target_platform: str | None = None,
    candidate_count: int = DEFAULT_TITLE_CANDIDATE_COUNT,
    include_general_candidates: bool = True,
    include_platform_matrix: bool = True,
    per_platform_count: int = PER_PLATFORM_TITLE_CANDIDATE_COUNT,
) -> dict[str, Any]:
    style = resolve_title_style(target_platform or _clean_text(profile.get("target_platform")))
    language = _clean_text(profile.get("language"))
    is_english = _is_english(language)
    platform_groups: list[dict[str, Any]] = []
    if include_platform_matrix:
        platform_groups = _build_platform_matrix(profile, per_platform_count, is_english)
        for group in platform_groups:
            group["candidates"] = _filter_and_annotate_candidates(
                group["candidates"],
                profile,
                per_platform_count,
            )
            group["candidate_count"] = len(group["candidates"])
        candidates = _renumber_candidates(
            [
                candidate
                for group in platform_groups
                for candidate in group["candidates"]
            ]
        )
    elif is_english:
        target_candidates = _build_english_candidates(profile, style, candidate_count + 8)
        candidates = _mix_target_and_general_candidates(
            profile,
            style,
            target_candidates,
            candidate_count + 8,
            include_general_candidates=include_general_candidates,
            is_english=is_english,
        )
        candidates = _filter_and_annotate_candidates(candidates, profile, candidate_count)
    else:
        target_candidates = _build_chinese_candidates(profile, style, candidate_count + 8)
        candidates = _mix_target_and_general_candidates(
            profile,
            style,
            target_candidates,
            candidate_count + 8,
            include_general_candidates=include_general_candidates,
            is_english=is_english,
        )
        candidates = _filter_and_annotate_candidates(candidates, profile, candidate_count)
    recommended_primary = _select_primary_candidate(candidates)
    return {
        "schema_version": PLATFORM_TITLE_WORKFLOW_VERSION,
        "target_platform": _clean_text(target_platform) or style.label,
        "platform_key": style.key,
        "platform_label": style.label,
        "candidate_policy": (
            "platform_matrix_5_each"
            if include_platform_matrix
            else (
                "target_platform_plus_general"
                if include_general_candidates and style.key != "general"
                else "single_platform"
            )
        ),
        "per_platform_count": per_platform_count if include_platform_matrix else None,
        "platform_count": len(platform_groups) if include_platform_matrix else 1,
        "candidate_count": len(candidates),
        "recommended_primary_title": recommended_primary,
        "platform_groups": platform_groups,
        "style_profile": {
            "min_chars": style.min_chars,
            "max_chars": style.max_chars,
            "preferred_min_chars": style.preferred_min_chars,
            "preferred_max_chars": style.preferred_max_chars,
            "design_rules": list(style.design_rules),
            "avoid_rules": list(style.avoid_rules),
        },
        "generation_steps": [
            "提取故事 DNA：类型、二级分类、标签、主角身份、核心钩子、读者承诺。",
            "按平台矩阵分别生成书名，而不是只按当前目标平台出一组。",
            "每个平台输出 5 个强点击候选：短 IP 名、开局句、身份逆袭、关系钩子等分组。",
            "执行硬过滤：长度、重复、空泛词、平台风格错配、标题与内容承诺不一致。",
            "按识别速度、类型命中、记忆度、平台贴合度打分并排序。",
        ],
        "scoring_dimensions": [
            "平台结构命中",
            "移动端一眼理解",
            "类型/标签覆盖",
            "IP 记忆度",
            "合规与不过度承诺",
        ],
        "candidates": candidates,
}


def _build_platform_matrix(
    profile: Mapping[str, Any],
    per_platform_count: int,
    is_english: bool,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    global_seen: set[str] = set()
    requested = max(1, per_platform_count)
    for key in PLATFORM_TITLE_MATRIX_KEYS:
        style = PLATFORM_TITLE_STYLES[key]
        emotion_pool = (
            []
            if is_english
            else _build_public_emotion_title_candidates(profile, style, requested + 2)
        )
        pool = emotion_pool + (
            _build_english_candidates(profile, style, requested + 12)
            if is_english
            else _build_chinese_candidates(profile, style, requested + 12)
        )
        selected: list[dict[str, Any]] = []
        for candidate in pool:
            if candidate.get("pattern") in {"当前主书名校准", "Current main title"}:
                continue
            row = _annotate_candidate_compliance(candidate, profile)
            if row.get("risk_blocked"):
                continue
            title = _clean_text(row.get("title"))
            if not title or title in global_seen:
                continue
            row["platform_rank"] = len(selected) + 1
            row["recommendation"] = _platform_recommendation(style, len(selected))
            selected.append(row)
            global_seen.add(title)
            if len(selected) >= requested:
                break
        groups.append(
            {
                "platform_key": style.key,
                "platform_label": style.label,
                "scope_label": "全平台" if style.key == "general" else "平台专项",
                "candidate_count": len(selected),
                "candidates": _renumber_candidates(selected),
            }
        )
    return groups


def _platform_recommendation(style: PlatformTitleStyle, index: int) -> str:
    if index == 0:
        return "主推"
    if style.key in {"fanqie", "feilu"} and index in {1, 2}:
        return "广告测试"
    if style.key in {"qidian", "jinjiang"} and index == 1:
        return "A/B测试"
    return "备选"


def select_primary_platform_title(
    profile: Mapping[str, Any],
    *,
    target_platform: str | None = None,
) -> dict[str, Any]:
    workflow = build_platform_title_workflow(
        profile,
        target_platform=target_platform,
        candidate_count=DEFAULT_TITLE_CANDIDATE_COUNT,
    )
    candidate = workflow.get("recommended_primary_title")
    return candidate if isinstance(candidate, dict) else {}


def _mix_target_and_general_candidates(
    profile: Mapping[str, Any],
    style: PlatformTitleStyle,
    target_candidates: list[dict[str, Any]],
    candidate_count: int,
    *,
    include_general_candidates: bool,
    is_english: bool,
) -> list[dict[str, Any]]:
    if not include_general_candidates or style.key == "general" or candidate_count <= 1:
        return _renumber_candidates(target_candidates[:candidate_count])

    general_style = PLATFORM_TITLE_STYLES["general"]
    general_candidates = (
        _build_english_candidates(profile, general_style, candidate_count)
        if is_english
        else _build_chinese_candidates(profile, general_style, candidate_count)
    )
    general_quota = max(4, candidate_count // 4)
    target_quota = max(1, candidate_count - general_quota)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for candidate in target_candidates[:target_quota]:
        _add_unique_candidate(rows, seen, candidate, candidate_count)

    for candidate in general_candidates:
        if len(rows) >= candidate_count:
            break
        _add_unique_candidate(rows, seen, candidate, candidate_count)

    for candidate in target_candidates[target_quota:]:
        if len(rows) >= candidate_count:
            break
        _add_unique_candidate(rows, seen, candidate, candidate_count)

    return _renumber_candidates(rows[:candidate_count])


def _add_unique_candidate(
    rows: list[dict[str, Any]],
    seen: set[str],
    candidate: Mapping[str, Any],
    candidate_count: int,
) -> None:
    if len(rows) >= candidate_count:
        return
    title = _clean_text(candidate.get("title"))
    if not title or title in seen:
        return
    seen.add(title)
    rows.append(dict(candidate))


def _renumber_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        row = dict(candidate)
        row["id"] = index
        rows.append(row)
    return rows


def _select_primary_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    for candidate in candidates:
        if candidate.get("pattern") == "当前主书名校准":
            continue
        if candidate.get("platform_scope") == "target_platform":
            return dict(candidate)
    for candidate in candidates:
        if candidate.get("pattern") != "当前主书名校准":
            return dict(candidate)
    return dict(candidates[0]) if candidates else {}


def _build_public_emotion_title_candidates(
    profile: Mapping[str, Any],
    style: PlatformTitleStyle,
    candidate_count: int,
) -> list[dict[str, Any]]:
    kernel = profile.get("public_emotion_kernel")
    if not isinstance(kernel, Mapping):
        return []
    bridges = kernel.get("emotion_bridges")
    if not isinstance(bridges, list):
        return []
    signals = _signal_tokens(profile)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_bridge in enumerate(bridges):
        if len(rows) >= candidate_count:
            break
        if not isinstance(raw_bridge, Mapping):
            continue
        raw_title = _first_nonempty(
            raw_bridge.get("title_hook"),
            raw_bridge.get("story_hook"),
            raw_bridge.get("genre_translation"),
        )
        title = _normalize_title(raw_title)
        if not title or title in seen:
            continue
        if len(title) < style.min_chars or len(title) > style.max_chars:
            continue
        seen.add(title)
        bridge_type = _clean_text(raw_bridge.get("bridge_type")) or "public_emotion_bridge"
        pattern = f"公共情绪桥：{bridge_type}"
        label_fields = _candidate_label_fields(style)
        rows.append(
            {
                "id": len(rows) + 1,
                "title": title,
                "subtitle": _subtitle_for(title, style, signals),
                "angle": f"{style.label}｜{pattern}",
                "recommendation": "主推" if index == 0 else "A/B测试",
                "platform": style.key,
                "platform_label": style.label,
                **label_fields,
                "pattern": pattern,
                "score": min(100, _score_candidate(title, style, signals) + 4),
                "fit_notes": _fit_notes(title, style, signals),
                "emotion_bridge_id": _clean_text(
                    raw_bridge.get("bridge_id") or raw_bridge.get("id")
                ),
                "emotion_bridge_type": bridge_type,
                "public_emotion_anchor": _clean_text(raw_bridge.get("public_anchor")),
                "public_emotion_payoff": _clean_text(raw_bridge.get("reader_payoff")),
            }
        )
    return rows


def _filter_and_annotate_candidates(
    candidates: list[dict[str, Any]],
    profile: Mapping[str, Any],
    candidate_count: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        title = _clean_text(candidate.get("title"))
        if not title or title in seen:
            continue
        row = _annotate_candidate_compliance(candidate, profile)
        if row.get("risk_blocked"):
            continue
        rows.append(row)
        seen.add(title)
        if len(rows) >= candidate_count:
            break
    return _renumber_candidates(rows)


def _annotate_candidate_compliance(
    candidate: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(candidate)
    texts = [
        _clean_text(row.get("title")),
        _clean_text(row.get("subtitle")),
        _clean_text(row.get("angle")),
    ]
    risks = scan_compliance_texts(
        [text for text in texts if text],
        profile.get("compliance_boundary_kernel")
        if isinstance(profile.get("compliance_boundary_kernel"), Mapping)
        else None,
    )
    high_risks = [risk for risk in risks if risk.severity in {"critical", "high"}]
    row["risk_flags"] = _dedupe_strings([risk.code for risk in risks])
    if risks:
        row["compliance_notes"] = "；".join(
            f"{risk.severity}:{risk.term}" for risk in risks[:4]
        )
    else:
        row["compliance_notes"] = "未命中配置化高风险词；仍需人工/平台审核。"
    row["risk_blocked"] = bool(high_risks)
    return row


def _build_chinese_candidates(
    profile: Mapping[str, Any],
    style: PlatformTitleStyle,
    candidate_count: int,
) -> list[dict[str, Any]]:
    signals = _signal_tokens(profile)
    template_specs = _platform_template_specs(style.key, signals)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for candidate in _build_public_emotion_title_candidates(profile, style, candidate_count):
        _add_unique_candidate(rows, seen, candidate, candidate_count)
        if len(rows) >= candidate_count:
            return rows[:candidate_count]

    if style.key == "fanqie" and _is_fanqie_short_profile(profile):
        for title, pattern, recommendation in _fanqie_short_template_specs(profile, signals):
            _append_candidate(rows, seen, title, pattern, recommendation, style, signals)
            if len(rows) >= candidate_count:
                return rows[:candidate_count]

    for title, pattern, recommendation in template_specs:
        _append_candidate(rows, seen, title, pattern, recommendation, style, signals)
        if len(rows) >= candidate_count:
            return rows[:candidate_count]

    for title in _expansion_titles(style.key, signals):
        _append_candidate(rows, seen, title, "扩展候选", "备选", style, signals)
        if len(rows) >= candidate_count:
            return rows[:candidate_count]

    return rows[:candidate_count]


def _append_candidate(
    rows: list[dict[str, Any]],
    seen: set[str],
    raw_title: str,
    pattern: str,
    recommendation: str,
    style: PlatformTitleStyle,
    signals: Mapping[str, str],
) -> None:
    title = _normalize_title(raw_title)
    if not title or title in seen:
        return
    if len(title) < style.min_chars and title != signals.get("title"):
        return
    if len(title) > style.max_chars:
        return
    seen.add(title)
    score = _score_candidate(title, style, signals)
    label_fields = _candidate_label_fields(style)
    rows.append(
        {
            "id": len(rows) + 1,
            "title": title,
            "subtitle": _subtitle_for(title, style, signals),
            "angle": f"{style.label}｜{pattern}",
            "recommendation": recommendation,
            "platform": style.key,
            "platform_label": style.label,
            **label_fields,
            "pattern": pattern,
            "score": score,
            "fit_notes": _fit_notes(title, style, signals),
        }
    )


def _is_fanqie_short_profile(profile: Mapping[str, Any]) -> bool:
    haystack = " ".join(
        [
            _clean_text(profile.get("target_platform")),
            _clean_text(profile.get("length_type")),
            _clean_text(profile.get("channel")),
            _clean_text(profile.get("primary_category")),
            _clean_text(profile.get("secondary_category")),
            " ".join(_string_list(profile.get("tags"))),
        ]
    )
    return any(token in haystack for token in ("短故事", "单篇完结", "fanqie_short", "tomato_short"))


def _fanqie_short_template_specs(
    profile: Mapping[str, Any],
    s: Mapping[str, str],
) -> list[tuple[str, str, str]]:
    text = " ".join(
        [
            _clean_text(profile.get("primary_title")),
            _clean_text(profile.get("logline")),
            _clean_text(profile.get("short_intro")),
            " ".join(_string_list(profile.get("promo_copy"))),
            " ".join(_string_list(profile.get("reader_promise"))),
            " ".join(_string_list(profile.get("tags"))),
        ]
    )
    crisis = _first_match(
        text,
        ("全员群", "离职当天", "发布会", "婚礼现场", "病房门口", "公司群", "直播间"),
        fallback="开局",
    )
    charge = _first_match(
        text,
        ("贪污犯", "背锅人", "小三", "替罪羊", "骗子", "嫌疑人", "罪名"),
        fallback=s["hook"],
    )
    villain = _first_match(
        text,
        ("老板", "上司", "前夫", "婆婆", "反派", "裴总", "周总", "幕后老板"),
        fallback="反派",
    )
    payoff = _first_match(
        text,
        ("自爆", "认罪", "撤回公告", "公开道歉", "直播翻车", "当众露馅"),
        fallback="当众自爆",
    )
    power = _first_match(
        text,
        ("情绪爆改器", "金手指", "系统", "黑屏提示", "能力", "读心术", "重生"),
        fallback=s["object"],
    )
    cost = _first_match(
        text,
        ("记忆代价", "亲情记忆", "温暖记忆", "反噬", "冷却"),
        fallback="代价",
    )
    amount = _first_match(text, ("四十七万", "五十万", "一百万", "三千万"), fallback="")
    accusation_title = (
        f"被栽赃{amount}后，我点开了{power}"
        if amount
        else f"被公司栽赃后，我点开了{power}"
    )
    public_arena = _first_match(
        text,
        ("发布会", "直播间", "婚礼现场", "病房门口"),
        fallback="全公司" if crisis in {"全员群", "公司群"} else crisis,
    )

    return [
        (
            f"{crisis}把我挂成{charge}后，我让{villain}当众自爆",
            "短故事强冲突长标题",
            "主推",
        ),
        (
            accusation_title,
            "罪名+金手指入口",
            "广告测试",
        ),
        (
            f"他们逼我背锅，我让{public_arena}变成自爆现场",
            "压迫转公开打脸",
            "广告测试",
        ),
        (
            f"我被挂上{crisis}那天，{villain}开始替我说真话",
            "开局羞辱+反派自证",
            "A/B测试",
        ),
        (
            f"每次打脸都要付出{cost}",
            "爽点代价钩子",
            "备选",
        ),
        (
            f"{power}一开，{villain}自己{payoff}",
            "金手指即时生效",
            "备选",
        ),
        (
            f"离职当天，我把背锅局改成公开审判",
            "现实职场打脸",
            "备选",
        ),
    ]


def _first_match(text: str, choices: Sequence[str], *, fallback: str = "") -> str:
    for choice in choices:
        if choice and choice in text:
            return choice
    return fallback


def _normalize_title(value: str) -> str:
    title = _compact_text(value)
    title = title.replace(":", "：").replace(",", "，").replace("?", "？")
    title = title.replace("!", "！")
    title = re.sub(r"\s+", "", title)
    title = re.sub(r"[·]{2,}", "·", title)
    title = title.strip("，。；：、 ")
    return title


def _candidate_label_fields(style: PlatformTitleStyle) -> dict[str, str]:
    if style.key == "general":
        return {
            "platform_scope": "all_platform",
            "scope_label": "全平台",
            "platform_tag": "全平台",
            "display_label": "全平台",
        }
    return {
        "platform_scope": "target_platform",
        "scope_label": "平台专项",
        "platform_tag": style.label,
        "display_label": style.label,
    }


def _platform_template_specs(
    key: str,
    s: Mapping[str, str],
) -> list[tuple[str, str, str]]:
    title = s["title"]
    if key == "fanqie":
        return [
            (f"开局{s['hook']}，我用{s['object']}{s['action']}", "开局事件+能力反制", "主推"),
            (f"我在{s['setting']}靠{s['object']}翻盘", "第一人称爽点入口", "A/B测试"),
            (
                f"让你{s['origin']}查{s['entry']}，你把{s['threat']}挖出来？",
                "反差命令句",
                "广告测试",
            ),
            (f"{s['hook']}规则：谁撒谎谁先出局", "强规则钩子", "广告测试"),
            (f"别人遇到{s['threat']}逃命，我靠{s['object']}通关", "对照爽点", "备选"),
            (f"全民{s['hook']}：我的{s['object']}能升级", "全民流+金手指", "垂类测试"),
            (f"{s['identity']}：从{s['origin']}开始{s['action']}", "身份+低位起点", "备选"),
        ]
    if key == "qidian":
        return [
            (f"{s['threat']}案卷", "核心案件/组织 IP", "主推"),
            (f"{s['setting']}诡案录", "世界观+案件长线", "A/B测试"),
            (f"{s['object']}夜巡人", "职业/能力 IP 名", "备选"),
            (f"{s['threat']}纪事", "世界规则纪事", "备选"),
            (f"苟在{s['setting']}查{s['hook']}", "策略型长线", "备选"),
            (f"{s['hook2']}秘档", "设定质感名", "垂类测试"),
            (f"{s['setting']}无疆", "古典意象+格局", "备选"),
        ]
    if key == "qimao":
        return [
            (f"{s['object']}神探", "强职业爽点", "主推"),
            (f"{s['hook']}巅峰：从{s['entry']}开始", "低位上升", "主推"),
            (f"{s['setting']}第一{s['identity']}", "地位承诺", "A/B测试"),
            (f"{s['identity']}归来，硬刚{s['threat']}", "身份反转", "广告测试"),
            (f"{s['hook2']}奇案", "强题材入口", "备选"),
            (f"{s['object']}破局录", "器物/能力爽点", "垂类测试"),
            (f"寒门{s['identity']}", "底层逆袭", "备选"),
            (f"{s['protagonist']}归来，{s['hook']}不装了", "身份反转", "广告测试"),
        ]
    if key == "jinjiang":
        return [
            (f"{s['hook']}倒计时", "情绪期限", "主推"),
            (f"协议{s['hook']}，但{s['twist']}", "关系契约+反转", "主推"),
            (f"{s['identity']}如何拯救{s['threat']}", "人物处境", "A/B测试"),
            (f"你就是那个{s['identity']}？", "角色反差问句", "备选"),
            (f"{s['object']}今天说真话了吗", "轻口语关系钩子", "备选"),
            (f"逃离{s['object']}", "意象+动作", "备选"),
            (f"{title}[{s['tag']}]", "题材标签补强", "垂类测试"),
        ]
    if key == "feilu":
        return [
            (f"{s['primary']}：开局{s['hook']}，我{s['action']}", "频道+开局", "主推"),
            (f"{s['setting']}：{s['object']}在手，开局{s['action']}", "频道前缀+能力", "垂类测试"),
            (f"盘点{s['hook']}名场面，{s['setting']}破防", "盘点流", "广告测试"),
            (f"让你查{s['entry']}，你把{s['object']}练成神？", "反差命令句", "主推"),
            (f"{s['primary']}：{s['identity']}开局硬刚{s['threat']}", "身份硬刚流", "备选"),
            (f"{s['primary']}！把{s['object']}{s['hook2']}写成神作", "强口播承诺", "备选"),
        ]
    if key in {"zongheng", "tadu"}:
        return [
            (f"{s['threat']}秘录", "核心案件/组织", "主推"),
            (f"{s['setting']}第一{s['identity']}", "地位承诺", "备选"),
            (f"{s['object']}神图", "器物 IP", "备选"),
            (f"我在{s['setting']}{s['action']}", "直观类型入口", "A/B测试"),
            (f"{s['origin']}青云路", "上升路线", "备选"),
            (f"{s['hook']}生死局", "危机钩子", "备选"),
        ]
    return [
        (f"{s['hook']}之书", "核心卖点", "备选"),
        (f"{s['threat']}规则", "规则钩子", "备选"),
        (f"我在{s['setting']}{s['action']}", "平台通用直观入口", "A/B测试"),
    ]


def _expansion_titles(key: str, s: Mapping[str, str]) -> list[str]:
    base = [
        f"{s['object']}回执",
        f"{s['hook']}名单",
        f"{s['setting']}不眠夜",
        f"{s['object']}第一案",
        f"{s['origin']}生死局",
        f"{s['hook']}秘卷",
        f"{s['object']}归来",
        f"{s['setting']}破局人",
        f"{s['protagonist']}的{s['object']}",
        f"{s['hook']}终局",
        f"开局撞见{s['object']}",
        f"{s['origin']}成王路",
        f"{s['object']}档案",
        f"{s['hook']}异闻录",
        f"{s['setting']}追凶",
        f"{s['object']}前夜",
        f"{s['origin']}登阶",
        f"{s['hook']}入局",
        f"{s['object']}之门",
        f"{s['setting']}余烬",
        f"{s['hook']}契约",
        f"{s['object']}失控",
        f"{s['protagonist']}不认输",
    ]
    if key == "jinjiang":
        return [
            f"{s['hook']}，请保持沉默",
            f"成为{s['identity']}以后",
            f"{s['object']}没有秘密",
            f"和{s['identity']}协议恋爱",
            f"我真不是{s['identity']}",
            f"{s['protagonist']}决定去死",
            f"{s['hook']}或像{s['hook']}的人",
            f"长日{s['object']}",
            f"偏离{s['hook']}剧情",
            f"{s['object']}春日",
            *base,
        ]
    if key == "qidian":
        return [
            f"{s['object']}谱",
            f"{s['hook']}道君",
            f"{s['setting']}稷",
            f"{s['object']}无疆",
            f"{s['origin']}修什么仙",
            f"{s['hook']}鉴",
            f"{s['object']}天书",
            *base,
        ]
    if key in {"fanqie", "feilu"}:
        return [
            f"开局{s['origin']}，我靠{s['object']}封神",
            f"让你管{s['hook']}，你管成{s['setting']}第一？",
            f"我把{s['object']}玩成了{s['hook']}天花板",
            f"{s['primary']}：{s['protagonist']}开局{s['action']}",
            *base,
        ]
    return base


def _score_candidate(
    title: str,
    style: PlatformTitleStyle,
    signals: Mapping[str, str],
) -> int:
    score = 55
    if style.preferred_min_chars <= len(title) <= style.preferred_max_chars:
        score += 12
    signal_tokens = (signals["hook"], signals["object"], signals["identity"])
    if any(token and token in title for token in signal_tokens):
        score += 10
    if "：" in title or "，" in title or "？" in title:
        score += 4
    if style.key in {"qidian", "jinjiang"} and len(title) <= style.preferred_max_chars:
        score += 6
    fanqie_prefixes = ("开局", "我在", "让你", "全民", "综武", "娱乐", "盘点")
    if style.key in {"fanqie", "feilu"} and title.startswith(fanqie_prefixes):
        score += 12
    qimao_tokens = ("巅峰", "神医", "归来", "下山", "第一", "寒门")
    if style.key == "qimao" and any(token in title for token in qimao_tokens):
        score += 10
    jinjiang_tokens = ("协议", "倒计时", "今天", "[", "？")
    if style.key == "jinjiang" and any(token in title for token in jinjiang_tokens):
        score += 10
    if len(title) > style.preferred_max_chars + 6:
        score -= 8
    return max(0, min(score, 100))


def _subtitle_for(title: str, style: PlatformTitleStyle, signals: Mapping[str, str]) -> str:
    if style.key in {"fanqie", "feilu"}:
        return f"{signals['hook']}开局，{signals['promise']}。"
    if style.key == "qidian":
        return f"{signals['object']}牵出长线规则，{signals['setting']}持续升级。"
    if style.key == "qimao":
        return f"{signals['identity']}从{signals['origin']}起势，主打{signals['hook']}。"
    if style.key == "jinjiang":
        return f"{signals['identity']}与{signals['hook']}之间的关系反转。"
    return f"{signals['hook']}入口，{signals['promise']}。"


def _fit_notes(title: str, style: PlatformTitleStyle, signals: Mapping[str, str]) -> list[str]:
    notes = [
        (
            f"长度 {len(title)} 字，平台偏好 "
            f"{style.preferred_min_chars}-{style.preferred_max_chars} 字。"
        ),
    ]
    if signals["hook"] in title:
        notes.append(f"包含核心钩子：{signals['hook']}。")
    if signals["object"] in title and signals["object"] != signals["hook"]:
        notes.append(f"包含可记忆物件/规则：{signals['object']}。")
    if style.key in {"fanqie", "feilu"} and any(mark in title for mark in ("开局", "让你", "我在")):
        notes.append("适合推荐流首屏快速理解。")
    if style.key in {"qidian", "jinjiang"} and len(title) <= style.preferred_max_chars:
        notes.append("保留短标题记忆点。")
    return notes


def _build_english_candidates(
    profile: Mapping[str, Any],
    style: PlatformTitleStyle,
    candidate_count: int,
) -> list[dict[str, Any]]:
    title = _clean_text(profile.get("primary_title")) or "Untitled"
    primary = _clean_text(profile.get("primary_category")) or "Genre"
    secondary = _clean_text(profile.get("secondary_category")) or primary
    tags = _string_list(profile.get("tags"))
    hook = tags[0] if tags else secondary
    specs = [
        title,
        f"{title}: {hook}",
        f"The {hook} Files",
        f"{primary} Chronicles",
        f"Rules of {hook}",
        f"The {secondary} Ledger",
        f"{hook}: Day One",
        f"Breaking {primary}",
        f"The {hook} List",
        f"{title}: Origins",
    ]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    label_fields = _candidate_label_fields(style)
    for raw_title in specs:
        normalized = _compact_text(raw_title)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        rows.append(
            {
                "id": len(rows) + 1,
                "title": normalized,
                "subtitle": f"{hook} entry point for {secondary}.",
                "angle": f"{style.label}｜English fallback",
                "recommendation": "Primary" if not rows else "Alt",
                "platform": style.key,
                "platform_label": style.label,
                **label_fields,
                "pattern": "English fallback",
                "score": 70,
                "fit_notes": ["English fallback candidate."],
            }
        )
        if len(rows) >= candidate_count:
            break
    while len(rows) < candidate_count:
        rows.append(
            {
                "id": len(rows) + 1,
                "title": f"{title}: Test {len(rows) + 1}",
                "subtitle": f"{hook} entry point for {secondary}.",
                "angle": f"{style.label}｜English fallback",
                "recommendation": "Alt",
                "platform": style.key,
                "platform_label": style.label,
                **label_fields,
                "pattern": "English fallback",
                "score": 50,
                "fit_notes": ["Auto-filled to keep the listing testable."],
            }
        )
    return rows[:candidate_count]
