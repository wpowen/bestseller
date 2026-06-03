from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# ruff: noqa: ANN401, RUF001
import re
from typing import Any

from bestseller.services.compliance_boundary_kernel import scan_compliance_texts

PLATFORM_TITLE_WORKFLOW_VERSION = "1.1"
PER_PLATFORM_TITLE_CANDIDATE_COUNT = 5
PLATFORM_TITLE_MATRIX_KEYS = (
    "fanqie",
    "qidian",
    "qimao",
    "jinjiang",
    "qq_read",
    "hongxiu",
    "zhangyue",
    "douban",
    "17k",
    "feilu",
    "zongheng",
    "tadu",
    "general",
)
DEFAULT_TITLE_CANDIDATE_COUNT = (
    PER_PLATFORM_TITLE_CANDIDATE_COUNT * len(PLATFORM_TITLE_MATRIX_KEYS)
)

BAD_TITLE_PART_MARKERS = (
    "未命名",
    "主角",
    "读者",
    "平台",
    "故事",
    "类型",
    "题材",
    "候选",
    "综合",
)

BAD_TITLE_FRAGMENT_MARKERS = (
    "参与者之",
    "凶手就在",
    "就在参与者",
    "而那",
)

BAD_TITLE_TOKEN_SUFFIXES = ("之", "的", "和", "与", "在")
BAD_TITLE_TOKEN_PREFIXES = ("而", "但", "却", "被", "把")


@dataclass(frozen=True)
class PlatformTitleStyle:
    key: str
    label: str
    aliases: tuple[str, ...]
    min_chars: int
    max_chars: int
    preferred_min_chars: int
    preferred_max_chars: int
    methodology_group: str
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
        methodology_group="强钩子叙事型",
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
        methodology_group="世界观驱动短标题",
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
        methodology_group="移动端爽点直给型",
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
        methodology_group="关系张力/情绪型",
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
    "qq_read": PlatformTitleStyle(
        key="qq_read",
        label="QQ阅读",
        aliases=("QQ阅读", "qq_read", "qqread", "qq阅读", "qq"),
        min_chars=6,
        max_chars=32,
        preferred_min_chars=10,
        preferred_max_chars=24,
        methodology_group="强钩子叙事型",
        design_rules=(
            "标题优先像一行前情提要，直接交代时间条件、身份压力、冲突和结果承诺。",
            "允许逗号、问号和反差句，读者看题目就应知道爽点或关系爆点。",
            "女频可优先重生、离婚、替嫁、萌宝、掉马、豪门等高识别入口；男频可用开局、系统、职业反转。",
        ),
        avoid_rules=(
            "不要只给 4-6 字意象短题。",
            "不要把长标题写成不通顺的简介拼接。",
        ),
    ),
    "hongxiu": PlatformTitleStyle(
        key="hongxiu",
        label="红袖添香",
        aliases=("红袖", "hongxiu", "红袖添香"),
        min_chars=4,
        max_chars=28,
        preferred_min_chars=6,
        preferred_max_chars=18,
        methodology_group="女频关系/钩子混合型",
        design_rules=(
            "标题要兼顾古言/现言关系身份、情绪钩子和可读懂的反转。",
            "可用重生后、替嫁后、退婚后、夫人、权臣、东宫等强频道词。",
            "短题要有女性向精致感，长题要有明确关系冲突。",
        ),
        avoid_rules=(
            "不要用男频世界观硬词压过人物关系。",
            "不要只剩甜宠泛词或陈旧总裁套话。",
        ),
    ),
    "zhangyue": PlatformTitleStyle(
        key="zhangyue",
        label="掌阅",
        aliases=("掌阅", "zhangyue", "ireader", "掌阅书城"),
        min_chars=2,
        max_chars=18,
        preferred_min_chars=3,
        preferred_max_chars=10,
        methodology_group="出版感/意象型",
        design_rules=(
            "标题降低套路词密度，优先物象、地点、季节、旧事、来信等有复读性的短题。",
            "保留类型暗示，但不要把推荐流广告语压成主书名。",
            "适合用副标题补足卖点，主标题保持克制。",
        ),
        avoid_rules=(
            "不要过多问号、逗号和口播式强钩子。",
            "不要堆叠系统、开局、全网等强网感词。",
        ),
    ),
    "douban": PlatformTitleStyle(
        key="douban",
        label="豆瓣读书",
        aliases=("豆瓣", "douban", "豆瓣读书"),
        min_chars=2,
        max_chars=16,
        preferred_min_chars=2,
        preferred_max_chars=8,
        methodology_group="文学留白/象征型",
        design_rules=(
            "标题优先短、稳、象征性强，保留场景或物件留白。",
            "可用地域、时间、证词、旧物、天气等低噪音词建立记忆点。",
            "类型卖点应更多放在副标题/简介，而不是主书名。",
        ),
        avoid_rules=(
            "不要使用免费阅读平台式长钩子标题。",
            "不要直接堆热词或爽点承诺。",
        ),
    ),
    "17k": PlatformTitleStyle(
        key="17k",
        label="17K小说网",
        aliases=("17K", "17k", "17K小说网", "17k小说网"),
        min_chars=2,
        max_chars=18,
        preferred_min_chars=3,
        preferred_max_chars=10,
        methodology_group="短硬类型/IP型",
        design_rules=(
            "标题短、硬、类型感强，优先力量、职业、江湖、玄门、战斗或都市身份词。",
            "可用“令、诀、录、行、刀、城、祭”等传统网文记忆尾缀。",
            "标题先让读者知道类型方向，再由简介展开具体卖点。",
        ),
        avoid_rules=(
            "不要用过长前情提要式标题。",
            "不要只剩空泛大词，例如单独使用天命、归来、成神。",
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
        methodology_group="强频道/开局爽点型",
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
        methodology_group="传统类型/热血长线型",
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
        methodology_group="传统类型+免费阅读平衡型",
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
        methodology_group="全平台折中型",
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
    # Split on sentence punctuation AND taxonomy separators (·／、) so a raw
    # multi-segment sub_genre like "修真·复仇·宗门权谋" never enters a title verbatim.
    text = re.split(r"[，,。.!！？?；;：:\s/·／、|]+", text)[0].strip()
    if not text or _is_bad_title_token(text):
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
    primary = _reader_title_token(primary, fallback="新故事")
    secondary = _reader_title_token(secondary, fallback=primary)
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
        and not _is_bad_title_token(item)
        and not _is_genre_like_token(item)
    ] or [
        item
        for item in tags
        if item not in {primary, secondary, title}
        and len(item) <= 10
        and not _is_bad_title_token(item)
    ]
    hook = _first_nonempty(*(hook_candidates[:2] or []), secondary, primary)
    hook2 = _first_nonempty(*(hook_candidates[1:3] or []), hook)
    hook = _reader_title_token(hook, fallback=secondary or primary)
    hook2 = _reader_title_token(hook2, fallback=hook)
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
        "仙侠",
        "升级流",
        "科幻",
        "历史",
        "脑洞",
        "男频",
        "女频",
        "探案",
        "破案",
        "小说",
        "综合",
        "类型",
        "题材",
        "读者",
        "平台",
        "故事",
    )
    return any(marker in token for marker in generic_markers)


# Genre/category TAXONOMY words. These are "shelf labels", not story content,
# and must never form the core of a book title (T-0, 2026-06-03 regression fix).
# Narrative tropes that legitimately appear in real titles (重生/穿越/赘婿/神医/
# 复仇/末世/都市…) are deliberately NOT listed here.
_GENRE_LABEL_WORDS: tuple[str, ...] = (
    "修仙",
    "修真",
    "修炼",
    "仙侠",
    "玄幻",
    "奇幻",
    "武侠",
    "言情",
    "古言",
    "现言",
    "纯爱",
    "耽美",
    "悬疑",
    "灵异",
    "科幻",
    "星际",
    "电竞",
    "二次元",
    "轻小说",
    "升级流",
    "系统流",
    "男频",
    "女频",
    "网游",
    "玄幻言情",
)


def _is_concise_ip_name(title: str) -> bool:
    """A 3-8 char, punctuation-free, non-genre title — a strong IP-style name."""

    text = _clean_text(title)
    if not text:
        return False
    cjk_len = len(re.findall(r"[一-鿿]", text))
    return (
        3 <= cjk_len <= 8
        and not re.search(r"[，,。.!！？?；;：:·／、\s]", text)
        and not _title_uses_genre_label(text)
    )


def _title_uses_genre_label(title: str, signals: Mapping[str, str] | None = None) -> bool:
    """True when a title's core is (or contains) a genre taxonomy label.

    A single sanctioned channel prefix ("仙侠：…" / "玄幻：…") is allowed and
    stripped before the check, because some platforms use a genre channel word
    as a prefix. The *body* of the title must still be free of taxonomy labels.
    """

    body = _clean_text(title)
    if not body:
        return False
    # Strip at most one *sanctioned* leading channel prefix — i.e. the prefix
    # token (before the colon) must itself be exactly a genre channel word, such
    # as "仙侠：…" / "玄幻：…". A prefix like "修真规则：" is NOT a channel word and
    # must remain subject to the genre-label check.
    prefix = re.match(r"^([一-鿿]{2,4})[：:]", body)
    if prefix and prefix.group(1) in _GENRE_LABEL_WORDS:
        body = body[prefix.end():]
    return any(word in body for word in _GENRE_LABEL_WORDS)


def _is_bad_title_token(value: str) -> bool:
    token = _clean_text(value)
    if not token:
        return True
    if any(marker in token for marker in BAD_TITLE_PART_MARKERS):
        return True
    if any(marker in token for marker in BAD_TITLE_FRAGMENT_MARKERS):
        return True
    if token.startswith(BAD_TITLE_TOKEN_PREFIXES):
        return True
    return len(token) > 1 and token.endswith(BAD_TITLE_TOKEN_SUFFIXES)


