"""Exit+1 presence, one final recheck, and a real-text fake-client repair."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from bookengine import q1_plan
from bookengine.retired_opponents import repair_retired
from bookengine.structure_planning import opponent_presence_issues
from tools.book_checkup import check_book
from test_retired_opponents_35 import design

FIXTURES = Path(__file__).parent / "fixtures/repeat_exit_tail_36"


@pytest.mark.parametrize("text", ["石远沉默不语。", "石远坐在角落。", "人群中还有石头领。", "门旁是石远。"])
def test_boundary_presence_is_detected_without_action_verb(text, tmp_path):
    import yaml

    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(design(), allow_unicode=True))
    (tmp_path / "chapter-011.body.md").write_text(text)
    report = check_book(tmp_path)
    row = next(r for r in report["antagonists"] if r["name"] == "石远")
    assert row["exit_next_chapter_presence"] == [11]


def test_exit_plus_one_cast_is_checked_without_stage_metadata():
    bible = design()
    bible.pop("volumes")
    assert opponent_presence_issues(bible, [dict(number=11, characters=["石头领"])])


@pytest.mark.parametrize("version", [1, 2])
def test_future_cached_boundary_cast_is_superseded_without_overwriting_history(tmp_path, monkeypatch, version):
    folder = tmp_path / "outline-diversity"
    folder.mkdir()
    path = folder / "011-011.json"
    bad = dict(number=11, title="新程", characters=["石头领"], scenes=[], transition="一夜后赶到山城。")
    path.write_text(
        json.dumps(dict(status="passed", normalized=True, structure_checks_version=version, chapters=[bad]))
    )
    before = path.read_bytes()
    bible = design()
    bible.pop("volumes")
    requested = []

    class Client:
        out = tmp_path

    def ask(*args, **kwargs):
        requested.append(args[2])
        return dict(chapters=[dict(bad, characters=["何晏"])])

    monkeypatch.setattr(q1_plan, "_ask", ask)
    result = q1_plan.outlines(Client(), bible, {}, {}, dict(number=2, start=11, end=20), 11, 11, {}, [], [], [])
    assert requested
    assert result[0]["characters"] == ["何晏"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("valid", [True, False])
def test_passive_presence_repair_rechecks_and_rolls_back(valid):
    text = "石远沉默不语。\n\n余下正文不能改。"
    original = {"events": [], "summary": "原记录"}
    calls = []

    def ask(*args, **kwargs):
        calls.append(args[1])
        return {"edits": [{"id": 1, "text": "石远已经离场。"}]}

    def extract(*args, **kwargs):
        calls.append(args[1])
        return {"events": []}, not valid, 0

    actual, raw, failed, note = repair_retired(
        None, "ch11", text, original, False, {}, design(), {"number": 11}, extract, ask
    )
    assert actual == (text.replace("石远沉默不语。", "石远已经离场。") if valid else text)
    assert note["status"] == ("repaired" if valid else "rolled_back")
    assert calls == ["ch11-opponent-repair", "ch11-opponent-recheck"]
    if not valid:
        assert raw is original and failed is False


def test_final_boundary_recheck_catches_reappearance_after_first_repair(monkeypatch):
    from bookengine import q1_chapter
    from support_minimal_b import MinimalBTests, CaptureClient, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        fixture.bible.update(design())
        fixture.bible["antagonists"][0]["exit"] = 1
        text = "石远沉默不语。\n\n" + body(60)
        calls = []

        def early(*args):
            calls.append("early")
            return args[2].replace("石远沉默不语。", "石远已经离场。"), args[3], args[4], {"status": "repaired"}

        def late(*args):
            calls.append("repetition")
            return args[2].replace("石远已经离场。", "石远沉默不语。"), args[3], args[4], {"status": "checked"}

        monkeypatch.setattr(q1_chapter, "repair_retired", early)
        monkeypatch.setattr(q1_chapter, "repair_repetitions", late)
        client = CaptureClient(
            fixture.out,
            [text],
            {
                "r2-ch2-exit-tail-opponent-repair": {"edits": [{"id": 1, "text": "石远已经离场。"}]},
                "r2-ch2-exit-tail-opponent-recheck": fixture.extraction(),
            },
        )
        row = fixture.run_chapter(client)
        saved = (fixture.out / "chapter-002.body.md").read_text()
        assert "石远沉默不语。" not in saved
        assert row["exit_tail_repair"]["status"] == "repaired"
        assert calls == ["early", "repetition"]
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize(
    "text",
    [
        "石远曾沉默不语。",
        "她想起当年石远坐在角落。",
        "她提起石远沉默不语的样子。",
        "石远已经离场。",
        "石远不在现场。",
        "门旁是石远留下的旧剑。",
    ],
)
def test_exit_tail_keeps_background_mentions_and_absence(text):
    from bookengine.retired_opponents import exit_boundary_hits

    assert not exit_boundary_hits(text, design(), 11)


def test_passive_check_applies_to_exit_plus_one_only():
    from bookengine.retired_opponents import exit_boundary_hits

    assert not exit_boundary_hits("石远沉默不语。", design(), 10)
    assert not exit_boundary_hits("石远沉默不语。", design(), 12)


def run_eval35_fake_repair(out):
    """Use the production parser/edits/recheck and a fake HTTP-free Client."""
    from bookengine.q1_extract import ask
    from bookengine.retired_opponents import exit_boundary_hits, repair_exit_tail
    from support_minimal_b import CaptureClient

    text = (FIXTURES / "chapter-021.md").read_text()
    bible = json.loads((FIXTURES / "design.json").read_text())
    hits = exit_boundary_hits(text, bible, 21)
    assert len(hits) == 1 and hits[0]["name"] == "韩柯铭"
    replacement = "众人提起已经离场的货运联盟理事韩柯铭，谁也不愿回头。"
    client = CaptureClient(
        out,
        [],
        {
            "ch21-exit-tail-opponent-repair": {"edits": [{"id": hits[0]["id"], "text": replacement}]},
            "ch21-exit-tail-opponent-recheck": {"events": [], "summary": "众人离开假闸，赶往旧泵站。"},
        },
    )
    client.prefix = ""

    def extract(client, name, candidate, *args, **kwargs):
        assert candidate == text[: hits[0]["start"]] + replacement + text[hits[0]["end"] :]
        return ask(client, name, "只据修改后的正文复检", [("正文", candidate)], "{}"), False, 0

    result = repair_exit_tail(client, "ch21", text, {}, False, {}, bible, {"number": 21}, extract, ask)
    return text, bible, client, result


def test_eval35_ch21_real_text_is_repaired_by_fake_client(tmp_path):
    from bookengine.retired_opponents import exit_boundary_hits

    text, bible, client, (changed, raw, failed, note) = run_eval35_fake_repair(tmp_path)
    assert note["status"] == "repaired"
    assert not failed and not exit_boundary_hits(changed, bible, 21)
    assert changed != text
    assert raw["summary"] == "众人离开假闸，赶往旧泵站。"
    assert [c["stage"] for c in client.calls] == ["ch21-exit-tail-opponent-repair", "ch21-exit-tail-opponent-recheck"]


def test_exit_tail_rolls_back_if_replacement_is_still_present():
    from bookengine.retired_opponents import repair_exit_tail

    text = "石远沉默不语。"
    raw = {"events": [], "summary": "原文"}

    def extract(*args, **kwargs):
        raise AssertionError("invalid candidate must not be extracted")

    changed, actual, _, note = repair_exit_tail(
        None,
        "ch11",
        text,
        raw,
        False,
        {},
        design(),
        {"number": 11},
        extract,
        lambda *a, **kw: {"edits": [{"id": 1, "text": "石远坐在角落。"}]},
    )
    assert changed == text and actual is raw
    assert note["status"] == "rolled_back" and note["after"]


def test_final_opponent_edit_cannot_introduce_a_repeated_sentence():
    from bookengine.retired_opponents import repair_exit_tail

    repeated = "守卫从城门外赶回山城营地。"
    text = repeated + "\n\n石远沉默不语。"
    changed, _, _, note = repair_exit_tail(
        None,
        "ch11",
        text,
        {},
        False,
        {},
        design(),
        {"number": 11},
        lambda *a, **kw: ({"events": []}, False, 0),
        lambda *a, **kw: {"edits": [{"id": 2, "text": repeated}]},
    )
    assert changed == text and note["status"] == "rolled_back"
