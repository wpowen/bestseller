"""Cast checks reach the actual writer context and sentence repair boundary."""

from bookengine.q1_cast import cast_hits, chapter_cast, previous_cast, seen_in


CARDS = [dict(name="林舟", identity="渡船伙计", role="protagonist", aliases=["阿舟"])]


def test_unknown_nickname_is_a_repair_target_but_a_bare_first_mention_is_not():
    """Instruction 71: who someone is shows in what they do; a first mention needs no identity."""
    bible = dict(characters=CARDS)
    text = "林舟推开门。\n\n“跟牛哥走！”他喊。"
    hits = cast_hits(text, bible, {})
    assert {(r["id"], r["kind"]) for r in hits} == {(2, "unregistered_alias")}
    assert all(text[r["start"] : r["end"]] == r["text"] for r in hits)


def test_stand_alone_identity_sentence_is_the_repair_target():
    bible = dict(characters=CARDS)
    text = "林舟推开门。他是渡口撑了十年船的伙计，袖口沾着河泥。\n\n他把缆绳甩上桩。"
    hits = cast_hits(text, bible, {})
    assert [(r["id"], r["kind"]) for r in hits] == [(2, "identity_statement")]
    assert cast_hits(text, bible, dict(seen_characters=["林舟"])) == []


def test_old_book_reconstruction_uses_saved_body_not_future_outline(tmp_path):
    (tmp_path / "chapter-001.body.md").write_text("阿舟登船。")
    (tmp_path / "chapter-003.body.md").write_text("另一位新客出现。")
    bible = dict(characters=CARDS)
    seen = previous_cast(tmp_path, 2, bible, {})
    assert seen == ["林舟"]
    assert chapter_cast(dict(number=2, characters=["林舟"]), bible, seen)["introductions"] == []
    assert seen_in("阿舟登船。", CARDS) == ["林舟"]


def test_address_is_not_identity_and_stale_seen_state_rebuilds_from_saved_prose(tmp_path):
    cards = [dict(name="林舟", identity="渡船伙计"), dict(name="顾衡", address="林舟")]
    (tmp_path / "chapter-001.body.md").write_text("林舟登船。")
    assert seen_in("林舟登船。", cards) == ["林舟"]
    assert previous_cast(tmp_path, 2, dict(characters=cards), dict(seen_characters=["林舟", "顾衡"])) == ["林舟"]


def test_chapter_outline_first_appearance_is_passed_to_writer_context():
    from bookengine.q1_extract import context

    bible = dict(characters=CARDS)
    outline = chapter_cast(dict(number=1, characters=["林舟"]), bible, [])
    first = context(bible, {}, {"number": 1}, outline, {}, [], [], "")[0][1]
    assert '"introductions"' in first and "渡船伙计" in first


def test_existing_repair_round_receives_identity_and_alias_findings():
    from bookengine.q1_extract import prose_hits

    hits = prose_hits("林舟推开门。他是渡口的船工。\n\n“跟牛哥走！”他喊。", dict(characters=CARDS), {})
    kinds = {r["kind"] for r in hits}
    assert {"identity_statement", "unregistered_alias"} <= kinds and "missing_introduction" not in kinds


def test_writer_adopts_rechecked_introduction_and_keeps_observed_seen_names():
    from support_minimal_b import MinimalBTests, CaptureClient, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        fixture.bible["characters"][0]["identity"] = "渡船伙计"
        original = "阿宁推开院门。她是渡口的渡船伙计，袖口沾着河泥。\n\n" + body(60)
        edited = original.replace("她是渡口的渡船伙计，袖口沾着河泥。", "她袖口沾着河泥。")
        first = fixture.extraction(edits=[dict(id=2, text="她袖口沾着河泥。")])
        client = CaptureClient(
            fixture.out, [original], {"r2-ch1-finish": first, "r2-ch1-final-state": fixture.extraction()}
        )
        row = fixture.run_chapter(client, rows=[], outline=dict(fixture.outline, number=1))
        assert (fixture.out / "chapter-001.body.md").read_text() == edited
        assert row["polish"]["cast_repair"] == "rechecked"
        assert row["state"]["seen_characters"] == ["阿宁"]
        assert not any(r["kind"] == "identity_statement" for r in row["cast_findings"])
    finally:
        fixture.doCleanups()


def test_writer_rolls_back_cast_edit_when_final_fact_check_fails():
    from support_minimal_b import MinimalBTests, CaptureClient, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        fixture.bible["characters"][0]["identity"] = "渡船伙计"
        original = "阿宁推开院门。她是渡口的渡船伙计，袖口沾着河泥。\n\n" + body(60)
        first = fixture.extraction(edits=[dict(id=2, text="她袖口沾着河泥。")])
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


def test_string_sentence_id_still_requires_final_fact_check_before_cast_adoption():
    from support_minimal_b import MinimalBTests, CaptureClient, body

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        fixture.bible["characters"][0]["identity"] = "渡船伙计"
        original = "阿宁推开院门。她是渡口的渡船伙计，袖口沾着河泥。\n\n" + body(60)
        first = fixture.extraction(edits=[dict(id="S2", text="她袖口沾着河泥。")])
        invalid = fixture.extraction(
            payoff=dict(type=fixture.bible["canon"]["payoff_menu"][0], fulfilled=True, quote="伪造引文")
        )
        client = CaptureClient(fixture.out, [original], {"r2-ch1-finish": first, "r2-ch1-final-state": invalid})
        row = fixture.run_chapter(client, rows=[], outline=dict(fixture.outline, number=1))
        assert (fixture.out / "chapter-001.body.md").read_text() == original
        assert row["polish"]["cast_repair"] == "rolled_back"
    finally:
        fixture.doCleanups()


def test_extraction_keeps_complete_numbered_prose_ahead_of_long_diagnostics():
    from support_minimal_b import MinimalBTests, CaptureClient, body
    from bookengine.q1_extract import extract_state
    from unittest.mock import patch

    fixture = MinimalBTests()
    fixture.setUp()
    try:
        text = body(60) + "\n\n末尾正文标记。"
        client = CaptureClient(fixture.out, [])
        diagnostics = [dict(kind="ambiguous_alias", message="诊断" * 4000)]
        with patch("bookengine.q1_extract.cast_findings", return_value=diagnostics):
            extract_state(client, "r2-ch2-finish", text, {}, fixture.bible, fixture.outline, [])
        assert "末尾正文标记。" in client.calls[0]["prompt"]
        assert client.calls[0]["prompt"].index("编号正文") < client.calls[0]["prompt"].index("登场与称呼")
    finally:
        fixture.doCleanups()
