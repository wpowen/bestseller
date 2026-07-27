from __future__ import annotations

import pytest

from bestseller.services.rolling_outline import (
    build_macro_plan,
    build_rolling_outline_plan,
    load_rolling_outline_plan,
    promote_rolling_outline,
)


def _macro(count: int = 20):
    return build_macro_plan(
        {"chapter_number": chapter, "anchor": f"anchor-{chapter}"}
        for chapter in range(1, count + 1)
    )


def test_macro_plan_is_contiguous_immutable_and_hashed() -> None:
    plan = build_macro_plan(
        [
            {"chapter_number": 1, "anchor": "anchor-1", "metadata": {"nested": [1, 2]}},
            {"chapter_number": 2, "anchor": "anchor-2"},
        ]
    )
    assert plan.total_chapters == 2
    assert plan.slots[0].chapter_number == 1
    assert plan.slots[0].anchor == "anchor-1"
    assert plan.macro_plan_hash == build_macro_plan(plan.to_dict()["slots"]).macro_plan_hash
    with pytest.raises(TypeError):
        plan.slots[0].metadata["x"] = "nope"  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.slots[0].metadata["nested"][0] = 9  # type: ignore[index]
    assert plan.to_dict()["slots"][0]["metadata"]["nested"] == [1, 2]


def test_rolling_plan_defaults_to_six_chapters_and_batch_four() -> None:
    result = build_rolling_outline_plan(
        _macro(),
        current_state_snapshot={"current_chapter": 0, "canon": ["fact-a"]},
        next_macro_anchor="anchor-7",
        source_snapshot_hash="design-v1",
    )
    assert (result.window_start, result.window_end, result.window_size) == (1, 6, 6)
    assert result.batch_size == 4
    assert result.source_snapshot_hash == "design-v1"
    assert result.current_state_hash
    assert result.macro_plan_hash == _macro().macro_plan_hash
    assert result.previous_state_hash
    assert result.plan_hash == build_rolling_outline_plan(
        _macro(),
        current_state_snapshot={"current_chapter": 0, "canon": ["fact-a"]},
        next_macro_anchor="anchor-7",
        source_snapshot_hash="design-v1",
    ).plan_hash


def test_window_rejects_bounds_and_confirmed_or_past_chapters() -> None:
    macro = _macro(12)
    with pytest.raises(ValueError, match="bounds"):
        build_rolling_outline_plan(
            macro,
            current_state_snapshot={},
            next_macro_anchor="x",
            source_snapshot_hash="design-v1",
            window_start=8,
            window_size=6,
        )
    with pytest.raises(ValueError, match="confirmed"):
        build_rolling_outline_plan(
            macro,
            current_state_snapshot={},
            next_macro_anchor="x",
            source_snapshot_hash="design-v1",
            window_start=2,
            confirmed_chapters=(1, 2),
        )
    with pytest.raises(ValueError, match="window_size"):
        build_rolling_outline_plan(
            _macro(),
            current_state_snapshot={},
            next_macro_anchor="x",
            source_snapshot_hash="design-v1",
            window_size=5,
        )
    result = build_rolling_outline_plan(
        _macro(),
        current_state_snapshot={"current_chapter": 3},
        next_macro_anchor="x",
        source_snapshot_hash="design-v1",
    )
    assert (result.window_start, result.window_end) == (4, 9)


def test_status_promotion_is_explicit_and_does_not_mutate_source() -> None:
    plan = build_rolling_outline_plan(
        _macro(),
        current_state_snapshot={},
        next_macro_anchor={"chapter": 7},
        source_snapshot_hash="design-v1",
    )
    approved = promote_rolling_outline(plan, "approved")
    assert plan.status == "planned"
    assert approved.status == "approved"
    assert promote_rolling_outline(plan, "needs_replan").status == "needs_replan"
    with pytest.raises(ValueError, match="invalid"):
        promote_rolling_outline(plan, "draft")


def test_persisted_rolling_plan_rejects_tampering_and_stale_status() -> None:
    macro = _macro(12)
    approved = promote_rolling_outline(
        build_rolling_outline_plan(
            macro,
            current_state_snapshot={"current_chapter": 0, "facts": []},
            next_macro_anchor=macro.slots[6].to_dict(),
            source_snapshot_hash="design-v1",
        ),
        "approved",
    )
    loaded_macro, loaded_plan = load_rolling_outline_plan(
        macro.to_dict(), approved.to_dict(), source_snapshot_hash="design-v1"
    )
    assert loaded_macro.macro_plan_hash == macro.macro_plan_hash
    assert loaded_plan.plan_hash == approved.plan_hash

    bad_state = approved.to_dict()
    bad_state["current_state_snapshot"]["facts"].append("tampered")
    with pytest.raises(ValueError, match="state hash"):
        load_rolling_outline_plan(macro.to_dict(), bad_state)

    stale = approved.to_dict()
    stale["status"] = "needs_replan"
    with pytest.raises(ValueError, match="not approved"):
        load_rolling_outline_plan(macro.to_dict(), stale)

    bad_macro = macro.to_dict()
    bad_macro["slots"].append("corrupt")
    with pytest.raises(ValueError, match="every rolling macro slot"):
        load_rolling_outline_plan(bad_macro, approved.to_dict())
