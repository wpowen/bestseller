"""G4 (xianxia benchmark): outline commercial judge findings drive in-loop repair.

Production failure (shilouyan-bench-v1): volume-1 outline scored 0.340 with 8
blocking codes, yet was saved verbatim — the judge ``payload`` (with its
``blocking_codes`` / ``repair_targets``) was only attached as metadata. The
``_commercial_repair_directives`` injection path read directives from
``project.metadata_json`` (populated only by an *outer* heal run on the NEXT
generate), so a single planning run never regenerated. G4 closes the loop
WITHIN the run, bounded by a round counter.
"""

from __future__ import annotations

from bestseller.services.planner import _outline_judge_repair_directives


def test_no_directives_when_judge_passed() -> None:
    payload = {"passed": True, "repair_directives": ["x"]}
    assert _outline_judge_repair_directives(payload, round_idx=0, max_rounds=1) == []


def test_directives_when_failed_within_budget() -> None:
    payload = {
        "passed": False,
        "repair_directives": ["【整改·B-SCENE】补具体动作", "【整改·B-HOOK】补章末钩子"],
    }
    out = _outline_judge_repair_directives(payload, round_idx=0, max_rounds=1)
    assert out == ["【整改·B-SCENE】补具体动作", "【整改·B-HOOK】补章末钩子"]


def test_bounded_by_max_rounds() -> None:
    """Once the round budget is spent, no more directives even if still failing
    (prevents the generate→reject→regenerate death loop)."""
    payload = {"passed": False, "repair_directives": ["d"]}
    assert _outline_judge_repair_directives(payload, round_idx=1, max_rounds=1) == []


def test_no_directives_when_none_available() -> None:
    payload = {"passed": False, "repair_directives": []}
    assert _outline_judge_repair_directives(payload, round_idx=0, max_rounds=1) == []


def test_none_payload_is_safe() -> None:
    assert _outline_judge_repair_directives(None, round_idx=0, max_rounds=1) == []
