"""Grounded chapter boundaries and bounded opening repair."""

from copy import deepcopy

from bookengine.q1_continuity import boundary_facts, opening_issue, opening_patch
from bookengine.q1_extract import context
from bookengine.q1_continuity import repair_opening


def test_boundary_observations_need_quotes_at_the_correct_end():
    text = "林舟站在石桥上。\n\n他们走过山道。\n\n林舟与向导停在驿站门口。"
    raw = dict(
        opening_location="石桥",
        opening_quote="林舟站在石桥上。",
        end_location="驿站",
        end_quote="林舟与向导停在驿站门口。",
        present=["林舟", "向导"],
        present_quotes={"林舟": "林舟与向导停在驿站门口。", "向导": "凭空引文"},
    )
    actual = boundary_facts(raw, text)
    assert actual["end_location"] == "驿站"
    assert actual["present"] == ["林舟"]
    assert boundary_facts(dict(raw, end_quote="他们走过山道。"), text)["end_location"] is None
    assert raw["present"] == ["林舟", "向导"]


def test_ending_dialogue_does_not_hide_grounded_location_in_the_last_scene():
    text = "开场。\n\n林舟走进驿站，向导也跟了进来。\n\n“坐下。”\n\n“喝口水。”\n\n“多谢。”"
    facts = boundary_facts(
        dict(
            end_location="驿站",
            end_quote="林舟走进驿站，向导也跟了进来。",
            present=["林舟"],
            present_quotes={"林舟": "林舟走进驿站，向导也跟了进来。"},
        ),
        text,
    )
    assert facts["end_location"] == "驿站" and facts["present"] == ["林舟"]


def test_location_jump_requires_movement_from_previous_location():
    previous = dict(end_location="石桥", present=["林舟"])
    assert opening_issue(previous, dict(opening_location="石桥"), "林舟站在石桥。") is None
    assert opening_issue(previous, dict(opening_location="驿站"), "林舟在驿站吃饭。")
    assert opening_issue(previous, dict(opening_location="驿站"), "林舟离开石桥，沿路走进驿站。") is None
    assert opening_issue(previous, dict(opening_location=None), "没有可靠地点。") is None


def test_only_one_or_two_opening_paragraphs_can_change():
    original = "第一段。\n\n第二段。\n\n第三段必须逐字保留。"
    assert opening_patch(original, dict(paragraphs=["补上移动。", "接续到达。"], replace_count=2)) == (
        "补上移动。\n\n接续到达。\n\n第三段必须逐字保留。"
    )
    for patch in (
        dict(paragraphs=["整章。"], replace_count=3),
        dict(paragraphs=["一。\n\n二。"], replace_count=1),
        dict(paragraphs=["一。", "复述下一段。"], replace_count=1),
    ):
        assert opening_patch(original, patch) == original


def test_handoff_is_in_first_outline_block_and_does_not_mutate_state():
    state = dict(end_location="石桥", present=["林舟"])
    before = deepcopy(state)
    sections = context({}, {}, {"number": 1}, {"number": 2}, state, [], [], "前章结尾")
    assert "开场必须承接" in sections[0][1]
    assert "石桥" in sections[0][1] and "林舟" in sections[0][1]
    assert state == before


def test_repair_rechecks_and_rolls_back_instead_of_adopting_unchecked_text():
    text = "林舟在驿站吃饭。\n\n第二段。\n\n后文不能变。"
    raw = dict(opening_location="驿站", opening_quote="林舟在驿站吃饭。")
    state = dict(end_location="石桥", present=["林舟"])
    for invalid in (False, True):
        replacement = "林舟换了一只饭碗。" if invalid else "林舟离开石桥，走进驿站吃饭。"

        def ask(client, name, *args, **kwargs):
            if name.endswith("-opening-repair"):
                return dict(replace_count=1, paragraphs=[replacement])
            if name.endswith("-opening-boundary-recheck"):
                return dict(opening_location="驿站", opening_quote=replacement)
            return {}

        def extract(*args, **kwargs):
            raise AssertionError("Opening recheck must not extract whole-chapter state")

        actual, _, _, _, note = repair_opening(None, "ch2", text, raw, False, state, {}, {}, extract, ask)
        assert note["status"] == ("rolled_back" if invalid else "repaired")
        assert actual == (text if invalid else replacement + text[len(text.split("\n\n")[0]) :])
        if invalid:
            assert note["rollback_reason"] == "opening_still_disconnected"


def test_end_observation_is_saved_from_adopted_body():
    from support_minimal_b import MinimalBTests, CaptureClient, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        text = body(60) + "\n\n阿宁站在院门。"
        response = fixture.extraction(
            end_location="院门", end_quote="阿宁站在院门。", present=["阿宁"], present_quotes={"阿宁": "阿宁站在院门。"}
        )
        client = CaptureClient(fixture.out, [text], {"r2-ch2-finish": response})
        row = fixture.run_chapter(client)
        assert row["state"]["end_location"] == "院门"
        assert row["state"]["present"] == ["阿宁"]
        assert row["end_quote"] in (fixture.out / "chapter-002.body.md").read_text()
    finally:
        fixture.doCleanups()
