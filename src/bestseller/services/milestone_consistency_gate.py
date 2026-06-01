"""Milestone consistency hard gate (@ every N chapters)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MILESTONE_CONSISTENCY_FAIL: Final[str] = "MILESTONE_CONSISTENCY_FAIL"


@dataclass(frozen=True)
class MilestoneConsistencyFinding:
    severity: str
    code: str
    detail: str
    chapter_position: int


@dataclass(frozen=True)
class MilestoneConsistencyReport:
    chapter_position: int
    findings: tuple[MilestoneConsistencyFinding, ...]
    blocking: bool

    @property
    def passed(self) -> bool:
        return not self.findings


def is_milestone_chapter(chapter_position: int, *, interval: int = 20) -> bool:
    return chapter_position > 0 and chapter_position % interval == 0


def evaluate_milestone_consistency(
    *,
    chapter_position: int,
    consistency_verdict: str | None,
    interval: int = 20,
    block_on_fail: bool = True,
) -> MilestoneConsistencyReport:
    """Block pipeline when project consistency audit fails at milestones."""

    if not is_milestone_chapter(chapter_position, interval=interval):
        return MilestoneConsistencyReport(
            chapter_position=chapter_position,
            findings=(),
            blocking=False,
        )

    verdict = (consistency_verdict or "").strip().lower()
    if verdict in {"", "pass", "ok", "approved"}:
        return MilestoneConsistencyReport(
            chapter_position=chapter_position,
            findings=(),
            blocking=False,
        )

    finding = MilestoneConsistencyFinding(
        severity="critical",
        code=MILESTONE_CONSISTENCY_FAIL,
        detail=f"milestone ch{chapter_position}: consistency verdict={verdict}",
        chapter_position=chapter_position,
    )
    return MilestoneConsistencyReport(
        chapter_position=chapter_position,
        findings=(finding,),
        blocking=block_on_fail,
    )


__all__ = [
    "MILESTONE_CONSISTENCY_FAIL",
    "MilestoneConsistencyFinding",
    "MilestoneConsistencyReport",
    "evaluate_milestone_consistency",
    "is_milestone_chapter",
]
