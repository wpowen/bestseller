# ruff: noqa: RUF001
"""Deterministic long-form Fanqie ranking readiness gate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "warning", "critical"]


class FanqieLongRankingFinding(BaseModel):
    code: str = Field(min_length=1)
    severity: Severity
    message: str
    evidence: str = ""
    target: str
    repair_hint: str


class FanqieLongRankingReport(BaseModel):
    passed: bool
    project_slug: str = ""
    phase: str = "long_form"
    findings: list[FanqieLongRankingFinding] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


_PRESSURE_TERMS = (
    "逼",
    "押",
    "锁",
    "威胁",
    "否则",
    "必须",
    "不能",
    "开除",
    "诬陷",
    "背锅",
    "冻结",
    "追杀",
    "危机",
    "债",
    "递解",
    "焚尸",
    "焚化",
    "封尸",
    "封井",
    "灭口",
    "疫尸",
    "封条",
    "栽赃",
    "停职",
    "跪",
    "抢",
    "夺",
    "threat",
    "forced",
    "must",
    "or else",
    "danger",
)
_ACTION_TERMS = (
    "抓",
    "按",
    "推",
    "冲",
    "躲",
    "反击",
    "反手",
    "盯",
    "拦",
    "挡",
    "验尸",
    "取证",
    "封存",
    "撬",
    "挑",
    "踢",
    "踢翻",
    "钉",
    "扣住",
    "踩住",
    "掀开",
    "交出",
    "递出",
    "撕",
    "砸",
    "选择",
    "决定",
    "grab",
    "push",
    "counter",
    "act",
    "choose",
)
_PAYOFF_TERMS = (
    "反击",
    "反制",
    "翻盘",
    "打脸",
    "证据",
    "物证",
    "验尸格",
    "钥匙",
    "残符",
    "纸灰",
    "线索",
    "指认",
    "供出",
    "封存",
    "破局",
    "抓到",
    "归",
    "筹码",
    "解锁",
    "到账",
    "发现",
    "真相",
    "曝光",
    "自爆",
    "认账",
    "升级",
    "赢",
    "gain",
    "win",
    "evidence",
    "unlock",
    "reveal",
)
_ABILITY_TERMS = (
    "系统",
    "面板",
    "异能",
    "能力",
    "技能",
    "金手指",
    "天赋",
    "重瞳",
    "阴阳",
    "验尸",
    "银针",
    "符",
    "符箓",
    "茅山",
    "物证",
    "证据链",
    "线索",
    "契约",
    "规则",
    "system",
    "ability",
    "skill",
)
_COST_TERMS = (
    "代价",
    "反噬",
    "咳血",
    "血珠",
    "疼",
    "痛",
    "暴露",
    "停职",
    "递解",
    "损失",
    "冷却",
    "限制",
    "惩罚",
    "流血",
    "cost",
    "limit",
    "exposed",
    "cooldown",
)
_HOOK_TERMS = (
    "？",
    "?",
    "否则",
    "突然",
    "就在这时",
    "门外",
    "下一刻",
    "真相",
    "谁",
    "不能",
    "必须",
    "却",
    "响起",
    "等你",
    "日落前死",
    "知情者",
    "归",
    "还债",
    "灭口",
    "找到了",
    "咬断",
    "吞没",
    "or else",
    "suddenly",
    "truth",
    "but",
)
_EXPOSITION_TERMS = (
    "据说",
    "传说",
    "世界分为",
    "等级分为",
    "背景是",
    "设定",
    "历史上",
    "千年前",
    "规则如下",
    "in this world",
    "the history",
    "there are",
)


def evaluate_fanqie_long_ranking_gate(
    chapter_texts: Mapping[int, str] | Sequence[str],
    *,
    project_slug: str = "",
    protagonist_name: str | None = None,
) -> FanqieLongRankingReport:
    """Evaluate long-form opening and chapter-loop readiness for Fanqie."""

    chapters = _normalize_chapters(chapter_texts)
    findings: list[FanqieLongRankingFinding] = []
    if not chapters:
        findings.append(
            _finding(
                "chapters_missing",
                "critical",
                "没有可检查的章节文本。",
                target="book",
                repair_hint="至少提供前三章或一个长篇试读样章。",
            )
        )
        return FanqieLongRankingReport(
            passed=False,
            project_slug=project_slug,
            findings=findings,
            metrics={"chapter_count": 0},
        )

    findings.extend(
        _opening_findings(
            chapters[0][1],
            protagonist_name=protagonist_name,
        )
    )
    findings.extend(_first_three_findings(chapters))
    findings.extend(_per_chapter_findings(chapters))
    metrics = _metrics(chapters)
    return FanqieLongRankingReport(
        passed=not any(finding.severity == "critical" for finding in findings),
        project_slug=project_slug,
        findings=findings,
        metrics=metrics,
    )


def _opening_findings(
    text: str,
    *,
    protagonist_name: str | None,
) -> list[FanqieLongRankingFinding]:
    findings: list[FanqieLongRankingFinding] = []
    first_50 = _slice(text, 50)
    first_100 = _slice(text, 100)
    first_300 = _slice(text, 300)
    first_1000 = _slice(text, 1000)
    first_3000 = _slice(text, 3000)
    protagonist_visible = bool(protagonist_name and protagonist_name in first_50)

    if not protagonist_visible and not _contains_any(first_50, _PRESSURE_TERMS):
        findings.append(
            _finding(
                "first_50_focus_missing",
                "critical",
                "前50字没有主角焦点或可见压力。",
                evidence=first_50,
                target="opening.first_50",
                repair_hint="开篇第一屏直接给主角处境、威胁或不可退让的当前损失。",
            )
        )
    if not _contains_any(first_100, _PRESSURE_TERMS):
        findings.append(
            _finding(
                "first_100_pressure_missing",
                "critical",
                "前100字没有明确冲突或压迫。",
                evidence=first_100,
                target="opening.first_100",
                repair_hint="把背景说明后移, 在前100字放入逼迫、威胁、损失或限时选择。",
            )
        )
    if not _contains_any(first_300, _ACTION_TERMS):
        findings.append(
            _finding(
                "first_300_reaction_missing",
                "warning",
                "前300字缺少主角动作反应或反制信号。",
                evidence=first_300[-120:],
                target="opening.first_300",
                repair_hint="让主角做出抓、挡、反击、决定等可见动作, 不只心理说明。",
            )
        )
    if not (_contains_any(first_1000, _PAYOFF_TERMS) or _contains_any(first_1000, _HOOK_TERMS)):
        findings.append(
            _finding(
                "first_1000_feedback_missing",
                "critical",
                "前1000字没有小回报、硬升级或下一步钩子。",
                evidence=first_1000[-160:],
                target="opening.first_1000",
                repair_hint="在1000字内完成一次证据、反制、暴露、升级或新危险。",
            )
        )
    if not _has_core_loop(first_3000):
        findings.append(
            _finding(
                "first_3000_core_loop_missing",
                "critical",
                "前3000字没有形成压迫-行动-反馈的可重复核心循环。",
                evidence=_loop_evidence(first_3000),
                target="opening.first_3000",
                repair_hint="明确主角优势如何被压迫触发, 并产生回报、代价或更大危机。",
            )
        )
    return findings


def _first_three_findings(chapters: list[tuple[int, str]]) -> list[FanqieLongRankingFinding]:
    findings: list[FanqieLongRankingFinding] = []
    first_three = dict(chapters[:3])
    chapter_1 = first_three.get(1, "")
    chapter_2 = first_three.get(2, "")
    chapter_3 = first_three.get(3, "")

    chapter_1_has_pressure = _contains_any(chapter_1, _PRESSURE_TERMS)
    chapter_1_has_payoff = _contains_any(chapter_1, _PAYOFF_TERMS)
    if chapter_1 and not (chapter_1_has_pressure and chapter_1_has_payoff):
        findings.append(
            _finding(
                "chapter_1_pressure_feedback_incomplete",
                "critical",
                "第1章没有同时建立压迫和第一次反馈。",
                target="chapters.1",
                repair_hint="第1章至少完成一次压迫-动作-反馈, 让读者知道继续读能得到什么。",
            )
        )
    if len(chapters) >= 2 and not _contains_any(chapter_2, _ABILITY_TERMS):
        findings.append(
            _finding(
                "chapter_2_advantage_not_operational",
                "warning",
                "第2章没有让主角优势、规则或能力进入可操作状态。",
                target="chapters.2",
                repair_hint="第2章让系统、职业能力、规则漏洞或人物筹码实际解决一个问题。",
            )
        )
    if len(chapters) >= 3 and not _has_core_loop(chapter_3):
        findings.append(
            _finding(
                "chapter_3_complete_loop_missing",
                "critical",
                "第3章没有完成第一次完整回报循环。",
                target="chapters.3",
                repair_hint="第3章必须完成压力升级、主角行动、阶段回报和新的追读钩子。",
            )
        )
    return findings


def _per_chapter_findings(chapters: list[tuple[int, str]]) -> list[FanqieLongRankingFinding]:
    findings: list[FanqieLongRankingFinding] = []
    exposition_streak: list[int] = []
    for number, text in chapters:
        has_payoff = _contains_any(text, _PAYOFF_TERMS)
        has_hook = _contains_any(_tail(text), _HOOK_TERMS)
        has_ability = _contains_any(text, _ABILITY_TERMS)
        has_cost = _contains_any(text, _COST_TERMS)
        exposition_only = _is_exposition_only(text)

        if not has_payoff:
            findings.append(
                _finding(
                    "chapter_payoff_missing",
                    "warning",
                    "章节缺少可识别回报、揭露或状态变化。",
                    target=f"chapters.{number}",
                    repair_hint="每章至少给一个小胜、小败、线索、代价、揭露或关系反应。",
                )
            )
        if not has_hook:
            findings.append(
                _finding(
                    "chapter_future_hook_missing",
                    "warning",
                    "章节尾部缺少下一步牵引。",
                    evidence=_tail(text, 120),
                    target=f"chapters.{number}",
                    repair_hint="章末留下新问题、新危险、新选择或下一场明确目标。",
                )
            )
        if has_ability and not has_cost:
            findings.append(
                _finding(
                    "advantage_cost_missing",
                    "warning",
                    "主角优势出现但缺少限制、代价或暴露风险。",
                    target=f"chapters.{number}",
                    repair_hint="给能力增加冷却、疼痛、暴露、资源消耗或道德代价。",
                )
            )

        if exposition_only:
            exposition_streak.append(number)
            if len(exposition_streak) >= 2:
                findings.append(
                    _finding(
                        "consecutive_exposition_only",
                        "critical",
                        "连续章节偏设定说明, 缺少动作、冲突或回报。",
                        evidence=", ".join(str(item) for item in exposition_streak[-3:]),
                        target="chapters.exposition_streak",
                        repair_hint="把设定信息挂到当前冲突里, 通过行动、证据和人物反应释放。",
                    )
                )
        else:
            exposition_streak = []
    return findings


def _normalize_chapters(chapter_texts: Mapping[int, str] | Sequence[str]) -> list[tuple[int, str]]:
    if isinstance(chapter_texts, Mapping):
        return [
            (int(number), str(text or ""))
            for number, text in sorted(chapter_texts.items())
            if str(text or "").strip()
        ]
    return [
        (index, str(text or ""))
        for index, text in enumerate(chapter_texts, start=1)
        if str(text or "").strip()
    ]


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _slice(text: str, cjk_chars: int) -> str:
    if not _contains_cjk(text):
        return text[: cjk_chars * 6]
    chars: list[str] = []
    count = 0
    for char in text:
        chars.append(char)
        if _is_cjk(char):
            count += 1
        if count >= cjk_chars:
            break
    return "".join(chars)


def _tail(text: str, chars: int = 240) -> str:
    stripped = text.strip()
    return stripped[-chars:] if len(stripped) > chars else stripped


def _contains_cjk(text: str) -> bool:
    return any(_is_cjk(char) for char in text)


def _is_cjk(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"


def _has_core_loop(text: str) -> bool:
    return (
        _contains_any(text, _PRESSURE_TERMS)
        and _contains_any(text, _ACTION_TERMS)
        and (
            _contains_any(text, _PAYOFF_TERMS)
            or _contains_any(text, _HOOK_TERMS)
            or _contains_any(text, _COST_TERMS)
        )
    )


def _is_exposition_only(text: str) -> bool:
    return (
        _contains_any(text, _EXPOSITION_TERMS)
        and not _contains_any(text, _ACTION_TERMS)
        and not _contains_any(text, _PRESSURE_TERMS)
        and not _contains_any(text, _PAYOFF_TERMS)
    )


def _loop_evidence(text: str) -> str:
    return (
        f"pressure={_contains_any(text, _PRESSURE_TERMS)}, "
        f"action={_contains_any(text, _ACTION_TERMS)}, "
        f"payoff_or_hook={_contains_any(text, _PAYOFF_TERMS) or _contains_any(text, _HOOK_TERMS)}"
    )


def _metrics(chapters: list[tuple[int, str]]) -> dict[str, Any]:
    return {
        "chapter_count": len(chapters),
        "payoff_chapter_count": sum(
            1 for _, text in chapters if _contains_any(text, _PAYOFF_TERMS)
        ),
        "hook_chapter_count": sum(
            1 for _, text in chapters if _contains_any(_tail(text), _HOOK_TERMS)
        ),
        "ability_chapter_count": sum(
            1 for _, text in chapters if _contains_any(text, _ABILITY_TERMS)
        ),
        "cost_chapter_count": sum(
            1 for _, text in chapters if _contains_any(text, _COST_TERMS)
        ),
    }


def _finding(
    code: str,
    severity: Severity,
    message: str,
    *,
    target: str,
    repair_hint: str,
    evidence: str = "",
) -> FanqieLongRankingFinding:
    return FanqieLongRankingFinding(
        code=code,
        severity=severity,
        message=message,
        evidence=evidence,
        target=target,
        repair_hint=repair_hint,
    )