def _unreadable_title_reason(title: str) -> str:
    """Return a reason when the title is a broken keyword phrase.

    This intentionally stays heuristic and conservative. The production
    regression was not that the title lacked genre fit; it contained the right
    profession word but formed an invalid verb-object phrase such as
    「验房师不开整改单」. That kind of title must be rejected even when it contains
    story terms.
    """

    text = _clean_text(title)
    if not text:
        return "empty"
    if re.search(r"不(?:开|出|写|发|交|下|做)?(?:整改单|验房报告|报告|台账|执照|合同|清单|表单)", text):
        return "negative_document_verb_phrase"
    if re.search(r"(?:签发|签出|开出|写出|提交)一份会决定两?案卷", text):
        return "broken_case_file_object"
    if re.search(r"(?:签发|签出|开出|写出|提交)一份会决定", text):
        return "broken_quantified_object"
    if re.search(r"(?:会决定|将决定)[^，。！？：:]{0,8}(?:案卷|故事|题材|平台)", text):
        return "abstract_meta_object"
    if any(fragment in text for fragment in BAD_TITLE_FRAGMENT_MARKERS):
        return "bad_fragment"
    return ""


def title_readability_issue(title: str) -> str:
    """Public-ish helper for tests and planner-side title refresh gates."""

    return _unreadable_title_reason(title)


def _reader_title_token(value: str, *, fallback: str = "") -> str:
    token = _clean_text(value)
    if not token:
        return fallback
    if "驱魔" in token and ("探案" in token or "灵异" in token or "悬疑" in token):
        return "驱魔"
    if "民国" in token and ("悬疑" in token or "探案" in token):
        return "民国诡案"
    if "东方" in token and "志怪" in token:
        return "东方志怪"
    if "古言" in token or "宫斗" in token:
        return "古言权谋"
    for marker in ("修仙", "仙侠", "玄幻", "科幻", "末世", "都市", "官场", "商战", "刑侦", "志怪"):
        if marker in token:
            return marker
    cleaned = token
    for marker in ("综合", "小说", "故事", "类型", "题材", "平台", "读者"):
        cleaned = cleaned.replace(marker, "")
    cleaned = cleaned.strip(" ：:，,。.!！？?；;、")
    if not cleaned or _is_bad_title_token(cleaned):
        return fallback
    return _short_token(cleaned, max_chars=8, fallback=fallback)


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
        if token and not _is_bad_title_token(token) and not _is_genre_like_token(token):
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
            token = _reader_title_token(item, fallback="")
            if token and not _is_bad_title_token(token):
                return token
    return _reader_title_token(secondary or primary, fallback=secondary or primary)


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
        if (
            item
            and len(item) >= 2
            and item not in vague_tokens
            and not _is_bad_title_token(item)
            and not _is_genre_like_token(item)
        ):
            return item
    for item in candidates:
        token = _reader_title_token(item, fallback="")
        if token and len(token) >= 2 and token not in vague_tokens and not _is_bad_title_token(token):
            return token
    for item in candidates:
        if item and len(item) >= 2 and item not in vague_tokens and not _is_bad_title_token(item):
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
        if token and not _is_bad_title_token(token) and not _is_genre_like_token(token)
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
        if (
            token
            and len(token) <= 8
            and not _is_bad_title_token(token)
            and not _is_genre_like_token(token)
        ):
            return token
    for token in [identity, secondary, primary, *tags]:
        if token and len(token) <= 8 and not _is_bad_title_token(token):
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
        if (
            token
            and len(token) <= 8
            and not _is_bad_title_token(token)
            and not _is_genre_like_token(token)
        ):
            return token
    return identity or fallback


def _resolve_twist(tags: list[str], logline: str, fallback: str) -> str:
    for token in [*tags, *re.findall(r"[\u4e00-\u9fff]{2,10}", logline)]:
        if _is_bad_title_token(token):
            continue
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
    signals = _signal_tokens(profile) if not _is_english(_clean_text(profile.get("language"))) else {}
    language = _clean_text(profile.get("language"))
    is_english = _is_english(language)
    platform_groups: list[dict[str, Any]] = []
    if include_platform_matrix:
        platform_groups = _build_platform_matrix(
            profile,
            per_platform_count,
            is_english,
            target_platform_key=style.key,
        )
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
    candidate_evaluations = _build_candidate_evaluation_index(
        candidates,
        profile,
        style,
        signals,
    )
    if is_english:
        recommended_primary = _select_primary_candidate(candidates)
    else:
        model_primary = _model_title_primary(profile, style)
        # Structural demotion (2026-06-03): a mechanical template is never shipped
        # as the primary. When the model title is rejected, fall back to a clean
        # story-DNA title marked provisional, so the conception layer regenerates
        # it via LLM. Templates survive only as A/B suggestions in `candidates`.
        recommended_primary = model_primary or _provisional_primary_candidate(
            profile, style, candidates
        )
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
        "evaluation_standards": _title_evaluation_standards(),
        "evaluation_summary": _title_evaluation_summary(candidates),
        "candidate_evaluations": candidate_evaluations,
        "platform_groups": platform_groups,
        "style_profile": {
            "min_chars": style.min_chars,
            "max_chars": style.max_chars,
            "preferred_min_chars": style.preferred_min_chars,
            "preferred_max_chars": style.preferred_max_chars,
            "methodology_group": style.methodology_group,
            "design_rules": list(style.design_rules),
            "avoid_rules": list(style.avoid_rules),
        },
        "methodology_source": {
            "name": "中文小说平台近三个月榜单书名研究与命名方法论",
            "snapshot_date": "2026-06-01",
            "principle": "先定平台与卖点，再定句法与信息密度，最后做搜索性与复读性校验。",
            "score_weights": {
                "attraction": 30,
                "readability": 25,
                "platform_fit": 30,
                "searchability": 15,
            },
        },
        "generation_steps": [
            "提取故事 DNA：类型、二级分类、标签、主角身份、核心钩子、读者承诺。",
            "按平台矩阵分别生成书名，而不是只按当前目标平台出一组。",
            "每个平台输出 5 个强点击候选：短 IP 名、开局句、身份逆袭、关系钩子等分组。",
            "执行硬过滤：长度、重复、空泛词、平台风格错配、标题与内容承诺不一致。",
            "按吸引力、通顺度、平台匹配度、可搜索性四项打分并排序。",
        ],
        "scoring_dimensions": [
            "吸引力：一眼内给出题材、冲突或关系承诺。",
            "通顺度：一口气读完，不像关键词拼接。",
            "平台匹配度：长度、句法、标点和词汇符合目标平台。",
            "可搜索性：有独特锚点，降低撞名和空泛风险。",
        ],
        "candidates": candidates,
}


