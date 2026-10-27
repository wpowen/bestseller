"""Readiness overlays retain supplied facts instead of masking them with blanks."""

from copy import deepcopy

import pytest
from test_planning_readiness_gate import _complete_chapter

from bestseller.services.planning_readiness_gate import (
    _merged,
    evaluate_chapter_outline_batch_planning_readiness,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("empty", [None, "", " \t\n", [], (), {}])
@pytest.mark.parametrize(
    "meaningful", ["The seal breaks.", ["evidence"], {"known": None}, 0, False]
)
def test_empty_overlay_preserves_values_the_gate_treats_as_meaningful(empty, meaningful):
    direct = {"cut_point": meaningful, "unknown": None}
    overlay = {"cut_point": empty, "other_unknown": empty}
    before = deepcopy((direct, overlay))
    result = _merged(direct, overlay)
    assert result["cut_point"] is meaningful
    assert result["unknown"] is None
    assert result["other_unknown"] is empty
    assert (direct, overlay) == before


@pytest.mark.parametrize(
    ("direct", "purpose", "methodology", "expected"),
    [
        ("direct", "purpose", "methodology", "methodology"),
        ("direct", "purpose", None, "purpose"),
        ("direct", None, "methodology", "methodology"),
        ("direct", "", {}, "direct"),
        (None, "purpose", "", "purpose"),
        ({}, [], "methodology", "methodology"),
        (None, None, None, None),
        ("", None, {}, {}),
        ("direct", 0, None, 0),
        ("direct", False, None, False),
        (False, "purpose", "methodology", "methodology"),
    ],
)
def test_meaningful_overlay_priority_and_all_unknown_values_remain_explicit(
    direct, purpose, methodology, expected
):
    layers = [{"value": value} for value in (direct, purpose, methodology)]
    before = deepcopy(layers)
    result = _merged(*layers)
    assert result["value"] == expected
    assert type(result["value"]) is type(expected)
    assert layers == before


@pytest.mark.parametrize("level", ["direct", "purpose", "methodology"])
@pytest.mark.parametrize("blank", [None, "", " ", [], {}])
def test_public_scene_readiness_preserves_cut_point_from_each_actual_level(level, blank):
    chapter = _complete_chapter()
    scene = chapter["scenes"][0]
    meaningful = "The wax seal falls off as the inspector signs the receipt."
    scene["cut_point"] = blank
    scene["purpose"]["cut_point"] = blank
    scene["methodology_contract"]["cut_point"] = blank
    target = {
        "direct": scene,
        "purpose": scene["purpose"],
        "methodology": scene["methodology_contract"],
    }
    target[level]["cut_point"] = meaningful
    payload = {"chapters": [chapter]}
    before = deepcopy(payload)
    report = evaluate_chapter_outline_batch_planning_readiness(payload)
    assert report.passed
    assert payload == before


def test_public_chapter_contract_empty_overlay_preserves_direct_action():
    chapter = _complete_chapter()
    chapter["protagonist_choice"] = "The inspector opens the sealed box in front of the witness."
    chapter["causal_contract"]["protagonist_choice"] = None
    before = deepcopy(chapter)
    report = evaluate_chapter_outline_batch_planning_readiness({"chapters": [chapter]})
    assert report.passed
    assert chapter == before


def test_missing_cut_point_in_another_scene_stays_blocked_without_borrowing_a_value():
    chapter = _complete_chapter()
    first = chapter["scenes"][0]
    first["cut_point"] = "The witness signs and leaves with the sealed box."
    first["purpose"]["cut_point"] = None
    first["methodology_contract"]["cut_point"] = ""
    second = deepcopy(first)
    second["scene_number"] = 2
    second["cut_point"] = None
    second["purpose"]["cut_point"] = " "
    second["methodology_contract"]["cut_point"] = {}
    chapter["scenes"].append(second)
    payload = {"chapters": [chapter]}
    before = deepcopy(payload)
    report = evaluate_chapter_outline_batch_planning_readiness(payload)
    missing = [f.path for f in report.blocking_findings if f.code == "PLANNING_SCENE_FIELD_MISSING"]
    assert missing == ["chapters[0].scenes[1].cut_point"]
    assert not report.passed
    assert payload == before


def test_unknown_keys_and_non_mapping_inputs_do_not_invent_fallbacks():
    source = {"unknown": None, "empty_object": {}, "empty_array": []}
    before = deepcopy(source)
    assert _merged(None, "not an overlay", source, {}, 0) == source
    assert source == before
    assert _merged() == {}
