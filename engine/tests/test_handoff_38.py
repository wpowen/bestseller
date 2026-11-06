"""F38: the opening repair and its acceptance share the same contract."""

import pytest

from bookengine.q1_continuity import opening_issue, repair_opening


def facts(text, location="驿站"):
    return dict(
        opening_location=location,
        opening_quote=text.split("\n\n")[0],
        end_location=location,
        end_quote=text.split("\n\n")[-1],
    )


def test_repair_receives_literal_endpoints_cast_and_accepts_scene_switch():
    original = "林舟站在驿站门外。\n\n林舟留在驿站。"
    replacement = "半个时辰后，林舟站在驿站门外。"
    state = dict(end_location="河湾石桥", present=["林舟", "陶青"])
    calls = []

    def ask(client, name, prompt, sections, *args, **kwargs):
        calls.append(name)
        payload = str(sections)
        assert all(s in payload for s in ("河湾石桥", "驿站", "林舟", "陶青"))
        assert "300" in prompt and "时间" in prompt and "场景" in prompt
        return dict(replace_count=1, paragraphs=[replacement])

    def extract(client, name, text, *args, **kwargs):
        return dict(facts(text), events=[]), False, 0

    actual, _, failed, _, note = repair_opening(
        None, "ch2", original, facts(original), False, state, {}, {"number": 2}, extract, ask
    )
    assert actual == replacement + "\n\n林舟留在驿站。"
    assert note["status"] == "repaired" and not failed
    assert calls == ["ch2-opening-repair"]


@pytest.mark.parametrize("marker", ["半个时辰后", "次日", "另一边", "与此同时"])
def test_explicit_time_or_scene_switch_with_current_place_is_a_handoff(marker):
    text = f"{marker}，林舟站在驿站门外。"
    assert opening_issue(dict(end_location="河湾石桥"), facts(text), text) is None


@pytest.mark.parametrize("text", ["林舟想起次日去驿站的计划。", "“次日在驿站见。”", "次日再去驿站，他打算休息。"])
def test_plans_or_dialogue_are_not_observed_transfers(text):
    assert opening_issue(dict(end_location="河湾石桥"), facts(text), text)


def test_rollback_has_a_specific_reason():
    text = "林舟在驿站。"
    *_, note = repair_opening(
        None,
        "ch2",
        text,
        facts(text),
        False,
        dict(end_location="石桥"),
        {},
        {"number": 2},
        None,
        lambda *a, **k: {},
    )
    assert note["status"] == "rolled_back"
    assert note["rollback_reason"] == "invalid_or_unchanged_patch"


@pytest.mark.parametrize("verb", ["追", "赶", "转入", "钻进", "回到", "折返", "跟着", "拐进", "爬出"])
def test_maintained_movement_vocabulary(verb):
    text = f"林舟从石桥{verb}驿站。"
    assert opening_issue(dict(end_location="石桥"), facts(text), text) is None


@pytest.mark.parametrize("old,new", [("狭巷", "巷中"), ("地下通道", "通道"), ("陆记钟铺", "陆记钟铺前堂")])
def test_same_place_core_or_subordinate_location(old, new):
    text = f"林舟守在{new}。"
    assert opening_issue(dict(end_location=old), facts(text, new), text) is None


def test_explicit_bible_location_relationships_do_not_join_distinct_named_places():
    bible = dict(locations=[dict(name="青石院", children=["东厢", "西厢"])])
    assert opening_issue(dict(end_location="青石院"), facts("林舟守在东厢。", "东厢"), "林舟守在东厢。", bible) is None
    for old, new in [("北街旧巷", "南街旧巷"), ("王府东院", "王府西院"), ("南城", "北城")]:
        assert opening_issue(dict(end_location=old), facts(f"林舟守在{new}。", new), f"林舟守在{new}。", bible)


def test_late_movement_and_negation_do_not_pass():
    for text in ["林舟在驿站。" + "风" * 300 + "林舟离开石桥，赶到驿站。", "林舟没有离开石桥去驿站。"]:
        assert opening_issue(dict(end_location="石桥"), facts(text), text)


def test_third_short_paragraph_can_supply_transition_within_300_characters():
    text = "风停了。\n\n灯亮了。\n\n半个时辰后，林舟来到驿站。"
    assert opening_issue(dict(end_location="石桥"), facts(text), text) is None


def test_trial37_saved_repair_establishes_destination_on_entry():
    text = (
        "陆和笙追进北街旧巷时，地上的油泥印还没干。黑泥混着车轴脱落的脂油，从北街口一路拖进窄巷，"
        "像有人故意留下的路标；凉风卷过墙根，细碎木屑贴着青砖滚向巷内。工具箱被拖行的刮擦声忽然停了，"
        "陆和笙循声转入两重院墙之间的阴影，脚步落在湿冷石面上，身后北街的灯火被砖墙一并截断。"
    )
    assert opening_issue(dict(end_location="灶间"), facts(text, "北街旧巷"), text) is None


@pytest.mark.parametrize("damage", ["lost_end", "repetition"])
def test_relaxed_acceptance_retains_boundary_and_repetition_guards(damage):
    text = "林舟站在驿站门外。\n\n车夫把三只破木箱搬到了驿站的墙边。\n\n林舟留在驿站。"
    replacement = "半个时辰后，林舟站在驿站门外。"
    if damage == "repetition":
        replacement += "车夫把三只破木箱搬到了驿站的墙边。"

    def extract(client, name, candidate, *args, **kwargs):
        raw = dict(facts(candidate), events=[])
        if damage == "lost_end":
            raw.pop("end_location")
        return raw, False, 0

    actual, _, _, _, note = repair_opening(
        None,
        "ch2",
        text,
        facts(text),
        False,
        dict(end_location="石桥"),
        {},
        dict(number=2),
        extract,
        lambda *a, **k: dict(replace_count=1, paragraphs=[replacement]),
    )
    assert actual == text and note["status"] == "rolled_back"
    assert note["rollback_reason"] == ("lost_grounded_boundary" if damage == "lost_end" else "introduced_repetition")
