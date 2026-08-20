"""调性关键词的单一合并口径。

2026-08-20 真机《罚我守坟，坟里换的全是活人》定罪：``style_guides.tone_keywords``
落库为 ``["轻松","幽默","明快","冷","压","悬","慢火"]``——建书页的调性 pick
（light → 轻松/幽默/明快）与题材画像自带的调性（冷/压/悬/慢火）被直接拼接，
无任何自洽校验。这份自相矛盾的清单逐字进了**每一章**的 system prompt
（PROJECT PROFILE · 语气关键词），写手同时被要求「轻松幽默明快」和
「冷、压、慢火」；而且爽文书里活着一个「慢火」，与 2026-08-19《摔下山三次》
定罪的「慢热」同族。

同批第二处：``story_bible`` 落 book_spec 时整体覆盖 ``tone_keywords``，
用户选的调性被模型自产 tone 无声抹掉——「同一事实住两地，后写的赢」。
两处现在都走本模块。

这里的词表是**配置层的自洽校验**，不是写进 prompt 的种词——它只做减法
（删掉与用户所选调性正面冲突的题材调性），永远不新增任何词。
"""

from __future__ import annotations

from typing import Iterable, Sequence

# 互斥调性族。同一行内部相容，跨对立行冲突。词表只用于**剔除**，
# 不会把任何词加进结果，因此不构成种词。
_ANTONYM_PAIRS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (
        frozenset({"轻松", "幽默", "明快", "轻快", "诙谐", "爽快", "热血", "燃"}),
        frozenset({"冷", "冷峻", "压", "压抑", "暗黑", "阴郁", "沉重", "肃杀", "苦"}),
    ),
    (
        frozenset({"明快", "节奏快", "爽快", "快", "燃"}),
        frozenset({"慢火", "慢热", "慢", "缓", "文火"}),
    ),
    (
        frozenset({"宏大", "厚重", "史诗感"}),
        frozenset({"市井", "市井气", "小品", "日常"}),
    ),
)

_MAX_TONE_KEYWORDS = 6


def _conflicts(lead_words: set[str], candidate: str) -> bool:
    for left, right in _ANTONYM_PAIRS:
        if candidate in right and lead_words & left:
            return True
        if candidate in left and lead_words & right:
            return True
    return False


def merge_tone_keywords(
    *,
    lead: Sequence[str] | Iterable[str],
    base: Sequence[str] | Iterable[str],
    max_keywords: int = _MAX_TONE_KEYWORDS,
) -> list[str]:
    """用户所选调性领衔，题材调性跟随，正面冲突的题材调性被剔除。

    ``lead`` 为空时原样返回 ``base``（无 pick = 不干预题材画像）。
    """

    lead_clean = [str(k).strip() for k in (lead or []) if str(k).strip()]
    base_clean = [str(k).strip() for k in (base or []) if str(k).strip()]
    if not lead_clean:
        return base_clean

    lead_set = set(lead_clean)
    ordered: list[str] = []
    for keyword in lead_clean:
        if keyword not in ordered:
            ordered.append(keyword)
    for keyword in base_clean:
        if keyword in ordered:
            continue
        if _conflicts(lead_set, keyword):
            continue
        ordered.append(keyword)
    return ordered[:max_keywords]
