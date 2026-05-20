"""番茄短故事开篇/解锁段门禁（全文比例缩放）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from bestseller.services.qimao_opening_gate import (
    QimaoOpeningFinding,
    evaluate_qimao_opening_gate,
)


@dataclass(frozen=True)
class FanqieShortOpeningReport:
    passed: bool
    unlock_zone_words: int
    total_words: int
    unlock_ratio: float
    findings: tuple[QimaoOpeningFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "unlock_zone_words": self.unlock_zone_words,
            "total_words": self.total_words,
            "unlock_ratio": self.unlock_ratio,
            "findings": [
                {
                    "code": f.code,
                    "severity": f.severity,
                    "message": f.message,
                    "evidence": f.evidence,
                }
                for f in self.findings
            ],
        }


def evaluate_fanqie_short_opening_gate(
    full_text: str,
    *,
    unlock_line_ratio: float = 0.30,
    protagonist_name: str | None = None,
) -> FanqieShortOpeningReport:
    """检查全文前 ``unlock_line_ratio`` 是否满足番茄短故事开篇要求。"""
    text = (full_text or "").strip()
    total_words = len(text)
    unlock_zone_words = max(50, int(total_words * unlock_line_ratio))
    unlock_slice = text[:unlock_zone_words]

    qimao_report = evaluate_qimao_opening_gate(
        unlock_slice,
        opening_contract={"protagonist_name": protagonist_name or "我"},
        protagonist_name=protagonist_name or "我",
    )
    return FanqieShortOpeningReport(
        passed=qimao_report.passed,
        unlock_zone_words=unlock_zone_words,
        total_words=total_words,
        unlock_ratio=unlock_line_ratio,
        findings=tuple(qimao_report.findings),
    )


def scan_fanqie_short_taboo_signals(full_text: str) -> list[str]:
    """轻量禁忌扫描（非 LLM）。"""
    issues: list[str] = []
    haystack = full_text or ""
    if "求过审" in haystack or "求审核" in haystack:
        issues.append("contains_review_begging")
    if "下章" in haystack and "且听" in haystack:
        issues.append("serial_chapter_teaser")
    if haystack.count(haystack[:200]) > 1 and len(haystack) > 400:
        issues.append("possible_duplicate_opening")
    return issues
