"""Quoted viewpoints choose the relevant character's last observed location."""

from copy import deepcopy

from bookengine import q1_continuity as continuity
from bookengine.q1_continuity import boundary_facts


def test_viewpoint_quotes_are_required_and_scoped_to_the_correct_boundary():
    text = "林舟站在石桥，望着河水。\n\n陶青留在灶间，想起哥哥。"
    raw = dict(
        opening_pov="林舟", opening_pov_quote=text.split("\n\n")[0], end_pov="陶青", end_pov_quote=text.split("\n\n")[1]
    )
    facts = boundary_facts(raw, text)
    assert facts["opening_pov"] == "林舟" and facts["end_pov"] == "陶青"
    assert boundary_facts(dict(raw, end_pov_quote="凭空引文"), text)["end_pov"] is None


def test_switched_viewpoint_uses_its_own_last_observation_without_mutating_state():
    state = dict(
        end_location="灶间",
        end_pov="陶青",
        pov_locations={
            "林舟": dict(location="石桥", quote="林舟站在石桥。", chapter=1),
            "陶青": dict(location="灶间", quote="陶青留在灶间。", chapter=1),
        },
    )
    before = deepcopy(state)
    facts = dict(opening_pov="林舟", opening_location="石桥", end_location="石桥")
    note = continuity.evaluate_handoff(state, facts, "林舟站在石桥。", {}, dict(number=2))
    assert note["status"] == "checked" and note["issue"] is None
    assert note["reference"]["end_location"] == "石桥"
    assert state == before


def test_viewpoint_with_no_location_history_is_unknown_not_a_break():
    state = dict(end_location="灶间", end_pov="陶青")
    facts = dict(opening_pov="林舟", opening_location="山城", end_location="山城")
    note = continuity.evaluate_handoff(state, facts, "林舟在山城。", {}, dict(number=2))
    assert note["status"] == "handoff_unknown" and note["issue"] is None
    assert "pov_location" in note["missing"]


def test_a_different_known_pov_location_still_requires_a_real_transfer():
    state = dict(end_location="灶间", end_pov="陶青", pov_locations={"林舟": dict(location="石桥")})
    note = continuity.evaluate_handoff(
        state,
        dict(opening_pov="林舟", opening_location="山城", end_location="山城"),
        "林舟在山城。",
        {},
        dict(number=2),
    )
    assert note["issue"]["previous"] == "石桥"


def test_adopted_boundaries_update_per_character_locations_and_clear_stale_end_pov():
    state = dict(end_location="灶间", end_pov="陶青", pov_locations={"陶青": dict(location="灶间")})
    facts = dict(
        opening_pov="林舟",
        opening_location="石桥",
        opening_quote="林舟站在石桥。",
        end_pov="林舟",
        end_location="山城",
        end_quote="林舟抵达山城。",
        present=["林舟"],
        present_status="grounded",
    )
    current = continuity.advance_boundary_state(state, facts, 2, "林舟站在石桥。林舟抵达山城。", {})
    assert current["pov_locations"]["林舟"]["location"] == "山城"
    assert current["pov_locations"]["陶青"]["location"] == "灶间"
    assert "林舟" not in state["pov_locations"]
    assert "end_pov" not in continuity.advance_boundary_state(current, {}, 3, "风停了。", {})


def test_earlier_character_observation_cannot_overwrite_later_end_location():
    text = "林舟站在石桥。\n\n林舟抵达驿站。"
    facts = dict(
        opening_pov="林舟",
        opening_location="石桥",
        opening_quote="林舟站在石桥。",
        end_pov="林舟",
        end_location="驿站",
        end_quote="林舟抵达驿站。",
        character_locations=[dict(name="林舟", location="石桥", quote="林舟站在石桥。")],
    )
    state = continuity.advance_boundary_state({}, facts, 1, text, {})
    assert state["pov_locations"]["林舟"]["location"] == "驿站"
