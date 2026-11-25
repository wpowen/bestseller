import pytest

from bookengine.cast_consistency import cast_findings, repair_cast_consistency


CAST = {"characters": [{"name": "周诚", "gender": "男"}, {"name": "方芸", "gender": "女"}]}


def unexpected(*args, **kwargs):
    pytest.fail("an uncertain actor must not trigger a repair or a state extraction")


@pytest.mark.parametrize(
    "source",
    [
        "周诚取出提灯，伸手去摸门闩。方芸站在他身后，握着钱袋。\n\n门外有人看守，他终于可以动身了。",
        "周诚取出提灯，伸手去摸门闩。方芸站在他身后，握着钱袋。门外有人看守，他终于可以动身了。",
        "周诚拿起提灯，方芸站在窗前。雨停了，他终于可以动身了。",
    ],
)
def test_two_separate_actions_do_not_assign_the_next_action_to_the_last_named_actor(source):
    issues = cast_findings(source, CAST)
    assert [(row["kind"], row["status"]) for row in issues] == [("gender_pronoun", "Unknown")]
    result, _, _, note = repair_cast_consistency(
        None, "ch1", source, {}, False, {}, CAST, {"number": 1}, unexpected, unexpected
    )
    assert result == source and note["edits"] == []


def test_neutral_opening_does_not_restore_last_paragraphs_ambiguous_subject():
    source = "周诚拿起提灯。方芸站在门旁。\n\n风停了。他要动身了。"
    assert not [row for row in cast_findings(source, CAST) if row["status"] != "Unknown"]


@pytest.mark.parametrize(
    ("suggestion", "expected", "status"),
    [
        ("方芸攥紧了灯绳。", "方芸攥紧了灯绳。", "repaired"),
        ("周诚攥紧了灯绳。", "他攥紧了灯绳。", "rolled_back"),
        ("方芸把灯给了周诚。", "她攥紧了灯绳。", "repaired"),
    ],
)
def test_actor_repairs_preserve_supported_name_and_never_replace_the_action(suggestion, expected, status):
    first = "方芸没有答。"
    original = first + "他攥紧了灯绳。"
    result, _, _, note = repair_cast_consistency(
        None,
        "ch1",
        original,
        {},
        False,
        {},
        CAST,
        {"number": 1},
        lambda *args, **kwargs: ({"events": []}, False, None),
        lambda *args, **kwargs: {"edits": [{"id": 2, "text": suggestion}]},
    )
    assert result == first + expected and note["status"] == status
