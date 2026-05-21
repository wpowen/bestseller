"""Opening three-chapter methodology gate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bestseller.services.checker_schema import CheckerIssue, CheckerReport, Severity
from bestseller.services.hype_engine import HypeType
from bestseller.services.methodology_overlay import text

OPENING_GATE_AGENT = "opening-three-function-gate"

_PRESSURE_TOKENS = (
    "压力",
    "危机",
    "追杀",
    "威胁",
    "倒计时",
    "时限",
    "失去",
    "暴露",
    "代价",
    "必须",
    "deadline",
    "threat",
    "risk",
    "pressure",
    "lose",
    "cost",
)
_DESIRE_TOKENS = (
    "想要",
    "决定",
    "必须",
    "追问",
    "问题",
    "线索",
    "目标",
    "下一步",
    "want",
    "decide",
    "question",
    "clue",
    "goal",
    "next",
)
_COST_TOKENS = ("代价", "付出", "受伤", "牺牲", "失去", "惩罚", "cost", "pay", "lose")
_CHOICE_TOKENS = ("选择", "决定", "出手", "行动", "拒绝", "答应", "choice", "choose", "act")
_STATE_TOKENS = ("改变", "不再", "成为", "获得", "失去", "暴露", "升级", "change", "becomes")
_LONG_DESIRE_TOKENS = (
    "真相",
    "幕后",
    "更大",
    "长期",
    "下一章",
    "第四章",
    "主线",
    "truth",
    "larger",
    "long",
    "next chapter",
    "mainline",
)


def evaluate_opening_three_function(
    *,
    chapter_texts: Sequence[tuple[int, str]] = (),
    chapter_outlines: Sequence[Mapping[str, Any]] = (),
    chapter_hype: Sequence[tuple[int, HypeType | str | None]] = (),
    mode: str = "audit_only",
) -> CheckerReport:
    """Check whether chapters 1-3 carry distinct opening responsibilities."""

    texts = _chapter_text_map(chapter_texts)
    outlines = _outline_text_map(chapter_outlines)
    hype = _hype_map(chapter_hype)
    issues: list[CheckerIssue] = []

    ch1 = _combined_text(1, texts, outlines)
    ch2 = _combined_text(2, texts, outlines)
    ch3 = _combined_text(3, texts, outlines)

    if ch1:
        _require(
            issues,
            code="OPENING_CH1_PRESSURE_MISSING",
            ok=_contains_any(ch1, _PRESSURE_TOKENS),
            description="第一章缺少主角处境中的异常压力或失败风险。",
            suggestion="补出主角正在承受的具体压力、时限、暴露风险或不可逆代价。",
            mode=mode,
        )
        _require(
            issues,
            code="OPENING_CH1_FIRST_DESIRE_MISSING",
            ok=_contains_any(ch1, _DESIRE_TOKENS),
            description="第一章没有形成第一追问或主角下一步欲望。",
            suggestion="让章节结尾留下读者问题，并让主角产生可执行的下一步目标。",
            mode=mode,
        )
    if ch2:
        _require(
            issues,
            code="OPENING_CH2_COST_PROOF_MISSING",
            ok=_contains_any(ch2, _COST_TOKENS),
            description="第二章没有证明行动失败或继续追查的具体代价。",
            suggestion="补出受伤、失去资源、暴露身份、关系破裂或时限压缩等代价证明。",
            mode=mode,
        )
        _require(
            issues,
            code="OPENING_CH2_CHOICE_ACTION_MISSING",
            ok=_contains_any(ch2, _CHOICE_TOKENS),
            description="第二章缺少主角在压力下做出的选择或行动。",
            suggestion="让主角做出不可空转的选择，行动结果继续压向第三章。",
            mode=mode,
        )
    if ch3:
        _require(
            issues,
            code="OPENING_CH3_STATE_CHANGE_MISSING",
            ok=_contains_any(ch3, _STATE_TOKENS),
            description="第三章没有产生主角状态、资源、关系或局面的可见变化。",
            suggestion="把前三章短刺激结算成状态变化，而不是回到开篇原点。",
            mode=mode,
        )
        _require(
            issues,
            code="OPENING_CH3_LONG_DESIRE_MISSING",
            ok=_contains_any(ch3, _LONG_DESIRE_TOKENS),
            description="第三章没有把短期刺激转成长线欲望或更大问题。",
            suggestion="在第三章结尾打开第四章必须追下去的主线问题。",
            mode=mode,
        )
    if _has_repeated_hype(hype):
        issues.append(
            _issue(
                "OPENING_THREE_REPEATED_STIMULUS",
                "前三章重复同一种刺激，缺少压力、代价和长线欲望的递进分工。",
                "调整前三章职责：第一章立追问，第二章证代价，第三章开长线。",
                mode=mode,
                severity="medium",
            )
        )

    missing_chapters = [chapter for chapter in (1, 2, 3) if not _combined_text(chapter, texts, outlines)]
    passed = not issues
    score = max(0, 100 - sum(_severity_penalty(issue.severity) for issue in issues))
    return CheckerReport(
        agent=OPENING_GATE_AGENT,
        chapter=1,
        overall_score=score,
        passed=passed,
        issues=tuple(issues),
        metrics={
            "checked_chapters": [chapter for chapter in (1, 2, 3) if chapter not in missing_chapters],
            "missing_chapters": missing_chapters,
            "hype_types": {chapter: value for chapter, value in hype.items() if chapter <= 3},
        },
        summary=(
            "黄金三章职责分工通过。"
            if passed
            else f"黄金三章职责分工发现 {len(issues)} 个方法论风险。"
        ),
    )


def _chapter_text_map(chapter_texts: Sequence[tuple[int, str]]) -> dict[int, str]:
    return {int(chapter): str(body or "") for chapter, body in chapter_texts}


def _outline_text_map(chapter_outlines: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    out: dict[int, str] = {}
    for outline in chapter_outlines:
        number = outline.get("chapter_number") or outline.get("number")
        if not number:
            continue
        parts = [
            text(outline.get(key))
            for key in (
                "title",
                "goal",
                "core_conflict",
                "summary",
                "opening_state",
                "closing_hook",
                "methodology_contract",
            )
        ]
        out[int(number)] = " ".join(part for part in parts if part)
    return out


def _hype_map(chapter_hype: Sequence[tuple[int, HypeType | str | None]]) -> dict[int, str]:
    out: dict[int, str] = {}
    for chapter, raw in chapter_hype:
        if raw is None:
            continue
        out[int(chapter)] = raw.value if isinstance(raw, HypeType) else str(raw)
    return out


def _combined_text(chapter: int, texts: Mapping[int, str], outlines: Mapping[int, str]) -> str:
    return f"{texts.get(chapter, '')} {outlines.get(chapter, '')}".strip().lower()


def _contains_any(body: str, tokens: Sequence[str]) -> bool:
    lowered = body.lower()
    return any(token.lower() in lowered for token in tokens)


def _has_repeated_hype(hype: Mapping[int, str]) -> bool:
    values = [hype.get(chapter) for chapter in (1, 2, 3)]
    present = [value for value in values if value]
    return len(present) == 3 and len(set(present)) == 1


def _require(
    issues: list[CheckerIssue],
    *,
    code: str,
    ok: bool,
    description: str,
    suggestion: str,
    mode: str,
) -> None:
    if ok:
        return
    issues.append(_issue(code, description, suggestion, mode=mode))


def _issue(
    code: str,
    description: str,
    suggestion: str,
    *,
    mode: str,
    severity: Severity = "high",
) -> CheckerIssue:
    return CheckerIssue(
        id=code,
        type="methodology_opening",
        severity=severity,
        location="chapters 1-3",
        description=description,
        suggestion=suggestion,
        can_override=str(mode) != "strict",
        allowed_rationales=("EDITORIAL_INTENT", "ARC_TIMING") if str(mode) != "strict" else (),
    )


def _severity_penalty(severity: Severity) -> int:
    return {"critical": 25, "high": 15, "medium": 8, "low": 3}[severity]


__all__ = ["OPENING_GATE_AGENT", "evaluate_opening_three_function"]
