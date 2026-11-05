"""INBOX35 repair-chain regressions; fake transport, real chapter adoption/cache."""

import json
from types import SimpleNamespace

import pytest

from bookengine import q1_plan
from bookengine.q1_chapter import chapter
from bookengine.prose_repetition import clean_repetitions
from bookengine.retired_opponents import active_opponent_hits, initiator_issues, repair_retired
from bookengine.stage_transition import stage_transition
from support_minimal_b import MinimalBTests, CaptureClient, body


@pytest.fixture
def repair_book(tmp_path):
    fixture = MinimalBTests()
    fixture.setUp()
    try:
        fixture.out = tmp_path
        fixture.bible.update(
            volumes=[
                dict(number=1, start=1, end=10, location="渡口", antagonist="石远"),
                dict(number=2, start=11, end=25, location="山城", antagonist="何晏"),
            ],
            antagonists=[dict(name="石远", stage=1, tier=1, exit=10)],
        )
        fixture.bible["characters"] += [dict(name="石远", role="antagonist"), dict(name="何晏", role="antagonist")]
        (tmp_path / "chapter-010.body.md").write_text("阿宁在渡口停步。", encoding="utf-8")
        yield fixture
    finally:
        fixture.doCleanups()


class RepairClient(CaptureClient):
    """Script optional follow-up repairs without choosing their exact stage prefix."""

    def __init__(self, fixture, text, opponent_edit):
        super().__init__(fixture.out, [text], {"r2-ch11-opponent-repair": dict(edits=[opponent_edit])})
        self.extracted = fixture.extraction()
        self.repetition_repairs = 0

    def request(self, stage, prompt, cap, effort, attempt):
        if "repetition" in stage:
            if stage.endswith(("recheck", "state")):
                self.replies[stage] = self.extracted
            else:
                self.repetition_repairs += 1
                self.replies[stage] = dict(edits=[dict(id=7, text="我握紧断剑，退到城门背后。")])
        elif stage.endswith(("-opponent-recheck", "-opening-recheck", "-clean-state")):
            self.replies[stage] = self.extracted
        elif stage.endswith("-opening-repair"):
            self.replies[stage] = dict(replace_count=1, paragraphs=["三天后，何晏派人堵住山城。"])
        return super().request(stage, prompt, cap, effort, attempt)


def run_chapter(fixture, client):
    return chapter(
        client,
        fixture.book,
        fixture.bible,
        fixture.threads,
        {},
        fixture.bible["volumes"][1],
        dict(fixture.outline, number=11),
        dict(end_location="渡口"),
        [],
        [],
        fixture.out,
        "b",
        fixture.data,
    )


def test_opponent_repair_new_unsafe_repetition_gets_one_bounded_repair(repair_book):
    repeated = "何晏要的是我身上那把已经断裂的旧剑。"
    # Four intervening sentences put the later copy beyond precision.apply's
    # three-neighbor suffix guard. The pronoun makes deterministic deletion unsafe.
    text = (
        "三天后，阿宁来到山城。\n\n"
        + repeated.replace("何晏", "石远")
        + "\n\n"
        + body(60)
        + "\n\n城门忽然关闭。\n\n墙头响起钟声。\n\n"
        + repeated
    )
    assert clean_repetitions(text, remove_safe=False)["counts"]["sentence_repetitions"] == 0
    client = RepairClient(repair_book, text, dict(id=2, text=repeated))
    row = run_chapter(repair_book, client)
    saved = (repair_book.out / "chapter-011.body.md").read_text(encoding="utf-8")
    remaining = clean_repetitions(saved, remove_safe=False)
    assert remaining["counts"]["sentence_repetitions"] == 0, remaining["needs_repair"]
    assert client.repetition_repairs == 1
    assert not active_opponent_hits(saved, repair_book.bible, 11)
    assert row["repetition"]["remaining"]["sentence_repetitions"] == 0
    assert row["status"] == "complete" and row["fact_source"] == "final_body"


