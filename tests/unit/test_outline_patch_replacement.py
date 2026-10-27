import copy

import pytest

from bestseller.services.planner import (
    _apply_outline_commercial_patches,
    _outline_baseline_covers_window,
    _prefer_outline_repair_baseline,
    _restore_persisted_outline_repair_baseline,
)

pytestmark = pytest.mark.unit


def _outline():
    return {
        "chapters": [
            {
                "chapter_number": 1,
                "title": "Unchanged title",
                "goal": "Keep the ship",
                "tail_hook_description": "Forbidden remote vision finds the code.",
                "world_state_deltas": ["Unsupported new power"],
                "causal_contract": {
                    "protagonist_choice": "Read minds without contact",
                    "pressure": "Storm",
                },
                "scenes": [
                    {
                        "scene_number": 1,
                        "participants": ["Mira", "Wrong person"],
                        "purpose": {"story": "Use forbidden power", "emotion": "Relief"},
                        "exit_state": {"summary": "Forbidden power succeeds", "location": "Deck"},
                    }
                ],
            }
        ]
    }


def _patch(scope, field, value):
    return {"chapter_number": 1, "scene_number": 1, "scope": scope, "field": field, "value": value}


def test_targeted_patch_replaces_wrong_fact_without_touching_other_facts():
    original = _outline()
    before = copy.deepcopy(original)
    repaired = _apply_outline_commercial_patches(
        original,
        [
            _patch("causal_contract", "protagonist_choice", "Touch the shard to sense a fear"),
            _patch("scene", "purpose", {"story": "Ask the captain for the code"}),
            _patch("scene", "exit_state", {"summary": "Captain gives the code"}),
            _patch("scene", "participants", ["Mira"]),
            _patch("chapter", "tail_hook_description", "The captain asks for a favor"),
            _patch("chapter", "world_state_deltas", []),
        ],
    )
    chapter = repaired["chapters"][0]
    assert chapter["causal_contract"] == {
        "protagonist_choice": "Touch the shard to sense a fear",
        "pressure": "Storm",
    }
    assert chapter["scenes"][0]["purpose"] == {
        "story": "Ask the captain for the code",
        "emotion": "Relief",
    }
    assert chapter["scenes"][0]["exit_state"] == {
        "summary": "Captain gives the code",
        "location": "Deck",
    }
    assert chapter["scenes"][0]["participants"] == ["Mira"]
    assert chapter["tail_hook_description"] == "The captain asks for a favor"
    assert chapter["world_state_deltas"] == []
    assert chapter["title"] == before["chapters"][0]["title"]
    assert chapter["goal"] == before["chapters"][0]["goal"]
    assert original == before


@pytest.mark.parametrize(
    "patch",
    [
        _patch("chapter", "title", "Hijacked"),
        _patch("chapter", "chapter_number", 5),
        _patch("scene", "purpose", "Destroy object schema"),
        _patch("causal_contract", "protagonist_choice", {"bad": "shape"}),
        _patch("causal_contract", "protagonist_choice", "   "),
        {**_patch("chapter", "goal", "Wrong target"), "chapter_number": 999},
        _patch("scene", "purpose", {"story": None}),
    ],
)
def test_invalid_or_out_of_scope_patch_cannot_damage_outline(patch):
    original = _outline()
    assert _apply_outline_commercial_patches(original, [patch]) == original


def test_reapplying_replacement_is_idempotent():
    patches = [_patch("scene", "purpose", {"story": "Corrected action"})]
    once = _apply_outline_commercial_patches(_outline(), patches)
    assert _apply_outline_commercial_patches(once, patches) == once


def test_goal_alias_is_updated_only_when_already_present():
    original = _outline()
    original["chapters"][0]["chapter_goal"] = "Keep the ship"
    result = _apply_outline_commercial_patches(
        original, [_patch("chapter", "goal", "Save the crew")]
    )
    assert result["chapters"][0]["chapter_goal"] == "Save the crew"
    without_alias = _apply_outline_commercial_patches(
        _outline(), [_patch("chapter", "goal", "Save the crew")]
    )
    assert "chapter_goal" not in without_alias["chapters"][0]


@pytest.mark.parametrize("compact_is_candidate", [True, False])
def test_compact_summary_cannot_outrank_complete_outline(compact_is_candidate):
    full = _outline()
    compact = {"chapters": [{"chapter_number": 1, "goal": "Truncated..."}]}
    full_report = {"overall_score": 0.6, "repair_directives": ["Fix full outline"]}
    compact_report = {"overall_score": 0.7, "restored_from_previous_replan": True}
    payload, report = _prefer_outline_repair_baseline(
        best_payload=full if compact_is_candidate else compact,
        best_report=full_report if compact_is_candidate else compact_report,
        candidate_payload=compact if compact_is_candidate else full,
        candidate_report=compact_report if compact_is_candidate else full_report,
    )
    assert payload == full
    assert report == full_report


def test_lossless_recovery_keeps_all_scenes_and_long_fields():
    full = _outline()
    full["chapters"][0]["scenes"] *= 3
    full["chapters"][0]["goal"] = "A long but complete goal. " * 100
    payload, report = _restore_persisted_outline_repair_baseline(
        {
            "outline_commercial_repair_baseline": full,
            "outline_commercial_repair_baseline_kind": "lossless-v1",
            "outline_commercial_last_failure": {"overall_score": 0.68},
        }
    )
    assert payload == full
    assert report["baseline_is_lossless"] is True


@pytest.mark.parametrize("numbers", [[7, 8, 9], [1, 2, 3], [1, 2, 3, 3], [1, "invalid", 3]])
def test_partial_or_duplicate_batch_is_not_a_complete_repair_window(numbers):
    payload = {"chapters": [{"chapter_number": number} for number in numbers]}
    assert not _outline_baseline_covers_window(payload, frozenset(range(1, 10)))


def test_complete_repair_window_preserves_all_requested_chapters():
    payload = {"chapters": [{"chapter_number": n} for n in range(1, 10)]}
    assert _outline_baseline_covers_window(payload, frozenset(range(1, 10)))
