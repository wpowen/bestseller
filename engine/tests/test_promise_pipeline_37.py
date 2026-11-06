"""A real chapter transaction must save a rechecked promise repair, not its draft."""

from support_minimal_b import MinimalBTests, CaptureClient, body


def test_first_chapter_adopts_only_rechecked_core_demonstration():
    fixture = MinimalBTests()
    fixture.setUp()
    try:
        fixture.bible.update(title="借一息", power=dict(rule="旧钟回拨一息，代价是失去一段记忆"))
        draft = body(60)
        old = draft.split("\n\n")[0]
        new = "阿宁拨动旧钟，借回一息，接住了落地的碗。母亲的脸在她记忆里消失了。"

        def review(status, quote=""):
            return dict(
                items=[
                    dict(id=k, status=status, paragraphs=[1], evidence=[dict(chapter=1, quote=quote)])
                    for k in ("core-1", "cost-1")
                ]
            )

        client = CaptureClient(
            fixture.out,
            [draft],
            {
                "r2-ch1-finish": fixture.extraction(),
                "r2-ch1-promise-review": review("missing"),
                "r2-ch1-promise-repair": dict(edits=[dict(id=1, original=old, text=new)]),
                "r2-ch1-recheck-promise-review": review("fulfilled", new),
                "r2-ch1-promise-final-state": fixture.extraction(),
            },
        )
        row = fixture.run_chapter(client, rows=[], outline=dict(fixture.outline, number=1))
        assert row["opening_promises"]["status"] == "repaired"
        assert row["opening_promises"]["after"]["status"] == "checked"
        saved = (fixture.out / "chapter-001.body.md").read_text()
        assert saved == new + draft[len(old) :]
        assert (fixture.out / "chapter-001.r2.draft.md").read_text() == draft
        assert all(e["quote"] in saved for i in row["opening_promises"]["after"]["items"] for e in i["evidence"])
    finally:
        fixture.doCleanups()


def test_promise_repair_rolls_back_when_recheck_loses_known_locations():
    from bookengine.q1_promise_repair import repair

    text = "林宁站在石桥。\n\n林宁守在石桥。"
    proposed = "林宁在石桥拨动旧钟，借回一息救下妹妹，忘记了她的生辰。"
    raw = dict(opening_location="石桥", opening_quote="林宁站在石桥。", end_location="石桥", end_quote="林宁守在石桥。")

    def ask(client, stage, instruction, sections, schema, **kwargs):
        if stage.endswith("promise-review"):
            status = "fulfilled" if "recheck" in stage else "missing"
            return dict(
                items=[
                    dict(id=k, status=status, paragraphs=[1], evidence=[dict(chapter=1, quote=proposed)])
                    for k in ("core-1", "cost-1")
                ]
            )
        return dict(edits=[dict(id=1, original="林宁站在石桥。", text=proposed)])

    result = repair(
        None,
        "ch1",
        text,
        raw,
        False,
        {},
        dict(title="旧钟"),
        dict(number=1),
        {},
        lambda *a, **k: ({"events": []}, False, 0),
        ask,
    )
    assert result[0] == text and result[-1]["status"] == "rolled_back"


def test_core_obligation_survives_long_outline_budget_without_early_finale_promises():
    from bookengine.q1_extract import context

    sections = context(
        dict(title="借钟", power=dict(rule="拨钟倒回一息"), reader_expectation="前三章救回妹妹。"),
        {},
        {"number": 1},
        {"number": 1, "scenes": [dict(action="甲" * 2600)]},
        {},
        [],
        [],
        "",
    )
    assert "core-1" in sections[0][1] and "cost-1" in sections[0][1]
    assert "前三章救回妹妹" not in sections[0][1]