def _build_platform_matrix(
    profile: Mapping[str, Any],
    per_platform_count: int,
    is_english: bool,
    *,
    target_platform_key: str,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    global_seen: set[str] = set()
    blocked_titles = _blocked_title_keys(profile)
    requested = max(1, per_platform_count)
    strict_story_anchor = _has_explicit_title_anchor_groups(profile)
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
            row = _annotate_candidate_compliance(candidate, profile)
            if row.get("risk_blocked"):
                continue
            title = _clean_text(row.get("title"))
            if not title or title in global_seen:
                continue
            if title.casefold() in blocked_titles:
                continue
            _apply_platform_scope(row, style, target_platform_key)
            _apply_title_evaluation(row, profile, style)
            if not _matrix_candidate_is_usable(
                row,
                profile,
                strict_story_anchor=strict_story_anchor,
            ):
                continue
            row["platform_rank"] = len(selected) + 1
            row["recommendation"] = _platform_recommendation(style, len(selected))
            selected.append(row)
            global_seen.add(title)
            if len(selected) >= requested:
                break
        if len(selected) < requested and not is_english:
            for candidate in _build_story_methodology_topup_candidates(
                profile,
                style,
                requested + 12,
            ):
                row = _annotate_candidate_compliance(candidate, profile)
                if row.get("risk_blocked"):
                    continue
                title = _clean_text(row.get("title"))
                if not title or title in global_seen:
                    continue
                if title.casefold() in blocked_titles:
                    continue
                _apply_platform_scope(row, style, target_platform_key)
                _apply_title_evaluation(row, profile, style)
                if not _matrix_candidate_is_usable(
                    row,
                    profile,
                    strict_story_anchor=strict_story_anchor,
                ):
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


def _has_explicit_title_anchor_groups(profile: Mapping[str, Any]) -> bool:
    raw = profile.get("title_anchor_groups")
    if not isinstance(raw, Mapping):
        return False
    return any(_string_list(value) for value in raw.values())


def _matrix_candidate_is_usable(
    row: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    strict_story_anchor: bool,
) -> bool:
    title = _clean_text(row.get("title"))
    if not title or _unreadable_title_reason(title):
        return False
    evaluation = row.get("title_evaluation")
    decision = _clean_text(evaluation.get("decision")) if isinstance(evaluation, Mapping) else ""
    if strict_story_anchor:
        return decision == "pass" and _passes_story_anchor_contract(profile, title)
    return decision != "reject"


def _apply_platform_scope(
    row: dict[str, Any],
    style: PlatformTitleStyle,
    target_platform_key: str,
) -> None:
    if style.key == target_platform_key:
        row["platform_scope"] = "target_platform"
        row["scope_label"] = "目标平台"
        row["display_label"] = f"{style.label} · 目标平台"
        return
    if style.key == "general":
        row["platform_scope"] = "all_platform"
        row["scope_label"] = "全平台"
        row["display_label"] = "全平台"
        return
    row["platform_scope"] = "platform_matrix"
    row["scope_label"] = "平台矩阵"
    row["display_label"] = f"{style.label} · 平台矩阵"


def _platform_recommendation(style: PlatformTitleStyle, index: int) -> str:
    if index == 0:
        return "主推"
    if style.key in {"fanqie", "feilu"} and index in {1, 2}:
        return "广告测试"
    if style.key in {"qidian", "jinjiang"} and index == 1:
        return "A/B测试"
    return "备选"


def _build_current_title_candidate(
    profile: Mapping[str, Any],
    style: PlatformTitleStyle,
    is_english: bool,
) -> dict[str, Any]:
    title = _clean_text(profile.get("primary_title"))
    if not title:
        return {}
    signals = {} if is_english else _signal_tokens(profile)
    if is_english:
        label_fields = _candidate_label_fields(style)
        return {
            "id": 1,
            "title": title,
            "subtitle": _clean_text(profile.get("logline")),
            "angle": f"{style.label}｜Current main title",
            "recommendation": "Primary",
            "platform": style.key,
            "platform_label": style.label,
            **label_fields,
            "pattern": "Current main title",
            "score": 80,
            "score_breakdown": {},
            "fit_notes": ["Use current title if it passes title evaluation."],
        }
    label_fields = _candidate_label_fields(style)
    return {
        "id": 1,
        "title": title,
        "subtitle": _subtitle_for(title, style, signals),
        "angle": f"{style.label}｜当前主书名校准",
        "recommendation": "主推",
        "platform": style.key,
        "platform_label": style.label,
        **label_fields,
        "pattern": "当前主书名校准",
        "score": _score_candidate(title, style, signals),
        "score_breakdown": _score_candidate_breakdown(title, style, signals),
        "reader_review": _reader_title_review(title, style, signals),
        "fit_notes": _fit_notes(title, style, signals),
    }


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


def evaluate_platform_title_candidate(
    profile: Mapping[str, Any],
    title: str,
    *,
    target_platform: str | None = None,
) -> dict[str, Any]:
    style = resolve_title_style(target_platform or _clean_text(profile.get("target_platform")))
    signals = _signal_tokens(profile)
    return _evaluate_title_candidate(_clean_text(title), style, signals, profile)


# ---------------------------------------------------------------------------
# P2 · LLM 真改写 (2026-06-03)
#
# These helpers are pure/sync so the *policy* (when to revise, what to ask, how
# to validate) is testable without an LLM. The actual model call lives in the
# conception layer, which owns the session/settings. A clean concise IP name is
# never sent for revision — we only optimise genuinely weak titles, so a good
# model title like 「烬骨登天录」 is preserved rather than barker-ised.
# ---------------------------------------------------------------------------


def should_revise_primary_title(candidate: Mapping[str, Any]) -> bool:
    """True when the selected primary title warrants an LLM revision pass.

    Two cases trigger revision:

    1. **Mechanical template fallback** — when the model's own title was rejected,
       selection falls back to a `_platform_template_specs` candidate. Those are
       genre-mismatch / token-repetition prone (e.g. a 玄幻 book becoming
       「灭门遗孤神探」), and a short template can masquerade as a clean IP name, so
       we regenerate from the story DNA regardless of its surface shape.
    2. **The model's own title, but weak** — a non-passing title that is not a
       clean concise IP name.

    Story-derived public-emotion bridge titles and clean model IP names are left
    untouched.
    """

    if not isinstance(candidate, Mapping):
        return False
    title = _clean_text(candidate.get("title"))
    if not title:
        return False
    evaluation = candidate.get("title_evaluation")
    decision = (
        _clean_text(evaluation.get("decision")) if isinstance(evaluation, Mapping) else ""
    )
    pattern = _clean_text(candidate.get("pattern"))
    if pattern.startswith("公共情绪桥"):
        # Story-derived public-emotion bridge titles are trusted as-is.
        return False
    if pattern != "当前主书名校准":
        # Mechanical template fallback → the model title was rejected; templates
        # are unreliable, so always regenerate from story DNA.
        return True
    # The model's own title: revise only weak ones; protect clean IP names.
    if decision == "pass":
        return False
    if _is_concise_ip_name(title):
        return False
    return decision in {"revise", "reject"}


def build_title_revision_messages(
    profile: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    target_platform: str | None = None,
) -> tuple[str, str] | None:
    """Build (system, user) prompts to LLM-revise a weak primary title.

    Returns ``None`` when no revision is warranted. The prompt is grounded in the
    story DNA (logline / reader promise / non-genre tags / identity) and the
    platform's style rules — never the genre taxonomy.
    """

    if not should_revise_primary_title(candidate):
        return None
    style = resolve_title_style(
        target_platform or _clean_text(profile.get("target_platform"))
    )
    original = _clean_text(candidate.get("title"))
    logline = _compact_text(profile.get("logline") or profile.get("short_intro"))
    promise_values = _string_list(profile.get("reader_promise"))
    promise = _compact_text(promise_values[0] if promise_values else profile.get("reader_promise"))
    signals = _signal_tokens(profile)
    story_tags = [
        tag
        for tag in _string_list(profile.get("tags"))
        if tag and not _is_genre_like_token(tag) and tag not in _GENRE_LABEL_WORDS
    ][:5]
    evaluation = candidate.get("title_evaluation")
    revision_hint = ""
    if isinstance(evaluation, Mapping):
        feedback = evaluation.get("feedback")
        if isinstance(feedback, Mapping):
            revision_hint = _clean_text(feedback.get("revision_prompt"))

    system_prompt = (
        f"你是中文网络小说{style.label}平台的资深起名编辑。"
        "只依据故事内容（一句话钩子、读者承诺、人物身份、情节标签）来起书名，"
        "绝不能把题材分类词（如玄幻/悬疑/修仙/言情）当作书名内容。"
        f"输出必须满足：长度 {style.min_chars}-{style.max_chars} 字；"
        "单行；不带书名号、引号或解释；只输出一个最终书名。"
        + ("；".join(["", *style.design_rules][:4]) if style.design_rules else "")
    )
    user_lines = [
        f"原书名：{original}",
        f"一句话钩子：{logline or '（无）'}",
        f"读者承诺：{promise or '（无）'}",
        f"主角身份：{signals.get('identity') or '（无）'}",
        f"情节标签：{'、'.join(story_tags) if story_tags else '（无）'}",
    ]
    if revision_hint:
        user_lines.append(f"待改进点：{revision_hint}")
    user_lines.append("请据此产出一个更有点击力、且贴合平台口播风格的书名，只输出书名本身。")
    return system_prompt, "\n".join(user_lines)


def _stringify_story_value(value: Any, *, max_chars: int = 120) -> str:
    if isinstance(value, str):
        text = _compact_text(value)
    elif isinstance(value, Mapping):
        parts = [
            _compact_text(item)
            for item in value.values()
            if isinstance(item, str) and _compact_text(item)
        ]
        text = "；".join(parts[:4])
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts = [_compact_text(item) for item in value if isinstance(item, str)]
        text = "；".join([part for part in parts if part][:6])
    else:
        text = _compact_text(value)
    return text[:max_chars].rstrip()


def _story_title_dna(profile: Mapping[str, Any]) -> dict[str, str]:
    raw = profile.get("story_title_dna")
    dna = raw if isinstance(raw, Mapping) else {}
    fallback_signals = _signal_tokens(profile)
    return {
        "protagonist": _stringify_story_value(
            dna.get("protagonist") or fallback_signals.get("protagonist"), max_chars=24
        ),
        "identity": _stringify_story_value(
            dna.get("identity") or fallback_signals.get("identity"), max_chars=36
        ),
        "opening": _stringify_story_value(
            dna.get("opening") or dna.get("opening_pressure") or profile.get("logline"),
            max_chars=90,
        ),
        "central_action": _stringify_story_value(
            dna.get("central_action") or dna.get("signature_action"), max_chars=72
        ),
        "conflict": _stringify_story_value(
            dna.get("conflict") or dna.get("antagonist_pressure"), max_chars=72
        ),
        "stakes": _stringify_story_value(dna.get("stakes") or dna.get("cost"), max_chars=72),
        "payoff": _stringify_story_value(dna.get("payoff") or dna.get("volume_goal"), max_chars=90),
    }


def _story_anchor_groups(profile: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = profile.get("title_anchor_groups")
    groups: dict[str, list[str]] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            values = [
                _clean_text(item)
                for item in _string_list(value)
                if 2 <= len(_clean_text(item)) <= 12
                and not _is_genre_like_token(_clean_text(item))
            ]
            if values:
                groups[str(key)] = values[:8]
    if groups:
        return groups
    dna = _story_title_dna(profile)
    return {
        "identity": [
            token
            for token in (
                _short_token(dna.get("identity", ""), max_chars=8),
                _short_token(dna.get("protagonist", ""), max_chars=4),
            )
            if token and not _is_genre_like_token(token)
        ],
        "action": [
            token
            for token in (
                _short_token(dna.get("central_action", ""), max_chars=8),
                _short_token(dna.get("payoff", ""), max_chars=8),
            )
            if token and not _is_genre_like_token(token)
        ],
        "stakes": [
            token
            for token in (
                _short_token(dna.get("stakes", ""), max_chars=8),
                _short_token(dna.get("conflict", ""), max_chars=8),
            )
            if token and not _is_genre_like_token(token)
        ],
    }


def _contains_any(text: str, values: Sequence[str]) -> bool:
    return any(value and value in text for value in values)


_GENERIC_TITLE_ACTION_TERMS = {"签发", "签出", "签", "指出", "提交", "破局", "翻盘", "通关"}
_MEANINGFUL_TITLE_ACTION_MARKERS = (
    "强制复检",
    "复检",
    "反杀",
    "扣分",
    "越权",
    "交叉",
    "违规",
    "台账",
    "豁免",
)


def _meaningful_story_action_terms(action_terms: Sequence[str]) -> list[str]:
    meaningful = [
        term
        for term in (_clean_text(item) for item in action_terms)
        if term
        and term not in _GENERIC_TITLE_ACTION_TERMS
        and (
            any(marker in term for marker in _MEANINGFUL_TITLE_ACTION_MARKERS)
            or len(term) >= 4
        )
    ]
    return meaningful or [
        term
        for term in (_clean_text(item) for item in action_terms)
        if term and term not in _GENERIC_TITLE_ACTION_TERMS
    ]


def _passes_story_anchor_contract(profile: Mapping[str, Any], title: str) -> bool:
    raw_groups = profile.get("title_anchor_groups")
    if not isinstance(raw_groups, Mapping):
        # The ordinary conception-time path may not have approved planning
        # artifacts yet. Only enforce story-anchor groups when the caller has
        # supplied them explicitly from planning outputs.
        return True
    groups = _story_anchor_groups(profile)
    if not groups:
        return True
    text = _clean_text(title)
    identity_terms = groups.get("identity") or groups.get("entry") or []
    action_terms = groups.get("action") or []
    stakes_terms = groups.get("stakes") or groups.get("payoff") or []
    object_terms = groups.get("object") or []
    meaningful_action_terms = _meaningful_story_action_terms(action_terms)
    has_story_subject = _contains_any(
        text,
        [*identity_terms, *meaningful_action_terms, *object_terms, *stakes_terms],
    )
    has_story_motion = _contains_any(
        text,
        [*meaningful_action_terms, *stakes_terms, *object_terms],
    )
    return has_story_subject and has_story_motion


def _story_grounded_title_score(
    profile: Mapping[str, Any],
    title: str,
    *,
    target_platform: str | None = None,
) -> int:
    """Rank valid story-grounded titles by reader-facing transmission.

    Validation answers "may this title be used"; this score answers "which
    usable title is more likely to make a reader click". It deliberately
    rewards protagonist/action/stakes/contrast over terse object labels.
    """

    text = _clean_text(title)
    if not text:
        return -100
    style = resolve_title_style(
        target_platform or _clean_text(profile.get("target_platform"))
    )
    groups = _story_anchor_groups(profile)
    identity_terms = groups.get("identity") or []
    action_terms = groups.get("action") or []
    object_terms = groups.get("object") or []
    stakes_terms = groups.get("stakes") or []
    score = 0
    cjk_len = len(re.findall(r"[一-鿿]", text))
    if style.preferred_min_chars <= cjk_len <= style.preferred_max_chars:
        score += 18
    elif style.min_chars <= cjk_len <= style.max_chars:
        score += 8
    if cjk_len <= 8 and not re.search(r"[我你他她谁？?：:，,]", text):
        score -= 12
    if text.startswith("我") or "我用" in text or "我把" in text:
        score += 22
    elif "我偏要" in text or "我也要" in text:
        score += 16
    elif _contains_any(text, identity_terms):
        score += 14
    if _contains_any(text, action_terms):
        score += 18
    if _contains_any(text, object_terms):
        score += 12
    if _contains_any(text, stakes_terms):
        score += 14
    if re.search(r"48小时|签约前|开局|倒计时|只准|只能|不让|越权|却|偏要|逼出|钉死|反杀", text):
        score += 16
    if re.search(r"[？?：:，,]", text):
        score += 6
    if re.search(r"(报告|台账|执照|清单|条款)[\u4e00-\u9fff]{0,3}(钉死|逼出|触发|扣|反杀)", text):
        score += 6
    if re.search(r"钉死超标|报告超标|复检报告$|把超标写进", text):
        score -= 14
    if re.search(r"之书|传说|逆袭录|登天录$", text) and not _contains_any(text, object_terms):
        score -= 18
    return score


def _story_grounded_fallback_title_candidates(profile: Mapping[str, Any]) -> list[str]:
    """Generate a small set of story-action candidates from planning anchors."""

    groups = _story_anchor_groups(profile)
    action_terms = groups.get("action") or []
    object_terms = groups.get("object") or []
    stakes_terms = groups.get("stakes") or []
    has = lambda term: term in [*action_terms, *object_terms, *stakes_terms]  # noqa: E731
    candidates: list[str] = []
    if has("验房报告") and has("强制复检"):
        candidates.append("我用验房报告逼出强制复检")
    if has("指出违规") and has("强制复检"):
        candidates.append("只准指出违规，我偏要签强制复检")
    if has("48小时") and (has("写字楼") or has("强制复检")):
        candidates.append("48小时，我把写字楼验到强制复检")
    if has("执照扣分") and has("强制复检"):
        candidates.append("执照扣分，我也要签强制复检")
    if has("岗位说明书") and has("豁免清单"):
        candidates.append("岗位说明书刷出豁免清单")
    return _dedupe_strings(candidates)


def _revised_title_candidates(raw: str) -> list[str]:
    text = _clean_text(raw)
    if not text:
        return []
    candidates: list[str] = []
    try:
        import json as _json

        parsed = _json.loads(text)
        if isinstance(parsed, str):
            candidates.append(parsed)
        elif isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes, bytearray)):
            candidates.extend(str(item) for item in parsed)
        elif isinstance(parsed, Mapping):
            for key in ("title", "best_title", "recommended_title"):
                if parsed.get(key):
                    candidates.append(str(parsed[key]))
            value = parsed.get("candidates")
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                candidates.extend(str(item) for item in value)
    except Exception:
        pass
    for line in re.split(r"[\r\n]+", text):
        line = re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", line).strip()
        if line:
            candidates.append(line)
    if not candidates:
        candidates.append(text)
    return _dedupe_strings(candidates)


