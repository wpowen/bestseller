from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.domain.ensemble_arc import (
    EnsembleArcKernel,
    EnsembleCharacterArc,
    IntersectionPoint,
)
from bestseller.domain.narrative import SubplotScheduleEntryRead
from bestseller.services.ensemble_arc_progress_gate import scan_ensemble_arc_progress

pytestmark = pytest.mark.unit


def _arc(chapters: list[int] | None = None) -> EnsembleCharacterArc:
    return EnsembleCharacterArc(
        owner_id=uuid4(),
        arc_kind="redemption",
        private_goal="救回被扣押的妹妹",
        private_obstacle="主角阵营也需要扣押者手里的证据",
        private_payoff="独自完成一次代价选择",
        pov_chapters=chapters or [10, 20, 30, 40],
        intersect_main=[
            IntersectionPoint(chapter=12, effect_on_mainline="提供假情报的代价"),
            IntersectionPoint(chapter=38, effect_on_mainline="牺牲证据换人命"),
        ],
        standalone_value="一条关于债与亲情的短篇线。",
        final_state="带着代价离开主城。",
    )


def test_long_book_requires_minimum_arcs() -> None:
    report = scan_ensemble_arc_progress(
        EnsembleArcKernel(arcs=[_arc(), _arc()], coverage_target=0.1),
        total_chapters=400,
        category="武侠群像",
    )
    assert any(f.code == "ensemble_arc_count_below_floor" for f in report.findings)


def test_arc_must_have_payoff() -> None:
    arc = _arc()
    broken = arc.model_copy(update={"final_state": ""})
    report = scan_ensemble_arc_progress(
        EnsembleArcKernel(arcs=[broken, _arc(), _arc()], coverage_target=0.1),
        total_chapters=400,
    )
    assert any(f.code == "ensemble_arc_missing_payoff" for f in report.findings)


def test_pov_chapters_coverage() -> None:
    report = scan_ensemble_arc_progress(
        EnsembleArcKernel(arcs=[_arc([10]), _arc(), _arc()], coverage_target=0.1),
        total_chapters=400,
    )
    assert any(f.code == "ensemble_arc_pov_coverage_low" for f in report.findings)


def test_arc_integrates_with_subplot_schedule() -> None:
    arc = _arc()
    schedule = SubplotScheduleEntryRead(
        id=uuid4(),
        plot_arc_id=uuid4(),
        arc_code="side-a",
        chapter_number=12,
        prominence="secondary",
        ensemble_arc_ref=str(arc.owner_id),
    )
    assert schedule.ensemble_arc_ref == str(arc.owner_id)

