"""AI flavour in *copy* — loglines, blurbs, selling points — not in prose.

Why a separate detector
-----------------------
``services.ai_flavor.detector`` is tuned for narrative prose: choppy rhythm,
filter words, negated definitions. Run it on a blurb and it returns 0.0 for
both of these, which a reader can tell apart instantly:

    good  十三岁少年被灵根碑判了死路，却瞄上一个刚欺负过他的杂役师兄——
          只要打赢，对方练了多年的功法就归他。
    bad   每章有人倒下、每章有新本事当场就用，下一个被点名的是谁
          永远吊着你的追读。

The bad one is not badly written. It is written **to the wrong reader**: it
describes the product to an editor instead of putting a person in a situation.
That is the failure mode this module measures.

Four families, all evidenced by real output from custom-xuanhuan-1785980083:

* ``meta_cadence`` — 每章/每3-5章/每卷 … : chapter arithmetic belongs in a plan,
  not in copy meant to hook someone.
* ``trade_jargon`` — 追读/爽点/留存/黄金三章/开局劝退 : industry vocabulary that
  only a platform editor uses.
* ``spec_sheet`` — 零延迟/同步升档/咬合升级/高密度 : product-spec register.
* ``framework_leak`` — 极简代价/绝不反向惩罚主角/不进度清零 : the framework's own
  directives copied verbatim into reader-facing text. This one is the most
  embarrassing: it means an internal instruction escaped into the shop window.

Deliberately narrow. Copy is allowed to be punchy, exaggerated, even trashy —
those are genre registers, not defects. Only the four families above are
flagged, and each carries the matched span so a verdict can be checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "CopyFlavorReport",
    "CopyFlavorSpan",
    "detect_copy_flavor",
]


@dataclass(frozen=True)
class CopyFlavorSpan:
    category: str
    matched: str
    why: str


@dataclass(frozen=True)
class CopyFlavorReport:
    score: float
    spans: tuple[CopyFlavorSpan, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.spans

    def to_payload(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "spans": [
                {"category": s.category, "matched": s.matched, "why": s.why}
                for s in self.spans
            ],
        }


#: (category, pattern, weight, why). Weights differ because the families are not
#: equally damning: a framework directive in the shop window is worse than one
#: piece of trade jargon.
_RULES: Final[tuple[tuple[str, str, float, str], ...]] = (
    (
        "meta_cadence",
        r"每\s*\d*\s*[-–~至到]?\s*\d*\s*章|每卷|每一章|章章|每回合",
        6.0,
        "用章节节奏描述作品——这是规划语言，读者不关心第几章发生什么",
    ),
    (
        "trade_jargon",
        r"追读|爽点|留存|黄金三章|开局劝退|完读率|上架|均订|数据表现|付费转化",
        8.0,
        "平台行业黑话，只有编辑会这么说话",
    ),
    (
        "spec_sheet",
        r"零延迟|同步升档|咬合升级|高密度|强度拉满|节奏拉满|密集输出|闭环|机制设计",
        6.0,
        "产品规格书口吻，在描述功能而不是讲故事",
    ),
    (
        "framework_leak",
        r"极简代价|代价外置|绝不反向惩罚|不进度清零|不长期失能|金手指不收费|反套路开篇",
        10.0,
        "框架内部指令泄漏进对外文案——这是给生成器看的话，不该出现在成品里",
    ),
)

_COMPILED: Final[tuple[tuple[str, re.Pattern[str], float, str], ...]] = tuple(
    (cat, re.compile(pat), weight, why) for cat, pat, weight, why in _RULES
)


def detect_copy_flavor(text: str | None) -> CopyFlavorReport:
    """Score reader-facing copy. Higher is worse; 0.0 means nothing flagged."""

    content = str(text or "").strip()
    if not content:
        return CopyFlavorReport(score=0.0)

    spans: list[CopyFlavorSpan] = []
    score = 0.0
    for category, pattern, weight, why in _COMPILED:
        for match in pattern.finditer(content):
            spans.append(
                CopyFlavorSpan(category=category, matched=match.group(0), why=why)
            )
            score += weight

    return CopyFlavorReport(score=round(score, 1), spans=tuple(spans))
