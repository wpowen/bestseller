"""Advance early-plant scans across chapter saves and process restarts."""

from pathlib import Path

from bookengine.storage import save
from bookengine.story_duties import load_ledger, record_story_duties, settle_early_plants
from test_story_design61 import make_bible


def _design():
    return {
        "total": 50,
        "foreshadows": [
            dict(id="f01", anchor="蓝信封", surface="封套", truth="封住一条密信", plant=3, reminders=[], payoff=12)
        ],
    }


def _chapter(out, number, text="他走过长廊。"):
    save(out / f"chapter-{number:03d}.body.md", text)


def test_early_plant_only_reads_new_bodies_after_a_chapter_ledger_update(tmp_path, monkeypatch):
    _chapter(tmp_path, 1)
    _chapter(tmp_path, 2)
    bible, design = make_bible(), _design()
    read = Path.read_text
    reads = []

    def trace(path, *args, **kwargs):
        if path.name.endswith(".body.md"):
            reads.append(path.name)
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", trace)
    assert not settle_early_plants(tmp_path, design, bible, 3)
    assert "f01-plant" not in load_ledger(tmp_path)
    assert reads == ["chapter-001.body.md", "chapter-002.body.md"]
    record_story_duties(
        tmp_path,
        3,
        "他走过长廊。",
        {"story_duties": [dict(id="f01-plant", kind="plant", chapter=3, anchors=["蓝信封"], names=[], text="埋设")]},
    )
    reads.clear()
    _chapter(tmp_path, 3, "他捡起蓝信封。")
    assert settle_early_plants(tmp_path, design, bible, 4) == ["f01-plant"]
    assert reads == ["chapter-003.body.md"]
    entry = load_ledger(tmp_path)["f01-plant"]
    assert (entry["chapter"], entry["status"], entry["early"]) == (3, "done", True)
    assert entry["attempts"] == [3]


def test_early_plant_rechecks_a_gap_when_its_body_becomes_available(tmp_path):
    _chapter(tmp_path, 2)
    bible, design = make_bible(), _design()
    assert not settle_early_plants(tmp_path, design, bible, 3)
    _chapter(tmp_path, 1, "他捡起蓝信封。")
    assert settle_early_plants(tmp_path, design, bible, 4) == ["f01-plant"]
    assert load_ledger(tmp_path)["f01-plant"]["chapter"] == 1
