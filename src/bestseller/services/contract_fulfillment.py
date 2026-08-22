"""章节契约兑现度：确定性、零噪声、可复现的「正文有没有照契约写」量具。

2026-08-22 建立。动机链条：

* 同参数两本书受控对照证明——**九项管道修复全部成立，判官的结构分只涨
  0.02-0.03**（contract_alignment 0.250 → 0.277，pass 仍 0/52）。
  差距锁在「写手会不会按契约写」这一层。
* 两次归因都排除了更便宜的解释：三类条款兑现率均匀在 20%（没有某类系统性
  失守的靶子）；72 条条款里只有 1 条是框架元语言（契约本身是具体可执行的）。
* 于是必须做写手 prompt 的 A/B。但 **判官噪声极差中位 0.17、最高 0.34**
  （见 memory shuangwen-chain-and-evaluation-system），而要测的差异是 0.03
  量级——**判官测不出来**。

所以先要一把确定性的尺子。本模块就是它：**契约条款里的具体名物，有多少
在正文里真的出现了**。零 LLM、零噪声、同输入永远同输出。

判据只做减法、不猜语义：抽名物用**形状**（连续汉字块按虚词切分），
与 :mod:`concept_entities` 同源；不认任何具体书的词表。

⚠️ 这是**代理指标**，不是真理：它测的是「契约里的东西有没有出现在正文里」，
不测「出现得好不好」。用它做 A/B 的前提是先验证它与判官
`contract_alignment` 同向（见 :func:`correlation_with_judge`）。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点与词形是刻意的。
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Any

# 虚词：抽名物时在这些字上切开。列的是**语法功能字**，不是内容词，
# 因此换任何题材都成立。
_FUNCTION_CHARS = set(
    "的了着是在和与把被让从对给下留这那有为其之以及或但而且就都也很更还再又"
    "只则即会要能不没上出去来做说想看过着吗呢吧啊们你我他她它"
)
_ZH_RUN_RE = re.compile(r"[一-鿿]+")
# 收尾钩子只在正文尾部核对——钩子写在开头不算兑现。
_TAIL_CHARS = 600
# 短于两字的片段没有辨识度，不计入。
_MIN_ENTITY_CHARS = 2
_MAX_ENTITY_CHARS = 4


@dataclass(frozen=True)
class ClauseFulfillment:
    """一条契约条款的兑现情况。"""

    clause_name: str
    entities: tuple[str, ...] = ()
    landed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def rate(self) -> float:
        if not self.entities:
            return 1.0
        return len(self.landed) / len(self.entities)


@dataclass(frozen=True)
class ChapterFulfillment:
    """一章所有契约条款的兑现情况。"""

    chapter_number: int
    clauses: tuple[ClauseFulfillment, ...] = ()

    @property
    def rate(self) -> float:
        measured = [c for c in self.clauses if c.entities]
        if not measured:
            return 1.0
        return sum(c.rate for c in measured) / len(measured)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "rate": round(self.rate, 4),
            "clauses": {
                c.clause_name: {
                    "rate": round(c.rate, 4),
                    "missing": list(c.missing),
                }
                for c in self.clauses
            },
        }


def extract_clause_entities(clause: str) -> tuple[str, ...]:
    """从条款里抽出「具体名物」。

    按虚词切分连续汉字块，保留 2-4 字的片段。刻意**不做语义判断**——
    切碎的短语（「文润必须」）会被算成未命中，因此本指标是**下界**：
    真实兑现度只会比它高，不会更低。做 A/B 时比较的是同一把尺子下的
    相对变化，下界偏差在两臂之间抵消。
    """

    out: list[str] = []
    for run in _ZH_RUN_RE.findall(str(clause or "")):
        index = 0
        while index < len(run):
            if run[index] in _FUNCTION_CHARS:
                index += 1
                continue
            end = index
            while (
                end < len(run)
                and run[end] not in _FUNCTION_CHARS
                and end - index < _MAX_ENTITY_CHARS
            ):
                end += 1
            token = run[index:end]
            if len(token) >= _MIN_ENTITY_CHARS and token not in out:
                out.append(token)
            index = end if end > index else index + 1
    return tuple(out)


def measure_clause(clause_name: str, clause: str, target: str) -> ClauseFulfillment:
    entities = extract_clause_entities(clause)
    landed = tuple(e for e in entities if e in target)
    missing = tuple(e for e in entities if e not in target)
    return ClauseFulfillment(
        clause_name=clause_name, entities=entities, landed=landed, missing=missing
    )


def measure_chapter(
    *,
    chapter_number: int,
    prose: str,
    core_conflict: str = "",
    information_release: str = "",
    closing_hook: str = "",
    emotional_shift: str = "",
) -> ChapterFulfillment:
    """量一章的契约兑现度。

    收尾钩子只在正文**尾部**核对——写在开头的钩子不算兑现，这正是
    `ending_hook_effectiveness` 长期偏低要查的东西。
    情绪弧是受控词表里的单词（爽/燃/悬…），出现在正文里反而是坏事
    （那是把策划词写进正文），所以**不计入**。
    """

    body = str(prose or "")
    tail = body[-_TAIL_CHARS:]
    clauses = (
        measure_clause("核心冲突", core_conflict, body),
        measure_clause("信息释放", information_release, body),
        measure_clause("收尾钩子", closing_hook, tail),
    )
    return ChapterFulfillment(chapter_number=chapter_number, clauses=clauses)


def measure_book(rows: Sequence[Mapping[str, Any]]) -> list[ChapterFulfillment]:
    return [
        measure_chapter(
            chapter_number=int(row.get("chapter_number") or 0),
            prose=str(row.get("prose") or ""),
            core_conflict=str(row.get("core_conflict") or ""),
            information_release=str(row.get("information_release") or ""),
            closing_hook=str(row.get("closing_hook") or ""),
        )
        for row in rows
    ]


def book_summary(results: Sequence[ChapterFulfillment]) -> dict[str, Any]:
    if not results:
        return {"chapters": 0}
    rates = [r.rate for r in results]
    by_clause: dict[str, list[float]] = {}
    for chapter in results:
        for clause in chapter.clauses:
            if clause.entities:
                by_clause.setdefault(clause.clause_name, []).append(clause.rate)
    return {
        "chapters": len(results),
        "mean": round(sum(rates) / len(rates), 4),
        "min": round(min(rates), 4),
        "by_clause": {
            name: round(sum(values) / len(values), 4)
            for name, values in by_clause.items()
        },
    }


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """样本相关系数。用于验证本代理指标与判官是否同向。

    少于 3 个点或任一侧方差为零时返回 None——**不报一个假的相关性**。
    """

    n = min(len(xs), len(ys))
    if n < 3:
        return None
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    sxx = sum((x - mx) ** 2 for x in xs[:n])
    syy = sum((y - my) ** 2 for y in ys[:n])
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return sxy / (sxx**0.5 * syy**0.5)


def correlation_with_judge(
    fulfillment: Sequence[float], judge_contract_alignment: Sequence[float]
) -> float | None:
    """本指标与判官 contract_alignment 的相关性。

    **这是使用前提**：相关性不成立，就说明这把尺子量的不是判官在扣分的东西，
    不能拿它驱动 A/B。相关性成立，才可以用它替代噪声极差 0.17-0.34 的判官
    去测 0.03 量级的差异。
    """

    return pearson(list(fulfillment), list(judge_contract_alignment))
