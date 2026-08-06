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

import inspect

from bestseller.services.planner import (
    _outline_judge_repair_directives,
    _outline_next_repair_directives,
    _select_active_commercial_repair_directives,
)


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


def test_directives_are_bounded_to_preserve_writer_prompt_budget() -> None:
    payload = {
        "passed": False,
        "repair_directives": [f"directive-{index}" for index in range(20)],
    }

    assert _outline_judge_repair_directives(
        payload, round_idx=0, max_rounds=1
    ) == [f"directive-{index}" for index in range(8)]


def test_creator_selected_effect_gaps_enter_next_repair_round() -> None:
    directives = _outline_next_repair_directives(
        {"passed": True, "repair_directives": []},
        {
            "chapters": [
                {"chapter_number": 1, "title": "第一章"},
                {"chapter_number": 2, "title": "第二章"},
                {"chapter_number": 3, "title": "第三章"},
            ]
        },
        project_metadata={
            "story_enhancers": {
                "effect_skills": ["comedy_engine", "hype_satisfaction_engine"]
            }
        },
        round_idx=0,
        max_rounds=2,
    )

    assert len(directives) == 2
    assert "`comedy_engine`" in directives[0]
    assert "`hype_satisfaction_engine`" in directives[1]
    assert all("不得把该效果或全部建书效果硬塞进每一章" in item for item in directives)


def test_creator_selected_effect_repairs_share_the_existing_round_bound() -> None:
    assert (
        _outline_next_repair_directives(
            {"passed": False, "repair_directives": ["commercial"]},
            {"chapters": [{"chapter_number": 1, "title": "第一章"}]},
            project_metadata={
                "story_enhancers": {"effect_skills": ["comedy_engine"]}
            },
            round_idx=2,
            max_rounds=2,
        )
        == []
    )


def test_both_full_and_progressive_planners_consume_enhancer_repair_directives() -> None:
    from bestseller.services import planner

    assert "_outline_next_repair_directives" in inspect.getsource(
        planner.generate_novel_plan
    )
    assert "_outline_next_repair_directives" in inspect.getsource(
        planner.generate_volume_plan
    )


def test_current_round_directives_replace_outer_heal_directives() -> None:
    assert _select_active_commercial_repair_directives(
        stored=["old-a", "old-b"],
        current=["new-a"],
    ) == ["new-a"]


def test_outer_heal_directives_seed_first_round_only() -> None:
    assert _select_active_commercial_repair_directives(
        stored=["old-a", "old-b"],
        current=[],
    ) == ["old-a", "old-b"]
