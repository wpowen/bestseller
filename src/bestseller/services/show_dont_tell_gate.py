"""Narrative-layer show-don't-tell gate."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from bestseller.services.checker_schema import CheckerIssue, CheckerReport

_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "SHOW_DONT_TELL_MOTIVE_EXPLANATION",
        "动机解释",
        re.compile(r"(?:他|她|[一-鿿]{2,4})(?:知道|明白|意识到)[^。！？!?]{0,60}(?:但|却|所以|因此)"),
    ),
    (
        "SHOW_DONT_TELL_EMOTION_NAMING",
        "情绪命名",
        re.compile(r"[一-鿿]{0,4}(?:恐惧|愤怒|决断|压抑|绝望|悲伤|痛苦|不甘|震惊)(?:涌上心头|在心里|从心底|让他|让她|使他|使她)"),
    ),
    (
        "SHOW_DONT_TELL_ABILITY_SUMMARY",
        "能力总结",
        re.compile(r"(?:他的|她的|[一-鿿]{2,4}的)(?:判断力|直觉|能力|力量|经验|理智)告诉(?:他|她)"),
    ),
    (
        "SHOW_DONT_TELL_RELATIONSHIP_LABEL",
        "关系定性",
        re.compile(r"(?:他|她|[一-鿿]{2,4})和[一-鿿]{2,4}的关系(?:开始|正在|已经)?(?:变化|改变|升温|破裂)"),
    ),
)
_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ("“", "”"),
    ("‘", "’"),
    ("「", "」"),
    ("『", "』"),
    ('"', '"'),
)


@dataclass(frozen=True)
class ShowDontTellFinding:
    code: str
    category: str
    excerpt: str
    location: str


@dataclass(frozen=True)
class ShowDontTellReport:
    chapter_position: int
    findings: tuple[ShowDontTellFinding, ...] = ()
    metrics: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.findings

    def to_checker_report(self) -> CheckerReport:
        issues = [
            CheckerIssue(
                id=f.code,
                type="show_dont_tell",
                severity="medium",
                location=f.location,
                description=f"叙述层直接讲出人物心理或关系：{f.category}",
                suggestion="改成具身动作、停顿、物件互动、短对白或环境反应，不要解释角色为什么这么做。",
                can_override=True,
            )
            for f in self.findings
        ]
        score = 100 if not issues else max(0, 100 - len(issues) * 12)
        return CheckerReport(
            agent="show-dont-tell-gate",
            chapter=self.chapter_position,
            overall_score=score,
            passed=self.passed,
            issues=tuple(issues),
            metrics={**self.metrics, "finding_count": len(self.findings)},
            summary=(
                "show-dont-tell passed"
                if self.passed
                else f"show-dont-tell found {len(issues)} telling issue(s)"
            ),
        )


def check_show_dont_tell_gate(
    text: str,
    *,
    chapter_position: int,
) -> ShowDontTellReport:
    if not text:
        return ShowDontTellReport(chapter_position=chapter_position)
    dialogue_ranges = _find_dialogue_ranges(text)
    findings: list[ShowDontTellFinding] = []
    for code, category, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if _is_in_ranges(match.start(), dialogue_ranges):
                continue
            findings.append(
                ShowDontTellFinding(
                    code=code,
                    category=category,
                    excerpt=_excerpt(text, match.start(), match.end()),
                    location=f"chars {match.start()}-{match.end()}",
                )
            )
    metrics: dict[str, int] = {}
    for finding in findings:
        metrics[finding.code] = metrics.get(finding.code, 0) + 1
    return ShowDontTellReport(
        chapter_position=chapter_position,
        findings=tuple(findings),
        metrics=metrics,
    )


def _excerpt(text: str, start: int, end: int, radius: int = 36) -> str:
    return text[max(0, start - radius): min(len(text), end + radius)].replace("\n", " ")


def _find_dialogue_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for open_q, close_q in _QUOTE_PAIRS:
        i = 0
        while True:
            start = text.find(open_q, i)
            if start < 0:
                break
            end = text.find(close_q, start + 1)
            if end < 0:
                break
            ranges.append((start, end + 1))
            i = end + 1
    ranges.sort()
    return ranges


def _is_in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
    for start, end in ranges:
        if start <= pos < end:
            return True
        if start > pos:
            break
    return False


__all__ = [
    "ShowDontTellFinding",
    "ShowDontTellReport",
    "check_show_dont_tell_gate",
]
