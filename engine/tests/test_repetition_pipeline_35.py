"""Repetition cleanup runs before extraction and after local repairs."""

from bookengine.q1_extract import prose_hits
from support_minimal_b import MinimalBTests, CaptureClient, body
from tools.book_checkup import check_book


def test_safe_duplicate_is_removed_before_extraction_and_counted():
    fixture = MinimalBTests()
    fixture.setUp()
    try:
        repeated = "院墙外面的灯笼已经照亮整条长街。"
        text = repeated + "\n\n" + body(60) + "\n\n院门忽然关上了。\n\n" + repeated
        client = CaptureClient(fixture.out, [text])
        row = fixture.run_chapter(client)
        saved = (fixture.out / "chapter-002.body.md").read_text()
        assert saved.count(repeated) == 1
        assert row["repetition"]["draft"]["counts"]["removed_spans"] == 1
        prompt = next(c["prompt"] for c in client.calls if c["stage"] == "r2-ch2-finish")
        assert prompt.count(repeated) == 1
    finally:
        fixture.doCleanups()


def test_unsafe_repetition_uses_existing_sentence_ids_for_repair():
    text = (
        "阿宁举起那盏已经熄灭的旧油灯。\n\n院子里响起一阵脚步声。\n\n阿宁举起那盏，已经熄灭的旧油灯。她转身跑进屋里。"
    )
    hits = [h for h in prose_hits(text, {}, {}) if h.get("kind") == "prose_repetition"]
    assert hits and hits[0]["id"] == 3
    assert hits[0]["text"] == text[hits[0]["start"] : hits[0]["end"]]


def test_final_repair_duplicate_is_removed_and_state_matches_adopted_text():
    fixture = MinimalBTests()
    fixture.setUp()
    try:
        repeated = "院墙外面的灯笼已经照亮整条长街。"
        text = repeated + "\n\n" + body(60) + "\n\n院门忽然关上了。"
        first = fixture.extraction(patch=dict(after=4, text=repeated))
        client = CaptureClient(
            fixture.out, [text], {"r2-ch2-finish": first, "r2-ch2-clean-state": fixture.extraction()}
        )
        row = fixture.run_chapter(client)
        assert (fixture.out / "chapter-002.body.md").read_text().count(repeated) == 1
        assert row["repetition"]["final"]["counts"]["removed_spans"] == 1
    finally:
        fixture.doCleanups()


def test_checkup_counts_repetition_without_cleaning_source(tmp_path):
    text = "古城东侧的钟楼下面，有东西醒了。\n\n人群奔向街口。\n\n古城东侧的钟楼下面，有东西醒了。"
    path = tmp_path / "chapter-026.body.md"
    path.write_text(text)
    report = check_book(tmp_path)
    assert path.read_text() == text
    assert report["repetition"]["sentence_repetitions"] == 1
    assert report["repetition"]["chapters"][0]["chapter"] == 26


def test_large_retirement_metadata_cannot_displace_chapter_scenes():
    from bookengine.q1_extract import context

    bible = dict(
        volumes=[dict(number=1, start=1, end=2000, location="港口")],
        antagonists=[dict(name=f"旧对手{i}", exit=i) for i in range(1, 150)],
    )
    sections = context(
        bible, {}, {"number": 1}, {"number": 170, "scenes": [{"action": "场景必须保留"}]}, {}, [], [], ""
    )
    assert "场景必须保留" in sections[0][1]


def test_new_dependent_repetition_gets_one_bounded_repair_and_recheck():
    from bookengine.q1_repetition import repair_repetitions

    duplicate = "何晏要的是我身上那把已经断裂的旧剑。"
    text = duplicate + "\n\n城里的钟声响了。\n\n" + duplicate.replace("旧剑", "旧，剑") + "他转身走了。"
    requests = []

    def ask(*args, **kwargs):
        requests.append(args[1])
        return {"edits": [{"id": 3, "text": "何晏伸手抓住了剑柄。"}]}

    def extract(*args, **kwargs):
        requests.append(args[1])
        return {"events": []}, False, 0

    changed, _, _, note = repair_repetitions(None, "ch11", text, {}, False, {}, {}, {"number": 11}, extract, ask)
    assert "何晏伸手抓住了剑柄。" in changed
    assert note["status"] == "repaired"
    assert requests == ["ch11-repetition-repair", "ch11-repetition-recheck"]


def test_repetition_repair_rolls_back_if_it_revives_a_retired_opponent():
    from bookengine.q1_repetition import repair_repetitions
    from test_retired_opponents_35 import design

    duplicate = "何晏要的是我身上那把已经断裂的旧剑。"
    text = duplicate + "\n\n城楼静悄悄。\n\n" + duplicate.replace("旧剑", "旧，剑")
    raw = {"events": []}

    def ask(*args, **kwargs):
        return {"edits": [{"id": 3, "text": "石远派人堵住山城。"}]}

    actual, _, _, note = repair_repetitions(
        None, "ch11", text, raw, False, {}, design(), {"number": 11}, lambda *a, **kw: ({"events": []}, False, 0), ask
    )
    assert actual == text and note["status"] == "rolled_back"
