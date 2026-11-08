"""INBOX 46: publication hygiene detection and bounded adoption."""

import json

from bookengine.text_hygiene import inspect_text, repair_text


def test_detects_english_and_structure_notes_but_allows_position_labels():
    text = "守卫退到A12号位，狭窄处/low石面刮过他的肩背。\n\n第二场戏开始时，天色已经暗了。\n\n顾川序抬头看向雪坡。"

    result = inspect_text(text)

    assert result["counts"]["english_fragments"] == 1
    assert result["english"][0]["text"] == "low"
    assert result["counts"]["structural_notes"] == 1
    assert result["structure"][0]["text"] == "第二场戏"


def test_detects_nonadjacent_exact_paragraph_block_from_eval45_chapter24():
    first = "陆烈收拾好刀，走到岩口却没有立刻下山。他回头问顾川序：“你真要跟苏斐然守在这里？”"
    second = "苏斐然已蹲回伤者身边，没有催促，只等顾川序决定。"
    text = f"{first}\n\n{second}\n\n众人先把伤者送离旧猎路。\n\n{first}\n\n{second}"

    result = inspect_text(text)

    assert result["counts"]["exact_paragraph_repetitions"] == 2
    assert result["counts"]["paragraph_repetitions"] == 2
    assert [(row["reference_paragraph"], row["paragraph"]) for row in result["paragraphs"]] == [(1, 4), (2, 5)]


def test_detects_near_explanation_paragraph_from_eval45_chapter38():
    first = (
        "方才两人同时伸手时，绳索之所以被拉向相反方向，并非因为他抓绳、霜砚秋拉绳。"
        "绳结内侧早已塞进了一块带倒刺的兽骨。她当时伸手，根本不是要救他，而是在把骨刺从绳结里拔出来。"
    )
    second = (
        "顾川序这才发现，两人同时伸手时，绳索之所以被拉向相反方向，并非因为他抓绳、霜砚秋拉绳。"
        "绳结内侧早已塞进一块带倒刺的兽骨，骨刺正卡住绳股，分别借两人的力道绷向两端。"
    )

    result = inspect_text(f"{first}\n\n{second}\n\n霜砚秋抬起头。")

    assert result["counts"]["near_paragraph_repetitions"] == 1
    assert result["counts"]["paragraph_repetitions"] == 1
    issue = result["paragraphs"][0]
    assert issue["kind"] == "near_paragraph"
    assert issue["reference_paragraph"] == 1 and issue["paragraph"] == 2
    assert issue["similarity"] >= 0.65


def test_short_repeated_dialogue_is_not_a_whole_paragraph_issue():
    result = inspect_text("“好。”\n\n众人继续赶路。\n\n“好。”\n\n“安澜！”\n\n他推开门。\n\n“安澜！”")

    assert result["counts"]["paragraph_repetitions"] == 0


def test_writer_repair_changes_only_targets_then_rechecks_state():
    repeated = "陆烈收拾好刀，走到岩口却没有立刻下山。"
    text = (
        "狭窄处/low石面刮过他的肩背。\n\n"
        "第二场戏开始时，天色已经暗了。\n\n"
        f"{repeated}\n\n众人先把伤者送走。\n\n{repeated}\n\n顾川序抬头看向雪坡。"
    )
    requests = []

    def ask(*args, **kwargs):
        requests.append(args[1])
        targets = json.loads(dict(args[3])["卫生命中"])
        replacements = {
            "english": "狭窄处低矮石面刮过他的肩背。",
            "structure": "天色已经暗了。",
            "exact_paragraph": "",
        }
        return {"edits": [{"id": row["id"], "text": replacements[row["kind"]]} for row in targets]}

    extracted = {"events": [], "state": {}}
    result, raw, failed, note = repair_text(
        None,
        "r2-ch24",
        text,
        {"events": []},
        False,
        {},
        {"characters": [{"name": "顾川序"}, {"name": "陆烈"}]},
        {"number": 24},
        lambda *args, **kwargs: (extracted, False, 0),
        ask,
    )

    assert requests == ["r2-ch24-text-hygiene"]
    assert not any(inspect_text(result)["counts"].values())
    assert result.endswith("顾川序抬头看向雪坡。")
    assert raw == extracted and failed is False
    assert note["status"] == "repaired" and len(note["edits"]) == 3


def test_writer_repair_rolls_back_when_hygiene_recheck_fails():
    text = "狭窄处/low石面刮过他的肩背。\n\n顾川序抬头看向雪坡。"
    extracted = []

    def ask(*args, **kwargs):
        target = json.loads(dict(args[3])["卫生命中"])[0]
        return {"edits": [{"id": target["id"], "text": "狭窄处/high石面刮过他的肩背。"}]}

    result, raw, failed, note = repair_text(
        None,
        "r2-ch6",
        text,
        {"events": []},
        False,
        {},
        {},
        {"number": 6},
        lambda *args, **kwargs: extracted.append(True),
        ask,
    )

    assert result == text and raw == {"events": []} and failed is False
    assert extracted == []
    assert note["status"] == "rolled_back"
    assert note["reason"] == "hygiene_recheck_failed"


def test_q1_chapter_runs_hygiene_before_saving(monkeypatch):
    from bookengine import q1_chapter
    from support_minimal_b import CaptureClient, MinimalBTests, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        draft = body(40, 190) + "\n\n狭窄处/low石面刮过他的肩背。"
        calls = []

        def repair(client, prefix, text, raw, failed, *args):
            calls.append(prefix)
            return text.replace("/low", "低矮"), raw, failed, {"status": "repaired"}

        monkeypatch.setattr(q1_chapter, "repair_text", repair)
        row = fixture.run_chapter(CaptureClient(fixture.out, [draft]))

        assert calls == ["r2-ch2"]
        assert "/low" not in (fixture.out / "chapter-002.body.md").read_text()
        assert row["cleaning"]["text_hygiene"] == {"status": "repaired"}
    finally:
        fixture.doCleanups()


def test_book_checkup_counts_each_hygiene_kind_and_lists_chapters(tmp_path):
    from tools.book_checkup import check_book

    repeated = "陆烈收拾好刀，走到岩口却没有立刻下山。"
    (tmp_path / "chapter-001.body.md").write_text("狭窄处/low石面刮过他的肩背。")
    (tmp_path / "chapter-002.body.md").write_text("第二场戏开始时，天色已经暗了。")
    (tmp_path / "chapter-003.body.md").write_text(f"{repeated}\n\n众人先走。\n\n{repeated}")

    result = check_book(tmp_path)["text_hygiene"]

    assert result["english_fragments"] == 1 and result["english_chapters"] == [1]
    assert result["structural_notes"] == 1 and result["structural_chapters"] == [2]
    assert result["paragraph_repetitions"] == 1 and result["paragraph_repetition_chapters"] == [3]
    assert [row["chapter"] for row in result["chapters"]] == [1, 2, 3]
