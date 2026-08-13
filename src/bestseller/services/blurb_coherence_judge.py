"""简介/正典自洽校验 — 引文核对式 LLM 判官（校验任务，不是评分任务）。

2026-08-07 真机 custom-xianxia-1786090118：对外简介同时压着四条倒计时
（一个时辰/今夜/月底/一个月），土豆先被徒弟揣走又被主角下锅，premise 说
三十五岁、spine 说三十年厨房功夫（5 岁开始颠勺）。而全链没有任何一处测逻辑：
``comprehensibility`` 只数生造黑话（打了满分 5.0），病理检测全是词表层，
画像判官只答「点不点」。**矛盾对现有每一把尺子都不可见。**

为什么不直接问 LLM「这段通顺吗」：同日校准已证明问感受得到吹捧
（aversion 案例：judge 说「不恶心」而人反胃）。所以这里只给校验任务——
「找出互相矛盾的两处，把两段原文逐字引出来」——然后**程序核对引文确实是
输入文本的子串**，引不出原文的发现按幻觉丢弃。模型没有打分空间，
只有可证伪的产出。

fail-open：LLM 不可用/超时/全部发现核对失败 → 空报告 + ``llm_used=False``，
绝不误毙。调用方拿 verified findings 当 fatal 级信号用。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — Chinese prompts are intentional.
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoherenceFinding:
    """一条**通过引文核对**的矛盾。quote_a/quote_b 保证接地于输入文本。

    ``touches_synopsis`` 标记矛盾是否涉及简介本身：正典内部矛盾
    （premise↔spine，如 35 岁 vs 三十年厨房功夫）不是文案的错——拿它连坐
    冠军候选会把候选全毙掉再回退到同病的 v0，等于白跑。候选淘汰只看
    touches_synopsis=True 的；正典矛盾交给构思重生循环去改正典。
    """

    kind: str        # timeline | fact | reference | number
    quote_a: str
    quote_b: str
    explanation: str
    touches_synopsis: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "quote_a": self.quote_a,
            "quote_b": self.quote_b,
            "explanation": self.explanation,
            "touches_synopsis": self.touches_synopsis,
        }


@dataclass(frozen=True)
class CoherenceReport:
    findings: tuple[CoherenceFinding, ...]
    llm_used: bool
    dropped_unverified: int = 0  # 模型给了但引文核对失败（幻觉）的条数

    @property
    def passed(self) -> bool:
        """fail-open：判官不可用视为通过；有核实矛盾才算不过。"""

        return not self.findings

    @property
    def synopsis_findings(self) -> tuple[CoherenceFinding, ...]:
        """涉及简介本身的矛盾（候选淘汰只看这些）。"""

        return tuple(f for f in self.findings if f.touches_synopsis)

    @property
    def canon_findings(self) -> tuple[CoherenceFinding, ...]:
        """纯正典内部矛盾（premise↔spine）——要送回构思重生去改，不怪文案。"""

        return tuple(f for f in self.findings if not f.touches_synopsis)

    def feedback_lines(self) -> list[str]:
        """供重生循环回灌的整改行（一条矛盾一行，带原文）。"""

        return [
            f"自相矛盾（{f.kind}）：「{f.quote_a}」与「{f.quote_b}」不能同时成立"
            f"——{f.explanation}。改到只留一个说法。"
            for f in self.findings
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "llm_used": self.llm_used,
            "dropped_unverified": self.dropped_unverified,
            "passed": self.passed,
            "schema_version": "blurb-coherence.v1",
        }


def build_coherence_messages(
    *, synopsis: str, premise: str = "", spine: dict[str, Any] | None = None
) -> tuple[str, str]:
    """(system, user)。输入把简介和正典事实并排给，跨文本矛盾一起查。"""

    spine_block = ""
    if spine:
        spine_block = "\n".join(
            f"  {k}：{v}" for k, v in spine.items() if str(v or "").strip()
        )
    system = (
        "你是逻辑校对员。只做一件事：在给出的文本里找**互相矛盾**的说法。"
        "矛盾指两处说法不能同时为真——时间期限互斥、同一物品/人物状态冲突、"
        "数字对不上（年龄与经历年数）、指代没有着落。"
        "不评价文笔，不提建议，不找'可以更好'的地方——只找硬矛盾。"
        "没有矛盾就输出空列表，不要硬凑。\n"
        "每条矛盾必须给出两段**逐字引用的原文**（quote_a、quote_b，各≤40字，"
        "必须能在原文里原样找到，一个字都不能改），引不出原文的不要报。\n"
        '只输出 JSON：{"contradictions": [{"kind": "timeline|fact|reference|number", '
        '"quote_a": "...", "quote_b": "...", "why": "一句话说明为何不能同时成立"}]}'
    )
    user_parts = []
    if premise.strip():
        user_parts.append(f"【前提】\n{premise.strip()}")
    if spine_block:
        user_parts.append(f"【故事脊柱】\n{spine_block}")
    user_parts.append(f"【简介】\n{synopsis.strip()}")
    user_parts.append("找出以上文本内部及互相之间的硬矛盾。输出严格 JSON。")
    return system, "\n\n".join(user_parts)


def _normalise_for_quote_match(s: str) -> str:
    """引文核对前压掉空白——模型常丢换行，但一个字都不许改。"""

    return "".join(str(s or "").split())


# 引文接地判据：全或无的逐字匹配在真机上不稳——同一 prompt 逐次采样，模型
# 偶尔掐头去尾或改一个标点，真发现就被整条当幻觉丢掉（冒烟实测 dropped=1、
# findings=0，检测器直接归零）。改成「长公共连续片段」：引文里必须有一段
# ≥ max(8, 60% 长度) 的连续文字逐字存在于原文。凭空编造的引文不会与原文
# 共享这么长的连续片段，可证伪性不丢。
_QUOTE_MIN_RUN = 8


def _quote_grounded(quote: str, haystack: str) -> bool:
    q = _normalise_for_quote_match(quote)
    if not q:
        return False
    if q in haystack:
        return True
    need = max(_QUOTE_MIN_RUN, int(len(q) * 0.6))
    if len(q) < need:
        return False
    for length in range(len(q), need - 1, -1):
        for start in range(0, len(q) - length + 1):
            if q[start : start + length] in haystack:
                return True
    return False


def parse_and_verify(
    raw: str,
    *,
    source_texts: tuple[str, ...],
    focus_text: str = "",
) -> tuple[tuple[CoherenceFinding, ...], int]:
    """解析模型输出并逐条核对引文；返回 (核实的发现, 丢弃的幻觉数)。

    ``focus_text``（通常是简介）非空时，为每条发现标注 ``touches_synopsis``：
    至少一段引文接地于 focus_text 才为 True。为空时全部视为 True（向后兼容）。
    """

    s = (raw or "").strip()
    payload: Any = None
    try:
        payload = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                payload = json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                payload = None
    items = payload.get("contradictions") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return (), 0

    haystack = _normalise_for_quote_match("\n".join(source_texts))
    verified: list[CoherenceFinding] = []
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        qa = str(item.get("quote_a") or "").strip()
        qb = str(item.get("quote_b") or "").strip()
        if not qa or not qb or qa == qb:
            dropped += 1
            continue
        if not _quote_grounded(qa, haystack) or not _quote_grounded(qb, haystack):
            dropped += 1  # 幻觉引文，丢弃——这是整个设计的核心保险
            continue
        kind = str(item.get("kind") or "fact")
        if kind not in ("timeline", "fact", "reference", "number"):
            kind = "fact"
        focus = _normalise_for_quote_match(focus_text)
        touches = True
        if focus:
            touches = _quote_grounded(qa, focus) or _quote_grounded(qb, focus)
        verified.append(
            CoherenceFinding(
                kind=kind,
                quote_a=qa[:60],
                quote_b=qb[:60],
                explanation=str(item.get("why") or "")[:120],
                touches_synopsis=touches,
            )
        )
    return tuple(verified), dropped


async def verify_blurb_coherence(
    session: Any,
    settings: Any,
    *,
    synopsis: str,
    premise: str = "",
    spine: dict[str, Any] | None = None,
    project_id: Any = None,
) -> CoherenceReport:
    """对（premise + spine + synopsis）跑一次引文核对式矛盾扫描。永不 raise。"""

    if not str(synopsis or "").strip():
        return CoherenceReport(findings=(), llm_used=False)
    try:
        from bestseller.services.llm import (  # noqa: PLC0415
            LLMCompletionRequest,
            complete_text,
        )

        system, user = build_coherence_messages(
            synopsis=synopsis, premise=premise, spine=spine
        )
        completion = await complete_text(
            session,
            settings,
            LLMCompletionRequest(
                logical_role="critic",
                model_tier="standard",
                system_prompt=system,
                user_prompt=user,
                fallback_response='{"contradictions": []}',
                prompt_template="blurb_coherence_judge",
                prompt_version="v1",
                max_tokens_override=700,
                project_id=project_id,
            ),
        )
        sources = tuple(
            t for t in (
                premise,
                "\n".join(f"{k}：{v}" for k, v in (spine or {}).items()),
                synopsis,
            ) if str(t or "").strip()
        )
        findings, dropped = parse_and_verify(
            completion.content or "", source_texts=sources, focus_text=synopsis
        )
        return CoherenceReport(
            findings=findings, llm_used=True, dropped_unverified=dropped
        )
    except Exception:
        logger.warning("blurb coherence verify failed (fail-open)", exc_info=True)
        return CoherenceReport(findings=(), llm_used=False)


__all__ = [
    "CoherenceFinding",
    "CoherenceReport",
    "build_coherence_messages",
    "parse_and_verify",
    "verify_blurb_coherence",
]
