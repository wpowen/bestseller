"""F40.4: bounded local movement, with the complete saved rollback sample."""

import json
from pathlib import Path

import pytest

from bookengine.q1_continuity import opening_issue, repair_opening


SAMPLE = json.loads((Path(__file__).parent / "fixtures/handoff40/rollback-pairs.json").read_text())


def test_complete_editorial_sample_is_distinct_from_deterministic_evidence():
    pairs = SAMPLE["pairs"]
    assert SAMPLE["authority"] == "agent_editorial_not_human"
    assert len(pairs) == len({p["chapter"] for p in pairs}) == 17
    assert sum(p["reader_connected"] for p in pairs) == 15
    assert all(len(p[k]) == 300 for p in pairs for k in ("previous_tail", "opening"))


@pytest.mark.parametrize("row", SAMPLE["pairs"], ids=lambda row: f"ch{row['chapter']}")
def test_saved_original_openings_use_only_bounded_deterministic_proof(row):
    issue = opening_issue(row["reference"], dict(opening_location=row["opening_location"]), row["opening"])
    assert (issue is None) == row["deterministic_connected"]


@pytest.mark.parametrize(
    "old,new,text,quote",
    [
        ("渡口", "船舱", "陶青从石阶上起身，向船舱挪去。", "陶青在渡口的石阶上坐下。"),
        ("院门", "长廊", "陶青沿院门退入长廊。", "陶青守在院门。"),
        ("草地上", "营地", "陶青离开草地，走进营地。", "陶青站在草地上。"),
        ("一处河湾", "河湾外", "陶青在河湾外收起渔网。", "陶青在一处河湾收网。"),
        ("石阶", "石阶上方", "陶青在石阶上方停下。", "陶青走上石阶。"),
        ("青石院内", "青石院中", "陶青在青石院中休息。", "陶青站在青石院内。"),
        ("画室更深处", "客厅", "陶青从画室最深处折返，来到客厅。", "陶青走向画室更深处。"),
    ],
)
def test_local_continuations_generalize_beyond_saved_book(old, new, text, quote):
    assert opening_issue(dict(end_location=old, end_quote=quote), dict(opening_location=new), text) is None


@pytest.mark.parametrize(
    "old,new,text,quote",
    [
        ("南街旧巷", "北街旧巷", "陶青在北街旧巷起身继续走。", "陶青在南街旧巷坐下。"),
        ("冰坡上方", "冰坡下方", "陶青在冰坡下方坐下。", "陶青停在冰坡上方。"),
        ("长通道上方", "长通道下方", "陶青在长通道下方坐下。", "陶青停在长通道上方。"),
        ("石阶上方", "船舱", "陶青从石阶下方走进船舱。", "陶青停在石阶上方。"),
        ("渡口", "船舱", "陶青从石阶上起身，向船舱挪去。", "陶青守在渡口。北城有人站在石阶上。"),
        ("南城", "北城", "陶青从石阶上起身，向北城挪去。", "陶青在南城吃饭。"),
        ("渡口", "船舱", "陶青没有从石阶上起身去船舱。", "陶青在渡口的石阶上坐下。"),
        ("渡口", "船舱", "陶青想起从石阶上起身去船舱的旧事。", "陶青在渡口的石阶上坐下。"),
        ("渡口", "船舱", "“陶青从石阶上起身，向船舱挪去。”", "陶青在渡口的石阶上坐下。"),
        ("渡口", "船舱", "陶青从石阶上起身，向船舱挪去。", "陶青在渡口说：‘石阶上有血。’"),
        ("渡口", "船舱", "陶青从石阶上起身，向船舱挪去。", "陶青没有在渡口的石阶上坐下。"),
        ("渡口", "船舱", "风" * 301 + "陶青从石阶上起身，向船舱挪去。", "陶青在渡口的石阶上坐下。"),
        ("剑阁", "剑阁外三十里", "陶青在剑阁外三十里喝水。", "陶青留在剑阁。"),
    ],
)
def test_local_relaxation_keeps_missing_or_unobserved_transfers_as_issues(old, new, text, quote):
    assert opening_issue(dict(end_location=old, end_quote=quote), dict(opening_location=new), text)


def test_generic_known_place_cannot_erase_a_specific_side_of_the_reference():
    state = dict(end_location="石阶上方", known_locations=["石阶"])
    assert opening_issue(state, dict(opening_location="船舱"), "陶青从石阶下方走进船舱。")


@pytest.mark.parametrize("damage", [None, "repetition", "lost_end"])
def test_local_departure_repair_retains_existing_adoption_guards(damage):
    text = "陶青站在船舱门外。\n\n船夫把三张旧木凳搬到了船舱的墙边。\n\n陶青留在船舱。"
    replacement = "陶青从石阶上起身，向船舱挪去。"
    if damage == "repetition":
        replacement += "船夫把三张旧木凳搬到了船舱的墙边。"
    if damage == "lost_end":
        text = "陶青站在船舱门外。"
    raw = dict(
        opening_location="船舱",
        opening_quote=text.split("\n\n")[0],
        end_location="船舱",
        end_quote=text.split("\n\n")[-1],
    )
    state = dict(end_location="渡口", end_quote="陶青在渡口的石阶上坐下。")
    calls = []

    def ask(client, name, *args, **kwargs):
        calls.append(name)
        if name.endswith("-opening-repair"):
            return dict(replace_count=1, paragraphs=[replacement])
        return {}  # Missing extraction must remain Inference.

    actual, _, _, facts, note = repair_opening(None, "ch2", text, raw, False, state, {}, dict(number=2), None, ask)
    if damage:
        assert actual == text and note["status"] == "rolled_back"
        assert note["rollback_reason"] == (
            "introduced_repetition" if damage == "repetition" else "lost_grounded_boundary"
        )
        expected_calls = ["ch2-opening-repair"]
        if damage == "lost_end":
            expected_calls.append("ch2-opening-boundary-recheck")
        assert calls == expected_calls
    else:
        assert actual.startswith(replacement) and actual.endswith(text[text.index("\n\n") :])
        assert note["status"] == "repaired" and note["recheck"]["evidence"] == "Inference"
        assert facts["opening_location"] is None and facts["end_location"] == "船舱"
