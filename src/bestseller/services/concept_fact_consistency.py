"""跨字段数字事实一致性——确定性对表（构思层）。

2026-08-18《九姓井口只认我》定罪：冠军卡 core_abnormality 同一字段 50 字内
「陆家第七代」与「这十八代」并存，直通成稿；chief_editor 抓到了但 advisory
零消费方。这类**数字型硬事实**（世代数/年数/期限）不需要判官——同一实体
锚点 + 同一单位出现两个不同数值，就是矛盾，纯规则可判。

设计约束（铁律）：
* 零杀权——发现只作为额外 findings 喂给既有的 canon 修复轮（一次有界最小
  修复+复验），修不好原样放行并留痕；
* 高精度优先——只报「锚点可对上」的冲突（陆家七代 vs 陆家十八代），
  不报单位相同但锚点不同的数字（三年前爹死 vs 守了十八年 不是矛盾）；
* min 计数护栏——数值解析失败的匹配直接丢弃，不猜。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

_CN_DIGITS = {
    "零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
# 锚点里的功能字（「陆家这十八代」的「这」不是实体的一部分）
_ANCHOR_FUNCTION_CHARS = "这那都已足整前后近约才就还又的了在是"

_NUMERIC_FACT_RE = re.compile(
    r"([一-鿿]{0,4}?)第?([0-9]{1,4}|[零一两二三四五六七八九十百]{1,4})"
    r"(代|年前|个月|时辰|炷香|柱香)"
)


def _parse_cn_number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    total = 0
    if "百" in token:
        head, _, rest = token.partition("百")
        total += (_CN_DIGITS.get(head, 1) if head else 1) * 100
        token = rest
    if "十" in token:
        head, _, rest = token.partition("十")
        total += (_CN_DIGITS.get(head, 1) if head else 1) * 10
        token = rest
    if token:
        digit = _CN_DIGITS.get(token)
        if digit is None:
            return None
        total += digit
    return total or None


def _normalize_anchor(raw: str) -> str:
    return raw.strip().strip(_ANCHOR_FUNCTION_CHARS)


@dataclass(frozen=True)
class NumericFactConflict:
    unit: str
    anchor: str
    quote_a: str
    quote_b: str
    field_a: str
    field_b: str

    @property
    def explanation(self) -> str:
        return (
            f"同一实体「{self.anchor}」的「{self.unit}」出现两个不同数值，"
            "硬事实只许有一个版本"
        )


def detect_numeric_fact_conflicts(
    fields: Mapping[str, str],
) -> list[NumericFactConflict]:
    """跨字段（含字段内）检出同锚点同单位的数值冲突。

    锚点匹配放宽为后缀包含（「陆家」vs「家」算同一锚点簇），空锚点不参与
    ——没有实体归属的裸数字对不上责任主体，报了就是误伤。
    """

    occurrences: list[tuple[str, str, int, str, str]] = []
    for field_name, text in fields.items():
        if not text:
            continue
        for m in _NUMERIC_FACT_RE.finditer(str(text)):
            anchor = _normalize_anchor(m.group(1))
            if not anchor:
                continue
            value = _parse_cn_number(m.group(2))
            if value is None:
                continue
            occurrences.append(
                (m.group(3), anchor, value, m.group(0), field_name)
            )

    conflicts: list[NumericFactConflict] = []
    seen_pairs: set[tuple[str, str]] = set()
    for i, (unit_a, anchor_a, value_a, quote_a, field_a) in enumerate(occurrences):
        for unit_b, anchor_b, value_b, quote_b, field_b in occurrences[i + 1 :]:
            if unit_a != unit_b or value_a == value_b:
                continue
            if not (anchor_a.endswith(anchor_b) or anchor_b.endswith(anchor_a)):
                continue
            key = (quote_a, quote_b)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            conflicts.append(
                NumericFactConflict(
                    unit=unit_a,
                    anchor=anchor_a if len(anchor_a) >= len(anchor_b) else anchor_b,
                    quote_a=quote_a,
                    quote_b=quote_b,
                    field_a=field_a,
                    field_b=field_b,
                )
            )
    return conflicts
