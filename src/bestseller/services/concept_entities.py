"""从已批准构思正文里确定性地抽取故事实体（地名 / 器物 / 人名）。

单一来源，供书名淘汰赛与 source-bound 世界编译共用。

**为什么必须零词表**：2026-08-21 真机 custom-xuanhuan-1787320762 上，
书名的物件抽取器 `_resolve_object_token` 里写死了一张
``priority_markers = ("重瞳","阴阳眼","青囊","困魂镜","归墟会","双穿门",…)``
——那是**上一本书**的物件清单，所以它永远找不到本书的「蒸灵锅」。
同一天在命名池检查里也查到同型病（靠手工黑名单做减法，每来一本新书加一批字，
永不收敛）。这里只用**形状**：地名靠地点后缀且必须落在小句开头，
器物取金手指描述里破折号之前的名词短语，人名取身份词之后紧跟的 2-3 字。

抽不出来就返回空——**空是诚实，占位符是谎**。调用方自己决定怎么兜底。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点与词形是刻意的。
import re
from typing import Any

_ZH = r"一-鿿"
_CJK_RE = re.compile(f"[{_ZH}]")

# 器物描述的分隔符：取破折号/逗号/括号之前的那个名词短语。
_OBJECT_SPLIT_RE = re.compile(r"[——\-—－,，。;；:：（(\[【]")
# 中文地名的常见收尾字。
_PLACE_SUFFIX = "巷街市镇村城乡坊宗门派谷峰山岭洞府院阁楼寺观塔堂殿域界州郡"
_PLACE_RE = re.compile(f"([{_ZH}]{{2,6}}[{_PLACE_SUFFIX}])")
# 地名里不会出现的结构助词/动词：命中说明正则回溯吃进了动词短语
# （真机把「守着父亲留下的早市」当成了地名）。
_NOT_A_PLACE = re.compile(r"[的了着是在和与把被让从对给下留]")
_CLAUSE_SPLIT_RE = re.compile(r"[，,。；;、！？!?\s]")
# 身份词 + 紧跟的名字。身份词只列**通用**角色词，不列任何具体书的设定词。
_ROLE_THEN_NAME_RE = re.compile(
    f"(?:少年|少女|弟子|徒|师兄|师弟|师姐|师妹|掌柜|摊主|书生|捕快|道士|和尚|"
    f"郎中|铁匠|厨子|杂役|守夜人|说书人|账房|镖师|货郎)([{_ZH}]{{2,3}})[，,。]"
)

_PLACEHOLDER_NAMES = frozenset({"主角", "Protagonist", "主角设定", "protagonist"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def first_clause(text: str) -> str:
    return re.split(r"[。！？!?\n]", _text(text), maxsplit=1)[0]


def extract_place_names(text: str) -> list[str]:
    """取出干净的地名（保序去重）。

    地名必须落在**小句开头**——不加这条，真机会从「每天辰时开锅替坊民」
    中间切出「天辰时开锅替坊」当地名。
    """

    out: list[str] = []
    for clause in _CLAUSE_SPLIT_RE.split(_text(text)):
        clause = clause.strip()
        if not clause:
            continue
        match = _PLACE_RE.match(clause)
        if not match:
            continue
        token = match.group(1)
        if token and token not in out and not _NOT_A_PLACE.search(token):
            out.append(token)
    return out


def extract_leading_noun_phrase(text: str, *, max_chars: int = 10) -> str:
    """从「百年蒸灵锅——一只能听见、能说话的老锅」里取出「百年蒸灵锅」。"""

    head = _OBJECT_SPLIT_RE.split(_text(text), maxsplit=1)[0].strip()
    if not head or not _CJK_RE.search(head):
        return ""
    return head if len(head) <= max_chars else ""


def extract_role_bound_name(text: str) -> str:
    """取身份词之后紧跟的人名（「温符徒温迟，」→「温迟」）。

    `book_design._protagonist_name_from_text` 的身份词表抽不出这个例子，
    因为「温符徒」是本书自造的身份词；这里靠**通用**身份词 + 位置形状。
    """

    match = _ROLE_THEN_NAME_RE.search(_text(text))
    return match.group(1) if match else ""


def is_placeholder_name(value: str) -> bool:
    """「主角」这类占位名不是人名。"""

    return _text(value) in _PLACEHOLDER_NAMES
