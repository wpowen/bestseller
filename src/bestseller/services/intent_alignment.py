"""意图对表——用户建书设定 ↔ 构思成品的三面对账。

2026-08-19《摔下山三次》定罪：意图契约（tags=废柴逆袭/升级流/血脉觉醒）
在构思全程只用于路由，没有任何门把它和成品对表——成品丢「升级流」、
简介标签行模型自产出「慢热」直接对抗爽文意图、设定把题材必给的升级
预期反着写（连飞都不会/慢道当核心卖点=用户口中的「反常识」）。
井认主取证缺口 #3 的正面命中。

三面对账：
1. 确定性 · 意图 tags 覆盖：每个意图 tag 必须出现在成品 tags（缺失即报）。
2. 确定性 · 简介标签行接地：标签行是**契约槽位不是句子**——每个 token 必须
   来自意图 tags ∪ 成品 tags ∪ 题材标签；异物（慢热）确定性重建整行，
   不劳 LLM。
3. LLM 判官（调用方接线）：每个意图项在 premise/synopsis/spine 找落点引文，
   找不到或找到对抗元素即 fail——prompt 构造与解析在本模块，遵守
   证据引文 + 两票定罪 + 零杀权铁律。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# 简介首行标签行形状：「标签：A+B+C」或「【A+B+C】」
_TAGLINE_RE = re.compile(r"^(?:标签[:：]\s*|【)([^\n】]+)(?:】)?\s*$")


def intent_tags_from_contract(contract: Mapping[str, Any] | None) -> list[str]:
    """从意图契约取有效意图 tags（user_tags 优先，空则 default_tags/tags）。"""

    if not isinstance(contract, Mapping):
        return []
    genre_intent = contract.get("genre_intent")
    source = genre_intent if isinstance(genre_intent, Mapping) else contract
    for key in ("user_tags", "tags", "default_tags"):
        values = [
            str(v).strip()
            for v in (source.get(key) or [])
            if str(v).strip()
        ]
        if values:
            return values
    return []


# 建书页那四档调性的**可核对标签**（不是写作指令——指令归生成端，
# 这里是给判官对表用的短语）。只认这四个键，未知值忽略。
_TONE_INTENT_LABELS: dict[str, str] = {
    "light": "轻松基调",
    "epic": "宏大基调",
    "dark": "暗黑基调",
    "hot": "热血基调",
}


def _as_mapping(value: object) -> Mapping[str, Any]:
    """把 pydantic 模型 / dataclass / dict 统一成只读映射。

    2026-08-26 定罪：``GenreIntentContract.explicit_enhancers`` 声明的是
    **pydantic 模型** ``StoryEnhancerSelection``，而两处消费方都写着
    ``if isinstance(enh, Mapping):``——恒不成立。后果是代价档判据自
    2026-08-19 落地起**一次都没生效过**（恒判 standard），勾「纯爽无代价」
    从来没被核对；本轮新加的 effect_skills 读取同样中招。
    真机回执 ``cost_style: "standard" / cost_checked: false``，而用户勾的是
    minimal——这就是那条 isinstance 守卫的实际产物。

    本仓库已为此付过一次学费：「别用 getattr 取自家 pydantic 必需字段，
    那会把类型错误降级成静默错误」。这里统一收口，不再让每个调用点自己判类型。
    """

    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            result = dump()
        except Exception:  # pragma: no cover
            return {}
        return result if isinstance(result, Mapping) else {}
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {k: v for k, v in vars(value).items() if not k.startswith("_")}
    return {}


def user_pick_intent_items(contract: Mapping[str, Any] | None) -> list[str]:
    """把**建书页的勾选**翻成可核对的中文意图项。

    2026-08-26 定罪（真机 custom-xuanhuan-1787662679）：用户勾的是
    玄幻／男频／轻松／喜剧＋爽点满足／纯爽无代价，而
    ``intent_tags_from_contract`` 读的是 ``user_tags / tags / default_tags``
    ——用分类选择器建书时这三个字段**恒为空**。于是意图对表门拿到空列表，
    调用现场那句 ``if _intent_tags:`` 把整整 197 行「核对→定罪→修复→复核」
    全部跳过，且不留任何回执：真机 metadata 里一条 intent_* 记录都没有。

    后果是用户勾的**每一项都没被核对过**——基调、故事技能，连本该「独立
    检查」的代价档也一起被关在同一个 if 里（那条判据的 docstring 自己写着
    「独立检查项，与上面的意图判定分开做」）。

    这些短语进的是**判官** prompt 而不是写手 prompt：本模块开篇即言
    「判官读证据≠写手读证据：意图 tag 本身就是用户给的词，不构成种词」。
    """

    contract = _as_mapping(contract)
    if not contract:
        return []
    genre_intent = _as_mapping(contract.get("genre_intent"))
    source = genre_intent or contract
    items: list[str] = []

    tone = _TONE_INTENT_LABELS.get(
        str(source.get("tone_preference") or "").strip().lower()
    )
    if tone:
        items.append(tone)

    enhancers = _as_mapping(source.get("explicit_enhancers"))
    if enhancers:
        try:
            from bestseller.services.story_effect_skills import (
                story_effect_skill_labels,
            )

            for label in story_effect_skill_labels(enhancers.get("effect_skills")):
                if label not in items:
                    items.append(label)
        except Exception:  # pragma: no cover - 目录缺失不该拖垮建书
            pass
    return items


def verifiable_intent_items(contract: Mapping[str, Any] | None) -> list[str]:
    """判官要逐项找落点引文的全部用户意图 = 分类标签 + 建书页勾选。

    两者来源不同、去向也不同：分类标签可以确定性补回 ``tags`` 元数据，
    勾选项不行（它们不是 taxonomy 公民，硬塞会污染跨书标签统计）。
    所以这里只合并**判定用**的清单，补全逻辑仍只认 ``intent_tags_from_contract``。
    """

    merged = list(intent_tags_from_contract(contract))
    for item in user_pick_intent_items(contract):
        if item not in merged:
            merged.append(item)
    return merged


def missing_intent_tags(
    intent_tags: Iterable[str],
    final_tags: Iterable[str],
) -> list[str]:
    final_set = {str(t).strip() for t in final_tags if str(t).strip()}
    return [t for t in intent_tags if t not in final_set]


@dataclass
class TaglineAudit:
    tagline: str | None = None
    tokens: list[str] = field(default_factory=list)
    alien_tokens: list[str] = field(default_factory=list)
    rebuilt_line: str | None = None


def audit_and_rebuild_tagline(
    synopsis: str,
    *,
    intent_tags: Iterable[str],
    final_tags: Iterable[str] = (),
    genre_labels: Iterable[str] = (),
) -> TaglineAudit:
    """核对简介首行标签行的每个 token 是否接地；有异物则确定性重建整行。

    重建规则：题材标签 + 意图 tags + 原行里已接地的其余 token（保序去重）。
    标签行缺失不算病（62% 头部有标签行，38% 没有），只有「有行且带异物」
    才动它。返回的 rebuilt_line 为 None 表示无需改动。
    """

    audit = TaglineAudit()
    lines = [ln for ln in str(synopsis or "").splitlines()]
    first_content = next((ln.strip() for ln in lines if ln.strip()), "")
    m = _TAGLINE_RE.match(first_content)
    if not m:
        return audit
    audit.tagline = first_content
    tokens = [t.strip() for t in m.group(1).split("+") if t.strip()]
    audit.tokens = tokens
    allowed = (
        {str(t).strip() for t in intent_tags}
        | {str(t).strip() for t in final_tags}
        | {str(t).strip() for t in genre_labels}
    )
    allowed.discard("")
    audit.alien_tokens = [t for t in tokens if t not in allowed]
    if not audit.alien_tokens:
        return audit
    ordered: list[str] = []
    for t in [*genre_labels, *intent_tags, *(x for x in tokens if x in allowed)]:
        t = str(t).strip()
        if t and t not in ordered:
            ordered.append(t)
    audit.rebuilt_line = "标签：" + "+".join(ordered)
    return audit


def replace_tagline(synopsis: str, rebuilt_line: str) -> str:
    lines = str(synopsis or "").splitlines()
    for index, line in enumerate(lines):
        if line.strip():
            if _TAGLINE_RE.match(line.strip()):
                lines[index] = rebuilt_line
            break
    return "\n".join(lines)


# ── LLM 意图对表判官 ────────────────────────────────────────────────────────


def build_intent_alignment_messages(
    *,
    intent_tags: list[str],
    genre_label: str,
    premise: str,
    synopsis: str,
    spine: Mapping[str, Any] | None,
    cost_style: str = "standard",
) -> tuple[str, str]:
    """每个意图项逐一找落点引文；找不到或找到对抗元素即 fail。

    判官读证据≠写手读证据：意图 tag 本身就是用户给的词，不构成种词。

    ``cost_style``（2026-08-19 追加）：建书页勾的代价档也是「用户设定」的
    一部分，此前无人对表——真机《替嫁夜…》勾了 minimal（能力不带自损），
    成品却把「反噬压在自己胸口／灰印多爬一寸」当核心笔墨，判官因为只判
    tag 落点而放行。代价档是**方向性**判据：限制≠代价（限制任何档都要有，
    见 docs/shuangwen-config-concept-design-20260819.md），所以只判自损，
    不判限制。
    """

    spine_json = json.dumps(dict(spine or {}), ensure_ascii=False)
    system = (
        "你是选题审核编辑，核对成品构思是否兑现了用户下单时勾选的题材意图。"
        "每一项判定必须给引文证据；给不出落点引文就判否。只输出JSON。"
    )
    items = "、".join(intent_tags)
    _cs = str(cost_style or "standard").strip().lower()
    cost_rule = ""
    if _cs in ("external", "minimal"):
        cost_rule = (
            "\n【代价档判定 · 独立检查项，与上面的意图判定分开做】"
            "用户勾的是「能力不带自损」档：主角使用核心能力不应付出自身折损。\n"
            "判定公式——**逐句扫一遍前提与简介**，凡是能填进"
            "「他每（用一次能力/做一次某动作），自己就（少/减/暗去/折损/"
            "虚弱/短一截）……」这个句式的句子，一律算自损，必须报。"
            "文学化写法同样算（把损耗写成焚香、灯油、笔画、白发、面容这类"
            "意象照报不误）——判断标准是**使用即扣减**这个结构，不是字面用词。\n"
            "反面：能力的**边界条件**（用不了什么、要什么资格才能用、"
            "招来多强的敌人、有时限或次数上限）不算自损，属于合法限制，不要报。\n"
            "在 cost_violations 里逐条给出逐字引文；确实没有才给空数组。\n"
        )
    user = (
        f"题材：{genre_label}\n用户勾选的意图标签：{items}\n"
        f"{cost_rule}\n"
        f"【前提】{premise}\n\n【简介】{synopsis}\n\n【故事脊柱】{spine_json}\n\n"
        "逐项判定：\n"
        "1. 对每个意图标签给 {\"pass\": bool, \"quote\": \"成品里兑现该意图的"
        "落点引文（逐字）\"}；引不出落点 → pass=false，quote 留空。\n"
        "2. counter_elements：列出成品中与任一意图**方向相反**的设定元素"
        "（例如意图要持续升级、成品却把'不能升级/放弃升级'当核心卖点），"
        "每条给逐字引文；没有则空数组。\n"
        "3. revise_direction：任一 fail 或 counter 非空时给修正方向，"
        "只给方向不给措辞，≤40字；否则空串。\n\n"
        "输出JSON：{\"items\":{\"<意图标签>\":{\"pass\":true,\"quote\":\"…\"}},"
        "\"counter_elements\":[{\"quote\":\"…\",\"against\":\"<意图标签>\"}],"
        "\"cost_violations\":[{\"quote\":\"…\"}],"
        "\"revise_direction\":\"…\"}"
    )
    return system, user


def parse_intent_alignment_verdict(
    payload: Mapping[str, Any] | None,
    *,
    intent_tags: list[str],
) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    items = payload.get("items")
    items = items if isinstance(items, Mapping) else {}
    verdict: dict[str, Any] = {"items": {}, "failed_tags": [], "counter_elements": []}
    for tag in intent_tags:
        entry = items.get(tag)
        if not isinstance(entry, Mapping):
            # 判官漏项 ≠ 定罪（无杀权精神），标 unknown
            verdict["items"][tag] = {"pass": None}
            continue
        verdict["items"][tag] = {
            k: v for k, v in entry.items() if isinstance(k, str)
        }
        if entry.get("pass") is False:
            verdict["failed_tags"].append(tag)
    counters = payload.get("counter_elements")
    if isinstance(counters, list):
        verdict["counter_elements"] = [
            {
                "quote": str(c.get("quote") or "").strip(),
                "against": str(c.get("against") or "").strip(),
            }
            for c in counters
            if isinstance(c, Mapping) and str(c.get("quote") or "").strip()
        ]
    # 代价档违规并入同一条修复通道（against 标为代价档，修复 prompt 一视同仁）
    cost_bad = payload.get("cost_violations")
    if isinstance(cost_bad, list):
        verdict["cost_violations"] = [
            {"quote": str(c.get("quote") or "").strip(), "against": "代价档（能力不带自损）"}
            for c in cost_bad
            if isinstance(c, Mapping) and str(c.get("quote") or "").strip()
        ]
        verdict["counter_elements"].extend(verdict["cost_violations"])
    verdict["revise_direction"] = str(payload.get("revise_direction") or "").strip()
    return verdict