def build_story_grounded_title_revision_messages(
    profile: Mapping[str, Any],
    *,
    current_title: str,
    target_platform: str | None = None,
    reason: str = "",
) -> tuple[str, str]:
    """Build a planning-grounded title revision prompt.

    Unlike the conception-time prompt, this path expects story_title_dna and
    title_anchor_groups assembled from approved planning artifacts. It asks for
    multiple candidates so validation can reject unreadable first attempts
    without falling back to a broken title.
    """

    style = resolve_title_style(
        target_platform or _clean_text(profile.get("target_platform"))
    )
    dna = _story_title_dna(profile)
    groups = _story_anchor_groups(profile)
    anchor_line = "；".join(
        f"{key}={ '、'.join(values) }" for key, values in groups.items() if values
    )
    system_prompt = (
        f"你是中文网络小说{style.label}平台的资深起名编辑。"
        "必须根据已批准的大纲和故事动作命名，不得根据题材标签命名。"
        "标题必须读得通，动词和宾语要自然，不能把关键词硬拼。"
        "如果涉及报告/清单/台账/执照，只能使用签出、写进、钉死、触发复检、扣分等符合故事逻辑的动作，"
        "禁止“不开整改单”这类动宾不通表达。"
        f"长度 {style.min_chars}-{style.max_chars} 字。"
        "输出 5 个候选，每行一个书名，不要解释。"
    )
    user_lines = [
        f"当前坏标题：{_clean_text(current_title)}",
        f"修订原因：{reason or '当前标题题材化、关键词化或可读性不足'}",
        f"主角：{dna.get('protagonist') or '（无）'}",
        f"主角身份：{dna.get('identity') or '（无）'}",
        f"开局压力：{dna.get('opening') or '（无）'}",
        f"核心动作：{dna.get('central_action') or '（无）'}",
        f"主要对抗：{dna.get('conflict') or '（无）'}",
        f"代价/爽点：{dna.get('stakes') or '（无）'}",
        f"卷目标/回报：{dna.get('payoff') or '（无）'}",
        f"标题必须命中的故事锚点：{anchor_line or '（无）'}",
        "候选结构必须覆盖：1个第一人称行动句、1个开局压力句、1个规则反杀句、1个职业反差句、1个短IP名。",
        "优先让读者一眼看到：谁在做什么、被谁误解/压制、动作带来什么回报或代价。",
        "硬要求：不要出现玄幻/修真/都市/升级流等题材货架词；不要用案卷/神探等错配词；不要输出不成句的词组。",
    ]
    return system_prompt, "\n".join(user_lines)


def finalize_revised_title(
    profile: Mapping[str, Any],
    original_title: str,
    revised_raw: str,
    *,
    target_platform: str | None = None,
) -> tuple[str, bool]:
    """Validate an LLM-revised title and decide whether to adopt it.

    Returns ``(adopted_title, was_revised)``. The revised title is adopted only
    when it is well-formed, free of genre labels, within platform length, and not
    evaluated as a reject. Otherwise the original (story-derived) title stands.
    """

    original = _clean_text(original_title)
    accepted: list[tuple[int, str]] = []
    candidate_pool = [
        *_revised_title_candidates(revised_raw),
        *_story_grounded_fallback_title_candidates(profile),
    ]
    for candidate in candidate_pool:
        revised = _clean_text(candidate)
        if not revised:
            continue
        revised = re.sub(
            r"^[《「\"'】\[\(（\s]+|[》」\"'】\]\)）\s]+$", "", revised
        ).strip()
        revised = revised.strip("：:，,。.！!？?；; 、")
        if not revised or revised == original:
            continue
        style = resolve_title_style(
            target_platform or _clean_text(profile.get("target_platform"))
        )
        cjk_len = len(re.findall(r"[一-鿿]", revised))
        if cjk_len < max(2, style.min_chars) or cjk_len > style.max_chars:
            continue
        if (
            _title_uses_genre_label(revised)
            or _is_bad_title_token(revised)
            or _unreadable_title_reason(revised)
        ):
            continue
        if not _passes_story_anchor_contract(profile, revised):
            continue
        evaluation = evaluate_platform_title_candidate(
            profile, revised, target_platform=target_platform
        )
        if evaluation.get("decision") == "reject":
            continue
        accepted.append(
            (
                _story_grounded_title_score(
                    profile, revised, target_platform=target_platform
                ),
                revised,
            )
        )
    if accepted:
        accepted.sort(key=lambda item: item[0], reverse=True)
        return accepted[0][1], True
    return original, False


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


def _model_title_primary(
    profile: Mapping[str, Any],
    style: PlatformTitleStyle,
) -> dict[str, Any]:
    """Return the model/human title as the recommended primary (F1, 2026-06-03).

    The model title is built from the full story DNA and is the default primary.
    It wins over mechanical template candidates unless it is rejected outright;
    platform-specific polishing is delegated to the downstream LLM revision step,
    not to template override. Returns ``{}`` when there is no usable title or it
    was rejected, so the caller falls back to template-based selection.

    This deliberately does NOT mutate the per-platform candidate list, so the
    A/B display order and candidate count contracts are preserved.
    """

    if not _clean_text(profile.get("primary_title")):
        return {}
    current = _build_current_title_candidate(profile, style, is_english=False)
    if not current:
        return {}
    _apply_platform_scope(current, style, style.key)
    _apply_title_evaluation(current, profile, style)
    if current.get("title_evaluation", {}).get("decision") != "pass":
        return {}
    return current


