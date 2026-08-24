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
* ``directive_voice`` — 开篇就亮出…/必须持续兑现/不允许无效日常堆积 : an order given
  to the *generator*, not a promise made to a reader. The lexical families above
  cannot see this one — 「主角成长路径、体系升级…必须持续兑现」 contains no jargon
  at all and still scores 0.0, yet nobody would print it on a book's listing.
  The discriminator is not vocabulary but addressee: the sentence commands the
  text's own production units (开篇 / 全书 / 章末 / 前三章) instead of describing
  what happens to a person.

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
    "pick_reader_facing",
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
    (
        # Two shapes of the same defect. First: a production unit of the text
        # itself (开篇/全书/章末/前三章) followed by an order. Second: bare
        # production orders that need no such subject. 「他必须在三天内赶到」 is
        # narrative and must stay clean — that is why the modal alone is never
        # enough, it has to be aimed at the manuscript.
        # 2026-08-24：数词类原本漏了「几」「数」，于是「前三章给出」命中而
        # 「前几章给出」漏网——真机书9 的 reader_promise 就是靠这个含糊数词
        # 绕过整道门的（打分 0.0）。同一句话换个模糊量词就无罪，是词表类
        # 检测器最常见的缺口。
        "directive_voice",
        r"(开篇|全书|正文|章末|收尾|前\s*[一二三四五六七八九十百千万几数\d]+\s*[章字])"
        r"[^。；！？]{0,16}?(必须|一定要|得先|亮出|抛出|给出|推进|证明|拉住)"
        r"|必须(持续|不断|连续)(兑现|推进|升级|维持)"
        r"|不允许[^。；！？]{0,12}(堆积|空转|注水|拖沓)"
        r"|不能只(靠|写|停留)",
        6.0,
        "在命令生成器该怎么写，而不是在告诉读者会看到什么——这话的听众是写手，不是读者",
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


def pick_reader_facing(*candidates: str | None) -> str:
    """Pick the first candidate a reader could actually be shown.

    Several places choose a reader-facing string from a preference chain whose
    first link is a generator directive. The clearest case: a genre preset's
    ``market.reader_promise`` holds an order to the writer —

        开篇快速亮出主角差异化优势、当前利益、即时危险和连载钩子，持续维持强追读。

    — and that string was becoming the listing subtitle. One field name, two
    jobs; the instruction shipped as shop-window text.

    Preference order is preserved, so this is a filter and not a reorder: a
    candidate that reads as a directive is skipped, nothing is promoted. When
    every candidate is a directive the **last** one is kept — callers order
    these directive-first and copy-last, so that degrades to the previous
    behaviour rather than to an empty field.

    Lives here rather than at either call site because both chains must make
    the same judgement; a second copy of this rule is a second place for it to
    drift.
    """

    texts = [str(candidate or "").strip() for candidate in candidates]
    for text in texts:
        if text and detect_copy_flavor(text).clean:
            return text
    return next((text for text in reversed(texts) if text), "")
