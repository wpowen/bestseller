"""Retired opponents cannot initiate new conflicts; recollections remain legal."""

from copy import deepcopy
import json

import pytest

from bookengine.structure_planning import opponent_presence_issues
from tools.book_checkup import check_book


def design():
    return dict(
        volumes=[
            dict(number=1, start=1, end=10, location="旧港", antagonist="石远"),
            dict(number=2, start=11, end=20, location="山城", antagonist="何晏"),
        ],
        antagonists=[dict(name="石远", stage=1, tier=1, exit=10)],
        characters=[dict(name="石远", aliases=["石头领"]), dict(name="何晏")],
    )


@pytest.mark.parametrize("field", ["conflict", "opponent"])
def test_retired_initiator_is_rewritten_even_without_presence_list(field):
    bible = design()
    batch = [dict(number=12, **{field: "石远派人封锁山城"}, scenes=[dict(characters=["何晏"])])]
    before = deepcopy(batch)
    assert any(i["kind"] == "retired_initiator" for i in opponent_presence_issues(bible, batch))
    assert batch == before


def test_retired_conflict_check_includes_scenes_and_aliases_but_allows_old_history():
    assert opponent_presence_issues(design(), [dict(number=12, scenes=[dict(conflict="石头领的人截路")])])
    assert not opponent_presence_issues(design(), [dict(number=12, conflict="回忆石远当年封港的旧事")])


@pytest.mark.parametrize(
    "text",
    [
        "石远要的是我。",
        "石远的人堵住了山路。",
        "石远派人来抢货。",
        "石头领在桥上怒吼。",
        "巡卒还在满城找你打伤石远的账。",
        "她说：石远正在封城，不能过去。",
    ],
)
def test_active_retired_opponent_is_reported(text):
    from bookengine.retired_opponents import active_opponent_hits

    hits = active_opponent_hits(text, design(), 12)
    assert hits and hits[0]["name"] == "石远"
    assert all(text[h["start"] : h["end"]] == h["text"] for h in hits)


@pytest.mark.parametrize(
    "text",
    [
        "石远曾用这种办法封过港。",
        "她回忆起当年石远派人追杀自己的旧事。",
        "“那年石远的人封了港。”她说。",
        "那是石远留下的旧剑。",
        "她早已斩断石远一臂。",
        "石远的残铃落在地上。",
        "何晏要找石远当年留下的旧账册。",
    ],
)
def test_background_or_object_mentions_are_not_active(text):
    from bookengine.retired_opponents import active_opponent_hits

    assert not active_opponent_hits(text, design(), 12)


def test_background_clause_cannot_hide_a_new_current_action():
    from bookengine.retired_opponents import active_opponent_hits

    text = "石远曾在旧港称霸，如今石远又派人封住山城。"
    assert active_opponent_hits(text, design(), 12)
    assert not active_opponent_hits(text, design(), 10)


def test_checkup_records_active_chapters_and_grounded_location_regression(tmp_path):
    import yaml

    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(design(), allow_unicode=True))
    (tmp_path / "chapters").mkdir()
    for n, text in [(12, "石远的人封住旧港。"), (13, "石远曾封过旧港。")]:
        (tmp_path / f"chapter-{n:03d}.body.md").write_text(text)
        (tmp_path / f"chapters/{n:03d}.json").write_text(json.dumps(dict(end_location="旧港", end_quote=text)))
    report = check_book(tmp_path)
    assert report["antagonists"][1 if report["antagonists"][0]["name"] == "何晏" else 0][
        "active_after_exit_chapters"
    ] == [12]
    assert [r["chapter"] for r in report["location_regressions"]["issues"]] == [12, 13]


@pytest.mark.parametrize("valid", [True, False])
def test_targeted_opponent_repair_rechecks_and_rolls_back(valid):
    from bookengine.retired_opponents import repair_retired

    text = "石远派人封住山城。\n\n余下正文不能改。"
    patch = {"edits": [{"id": 1, "text": "何晏派人封住山城。"}]}

    def extract(*args, **kwargs):
        return {"events": []}, not valid, 0

    changed, _, _, note = repair_retired(
        None, "ch12", text, {}, False, {}, design(), {"number": 12}, extract, lambda *a, **kw: patch
    )
    assert changed == (text.replace("石远", "何晏") if valid else text)
    assert note["status"] == ("repaired" if valid else "rolled_back")