def _strip_genre_words(text: str) -> str:
    """Remove genre taxonomy words and separators, salvaging a story title core."""

    cleaned = _clean_text(text)
    for word in _GENRE_LABEL_WORDS:
        cleaned = cleaned.replace(word, "")
    cleaned = re.sub(r"[·／、,，;；:：\s/|]+", "", cleaned).strip()
    return cleaned


def _is_valid_fallback_title(text: str, style: PlatformTitleStyle) -> bool:
    token = _clean_text(text)
    if not token or _is_bad_title_token(token) or _title_uses_genre_label(token):
        return False
    if _unreadable_title_reason(token):  # reuse the shared readability gate
        return False
    if re.search(r"([一-鿿]{2,4})\1", token):  # adjacent duplicate phrase
        return False
    cjk = len(re.findall(r"[一-鿿]", token))
    return 2 <= cjk <= style.max_chars


def build_story_dna_fallback_title(
    profile: Mapping[str, Any],
    *,
    target_platform: str | None = None,
) -> str:
    """Deterministic, genre-free fallback title built only from story DNA.

    Used as the *provisional* primary when the model title is rejected and the
    LLM revision is unavailable/disabled — a clean story-derived name instead of
    a mechanical template like 「灭门遗孤神探」. Returns ``""`` when nothing usable.

    Priority: (1) salvage the model's own title by stripping genre labels;
    (2) the most distinctive story-DNA noun (object / threat / entry / identity).
    """

    style = resolve_title_style(
        target_platform or _clean_text(profile.get("target_platform"))
    )
    salvaged = _strip_genre_words(profile.get("primary_title"))
    if _is_valid_fallback_title(salvaged, style):
        return salvaged
    signals = _signal_tokens(profile)
    for key in ("object", "threat", "entry", "identity", "hook"):
        token = _clean_text(signals.get(key))
        if _is_valid_fallback_title(token, style):
            return token
    return ""


