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


def test_eval52_short_sentence_and_dialogue_duplicates_are_one_safe_finding_each():
    samples = [
        "“藏什么？”他问。\n\n“藏什么？”他问。",
        "两难就在眼前。\n\n两难就在眼前。",
    ]

    for text in samples:
        result = inspect_text(text)
        assert result["counts"]["sentence_repetitions"] == 1
        assert result["sentences"][0]["kind"] == "exact_short_sentence"
        assert result["sentences"][0]["safe_delete"] is True


def test_short_exact_duplicate_within_three_sentences_is_detected_but_three_calls_are_not():
    within_three = "别走。风压低了火苗。门外传来脚步。顾晴棣握紧药瓶。别走。"
    rhetorical_three = "“回来！”\n\n“回来！”\n\n“回来！”"

    assert inspect_text(within_three)["counts"]["sentence_repetitions"] == 1
    assert inspect_text(rhetorical_three)["counts"]["sentence_repetitions"] == 0


def test_eval52_short_duplicate_repair_deletes_only_the_later_complete_line():
    text = "“藏什么？”他问。\n\n“藏什么？”他问。\n\n雪沛宁低头刨雪。"

    result, _, failed, note = repair_text(
        None,
        "r2-ch11",
        text,
        {"events": []},
        False,
        {},
        {"characters": [{"name": "雪沛宁"}]},
        {"number": 11},
        lambda *_args, **_kwargs: ({"events": []}, False, 0),
        lambda *_args, **_kwargs: {"edits": []},
    )

    assert result == "“藏什么？”他问。\n\n雪沛宁低头刨雪。"
    assert inspect_text(result)["counts"]["sentence_repetitions"] == 0
    assert failed is False and note["status"] == "repaired"


def test_eval49_complete_sentence_repetitions_cover_exact_and_fused_forms():
    samples = {
        19: (
            "灰烬下，一枚陌生的兽铃碎片缓缓浮现，铃舌朝向外围，指向矿镇的方向。\n\n"
            "灰烬下，一枚陌生的兽铃碎片缓缓浮现，铃舌朝向外围，指向矿镇的方向。"
            "蛮玉筠盯着碎片上残留的旧骨纹路。"
        ),
        20: (
            "顾昕冉低头。猎团令牌贴在掌心，正与脚下骨阵的灰纹发出同一种细微震动。\n\n"
            "顾昕冉低头，猎团令牌贴在掌心，正与脚下骨阵的灰纹发出同一种细微震动。"
            "撤出弟子，骨阵便能循着令牌找到出口。"
        ),
        21: (
            "顾昕冉一怔，重新看向铜铃。兽铃回声再次响起，其中混着黑牙寨中特有的双层铃音。\n\n"
            "顾昕冉一怔，重新看向铜铃。兽铃回声再次响起，其中混着黑牙寨中特有的双层铃音。"
            "他判断药囊已被送入王城药道。"
        ),
    }

    results = {chapter: inspect_text(text) for chapter, text in samples.items()}

    assert results[19]["counts"]["sentence_repetitions"] == 1
    assert results[20]["counts"]["sentence_repetitions"] == 1
    assert results[20]["sentences"][0]["kind"] == "fused_sentence"
    assert results[21]["counts"]["sentence_repetitions"] == 1
    assert results[21]["sentences"][0]["kind"] == "sentence_sequence"
    assert all(len(row["normalized"]) >= 15 for result in results.values() for row in result["sentences"])


def test_sentence_repair_deletes_only_later_copy_and_preserves_unique_information():
    text = (
        "顾昕冉一怔，重新看向铜铃。兽铃回声再次响起，其中混着黑牙寨中特有的双层铃音。\n\n"
        "顾昕冉一怔，重新看向铜铃。兽铃回声再次响起，其中混着黑牙寨中特有的双层铃音。"
        "他判断药囊已被送入王城药道。\n\n顾小满抬头望向北门。"
    )

    def ask(*args, **kwargs):
        targets = json.loads(dict(args[3])["卫生命中"])
        edits = []
        for target in targets:
            replacement = (
                "顾昕冉判断药囊已被送入王城药道。" if "他判断药囊" in target["text"] else "这段文字不会被采用。"
            )
            edits.append({"id": target["id"], "text": replacement})
        return {"edits": edits}

    result, _, _, note = repair_text(
        None,
        "r2-ch21",
        text,
        {"events": []},
        False,
        {},
        {"characters": [{"name": "顾昕冉"}, {"name": "顾小满"}, {"name": "黑牙"}]},
        {"number": 21},
        lambda *args, **kwargs: ({"events": []}, False, 0),
        ask,
    )

    assert result.count("顾昕冉一怔，重新看向铜铃。") == 1
    assert result.count("兽铃回声再次响起，其中混着黑牙寨中特有的双层铃音。") == 1
    assert "顾昕冉判断药囊已被送入王城药道。" in result
    assert result.endswith("顾小满抬头望向北门。")
    assert inspect_text(result)["counts"]["sentence_repetitions"] == 0
    assert note["status"] == "repaired"


