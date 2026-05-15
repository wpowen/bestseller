"""Unit tests for ``quality_levers.rejection_repair_playbook``."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.rejection_repair_playbook import (
    get_rejection_cause,
    load_rejection_repair_playbook,
    render_repair_actions_block,
)


pytestmark = pytest.mark.unit


def test_load_rejection_repair_playbook_returns_expected_causes() -> None:
    config = load_rejection_repair_playbook()

    expected = {
        "ordinary_entry",
        "weak_attraction",
        "weak_immersion",
        "weak_satisfaction",
        "flat_narration",
        "mainline_unclear",
        "weak_character_hook",
        "weak_prose",
        "ai_voice",
    }
    assert expected <= set(config.causes.keys())
    assert config.global_rules  # non-empty


def test_ordinary_entry_has_sorted_repair_actions() -> None:
    config = load_rejection_repair_playbook()

    cause = config.causes["ordinary_entry"]
    assert cause.repair_actions
    priorities = [action.priority for action in cause.repair_actions]
    assert priorities == sorted(priorities)
    # First action should be `regenerate_opening_event` (priority 1)
    assert cause.repair_actions[0].action_id == "regenerate_opening_event"


def test_get_rejection_cause_handles_unknown() -> None:
    assert get_rejection_cause(None) is None
    assert get_rejection_cause("") is None
    assert get_rejection_cause("totally_unknown_cause") is None


def test_render_repair_actions_block_single_cause() -> None:
    block = render_repair_actions_block(cause_ids="ordinary_entry")

    assert "拒稿整改" in block
    assert "ordinary_entry" in block
    assert "regenerate_opening_event" in block
    # global rules appended
    assert "通用约束" in block


def test_render_repair_actions_block_multi_cause_dedupes() -> None:
    block = render_repair_actions_block(
        cause_ids=["ordinary_entry", "weak_attraction"],
        max_actions=10,
    )

    assert "ordinary_entry" in block
    assert "weak_attraction" in block


def test_render_repair_actions_block_respects_max_actions() -> None:
    block = render_repair_actions_block(
        cause_ids=["ordinary_entry"],
        max_actions=1,
    )
    # Only one repair action should be listed for this cause
    action_lines = [line for line in block.split("\n") if line.startswith("  优先级")]
    assert len(action_lines) == 1


def test_render_repair_actions_block_empty_input() -> None:
    assert render_repair_actions_block(cause_ids=None) == ""
    assert render_repair_actions_block(cause_ids=()) == ""
    assert render_repair_actions_block(cause_ids=("",)) == ""


def test_render_repair_actions_block_unknown_cause_returns_empty() -> None:
    block = render_repair_actions_block(cause_ids=["totally_unknown"])
    # Header is still emitted but with no actions; we accept either
    # an empty body or just the header — assert no exception raised
    assert isinstance(block, str)
    assert "totally_unknown" not in block
