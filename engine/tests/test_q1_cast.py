"""Cast checks reach the actual writer context and sentence repair boundary."""

from bookengine.q1_cast import cast_hits, chapter_cast, previous_cast, seen_in


CARDS = [dict(name="林舟", identity="渡船伙计", role="protagonist", aliases=["阿舟"])]


def test_unknown_nickname_and_missing_origin_are_numbered_repair_targets():
    bible = dict(characters=CARDS)
    text = "林舟推开门。\n\n“跟牛哥走！”他喊。"
    hits = cast_hits(text, bible, {})
    assert {(r["id"], r["kind"]) for r in hits} == {(1, "missing_introduction"), (2, "unregistered_alias")}
    assert all(text[r["start"] : r["end"]] == r["text"] for r in hits)


def test_old_book_reconstruction_uses_saved_body_not_future_outline(tmp_path):
    (tmp_path / "chapter-001.body.md").write_text("阿舟登船。")
    (tmp_path / "chapter-003.body.md").write_text("另一位新客出现。")
    bible = dict(characters=CARDS)
    seen = previous_cast(tmp_path, 2, bible, {})
    assert seen == ["林舟"]
    assert chapter_cast(dict(number=2, characters=["林舟"]), bible, seen)["introductions"] == []
    assert seen_in("阿舟登船。", CARDS) == ["林舟"]


def test_chapter_outline_first_appearance_is_passed_to_writer_context():
    from bookengine.q1_extract import context

    bible = dict(characters=CARDS)
    outline = chapter_cast(dict(number=1, characters=["林舟"]), bible, [])
    first = context(bible, {}, {"number": 1}, outline, {}, [], [], "")[0][1]
    assert '"introductions"' in first and "渡船伙计" in first


def test_existing_repair_round_receives_introduction_and_alias_findings():
    from bookengine.q1_extract import prose_hits

    hits = prose_hits("林舟推开门。\n\n“跟牛哥走！”他喊。", dict(characters=CARDS), {})
    assert {"missing_introduction", "unregistered_alias"} <= {r["kind"] for r in hits}


def test_writer_adopts_rechecked_introduction_and_keeps_observed_seen_names():
    from support_minimal_b import MinimalBTests, CaptureClient, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        fixture.bible["characters"][0]["identity"] = "渡船伙计"
        original = "阿宁推开院门。\n\n" + body(60)
        edited = original.replace("阿宁推开院门。", "渡船伙计阿宁推开院门。")
        first = fixture.extraction(edits=[dict(id=1, text="渡船伙计阿宁推开院门。")])
        client = CaptureClient(
            fixture.out, [original], {"r2-ch1-finish": first, "r2-ch1-final-state": fixture.extraction()}
        )
        row = fixture.run_chapter(client, rows=[], outline=dict(fixture.outline, number=1))
        assert (fixture.out / "chapter-001.body.md").read_text() == edited
        assert row["polish"]["cast_repair"] == "rechecked"
        assert row["state"]["seen_characters"] == ["阿宁"]
        assert any(r["kind"] == "introduction_present" for r in row["cast_findings"])
    finally:
        fixture.doCleanups()


def test_writer_rolls_back_cast_edit_when_final_fact_check_fails():
    from support_minimal_b import MinimalBTests, CaptureClient, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        fixture.bible["characters"][0]["identity"] = "渡船伙计"
        original = "阿宁推开院门。\n\n" + body(60)
        first = fixture.extraction(edits=[dict(id=1, text="渡船伙计阿宁推开院门。")])
        invalid = fixture.extraction(
            payoff=dict(type=fixture.bible["canon"]["payoff_menu"][0], fulfilled=True, quote="伪造引文")
        )
        client = CaptureClient(fixture.out, [original], {"r2-ch1-finish": first, "r2-ch1-final-state": invalid})
        row = fixture.run_chapter(client, rows=[], outline=dict(fixture.outline, number=1))
        assert (fixture.out / "chapter-001.body.md").read_text() == original
        assert row["polish"]["cast_repair"] == "rolled_back"
    finally:
        fixture.doCleanups()


def test_null_edit_list_does_not_turn_optional_cast_repair_into_project_error():
    from support_minimal_b import MinimalBTests, CaptureClient, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        client = CaptureClient(fixture.out, [body(60)], {"r2-ch2-finish": fixture.extraction(edits=None)})
        row = fixture.run_chapter(client)
        assert row["status"] == "complete"
    finally:
        fixture.doCleanups()
