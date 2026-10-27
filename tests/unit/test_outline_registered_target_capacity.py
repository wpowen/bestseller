from copy import deepcopy

import pytest

from bestseller.services import chapter_outline_readiness as prefix, planner

pytestmark = pytest.mark.unit


def candidate():
    return {"chapters": [{"chapter_number": 2, "volume_number": 1,
        "title": "Observatory archive", "goal": "Compare the dated entries",
        "authored_clause": {"keep": "full source"},
        "scenes": [{"scene_number": n, "participants": ["Mira"],
                    "action_sequence": ["Read the instrument register"],
                    "untouched": {"original": n}} for n in range(1, 4)]}]}


def patches():
    return [{"chapter_number": 2, "scope": "scene", "scene_number": n,
             "field": field, "value": value}
            for n in range(1, 4) for field, value in (
                ("concrete_goal", f"Compare entry {n}"),
                ("purpose", {"story": f"Check the dated observation {n}"}),
                ("entry_state", {"summary": f"The register is open at page {n}"}),
                ("exit_state", {"summary": f"Entry {n} has been checked"}),
                ("action_sequence", [f"Read entry {n}"]))]


def test_fifteen_consistent_projection_updates_fit_registered_targets():
    source = candidate()
    before = deepcopy(source)
    protocol = planner._outline_patch_protocol(source)
    expected = (len(planner._OUTLINE_PATCH_CHAPTER_FIELDS)
                + len(planner._OUTLINE_PATCH_CAUSAL_FIELDS)
                + 3 * len(planner._OUTLINE_PATCH_SCENE_FIELDS))
    assert protocol["max_patches"] == expected
    assert len(patches()) > 12
    result = planner._apply_outline_commercial_patches(source, patches(), strict=True)
    assert source == before
    assert result["chapters"][0]["authored_clause"] == before["chapters"][0]["authored_clause"]
    assert result["chapters"][0]["goal"] == before["chapters"][0]["goal"]
    assert all(s["untouched"] == {"original": s["scene_number"]}
               for s in result["chapters"][0]["scenes"])


def test_prefix_extra_registered_fields_have_actual_capacity():
    source = candidate()
    protocol = prefix._projection_patch_protocol(source)
    extra = prefix._EXTRA_PROJECTION_FIELDS.keys() - planner._OUTLINE_PATCH_CHAPTER_FIELDS
    assert protocol["max_patches"] == planner._outline_patch_capacity(source) + len(extra)
    result = prefix._apply_projection_patches(source, patches() + [{
        "chapter_number": 2, "scope": "chapter", "field": "selected_effect_skills",
        "value": {"primary": "creator_selected"}}])
    assert result["chapters"][0]["selected_effect_skills"] == {"primary": "creator_selected"}


def test_duplicate_and_invalid_identifiers_cannot_inflate_capacity():
    source = candidate()
    expected = planner._outline_patch_capacity(source)
    source["chapters"] += [deepcopy(source["chapters"][0]), {"chapter_number": True},
                           {"chapter_number": "3"}, {"chapter_number": -1}]
    source["chapters"][0]["scenes"] += [
        {"scene_number": True}, {"scene_number": "4"}, {"scene_number": 0}, {"scene_number": 1}]
    assert planner._outline_patch_capacity(source) == expected


@pytest.mark.parametrize("alias", ["goal", "chapter_goal"])
def test_repeated_semantic_target_remains_rejected(alias):
    source = candidate()
    with pytest.raises(planner.PlannerFallbackError, match="duplicate"):
        planner._apply_outline_commercial_patches(source, [
            {"chapter_number": 2, "scope": "chapter", "field": "goal", "value": "First"},
            {"chapter_number": 2, "scope": "chapter", "field": alias, "value": "Second"},
        ], strict=True)


def test_capacity_does_not_grant_outside_scene_or_unregistered_field():
    for patch in (
        {"chapter_number": 2, "scope": "scene", "scene_number": 9,
         "field": "concrete_goal", "value": "Outside"},
        {"chapter_number": 2, "scope": "chapter", "field": "title", "value": "Hijack"},
    ):
        with pytest.raises(planner.PlannerFallbackError):
            planner._apply_outline_commercial_patches(candidate(), [patch], strict=True)