def test_opponent_repair_cannot_remove_only_stage_transition(repair_book):
    text = "三天后，石远派人堵住山城。\n\n" + body(60)
    assert stage_transition(repair_book.bible, 11, text, "渡口")["status"] == "present"
    client = RepairClient(repair_book, text, dict(id=1, text="何晏派人堵住山城。"))
    row = run_chapter(repair_book, client)
    saved = (repair_book.out / "chapter-011.body.md").read_text(encoding="utf-8")
    observed = stage_transition(repair_book.bible, 11, saved, "渡口")
    assert observed["status"] == "present", dict(observed=observed, recorded=row["handoff"])
    if row["opponent_repair"]["status"] == "rolled_back":
        assert saved == text
    else:
        assert not active_opponent_hits(saved, repair_book.bible, 11)
    assert row["handoff"]["transition"]["status"] == observed["status"]
    assert all(quote in saved[:300] for quote in row["handoff"]["transition"]["quotes"])


def test_opponent_repair_cannot_create_a_new_grounded_opening_break(repair_book):
    original = "石远派人围住渡口。\n\n阿宁站在岸边等候。"
    replacement = "何晏派人围住山城。"
    raw = dict(events=[], opening_location="渡口", opening_quote=original.split("\n\n")[0])

    def extract(*args, **kwargs):
        return dict(events=[], opening_location="山城", opening_quote=replacement), False, 0

    actual, actual_raw, failed, note = repair_retired(
        None,
        "ch12",
        original,
        raw,
        False,
        dict(end_location="渡口"),
        repair_book.bible,
        dict(number=12),
        extract,
        lambda *args, **kwargs: dict(edits=[dict(id=1, text=replacement)]),
    )
    assert actual == original
    assert actual_raw is raw and failed is False
    assert note["status"] == "rolled_back"


@pytest.mark.parametrize("surface", ["body", "conflict", "opponent"])
def test_history_word_in_action_object_does_not_exempt_current_agent(repair_book, surface):
    text = "石远派人追查当年的失火案。"
    if surface == "body":
        issues = active_opponent_hits(text, repair_book.bible, 11)
    else:
        issues = initiator_issues(repair_book.bible, [dict(number=11, **{surface: text})])
    assert issues, "当年 modifies the investigated fire, not 石远派人"
    assert issues[0]["name"] == "石远"
    assert not active_opponent_hits("石远当年派人追查失火案。", repair_book.bible, 11)


def test_history_object_does_not_bypass_full_chapter_opponent_repair(repair_book):
    text = "三天后，阿宁来到山城。\n\n石远派人追查当年的失火案。\n\n" + body(60)
    client = RepairClient(repair_book, text, dict(id=2, text="何晏派人追查当年的失火案。"))
    row = run_chapter(repair_book, client)
    saved = (repair_book.out / "chapter-011.body.md").read_text(encoding="utf-8")
    assert "石远派人追查当年的失火案。" not in saved
    assert row["opponent_repair"]["status"] == "repaired"


def test_legacy_cache_with_history_object_cannot_skip_retired_agent_rewrite(repair_book, monkeypatch):
    folder = repair_book.out / "outline-diversity"
    folder.mkdir()
    original = dict(
        number=11,
        title="新程",
        conflict="石远派人追查当年的失火案。",
        transition="三天后为救人赶到山城。",
        scenes=[],
    )
    path = folder / "011-011.json"
    path.write_text(json.dumps(dict(status="passed", planned_chapters=[original], chapters=[original])))
    before = path.read_bytes(), path.stat().st_mtime_ns
    requests = []

    def ask(*args, **kwargs):
        requests.append(args[2])
        return dict(chapters=[dict(original, conflict="何晏拦住阿宁")])

    monkeypatch.setattr(q1_plan, "_ask", ask)
    result = q1_plan.outlines(
        SimpleNamespace(out=repair_book.out),
        repair_book.bible,
        {},
        {},
        repair_book.bible["volumes"][1],
        11,
        11,
        {},
        [],
        [],
        [],
    )
    assert result[0]["conflict"] == "何晏拦住阿宁", result
    assert requests
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
