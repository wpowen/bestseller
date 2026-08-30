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
those are genre registers, not defects. Only the families above are flagged,
and each carries the matched span so a verdict can be checked.

2026-08-30 去AI味融合批追加五族（17 个外部仓库调研，判据在 2218 条真实
番茄榜单简介上校准，误报率标注在各规则 why 里；粗形词表被否决的教训值得
记录：「很简单：」「评论区见」「未来可期」在真实平台简介里是**人类常态**
——自媒体腔在平台语境不是缺陷，收窄到助手腔/擂鼓帽句/饱和密度才是）：

* ``colon_hat`` — 句首「答案：/真相：/结论是：」擂鼓宣告（0/2218 人类命中）。
* ``fake_interaction`` — 「你觉得呢/建议收藏/一文读懂」助手式假互动（0/2218）。
* ``uplift_closer`` — 「未来可期/拭目以待」万金油升华（1/2218）。
* ``milestone_hype`` — 「标志着/里程碑/范式转移」意义拔高（0/2218）。
* ``contrast_saturation`` — 「不是A而是B」翻案腔 **≥2 次**才报：单次是人类
  合法钩子修辞（2.30% 真实简介在用），饱和才是模板（≥2 仅 0.05%）。
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


#: (category, pattern, weight, why[, min_count]). Weights differ because the
#: families are not equally damning: a framework directive in the shop window is
#: worse than one piece of trade jargon. ``min_count``（缺省 1）：命中次数达到
#: 该值才报——给「单次合法、饱和才是病」的族用（contrast_saturation）。
_RULES: Final[tuple[tuple, ...]] = (
    (
        "meta_cadence",
        r"每\s*\d*\s*[-–~至到]?\s*\d*\s*章|每卷|每一章|章章|每回合",
        6.0,
        "用章节节奏描述作品——这是规划语言，读者不关心第几章发生什么",
    ),
    (
        # 2026-08-24：读者文案里**出现章号本身**就是生产口吻，不必再配一个指令
        # 动词。真机书9 的上架简介结尾「第50章，账本只剩最后几页」与 synopsis
        # 里「上一章替他挡刀的人，下一章就得哭着求他还人情」都打 0 分——因为
        # directive_voice 要求「锚点+动词」同时命中，而它们只有锚点。
        # 读者买书时还没有章的概念；说「第几章如何如何」的只有写手和编辑。
        #
        # 边界（本条只用于**对外文案**，正文不走这套规则）：
        #  · 必须是「第N章/第N卷」，不收裸「N章」——「三章鱼干」不许命中
        #  · 相对指代必须带「一」：上一章/下一章/前一章/后一章 与 本章/本卷；
        #    不收裸「上卷/下卷」——「秘籍上卷」是故事内的东西
        #  · 「第三层」「第九笔」「第五个」都不带章/卷，天然不命中
        "meta_cadence",
        r"第\s*[0-9零一二三四五六七八九十百千两]+\s*[章卷]|[上下前后]一[章卷]|本[章卷]",
        6.0,
        "对外文案里报章号——读者买书时还没有章的概念，这么说话的只有写手和编辑",
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
        r"[^。；！？]{0,16}?(必须|一定要|得先|亮出|抛出|给出|推进|证明|拉住"
        r"|让读者[^。；！？]{0,4}?(?:看到|感觉|知道|记住|相信)|交代|铺垫|建立|呈现|展示)"
        r"|必须(持续|不断|连续)(兑现|推进|升级|维持)"
        r"|不允许[^。；！？]{0,12}(堆积|空转|注水|拖沓)"
        r"|不能只(靠|写|停留)",
        6.0,
        "在命令生成器该怎么写，而不是在告诉读者会看到什么——这话的听众是写手，不是读者",
    ),
    # ── 2026-08-30 去AI味融合批（校准语料：2218 条真实番茄榜单简介）──────
    (
        "colon_hat",
        r"(?:^|[。！？\n])\s*(?:答案|真相|结论|关键|重点)(?:是|在于)?[：:]",
        6.0,
        "擂鼓帽句：在内容前敲锣（答案：/真相：/结论是：）——重点不需要宣告自己"
        "是重点。真实平台简介 0/2218 命中；注意「目标很简单：吃睡变强」是人物"
        "目标的合法叙述，不在此列",
    ),
    (
        "fake_interaction",
        r"你觉得呢|是不是很有启发|建议收藏|一文读懂|点个(?:赞|关注)|欢迎点赞",
        8.0,
        "助手式假互动——这是聊天机器人和自媒体教程的收束话术，不是小说简介"
        "（0/2218 真实简介命中；「评论区见/欢迎催更」是平台人类常态，不收）",
    ),
    (
        "uplift_closer",
        r"未来可期|前景光明|拭目以待|砥砺前行|注入了?新的活力|激动人心的时代",
        4.0,
        "万金油升华收尾——放到任何简介结尾都成立的话等于没说（1/2218 真实简介"
        "命中）。停在最后一个具体的钩子事实上",
    ),
    (
        "milestone_hype",
        r"标志着|里程碑式?|范式转移",
        4.0,
        "里程碑腔：给普通事实颁奖（0/2218 真实简介命中；「见证了她的堕落」是"
        "叙事动词用法，刻意不收）",
    ),
    (
        "contrast_saturation",
        r"(?:不是|并非|不在于)[^，。！？\n]{1,20}[，,]?(?:而是|而在于)",
        6.0,
        "翻案腔饱和：「不是A而是B」出现 ≥2 次。单次是人类合法钩子修辞"
        "（2.30% 真实简介在用，不收），连用才是骨架依赖（≥2 仅 0.05%）",
        2,
    ),
)

_COMPILED: Final[tuple[tuple[str, re.Pattern[str], float, str, int], ...]] = tuple(
    (
        rule[0],
        re.compile(rule[1]),
        float(rule[2]),
        rule[3],
        int(rule[4]) if len(rule) > 4 else 1,
    )
    for rule in _RULES
)


def detect_copy_flavor(text: str | None) -> CopyFlavorReport:
    """Score reader-facing copy. Higher is worse; 0.0 means nothing flagged."""

    content = str(text or "").strip()
    if not content:
        return CopyFlavorReport(score=0.0)

    spans: list[CopyFlavorSpan] = []
    score = 0.0
    for category, pattern, weight, why, min_count in _COMPILED:
        matches = list(pattern.finditer(content))
        if len(matches) < min_count:
            continue
        for match in matches:
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