def _provisional_primary_candidate(
    profile: Mapping[str, Any],
    style: PlatformTitleStyle,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the provisional primary when the model title was rejected.

    Prefers a clean story-DNA fallback title (marked provisional so the
    conception layer regenerates it via LLM). Only when no DNA fallback exists
    does it fall back — as a true last resort — to the best template candidate.
    """

    fallback = build_story_dna_fallback_title(profile, target_platform=style.key)
    if fallback:
        signals = _signal_tokens(profile)
        candidate = {
            "id": 0,
            "title": fallback,
            "subtitle": _subtitle_for(fallback, style, signals),
            "angle": f"{style.label}｜故事DNA兜底",
            "recommendation": "主推",
            "platform": style.key,
            "platform_label": style.label,
            **_candidate_label_fields(style),
            "pattern": "故事DNA兜底",
            "platform_scope": "target_platform",
            "scope_label": "目标平台",
            "provisional": True,
            "requires_llm_revision": True,
            "score": _score_candidate(fallback, style, signals),
            "score_breakdown": _score_candidate_breakdown(fallback, style, signals),
            "reader_review": _reader_title_review(fallback, style, signals),
            "fit_notes": _fit_notes(fallback, style, signals),
        }
        _apply_title_evaluation(candidate, profile, style)
        return candidate
    chosen = _select_primary_candidate(candidates)
    if chosen:
        chosen = dict(chosen)
        chosen["provisional"] = True
        chosen["requires_llm_revision"] = True
    return chosen


def _select_primary_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    # Fallback selection when the model title was rejected (or English path):
    # `_model_title_primary` already gave the story-derived title first refusal.
    for candidate in candidates:
        evaluation = candidate.get("title_evaluation")
        if (
            candidate.get("platform_scope") == "target_platform"
            and isinstance(evaluation, Mapping)
            and evaluation.get("decision") == "pass"
        ):
            return dict(candidate)
    for candidate in candidates:
        evaluation = candidate.get("title_evaluation")
        if isinstance(evaluation, Mapping) and evaluation.get("decision") == "pass":
            return dict(candidate)
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
                "score_breakdown": _score_candidate_breakdown(title, style, signals),
                "reader_review": _reader_title_review(title, style, signals),
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
    blocked_titles = _blocked_title_keys(profile)
    for candidate in candidates:
        title = _clean_text(candidate.get("title"))
        if not title or title in seen:
            continue
        if title.casefold() in blocked_titles:
            continue
        row = _annotate_candidate_compliance(candidate, profile)
        if row.get("risk_blocked"):
            continue
        style = PLATFORM_TITLE_STYLES.get(
            _clean_text(row.get("platform")),
            resolve_title_style(profile.get("target_platform") if isinstance(profile, Mapping) else None),
        )
        _apply_title_evaluation(row, profile, style)
        if not _matrix_candidate_is_usable(
            row,
            profile,
            strict_story_anchor=_has_explicit_title_anchor_groups(profile),
        ):
            continue
        rows.append(row)
        seen.add(title)
        if len(rows) >= candidate_count:
            break
    return _renumber_candidates(rows)


def _blocked_title_keys(profile: Mapping[str, Any]) -> set[str]:
    titles = [
        *_string_list(profile.get("previous_titles")),
        _clean_text(profile.get("previous_title")),
    ]
    metadata = profile.get("metadata")
    if isinstance(metadata, Mapping):
        titles.extend(_string_list(metadata.get("previous_titles")))
        titles.append(_clean_text(metadata.get("previous_title")))
    return {_clean_text(title).casefold() for title in titles if _clean_text(title)}


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


def _first_story_term(*groups: Sequence[str], fallback: str = "") -> str:
    for group in groups:
        for item in group:
            token = _clean_text(item)
            if token and not _is_bad_title_token(token) and not _is_genre_like_token(token):
                return token
    return fallback


def _story_term_at(group: Sequence[str], index: int, *, fallback: str) -> str:
    values = [
        _clean_text(item)
        for item in group
        if _clean_text(item)
        and not _is_bad_title_token(_clean_text(item))
        and not _is_genre_like_token(_clean_text(item))
    ]
    if len(values) > index:
        return values[index]
    return fallback


def _build_story_methodology_topup_candidates(
    profile: Mapping[str, Any],
    style: PlatformTitleStyle,
    candidate_count: int,
) -> list[dict[str, Any]]:
    """Fill platform-matrix gaps with story-DNA candidates.

    These candidates are not a replacement for platform templates. They are a
    bounded backstop after filtering removes too many weak/topic-like rows, so
    the method contract "each platform has 5 candidates" remains true without
    accepting rejected titles.
    """

    signals = _signal_tokens(profile)
    groups = _story_anchor_groups(profile)
    identity_terms = groups.get("identity", [])
    action_terms = groups.get("action", [])
    object_terms = groups.get("object", [])
    stakes_terms = groups.get("stakes", [])
    meaningful_action_terms = _meaningful_story_action_terms(action_terms)
    identity = _first_story_term(
        identity_terms,
        [signals.get("identity", "")],
        fallback="主角",
    )
    protagonist = _story_term_at(identity_terms, 1, fallback=identity)
    action = _first_story_term(
        meaningful_action_terms,
        action_terms,
        [signals.get("action", "")],
        fallback="破局",
    )
    rule_action = _story_term_at(meaningful_action_terms, 1, fallback=action)
    signature_action = _story_term_at(action_terms, 2, fallback=action)
    obj = _first_story_term(
        object_terms,
        [signals.get("object", "")],
        fallback="关键证据",
    )
    object_two = _story_term_at(object_terms, 1, fallback=obj)
    object_three = _story_term_at(object_terms, 2, fallback=obj)
    time_or_place = _story_term_at(object_terms, 3, fallback=obj)
    stakes = _first_story_term(
        stakes_terms,
        [signals.get("threat", ""), signals.get("hook", "")],
        fallback="危机",
    )
    stakes_two = _story_term_at(stakes_terms, 1, fallback=stakes)
    stakes_three = _story_term_at(stakes_terms, 2, fallback=stakes)

    platform_titles: dict[str, list[tuple[str, str, str]]] = {
        "fanqie": [
            (f"开局{stakes}，我用{obj}逼出{action}", "故事锚点补齐：开局强钩子", "广告测试"),
            (f"只准{rule_action}，我偏要签{action}", "故事锚点补齐：规则反差", "广告测试"),
            (f"让你查{stakes_three}，我用{object_three}反杀", "故事锚点补齐：命令反差", "备选"),
            (f"{time_or_place}{identity}，我逼出{action}", "故事锚点补齐：身份开场", "备选"),
            (f"别人怕{stakes}，我签{action}", "故事锚点补齐：对照爽点", "备选"),
        ],
        "qidian": [
            (f"{obj}纪事", "故事锚点补齐：短 IP", "备选"),
            (f"{action}录", "故事锚点补齐：结果 IP", "备选"),
            (f"{object_three}秘档", "故事锚点补齐：物件 IP", "备选"),
            (f"{stakes}局", "故事锚点补齐：代价 IP", "备选"),
            (f"{identity}巡检录", "故事锚点补齐：职业 IP", "备选"),
        ],
        "qimao": [
            (f"{obj}破局", "故事锚点补齐：器物爽点", "主推"),
            (f"{action}归来", "故事锚点补齐：结果爽点", "备选"),
            (f"{identity}硬刚{stakes_three}", "故事锚点补齐：身份反差", "广告测试"),
            (f"{object_three}反杀", "故事锚点补齐：反杀爽点", "A/B测试"),
            (f"{time_or_place}{action}局", "故事锚点补齐：期限事件", "备选"),
        ],
        "jinjiang": [
            (f"{time_or_place}倒计时", "故事锚点补齐：情绪期限", "主推"),
            (f"{obj}今天说真话了吗", "故事锚点补齐：轻口语", "备选"),
            (f"只准{rule_action}", "故事锚点补齐：规则压力", "A/B测试"),
            (f"{protagonist}不肯{stakes_three}", "故事锚点补齐：人物选择", "备选"),
            (f"{action}前夜", "故事锚点补齐：期限意象", "备选"),
        ],
        "qq_read": [
            (f"开局{obj}，我签出{action}", "故事锚点补齐：开局+结果", "主推"),
            (f"让你查{stakes_three}，你把{stakes}坐实？", "故事锚点补齐：命令反差", "广告测试"),
            (f"说好{rule_action}，你怎么签{action}了？", "故事锚点补齐：对话问句", "广告测试"),
            (f"别人被{stakes}逼退，我用{object_three}翻盘", "故事锚点补齐：对照爽点", "A/B测试"),
            (f"{identity}当众签出{action}", "故事锚点补齐：公开掉马", "备选"),
            (f"开局{stakes}，我用{obj}翻盘", "故事锚点补齐：开局对照", "备选"),
            (f"让你守{stakes_two}，我签出{action}", "故事锚点补齐：边界反差", "备选"),
        ],
        "hongxiu": [
            (f"{obj}藏住真相", "故事锚点补齐：物件悬念", "备选"),
            (f"{action}那一天", "故事锚点补齐：事件日", "主推"),
            (f"{stakes_three}之后，{identity}不忍了", "故事锚点补齐：处境反转", "A/B测试"),
            (f"{stakes}也要{action}", "故事锚点补齐：代价选择", "备选"),
            (f"{object_three}里的{protagonist}", "故事锚点补齐：人物物件", "备选"),
            (f"{stakes}那一天", "故事锚点补齐：代价事件日", "备选"),
            (f"{stakes_two}倒计时", "故事锚点补齐：边界期限", "备选"),
            (f"{action}藏住真相", "故事锚点补齐：结果悬念", "备选"),
        ],
        "zhangyue": [
            (f"{obj}之前", "故事锚点补齐：留白", "主推"),
            (f"{action}前夜", "故事锚点补齐：期限留白", "备选"),
            (f"{object_three}来信", "故事锚点补齐：物象", "A/B测试"),
            (f"{stakes}旧影", "故事锚点补齐：代价意象", "备选"),
            (f"{time_or_place}之后", "故事锚点补齐：时间留白", "备选"),
            (f"{stakes_two}来信", "故事锚点补齐：边界物象", "备选"),
            (f"{stakes_three}旧影", "故事锚点补齐：冲突留白", "备选"),
        ],
        "douban": [
            (f"{action}之前", "故事锚点补齐：时间留白", "主推"),
            (f"{object_three}证词", "故事锚点补齐：物件证词", "A/B测试"),
            (f"{stakes}之前", "故事锚点补齐：代价留白", "备选"),
            (f"{stakes_three}证词", "故事锚点补齐：冲突证词", "备选"),
            (f"{time_or_place}证词", "故事锚点补齐：期限证词", "备选"),
        ],
        "17k": [
            (f"{obj}行", "故事锚点补齐：短硬行动", "主推"),
            (f"{action}令", "故事锚点补齐：号令感", "备选"),
            (f"{object_three}祭", "故事锚点补齐：物件意象", "备选"),
            (f"破{stakes_three}令", "故事锚点补齐：冲突号令", "A/B测试"),
            (f"{identity}第一{action}", "故事锚点补齐：地位承诺", "备选"),
            (f"{identity}破{stakes_three}", "故事锚点补齐：职业硬刚", "备选"),
            (f"{object_three}令", "故事锚点补齐：物件号令", "备选"),
        ],
        "feilu": [
            (f"开局{obj}在手，我逼出{action}", "故事锚点补齐：开局能力", "主推"),
            (f"让你查{stakes_three}，你把{object_three}签爆？", "故事锚点补齐：命令反差", "广告测试"),
            (f"{stakes}倒计时，我偏要签{action}", "故事锚点补齐：代价倒计时", "广告测试"),
            (f"{identity}开局硬刚{stakes}", "故事锚点补齐：身份硬刚", "备选"),
            (f"盘点{action}名场面，{stakes_three}破防", "故事锚点补齐：口播名场面", "备选"),
            (f"开局{identity}查{stakes_three}，我签出{action}", "故事锚点补齐：职业开局", "备选"),
            (f"别人验到{stakes}，我验出{action}", "故事锚点补齐：对照口播", "备选"),
            (f"让你只写{obj}，我逼出{action}", "故事锚点补齐：职责反差", "广告测试"),
        ],
        "zongheng": [
            (f"{action}秘录", "故事锚点补齐：核心事件", "主推"),
            (f"{object_three}秘录", "故事锚点补齐：物件长线", "备选"),
            (f"我在{time_or_place}签{action}", "故事锚点补齐：行动入口", "A/B测试"),
            (f"{stakes}生死局", "故事锚点补齐：代价危机", "备选"),
            (f"{identity}{signature_action}录", "故事锚点补齐：职业动作", "备选"),
            (f"{obj}秘录", "故事锚点补齐：物件秘录", "备选"),
            (f"{stakes_two}秘档", "故事锚点补齐：边界秘档", "备选"),
        ],
        "tadu": [
            (f"{action}秘档", "故事锚点补齐：事件秘档", "主推"),
            (f"{obj}归来", "故事锚点补齐：物件结果", "备选"),
            (f"{object_three}破局人", "故事锚点补齐：人物承诺", "A/B测试"),
            (f"{stakes}生死局", "故事锚点补齐：代价危机", "备选"),
            (f"{identity}第一案", "故事锚点补齐：职业案件", "备选"),
            (f"{stakes}秘档", "故事锚点补齐：代价秘档", "备选"),
            (f"{stakes_two}秘录", "故事锚点补齐：边界长线", "备选"),
            (f"{time_or_place}破局人", "故事锚点补齐：期限人物", "备选"),
        ],
        "general": [
            (f"{obj}回执", "故事锚点补齐：物件结果", "备选"),
            (f"{action}规则", "故事锚点补齐：规则钩子", "备选"),
            (f"我用{object_three}签{action}", "故事锚点补齐：行动承诺", "A/B测试"),
            (f"{stakes}倒计时", "故事锚点补齐：代价期限", "备选"),
            (f"{stakes_two}反杀局", "故事锚点补齐：反杀结果", "备选"),
            (f"{action}回执", "故事锚点补齐：结果回执", "备选"),
            (f"{object_three}规则", "故事锚点补齐：物件规则", "备选"),
        ],
    }
    common = [
        *platform_titles.get(style.key, []),
        (f"我用{obj}逼出{action}", "故事锚点补齐：第一人称行动", "备选"),
        (f"{obj}触发{action}", "故事锚点补齐：物件触发结果", "备选"),
        (f"只准{rule_action}，我偏要{action}", "故事锚点补齐：规则反差", "A/B测试"),
        (f"{identity}签出{stakes}", "故事锚点补齐：职业动作", "备选"),
        (f"{time_or_place}第一{identity}", "故事锚点补齐：地位承诺", "备选"),
        (f"{obj}反杀局", "故事锚点补齐：短硬结果", "备选"),
        (f"{stakes}倒计时", "故事锚点补齐：期限钩子", "备选"),
        (f"{stakes_two}破局人", "故事锚点补齐：人物承诺", "备选"),
    ]

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for title, pattern, recommendation in common:
        _append_candidate(rows, seen, title, pattern, recommendation, style, signals)
        if len(rows) >= candidate_count:
            break
    return rows


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
    if _is_low_quality_title(title, signals):
        return
    if len(title) < style.min_chars and title != signals.get("title"):
        return
    if len(title) > style.max_chars:
        return
    seen.add(title)
    score = _score_candidate(title, style, signals)
    score_breakdown = _score_candidate_breakdown(title, style, signals)
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
            "score_breakdown": score_breakdown,
            "reader_review": _reader_title_review(title, style, signals),
            "fit_notes": _fit_notes(title, style, signals),
        }
    )


def _is_low_quality_title(title: str, signals: Mapping[str, str]) -> bool:
    generic_tokens = {"黑科技创业死局", "都市悬疑", "能力回报", "系统化力量体系"}
    if title in generic_tokens:
        return True
    if _unreadable_title_reason(title):
        return True
    if re.search(r"[\[\]【】]", title):
        return True
    # T-0: a fabricated candidate must never use a genre taxonomy label as its
    # core. (The model's own title is built separately and is exempt.)
    if _title_uses_genre_label(title, signals):
        return True
    if any(marker in title for marker in BAD_TITLE_PART_MARKERS):
        return True
    if any(marker in title for marker in BAD_TITLE_FRAGMENT_MARKERS):
        return True
    if re.search(r"([\u4e00-\u9fff]{2,4})\1", title):
        return True
    for signal in signals.values():
        if signal and _is_bad_title_token(signal) and signal in title:
            return True
    chunks = [chunk for chunk in re.findall(r"[\u4e00-\u9fff]{2,8}", title) if len(chunk) >= 4]
    return any(title.count(chunk) > 1 for chunk in chunks)


def _reader_title_review(
    title: str,
    style: PlatformTitleStyle,
    signals: Mapping[str, str],
) -> dict[str, Any]:
    attraction_marks = (
        "我",
        "你",
        "他们",
        "全当",
        "以为",
        "越",
        "开局",
        "让你",
        "死局",
        "布局",
        "误解",
        "翻盘",
        "通关",
        "倒计时",
        "？",
    )
    story_marks = (
        signals.get("hook", ""),
        signals.get("object", ""),
        signals.get("threat", ""),
        signals.get("action", ""),
        "解释",
        "误解",
        "布局",
        "创业",
        "商战",
        "黑科技",
        "破局",
    )
    attraction_hits = [mark for mark in attraction_marks if mark and mark in title]
    story_hits = [
        mark
        for mark in story_marks
        if mark and len(mark) >= 2 and mark in title
    ]
    is_sentence_hook = bool(re.search(r"(我|你|他们).{1,12}(以为|全当|越|把|靠|用)", title))
    attraction_score = min(100, 45 + len(attraction_hits) * 12 + (18 if is_sentence_hook else 0))
    transmission_score = min(100, 40 + len(set(story_hits)) * 12)
    if len(title) < style.min_chars or len(title) > style.max_chars:
        attraction_score = max(0, attraction_score - 20)
        transmission_score = max(0, transmission_score - 10)
    return {
        "reader_attraction": {
            "passed": attraction_score >= 70,
            "score": attraction_score,
            "reason": (
                "有人物视角、反差或局势变化，具备首眼点击钩子。"
                if attraction_score >= 70
                else "更像题材/设定标签，首眼冲突还不够强。"
            ),
        },
        "story_transmission": {
            "passed": transmission_score >= 64,
            "score": transmission_score,
            "reason": (
                "标题能传出主角处境、核心误会或主要对抗。"
                if transmission_score >= 64
                else "读者不容易从标题判断这本书到底在讲什么。"
            ),
        },
    }


def _title_evaluation_standards() -> dict[str, Any]:
    return {
        "decision_levels": {
            "pass": "可直接作为平台候选或主推标题。",
            "revise": "有可用信息，但需要按反馈改写后再入选。",
            "reject": "不应进入候选池或不能作为该平台主推。",
        },
        "pass_conditions": [
            "对用户有首眼吸引力：存在人物动作、反差、误解、强处境、关系张力或明确爽点。",
            "能传导故事：读者能看出主角在做什么、遇到什么阻力、故事大概往哪里走。",
            "符合平台特性：长度、句法、标点、信息密度和频道词符合目标平台。",
            "不是题材评述、关键词拼接、内部标签、半句碎片或泛化套话。",
        ],
        "reject_conditions": [
            "只是在描述题材、类型或设定，没有人物动作和故事冲突。",
            "标题读不顺、像关键词拼接、包含内部字段或截断半句。",
            "明显套错平台，例如豆瓣/掌阅使用免费阅读长口播标题。",
            "承诺与 logline、读者承诺或前三章方向不一致。",
        ],
        "platform_fit_examples": {
            "qidian": "起点可接受短硬机制名或反常识短句，但需要有长期 IP 感或策略感。",
            "fanqie": "番茄/飞卢/QQ 阅读更适合开局句、反差句、命令句和结果承诺。",
            "jinjiang": "晋江优先关系张力、人物处境和情绪钩子。",
            "zhangyue_douban": "掌阅/豆瓣优先短、稳、留白，避免口播式强钩子。",
        },
    }


def _apply_title_evaluation(
    row: dict[str, Any],
    profile: Mapping[str, Any],
    style: PlatformTitleStyle,
) -> None:
    signals = _signal_tokens(profile)
    evaluation = _evaluate_title_candidate(
        _clean_text(row.get("title")),
        style,
        signals,
        profile,
    )
    row["reader_review"] = evaluation["checks"]
    row["title_evaluation"] = evaluation


def _build_candidate_evaluation_index(
    candidates: list[dict[str, Any]],
    profile: Mapping[str, Any],
    target_style: PlatformTitleStyle,
    signals: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    primary_title = _clean_text(profile.get("primary_title"))
    if primary_title:
        rows[primary_title] = _evaluate_title_candidate(
            primary_title,
            target_style,
            signals,
            profile,
        )
    for candidate in candidates:
        title = _clean_text(candidate.get("title"))
        if not title:
            continue
        evaluation = candidate.get("title_evaluation")
        if isinstance(evaluation, dict):
            rows[title] = evaluation
            continue
        style = PLATFORM_TITLE_STYLES.get(
            _clean_text(candidate.get("platform")),
            target_style,
        )
        rows[title] = _evaluate_title_candidate(title, style, signals, profile)
    return rows


def _title_evaluation_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"pass": 0, "revise": 0, "reject": 0}
    for candidate in candidates:
        evaluation = candidate.get("title_evaluation")
        if not isinstance(evaluation, Mapping):
            continue
        decision = _clean_text(evaluation.get("decision"))
        if decision in counts:
            counts[decision] += 1
    return {
        "decision_counts": counts,
        "feedback_loop": (
            "主推选择优先使用目标平台且 decision=pass 的标题；"
            "revise/reject 标题保留反馈原因和改写提示，避免反复人工试错。"
        ),
    }


def _evaluate_title_candidate(
    title: str,
    style: PlatformTitleStyle,
    signals: Mapping[str, str],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    reader_check = _evaluate_reader_attraction(title, style)
    story_check = _evaluate_story_transmission(title, signals, profile)
    platform_check = _evaluate_platform_fit(title, style)
    quality_check = _evaluate_title_quality(title, signals)
    checks = {
        "reader_attraction": reader_check,
        "story_transmission": story_check,
        "platform_fit": platform_check,
        "title_quality": quality_check,
    }
    failed = [key for key, item in checks.items() if not item["passed"]]
    if not failed:
        decision = "pass"
    elif "title_quality" in failed and len(failed) >= 2:
        decision = "reject"
    else:
        decision = "revise"
    return {
        "decision": decision,
        "checks": checks,
        "pass_conditions": [
            key for key, item in checks.items() if item["passed"]
        ],
        "fail_reasons": [
            item["reason"] for item in checks.values() if not item["passed"]
        ],
        "feedback": {
            "revision_prompt": _title_revision_prompt(title, style, failed, checks),
        },
    }


def _evaluate_reader_attraction(title: str, style: PlatformTitleStyle) -> dict[str, Any]:
    attraction_marks = (
        "我",
        "你",
        "他们",
        "全当",
        "以为",
        "越",
        "开局",
        "让你",
        "死局",
        "布局",
        "误解",
        "翻盘",
        "通关",
        "倒计时",
        "当众",
        "？",
        "逼出",
        "签出",
        "签发",
        "触发",
        "强制复检",
        "反杀",
        "扣分",
        "越权",
    )
    hits = [mark for mark in attraction_marks if mark in title]
    sentence_hook = bool(
        re.search(
            r"(我|你|他们|她|他).{0,16}(以为|全当|越|把|靠|用|说|签|逼|触发|反杀|钉死)",
            title,
        )
    )
    score = min(100, 42 + len(hits) * 10 + (20 if sentence_hook else 0))
    if style.key in {"zhangyue", "douban"} and ("，" in title or "？" in title):
        score = max(0, score - 18)
    # 凝练 IP 名通道：3-8 字、无标点、非题材标签的书名（如「烬骨登天录」「青云志」）
    # 本身就是合格的强 IP 标题，不应因缺少口播钩子词而被判废 (F2, 2026-06-03)。
    is_concise_ip_name = _is_concise_ip_name(title)
    passed = score >= 70 or is_concise_ip_name
    return {
        "passed": passed,
        "score": max(score, 72) if is_concise_ip_name else score,
        "reason": (
            "凝练 IP 名：意象凝练、可搜索、复读性强。"
            if (is_concise_ip_name and score < 70)
            else (
                "有人物视角、反差或局势变化，具备首眼点击钩子。"
                if passed
                else "更像题材/设定标签，缺少人物动作、反差或局势变化。"
            )
        ),
    }


def _evaluate_story_transmission(
    title: str,
    signals: Mapping[str, str],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    story_marks = {
        "解释",
        "误解",
        "布局",
        "创业",
        "商战",
        "黑科技",
        "卖黑科技",
        "破局",
        "翻案",
        "案",
        "旧账",
        "来信",
        "规则",
        "强制复检",
        "验房报告",
        "合规台账",
        "执照扣分",
        "越权",
        "反杀",
    }
    anchor_groups = _story_anchor_groups(profile)
    for values in anchor_groups.values():
        story_marks.update(token for token in values if token and len(token) >= 2)
    story_marks.update(
        token
        for token in (
            signals.get("hook", ""),
            signals.get("object", ""),
            signals.get("threat", ""),
            signals.get("action", ""),
            signals.get("identity", ""),
        )
        if token and len(token) >= 2
    )
    hits = [mark for mark in story_marks if mark and mark in title]
    has_actor_action = bool(
        re.search(
            r"(我|你|他们|她|他).{0,16}"
            r"(想|卖|查|救|全当|以为|解释|布局|翻盘|破局|说|用|把|靠|签|逼出|触发|反杀|钉死)",
            title,
        )
    )
    has_relation = bool(
        re.search(
            r"(越|只想|全当|以为|说好|让你|只准|只能|不让).{1,18}"
            r"(越|布局|误解|破局|翻盘|通关|挖出来|强制复检|反杀|逼出|扣分)",
            title,
        )
    )
    title_like_label = len(title) <= 10 and not has_actor_action and not has_relation
    score = min(
        100,
        36
        + len(set(hits)) * 10
        + (24 if has_actor_action else 0)
        + (16 if has_relation else 0),
    )
    if title_like_label:
        score = min(score, 58)
    is_concise_ip_name = _is_concise_ip_name(title) and not _is_low_quality_title(title, signals)
    passed = score >= 64 or is_concise_ip_name
    return {
        "passed": passed,
        "score": max(score, 66) if is_concise_ip_name else score,
        "reason": (
            "凝练 IP 名：用非题材标签的核心意象承载故事入口，适合由简介补足冲突。"
            if (is_concise_ip_name and score < 64)
            else (
                "标题能传出主角处境、核心误会或主要对抗。"
                if passed
                else "读者不容易从标题判断主角动作、故事冲突或大概剧情。"
            )
        ),
    }


def _evaluate_platform_fit(title: str, style: PlatformTitleStyle) -> dict[str, Any]:
    score = 55
    if style.min_chars <= len(title) <= style.max_chars:
        score += 18
    if style.preferred_min_chars <= len(title) <= style.preferred_max_chars:
        score += 12
    has_long_punctuation = "，" in title or "？" in title or "：" in title
    if style.key in {"fanqie", "feilu", "qq_read"} and has_long_punctuation:
        score += 10
    if style.key == "qidian" and len(title) <= style.max_chars:
        score += 8
    if style.key in {"zhangyue", "douban"} and has_long_punctuation:
        score -= 28
    if style.key in {"zhangyue", "douban"} and any(mark in title for mark in ("开局", "让你", "全当", "他们", "我只想")):
        score -= 22
    if len(title) > style.max_chars:
        score -= 30
    is_concise_ip_name = _is_concise_ip_name(title)
    passed = score >= 65 or is_concise_ip_name
    return {
        "passed": passed,
        "score": (
            max(max(0, min(100, score)), 68)
            if is_concise_ip_name
            else max(0, min(100, score))
        ),
        "reason": (
            "凝练 IP 名可作为主书名，平台卖点由简介/副标题补足。"
            if (is_concise_ip_name and score < 65)
            else (
                f"长度和句法符合{style.label}标题习惯。"
                if passed
                else f"长度、标点或信息密度不符合{style.label}标题习惯。"
            )
        ),
    }


def _evaluate_title_quality(title: str, signals: Mapping[str, str]) -> dict[str, Any]:
    bad = _is_low_quality_title(title, signals)
    return {
        "passed": not bad,
        "score": 100 if not bad else 35,
        "reason": (
            "标题不是内部标签、半句碎片或明显关键词拼接。"
            if not bad
            else "标题像题材评述、内部标签、半句碎片或关键词拼接。"
        ),
    }


def _title_revision_prompt(
    title: str,
    style: PlatformTitleStyle,
    failed: list[str],
    checks: Mapping[str, Mapping[str, Any]],
) -> str:
    if not failed:
        return ""
    instructions: list[str] = []
    if "reader_attraction" in failed:
        instructions.append("增加人物动作、反差、误解或强处境，让读者第一眼知道为什么要点。")
    if "story_transmission" in failed:
        instructions.append("补出人物动作和故事冲突，例如“我在做什么、别人误会/阻拦什么、局势如何升级”。")
    if "platform_fit" in failed:
        instructions.append(f"按{style.label}改写长度、标点和信息密度。")
    if "title_quality" in failed:
        instructions.append("删除题材评述、内部标签和关键词堆叠，改成一句能被读者复述的书名。")
    return f"《{title}》需要修改：" + "；".join(instructions)


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
    if key == "qq_read":
        return [
            (f"开局{s['entry']}，我靠{s['object']}{s['action']}", "开局+能力+结果承诺", "主推"),
            (f"{s['twist']}后，{s['identity']}不想再忍了", "前情反转+身份压力", "主推"),
            (f"让你查{s['entry']}，你把{s['threat']}送上热搜？", "命令反差+传播结果", "广告测试"),
            (f"别人被{s['threat']}逼退，我用{s['object']}翻盘", "对照爽点", "A/B测试"),
            (f"说好{s['hook']}，你怎么偷偷{s['action']}了？", "对话问句", "广告测试"),
            (f"{s['identity']}当众掉马后，全场都慌了", "身份掉马+公开场", "备选"),
        ]
    if key == "hongxiu":
        return [
            (f"重生后，{s['identity']}成了{s['hook']}心尖宠", "重生+关系逆转", "主推"),
            (f"替嫁后，{s['threat']}先动心了", "替嫁+关系反转", "主推"),
            (f"退婚当天，{s['identity']}把{s['object']}藏进东宫", "当天事件+古言身份", "A/B测试"),
            (f"{s['object']}藏娇", "古风短题", "备选"),
            (f"夫人今日也在{s['action']}", "轻口语女频钩子", "广告测试"),
            (f"他先失心，我后{s['action']}", "对仗情绪推进", "备选"),
        ]
    if key == "zhangyue":
        return [
            (f"{s['setting']}旧影", "场景+留白", "主推"),
            (f"长街有{s['object']}", "物象短题", "A/B测试"),
            (f"{s['origin']}故人", "人物关系含蓄呈现", "备选"),
            (f"月照{s['hook']}", "意象+核心钩子", "备选"),
            (f"{s['object']}来信", "物件+出版感", "主推"),
            (f"风从{s['setting']}来", "经典书名式节奏", "备选"),
        ]
    if key == "douban":
        return [
            (f"{s['object']}之前", "时间留白", "主推"),
            (f"{s['setting']}来信", "地域/物象", "主推"),
            (f"{s['object']}证词", "物件+悬疑", "A/B测试"),
            (f"旧{s['object']}", "物件象征", "备选"),
            (f"{s['setting']}以南", "地理诗性", "备选"),
            (f"无人{s['action']}", "存在感留白", "备选"),
        ]
    if key == "17k":
        return [
            (f"{s['object']}行", "短硬行动名", "主推"),
            (f"{s['setting']}猎人", "职业+场景", "A/B测试"),
            (f"{s['hook']}长歌", "升级史诗感", "备选"),
            (f"破{s['threat']}令", "号令感", "备选"),
            (f"{s['object']}祭", "东方玄奇意象", "备选"),
            (f"{s['setting']}第一{s['identity']}", "强标签地位承诺", "主推"),
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
    if key == "qq_read":
        return [
            f"离开{s['entry']}后，{s['identity']}杀回主场",
            f"开局被{s['threat']}盯上，我反手掀了{s['object']}",
            f"我在{s['setting']}直播{s['action']}，全网蹲我",
            f"听懂{s['object']}后，我把{s['threat']}送上审判席",
            f"说好只查{s['hook']}，怎么全员都破防了？",
            *base,
        ]
    if key == "hongxiu":
        return [
            f"惊{s['object']}",
            f"{s['identity']}不想成婚",
            f"{s['hook']}那夜，{s['threat']}认错人了",
            f"京门小{s['object']}",
            f"半路捡来的{s['identity']}太会演",
            *base,
        ]
    if key == "zhangyue":
        return [
            f"山河{s['object']}",
            f"庭前{s['hook']}",
            f"纸上{s['setting']}",
            f"她从{s['object']}来",
            f"星轨之下",
            *base,
        ]
    if key == "douban":
        return [
            f"雨停之前",
            f"北方来信",
            f"空椅子",
            f"夜色录",
            f"河流练习册",
            *base,
        ]
    if key == "17k":
        return [
            f"夜{s['object']}行",
            f"{s['setting']}旧事",
            f"武极{s['hook']}",
            f"北地{s['identity']}",
            f"逆火",
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
    score = sum(_score_candidate_breakdown(title, style, signals).values())
    return max(0, min(score, 100))


def _score_candidate_breakdown(
    title: str,
    style: PlatformTitleStyle,
    signals: Mapping[str, str],
) -> dict[str, int]:
    attraction = 12
    readability = 17
    platform_fit = 12
    searchability = 7

    if style.preferred_min_chars <= len(title) <= style.preferred_max_chars:
        readability += 5
        platform_fit += 8
    signal_tokens = (signals["hook"], signals["object"], signals["identity"])
    matched_signal_count = sum(1 for token in signal_tokens if token and token in title)
    if matched_signal_count:
        attraction += min(12, 6 + matched_signal_count * 3)
        searchability += min(5, matched_signal_count * 2)
    if re.search(r"(我|你|他们|她|他).{1,14}(以为|全当|越|把|靠|用|说|卖)", title):
        attraction += 12
        readability += 3
        searchability += 2
    if any(mark in title for mark in ("误解", "布局", "黑科技", "商战", "创业")):
        attraction += 5
        searchability += 3
    if "：" in title or "，" in title or "？" in title:
        if style.key in {"fanqie", "feilu", "qq_read", "hongxiu"}:
            platform_fit += 5
            attraction += 3
        elif style.key in {"douban", "zhangyue", "17k"}:
            platform_fit -= 4
            readability -= 2
        else:
            platform_fit += 1
    if style.key in {"qidian", "jinjiang"} and len(title) <= style.preferred_max_chars:
        platform_fit += 5
    fanqie_prefixes = ("开局", "我在", "让你", "全民", "综武", "娱乐", "盘点")
    if style.key in {"fanqie", "feilu"} and title.startswith(fanqie_prefixes):
        attraction += 6
        platform_fit += 6
    if style.key == "qq_read" and any(mark in title for mark in ("后", "当天", "重生", "离婚", "开局", "？")):
        attraction += 6
        platform_fit += 5
    qimao_tokens = ("巅峰", "神医", "归来", "下山", "第一", "寒门")
    if style.key == "qimao" and any(token in title for token in qimao_tokens):
        attraction += 5
        platform_fit += 5
    jinjiang_tokens = ("协议", "倒计时", "今天", "[", "？")
    if style.key == "jinjiang" and any(token in title for token in jinjiang_tokens):
        attraction += 5
        platform_fit += 5
    if style.key in {"douban", "zhangyue"} and len(title) <= style.preferred_max_chars:
        readability += 3
        platform_fit += 6
    if style.key == "17k" and len(title) <= 10:
        readability += 3
        platform_fit += 5
    if len(title) > style.preferred_max_chars + 6:
        readability -= 5
        platform_fit -= 5
    generic_tokens = {"天命", "归来", "成神", "逆袭", "破局", "规则", "类型", "故事"}
    if title in generic_tokens or any(title.endswith(token) and len(title) <= 4 for token in generic_tokens):
        searchability -= 4
        attraction -= 2
    if re.search(r"([\u4e00-\u9fff]{2,6})\1", title):
        readability -= 6
    return {
        "attraction": max(0, min(attraction, 30)),
        "readability": max(0, min(readability, 25)),
        "platform_fit": max(0, min(platform_fit, 30)),
        "searchability": max(0, min(searchability, 15)),
    }


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
            "score_breakdown": {
                "attraction": 20,
                "readability": 20,
                "platform_fit": 20,
                "searchability": 10,
            },
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
                "score_breakdown": {
                    "attraction": 12,
                    "readability": 18,
                    "platform_fit": 12,
                    "searchability": 8,
                },
                "fit_notes": ["Auto-filled to keep the listing testable."],
            }
        )
    return rows[:candidate_count]
