from __future__ import annotations

from bestseller.domain.volume_plan import VolumeMilestone, VolumePlanV2
from bestseller.services.volume_plan_resolution_gate import evaluate_volume_plan_resolution


def _plan(*ranges: tuple[int, int]) -> VolumePlanV2:
    return VolumePlanV2(
        volume_no=1,
        chapter_range=(1, 50),
        milestones=tuple(
            VolumeMilestone(
                chapter_range=chapter_range,
                milestone_label=f"林渊核验第{index}段井口账印并付代价",
                required_evidence=("井口账印",),
            )
            for index, chapter_range in enumerate(ranges, start=1)
        ),
    )


def test_blocks_coarse_milestone_window() -> None:
    verdict = evaluate_volume_plan_resolution(
        [_plan((1, 11), (12, 20), (21, 30), (31, 40), (41, 50))]
    )

    assert verdict.verdict == "blocked"
    assert "VOLUME_PLAN_TOO_COARSE" in {finding.code for finding in verdict.findings}


def test_flags_gap_and_overlap() -> None:
    verdict = evaluate_volume_plan_resolution(
        [_plan((1, 8), (8, 16), (17, 24), (25, 32), (33, 40))]
    )

    codes = {finding.code for finding in verdict.findings}
    assert "MILESTONE_GAP" in codes
    assert "MILESTONE_OVERLAP" in codes


def test_passes_resolved_volume_plan() -> None:
    verdict = evaluate_volume_plan_resolution(
        [_plan((1, 10), (11, 20), (21, 30), (31, 40), (41, 50))]
    )

    assert verdict.passed is True
    assert verdict.metrics["coverage_by_volume"]["1"] == 1.0
