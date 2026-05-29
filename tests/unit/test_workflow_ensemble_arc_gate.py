from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.workflows import _run_ensemble_arc_progress_gate

pytestmark = pytest.mark.unit


def _arc() -> dict[str, object]:
    return {
        "owner_id": str(uuid4()),
        "arc_kind": "redemption",
        "private_goal": "救回被扣押的妹妹",
        "private_obstacle": "主角阵营也需要扣押者手里的证据",
        "private_payoff": "独自完成一次代价选择",
        "pov_chapters": [10, 20, 30, 40],
        "intersect_main": [
            {"chapter": 12, "effect_on_mainline": "提供假情报的代价"},
            {"chapter": 38, "effect_on_mainline": "牺牲证据换人命"},
        ],
        "standalone_value": "一条关于债与亲情的短篇线。",
        "final_state": "带着代价离开主城。",
    }


def test_ensemble_arc_gate_is_flagged_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BESTSELLER_METHODOLOGY_V2", raising=False)
    project = SimpleNamespace(
        genre="武侠群像",
        sub_genre=None,
        target_chapters=400,
        metadata_json={"ensemble_arc_kernel": {"arcs": [_arc(), _arc()]}},
    )

    assert _run_ensemble_arc_progress_gate(project) is None


def test_ensemble_arc_gate_runs_when_methodology_v2_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BESTSELLER_METHODOLOGY_V2", "1")
    project = SimpleNamespace(
        genre="武侠群像",
        sub_genre=None,
        target_chapters=400,
        metadata_json={"ensemble_arc_kernel": {"arcs": [_arc(), _arc()]}},
    )

    report = _run_ensemble_arc_progress_gate(project)

    assert report is not None
    assert any(finding.code == "ensemble_arc_count_below_floor" for finding in report.findings)
