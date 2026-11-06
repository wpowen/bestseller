from pathlib import Path
import json

from bookengine.q1_continuity import repair_opening
from bookengine.q1_extract import context
from tools.book_checkup import check_book

FIX = Path(__file__).parent / "fixtures/review37"


def test_missing_boundaries_retry_once_then_propagate_unknown():
    calls = []

    def ask(client, stage, *args, **kwargs):
        calls.append(stage)
        return {}

    def extract(*args, **kwargs):
        raise AssertionError("no prose repair when boundaries unknown")

    *_, facts, note = repair_opening(None, "ch2", "风吹过。", {}, False, {}, {}, {"number": 2}, extract, ask)
    assert calls == ["ch2-boundary-reextract"]
    assert note["status"] == "handoff_unknown" and note["missing"] == [
        "opening_location",
        "end_location",
        "previous_end",
    ]
    assert facts["end_location"] is None
    sections = context({}, {}, {"number": 1}, {"number": 3}, {"handoff_unknown": True}, [], [], "")
    assert "上章地点未知" in sections[0][1]


def test_reextraction_is_quote_grounded_and_detects_jump():
    calls = []
    text = "林舟在驿站吃饭。\n\n林舟守在驿站。"

    def ask(client, stage, *args, **kwargs):
        calls.append(stage)
        if stage.endswith("boundary-reextract"):
            return dict(
                opening_location="驿站",
                opening_quote="林舟在驿站吃饭。",
                end_location="驿站",
                end_quote="林舟守在驿站。",
            )
        return {}

    *_, facts, note = repair_opening(
        None, "ch2", text, {}, False, {"end_location": "石桥"}, {}, {"number": 2}, None, ask
    )
    assert calls == ["ch2-boundary-reextract", "ch2-opening-repair"]
    assert facts["end_location"] == "驿站" and note["issue"]["kind"] == "location_break"
    assert note["status"] == "rolled_back"


def test_planning_adds_transition_for_adjacent_location_change_without_mutation():
    from bookengine.stage_transition import complete_transitions

    batch = [dict(number=3, location="剑阁", scenes=[]), dict(number=4, location="旧器室", scenes=[])]
    actual = complete_transitions(batch, [dict(number=2, location="残兵巷")])
    assert all(row["transition"] for row in actual)
    assert "残兵巷" in actual[0]["transition"] and "剑阁" in actual[1]["transition"]
    assert "transition" not in batch[0]


def test_eval33_missing_observations_report_two_jumps_as_inference(tmp_path):
    for n in [2, 3, 4]:
        (tmp_path / f"chapter-{n:03d}.body.md").write_text((FIX / f"eval33-{n:03d}.md").read_text())
    result = check_book(tmp_path, source="draft")["location_breaks"]
    assert [2, 3] in [r["chapters"] for r in result["issues"]]
    assert [3, 4] in [r["chapters"] for r in result["issues"]]
    assert all(r["status"] == "Inference" for r in result["issues"])
