"""Checkup replays shared, quote-grounded handoffs without adopting old state."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from bookengine.boundary_diagnostics import _breaks
from tools.book_checkup import check_book


FIX = Path(__file__).parent / "fixtures/review37"


def chapter(number, opening, ending=None, *, pov="林舟", end_pov=None):
    ending = ending or opening
    end_pov = end_pov or pov
    head = f"{pov}守在{opening}。"
    tail = f"{end_pov}停在{ending}。"
    raw = dict(
        opening_location=opening,
        opening_quote=head,
        end_location=ending,
        end_quote=tail,
        opening_pov=pov,
        opening_pov_quote=head,
        end_pov=end_pov,
        end_pov_quote=tail,
    )
    return dict(number=number, text=head + "\n\n" + tail, meta=dict(state=raw))


def save_book(book, chapters, bible=None):
    (book / "chapters").mkdir()
    (book / "story_bible.yaml").write_text(yaml.safe_dump(bible or {}, allow_unicode=True))
    for row in chapters:
        number = row["number"]
        (book / f"chapter-{number:03d}.body.md").write_text(row["text"])
        (book / f"chapters/{number:03d}.json").write_text(json.dumps(row["meta"], ensure_ascii=False))


def files(book):
    return {str(p.relative_to(book)): p.read_bytes() for p in book.rglob("*") if p.is_file()}


def test_each_chapter_retains_verified_facts_and_shared_result_without_writes(tmp_path):
    rows = [chapter(1, "河桥"), chapter(2, "河桥")]
    save_book(tmp_path, rows)
    before = files(tmp_path)
    result = check_book(tmp_path, source="draft")["location_breaks"]
    assert files(tmp_path) == before
    assert len(result["checks"]) == 2
    assert result["checked"] == 2
    for row, original in zip(result["checks"], rows):
        assert row["status"] == "Evidence"
        assert row["pov_status"] == "Evidence"
        assert row["handoff_status"] == "checked"
        assert row["facts"]["opening_quote"] in original["text"]
        assert row["facts"]["end_quote"] in original["text"]
        assert row["evaluation"]["status"] == "checked"


@pytest.mark.parametrize("opening", ["巷中", "狭巷"])
def test_same_place_uses_shared_equivalence(opening):
    result = _breaks([chapter(1, "狭巷"), chapter(2, opening)])
    assert result["count"] == 0
    assert result["checked"] == 2


@pytest.mark.parametrize("head", ["半个时辰后，林舟追进北街旧巷。", "林舟追进北街旧巷时，灯火已经熄了。"])
def test_explicit_scene_change_uses_shared_standard(head):
    first, second = chapter(1, "灶间"), chapter(2, "北街旧巷")
    second["text"] = head + "\n\n" + second["meta"]["state"]["end_quote"]
    second["meta"]["state"].update(opening_quote=head, opening_pov_quote=head)
    result = _breaks([first, second])
    assert result["count"] == 0
    assert result["checks"][1]["handoff_status"] == "checked"


def test_pov_history_is_replayed_instead_of_imposing_other_persons_location():
    rows = [chapter(1, "驿站"), chapter(2, "河桥", pov="孟青"), chapter(3, "驿站")]
    before = deepcopy(rows)
    result = _breaks(rows)
    assert rows == before
    assert result["checks"][1]["status"] == "Unknown"
    assert result["checks"][1]["handoff_status"] != "checked"
    third = result["checks"][2]
    assert third["handoff_status"] == "checked"
    assert third["evaluation"]["reference"]
    assert result["count"] == 0


def test_extracted_character_locations_are_replayed_with_quotes():
    first = chapter(1, "河桥", pov="孟青")
    quote = "林舟还守在驿站。"
    first["text"] += "\n\n" + quote
    first["meta"]["state"]["character_locations"] = [dict(name="林舟", location="驿站", quote=quote)]
    result = _breaks([first, chapter(2, "驿站")])
    check = result["checks"][1]
    assert check["handoff_status"] == "checked"
    assert check["end_quote"] == quote
    assert check["end_chapter"] == 1


def test_switched_viewpoint_issue_cites_its_own_observation_chapter():
    rows = [chapter(1, "驿站"), chapter(2, "河桥", pov="孟青"), chapter(3, "山城")]
    result = _breaks(rows)
    assert result["checked"] == 1
    assert result["checks"][2]["end_quote"] == rows[0]["meta"]["state"]["end_quote"]
    issue = result["issues"][0]
    assert issue["chapters"] == [2, 3]
    assert issue["end_quote"] in rows[0]["text"]
    assert issue["end_chapter"] == 1
    assert issue["end_quote"] not in rows[1]["text"]


def test_switch_without_pov_history_is_unknown_even_with_lexical_place_change():
    rows = [chapter(1, "青石渡口"), chapter(2, "南山客栈", pov="孟青")]
    result = _breaks(rows)
    assert result["count"] == 0
    assert result["checks"][1]["status"] == "Unknown"
    assert result["unknown"][0]["chapters"] == [1, 2]


def test_missing_current_boundary_cannot_inherit_saved_metadata_state():
    rows = [chapter(1, "驿站"), chapter(2, "驿站"), chapter(3, "驿站")]
    rows[1]["meta"]["state"].update(end_quote="伪造的引文", pov_locations={"林舟": dict(location="驿站")})
    result = _breaks(rows)
    second, third = result["checks"][1:]
    assert second["facts"]["end_location"] is None
    assert second["status"] == "Unknown"
    assert third["handoff_status"] != "checked"
    assert third["status"] == "Unknown"


def test_gap_resets_pov_history_and_never_claims_checked():
    rows = [chapter(1, "驿站"), chapter(3, "驿站")]
    result = _breaks(rows)
    assert result["checks"][1]["handoff_status"] != "checked"
    assert result["checks"][1]["status"] == "Unknown"
    assert result["unknown"][0]["reason"] == "missing_chapters"


def test_shared_ending_candidate_is_exposed_as_inference_never_checked():
    first = chapter(1, "驿站", "北港")
    first["meta"]["state"].update(end_location=None, end_quote=None)
    bible = dict(locations=["北港"])
    result = _breaks([first, chapter(2, "北港")], bible)
    assert result["checked"] == 0
    check = result["checks"][0]
    assert check["facts"]["end_location"] is None
    candidate = check["end_location_candidate"]
    assert candidate["status"] == "Inference"
    assert "北港" in candidate["locations"]
    assert all(quote in first["text"] for quote in candidate["quotes"])
    assert result["checks"][1]["status"] == "Unknown"


def test_unverified_pov_quote_never_selects_a_saved_character_location():
    rows = [chapter(1, "驿站"), chapter(2, "河桥", pov="孟青")]
    rows[1]["meta"]["state"].update(opening_pov_quote="并不存在的孟青引文")
    result = _breaks(rows)
    assert result["checks"][1]["facts"]["opening_pov"] is None
    assert result["checks"][1]["status"] == "Unknown"
    assert result["count"] == 0


def test_legacy_eval33_candidates_remain_unknown_and_inference_not_checked(tmp_path):
    rows = [dict(number=n, text=(FIX / f"eval33-{n:03d}.md").read_text(), meta={}) for n in (2, 3, 4)]
    save_book(tmp_path, rows)
    result = check_book(tmp_path, source="draft")["location_breaks"]
    assert result["checked"] == 0
    assert {(2, 3), (3, 4)} <= {tuple(row["chapters"]) for row in result["issues"]}
    assert {(2, 3), (3, 4)} <= {tuple(row["chapters"]) for row in result["unknown"]}
    assert all(row["status"] == "Inference" for row in result["issues"])
    assert all(row["status"] == "Unknown" for row in result["checks"])
    assert all(row["pov_status"] == "Unknown" for row in result["checks"])
    for issue in result["issues"]:
        old, new = (rows[n - 2] for n in issue["chapters"])
        assert issue["end_quote"] in old["text"]
        assert issue["opening_quote"] in new["text"]


def test_top_level_nested_and_boundary_metadata_share_quote_verification():
    rows = [chapter(1, "河桥"), chapter(2, "河桥")]
    raw = rows[1]["meta"].pop("state")
    rows[1]["meta"].update(opening_location="海港", opening_quote="并不存在")
    rows[1]["meta"]["boundary"] = raw
    result = _breaks(rows)
    assert result["checks"][1]["facts"]["opening_location"] == "河桥"
    assert result["checked"] == 2


def test_shared_stage_transfer_is_not_overridden_by_legacy_lexical_hypotheses():
    first, second = chapter(1, "狭长旧巷"), chapter(2, "北城")
    first["meta"] = {}
    head = "林舟离开南港，抵达北城。"
    second["text"] = head + "\n\n" + second["meta"]["state"]["end_quote"]
    second["meta"]["state"]["opening_quote"] = head
    for key in list(second["meta"]["state"]):
        if "pov" in key:
            del second["meta"]["state"][key]
    bible = dict(
        volumes=[dict(number=1, start=1, end=1, location="南港"), dict(number=2, start=2, end=2, location="北城")]
    )
    result = _breaks([first, second], bible)
    assert result["checks"][1]["evaluation"]["transition"]["status"] == "present"
    assert result["count"] == 0
    assert result["checks"][1]["status"] == "Unknown"


def test_stage_transfer_does_not_suppress_a_grounded_previous_location_conflict():
    first, second = chapter(1, "河桥"), chapter(2, "北城")
    head = "林舟离开南港，抵达北城。"
    second["text"] = head + "\n\n" + second["meta"]["state"]["end_quote"]
    second["meta"]["state"].update(opening_quote=head, opening_pov_quote=head)
    bible = dict(
        volumes=[dict(number=1, start=1, end=1, location="南港"), dict(number=2, start=2, end=2, location="北城")]
    )
    result = _breaks([first, second], bible)
    assert result["checks"][1]["evaluation"]["transition"]["status"] == "present"
    assert result["issues"][0]["kind"] == "location_break"
    assert result["issues"][0]["previous"] == "河桥"
    assert result["checks"][1]["handoff_status"] != "checked"


@pytest.mark.parametrize("saved_boundaries", [True, False])
def test_checkup_passes_bible_to_the_shared_handoff_rule(tmp_path, saved_boundaries):
    rows = [chapter(1, "河桥"), chapter(2, "北城")]
    if not saved_boundaries:
        for row in rows:
            row["meta"] = {}
    bible = dict(
        volumes=[dict(number=1, start=1, end=1, location="河桥"), dict(number=2, start=2, end=2, location="北城")]
    )
    save_book(tmp_path, rows, bible)
    result = check_book(tmp_path, source="draft")
    check = result["location_breaks"]["checks"][1]
    assert check["evaluation"]["transition"]["status"] == "missing"
    assert result["stage_transitions"]["checks"][0] == dict(check["evaluation"]["transition"], chapters=[1, 2])
    issues = result["location_breaks"]["issues"]
    assert result["location_breaks"]["count"] == 1
    assert len(issues) == 1
    assert issues[0]["kind"] == "missing_stage_transition"
    assert issues[0]["chapters"] == [1, 2]
    assert issues[0]["status"] == "Inference"
    assert issues[0]["end_quote"] in rows[0]["text"]
    assert issues[0]["opening_quote"] in rows[1]["text"]
    if not saved_boundaries:
        assert result["location_breaks"]["checked"] == 0
        assert check["status"] == "Unknown"
