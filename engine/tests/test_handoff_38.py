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
