import pytest

from bookengine.disclosure_choices import HEADING, disclosure_context, disclosure_effect, disclosure_extraction
from bookengine.storage import load, save
from support_minimal_b import CaptureClient, MinimalBTests as WriterFixture, body as existing_prose


SAID = "封桥的命令是我爹亲手签的。"
REPLY = "等落雨停了再谈封桥。"
CAUSE = "签下封桥命令的竟是她父亲，交给她会使消息传回家里。"
ACTION = "容春把渡册放进自己衣襟，请林澜先去等船。"


def scene(out):
    people = ["林澜", "容春"]
    bible = dict(
        characters=[dict(name=name, role="protagonist" if i == 0 else "support") for i, name in enumerate(people)],
        world=dict(truth_layers=[dict(layer=2, truth="封桥的命令由林澜的父亲签下")]),
    )
    prior = f"林澜低声道：“{SAID}”\n\n容春按住长凳：“{REPLY}”"
    save(out / "chapter-001.body.md", prior)
    record = dict(
        number=1,
        status="complete",
        fact_source="final_body",
        summary="两人在桥头碰见",
        story_duties=[dict(id="truth-2", kind="truth", status="done")],
    )
    outline = dict(
        number=3,
        type="对抗",
        title="渡桥",
        story_duties=[
            dict(
                id="rel-林澜-容春-s2",
                chapter=2,
                deadline=3,
                text="决定谁保管渡册",
                kind="relationship",
                names=people,
                anchors=[],
            )
        ],
        scenes=[
            dict(
                characters=people,
                goal="离开城门",
                choice="缓渡",
                result="林澜先走",
                speaker_intents=[dict(name="容春", goal="决定渡册交给谁")],
            )
        ],
    )
    return bible, [record], outline


def test_normal_chapter_production_carries_only_witnessed_facts_into_the_action_scene():
    fixture = WriterFixture()
    fixture.setUp()
    try:
        bible, rows, outline = scene(fixture.out)
        fixture.bible.update(bible)
        outline.update(number=2, conflict_type="守桥", opening_conflict="有人来抢渡册")

        class DraftSeen(Exception):
            pass

        client = CaptureClient(fixture.out, [], {"r2-ch2-draft": DraftSeen()})
        with pytest.raises(DraftSeen):
            fixture.run_chapter(client, {}, rows, outline)
        prompt = client.calls[0]["prompt"]
        assert HEADING in prompt and SAID in prompt and REPLY in prompt and "决定渡册交给谁" in prompt
        assert "本章章型：对抗" in prompt
    finally:
        fixture.doCleanups()


def test_finished_chapter_records_the_choice_from_final_prose():
    fixture = WriterFixture()
    fixture.setUp()
    try:
        bible, rows, outline = scene(fixture.out)
        fixture.bible.update(bible)
        outline.update(number=2, conflict_type="守桥", opening_conflict="有人来抢渡册")
        observation = dict(status="acted", cause_quote=CAUSE, action_quote=ACTION, reason="容春决定留书")
        extract = fixture.extraction(type="对抗", scene_types=["对抗"], disclosure_effect=observation)
        draft = existing_prose(52, total=139) + "\n\n" + CAUSE + "\n\n" + ACTION
        client = CaptureClient(fixture.out, [draft], {"r2-ch2-finish": extract, "r2-ch2-final-state": extract})
        row = fixture.run_chapter(client, {}, rows, outline)
        assert row["disclosure_effect"]["status"] == "evidenced"
        assert load(fixture.out / "chapters/002.json")["disclosure_effect"] == row["disclosure_effect"]
        assert ACTION in (fixture.out / "chapter-002.body.md").read_text()
        final_requests = [c["prompt"] for c in client.calls if c["stage"] == "r2-ch2-finish"]
        assert final_requests and SAID in final_requests[0] and "action_quote" in final_requests[0]
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize(
    "variant",
    [
        "speculation_in_memory",
        "another_hearer",
        "object_not_actor",
        "missing_body",
        "unwritten",
        "failed",
        "future",
        "off_duty",
    ],
)
def test_unknown_witness_and_deferred_scene_do_not_create_an_obligation(tmp_path, variant):
    bible, rows, outline = scene(tmp_path)
    if variant == "speculation_in_memory":
        save(tmp_path / "chapter-001.body.md", f"林澜心想：“{SAID}”\n\n容春按住长凳：“{REPLY}”")
    elif variant == "another_hearer":
        save(tmp_path / "chapter-001.body.md", f"林澜说：“{SAID}”\n\n韩文答：“我也听说了。”\n\n容春：“{REPLY}”")
    elif variant == "object_not_actor":
        save(tmp_path / "chapter-001.body.md", f"旁人望着林澜：“{SAID}”\n\n容春答：“{REPLY}”")
    elif variant == "missing_body":
        (tmp_path / "chapter-001.body.md").unlink()
    elif variant == "unwritten":
        rows[0]["story_duties"][0]["status"] = "missing"
    elif variant == "failed":
        rows[0]["state_failed"] = True
    elif variant == "future":
        rows[0]["number"] = 5
    else:
        outline["story_duties"] = []
    assert disclosure_context(outline, bible, rows, tmp_path) is None


def test_historical_disclosure_needs_separate_local_evidence_of_choice_and_cause(tmp_path):
    bible, rows, outline = scene(tmp_path)
    focus = disclosure_context(outline, bible, rows, tmp_path)
    outline["disclosure_context"] = focus
    assert SAID in disclosure_extraction(outline)[0]
    assert disclosure_extraction({}) == ("", {})
    raw = dict(
        disclosure_effect=dict(status="acted", cause_quote=CAUSE, action_quote=ACTION, reason="她决定自己保管渡册")
    )
    text = CAUSE + "\n\n" + ACTION
    note = disclosure_effect(outline, raw, text)
    assert note["status"] == "evidenced"
    for bad in (ACTION, CAUSE, CAUSE + "\n\n" + "山上的灯亮了。\n\n" * 4 + ACTION):
        assert disclosure_effect(outline, raw, bad)["status"] == "unknown"
    unrelated = "河口两艘船还没开走。"
    raw["disclosure_effect"]["cause_quote"] = unrelated
    assert disclosure_effect(outline, raw, unrelated + ACTION)["status"] == "unknown"
    wrong_person = "林澜把渡册塞进容春衣襟。"
    raw["disclosure_effect"].update(cause_quote=CAUSE, action_quote=wrong_person)
    assert disclosure_effect(outline, raw, CAUSE + wrong_person)["status"] == "unknown"
    rows.append(dict(number=2, status="complete", fact_source="final_body", disclosure_effect=note))
    assert disclosure_context(outline, bible, rows, tmp_path) is None
    rows[-1]["disclosure_effect"] = dict(note, status="deferred")
    assert disclosure_context(outline, bible, rows, tmp_path)["id"] == focus["id"]