def test_safe_sentence_duplicate_accepts_empty_targeted_deletion():
    repeated = "灰烬下，一枚陌生的兽铃碎片缓缓浮现，铃舌朝向外围，指向矿镇的方向。"
    text = f"{repeated}\n\n{repeated}蛮玉筠抬起头。\n\n顾昕冉收起骨片。"

    result, _, _, note = repair_text(
        None,
        "r2-ch19",
        text,
        {"events": []},
        False,
        {},
        {"characters": [{"name": "顾昕冉"}, {"name": "蛮玉筠"}]},
        {"number": 19},
        lambda *args, **kwargs: ({"events": []}, False, 0),
        lambda *_args, **_kwargs: {"edits": [{"id": "H1", "text": ""}]},
    )

    assert result.count(repeated) == 1
    assert "蛮玉筠抬起头。" in result
    assert note["status"] == "repaired"


def test_dependency_repair_does_not_trust_an_echoed_repeated_block():
    repeated = "顾昕冉一怔，重新看向铜铃。兽铃回声再次响起，其中混着黑牙寨中特有的双层铃音。"
    text = f"{repeated}\n\n{repeated}他判断药囊已被送入王城药道。\n\n顾小满抬头望向北门。"

    def ask(*args, **kwargs):
        target = json.loads(dict(args[3])["卫生命中"])[0]
        return {"edits": [{"id": target["id"], "text": target["text"]}]}

    result, _, _, note = repair_text(
        None,
        "r2-ch21",
        text,
        {"events": []},
        False,
        {},
        {"characters": [{"name": "顾昕冉"}, {"name": "顾小满"}, {"name": "黑牙"}]},
        {"number": 21},
        lambda *args, **kwargs: ({"events": []}, False, 0),
        ask,
    )

    assert result.count(repeated) == 1
    assert "顾昕冉判断药囊已被送入王城药道。" in result
    assert note["status"] == "repaired"


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


def test_writer_repair_rolls_back_when_quote_balance_gets_worse():
    text = "顾川序说：“这不是 unquestioning 的归属。”\n\n霜砚秋抬头看他。"
    extracted = []

    def ask(*args, **kwargs):
        target = json.loads(dict(args[3])["卫生命中"])[0]
        return {"edits": [{"id": target["id"], "text": "顾川序说：“这不是无条件的归属。"}]}

    result, _, _, note = repair_text(
        None,
        "r2-ch31",
        text,
        {"events": []},
        False,
        {},
        {"characters": [{"name": "顾川序"}]},
        {"number": 31},
        lambda *args, **kwargs: extracted.append(True),
        ask,
    )

    assert result == text and extracted == []
    assert note["status"] == "rolled_back" and note["reason"] == "punctuation_recheck_failed"


def test_exact_duplicate_target_is_deleted_instead_of_paraphrased():
    repeated = "陆烈收拾好刀，走到岩口却没有立刻下山。"
    text = f"{repeated}\n\n众人先走。\n\n{repeated}\n\n顾川序抬头看向雪坡。"

    def ask(*args, **kwargs):
        target = json.loads(dict(args[3])["卫生命中"])[0]
        return {"edits": [{"id": target["id"], "text": "陆烈把刀收好，暂时没有下山。"}]}

    result, _, _, note = repair_text(
        None,
        "r2-ch24",
        text,
        {"events": []},
        False,
        {},
        {"characters": [{"name": "陆烈"}, {"name": "顾川序"}]},
        {"number": 24},
        lambda *args, **kwargs: ({"events": []}, False, 0),
        ask,
    )

    assert result.count(repeated) == 1
    assert "陆烈把刀收好" not in result
    assert note["status"] == "repaired"


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


def test_book_checkup_uses_the_same_sentence_repetition_counts(tmp_path):
    from tools.book_checkup import check_book

    repeated = "灰烬下，一枚陌生的兽铃碎片缓缓浮现，铃舌朝向外围，指向矿镇的方向。"
    (tmp_path / "chapter-019.body.md").write_text(f"{repeated}\n\n{repeated}蛮玉筠抬起头。")

    result = check_book(tmp_path, source="draft")["text_hygiene"]

    assert result["sentence_repetitions"] == 1
    assert result["sentence_repetition_chapters"] == [19]
