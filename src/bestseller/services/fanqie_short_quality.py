"""番茄短故事全文质量复审（Phase 2 门控）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bestseller.domain.fanqie_short import (
    SCENE_HOOK_MIN_SCORE,
    SCENE_MIN_SCORE,
    SCENE_PAYOFF_MIN_SCORE,
)
from bestseller.services.fanqie_short_opening_gate import (
    evaluate_fanqie_short_opening_gate,
    scan_fanqie_short_taboo_signals,
)
from bestseller.services.fanqie_short_ranking_gate import (
    FanqieRankingGateReport,
    evaluate_fanqie_ranking_readiness,
)


@dataclass(frozen=True)
class FanqieWholePieceReview:
    passed: bool
    opening_passed: bool
    ranking_passed: bool
    taboo_signals: list[str]
    notes: list[str]
    ranking_report: FanqieRankingGateReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "opening_passed": self.opening_passed,
            "ranking_passed": self.ranking_passed,
            "taboo_signals": self.taboo_signals,
            "notes": self.notes,
            "ranking_report": (
                self.ranking_report.to_dict() if self.ranking_report is not None else None
            ),
            "thresholds": {
                "scene_min_score": SCENE_MIN_SCORE,
                "hook_min_score": SCENE_HOOK_MIN_SCORE,
                "payoff_min_score": SCENE_PAYOFF_MIN_SCORE,
            },
        }


def review_whole_fanqie_short_story(
    full_text: str,
    *,
    unlock_line_ratio: float = 0.30,
    protagonist_name: str | None = None,
) -> FanqieWholePieceReview:
    opening = evaluate_fanqie_short_opening_gate(
        full_text,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
    )
    taboo = scan_fanqie_short_taboo_signals(full_text)
    ranking = evaluate_fanqie_ranking_readiness(
        full_text,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
    )
    notes: list[str] = []
    if not opening.passed:
        notes.append("前30%未通过番茄短故事 opening gate")
    if not ranking.passed:
        codes = [
            finding.code
            for finding in ranking.findings
            if finding.severity == "critical"
        ]
        notes.append("榜单级门禁未过：" + ", ".join(codes[:8]))
    if taboo:
        notes.append(f"禁忌信号：{', '.join(taboo)}")
    if len(full_text.strip()) < 500:
        notes.append("全文过短")
    passed = opening.passed and ranking.passed and not taboo and len(full_text.strip()) >= 500
    return FanqieWholePieceReview(
        passed=passed,
        opening_passed=opening.passed,
        ranking_passed=ranking.passed,
        taboo_signals=taboo,
        notes=notes,
        ranking_report=ranking,
    )
