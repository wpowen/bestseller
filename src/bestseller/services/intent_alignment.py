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
) -> tuple[str, str]:
    """每个意图项逐一找落点引文；找不到或找到对抗元素即 fail。

    判官读证据≠写手读证据：意图 tag 本身就是用户给的词，不构成种词。
    """

    spine_json = json.dumps(dict(spine or {}), ensure_ascii=False)
    system = (
        "你是选题审核编辑，核对成品构思是否兑现了用户下单时勾选的题材意图。"
        "每一项判定必须给引文证据；给不出落点引文就判否。只输出JSON。"
    )
    items = "、".join(intent_tags)
    user = (
        f"题材：{genre_label}\n用户勾选的意图标签：{items}\n\n"
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
    verdict["revise_direction"] = str(payload.get("revise_direction") or "").strip()
    return verdict
