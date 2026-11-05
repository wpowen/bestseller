"""Offline structure checks, bounded repair and immutable legacy recovery."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from bookengine.config import Book
from bookengine import q1_prepare, q1_plan
from bookengine.q1_schema import normalize_threads, DATA
from bookengine import structure_planning as structure

ROOT = Path(__file__).resolve().parents[2]


def design(total=50):
    stages = [
        dict(
            s,
            location=f"地点{s['number']}",
            antagonist=f"对手{s['number']}",
            goal="救人",
            climax="赢得自由",
            exit_chapter=s["end"],
        )
        for s in structure.stage_ranges(total)
    ]
    return dict(
        structure_version=1,
        volumes=stages,
        antagonists=[dict(name=s["antagonist"], tier=s["number"], stage=s["number"], exit=s["end"]) for s in stages],
    )


def cast():
    return [
        dict(name=n, role=r, voice="直说", desire="回家")
        for n, r in [("周远舟", "protagonist"), ("赵听禾", "support"), ("王见微", "antagonist")]
    ]


class Fixture:
    prefix = ""

    def __init__(self, out, reply):
        self.out, self.reply, self.calls = out, reply, []

    def stage(self, name, prompt, schema=None, **kwargs):
        self.calls.append((name, prompt))
        value = self.reply(name, prompt)
        return kwargs["parser"](json.dumps(value, ensure_ascii=False))


@pytest.mark.parametrize("total", [3, 10, 12, 13, 19, 20, 24, 25, 30, 50, 100, 1000])
def test_stage_ranges_cover_every_chapter(total):
    stages = structure.stage_ranges(total)
    assert [n for s in stages for n in range(s["start"], s["end"] + 1)] == list(range(1, total + 1))
    assert all(1 <= s["end"] - s["start"] + 1 <= 12 for s in stages)
    if total >= 20:
        assert all(10 <= s["end"] - s["start"] + 1 <= 12 for s in stages[:-1])
    assert len(stages) >= (total + 11) // 12


def test_validator_checks_locations_opponent_scope_and_coverage():
    bible = design()
    assert structure.structure_issues(bible, 50) == []
    broken = deepcopy(bible)
    broken["volumes"][1]["location"] = broken["volumes"][0]["location"]
    broken["antagonists"][0]["exit"] = 50
    broken["volumes"][0]["goal"] = ""
    broken["volumes"][-1]["end"] = 48
    issues = "\n".join(structure.structure_issues(broken, 50))
    for word in ("location", "exit", "goal", "50"):
        assert word in issues
    broken["antagonists"][0]["stage"] = 999
    assert structure.structure_issues(broken, 50)
    assert bible == design()


def test_stage_lookup_keeps_saved_legacy_boundaries():
    legacy = dict(volumes=[dict(number=1, start=1, end=25), dict(number=2, start=26, end=50)])
    assert structure.stage_for_chapter(legacy, 12, 50)["number"] == 1
    assert structure.stage_for_chapter(legacy, 26, 50)["start"] == 26
    assert structure.stage_by_number({}, 2, 50)["start"] == 26
    assert structure.final_stage_start(legacy, 50) == 26
    assert structure.stage_for_chapter(design(), 11, 50)["number"] == 2
    assert structure.final_stage_start(design(), 50) == 41


def test_new_thread_schedule_uses_actual_stage_lengths_and_last_stage_start():
    stages = structure.stage_ranges(50)
    result = normalize_threads({}, len(stages), 50, DATA["profile"], stages=stages)
    hidden = next(t for t in result["threads"] if t["kind"] == "hidden")
    assert {b["reveal_not_before"] for b in hidden["beats"]} == {41}
    assert {b["volume"] for b in hidden["beats"]} == {1, 2, 3, 4, 5}
    legacy = normalize_threads({}, 2, 50, DATA["profile"])
    assert {b["reveal_not_before"] for t in legacy["threads"] if t["kind"] == "hidden" for b in t["beats"]} == {26}


@pytest.mark.parametrize("repair_success", [True, False])
def test_prepare_repairs_only_structure_three_times_and_records_failure(tmp_path, repair_success):
    original = dict(title="归途", characters=cast(), **design())
    original["volumes"][1]["location"] = original["volumes"][0]["location"]

    def reply(name, prompt):
        if name == "plan-q1-prepare":
            return dict(bible=original, threads={})
        assert "location" in prompt
        return dict(bible=dict(design(), title="禁止换标题", characters=[]), threads={}) if repair_success else {}

    client = Fixture(tmp_path, reply)
    bible, threads, _ = q1_prepare.prepare(client, Book("suspense-mystery", 50, 2200, ""), tmp_path, ROOT / "config")
    assert bible["title"] == "归途" and len(bible["characters"]) == 3
    assert len(client.calls) == (2 if repair_success else 4)
    report = json.loads((tmp_path / "structure-planning.json").read_text())
    assert report["status"] == ("passed" if repair_success else "failed_continued")
    assert len(report["attempts"]) == (2 if repair_success else 4)
    assert bool(report["issues"]) is not repair_success
    paths = [tmp_path / n for n in ("story_bible.yaml", "threads.yaml")]
    before = [p.read_bytes() for p in paths]
    again = q1_prepare.prepare(
        Fixture(tmp_path, lambda *a: pytest.fail("resume called model")),
        Book("suspense-mystery", 50, 2200, ""),
        tmp_path,
        ROOT / "config",
    )
    assert again[0] == bible
    assert [p.read_bytes() for p in paths] == before
    assert all(b["reveal_not_before"] == 41 for t in threads["threads"] if t["kind"] == "hidden" for b in t["beats"])


def test_missing_threads_does_not_regenerate_or_rewrite_legacy_bible(tmp_path):
    bible = dict(title="旧书", characters=cast(), volumes=[dict(number=1, start=1, end=25)])
    path = tmp_path / "story_bible.yaml"
    path.write_text(yaml.safe_dump(bible, allow_unicode=True))
    before = path.read_bytes()
    client = Fixture(tmp_path, lambda *a: dict(bible=dict(design(25), characters=cast()), threads={}))
    result, _, _ = q1_prepare.prepare(client, Book("suspense-mystery", 25, 2200, ""), tmp_path, ROOT / "config")
    assert path.read_bytes() == before
    assert result["volumes"] == bible["volumes"]
    assert len(client.calls) == 1


def test_expanded_stage_plan_cannot_override_fixed_stage_contract(tmp_path):
    client = Fixture(tmp_path, lambda *a: dict(number=99, start=999, end=999, location="别处", antagonist="旧对手"))
    bible = design()
    stage = q1_plan.volume(client, bible, {}, 2, 50, {}, [], [])
    assert (stage["number"], stage["start"], stage["end"]) == (2, 11, 20)
    assert stage["location"] == "地点2" and stage["antagonist"] == "对手2"
