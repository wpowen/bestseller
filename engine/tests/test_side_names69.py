"""Instruction 69: early plants, the final checkup after de-AI, recurring unnamed side characters."""

import json

from bookengine.story_design import normalize
from bookengine.story_duties import load_ledger, with_story_duties
from test_story_design61 import make_bible, model_design


def _bible():
    bible = make_bible()
    bible["story_design"] = normalize(model_design(), bible, 50)
    return bible


def test_plant_written_before_its_chapter_is_closed_as_early(tmp_path):
    bible = _bible()
    plant = next(f for f in bible["story_design"]["foreshadows"] if f["id"] == "f01")
    chapter = plant["plant"]
    (tmp_path / "chapter-001.body.md").write_text(f"门槛下压着{plant['anchor']}，没人多看一眼。\n", encoding="utf-8")
    outline = with_story_duties(dict(number=chapter, type="对抗", scenes=[]), bible, tmp_path)
    assert "f01-plant" not in [duty["id"] for duty in outline["story_duties"]]
    entry = load_ledger(tmp_path)["f01-plant"]
    assert (entry["status"], entry["chapter"], entry["early"]) == ("done", 1, True)
    assert plant["anchor"] in entry["quote"]


def test_plant_not_yet_written_is_still_handed_to_the_writer(tmp_path):
    bible = _bible()
    chapter = next(f for f in bible["story_design"]["foreshadows"] if f["id"] == "f01")["plant"]
    (tmp_path / "chapter-001.body.md").write_text("院里只有风声。\n", encoding="utf-8")
    outline = with_story_duties(dict(number=chapter, type="对抗", scenes=[]), bible, tmp_path)
    assert "f01-plant" in [duty["id"] for duty in outline["story_duties"]]
    assert not (tmp_path / "story_ledger.json").exists() or "f01-plant" not in json.loads(
        (tmp_path / "story_ledger.json").read_text(encoding="utf-8")
    ).get("duties", {})


def _present(tmp_path, number, people):
    folder = tmp_path / "chapters"
    folder.mkdir(exist_ok=True)
    (folder / f"{number:03d}.json").write_text(json.dumps(dict(number=number, present=people)), encoding="utf-8")


def test_recurring_role_label_reaches_the_planner_and_names_stick(tmp_path):
    from bookengine.side_cast import load_side_cast, recurring_unnamed
    from bookengine.story_duties import planner_sections, writer_sections

    bible = _bible()
    hero = bible["characters"][0]["name"]
    for number in (1, 2, 4):
        _present(tmp_path, number, [hero, "药童", "两名守卫", "裴照"])
    _present(tmp_path, 3, [hero, "看守"])
    assert [row["label"] for row in recurring_unnamed(tmp_path, bible, 5)] == ["药童"]
    assert recurring_unnamed(tmp_path, bible, 4) == []
    headings = [heading for heading, _ in planner_sections(bible, tmp_path, 5, 9, 1)]
    assert any("没有名字" in heading for heading in headings)

    outline = dict(number=5, type="对抗", scenes=[dict(characters=[hero, "小满（药童）"]), dict(characters=["药童"])])
    outline = with_story_duties(outline, bible, tmp_path)
    assert outline["scenes"][0]["characters"] == [hero, "小满"]
    assert load_side_cast(tmp_path) == {"药童": "小满"}
    assert recurring_unnamed(tmp_path, bible, 5) == []
    body = dict(writer_sections(outline, bible))
    assert any("药童=小满" in text for text in body.values())

    again = with_story_duties(dict(number=6, scenes=[dict(characters=["阿福（药童）"])]), bible, tmp_path)
    assert again["scenes"][0]["characters"] == ["小满"]
    assert load_side_cast(tmp_path) == {"药童": "小满"}


def test_card_names_and_counted_groups_are_never_role_labels(tmp_path):
    from bookengine.side_cast import recurring_unnamed

    bible = _bible()
    names = [card["name"] for card in bible["characters"]][:2]
    for number in (1, 2, 3):
        _present(tmp_path, number, [*names, "三名弟子", "众弟子"])
    assert recurring_unnamed(tmp_path, bible, 10) == []


def test_completed_book_gets_a_checkup_of_its_final_text(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from test_webui_contracts import WEBUI  # noqa: F401  (puts the web modules on sys.path)
    import final_checkup

    calls = []

    class Child:
        def wait(self):
            return 0

    def spawn(command, log, env_file, **kwargs):
        calls.append(dict(command=command, log=log, cwd=kwargs["cwd"]))
        return Child()

    monkeypatch.setattr(final_checkup, "spawn", spawn)
    queue = SimpleNamespace(env=tmp_path / ".env", engine=tmp_path / "engine", child_env=lambda: {})
    assert final_checkup.start_final_checkup(queue, tmp_path) is not None
    assert calls[0]["command"] == [sys.executable, "-m", "tools.book_checkup", str(tmp_path), "--source", "final"]
    assert calls[0]["log"] == tmp_path / "web-process.log"

    def broken(*args, **kwargs):
        raise OSError("no exec")

    monkeypatch.setattr(final_checkup, "spawn", broken)
    assert final_checkup.start_final_checkup(queue, tmp_path) is None
    assert "final checkup not started" in (tmp_path / "web-process.log").read_text(encoding="utf-8")
